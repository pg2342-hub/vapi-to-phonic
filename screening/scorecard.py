"""Scorecard for a set of screening calls: finish rate, criteria passed, critical failures,
approve / coach-call agreement, latency and cost per finished screen."""

import math
from statistics import median

from .criteria import CRITERIA, FAIL, PASS, grade


def p90(values):
    if not values:
        return None
    s = sorted(values)  # nearest-rank percentile
    k = max(0, math.ceil(0.9 * len(s)) - 1)
    return s[k]


def summarize(calls, price_per_min, verdicts=None):
    """Metrics for one setup. `verdicts` maps call_id -> {criterion: verdict};
    if missing, the rule grader is used (synthetic mode)."""
    verdicts = verdicts or {c["call_id"]: grade(c) for c in calls}
    n = len(calls)
    completed = [c for c in calls if c["outcome"] == "completed"]
    lat = [x for c in calls for x in c["latencies_ms"]]
    minutes = sum(c["duration_s"] for c in calls) / 60
    cost = minutes * price_per_min
    passes = fails = 0
    per_crit = {}
    failing_calls = {}
    for cr in CRITERIA:
        p = f = na = 0
        for c in calls:
            v = verdicts[c["call_id"]][cr.id]
            if v == PASS:
                p += 1
            elif v == FAIL:
                f += 1
                failing_calls.setdefault(cr.id, []).append(c["call_id"])
            else:
                na += 1
        per_crit[cr.id] = {"pass": p, "fail": f, "na": na}
        passes += p
        fails += f
    critical = [(cr.id, cid) for cr in CRITERIA if cr.critical for cid in failing_calls.get(cr.id, [])]
    decided = [c for c in completed if c.get("expected_decision") is not None]
    agree = sum(1 for c in decided if c["decision"] == c["expected_decision"])
    return {
        "calls": n,
        "completed": len(completed),
        "completion_rate": len(completed) / n if n else 0,
        "criteria_pass": passes,
        "criteria_judged": passes + fails,
        "criteria_pass_rate": passes / (passes + fails) if passes + fails else 0,
        "critical_failures": critical,
        "decision_agree": agree,
        "decision_total": len(decided),
        "latency_p50_ms": round(median(lat)) if lat else None,
        "latency_p90_ms": p90(lat),
        "minutes": round(minutes, 1),
        "cost_usd": round(cost, 2),
        "cost_per_completed_screen": round(cost / len(completed), 3) if completed else None,
        "per_criterion": per_crit,
        "failing_calls": failing_calls,
    }


DECISION_BAR = 0.90
