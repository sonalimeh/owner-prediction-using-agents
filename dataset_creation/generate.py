#!/usr/bin/env python3
"""Deterministic fixture generator for the owner-proposal take-home.

    python generate.py --seed 42 --out fixture/

writes  fixture/seed.jsonl            assets, users, edges, signals (reference format)
        fixture/truth.jsonl           one row per orphan: owner | null, case, split
        fixture/changes.jsonl         human edits made after propose
        fixture/eval/manifest.jsonl   case + scenario per orphan (for eval breakdowns; propose must not read it)
        fixture/SHA256SUMS            checksums of the files above

The four files of the generator
  scenarios.py   WHAT is generated: one row per scenario, with its count and right answer
  recipes.py     HOW each case's evidence is built: one function per case
  vocabulary.py  the fixed lists: teams, robots, names, description and note texts
  generate.py    this file: the pipeline, steps 1-8 below

The pipeline
  1. users        200 users, with name clashes planned on purpose (two Akiras on one team, ...)
  2. plan         one orphan per row count in scenarios.py; noise layers assigned evenly per case
  3. assets       3,000 asset ids, shuffled so ids never reveal the case; owners for owned assets
  4. splits       half of every scenario to calibration, half to held_out
  5. orphans      for each orphan: its recipe (answer first, then evidence), then noise
  6. background   the owned assets (3,000 minus the orphans); they reuse every text and robot behaviour seen on orphans
  7. changes      the human edits (set_owner, delete_asset, deactivate_user)
  8. write        sort, number, write the files and SHA256SUMS

Determinism: one master random.Random(seed); one generator per orphan seeded from the string
"seed/case/scenario/k"; choices are always made from sorted lists; no clock, no set iteration, no floats in
the output. Same seed -> byte-identical files (tested on Python 3.13; pin the minor version).
"""
import argparse
import collections
import hashlib
import json
import os
import random
from datetime import timedelta

from recipes import RECIPES
from scenarios import PERSON, SCENARIOS
from vocabulary import (
    DAY, DESC, DOMAINS, DOMAIN_OF, EDIT_NOTES, FIRST, FULL_KEYS, GENERIC_DESC, INCIDENT_NOTES, LAST, NOTE,
    OTHER_WORDINGS, OWN_SVC, QUERY_NOTES, QUERY_SVC, SERVICES, SHORT_KEYS, START, STEMS, SUFFIXES, TEAM,
    TEAMS, TEAM_SIZE, TYPE_CYCLE, UNKNOWN_FIRST, WINDOWS)

# ------------------------------------------------------------------ sizes
N_ASSETS = 3000
N_OWNED = N_ASSETS - sum(row[2] for row in SCENARIOS)     # owned (background) assets; the rest are orphans
N_CROSS_NAMES = 20              # first names held by exactly one person in every team
# Background (owned asset) base recipe. Tuned so the whole fixture has ~50,000 signals.
BG = dict(owner_edits=(2, 5), incident_p=0.25, airflow_edits=(0, 3), dbt_p=0.15, own_svc_p=0.30,
          svc_extra_edits=(1, 4), svc_queries=(2, 5), readers=(1, 3), reader_queries=(1, 3))
SIGNAL_BAND = (47000, 53000)


# ------------------------------------------------------------------ small containers
class Spec:
    """One orphan: its scenario, its evidence and its right answer."""

    def __init__(self, case, variant, k, truth_kind, recipe, opts):
        self.case, self.variant, self.k = case, variant, k
        self.key = f"{case}/{variant}/{k}"
        self.truth_kind, self.recipe, self.opts = truth_kind, recipe, opts
        self.truth = None            # user id or None
        self.desc = None             # None -> the description overlay decides
        self.sigs = []               # (asset, user, type, second, note)
        self.exclude = set()         # people who must get no noise signals
        self.named = set()           # active people named in text
        self.principals = []         # people the recipe is about (used when choosing changes)
        self.svc_edits = True        # may robots edit this asset as noise?
        self.split = self.split_pos = None   # calibration | held_out, and the position inside that half
        self.aid = self.domain = self.team = self.type = self.rank = None


class Ctx:
    def __init__(self, seed):
        self.seed = seed
        self.rng = random.Random(seed)          # master generator: users, assets, schedule, background, changes
        self.U, self.A = {}, {}
        self.edges, self.pairs = [], set()
        self.bg_sigs = []
        self.keys = {"orphan": set(), "owned": set()}   # text templates used on each side

    # ---- people
    def kind(self, uid):
        u = self.U[uid]
        return "service" if u["user_type"] == "service" else ("person" if u["active"] else "inactive")

    def full(self, uid):
        return self.U[uid]["name"]

    def first(self, uid):
        return self.U[uid]["name"].split()[0]

    def person(self, rng, team=None, not_team=None, exclude=(), pred=None):
        """An active person, chosen from a sorted list. Users with a duplicated full name never get a role."""
        pool = self.active[team] if team else self.active_all
        c = [u for u in pool if u not in exclude and u not in self.dup_ids
             and (not_team is None or self.U[u]["team"] != not_team) and (pred is None or pred(u))]
        assert c, f"no candidate person (team={team}, not_team={not_team})"
        return rng.choice(c)

    # ---- time and signals
    def second(self, rng, lo_day, hi_day):
        return rng.randrange(lo_day * DAY, hi_day * DAY)

    def auto_note(self, rng, uid, typ, side):
        """Generic note for a signal: incidents always have one; a few person edits/queries do."""
        if typ == "incident_response":
            text = rng.choice(INCIDENT_NOTES)
        elif self.U[uid]["user_type"] == "person":
            r = rng.random()
            text = ""
            if typ == "edit" and r < 0.05:
                text = EDIT_NOTES[int(r * 1000) % len(EDIT_NOTES)]
            elif typ == "query" and r < 0.02:
                text = QUERY_NOTES[0]
        else:
            text = ""
        self.keys[side].add(("note", text))
        return text

    def add(self, sp, rng, uid, typ, n, lo=None, hi=None):
        """n signals of one type for an orphan, at random seconds in [lo, hi) (default: the orphan's window)."""
        lo, hi = (sp.lo, sp.hi) if lo is None else (lo, hi)
        for _ in range(n):
            sp.sigs.append((sp.aid, uid, typ, rng.randrange(lo, hi), self.auto_note(rng, uid, typ, "orphan")))

    def note(self, sp, rng, uid, typ, key, name=None, at=None):
        """One signal carrying a templated note, e.g. 'Escalated to X, who maintains this table; ...'."""
        text = NOTE[key].format(n=name, ty=sp.type)
        self.keys["orphan"].add(("note", key, sp.type if key == "esc" else ""))
        sp.sigs.append((sp.aid, uid, typ, rng.randrange(sp.lo, sp.hi) if at is None else at, text))

    def say(self, sp, rng, uid, typ, text, at=None):
        """One signal carrying a ready-made note."""
        sp.sigs.append((sp.aid, uid, typ, rng.randrange(sp.lo, sp.hi) if at is None else at, text))

    def other_wording(self, rng, sp, kind):
        """A wording the rules scorer does not know. held_out orphans mostly get the wording kept for held_out."""
        shared, held_out_only = OTHER_WORDINGS[kind]
        text = held_out_only[0] if (sp.split == "held_out" and sp.split_pos % 2 == 0) else rng.choice(shared)
        self.keys["orphan"].add(("other", text))
        return text

    def desc(self, sp, key, name=None, team=None):
        self.keys["orphan"].add(("desc", key))
        return DESC[key].format(n=name, t=team)

    def named_desc(self, rng, sp, uid):
        """A description that names this active person by full name."""
        sp.named.add(uid)
        return self.desc(sp, rng.choice(FULL_KEYS), self.full(uid), self.U[uid]["team"])

    # ---- edges
    def free(self, a, b):
        return a != b and ((a, b) if a < b else (b, a)) not in self.pairs

    def link(self, a, b, typ="upstream_of"):
        assert self.free(a, b), (a, b)
        self.pairs.add((a, b) if a < b else (b, a))
        self.edges.append((a, b, typ))

    def up(self, uid, sp):          # this person's assets that can sit upstream of the orphan
        return [a for a in self.owned_by[uid] if self.A[a]["rank"] < sp.rank and self.free(a, sp.aid)]

    def down(self, uid, sp):
        return [a for a in self.owned_by[uid] if self.A[a]["rank"] > sp.rank and self.free(a, sp.aid)]

    def parents(self, uid, sp):     # this person's dashboards/pipelines, which can 'contain' the orphan
        return [a for a in self.owned_by[uid] if self.A[a]["type"] in ("dashboard", "pipeline")
                and self.free(a, sp.aid)]


# Noise layers. Each value list is cycled within a case and shuffled, so every case gets every value.
OVERLAYS = [
    ("domain", DOMAINS),
    ("type", TYPE_CYCLE),
    ("svc", ["none", "low", "low", "high"]),                       # robot noise
    ("querier", ["normal"] * 4 + ["heavy_same", "heavy_other"]),      # an extra heavy reader (never an editor)
    ("edge", ["none", "none", "upstream", "downstream", "contains", "orphan"]),
    ("descmode", ["empty", "generic", "generic"]),                 # used when the description is not evidence
    ("inactive_query", [False, False, False, True]),
    ("window", ["early", "mid", "late", "spread", "spread"]),
]


# ------------------------------------------------------------------ step 1: users
def make_users(ctx):
    rng = ctx.rng
    firsts = list(FIRST)
    rng.shuffle(firsts)
    ctx.shared_name = {t: firsts.pop() for t in TEAMS}      # two active teammates share it
    ctx.ai_name = {t: firsts.pop() for t in TEAMS}          # an active and an inactive teammate share it
    ctx.cross_names = sorted(firsts.pop() for _ in range(N_CROSS_NAMES))   # one holder in every team
    people = []                                             # [first, team, active]
    for t in TEAMS:
        team = [[ctx.shared_name[t], t, True], [ctx.shared_name[t], t, True],
                [ctx.ai_name[t], t, True], [ctx.ai_name[t], t, False]]
        team += [[f, t, True] for f in ctx.cross_names]
        team.append([firsts.pop(), t, False])               # second inactive person, unique first name
        while len(team) < TEAM_SIZE.get(t, 39):
            team.append([firsts.pop(), t, True])            # globally unique first names
        people += team
    dup_teams = sorted(rng.sample(TEAMS, 2))                # one duplicated full name, on two teams
    dup_first, dup_last = ctx.cross_names[0], rng.choice(LAST)
    ctx.dup_name, ctx.dup_domains = f"{dup_first} {dup_last}", [DOMAIN_OF[t] for t in dup_teams]
    used = {ctx.dup_name}
    recs = []
    for f, t, active in people:
        if f == dup_first and t in dup_teams:
            name = ctx.dup_name
        else:
            name = f"{f} {rng.choice(LAST)}"
            while name in used:
                name = f"{f} {rng.choice(LAST)}"
            used.add(name)
        recs.append({"name": name, "team": t, "user_type": "person", "active": active})
    recs += [{"name": n, "team": t, "user_type": "service", "active": True} for n, t in SERVICES]
    rng.shuffle(recs)                                       # ids must not group inactive users or robots
    for i, r in enumerate(recs, 1):
        ctx.U[f"u_{i:03d}"] = {"record_type": "user", "id": f"u_{i:03d}", **r}
    ids = sorted(ctx.U)
    is_person = lambda u: ctx.U[u]["user_type"] == "person"
    ctx.svc = {ctx.U[u]["name"]: u for u in ids if not is_person(u)}
    ctx.active = {t: [u for u in ids if is_person(u) and ctx.U[u]["active"] and ctx.U[u]["team"] == t] for t in TEAMS}
    ctx.inactive = {t: [u for u in ids if is_person(u) and not ctx.U[u]["active"] and ctx.U[u]["team"] == t] for t in TEAMS}
    ctx.active_all = [u for u in ids if is_person(u) and ctx.U[u]["active"]]
    ctx.inactive_all = [u for u in ids if is_person(u) and not ctx.U[u]["active"]]
    ctx.first_count = collections.Counter(ctx.first(u) for u in ids if is_person(u))
    ctx.dup_ids = {u for u in ids if ctx.U[u]["name"] == ctx.dup_name}
    ctx.cross_holder = {(ctx.first(u), ctx.U[u]["team"]): u for u in ctx.active_all if ctx.first(u) in ctx.cross_names}


# ------------------------------------------------------------------ step 2: plan orphans, schedule noise
OPTION_WORDS = {  # the options column of scenarios.py
    "edges_fixed": {"edges_fixed": True},
    "no_noise": {"no_noise": True},
    "table_or_view": {"types": ["table", "view"]},
    "finance_or_risk": {"domains": ["finance", "risk"]},
    "not_dup_domains": {"not_dup_domains": True},
}


def option_dict(words):
    out = {}
    for w in words.split():
        out.update(OPTION_WORDS[w])
    return out


def plan_orphans(ctx):
    specs = [Spec(c, v, k, truth, RECIPES[c], option_dict(opts))
             for c, v, n, truth, _description, opts in SCENARIOS for k in range(n)]
    assert all(n % 2 == 0 for _, _, n, *_ in SCENARIOS), "scenario counts must be even"
    by_case = collections.defaultdict(list)
    for sp in specs:
        by_case[sp.case].append(sp)
    for dim, values in OVERLAYS:
        for case in sorted(by_case):
            vals = [values[i % len(values)] for i in range(len(by_case[case]))]
            ctx.rng.shuffle(vals)
            for sp, val in zip(by_case[case], vals):
                setattr(sp, dim, val)
    for sp in specs:                                        # recipe constraints override the schedule
        allowed = sp.opts.get("domains")
        if sp.opts.get("not_dup_domains"):
            allowed = [d for d in DOMAINS if d not in ctx.dup_domains]
        if allowed and sp.domain not in allowed:
            sp.domain = allowed[sp.k % len(allowed)]
        if "types" in sp.opts and sp.type not in sp.opts["types"]:
            sp.type = sp.opts["types"][sp.k % len(sp.opts["types"])]
        sp.team = TEAM[sp.domain]
        sp.lo, sp.hi = (d * DAY for d in WINDOWS[sp.window])
    return specs


# ------------------------------------------------------------------ step 3: assets, owners, lineage rank
def make_assets(ctx, specs):
    rng = ctx.rng
    slots = [None] * N_OWNED + specs
    rng.shuffle(slots)                                      # orphans are scattered across the id range
    names = {}
    for d in DOMAINS:
        names[d] = [f"warehouse.{d}.{s}_{x}" for s in STEMS for x in SUFFIXES]
        rng.shuffle(names[d])
    owner_cycle = {}
    for d in DOMAINS:
        owner_cycle[d] = list(ctx.active[TEAM[d]])
        rng.shuffle(owner_cycle[d])
    owned_ranks = list(range(N_OWNED))
    rng.shuffle(owned_ranks)
    n_owned = 0
    for i, slot in enumerate(slots, 1):
        aid = f"a_{i:04d}"
        if slot is None:                                    # an owned (background) asset
            d = DOMAINS[n_owned % len(DOMAINS)]
            owner = owner_cycle[d][(n_owned // len(DOMAINS)) % len(owner_cycle[d])]
            if rng.random() < 0.01:
                owner = rng.choice(ctx.inactive[TEAM[d]])   # stale catalogue entry; also a lineage decoy
            a = {"id": aid, "domain": d, "type": rng.choice(TYPE_CYCLE), "owner": owner,
                 "rank": owned_ranks[n_owned] * 10, "version": rng.randint(2, 4) if rng.random() < 0.1 else 1}
            n_owned += 1
        else:                                               # an orphan; ranks sit in the middle of the lineage
            slot.aid, slot.rank = aid, rng.randrange(N_OWNED // 5, N_OWNED * 4 // 5) * 10 + 5
            a = {"id": aid, "domain": slot.domain, "type": slot.type, "owner": None, "rank": slot.rank,
                 "version": rng.randint(2, 4) if rng.random() < 0.1 else 1}
        a["name"], a["desc"] = names[a["domain"]].pop(), ""
        ctx.A[aid] = a
    ids = sorted(ctx.A)
    ctx.owned = [a for a in ids if ctx.A[a]["owner"]]
    ctx.owned_dom = {d: [a for a in ctx.owned if ctx.A[a]["domain"] == d] for d in DOMAINS}
    ctx.owned_by = collections.defaultdict(list)
    for a in ctx.owned:
        ctx.owned_by[ctx.A[a]["owner"]].append(a)


# ------------------------------------------------------------------ step 4: splits
def make_splits(ctx, specs):
    """Half of every scenario goes to calibration, half to held_out. Done before the orphans are built,
    so a recipe knows whether it may use a wording kept for held_out."""
    strata = collections.defaultdict(list)
    for sp in specs:
        strata[(sp.case, sp.variant)].append(sp)
    for key in sorted(strata):
        group = sorted(strata[key], key=lambda sp: sp.aid)
        ctx.rng.shuffle(group)
        half = len(group) // 2
        for i, sp in enumerate(group):
            sp.split, sp.split_pos = ("calibration", i) if i < half else ("held_out", i - half)


# ------------------------------------------------------------------ step 5: orphans (recipe + noise)
def add_noise(ctx, rng, sp):
    """Robots and readers. Never a person's edit or incident, never a name."""
    S = ctx.svc
    if sp.svc != "none":
        high = sp.svc == "high"
        if sp.svc_edits:
            ctx.add(sp, rng, S["svc_airflow"], "edit", rng.randint(3, 6) if high else rng.randint(0, 3))
            if high and rng.random() < 0.5:
                ctx.add(sp, rng, S["svc_dbt"], "edit", rng.randint(1, 3))
            if sp.domain in OWN_SVC and rng.random() < 0.25:
                ctx.add(sp, rng, S[OWN_SVC[sp.domain]], "edit", rng.randint(1, 3))
        for q in rng.sample(QUERY_SVC, 2 if high else 1):
            ctx.add(sp, rng, S[q], "query", rng.randint(4, 9) if high else rng.randint(2, 6))
    taken = set(sp.exclude) | {s[1] for s in sp.sigs}
    readers = [rng.random() < 0.3 for _ in range(rng.randint(1, 4))]
    if sp.querier != "normal":
        readers.append(sp.querier == "heavy_same")
    for n, same_team in enumerate(readers):
        heavy = sp.querier != "normal" and n == len(readers) - 1
        r = ctx.person(rng, team=sp.team if same_team else None, not_team=None if same_team else sp.team, exclude=taken)
        taken.add(r)
        ctx.add(sp, rng, r, "query", rng.randint(15, 25) if heavy else rng.randint(1, 3))
    if sp.inactive_query:
        gone = [u for u in ctx.inactive_all if u not in taken]
        ctx.add(sp, rng, rng.choice(gone), "query", rng.randint(1, 3))


def add_edge_overlay(ctx, rng, sp):
    """One neighbour for orphans whose recipe did not fix the edges."""
    if sp.opts.get("edges_fixed") or sp.edge in ("none", "orphan"):
        return
    mode = sp.edge if (sp.edge != "contains" or sp.type in ("table", "view")) else "upstream"
    # when the answer is none, a neighbour's owner must not be anyone who touches or is named on this asset
    banned = ({s[1] for s in sp.sigs} | sp.exclude | sp.named) if sp.truth is None else set()
    dom = sp.domain if rng.random() < 0.8 else rng.choice([d for d in DOMAINS if d != sp.domain])
    ok = {"upstream": lambda a: ctx.A[a]["rank"] < sp.rank, "downstream": lambda a: ctx.A[a]["rank"] > sp.rank,
          "contains": lambda a: ctx.A[a]["type"] in ("dashboard", "pipeline")}[mode]
    c = [a for a in ctx.owned_dom[dom] if ok(a) and ctx.A[a]["owner"] not in banned and ctx.free(a, sp.aid)]
    if not c:
        return
    a = rng.choice(c)
    if mode == "downstream":
        ctx.link(sp.aid, a)
    else:
        ctx.link(a, sp.aid, "contains" if mode == "contains" else "upstream_of")


def build_orphans(ctx, specs):
    for sp in specs:                                        # table order; each orphan has its own generator
        rng = random.Random(f"{ctx.seed}/{sp.key}")
        sp.recipe(ctx, rng, sp)
        assert (sp.truth is not None) == (sp.truth_kind == PERSON), f"{sp.key}: truth does not match scenarios.py"
        if not sp.opts.get("no_noise"):
            add_noise(ctx, rng, sp)
        if sp.desc is None:
            sp.desc = "" if sp.descmode == "empty" else rng.choice(GENERIC_DESC)
        add_edge_overlay(ctx, rng, sp)
        ctx.A[sp.aid]["desc"] = sp.desc
    # orphan-orphan edges: every 'orphan_neighbour' orphan gets one; the rest pair up among themselves
    rng = random.Random(f"{ctx.seed}/orphan_edges")
    by_id = sorted(specs, key=lambda s: s.aid)
    need = [s for s in by_id if s.variant == "orphan_neighbour"]
    pool = [s for s in by_id if s.edge == "orphan" and not s.opts.get("edges_fixed")]
    rng.shuffle(pool)
    pairs = [(n, pool.pop()) for n in need]
    pairs += [(pool[i], pool[i + 1]) for i in range(0, len(pool) - 1, 2)]
    for x, y in pairs:
        lo, hi = sorted((x, y), key=lambda s: (s.rank, s.aid))
        if ctx.free(lo.aid, hi.aid):
            ctx.link(lo.aid, hi.aid)


# ------------------------------------------------------------------ step 6: background (owned assets)
def build_background(ctx, specs):
    """Owned assets. Reuses every template, robot behaviour and count range seen on orphans."""
    rng, U, S = ctx.rng, ctx.U, ctx.svc
    sig = ctx.bg_sigs

    def add(aid, uid, typ, n, lo=0, hi=91, note=None):
        for _ in range(n):
            text = ctx.auto_note(rng, uid, typ, "owned") if note is None else note
            sig.append((aid, uid, typ, ctx.second(rng, lo, hi), text))

    def keyed(key, extra=""):
        ctx.keys["owned"].add(("note", key, extra))

    # the largest per-user count on any orphan, for each (who, signal type)
    per = collections.Counter((s[0], ctx.kind(s[1]), s[2], s[1]) for sp in specs for s in sp.sigs)
    omax = collections.Counter()
    for (aid, kind, typ, uid), n in per.items():
        omax[(kind, typ)] = max(omax[(kind, typ)], n)
    pool = [a for a in ctx.owned if U[ctx.A[a]["owner"]]["active"]]
    rng.shuffle(pool)

    def take(n, pred=lambda a: True):                       # n distinct owned assets for one planted pattern
        out = [a for a in pool if pred(a)][:n]
        for a in out:
            pool.remove(a)
        return out

    plants = collections.defaultdict(list)
    for key in sorted(omax):                                # heavy tail: 18 assets at/above the orphan maximum
        m = omax[key]
        for a in take(18):
            plants[a].append(("mag", key, rng.randint(m, m + 2)))
        for a in take(18):
            plants[a].append(("mag", key, rng.randint(max(1, m // 2), m)))
    for ty in ["table", "view", "pipeline", "dashboard"]:   # every note template, on every asset type it can name
        for a in take(8, lambda a: ctx.A[a]["type"] == ty):
            plants[a].append(("esc",))
    for tag, n in [("questions", 12), ("self_claim", 12), ("hand_sep", 10), ("hand_oct", 10), ("disown", 12),
                   ("edit_note", 12), ("query_note", 6)]:
        for a in take(n):
            plants[a].append((tag,))
    for kind in sorted(OTHER_WORDINGS):                     # every other wording, on a few owned assets each
        for text in OTHER_WORDINGS[kind][0] + OTHER_WORDINGS[kind][1]:
            for a in take(5):
                plants[a].append(("other", kind, text))

    for aid in ctx.owned:
        A = ctx.A[aid]
        owner, team, dom = A["owner"], TEAM[A["domain"]], A["domain"]
        pl = plants.get(aid, [])
        tags = {p[0] for p in pl}
        mags = {p[1]: p[2] for p in pl if p[0] == "mag"}
        lo, hi = (0, 91) if U[owner]["active"] else (0, 30)  # an inactive owner only has old activity
        other = [(p[1], p[2]) for p in pl if p[0] == "other"]
        ctx.keys["owned"].update(("other", text) for _, text in other)
        hand = "hand_oct" if "hand_oct" in tags else ("hand_sep" if "hand_sep" in tags else None)
        hand_text = NOTE[hand] if hand else next((text for kind, text in other if kind == "hands_over"), None)
        if hand_text:                                       # previous editor hands over, owner edits afterwards
            day = 62 if hand == "hand_oct" else 45
            old = ctx.person(rng, team=team, exclude={owner})
            add(aid, old, "edit", rng.randint(2, 3), 0, day - 1)
            add(aid, old, "edit", 1, day - 1, day, hand_text.format(n=ctx.full(owner)))
            if hand:
                keyed(hand)
            lo = day
        n_edit = mags.get(("person", "edit"), rng.randint(*BG["owner_edits"]))
        n_inc = mags.get(("person", "incident_response"), int(rng.random() < BG["incident_p"]))
        add(aid, owner, "edit", n_edit, lo, hi)
        add(aid, owner, "incident_response", n_inc, lo, hi)
        add(aid, S["svc_airflow"], "edit", rng.randint(*BG["airflow_edits"]))
        if rng.random() < BG["dbt_p"]:
            add(aid, S["svc_dbt"], "edit", rng.randint(*BG["svc_extra_edits"]))
        if dom in OWN_SVC and rng.random() < BG["own_svc_p"]:
            add(aid, S[OWN_SVC[dom]], "edit", rng.randint(*BG["svc_extra_edits"]))
        add(aid, S[rng.choice(QUERY_SVC)], "query", rng.randint(*BG["svc_queries"]))
        taken = {owner}
        for _ in range(rng.randint(*BG["readers"])):
            same = rng.random() < 0.3
            r = ctx.person(rng, team=team if same else None, not_team=None if same else team, exclude=taken)
            taken.add(r)
            add(aid, r, "query", rng.randint(*BG["reader_queries"]))
        if rng.random() < 0.10:
            add(aid, owner, "query", rng.randint(1, 3))
        if rng.random() < 0.05 and n_edit >= 3:             # an occasional edit by a teammate
            add(aid, ctx.person(rng, team=team, exclude={owner}), "edit", 1)
        if rng.random() < 0.03:
            add(aid, rng.choice(ctx.inactive_all), "query", rng.randint(1, 2))
        # ---- planted patterns
        for (kind, typ), v in sorted(mags.items()):
            if (kind, typ) == ("person", "query"):
                r = rng.random()
                who = owner if r < 0.2 else ctx.person(rng, team=team if r < 0.4 else None,
                                                       not_team=None if r < 0.4 else team, exclude=taken)
                add(aid, who, "query", v)
            elif kind == "service":
                names = QUERY_SVC if typ == "query" else ["svc_airflow", "svc_dbt"] + ([OWN_SVC[dom]] if dom in OWN_SVC else [])
                add(aid, S[rng.choice(names)], typ, v)
            elif kind == "inactive":                        # a departed colleague; the owner took over since
                gone = rng.choice(ctx.inactive_all if typ == "query" else ctx.inactive[team])
                add(aid, gone, typ, v, 0, 91 if typ == "query" else 25)
        if "esc" in tags:
            w = ctx.person(rng, not_team=team)
            add(aid, w, "incident_response", 1, note=NOTE["esc"].format(n=ctx.full(owner), ty=A["type"]))
            keyed("esc", A["type"])
        if "questions" in tags:
            add(aid, ctx.person(rng, exclude={owner}), "query", 1, note=NOTE["questions"].format(n=ctx.full(owner)))
            keyed("questions")
        if "self_claim" in tags:                            # the owner's own incident note
            add(aid, owner, "incident_response", 1, lo, hi, NOTE["self_claim"])
            keyed("self_claim")
        if "disown" in tags:                                # a former occasional editor steps back
            old = ctx.person(rng, team=team, exclude={owner})
            add(aid, old, "edit", rng.randint(1, 2), 0, 30)
            add(aid, old, rng.choice(["incident_response", "edit", "query"]), 1, 30, 40, NOTE["disown"])
            keyed("disown")
        for kind, text in other:                            # other wordings, always consistent with the real owner
            if kind == "names_note":
                add(aid, ctx.person(rng, not_team=team), "incident_response", 1, note=text.format(n=ctx.full(owner)))
            elif kind == "disowns":
                old = ctx.person(rng, team=team, exclude={owner})
                add(aid, old, "edit", rng.randint(1, 2), 0, 30)
                add(aid, old, rng.choice(["incident_response", "edit", "query"]), 1, 30, 40, text)
            elif kind == "harmless_note":
                add(aid, ctx.person(rng, exclude={owner}), "query", 1, note=text.format(f=ctx.first(ctx.person(rng))))
        if "edit_note" in tags:
            text = EDIT_NOTES[len(sig) % len(EDIT_NOTES)]
            add(aid, owner, "edit", 1, lo, hi, text)
            ctx.keys["owned"].add(("note", text))
        if "query_note" in tags:
            add(aid, ctx.person(rng, exclude={owner}), "query", 1, note=QUERY_NOTES[0])
            ctx.keys["owned"].add(("note", QUERY_NOTES[0]))
        # ---- description: 30% empty, 55% generic, 15% names the owner (or the owner's team)
        r = rng.random()
        if r < 0.30:
            A["desc"] = ""
        elif r < 0.85:
            A["desc"] = rng.choice(GENERIC_DESC)
        else:
            key = rng.choice(sorted(DESC))
            alone = sum(1 for u in ctx.active[team] + ctx.inactive[team] if ctx.first(u) == ctx.first(owner)) == 1
            first_only = key in SHORT_KEYS and rng.random() < 0.2 and alone and U[owner]["team"] == team
            A["desc"] = DESC[key].format(n=ctx.first(owner) if first_only else ctx.full(owner), t=U[owner]["team"])
            ctx.keys["owned"].add(("desc", key))
        for kind, text in other:
            if kind in ("names_desc", "harmless_desc"):
                A["desc"] = text.format(n=ctx.full(owner))

    # every robot / inactive user who does something on an orphan also does it on an owned asset
    seen = {(s[1], s[2]) for s in sig}
    need = sorted({(s[1], s[2]) for sp in specs for s in sp.sigs if ctx.kind(s[1]) != "person"} - seen)
    for uid, typ in need:
        aid = rng.choice(ctx.owned_dom[DOMAIN_OF[U[uid]["team"]]])
        add(aid, uid, typ, rng.randint(1, 2), 0, 25 if ctx.kind(uid) == "inactive" else 91)
    missing = ctx.keys["orphan"] - ctx.keys["owned"] - {("note", "")}
    assert not missing, f"templates used only on orphans: {sorted(missing)}"

    # ---- background lineage: mostly same-domain upstream_of, some cross-domain, some contains
    rank = lambda a: ctx.A[a]["rank"]
    for aid in ctx.owned:
        dom = ctx.A[aid]["domain"]
        picks = []
        if rng.random() < 0.55:
            picks.append((rng.choice(ctx.owned_dom[dom]), "upstream_of"))
        if rng.random() < 0.05:
            picks.append((rng.choice(ctx.owned_dom[rng.choice([d for d in DOMAINS if d != dom])]), "upstream_of"))
        if ctx.A[aid]["type"] in ("table", "view") and rng.random() < 0.10:
            parent = rng.choice(ctx.owned_dom[dom])
            if ctx.A[parent]["type"] in ("dashboard", "pipeline") and ctx.free(parent, aid):
                ctx.link(parent, aid, "contains")
        for other, typ in picks:
            if ctx.free(aid, other):
                a, b = sorted((aid, other), key=rank)       # always low rank -> high rank: no cycles
                ctx.link(a, b, typ)


# ------------------------------------------------------------------ step 7: changes made by people
def make_changes(ctx, specs):
    rng, U = ctx.rng, ctx.U
    S = sorted(specs, key=lambda s: s.aid)
    by_owner = collections.defaultdict(list)
    for sp in S:
        if sp.truth:
            by_owner[sp.truth].append(sp)
    used_a, used_u = set(), set()

    def claim_user(pred):
        u = rng.choice([u for u in sorted(by_owner) if u not in used_u and pred(by_owner[u])])
        used_u.add(u)
        used_a.update(sp.aid for sp in by_owner[u])         # their orphans stay untouched by other changes
        return u

    def claim(pred, n=1):
        out = rng.sample([sp for sp in S if sp.aid not in used_a and pred(sp)], n)
        used_a.update(sp.aid for sp in out)
        return out

    def teammate(sp, other_team=False, avoid=()):
        return ctx.person(rng, team=None if other_team else sp.team, not_team=sp.team if other_team else None,
                          exclude=used_u | set(avoid))

    likely = ("clear_owner", "cross_team_decoy")
    # --- users to deactivate later (chosen first so their orphans are reserved)
    u_multi = claim_user(lambda l: len(l) >= 2)                               # owner of several live orphans
    u_del = claim_user(lambda l: len(l) >= 2 and any(sp.case in likely for sp in l))
    del_sp = [sp for sp in by_owner[u_del] if sp.case in likely][0]           # this one gets deleted too
    singles = [claim_user(lambda l: len(l) == 1 and l[0].case == "clear_owner") for _ in range(3)]
    u_text = claim_user(lambda l: any(sp.case == "text_only_evidence" for sp in l))
    # --- set_owner
    sets = []
    cands = [sp for sp in S if sp.aid not in used_a and sp.truth and sp.truth not in used_u
             and sp.case in ("clear_owner", "text_only_evidence", "cross_team_owner")]
    rng.shuffle(cands)
    same = []                                               # four orphans, four different truth owners
    for sp in cands:
        if len(same) < 4 and sp.truth not in used_u:
            same.append(sp)
            used_a.add(sp.aid)
            used_u.add(sp.truth)
    assert len(same) == 4
    u_hs = same[0].truth                                    # a human-set owner who is deactivated later
    sets += [(sp.aid, sp.truth) for sp in same]
    sets += [(sp.aid, teammate(sp, avoid=[sp.truth])) for sp in claim(lambda sp: sp.truth, 3)]      # different owner
    sets += [(sp.aid, teammate(sp)) for sp in claim(lambda sp: sp.truth is None, 3)]                # on a none orphan
    sets += [(sp.aid, teammate(sp, other_team=True, avoid=[sp.truth])) for sp in claim(lambda sp: sp.truth)]
    sets += [(sp.aid, rng.choice(ctx.inactive[sp.team])) for sp in claim(lambda sp: True)]          # inactive target
    set_del = claim(lambda sp: sp.truth and sp.case == "clear_owner")[0]                            # set, then delete
    sets.append((set_del.aid, teammate(set_del, avoid=[set_del.truth])))
    sets.append((same[1].aid, same[1].truth))                                                       # exact repeat
    orphan_ids = {sp.aid for sp in S}
    neighbours = sorted({a for a, b, _ in ctx.edges if b in orphan_ids and a not in orphan_ids})
    gone_neighbour = rng.choice(neighbours)                 # an owned upstream neighbour that gets deleted
    owned_pick = rng.choice([a for a in ctx.owned if a != gone_neighbour and U[ctx.A[a]["owner"]]["active"]])
    sets.append((owned_pick, ctx.person(rng, team=TEAM[ctx.A[owned_pick]["domain"]],
                                        exclude=used_u | {ctx.A[owned_pick]["owner"]})))            # an owned asset
    # --- delete_asset
    deletes = [del_sp.aid]
    deletes += [sp.aid for sp in claim(lambda sp: sp.truth and sp.case in likely, 2)]
    deletes += [sp.aid for sp in claim(lambda sp: sp.truth and sp.case == "text_only_evidence")]
    deletes += [sp.aid for sp in claim(lambda sp: sp.truth is None)]
    deletes += [set_del.aid, gone_neighbour]
    # --- deactivate_user
    tied = rng.choice([u for sp in S if sp.case == "tied_evidence" for u in sp.principals if u not in used_u])
    used_u.add(tied)
    deactivate = singles + [u_multi, u_del, u_text, u_hs, tied]
    # --- one late set_owner that names a user who was just deactivated
    late = claim(lambda sp: sp.truth)[0]

    out = []

    def rec(op, actor, **kw):
        out.append({"record_type": "change", "change_id": f"c_{len(out) + 1:03d}", "op": op, **kw, "actor": actor})
    for aid, owner in sets:
        rec("set_owner", "human:steward", asset_id=aid, owner=owner)
    for aid in deletes:
        rec("delete_asset", "human:steward", asset_id=aid)
    for uid in deactivate:
        rec("deactivate_user", "human:it-admin", user_id=uid)
    rec("set_owner", "human:steward", asset_id=late.aid, owner=singles[0])
    return out


# ------------------------------------------------------------------ step 8: write
def ts(sec):
    return (START + timedelta(seconds=sec)).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_jsonl(path, records):
    with open(path, "w", newline="\n", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")


def generate(seed, out):
    assert len(FIRST) == len(set(FIRST)) and not set(FIRST) & set(UNKNOWN_FIRST)
    ctx = Ctx(seed)
    make_users(ctx)
    specs = plan_orphans(ctx)
    make_assets(ctx, specs)
    make_splits(ctx, specs)
    build_orphans(ctx, specs)
    build_background(ctx, specs)
    changes = make_changes(ctx, specs)

    # safety net: when the answer is none, no neighbour's owner touches or is named on the asset
    owner_of = lambda a: ctx.A[a]["owner"]
    for sp in specs:
        if sp.truth is None:
            involved = {s[1] for s in sp.sigs} | sp.named
            near = {owner_of(b if a == sp.aid else a) for a, b, _ in ctx.edges if sp.aid in (a, b)}
            assert not (near & involved), f"{sp.key}: a neighbour's owner is involved in a none case"

    signals = sorted(ctx.bg_sigs + [s for sp in specs for s in sp.sigs], key=lambda s: (s[3], s[0], s[1], s[2], s[4]))
    assert SIGNAL_BAND[0] <= len(signals) <= SIGNAL_BAND[1], f"{len(signals)} signals: retune BG"
    assets = [{"record_type": "asset", "id": a["id"], "qualified_name": a["name"], "type": a["type"],
               "description": a["desc"], "owner": a["owner"], "owner_set_by": "human" if a["owner"] else None,
               "version": a["version"]} for a in (ctx.A[i] for i in sorted(ctx.A))]
    users = [ctx.U[u] for u in sorted(ctx.U)]
    edges = [{"record_type": "edge", "edge_id": f"e_{i:04d}", "from_id": a, "to_id": b, "edge_type": t}
             for i, (a, b, t) in enumerate(sorted(ctx.edges), 1)]
    sigs = [{"record_type": "signal", "signal_id": f"s_{i:05d}", "asset_id": a, "user_id": u, "signal_type": t,
             "ts": ts(sec), "note": note} for i, (a, u, t, sec, note) in enumerate(signals, 1)]
    by_id = sorted(specs, key=lambda s: s.aid)
    truth = [{"record_type": "truth", "asset_id": sp.aid, "owner": sp.truth, "case": sp.case, "split": sp.split}
             for sp in by_id]
    manifest = [{"record_type": "variant", "asset_id": sp.aid, "case": sp.case, "variant": sp.variant} for sp in by_id]

    os.makedirs(os.path.join(out, "eval"), exist_ok=True)
    files = {"seed.jsonl": assets + users + edges + sigs, "truth.jsonl": truth, "changes.jsonl": changes,
             "eval/manifest.jsonl": manifest}
    sums = []
    for name in sorted(files):
        path = os.path.join(out, *name.split("/"))
        write_jsonl(path, files[name])
        with open(path, "rb") as fh:
            sums.append(f"{hashlib.sha256(fh.read()).hexdigest()}  {name}\n")
    with open(os.path.join(out, "SHA256SUMS"), "w", newline="\n") as fh:
        fh.writelines(sums)
    none = sum(1 for sp in specs if sp.truth is None)
    print(f"seed={seed}: {len(assets)} assets, {len(users)} users, {len(edges)} edges, {len(sigs)} signals, "
          f"{len(specs)} orphans ({none} with truth none), {len(changes)} changes -> {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default="fixture")
    args = ap.parse_args()
    generate(args.seed, args.out)
