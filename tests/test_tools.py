import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from owner_agent import cli, tools
from owner_agent.catalog import load
from owner_agent.storage import db
from owner_agent.tools import registry

SEED = os.path.join(os.path.dirname(__file__), "..", "reference", "seed.jsonl")


class ToolTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.dir.name, "catalog.db")
        self.log = os.path.join(self.dir.name, "tools.jsonl")
        load.load(SEED, self.db)
        patcher = mock.patch.dict(os.environ, {tools.LOG_ENV: self.log})
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.dir.cleanup()

    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main([*argv, "--db", self.db])
        return code, out.getvalue(), err.getvalue()

    def logged(self):
        if not os.path.exists(self.log):
            return []
        with open(self.log) as f:
            return [json.loads(line) for line in f]

    def ctx(self, command, readonly=False):
        conn = db.open_existing(self.db, readonly=readonly)
        self.addCleanup(conn.close)
        return tools.Context(conn, command, readonly=readonly)

    def test_every_command_is_a_tool(self):
        names = {t["name"] for t in tools.catalog()}
        self.assertLessEqual(
            {"load_catalog", "propose_owners", "list_proposals", "list_runs", "approve_proposal",
             "reject_proposal", "approve_batch", "apply_changes", "apply_approved", "dump_assets",
             "read_audit_log"},
            names,
        )
        effects = {t["name"]: t["effect"] for t in tools.catalog()}
        self.assertEqual([n for n, e in effects.items() if e == tools.MODEL], ["extract_text_facts"])
        self.assertTrue(all(t["description"] for t in tools.catalog()))

    def test_one_log_line_per_call(self):
        self.cli("dump")
        (entry,) = self.logged()
        self.assertEqual((entry["tool"], entry["command"], entry["effect"], entry["ok"]), ("dump_assets", "dump", "read", True))
        self.assertEqual(entry["result"], {"count": 240})
        self.assertIsNone(entry["parent"])
        self.assertGreaterEqual(entry["ms"], 0)

    def test_propose_steps_are_logged_under_the_run(self):
        code, out, _ = self.cli("propose", "--baseline")
        self.assertEqual(code, 0)
        entries = self.logged()
        (top,) = [e for e in entries if e["tool"] == "propose_owners"]
        self.assertEqual(top["result"]["run_id"], out.strip())
        children = [e for e in entries if e["parent"] == top["call_id"]]
        per_tool = {}
        for e in children:
            per_tool[e["tool"]] = per_tool.get(e["tool"], 0) + 1
        self.assertEqual(per_tool["find_orphans"], 1)
        for name in ("read_evidence", "read_text", "decide_owner", "record_proposal"):
            self.assertEqual(per_tool[name], 40, name)
        self.assertNotIn("extract_text_facts", per_tool)
        users = [e for e in entries if e["tool"] == "read_users"]
        self.assertEqual(len(users), 1)

    def test_errors_are_logged_and_reported(self):
        code, _, err = self.cli("approve", "p_nope", "--actor", "dana")
        self.assertEqual(code, 1)
        self.assertIn("no proposal p_nope", err)
        (entry,) = self.logged()
        self.assertEqual((entry["tool"], entry["ok"]), ("approve_proposal", False))
        self.assertIn("no proposal p_nope", entry["error"])

    def test_arguments_are_checked(self):
        ctx = self.ctx("approve")
        cases = [
            ({"proposal_id": "p_1"}, "actor is required"),
            ({"proposal_id": "p_1", "actor": 3}, "actor must be str, got int"),
            ({"proposal_id": "p_1", "actor": "dana", "force": True}, "unknown argument"),
        ]
        for args, message in cases:
            with self.assertRaisesRegex(tools.ToolError, message):
                tools.call(ctx, "approve_proposal", **args)
        with self.assertRaisesRegex(tools.ToolError, "must be one of"):
            tools.call(self.ctx("proposals", readonly=True), "list_proposals", run_id="latest", status="maybe")
        with self.assertRaisesRegex(tools.ToolError, "baseline must be bool"):
            tools.call(self.ctx("propose"), "propose_owners", baseline=1)
        with self.assertRaisesRegex(tools.ToolError, "unknown tool"):
            tools.call(ctx, "delete_everything")
        self.assertEqual([e["ok"] for e in self.logged()], [False] * 6)

    def test_only_propose_may_call_a_model(self):
        with self.assertRaisesRegex(tools.ToolError, "only propose may"):
            tools.call(self.ctx("apply"), "extract_text_facts", asset=None, signals=[])

    def test_read_only_commands_cannot_write(self):
        with self.assertRaisesRegex(tools.ToolError, "read-only"):
            tools.call(self.ctx("dump", readonly=True), "apply_approved")

    def test_logging_is_off_by_default(self):
        with mock.patch.dict(os.environ, {tools.LOG_ENV: ""}):
            self.cli("propose", "--baseline")
        self.assertEqual(self.logged(), [])

    def test_log_entries_are_brief(self):
        brief = registry._brief
        self.assertEqual(brief("x" * 500), "x" * 117 + "...")
        self.assertEqual(brief(list(range(50))), {"count": 50})
        self.assertEqual(brief({"a": {"b": {"c": 1}}}), {"a": {"b": "<dict>"}})


if __name__ == "__main__":
    unittest.main()
