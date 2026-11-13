# SPDX-License-Identifier: LGPL-2.1-or-later
# The Sketcher commands that were window-only: arc slots, chamfers, group
# constraints (FreeCAD 1.2), text, internal geometry and axes alignment.
import math
import unittest

from _pd_base import HAVE, Base


@unittest.skipUnless(HAVE, "FreeCAD with PartDesign and Sketcher is not importable here")
class SketchGaps(Base):
    def sketch(self, name):
        if not any(o.TypeId == "PartDesign::Body" for o in self.session.doc.Objects):
            self.ok("pd_body_new", {"name": "G body"})
        self.ok("sketch_new", {"plane": "XY", "name": name})

    def test_arc_slot_pads_to_its_area(self):
        self.sketch("G slot")
        self.ok("sketch_add", {"sketch": "G slot", "items": [{"kind": "arc_slot", "center": [0, 0], "radius": 20, "width": 6, "start": 0, "end": 90}]})
        st = self.ok("sketch_state", {"sketch": "G slot", "detail": False})
        self.assertTrue(st.get("readyForFeature", True), st)
        sk = self.session.doc.getObjectsByLabel("G slot")[0]
        import Part

        face = Part.Face(Part.Wire(Part.__sortEdges__(sk.Shape.Edges)))
        want = math.pi / 2 * 20 * 6 + math.pi * 9
        self.assertAlmostEqual(face.Area, want, delta=0.05)

    def test_chamfer_a_corner(self):
        self.sketch("G ch")
        self.ok("sketch_add", {"sketch": "G ch", "items": [{"kind": "polyline", "points": [[0, 0], [20, 0], [20, 10]]}]})
        r = self.ok("sketch_edit", {"sketch": "G ch", "fillet": {"at": [0, 2], "radius": 3, "chamfer": True}})
        self.assertIn("chamfer", r["done"][0])
        sk = self.session.doc.getObjectsByLabel("G ch")[0]
        self.assertEqual(type(sk.Geometry[-1]).__name__, "LineSegment")

    def test_axes_alignment_and_internal_geometry(self):
        self.sketch("G ax")
        self.ok("sketch_add", {"sketch": "G ax", "items": [{"kind": "rectangle", "center": [0, 0], "size": [10, 6]}, {"kind": "ellipse", "center": [30, 0], "major": 10, "minor": 4}]})
        sk = self.session.doc.getObjectsByLabel("G ax")[0]
        n_hv = sum(c.Type in ("Horizontal", "Vertical") for c in sk.Constraints)
        self.assertGreater(n_hv, 0)
        self.ok("sketch_edit", {"sketch": "G ax", "removeAxesAlignment": [0, 1, 2, 3]})
        self.assertLess(sum(c.Type in ("Horizontal", "Vertical") for c in sk.Constraints), n_hv)
        ell = next(i for i, g in enumerate(sk.Geometry) if type(g).__name__ == "Ellipse")
        internal = [i for i in range(len(sk.Geometry)) if i != ell and sk.getConstruction(i)]
        if internal:
            sk.delGeometries(internal)
        self.ok("sketch_edit", {"sketch": "G ax", "exposeInternal": [ell]})

    def test_group_and_text(self):
        self.sketch("G grp")
        self.ok("sketch_add", {"sketch": "G grp", "items": [{"kind": "circle", "center": [0, 0], "radius": 2}, {"kind": "circle", "center": [10, 0], "radius": 2}]})
        err, out = self.call("sketch_edit", {"sketch": "G grp", "group": [0, 1]})
        if err:
            self.assertEqual(out["error"]["code"], "unavailable")
        else:
            self.assertIn("grouped", out["done"][0])
        t = self.ok("sketch_edit", {"sketch": "G grp", "text": {"text": "AL", "at": [0, 10], "height": 5}})
        self.assertIn("text", t["done"][-1])
        sk = self.session.doc.getObjectsByLabel("G grp")[0]
        self.assertGreater(len(sk.Geometry), 4)


if __name__ == "__main__":
    unittest.main()
