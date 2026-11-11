# SPDX-License-Identifier: LGPL-2.1-or-later
# PartDesign primitives, helix, MultiTransform, datums, binders and booleans.
import math
import unittest

from _pd_base import HAVE, Base


@unittest.skipUnless(HAVE, "FreeCAD with PartDesign and Sketcher is not importable here")
class FeatureMore(Base):
    def test_1_primitives_and_multitransform(self):
        self.ok("pd_body_new", {"name": "Block"})
        self.assertEqual(self.ok("pd_primitive", {"body": "Block", "kind": "box", "size": {"length": 40, "width": 30, "height": 10}})["volume"], 12000.0)
        self.ok("pd_primitive", {"body": "Block", "kind": "cylinder", "size": {"radius": 3, "height": 20}, "at": [10, 10, -5], "subtractive": True, "name": "Bore"})
        m = self.ok("pd_multitransform", {"body": "Block", "features": ["Bore"], "steps": [{"kind": "linear", "direction": "x", "occurrences": 2, "spacing": 20}, {"kind": "linear", "direction": "y", "occurrences": 2, "spacing": 10}]})
        self.assertAlmostEqual(m["volume"], 12000 - 4 * math.pi * 9 * 10, delta=0.5)
        self.refused("pd_primitive", {"body": "Block", "kind": "pyramid"})

    def test_2_datums(self):
        d = self.ok("pd_datum", {"body": "Block", "kind": "line", "on": {"near": [13, 10, 10]}})
        self.assertEqual(d["at"][:2], [10.0, 10.0])
        self.ok("pd_primitive", {"body": "Block", "kind": "prism", "size": {"sides": 6, "circumradius": 2, "height": 4}, "at": [5, 25, 10], "name": "Knob"})
        p = self.ok("pd_polar_pattern", {"body": "Block", "features": ["Knob"], "axis": d["datum"], "occurrences": 3, "angle": 360})
        self.assertGreater(p["volumeChange"], 0)

    def test_3_helix(self):
        self.ok("pd_body_new", {"name": "Spring"})
        self.ok("sketch_new", {"body": "Spring", "plane": "XZ", "name": "Wire"})
        self.ok("sketch_add", {"sketch": "Wire", "items": [{"kind": "circle", "center": [10, 0], "radius": 1}]})
        h = self.ok("pd_helix", {"body": "Spring", "profile": "Wire", "pitch": 4, "height": 20})
        self.assertEqual(h["helix"]["turns"], 5.0)
        self.refused("pd_helix", {"body": "Spring", "profile": "Wire", "pitch": 4})

    def test_4_binder_boolean(self):
        self.ok("pd_body_new", {"name": "Cap"})
        self.ok("pd_binder", {"body": "Cap", "from": "Block", "faces": [{"at": [20, 2, 10], "normal": [0, 0, 1]}]})
        self.ok("pd_body_new", {"name": "Cutter"})
        self.ok("pd_primitive", {"body": "Cutter", "kind": "cylinder", "size": {"radius": 6, "height": 20}, "at": [20, 15, 5]})
        self.ok("pd_body_new", {"name": "Sheet"})
        self.ok("pd_primitive", {"body": "Sheet", "kind": "box", "size": {"length": 50, "width": 50, "height": 3}, "at": [0, 0, 8]})
        c = self.ok("pd_boolean", {"body": "Sheet", "op": "cut", "with": ["Cutter"]})
        self.assertAlmostEqual(c["volumeChange"], -math.pi * 36 * 3, delta=0.5)


if __name__ == "__main__":
    unittest.main()
