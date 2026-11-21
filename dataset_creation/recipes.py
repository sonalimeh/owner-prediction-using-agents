"""How the evidence is built for each case: one function per case.

Every function takes (ctx, rng, sp):
  ctx  the shared state from generate.py (users, assets, helpers such as ctx.person and ctx.add)
  rng  this orphan's own random generator
  sp   the orphan being built; sp.variant says which scenario (see scenarios.py)

Each function decides the right answer FIRST (sp.truth = a user id, or leaves it None), then writes the
evidence that produces it: signals, description, notes, edges. Noise is added later by generate.py and
can never change the answer.

The functions only call ctx helpers and use the fixed lists in vocabulary.py.
"""
from vocabulary import (DAY, DOMAINS, FULL_KEYS, GENERIC_DESC, OWN_SVC, QUERY_SVC, SHORT_KEYS,
                        UNKNOWN_FIRST, UNKNOWN_LAST, WINDOW)


def _harmless_text(ctx, rng, sp):
    """A note or a description in words the rules don't know, saying nothing about ownership."""
    if sp.k % 2 == 0:
        reader = ctx.person(rng, exclude=sp.exclude)
        text = ctx.other_wording(rng, sp, "harmless_note").format(f=ctx.first(ctx.person(rng)))
        ctx.say(sp, rng, reader, "query", text)
    else:
        sp.desc = ctx.other_wording(rng, sp, "harmless_desc")


def clear_owner(ctx, rng, sp):
    v = sp.variant
    pred = {"lineage_agrees": lambda u: bool(ctx.up(u, sp)),
            "contains_parent_is_editor": lambda u: bool(ctx.parents(u, sp))}.get(v)
    o = ctx.person(rng, team=sp.team, pred=pred)
    sp.truth = o
    # basic: just this common part (the owner edits 4-8 times, sometimes with 1 incident).
    # Every other clear_owner scenario below changes or adds to it.
    e, i = rng.randint(4, 8), (1 if rng.random() < 0.3 else 0)
    if v == "thin":
        e, i = 2, 0
    elif v == "single_edit":                        # one edit and nobody else: a lone candidate is the answer
        e, i = 1, 0
    elif v == "incident_heavy":
        e, i = 2, 2
    elif v == "incidents_only":
        e, i = 0, rng.randint(2, 3)
    elif v == "minor_second_editor":
        e = rng.randint(6, 8)
    elif v == "half_second_editor":                 # the owner makes exactly twice the teammate's edits
        e = 2 * rng.randint(4, 5)
    elif v == "svc_outedits_owner":
        e = rng.randint(4, 6)
    ctx.add(sp, rng, o, "edit", e)
    ctx.add(sp, rng, o, "incident_response", i)
    sp.exclude.add(o)
    if v in ("heavy_same_team_reader", "heavy_other_team_reader"):
        same = v == "heavy_same_team_reader"
        r = ctx.person(rng, team=sp.team if same else None, not_team=None if same else sp.team, exclude=sp.exclude)
        ctx.add(sp, rng, r, "query", rng.randint(15, 25))
        sp.exclude.add(r)
        sp.querier = "normal"
    elif v == "owner_is_top_reader":
        ctx.add(sp, rng, o, "query", rng.randint(15, 25))
        sp.querier = "normal"
    elif v == "svc_outedits_owner":
        ctx.add(sp, rng, ctx.svc[rng.choice(["svc_airflow", "svc_dbt"])], "edit", e + rng.randint(2, 6))
    elif v == "minor_second_editor":
        m = ctx.person(rng, team=sp.team, exclude=sp.exclude)
        ctx.add(sp, rng, m, "edit", rng.randint(1, 2))
        sp.exclude.add(m)
    elif v == "half_second_editor":                 # e.g. 8 edits vs 4: 16 points vs 8, a clear lead
        m = ctx.person(rng, team=sp.team, exclude=sp.exclude)
        ctx.add(sp, rng, m, "edit", e // 2)
        sp.exclude.add(m)
    elif v == "lineage_agrees":
        ctx.link(rng.choice(ctx.up(o, sp)), sp.aid)
    elif v == "contains_parent_is_editor":
        ctx.link(rng.choice(ctx.parents(o, sp)), sp.aid, "contains")
    elif v == "agreeing_description":
        sp.desc = ctx.named_desc(rng, sp, o)
    elif v == "harmless_text":                      # unread text that changes nothing
        _harmless_text(ctx, rng, sp)


def service_or_inactive_top_user(ctx, rng, sp):
    v, S = sp.variant, ctx.svc
    worker = lambda lo, hi: ctx.add(sp, rng, sp.truth, "edit", rng.randint(lo, hi))
    if v in ("svc_top_editor", "svc_top_editor_and_querier", "own_team_service_top"):
        sp.truth = ctx.person(rng, team=sp.team)
        worker(2, 3)
        ctx.add(sp, rng, sp.truth, "incident_response", 1)
        if v == "own_team_service_top":
            ctx.add(sp, rng, S[OWN_SVC[sp.domain]], "edit", rng.randint(12, 25))
            sp.svc = "low"
        else:                                       # svc_top_editor, svc_top_editor_and_querier
            ctx.add(sp, rng, S[rng.choice(["svc_airflow", "svc_dbt"])], "edit", rng.randint(15, 30))
        if v == "svc_top_editor_and_querier":
            ctx.add(sp, rng, S[rng.choice(QUERY_SVC)], "query", rng.randint(20, 35))
    elif v in ("service_only_editor", "harmless_text"):
        ctx.add(sp, rng, S[rng.choice(["svc_airflow", "svc_dbt"])], "edit", rng.randint(8, 25))   # robots only, and busy
        if v == "harmless_text":                    # unread text that changes nothing
            _harmless_text(ctx, rng, sp)
    else:   # inactive_top_active_teammate, inactive_only_editor, inactive_with_incident: someone who left edits most
        gone = rng.choice(ctx.inactive[sp.team])
        if sp.k % 2 == 0:
            sp.svc = "none"                         # so the inactive person is also the most frequent user
        if v == "inactive_top_active_teammate":
            ctx.add(sp, rng, gone, "edit", rng.randint(6, 9))
            sp.truth = ctx.person(rng, team=sp.team)
            worker(2, 4)
        elif v == "inactive_only_editor":
            ctx.add(sp, rng, gone, "edit", rng.randint(4, 8))
            r = ctx.person(rng, team=sp.team)       # an active teammate who only reads
            ctx.add(sp, rng, r, "query", rng.randint(5, 9))
            sp.exclude.add(r)
        elif v == "inactive_with_incident":
            ctx.add(sp, rng, gone, "edit", rng.randint(3, 6))
            ctx.add(sp, rng, gone, "incident_response", 1)
    if sp.truth:
        sp.exclude.add(sp.truth)


def readers_only(ctx, rng, sp):
    # heavy_teammate_reader (the only scenario): a teammate reads a lot; nobody edits.
    r = ctx.person(rng, team=sp.team)
    ctx.add(sp, rng, r, "query", rng.randint(15, 25))
    sp.exclude.add(r)
    sp.querier, sp.svc_edits = "normal", False      # nobody edits at all, not even robots


def tied_evidence(ctx, rng, sp):
    v = sp.variant
    a = ctx.person(rng, team=sp.team)
    b = ctx.person(rng, team=None if v == "tied_cross_team" else sp.team,
                   not_team=sp.team if v == "tied_cross_team" else None, exclude={a})
    people = [a, b]
    e, i = rng.randint(3, 5), 0
    if v == "tied_edits_and_incidents":
        i = 1
    elif v == "tied_edits_only":
        e = rng.randint(2, 6)
    elif v == "three_way":
        e, i = rng.randint(2, 4), rng.randint(0, 1)
        people.append(ctx.person(rng, team=sp.team, exclude=set(people)))
    for p in people:                                # identical edits and incidents for everyone
        ctx.add(sp, rng, p, "edit", e)
        ctx.add(sp, rng, p, "incident_response", i)
        ctx.add(sp, rng, p, "query", rng.randint(0, 4))
    sp.exclude.update(people)
    sp.principals = people
    if v == "tied_with_svc_top":
        ctx.add(sp, rng, ctx.svc[rng.choice(["svc_airflow", "svc_dbt"])], "edit", e + rng.randint(4, 14))
    elif v == "tied_third_party_neighbour":         # lineage trap: upstream asset of a third, silent teammate
        t = ctx.person(rng, team=sp.team, exclude=sp.exclude, pred=lambda u: bool(ctx.up(u, sp)))
        ctx.link(rng.choice(ctx.up(t, sp)), sp.aid)
        sp.exclude.add(t)


def near_tie(ctx, rng, sp):
    a = ctx.person(rng, team=sp.team)
    b = ctx.person(rng, team=sp.team, exclude={a})
    if sp.variant == "margin_one":                  # margin_one: e.g. 5 edits vs 4
        e = rng.randint(4, 6)
        ctx.add(sp, rng, a, "edit", e)
        ctx.add(sp, rng, b, "edit", e - 1)
    else:                                           # mixed: 4 edits vs 3 edits + 1 incident
        ctx.add(sp, rng, a, "edit", 4)
        ctx.add(sp, rng, b, "edit", 3)
        ctx.add(sp, rng, b, "incident_response", 1)
    sp.exclude.update((a, b))


def text_only_evidence(ctx, rng, sp):
    """The owner (if any) is identified by text, a description or a note, not by their own edits."""
    v, team, o = sp.variant, sp.team, None
    D = lambda key, name=None, t=None: ctx.desc(sp, key, name, t)
    if sp.svc == "none" and v != "text_no_signals":
        sp.svc = "low"                              # 'only robots edit'
    if v == "desc_maintained_by":
        o = ctx.person(rng, team=team)
        sp.desc = D("maint", ctx.full(o))
    elif v == "desc_built_and_maintained":
        o = ctx.person(rng, team=team)
        sp.desc = D("built", ctx.full(o))
    elif v == "desc_owned_with_team":
        o = ctx.person(rng, team=team)
        sp.desc = D("owned_team", ctx.full(o), team)
    elif v == "varied_wording":
        o = ctx.person(rng, team=team)
        sp.desc = D(rng.choice(["owned", "maintainer"]), ctx.full(o))
    elif v == "text_no_signals":
        o = ctx.person(rng, team=team)
        sp.desc = D(rng.choice(FULL_KEYS), ctx.full(o), team)
    elif v == "note_escalated":                     # an incident note written by someone on another team
        o = ctx.person(rng, team=team)
        w = ctx.person(rng, not_team=team)
        if rng.random() < 0.5:
            sp.desc = "Nightly extract; see runbook."
        ctx.note(sp, rng, w, "incident_response", "esc", ctx.full(o))
        sp.exclude.add(w)
    elif v == "desc_other_wording":                 # the description names o, in words the rules don't know
        o = ctx.person(rng, team=team)
        sp.desc = ctx.other_wording(rng, sp, "names_desc").format(n=ctx.full(o))
    elif v == "note_other_wording":                 # someone else's note names o, in words the rules don't know
        o = ctx.person(rng, team=team)
        w = ctx.person(rng, not_team=team)
        ctx.say(sp, rng, w, "incident_response", ctx.other_wording(rng, sp, "names_note").format(n=ctx.full(o)))
        sp.exclude.add(w)
    elif v == "note_writer_also_edits":             # a teammate edits 3-5 times AND writes that o maintains it
        o = ctx.person(rng, team=team)
        w = ctx.person(rng, team=team, exclude={o})
        ctx.add(sp, rng, w, "edit", rng.randint(3, 5))
        ctx.note(sp, rng, w, "incident_response", "esc", ctx.full(o))
        sp.exclude.add(w)                           # the writer points away from themselves, so they are out
    elif v == "self_claim_note":                    # the person's own incident note: "...; I maintain this job."
        o = ctx.person(rng, team=team)
        ctx.note(sp, rng, o, "incident_response", "self_claim")
    elif v == "note_on_query":
        o = ctx.person(rng, team=team)
        w = ctx.person(rng, exclude={o})
        ctx.note(sp, rng, w, "query", "questions", ctx.full(o))
        sp.exclude.add(w)
    elif v in ("first_name_plus_team", "first_name_team_context"):
        ok = [f for f in ctx.cross_names if ctx.cross_holder[(f, team)] not in ctx.dup_ids]
        f = rng.choice(ok)                          # one person per team has this first name
        holder = ctx.cross_holder[(f, team)]
        if v == "first_name_plus_team":             # "Farah (platform-eng)": the team picks exactly one person
            o = holder
            sp.desc = D("owned_team", f, team)
        else:                                       # first_name_team_context: "Owned by Farah." names nobody
            sp.desc = D(rng.choice(SHORT_KEYS), f)
            sp.named.add(holder)                    # keep them off this asset so no other evidence points to them
            sp.exclude.add(holder)
    elif v == "unique_first_name":                  # "Maintainer: Ingrid.": a first name alone names nobody
        p = ctx.person(rng, team=team, pred=lambda u: ctx.first_count[ctx.first(u)] == 1)
        sp.desc = D(rng.choice(SHORT_KEYS), ctx.first(p))
        sp.named.add(p)
        sp.exclude.add(p)
    elif v == "inactive_editor_names_teammate":
        ctx.add(sp, rng, rng.choice(ctx.inactive[team]), "edit", rng.randint(3, 6))
        o = ctx.person(rng, team=team)
        sp.desc = D(rng.choice(FULL_KEYS), ctx.full(o), team)
    # ---- text that does not single out one active person -> none
    elif v == "names_inactive":
        sp.desc = D(rng.choice(FULL_KEYS), ctx.full(rng.choice(ctx.inactive[team])), team)
    elif v == "shared_first_name_same_team":        # two active teammates share this first name
        sp.desc = D(rng.choice(SHORT_KEYS), ctx.shared_name[team])
    elif v == "first_name_active_inactive":         # an active and an inactive teammate share it
        sp.desc = D(rng.choice(SHORT_KEYS), ctx.ai_name[team])
    elif v == "unknown_name":
        sp.desc = D(rng.choice(["maint", "built", "owned", "maintainer"]),
                    rng.choice(UNKNOWN_FIRST) + " " + rng.choice(UNKNOWN_LAST))
    elif v == "names_service":
        sp.desc = D("maint", rng.choice(["svc_dbt", "svc_airflow"]))
    elif v == "team_only_text":
        sp.desc = D("team_only", None, team)
    elif v == "duplicate_full_name":                # two users with this exact name, neither on this team
        sp.desc = D(rng.choice(["maint", "built", "owned", "maintainer"]), ctx.dup_name)
    else:
        raise ValueError(v)
    if o:
        sp.truth = o
        sp.named.add(o)
        sp.exclude.add(o)                           # the named owner has no signals on the asset


def no_signals(ctx, rng, sp):
    v = sp.variant
    if v == "isolated_empty":
        sp.desc = ""
    elif v == "generic_description":
        sp.desc = rng.choice(GENERIC_DESC)
    elif v == "cross_team_neighbours":
        others = [a for d in DOMAINS if d != sp.domain for a in ctx.owned_dom[d]]
        ctx.link(rng.choice([a for a in others if ctx.A[a]["rank"] < sp.rank]), sp.aid)
        ctx.link(sp.aid, rng.choice([a for a in others if ctx.A[a]["rank"] > sp.rank]))
    elif v == "same_team_neighbour":                # a neighbour's owner alone does not decide
        t = ctx.person(rng, team=sp.team, pred=lambda u: bool(ctx.up(u, sp)))
        ctx.link(rng.choice(ctx.up(t, sp)), sp.aid)
    elif v == "contains_parent_teammate":
        t = ctx.person(rng, team=sp.team, pred=lambda u: bool(ctx.parents(u, sp)))
        ctx.link(rng.choice(ctx.parents(t, sp)), sp.aid, "contains")
    # orphan_neighbour: nothing here. generate.py links it to another orphan (step 4, end of build_orphans),
    # because that needs every orphan's asset id first.


def cross_team_decoy(ctx, rng, sp):
    """A person from another team reads a lot and owns the neighbours; a teammate does the editing."""
    v = sp.variant
    o = ctx.person(rng, team=sp.team)
    need = {"upstream_decoy": lambda u: len(ctx.up(u, sp)) >= 2,
            "contains_decoy": lambda u: bool(ctx.parents(u, sp))}.get(v, lambda u: len(ctx.down(u, sp)) >= 3)
    d = ctx.person(rng, not_team=sp.team, pred=need)
    sp.truth = o
    sp.exclude.update((o, d))
    ctx.add(sp, rng, o, "edit", rng.randint(4, 6))
    ctx.add(sp, rng, o, "incident_response", rng.randint(1, 2) if v == "decoy_incident" else int(rng.random() < 0.3))
    ctx.add(sp, rng, d, "query", rng.randint(15, 25))
    if v == "upstream_decoy":
        for a in rng.sample(ctx.up(d, sp), 2):
            ctx.link(a, sp.aid)
    elif v == "contains_decoy":
        ctx.link(rng.choice(ctx.parents(d, sp)), sp.aid, "contains")
    else:   # downstream_decoy, and decoy_minor_edit / decoy_incident / decoy_agreeing_description (also downstream)
        for a in rng.sample(ctx.down(d, sp), 3):
            ctx.link(sp.aid, a)
    if v == "decoy_minor_edit":
        ctx.add(sp, rng, d, "edit", 1)
    elif v == "decoy_incident":
        ctx.add(sp, rng, d, "incident_response", 1)
    elif v == "decoy_agreeing_description":
        sp.desc = ctx.named_desc(rng, sp, o)


def conflict(ctx, rng, sp):
    """Text names A; a teammate B does the editing. Being named is worth 10 points and each edit 2.
    A is the answer only when clearly ahead (by more than 2 points); otherwise the answer is none."""
    v = sp.variant
    a = ctx.person(rng, team=sp.team)
    b = ctx.person(rng, team=sp.team, exclude={a})
    if v == "tied_editors_note_other_wording":      # A and B are tied on edits; an unread note names A
        e = rng.randint(3, 5)
        ctx.add(sp, rng, a, "edit", e)
        ctx.add(sp, rng, b, "edit", e)
        w = ctx.person(rng, not_team=sp.team)
        ctx.say(sp, rng, w, "incident_response", ctx.other_wording(rng, sp, "names_note").format(n=ctx.full(a)))
        sp.named.add(a)
        sp.exclude.update((a, b, w))
        sp.truth = a
        return
    lo, hi = {"desc_vs_minor_editor": (1, 3),       # 10 vs 2-6: A is clearly ahead -> A
              "desc_vs_close_editor": (4, 6),       # 10 vs 8-12: close -> none
              }.get(v, (7, 20))                     # desc_vs_editor, note_vs_editor: 10 vs 14-40: B ahead -> none
    if v == "desc_vs_incident_rival":               # B: 1-2 edits + 1 incident = 5-7 points; A (10) is clearly ahead
        ctx.add(sp, rng, b, "edit", rng.randint(1, 2))
        ctx.add(sp, rng, b, "incident_response", 1)
    else:
        ctx.add(sp, rng, b, "edit", rng.randint(lo, hi))
    if v == "note_vs_editor":                       # an incident note names A
        w = ctx.person(rng, not_team=sp.team)
        ctx.note(sp, rng, w, "incident_response", "esc", ctx.full(a))
        sp.exclude.add(w)
        sp.named.add(a)
    elif v == "desc_other_wording":                 # the description names A, in words the rules don't know
        sp.desc = ctx.other_wording(rng, sp, "names_desc").format(n=ctx.full(a))
        sp.named.add(a)
    else:                                           # the four desc_vs_* scenarios: the description names A
        sp.desc = ctx.named_desc(rng, sp, a)
    if v in ("desc_vs_minor_editor", "desc_vs_incident_rival"):
        sp.truth = a
    sp.exclude.update((a, b))


def handover(ctx, rng, sp):
    """The old maintainer edits, then writes 'Handed this to X in <month>.' and stops."""
    old = ctx.person(rng, team=sp.team)
    october = sp.k % 2 == 1
    edit_until, note_days = (58, (61, 75)) if october else (38, (40, 58))
    ctx.add(sp, rng, old, "edit", rng.randint(4, 7), 0, edit_until * DAY)
    at = ctx.second(rng, *note_days)
    if sp.variant == "stale_description":           # the description still names the old maintainer
        sp.desc = ctx.named_desc(rng, sp, old)
    if sp.variant in ("to_active", "stale_description", "other_wording"):
        x = ctx.person(rng, team=sp.team, exclude={old})
        name, sp.truth = ctx.full(x), x
        sp.named.add(x)
        sp.exclude.add(x)
    else:                                           # to_inactive: handed to someone who has left
        name = ctx.full(rng.choice(ctx.inactive[sp.team]))
    typ = rng.choice(["edit", "query"])
    if sp.variant == "other_wording":               # the handover note is in words the rules don't know
        ctx.say(sp, rng, old, typ, ctx.other_wording(rng, sp, "hands_over").format(n=name), at)
    else:
        ctx.note(sp, rng, old, typ, "hand_oct" if october else "hand_sep", name, at)
    if sp.truth:
        ctx.add(sp, rng, sp.truth, "edit", rng.randint(0, 2), at + 1, WINDOW)   # only after the handover
    sp.exclude.add(old)


def disowned(ctx, rng, sp):
    """The top editor's last note says 'I no longer maintain this.'"""
    w = ctx.person(rng, team=sp.team)
    ctx.add(sp, rng, w, "edit", rng.randint(4, 7), 0, 45 * DAY)
    at = ctx.second(rng, 46, 60)
    typ = rng.choice(["incident_response", "edit", "query"])
    if sp.variant.endswith("other_wording"):        # the note is in words the rules don't know
        ctx.say(sp, rng, w, typ, ctx.other_wording(rng, sp, "disowns"), at)
    else:
        ctx.note(sp, rng, w, typ, "disown", None, at)
    sp.exclude.add(w)
    # sole, sole_other_wording: nothing more.
    # with_successor, successor_other_wording: a teammate carries on after the note.
    if sp.variant in ("with_successor", "successor_other_wording"):
        s = ctx.person(rng, team=sp.team, exclude={w})
        ctx.add(sp, rng, s, "edit", rng.randint(2, 4), at + 1, WINDOW)
        sp.truth = s
        sp.exclude.add(s)


def two_texts(ctx, rng, sp):
    """Two pieces of text. If they name different people the answer is none. No person edits."""
    a = ctx.person(rng, team=sp.team)
    sp.desc = ctx.named_desc(rng, sp, a)
    if sp.variant == "self_claim_vs_desc":          # the description names A; teammate B writes "I maintain this job"
        b = ctx.person(rng, team=sp.team, exclude={a})
        ctx.note(sp, rng, b, "incident_response", "self_claim")
        sp.named.add(b)
        sp.exclude.update((a, b))
        return
    # agree: the note names A too. disagree: the note names a different teammate, B.
    b = a if sp.variant == "agree" else ctx.person(rng, team=sp.team, exclude={a})
    w = ctx.person(rng, not_team=sp.team)
    ctx.note(sp, rng, w, "incident_response", "esc", ctx.full(b))
    sp.named.add(b)
    sp.exclude.update((a, b, w))
    sp.truth = a if sp.variant == "agree" else None


def cross_team_owner(ctx, rng, sp):
    """The only maintainer is on a different team from the asset's domain."""
    o = ctx.person(rng, not_team=sp.team)
    sp.truth = o
    ctx.add(sp, rng, o, "edit", rng.randint(4, 8))
    ctx.add(sp, rng, o, "incident_response", int(rng.random() < 0.3))
    sp.exclude.add(o)
    # sole_maintainer: just this common part. The two scenarios below add to it.
    if sp.variant == "with_domain_readers":
        r = ctx.person(rng, team=sp.team)
        ctx.add(sp, rng, r, "query", rng.randint(15, 25))
        sp.exclude.add(r)
        sp.querier = "normal"
    elif sp.variant == "with_agreeing_description":
        sp.desc = ctx.named_desc(rng, sp, o)


# case name (as in scenarios.py) -> the function that builds it
RECIPES = {
    "clear_owner": clear_owner,
    "service_or_inactive_top_user": service_or_inactive_top_user,
    "readers_only": readers_only,
    "tied_evidence": tied_evidence,
    "near_tie": near_tie,
    "text_only_evidence": text_only_evidence,
    "no_signals": no_signals,
    "cross_team_decoy": cross_team_decoy,
    "conflict": conflict,
    "handover": handover,
    "disowned": disowned,
    "two_texts": two_texts,
    "cross_team_owner": cross_team_owner,
}
