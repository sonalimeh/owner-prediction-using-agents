#!/usr/bin/env python3
"""Independent checker for the write path: runs the whole scenario against the tool as a black box
and tests the eight invariants from the brief, plus the decision rules, on what the tool prints.

    python checker/check_invariants.py --fixture reference
    python checker/check_invariants.py --fixture dataset_creation/fixture --keep

It shares no code with the tool: standard library only, no import of owner_agent. Every command is
run as a subprocess (`python -m owner_agent ...`), and every judgement is made from the command
outputs the brief names: the dump, the audit log, the apply report, and the proposals listing. The
checker never opens the database. What the catalog *should* look like is derived from the fixture's
own seed.jsonl and changes.jsonl.

Scenario (in a temporary directory):
  load -> propose -> hand decisions -> approve-batch -> changes -> apply with CRASH_AFTER
  -> apply (resume) -> apply (again), with a dump and an audit snapshot after every step, and an
  uninterrupted twin copy of the database applied once for comparison.

Prints one PASS/FAIL line per check and exits non-zero if any check fails.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

CRASH_EXIT_CODE = 75
TIME_LIMIT_S = 30.0
PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SKIP_CONDITIONS = (  # the reasons apply may give, and the condition each one claims
    "deleted", "set by a person", "set by the agent", "version changed", "service account", "no longer active",
)


class CheckFailed(Exception):
    pass


# ------------------------------------------------------------------ running the tool
class Tool:
    """Runs the tool's commands on one database file and records how long each took."""

    def __init__(self, db_path, timings, propose_args):
        self.db = db_path
        self.timings = timings
        self.propose_args = propose_args

    def run(self, *args, env=None, expect=0):
        """Run one command. Returns the CompletedProcess; raises CheckFailed on an unexpected exit code."""
        base_env = {k: v for k, v in os.environ.items() if k != "CRASH_AFTER"}
        if env:
            base_env.update(env)
        cmd = [sys.executable, "-m", "owner_agent", *args, "--db", self.db]
        started = time.perf_counter()
        result = subprocess.run(cmd, cwd=PROJECT, env=base_env, capture_output=True, text=True)
        self.timings.append((args[0], time.perf_counter() - started))
        if expect is not None and result.returncode != expect:
            raise CheckFailed(
                f"{' '.join(args)} exited {result.returncode}, expected {expect}: {result.stderr.strip()[:400]}"
            )
        return result

    def jsonl(self, *args):
        out = self.run(*args).stdout
        return [json.loads(line) for line in out.splitlines() if line.strip()]

    def dump(self):
        return {r["id"]: r for r in self.jsonl("dump")}

    def audit(self):
        return self.jsonl("audit")

    def proposals(self, run_id):
        return {r["proposal_id"]: r for r in self.jsonl("proposals", "--run", run_id, "--json")}

    def load(self, seed):
        self.run("load", seed)

    def propose(self):
        result = self.run("propose", *self.propose_args)
        run_id = result.stdout.strip().splitlines()[-1]
        if not run_id.startswith("r_"):
            raise CheckFailed(f"propose did not print a run_id: {result.stdout!r}")
        return run_id

    def apply(self, crash_after=None):
        if crash_after is None:
            return json.loads(self.run("apply").stdout)
        result = self.run("apply", env={"CRASH_AFTER": str(crash_after)}, expect=None)
        return result


# ------------------------------------------------------------------ the checker's own model of the inputs
def read_jsonl(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def expected_users(seed_path, changes_path):
    """Who can own an asset after the changes: user id -> (user_type, active)."""
    users = {r["id"]: [r["user_type"], bool(r["active"])] for r in read_jsonl(seed_path) if r["record_type"] == "user"}
    for c in read_jsonl(changes_path):
        if c["op"] == "deactivate_user" and c["user_id"] in users:
            users[c["user_id"]][1] = False
    return users


def expected_changes(changes_path):
    """What people did to assets: asset id -> ('deleted' | owner id | None), last change wins."""
    state = {}
    for c in read_jsonl(changes_path):
        if c["op"] == "delete_asset":
            state[c["asset_id"]] = "deleted"
        elif c["op"] == "set_owner" and state.get(c["asset_id"]) != "deleted":
            state[c["asset_id"]] = c.get("owner")
    return state


# ------------------------------------------------------------------ normalisation for comparisons
def strip(records, keys):
    """Records as a sorted list of JSON strings, without the keys the tool generates."""
    return sorted(json.dumps({k: v for k, v in r.items() if k not in keys}, sort_keys=True) for r in records)


def agent_owners(dump):
    return {(a["id"], a["owner"], a["version"]) for a in dump.values() if a["owner_set_by"] == "agent"}


def logged_owners(audit):
    return {(e["asset_id"], e["new_owner"], e["version_after"]) for e in audit}


# ------------------------------------------------------------------ the checks
class Report:
    def __init__(self):
        self.results = []

    def check(self, name, title, problems):
        problems = list(problems)
        self.results.append({"check": name, "title": title, "ok": not problems, "problems": problems})
        status = "PASS" if not problems else "FAIL"
        print(f"{status}  {name:10s} {title}")
        for p in problems[:5]:
            print(f"        - {p}")
        if len(problems) > 5:
            print(f"        - ... and {len(problems) - 5} more")

    @property
    def ok(self):
        return all(r["ok"] for r in self.results)


def run_scenario(args, workdir, report):
    seed = os.path.join(args.fixture, "seed.jsonl")
    changes_file = os.path.join(args.fixture, "changes.jsonl")
    for path in (seed, changes_file):
        if not os.path.isfile(path):
            raise CheckFailed(f"fixture file missing: {path}")
    users = expected_users(seed, changes_file)
    human_changes = expected_changes(changes_file)

    timings = []
    db_path = os.path.join(workdir, "catalog.db")
    tool = Tool(db_path, timings, args.propose_args.split())

    # ---- 1. load
    tool.load(seed)
    d0, a0 = tool.dump(), tool.audit()
    report.check("setup", "load creates the catalog with an empty audit log",
                 ([f"audit log has {len(a0)} entries after load"] if a0 else [])
                 + ([] if d0 else ["dump is empty after load"]))

    # ---- 2. propose: invariant 1
    run_id = tool.propose()
    d1, a1 = tool.dump(), tool.audit()
    problems = []
    if strip(d1.values(), ()) != strip(d0.values(), ()):
        changed = [i for i in d0 if d0[i] != d1.get(i)] + [i for i in d1 if i not in d0]
        problems.append(f"dump differs after propose on {len(changed)} asset(s): {changed[:5]}")
    if a1 != a0:
        problems.append(f"audit log changed during propose ({len(a0)} -> {len(a1)} entries)")
    report.check("inv1", "propose changes no asset: dump identical before and after", problems)

    proposals = tool.proposals(run_id)
    orphans = [i for i, a in d0.items() if a["owner"] is None and not a["deleted"]]
    problems = []
    if sorted(p["asset_id"] for p in proposals.values()) != sorted(orphans):
        problems.append(f"{len(proposals)} proposals for {len(orphans)} orphans")
    report.check("setup", "propose records exactly one proposal per orphan", problems)

    # ---- 3. decisions by hand: the rules for approve and reject
    pending = sorted(p for p, r in proposals.items() if r["status"] == "pending" and r["proposed_owner"])
    declined = sorted(p for p, r in proposals.items() if r["status"] == "declined")
    problems = []
    manual_approved, manual_rejected = set(), set()
    if len(pending) < 2 or not declined:
        problems.append(f"fixture gives {len(pending)} pending owner proposals and {len(declined)} declines; need 2 and 1")
    else:
        p_approve, p_reject, q = pending[0], pending[1], declined[0]
        first = json.loads(tool.run("approve", p_approve, "--actor", "checker").stdout)
        if first.get("outcome") != "recorded":
            problems.append(f"first approve of {p_approve} reported {first.get('outcome')!r}, not 'recorded'")
        again = json.loads(tool.run("approve", p_approve, "--actor", "someone-else").stdout)
        if again.get("outcome") != "unchanged" or again.get("actor") != "checker":
            problems.append(f"repeated approve of {p_approve} should change nothing; got {again}")
        contra = tool.run("reject", p_approve, "--actor", "checker", "--reason", "changed my mind", expect=None)
        if contra.returncode == 0:
            problems.append(f"rejecting the approved {p_approve} succeeded; a contradicting decision must be an error")
        rej = json.loads(tool.run("reject", p_reject, "--actor", "checker", "--reason", "wrong person").stdout)
        if rej.get("outcome") != "recorded":
            problems.append(f"reject of {p_reject} reported {rej.get('outcome')!r}")
        contra = tool.run("approve", p_reject, "--actor", "checker", expect=None)
        if contra.returncode == 0:
            problems.append(f"approving the rejected {p_reject} succeeded; must be an error")
        bad = tool.run("approve", q, "--actor", "checker", expect=None)
        if bad.returncode == 0:
            problems.append(f"approving the declined proposal {q} succeeded; only a proposal that names an owner can be approved")
        manual_approved, manual_rejected = {p_approve}, {p_reject}
        after = tool.proposals(run_id)
        if after[p_approve]["status"] != "approved" or after[p_reject]["status"] != "rejected" or after[q]["status"] != "declined":
            problems.append("statuses after the hand decisions are not approved/rejected/declined as expected")
    if strip(tool.dump().values(), ()) != strip(d0.values(), ()):
        problems.append("dump changed during approve/reject")
    report.check("decisions", "decisions are final; only owner proposals can be approved", problems)

    # ---- 4. approve-batch, then read the approved set from the proposals listing
    batch = json.loads(tool.run("approve-batch", "--run", run_id, "--min-confidence", str(args.min_confidence),
                                "--actor", "checker").stdout)
    proposals = tool.proposals(run_id)
    approved = {p: r for p, r in proposals.items() if r["status"] in ("approved", "applied")}
    not_approved = {p for p, r in proposals.items() if r["status"] in ("pending", "rejected", "declined")}
    problems = []
    expected_batch = {p for p, r in proposals.items() if r["proposed_owner"] and r["confidence"] >= args.min_confidence
                      and p not in manual_approved and p not in manual_rejected}
    got_batch = {a["proposal_id"] for a in batch.get("approved", [])}
    if got_batch != expected_batch:
        problems.append(f"approve-batch approved {len(got_batch)} proposals, expected {len(expected_batch)} "
                        f"(owner named, confidence >= {args.min_confidence}, still pending)")
    if manual_rejected & set(approved):
        problems.append("approve-batch approved a proposal a person had rejected")
    if any(r["proposed_owner"] is None for r in approved.values()):
        problems.append("a declined proposal is approved")
    report.check("setup", f"approve-batch approves pending owner proposals at or above {args.min_confidence}", problems)

    # ---- 5. changes, then snapshot the state apply will act on; copy it for the uninterrupted twin
    tool.run("changes", changes_file)
    d2, a2 = tool.dump(), tool.audit()
    problems = [] if a2 == a0 else ["audit log changed during changes"]
    for asset_id, what in human_changes.items():
        a = d2.get(asset_id)
        if a is None:
            problems.append(f"{asset_id} from changes.jsonl is not in the dump")
        elif what == "deleted" and not a["deleted"]:
            problems.append(f"{asset_id} should be deleted after changes")
        elif what != "deleted" and (a["owner"] != what or (what and a["owner_set_by"] != "human")):
            problems.append(f"{asset_id} should have owner {what} set by a person after changes; has {a['owner']}/{a['owner_set_by']}")
    for asset_id in d0:
        if asset_id not in human_changes and d2[asset_id] != d0[asset_id]:
            problems.append(f"{asset_id} changed although changes.jsonl does not mention it")
    report.check("setup", "changes applies exactly the edits people made", problems)
    pristine = os.path.join(workdir, "after_changes.db")  # the state every apply below starts from
    shutil.copy(db_path, pristine)
    twin = os.path.join(workdir, "twin.db")
    shutil.copy(pristine, twin)

    # ---- 6. apply with a crash: invariant 7, middle state
    problems = []
    for n in args.crash_after:
        crashed = os.path.join(workdir, f"crash{n}.db")
        shutil.copy(pristine, crashed)
        ctool = Tool(crashed, timings, tool.propose_args)
        result = ctool.apply(crash_after=n)
        if result.returncode != CRASH_EXIT_CODE:
            problems.append(f"CRASH_AFTER={n}: exit code {result.returncode}, expected {CRASH_EXIT_CODE}")
        if result.stdout.strip():
            problems.append(f"CRASH_AFTER={n}: printed a report although it crashed")
        d_mid, a_mid = ctool.dump(), ctool.audit()
        new_owners = agent_owners(d_mid) - agent_owners(d2)
        logged = logged_owners(a_mid) - logged_owners(a2)
        if new_owners != logged:
            problems.append(f"CRASH_AFTER={n}: owner changes {sorted(new_owners - logged)[:3]} without audit entry, "
                            f"audit entries {sorted(logged - new_owners)[:3]} without owner change")
        if len(a_mid) - len(a2) != n:
            problems.append(f"CRASH_AFTER={n}: {len(a_mid) - len(a2)} transactions committed, expected {n}")
        ctool.apply()  # resume
        d_res, a_res = ctool.dump(), ctool.audit()
        if not set(json.dumps(e, sort_keys=True) for e in a_mid) <= set(json.dumps(e, sort_keys=True) for e in a_res):
            problems.append(f"CRASH_AFTER={n}: an audit entry written before the crash was changed by the resume")
        if n == args.crash_after[0]:
            # the first crash run, resumed, becomes the main database for the rest of the checks
            d3, a3 = d_res, a_res
            shutil.copy(crashed, db_path)
    report.check("inv7a", "crash leaves no owner change without its audit entry and vice versa", problems)

    # ---- 7. the resumed run against the uninterrupted twin: invariant 7, end state
    ttool = Tool(twin, timings, tool.propose_args)
    report_twin = ttool.apply()
    d3t, a3t = ttool.dump(), ttool.audit()
    problems = []
    if strip(d3.values(), ()) != strip(d3t.values(), ()):
        diff = [i for i in d3 if d3[i] != d3t.get(i)]
        problems.append(f"final dump after crash+resume differs from the uninterrupted run on {len(diff)} asset(s): {diff[:5]}")
    if strip(a3, {"audit_id", "ts"}) != strip(a3t, {"audit_id", "ts"}):
        problems.append(f"final audit log after crash+resume differs from the uninterrupted run "
                        f"({len(a3)} vs {len(a3t)} entries)")
    report.check("inv7b", "crash + resume reaches the same final dump and audit log as an uninterrupted run", problems)

    # The apply report checked below is the uninterrupted twin's: one run over every approved proposal.
    report1 = report_twin

    # ---- 8. apply again: invariant 6
    report2 = tool.apply()
    d4, a4 = tool.dump(), tool.audit()
    problems = []
    if strip(d4.values(), ()) != strip(d3.values(), ()):
        problems.append("dump changed on the second apply")
    if a4 != a3:
        problems.append(f"audit log changed on the second apply ({len(a3)} -> {len(a4)} entries)")
    if report2.get("applied") != 0:
        problems.append(f"second apply reports {report2.get('applied')} applied, expected 0")
    if report2.get("already_applied") != report1.get("applied", 0) + report1.get("already_applied", 0):
        problems.append("second apply's already_applied count does not equal the first run's applied count")
    skipped1 = {(r["proposal_id"], r.get("reason")) for r in report1["results"] if r["outcome"] == "skipped"}
    skipped2 = {(r["proposal_id"], r.get("reason")) for r in report2["results"] if r["outcome"] == "skipped"}
    if skipped1 != skipped2:
        problems.append("the skipped proposals and reasons differ between the two applies")
    report.check("inv6", "apply is idempotent: second run gives the same dump and adds no audit entries", problems)

    # ---- 9. invariant 2: every agent owner change traces to one approved proposal and one audit entry
    problems = []
    by_asset_audit = {}
    for e in a3:
        by_asset_audit.setdefault(e["asset_id"], []).append(e)
    for asset_id, owner, version in sorted(agent_owners(d3)):
        matches = [p for p, r in approved.items() if r["asset_id"] == asset_id and r["proposed_owner"] == owner]
        if len(matches) != 1:
            problems.append(f"{asset_id} owner {owner}: {len(matches)} approved proposals name that owner, expected exactly 1")
        entries = by_asset_audit.get(asset_id, [])
        if len(entries) != 1:
            problems.append(f"{asset_id}: {len(entries)} audit entries, expected exactly 1")
        elif entries[0]["new_owner"] != owner or (matches and entries[0]["proposal_id"] != matches[0]):
            problems.append(f"{asset_id}: audit entry names {entries[0]['new_owner']}/{entries[0]['proposal_id']}, "
                            f"dump has {owner}/{matches[:1]}")
    agent_assets = {a for a, _, _ in agent_owners(d3)}
    for e in a3:
        if e["asset_id"] not in agent_assets:
            problems.append(f"audit entry {e['proposal_id']} for {e['asset_id']}, but the asset is not agent-owned in the dump")
    report.check("inv2", "every agent owner change traces to exactly one approved proposal and one audit entry", problems)

    # ---- 10. invariant 3: what apply must never do
    problems = []
    for r in report1["results"]:
        p = approved.get(r["proposal_id"])
        if p is None:
            continue  # inv4 reports it
        before, after = d2.get(p["asset_id"]), d3.get(p["asset_id"])
        user = users.get(p["proposed_owner"])
        if r["outcome"] == "applied":
            if before["owner"] is not None:
                problems.append(f"{r['proposal_id']} applied over existing owner {before['owner']} ({before['owner_set_by']}) on {p['asset_id']}")
            if before["deleted"]:
                problems.append(f"{r['proposal_id']} applied to deleted asset {p['asset_id']}")
            if before["version"] != p["asset_version"]:
                problems.append(f"{r['proposal_id']} applied although {p['asset_id']} moved from version "
                                f"{p['asset_version']} to {before['version']}")
            if user is None or user[0] != "person" or not user[1]:
                problems.append(f"{r['proposal_id']} applied with owner {p['proposed_owner']} who is {user} (type, active)")
            if after["owner"] != p["proposed_owner"] or after["owner_set_by"] != "agent" or after["version"] != before["version"] + 1:
                problems.append(f"{r['proposal_id']} reported applied but {p['asset_id']} shows {after['owner']}/{after['owner_set_by']}/v{after['version']}")
        elif r["outcome"] == "skipped":
            reason = r.get("reason", "")
            if not reason:
                problems.append(f"{r['proposal_id']} skipped without a reason")
            holds = (
                ("deleted" in reason and before["deleted"])
                or ("set by a person" in reason and before["owner"] is not None and before["owner_set_by"] == "human")
                or ("set by the agent" in reason and before["owner"] is not None and before["owner_set_by"] == "agent")
                or ("version changed" in reason and before["version"] != p["asset_version"])
                or ("service account" in reason and user is not None and user[0] != "person")
                or ("no longer active" in reason and user is not None and not user[1])
            )
            if not holds:
                problems.append(f"{r['proposal_id']} skipped for '{reason}', but that condition does not hold on {p['asset_id']}")
            if after != before:
                problems.append(f"{r['proposal_id']} was skipped but {p['asset_id']} changed anyway")
    for asset_id, before in d2.items():
        after = d3[asset_id]
        if before["owner_set_by"] == "human" and after != before:
            problems.append(f"{asset_id}: owner set by a person was changed by apply")
        if before["deleted"] and after != before:
            problems.append(f"{asset_id}: deleted asset was changed by apply")
        if before["owner"] is not None and after["owner"] != before["owner"]:
            problems.append(f"{asset_id}: existing owner {before['owner']} replaced by {after['owner']}")
    report.check("inv3", "apply never overwrites a person's owner, a deleted or moved asset, or an inactive/service owner", problems)

    # ---- 11. invariant 4: the report accounts for every approved proposal exactly once
    problems = []
    ids = [r["proposal_id"] for r in report1["results"]]
    if len(ids) != len(set(ids)):
        problems.append("a proposal appears more than once in the apply report")
    missing = set(approved) - set(ids)
    extra = set(ids) - set(approved)
    if missing:
        problems.append(f"{len(missing)} approved proposal(s) missing from the report: {sorted(missing)[:5]}")
    if extra:
        problems.append(f"{len(extra)} proposal(s) in the report that were not approved: {sorted(extra)[:5]}")
    if set(ids) & not_approved:
        problems.append("a pending, rejected or declined proposal is in the report")
    for outcome in ("applied", "skipped", "already_applied"):
        n = sum(r["outcome"] == outcome for r in report1["results"])
        if report1.get(outcome) != n:
            problems.append(f"report says {outcome}={report1.get(outcome)} but lists {n}")
    bad_outcome = [r for r in report1["results"] if r["outcome"] not in ("applied", "skipped", "already_applied")]
    if bad_outcome:
        problems.append(f"unknown outcome(s): {[r['outcome'] for r in bad_outcome][:5]}")
    report.check("inv4", "the apply report accounts for every approved proposal exactly once", problems)

    # ---- 12. invariant 5: audit entries are complete and only ever appended
    problems = []
    required = ("proposal_id", "actor", "asset_id", "new_owner", "version_before", "version_after", "ts")
    for e in a3:
        absent = [k for k in required if e.get(k) in (None, "")] + (["old_owner"] if "old_owner" not in e else [])
        if absent:
            problems.append(f"audit entry for {e.get('asset_id')} lacks {absent}")
            continue
        if e["version_after"] != e["version_before"] + 1:
            problems.append(f"{e['asset_id']}: version {e['version_before']} -> {e['version_after']} is not +1")
        before = d2.get(e["asset_id"])
        if before and (e["version_before"] != before["version"] or e["old_owner"] != before["owner"]):
            problems.append(f"{e['asset_id']}: audit says before v{e['version_before']}/{e['old_owner']}, "
                            f"dump before apply had v{before['version']}/{before['owner']}")
        if e["actor"] != "checker":
            problems.append(f"{e['asset_id']}: approving actor recorded as {e['actor']!r}, expected 'checker'")
        p = approved.get(e["proposal_id"])
        if p is None or p["asset_id"] != e["asset_id"] or p["proposed_owner"] != e["new_owner"]:
            problems.append(f"{e['asset_id']}: audit proposal {e['proposal_id']} does not match an approved proposal for that asset and owner")
    full = lambda entries: [json.dumps(e, sort_keys=True) for e in entries]
    if not set(full(a2)) <= set(full(a3)) or a3 != a4:
        problems.append("audit entries were changed or removed between snapshots")
    ids = [e["audit_id"] for e in a3]
    if len(ids) != len(set(ids)):
        problems.append("duplicate audit_id")
    report.check("inv5", "each audit entry has proposal, actor, asset, old/new owner, versions, timestamp; append-only", problems)

    # ---- 13. invariant 8: time
    slow = [(cmd, s) for cmd, s in timings if cmd != "propose" and s >= TIME_LIMIT_S]
    slowest = max((s for cmd, s in timings if cmd != "propose"), default=0.0)
    report.check("inv8", f"every command except propose completes in under {TIME_LIMIT_S:.0f}s (slowest {slowest:.2f}s)",
                 [f"{cmd} took {s:.1f}s" for cmd, s in slow])

    return {
        "run_id": run_id, "orphans": len(orphans), "approved": len(approved),
        "apply_report": {k: report1[k] for k in ("applied", "skipped", "already_applied")},
        "timings": {cmd: round(s, 3) for cmd, s in timings},
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--fixture", required=True, help="directory with seed.jsonl and changes.jsonl")
    ap.add_argument("--min-confidence", type=float, default=0.95, help="threshold for approve-batch (default 0.95)")
    ap.add_argument("--crash-after", type=int, nargs="+", default=[3, 0, 1],
                    help="CRASH_AFTER values to test (default 3 0 1); the first one becomes the resumed main run")
    ap.add_argument("--propose-args", default="--baseline",
                    help='arguments for propose (default "--baseline"; use "" for the model-assisted mode)')
    ap.add_argument("--keep", action="store_true", help="keep the working directory and print its path")
    ap.add_argument("--json", action="store_true", help="also print the results as JSON")
    args = ap.parse_args(argv)
    if any(n < 0 for n in args.crash_after):
        ap.error("--crash-after values must not be negative")

    report = Report()
    workdir = tempfile.mkdtemp(prefix="owner_agent_check_")
    summary = {}
    try:
        summary = run_scenario(args, workdir, report)
    except CheckFailed as e:
        report.check("abort", "the scenario could not be completed", [str(e)])
    finally:
        if args.keep:
            print(f"working directory kept: {workdir}")
        else:
            shutil.rmtree(workdir, ignore_errors=True)

    failed = [r for r in report.results if not r["ok"]]
    print(f"\n{len(report.results) - len(failed)} of {len(report.results)} checks passed"
          + (f"; {len(failed)} FAILED" if failed else ""))
    if args.json:
        print(json.dumps({"ok": not failed, "checks": report.results, **summary}, indent=2, sort_keys=True))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
