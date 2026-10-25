"""runops: stages 5-8 as local operations (prepare / survey / verify / restart overlay)."""

import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

import yaml

from src.userguide import dashboard, runops
from tests.test_dashboard import DRAFT, PKG, _design

# a real URBPARM-producing run directory, if this machine has one (path given by the environment)
MOSCOW = Path(os.environ.get("WRFAGENT_REAL_RUN_DIR", "/nonexistent/run"))


def _touch_frames(d: Path, domain="d03", start="2024-06-15_18:00:00", n=10, step_min=20):
    d.mkdir(parents=True, exist_ok=True)
    t0 = datetime.strptime(start, "%Y-%m-%d_%H:%M:%S")
    for i in range(n):
        (d / f"wrfout_{domain}_{(t0 + timedelta(minutes=step_min * i)).strftime('%Y-%m-%d_%H:%M:%S')}").touch()
    (d / f"wrfrst_{domain}_{(t0 + timedelta(minutes=step_min * (n - 1))).strftime('%Y-%m-%d_%H:%M:%S')}").touch()
    (d / "rsl.error.0000").write_text("... SUCCESS COMPLETE WRF\n")


class TestRunops(unittest.TestCase):
    def setUp(self):
        self.case = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.case, ignore_errors=True)
        dashboard.init_case(self.case, "test", DRAFT)
        d = _design()
        dashboard.submit(self.case, d, PKG / "field_map.yaml")
        from src.userguide import emit
        self.manifest = emit.emit({"wrf_version": "4.6.1", "forcing": "era5",
                                   "design": {k: {"value": v} for k, v in d.items()}},
                                  self.case / "build")
        self.assertEqual(self.manifest["verdict"], "COMPLETE", self.manifest["missing_required"])

    def test_prepare_launch(self):
        rep = runops.prepare_launch(self.case)
        self.assertTrue(rep["ok"])
        self.assertFalse(rep["manifest"]["submitted"])
        self.assertTrue((self.case / "run" / "Allrun").exists())
        self.assertEqual((self.case / "run" / "namelist.input").read_bytes(),
                         (self.case / "build" / "namelist.input").read_bytes())

    def test_survey_verify(self):
        rd = self.case / "run"
        _touch_frames(rd, n=10)
        s = runops.survey(rd)
        self.assertEqual(s["domains"]["d03"]["frames"], 10)
        self.assertEqual(s["domains"]["d03"]["interval_min"], 20)
        self.assertEqual(s["domains"]["d03"]["gaps"], 0)
        self.assertTrue(s["rsl_success"])
        self.assertEqual(s["state"], "finished")
        # a frame count alone is not an acceptance criterion: without cadence and window it must not pass
        v = runops.verify(rd, expect_frames=10, interval_min=20)
        self.assertEqual(v["verdict"], "FAIL", v["checks"])
        failed = [c["check"] for c in v["checks"] if not c["ok"]]
        self.assertIn("explicit count, cadence and window", failed)
        # with the approved window, cadence and count it passes
        v = runops.verify(rd, expect_frames=10, interval_min=20,
                          start=s["domains"]["d03"]["first"], end=s["domains"]["d03"]["last"])
        self.assertEqual(v["verdict"], "PASS", v["checks"])
        # a count that does not match the window is rejected
        v = runops.verify(rd, expect_frames=12, interval_min=20,
                          start=s["domains"]["d03"]["first"], end=s["domains"]["d03"]["last"])
        self.assertEqual(v["verdict"], "FAIL")

    def test_restart_overlay_locks_design(self):
        rd = self.case / "run"
        _touch_frames(rd, n=10)  # latest wrfrst at +180 min = 21:00
        rep = runops.restart_override(self.case, rd)
        self.assertTrue(rep["ok"], rep)
        self.assertEqual(rep["changed_design_fields"], [])
        spec = yaml.safe_load((self.case / "restart.yaml").read_text())
        self.assertEqual(spec["design"]["wps:start_date"]["value"], "2024-06-15_21:00:00")
        self.assertEqual(spec["design"]["input:restart"]["value"], ".true.")
        self.assertIn("remaining_hours", spec["restart"])
        self.assertEqual(rep["emit_verdict"], "COMPLETE")
        changed_input = (self.case / "build" / "restart" / "namelist.input").read_text()
        self.assertIn("restart = .true.,", changed_input)
        self.assertIn("start_date", (self.case / "build" / "restart" / "namelist.wps").read_text())

    def test_restart_without_rst(self):
        rep = runops.restart_override(self.case, self.case / "run")
        self.assertFalse(rep["ok"])


@unittest.skipUnless(MOSCOW.exists(), "moscow local run not present")
class TestMoscowIntegration(unittest.TestCase):
    def test_survey_real_case(self):
        s = runops.survey(MOSCOW)
        d = s["domains"]["d03"]
        self.assertGreater(d["frames"], 500)          # historically 577 frames
        self.assertEqual(d["interval_min"], 20)
        v = runops.verify(MOSCOW, expect_frames=d["frames"], interval_min=20,
                          start=d["first"], end=d["last"])
        self.assertEqual(v["verdict"], "PASS", v["checks"])


if __name__ == "__main__":
    unittest.main()
