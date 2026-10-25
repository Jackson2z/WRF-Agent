"""Workflow dashboard: draft -> review -> Submit -> compile check -> next stage."""

import shutil
import tempfile
import unittest
from pathlib import Path

import yaml

from src.userguide import checks, dashboard

REPO = Path(__file__).resolve().parents[1]
PKG = REPO / "src" / "userguide"
DRAFT = REPO / "cases" / "singapore_albedo" / "draft.yaml"
REGISTRY = REPO / "build" / "userguide" / "registry_fields.yaml"


def _design():
    d = {k: str(v["value"]) for k, v in yaml.safe_load(DRAFT.read_text())["design"].items()}
    d.setdefault("wps:stand_lon", "103.8")
    return d


def _ids():
    fm = yaml.safe_load((PKG / "field_map.yaml").read_text())
    return [k for k, v in fm["fields"].items() if v["class"] == "design"]


class TestChecks(unittest.TestCase):
    def test_draft_passes(self):
        rep = checks.run(_design(), _ids())
        self.assertEqual(rep["verdict"], "PASS", [r for r in rep["rules"] if r["status"] == "FAIL"])
        self.assertEqual(rep["derived"]["input:dx"], [9000.0, 3000.0, 1000.0])
        self.assertEqual(rep["derived"]["input:time_step"], [45])
        self.assertEqual(rep["derived"]["input:o3input"], [2])

    def test_rule_failures(self):
        bad = dict(_design(), **{"wps:parent_grid_ratio": "1,2,3", "input:bl_pbl_physics": "1",
                                 "input:sf_surface_physics": "1", "wps:i_parent_start": "1,90,41"})
        failed = {r["rule"] for r in checks.run(bad, _ids())["rules"] if r["status"] == "FAIL"}
        self.assertTrue({"ratio_odd", "pbl_sfclay_compat", "urban_noah", "nest_inside_parent"} <= failed, failed)

    def test_missing_and_length(self):
        d = _design()
        d.pop("input:e_vert")
        d["wps:e_we"] = "100,121"
        failed = {r["rule"] for r in checks.run(d, _ids())["rules"] if r["status"] == "FAIL"}
        self.assertIn("required", failed)
        self.assertIn("per_domain_length", failed)


@unittest.skipUnless(REGISTRY.exists(), "registry not built (python3 -m src.userguide all)")
class TestDashboardFlow(unittest.TestCase):
    def setUp(self):
        self.case = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.case, ignore_errors=True)

    def test_submit_advances_or_returns(self):
        dashboard.init_case(self.case, "test request", DRAFT)
        dashboard.build(self.case)
        self.assertEqual(dashboard.load_state(self.case)["current"], "review")
        page = (self.case / "dashboard" / "03_review.html").read_text()
        self.assertIn('id="submit"', page)

        bad = dict(_design(), **{"wps:parent_grid_ratio": "1,2,3"})
        self.assertEqual(dashboard.submit(self.case, bad, PKG / "field_map.yaml")["verdict"], "FAIL")
        st = dashboard.load_state(self.case)
        # a failed compile returns to the human stage and must not leave a usable approval behind
        self.assertEqual(st["current"], "review")
        self.assertFalse(yaml.safe_load((self.case / "approval.yaml").read_text())["valid"])
        self.assertEqual(yaml.safe_load((self.case / "compile_report.yaml").read_text())["verdict"], "FAIL")

        self.assertEqual(dashboard.submit(self.case, _design(), PKG / "field_map.yaml")["verdict"], "PASS")
        st = dashboard.load_state(self.case)
        self.assertEqual(st["current"], "compile")
        self.assertTrue(yaml.safe_load((self.case / "approval.yaml").read_text())["valid"])
        self.assertEqual(yaml.safe_load((self.case / "experiment.yaml").read_text())["changed_from_draft"], [])
        dashboard.build(self.case)
        self.assertEqual(len(list((self.case / "dashboard").glob("0*.html"))), 8)

    def test_autosave_marks_and_reverts_user_edits(self):
        dashboard.init_case(self.case, "test request", DRAFT)
        fm = PKG / "field_map.yaml"
        d = _design()
        self.assertEqual(dashboard.save_review(self.case, d, fm), 0)          # unchanged form -> nothing written
        d["input:bl_pbl_physics"] = "1"
        self.assertEqual(dashboard.save_review(self.case, d, fm), 1)
        e = yaml.safe_load((self.case / "draft.yaml").read_text())["design"]["input:bl_pbl_physics"]
        self.assertEqual((e["value"], e["edited_by"], e["agent_value"]), ("1", "user", 2))
        self.assertTrue(e["reason"])                                          # agent's reason kept
        d["input:bl_pbl_physics"] = "2"                                       # back to the agent value
        dashboard.save_review(self.case, d, fm)
        e = yaml.safe_load((self.case / "draft.yaml").read_text())["design"]["input:bl_pbl_physics"]
        self.assertNotIn("edited_by", e)
        d["input:e_vert"] = "45"
        dashboard.submit(self.case, d, fm)
        self.assertEqual(yaml.safe_load((self.case / "experiment.yaml").read_text())["changed_from_draft"], ["input:e_vert"])


if __name__ == "__main__":
    unittest.main()
