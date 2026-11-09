# SPDX-License-Identifier: LGPL-2.1-or-later
# The PartDesign feature tools on a real FreeCAD: skipped where it cannot be imported.
import math
import unittest

from _pd_base import HAVE, Base


@unittest.skipUnless(HAVE, "FreeCAD with PartDesign and Sketcher is not importable here")
class FeatureTools(Base):
    def test_1_pad(self):
        self.ok("pd_body_new", {"name": "Block"})
        self.ok("sketch_new", {"plane": "XY", "name": "Base"})
        self.ok("sketch_add", {"sketch": "Base", "items": [{"kind": "rectangle", "center": [0, 0], "size": [40, 20]}]})
        p = self.ok("pd_pad", {"sketch": "Base", "length": 10})
        self.assertAlmostEqual(p["volume"], 8000, delta=0.5)
        self.assertEqual(p["box"], [40, 20, 10])
        self.assertTrue(p["valid"])

    def test_2_pocket_on_a_face_found_by_query(self):
        sk = self.ok("sketch_new", {"face": {"at": [5, 5, 10], "normal": [0, 0, 1]}, "name": "Top"})
        self.assertEqual(sk["frame"]["origin"], [0, 0, 10])
        self.assertEqual(sk["frame"]["xAxis"], [1, 0, 0])
        self.ok("sketch_add", {"sketch": "Top", "items": [{"kind": "circle", "center": [10, 0], "radius": 3}]})
        p = self.ok("pd_pocket", {"sketch": "Top", "type": "through_all"})
        self.assertAlmostEqual(p["volumeChange"], -math.pi * 9 * 10, delta=0.5)

    def test_3_pocket_that_cuts_nothing_is_refused(self):
        n = len(self.ok("feature_list")["features"])
        self.ok("sketch_new", {"face": {"at": [-5, -5, 10], "normal": [0, 0, 1]}, "name": "Away"})
        self.ok("sketch_add", {"sketch": "Away", "items": [{"kind": "circle", "center": [-10, 0], "radius": 2}]})
        self.refused("pd_pocket", {"sketch": "Away", "length": 5, "reversed": True})
        names = [f["name"] for f in self.ok("feature_list")["features"]]
        self.assertEqual(len(names), n + 1, names)  # the sketch stays, the pocket does not
        self.assertFalse([x for x in names if x.startswith("Pocket") and x != "Pocket"], names)

    def test_4_fillet_by_a_point_near_the_edge(self):
        before = self.ok("feature_list")["volume"]
        f = self.ok("pd_fillet", {"edges": [{"near": [20, 10, 5]}], "radius": 2})
        self.assertAlmostEqual(f["volumeChange"], -(4 - math.pi) * 10, delta=0.2)
        self.assertLess(f["volume"], before)
        self.refused("pd_fillet", {"edges": [{"near": [100, 100, 100]}], "radius": 1})

    def test_5_threaded_hole_and_pattern(self):
        self.ok("sketch_new", {"face": {"at": [0, 0, 10], "normal": [0, 0, 1]}, "name": "Holes"})
        self.ok("sketch_add", {"sketch": "Holes", "items": [{"kind": "circle", "center": [-10, 0], "radius": 2}]})
        h = self.ok("pd_hole", {"sketch": "Holes", "thread": "M5", "depth": "through"})
        self.assertLess(h["volumeChange"], -100)
        self.assertIn("M5", h["thread"])
        p = self.ok("pd_linear_pattern", {"features": ["Pocket"], "direction": "y", "occurrences": 2, "spacing": 6})
        self.assertLess(p["volumeChange"], -50)

    def test_6_edit_suppress_remove(self):
        e = self.ok("feature_edit", {"feature": "Pad", "set": {"Length": 12}})
        self.assertEqual(e["box"][2], 12)
        self.refused("feature_edit", {"feature": "Pad", "set": {"Nonsense": 1}})
        listing = self.ok("feature_list")
        pad = next(f for f in listing["features"] if f["name"] == "Pad")
        self.assertEqual(pad["length"], 12)
        err, s = self.call("feature_suppress", {"feature": "Fillet"})
        if err:
            self.assertEqual(s["error"]["code"], "unavailable")
        else:
            self.assertGreater(s["volumeChange"], 0)
            self.ok("feature_suppress", {"feature": "Fillet", "suppressed": False})
        r = self.ok("feature_remove", {"feature": "LinearPattern"})
        self.assertGreater(r["volumeChange"], 0)

    def test_7_shell(self):
        self.ok("pd_body_new", {"name": "Tray"})
        self.ok("sketch_new", {"body": "Tray", "plane": "XY", "name": "TrayBase"})
        self.ok("sketch_add", {"sketch": "TrayBase", "items": [{"kind": "rectangle", "center": [0, 0], "size": [40, 20]}]})
        self.ok("pd_pad", {"body": "Tray", "sketch": "TrayBase", "length": 10})
        t = self.ok("pd_thickness", {"body": "Tray", "faces": [{"at": [0, 0, 10], "normal": [0, 0, 1]}], "thickness": 2})
        self.assertAlmostEqual(t["volume"], 8000 - 36 * 16 * 8, delta=1)

    def test_8_revolution_and_loft(self):
        self.ok("pd_body_new", {"name": "Ring"})
        self.ok("sketch_new", {"body": "Ring", "plane": "XZ", "name": "RingProfile"})
        self.ok("sketch_add", {"sketch": "RingProfile", "items": [{"kind": "rectangle", "corner": [5, 0], "size": [5, 10]}]})
        r = self.ok("pd_revolution", {"body": "Ring", "sketch": "RingProfile", "axis": "V_Axis"})
        self.assertAlmostEqual(r["volume"], math.pi * (100 - 25) * 10, delta=1)

        self.ok("pd_body_new", {"name": "Frustum"})
        self.ok("sketch_new", {"body": "Frustum", "plane": "XY", "name": "Bottom"})
        self.ok("sketch_add", {"sketch": "Bottom", "items": [{"kind": "rectangle", "center": [0, 0], "size": [20, 20]}]})
        d = self.ok("pd_datum_plane", {"body": "Frustum", "on": "XY", "offset": 20, "name": "Up"})
        self.assertEqual(d["origin"], [0, 0, 20])
        self.ok("sketch_new", {"body": "Frustum", "plane": "Up", "name": "TopSec"})
        self.ok("sketch_add", {"sketch": "TopSec", "items": [{"kind": "rectangle", "center": [0, 0], "size": [10, 10]}]})
        lo = self.ok("pd_loft", {"body": "Frustum", "profile": "Bottom", "sections": ["TopSec"], "ruled": True})
        self.assertAlmostEqual(lo["volume"], 20 / 3 * (400 + 100 + 200), delta=1)


if __name__ == "__main__":
    unittest.main()
