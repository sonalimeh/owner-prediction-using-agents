import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from owner_agent import cli
from owner_agent.catalog import load
from owner_agent.proposing import propose
from owner_agent.review import decisions
from owner_agent.storage import db

HERE = os.path.dirname(__file__)
PROJECT = os.path.join(HERE, "..")
SEED = os.path.join(PROJECT, "reference", "seed.jsonl")
CHANGES = os.path.join(PROJECT, "reference", "changes.jsonl")


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.dir.name, "catalog.db")
        load.load(SEED, self.db)

    def tearDown(self):
        self.dir.cleanup()

    def cli(self, *argv, db_path=None):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main([*argv, "--db", db_path or self.db])
        return code, out.getvalue(), err.getvalue()

    def report(self, *argv):
        code, out, err = self.cli(*argv)
        self.assertEqual(code, 0, err)
        return json.loads(out)

    def query(self, sql, params=()):
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in conn.execute(sql, params)]
        finally:
            conn.close()

    def asset(self, asset_id):
        return self.query("SELECT * FROM assets WHERE id = ?", (asset_id,))[0]

    def write_changes(self, records):
        path = os.path.join(self.dir.name, "changes.jsonl")
        with open(path, "w") as f:
            f.writelines(json.dumps({"record_type": "change", **r}) + "\n" for r in records)
        return path

    def baseline_and_approve_all(self):
        conn = db.connect(self.db)
        run_id, _ = propose.propose(conn, use_baseline=True)
        decisions.approve_batch(conn, run_id, 0, "dana")
        conn.close()
        return run_id


class ChangesTest(Base):
    def test_each_op(self):
        path = self.write_changes([
            {"change_id": "c_1", "op": "set_owner", "asset_id": "a_0012", "owner": "u_001", "actor": "human:dana"},
            {"change_id": "c_2", "op": "delete_asset", "asset_id": "a_0017", "actor": "human:dana"},
            {"change_id": "c_3", "op": "deactivate_user", "user_id": "u_002", "actor": "human:it"},
        ])
        report = self.report("changes", path)
        self.assertEqual((report["applied"], report["skipped"], report["already_applied"]), (3, 0, 0))
        a12, a17 = self.asset("a_0012"), self.asset("a_0017")
        self.assertEqual((a12["owner"], a12["owner_set_by"], a12["version"]), ("u_001", "human", 2))
        self.assertEqual((a17["deleted"], a17["version"]), (1, 2))
        self.assertEqual(self.query("SELECT active FROM users WHERE id = 'u_002'")[0]["active"], 0)

    def test_repeating_a_file_changes_nothing(self):
        self.report("changes", CHANGES)
        _, before, _ = self.cli("dump")
        report = self.report("changes", CHANGES)
        self.assertEqual(report["already_applied"], 7)
        self.assertEqual(self.cli("dump")[1], before)

    def test_noop_changes_are_skipped(self):
        path = self.write_changes([
            {"change_id": "c_1", "op": "delete_asset", "asset_id": "a_0017", "actor": "h"},
            {"change_id": "c_2", "op": "delete_asset", "asset_id": "a_0017", "actor": "h"},
            {"change_id": "c_3", "op": "set_owner", "asset_id": "a_0017", "owner": "u_001", "actor": "h"},
            {"change_id": "c_4", "op": "deactivate_user", "user_id": "u_031", "actor": "h"},
        ])
        report = self.report("changes", path)
        self.assertEqual([c["outcome"] for c in report["changes"]], ["applied", "skipped", "skipped", "skipped"])
        self.assertEqual(self.asset("a_0017")["version"], 2)

    def test_reused_change_id_with_other_content_is_an_error(self):
        self.report("changes", self.write_changes([{"change_id": "c_1", "op": "delete_asset", "asset_id": "a_0017", "actor": "h"}]))
        code, _, err = self.cli("changes", self.write_changes([{"change_id": "c_1", "op": "delete_asset", "asset_id": "a_0024", "actor": "h"}]))
        self.assertEqual(code, 1)
        self.assertIn("different content", err)
        self.assertEqual(self.asset("a_0024")["deleted"], 0)

    def test_bad_file_writes_nothing(self):
        _, before, _ = self.cli("dump")
        cases = [
            ([{"change_id": "c_1", "op": "delete_asset", "asset_id": "a_0017", "actor": "h"},
              {"change_id": "c_2", "op": "delete_asset", "asset_id": "a_9999", "actor": "h"}], "unknown asset a_9999"),
            ([{"change_id": "c_1", "op": "set_owner", "asset_id": "a_0017", "owner": "u_999", "actor": "h"}], "unknown user u_999"),
            ([{"change_id": "c_1", "op": "rename", "asset_id": "a_0017", "actor": "h"}], "unknown op"),
            ([{"change_id": "c_1", "op": "delete_asset", "actor": "h"}], "missing asset_id"),
            ([{"change_id": "c_1", "op": "delete_asset", "asset_id": "a_0017", "actor": "h"}] * 2, "appears twice"),
        ]
        for records, message in cases:
            code, _, err = self.cli("changes", self.write_changes(records))
            self.assertEqual(code, 1)
            self.assertIn(message, err)
        self.assertEqual(self.cli("dump")[1], before)
        self.assertEqual(self.query("SELECT count(*) AS n FROM changes")[0]["n"], 0)

    def test_deleted_assets_get_no_new_proposals(self):
        self.report("changes", self.write_changes([{"change_id": "c_1", "op": "delete_asset", "asset_id": "a_0017", "actor": "h"}]))
        conn = db.connect(self.db)
        run_id, counts = propose.propose(conn, use_baseline=True)
        conn.close()
        self.assertEqual(counts["proposals"], 39)


class ApplyTest(Base):
    def outcomes(self, report):
        return {r["asset_id"]: (r["outcome"], r.get("reason", "")) for r in report["results"]}

    def test_reference_scenario(self):
        self.baseline_and_approve_all()
        self.report("changes", CHANGES)
        report = self.report("apply")
        out = self.outcomes(report)

        self.assertEqual(report["applied"] + report["skipped"], 27)
        self.assertIn("set by a person", out["a_0187"][1])
        self.assertIn("set by a person", out["a_0105"][1])
        self.assertIn("deleted", out["a_0179"][1])
        self.assertIn("deleted", out["a_0228"][1])
        self.assertIn("u_020 is no longer active", out["a_0038"][1])
        self.assertIn("u_003 is no longer active", out["a_0123"][1])
        self.assertEqual(out["a_0012"][0], "applied")

        a12 = self.asset("a_0012")
        self.assertEqual((a12["owner"], a12["owner_set_by"], a12["version"]), ("u_025", "agent", 2))
        self.assertEqual(self.asset("a_0187")["owner"], "u_008")
        self.assertEqual(self.asset("a_0187")["owner_set_by"], "human")

    def test_apply_notices_the_changes_people_made(self):
        self.baseline_and_approve_all()
        untouched = os.path.join(self.dir.name, "untouched.db")
        shutil.copy(self.db, untouched)
        code, out, _ = self.cli("apply", db_path=untouched)
        self.assertEqual(json.loads(out)["skipped"], 0)

        self.report("changes", CHANGES)
        out = self.outcomes(self.report("apply"))
        self.assertIn("(change c_001 by human:steward)", out["a_0187"][1])
        self.assertIn("(change c_002 by human:steward)", out["a_0105"][1])
        self.assertIn("(change c_004 by human:steward)", out["a_0179"][1])
        self.assertIn("(change c_005 by human:steward)", out["a_0228"][1])
        self.assertIn("(change c_006 by human:it-admin)", out["a_0038"][1])
        self.assertIn("(change c_007 by human:it-admin)", out["a_0123"][1])

    def test_every_agent_owner_has_exactly_one_audit_entry(self):
        self.baseline_and_approve_all()
        self.report("changes", CHANGES)
        report = self.report("apply")
        agent_owned = {a["id"]: a for a in self.query("SELECT * FROM assets WHERE owner_set_by = 'agent'")}
        audit = self.query("SELECT * FROM audit_log")
        self.assertEqual(sorted(e["asset_id"] for e in audit), sorted(agent_owned))
        self.assertEqual(len(audit), report["applied"])
        for e in audit:
            asset = agent_owned[e["asset_id"]]
            self.assertEqual((e["new_owner"], e["old_owner"]), (asset["owner"], None))
            self.assertEqual((e["version_after"], e["version_before"] + 1), (asset["version"], asset["version"]))
            self.assertEqual(e["actor"], "dana")
            decision = self.query("SELECT * FROM decisions WHERE proposal_id = ?", (e["proposal_id"],))[0]
            self.assertEqual(decision["decision"], "approved")

    def test_report_covers_every_approved_proposal_once(self):
        self.baseline_and_approve_all()
        report = self.report("apply")
        approved = {r["proposal_id"] for r in self.query("SELECT proposal_id FROM decisions WHERE decision = 'approved'")}
        ids = [r["proposal_id"] for r in report["results"]]
        self.assertEqual(sorted(ids), sorted(approved))
        self.assertEqual(len(ids), len(set(ids)))

    def test_apply_is_idempotent(self):
        self.baseline_and_approve_all()
        self.report("changes", CHANGES)
        first = self.report("apply")
        _, dump1, _ = self.cli("dump")
        _, audit1, _ = self.cli("audit")
        second = self.report("apply")
        self.assertEqual(self.cli("dump")[1], dump1)
        self.assertEqual(self.cli("audit")[1], audit1)
        self.assertEqual(second["applied"], 0)
        self.assertEqual(second["already_applied"], first["applied"])
        self.assertEqual(second["skipped"], first["skipped"])

    def test_rejected_and_pending_proposals_are_not_applied(self):
        conn = db.connect(self.db)
        run_id, _ = propose.propose(conn, use_baseline=True)
        rows = conn.execute(
            "SELECT proposal_id FROM proposals WHERE run_id = ? AND proposed_owner IS NOT NULL ORDER BY asset_id", (run_id,)
        ).fetchall()
        decisions.record(conn, rows[0]["proposal_id"], "rejected", "dana", "no")
        decisions.record(conn, rows[1]["proposal_id"], "approved", "dana")
        conn.close()
        report = self.report("apply")
        self.assertEqual([r["proposal_id"] for r in report["results"]], [rows[1]["proposal_id"]])
        self.assertEqual(len(self.query("SELECT * FROM assets WHERE owner_set_by = 'agent'")), 1)

    def test_version_change_is_skipped(self):
        self.baseline_and_approve_all()
        conn = sqlite3.connect(self.db)
        conn.execute("UPDATE assets SET version = version + 1 WHERE id = 'a_0012'")
        conn.commit()
        conn.close()
        out = self.outcomes(self.report("apply"))
        self.assertEqual(out["a_0012"], ("skipped", "asset version changed from 1 to 2 since the proposal"))
        self.assertIsNone(self.asset("a_0012")["owner"])

    def test_second_approved_proposal_for_same_asset_is_skipped(self):
        self.baseline_and_approve_all()
        self.baseline_and_approve_all()
        report = self.report("apply")
        by_asset = {}
        for r in report["results"]:
            by_asset.setdefault(r["asset_id"], []).append(r)
        for results in by_asset.values():
            self.assertEqual([r["outcome"] for r in results], ["applied", "skipped"])
            self.assertIn("set by the agent", results[1]["reason"])

    def test_never_writes_to_service_accounts(self):
        conn = db.connect(self.db)
        conn.execute("INSERT INTO runs VALUES ('r_x', 'baseline', NULL, 'v', 't')")
        conn.execute(
            "INSERT INTO proposals VALUES ('p_x', 'r_x', 'a_0012', 1, 'u_035', 0.9, 'g', '[]', '[]', 'r', NULL, 'v', 0, 0, 0, 0, 't')"
        )
        decisions.record(conn, "p_x", "approved", "dana")
        conn.close()
        out = self.outcomes(self.report("apply"))
        self.assertEqual(out["a_0012"], ("skipped", "proposed owner u_035 is a service account"))


def strip_generated(lines, keys):
    return sorted(json.dumps({k: v for k, v in json.loads(l).items() if k not in keys}, sort_keys=True) for l in lines)


class CrashResumeTest(Base):
    def run_apply(self, db_path, crash_after=None):
        env = {k: v for k, v in os.environ.items() if k != "CRASH_AFTER"}
        if crash_after is not None:
            env["CRASH_AFTER"] = str(crash_after)
        return subprocess.run(
            [sys.executable, "-m", "owner_agent", "apply", "--db", db_path],
            cwd=PROJECT, env=env, capture_output=True, text=True,
        )

    def snapshot(self, db_path):
        _, dump, _ = self.cli("dump", db_path=db_path)
        _, audit, _ = self.cli("audit", db_path=db_path)
        return (
            strip_generated(dump.splitlines(), set()),
            strip_generated(audit.splitlines(), {"audit_id", "ts"}),
        )

    def assert_consistent(self, db_path):
        dump, audit = self.snapshot(db_path)
        agent = {(a["id"], a["owner"], a["version"]) for a in map(json.loads, dump) if a["owner_set_by"] == "agent"}
        logged = {(e["asset_id"], e["new_owner"], e["version_after"]) for e in map(json.loads, audit)}
        self.assertEqual(agent, logged)

    def test_crash_and_resume_matches_uninterrupted_run(self):
        self.baseline_and_approve_all()
        self.report("changes", CHANGES)
        reference = os.path.join(self.dir.name, "reference.db")
        shutil.copy(self.db, reference)
        self.assertEqual(self.run_apply(reference).returncode, 0)
        expected = self.snapshot(reference)

        for n in (0, 1, 3, 7):
            crashed = os.path.join(self.dir.name, f"crash{n}.db")
            shutil.copy(self.db, crashed)
            result = self.run_apply(crashed, crash_after=n)
            self.assertEqual(result.returncode, db.CRASH_EXIT_CODE, result.stderr)
            self.assertEqual(result.stdout, "")
            self.assert_consistent(crashed)
            self.assertEqual(len(self.snapshot(crashed)[1]), n)
            self.assertEqual(self.run_apply(crashed).returncode, 0)
            self.assertEqual(self.snapshot(crashed), expected)


if __name__ == "__main__":
    unittest.main()
