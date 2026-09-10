"""
Document round-trip — take any SOP or experiment (proposal) off the platform
for discussion and bring the edits back.

Export formats: Markdown (.md, the canonical human format), Word (.docx, built
without external libraries), JSON (lossless), CSV (library index) and a ZIP
bundle of the whole SOP library (md + docx + json per SOP + index.csv).
Every Markdown/DOCX export ends with a machine block (the full JSON) so an
edited document parses back losslessly: human-edited fields override the block.

Import: parse .md / .docx / .json → preview (diff, framework check, India-fit
before/after) → apply = a new SOP version (`define_sop`) or a proposal
revision (`approvals.update_payload`). Nothing sends; runs still queue for
approval as always.
"""
from __future__ import annotations
import csv
import io
import json
import re
import zipfile
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from xml.sax.saxutils import escape

MACHINE_RE = re.compile(r"<!--\s*moe-(sop|experiment)-json:\s*(\{.*?\})\s*-->", re.S)


# ── SOP → Markdown ────────────────────────────────────────────────────────────
def _kv(k: str, v: Any) -> str:
    if isinstance(v, list):
        v = "; ".join(str(x) for x in v)
    return f"- {k}: {'' if v is None else v}"


def sop_markdown(sop: Dict[str, Any], machine_block: bool = True) -> str:
    aud = sop.get("audience") or {}; fr = sop.get("frequency") or {}; comp = sop.get("compliance") or {}
    lines = [f"# SOP: {sop.get('name', '')}", "", f"id: {sop.get('id')} | version: {sop.get('version', 1)} | type: {sop.get('campaign_type')} | transition: {sop.get('transition')}", "",
             "> Edit any value after a colon, any step, any bullet. Keep the headings and the `key: value` shape. Upload the file back in SOP Library → Upload edited SOP; the engine shows the diff and the checks before saving a new version.", "",
             "## Objective", sop.get("objective", ""), "", "## Who", sop.get("user", ""), "",
             "## Audience", _kv("segment_family", aud.get("segment_family")), _kv("description", aud.get("description")), _kv("min_reach", aud.get("min_reach")), _kv("exclusions", aud.get("exclusions") or []), _kv("jurisdictions_excluded", aud.get("jurisdictions_excluded") or []), "",
             "## Steps"]
    for i, st in enumerate(sop.get("steps") or [], 1):
        lines += [f"### Step {i} · day {st.get('day', 0)} · {st.get('channel', '')} · {st.get('send_time_ist') or '—'} IST", _kv("purpose", st.get("purpose")), _kv("copy_brief", st.get("copy_brief")), _kv("condition", st.get("condition")), _kv("ttl_hours", st.get("ttl_hours")), ""]
    lines += ["## Cadence & limits", _kv("duration_days", sop.get("duration_days")), _kv("cadence", fr.get("cadence")), _kv("max_messages_per_user_per_week", fr.get("max_messages_per_user_per_week")), _kv("holdout_pct", sop.get("holdout_pct")), "",
              "## Measurement", _kv("primary_kpi", sop.get("primary_kpi")), _kv("target", sop.get("target")), _kv("guardrail_metric", sop.get("guardrail_metric")), _kv("measurement_window_days", sop.get("measurement_window_days")), _kv("kill_criteria", sop.get("kill_criteria") or []), "",
              "## Compliance", _kv("disclaimer_channels", comp.get("disclaimer_channels") or []), _kv("banned_angles", comp.get("banned_angles") or []), "",
              "## Ideas to test"] + [f"- {x}" for x in (sop.get("ideas") or [])] + [""]
    if machine_block:
        clean = {k: v for k, v in sop.items() if k not in ("framework_ok", "framework_problems", "steps_count", "active", "updated_at")}
        lines += ["", "<!-- machine block · do not edit · lets the engine restore anything you did not touch -->", f"<!-- moe-sop-json: {json.dumps(clean, default=str)} -->", ""]
    return "\n".join(lines)


# ── Markdown → SOP ────────────────────────────────────────────────────────────
def _parse_kv_block(lines: List[str]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for l in lines:
        m = re.match(r"^\s*[-*•]\s*([a-zA-Z_ ]+?)\s*:\s*(.*)$", l)
        if m:
            out[m.group(1).strip().lower().replace(" ", "_")] = m.group(2).strip()
    return out


def _list(v: Optional[str]) -> List[str]:
    return [x.strip() for x in re.split(r"\s*;\s*", v or "") if x.strip()]


def _num(v: Optional[str], cast=float):
    try:
        if v in (None, "", "—", "None"):
            return None
        f = float(str(v).strip())
        if cast is int or f.is_integer():
            return int(f)
        return f
    except Exception:
        return None


def parse_sop_markdown(text: str) -> Dict[str, Any]:
    base: Dict[str, Any] = {}
    m = MACHINE_RE.search(text)
    if m and m.group(1) == "sop":
        try:
            base = json.loads(m.group(2))
        except Exception:
            base = {}
    body = MACHINE_RE.sub("", text)
    sections: Dict[str, List[str]] = {}; cur = "_head"; sections[cur] = []
    for line in body.splitlines():
        h = re.match(r"^##\s+(.+?)\s*$", line)
        if h:
            cur = h.group(1).strip().lower(); sections.setdefault(cur, []); continue
        sections[cur].append(line)
    spec = dict(base)
    t = re.search(r"^#\s*SOP:\s*(.+)$", body, re.M)
    if t:
        spec["name"] = t.group(1).strip()
    meta = re.search(r"^id:\s*(\S+)\s*\|\s*version:\s*(\d+)\s*\|\s*type:\s*(\S+)\s*\|\s*transition:\s*(\S+)", body, re.M)
    if meta:
        spec.setdefault("id", meta.group(1)); spec["campaign_type"] = meta.group(3); spec["transition"] = meta.group(4)
    def para(name):
        ls = [l for l in sections.get(name, []) if not l.startswith(">")]
        return "\n".join(ls).strip()
    if "objective" in sections:
        spec["objective"] = para("objective")
    if "who" in sections:
        spec["user"] = para("who")
    if "audience" in sections:
        kv = _parse_kv_block(sections["audience"]); aud = dict(spec.get("audience") or {})
        for k in ("segment_family", "description"):
            if k in kv:
                aud[k] = kv[k]
        if "min_reach" in kv:
            aud["min_reach"] = _num(kv["min_reach"], int) or 0
        if "exclusions" in kv:
            aud["exclusions"] = _list(kv["exclusions"])
        if "jurisdictions_excluded" in kv:
            aud["jurisdictions_excluded"] = _list(kv["jurisdictions_excluded"])
        spec["audience"] = aud
    if "steps" in sections:
        steps = []; cur_step = None
        for l in sections["steps"]:
            h = re.match(r"^###\s*Step\s*\d+\s*·\s*day\s*(\d+)\s*·\s*([^·]+?)\s*·\s*([^\s]+)\s*IST", l, re.I)
            if h:
                cur_step = {"day": int(h.group(1)), "channel": h.group(2).strip().lower(), "send_time_ist": None if h.group(3).strip() in ("—", "-") else h.group(3).strip()}
                steps.append(cur_step); continue
            if cur_step is not None:
                kv = _parse_kv_block([l])
                for k, v in kv.items():
                    if k in ("purpose", "copy_brief", "condition"):
                        cur_step[k] = v or None
                    elif k == "ttl_hours":
                        cur_step["ttl_hours"] = _num(v, int)
        if steps:
            for st in steps:
                st.setdefault("purpose", ""); st.setdefault("copy_brief", "")
                if st.get("condition") in ("", None):
                    st.pop("condition", None)
                if st.get("ttl_hours") is None:
                    st.pop("ttl_hours", None)
            spec["steps"] = steps
    if "cadence & limits" in sections:
        kv = _parse_kv_block(sections["cadence & limits"]); fr = dict(spec.get("frequency") or {})
        if "duration_days" in kv:
            spec["duration_days"] = _num(kv["duration_days"], int)
        if "cadence" in kv:
            fr["cadence"] = kv["cadence"]
        if "max_messages_per_user_per_week" in kv:
            fr["max_messages_per_user_per_week"] = _num(kv["max_messages_per_user_per_week"], int)
        if "holdout_pct" in kv:
            spec["holdout_pct"] = _num(kv["holdout_pct"])
        spec["frequency"] = fr
    if "measurement" in sections:
        kv = _parse_kv_block(sections["measurement"])
        for k in ("primary_kpi", "target", "guardrail_metric"):
            if k in kv:
                spec[k] = kv[k]
        if "measurement_window_days" in kv:
            spec["measurement_window_days"] = _num(kv["measurement_window_days"], int)
        if "kill_criteria" in kv:
            spec["kill_criteria"] = _list(kv["kill_criteria"])
    if "compliance" in sections:
        kv = _parse_kv_block(sections["compliance"]); comp = dict(spec.get("compliance") or {})
        if "disclaimer_channels" in kv:
            comp["disclaimer_channels"] = _list(kv["disclaimer_channels"])
        if "banned_angles" in kv:
            comp["banned_angles"] = _list(kv["banned_angles"])
        spec["compliance"] = comp
    if "ideas to test" in sections:
        ideas = [re.sub(r"^\s*[-*•]\s*", "", l).strip() for l in sections["ideas to test"] if re.match(r"^\s*[-*•]\s+\S", l)]
        if ideas or base.get("ideas"):
            spec["ideas"] = ideas
    return spec


# ── minimal DOCX writer / reader (no external libraries) ─────────────────────
def _docx_paragraph(text: str) -> str:
    style = ""; size = 22; bold = False
    if text.startswith("# "):
        text = text[2:]; size = 36; bold = True
    elif text.startswith("## "):
        text = text[3:]; size = 28; bold = True
    elif text.startswith("### "):
        text = text[4:]; size = 24; bold = True
    mono = text.startswith("<!--")
    b_tag = "<w:b/>" if bold else ""
    color = '<w:color w:val="999999"/>' if mono else ""
    font = "Courier New" if mono else "Calibri"
    rpr = '<w:rPr>%s<w:sz w:val="%d"/>%s<w:rFonts w:ascii="%s" w:hAnsi="%s"/></w:rPr>' % (b_tag, 12 if mono else size, color, font, font)
    return '<w:p>%s<w:r>%s<w:t xml:space="preserve">%s</w:t></w:r></w:p>' % (style, rpr, escape(text))


def markdown_to_docx(md: str, title: str = "document") -> bytes:
    paras = "".join(_docx_paragraph(l) for l in md.splitlines())
    document = f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>{paras}<w:sectPr/></w:body></w:document>'
    ct = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/><Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/></Types>'
    rels = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/></Relationships>'
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    core = f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><dc:title>{escape(title)}</dc:title><dc:creator>MoEngage Engine</dc:creator><dcterms:created xsi:type="dcterms:W3CDTF">{now}</dcterms:created></cp:coreProperties>'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ct); z.writestr("_rels/.rels", rels); z.writestr("word/document.xml", document); z.writestr("docProps/core.xml", core)
    return buf.getvalue()


def docx_to_text(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        xml = z.read("word/document.xml").decode("utf-8", errors="replace")
    paras = re.findall(r"<w:p[ >].*?</w:p>", xml, re.S)
    out = []
    for p in paras:
        texts = re.findall(r"<w:t[^>]*>(.*?)</w:t>", p, re.S)
        line = "".join(texts)
        line = line.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&").replace("&quot;", '"').replace("&apos;", "'")
        out.append(line)
    return "\n".join(out)


# ── experiments (proposals) ───────────────────────────────────────────────────
def experiment_markdown(p: Dict[str, Any], machine_block: bool = True) -> str:
    pl = p.get("payload") or {}; goal = pl.get("goal") or {}; ice = pl.get("ice") or {}
    lines = [f"# Experiment #{p.get('id')}: {p.get('title', '')}", "", f"kind: {p.get('kind')} | status: {p.get('status')} | created: {str(p.get('created_at') or '')[:16]} | by: {p.get('created_by')}", "",
             "> Edit the rationale, the goal values, the variants (title / body / cta) or the schedule. Upload back on the Ideas board (Upload revision) — the change is recorded on the proposal and still needs approval.", "",
             "## Rationale", p.get("rationale") or "", ""]
    if ice:
        lines += ["## ICE", _kv("impact", ice.get("impact")), _kv("confidence", ice.get("confidence")), _kv("ease", ice.get("ease")), _kv("tagline", ice.get("tagline")), ""]
    if p.get("kind") == "create_campaign":
        lines += ["## Campaign", _kv("name", pl.get("name")), _kv("channel", pl.get("channel")), _kv("target_segment", pl.get("target_segment")), _kv("ttl_hours", pl.get("ttl_hours")), _kv("frequency_cap", pl.get("frequency_cap")), _kv("exclusions", pl.get("exclusions") or []), ""]
        lines += ["## Goal"] + [_kv(k, goal.get(k)) for k in ("transition", "hypothesis", "primary_kpi", "target", "guardrail_metric", "control_group_pct", "measurement_window_days", "kill_criteria", "suppressions")] + [""]
        lines += ["## Variants"]
        for i, v in enumerate(pl.get("variants") or [], 1):
            lines += [f"### Variant {i} · {v.get('label', '')}", _kv("title", v.get("title")), _kv("body", v.get("body")), _kv("cta", v.get("cta")), ""]
        sch = pl.get("schedule") or {}
        lines += ["## Schedule", _kv("date", sch.get("date")), _kv("time_ist", sch.get("time_ist")), _kv("condition", sch.get("condition")), ""]
    else:
        lines += ["## Payload", "```json", json.dumps(pl, indent=1, default=str)[:6000], "```", ""]
    if p.get("comments"):
        lines += ["## Comments"] + [f"- {c.get('actor') or c.get('author') or ''} ({str(c.get('created_at') or '')[:16]}): {c.get('text')}" for c in p["comments"][:12]] + [""]
    if machine_block:
        lines += ["", "<!-- machine block · do not edit -->", f"<!-- moe-experiment-json: {json.dumps({'id': p.get('id'), 'kind': p.get('kind'), 'payload': pl, 'rationale': p.get('rationale')}, default=str)} -->", ""]
    return "\n".join(lines)


def parse_experiment_markdown(text: str) -> Dict[str, Any]:
    base: Dict[str, Any] = {}
    m = MACHINE_RE.search(text)
    if m and m.group(1) == "experiment":
        try:
            base = json.loads(m.group(2))
        except Exception:
            base = {}
    body = MACHINE_RE.sub("", text)
    sections: Dict[str, List[str]] = {}; cur = "_head"; sections[cur] = []
    for line in body.splitlines():
        h = re.match(r"^##\s+(.+?)\s*$", line)
        if h:
            cur = h.group(1).strip().lower(); sections.setdefault(cur, []); continue
        sections[cur].append(line)
    pid = None
    t = re.search(r"^#\s*Experiment\s*#(\d+)", body, re.M)
    if t:
        pid = int(t.group(1))
    changes: Dict[str, Any] = {}
    if "rationale" in sections:
        r = "\n".join(l for l in sections["rationale"] if not l.startswith(">")).strip()
        if r:
            changes["rationale"] = r
    pl: Dict[str, Any] = dict(base.get("payload") or {})
    if "campaign" in sections:
        kv = _parse_kv_block(sections["campaign"])
        for k in ("name", "channel", "target_segment", "frequency_cap"):
            if k in kv:
                pl[k] = kv[k]
        if "ttl_hours" in kv:
            pl["ttl_hours"] = _num(kv["ttl_hours"], int)
        if "exclusions" in kv:
            pl["exclusions"] = _list(kv["exclusions"])
    if "goal" in sections:
        kv = _parse_kv_block(sections["goal"]); goal = dict(pl.get("goal") or {})
        for k in ("transition", "hypothesis", "primary_kpi", "target", "guardrail_metric", "kill_criteria"):
            if k in kv:
                goal[k] = kv[k]
        if "control_group_pct" in kv:
            goal["control_group_pct"] = _num(kv["control_group_pct"])
        if "measurement_window_days" in kv:
            goal["measurement_window_days"] = _num(kv["measurement_window_days"], int)
        if "suppressions" in kv:
            goal["suppressions"] = _list(kv["suppressions"])
        pl["goal"] = goal
    if "variants" in sections:
        variants = []; cur_v = None
        for l in sections["variants"]:
            h = re.match(r"^###\s*Variant\s*\d+\s*·\s*(.*)$", l)
            if h:
                cur_v = {"label": h.group(1).strip()}; variants.append(cur_v); continue
            if cur_v is not None:
                for k, v in _parse_kv_block([l]).items():
                    if k in ("title", "body", "cta"):
                        cur_v[k] = v
        if variants:
            pl["variants"] = variants
    if "schedule" in sections:
        kv = _parse_kv_block(sections["schedule"]); sch = dict(pl.get("schedule") or {})
        for k in ("date", "time_ist", "condition"):
            if k in kv:
                sch[k] = kv[k] or None
        pl["schedule"] = sch
    if "ice" in sections:
        kv = _parse_kv_block(sections["ice"]); ice = dict(pl.get("ice") or {})
        for k in ("impact", "confidence", "ease"):
            if k in kv:
                ice[k] = _num(kv[k], int)
        if "tagline" in kv:
            ice["tagline"] = kv["tagline"]
        pl["ice"] = ice
    if pl:
        changes["payload"] = pl
    return {"id": pid or base.get("id"), "kind": base.get("kind"), "changes": changes}


# ── diff helper ───────────────────────────────────────────────────────────────
def diff(a: Any, b: Any, path: str = "") -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            out += diff(a.get(k), b.get(k), f"{path}.{k}" if path else k)
    elif isinstance(a, list) and isinstance(b, list):
        if a != b:
            if all(not isinstance(x, (dict, list)) for x in a + b):
                out.append({"path": path, "from": a, "to": b})
            else:
                for i in range(max(len(a), len(b))):
                    out += diff(a[i] if i < len(a) else None, b[i] if i < len(b) else None, f"{path}[{i}]")
    elif a != b and not (a in (None, "") and b in (None, "")):
        out.append({"path": path, "from": a, "to": b})
    return out


# ── bundles ───────────────────────────────────────────────────────────────────
def sop_index_csv(sops: List[Dict[str, Any]], india: Optional[Dict[str, Any]] = None) -> str:
    fit = {x["id"]: x for x in ((india or {}).get("sops") or [])}
    buf = io.StringIO(); w = csv.writer(buf)
    w.writerow(["id", "name", "version", "type", "transition", "segment_family", "steps", "channels", "duration_days", "per_week", "holdout_pct", "primary_kpi", "target", "india_fit_score", "india_fit_status", "objective"])
    for s in sops:
        chans = " / ".join(sorted({str(st.get("channel")) for st in s.get("steps") or [] if not str(st.get("purpose", "")).startswith("(internal)")}))
        f = fit.get(s["id"]) or {}
        w.writerow([s["id"], s["name"], s.get("version"), s.get("campaign_type"), s.get("transition"), (s.get("audience") or {}).get("segment_family"), len(s.get("steps") or []), chans, s.get("duration_days"), (s.get("frequency") or {}).get("max_messages_per_user_per_week"), s.get("holdout_pct"), s.get("primary_kpi"), s.get("target"), f.get("score"), f.get("status"), (s.get("objective") or "")[:300]])
    return buf.getvalue()


def sop_bundle_zip(sops: List[Dict[str, Any]], india: Optional[Dict[str, Any]] = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("README.md", "# SOP library export\n\nOne folder per SOP: `<id>.md` (edit this one), `<id>.docx` (same content for Word), `<id>.json` (machine copy). `index.csv` lists everything. Edited .md/.docx/.json files upload back one at a time in SOP Library → Upload edited SOP.\n")
        z.writestr("index.csv", sop_index_csv(sops, india))
        z.writestr("library.json", json.dumps(sops, indent=1, default=str))
        z.writestr("library.md", "\n\n---\n\n".join(sop_markdown(s, machine_block=False) for s in sops))
        for s in sops:
            md = sop_markdown(s)
            z.writestr(f"sops/{s['id']}/{s['id']}.md", md)
            z.writestr(f"sops/{s['id']}/{s['id']}.docx", markdown_to_docx(md, s.get("name", s["id"])))
            z.writestr(f"sops/{s['id']}/{s['id']}.json", json.dumps({k: v for k, v in s.items() if k not in ("framework_ok", "framework_problems", "steps_count", "active", "updated_at")}, indent=1, default=str))
    return buf.getvalue()


def parse_upload(filename: str, data: bytes, kind: str = "sop") -> Tuple[Dict[str, Any], str]:
    """Returns (parsed_spec_or_changes, detected_format)."""
    name = (filename or "").lower()
    if name.endswith(".json"):
        obj = json.loads(data.decode("utf-8", errors="replace"))
        return (obj, "json")
    if name.endswith(".docx"):
        text = docx_to_text(data); fmt = "docx"
    else:
        text = data.decode("utf-8", errors="replace"); fmt = "md"
    return ((parse_sop_markdown(text) if kind == "sop" else parse_experiment_markdown(text)), fmt)
