"""Studio endpoints for the discussion layer: the page must see the record and be able to select an alternative."""

from __future__ import annotations

import json
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import mkdtemp
import shutil

from src.userguide import dashboard, server
from src.userguide.flow import discussion
from tests.test_discussion import _client

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "src" / "userguide"
REGISTRY = ROOT / "build" / "userguide" / "registry_fields.yaml"


class TestDiscussionEndpoints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not REGISTRY.exists():
            raise unittest.SkipTest("parameter registry not built (run `python3 -m src.userguide all`)")
        cls.case = Path(mkdtemp())
        (cls.case / "request.md").write_text("urban albedo over one August week", encoding="utf-8")
        discussion.discuss(cls.case, client=_client())
        handler = server.make_handler(cls.case, PKG / "field_map.yaml", PKG / "dictionary.yaml", REGISTRY)
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        shutil.rmtree(cls.case, ignore_errors=True)

    def _get(self, path):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=20) as r:
            return json.loads(r.read())

    def _post(self, path, payload):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}",
                                     data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())

    def test_discussion_record_is_visible_to_the_page(self):
        j = self._get("/api/discussion")
        self.assertTrue(j["exists"])
        self.assertEqual(j["status"], "complete")
        self.assertEqual([a["id"] for a in j["alternatives"]], ["A"])
        self.assertTrue(j["disagreements"])
        self.assertFalse(j["running"])
        self.assertIsNone(j["selected"])

    def test_selecting_an_alternative_from_the_page(self):
        out = self._post("/api/select", {"alternative": "A"})
        self.assertEqual(out["selected"], "A")
        j = self._get("/api/discussion")
        self.assertTrue(j["alternatives"][0]["selected"])
        # selecting is not approving: the review stage still has no valid approval
        st = dashboard.load_state(self.case)
        self.assertEqual(st["current"], "review")
        approval = self.case / "approval.yaml"
        self.assertFalse(approval.exists() and json.loads(json.dumps(
            __import__("yaml").safe_load(approval.read_text())))["valid"])

    def test_unknown_alternative_is_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._post("/api/select", {"alternative": "Z"})
        self.assertEqual(ctx.exception.code, 409)

    def test_the_discussion_panel_lives_on_the_decompose_page(self):
        """Stage 2 is where the experts work; Review is the approval form and stays free of it."""
        dashboard.build(self.case)
        dec = (self.case / "dashboard" / "02_decompose.html").read_text(encoding="utf-8")
        self.assertIn('id="disc"', dec)
        self.assertIn("loadDiscussion", dec)
        self.assertIn("/api/discussion", dec)
        self.assertIn("/api/select", dec)
        review = (self.case / "dashboard" / "03_review.html").read_text(encoding="utf-8")
        self.assertNotIn("loadDiscussion", review)


if __name__ == "__main__":
    unittest.main()
