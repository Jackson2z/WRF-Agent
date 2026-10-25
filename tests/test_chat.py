"""In-page agent: tool loop over agent_tools with a scripted fake model (no network)."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

import yaml

from src.userguide import agent_tools, chat, dashboard

REPO = Path(__file__).resolve().parents[1]
DRAFT = REPO / "cases" / "singapore_albedo" / "draft.yaml"


class FakeClient:
    """Returns the scripted assistant turns in order."""

    def __init__(self, turns):
        self.turns = list(turns)
        self.seen = []
        self.chat = NS(completions=NS(create=self._create))

    def _create(self, model, messages, tools):
        self.seen.append(messages)
        content, calls = self.turns.pop(0)
        tc = [NS(id=f"c{i}", function=NS(name=n, arguments=json.dumps(a))) for i, (n, a) in enumerate(calls)]
        return NS(choices=[NS(message=NS(content=content, tool_calls=tc or None))])


@unittest.skipUnless(agent_tools.REGISTRY.exists(), "registry not built (python3 -m src.userguide all)")
class TestChat(unittest.TestCase):
    def setUp(self):
        self.case = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.case, ignore_errors=True)

    def test_agent_records_request_and_drafts(self):
        values = {k: v for k, v in yaml.safe_load(DRAFT.read_text())["design"].items()}
        fake = FakeClient([
            ("", [("set_request", {"text": "Singapore July 2024 albedo study"}), ("design_fields", {})]),
            ("", [("set_draft", {"values": values, "note": "first draft"})]),
            ("Draft ready. Please review the left panel and press Submit.", []),
        ])
        rows = chat.send(self.case, "I want a Singapore UHI albedo run", client=fake)
        self.assertEqual(rows[0]["role"], "user")
        self.assertEqual(rows[-1]["content"], "Draft ready. Please review the left panel and press Submit.")
        tool_rows = [r for r in rows if r["role"] == "tool"]
        self.assertEqual([r["name"] for r in tool_rows], ["set_request", "design_fields", "set_draft"])
        self.assertEqual(tool_rows[-1]["result"]["check"]["verdict"], "PASS")
        self.assertEqual(dashboard.load_state(self.case)["current"], "review")
        # the log replays into a valid tool-calling transcript
        msgs = chat._to_messages(chat.history(self.case))
        self.assertEqual(msgs[0]["role"], "system")
        self.assertTrue(any(m.get("tool_calls") for m in msgs))

    def test_rejects_non_design_field(self):
        res = agent_tools.set_draft(self.case, {"input:time_step": {"value": 30, "reason": "x"}})
        self.assertFalse(res["accepted"])

    def test_no_submit_tool(self):
        self.assertNotIn("submit", agent_tools.FUNCS)


if __name__ == "__main__":
    unittest.main()
