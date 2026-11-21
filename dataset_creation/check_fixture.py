#!/usr/bin/env python3
"""Fixture checker for the owner-proposal take-home (v3: after independent review, and with the agreed points rule).

Validates a fixture directory (seed.jsonl, truth.jsonl, changes.jsonl, optional
eval/manifest.jsonl and SHA256SUMS) against the brief, the generation plan and
per-case evidence rules. It shares NO code with generate.py, propose or apply:
every rule is re-derived from the raw JSONL.

Usage:
  python3 -I check_fixture.py --dir fixture/                          # generated fixture (strict)
  python3 -I check_fixture.py --dir reference/ --profile reference     # examiners' set (relaxed)
  python3 -I check_fixture.py --dir fixture/ --generator generate.py --seed 42   # + determinism

Exit 0 when there are no FAILs. WARNs are printed but do not fail.
"""
import argparse
import collections
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile

# ---------------------------------------------------------------- constants
SCHEMA = {
    "asset": ["record_type", "id", "qualified_name", "type", "description", "owner", "owner_set_by", "version"],
    "user": ["record_type", "id", "name", "team", "user_type", "active"],
    "edge": ["record_type", "edge_id", "from_id", "to_id", "edge_type"],
    "signal": ["record_type", "signal_id", "asset_id", "user_id", "signal_type", "ts", "note"],
    "truth": ["record_type", "asset_id", "owner", "case", "split"],
}
CHANGE_KEYS = {
    "set_owner": ["record_type", "change_id", "op", "asset_id", "owner", "actor"],
    "delete_asset": ["record_type", "change_id", "op", "asset_id", "actor"],
    "deactivate_user": ["record_type", "change_id", "op", "user_id", "actor"],
}
ASSET_TYPES = {"table", "view", "dashboard", "pipeline"}
EDGE_TYPES = {"upstream_of", "contains"}
SIGNAL_TYPES = {"query", "edit", "incident_response"}
SPLITS = {"calibration", "held_out"}
DOMAIN_TEAM = {
    "finance": "finance-data", "growth": "growth-analytics", "platform": "platform-eng",
    "marketing": "marketing-ops", "risk": "risk-analytics",
}
REQUIRED_CASES = ["clear_owner", "service_or_inactive_top_user", "tied_evidence",
                  "text_only_evidence", "no_signals", "cross_team_decoy"]
# Changes combinations the plan (§11) requires in a generated fixture.
REQUIRED_CHANGE_KINDS = [
    "set_same", "set_different", "set_on_none", "set_then_delete", "set_inactive_target", "set_on_owned",
    "set_cross_team", "repeated_change", "deactivate_then_set", "delete_truth_person", "delete_truth_none",
    "delete_owned_neighbour", "deactivate_owner_of_deleted", "deactivate_human_set_owner",
    "deactivate_tied_worker", "deactivate_multi_orphan_owner",
]
ID_RE = {"asset": re.compile(r"^a_\d{4,}$"), "user": re.compile(r"^u_\d{3,}$"),
         "edge": re.compile(r"^e_\d{4,}$"), "signal": re.compile(r"^s_\d{5,}$"),
         "change": re.compile(r"^c_\d{3,}$")}
QN_RE = re.compile(r"^warehouse\.([a-z]+)\.[a-z0-9_]+$")
TS_RE = re.compile(r"^2026-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
TS_MIN, TS_MAX = "2026-08-01T00:00:00Z", "2026-10-30T23:59:59Z"

# Ownership phrases that introduce a name (anchored; "Ask"/"Contact" deliberately excluded).
NAME_RE = re.compile(
    r"\b(?:[Mm]aintained by|[Mm]aintainer:|[Oo]wned by|[Ee]scalated to|[Qq]uestions (?:go )?to|"
    r"[Hh]anded (?:this |it )?(?:over )?to)\s+"
    r"(svc_\w+|[A-Z][\w'-]+(?:\s+[A-Z][\w'-]+)?)"
    r"(?:\s*\(([a-z]+-[a-z]+)\))?")
TEAM_TEXT_RE = re.compile(r"\b(?:finance-data|growth-analytics|platform-eng|marketing-ops|risk-analytics) team\b")
NOT_NAMES = {"Them", "The", "This", "Data", "September", "October", "August"}
# The same statements in other wordings (the checker has to read every wording the generator can write).
FULL_NAME = r"([A-Z][\w'-]+ [A-Z][\w'-]+)"
OTHER_NAME_RES = [
    re.compile(r"\b" + FULL_NAME + r" (?:looks after this|is responsible for this|has taken this over|is picking this up)"),
    re.compile(r"\b(?:Passed to|Raised with|Pinged|Speak to|Passing this one to) " + FULL_NAME),
]
DISOWN_RE = re.compile(r"no longer (?:maintain|own)|not mine any more|stopped looking after|moved off this", re.I)
SELF_RE = re.compile(r"\bI maintain\b")           # the writer claims it: "...; I maintain this job."
# The labelling rule's points (scenarios.md 3.1). A lead of CLOSE points or less does not single anyone out.
TEXT_PTS, INCIDENT_PTS, EDIT_PTS, CLOSE = 10, 3, 2, 2
HANDOVER_RE = re.compile(r"[Hh]anded (?:this |it )?(?:over )?to|has taken this over from me|Passing this one to"
                         r"|is picking this up")
# Cases whose truth=none is allowed even though one worker dominates / a person is named.
# two_texts: two texts naming different people give none whatever the points say (scenarios.md 3.4).
NONE_DOMINANCE_EXCUSED = {"conflict", "handover", "disowned", "two_texts"}
NONE_NAMED_EXCUSED = {"conflict", "handover", "two_texts"}


# ---------------------------------------------------------------- reporting
class Report:
    def __init__(self):
        self.fails, self.warns, self.passes = [], [], 0
        self._shown = {"FAIL": 0, "WARN": 0}

    def check(self, ok, msg, warn=False):
        if ok:
            self.passes += 1
        elif warn:
            self.warns.append(msg)
        else:
            self.fails.append(msg)
        return ok

    def section(self, name):
        print(f"\n== {name}")

    def flush(self, limit=25):
        for kind, lst in (("FAIL", self.fails), ("WARN", self.warns)):
            new = lst[self._shown[kind]:]
            for m in new[:limit]:
                print(f"  {kind} {m}")
            if len(new) > limit:
                print(f"  {kind} ... and {len(new) - limit} more")
            self._shown[kind] = len(lst)


def read_jsonl(path, rep):
    if not os.path.exists(path):
        rep.check(False, f"missing file {path}")
        return []
    out = []
    with open(path, "rb") as fh:
        raw = fh.read()
    name = os.path.basename(path)
    rep.check(raw.endswith(b"\n"), f"{name}: last line must end with a newline")
    rep.check(b"\r" not in raw, f"{name}: CR characters found (use \\n line endings)")
    for n, line in enumerate(raw.decode("utf-8").split("\n"), 1):
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError as e:
            rep.check(False, f"{name}:{n}: invalid JSON ({e})")
    return out


# ---------------------------------------------------------------- name resolution
def first_name(full):
    return full.split()[0]


def extract_names(text):
    """[(name, team_hint_or_None)] for every ownership phrase in the text."""
    found = []
    for m in NAME_RE.finditer(text or ""):
        name = re.sub(r"'s$", "", m.group(1))
        if name in NOT_NAMES:
            continue
        found.append((name, m.group(2)))
    for rx in OTHER_NAME_RES:
        found += [(m.group(1), None) for m in rx.finditer(text or "")]
    return found


def resolve(name, team_hint, users, asset_team=None):
    """Who a name in text can mean (scenarios.md 3.4). It counts only as
      - a full name ("Farah Demir"), or
      - a first name with a team ("Farah (platform-eng)").
    A first name alone names nobody, even if only one user has it. asset_team is not used."""
    low = name.lower()
    cands = [u for u in users.values() if u["name"].lower() == low]
    if not cands and " " not in name and team_hint:
        cands = [u for u in users.values() if first_name(u["name"]).lower() == low]
    if team_hint:
        cands = [u for u in cands if u["team"] == team_hint]
    return sorted(cands, key=lambda u: u["id"])


def unique_active(name, team_hint, users, asset_team=None):
    """The single active person the name points to, else None (unknown / ambiguous / inactive / service)."""
    c = resolve(name, team_hint, users, asset_team)
    if len(c) == 1 and c[0]["active"] and c[0]["user_type"] == "person":
        return c[0]["id"]
    return None


# ---------------------------------------------------------------- per-asset evidence
class Evidence:
    def __init__(self, aid, asset, sigs, edges_by_asset, assets, users):
        self.aid, self.asset, self.users = aid, asset, users
        m = QN_RE.match(asset["qualified_name"])
        self.domain = m.group(1) if m else None
        self.team = DOMAIN_TEAM.get(self.domain)
        # ignore malformed signals here (section 1 already reports them)
        self.sigs = [s for s in sigs if s.get("signal_type") in SIGNAL_TYPES and s.get("user_id") in users]
        self.E, self.I, self.Q = collections.Counter(), collections.Counter(), collections.Counter()
        self.last_edit = {}
        for s in self.sigs:
            {"edit": self.E, "incident_response": self.I, "query": self.Q}[s["signal_type"]][s["user_id"]] += 1
            if s["signal_type"] == "edit":
                self.last_edit[s["user_id"]] = max(self.last_edit.get(s["user_id"], ""), s["ts"])
        is_ap = lambda u: users[u]["active"] and users[u]["user_type"] == "person"
        self.actors = sorted(set(self.E) | set(self.I) | set(self.Q))
        self.active_people = [u for u in self.actors if is_ap(u)]
        self.workers = [u for u in self.active_people if self.E[u] + self.I[u] > 0]
        self.desc_names = extract_names(asset["description"])
        self.note_names = [(n, t, s) for s in self.sigs for (n, t) in extract_names(s["note"])]
        # People who took themselves out: they wrote that someone else maintains it, handed it over, or disowned it.
        self.removed = sorted({s["user_id"] for s in self.sigs
                               if extract_names(s["note"]) or DISOWN_RE.search(s["note"])})
        # Active people who claim it in their own note ("I maintain this job"); this counts like being named.
        self.self_claims = sorted({s["user_id"] for s in self.sigs if SELF_RE.search(s["note"])
                                   and is_ap(s["user_id"])} - set(self.removed))
        self.contenders = [u for u in self.workers if u not in self.removed]
        self.neighbours = []
        for e in edges_by_asset.get(aid, []):
            other = e["to_id"] if e["from_id"] == aid else e["from_id"]
            self.neighbours.append((e, other))
        self.neighbour_owners = sorted({assets[o]["owner"] for _, o in self.neighbours
                                        if o in assets and assets[o]["owner"]})

    def strength(self, u):
        return (self.E[u], self.I[u])

    def total(self, u):
        return self.E[u] + self.I[u] + self.Q[u]

    def ua(self, name, hint):
        return unique_active(name, hint, self.users, self.team)

    def pts(self, u):
        return EDIT_PTS * self.E[u] + INCIDENT_PTS * self.I[u]

    def named_unique(self):
        out = {self.ua(n, t) for n, t in self.desc_names} | {self.ua(n, t) for n, t, _ in self.note_names}
        # someone who took themselves out is no longer named, even if an old description still says so
        return sorted((out | set(self.self_claims)) - {None} - set(self.removed))

    def has_text(self):
        texts = [self.asset["description"]] + [s["note"] for s in self.sigs]
        return bool(self.desc_names or self.note_names or self.self_claims
                    or any(TEAM_TEXT_RE.search(t or "") for t in texts))

    def dominates(self, owner, others=None):
        """A clear lead on activity (scenarios.md 3.1): edit = 2 points, incident = 3. A lone contender needs
        any edit or incident; with other contenders, the owner needs a lead of more than CLOSE points over each."""
        others = [u for u in (self.contenders if others is None else others) if u != owner]
        p = self.pts(owner)
        return p > 0 and all(p - self.pts(v) > CLOSE for v in others)

    def dominant_worker(self, among=None):
        among = self.contenders if among is None else among
        d = [u for u in among if self.dominates(u, among)]
        return d[0] if len(d) == 1 else None

    def ranked(self, among=None):
        among = self.workers if among is None else among
        return sorted(among, key=lambda u: (self.E[u] + self.I[u], self.E[u], self.I[u], u), reverse=True)


# ---------------------------------------------------------------- per-case rules
def rule_check(case, ev, truth, users):
    """Problems with this label ([] = supported by the seed); None for an unknown case."""
    p = []
    team_of = lambda u: users[u]["team"]
    named = ev.named_unique()
    active_person_edits = sum(ev.E[u] for u in ev.active_people if u not in ev.removed)

    if case == "no_signals":
        if ev.sigs:
            p.append(f"has {len(ev.sigs)} signals")
        if truth is not None:
            p.append("truth must be none")
        return p

    if case in ("clear_owner", "cross_team_decoy"):
        if truth is None:
            return ["truth must be a person"]
        if not ev.dominates(truth):
            p.append(f"truth {truth} {ev.strength(truth)} does not dominate workers "
                     f"{[(u, ev.strength(u)) for u in ev.workers]}")
        if case == "cross_team_decoy":
            if team_of(truth) != ev.team:
                p.append("truth owner should be on the asset's team")
            decoys = [o for o in ev.neighbour_owners if o != truth and team_of(o) != ev.team]
            if not decoys:
                p.append("no other-team owned neighbour (decoy)")
            elif not any(ev.total(d) > ev.total(truth) for d in decoys):
                p.append("decoy is not a heavier user than the truth owner")
        return p

    if case == "service_or_inactive_top_user":
        if not ev.actors:
            return ["no signals"]
        top_total = max(ev.actors, key=lambda u: (ev.total(u), u))
        top_edit = max(ev.actors, key=lambda u: (ev.E[u], u))
        bad = lambda u: users[u]["user_type"] == "service" or not users[u]["active"]
        if not (bad(top_total) or bad(top_edit)):
            p.append(f"top user {top_total} / top editor {top_edit} is an active person")
        if truth is None:
            if ev.workers:
                p.append(f"truth none but active workers exist {ev.workers}")
        elif not ev.dominates(truth):
            p.append(f"truth {truth} does not dominate active workers")
        return p

    if case == "tied_evidence":
        if truth is not None:
            return ["truth must be none"]
        r = ev.ranked()
        if len(r) < 2 or ev.strength(r[0]) != ev.strength(r[1]):
            p.append(f"top two workers not tied: {[(u, ev.strength(u)) for u in r[:3]]}")
        return p

    if case == "near_tie":  # gray zone, labelled none, reported separately
        if truth is not None:
            return ["truth must be none"]
        r = ev.ranked()
        if len(r) < 2:
            p.append("needs two workers")
        elif ev.strength(r[0]) == ev.strength(r[1]):
            p.append("exact tie: belongs in tied_evidence")
        elif ev.dominant_worker():
            p.append(f"{ev.dominant_worker()} dominates: not a gray-zone case")
        return p

    if case == "readers_only":
        if truth is not None:
            return ["truth must be none"]
        if ev.workers:
            p.append(f"active workers exist {ev.workers}")
        if not ev.active_people:
            p.append("no active person reads it")
        if ev.has_text():
            p.append("naming text present")
        return p

    if case == "text_only_evidence":
        if active_person_edits:
            p.append(f"{active_person_edits} edits by active people: evidence is not text-only")
        note_sigs = ({s["signal_id"] for (_, _, s) in ev.note_names}
                     | {s["signal_id"] for s in ev.sigs if SELF_RE.search(s["note"])})
        stray = [s["signal_id"] for s in ev.sigs if s["signal_type"] == "incident_response"
                 and s["user_id"] in ev.active_people and s["signal_id"] not in note_sigs]
        if stray:
            p.append(f"active-person incidents without a naming note {stray}")
        if not ev.has_text():
            p.append("no name or team in description/notes")
        if truth is not None and named != [truth]:
            p.append(f"text resolves to {named}, truth {truth}")
        return p

    if case == "conflict":  # text names A, someone else edits (scenarios.md 3.3)
        if len(named) != 1:
            return [f"text should name exactly one active person, got {named}"]
        a = named[0]
        rivals = [u for u in ev.contenders if u != a]
        if not rivals:
            p.append("nobody else edits: not a conflict")
        a_pts = TEXT_PTS + ev.pts(a)
        best = max((ev.pts(u) for u in rivals), default=0)
        expected = a if a_pts - best > CLOSE else None      # the person named must be clearly ahead
        if truth != expected:
            p.append(f"named {a} has {a_pts} points, best other {best}: expected {expected}, truth {truth}")
        return p

    if case == "handover":
        hs = [s for s in ev.sigs if HANDOVER_RE.search(s["note"])]
        if not hs:
            return ["no handover note"]
        s = max(hs, key=lambda x: x["ts"])
        author = s["user_id"]
        if author not in ev.workers or ev.E[author] < 2:
            p.append(f"handover note written by {author}, who is not the previous maintainer")
        if ev.last_edit.get(author, "") > s["ts"]:
            p.append("previous maintainer keeps editing after the handover note")
        names = extract_names(s["note"])
        target = ev.ua(*names[-1]) if names else None
        if truth != target:
            p.append(f"handover target resolves to {target}, truth {truth}")
        if truth is not None:
            early = [x["signal_id"] for x in ev.sigs if x["user_id"] == truth and x["signal_type"] == "edit"
                     and x["ts"] < s["ts"]]
            if early:
                p.append(f"successor edits before the handover {early[:3]}")
        return p

    if case == "disowned":
        ds = [s for s in ev.sigs if DISOWN_RE.search(s["note"])]
        if not ds:
            return ["no disowning note"]
        writers = sorted({s["user_id"] for s in ds})
        for w in writers:
            last_note = max(s["ts"] for s in ds if s["user_id"] == w)
            if ev.last_edit.get(w, "") > last_note:
                p.append(f"{w} keeps editing after disowning")
        if truth in writers:
            p.append("truth is the person who disowned it")
        rest = [u for u in ev.workers if u not in writers]
        if truth is None:
            if ev.dominant_worker(rest):
                p.append(f"truth none but {ev.dominant_worker(rest)} dominates the remaining workers")
        elif not ev.dominates(truth, rest):
            p.append("truth is not the dominant remaining worker")
        return p

    if case == "two_texts":
        if active_person_edits:
            p.append("people edit: not a pure text conflict")
        d = sorted({ev.ua(n, t) for n, t in ev.desc_names} - {None})
        n_ = sorted(({ev.ua(n, t) for n, t, _ in ev.note_names} | set(ev.self_claims)) - {None})
        if not d or not n_:
            return p + [f"needs a name in the description {d} and in a note {n_}"]
        if truth is None and d == n_:
            p.append("texts agree but truth is none")
        if truth is not None and not (d == n_ == [truth]):
            p.append(f"texts {d}/{n_} vs truth {truth}")
        return p

    if case == "cross_team_owner":
        if truth is None:
            return ["truth must be a person"]
        if team_of(truth) == ev.team:
            p.append("truth owner is on the asset's own team")
        if not ev.dominates(truth):
            p.append("truth owner does not dominate workers")
        return p

    return None


def global_rules(case, ev, truth, users, strict):
    """Rules every label must satisfy, whatever its case (scenarios.md 3.1)."""
    p = []
    named = ev.named_unique()
    if truth is not None:
        if not (ev.E[truth] + ev.I[truth] > 0 or truth in named):
            p.append(f"truth {truth} has no edit/incident and is not named")
        others = [n for n in named if n != truth]
        if others:
            p.append(f"text names another active person {others}")
    else:
        dom = ev.dominant_worker()
        if dom and case not in NONE_DOMINANCE_EXCUSED:
            p.append(f"truth none but {dom} dominates")
        if named and case not in NONE_NAMED_EXCUSED:
            p.append(f"truth none but text names {named}")
        # §8: in none cases a neighbour owner must not also maintain / be named for this asset (always),
        # and in our generated fixture must not even read it (the reference allows a reading neighbour owner)
        bad = [o for o in ev.neighbour_owners if o in ev.workers or o in named]
        if bad:
            p.append(f"truth none but neighbour owner(s) {bad} also maintain / are named for this asset")
        readers = [o for o in ev.neighbour_owners if o in ev.actors and o not in bad]
        if readers and strict:
            p.append(f"truth none but neighbour owner(s) {readers} read this asset (generator rule §8)")
    return p


# ---------------------------------------------------------------- helpers for leakage
def make_templater(users):
    firsts = sorted({first_name(u["name"]) for u in users.values() if u["user_type"] == "person"}, key=len,
                    reverse=True)
    fulls = sorted((u["name"] for u in users.values()), key=len, reverse=True)
    first_re = re.compile(r"\b(?:" + "|".join(map(re.escape, firsts)) + r")\b") if firsts else None

    def template(text):
        t = NAME_RE.sub(lambda m: m.group(0).replace(m.group(1), "{NAME}"), text or "")
        for n in fulls:
            t = t.replace(n, "{NAME}")
        if first_re:
            t = first_re.sub("{NAME}", t)
        t = re.sub(r"\((?:[a-z]+-[a-z]+)\)", "({TEAM})", t)
        return re.sub(r"\b(?:finance-data|growth-analytics|platform-eng|marketing-ops|risk-analytics)\b", "{TEAM}", t)
    return template


def percentile(xs, q):
    if not xs:
        return 0
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * (len(xs) - 1) + 0.5))]


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--profile", choices=["generated", "reference"], default="generated")
    ap.add_argument("--manifest", help="default: <dir>/eval/manifest.jsonl")
    ap.add_argument("--generator", help="path to generate.py; enables the determinism check")
    ap.add_argument("--seed", type=int, default=42)
    a = ap.parse_args()
    strict = a.profile == "generated"
    soft = not strict
    rep = Report()
    P = lambda f: os.path.join(a.dir, f)

    # ---------- 1. format & schema
    rep.section("1. format and schema")
    seed = read_jsonl(P("seed.jsonl"), rep)
    truth = read_jsonl(P("truth.jsonl"), rep)
    changes = read_jsonl(P("changes.jsonl"), rep)
    by_type = collections.defaultdict(list)
    for r in seed:
        by_type[r.get("record_type")].append(r)
    rep.check(set(by_type) <= {"asset", "user", "edge", "signal"}, f"unexpected seed record types {sorted(set(by_type))}")
    order = []
    for r in seed:
        if not order or order[-1] != r.get("record_type"):
            order.append(r.get("record_type"))
    rep.check(order == [t for t in ["asset", "user", "edge", "signal"] if by_type[t]],
              f"seed must be grouped asset,user,edge,signal; got {order}", warn=soft)
    for t in ("asset", "user", "edge", "signal"):
        key = SCHEMA[t][1]
        good = []
        for r in by_type[t]:
            if rep.check(list(r.keys()) == SCHEMA[t], f"{t} {r.get(key)}: keys {list(r.keys())} != {SCHEMA[t]}"):
                rep.check(isinstance(r[key], str) and bool(ID_RE[t].match(r[key])), f"{t} id format {r[key]}")
                good.append(r)
        by_type[t] = good
        ids = [r[key] for r in good]
        rep.check(ids == sorted(ids), f"{t} records not sorted by {key}", warn=soft)
        rep.check(len(ids) == len(set(ids)), f"duplicate {t} ids")
    users = {u["id"]: u for u in by_type["user"]}
    assets = {x["id"]: x for x in by_type["asset"]}
    for u in users.values():
        rep.check(u["user_type"] in {"person", "service"} and isinstance(u["active"], bool)
                  and u["team"] in DOMAIN_TEAM.values() and isinstance(u["name"], str) and bool(u["name"]),
                  f"user {u['id']} bad field values")
        rep.check((u["user_type"] == "service") == u["name"].startswith("svc_"),
                  f"user {u['id']}: only service accounts should be named svc_*", warn=True)
    fulln = collections.Counter(u["name"] for u in users.values())
    dups = sorted(n for n, c in fulln.items() if c > 1)
    print(f"  duplicate full names: {dups or 'none'}")
    qns = collections.Counter(x["qualified_name"] for x in assets.values())
    rep.check(all(c == 1 for c in qns.values()), f"duplicate qualified_name {[q for q, c in qns.items() if c > 1][:5]}")
    inactive_owned = 0
    for x in assets.values():
        m = QN_RE.match(x["qualified_name"])
        rep.check(bool(m) and m.group(1) in DOMAIN_TEAM, f"asset {x['id']} qualified_name {x['qualified_name']}")
        rep.check(x["type"] in ASSET_TYPES, f"asset {x['id']} type {x['type']}")
        rep.check(isinstance(x["description"], str), f"asset {x['id']} description must be a string")
        rep.check(isinstance(x["version"], int) and x["version"] >= 1, f"asset {x['id']} version {x['version']}")
        if x["owner"] is None:
            rep.check(x["owner_set_by"] is None, f"orphan {x['id']} has owner_set_by")
        else:
            rep.check(x["owner_set_by"] in {"human", "agent"}, f"asset {x['id']} owner_set_by {x['owner_set_by']}")
            o = users.get(x["owner"])
            if rep.check(o is not None, f"asset {x['id']} owner {x['owner']} not a user"):
                rep.check(o["user_type"] == "person", f"asset {x['id']} owned by a service account")
                inactive_owned += not o["active"]
    print(f"  owned assets whose owner is inactive: {inactive_owned}; versions>1: "
          f"{sum(1 for x in assets.values() if x['version'] > 1)}")
    for e in by_type["edge"]:
        rep.check(e["edge_type"] in EDGE_TYPES, f"edge {e['edge_id']} type {e['edge_type']}")
        rep.check(e["from_id"] in assets and e["to_id"] in assets, f"edge {e['edge_id']} dangles")
        rep.check(e["from_id"] != e["to_id"], f"edge {e['edge_id']} is a self-loop")
    pairs = collections.Counter((e["from_id"], e["to_id"]) for e in by_type["edge"])
    rep.check(all(c == 1 for c in pairs.values()), "duplicate edges between the same pair")
    # cycle check on upstream_of
    adj = collections.defaultdict(list)
    for e in by_type["edge"]:
        if e["edge_type"] == "upstream_of":
            adj[e["from_id"]].append(e["to_id"])
    state, cyc = {}, []
    for root in sorted(adj):
        stack = [(root, iter(sorted(adj[root])))]
        state[root] = 1 if root not in state else state[root]
        if state[root] == 2:
            continue
        while stack:
            node, it = stack[-1]
            nxt = next(it, None)
            if nxt is None:
                state[node] = 2
                stack.pop()
            elif state.get(nxt) == 1:
                cyc.append((node, nxt))
            elif state.get(nxt) is None:
                state[nxt] = 1
                stack.append((nxt, iter(sorted(adj[nxt]))))
    rep.check(not cyc, f"upstream_of graph has cycles, e.g. {cyc[:3]}")
    prev_ts = ""
    for s in by_type["signal"]:
        rep.check(s["signal_type"] in SIGNAL_TYPES, f"signal {s['signal_id']} type {s['signal_type']}")
        rep.check(s["asset_id"] in assets and s["user_id"] in users, f"signal {s['signal_id']} dangles")
        rep.check(isinstance(s["ts"], str) and bool(TS_RE.match(s["ts"])) and TS_MIN <= s["ts"] <= TS_MAX,
                  f"signal {s['signal_id']} ts {s['ts']}")
        rep.check(isinstance(s["note"], str), f"signal {s['signal_id']} note must be a string")
        rep.check(s["ts"] >= prev_ts, f"signal {s['signal_id']} ts goes backwards (assign ids after sorting by ts)",
                  warn=True)
        prev_ts = max(prev_ts, s["ts"]) if isinstance(s["ts"], str) else prev_ts
    rep.flush()

    # ---------- 2. sizes
    rep.section("2. sizes")
    orphans = sorted(x["id"] for x in assets.values() if x["owner"] is None)
    n_none = sum(1 for t in truth if t.get("owner") is None)
    print(f"  assets={len(assets)} users={len(users)} signals={len(by_type['signal'])} edges={len(by_type['edge'])} "
          f"orphans={len(orphans)} truth_none={n_none}")
    if strict:
        rep.check(2700 <= len(assets) <= 3300, f"assets {len(assets)} not ~3000")
        rep.check(180 <= len(users) <= 220, f"users {len(users)} not ~200")
        rep.check(45000 <= len(by_type["signal"]) <= 55000, f"signals {len(by_type['signal'])} not ~50000")
        rep.check(len(orphans) >= 150, f"orphans {len(orphans)} < 150")
        rep.check(n_none >= 20, f"truth none {n_none} < 20")
        rep.check(any(e["edge_type"] == "contains" for e in by_type["edge"]), "no contains edges")
        rep.check(sum(1 for u in users.values() if u["user_type"] == "service") >= 3, "fewer than 3 service accounts")
        rep.check(sum(1 for u in users.values() if not u["active"]) >= 5, "fewer than 5 inactive users")
    rep.flush()

    # ---------- 3. truth & splits
    rep.section("3. truth file and splits")
    tmap = {}
    for t in truth:
        if not rep.check(list(t.keys()) == SCHEMA["truth"], f"truth keys {list(t.keys())}"):
            continue
        rep.check(t["asset_id"] not in tmap, f"duplicate truth for {t['asset_id']}")
        tmap[t["asset_id"]] = t
        rep.check(t["split"] in SPLITS, f"truth {t['asset_id']} split {t['split']}")
        if t["owner"] is not None:
            o = users.get(t["owner"])
            rep.check(o is not None and o["active"] and o["user_type"] == "person",
                      f"truth {t['asset_id']} owner {t['owner']} is not an active person")
    rep.check(sorted(tmap) == orphans, f"truth assets != orphans (missing {sorted(set(orphans) - set(tmap))[:5]}, "
                                       f"extra {sorted(set(tmap) - set(orphans))[:5]})")
    rep.check([t.get("asset_id") for t in truth] == sorted(t.get("asset_id") for t in truth),
              "truth not sorted by asset_id", warn=soft)
    cases = collections.Counter(t["case"] for t in tmap.values())
    for c in REQUIRED_CASES:
        rep.check(cases[c] > 0, f"required case {c} missing")
    grid = collections.defaultdict(collections.Counter)
    for t in tmap.values():
        grid[t["case"]][(t["split"], t["owner"] is None)] += 1
    print(f"  {'case':30s} {'cal':>4s} {'cal∅':>5s} {'held':>5s} {'held∅':>6s}")
    for c in sorted(grid):
        g = grid[c]
        cal = g[("calibration", False)] + g[("calibration", True)]
        held = g[("held_out", False)] + g[("held_out", True)]
        print(f"  {c:30s} {cal:4d} {g[('calibration', True)]:5d} {held:5d} {g[('held_out', True)]:6d}")
        if strict:
            rep.check(abs(cal - held) <= 1, f"case {c}: split imbalance cal={cal} held={held}")
            rep.check(abs(g[("calibration", True)] - g[("held_out", True)]) <= 1, f"case {c}: none-truth imbalance")
            if c in REQUIRED_CASES:
                rep.check(cal >= 2 and held >= 2, f"case {c}: needs >=2 per split")
    if strict:
        for sp in sorted(SPLITS):
            k = sum(1 for t in tmap.values() if t["split"] == sp and t["owner"] is None)
            rep.check(k >= 20, f"split {sp}: only {k} truth-none orphans (plan: >=20 per split)")
    man_path = a.manifest or P(os.path.join("eval", "manifest.jsonl"))
    variants = {}
    if os.path.exists(man_path):
        variants = {m.get("asset_id"): m for m in read_jsonl(man_path, rep)}
        rep.check(sorted(variants) == orphans, "manifest assets != orphans")
        vg = collections.defaultdict(collections.Counter)
        for aid, m in variants.items():
            t = tmap.get(aid)
            if t:
                rep.check(m.get("case") == t["case"], f"manifest case mismatch {aid}")
                vg[(t["case"], m.get("variant"))][t["split"]] += 1
        for (c, v), g in sorted(vg.items()):
            rep.check(abs(g["calibration"] - g["held_out"]) <= 1, f"variant {c}/{v}: split imbalance {dict(g)}")
            rep.check(g["calibration"] >= 1 and g["held_out"] >= 1, f"variant {c}/{v}: absent from a split {dict(g)}")
        print(f"  manifest: {len(vg)} (case, variant) strata")
    elif strict:
        rep.check(False, f"no {man_path}: variant balance unchecked", warn=True)
    rep.flush()

    # ---------- 4. evidence rules
    rep.section("4. evidence rules (each truth label is supported by the seed)")
    sigs_by_asset = collections.defaultdict(list)
    for s in by_type["signal"]:
        sigs_by_asset[s["asset_id"]].append(s)
    edges_by_asset = collections.defaultdict(list)
    for e in by_type["edge"]:
        edges_by_asset[e["from_id"]].append(e)
        edges_by_asset[e["to_id"]].append(e)
    case_ok, unknown, evs = collections.Counter(), set(), {}
    for aid in orphans:
        t = tmap.get(aid)
        if not t:
            continue
        ev = evs[aid] = Evidence(aid, assets[aid], sigs_by_asset[aid], edges_by_asset, assets, users)
        probs = rule_check(t["case"], ev, t["owner"], users)
        if probs is None:
            unknown.add(t["case"])
            probs = []
        probs += global_rules(t["case"], ev, t["owner"], users, strict)
        tag = t["case"] + ("/" + str(variants[aid].get("variant")) if aid in variants else "")
        if rep.check(not probs, f"{aid} [{tag}] truth={t['owner']}: " + "; ".join(probs)):
            case_ok[t["case"]] += 1
    for c in sorted(unknown):
        rep.check(False, f"no case rule for '{c}' (only global rules applied)", warn=True)
    rep.flush()
    print("  rule pass per case: " + ", ".join(f"{c} {case_ok[c]}/{cases[c]}" for c in sorted(cases)))

    # ---------- 5. leakage
    rep.section("5. leakage")
    orphan_set = set(orphans)
    owned = [x for x in assets.values() if x["owner"] is not None]
    template = make_templater(users)
    desc_owned = {template(x["description"]) for x in owned}
    note_owned = {template(s["note"]) for s in by_type["signal"] if s["asset_id"] not in orphan_set}
    for aid in orphans:
        d = template(assets[aid]["description"])
        rep.check(d in desc_owned, f"{aid}: description template only on orphans: {d!r}", warn=soft)
        for s in sigs_by_asset[aid]:
            n = template(s["note"])
            rep.check(n in note_owned, f"{aid}: note template only on orphans: {n!r}", warn=soft)
    # 5a. behaviour of services / inactive users seen only on orphans
    beh_owned = {(s["user_id"], s["signal_type"]) for s in by_type["signal"] if s["asset_id"] not in orphan_set}
    beh_orphan = {(s["user_id"], s["signal_type"]) for s in by_type["signal"] if s["asset_id"] in orphan_set}
    for uid, st in sorted(beh_orphan - beh_owned):
        u = users.get(uid)
        if u and (u["user_type"] == "service" or not u["active"]):
            rep.check(False, f"{u['name']} ({uid}) does '{st}' only on orphans", warn=soft)

    # 5b. magnitude: per-asset max per-user count by (user kind, signal type); orphan max <= owned p99.5
    def kind(u):
        u = users[u]
        return "service" if u["user_type"] == "service" else ("inactive" if not u["active"] else "person")

    def maxima(aid):
        c = collections.Counter((kind(s["user_id"]), s["signal_type"], s["user_id"]) for s in sigs_by_asset[aid]
                                if s["user_id"] in users)
        m = collections.Counter()
        for (k, st, _), n in c.items():
            m[(k, st)] = max(m[(k, st)], n)
        return m
    mo = {x["id"]: maxima(x["id"]) for x in owned}
    mr = {aid: maxima(aid) for aid in orphans}
    for key in sorted({k for m in list(mo.values()) + list(mr.values()) for k in m}):
        o_vals = [m[key] for m in mo.values()]
        r_max = max((m[key] for m in mr.values()), default=0)
        cap = percentile(o_vals, 0.995)
        rep.check(r_max <= max(cap, 1), f"magnitude leak {key}: orphan max {r_max} > owned p99.5 {cap}", warn=soft)
    # 5c. edges
    def dom(a_):
        m = QN_RE.match(assets[a_]["qualified_name"])
        return m.group(1) if m else None
    xdom_owned = sum(1 for e in by_type["edge"] if e["from_id"] not in orphan_set and e["to_id"] not in orphan_set
                     and dom(e["from_id"]) != dom(e["to_id"]))
    rep.check(xdom_owned > 0, "cross-domain edges appear only on orphans", warn=soft)
    rep.check(any(e["from_id"] in orphan_set and e["to_id"] in orphan_set for e in by_type["edge"]),
              "no orphan-orphan edges", warn=soft)
    # 5d. ids must not encode the case
    seq = [tmap[a_]["case"] for a_ in orphans if a_ in tmap]
    longest = run = 0
    for i, c in enumerate(seq):
        run = run + 1 if i and seq[i - 1] == c else 1
        longest = max(longest, run)
    rep.check(longest <= 8, f"case runs of length {longest} in id order", warn=soft)
    for c in sorted(cases):
        if cases[c] >= 10:
            doms = {dom(a_) for a_ in orphans if tmap.get(a_, {}).get("case") == c}
            types = {assets[a_]["type"] for a_ in orphans if tmap.get(a_, {}).get("case") == c}
            rep.check(len(doms) >= 3 and len(types) >= 2, f"case {c} confined to domains {doms} / types {types}",
                      warn=soft)
    # 5e. within-orphan surface features that nearly determine the truth class
    feats = {
        "naming_description": lambda ev: bool(ev.desc_names),
        "any_note": lambda ev: any(s["note"] for s in ev.sigs),
        "zero_signals": lambda ev: not ev.sigs,
        "svc_edits>10": lambda ev: any(users[u]["user_type"] == "service" and ev.E[u] > 10 for u in ev.E),
        "heavy_reader>=15": lambda ev: any(ev.Q[u] >= 15 for u in ev.active_people),
        "contains_edge": lambda ev: any(e["edge_type"] == "contains" for e, _ in ev.neighbours),
        "inactive_editor": lambda ev: any(not users[u]["active"] and ev.E[u] for u in ev.E),
        "no_edges": lambda ev: not ev.neighbours,
    }
    for name, f in feats.items():
        hit = [aid for aid in orphans if aid in evs and f(evs[aid])]
        if len(hit) >= 10:
            classes = collections.Counter("none" if tmap[a_]["owner"] is None else "person" for a_ in hit)
            top, n = classes.most_common(1)[0]
            rep.check(n / len(hit) < 0.95, f"feature '{name}' -> truth {top} for {n}/{len(hit)} orphans", warn=True)
    rep.flush()

    # ---------- 6. changes
    rep.section("6. changes.jsonl")
    ids = [c.get("change_id") for c in changes]
    rep.check(ids == sorted(ids) and len(ids) == len(set(ids)), "change ids must be unique and in order")
    ops, kinds = collections.Counter(), set()
    deleted, deactivated, human_set = set(), set(), {}
    seen_payload = set()
    truth_assets_of = collections.defaultdict(list)
    for t in tmap.values():
        if t["owner"]:
            truth_assets_of[t["owner"]].append(t["asset_id"])
    tied_workers = {u for aid, t in tmap.items() if t["case"] == "tied_evidence" and aid in evs for u in evs[aid].workers}
    neighbours_of_orphans = {o for aid in orphans for _, o in (evs[aid].neighbours if aid in evs else [])} - orphan_set
    for c in changes:
        op = c.get("op")
        if not rep.check(op in CHANGE_KEYS and list(c.keys()) == CHANGE_KEYS[op], f"change {c.get('change_id')} bad keys/op"):
            continue
        rep.check(bool(ID_RE["change"].match(c["change_id"])), f"change id {c['change_id']}")
        rep.check(isinstance(c["actor"], str) and c["actor"].startswith("human:"), f"change {c['change_id']} actor")
        payload = tuple((k, c[k]) for k in CHANGE_KEYS[op] if k != "change_id")
        if payload in seen_payload:
            kinds.add("repeated_change")
        seen_payload.add(payload)
        if op in ("set_owner", "delete_asset"):
            if not rep.check(c["asset_id"] in assets, f"change {c['change_id']} unknown asset"):
                continue
            rep.check(c["asset_id"] not in deleted, f"change {c['change_id']} touches an already-deleted asset", warn=True)
            t = tmap.get(c["asset_id"])
            if op == "set_owner":
                o = users.get(c["owner"])
                if not rep.check(o is not None, f"change {c['change_id']} unknown owner"):
                    continue
                human_set[c["asset_id"]] = c["owner"]
                if not o["active"] or c["owner"] in deactivated:
                    kinds.add("set_inactive_target")
                if c["owner"] in deactivated:
                    kinds.add("deactivate_then_set")
                if not t:
                    kinds.add("set_on_owned")
                    continue
                ops["set_owner_orphan"] += 1
                kinds.add("set_same" if t["owner"] == c["owner"] else
                          "set_on_none" if t["owner"] is None else "set_different")
                if o["team"] != evs[c["asset_id"]].team if c["asset_id"] in evs else False:
                    kinds.add("set_cross_team")
            else:
                deleted.add(c["asset_id"])
                if c["asset_id"] in human_set:
                    kinds.add("set_then_delete")
                if t:
                    ops["delete_orphan"] += 1
                    kinds.add("delete_truth_person" if t["owner"] else "delete_truth_none")
                elif c["asset_id"] in neighbours_of_orphans:
                    kinds.add("delete_owned_neighbour")
        else:
            if not rep.check(c["user_id"] in users, f"change {c['change_id']} unknown user"):
                continue
            uid = c["user_id"]
            rep.check(uid not in deactivated, f"change {c['change_id']} deactivates a user twice", warn=True)
            rep.check(users[uid]["active"], f"change {c['change_id']} deactivates an already inactive user", warn=True)
            deactivated.add(uid)
            mine = truth_assets_of.get(uid, [])
            live = [x for x in mine if x not in deleted and x not in human_set]
            if live:
                ops["deactivate_truth_owner_live"] += 1
            if any(x in deleted for x in mine):
                kinds.add("deactivate_owner_of_deleted")
            if len(live) >= 2:
                kinds.add("deactivate_multi_orphan_owner")
            if uid in human_set.values():
                kinds.add("deactivate_human_set_owner")
            if uid in tied_workers:
                kinds.add("deactivate_tied_worker")
    print(f"  {dict(ops)}")
    print(f"  combinations present: {sorted(kinds)}")
    if strict:
        rep.check(ops["set_owner_orphan"] >= 10, f"set_owner on orphans {ops['set_owner_orphan']} < 10")
        rep.check(sum(1 for c in changes if c.get("op") == "delete_asset") >= 5, "delete_asset < 5")
        rep.check(ops["deactivate_truth_owner_live"] >= 5,
                  f"deactivated truth owners with a live orphan {ops['deactivate_truth_owner_live']} < 5")
        for k in REQUIRED_CHANGE_KINDS:
            rep.check(k in kinds, f"changes miss combination {k}")
        sp = {tmap[c["asset_id"]]["split"] for c in changes if c.get("asset_id") in tmap}
        rep.check(sp == SPLITS, "changes should touch orphans in both splits", warn=True)
    rep.flush()

    # ---------- 7. determinism
    sums = P("SHA256SUMS")
    if os.path.exists(sums) or a.generator:
        rep.section("7. determinism")

    def digest_dir(d):
        out = {}
        for root, _, files in os.walk(d):
            for f in sorted(files):
                if f == "SHA256SUMS":
                    continue
                full = os.path.join(root, f)
                with open(full, "rb") as fh:
                    out[os.path.relpath(full, d)] = hashlib.sha256(fh.read()).hexdigest()
        return out
    here = digest_dir(a.dir)
    if os.path.exists(sums):
        expected = {}
        for line in open(sums):
            if line.strip():
                h, f = line.split(None, 1)
                expected[f.strip().lstrip("*")] = h
        for f, h in sorted(expected.items()):
            rep.check(here.get(f) == h, f"{f}: sha256 differs from committed SHA256SUMS")
    if a.generator:
        gen = os.path.abspath(a.generator)
        runs = []
        with tempfile.TemporaryDirectory() as tmp:
            # our own code: no -I (it would drop the script dir from sys.path and ignore PYTHONHASHSEED)
            for i, (sd, hs, tz) in enumerate([(a.seed, "0", "UTC"), (a.seed, "12345", "Asia/Kolkata"),
                                              (a.seed + 1, "0", "UTC")]):
                out = os.path.join(tmp, f"run{i}")
                env = dict(os.environ, PYTHONHASHSEED=hs, TZ=tz, LC_ALL="C")
                r = subprocess.run([sys.executable, "-s", gen, "--seed", str(sd), "--out", out],
                                   capture_output=True, text=True, env=env, cwd=os.path.dirname(gen))
                if not rep.check(r.returncode == 0, f"generator failed: {r.stderr[-500:]}"):
                    break
                runs.append(digest_dir(out))
        if len(runs) == 3:
            rep.check(runs[0] == runs[1], "same seed, different PYTHONHASHSEED/TZ -> different output")
            rep.check(runs[0] != runs[2], "different seed -> identical output (seed ignored?)")
            rep.check(all(here.get(f) == h for f, h in runs[0].items()),
                      f"--dir differs from a fresh seed={a.seed} run", warn=soft)
            for f, h in sorted(runs[0].items()):
                print(f"  {h}  {f}")
    rep.flush()

    print(f"\nRESULT: {rep.passes} checks passed, {len(rep.warns)} warnings, {len(rep.fails)} failures")
    sys.exit(1 if rep.fails else 0)


if __name__ == "__main__":
    main()
