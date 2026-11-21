import json
import os
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest import mock

from owner_agent.catalog import load
from owner_agent.proposing import llms, propose, rules
from owner_agent.proposing.llms import Completion, LLMError
from owner_agent.proposing.rules import Asset, Roster, Signal, User
from owner_agent.proposing.text_facts import Extractor

SEED = os.path.join(os.path.dirname(__file__), "..", "reference", "seed.jsonl")

ROSTER = Roster({
    u.id: u
    for u in [
        User("u_rhea", "Rhea Varga", "finance-data", "person", True),
        User("u_sam", "Sam Lee", "finance-data", "person", True),
        User("u_ana", "Ana Diaz", "finance-data", "person", True),
        User("u_ana2", "Ana Costa", "growth-analytics", "person", True),
        User("u_old", "Old Timer", "finance-data", "person", False),
        User("u_svc", "svc_airflow", "platform-eng", "service", True),
    ]
})
CONFIDENCE = rules.load_confidence()
UNREAD = "Passing this one on from next sprint; ask before changing the schema."


class FakeClient:
    """Stands in for a model: replies with whatever facts the test hands it."""

    name = "fake-model"

    def __init__(self, reply=None, error=None):
        self.reply = reply if reply is not None else {"facts": []}
        self.error = error
        self.calls = []

    def check(self):
        pass

    def complete(self, system, user):
        self.calls.append(json.loads(user))
        if self.error:
            raise LLMError(self.error)
        text = self.reply if isinstance(self.reply, str) else json.dumps(self.reply)
        return Completion(text, input_tokens=300, output_tokens=40)

    def cost(self, input_tokens, output_tokens):
        return (input_tokens + output_tokens) * 1e-6


class SlowClient(FakeClient):
    """A FakeClient whose calls take a while, counting how many are in flight at once."""

    def __init__(self):
        super().__init__()
        self.lock = threading.Lock()
        self.in_flight = self.peak = 0

    def complete(self, system, user):
        with self.lock:
            self.in_flight += 1
            self.peak = max(self.peak, self.in_flight)
        time.sleep(0.02)
        with self.lock:
            self.in_flight -= 1
        return super().complete(system, user)


def fact(user_id, role, source, quote):
    return {"user_id": user_id, "role": role, "source": source, "quote": quote}


def edits(user, n, start=0):
    return [Signal(f"s_{user}_{start + i}", user, "edit", "") for i in range(n)]


def run(client, description="", signals=()):
    """read_text, then the model on whatever it left unread, then decide: what propose does for one asset."""
    asset = Asset("a_1", "warehouse.finance.ledger", description, 1)
    signals = list(signals)
    mentions = rules.read_text(ROSTER, asset, signals)
    unread = rules.unread_text(asset, signals, {m.source for m in mentions})
    extraction = Extractor(client, ROSTER).extract(asset, signals, unread)
    decision = rules.decide(asset, signals, [], ROSTER, CONFIDENCE, mentions + extraction.mentions,
                            extraction.read, extraction.notes)
    return extraction, decision


class ExtractorTest(unittest.TestCase):
    def test_routine_and_pattern_text_make_no_call(self):
        client = FakeClient()
        signals = [Signal("s_1", "u_sam", "incident_response", "Restarted the load; data caught up."),
                   Signal("s_2", "u_ana", "incident_response", "Escalated to Rhea Varga, who maintains this.")]
        extraction, decision = run(client, "Maintained by Sam Lee.", signals)
        self.assertFalse(extraction.called)
        self.assertEqual(client.calls, [])
        self.assertEqual(decision.why, "texts_disagree")

    def test_only_unread_text_and_its_roster_with_teams_are_sent(self):
        client = FakeClient()
        signals = [Signal("s_1", "u_sam", "edit", "Restarted the load; data caught up."),
                   Signal("s_2", "u_ana", "incident_response", "Rhea Varga looks after this now.")]
        run(client, "Legacy extract.", signals)
        sent = client.calls[0]
        self.assertEqual([s["source"] for s in sent["sources"]], ["s_2"])
        self.assertEqual({r["user_id"] for r in sent["roster"]}, {"u_ana", "u_rhea"})
        self.assertEqual({r["team"] for r in sent["roster"]}, {"finance-data"})

    def test_handover_in_new_wording(self):
        note = Signal("s_9", "u_rhea", "edit", "Passing this one to Sam Lee from now on.")
        client = FakeClient({"facts": [
            fact("u_rhea", "former_maintainer", "s_9", "Passing this one to Sam Lee"),
            fact("u_sam", "maintainer", "s_9", "to Sam Lee from now on"),
        ]})
        extraction, decision = run(client, "", [note] + edits("u_rhea", 9) + edits("u_sam", 1))
        self.assertEqual((decision.owner, decision.group), ("u_sam", "proposal_model_text"))
        self.assertIn("Not counted: Rhea Varga says", decision.reason)
        self.assertEqual(extraction.read, ["s_9"])
        self.assertEqual((extraction.input_tokens, extraction.output_tokens), (300, 40))

    def test_former_maintainer_who_is_not_the_writer_is_ruled_out(self):
        description = "Rhea Varga used to look after this before she moved teams."
        client = FakeClient({"facts": [fact("u_rhea", "former_maintainer", "description", "Rhea Varga used to look after this")]})
        _, decision = run(client, description, edits("u_rhea", 5))
        self.assertEqual((decision.owner, decision.group), (None, "decline_model_text"))
        self.assertIn("Rhea Varga is ruled out by the text", decision.reason)

    def test_first_person_note_is_a_claim_by_the_writer(self):
        note = Signal("s_9", "u_ana", "incident_response", "Fixed the partition again; this one is mine.")
        client = FakeClient({"facts": [fact("u_ana", "maintainer", "s_9", "this one is mine")]})
        _, decision = run(client, "", [note])
        self.assertEqual(decision.owner, "u_ana")
        self.assertIn("s_9", decision.signal_ids)

    def test_names_must_meet_the_rules_name_policy(self):
        description = "Ana looks after this; Ana (growth-analytics) reviews changes."
        client = FakeClient({"facts": [
            fact("u_ana", "maintainer", "description", "Ana looks after this"),
            fact("u_ana2", "maintainer", "description", "Ana (growth-analytics) reviews changes"),
        ]})
        extraction, decision = run(client, description, edits("u_rhea", 2))
        self.assertEqual([m.user_id for m in extraction.mentions], ["u_ana2"])
        self.assertIn("Dropped 1 unsupported model fact(s)", decision.reason)

    def test_unsupported_facts_are_dropped(self):
        description = "Built by Sam Lee."
        client = FakeClient({"facts": [
            fact("u_sam", "maintainer", "description", "Sam Lee maintains this"),
            fact("u_ghost", "maintainer", "description", "Built by Sam Lee"),
            fact("u_sam", "maintainer", "s_404", "Built by Sam Lee"),
            fact("u_sam", "owner", "description", "Built by Sam Lee"),
        ]})
        _, decision = run(client, description, edits("u_rhea", 2))
        self.assertEqual((decision.owner, decision.group), ("u_rhea", "proposal_model_text"))
        self.assertIn("Dropped 4 unsupported model fact(s)", decision.reason)

    def test_maintainer_who_left_names_nobody(self):
        description = "Old Timer looks after this."
        client = FakeClient({"facts": [fact("u_old", "maintainer", "description", "Old Timer looks after this")]})
        _, decision = run(client, description, edits("u_sam", 2))
        self.assertEqual(decision.owner, "u_sam")
        self.assertIn("names 'Old Timer', who has left", decision.reason)

    def test_model_error_and_bad_reply_leave_the_text_unread(self):
        for client, error in ((FakeClient(error="HTTP 500"), "HTTP 500"), (FakeClient("sorry, I can't"), "unreadable model reply")):
            extraction, decision = run(client, UNREAD, edits("u_sam", 3))
            self.assertTrue(extraction.called)
            self.assertEqual((extraction.mentions, extraction.read), ([], []))
            self.assertIn(error, extraction.error)
            self.assertEqual(decision.group, "proposal_unread_text")


class ModelProposeTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.dir.name, "catalog.db")
        load.load(SEED, self.db)
        self.conn = sqlite3.connect(self.db, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        # The reference seed has no text the patterns cannot read; give a few orphans some.
        self.conn.execute(
            "UPDATE assets SET description = ? WHERE id IN "
            "(SELECT id FROM assets WHERE owner IS NULL ORDER BY id LIMIT 6)", (UNREAD,)
        )

    def tearDown(self):
        self.conn.close()
        self.dir.cleanup()

    def proposals(self, run_id):
        return {r["asset_id"]: dict(r) for r in self.conn.execute("SELECT * FROM proposals WHERE run_id = ?", (run_id,))}

    def test_model_is_called_only_for_unread_text(self):
        client = FakeClient()
        before = list(self.conn.execute("SELECT * FROM assets ORDER BY id"))
        run_id, counts = propose.propose(self.conn, use_baseline=False, client=client)
        self.assertEqual(list(map(tuple, self.conn.execute("SELECT * FROM assets ORDER BY id"))), list(map(tuple, before)))

        proposals = self.proposals(run_id)
        self.assertEqual(len(proposals), 40)
        called = [p for p in proposals.values() if p["model"] == "fake-model"]
        self.assertEqual((len(called), len(client.calls), counts["model_calls"]), (6, 6, 6))
        for p in called:
            self.assertEqual((p["input_tokens"], p["output_tokens"]), (300, 40))
            self.assertGreater(p["cost_usd"], 0)
            self.assertIn(p["confidence_group"], ("proposal_model_text", "decline_model_text"))
        for p in proposals.values():
            if p["model"] is None:
                self.assertEqual(p["cost_usd"], 0)
            self.assertEqual(p["confidence"], CONFIDENCE[p["confidence_group"]])
        run = self.conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        self.assertEqual((run["mode"], run["model"], run["prompt_version"]),
                         ("model", "fake-model", "scorer-v1+text-facts-v2"))

    def test_model_errors_keep_the_baseline_answer(self):
        baseline_run, _ = propose.propose(self.conn, use_baseline=True)
        model_run, counts = propose.propose(self.conn, use_baseline=False, client=FakeClient(error="HTTP 503"))
        self.assertEqual(counts["model_errors"], 6)
        base, model = self.proposals(baseline_run), self.proposals(model_run)
        for asset_id in base:
            self.assertEqual([base[asset_id][k] for k in ("proposed_owner", "confidence_group")],
                             [model[asset_id][k] for k in ("proposed_owner", "confidence_group")])
        self.assertEqual(sum("Model call failed (HTTP 503)" in p["reason"] for p in model.values()), 6)

    def test_workers_overlap_model_calls_with_same_results(self):
        serial_run, serial_counts = propose.propose(self.conn, use_baseline=False, client=SlowClient(), workers=1)
        client = SlowClient()
        parallel_run, parallel_counts = propose.propose(self.conn, use_baseline=False, client=client, workers=4)
        self.assertGreater(client.peak, 1)
        self.assertEqual(serial_counts, parallel_counts)
        fields = ("proposed_owner", "confidence", "confidence_group", "reason", "model")
        parallel = self.proposals(parallel_run)
        for asset_id, p in self.proposals(serial_run).items():
            q = parallel[asset_id]
            self.assertEqual([p[f] for f in fields], [q[f] for f in fields])
            if p["model"]:
                self.assertLess(q["latency_ms"], 1000)

    def test_workers_must_be_positive(self):
        with self.assertRaisesRegex(propose.ProposeError, "--workers"):
            propose.propose(self.conn, use_baseline=False, client=FakeClient(), workers=0)

    def test_missing_key_writes_nothing(self):
        spec = llms.ModelSpec("m", "openai", "m", "OWNER_AGENT_TEST_MISSING_KEY", 0, 0)
        client = llms.LLMClient(spec, send=None)
        with self.assertRaisesRegex(propose.ProposeError, "OWNER_AGENT_TEST_MISSING_KEY"):
            propose.propose(self.conn, use_baseline=False, client=client)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM runs").fetchone()[0], 0)


class RegistryTest(unittest.TestCase):
    def test_bundled_registry_loads(self):
        default, specs = llms.load_registry()
        self.assertIn(default, specs)
        for spec in specs.values():
            self.assertTrue(spec.api_key_env)

    def test_unknown_model(self):
        with self.assertRaisesRegex(LLMError, "unknown model"):
            llms.get_client("nope")

    def test_cost(self):
        spec = llms.ModelSpec("m", "openai", "m", "K", input_usd_per_mtok=1.0, output_usd_per_mtok=4.0)
        self.assertAlmostEqual(llms.LLMClient(spec, None).cost(1000, 500), 0.003)

    def test_dotenv_does_not_override(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, ".env")
            with open(path, "w") as f:
                f.write("# comment\nOWNER_AGENT_T1='from-file'\nexport OWNER_AGENT_T2=file\n")
            with mock.patch.dict(os.environ, {"OWNER_AGENT_T2": "shell"}):
                llms.load_dotenv(path)
                self.assertEqual(os.environ["OWNER_AGENT_T1"], "from-file")
                self.assertEqual(os.environ["OWNER_AGENT_T2"], "shell")
            os.environ.pop("OWNER_AGENT_T1", None)


if __name__ == "__main__":
    unittest.main()
