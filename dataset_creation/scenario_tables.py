#!/usr/bin/env python3
"""Rewrite the top of scenarios.md (the summary and the per-case tables) from scenarios.py and the fixture.

    python generate.py --seed 42 --out fixture/
    python scenario_tables.py                      # refreshes scenarios.md sections 1 and 2

Every example is the first orphan (lowest asset id) of its scenario, described from the generated files.
Sections 3 onward of scenarios.md are written by hand and are left alone.
"""
import collections
import json
import re

from scenarios import ALL_CASES, BRIEF_CASES, NONE, SCENARIOS
from vocabulary import EDIT_NOTES, GENERIC_DESC, INCIDENT_NOTES, QUERY_NOTES, TEAM

FIXTURE, DOC, MARK = "fixture", "scenarios.md", "## 3. Decisions and why"
TITLES = {
    "clear_owner": "one person clearly maintains it",
    "service_or_inactive_top_user": "the busiest user is a robot or someone who left",
    "tied_evidence": "people with exactly equal evidence",
    "text_only_evidence": "the owner is identified by text, not by their own edits",
    "no_signals": "no activity at all",
    "cross_team_decoy": "someone from another team looks like the owner",
    "readers_only": "people only read",
    "near_tie": "almost equal",
    "conflict": "text names one person, someone else works on the asset",
    "handover": "the old owner hands it over",
    "disowned": "the editor says they stopped",
    "two_texts": "two pieces of text",
    "cross_team_owner": "the maintainer is on another team",
}
WHY_ADDED = {
    "readers_only": 'Tests the brief\'s warning "who maintains it, not who reads it most".',
    "near_tie": "Tests the gap between an exact tie and a clear owner.",
    "conflict": "Text names one person and someone else works on the asset. The answer depends on how much they do.",
    "handover": "The old owner names a successor.",
    "disowned": "The editor says they stopped maintaining it.",
    "two_texts": "Two pieces of text name different people.",
    "cross_team_owner": "The real maintainer is on another team.",
}
ROUTINE = set(INCIDENT_NOTES + EDIT_NOTES + QUERY_NOTES)
KNOWN_NAME = re.compile(r"(?:Maintained by|maintained by|Owned by|Maintainer:)\s+(svc_\w+|[A-Z]\w+(?: [A-Z]\w+)?)(?:\s*\(([a-z-]+)\))?")

recs = [json.loads(line) for line in open(f"{FIXTURE}/seed.jsonl")]
U = {r["id"]: r for r in recs if r["record_type"] == "user"}
A = {r["id"]: r for r in recs if r["record_type"] == "asset"}
signals, edges = collections.defaultdict(list), collections.defaultdict(list)
for r in recs:
    if r["record_type"] == "signal":
        signals[r["asset_id"]].append(r)
    elif r["record_type"] == "edge":
        edges[r["from_id"]].append(r)
        edges[r["to_id"]].append(r)
truth = {t["asset_id"]: t for t in map(json.loads, open(f"{FIXTURE}/truth.jsonl"))}
by_scenario = collections.defaultdict(list)
for m in map(json.loads, open(f"{FIXTURE}/eval/manifest.jsonl")):
    by_scenario[(m["case"], m["variant"])].append(m["asset_id"])
people = [u for u in U.values() if u["user_type"] == "person"]


def plural(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"


def label(uid, team):
    u = U[uid]
    if u["user_type"] != "person":
        return f"robot {u['name']}"
    if not u["active"]:
        return f"{u['name']} (left)"
    return u["name"] if u["team"] == team else f"{u['name']} ({u['team']})"


def name_fact(desc, team):
    """A plain remark about the name in a description, when the name alone does not pick out one person."""
    m = KNOWN_NAME.search(desc)
    if not m or m.group(1).startswith("svc_"):
        return ""
    name, hint = m.group(1), m.group(2)
    if " " in name:
        same = [u for u in people if u["name"] == name]
        if not same:
            return "Nobody of that name is a user."
        if len(same) > 1:
            return f"{len(same)} users have exactly that name."
        return "" if same[0]["active"] else f"{name} has left."
    same = [u for u in people if u["name"].split()[0] == name]
    here = [u for u in same if u["team"] == (hint or team)]
    who = " and ".join(f"{u['name']}{'' if u['active'] else ' (left)'}" for u in here)
    where = f"on {hint}" if hint else "on this asset's team"
    return f"{plural(len(same), 'user')} called {name}; {where}: {who or 'none'}."


def example(aid, show_links):
    a = A[aid]
    domain = a["qualified_name"].split(".")[1]
    team = TEAM[domain]
    out = [f"A {domain} {a['type']}."]
    desc = a["description"]
    if desc and desc not in GENERIC_DESC:
        out.append(f'Description: "{desc}"')
        fact = name_fact(desc, team)
        if fact:
            out.append(fact)
    count = collections.defaultdict(collections.Counter)
    for s in signals[aid]:
        count[s["user_id"]][s["signal_type"]] += 1
    order = sorted(count, key=lambda u: (-(2 * count[u]["edit"] + 3 * count[u]["incident_response"]), -count[u]["query"], u))
    for uid in order:
        e, i, q = count[uid]["edit"], count[uid]["incident_response"], count[uid]["query"]
        robot = U[uid]["user_type"] != "person"
        bits = []
        if e and (not robot or e >= 8):
            bits.append(plural(e, "edit"))
        if i:
            bits.append(plural(i, "incident"))
        if q >= (20 if robot else 15):
            bits.append(plural(q, "read"))
        if bits and (e or i or not robot or q >= 20):
            who = label(uid, team)
            out.append(f"{who[0].upper() + who[1:]}: {' + '.join(bits)}.")
    for s in sorted(signals[aid], key=lambda s: s["ts"]):
        if s["note"] and s["note"] not in ROUTINE:
            out.append(f'Note by {U[s["user_id"]]["name"]}: "{s["note"]}"')
    links = collections.Counter()
    for e in edges[aid] if show_links else []:   # neighbours are only shown where the scenario is about them
        other = e["to_id"] if e["from_id"] == aid else e["from_id"]
        owner = A[other]["owner"]
        if e["edge_type"] == "contains":
            kind = "contains it" if e["to_id"] == aid else "is contained in it"
        else:
            kind = "upstream" if e["to_id"] == aid else "downstream"
        links[(label(owner, team) if owner else "another orphan", kind)] += 1
    for (who, kind), n in sorted(links.items()):
        if kind == "contains it":
            out.append(f"An asset of {who} contains it." if who != "another orphan" else "Another orphan contains it.")
        elif who == "another orphan":
            out.append(f"Linked to another orphan ({kind}).")
        else:
            out.append(f"{who} owns {plural(n, f'{kind} asset')}.")
    if not signals[aid]:
        out.append("No activity at all.")
    elif len(out) == 1:
        out.append("Only light robot activity and a few readers.")
    return " ".join(out)


def build():
    rows = collections.defaultdict(list)
    for case, scenario, n, answer, situation, _options in SCENARIOS:
        rows[case].append((scenario, n, answer, situation, "edges_fixed" in _options))
    total = sum(n for _, _, n, *_ in SCENARIOS)
    none = sum(n for _, _, n, answer, *_ in SCENARIOS if answer == NONE)
    half_none = sum(1 for t in truth.values() if t["split"] == "calibration" and t["owner"] is None)
    L = ["# Scenarios in the generated fixture", "",
         "This file lists every orphan scenario that `generate.py` creates, with the right answer for each. "
         "Sections 1 and 2 are written by `python scenario_tables.py` from `scenarios.py` and the generated "
         "files, so they always match the data (seed 42). Section 3 onward is written by hand.", "",
         f"- **{len(ALL_CASES)} cases**: {len(BRIEF_CASES)} that the brief requires and "
         f"{len(ALL_CASES) - len(BRIEF_CASES)} that we added. The brief allows new case names.",
         f"- **{len(SCENARIOS)} scenarios** in total. Each case is split into smaller scenarios.",
         f"- **{total} orphans**: {total - none} have a person as the right answer and {none} have none.",
         f"- Every scenario has an even count, so it splits exactly in half: {total // 2} calibration and "
         f"{total // 2} held_out, with {half_none} none in each.", "",
         "**How to read the examples:**",
         '- "6 edits" means the person edited the asset 6 times; a "read" is a query.',
         '- "(left)" means the person is inactive. A team in brackets means the person is not on the asset\'s team.',
         '- "Robot" means a service account such as `svc_airflow`.',
         "- Small background activity is left out: light robot activity and people who read now and then.", "",
         "---", "", "## 1. The 13 cases", "",
         "| # | Case | Where it comes from | Scenarios | Orphans | Answer is a person | Answer is none |",
         "|---|---|---|---|---|---|---|"]
    for k, case in enumerate(ALL_CASES, 1):
        n = sum(r[1] for r in rows[case])
        nn = sum(r[1] for r in rows[case] if r[2] == NONE)
        L.append(f"| {k} | {case} | {'brief' if case in BRIEF_CASES else 'added'} | {len(rows[case])} | {n} | {n - nn} | {nn} |")
    L += [f"| | **Total** | | **{len(SCENARIOS)}** | **{total}** | **{total - none}** | **{none}** |", "",
          "**Why each added case exists:**", "", "| Case | Why |", "|---|---|"]
    L += [f"| {case} | {WHY_ADDED[case]} |" for case in ALL_CASES if case not in BRIEF_CASES]
    L += ["", "The handoff note planned conflict, handover, disowned, two_texts and cross_team_owner. "
          "The independent review added readers_only and near_tie.", "", "---", "",
          "## 2. The scenarios in each case", ""]
    for k, case in enumerate(ALL_CASES, 1):
        n = sum(r[1] for r in rows[case])
        L += [f"### Case {k}. {case}: {TITLES[case]} ({n})", "",
              "| Scenario | n | Situation | Example from the data | Right answer |", "|---|---|---|---|---|"]
        for scenario, count, answer, situation, links in rows[case]:
            aid = sorted(by_scenario[(case, scenario)])[0]
            owner = truth[aid]["owner"]
            situation = re.sub(r"<([^<>]+)>", r"*\1*", situation).replace(" -> ", " → ")
            L.append(f"| {scenario} | {count} | {situation} | {example(aid, links)} | **{U[owner]['name'] if owner else 'none'}** |")
        L.append("")
    L += ["---", ""]
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    doc = open(DOC, encoding="utf-8").read()
    rest = doc[doc.index(MARK):]
    open(DOC, "w", encoding="utf-8", newline="\n").write(build() + rest)
    print(f"rewrote sections 1 and 2 of {DOC}: {len(SCENARIOS)} scenarios")
