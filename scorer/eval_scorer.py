#!/usr/bin/env python3
"""Grade the rules scorer against truth.jsonl. Kept apart from scorer.py, which must never read the truth.

    python eval_scorer.py --dir fixture                      # the calibration split (the default)
    python eval_scorer.py --dir fixture --split held_out     # only for the final report

Use the calibration split while the rules are still changing. held_out and the examiners' reference set
are the final exam: look at them once, when the scorer is finished.
"""
import argparse
import collections
import json
import os

from scorer import Catalog, score_asset

ap = argparse.ArgumentParser()
ap.add_argument("--dir", required=True, help="folder with seed.jsonl and truth.jsonl")
ap.add_argument("--split", default="calibration", choices=["calibration", "held_out"])
ap.add_argument("--show", type=int, default=10, help="how many mistakes to print")
args = ap.parse_args()

cat = Catalog.from_jsonl(os.path.join(args.dir, "seed.jsonl"))
truth = [json.loads(line) for line in open(os.path.join(args.dir, "truth.jsonl"))]
rows = [t for t in truth if t["split"] == args.split]
by_case = collections.defaultdict(lambda: collections.Counter())
mistakes = []
for t in rows:
    r = score_asset(cat, t["asset_id"])
    c = by_case[t["case"]]
    c["n"] += 1
    c["proposed"] += r["owner"] is not None
    c["right_proposal"] += r["owner"] is not None and r["owner"] == t["owner"]
    c["truth_none"] += t["owner"] is None
    c["right_decline"] += t["owner"] is None and r["owner"] is None
    c["correct"] += r["owner"] == t["owner"]
    if r["owner"] != t["owner"]:
        mistakes.append((t, r))

total = collections.Counter()
for c in by_case.values():
    total.update(c)
ratio = lambda a, b: f"{a}/{b}" if b else "-"
print(f"split: {args.split}   orphans: {total['n']}")
print(f"correct: {total['correct']}/{total['n']}")
print(f"precision (proposals that are right): {ratio(total['right_proposal'], total['proposed'])}")
print(f"coverage (orphans that get a proposal): {ratio(total['proposed'], total['n'])}")
print(f"correct declines (truth none, declined): {ratio(total['right_decline'], total['truth_none'])}\n")
print(f"{'case':30s} {'correct':>9s} {'precision':>10s} {'declines':>9s}")
for case in sorted(by_case):
    c = by_case[case]
    print(f"{case:30s} {ratio(c['correct'], c['n']):>9s} {ratio(c['right_proposal'], c['proposed']):>10s} "
          f"{ratio(c['right_decline'], c['truth_none']):>9s}")
if mistakes:
    name = lambda u: cat.users[u]["name"] if u else "none"
    print(f"\nmistakes ({len(mistakes)}):")
    for t, r in mistakes[:args.show]:
        print(f"  {t['asset_id']} [{t['case']}] scorer={name(r['owner'])} truth={name(t['owner'])}: {r['reason'][:160]}")
