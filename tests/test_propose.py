import io
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from unittest import mock

from owner_agent import cli
from owner_agent.catalog import load
from owner_agent.proposing import rules
from owner_agent.proposing.rules import Asset, Mention, Neighbour, Roster, Signal, User
from owner_agent.storage import db

SEED = os.path.join(os.path.dirname(__file__), "..", "reference", "seed.jsonl")

ROSTER = Roster({
    u.id: u
    for u in [
        User("u_1", "Priya Rao", "finance-data", "person", True),
        User("u_2", "Sam Lee", "finance-data", "person", True),
        User("u_3", "Ana Diaz", "growth-analytics", "person", True),
        User("u_4", "Ana Moreau", "finance-data", "person", True),
        User("u_old", "Old Timer", "finance-data", "person", False),
        User("u_svc", "svc_airflow", "platform-eng", "service", True),
    ]
})
CONFIDENCE = rules.load_confidence()

_n = 0


def sig(user, kind="edit", note=""):
    global _n
    _n += 1
    return Signal(f"s_{_n:03}", user, kind, note)


def run_decide(signals=(), description="", neighbours=(), extra=(), model_read=()):
    asset = Asset("a_1", "warehouse.finance.revenue_daily", description, 1)
    signals = list(signals)
    mentions = rules.read_text(ROSTER, asset, signals) + list(extra)
    return rules.decide(asset, signals, list(neighbours), ROSTER, CONFIDENCE, mentions, model_read)


class CandidateTest(unittest.TestCase):
    def test_robots_and_people_who_left_are_not_candidates(self):
        d = run_decide([sig("u_svc")] * 9 + [sig("u_old")] * 9 + [sig("u_1")])
        self.assertEqual((d.owner, d.why, d.group), ("u_1", "only_candidate", "proposal_thin"))
        self.assertIn("svc_airflow is a robot", d.reason)
        self.assertIn("Old Timer has left", d.reason)

    def test_readers_are_not_candidates(self):
        d = run_decide([sig("u_1", "query")] * 50)
        self.assertEqual((d.owner, d.why, d.group), (None, "nobody", "decline_nobody"))
        self.assertIn("Priya Rao only reads (50 reads)", d.reason)

    def test_neighbours_are_context_only(self):
        d = run_decide([sig("u_1")] * 3, neighbours=[Neighbour("e_1", "a_9", "u_3"), Neighbour("e_2", "a_8", "u_1")])
        self.assertEqual((d.owner, d.edge_ids), ("u_1", ["e_2"]))
        self.assertIn("also owns 1 neighbouring asset", d.reason)


class PointsAndDecisionTest(unittest.TestCase):
    def test_incident_is_three_points_and_edit_two(self):
        level = run_decide([sig("u_1")] * 3 + [sig("u_2", "incident_response")] * 2)
        self.assertEqual((level.owner, level.why), (None, "close"))
        self.assertIn("They are level", level.reason)
        clear = run_decide([sig("u_1")] * 4 + [sig("u_2", "incident_response")])
        self.assertEqual((clear.owner, clear.why, clear.group), ("u_1", "clear_lead", "proposal_activity"))
        self.assertIn("Lead of 5 points", clear.reason)

    def test_a_lead_of_two_is_not_enough(self):
        d = run_decide([sig("u_1"), sig("u_1"), sig("u_2")])
        self.assertEqual((d.owner, d.why, d.group), (None, "close", "decline_close"))
        self.assertIn("a lead of 2 is not enough", d.reason)
        self.assertEqual(len(d.signal_ids), 3)

    def test_named_in_description_is_ten_points(self):
        d = run_decide(description="Maintained by Sam Lee.")
        self.assertEqual((d.owner, d.why, d.group), ("u_2", "only_candidate", "proposal_named_in_text"))
        self.assertIn("named in the description = 10", d.reason)

    def test_text_against_more_work_is_a_conflict(self):
        d = run_decide([sig("u_1")] * 6, description="Maintained by Sam Lee.")
        self.assertEqual((d.owner, d.why), (None, "conflict"))

    def test_two_texts_naming_different_people_decline(self):
        d = run_decide([sig("u_1", "incident_response", "Fixed the late partition; I maintain this job.")],
                       description="Maintained by Sam Lee.")
        self.assertEqual((d.owner, d.why), (None, "texts_disagree"))

    def test_handover_removes_the_writer(self):
        d = run_decide([sig("u_1")] * 4 + [sig("u_1", "incident_response", "Handed this to Sam Lee last week.")])
        self.assertEqual(d.owner, "u_2")
        self.assertIn("Priya Rao handed it over to Sam Lee", d.reason)

    def test_disowning_removes_the_writer(self):
        d = run_decide([sig("u_1")] * 5 + [sig("u_1", "edit", "I no longer maintain this.")] + [sig("u_2")] * 3)
        self.assertEqual(d.owner, "u_2")


class NameTest(unittest.TestCase):
    def test_first_name_alone_names_nobody(self):
        d = run_decide(description="Maintainer: Ana.")
        self.assertIsNone(d.owner)
        self.assertIn("names 'Ana', but a first name alone names nobody", d.reason)

    def test_first_name_with_team_names_one_person(self):
        self.assertEqual(run_decide(description="Owned and maintained by Ana (growth-analytics).").owner, "u_3")

    def test_named_person_who_left_names_nobody(self):
        d = run_decide([sig("u_1")] * 3, description="Maintained by Old Timer.")
        self.assertEqual(d.owner, "u_1")
        self.assertIn("names 'Old Timer', who has left", d.reason)

    def test_identifies_needs_a_unique_full_name_or_first_name_with_team(self):
        self.assertTrue(ROSTER.identifies("u_2", "passing this to sam lee"))
        self.assertFalse(ROSTER.identifies("u_3", "ask Ana"))
        self.assertTrue(ROSTER.identifies("u_3", "ask Ana from growth-analytics"))


class GroupTest(unittest.TestCase):
    def test_unread_text_has_its_own_groups(self):
        note = sig("u_2", "edit", "Passing this one to Priya Rao from now on.")
        d = run_decide([note] + [sig("u_2")] * 4)
        self.assertEqual((d.owner, d.group), ("u_2", "proposal_unread_text"))
        self.assertIn(f"Could not read 1 text(s) on this asset: {note.signal_id}", d.reason)
        self.assertEqual(run_decide(description="Ask around.").group, "decline_unread_text")

    def test_model_read_text_moves_to_the_model_groups(self):
        note = sig("u_2", "edit", "Passing this one to Priya Rao from now on.")
        model = [Mention("disowns", note.signal_id, "u_2"),
                 Mention("names", note.signal_id, "u_2", "Priya Rao", "u_1")]
        d = run_decide([note] + [sig("u_2")] * 4, extra=model, model_read=[note.signal_id])
        self.assertEqual((d.owner, d.group), ("u_1", "proposal_model_text"))
        self.assertEqual(d.confidence, CONFIDENCE["proposal_model_text"])
        self.assertEqual(run_decide(description="Ask around.", model_read=["description"]).group, "decline_model_text")

    def test_rules_out_removes_a_person_who_is_not_the_writer(self):
        ruled_out = [Mention("rules_out", "description", None, "Priya Rao", "u_1")]
        d = run_decide([sig("u_1")] * 5 + [sig("u_2")], description="Priya Rao moved on.",
                       extra=ruled_out, model_read=["description"])
        self.assertEqual(d.owner, "u_2")
        self.assertIn("Priya Rao is ruled out by the text", d.reason)

    def test_a_writer_who_also_claims_it_stays_a_candidate(self):
        note = sig("u_1", "edit", "Sam Lee and I maintain this.")
        model = [Mention("names", note.signal_id, "u_1", "Sam Lee", "u_2"), Mention("claims", note.signal_id, "u_1", user_id="u_1")]
        d = run_decide([note], extra=model, model_read=[note.signal_id])
        self.assertEqual((d.owner, d.why), (None, "texts_disagree"))


class ConfidenceTableTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "confidence.json")

    def tearDown(self):
        self.dir.cleanup()

    def write(self, value):
        with open(self.path, "w") as f:
            f.write(value if isinstance(value, str) else json.dumps(value))

    def test_packaged_table_covers_every_group(self):
        self.assertEqual(set(CONFIDENCE), set(rules.GROUPS))
        self.assertEqual(CONFIDENCE["proposal_activity"], 0.99)

    def test_unmeasured_group_gets_the_prior(self):
        self.write({"groups": {"proposal_activity": {"confidence": 0.9}}})
        table = rules.load_confidence(self.path)
        self.assertEqual((table["proposal_activity"], table["proposal_model_text"]), (0.9, rules.PRIOR))

    def test_env_overrides_the_packaged_table(self):
        self.write({"groups": {"proposal_activity": {"confidence": 0.5}}})
        with mock.patch.dict(os.environ, {rules.CONFIDENCE_ENV: self.path}):
            self.assertEqual(rules.load_confidence()["proposal_activity"], 0.5)

    def test_bad_tables_are_errors(self):
        for bad in ("not json", {"nope": 1}, {"groups": {"proposal_activity": {"confidence": 2}}}):
            self.write(bad)
            with self.assertRaises(rules.ConfidenceError):
                rules.load_confidence(self.path)


class ProposeCommandTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.dir.name, "catalog.db")
        load.load(SEED, self.db)

    def tearDown(self):
        self.dir.cleanup()

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main([*argv, "--db", self.db])
        return code, out.getvalue(), err.getvalue()

    def proposals(self, run_id):
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        return {r["asset_id"]: dict(r) for r in conn.execute("SELECT * FROM proposals WHERE run_id = ?", (run_id,))}

    def test_one_proposal_per_orphan_and_assets_unchanged(self):
        _, before, _ = self.run_cli("dump")
        code, out, _ = self.run_cli("propose", "--baseline")
        self.assertEqual(code, 0)
        run_id = out.strip()
        self.assertRegex(run_id, r"^r_[0-9a-f]{12}$")
        self.assertEqual(self.run_cli("dump")[1], before)

        proposals = self.proposals(run_id)
        conn = sqlite3.connect(self.db)
        orphans = {r[0] for r in conn.execute("SELECT id FROM assets WHERE owner IS NULL")}
        self.assertEqual(set(proposals), orphans)
        for p in proposals.values():
            self.assertIsNone(p["model"])
            self.assertEqual((p["input_tokens"], p["output_tokens"], p["cost_usd"]), (0, 0, 0))
            self.assertEqual(p["asset_version"], 1)
            self.assertIsInstance(json.loads(p["signal_ids"]), list)
            self.assertTrue(p["reason"])
            self.assertEqual(p["confidence"], CONFIDENCE[p["confidence_group"]])

    def test_runs_are_deterministic(self):
        first = self.proposals(self.run_cli("propose", "--baseline")[1].strip())
        second = self.proposals(self.run_cli("propose", "--baseline")[1].strip())
        keys = ("proposed_owner", "confidence", "confidence_group", "signal_ids", "edge_ids", "reason")
        for asset_id in first:
            self.assertEqual([first[asset_id][k] for k in keys], [second[asset_id][k] for k in keys])

    def test_proposals_are_immutable(self):
        self.run_cli("propose", "--baseline")
        conn = db.connect(self.db)
        for sql in ("UPDATE proposals SET confidence = 1", "DELETE FROM proposals", "DELETE FROM runs"):
            with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
                conn.execute(sql)

    def test_bad_confidence_table_writes_nothing(self):
        bad = os.path.join(self.dir.name, "bad.json")
        with open(bad, "w") as f:
            f.write("{")
        with mock.patch.dict(os.environ, {rules.CONFIDENCE_ENV: bad}):
            code, out, err = self.run_cli("propose", "--baseline")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("cannot read confidence table", err)
        self.assertEqual(sqlite3.connect(self.db).execute("SELECT count(*) FROM runs").fetchone()[0], 0)

    def test_model_mode_without_api_key_writes_nothing(self):
        env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "OWNER_AGENT_MODEL", "OWNER_AGENT_MODELS")}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch("owner_agent.proposing.llms.load_dotenv"):
            code, out, err = self.run_cli("propose")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("ANTHROPIC_API_KEY", err)
        conn = sqlite3.connect(self.db)
        self.assertEqual(conn.execute("SELECT count(*) FROM runs").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
