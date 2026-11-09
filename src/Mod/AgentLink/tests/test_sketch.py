# SPDX-License-Identifier: LGPL-2.1-or-later
# The sketch tools on a real FreeCAD: skipped where it cannot be imported.
import unittest

from _pd_base import HAVE, Base


@unittest.skipUnless(HAVE, "FreeCAD with PartDesign and Sketcher is not importable here")
class SketchTools(Base):
    def test_1_rectangle_constrained_to_zero_dof(self):
        self.ok("pd_body_new", {"name": "Plate"})
        sk = self.ok("sketch_new", {"plane": "XY", "name": "Outline"})
        self.assertEqual(sk["frame"]["normal"], [0, 0, 1])
        add = self.ok("sketch_add", {"sketch": "Outline", "items": [{"kind": "rectangle", "center": [0, 0], "size": [40, 20], "dims": True, "name": "plate"}]})
        self.assertEqual(add["added"][0]["ids"], [0, 1, 2, 3])
        self.assertEqual(add["state"]["dof"], 2)
        self.assertTrue(add["state"]["readyForFeature"], add["state"])
        c = self.ok("sketch_constrain", {"sketch": "Outline", "constraints": [{"type": "symmetric", "refs": [[0, 1], [1, 2], "origin"]}]})
        self.assertEqual(c["state"]["dof"], 0)
        self.assertTrue(c["state"]["fullyConstrained"])

    def test_2_conflict_is_refused_and_nothing_added(self):
        before = len(self.ok("sketch_state", {"sketch": "Outline"})["constraints"])
        e = self.refused("sketch_constrain", {"sketch": "Outline", "constraints": [{"type": "distance_x", "refs": [0], "value": 50}]})
        self.assertIn("over-constrain", e["message"])
        self.assertEqual(len(self.ok("sketch_state", {"sketch": "Outline"})["constraints"]), before)

    def test_3_edit_a_named_dimension(self):
        self.ok("sketch_edit", {"sketch": "Outline", "set": {"constraint": "plate_width", "value": 50}})
        st = self.ok("sketch_state", {"sketch": "Outline"})
        line = st["geometry"][0]
        self.assertAlmostEqual(line["to"][0] - line["from"][0], 50, places=4)
        self.assertAlmostEqual(line["from"][0], -25, places=4)

    def test_4_shapes_close_and_solve(self):
        self.ok("sketch_new", {"plane": "XZ", "name": "Shapes"})
        add = self.ok("sketch_add", {"sketch": "Shapes", "items": [
            {"kind": "rounded_rectangle", "center": [0, 0], "size": [30, 20], "radius": 3},
            {"kind": "polygon", "center": [40, 0], "radius": 8, "sides": 6},
            {"kind": "slot", "from": [70, 0], "to": [90, 0], "radius": 4},
            {"kind": "line", "from": [0, -20], "to": [0, 20], "construction": True},
        ]})
        st = add["state"]
        self.assertEqual(st["solver"], "solved", st)
        self.assertEqual(st["closedWires"], 3, st)
        # rounded rectangle 5, polygon 4, slot 4, the construction line 4
        self.assertEqual(st["dof"], 17, st)
        self.assertNotIn("openVertices", st)

    def test_5_open_polyline_is_not_ready(self):
        self.ok("sketch_new", {"plane": "YZ", "name": "Open"})
        add = self.ok("sketch_add", {"sketch": "Open", "items": [{"kind": "polyline", "points": [[0, 0], [10, 0], [10, 10]]}]})
        self.assertFalse(add["state"]["readyForFeature"])
        self.assertIn("openVertices", add["state"])


if __name__ == "__main__":
    unittest.main()
