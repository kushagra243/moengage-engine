#!/usr/bin/env python3
"""
Rebuild the local MoEngage API knowledge pack from moengage.com (public docs only):
  backend/knowledge/moengage-api/catalog.json      every documented API page + endpoint
  backend/knowledge/moengage-api/specs/*.json      the OpenAPI specs (converted from YAML)
  backend/knowledge/moengage-api/OFFICIAL-SKILL.md MoEngage's own agent skill file
Run:  .venv/bin/python tools/build_api_catalog.py        (needs network; ~20 MB download)
Nothing from the workspace is sent anywhere; this only reads public documentation.
"""
from __future__ import annotations
import json, os, re, sys, datetime, collections
import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "backend", "knowledge", "moengage-api")
DOCS = "https://www.moengage.com/docs"
READ_SAFE_POST = re.compile(r"[/-](search|meta|stats|get-by-ids|executions|status|history|usage-report)$")   # export/fetch/preview return per-user data → never read-safe
# which API key (Settings → Integration) authenticates each spec
KEY_KIND = {"data": "data", "business-events": "data", "business-events-v5": "data", "cohort-audience": "segmentation", "custom-segments": "segmentation",
            "campaigns": "campaigns", "campaign-draft": "campaigns", "stats-report": "campaigns", "flows": "campaigns", "content-blocks": "campaigns",
            "email-templates-1": "campaigns", "email-templates-2": "campaigns", "push-templates": "campaigns", "sms-templates": "campaigns",
            "in-app-templates": "campaigns", "osm-templates": "campaigns", "inform": "inform", "analytics": "campaigns", "analytics-query": "campaigns",
            "cards": "data", "catalog": "data", "coupons": "campaigns", "content-apis": "campaigns", "email-subscription": "data", "subscription-categories": "data",
            "gdpr-ccpa": "data", "live-activities": "data", "locales": "campaigns", "message-archival": "campaigns", "offerings": "campaigns",
            "personalize-experience": "data", "recommendations": "data"}


def fetch(url: str) -> bytes:
    r = requests.get(url, headers={"User-Agent": "moengage-engine-catalog-builder"}, timeout=120)
    r.raise_for_status()
    return r.content


def main() -> None:
    os.makedirs(os.path.join(OUT, "specs"), exist_ok=True)
    print("fetching llms-full.txt (~17 MB)…"); full = fetch(f"{DOCS}/llms-full.txt").decode("utf-8", "replace")
    print("fetching skill.md…"); skill = fetch(f"{DOCS}/skill.md").decode("utf-8", "replace")
    stamp = datetime.date.today().isoformat()
    open(os.path.join(OUT, "OFFICIAL-SKILL.md"), "w").write(f"<!-- vendored from {DOCS}/skill.md on {stamp}; regenerate with tools/build_api_catalog.py -->\n" + skill)
    pages = re.split(r"\n(?=# [^\n]+\nSource: https://moengage\.com/docs/)", "\n" + full)
    api_pages = []
    for p in pages:
        m = re.match(r"\n?# ([^\n]+)\nSource: (https://moengage\.com/docs/api/[^\n]+)\n", p)
        if not m:
            continue
        title, url = m.group(1).strip(), m.group(2).strip()
        body = p[m.end():]
        em = re.match(r"\s*(/api/[^\s]+\.ya?ml) (get|post|put|patch|delete) (\S+)\n", body)
        ep = None
        if em:
            spec = os.path.basename(em.group(1)).rsplit(".", 1)[0]
            ep = {"spec": spec, "method": em.group(2).upper(), "path": em.group(3)}
            body = body[em.end():]
        rl = re.search(r"Rate Limit[^\n]*\n+([^\n]+)", body)
        api_pages.append({"title": title, "url": url, "category": url.split("/docs/api/")[1].split("/")[0], "endpoint": ep,
                          "rate_limit": rl.group(1).strip() if rl else None, "summary": re.sub(r"\s+", " ", body.strip())[:600]})
    specs = sorted({a["endpoint"]["spec"] for a in api_pages if a["endpoint"]})
    spec_paths = {}
    for a in api_pages:
        if a["endpoint"]:
            spec_paths.setdefault(a["endpoint"]["spec"], None)
    # spec urls are the yaml paths seen in the dump
    yaml_paths = {}
    for p in pages:
        for m in re.finditer(r"(/api/[^\s]+/([a-z0-9-]+)\.ya?ml) (?:get|post|put|patch|delete) ", p):
            yaml_paths[m.group(2)] = m.group(1)
    try:
        import yaml  # available in the project venv
    except ImportError:
        sys.exit("run with the project venv: .venv/bin/python tools/build_api_catalog.py")
    servers, ops = {}, []
    for s in specs:
        url = DOCS + yaml_paths[s]
        print("fetching", url)
        d = yaml.safe_load(fetch(url).decode("utf-8", "replace"))
        json.dump(d, open(os.path.join(OUT, "specs", f"{s}.json"), "w"), separators=(",", ":"))
        base = [x.get("url") for x in d.get("servers", [])]
        servers[s] = base
        for path, item in (d.get("paths") or {}).items():
            for m, op in item.items():
                if m.lower() not in ("get", "post", "put", "patch", "delete") or not isinstance(op, dict):
                    continue
                full_path = (base[0] if base else "https://api-{dc}.moengage.com").replace("https://api-{dc}.moengage.com", "").rstrip("/") + path
                method = m.upper()
                ops.append({"spec": s, "method": method, "path": path, "full_path": full_path, "server": base[0] if base else None,
                            "operationId": op.get("operationId"), "summary": op.get("summary"), "tags": op.get("tags") or [],
                            "key_kind": KEY_KIND.get(s, "campaigns"),
                            "read_safe": method == "GET" or (method == "POST" and bool(READ_SAFE_POST.search(path)))})
    doc_by_op = {}
    for a in api_pages:
        if a["endpoint"]:
            doc_by_op[(a["endpoint"]["spec"], a["endpoint"]["method"], a["endpoint"]["path"])] = a
    for o in ops:
        a = doc_by_op.get((o["spec"], o["method"], o["path"]))
        if a:
            o["doc_url"], o["title"], o["rate_limit"], o["doc_summary"] = a["url"], a["title"], a["rate_limit"], a["summary"][:300]
    catalog = {"built_on": stamp, "source": DOCS, "pages": api_pages, "operations": ops, "spec_servers": servers,
               "categories": dict(collections.Counter(a["category"] for a in api_pages)),
               "auth": {"scheme": "HTTP Basic: Workspace ID as username, the feature API key as password", "extra_headers": {"MOE-APPKEY": "Workspace ID (File import, Test connection, catalog, inform)"},
                        "data_centres": {f"{n:02d}": f"https://api-{n:02d}.moengage.com" for n in (1, 2, 3, 4, 5, 6)} | {"101": "https://api-101.moengage.com"}},
               "mcp": {"docs_server": "https://moengage.com/docs/mcp (documentation only; no workspace data)",
                       "workspace_server": "https://mcp.moengage.com (OAuth; runs in the AI vendor's cloud — NOT used by this engine: data would leave the machine)"}}
    json.dump(catalog, open(os.path.join(OUT, "catalog.json"), "w"), indent=1)
    print(f"pages {len(api_pages)}  operations {len(ops)}  specs {len(specs)}  → {OUT}")


if __name__ == "__main__":
    main()
