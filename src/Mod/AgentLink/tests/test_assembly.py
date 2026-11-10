# SPDX-License-Identifier: LGPL-2.1-or-later
# Assemblies with FreeCAD's Assembly workbench, on a real FreeCAD: parts
# put together, a joint solved, overlaps found.
import json
import os
import unittest

from _fc import HAVE, add_body, caller, server

try:
    import JointObject  # noqa: F401

    ASM = HAVE
except Exception:
    ASM = False


@unittest.skipUnless(ASM, "FreeCAD with the Assembly workbench is not importable here")
class Assembly(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import FreeCAD
        import Part

        cls.session, srv = server()
        cls.call = staticmethod(caller(srv))
        FreeCAD.newDocument("Asm")
        cls.session.switch(FreeCAD.ActiveDocument)
        add_body(cls.session, "Base", Part.makeBox(40, 30, 10))
        # The lid starts off to the side, overlapping nothing.
        lid = Part.makeBox(40, 30, 4)
        lid.translate(FreeCAD.Vector(60, 10, 25))
        add_body(cls.session, "Lid", lid)

    def test_1_refusals(self):
        err, r = self.call("asm_joint", {"kind": "fixed", "a": {"part": "Base"}, "b": {"part": "Lid"}})
        self.assertTrue(err)
        self.assertEqual(r["error"]["code"], "not_found")

    def test_2_stack_with_a_joint(self):
        err, n = self.call("asm_new", {"parts": ["Base", "Lid"], "name": "Box"})
        self.assertFalse(err, n)
        self.assertEqual(n["grounded"], ["Base"])
        err, r = self.call("asm_joint", {"kind": "fixed", "a": {"part": "Base", "element": {"kind": "face", "normal": [0, 0, 1]}}, "b": {"part": "Lid", "element": {"kind": "face", "normal": [0, 0, -1]}}})
        print("\njoint:", json.dumps(r))
        self.assertFalse(err, r)
        self.assertEqual(r["solve"], "solved")
        self.assertIn("Lid", r["moved"])
        lid = self.session.body("Lid").Shape.BoundBox
        base = self.session.body("Base").Shape.BoundBox
        # On the base's top face, face to face, not into it.
        self.assertAlmostEqual(lid.ZMin, 10, delta=1e-3)
        self.assertAlmostEqual(lid.XLength, 40, delta=1e-3)
        self.assertAlmostEqual(base.ZMax, 10, delta=1e-6)
        err, c = self.call("asm_check", {"clearance": 0.5})
        self.assertFalse(err, c)
        self.assertEqual(c["overlaps"], [])
        err, ls = self.call("asm_list")
        self.assertEqual([j["kind"] for j in ls["joints"]], ["fixed"])
        out = os.environ.get("AGENTLINK_RENDERS")
        if out:
            self.call("view_render", {"views": ["iso", "front"], "inline": False, "path": os.path.join(out, "assembly_stacked.png")})

    def test_3_overlap_found(self):
        import FreeCAD

        lid = self.session.body("Lid")
        keep = lid.Placement.copy()
        lid.Placement = FreeCAD.Placement(lid.Placement.Base + FreeCAD.Vector(0, 0, -3), lid.Placement.Rotation)
        self.session.doc.recompute()
        # Free the lid from its joint so the solve does not put it back.
        err, ls = self.call("asm_list")
        for j in ls["joints"]:
            self.call("asm_joint_remove", {"joint": j["joint"]})
        lid.Placement = FreeCAD.Placement(keep.Base + FreeCAD.Vector(0, 0, -3), keep.Rotation)
        self.session.doc.recompute()
        err, c = self.call("asm_check")
        self.assertFalse(err, c)
        self.assertEqual(c["verdict"], "fix")
        self.assertEqual(sorted(c["overlaps"][0]["parts"]), ["Base", "Lid"])
        self.assertAlmostEqual(c["overlaps"][0]["volume"], 40 * 30 * 3, delta=1)

    def test_4_lid_on_its_box(self):
        """A project box's lid, printed top down beside it, joined face to
        face on the rim: turned over so its lip goes into the box."""
        import FreeCAD

        FreeCAD.newDocument("LidOn")
        self.session.switch(FreeCAD.ActiveDocument)
        err, g = self.call("gen_part", {"generator": "project_box", "params": {"length": 80, "width": 60, "height": 30, "bosses": True}, "name": "Box"})
        self.assertFalse(err, g)
        self.call("asm_new", {"parts": ["Box", "Box lid"]})
        err, r = self.call("asm_joint", {"kind": "fixed", "a": {"part": "Box", "element": {"kind": "face", "normal": [0, 0, 1], "near": [0, 29, 30]}}, "b": {"part": "Box lid", "element": {"kind": "face", "normal": [0, 0, 1], "near": [90, 29.5, 2]}}})
        print("\nlid:", json.dumps(r))
        self.assertFalse(err, r)
        self.assertNotIn("overlaps", r)
        self.assertIn("flipped", r)
        lid = self.session.body("Box lid").Shape.BoundBox
        self.assertAlmostEqual(lid.ZMax, 32, delta=0.01)
        self.assertAlmostEqual(lid.ZMin, 27, delta=0.01)
        err, c = self.call("asm_check", {})
        self.assertEqual(c["verdict"], "clear", c)


if __name__ == "__main__":
    unittest.main()
