import io
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from owner_agent import cli
from owner_agent.catalog import load
from owner_agent.storage import db

SEED = os.path.join(os.path.dirname(__file__), "..", "reference", "seed.jsonl")


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class DumpTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.dir.name, "catalog.db")
        load.load(SEED, self.db)

    def tearDown(self):
        self.dir.cleanup()

    def test_dump_matches_seed_assets(self):
        code, out, _ = run("dump", "--db", self.db)
        self.assertEqual(code, 0)
        with open(SEED) as f:
            seed_assets = [json.loads(l) for l in f if '"record_type": "asset"' in l]
        expected = sorted(
            (json.dumps({**a, "deleted": False}, sort_keys=True) for a in seed_assets),
            key=lambda l: json.loads(l)["id"],
        )
        self.assertEqual(out.splitlines(), expected)

    def test_dump_is_sorted_and_stable(self):
        _, first, _ = run("dump", "--db", self.db)
        _, second, _ = run("dump", "--db", self.db)
        self.assertEqual(first, second)
        ids = [json.loads(l)["id"] for l in first.splitlines()]
        self.assertEqual(ids, sorted(ids))

    def test_audit_empty_after_load(self):
        self.assertEqual(run("audit", "--db", self.db), (0, "", ""))

    def connect_with_proposals(self):
        conn = db.connect(self.db)
        conn.execute("INSERT INTO runs VALUES ('r_1', 'baseline', NULL, 'v', 't')")
        for pid, asset in (("p_1", "a_0002"), ("p_2", "a_0012"), ("p_3", "a_0003")):
            conn.execute(
                "INSERT INTO proposals VALUES (?, 'r_1', ?, 1, 'u_001', 0.9, 'g', '[]', '[]', 'r', NULL, 'v', 0, 0, 0, 0, 't')",
                (pid, asset),
            )
        return conn

    def test_audit_lines(self):
        conn = self.connect_with_proposals()
        conn.execute(
            "INSERT INTO audit_log (proposal_id, actor, asset_id, old_owner, new_owner, version_before, version_after, ts)"
            " VALUES ('p_2', 'dana', 'a_0012', NULL, 'u_025', 1, 2, '2026-11-19T00:00:00Z'),"
            "        ('p_1', 'dana', 'a_0002', NULL, 'u_001', 1, 2, '2026-11-19T00:00:01Z')"
        )
        conn.close()
        _, out, _ = run("audit", "--db", self.db)
        entries = [json.loads(l) for l in out.splitlines()]
        self.assertEqual([e["asset_id"] for e in entries], ["a_0002", "a_0012"])
        self.assertEqual(
            set(entries[0]),
            {"record_type", "audit_id", "proposal_id", "actor", "asset_id", "old_owner",
             "new_owner", "version_before", "version_after", "ts"},
        )

    def test_audit_log_is_append_only(self):
        conn = self.connect_with_proposals()
        conn.execute(
            "INSERT INTO audit_log (proposal_id, actor, asset_id, new_owner, version_before, version_after, ts)"
            " VALUES ('p_1', 'dana', 'a_0002', 'u_001', 1, 2, 't')"
        )
        for sql in ("UPDATE audit_log SET actor = 'x'", "DELETE FROM audit_log"):
            with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                conn.execute(sql)
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO audit_log (proposal_id, actor, asset_id, new_owner, version_before, version_after, ts)"
                " VALUES ('p_1', 'dana', 'a_0003', 'u_001', 1, 2, 't')"
            )
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO audit_log (proposal_id, actor, asset_id, new_owner, version_before, version_after, ts)"
                " VALUES ('p_unknown', 'dana', 'a_0003', 'u_001', 1, 2, 't')"
            )
        conn.close()

    def test_missing_db(self):
        missing = os.path.join(self.dir.name, "nope.db")
        code, out, err = run("dump", "--db", missing)
        self.assertEqual((code, out), (1, ""))
        self.assertIn("database not found", err)
        self.assertFalse(os.path.exists(missing))

    def test_old_schema_rejected(self):
        conn = db.connect(self.db)
        conn.execute("PRAGMA user_version = 1")
        conn.close()
        code, _, err = run("audit", "--db", self.db)
        self.assertEqual(code, 1)
        self.assertIn("run load again", err)


if __name__ == "__main__":
    unittest.main()
