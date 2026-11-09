# SPDX-License-Identifier: LGPL-2.1-or-later
# The parameter tools on a real FreeCAD: skipped where it cannot be imported.
import unittest

from _pd_base import HAVE, Base


@unittest.skipUnless(HAVE, "FreeCAD with PartDesign and Sketcher is not importable here")
class ParamTools(Base):
    def test_1_bind_and_drive(self):
        self.ok("pd_body_new", {"name": "Plate"})
        self.ok("sketch_new", {"plane": "XY", "name": "Outline"})
        self.ok("sketch_add", {"sketch": "Outline", "items": [{"kind": "rectangle", "corner": [0, 0], "size": [40, 20], "dims": True, "name": "plate"}]})
        self.ok("pd_pad", {"sketch": "Outline", "length": 10})
        p = self.ok("param_set", {"name": "width", "value": 50})
        self.assertEqual(p["value"], 50)
        self.ok("param_set", {"name": "height", "value": 8, "note": "plate thickness"})
        b = self.ok("param_bind", {"target": "Outline.plate_width", "expression": f"{p['ref'].split('.')[0]}.width"})
        self.assertEqual(b["value"], 50)
        b = self.ok("param_bind", {"target": "Pad.Length", "expression": "Params.height"})
        self.assertAlmostEqual(b["bodies"][0]["volume"], 50 * 20 * 8, delta=0.5)
        s = self.ok("param_set", {"name": "width", "value": 30})
        self.assertAlmostEqual(s["bodies"][0]["volume"], 30 * 20 * 8, delta=0.5)
        self.assertIn("Pad.Length", self.ok("param_list")["params"][1]["usedBy"])

    def test_2_refusals(self):
        self.refused("param_remove", {"name": "width"})
        self.refused("param_set", {"name": "height", "value": 0})
        self.assertEqual(self.ok("param_list")["params"][1]["value"], 8)
        self.refused("feature_edit", {"feature": "Pad", "set": {"Length": 3}})
        self.refused("param_bind", {"target": "Pad.Length", "expression": "Params.nothere"})
        self.refused("param_set", {"name": "A1", "value": 1})

    def test_3_derived_and_remove(self):
        self.ok("param_set", {"name": "half", "value": "width / 2"})
        self.assertEqual(self.ok("param_list")["params"][2]["value"], 15)
        self.ok("param_remove", {"name": "half"})
        self.ok("param_bind", {"target": "Pad.Length", "expression": None})
        self.ok("param_remove", {"name": "height"})
        self.assertEqual([x["name"] for x in self.ok("param_list")["params"]], ["width"])


if __name__ == "__main__":
    unittest.main()
