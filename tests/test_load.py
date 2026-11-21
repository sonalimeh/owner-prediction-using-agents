import json
import os
import sqlite3
import tempfile
import unittest

from owner_agent.catalog import load

SEED = os.path.join(os.path.dirname(__file__), "..", "reference", "seed.jsonl")

USER = {"record_type": "user", "id": "u_1", "name": "A", "team": "t", "user_type": "person", "active": True}
ASSET = {
    "record_type": "asset", "id": "a_1", "qualified_name": "w.s.t", "type": "table",
    "description": "", "owner": "u_1", "owner_set_by": "human", "version": 1,
}


class LoadTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.dir.name, "catalog.db")

    def tearDown(self):
        self.dir.cleanup()

    def write_seed(self, records):
        path = os.path.join(self.dir.name, "seed.jsonl")
        with open(path, "w") as f:
            f.writelines(json.dumps(r) + "\n" for r in records)
        return path

    def assert_load_fails(self, records, message):
        with self.assertRaisesRegex(load.LoadError, message):
            load.load(self.write_seed(records), self.db)
        self.assertFalse(os.path.exists(self.db))
        self.assertFalse(os.path.exists(self.db + ".loading"))

    def test_reference_seed(self):
        counts = load.load(SEED, self.db)
        self.assertEqual(counts, {"asset": 240, "user": 38, "edge": 158, "signal": 9856, "orphans": 40})
        conn = sqlite3.connect(self.db)
        row = conn.execute("SELECT active, user_type FROM users WHERE active = 0 LIMIT 1").fetchone()
        self.assertEqual(row[0], 0)

    def test_round_trips_fields(self):
        load.load(self.write_seed([ASSET, USER]), self.db)
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        asset = dict(conn.execute("SELECT * FROM assets").fetchone())
        self.assertEqual(asset, {**{k: v for k, v in ASSET.items() if k != "record_type"}, "deleted": 0})

    def test_replaces_existing_db(self):
        load.load(SEED, self.db)
        self.assertEqual(load.load(self.write_seed([USER]), self.db)["user"], 1)
        conn = sqlite3.connect(self.db)
        self.assertEqual(conn.execute("SELECT count(*) FROM assets").fetchone()[0], 0)

    def test_failed_load_keeps_existing_db(self):
        load.load(SEED, self.db)
        with self.assertRaises(load.LoadError):
            load.load(self.write_seed([ASSET]), self.db)
        conn = sqlite3.connect(self.db)
        self.assertEqual(conn.execute("SELECT count(*) FROM assets").fetchone()[0], 240)

    def test_missing_seed_file(self):
        with self.assertRaisesRegex(load.LoadError, "seed file not found"):
            load.load(os.path.join(self.dir.name, "nope.jsonl"), self.db)

    def test_unknown_record_type(self):
        self.assert_load_fails([USER, {"record_type": "truth"}], "line 2: unknown record_type")

    def test_missing_field(self):
        self.assert_load_fails([{k: v for k, v in USER.items() if k != "team"}], "missing team")

    def test_dangling_owner(self):
        self.assert_load_fails([ASSET], "assets a_1 references a missing users row")

    def test_bad_enum(self):
        self.assert_load_fails([USER, {**ASSET, "type": "model"}], "line 2: asset rejected")

    def test_duplicate_qualified_name(self):
        self.assert_load_fails([USER, ASSET, {**ASSET, "id": "a_2"}], "line 3: asset rejected")

    def test_owner_set_by_without_owner(self):
        self.assert_load_fails([{**ASSET, "owner": None}], "line 1: asset rejected")

    def test_active_must_be_boolean(self):
        self.assert_load_fails([{**USER, "active": "yes"}], "active must be true or false")


if __name__ == "__main__":
    unittest.main()
