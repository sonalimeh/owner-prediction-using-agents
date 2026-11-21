"""Score a propose run against a truth file. Reads the truth file and the proposals table; writes nothing.

Every truth orphan lands in exactly one bucket (see bucket()). Eval never reads the catalog, so
changes or apply after the run do not move the numbers. Proposals for assets the selected truth
rows do not list are counted but not scored.
"""

import json
import math
from collections import Counter

from owner_agent.proposing.rules import GROUPS
from owner_agent.review import listing

SPLITS = ("calibration", "held_out")
CORRECT, WRONG, SHOULD_DECLINE = "correct_owner", "wrong_person", "should_have_declined"
DECLINED, MISSED, MISSING = "correct_decline", "missed", "missing"
BUCKETS = (CORRECT, WRONG, SHOULD_DECLINE, DECLINED, MISSED, MISSING)
NAMED = (CORRECT, WRONG, SHOULD_DECLINE)
THRESHOLDS = [round(0.05 * i, 2) for i in range(20)]  # 0.00 ... 0.95
MIN_CONSISTENCY_RUNS = 3


class EvalError(Exception):
    pass


def load_truth(path, split=None):
    rows = {}
    try:
        f = open(path, encoding="utf-8")
    except OSError as e:
        raise EvalError(f"cannot read truth file: {e}") from None
    with f:
        for lineno, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError as e:
                raise EvalError(f"{path} line {lineno}: {e}") from None
            if r.get("record_type") != "truth":
                raise EvalError(f"{path} line {lineno}: not a truth record")
            absent = {"asset_id", "owner", "case", "split"} - set(r)
            if absent:
                raise EvalError(f"{path} line {lineno}: missing {', '.join(sorted(absent))}")
            if r["split"] not in SPLITS:
                raise EvalError(f"{path} line {lineno}: unknown split {r['split']!r}")
            if r["asset_id"] in rows:
                raise EvalError(f"{path} line {lineno}: duplicate asset {r['asset_id']}")
            rows[r["asset_id"]] = r
    return [r for r in rows.values() if split is None or r["split"] == split]


def run_proposals(conn, run_id):
    run = listing.resolve_run(conn, run_id)
    rows = conn.execute(
        "SELECT asset_id, proposed_owner, confidence, confidence_group, reason, model, cost_usd, latency_ms "
        "FROM proposals WHERE run_id = ?",
        (run["run_id"],),
    )
    return dict(run), {r["asset_id"]: dict(r) for r in rows}


def bucket(proposal, truth_owner):
    if proposal is None:
        return MISSING
    owner = proposal["proposed_owner"]
    if owner is None:
        return DECLINED if truth_owner is None else MISSED
    if truth_owner is None:
        return SHOULD_DECLINE
    return CORRECT if owner == truth_owner else WRONG


def _ratio(n, d, digits=4):
    return round(n / d, digits) if d else None


def _percentile(values, q):
    """Nearest-rank percentile."""
    if not values:
        return None
    s = sorted(values)
    return s[max(math.ceil(q * len(s)) - 1, 0)]


def _groups(items):
    cases = sorted({i["case"] for i in items})
    return [("overall", items)] + [(c, [i for i in items if i["case"] == c]) for c in cases]


def summarize(scored):
    b = Counter(s["bucket"] for s in scored)
    props = [s["proposal"] for s in scored if s["proposal"]]
    named = sum(b[k] for k in NAMED)
    truth_none = sum(s["truth"] is None for s in scored)
    cost = sum(p["cost_usd"] for p in props)
    latencies = [p["latency_ms"] for p in props]
    model_calls = sum(p["model"] is not None for p in props)
    return {
        "orphans": len(scored),
        "proposals": len(props),
        "named": named,
        **{k: b[k] for k in BUCKETS},
        "truth_none": truth_none,
        "precision": _ratio(b[CORRECT], named),
        "coverage": _ratio(named, len(scored)),
        "max_coverage": _ratio(len(scored) - truth_none, len(scored)),
        "correct_declines": _ratio(b[DECLINED], truth_none),
        "model_calls": model_calls,
        "no_model_share": _ratio(len(props) - model_calls, len(props)),
        "cost_usd": round(cost, 6),
        "cost_per_proposal": _ratio(cost, len(props), 6),
        "p50_latency_ms": _percentile(latencies, 0.50),
        "p95_latency_ms": _percentile(latencies, 0.95),
    }


def thresholds(scored):
    """Precision and coverage if only owner proposals with confidence >= x were approved."""
    named = [s for s in scored if s["bucket"] in NAMED]
    rows = []
    for x in THRESHOLDS:
        kept = [s for s in named if s["proposal"]["confidence"] >= x]
        right = sum(s["bucket"] == CORRECT for s in kept)
        rows.append({
            "x": x, "approved": len(kept), "correct": right,
            "precision": _ratio(right, len(kept)), "coverage": _ratio(len(kept), len(scored)),
        })
    return rows


def reliability(scored):
    """Owner proposals grouped by confidence decile, with how many were actually right."""
    bins = {}
    for s in scored:
        if s["bucket"] in NAMED:
            conf = s["proposal"]["confidence"]
            bins.setdefault(min(int(round(conf * 10, 6)), 9), []).append(s)
    return [
        {
            "bin": f"{lo / 10:.1f}-{(lo + 1) / 10:.1f}",
            "proposals": len(items),
            "correct": sum(s["bucket"] == CORRECT for s in items),
            "accuracy": _ratio(sum(s["bucket"] == CORRECT for s in items), len(items)),
            "mean_confidence": _ratio(sum(s["proposal"]["confidence"] for s in items), len(items)),
        }
        for lo, items in sorted(bins.items())
    ]


def confidence_groups(scored):
    """Each confidence group: how often its answers were right, the confidence that measures
    ((right + 1) / (total + 2), as rules.load_confidence expects), and the confidence the run recorded."""
    by_group = {}
    for s in scored:
        if s["proposal"]:
            by_group.setdefault(s["proposal"]["confidence_group"], []).append(s)
    order = list(GROUPS)
    rows = {}
    for g in sorted(by_group, key=lambda g: (order.index(g) if g in order else len(order), g)):
        items = by_group[g]
        right = sum(s["proposal"]["proposed_owner"] == s["truth"] for s in items)
        rows[g] = {
            "total": len(items), "right": right, "accuracy": _ratio(right, len(items)),
            "measured": round((right + 1) / (len(items) + 2), 2),
            "recorded": _ratio(sum(s["proposal"]["confidence"] for s in items), len(items)),
        }
    return rows


def calibration_file(result, existing=None, target=0.95):
    """confidence.json from a calibration run: the groups this run answered in, and the existing file's others.

    Merging lets one file hold both modes: a baseline run measures the unread-text groups, a model run
    the model-text groups, and both measure the rest.
    """
    if result["split"] != "calibration":
        raise EvalError("--calibrate needs --split calibration: confidences are measured on calibration only")
    if "confidence_groups" not in result:
        raise EvalError("nothing to calibrate: no proposals were scored")
    groups = dict((existing or {}).get("groups", {}))
    for g, row in result["confidence_groups"].items():
        groups[g] = {"right": row["right"], "total": row["total"], "confidence": row["measured"],
                     "run_id": result["run"]["run_id"]}
    order = list(GROUPS)
    return {
        "version": 1, "split": "calibration", "threshold": target,
        "groups": {g: groups[g] for g in sorted(groups, key=lambda g: (order.index(g) if g in order else len(order), g))},
    }


def consistency(truth, runs):
    """Orphans with the same answer in every run, among orphans every run proposed for."""
    shared = [t for t in truth if all(t["asset_id"] in r for r in runs)]
    same = {t["asset_id"] for t in shared if len({r[t["asset_id"]]["proposed_owner"] for r in runs}) == 1}
    groups = {}
    for name, items in _groups(shared):
        n = sum(t["asset_id"] in same for t in items)
        groups[name] = {"compared": len(items), "same": n, "consistency": _ratio(n, len(items))}
    return {"skipped": len(truth) - len(shared), "groups": groups}


def evaluate(conn, truth_path, run_id, compare=(), split=None):
    truth = load_truth(truth_path, split)
    run, props = run_proposals(conn, run_id)
    truth_ids = {t["asset_id"] for t in truth}
    result = {
        "run": run, "split": split or "all", "truth_orphans": len(truth),
        "proposals_not_scored": len(set(props) - truth_ids), "notes": [],
    }
    if not truth:
        result["notes"].append(f"no truth rows with split={split}; nothing to score")
        return result

    scored = [
        {"asset_id": t["asset_id"], "case": t["case"], "truth": t["owner"],
         "proposal": props.get(t["asset_id"]), "bucket": bucket(props.get(t["asset_id"]), t["owner"])}
        for t in truth
    ]
    result.update(
        groups={name: summarize(items) for name, items in _groups(scored)},
        thresholds=thresholds(scored),
        reliability=reliability(scored),
        confidence_groups=confidence_groups(scored),
        wrong=[
            {"asset_id": s["asset_id"], "case": s["case"], "bucket": s["bucket"],
             "proposed": s["proposal"]["proposed_owner"], "truth": s["truth"],
             "confidence": s["proposal"]["confidence"], "reason": s["proposal"]["reason"]}
            for s in scored if s["bucket"] in (WRONG, SHOULD_DECLINE)
        ],
        missing=[s["asset_id"] for s in scored if s["bucket"] == MISSING],
    )
    if result["missing"]:
        result["notes"].append(f"{len(result['missing'])} truth orphan(s) have no proposal in the run: a bug")

    if compare:
        others = [run_proposals(conn, r) for r in compare]
        result["compared_runs"] = [run["run_id"]] + [r["run_id"] for r, _ in others]
        result["consistency"] = consistency(truth, [props] + [p for _, p in others])
        if len(result["compared_runs"]) < MIN_CONSISTENCY_RUNS:
            result["notes"].append(f"consistency needs at least {MIN_CONSISTENCY_RUNS} runs")
        c = result["consistency"]
        if c["skipped"]:
            result["notes"].append(
                f"consistency compares the {c['groups']['overall']['compared']} orphans every run proposed for; "
                f"{c['skipped']} skipped"
            )
    return result


def _fmt(v, digits=3):
    if v is None:
        return "n/a"
    return f"{v:.{digits}f}" if isinstance(v, float) else str(v)


def _table(header, body):
    widths = [max(len(str(c)) for c in col) for col in zip(header, *body)]
    return ["  ".join(str(c).ljust(w) for c, w in zip(cells, widths)).rstrip() for cells in [header, *body]]


def report(result):
    run = result["run"]
    lines = [
        f"run {run['run_id']}  mode={run['mode']}  model={run['model'] or '-'}  split={result['split']}  "
        f"truth orphans={result['truth_orphans']}  proposals not scored={result['proposals_not_scored']}",
    ]
    lines += [f"note: {n}" for n in result["notes"]]
    if "groups" not in result:
        return lines

    groups = result["groups"].items()
    lines += ["", "Quality (MAX_COV is the share of orphans with a true owner: the coverage a perfect run would have)"]
    lines += _table(
        ("GROUP", "ORPHANS", "NAMED", "CORRECT", "WRONG", "SHOULD_DECLINE", "MISSED", "MISSING",
         "PRECISION", "COVERAGE", "MAX_COV", "CORRECT_DECLINES"),
        [
            (name, g["orphans"], g["named"], g[CORRECT], g[WRONG], g[SHOULD_DECLINE], g[MISSED], g[MISSING],
             _fmt(g["precision"]), _fmt(g["coverage"]), _fmt(g["max_coverage"]),
             f"{g[DECLINED]}/{g['truth_none']} = {_fmt(g['correct_declines'])}" if g["truth_none"] else "-")
            for name, g in groups
        ],
    )
    lines += ["", "Cost and latency"]
    lines += _table(
        ("GROUP", "PROPOSALS", "MODEL_CALLS", "NO_MODEL", "COST_USD", "COST/PROPOSAL", "P50_MS", "P95_MS"),
        [
            (name, g["proposals"], g["model_calls"], _fmt(g["no_model_share"]), _fmt(g["cost_usd"], 6),
             _fmt(g["cost_per_proposal"], 6), _fmt(g["p50_latency_ms"], 1), _fmt(g["p95_latency_ms"], 1))
            for name, g in groups
        ],
    )
    lines += ["", "Thresholds (approve only owner proposals with confidence >= x)"]
    lines += _table(
        ("X", "APPROVED", "CORRECT", "PRECISION", "COVERAGE"),
        [(f"{t['x']:.2f}", t["approved"], t["correct"], _fmt(t["precision"]), _fmt(t["coverage"]))
         for t in result["thresholds"]],
    )
    lines += ["", "Reliability (does confidence match how often the owner is right?)"]
    lines += _table(
        ("CONFIDENCE", "PROPOSALS", "CORRECT", "ACCURACY", "MEAN_CONF"),
        [(r["bin"], r["proposals"], r["correct"], _fmt(r["accuracy"]), _fmt(r["mean_confidence"]))
         for r in result["reliability"]],
    ) if result["reliability"] else ["(no owner proposals)"]

    lines += ["", "Confidence groups (RECORDED is what propose used; MEASURED is what this run supports)"]
    lines += _table(
        ("GROUP", "ANSWERS", "RIGHT", "ACCURACY", "RECORDED", "MEASURED"),
        [(g, r["total"], r["right"], _fmt(r["accuracy"]), _fmt(r["recorded"], 2), _fmt(r["measured"], 2))
         for g, r in result["confidence_groups"].items()],
    )

    if "consistency" in result:
        lines += ["", f"Consistency across {', '.join(result['compared_runs'])}"]
        lines += _table(
            ("GROUP", "COMPARED", "SAME", "CONSISTENCY"),
            [(name, c["compared"], c["same"], _fmt(c["consistency"]))
             for name, c in result["consistency"]["groups"].items()],
        )

    lines += ["", f"Wrong proposals ({len(result['wrong'])})"]
    lines += _table(
        ("ASSET", "CASE", "PROPOSED", "TRUTH", "CONF", "REASON"),
        [(w["asset_id"], w["case"], w["proposed"], w["truth"] or "none", _fmt(w["confidence"]), w["reason"])
         for w in result["wrong"]],
    ) if result["wrong"] else ["(none)"]
    if result["missing"]:
        lines += ["", f"Missing proposals, a bug ({len(result['missing'])}): {', '.join(result['missing'])}"]
    return lines
