"""The scenario table: every orphan the fixture contains, and its right answer.

This is the one file to read to know WHAT generate.py creates. It has no logic.
  - Each row is one scenario: case, scenario name, how many orphans, the right answer, and what the
    situation looks like.
  - How the evidence is built for a case lives in recipes.py (one function per case).
  - Rows are in a fixed order: changing the order changes which asset id each orphan gets.
  - Counts are even, so every scenario splits exactly half calibration / half held_out.
  - The decisions behind the answers are in scenarios.md, section 3.

Run `python scenarios.py` to print a summary per case.

Options (optional last column; several are separated by spaces):
  edges_fixed      the recipe sets this orphan's edges itself; no random neighbour is added
  no_noise         no robot or reader activity is added (the scenario has no signals, or only text)
  table_or_view    the asset is a table or view, so a dashboard or pipeline can contain it
  finance_or_risk  only finance and risk assets (those teams have their own robot)
  not_dup_domains  the asset is not on either team of the duplicated full name

Scenarios whose name ends in other_wording or harmless_text use text in words the rules scorer does not
know (vocabulary.py, OTHER_WORDINGS). One wording of each kind is kept for the held_out split only.
"""

PERSON, NONE = "person", "none"


def S(case, scenario, count, answer, description, options=""):
    return (case, scenario, count, answer, description, options)


SCENARIOS = [
    # ---- clear_owner (brief): one person clearly maintains it
    S("clear_owner", "basic", 12, PERSON,
      "The owner edits 4-8 times (sometimes with 1 incident); everyone else only reads."),
    S("clear_owner", "heavy_same_team_reader", 6, PERSON,
      "Like basic, plus a teammate who reads 15-25 times and never edits."),
    S("clear_owner", "heavy_other_team_reader", 6, PERSON,
      "Like basic, plus someone from another team who reads 15-25 times."),
    S("clear_owner", "owner_is_top_reader", 6, PERSON,
      "Like basic, and the owner is also the heaviest reader (15-25 reads)."),
    S("clear_owner", "svc_outedits_owner", 8, PERSON,
      "The owner edits 4-6 times; a robot (svc_airflow or svc_dbt) edits more. Robots can't own."),
    S("clear_owner", "minor_second_editor", 6, PERSON,
      "The owner edits 6-8 times; a teammate makes 1-2 edits."),
    S("clear_owner", "half_second_editor", 4, PERSON,
      "The owner edits 8 or 10 times; a teammate makes exactly half as many (4 or 5). 16 points vs 8 is a "
      "clear lead."),
    S("clear_owner", "lineage_agrees", 6, PERSON,
      "Like basic, and the owner also owns an upstream asset.", "edges_fixed"),
    S("clear_owner", "contains_parent_is_editor", 4, PERSON,
      "Like basic, and the owner's dashboard or pipeline contains this asset.", "edges_fixed table_or_view"),
    S("clear_owner", "incident_heavy", 4, PERSON,
      "The owner has 2 edits and 2 incidents."),
    S("clear_owner", "incidents_only", 4, PERSON,
      "The owner has 0 edits and 2-3 incidents."),
    S("clear_owner", "thin", 6, PERSON,
      "The owner has only 2 edits; nobody else edits."),
    S("clear_owner", "single_edit", 4, PERSON,
      "One person made a single edit; nobody else edits or handles incidents. A lone candidate is the answer."),
    S("clear_owner", "harmless_text", 6, PERSON,
      "Like basic, plus a note or description in words the rules don't know. It says nothing about "
      "ownership, so the answer is still the editor."),
    S("clear_owner", "agreeing_description", 4, PERSON,
      "Like basic, and the description names the owner."),

    # ---- service_or_inactive_top_user (brief): the busiest user is a robot or someone who left
    S("service_or_inactive_top_user", "svc_top_editor", 10, PERSON,
      "A robot edits 15-30 times; one person edits 2-3 times + 1 incident. Answer: that person."),
    S("service_or_inactive_top_user", "svc_top_editor_and_querier", 6, PERSON,
      "Like svc_top_editor, plus a robot that reads 20-35 times."),
    S("service_or_inactive_top_user", "own_team_service_top", 6, PERSON,
      "The asset team's own robot (svc_finance_etl or svc_risk_scorer) edits 12-25 times; "
      "one person edits 2-3 times + 1 incident.", "finance_or_risk"),
    S("service_or_inactive_top_user", "inactive_top_active_teammate", 8, PERSON,
      "Someone who left edits 6-9 times; an active teammate edits 2-4 times. Answer: the teammate."),
    S("service_or_inactive_top_user", "inactive_only_editor", 8, NONE,
      "Someone who left edits 4-8 times; an active teammate only reads (5-9 times)."),
    S("service_or_inactive_top_user", "inactive_with_incident", 6, NONE,
      "Someone who left edits 3-6 times and handled 1 incident; nobody active edits."),
    S("service_or_inactive_top_user", "service_only_editor", 10, NONE,
      "Only a robot edits (8-25 times); people only read."),
    S("service_or_inactive_top_user", "harmless_text", 6, NONE,
      "Like service_only_editor, plus a note or description in words the rules don't know. It says "
      "nothing about ownership, so the answer is still none."),

    # ---- readers_only (added): people only read
    S("readers_only", "heavy_teammate_reader", 10, NONE,
      "A teammate reads 15-25 times; nobody edits, not even robots."),

    # ---- tied_evidence (brief): people with exactly equal evidence
    S("tied_evidence", "tied_edits_and_incidents", 10, NONE,
      "Two teammates with identical edits (3-5) and 1 incident each."),
    S("tied_evidence", "tied_edits_only", 8, NONE,
      "Two teammates with identical edits (2-6) and no incidents."),
    S("tied_evidence", "three_way", 6, NONE,
      "Three teammates with identical edits and incidents."),
    S("tied_evidence", "tied_with_svc_top", 6, NONE,
      "Two teammates tied, and a robot edits more than either of them."),
    S("tied_evidence", "tied_cross_team", 4, NONE,
      "Two people tied, on different teams. The team does not break a tie."),
    S("tied_evidence", "tied_third_party_neighbour", 4, NONE,
      "Two teammates tied; a third teammate with no activity here owns the upstream asset (a trap).",
      "edges_fixed"),

    # ---- near_tie (added): almost equal; decided none (scenarios.md 3.2)
    S("near_tie", "margin_one", 4, NONE,
      "Two teammates; one has exactly one more edit (e.g. 6 vs 5)."),
    S("near_tie", "mixed", 4, NONE,
      "One teammate has 4 edits; the other has 3 edits + 1 incident."),

    # ---- text_only_evidence (brief): the owner is identified by text, not by their own edits
    S("text_only_evidence", "desc_maintained_by", 6, PERSON,
      'Description "Maintained by <full name>."; only robots edit.'),
    S("text_only_evidence", "desc_built_and_maintained", 6, PERSON,
      'Description "Built and maintained by <full name>; rebuilt nightly by the scheduler."'),
    S("text_only_evidence", "desc_owned_with_team", 6, PERSON,
      'Description "Owned and maintained by <full name> (<team>). Ask them before changing the schema."'),
    S("text_only_evidence", "note_escalated", 8, PERSON,
      'Someone from another team writes the incident note "Escalated to <full name>, who maintains this ...". '
      "Answer: the person named, not the writer."),
    S("text_only_evidence", "note_writer_also_edits", 4, PERSON,
      'A teammate edits 3-5 times and also writes "Escalated to <full name>, who maintains this ...". '
      "The writer points away from themselves, so the answer is the person named."),
    S("text_only_evidence", "self_claim_note", 4, PERSON,
      'The person\'s own incident note says "Fixed the late partition; I maintain this job." (the brief\'s '
      "example); nobody else edits."),
    S("text_only_evidence", "desc_other_wording", 6, PERSON,
      'The description names the maintainer in words the rules don\'t know, e.g. "<full name> looks after '
      'this one."; only robots edit.'),
    S("text_only_evidence", "note_other_wording", 6, PERSON,
      'Someone from another team names the maintainer in an incident note, in words the rules don\'t know, '
      'e.g. "Passed to <full name>, who looks after this."'),
    S("text_only_evidence", "note_on_query", 4, PERSON,
      'A query note: "Questions go to <full name>; they maintain this."'),
    S("text_only_evidence", "varied_wording", 4, PERSON,
      'Description "Owned by <full name>." or "Maintainer: <full name>."'),
    S("text_only_evidence", "first_name_plus_team", 4, PERSON,
      'Description "Owned and maintained by <first name> (<team>)."; only one person on that team has '
      "that first name."),
    S("text_only_evidence", "first_name_team_context", 4, NONE,
      'Description "Maintained by <first name>."; only one person on the asset\'s team has that first name, '
      "but a first name alone names nobody."),
    S("text_only_evidence", "unique_first_name", 4, NONE,
      'Description "Maintained by <first name>."; only one user in the company has that first name, '
      "but a first name alone names nobody."),
    S("text_only_evidence", "text_no_signals", 4, PERSON,
      "The description names someone by full name, and there is no activity at all.", "no_noise"),
    S("text_only_evidence", "inactive_editor_names_teammate", 4, PERSON,
      "Someone who left edits 3-6 times; the description names an active teammate. Answer: the teammate."),
    S("text_only_evidence", "names_inactive", 6, NONE,
      "The description names someone who has left."),
    S("text_only_evidence", "shared_first_name_same_team", 6, NONE,
      'Description "Maintained by <first name>."; two active teammates share that first name.'),
    S("text_only_evidence", "first_name_active_inactive", 4, NONE,
      "The first name in the description matches an active teammate and a teammate who left."),
    S("text_only_evidence", "unknown_name", 4, NONE,
      "The description names someone who is not a user."),
    S("text_only_evidence", "names_service", 2, NONE,
      'Description "Maintained by svc_dbt." (or svc_airflow).'),
    S("text_only_evidence", "team_only_text", 4, NONE,
      'Description "Maintained by the <team> team."'),
    S("text_only_evidence", "duplicate_full_name", 2, NONE,
      "Two users have exactly the full name in the description; neither is on the asset's team.",
      "not_dup_domains"),

    # ---- no_signals (brief): no activity at all
    S("no_signals", "isolated_empty", 6, NONE,
      "No activity, no neighbours, empty description.", "edges_fixed no_noise"),
    S("no_signals", "generic_description", 6, NONE,
      "No activity, no neighbours, a generic description.", "edges_fixed no_noise"),
    S("no_signals", "cross_team_neighbours", 6, NONE,
      "No activity; the upstream and downstream assets are owned by other teams.", "edges_fixed no_noise"),
    S("no_signals", "same_team_neighbour", 8, NONE,
      "No activity; a teammate owns the upstream asset. A neighbour alone does not decide.",
      "edges_fixed no_noise"),
    S("no_signals", "orphan_neighbour", 4, NONE,
      "No activity; only linked to other orphans.", "edges_fixed no_noise"),
    S("no_signals", "contains_parent_teammate", 4, NONE,
      "No activity; it sits inside a teammate's dashboard or pipeline.", "edges_fixed table_or_view no_noise"),

    # ---- cross_team_decoy (brief): someone from another team looks like the owner
    S("cross_team_decoy", "downstream_decoy", 14, PERSON,
      "A teammate edits 4-6 times; someone from another team reads 15-25 times and owns 3 downstream "
      "assets. Answer: the teammate.", "edges_fixed"),
    S("cross_team_decoy", "upstream_decoy", 6, PERSON,
      "Like downstream_decoy, but the decoy owns 2 upstream assets.", "edges_fixed"),
    S("cross_team_decoy", "contains_decoy", 6, PERSON,
      "Like downstream_decoy, but the decoy's dashboard or pipeline contains this asset.",
      "edges_fixed table_or_view"),
    S("cross_team_decoy", "decoy_minor_edit", 4, PERSON,
      "Like downstream_decoy, and the decoy also makes 1 edit.", "edges_fixed"),
    S("cross_team_decoy", "decoy_incident", 4, PERSON,
      "Like downstream_decoy; the decoy handled 1 incident and the editor 1-2.", "edges_fixed"),
    S("cross_team_decoy", "decoy_agreeing_description", 4, PERSON,
      "Like downstream_decoy, and the description names the editor.", "edges_fixed"),

    # ---- conflict (added): text names one person, someone else edits (scenarios.md 3.3)
    # Being named is worth 10 points, an edit 2. The person named wins only when ahead by more than 2 points.
    S("conflict", "desc_vs_editor", 6, NONE,
      "The description names A; teammate B edits 7-20 times; A has no activity. B is ahead -> none."),
    S("conflict", "note_vs_editor", 4, NONE,
      'An incident note says "Escalated to A, who maintains this ..."; teammate B edits 7-20 times.'),
    S("conflict", "desc_vs_close_editor", 4, NONE,
      "The description names A; teammate B edits 4-6 times (10 points vs 8-12: close) -> none."),
    S("conflict", "desc_vs_minor_editor", 4, PERSON,
      "The description names A; teammate B edits 1-3 times (10 points vs 2-6: A is clearly ahead). Answer: A."),
    S("conflict", "desc_other_wording", 6, NONE,
      "Like desc_vs_editor, but the description names A in words the rules don't know."),
    S("conflict", "tied_editors_note_other_wording", 6, PERSON,
      "Two teammates, A and B, have identical edits; someone else's note names A as the maintainer, in "
      "words the rules don't know. Answer: A."),
    S("conflict", "desc_vs_incident_rival", 4, PERSON,
      "The description names A; teammate B has 1-2 edits and 1 incident (10 points vs 5-7). Answer: A."),

    # ---- handover (added): the old maintainer hands it over
    S("handover", "to_active", 8, PERSON,
      'The old maintainer edits 4-7 times, writes "Handed this to <full name> in September/October." and '
      "stops; the new person edits 0-2 times afterwards. Answer: the new person."),
    S("handover", "to_inactive", 4, NONE,
      "Like to_active, but the person it was handed to has left."),
    S("handover", "other_wording", 6, PERSON,
      'Like to_active, but the handover note is in words the rules don\'t know, e.g. "<full name> has taken '
      'this over from me."'),
    S("handover", "stale_description", 4, PERSON,
      "Like to_active, and the description still names the old maintainer. They took themselves out, so "
      "the answer is the new person."),

    # ---- disowned (added): the editor says they stopped
    S("disowned", "sole", 6, NONE,
      'The only editor edits 4-7 times, then writes "I no longer maintain this."'),
    S("disowned", "with_successor", 6, PERSON,
      "Like sole, and a teammate edits 2-4 times afterwards. Answer: the teammate."),
    S("disowned", "sole_other_wording", 6, NONE,
      'Like sole, but the note is in words the rules don\'t know, e.g. "Not mine any more."'),
    S("disowned", "successor_other_wording", 6, PERSON,
      "Like with_successor, but the note is in words the rules don't know."),

    # ---- two_texts (added): two pieces of text
    S("two_texts", "disagree", 8, NONE,
      "The description names A; an incident note names B; no person edits."),
    S("two_texts", "agree", 4, PERSON,
      "The description and an incident note name the same person."),
    S("two_texts", "self_claim_vs_desc", 4, NONE,
      'The description names A; teammate B\'s incident note says "...; I maintain this job." Two texts, '
      "two people."),

    # ---- cross_team_owner (added): the maintainer is on another team
    S("cross_team_owner", "sole_maintainer", 8, PERSON,
      "The only editor (4-8 edits) is on a different team from the asset."),
    S("cross_team_owner", "with_domain_readers", 6, PERSON,
      "Like sole_maintainer, plus someone from the asset's team reads 15-25 times."),
    S("cross_team_owner", "with_agreeing_description", 4, PERSON,
      "Like sole_maintainer, and the description names them."),
]

# The 13 cases. The brief requires the first 6; we added the other 7 (the brief allows new case names).
BRIEF_CASES = ["clear_owner", "service_or_inactive_top_user", "tied_evidence",
               "text_only_evidence", "no_signals", "cross_team_decoy"]
ADDED_CASES = ["readers_only", "near_tie", "conflict", "handover", "disowned", "two_texts",
               "cross_team_owner"]
ALL_CASES = BRIEF_CASES + ADDED_CASES

_in_table = {row[0] for row in SCENARIOS}
assert _in_table == set(ALL_CASES), f"case lists and table differ: {sorted(_in_table ^ set(ALL_CASES))}"


if __name__ == "__main__":
    cases = ALL_CASES
    print(f"{'case':30s} {'from':6s} {'scenarios':>9s} {'orphans':>8s} {'person':>7s} {'none':>5s}")
    for c in cases:
        rows = [r for r in SCENARIOS if r[0] == c]
        n = sum(r[2] for r in rows)
        none = sum(r[2] for r in rows if r[3] == NONE)
        print(f"{c:30s} {'brief' if c in BRIEF_CASES else 'added':6s} {len(rows):9d} {n:8d} {n - none:7d} {none:5d}")
    n = sum(r[2] for r in SCENARIOS)
    none = sum(r[2] for r in SCENARIOS if r[3] == NONE)
    print(f"{'total':30s} {'':6s} {len(SCENARIOS):9d} {n:8d} {n - none:7d} {none:5d}")
