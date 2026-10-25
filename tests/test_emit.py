"""Emitter: approved specification -> deterministic namelist.wps / namelist.input + manifest."""

import shutil
import tempfile
import unittest
from pathlib import Path

import yaml

from src.userguide import checks, emit
from tests.test_dashboard import DRAFT, _design, _ids

DESIGN = dict(_design(), **{
    "wps:map_proj": "lambert",
    "wps:truelat1": 1.0, "wps:truelat2": 1.0, "wps:stand_lon": 103.8,  # projection params present
    "input:run_hours": "192",
})


def _spec(design):
    return {"wrf_version": "4.6.1", "forcing": "era5", "design": {k: {"value": v} for k, v in design.items()}}


class TestEmit(unittest.TestCase):
    def setUp(self):
        self.out = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.out, ignore_errors=True)

    def test_refuses_on_fail(self):
        with self.assertRaises(emit.EmitError):
            emit.emit(_spec(dict(DESIGN, **{"wps:parent_grid_ratio": "1,2,3"})), self.out)

    def test_complete_and_deterministic(self):
        m1 = emit.emit(_spec(DESIGN), self.out)
        self.assertEqual(m1["verdict"], "COMPLETE", (m1["missing_required"], m1["unplaced_spec_keys"]))
        a = (self.out / "namelist.input").read_text()
        self.assertIn("sf_urban_physics = 3, 3, 3,", a)
        self.assertIn("input:sf_urban_physics", m1["value_sources"])
        self.assertIn("time_step = 45,", a)                      # derived by the compiler
        self.assertIn("interval_seconds = 3600,", a)             # ERA5, not the 21600 template default
        self.assertIn("num_metgrid_levels = 38,", a)             # deferred default, flagged
        self.assertIn("input:num_metgrid_levels", m1["deferred_defaults"])
        w = (self.out / "namelist.wps").read_text()
        self.assertIn("&share", w) and self.assertIn("&geogrid", w)
        self.assertIn("e_we = 100, 121, 151,", w)
        m2 = emit.emit(_spec(DESIGN), self.out)
        self.assertEqual(m1["files"], m2["files"])                # deterministic bytes
        self.assertNotIn("generated_at", m1["files"])

    def test_missing_projection_param_is_not_guessed(self):
        design = {k: v for k, v in DESIGN.items() if k != "wps:stand_lon"}
        m = emit.emit(_spec(design), self.out)
        self.assertEqual(m["verdict"], "INCOMPLETE")
        self.assertIn("wps:stand_lon", m["missing_required"])
        self.assertIn("! MISSING: stand_lon", (self.out / "namelist.wps").read_text())

    def test_unplaced_spec_key_reported(self):
        design = dict(DESIGN)
        design["input:not_a_field"] = {"value": 1}
        m = emit.emit(_spec(design), self.out)
        self.assertIn("input:not_a_field", m["unplaced_spec_keys"])

    def test_sources_are_recorded(self):
        m = emit.emit(_spec(DESIGN), self.out)
        self.assertEqual(m["value_sources"]["input:sf_urban_physics"], "spec/derived")
        self.assertEqual(m["value_sources"]["input:io_form_history"], "template-default")


if __name__ == "__main__":
    unittest.main()
