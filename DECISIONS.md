# Decisions

## What is code and what needs a model

Everything is deterministic code except one step: reading free text whose wording the rule patterns do not know. Counting edits and incidents, dropping robots, leavers and readers, resolving a name to a user, the points (named 10, incident 3, edit 2), the decision (decline when nobody, when two texts disagree, when text and work disagree, or when the lead is 2 points or less), the confidence lookup and every write are code ([scorer.md](scorer/scorer.md), `owner_agent/proposing/rules.py`).

The model is called only for an orphan whose description or note is neither a known wording nor routine: 11% of orphans on our fixture, 0% on the reference set. It never picks an owner. It returns facts (user id, role, source, quote); code drops any fact whose quote is not an exact substring of the source or does not name the user the way the rules accept a name. Surviving facts become the same mentions the patterns produce, and the rest is the baseline code (`owner_agent/proposing/text_facts.py`).

Why: the brief wants a safe, checkable write path and honest measurement. Code gives a reason a reviewer can verify, the same answer every run (consistency 1.000 across three runs) and no cost for 89% of orphans. Free text is the one input fixed patterns cannot cover, and it is where the baseline makes every one of its mistakes ([EVAL.md](EVAL.md) section 3).

## The threshold

`approve-batch --min-confidence 0.95`, chosen on the calibration split only. Confidence is the measured accuracy of a proposal's decision group on calibration. Any threshold from 0.25 to 0.89 gives precision 1.000 there, at coverage 0.510 (baseline) or 0.584 (model); 0.95 gives up 7 proposals, the thin group (a lone candidate with one or two edits, or one incident, confidence 0.89). Those are right 7 of 7 in our data, but the examiners' owners never have so little evidence, so they go to a person. Above the line: proposals on activity (0.99), on a name in a known wording (0.98), on text the model read (0.95). On held_out every approved group measured within 0.01 of its recorded confidence. Declines are never approved (EVAL.md section 4).

## The rule apply follows for each conflict (invariant 3)

Approved proposals are applied in approval order, one transaction each; the owner write and its audit entry commit together. `apply` re-reads the asset and the user and skips, naming the change and actor that caused it, when:

| Conflict | Rule |
|---|---|
| the asset was deleted | skip, never write to it |
| the asset has an owner set by a person | skip, never overwrite |
| the asset has an owner set by the agent | skip; the first applied proposal wins |
| the asset's version differs from the one the proposal saw | skip, even if it is still an orphan |
| the proposed owner is a service account | skip |
| the proposed owner is now inactive | skip |

A person's `set_owner` also raises the version, so the owner and version guards back each other up (the checker's mutation test confirmed it). The `UPDATE` repeats the conditions, so nothing can slip in between the read and the write. A skipped proposal stays approved and is reported as skipped on every later `apply`, never retried silently.

## What the coding agent proposed that was rejected

| Proposed | Rejected because | Chosen instead |
|---|---|---|
| The person named in the text is always the owner, whatever the work says. | A description can be stale and nothing says when. Either name could be wrong, and the brief puts safety first. | Being named is 10 points. A clear lead of work beats it; a close fight declines; text against more work declines (scenarios.md 3.3). |
| Confidence as the leader's share of points. | 126 of 143 calibration proposals have one candidate, so all of them, including 11 of the 15 wrong ones, would score 100%. | Decision groups with measured accuracy on calibration (scorer.md section 8). |
| In a near tie, name the leader with a low confidence. | Calibration measures how often a group is right. With the leader as truth, near ties would measure as reliable and be auto-approved. | A lead of 2 points or less declines; truth for those rows is none (scenarios.md 3.2). |

## What I verified myself

<!-- Drafted from the project's record. Edit this list so it says exactly what you did. -->

- Read the 40 reference orphans and their labels before fixing the rules, to see what the examiners count as an owner.
- Ran the full scenario on both fixtures and read the dump, the audit log and the apply report by hand, including the 8 skips on the reference set and the 24 on the fixture.
- Ran the invariant checker on both fixtures, then on three deliberately broken copies of `apply` (audit entry not written; owner and version guards removed; skipped rows dropped) and saw it fail on each (checker.md).
- Read every wrong proposal's reason on held_out and the two notes the model misread ("Paired with X on the backfill").
- Confirmed from the tool registry and its tests that `propose` cannot read the truth file and that only `propose` can call a model.
