# Eval results

Every table below is copied from a report in `eval_results/`; the file is named under each table. `eval_results/runs.json` lists the run ids and the databases they live in. The commands that regenerate everything are in the README.

## 1. Setup

| | |
|---|---|
| Fixture | `dataset_creation/fixture/`: 490 labelled orphans, 245 calibration and 245 held_out, 13 cases ([scenarios.md](dataset_creation/scenarios.md)) |
| Reference set | `reference/`: 40 orphans labelled by the examiners, 6 cases |
| Baseline | `propose --baseline`: rules only, prompt version `scorer-v1` |
| Model-assisted | `propose`: the same rules; the model reads only the text the rule patterns cannot. `claude-haiku-4.5`, temperature 0, prompt `scorer-v1+text-facts-v2` |
| Threshold | 0.95 for `approve-batch`, chosen on the calibration split only (section 4) |
| Held_out | Looked at once, after the rules, the prompt and the threshold were fixed |

Confidence is per decision group: how often that group was right on calibration, pulled slightly towards 0.5 ((right + 1) / (total + 2)). A decline's confidence is the probability that none is the right answer; declines are never approved.

**How the fixture was designed to test this.** 13 cases in 87 scenarios: the brief's 6 plus 7 we added (`readers_only`, `near_tie`, `conflict`, `handover`, `disowned`, `two_texts`, `cross_team_owner`) because the brief's definition of an owner raises them. Every label follows one written rule (scenarios.md 3.1), set before the evidence is generated. 54 orphans carry text in wordings the rule patterns do not know; without them the baseline would never be wrong and nothing could be calibrated. Each kind of text has two wordings in both splits and one kept for held_out only, so held_out contains text the model was never tuned on. Decoys (heavy readers who own the neighbours, robots that out-edit the owner, people who left, planned name clashes) and background noise sit on orphans and owned assets alike. Two of the added cases, `near_tie` and `conflict`, rest on our own judgment of the weights, so they are reported as their own rows and a reader can discount them.

## 2. Results

### Held_out split (245 orphans)

| | Baseline | Model-assisted |
|---|---|---|
| Precision (named proposals that are right) | 0.901 (128 of 142) | **1.000** (141 of 141) |
| Coverage (orphans that get a named owner) | 0.580 | 0.576 |
| Max coverage (orphans whose truth is a person) | 0.584 | 0.584 |
| Correct declines (truth none, agent declined) | 0.941 (96 of 102) | **1.000** (102 of 102) |
| Wrong owner | 8 | 0 |
| Named someone when truth is none | 6 | 0 |
| Declined when truth is a person | 7 | 2 |
| Orphans decided without a model call | 100% | 89.0% (27 calls) |
| Cost per proposal | $0 | $0.000102 ($0.025 for the split) |
| Latency p50 / p95 | 0.2 ms / 0.2 ms | 0.2 ms / 1,254 ms |

`fixture_held_out_baseline.txt`, `fixture_held_out_model.txt`

Per case, model-assisted (baseline in brackets where it differs):

| Case | Orphans | Precision | Coverage | Correct declines |
|---|---|---|---|---|
| clear_owner | 43 | 1.000 | 0.954 (1.000) | - |
| conflict | 17 | 1.000 (0.571) | 0.412 | 10/10 (7/10) |
| cross_team_decoy | 19 | 1.000 | 1.000 | - |
| cross_team_owner | 9 | 1.000 | 1.000 | - |
| disowned | 12 | 1.000 (0.375) | 0.500 (0.667) | 6/6 (3/6) |
| handover | 11 | 1.000 (0.667) | 0.818 | 2/2 |
| near_tie | 4 | n/a | 0.000 | 4/4 |
| no_signals | 17 | n/a | 0.000 | 17/17 |
| readers_only | 5 | n/a | 0.000 | 5/5 |
| service_or_inactive_top_user | 30 | 1.000 | 0.500 | 15/15 |
| text_only_evidence | 51 | 1.000 (0.900) | 0.647 (0.588) | 18/18 |
| tied_evidence | 19 | n/a | 0.000 | 19/19 |
| two_texts | 8 | 1.000 | 0.250 | 6/6 |

Coverage is at its maximum for every case except `clear_owner` (the two model errors in section 6). `two_texts` has max coverage 0.250: six of its eight orphans have truth none.

### Reference set (40 orphans)

Baseline and model-assisted give identical results, because the reference text contains only wordings the rule patterns already know: the model-assisted run made **zero model calls**.

| Case | Orphans | Precision | Coverage | Correct declines |
|---|---|---|---|---|
| overall | 40 | 1.000 (27 of 27) | 0.675 | 13/13 |
| clear_owner | 10 | 1.000 | 1.000 | - |
| cross_team_decoy | 5 | 1.000 | 1.000 | - |
| no_signals | 5 | n/a | 0.000 | 5/5 |
| service_or_inactive_top_user | 7 | 1.000 | 0.714 | 2/2 |
| text_only_evidence | 7 | 1.000 | 1.000 | - |
| tied_evidence | 6 | n/a | 0.000 | 6/6 |

`reference_baseline.txt`, `reference_model.txt`. Caveat: the reference labels were read early on to understand the examiners' intent, so this is not an unseen test.

## 3. Baseline against the model, on the same orphans

The model is called for an orphan only when its description or a note is neither a known wording nor routine. On held_out that is 27 of 245 orphans (11%). Everything else is decided by the same code in both modes, so the two runs differ only where the model was called.

| Case | Model calls | What changed | Verdict |
|---|---|---|---|
| disowned | 6 | 2 wrong owners, 3 names where truth is none and 1 missed owner all fixed | pays: notes like "I have moved off this; please stop assigning it to me" |
| handover | 3 | 3 wrong owners (the old maintainer) became the person handed to | pays |
| conflict | 6 | 3 wrong names and 3 wrong declines fixed | pays: descriptions like "Speak to X, who runs this" |
| text_only_evidence | 6 | 3 wrong owners and 3 missed owners fixed | pays: the only evidence is text in an unknown wording |
| service_or_inactive_top_user | 3 | nothing: the text was harmless and the rules were already right | cost without gain, $0.0025 |
| clear_owner | 3 | **2 right owners became declines** (section 6) | the only loss |
| the other 7 cases | 0 | nothing | no cost |

The same picture from the other direction: `eval --compare` between the first model run and the baseline run on the scenario database (`catalog.db`, runs `r_051be9c27b17` and `r_3c4c06f67891`, calibration split) shows where the two modes give different answers:

| Case | Compared | Same answer | Agreement |
|---|---|---|---|
| overall | 245 | 224 | 0.914 |
| disowned | 12 | 6 | 0.500 |
| conflict | 17 | 11 | 0.647 |
| handover | 11 | 8 | 0.727 |
| text_only_evidence | 51 | 45 | 0.882 |
| every other case | | | 1.000 |

The 21 disagreements are exactly the 21 calibration orphans whose answer depends on an unknown wording, and in every one the model run has the right answer. This is not the consistency metric (section 5 is), but it is the cleanest statement of what the model adds: nothing changes outside those four cases.

On cost: the model-assisted run spends $0.05 per 490 orphans, so the question is not money but the two `clear_owner` declines and the latency (p95 1.25 s against 0.2 ms). Reading text is where the baseline is blind: every one of its 14 wrong proposals and 7 wrong declines on held_out sits on an asset with unread text, and all 14 wrong proposals carry confidence 0.20, below the threshold.

## 4. Threshold: precision against coverage

Chosen on the calibration split. `fixture_calibration_baseline.txt`, `fixture_calibration_model.txt`.

| Approve at x ≥ | Baseline approved | Baseline precision | Baseline coverage | Model approved | Model precision | Model coverage |
|---|---|---|---|---|---|---|
| 0.00 – 0.20 | 143 | 0.895 | 0.584 | 143 | 1.000 | 0.584 |
| 0.25 – 0.85 | 125 | 1.000 | 0.510 | 143 | 1.000 | 0.584 |
| 0.90 – 0.95 | 118 | 1.000 | 0.482 | 136 | 1.000 | 0.555 |

The confidence groups, as recorded in `owner_agent/proposing/confidence.json`:

| Group | Calibration right | Confidence | At 0.95 |
|---|---|---|---|
| proposal_activity (5 points or more) | 73 / 73 | 0.99 | auto-approved |
| proposal_named_in_text | 45 / 45 | 0.98 | auto-approved |
| proposal_model_text (text only the model read) | 18 / 18 | 0.95 | auto-approved |
| proposal_thin (one or two edits, or one incident) | 7 / 7 | 0.89 | to a person |
| proposal_unread_text (baseline only) | 3 / 18 | 0.20 | to a person |
| decline_nobody | 57 / 57 | 0.98 | never approved |
| decline_close | 36 / 36 | 0.97 | never approved |
| decline_model_text | 9 / 9 | 0.91 | never approved |
| decline_unread_text (baseline only) | 3 / 9 | 0.36 | never approved |

**Why 0.95.** Any x between 0.25 and 0.89 gives the same precision on calibration, so the table alone does not decide. 0.95 is chosen so that thin proposals (0.89) go to a person: they are right 7 of 7 times in our data, but the examiners' reference owners always have at least two edits, so a single edit as the only evidence is not something the data can vouch for. The cost is 7 proposals of coverage on calibration (0.510 to 0.482 for the baseline).

**Does the confidence hold on held_out?** `fixture_held_out_model.txt`:

| Confidence bin | Proposals | Correct | Accuracy | Mean confidence |
|---|---|---|---|---|
| 0.8 – 0.9 | 8 | 8 | 1.000 | 0.890 |
| 0.9 – 1.0 | 133 | 133 | 1.000 | 0.982 |

| Group | Held_out answers | Right | Recorded | Measured |
|---|---|---|---|---|
| proposal_model_text | 16 | 16 | 0.95 | 0.94 |
| proposal_named_in_text | 45 | 45 | 0.98 | 0.98 |
| proposal_thin | 8 | 8 | 0.89 | 0.90 |
| proposal_activity | 72 | 72 | 0.99 | 0.99 |
| decline_model_text | 11 | 9 | 0.91 | **0.77** |
| decline_nobody | 57 | 57 | 0.98 | 0.98 |
| decline_close | 36 | 36 | 0.97 | 0.97 |

Every proposal group holds. The one miss is `decline_model_text`: recorded 0.91, measured 0.77 on held_out. Both failures are the wrong declines in section 6, on a wording that exists only in held_out. Declines are never approved, so this costs coverage, not a wrong write.

## 5. Consistency across three runs

Three model-assisted runs on the same database (`r_f7503a4d606f`, `r_95d662024341`, `r_a72ddb752fe6`), compared on all 245 held_out orphans:

| | Compared | Same answer every time | Consistency |
|---|---|---|---|
| overall | 245 | 245 | 1.000 |
| every case | | | 1.000 |

`fixture_held_out_model.txt`. The model runs at temperature 0, the rules are deterministic, and the model only emits facts (user, role, source, quote) that code verifies before use, so a slightly different wording in the model's reply cannot change the answer. Cost per run: $0.0503, $0.0503, $0.0500.

## 6. Wrong proposals

Five, chosen to cover each cause. For each: was the cause the evidence given to the model, a tool or query, or the model's decision?

**1. a_1822, clear_owner, model-assisted. Declined; truth Laszlo Silva (u_197).**
Laszlo Silva has 4 edits and 1 incident (11 points). An incident note by another person reads "Paired with Rohan on the backfill." The rules cannot read it, so the model is asked. It reported Yusuf Mbeki as `maintainer` with that quote. Code accepted the fact (the quote is real and names a unique user), the rules then saw a text naming one person while another leads on points, and declined with "the text and the work disagree", confidence 0.91. **Cause: the model's decision.** Pairing on a backfill is not maintaining. The evidence was complete and the tool did what it should: the cross-check turned a wrong owner into a safe decline.

**2. a_1914, clear_owner, model-assisted. Declined; truth Daria Zhou (u_163).**
The same wording, "Paired with Yuki on the backfill.", read as Chen Yilmaz maintaining the asset; Daria Zhou has 6 edits. **Cause: the model's decision**, the same mistake. This wording is one of the held_out-only texts ([scenarios.md 3.5](dataset_creation/scenarios.md)); the two calibration wordings for harmless notes were read correctly in all 6 cases. A prompt example of a harmless note is the fix, and it would have to be tested on yet another unseen wording.

**3. a_0150, handover, baseline. Proposed Keiko Iyer (u_071); truth Rafael Castillo (u_155). Confidence 0.20.**
Keiko Iyer made 5 edits and then wrote "Passing this one to Rafael Castillo from now on." The rule patterns know "Handed this to X" but not this wording, so the note was flagged unread and Keiko was proposed. **Cause: a tool**: the deterministic text reader covers a fixed list of wordings. The proposal carries 0.20 precisely because unread text is present, so approve-batch would not take it. The model-assisted run reads the note and proposes Rafael Castillo.

**4. a_0522, disowned, baseline. Proposed Paloma Novak (u_053); truth none. Confidence 0.20.**
Paloma Novak made 6 edits and wrote "I have moved off this; please stop assigning it to me." Unread by the patterns, so the only editor was proposed. **Cause: a tool**, as above. Model-assisted: declined, correctly.

**5. a_0804, conflict, baseline. Proposed Beatriz Zhou (u_127); truth none. Confidence 0.20.**
Beatriz Zhou made 10 edits; the description says "Speak to Valeria Mbeki, who runs this." Unread, so the editor was proposed. The right answer under our rule is none: the text names one person and the work points to another. **Cause: a tool.** Model-assisted: the description is read, the conflict is seen, declined.

Pattern: the baseline's errors are all one kind (text it cannot read) and all land in the two low-confidence groups, which is what makes the baseline safe to use with approve-batch. The model's errors are its own reading mistakes, and every one of them became a decline rather than a wrong owner, because the model never picks the owner.

## 7. What these numbers do not show

- The truth labels follow our own rule ([scenarios.md 3.1](dataset_creation/scenarios.md)). Held_out shows that the code follows the rule and that the model reads unseen wordings; it cannot show that the rule itself is right. Near ties and text-versus-work conflicts are our judgment and are reported as their own cases.
- The reference labels were read before the rules were final.
- The fixture's mix of text that matters (42 orphans) against harmless text (12) is our choice. The examiners' data has no unknown wordings at all.
- Precision of 1.000 on held_out comes from data built so that the only hard part is text. Real catalogs will have other kinds of mistakes, and the 0.98 and 0.99 confidences are optimistic there.
