import io
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from owner_agent import cli
from owner_agent.catalog import load
from owner_agent.proposing import propose
from owner_agent.storage import db

SEED = os.path.join(os.path.dirname(__file__), "..", "reference", "seed.jsonl")


class DecisionTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.dir.name, "catalog.db")
        load.load(SEED, self.db)
        conn = db.connect(self.db)
        self.run_id, _ = propose.propose(conn, use_baseline=True)
        rows = conn.execute(
            "SELECT proposal_id, proposed_owner FROM proposals WHERE run_id = ? ORDER BY asset_id", (self.run_id,)
        ).fetchall()
        conn.close()
        owners = [r["proposal_id"] for r in rows if r["proposed_owner"]]
        self.owner_a, self.owner_b = owners[0], owners[1]
        self.declined = next(r["proposal_id"] for r in rows if r["proposed_owner"] is None)

    def tearDown(self):
        self.dir.cleanup()

    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main([*argv, "--db", self.db])
        return code, out.getvalue(), err.getvalue()

    def decisions(self):
        conn = sqlite3.connect(self.db)
        try:
            return conn.execute("SELECT proposal_id, decision, actor, reason FROM decisions ORDER BY proposal_id").fetchall()
        finally:
            conn.close()

    def test_approve_records_decision(self):
        code, out, _ = self.cli("approve", self.owner_a, "--actor", "dana")
        self.assertEqual(code, 0)
        result = json.loads(out)
        self.assertEqual((result["outcome"], result["decision"], result["actor"]), ("recorded", "approved", "dana"))
        self.assertEqual(self.decisions(), [(self.owner_a, "approved", "dana", None)])

    def test_reject_records_reason(self):
        code, out, _ = self.cli("reject", self.owner_a, "--actor", "dana", "--reason", "wrong team")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["reason"], "wrong team")

    def test_repeating_the_same_decision_changes_nothing(self):
        self.cli("approve", self.owner_a, "--actor", "dana")
        before = self.decisions()
        code, out, _ = self.cli("approve", self.owner_a, "--actor", "lee")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["outcome"], "unchanged")
        self.assertEqual(json.loads(out)["actor"], "dana")
        self.assertEqual(self.decisions(), before)

        self.cli("reject", self.owner_b, "--actor", "dana", "--reason", "no")
        code, out, _ = self.cli("reject", self.owner_b, "--actor", "dana", "--reason", "other words")
        self.assertEqual((code, json.loads(out)["outcome"], json.loads(out)["reason"]), (0, "unchanged", "no"))

    def test_contradicting_decision_is_an_error(self):
        self.cli("approve", self.owner_a, "--actor", "dana")
        code, out, err = self.cli("reject", self.owner_a, "--actor", "lee", "--reason", "no")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("already approved by dana", err)

        self.cli("reject", self.owner_b, "--actor", "dana", "--reason", "no")
        code, _, err = self.cli("approve", self.owner_b, "--actor", "dana")
        self.assertEqual(code, 1)
        self.assertIn("already rejected", err)
        self.assertEqual(len(self.decisions()), 2)

    def test_declined_proposal_cannot_be_approved(self):
        code, _, err = self.cli("approve", self.declined, "--actor", "dana")
        self.assertEqual(code, 1)
        self.assertIn("cannot be approved", err)
        self.assertEqual(self.decisions(), [])

    def test_declined_proposal_can_be_rejected(self):
        code, _, _ = self.cli("reject", self.declined, "--actor", "dana", "--reason", "Priya owns it")
        self.assertEqual(code, 0)

    def test_unknown_proposal_and_blank_inputs(self):
        self.assertIn("no proposal p_nope", self.cli("approve", "p_nope", "--actor", "dana")[2])
        self.assertIn("--actor must not be empty", self.cli("approve", self.owner_a, "--actor", "  ")[2])
        self.assertIn("needs a --reason", self.cli("reject", self.owner_a, "--actor", "dana", "--reason", " ")[2])
        self.assertEqual(self.decisions(), [])

    def test_database_enforces_the_rules_too(self):
        conn = db.connect(self.db)
        with self.assertRaisesRegex(sqlite3.IntegrityError, "names an owner"):
            conn.execute("INSERT INTO decisions VALUES (?, 'approved', 'x', NULL, 't')", (self.declined,))
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO decisions VALUES (?, 'rejected', 'x', NULL, 't')", (self.owner_a,))
        conn.execute("INSERT INTO decisions VALUES (?, 'approved', 'x', NULL, 't')", (self.owner_a,))
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO decisions VALUES (?, 'rejected', 'x', 'r', 't')", (self.owner_a,))
        for sql in ("UPDATE decisions SET actor = 'y'", "DELETE FROM decisions"):
            with self.assertRaisesRegex(sqlite3.IntegrityError, "final"):
                conn.execute(sql)
        conn.close()

    def test_decisions_do_not_touch_assets(self):
        _, before, _ = self.cli("dump")
        self.cli("approve", self.owner_a, "--actor", "dana")
        self.cli("reject", self.owner_b, "--actor", "dana", "--reason", "no")
        self.assertEqual(self.cli("dump")[1], before)

    def test_proposals_listing_shows_status(self):
        self.cli("approve", self.owner_a, "--actor", "dana")
        self.cli("reject", self.owner_b, "--actor", "lee", "--reason", "no")
        code, out, _ = self.cli("proposals", "--json")
        self.assertEqual(code, 0)
        rows = {r["proposal_id"]: r for r in map(json.loads, out.splitlines())}
        self.assertEqual(len(rows), 40)
        self.assertEqual(rows[self.owner_a]["status"], "approved")
        self.assertEqual(rows[self.owner_b]["decided_by"], "lee")
        self.assertEqual(rows[self.declined]["status"], "declined")

        _, out, _ = self.cli("proposals", "--status", "approved", "--json")
        self.assertEqual([json.loads(l)["proposal_id"] for l in out.splitlines()], [self.owner_a])

        _, out, _ = self.cli("proposals")
        self.assertIn(f"run {self.run_id}", out.splitlines()[0])
        self.assertIn("PROPOSAL", out.splitlines()[1])

        _, out, _ = self.cli("proposals", "--runs")
        self.assertEqual(json.loads(out)["run_id"], self.run_id)

    def batch(self, x, run=None, actor="dana"):
        code, out, err = self.cli("approve-batch", "--run", run or self.run_id, "--min-confidence", str(x), "--actor", actor)
        return code, json.loads(out) if out else None, err

    def run_proposals(self):
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        try:
            return conn.execute(
                "SELECT p.proposal_id, p.proposed_owner, p.confidence, d.decision, d.actor FROM proposals p"
                " LEFT JOIN decisions d USING (proposal_id) WHERE p.run_id = ?", (self.run_id,)
            ).fetchall()
        finally:
            conn.close()

    def test_batch_approves_pending_owner_proposals_at_or_above_threshold(self):
        threshold = 0.99
        code, report, _ = self.batch(threshold)
        self.assertEqual(code, 0)
        rows = self.run_proposals()
        expected = {r["proposal_id"] for r in rows if r["proposed_owner"] and r["confidence"] >= threshold}
        self.assertTrue(any(r["confidence"] == threshold for r in rows))
        self.assertEqual({a["proposal_id"] for a in report["approved"]}, expected)
        self.assertEqual({r["proposal_id"] for r in rows if r["decision"] == "approved"}, expected)
        declined = sum(r["proposed_owner"] is None for r in rows)
        self.assertEqual(report["skipped"]["declined"], declined)
        self.assertEqual(report["approved_count"] + sum(report["skipped"].values()), len(rows))

    def test_batch_leaves_decided_proposals_alone(self):
        self.cli("reject", self.owner_a, "--actor", "lee", "--reason", "no")
        self.cli("approve", self.owner_b, "--actor", "lee")
        _, report, _ = self.batch(0)
        self.assertEqual(report["skipped"]["already_decided"], 2)
        by_id = {r["proposal_id"]: r for r in self.run_proposals()}
        self.assertEqual((by_id[self.owner_a]["decision"], by_id[self.owner_a]["actor"]), ("rejected", "lee"))
        self.assertEqual(by_id[self.owner_b]["actor"], "lee")
        self.assertIsNone(by_id[self.declined]["decision"])

    def test_batch_is_repeatable(self):
        _, first, _ = self.batch(0.5)
        _, second, _ = self.batch(0.5, actor="lee")
        self.assertGreater(first["approved_count"], 0)
        self.assertEqual(second["approved_count"], 0)
        self.assertEqual(second["skipped"]["already_decided"], first["approved_count"])
        self.assertTrue(all(r["actor"] in (None, "dana") for r in self.run_proposals()))

    def test_batch_only_touches_its_run(self):
        conn = db.connect(self.db)
        other_run, _ = propose.propose(conn, use_baseline=True)
        conn.close()
        self.batch(0, run=other_run)
        self.assertTrue(all(r["decision"] is None for r in self.run_proposals()))

    def test_batch_rejects_bad_input(self):
        self.assertIn("no run r_nope", self.batch(0.5, run="r_nope")[2])
        self.assertIn("between 0 and 1", self.batch(1.5)[2])
        self.assertIn("--actor must not be empty", self.batch(0.5, actor=" ")[2])
        self.assertEqual(self.decisions(), [])

    def test_proposals_without_runs(self):
        load.load(SEED, self.db)
        code, _, err = self.cli("proposals")
        self.assertEqual(code, 1)
        self.assertIn("run propose first", err)


if __name__ == "__main__":
    unittest.main()
