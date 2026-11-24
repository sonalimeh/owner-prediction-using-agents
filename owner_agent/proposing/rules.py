"""Deterministic owner rules shared by both propose modes: the scorer explained in scorer/scorer.md.

For one orphaned asset:
  1. collect     each person's edits, incidents and reads on it
  2. read text   who the description and the notes name, and who wrote themselves out. read_text()
                 knows a fixed set of wordings; in model mode the model adds Mentions for the rest
  3. candidates  active persons who edited it, handled an incident on it or are named, minus anyone
                 the text rules out
  4. points      named 10 (once), incident 3, edit 2; reads and neighbouring assets count 0
  5. decide      the first that applies: nobody -> decline; two texts name different people -> decline;
                 the text names one person and someone else has more points -> decline; a lead of
                 CLOSE points or less -> decline; otherwise the leader

The points decide who. The confidence is looked up afterwards: each decision falls in one group, using
only what the rules can see, and the group's confidence is how often that group was right on the
calibration split. Those numbers live in confidence.json, written by evals --calibrate; propose only reads them.
"""

import collections
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

POINTS = {"named": 10, "incident_response": 3, "edit": 2}
CLOSE = 2  # a lead of this many points or fewer does not single anyone out (2 points = one edit)

CONFIDENCE_FILE = Path(__file__).with_name("confidence.json")
CONFIDENCE_ENV = "OWNER_AGENT_CONFIDENCE"
PRIOR = 0.5  # (0 + 1) / (0 + 2): a group with no calibration answers yet

# A name and, optionally, a team in brackets: "Farah (platform-eng)".
NAME = r"(svc_\w+|[A-Z][\w'-]+(?: [A-Z][\w'-]+)?)(?:\s*\(([a-z]+-[a-z]+)\))?"
DESCRIPTION_NAMES = re.compile(
    r"(?:Maintained by|Built and maintained by|Owned and maintained by|Owned by|Maintainer:)\s+" + NAME)
NOTE_NAMES_MAINTAINER = re.compile(r"(?:Escalated to|Questions go to)\s+" + NAME + r"[,;]\s+(?:who maintains|they maintain)")
NOTE_HANDS_OVER = re.compile(r"[Hh]anded (?:this |it )?(?:over )?to\s+" + NAME)
NOTE_DISOWNS = re.compile(r"\bno longer (?:maintain|own)", re.I)
NOTE_CLAIMS = re.compile(r"\bI maintain\b")
DESCRIPTION_NAMES_A_TEAM = re.compile(r"Maintained by the [a-z]+-[a-z]+ team")  # understood: it names no person

# Text that says nothing about ownership. Other text the patterns don't match is "unread".
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

# Confidence groups. Each decision falls in exactly one; evals --calibrate measures how often each is right.
GROUPS = {
    "proposal_unread_text": "proposes an owner, but the asset has text nobody could read",
    "proposal_model_text": "proposes an owner, on an asset with text only the model could read",
    "proposal_named_in_text": "proposes the person named in the text",
    "proposal_thin": "proposes on thin activity: one or two edits, or one incident",
    "proposal_activity": "proposes on activity: 5 points or more",
    "decline_unread_text": "declines, but the asset has text nobody could read",
    "decline_model_text": "declines, on an asset with text only the model could read",
    "decline_nobody": "declines: no candidates",
    "decline_close": "declines: too close, or the evidence disagrees",
}


class ConfidenceError(Exception):
    pass


@dataclass(frozen=True)
class User:
    id: str
    name: str
    team: str
    user_type: str
    active: bool

    @property
    def is_candidate(self):
        return self.active and self.user_type == "person"


@dataclass(frozen=True)
class Signal:
    signal_id: str
    user_id: str
    signal_type: str
    note: str


@dataclass(frozen=True)
class Neighbour:
    edge_id: str
    asset_id: str
    owner: str | None


@dataclass(frozen=True)
class Asset:
    id: str
    qualified_name: str
    description: str
    version: int


@dataclass
class Mention:
    """One thing a text says about ownership. read_text() produces these, and so does the model.

    kind: names (the text says user_id maintains it), claims (the writer says they maintain it),
    hands_over (the writer handed it to user_id), disowns (the writer no longer maintains it),
    rules_out (the text says user_id does not or no longer maintains it; from the model only).
    """

    kind: str
    source: str  # "description" or a signal id
    writer: str | None = None  # the note's writer; None for the description
    name: str | None = None  # the name as written
    user_id: str | None = None  # who the name means, when it means exactly one active person
    problem: str | None = None  # why the name means nobody


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
            parts.append(f"{self.incidents} incident{'s' * (self.incidents != 1)} = {self.incidents * POINTS['incident_response']}")
        return f"{self.name} ({self.team}): {', '.join(parts)} -> {self.points} points"


@dataclass
class Decision:
    owner: str | None
    confidence: float
    reason: str
    signal_ids: list = field(default_factory=list)
    edge_ids: list = field(default_factory=list)
    why: str = ""
    group: str = ""


class Roster:
    """The users, with the name lookups the rules use to decide who a name in text means."""

    def __init__(self, users):
        self.users = users
        self.by_full_name = collections.defaultdict(list)
        self.by_first_and_team = collections.defaultdict(list)
        for uid in sorted(users):
            u = users[uid]
            self.by_full_name[u.name.lower()].append(uid)
            self.by_first_and_team[(u.name.split()[0].lower(), u.team)].append(uid)

    def can_own(self, uid):
        return self.users[uid].is_candidate

    def why_not(self, uid):
        u = self.users[uid]
        return "is a robot" if u.user_type != "person" else ("has left" if not u.active else None)

    def resolve(self, name, team):
        """Who a name means: (user id, None), or (None, why it means nobody).
        A full name counts, and so does a first name with a team. A first name alone names nobody."""
        ids = self.by_full_name.get(name.lower(), [])
        if not ids and " " not in name:
            if not team:
                return None, "a first name alone names nobody"
            ids = self.by_first_and_team.get((name.lower(), team), [])
        elif team:
            ids = [u for u in ids if self.users[u].team == team]
        if not ids:
            return None, "is not a user"
        if len(ids) > 1:
            return None, f"matches {len(ids)} users"
        if not self.can_own(ids[0]):
            return None, self.why_not(ids[0])
        return ids[0], None

    def identifies(self, uid, text):
        """Whether text names this user the way resolve() accepts a name: a full name only they have,
        or their first name with their team, which only they share."""
        u = self.users[uid]
        first = u.name.split()[0]
        return (
            (len(self.by_full_name[u.name.lower()]) == 1 and _contains(text, u.name))
            or (len(self.by_first_and_team[(first.lower(), u.team)]) == 1
                and _contains(text, first) and _contains(text, u.team))
        )


def _contains(text, word):
    return re.search(r"(?<!\w)" + re.escape(word) + r"(?!\w)", text, re.IGNORECASE) is not None


def read_text(roster, asset, signals):
    """Every ownership statement the patterns recognise in the description and the notes."""
    out = []

    def mention(kind, source, match, writer=None):
        uid, problem = roster.resolve(match.group(1), match.group(2))
        out.append(Mention(kind, source, writer, match.group(1), uid, problem))

    m = DESCRIPTION_NAMES.search(asset.description or "")
    if m:
        mention("names", "description", m)
    for s in signals:
        note = s.note or ""
        m = NOTE_NAMES_MAINTAINER.search(note)
        if m:
            mention("names", s.signal_id, m, s.user_id)
        m = NOTE_HANDS_OVER.search(note)
        if m:
            mention("hands_over", s.signal_id, m, s.user_id)
        if NOTE_DISOWNS.search(note):
            out.append(Mention("disowns", s.signal_id, s.user_id))
        elif NOTE_CLAIMS.search(note):
            out.append(Mention("claims", s.signal_id, s.user_id, user_id=s.user_id))
    return out


def unread_text(asset, signals, understood):
    """The texts that are neither understood nor routine: "description" and signal ids."""
    out = []
    desc = asset.description or ""
    if desc and desc not in ROUTINE_DESCRIPTIONS and "description" not in understood \
            and not DESCRIPTION_NAMES_A_TEAM.search(desc):
        out.append("description")
    out += [s.signal_id for s in signals
            if s.note and s.note not in ROUTINE_NOTES and s.signal_id not in understood]
    return out


def group_of(owner, why, leader, unread, model_read):
    if owner is None:
        if unread:
            return "decline_unread_text"
        if model_read:
            return "decline_model_text"
        return "decline_nobody" if why == "nobody" else "decline_close"
    if unread:
        return "proposal_unread_text"
    if model_read:
        return "proposal_model_text"
    if leader.named_in:
        return "proposal_named_in_text"
    return "proposal_thin" if leader.points <= 4 else "proposal_activity"


def load_confidence(path=None):
    """The measured confidence of every group, from confidence.json. A group not measured yet gets PRIOR."""
    path = Path(path or os.environ.get(CONFIDENCE_ENV) or CONFIDENCE_FILE)
    try:
        with open(path, encoding="utf-8") as f:
            groups = json.load(f)["groups"]
        table = {g: float(groups[g]["confidence"]) if g in groups else PRIOR for g in GROUPS}
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise ConfidenceError(f"cannot read confidence table {path}: {e}") from None
    if not all(0 <= c <= 1 for c in table.values()):
        raise ConfidenceError(f"{path}: every confidence must be between 0 and 1")
    return table


def decide(asset, signals, neighbours, roster, confidence, mentions, model_read=(), notes=()):
    """Steps 3-5 for one asset, given every Mention from step 2. model_read lists the texts the model read."""
    users = roster.users
    signals = sorted(signals, key=lambda s: s.signal_id)

    # step 1: collect
    people = {}
    for s in signals:
        c = people.setdefault(s.user_id, Candidate(s.user_id, users[s.user_id].name, users[s.user_id].team))
        if s.signal_type == "edit":
            c.edits += 1
            c.signal_ids.append(s.signal_id)
        elif s.signal_type == "incident_response":
            c.incidents += 1
            c.signal_ids.append(s.signal_id)
        else:
            c.reads += 1
    unread = unread_text(asset, signals, {m.source for m in mentions} | set(model_read))

    # step 3: who is a candidate
    claimers = {m.writer for m in mentions if m.kind == "claims"}
    removed = {}
    for m in mentions:
        if m.kind == "disowns":
            removed[m.writer] = f"says they no longer maintain it ({m.source})"
        elif m.kind == "hands_over" and m.user_id != m.writer:
            removed[m.writer] = f"handed it over to {m.name} ({m.source})"
        elif m.kind == "names" and m.writer and m.user_id != m.writer and m.writer not in claimers:
            removed[m.writer] = f"says {m.name} maintains it ({m.source})"
        elif m.kind == "rules_out" and m.user_id:
            removed[m.user_id] = f"is ruled out by the text ({m.source})"
    named = collections.defaultdict(list)
    for m in mentions:
        if m.kind not in ("disowns", "rules_out") and m.user_id and m.user_id not in removed:
            named[m.user_id].append("the description" if m.source == "description" else f"note {m.source}")
    for uid, c in people.items():
        if uid not in removed and (c.edits or c.incidents) and not roster.can_own(uid):
            removed[uid] = roster.why_not(uid)
    candidates = []
    for uid in sorted(set(people) | set(named)):
        c = people.get(uid) or Candidate(uid, users[uid].name, users[uid].team)
        if uid in removed or not roster.can_own(uid):
            continue
        if not (c.edits or c.incidents or uid in named):
            continue  # only reads: reading is not maintaining
        c.named_in = list(dict.fromkeys(named.get(uid, [])))
        # step 4: points
        c.points = POINTS["named"] * bool(c.named_in) + POINTS["incident_response"] * c.incidents + POINTS["edit"] * c.edits
        candidates.append(c)
    candidates.sort(key=lambda c: (-c.points, c.user_id))

    # step 5: decide
    named_people = [c for c in candidates if c.named_in]
    leader = candidates[0] if candidates else None
    second = candidates[1] if len(candidates) > 1 else None
    owner = None
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
    set_aside = [f"{users[u].name} {w}" for u, w in sorted(removed.items())
                 if w != "is a robot" or people[u].edits >= 8]
    set_aside += [f"{c.name} only reads ({c.reads} reads)" for u, c in sorted(people.items())
                  if c.reads >= 15 and not (c.edits or c.incidents) and u not in named and roster.can_own(u)]
    set_aside += [f"{'the description' if m.source == 'description' else 'note ' + m.source} names '{m.name}', "
                  f"{'who ' if m.problem in ('has left', 'is a robot', 'is not a user') else 'but '}{m.problem}"
                  for m in mentions if m.name and not m.user_id]
    if set_aside:
        reason += " Not counted: " + "; ".join(set_aside) + "."
    if unread:
        reason += f" Could not read {len(unread)} text(s) on this asset: {', '.join(unread[:3])}."
    if notes:
        reason += " " + " ".join(n.rstrip(".") + "." for n in notes)

    # evidence: the signals behind the people compared, and the notes read
    compared = candidates[:2] if owner is None else [leader]
    signal_ids = sorted({sid for c in compared for sid in c.signal_ids}
                        | {m.source for m in mentions if m.source != "description"})
    owned = [n.edge_id for n in neighbours if owner and n.owner == owner]
    if owned:  # worth 0 points; shown as context only
        reason += f" Context: {leader.name} also owns {len(owned)} neighbouring asset(s)."
    group = group_of(owner, why, leader, unread, model_read)
    return Decision(owner, confidence[group], reason, signal_ids, sorted(owned), why, group)
