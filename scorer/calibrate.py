#!/usr/bin/env python3
"""Measure the scorer's confidence groups on the calibration split, and tabulate precision against coverage.

    python calibrate.py --dir fixture                 # prints the tables and writes confidence.json
    python calibrate.py --dir fixture --target 0.9    # a different bar for auto-approval

It reads truth.jsonl, so it is kept apart from scorer.py. It only ever uses the calibration split.

Confidence of a group = (right + 1) / (total + 2): the share that was right, pulled slightly towards 0.5
so that no group claims certainty and small groups are trusted less.

Threshold for approve-batch = the target (default 0.95). A proposal is auto-approved only when its group's
confidence reaches the target; everything else goes to a person. Declines are never approved.
"""
import argparse
import collections
import json
import os

from scorer import GROUPS, Catalog, score_asset

ap = argparse.ArgumentParser()
ap.add_argument("--dir", required=True, help="folder with seed.jsonl and truth.jsonl")
ap.add_argument("--out", default="confidence.json")
ap.add_argument("--target", type=float, default=0.95, help="how sure a proposal must be to be auto-approved")
args = ap.parse_args()

cat = Catalog.from_jsonl(os.path.join(args.dir, "seed.jsonl"))
truth = [t for t in map(json.loads, open(os.path.join(args.dir, "truth.jsonl"))) if t["split"] == "calibration"]
results = [(t, score_asset(cat, t["asset_id"])) for t in truth]

right, total = collections.Counter(), collections.Counter()
for t, r in results:
    total[r["group"]] += 1
    right[r["group"]] += r["owner"] == t["owner"]
confidence = {g: round((right[g] + 1) / (total[g] + 2), 2) for g in GROUPS}

print(f"calibration split: {len(results)} orphans\n")
print(f"{'group':26s} {'right':>9s} {'confidence':>11s}   meaning")
for g in GROUPS:
    print(f"{g:26s} {right[g]:4d}/{total[g]:<4d} {confidence[g]:11.2f}   {GROUPS[g]}")

proposals = [(confidence[r["group"]], r["owner"] == t["owner"]) for t, r in results if r["owner"]]
print(f"\nprecision against coverage (only proposals can be approved; {len(proposals)} proposals, {len(results)} orphans)")
print(f"{'approve at or above':>20s} {'approved':>9s} {'right':>6s} {'wrong':>6s} {'precision':>10s} {'coverage':>9s}")
rows = []
for x in sorted({c for c, _ in proposals}, reverse=True):
    ok = [good for c, good in proposals if c >= x]
    rows.append({"threshold": x, "approved": len(ok), "right": sum(ok), "precision": round(sum(ok) / len(ok), 3),
                 "coverage": round(len(ok) / len(results), 3)})
    print(f"{x:20.2f} {len(ok):9d} {sum(ok):6d} {len(ok) - sum(ok):6d} {sum(ok) / len(ok):10.3f} {len(ok) / len(results):9.3f}")

auto = [g for g in GROUPS if g.startswith("proposal") and confidence[g] >= args.target]
person = [g for g in GROUPS if g.startswith("proposal") and confidence[g] < args.target]
ok = [good for c, good in proposals if c >= args.target]
print(f"\nthreshold for approve-batch: {args.target}")
print(f"  auto-approved: {', '.join(auto) or 'nothing'}")
print(f"  sent to a person: {', '.join(person) or 'nothing'}; and every decline")
print(f"  on calibration: {len(ok)} proposals approved, {len(ok) - sum(ok)} wrong, "
      f"precision {sum(ok) / max(len(ok), 1):.3f}, coverage {len(ok) / len(results):.3f}")

with open(args.out, "w", newline="\n") as fh:
    json.dump({"version": 1, "split": "calibration", "orphans": len(results), "threshold": args.target,
               "groups": {g: {"right": right[g], "total": total[g], "confidence": confidence[g]} for g in GROUPS},
               "precision_coverage": rows}, fh, indent=2)
    fh.write("\n")
print(f"\nwrote {args.out}")
