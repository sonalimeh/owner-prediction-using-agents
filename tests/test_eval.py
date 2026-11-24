import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from owner_agent import cli, tools
from owner_agent.catalog import load
from owner_agent.storage import db

SEED = os.path.join(os.path.dirname(__file__), "..", "reference", "seed.jsonl")
TRUTH = [
    ("a_0002", "u_001", "clear_owner"),   # correct owner
    ("a_0003", "u_002", "clear_owner"),   # wrong person
    ("a_0012", None, "tied_evidence"),    # should have declined
    ("a_0017", None, "tied_evidence"),    # correct decline
    ("a_0024", "u_003", "no_signals"),    # missed
    ("a_0030", "u_004", "no_signals"),    # missing: no proposal
]
# asset -> (proposed owner, confidence, model)
RUN_1 = {
    "a_0002": ("u_001", 0.9, None), "a_0003": ("u_001", 0.6, "m"), "a_0012": ("u_001", 0.8, None),
    "a_0017": (None, 0.5, None), "a_0024": (None, 0.5, None),
}


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class EvalTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.dir.name, "catalog.db")
        self.truth = os.path.join(self.dir.name, "truth.jsonl")
        load.load(SEED, self.db)
        with open(self.truth, "w") as f:
            for asset, owner, case in TRUTH:
                f.write(json.dumps({"record_type": "truth", "asset_id": asset, "owner": owner,
                                    "case": case, "split": "held_out"}) + "\n")
        self.add_run("r_1", RUN_1)

    def tearDown(self):
        self.dir.cleanup()

    def add_run(self, run_id, proposals):
        conn = db.connect(self.db)
        conn.execute("INSERT INTO runs VALUES (?, 'model', 'm', 'v', ?)", (run_id, run_id))
        for asset, (owner, conf, model) in proposals.items():
            conn.execute(
                "INSERT INTO proposals VALUES (?, ?, ?, 1, ?, ?, ?, '[]', '[]', 'why', ?, 'v', 0, 0, 0.01, 5, 't')",
                (f"{run_id}_{asset}", run_id, asset, owner, conf,
                 "proposal_activity" if owner else "decline_nobody", model),
            )
        conn.close()

    def evaluate(self, *extra):
        code, out, err = run("evals", self.truth, "--db", self.db, "--run", "r_1", "--json", *extra)
        self.assertEqual(code, 0, err)
        return json.loads(out)

    def test_buckets_and_metrics(self):
        r = self.evaluate()
        g = r["groups"]["overall"]
        self.assertEqual(
            [g[k] for k in ("correct_owner", "wrong_person", "should_have_declined", "correct_decline", "missed", "missing")],
            [1, 1, 1, 1, 1, 1],
        )
        self.assertEqual((g["precision"], g["coverage"], g["correct_declines"]), (0.3333, 0.5, 0.5))
        self.assertEqual((g["proposals"], g["model_calls"], g["no_model_share"]), (5, 1, 0.8))
        self.assertEqual(r["missing"], ["a_0030"])
        self.assertEqual({w["asset_id"] for w in r["wrong"]}, {"a_0003", "a_0012"})
        self.assertTrue(any("no proposal" in n for n in r["notes"]))

    def test_group_with_no_owner_proposals_has_no_precision(self):
        self.assertIsNone(self.evaluate()["groups"]["no_signals"]["precision"])

    def test_thresholds(self):
        t = {row["x"]: row for row in self.evaluate()["thresholds"]}
        self.assertEqual((t[0.6]["approved"], t[0.8]["approved"], t[0.95]["approved"]), (3, 2, 0))
        self.assertEqual(t[0.85]["precision"], 1.0)
        self.assertIsNone(t[0.95]["precision"])

    def test_empty_split_says_so(self):
        r = self.evaluate("--split", "calibration")
        self.assertNotIn("groups", r)
        self.assertIn("no truth rows with split=calibration", r["notes"][0])

    def test_compare_uses_shared_orphans_only(self):
        self.add_run("r_2", {**RUN_1, "a_0002": ("u_005", 0.9, None)})
        self.add_run("r_3", {k: v for k, v in RUN_1.items() if k != "a_0024"})
        c = self.evaluate("--compare", "r_2", "r_3")["consistency"]
        self.assertEqual(c["skipped"], 2)
        self.assertEqual((c["groups"]["overall"]["compared"], c["groups"]["overall"]["same"]), (4, 3))

    def test_unaffected_by_apply(self):
        before = self.evaluate()
        run("approve-batch", "--db", self.db, "--run", "r_1", "--min-confidence", "0.5", "--actor", "me")
        run("apply", "--db", self.db)
        self.assertEqual(self.evaluate()["groups"], before["groups"])

    def test_text_report(self):
        code, out, _ = run("evals", self.truth, "--db", self.db, "--run", "r_1")
        self.assertEqual(code, 0)
        self.assertIn("Thresholds", out)
        self.assertIn("Missing proposals, a bug (1): a_0030", out)

    def test_only_eval_may_read_truth(self):
        conn = db.open_existing(self.db, readonly=True)
        try:
            with self.assertRaises(tools.ToolError):
                tools.call(tools.Context(conn, "proposals", readonly=True), "evaluate_run",
                           truth_path=self.truth, run_id="r_1")
        finally:
            conn.close()

    def test_confidence_groups_and_calibrate(self):
        table = os.path.join(self.dir.name, "confidence.json")
        code, _, err = run("evals", self.truth, "--db", self.db, "--run", "r_1", "--calibrate", table)
        self.assertEqual(code, 1)
        self.assertIn("needs --split calibration", err)

        with open(self.truth) as f:
            rows = [json.loads(l) for l in f]
        with open(self.truth, "w") as f:
            f.writelines(json.dumps({**r, "split": "calibration"}) + "\n" for r in rows)
        with open(table, "w") as f:
            json.dump({"groups": {"proposal_unread_text": {"right": 3, "total": 18, "confidence": 0.2},
                                  "proposal_activity": {"right": 9, "total": 9, "confidence": 0.91}}}, f)
        r = self.evaluate("--split", "calibration", "--calibrate", table)
        self.assertEqual(r["confidence_groups"]["proposal_activity"]["right"], 1)
        with open(table) as f:
            groups = json.load(f)["groups"]
        self.assertEqual(groups["proposal_unread_text"]["confidence"], 0.2)
        self.assertEqual((groups["proposal_activity"]["confidence"], groups["proposal_activity"]["run_id"]), (0.4, "r_1"))
        self.assertEqual(groups["decline_nobody"]["confidence"], 0.5)

    def test_rejects_non_truth_file(self):
        code, _, err = run("evals", SEED, "--db", self.db, "--run", "r_1")
        self.assertEqual(code, 1)
        self.assertIn("not a truth record", err)


if __name__ == "__main__":
    unittest.main()
