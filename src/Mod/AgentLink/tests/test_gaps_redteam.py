# SPDX-License-Identifier: LGPL-2.1-or-later
# The gap tools as a red team tried to break them: rotation given flat, sketch
# and clone scaling, undo on part tools, reorder past the tip, empty filters,
# linearizing off the part, helix growth, sewing tolerance.
import math
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from _fc import HAVE, add_body, caller, server  # noqa: E402

class Guard(unittest.TestCase):
    """M6: runs without FreeCAD — the path guard must treat writers as writers."""

    def test_cam_template_export_is_a_writer(self):
        from agentlink import paths

        self.assertIn("cam_template_export", paths.WRITERS)

@unittest.skipUnless(HAVE, "FreeCAD is not importable here")
class GapsRedTeam(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session, srv = server()
        cls.call = staticmethod(caller(srv))

    def ok(self, n, a=None):
        err, out = self.call(n, a)
        self.assertFalse(err, f"{n}: {out}")
        return out

    def refused(self, n, a=None):
        err, out = self.call(n, a)
        self.assertTrue(err, f"{n} should refuse: {out}")
        return out

    def obj(self, label):
        return self.session.doc.getObjectsByLabel(label)[0]

    # H1 ----------------------------------------------------------------------
    def test_h1_link_rotation_is_applied(self):
        import Part

        add_body(self.session, "H1 box", Part.makeBox(10, 20, 5))
        lk = self.ok("link_make", {"to": "H1 box", "at": [0, 0, 0], "axis": [0, 0, 1], "angle": 90})
        rot = self.obj(lk["link"]).Placement.Rotation
        self.assertAlmostEqual(math.degrees(rot.Angle), 90.0, places=3)

    def test_h1_rotation_alone_is_applied(self):
        import Part

        add_body(self.session, "H1 b2", Part.makeBox(10, 20, 5))
        p = self.ok("part_container", {"objects": ["H1 b2"], "axis": [0, 0, 1], "angle": 45, "name": "H1 part"})
        self.assertAlmostEqual(math.degrees(self.obj(p["part"]).Placement.Rotation.Angle), 45.0, places=3)

    # H2 ----------------------------------------------------------------------
    def test_h2_sketch_scale_doubles(self):
        self.ok("pd_body_new", {"name": "H2 body"})
        self.ok("sketch_new", {"body": "H2 body", "plane": "XY", "name": "H2 sk"})
        self.ok("sketch_add", {"sketch": "H2 sk", "items": [{"kind": "circle", "center": [10, 0], "radius": 5}]})
        self.ok("sketch_copy", {"sketch": "H2 sk", "geometry": [0], "scale": {"center": [0, 0], "factor": 2}})
        g = self.obj("H2 sk").Geometry[-1]
        self.assertAlmostEqual(g.Radius, 10.0, places=4)
        self.assertAlmostEqual(g.Center.x, 20.0, places=4)
        self.assertAlmostEqual(g.Center.y, 0.0, places=4)

    # H3 ----------------------------------------------------------------------
    def test_h3_draft_clone_scales_not_moves(self):
        import Part

        add_body(self.session, "H3 cube", Part.makeBox(10, 10, 10))
        c = self.ok("draft_clone", {"object": "H3 cube", "scale": 2})
        o = self.obj(c["object"])
        self.assertAlmostEqual(o.Shape.Volume, 8000.0, delta=1)
        self.assertAlmostEqual(o.Placement.Base.Length, 0.0, places=6)

    # H4 ----------------------------------------------------------------------
    def test_h4_failed_section_leaves_nothing(self):
        import FreeCAD
        import Part

        add_body(self.session, "H4 a", Part.makeBox(5, 5, 5))
        add_body(self.session, "H4 b", Part.makeBox(5, 5, 5, FreeCAD.Vector(100, 0, 0)))
        n0 = len(self.session.doc.Objects)
        self.refused("part_section", {"body": "H4 a", "with": "H4 b"})
        self.assertEqual(len(self.session.doc.Objects), n0, "the Part::Section was left behind")

    def test_h4_part_more_tools_are_undoable(self):
        from agentlink.families import part_more

        for t in part_more.tools(self.session):
            self.assertTrue(t.changes, f"{t.name} changes the document but is not a transaction")

    # M1 ----------------------------------------------------------------------
    def test_m1_reorder_after_tip_keeps_feature(self):
        self.ok("pd_body_new", {"name": "M1 body"})
        self.ok("pd_primitive", {"body": "M1 body", "kind": "box", "size": {"length": 40, "width": 30, "height": 10}})
        self.ok("pd_primitive", {"body": "M1 body", "kind": "cylinder", "size": {"radius": 3, "height": 20}, "at": [10, 10, -5], "subtractive": True, "name": "M1 bore"})
        v0 = self.obj("M1 body").Shape.Volume
        self.ok("pd_primitive", {"body": "M1 body", "kind": "box", "size": {"length": 5, "width": 5, "height": 5}, "at": [40, 0, 0], "name": "M1 tab"})
        v1 = self.obj("M1 body").Shape.Volume
        self.assertGreater(v1, v0)
        err, out = self.call("pd_reorder", {"feature": "M1 bore", "after": "M1 tab"})
        if not err:
            # either refused, or the bore is still in the body's shape
            self.assertAlmostEqual(self.obj("M1 body").Shape.Volume, v1, delta=0.5)

    def test_m1_move_feature_refuses_broken(self):
        self.ok("pd_body_new", {"name": "M1 src"})
        self.ok("pd_primitive", {"body": "M1 src", "kind": "box"})
        self.ok("pd_primitive", {"body": "M1 src", "kind": "cylinder", "size": {"radius": 2, "height": 20}, "at": [5, 5, -5], "subtractive": True, "name": "M1 hole"})
        self.ok("pd_body_new", {"name": "M1 empty"})
        err, out = self.call("pd_move_feature", {"feature": "M1 hole", "to": "M1 empty"})
        self.assertTrue(err or "broken" not in out, f"committed a broken body: {out}")

    # M3 ----------------------------------------------------------------------
    def test_m3_empty_filter_is_refused(self):
        import FreeCAD
        import Part

        add_body(self.session, "M3 comp", Part.makeCompound([Part.makeBox(5, 5, 5), Part.makeBox(5, 5, 5, FreeCAD.Vector(10, 0, 0))]))
        self.refused("compound_filter", {"compound": "M3 comp", "keep": "collision-pass"})  # no stencil

    # M7 ----------------------------------------------------------------------
    def test_m7_linearize_off_the_part_is_refused(self):
        try:
            import Fem
            import ObjectsFem
        except ImportError:
            self.skipTest("no FEM")
        fm = Fem.FemMesh()
        ids, vm = [], []
        n = 1
        for z in range(0, 11):
            fm.addNode(0, 0, z, n)
            ids.append(n)
            vm.append(10 + 4 * z)
            n += 1
        mo = self.session.doc.addObject("Fem::FemMeshObject", "M7mesh")
        mo.FemMesh = fm
        res = ObjectsFem.makeResultMechanical(self.session.doc, "M7result")
        res.Mesh, res.NodeNumbers, res.vonMises = mo, ids, vm
        self.refused("fem_linearize", {"result": "M7result", "from": [500, 500, 500], "to": [510, 500, 500]})

    # M9 ----------------------------------------------------------------------
    def test_m9_helix_growth_used_or_refused(self):
        self.ok("pd_body_new", {"name": "M9 spring"})
        self.ok("sketch_new", {"body": "M9 spring", "plane": "XZ", "name": "M9 wire"})
        self.ok("sketch_add", {"sketch": "M9 wire", "items": [{"kind": "circle", "center": [10, 0], "radius": 1}]})
        err, out = self.call("pd_helix", {"body": "M9 spring", "profile": "M9 wire", "height": 20, "turns": 5, "growth": 2})
        if not err:
            h = next(o for o in self.obj("M9 spring").Group if o.TypeId == "PartDesign::AdditiveHelix")
            self.assertEqual(h.Mode, "height-turns-growth")

    # M10 ---------------------------------------------------------------------
    def test_m10_sew_uses_tolerance(self):
        import FreeCAD
        import Part

        box = Part.makeBox(10, 10, 10)
        faces = [f.copy() for f in box.Faces]
        faces[0].translate(FreeCAD.Vector(-0.05, 0, 0))  # a 0.05 mm gap
        add_body(self.session, "M10 faces", Part.makeCompound(faces))
        r = self.ok("part_extract", {"refs": ["M10 faces"], "build": "solid", "tolerance": 0.1, "name": "M10 solid"})
        self.assertEqual(r["solids"], 1)

if __name__ == "__main__":
    unittest.main()
