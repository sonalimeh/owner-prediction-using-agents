#!/usr/bin/env python3
"""Rules scorer: for one orphaned asset, work out who the owner is, or decline.

    python scorer.py --seed fixture/seed.jsonl --asset a_0306      # explain one asset
    python scorer.py --seed fixture/seed.jsonl --all               # one JSON line per orphan
    python scorer.py --seed fixture/seed.jsonl --asset a_0974 --confidence confidence.json

It reads only the catalog (assets, users, signals, edges). It never reads truth.jsonl.
The rule it follows is in scenarios.md, section 3.1. scorer.md explains this file step by step.

The five steps, for one asset:
  1. collect    count each person's edits, incidents and reads on this asset
  2. read text  find who the description and the notes name, and who wrote themselves out
  3. candidates keep active people who worked on it or are named; drop the rest
  4. points     named = 10 (once), incident = 3, edit = 2
  5. decide     one candidate -> that person; several -> the leader if ahead by more than 2 points
"""
import argparse
import collections
import json
import re
from dataclasses import dataclass, field

POINTS = {"named": 10, "incident": 3, "edit": 2}
CLOSE = 2          # a lead of this many points or fewer does not single anyone out (2 points = one edit)

# ------------------------------------------------------------------ the text the rules can read
# Each pattern captures a name and, optionally, a team in brackets: "Farah (platform-eng)".
NAME = r"(svc_\w+|[A-Z][\w'-]+(?: [A-Z][\w'-]+)?)(?:\s*\(([a-z]+-[a-z]+)\))?"
DESCRIPTION_NAMES = re.compile(
    r"(?:Maintained by|Built and maintained by|Owned and maintained by|Owned by|Maintainer:)\s+" + NAME)
NOTE_NAMES_MAINTAINER = re.compile(r"(?:Escalated to|Questions go to)\s+" + NAME + r"[,;]\s+(?:who maintains|they maintain)")
NOTE_HANDS_OVER = re.compile(r"[Hh]anded (?:this |it )?(?:over )?to\s+" + NAME)
NOTE_DISOWNS = re.compile(r"\bno longer (?:maintain|own)", re.I)
NOTE_CLAIMS = re.compile(r"\bI maintain\b")
DESCRIPTION_NAMES_A_TEAM = re.compile(r"Maintained by the [a-z]+-[a-z]+ team")     # understood: it names no person

# Routine text that says nothing about ownership. Any other text the patterns above don't match is
# "text the scorer could not read": it can't use it, but it can notice that it is there.
ROUTINE_DESCRIPTIONS = {
    "Source of truth for reporting; see runbook.", "Incremental load, partitioned by day.",
    "Deduplicated and typed; safe for joins.", "Feeds the weekly business review.",
    "Used by downstream dashboards.", "Rebuilt nightly from source extracts.",
    "Nightly extract; see runbook.", "Legacy extract.",
}
ROUTINE_NOTES = {
    "Restarted the load; data caught up.", "Re-ran after the upstream delay.", "Backfilled the missing partition.",
    "Investigated row-count alert; false alarm.", "Added a column for the new source field.", "Fixed a type cast.",
    "Ad hoc check for the weekly review.",
}

# The groups used for confidence. Each result falls in exactly one; calibrate.py measures how often each is right.
GROUPS = {
    "proposal_unread_text": "proposes an owner, but the asset has text the scorer could not read",
    "proposal_named_in_text": "proposes the person named in the text",
    "proposal_thin": "proposes on thin activity: one or two edits, or one incident",
    "proposal_activity": "proposes on activity: 5 points or more",
    "decline_unread_text": "declines, but the asset has text the scorer could not read",
    "decline_nobody": "declines: no candidates",
    "decline_close": "declines: too close, or the evidence disagrees",
}


@dataclass
class Mention:
    """One thing a text says about ownership. The rules produce these; later, the model will too."""
    kind: str                  # names | hands_over | disowns | claims
    source: str                # "description" or a signal id
    writer: str = None         # user id of the note's writer (None for the description)
    name: str = None           # the name as written
    user_id: str = None        # who the name means, when it means exactly one active person
    problem: str = None        # why the name means nobody


@dataclass
class Candidate:
    user_id: str
    name: str
    team: str
    edits: int = 0
    incidents: int = 0
    reads: int = 0
    named_in: list = field(default_factory=list)
    signal_ids: list = field(default_factory=list)
    points: int = 0

    def describe(self):
        parts = []
        if self.named_in:
            parts.append(f"named in {' and '.join(self.named_in)} = {POINTS['named']}")
        if self.edits:
            parts.append(f"{self.edits} edit{'s' * (self.edits != 1)} = {self.edits * POINTS['edit']}")
        if self.incidents:
            parts.append(f"{self.incidents} incident{'s' * (self.incidents != 1)} = {self.incidents * POINTS['incident']}")
        return f"{self.name} ({self.team}): {', '.join(parts)} -> {self.points} points"


# ------------------------------------------------------------------ the catalog (the scorer's only input)
class Catalog:
    """Users, assets, signals and edges, indexed so that one asset's evidence is a direct lookup.
    Built here from seed.jsonl; the propose command will build the same thing from SQLite."""

    def __init__(self, records):
        self.users, self.assets = {}, {}
        self.signals = collections.defaultdict(list)       # asset id -> its signals
        self.edges = collections.defaultdict(list)         # asset id -> edges that touch it
        for r in records:
            kind = r["record_type"]
            if kind == "user":
                self.users[r["id"]] = r
            elif kind == "asset":
                self.assets[r["id"]] = r
            elif kind == "signal":
                self.signals[r["asset_id"]].append(r)
            elif kind == "edge":
                self.edges[r["from_id"]].append(r)
                self.edges[r["to_id"]].append(r)
        self.by_full_name = collections.defaultdict(list)          # "farah demir" -> [user ids]
        self.by_first_and_team = collections.defaultdict(list)     # ("farah", "platform-eng") -> [user ids]
        for uid in sorted(self.users):
            u = self.users[uid]
            self.by_full_name[u["name"].lower()].append(uid)
            self.by_first_and_team[(u["name"].split()[0].lower(), u["team"])].append(uid)

    @classmethod
    def from_jsonl(cls, path):
        with open(path, encoding="utf-8") as fh:
            return cls(json.loads(line) for line in fh if line.strip())

    def orphans(self):
        return sorted(a for a, rec in self.assets.items() if rec["owner"] is None)

    def can_own(self, uid):
        u = self.users[uid]
        return u["user_type"] == "person" and u["active"]

    def why_not(self, uid):
        u = self.users[uid]
        return "is a robot" if u["user_type"] != "person" else ("has left" if not u["active"] else None)

    def resolve(self, name, team):
        """Who a name in text means: (user id, None), or (None, why it means nobody).
        A full name counts, and so does a first name with a team. A first name alone names nobody."""
        ids = self.by_full_name.get(name.lower(), [])
        if not ids and " " not in name:
            if not team:
                return None, "a first name alone names nobody"
            ids = self.by_first_and_team.get((name.lower(), team), [])
        elif team:
            ids = [u for u in ids if self.users[u]["team"] == team]
        if not ids:
            return None, "is not a user"
        if len(ids) > 1:
            return None, f"matches {len(ids)} users"
        if not self.can_own(ids[0]):
            return None, self.why_not(ids[0])
        return ids[0], None


# ------------------------------------------------------------------ step 2: read the text
def read_text(catalog, asset, signals):
    """Every ownership statement the rules recognise in the description and the notes."""
    out = []

    def mention(kind, source, match, writer=None):
        uid, problem = catalog.resolve(match.group(1), match.group(2))
        out.append(Mention(kind, source, writer, match.group(1), uid, problem))

    m = DESCRIPTION_NAMES.search(asset["description"] or "")
    if m:
        mention("names", "description", m)
    for s in signals:
        note = s["note"] or ""
        m = NOTE_NAMES_MAINTAINER.search(note)
        if m:
            mention("names", s["signal_id"], m, s["user_id"])
        m = NOTE_HANDS_OVER.search(note)
        if m:
            mention("hands_over", s["signal_id"], m, s["user_id"])
        if NOTE_DISOWNS.search(note):
            out.append(Mention("disowns", s["signal_id"], s["user_id"]))
        elif NOTE_CLAIMS.search(note):
            out.append(Mention("claims", s["signal_id"], s["user_id"], user_id=s["user_id"]))
    return out


def unread_text(asset, signals, mentions):
    """Where the asset has text the rules neither understood nor know as routine: 'description' or signal ids."""
    understood = {m.source for m in mentions}
    out = []
    desc = asset["description"] or ""
    if desc and desc not in ROUTINE_DESCRIPTIONS and "description" not in understood \
            and not DESCRIPTION_NAMES_A_TEAM.search(desc):
        out.append("description")
    out += [s["signal_id"] for s in signals
            if s["note"] and s["note"] not in ROUTINE_NOTES and s["signal_id"] not in understood]
    return out


def group_of(owner, why, leader, unread):
    """Which confidence group a result belongs to. Uses only what the scorer can see."""
    if owner is None:
        return "decline_unread_text" if unread else ("decline_nobody" if why == "nobody" else "decline_close")
    if unread:
        return "proposal_unread_text"
    if leader.named_in:
        return "proposal_named_in_text"
    return "proposal_thin" if leader.points <= 4 else "proposal_activity"


# ------------------------------------------------------------------ the scorer
def score_asset(catalog, asset_id, extra_mentions=()):
    """Steps 1-5 for one asset. extra_mentions is where the model's reading of the text will plug in."""
    asset = catalog.assets[asset_id]
    signals = sorted(catalog.signals[asset_id], key=lambda s: s["signal_id"])
    users = catalog.users

    # ---- step 1: collect what each person did on this asset
    people = {}
    for s in signals:
        uid = s["user_id"]
        c = people.setdefault(uid, Candidate(uid, users[uid]["name"], users[uid]["team"]))
        if s["signal_type"] == "edit":
            c.edits += 1
            c.signal_ids.append(s["signal_id"])
        elif s["signal_type"] == "incident_response":
            c.incidents += 1
            c.signal_ids.append(s["signal_id"])
        else:
            c.reads += 1

    # ---- step 2: read the text
    mentions = read_text(catalog, asset, signals) + list(extra_mentions)
    unread = unread_text(asset, signals, mentions)

    # ---- step 3: who is a candidate
    removed = {}                                        # user id -> why they are not a candidate
    for m in mentions:                                  # people who wrote themselves out
        if m.kind == "disowns":
            removed[m.writer] = f"says they no longer maintain it ({m.source})"
        elif m.kind == "hands_over" and m.user_id != m.writer:
            removed[m.writer] = f"handed it over to {m.name} ({m.source})"
        elif m.kind == "names" and m.writer and m.user_id != m.writer:
            removed[m.writer] = f"says {m.name} maintains it ({m.source})"
    named = collections.defaultdict(list)               # user id -> the texts that name them
    for m in mentions:
        if m.kind != "disowns" and m.user_id and m.user_id not in removed:
            named[m.user_id].append("the description" if m.source == "description" else f"note {m.source}")
    for uid, c in people.items():
        if uid not in removed and (c.edits or c.incidents) and not catalog.can_own(uid):
            removed[uid] = catalog.why_not(uid)
    candidates = []
    for uid in sorted(set(people) | set(named)):
        c = people.get(uid) or Candidate(uid, users[uid]["name"], users[uid]["team"])
        if uid in removed or not catalog.can_own(uid):
            continue
        if not (c.edits or c.incidents or uid in named):
            continue                                    # only reads: reading is not maintaining
        c.named_in = named.get(uid, [])
        # ---- step 4: points
        c.points = (POINTS["named"] * bool(c.named_in) + POINTS["incident"] * c.incidents + POINTS["edit"] * c.edits)
        candidates.append(c)
    candidates.sort(key=lambda c: (-c.points, c.user_id))

    # ---- step 5: decide
    named_people = [c for c in candidates if c.named_in]
    leader = candidates[0] if candidates else None
    second = candidates[1] if len(candidates) > 1 else None
    owner, why = None, None
    if not candidates:
        why = "nobody"
        reason = "No active person edited it, handled an incident on it or is named in its text."
    elif len(named_people) > 1:
        why = "texts_disagree"
        reason = ("Two texts name different people: " + " and ".join(c.name for c in named_people[:2])
                  + ". " + " ".join(c.describe() + "." for c in named_people[:2]))
    elif named_people and leader is not named_people[0]:
        why = "conflict"
        reason = (f"The text names {named_people[0].name}, but {leader.name} has more points. "
                  f"{named_people[0].describe()}. {leader.describe()}. The text and the work disagree.")
    elif second and leader.points - second.points <= CLOSE:
        why = "close"
        gap = leader.points - second.points
        lean = "They are level." if gap == 0 else f"{leader.name} is slightly ahead, but a lead of {gap} is not enough."
        reason = f"Too close to call. {leader.describe()}. {second.describe()}. {lean}"
    else:
        owner = leader.user_id
        why = "clear_lead" if second else "only_candidate"
        reason = leader.describe() + "."
        reason += (f" Next: {second.describe()}. Lead of {leader.points - second.points} points." if second
                   else " Nobody else is a candidate.")
    if owner is None:
        reason += " Not proposing an owner."

    # what was set aside, so a reviewer can see it
    notes = [f"{users[u]['name']} {w}" for u, w in sorted(removed.items())
             if w not in ("is a robot",) or people[u].edits >= 8]
    notes += [f"{c.name} only reads ({c.reads} reads)" for u, c in sorted(people.items())
              if c.reads >= 15 and not (c.edits or c.incidents) and u not in named and catalog.can_own(u)]
    notes += [f"{'the description' if m.source == 'description' else 'note ' + m.source} names '{m.name}', "
              f"{'who ' if m.problem in ('has left', 'is a robot', 'is not a user') else 'but '}{m.problem}"
              for m in mentions if m.name and not m.user_id]
    if notes:
        reason += " Not counted: " + "; ".join(notes) + "."
    if unread:
        reason += f" Could not read {len(unread)} text(s) on this asset: {', '.join(unread[:3])}."

    # evidence: the signals behind the people the decision compared, and the notes it read
    compared = candidates[:2] if owner is None else [leader]
    signal_ids = sorted({sid for c in compared for sid in c.signal_ids}
                        | {m.source for m in mentions if m.source != "description"})
    neighbours = [e for e in catalog.edges[asset_id] if owner and
                  catalog.assets[e["to_id"] if e["from_id"] == asset_id else e["from_id"]]["owner"] == owner]
    if neighbours:                                      # worth 0 points; shown as context only
        reason += f" Context: {leader.name} also owns {len(neighbours)} neighbouring asset(s)."
    return {
        "asset_id": asset_id,
        "owner": owner,
        "outcome": "propose" if owner else "decline",
        "why": why,
        "group": group_of(owner, why, leader, unread),
        "unread_text": unread,
        "reason": reason,
        "candidates": [{"user_id": c.user_id, "name": c.name, "team": c.team, "points": c.points, "edits": c.edits,
                        "incidents": c.incidents, "named_in": c.named_in} for c in candidates],
        "not_candidates": {u: w for u, w in sorted(removed.items())},
        "signal_ids": signal_ids,
        "edge_ids": sorted(e["edge_id"] for e in neighbours),
    }


def add_confidence(result, table):
    """Attach the measured confidence of the result's group, and whether approve-batch would take it.
    table is confidence.json, written by calibrate.py. For a decline, the confidence is how likely it is
    that 'none' is the right answer; a decline is never approved."""
    result["confidence"] = table["groups"][result["group"]]["confidence"]
    result["auto_approve"] = result["owner"] is not None and result["confidence"] >= table["threshold"]
    return result


def explain(catalog, result):
    """A readable report for one asset."""
    a = catalog.assets[result["asset_id"]]
    lines = [f"{a['id']}  {a['qualified_name']}  ({a['type']})",
             f"description: {a['description']!r}", "", "candidates, by points:"]
    for c in result["candidates"]:
        named = f", named in {' and '.join(c['named_in'])}" if c["named_in"] else ""
        lines.append(f"  {c['points']:3d}  {c['name']} ({c['team']}): {c['edits']} edit(s), {c['incidents']} incident(s){named}")
    if not result["candidates"]:
        lines.append("  (none)")
    if result["not_candidates"]:
        lines += ["", "not candidates:"]
        lines += [f"  {catalog.users[u]['name']}: {w}" for u, w in result["not_candidates"].items()]
    owner = catalog.users[result["owner"]]["name"] if result["owner"] else "none (declined)"
    lines += ["", f"answer: {owner}   [{result['why']}]", f"group: {result['group']}", f"reason: {result['reason']}",
              f"evidence: {len(result['signal_ids'])} signals, {len(result['edge_ids'])} edges"]
    if "confidence" in result:
        what = "that this owner is right" if result["owner"] else "that none is right"
        then = ("auto-approved" if result["auto_approve"] else
                "goes to a person" if result["owner"] else "a decline is never approved; a person can set an owner")
        lines.insert(-2, f"confidence: {result['confidence']:.2f} {what} -> {then}")
    return "\n".join(lines)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Rules scorer for orphaned assets.")
    ap.add_argument("--seed", required=True, help="path to seed.jsonl")
    ap.add_argument("--asset", help="explain one asset")
    ap.add_argument("--all", action="store_true", help="score every orphan; one JSON line each")
    ap.add_argument("--confidence", help="confidence.json from calibrate.py; adds the confidence to each answer")
    args = ap.parse_args()
    cat = Catalog.from_jsonl(args.seed)
    table = json.load(open(args.confidence)) if args.confidence else None
    score = lambda aid: add_confidence(score_asset(cat, aid), table) if table else score_asset(cat, aid)
    if args.asset:
        print(explain(cat, score(args.asset)))
    elif args.all:
        for aid in cat.orphans():
            print(json.dumps(score(aid)))
    else:
        ap.error("give --asset <id> or --all")
