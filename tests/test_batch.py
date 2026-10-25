"""Batch backend: design-matrix expansion with constructional axis guarantees."""

import os
import shutil
import tempfile
import unittest
from pathlib import Path

import yaml

from src.userguide import batch, dashboard, emit
from tests.test_dashboard import DRAFT, PKG, _design

# a real 3-class URBPARM.TBL from a production run, if the machine has one (path given by the environment)
MOSCOW_TBL = Path(os.environ.get("WRFAGENT_REAL_URBPARM", "/nonexistent/URBPARM.TBL"))

TBL = """# synthetic 3-class table
Number of urban categories: 3
ALBR: 0.20, 0.20, 0.20
ALBB: 0.20, 0.20, 0.20
ALBG: 0.20, 0.20, 0.20
HIURT: 0.4
"""


class TestBatch(unittest.TestCase):
    def setUp(self):
        self.case = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.case, ignore_errors=True)
        dashboard.init_case(self.case, "test", DRAFT)
        d = _design()
        dashboard.submit(self.case, d, PKG / "field_map.yaml")
        spec = {"wrf_version": "4.6.1", "forcing": "era5",
                "design": {k: {"value": v} for k, v in d.items()}}
        rep = emit.emit(spec, self.case / "build")
        self.assertEqual(rep["verdict"], "COMPLETE", rep["missing_required"])
        (self.case / "batch").mkdir(exist_ok=True)
        (self.case / "batch" / "URBPARM.TBL").write_text(TBL, encoding="utf-8")

    def _batch(self, axes, extra=""):
        (self.case / "batch.yaml").write_text(
            f"base: experiment.yaml\nurbparm_base: batch/URBPARM.TBL\naxes:\n{axes}\n{extra}", encoding="utf-8")
        return batch.expand(self.case)

    def test_c01_shape_3x3x2(self):
        rep = self._batch(
            "  albr: {target: \"urbparm:ALBR\", values: [0.12, 0.20, 0.60]}\n"
            "  albb: {target: \"urbparm:ALBB\", values: [0.12, 0.20, 0.60]}\n"
            "  pbl:  {target: \"input:bl_pbl_physics\", values: [2, 8],\n"
            "        companions: {\"input:sf_sfclay_physics\": {2: 2, 8: 1}}}")  # MYJ / BouLac, surface layer coupled
        self.assertTrue(rep["ok"], rep.get("error"))
        self.assertEqual(len(rep["members"]), 18)                      # the C01 matrix
        mids = {m["member"] for m in rep["members"]}
        self.assertIn("albb0p6_albr0p2_pbl2", mids)  # sorted axis order in the member id
        for m in rep["members"]:
            self.assertEqual(m["emit"], "COMPLETE", m)
            self.assertIn("urbparm_sha256", m)
        # axis guarantee executed over all 153 pairs without raising
        self.assertIn("differ only on declared", rep["guarantee"])
        # a member's spec carries the axis provenance
        spec = yaml.safe_load((self.case / "members" / "albb0p2_albr0p2_pbl8" / "experiment.yaml").read_text())
        self.assertEqual(spec["design"]["input:bl_pbl_physics"]["value"], 8)
        self.assertEqual(spec["design"]["input:sf_sfclay_physics"]["value"], 1)  # coupled by the axis
        self.assertIn("batch axis", spec["design"]["input:bl_pbl_physics"]["reason"])

    def test_urbparm_only_declared_keys_change(self):
        rep = self._batch("  albr: {target: \"urbparm:ALBR\", values: [0.30]}\n")
        self.assertTrue(rep["ok"])
        tbl = (self.case / "members" / "albr0p3" / "build" / "URBPARM.TBL").read_text()
        self.assertRegex(tbl, r"ALBR:\s+0\.3, 0\.3, 0\.3")
        self.assertIn("ALBB: 0.20, 0.20, 0.20", tbl)      # untouched

    def test_missing_urbparm_key_rejected(self):
        rep = self._batch("  fake: {target: \"urbparm:NOPE\", values: [1]}\n")
        self.assertFalse(rep["ok"])

    def test_no_axes_rejected(self):
        (self.case / "batch.yaml").write_text("base: experiment.yaml\naxes: {}\n", encoding="utf-8")
        self.assertFalse(batch.expand(self.case)["ok"])


@unittest.skipUnless(MOSCOW_TBL.exists(), "real URBPARM.TBL not present")
class TestBatchRealTable(unittest.TestCase):
    def test_real_table_3class(self):
        case = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, case, ignore_errors=True)
        shutil.copy(MOSCOW_TBL, case / "URBPARM.TBL")
        tbl = batch._set_urbparm((case / "URBPARM.TBL").read_text(), "ALBR", 0.6)
        self.assertIn("ALBR: 0.6, 0.6, 0.6", tbl)


if __name__ == "__main__":
    unittest.main()
