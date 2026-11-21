# The invariant checker

`check_invariants.py` is the independent referee the brief asks for. It runs the whole scenario against the tool as a black box and tests the eight invariants on what the tool prints. It shares no code with `propose` or `apply`: standard library only, no `import owner_agent`, and it never opens the database file.

```
python checker/check_invariants.py --fixture reference
python checker/check_invariants.py --fixture dataset_creation/fixture
python checker/check_invariants.py --fixture reference --keep        # keep the working directory
python checker/check_invariants.py --fixture reference --propose-args ""   # model-assisted propose (needs an API key)
```

Exit code 0 means every check passed. One line per check, `PASS` or `FAIL`, with the first offending records under a failure.

## What it reads

| Source | Used for |
|---|---|
| `dump` output | the state of every asset, snapshotted after every command |
| `audit` output | the audit log, snapshotted after every command |
| `apply` output | the apply report |
| `proposals --run <id> --json` output | which proposals exist, which asset, owner and version each one saw, and its status |
| the fixture's `seed.jsonl` and `changes.jsonl` | the checker's own model of who is an active person and what people changed |

Nothing else. In particular the checker does not read the SQLite file, so it cannot share an assumption with the code it checks.

## The scenario it runs

In a temporary directory:

1. `load seed.jsonl`, snapshot D0 / A0.
2. `propose --baseline`, snapshot D1 / A1.
3. By hand: approve one pending proposal twice, try to reject it, reject another, try to approve that one, try to approve a declined proposal.
4. `approve-batch --min-confidence 0.95`, then read the proposals listing to get the approved set.
5. `changes changes.jsonl`, snapshot D2 / A2. This is the state every `apply` below starts from; it is copied.
6. For each `--crash-after` value (default 3, 0 and 1): copy, `apply` with `CRASH_AFTER=n`, snapshot the mid state, `apply` again to resume, snapshot.
7. `apply` once on an untouched copy, the uninterrupted twin.
8. `apply` a second time on the resumed database.

## What each check compares

| Check | Compares |
|---|---|
| inv1 | D1 equals D0 and A1 equals A0: propose changed nothing. |
| decisions | The first approve is `recorded`, the repeat is `unchanged` and keeps the first actor, the contradicting reject exits non-zero, approving a rejected or a declined proposal exits non-zero, and the dump is untouched throughout. |
| inv7a | In the mid state, the set of (asset, owner, version) with `owner_set_by = agent` equals the set of (asset, new_owner, version_after) in the audit log; exactly n new audit entries; the tool exited with code 75 and printed no report; entries written before the crash are unchanged after the resume. |
| inv7b | The resumed run's final dump and audit log equal the uninterrupted twin's, ignoring `audit_id` and `ts`. |
| inv6 | The second apply leaves the dump and the audit log byte-identical, reports 0 applied, and the same skipped proposals with the same reasons. |
| inv2 | Each agent-owned asset in the final dump has exactly one approved proposal naming that asset and owner, and exactly one audit entry, whose proposal id and new owner match. Every audit entry points at an agent-owned asset. |
| inv3 | For each `applied` row: in D2 the asset had no owner, was not deleted, had the version the proposal saw, and the owner was an active person according to seed + changes; the final dump shows that owner, `agent`, version + 1. For each `skipped` row: the reason names a condition, and that condition really holds in D2; the asset is unchanged. No asset with an owner set by a person, no asset with any owner, and no deleted asset differs between D2 and the final dump. |
| inv4 | The proposal ids in the report equal the approved set, with no duplicates and no pending, rejected or declined proposal; the counts match the rows. |
| inv5 | Every audit entry has proposal id, actor, asset, old and new owner, version before and after, and a timestamp; `version_after = version_before + 1`; the before values match D2; the actor is the approver; entries present in earlier snapshots are present and unchanged in later ones. |
| inv8 | Every command except `propose` ran in under 30 seconds, measured around the subprocess. |

The `setup` lines are preconditions (one proposal per orphan, approve-batch approved exactly the expected set, changes did exactly what the file says). They are reported so a failure further down can be read in context.

## Proof that it bites

Run against three deliberately broken copies of `apply` (reference fixture):

| Mutation | Checks that fail |
|---|---|
| audit entry not written | inv7a, inv6, inv2 |
| owner and version guards removed, so a person's owner is overwritten | inv3, inv5 |
| skipped proposals left out of the report | inv4 |

Removing only the "already has an owner" guard changes nothing: a person's `set_owner` also raises the version, so the version guard still skips the proposal, with a truthful reason. The two guards back each other up.

The unmodified tool passes all checks on both fixtures.
