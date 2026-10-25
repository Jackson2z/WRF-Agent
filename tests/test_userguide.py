"""userguide: upstream docs -> parameter registry -> field_map/dictionary -> HTML form."""

import re
import unittest
from pathlib import Path

import yaml

from src.userguide import parse, render

HERE = Path(__file__).resolve().parent
PKG = HERE.parent / "src" / "userguide"
SNAP = HERE.parent / "references" / "upstream" / "wrf-4.6.1"


@unittest.skipUnless((SNAP / "Registry" / "Registry.EM").exists(), "upstream snapshot not fetched")
class TestUserGuide(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reg = parse.build(SNAP)
        cls.fm = yaml.safe_load((PKG / "field_map.yaml").read_text(encoding="utf-8"))
        cls.dic = yaml.safe_load((PKG / "dictionary.yaml").read_text(encoding="utf-8"))
        cls.merged = render.merge(cls.reg, cls.fm, cls.dic)
        cls.by_id = {f["id"]: f for f in cls.merged["fields"]}

    def test_registry_is_full_set(self):
        # Registry.EM (with includes) defines >1000 namelist.input fields in 4.6.1
        self.assertGreater(self.reg["stats"]["namelist_input_fields"], 1000)
        self.assertGreater(self.reg["stats"]["namelist_wps_fields"], 40)

    def test_registry_metadata(self):
        f = self.by_id["input:sf_urban_physics"]
        self.assertEqual(f["block"], "physics")
        self.assertTrue(f["per_domain"])
        self.assertEqual({o["value"] for o in f["options"]}, {"0", "1", "2", "3"})
        if (SNAP / "users_guide" / "namelist_variables.html").exists():   # the Guide HTML is fetched, not shipped
            self.assertIn("namelist_variables.html#physics", f["ug_url"])

    def test_registry_derived_not_namelist(self):
        # README.namelist lists num_urban_ndm, but in 4.6.1 Registry it is derived -> not a namelist field
        self.assertNotIn("input:num_urban_ndm", self.by_id)

    def test_mapping_consistent(self):
        self.assertEqual(self.merged["problems"], [])

    def test_every_design_field_has_question_and_group(self):
        for f in self.merged["fields"]:
            if f["class"] == "design":
                self.assertTrue(f["question"], f["id"])
                self.assertTrue(f["group"], f["id"])

    def test_html_english_only_and_selfcontained(self):
        page = render.render_html(self.merged)
        self.assertIsNone(re.search(r"[\u4e00-\u9fff]", page))
        self.assertNotIn("<script src", page)
        self.assertNotIn('<link rel="stylesheet"', page)


if __name__ == "__main__":
    unittest.main()
