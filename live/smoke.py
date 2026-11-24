"""Smoke-test the migrated agent on the 20 scripted calls (agent/caller_scripts.md).

  python3 -m migrate smoke --since 2026-11-24

Pulls the migrated agent's conversations from Phonic in start order (call 1 = script 1), grades
each on the 10 screening criteria with Phonic's own eval prompts (created on first run), reads the
outcome with the migrated extraction schema, and writes out/smoke.json plus a summary.
Verdicts: successful -> pass, unsuccessful -> fail, undecided -> na, error -> error.
An "error" verdict stops the summary until the evals are re-run.
"""

import json
from pathlib import Path

from screening.criteria import CRITERIA
from screening.scorecard import summarize
from .phonic import call, list_conversations, project_id

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out"
PRICE = 0.065  # Phonic Pro list price per minute, phonic.ai/pricing (2026-11-20)

# Expected approve / coach-call outcome per script number (agent/caller_scripts.md)
EXPECTED = {1: "approve", 2: "coach_call", 3: "approve", 4: "coach_call", 5: "approve",
            6: "approve", 7: "approve", 8: "approve", 9: "approve", 10: None,
            11: "approve", 12: "approve", 13: "approve", 14: None, 15: "approve",
            16: "approve", 17: "coach_call", 18: "approve", 19: "approve", 20: "approve"}
VERDICT = {"successful": "pass", "unsuccessful": "fail", "undecided": "na", "error": "error"}


def criteria_prompts(ids):
    prompts = ids.setdefault("criteria_prompts", {})
    pid = None
    for c in CRITERIA:
        if c.id not in prompts:
            pid = pid or project_id("main")
            prompts[c.id] = call("POST", f"/projects/{pid}/conversation_eval_prompts",
                                 {"name": f"screener-{c.id}", "prompt": c.text})["id"]
    (OUT / "ids.json").write_text(json.dumps(ids, indent=1))
    return prompts


def record(conv, ids, prompts, n):
    cid = conv["id"]
    verdicts = {k: VERDICT.get(call("POST", f"/conversations/{cid}/evals", {"prompt_id": p})["evaluation"]["result"], "na")
                for k, p in prompts.items()}
    ext = call("POST", f"/conversations/{cid}/extractions", {"schema_id": ids["schema_id"]})["result"]
    done = bool(ext.get("screen_completed"))
    analysis = conv.get("analysis") or {}
    return {"call_id": f"S{n:02d}", "phonic_conversation_id": cid, "worker": f"S{n:02d}",
            "outcome": "completed" if done else "abandoned",
            "duration_s": round((conv.get("duration_ms") or 0) / 1000),
            "latencies_ms": [round(x) for x in analysis.get("latencies_ms", [])],
            "decision": ext.get("recommendation") if done and ext.get("recommendation") in ("approve", "coach_call") else None,
            "expected_decision": EXPECTED.get(n), "extraction": ext, "verdicts": verdicts,
            "transcript": [{"role": "agent" if it["role"] == "assistant" else "worker",
                            "text": it.get("post_call_transcript") or it.get("live_transcript") or ""}
                           for it in conv.get("items", [])]}


def main(since):
    ids = json.loads((OUT / "ids.json").read_text())
    slug = (ids["agent"].get("agent") or ids["agent"]).get("name")
    prompts = criteria_prompts(ids)
    convs = sorted((c for c in list_conversations(started_at_min=since)
                    if ((c.get("agent") or {}).get("name") or (c.get("agent") or {}).get("slug")) == slug),
                   key=lambda c: c.get("started_at") or "")
    calls = [record(c, ids, prompts, i + 1) for i, c in enumerate(convs[:20])]
    errors = [(c["call_id"], k) for c in calls for k, v in c["verdicts"].items() if v == "error"]
    if errors:
        raise SystemExit(f"Phonic's grader returned 'error' on {len(errors)} verdicts; re-run: {errors[:10]}")
    s = summarize(calls, PRICE, {c["call_id"]: c["verdicts"] for c in calls})
    out = {"agent": slug, "since": since, "price_per_min": PRICE, "summary": s, "calls": calls,
           "note": "Live calls to the migrated Phonic-1 agent. The worker was played by the author from scripted personas."}
    (OUT / "smoke.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))
    print(f"{len(calls)} calls on {slug}: {s['completed']} finished, criteria {s['criteria_pass']}/{s['criteria_judged']}, "
          f"{len(s['critical_failures'])} critical, decisions {s['decision_agree']}/{s['decision_total']}, "
          f"p90 latency {s['latency_p90_ms']} ms, ${s['cost_per_completed_screen']} per finished screen")
