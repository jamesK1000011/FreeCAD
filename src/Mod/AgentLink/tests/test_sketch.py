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

    def test_6_curves_offset_and_outside_geometry(self):
        self.ok("pd_body_new", {"name": "Tray"})
        self.ok("sketch_new", {"body": "Tray", "plane": "XY", "name": "TrayOut"})
        self.ok("sketch_add", {"sketch": "TrayOut", "items": [{"kind": "rectangle", "center": [0, 0], "size": [40, 30]}]})
        off = self.ok("sketch_offset", {"sketch": "TrayOut", "distance": 3})
        # Four sides and four round corners, joined into one more closed profile.
        self.assertEqual(len(off["added"]), 8, off)
        self.assertEqual(off["state"]["closedWires"], 2, off["state"])
        self.ok("sketch_new", {"body": "Tray", "plane": "XZ", "name": "Curves"})
        c = self.ok("sketch_add", {"sketch": "Curves", "items": [
            {"kind": "bspline", "points": [[0, 0], [10, 8], [20, 0], [30, 8]]},
            {"kind": "bspline", "points": [[0, 20], [10, 30], [20, 20], [10, 15]], "closed": True},
            {"kind": "ellipse", "center": [50, 10], "major": 20, "minor": 10, "rotation": 30},
        ]})
        self.assertEqual(c["state"]["solver"], "solved", c["state"])
        self.assertEqual(c["state"]["closedWires"], 2, c["state"])
        # A pad of the two profiles is a 3 mm frame; a sketch on its top with an inside edge brought in.
        self.ok("pd_pad", {"body": "Tray", "sketch": "TrayOut", "length": 5})
        self.ok("sketch_new", {"body": "Tray", "face": {"at": [21.5, 0, 5], "normal": [0, 0, 1]}, "name": "OnTop"})
        ext = self.ok("sketch_external", {"sketch": "OnTop", "edges": [{"type": "line", "near": [0, 15, 5]}]})
        self.assertEqual(ext["ids"], [-3], ext)
        self.refused("sketch_offset", {"sketch": "TrayOut", "distance": 0})


    def test_9_fillet_mirror_trim_split(self):
        # A 40 x 20 plate: one corner rounded 5 mm, a 3 mm hole mirrored
        # across the vertical axis, then padded 4 mm.
        import math

        self.ok("pd_body_new", {"name": "Edited"})
        self.ok("sketch_new", {"body": "Edited", "plane": "XY", "name": "EditMe"})
        self.ok("sketch_add", {"sketch": "EditMe", "items": [{"kind": "rectangle", "center": [0, 0], "size": [40, 20]}, {"kind": "circle", "center": [12, 4], "radius": 3}]})
        st = self.ok("sketch_state", {"sketch": "EditMe"})
        # The corner where line 0 ends.
        f = self.ok("sketch_edit", {"sketch": "EditMe", "fillet": {"at": [0, 2], "radius": 5}})
        self.assertIn("fillet r5", f["done"][0])
        m = self.ok("sketch_edit", {"sketch": "EditMe", "mirror": {"geos": [4], "about": "y"}})
        self.assertIn("mirrored", m["done"][0])
        p = self.ok("pd_pad", {"body": "Edited", "sketch": "EditMe", "length": 4})
        vol = next(o for o in self.session.doc.Objects if o.Label == "Edited").Shape.Volume
        want = (40 * 20 - (25 - math.pi * 25 / 4) - 2 * math.pi * 9) * 4
        self.assertAlmostEqual(vol, want, delta=want * 0.005)
        # Trim and split on a sketch of its own: a line across a circle.
        self.ok("sketch_new", {"body": "Edited", "plane": "XY", "name": "Cuts"})
        self.ok("sketch_add", {"sketch": "Cuts", "items": [{"kind": "circle", "center": [0, 0], "radius": 10}, {"kind": "line", "from": [-15, 0], "to": [15, 0]}]})
        t = self.ok("sketch_edit", {"sketch": "Cuts", "trim": {"geo": 1, "at": [-13, 0]}})
        self.assertIn("trimmed", t["done"][0])
        sp = self.ok("sketch_edit", {"sketch": "Cuts", "split": {"geo": 0, "at": [0, 10]}})
        self.assertIn("split", sp["done"][0])
        e = self.refused("sketch_edit", {"sketch": "Cuts", "fillet": {"at": [0, 1], "radius": 0}})
        self.assertIn("radius", e["message"])


if __name__ == "__main__":
    unittest.main()
