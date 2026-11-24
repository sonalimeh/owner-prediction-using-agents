# Owner proposals with approval

A command-line agent that proposes an owner for every data asset that has none, waits for a person to approve each proposal, and only then writes it to the catalog. One Python process, one SQLite file.

```
load  →  propose  →  approve / reject / approve-batch  →  changes  →  apply  →  dump, audit
```

Three rules hold everywhere: only an approved proposal can reach the catalog; only `propose` may call a model; `propose` never reads the truth file. An independent checker ([checker/checker.md](checker/checker.md)) proves the write-path invariants from the outside.

Deliverables: this README, [EVAL.md](EVAL.md) (results, baseline comparison, wrong proposals) and [DECISIONS.md](DECISIONS.md) (one page of decisions).

## Layout

| Folder | What it is | Read |
|---|---|---|
| `owner_agent/` | The tool: storage, catalog loading, proposing (rules + model), review, applying, evaluating. Every action is a registered tool with an effect (`read`, `write`, `model`) so the rules above are enforced in one place (`owner_agent/tools/registry.py`). | module docstrings |
| `dataset_creation/` | The fixture generator, its checker, and the generated fixture (3,000 assets, 200 users, 52,114 signals, 490 labelled orphans). | [dataset_creation.md](dataset_creation/dataset_creation.md), [scenarios.md](dataset_creation/scenarios.md) |
| `scorer/` | The owner rules, explained step by step with a worked example, and how confidence is measured. The live implementation is `owner_agent/proposing/rules.py`. | [scorer.md](scorer/scorer.md) |
| `checker/` | The invariant checker. Standard library only, drives the tool as a black box. | [checker.md](checker/checker.md) |
| `reference/` | The examiners' reference set: 40 labelled orphans. | |
| `eval_results/` | The eval reports behind EVAL.md and `runs.json` with the run ids they came from. | |
| `scripts/explain_one.py` | Explain one asset through the real propose pipeline without writing anything. | |
| `tests/` | 115 unit tests. | |

## Setup

Python 3.11 or newer. No third-party packages.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

Model mode needs an API key. Copy it into `.env` (git-ignored) or export it:

```
ANTHROPIC_API_KEY=sk-ant-...
```

Models are declared in `owner_agent/proposing/llms/models.toml` (default `claude-haiku-4.5`); pick another with `propose --model <name>` or `OWNER_AGENT_MODEL`. An OpenAI-compatible endpoint works with `provider = "openai"`.

Every command takes `--db <file>`; the default is `$OWNER_AGENT_DB` or `catalog.db` in the current directory.

`pip install -e .` installs each command as a script on the virtualenv's path: `load`, `propose`, `proposals`, `approve`, `approve-batch`, `reject`, `changes`, `apply`, `evals`, `dump`, `audit`. `python -m owner_agent <command>` does the same without the scripts.

## The fixture and how it was built

`dataset_creation/generate.py --seed 42` builds 3,000 assets, 200 users, 1,953 edges, 52,114 signals and 490 orphans whose right answer is known: 286 with a person, 204 with none. The design is in [scenarios.md](dataset_creation/scenarios.md); the mechanics in [dataset_creation.md](dataset_creation/dataset_creation.md). The decisions that shape it:

- **13 cases, 87 scenarios.** The 6 cases the brief requires (`clear_owner`, `service_or_inactive_top_user`, `tied_evidence`, `text_only_evidence`, `no_signals`, `cross_team_decoy`) plus 7 we added because the brief's definition of an owner raises them: `readers_only` (who reads most is not the owner), `near_tie` (a lead of one edit), `conflict` (text names A, B does the work), `handover`, `disowned`, `two_texts` (two texts, two people) and `cross_team_owner` (the maintainer is on another team). Each case is split into scenarios with an even count, so every scenario lands half in `calibration` and half in `held_out`.
- **One rule for every label** (scenarios.md 3.1): an active person who edited, handled an incident or is named in text; named 10 points, incident 3, edit 2; the leader wins only by more than 2 points; text against more work, two texts naming different people, and near ties are none. The recipes set the answer first and then write the evidence, so no label is ever derived from the scorer.
- **Text in unknown wordings.** 54 orphans carry descriptions or notes in words the rule patterns do not know ("Passing this one to X from now on", "Not mine any more", "Speak to X, who runs this"). Without them the rules would never be wrong on our data and the confidence could not be measured. Each kind has two wordings used in both splits and one kept for `held_out` only, so the model is tested on text it was never tuned on. Owned assets carry the same wordings, so the texts do not give orphans away.
- **Decoys and noise.** Heavy readers from other teams who own the neighbouring assets, robots that out-edit the owner, people who left, name clashes planned on purpose (two Akiras on one team, a duplicated full name), and background activity that cannot change any answer. Asset ids are shuffled so an id never reveals the case.
- **Changes people make afterwards**: 16 `set_owner`, 7 `delete_asset`, 8 `deactivate_user`, all aimed at assets and users that proposals point to, so `apply` has real conflicts to refuse.
- **An independent checker.** `check_fixture.py` re-derives every truth label from the output files and shares no code with the generator.

```bash
python dataset_creation/generate.py --seed 42 --out dataset_creation/fixture/
python dataset_creation/check_fixture.py --dir dataset_creation/fixture/
```

The same seed always gives byte-identical files; `dataset_creation/fixture/SHA256SUMS` records them. The reference set can be checked too: `python dataset_creation/check_fixture.py --dir reference --profile reference`.

## Run the scenario

On the reference set (swap in `dataset_creation/fixture/` for the large one):

```bash
load reference/seed.jsonl
B=$(propose --baseline)                       # deterministic rules, no model call
M=$(propose)                                  # model-assisted: the model reads text the rules cannot

proposals --run $M                            # table of proposals with confidence and status
proposals --run $M --json                     # the full records

approve-batch --run $M --min-confidence 0.95 --actor dana
approve p_xxxxxxxxxxxx --actor dana
reject  p_xxxxxxxxxxxx --actor dana --reason "wrong team"

changes reference/changes.jsonl               # edits people made after propose ran
apply                                         # JSON report: applied / skipped (with reason) / already_applied

dump  > dump.jsonl
audit > audit.jsonl

evals reference/truth.jsonl --run $B
evals reference/truth.jsonl --run $M --compare $B
```

Approve from one run only: both runs propose for the same assets, so `apply` would write whichever was approved first and skip the other as a conflict. `propose` prints the run id on stdout and its counts on stderr. Proposals are immutable; decisions are final; `apply` is idempotent and resumable:

```bash
CRASH_AFTER=3 apply   # exits with code 75 after 3 committed owner writes
apply                 # resumes; ends in the same state as an uninterrupted run
```

### A real run

A session on the generated fixture (output trimmed). Note that `apply` writes nothing until a person has approved something:

```
% load dataset_creation/fixture/seed.jsonl
{"asset": 3000, "db": "catalog.db", "edge": 1953, "orphans": 490, "signal": 52114, "user": 200}
% propose --baseline
r_3c4c06f67891
{"cost_usd": 0.0, "declined": 205, "model_calls": 0, "model_errors": 0, "owners": 285, "proposals": 490}
% propose
r_051be9c27b17
{"cost_usd": 0.050309, "declined": 206, "model_calls": 54, "model_errors": 0, "owners": 284, "proposals": 490}
% changes dataset_creation/fixture/changes.jsonl
{"already_applied": 0, "applied": 31, "changes": [{"change_id": "c_001", "op": "set_owner", "outcome": "applied"}, ...], "skipped": 0}
% apply
{"already_applied": 0, "applied": 0, "results": [], "skipped": 0}
% evals dataset_creation/fixture/truth.jsonl --run r_051be9c27b17 --compare r_3c4c06f67891 --split calibration
run r_051be9c27b17  mode=model  model=claude-haiku-4.5  split=calibration  truth orphans=245  proposals not scored=245
note: consistency needs at least 3 runs
...
Consistency across r_051be9c27b17, r_3c4c06f67891
GROUP                         COMPARED  SAME  CONSISTENCY
overall                       245       224   0.914
conflict                      17        11    0.647
disowned                      12        6     0.500
handover                      11        8     0.727
text_only_evidence            51        45    0.882
(every other case)                           1.000
```

The next step in that session is `approve-batch --run r_051be9c27b17 --min-confidence 0.95 --actor <name>`, then `apply` again. Comparing the model run with the baseline run is not the consistency metric (that needs three runs of the same mode) but it is a quick per-case diff: the two agree everywhere except the four cases whose answer depends on text the rules cannot read. [EVAL.md](EVAL.md) section 3 uses it that way.

To look at one asset without writing anything:

```bash
python scripts/explain_one.py a_0120            # rules only
python scripts/explain_one.py a_0120 --model    # with the model, if the asset has text the rules cannot read
```

## Run the checker

```bash
python checker/check_invariants.py --fixture reference
python checker/check_invariants.py --fixture dataset_creation/fixture
```

Each run loads a fresh database in a temporary directory, runs the whole scenario including the crash and resume, and prints one `PASS`/`FAIL` line per invariant. Exit code 0 means all passed. `--keep` leaves the working directory for inspection. What each line compares is in [checker/checker.md](checker/checker.md).

## Run the evals

The reports in `eval_results/` were produced like this (run ids in `eval_results/runs.json`). Three model runs cost about $0.15 in total.

```bash
export OWNER_AGENT_DB=eval.db
load dataset_creation/fixture/seed.jsonl
B=$(propose --baseline)
M1=$(propose); M2=$(propose); M3=$(propose)
T=dataset_creation/fixture/truth.jsonl

evals $T --run $M1 --split calibration             # threshold table: pick x here only
evals $T --run $B  --split calibration
evals $T --run $B  --split held_out
evals $T --run $M1 --split held_out --compare $M2 $M3   # adds consistency across runs

export OWNER_AGENT_DB=ref_eval.db
load reference/seed.jsonl
RB=$(propose --baseline); RM=$(propose)
evals reference/truth.jsonl --run $RB
evals reference/truth.jsonl --run $RM
```

Add `--json` for the full result. `evals` never writes to the database; it reads the truth file, which no other command may do.

The confidence that `propose` records is the measured accuracy of each decision group on the calibration split, stored in `owner_agent/proposing/confidence.json`. To re-measure it after changing the rules:

```bash
evals $T --run $M1 --split calibration --calibrate owner_agent/proposing/confidence.json
```

## Tests

```bash
python -m unittest discover -s tests
```
