# Scenarios in the generated fixture

This file lists every orphan scenario that `generate.py` creates, with the right answer for each. Sections 1 and 2 are written by `python scenario_tables.py` from `scenarios.py` and the generated files, so they always match the data (seed 42). Section 3 onward is written by hand.

- **13 cases**: 6 that the brief requires and 7 that we added. The brief allows new case names.
- **87 scenarios** in total. Each case is split into smaller scenarios.
- **490 orphans**: 286 have a person as the right answer and 204 have none.
- Every scenario has an even count, so it splits exactly in half: 245 calibration and 245 held_out, with 102 none in each.

**How to read the examples:**
- "6 edits" means the person edited the asset 6 times; a "read" is a query.
- "(left)" means the person is inactive. A team in brackets means the person is not on the asset's team.
- "Robot" means a service account such as `svc_airflow`.
- Small background activity is left out: light robot activity and people who read now and then.

---

## 1. The 13 cases

| # | Case | Where it comes from | Scenarios | Orphans | Answer is a person | Answer is none |
|---|---|---|---|---|---|---|
| 1 | clear_owner | brief | 15 | 86 | 86 | 0 |
| 2 | service_or_inactive_top_user | brief | 8 | 60 | 30 | 30 |
| 3 | tied_evidence | brief | 6 | 38 | 0 | 38 |
| 4 | text_only_evidence | brief | 22 | 102 | 66 | 36 |
| 5 | no_signals | brief | 6 | 34 | 0 | 34 |
| 6 | cross_team_decoy | brief | 6 | 38 | 38 | 0 |
| 7 | readers_only | added | 1 | 10 | 0 | 10 |
| 8 | near_tie | added | 2 | 8 | 0 | 8 |
| 9 | conflict | added | 7 | 34 | 14 | 20 |
| 10 | handover | added | 4 | 22 | 18 | 4 |
| 11 | disowned | added | 4 | 24 | 12 | 12 |
| 12 | two_texts | added | 3 | 16 | 4 | 12 |
| 13 | cross_team_owner | added | 3 | 18 | 18 | 0 |
| | **Total** | | **87** | **490** | **286** | **204** |

**Why each added case exists:**

| Case | Why |
|---|---|
| readers_only | Tests the brief's warning "who maintains it, not who reads it most". |
| near_tie | Tests the gap between an exact tie and a clear owner. |
| conflict | Text names one person and someone else works on the asset. The answer depends on how much they do. |
| handover | The old owner names a successor. |
| disowned | The editor says they stopped maintaining it. |
| two_texts | Two pieces of text name different people. |
| cross_team_owner | The real maintainer is on another team. |

The handoff note planned conflict, handover, disowned, two_texts and cross_team_owner. The independent review added readers_only and near_tie.

---

## 2. The scenarios in each case

### Case 1. clear_owner: one person clearly maintains it (86)

| Scenario | n | Situation | Example from the data | Right answer |
|---|---|---|---|---|
| basic | 12 | The owner edits 4-8 times (sometimes with 1 incident); everyone else only reads. | A marketing table. Oren Schmidt: 7 edits. Eitan Costa: 18 reads. | **Oren Schmidt** |
| heavy_same_team_reader | 6 | Like basic, plus a teammate who reads 15-25 times and never edits. | A growth table. Wren Vasquez: 6 edits. Kai Varga: 19 reads. | **Wren Vasquez** |
| heavy_other_team_reader | 6 | Like basic, plus someone from another team who reads 15-25 times. | A growth dashboard. Eamon Jensen: 4 edits + 1 incident. Oren Schmidt (marketing-ops): 19 reads. | **Eamon Jensen** |
| owner_is_top_reader | 6 | Like basic, and the owner is also the heaviest reader (15-25 reads). | A finance pipeline. Naveen Trent: 8 edits + 1 incident + 16 reads. | **Naveen Trent** |
| svc_outedits_owner | 8 | The owner edits 4-6 times; a robot (svc_airflow or svc_dbt) edits more. Robots can't own. | A growth view. Robot svc_dbt: 8 edits. Keiko Iyer: 4 edits. | **Keiko Iyer** |
| minor_second_editor | 6 | The owner edits 6-8 times; a teammate makes 1-2 edits. | A growth pipeline. Niamh Quispe: 7 edits + 1 incident. Rafael Castillo: 1 edit. | **Niamh Quispe** |
| half_second_editor | 4 | The owner edits 8 or 10 times; a teammate makes exactly half as many (4 or 5). 16 points vs 8 is a clear lead. | A platform pipeline. Greta Schmidt: 10 edits. Sofia Bose: 5 edits. | **Greta Schmidt** |
| lineage_agrees | 6 | Like basic, and the owner also owns an upstream asset. | A marketing table. Hiro Bose: 6 edits. Hiro Bose owns 1 upstream asset. | **Hiro Bose** |
| contains_parent_is_editor | 4 | Like basic, and the owner's dashboard or pipeline contains this asset. | A finance view. Kira Ferreira: 4 edits + 1 incident. An asset of Kira Ferreira contains it. | **Kira Ferreira** |
| incident_heavy | 4 | The owner has 2 edits and 2 incidents. | A finance view. Joss Trent: 2 edits + 2 incidents. | **Joss Trent** |
| incidents_only | 4 | The owner has 0 edits and 2-3 incidents. | A risk table. Sione Zhou: 2 incidents. Emeka Nakamura: 25 reads. | **Sione Zhou** |
| thin | 6 | The owner has only 2 edits; nobody else edits. | A finance pipeline. Farah Petrov: 2 edits. Abena Wong (risk-analytics): 22 reads. | **Farah Petrov** |
| single_edit | 4 | One person made a single edit; nobody else edits or handles incidents. A lone candidate is the answer. | A platform table. Sofia Bose: 1 edit. | **Sofia Bose** |
| harmless_text | 6 | Like basic, plus a note or description in words the rules don't know. It says nothing about ownership, so the answer is still the editor. | A finance table. Description: "Ask in the data channel before running a backfill." Emeka Petrov: 4 edits + 1 incident. Olu Abara (platform-eng): 20 reads. | **Emeka Petrov** |
| agreeing_description | 4 | Like basic, and the description names the owner. | A growth view. Description: "Owned and maintained by Goran Haddad (growth-analytics). Ask them before changing the schema." Goran Haddad: 6 edits. | **Goran Haddad** |

### Case 2. service_or_inactive_top_user: the busiest user is a robot or someone who left (60)

| Scenario | n | Situation | Example from the data | Right answer |
|---|---|---|---|---|
| svc_top_editor | 10 | A robot edits 15-30 times; one person edits 2-3 times + 1 incident. Answer: that person. | A marketing dashboard. Robot svc_dbt: 21 edits. Wren Wong: 2 edits + 1 incident. Laszlo Ferreira (platform-eng): 24 reads. | **Wren Wong** |
| svc_top_editor_and_querier | 6 | Like svc_top_editor, plus a robot that reads 20-35 times. | A finance table. Robot svc_dbt: 25 edits. Elif Brennan: 3 edits + 1 incident. Robot svc_tableau: 27 reads. | **Elif Brennan** |
| own_team_service_top | 6 | The asset team's own robot (svc_finance_etl or svc_risk_scorer) edits 12-25 times; one person edits 2-3 times + 1 incident. | A risk table. Robot svc_risk_scorer: 24 edits. Daria Zhou: 3 edits + 1 incident. Paloma Kaur (platform-eng): 18 reads. | **Daria Zhou** |
| inactive_top_active_teammate | 8 | Someone who left edits 6-9 times; an active teammate edits 2-4 times. Answer: the teammate. | A risk table. Jalen Kaur (left): 7 edits. Mina Ueda: 2 edits. Jonas Rao (marketing-ops): 24 reads. | **Mina Ueda** |
| inactive_only_editor | 8 | Someone who left edits 4-8 times; an active teammate only reads (5-9 times). | A marketing table. Imani Kaur (left): 7 edits. | **none** |
| inactive_with_incident | 6 | Someone who left edits 3-6 times and handled 1 incident; nobody active edits. | A marketing view. Chen Vasquez (left): 6 edits + 1 incident. | **none** |
| service_only_editor | 10 | Only a robot edits (8-25 times); people only read. | A finance dashboard. Robot svc_dbt: 21 edits. | **none** |
| harmless_text | 6 | Like service_only_editor, plus a note or description in words the rules don't know. It says nothing about ownership, so the answer is still none. | A platform table. Description: "Do not query during the nightly load." Robot svc_dbt: 24 edits. | **none** |

### Case 3. tied_evidence: people with exactly equal evidence (38)

| Scenario | n | Situation | Example from the data | Right answer |
|---|---|---|---|---|
| tied_edits_and_incidents | 10 | Two teammates with identical edits (3-5) and 1 incident each. | A risk table. Beatriz Varga: 4 edits + 1 incident. Xavi Dahl: 4 edits + 1 incident. | **none** |
| tied_edits_only | 8 | Two teammates with identical edits (2-6) and no incidents. | A risk view. Malik Kimura: 4 edits. Santiago Brennan: 4 edits. Reza Castillo: 17 reads. | **none** |
| three_way | 6 | Three teammates with identical edits and incidents. | A marketing table. Paloma Novak: 2 edits. Yuki Haddad: 2 edits. Hiro Bose: 2 edits. | **none** |
| tied_with_svc_top | 6 | Two teammates tied, and a robot edits more than either of them. | A finance pipeline. Robot svc_dbt: 14 edits. Freya Duarte: 3 edits. Joss Trent: 3 edits. | **none** |
| tied_cross_team | 4 | Two people tied, on different teams. The team does not break a tie. | A platform view. Dilnoza Quispe (marketing-ops): 3 edits. Mina Schmidt: 3 edits. | **none** |
| tied_third_party_neighbour | 4 | Two teammates tied; a third teammate with no activity here owns the upstream asset (a trap). | A growth table. Kai Varga: 3 edits. Farah Moreau: 3 edits. Emeka Ortiz owns 1 upstream asset. | **none** |

### Case 4. text_only_evidence: the owner is identified by text, not by their own edits (102)

| Scenario | n | Situation | Example from the data | Right answer |
|---|---|---|---|---|
| desc_maintained_by | 6 | Description "Maintained by *full name*."; only robots edit. | A finance view. Description: "Maintained by Tomas Dahl." | **Tomas Dahl** |
| desc_built_and_maintained | 6 | Description "Built and maintained by *full name*; rebuilt nightly by the scheduler." | A risk table. Description: "Built and maintained by Rhea Ferreira; rebuilt nightly by the scheduler." | **Rhea Ferreira** |
| desc_owned_with_team | 6 | Description "Owned and maintained by *full name* (*team*). Ask them before changing the schema." | A growth dashboard. Description: "Owned and maintained by Emeka Ortiz (growth-analytics). Ask them before changing the schema." | **Emeka Ortiz** |
| note_escalated | 8 | Someone from another team writes the incident note "Escalated to *full name*, who maintains this ...". Answer: the person named, not the writer. | A platform table. Emeka Petrov (finance-data): 1 incident. Mina Wong (growth-analytics): 18 reads. Note by Emeka Petrov: "Escalated to Zane Costa, who maintains this table; they fixed the upstream filter." | **Zane Costa** |
| note_writer_also_edits | 4 | A teammate edits 3-5 times and also writes "Escalated to *full name*, who maintains this ...". The writer points away from themselves, so the answer is the person named. | A growth table. Kofi Kaur: 5 edits + 1 incident. Note by Kofi Kaur: "Escalated to Tomas Demir, who maintains this table; they fixed the upstream filter." | **Tomas Demir** |
| self_claim_note | 4 | The person's own incident note says "Fixed the late partition; I maintain this job." (the brief's example); nobody else edits. | A risk table. Noor Kaur: 1 incident. Note by Noor Kaur: "Fixed the late partition; I maintain this job." | **Noor Kaur** |
| desc_other_wording | 6 | The description names the maintainer in words the rules don't know, e.g. "*full name* looks after this one."; only robots edit. | A growth table. Description: "Mei Kaur is responsible for this dataset." Elif Dahl: 20 reads. | **Mei Kaur** |
| note_other_wording | 6 | Someone from another team names the maintainer in an incident note, in words the rules don't know, e.g. "Passed to *full name*, who looks after this." | A platform view. Akira Novak (marketing-ops): 1 incident. Rafael Abara (finance-data): 25 reads. Note by Akira Novak: "Passed to Greta Schmidt, who looks after this." | **Greta Schmidt** |
| note_on_query | 4 | A query note: "Questions go to *full name*; they maintain this." | A finance table. Note by Vikram Haddad: "Questions go to Tariq Bose; they maintain this." | **Tariq Bose** |
| varied_wording | 4 | Description "Owned by *full name*." or "Maintainer: *full name*." | A finance view. Description: "Owned by Rhea Varga." | **Rhea Varga** |
| first_name_plus_team | 4 | Description "Owned and maintained by *first name* (*team*)."; only one person on that team has that first name. | A finance dashboard. Description: "Owned and maintained by Emeka (finance-data). Ask them before changing the schema." 5 users called Emeka; on finance-data: Emeka Petrov. Elif Brennan: 18 reads. | **Emeka Petrov** |
| first_name_team_context | 4 | Description "Maintained by *first name*."; only one person on the asset's team has that first name, but a first name alone names nobody. | A growth table. Description: "Maintainer: Beatriz." 5 users called Beatriz; on this asset's team: Beatriz Adeyemi. | **none** |
| unique_first_name | 4 | Description "Maintained by *first name*."; only one user in the company has that first name, but a first name alone names nobody. | A marketing table. Description: "Maintained by Jonas." 1 user called Jonas; on this asset's team: Jonas Rao. | **none** |
| text_no_signals | 4 | The description names someone by full name, and there is no activity at all. | A risk view. Description: "Built and maintained by Elif Bose; rebuilt nightly by the scheduler." No activity at all. | **Elif Bose** |
| inactive_editor_names_teammate | 4 | Someone who left edits 3-6 times; the description names an active teammate. Answer: the teammate. | A platform pipeline. Description: "Owned and maintained by Nico Eze (platform-eng). Ask them before changing the schema." Petra Silva (left): 5 edits. | **Nico Eze** |
| names_inactive | 6 | The description names someone who has left. | A platform view. Description: "Owned and maintained by Wanjiru Haddad (platform-eng). Ask them before changing the schema." Wanjiru Haddad has left. Mina Ueda (risk-analytics): 21 reads. | **none** |
| shared_first_name_same_team | 6 | Description "Maintained by *first name*."; two active teammates share that first name. | A marketing view. Description: "Maintainer: Akira." 2 users called Akira; on this asset's team: Akira Novak and Akira Adeyemi. Reza Vasquez (platform-eng): 21 reads. | **none** |
| first_name_active_inactive | 4 | The first name in the description matches an active teammate and a teammate who left. | A growth table. Description: "Maintained by Esme." 2 users called Esme; on this asset's team: Esme Tanaka (left) and Esme Nakamura. | **none** |
| unknown_name | 4 | The description names someone who is not a user. | A growth dashboard. Description: "Maintained by Bram Marchetti." Nobody of that name is a user. | **none** |
| names_service | 2 | Description "Maintained by svc_dbt." (or svc_airflow). | A finance view. Description: "Maintained by svc_dbt." | **none** |
| team_only_text | 4 | Description "Maintained by the *team* team." | A platform table. Description: "Maintained by the platform-eng team." | **none** |
| duplicate_full_name | 2 | Two users have exactly the full name in the description; neither is on the asset's team. | A finance pipeline. Description: "Owned by Alba Castillo." 2 users have exactly that name. | **none** |

### Case 5. no_signals: no activity at all (34)

| Scenario | n | Situation | Example from the data | Right answer |
|---|---|---|---|---|
| isolated_empty | 6 | No activity, no neighbours, empty description. | A risk table. No activity at all. | **none** |
| generic_description | 6 | No activity, no neighbours, a generic description. | A growth dashboard. No activity at all. | **none** |
| cross_team_neighbours | 6 | No activity; the upstream and downstream assets are owned by other teams. | A platform table. Mei Kaur (growth-analytics) owns 1 upstream asset. Tomas Brennan (risk-analytics) owns 1 downstream asset. No activity at all. | **none** |
| same_team_neighbour | 8 | No activity; a teammate owns the upstream asset. A neighbour alone does not decide. | A marketing view. Dilnoza Quispe owns 1 upstream asset. No activity at all. | **none** |
| orphan_neighbour | 4 | No activity; only linked to other orphans. | A marketing table. Linked to another orphan (downstream). No activity at all. | **none** |
| contains_parent_teammate | 4 | No activity; it sits inside a teammate's dashboard or pipeline. | A marketing table. An asset of Paloma Novak contains it. No activity at all. | **none** |

### Case 6. cross_team_decoy: someone from another team looks like the owner (38)

| Scenario | n | Situation | Example from the data | Right answer |
|---|---|---|---|---|
| downstream_decoy | 14 | A teammate edits 4-6 times; someone from another team reads 15-25 times and owns 3 downstream assets. Answer: the teammate. | A growth table. Hiro Kaur: 4 edits. Santiago Brennan (risk-analytics): 18 reads. Santiago Brennan (risk-analytics) owns 3 downstream assets. | **Hiro Kaur** |
| upstream_decoy | 6 | Like downstream_decoy, but the decoy owns 2 upstream assets. | A finance view. Paloma Vasquez: 4 edits + 1 incident. Rafael Sato (platform-eng): 16 reads. Rafael Sato (platform-eng) owns 2 upstream assets. | **Paloma Vasquez** |
| contains_decoy | 6 | Like downstream_decoy, but the decoy's dashboard or pipeline contains this asset. | A risk table. Valeria Kaur: 5 edits. Emeka Abara (marketing-ops): 15 reads. An asset of Emeka Abara (marketing-ops) contains it. | **Valeria Kaur** |
| decoy_minor_edit | 4 | Like downstream_decoy, and the decoy also makes 1 edit. | A finance table. Aarav Costa: 6 edits. Elif Jensen (marketing-ops): 1 edit + 15 reads. Hiro Zhou: 25 reads. Elif Jensen (marketing-ops) owns 3 downstream assets. | **Aarav Costa** |
| decoy_incident | 4 | Like downstream_decoy; the decoy handled 1 incident and the editor 1-2. | A platform table. Mei Lindqvist: 6 edits + 2 incidents. Kira Ferreira (finance-data): 1 incident + 25 reads. Kira Ferreira (finance-data) owns 3 downstream assets. | **Mei Lindqvist** |
| decoy_agreeing_description | 4 | Like downstream_decoy, and the description names the editor. | A finance view. Description: "Maintained by Aarav Costa." Aarav Costa: 6 edits. Xavi Dahl (risk-analytics): 18 reads. Xavi Dahl (risk-analytics) owns 3 downstream assets. | **Aarav Costa** |

### Case 7. readers_only: people only read (10)

| Scenario | n | Situation | Example from the data | Right answer |
|---|---|---|---|---|
| heavy_teammate_reader | 10 | A teammate reads 15-25 times; nobody edits, not even robots. | A finance table. Reza Dahl: 20 reads. | **none** |

### Case 8. near_tie: almost equal (8)

| Scenario | n | Situation | Example from the data | Right answer |
|---|---|---|---|---|
| margin_one | 4 | Two teammates; one has exactly one more edit (e.g. 6 vs 5). | A growth view. Beatriz Adeyemi: 4 edits. Valeria Silva: 3 edits. | **none** |
| mixed | 4 | One teammate has 4 edits; the other has 3 edits + 1 incident. | A risk table. Valeria Kaur: 3 edits + 1 incident. Beatriz Varga: 4 edits. | **none** |

### Case 9. conflict: text names one person, someone else works on the asset (34)

| Scenario | n | Situation | Example from the data | Right answer |
|---|---|---|---|---|
| desc_vs_editor | 6 | The description names A; teammate B edits 7-20 times; A has no activity. B is ahead → none. | A marketing table. Description: "Maintainer: Rhea Kaur." Beatriz Yilmaz: 10 edits. | **none** |
| note_vs_editor | 4 | An incident note says "Escalated to A, who maintains this ..."; teammate B edits 7-20 times. | A platform table. Greta Schmidt: 11 edits. Kofi Kaur (growth-analytics): 1 incident. Note by Kofi Kaur: "Escalated to Elif Varga, who maintains this table; they fixed the upstream filter." | **none** |
| desc_vs_close_editor | 4 | The description names A; teammate B edits 4-6 times (10 points vs 8-12: close) → none. | A platform table. Description: "Built and maintained by Emeka Sato; rebuilt nightly by the scheduler." Zainab Vasquez: 6 edits. Sione Varga: 23 reads. | **none** |
| desc_vs_minor_editor | 4 | The description names A; teammate B edits 1-3 times (10 points vs 2-6: A is clearly ahead). Answer: A. | A growth view. Description: "Built and maintained by Quinn Ortiz; rebuilt nightly by the scheduler." Sana Brennan: 3 edits. | **Quinn Ortiz** |
| desc_other_wording | 6 | Like desc_vs_editor, but the description names A in words the rules don't know. | A platform view. Description: "Valeria Castillo looks after this one." Hiro Rossi: 10 edits. | **none** |
| tied_editors_note_other_wording | 6 | Two teammates, A and B, have identical edits; someone else's note names A as the maintainer, in words the rules don't know. Answer: A. | A platform pipeline. Rafael Sato: 4 edits. Santiago Bose: 4 edits. Rhea Kaur (marketing-ops): 1 incident. Ingrid Rossi: 22 reads. Note by Rhea Kaur: "Passed to Rafael Sato, who looks after this." | **Rafael Sato** |
| desc_vs_incident_rival | 4 | The description names A; teammate B has 1-2 edits and 1 incident (10 points vs 5-7). Answer: A. | A risk table. Description: "Maintained by Rafael Bose." Beatriz Varga: 2 edits + 1 incident. | **Rafael Bose** |

### Case 10. handover: the old owner hands it over (22)

| Scenario | n | Situation | Example from the data | Right answer |
|---|---|---|---|---|
| to_active | 8 | The old maintainer edits 4-7 times, writes "Handed this to *full name* in September/October." and stops; the new person edits 0-2 times afterwards. Answer: the new person. | A growth table. Tomas Demir: 6 edits. Yara Ueda: 2 edits. Note by Tomas Demir: "Handed this to Yara Ueda in October." | **Yara Ueda** |
| to_inactive | 4 | Like to_active, but the person it was handed to has left. | A finance pipeline. Farah Petrov: 7 edits. Note by Farah Petrov: "Handed this to Freya Eze in October." | **none** |
| other_wording | 6 | Like to_active, but the handover note is in words the rules don't know, e.g. "*full name* has taken this over from me." | A growth table. Keiko Iyer: 5 edits. Ayaan Sato: 20 reads. Note by Keiko Iyer: "Passing this one to Rafael Castillo from now on." | **Rafael Castillo** |
| stale_description | 4 | Like to_active, and the description still names the old maintainer. They took themselves out, so the answer is the new person. | A risk pipeline. Description: "Owned and maintained by Yara Wong (risk-analytics). Ask them before changing the schema." Yara Wong: 6 edits. Tomas Brennan: 2 edits. Wren Quispe: 24 reads. Note by Yara Wong: "Handed this to Tomas Brennan in September." | **Tomas Brennan** |

### Case 11. disowned: the editor says they stopped (24)

| Scenario | n | Situation | Example from the data | Right answer |
|---|---|---|---|---|
| sole | 6 | The only editor edits 4-7 times, then writes "I no longer maintain this." | A platform table. Wren Kaur: 4 edits + 1 incident. Note by Wren Kaur: "I no longer maintain this." | **none** |
| with_successor | 6 | Like sole, and a teammate edits 2-4 times afterwards. Answer: the teammate. | A growth table. Sana Brennan: 7 edits + 1 incident. Ayaan Sato: 4 edits. Paloma Eze (risk-analytics): 21 reads. Note by Sana Brennan: "I no longer maintain this." | **Ayaan Sato** |
| sole_other_wording | 6 | Like sole, but the note is in words the rules don't know, e.g. "Not mine any more." | A marketing pipeline. Paloma Novak: 6 edits. Note by Paloma Novak: "I have moved off this; please stop assigning it to me." | **none** |
| successor_other_wording | 6 | Like with_successor, but the note is in words the rules don't know. | A platform table. Maeve Tanaka: 5 edits. Nico Eze: 2 edits. Note by Maeve Tanaka: "I have moved off this; please stop assigning it to me." | **Nico Eze** |

### Case 12. two_texts: two pieces of text (16)

| Scenario | n | Situation | Example from the data | Right answer |
|---|---|---|---|---|
| disagree | 8 | The description names A; an incident note names B; no person edits. | A marketing table. Description: "Maintainer: Beatriz Yilmaz." Sofia Bose (platform-eng): 1 incident. Note by Sofia Bose: "Escalated to Tomas Varga, who maintains this table; they fixed the upstream filter." | **none** |
| agree | 4 | The description and an incident note name the same person. | A finance table. Description: "Owned by Mina Kimura." Paloma Eze (risk-analytics): 1 incident. Note by Paloma Eze: "Escalated to Mina Kimura, who maintains this table; they fixed the upstream filter." | **Mina Kimura** |
| self_claim_vs_desc | 4 | The description names A; teammate B's incident note says "...; I maintain this job." Two texts, two people. | A finance table. Description: "Maintainer: Joss Trent." Yara Schmidt: 1 incident. Hiro Zhou: 20 reads. Note by Yara Schmidt: "Fixed the late partition; I maintain this job." | **none** |

### Case 13. cross_team_owner: the maintainer is on another team (18)

| Scenario | n | Situation | Example from the data | Right answer |
|---|---|---|---|---|
| sole_maintainer | 8 | The only editor (4-8 edits) is on a different team from the asset. | A platform table. Reza Tanaka (growth-analytics): 8 edits + 1 incident. | **Reza Tanaka** |
| with_domain_readers | 6 | Like sole_maintainer, plus someone from the asset's team reads 15-25 times. | A platform view. Tomas Demir (growth-analytics): 7 edits + 1 incident. Lars Iyer: 15 reads. | **Tomas Demir** |
| with_agreeing_description | 4 | Like sole_maintainer, and the description names them. | A growth dashboard. Description: "Built and maintained by Mei Brennan; rebuilt nightly by the scheduler." Mei Brennan (marketing-ops): 4 edits. Mina Schmidt (platform-eng): 22 reads. | **Mei Brennan** |

---

## 3. Decisions and why

### 3.1 The rule for the right answer

The brief says: *"The active person accountable for the asset: who maintains it, not who reads it most. … When the evidence does not single out one active person, the correct answer is none."*

It gives no weights, so we chose them. Every answer in the dataset follows this rule.

**Who is a candidate.** An active person who edited the asset, handled an incident on it, or is named in its text. These are never candidates:
- robots and people who have left
- people who only read
- anyone who took themselves out in a note: "I no longer maintain this", "Handed this to X", or "Escalated to X, who maintains this"

**Points.**

| Evidence | Points |
|---|---|
| Named as the maintainer in a description or a note, or says so in their own note ("I maintain this job") | 10, counted once however many texts name them |
| Incident response | 3 each |
| Edit | 2 each |
| Reads, generic text, owning a neighbouring asset | 0 |

There are no caps: 8 edits are 16 points.

**The answer.**
- **One candidate:** that person. A single edit or incident is enough when nobody else is in the picture.
- **Two or more candidates:** the one with the most points, if they lead every other candidate by **more than 2 points** (2 points is one edit). Otherwise none.

Two cases are none whatever the points say:
- Text names one person and **someone else has more points**. We don't switch to the editor (section 3.3).
- **Two texts name different people.** A person who took themselves out no longer counts as named.

**What this means for the scorer.** The scorer will use the same points. On rows whose answer depends on the chosen weights (near_tie and conflict), eval shows that the scorer follows this rule, not that the rule is right. Those cases are reported separately.

### 3.2 Near tie → none

| Scenario | Example | Points | Answer |
|---|---|---|---|
| margin_one | Beatriz Adeyemi 4 edits vs Valeria Silva 3 | 8 vs 6, a lead of 2 | none |
| mixed | Beatriz Varga 4 edits vs Valeria Kaur 3 edits + 1 incident | 8 vs 9, a lead of 1 | none |

**What the examiners' data says:** it has **no near ties**. The only assets where two people edit are 6 exact ties (for example Keiko Mbeki 3 + 1 vs Rhea Varga 3 + 1), and all 6 are none. So this label is our own judgment.

**Why not label the leader and give it low confidence?** Confidence is measured on the calibration split as "how often this group was right".
- If the truth said Beatriz, proposing Beatriz would be right every time. Calibration would then give near ties *high* confidence, and they would be auto-approved.
- With truth = none, they stay away from auto-approval.

### 3.3 Text names one person, someone else works on the asset

The person named in the text has 10 points. Each edit by someone else is 2 points and each incident 3.

| Someone else's activity | Points | Answer | Scenario |
|---|---|---|---|
| none | 10 vs 0 | the person named | text_only_evidence |
| 1–3 edits | 10 vs 2–6 | **the person named** | conflict / desc_vs_minor_editor |
| 1–2 edits + 1 incident | 10 vs 5–7 | **the person named** | conflict / desc_vs_incident_rival |
| 4–6 edits | 10 vs 8–12 | **none** (close) | conflict / desc_vs_close_editor |
| 7–20 edits | 10 vs 14–40 | **none** (the editor is ahead) | conflict / desc_vs_editor, note_vs_editor |

**Why none, and not the editor, when the editor is ahead:** the description says one person and the work says another. A description can be out of date, but nothing tells us that it is. Writing either name could be wrong, and the PDF asks above all for a safe write path.

**What the examiners' data says:**

| Situation in their data | Count | Their answer |
|---|---|---|
| "Maintained by X" and X is also the editor | 34 owned assets | X |
| "Maintained by X" and only robots edit | 4 orphans | X |
| Y writes "Escalated to X, who maintains this" and has 1 incident; X has no activity | 3 orphans | X, not Y |
| "Maintained by X" while another person edits | **0** | never tested |

Their data shows that text wins over weak or no activity. It never tests text against a person who edits. So the boundary (6 points or fewer → the person named; 8 or more → none) is our judgment.

This reverses the earlier handoff, which said the person named in the text always wins (Bianca).

### 3.4 Other decisions

| Situation | Answer | Why |
|---|---|---|
| One person made a single edit, and nobody else edits | that person | The evidence points at exactly one person. The "close" rule compares two people, so it doesn't apply. The examiners' data has no such case: their owners always have at least 2 edits. |
| A full name in text ("Maintained by Tomas Dahl") | Tomas Dahl | A full name identifies one person. |
| A first name with a team ("Owned and maintained by Emeka (finance-data)") | Emeka Petrov | The team narrows the five Emekas to one. |
| A first name alone ("Maintainer: Beatriz", "Maintained by Jonas", "Maintainer: Akira") | none | A first name alone names nobody, even when only one person has it. The examiners' data never uses one: all 41 names in its text are full names. |
| "I maintain this job" in the person's own note | that person | It counts the same as being named (10 points). It is the brief's own example note. |
| The same person is named in two texts | still 10 points | Two mentions are one statement. |
| Two texts name different people, including a self-claim against a description | none | Yara Schmidt's claim sits on an incident, so Yara has 13 points against Joss Trent's 10. We still say none: two texts disagree, and we can't tell which is right. |
| A note writer who also edits (Kofi Kaur: 5 edits, and "Escalated to Tomas Demir, who maintains this") | Tomas Demir | Kofi says someone else maintains it, so Kofi is out, whatever they did. |
| Handover to an active person, even with 0 edits so far | the person it was handed to | The previous maintainer said so and then stopped. |
| Handover, and an old description still names the previous maintainer | the person it was handed to | The previous maintainer took themselves out, so only one named person is left. |
| Handover to someone who has left | none | The writer is out, and the person named can't own anything. |
| Disowned, and someone continues (Sana Brennan 7 edits, then the note; Ayaan Sato 4 edits after) | Ayaan Sato | Sana removed themselves, so their edits no longer count. |
| A neighbour's owner with no other evidence | none | Owning a neighbouring asset is worth 0 points and never makes someone a candidate. The examiners agree: their two neighbour-only orphans are none. |
| Two people tied, on different teams | none | Being on the asset's team does not break a tie. |
| A robot or someone who left is the busiest | the active person who edits, or none if there isn't one | Robots and people who have left are never candidates. |

### 3.5 Text in other wordings

**Why.** The rules scorer reads text by looking for fixed wordings, such as "Maintained by X" or "Handed this to X". Real notes are written in many ways. If the dataset only used wordings the rules know, the scorer would never be wrong, and we could not measure how sure it should be.

**What we added.** 9 scenarios with 54 orphans, whose names end in `other_wording` or `harmless_text`. Their text is in words the rules scorer does not know.

| Kind of text | Example | Does it change the answer? |
|---|---|---|
| Names the maintainer | "Valeria Castillo looks after this one." | Yes. It counts exactly like "Maintained by Valeria Castillo." |
| Hands over | "Passing this one to Rafael Castillo from now on." | Yes. It counts like "Handed this to Rafael Castillo." |
| Disowns | "Not mine any more." | Yes. It counts like "I no longer maintain this." |
| Says nothing about ownership | "Ask in the data channel before running a backfill." | No. |

**The right answer never depends on the wording.** It follows the rule in 3.1, as if the text were in a known wording.

**Who can read these wordings.**

| Piece | Reads them? |
|---|---|
| The generator and this file | Yes. They decide the right answers. |
| The checker | Yes. It has to verify every answer. |
| The rules scorer | **No, on purpose.** It gets these orphans wrong, and those mistakes are what the confidence is measured from. |
| The model (later) | Reading them is its job. |

**Wordings kept for held_out.** Each kind has three wordings. Two are used in both splits. The third appears only on held_out orphans (and on owned assets), never on calibration orphans. That way the model can later be tested on a wording it was never tuned on.

| Kind | Kept for held_out only |
|---|---|
| Names the maintainer (description) | "Speak to X, who runs this." |
| Names the maintainer (note) | "Pinged X, who runs this." |
| Hands over | "X is picking this up; I have moved to another project." |
| Disowns | "I have moved off this; please stop assigning it to me." |
| Says nothing (note) | "Paired with X on the backfill." |
| Says nothing (description) | "Do not query during the nightly load." |

**Owned assets carry the same wordings**, always in line with the real owner, so that these texts do not give orphans away.

**The mix is our choice.** 42 of the 54 orphans have text that changes the answer, and 12 have text that changes nothing. The examiners' data has no such text, so this mix can't be checked against it.

### 3.6 How these rows are marked in the data

The truth file has fixed field names: `asset_id`, `owner`, `case` and `split`. We add no new fields. The `case` value already marks the judgment rows (`near_tie`, `conflict`), so eval can report them separately. The other-wording scenarios sit inside their normal cases; `eval/manifest.jsonl` gives each orphan's scenario name.

---

## 4. Notes for the scorer (these don't change the data)

> Done. Every note below is implemented in `owner_agent/proposing/rules.py` and measured in [EVAL.md](../EVAL.md); the groups in note 3 and 4 are the confidence groups there.

1. **Use the rule in section 3.1.** Run on the calibration split only (245 orphans), the rules scorer gets 224 right.
   - It is right on all 218 orphans whose text is in a known wording, and on all 6 with text that changes nothing.
   - It is wrong on all 21 whose answer depends on a wording it can't read. That is expected (section 3.5).
   - On the 2,510 owned assets it returns the catalog owner on 2,470. Of the other 40, 23 have an owner who has left (deliberate) and 17 carry a wording it can't read.
   - Calibration pins the weights down: being named must be 9–10 points, an incident 1–4, an edit 2, and "close" 2–3.
   - Owning a neighbouring asset is worth 0. The scorer can still mention it in the reason.
2. **Decline and explain.** For a near tie, a conflict or two texts:
   - Set owner = null, and list both people's signal_ids as evidence.
   - The reason says which way the scorer leans, for example: "Near tie: Beatriz Adeyemi 4 edits vs Valeria Silva 3. Beatriz is slightly ahead, but not enough to propose an owner." Or: "Description names Rhea Kaur, but Beatriz Yilmaz made 10 edits."
   - A decline can't be approved. If the reviewer agrees with the hint, they set the owner by hand, which is a `set_owner` change.
3. **Notice text it could not read.** The scorer can't read the other wordings, but it can see that an asset has a note or description it did not recognise. Proposals and declines on such assets should form their own groups, with their own measured confidence.
4. **Mark thin proposals.** A lone candidate with one or two edits is the right answer in this dataset, but the evidence is thin. The scorer should give these their own group so their confidence is measured separately.
5. **Report near_tie and conflict separately, and leave them out when choosing the approve-batch threshold.** They are our judgment, not the examiners'.
6. **The old scoring document is replaced by this rule.** The differences from it:
   - text is 10 points, not 20, counted once; edits and incidents have no caps
   - negative notes remove the writer; they don't subtract points
   - "close" is a lead of 2 points or less, and only between two candidates
   - owning an upstream asset is worth 0, not +1
   - "Text says Bianca, Tara made 10 edits" becomes a decline
   - the `cross_team` doubt can't be forced below the threshold: cross_team_owner answers are clear, so calibration will show those proposals are right

`generate.py` follows every decision in section 3. To change a count, edit the row in `scenarios.py`.
