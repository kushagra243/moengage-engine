import json, os, subprocess


def test_tool_errors_are_recorded_and_grouped_into_fix_requests():
    from backend.llm.tools import _safe
    from backend import selfheal
    selfheal.init_selfheal_tables()
    def boom(**kw):
        raise KeyError("segment_filters missing for campaign c_9")
    out = _safe(boom)(campaign_id="c_9", secret="sk-or-v1-SHOULDNOTAPPEAR")
    assert out["error_type"] == "KeyError" and out["error_signature"] and "self_repair" in out
    out2 = _safe(boom)(campaign_id="c_10")
    assert out2["error_signature"] == out["error_signature"]          # same signature → grouped
    errs = selfheal.recent_errors()
    assert errs and "SHOULDNOTAPPEAR" not in json.dumps(errs)         # redacted
    rep = selfheal.health_report()
    grp = next(g for g in rep["error_groups"] if g["signature"] == out["error_signature"])
    assert grp["count"] >= 2 and grp["name"] == "boom"
    fx = next(f for f in rep["fix_requests"] if f["signature"] == out["error_signature"])
    assert "KeyError" in fx["request"] and "regression test" in fx["request"] and fx["scope"] == "backend"
    assert rep["status"] in ("degraded", "unhealthy")
    from backend.llm.tools import TOOLS
    assert "self_diagnose" in TOOLS and "rollback_last_change" in TOOLS
    assert TOOLS["self_diagnose"]()["fix_requests"]


def test_rollback_last_merge_reverts_merge_commit(tmp_path, monkeypatch):
    from backend import selfheal
    repo = str(tmp_path / "r"); subprocess.run(["git", "init", "-q", "-b", "main", repo], check=True)
    def sh(*a): return subprocess.run(["git", "-C", repo, "-c", "user.name=t", "-c", "user.email=t@t"] + list(a), capture_output=True, text=True, check=True)
    open(os.path.join(repo, "a.py"), "w").write("x = 1\n"); sh("add", "-A"); sh("commit", "-q", "-m", "init")
    pre = sh("rev-parse", "--short", "HEAD").stdout.strip()
    sh("checkout", "-q", "-b", "agent/change-1"); open(os.path.join(repo, "a.py"), "w").write("x = 2  # broken\n"); sh("commit", "-qam", "agent change")
    sh("checkout", "-q", "main"); sh("merge", "--no-ff", "--no-edit", "-m", "Merge agent change #1", "agent/change-1")
    post = sh("rev-parse", "--short", "HEAD").stdout.strip()
    monkeypatch.setattr(selfheal, "ROOT", repo); monkeypatch.setattr(selfheal, "LAST_MERGE", os.path.join(repo, "data", "last_merge.json"))
    selfheal.note_merge(1, pre, post)
    r = selfheal.rollback_last_merge("test")
    assert r["ok"], r
    assert open(os.path.join(repo, "a.py")).read() == "x = 1\n"
    assert selfheal.last_merge()["rolled_back"] is True
    assert not selfheal.rollback_last_merge("again")["ok"]
