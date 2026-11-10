# SPDX-License-Identifier: LGPL-2.1-or-later
# Lettering and filled surfaces, on a real FreeCAD.
import json
import os
import unittest

from _fc import HAVE, add_body, caller, server


@unittest.skipUnless(HAVE, "FreeCAD is not importable here")
class Draft(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import FreeCAD
        import Part

        cls.session, srv = server()
        cls.call = staticmethod(caller(srv))
        FreeCAD.newDocument("Letters")
        cls.session.switch(FreeCAD.ActiveDocument)
        add_body(cls.session, "Tag", Part.makeBox(60, 30, 10))
        saddle = Part.makePolygon([FreeCAD.Vector(0, 0, 0), FreeCAD.Vector(20, 0, 5), FreeCAD.Vector(20, 20, 0), FreeCAD.Vector(0, 20, 5), FreeCAD.Vector(0, 0, 0)])
        o = cls.session.doc.addObject("Part::Feature", "Frame")
        o.Shape = saddle
        cls.session.doc.recompute()

    def test_1_fonts(self):
        err, f = self.call("draft_fonts")
        self.assertFalse(err, f)
        print("\nfonts:", f["found"], f["default"])
        self.assertGreater(f["found"], 0)

    def test_2_emboss_and_engrave(self):
        err, e = self.call("draft_text", {"body": "Tag", "text": "AL-7", "size": 8, "depth": 1})
        print("emboss:", json.dumps(e))
        self.assertFalse(err, e)
        self.assertGreater(e["volumeChange"], 5)
        bb = self.session.body("Tag").Shape.BoundBox
        self.assertAlmostEqual(bb.ZMax, 11, delta=1e-3)
        err, g = self.call("draft_text", {"body": "Tag", "text": "B", "size": 6, "depth": 0.5, "mode": "engrave", "face": {"normal": [0, -1, 0]}})
        self.assertFalse(err, g)
        self.assertLess(g["volumeChange"], -1)
        self.assertNotIn("warning", g)
        err, w = self.call("draft_text", {"body": "Tag", "text": "MUCH TOO LONG FOR THIS FACE", "size": 8, "depth": 0.4, "mode": "engrave", "face": {"normal": [1, 0, 0]}})
        self.assertFalse(err, w)
        self.assertIn("warning", w)
        out = os.environ.get("AGENTLINK_RENDERS")
        if out:
            self.call("view_render", {"objects": ["Tag"], "views": ["iso", "front"], "inline": False, "path": os.path.join(out, "lettering.png")})

    def test_3_surface_fill(self):
        err, f = self.call("surface_fill", {"edges": ["Frame"], "thickness": 1, "name": "Saddle"})
        print("surface:", json.dumps(f))
        self.assertFalse(err, f)
        self.assertTrue(f["solid"])
        self.assertGreater(f["area"], 400)

    def test_4_surface_loft_points_thicken(self):
        # A curved panel through three arcs of points: a sheet, then 2 mm thick.
        def arc(y, sag):
            return {"points": [[x, y, sag * (1 - ((x - 20) / 20) ** 2)] for x in (0, 10, 20, 30, 40)]}
        err, p = self.call("surface_loft", {"curves": [arc(0, 4), arc(15, 6), arc(30, 4)], "thickness": 2, "side": "both", "name": "Panel"})
        print("\nloft:", json.dumps(p))
        self.assertFalse(err, p)
        self.assertTrue(p["solid"])
        self.assertAlmostEqual(p["volume"], p["area"] * 2, delta=p["area"] * 2 * 0.05)
        self.assertNotIn("warnings", p)
        # Open and closed curves together are refused.
        err, x = self.call("surface_loft", {"curves": [arc(0, 4), {"points": [[0, 0, 0], [10, 0, 0], [10, 10, 0]], "closed": True}]})
        self.assertTrue(err)
        # Through a grid of points: every point on it; then thickened thin.
        rows = [[[x, y, 0.02 * (x - 15) ** 2 - 0.01 * (y - 10) ** 2] for x in range(0, 31, 5)] for y in range(0, 21, 5)]
        err, g = self.call("surface_points", {"rows": rows, "name": "Fitted"})
        print("points:", json.dumps(g))
        self.assertFalse(err, g)
        self.assertLess(g["farthestPoint"], 0.01)
        self.assertFalse(g["solid"])
        err, t = self.call("surface_thicken", {"surface": "Fitted", "thickness": 0.6})
        print("thicken:", json.dumps(t))
        self.assertFalse(err, t)
        self.assertTrue(t["solid"])
        self.assertTrue(any("two printed lines" in w for w in t["warnings"]), t)
        err, r = self.call("surface_points", {"rows": [[[0, 0, 0], [1, 0, 0]], [[0, 1, 0]]]})
        self.assertTrue(err)


if __name__ == "__main__":
    unittest.main()
