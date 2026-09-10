"""Document round-trips: SOP markdown/docx/json export and import with diff; experiment export/import; library bundle."""
import io, json, zipfile


def test_sop_markdown_round_trip_and_edit():
    from backend import docs_io, sops
    sop = sops.get_sop("sop_verified_to_funded")
    md = docs_io.sop_markdown(sop)
    assert md.startswith("# SOP: ") and "## Steps" in md and "moe-sop-json" in md
    back = docs_io.parse_sop_markdown(md)
    for k in ("id", "name", "campaign_type", "transition", "objective", "primary_kpi", "target", "holdout_pct", "duration_days"):
        assert back[k] == sop[k], k
    assert len(back["steps"]) == len(sop["steps"]) and back["steps"][0]["channel"] == sop["steps"][0]["channel"] and back["audience"]["exclusions"] == sop["audience"]["exclusions"]
    assert docs_io.diff({k: v for k, v in sop.items() if k not in ("framework_ok", "framework_problems", "steps_count", "active", "updated_at")}, back) == []
    # a human edit in the text wins over the machine block
    edited = md.replace("- holdout_pct: " + str(sop["holdout_pct"]), "- holdout_pct: 25").replace(sop["steps"][0]["purpose"], "UPI deposit in 15 seconds (edited)", 1)
    back2 = docs_io.parse_sop_markdown(edited)
    assert back2["holdout_pct"] == 25 and back2["steps"][0]["purpose"] == "UPI deposit in 15 seconds (edited)"
    d = docs_io.diff(back, back2)
    assert {x["path"] for x in d} == {"holdout_pct", "steps[0].purpose"}
    assert sops.sop_check(back2)["ok"]


def test_docx_writer_reader_round_trip():
    from backend import docs_io, sops
    sop = sops.get_sop("sop_second_trade_72h")
    md = docs_io.sop_markdown(sop)
    blob = docs_io.markdown_to_docx(md, sop["name"])
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        assert {"[Content_Types].xml", "_rels/.rels", "word/document.xml", "docProps/core.xml"} <= set(z.namelist())
    text = docs_io.docx_to_text(blob)
    back = docs_io.parse_sop_markdown(text)
    assert back["id"] == sop["id"] and back["name"] == sop["name"] and len(back["steps"]) == len(sop["steps"])
    spec, fmt = docs_io.parse_upload("x.docx", blob, kind="sop")
    assert fmt == "docx" and spec["id"] == sop["id"]


def test_experiment_markdown_round_trip():
    from backend import docs_io
    p = {"id": 42, "title": "Campaign draft: test", "kind": "create_campaign", "status": "pending", "created_at": "2026-09-10 10:00:00", "created_by": "agent", "rationale": "because",
         "payload": {"name": "T", "channel": "push", "target_segment": "FTD_NOTRADE", "ttl_hours": 4, "exclusions": ["dnd"], "goal": {"transition": "funded_activated", "hypothesis": "h", "primary_kpi": "first_trade_rate_7d", "target": "+4 pp", "guardrail_metric": "unsubscribe_rate", "control_group_pct": 10, "measurement_window_days": 7, "kill_criteria": "x", "suppressions": ["liquidated"]},
                     "variants": [{"label": "A", "title": "t1", "body": "b1", "cta": "c1"}, {"label": "B", "title": "t2", "body": "b2", "cta": "c2"}], "schedule": {"date": "2026-09-11", "time_ist": "11:00"}, "ice": {"impact": 8, "confidence": 7, "ease": 8, "tagline": "tag"}}}
    md = docs_io.experiment_markdown(p)
    parsed = docs_io.parse_experiment_markdown(md.replace("- body: b1", "- body: b1 edited").replace("- control_group_pct: 10", "- control_group_pct: 20"))
    assert parsed["id"] == 42 and parsed["changes"]["payload"]["variants"][0]["body"] == "b1 edited" and parsed["changes"]["payload"]["goal"]["control_group_pct"] == 20
    d = docs_io.diff(p["payload"], parsed["changes"]["payload"])
    assert {x["path"] for x in d} == {"variants[0].body", "goal.control_group_pct"}


def test_library_bundle_and_csv():
    from backend import docs_io, sops
    all_sops = [sops.get_sop(x["id"]) for x in sops.list_sops()]
    csv_text = docs_io.sop_index_csv(all_sops)
    assert csv_text.splitlines()[0].startswith("id,name,version") and len(csv_text.splitlines()) == len(all_sops) + 1
    blob = docs_io.sop_bundle_zip(all_sops[:3])
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        names = z.namelist()
        assert "index.csv" in names and "library.json" in names and any(n.endswith(".docx") for n in names) and sum(1 for n in names if n.endswith(".md") and n.startswith("sops/")) == 3
