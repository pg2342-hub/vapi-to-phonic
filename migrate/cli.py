"""Vapi to Phonic migration CLI.

  python3 -m migrate plan  examples/line_cook_screener.vapi.json   # no network: writes out/ and prints the gap report
  python3 -m migrate apply examples/line_cook_screener.vapi.json   # creates tools, agent, number, schema and evals on Phonic
  python3 -m migrate smoke --since 2026-11-24                       # grades the scripted test calls on the migrated agent

apply needs PHONIC_API_KEY (environment or ~/.config/phonic/.env). It refuses to run while the
report has "manual" items, unless --accept-manual is passed after a person has read them.
"""

import argparse
import json
import sys
from pathlib import Path

from .mapper import to_phonic

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out"
ICON = {"mapped": "ok", "adjusted": "~", "manual": "!!", "dropped": "-"}


def plan(path, phone="assign-automatically", skip_tools=()):
    vapi = json.loads(Path(path).read_text())
    agent, tools, schema, evals, report = to_phonic(vapi, phone, skip_tools)
    return {"source": str(path), "agent": agent, "tools": tools, "extraction_schema": schema,
            "eval_prompts": evals, "report": report.items, "counts": report.counts()}


def report_md(p):
    c = p["counts"]
    lines = [f"# Migration report: {p['source']}", "",
             f"Phonic agent `{p['agent']['slug']}`: {c['mapped']} mapped, {c['adjusted']} adjusted, "
             f"{c['manual']} need a person, {c['dropped']} not needed on Phonic.", "",
             "| Status | Vapi field | Phonic field | Note |", "|---|---|---|---|"]
    order = {"manual": 0, "adjusted": 1, "mapped": 2, "dropped": 3}
    for i in sorted(p["report"], key=lambda i: order[i["status"]]):
        lines.append(f"| {i['status']} | `{i['vapi']}` | {('`' + i['phonic'] + '`') if i['phonic'] else ''} | "
                     f"{i['note'].replace('|', '/')} |")
    return "\n".join(lines) + "\n"


def write_plan(p):
    OUT.mkdir(exist_ok=True)
    (OUT / "plan.json").write_text(json.dumps(p, indent=1))
    (OUT / "report.md").write_text(report_md(p))


def apply(p, accept_manual=False):
    from live.phonic import call, project_id
    manual = [i for i in p["report"] if i["status"] == "manual"]
    if manual and not accept_manual:
        sys.exit(f"{len(manual)} items need a person (see out/report.md). Re-run with --accept-manual once read.")
    ids = {"tools": {}}
    existing = {t["name"] for t in call("GET", "/tools").get("tools", [])}
    for t in p["tools"]:
        if t["name"] in existing:
            ids["tools"][t["name"]] = "exists"
            continue
        if t["type"] == "custom_webhook" and not t.get("endpoint_url"):
            sys.exit(f"tool {t['name']} has no endpoint_url")
        ids["tools"][t["name"]] = call("POST", "/tools", t)["id"]
    ids["agent"] = call("PUT", "/agents/upsert", p["agent"])
    pid = project_id("main")
    if p["extraction_schema"]:
        ids["schema_id"] = call("POST", "/extraction_schemas", p["extraction_schema"])["id"]
    ids["eval_prompts"] = {e["name"]: call("POST", f"/projects/{pid}/conversation_eval_prompts", e)["id"]
                           for e in p["eval_prompts"]}
    OUT.mkdir(exist_ok=True)
    (OUT / "ids.json").write_text(json.dumps(ids, indent=1))
    return ids


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python3 -m migrate")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("plan", "apply"):
        s = sub.add_parser(name)
        s.add_argument("config", help="Vapi assistant JSON (GET /assistant/{id} or the dashboard export)")
        s.add_argument("--phone", default="assign-automatically",
                       help='"assign-automatically", null for none, or "custom" with numbers set by hand')
        s.add_argument("--skip-tool", action="append", default=[],
                       help="leave a tool out (its Phonic name, or a type such as custom_webhook); repeatable")
        if name == "apply":
            s.add_argument("--accept-manual", action="store_true")
    s = sub.add_parser("smoke")
    s.add_argument("--since", required=True)
    a = ap.parse_args(argv)
    if a.cmd == "smoke":
        from live import smoke
        return smoke.main(a.since)
    p = plan(a.config, None if a.phone == "null" else a.phone, a.skip_tool)
    write_plan(p)
    print(report_md(p))
    if a.cmd == "apply":
        ids = apply(p, a.accept_manual)
        agent = ids["agent"].get("agent", ids["agent"])
        print("Phonic agent ready:", agent.get("name"), agent.get("phone_number") or agent.get("phone_numbers"))
