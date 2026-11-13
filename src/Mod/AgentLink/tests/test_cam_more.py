# SPDX-License-Identifier: LGPL-2.1-or-later
# CAM's remaining commands headless: area, path from a shape, compound,
# array, stop, fixture, property bag, template export, and the newer op
# kinds; Draft's dimension flip; Assembly's new part and rigid group.
import json
import os
import unittest

from _fc import HAVE, add_body, caller, server

try:
    import Path.Main.Job  # noqa: F401

    CAM = HAVE
except Exception:
    CAM = False


@unittest.skipUnless(CAM, "FreeCAD with the CAM workbench is not importable here")
class CamMore(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import FreeCAD
        import Part

        cls.session, srv = server()
        cls.call = staticmethod(caller(srv))
        FreeCAD.newDocument("CamMore")
        cls.session.switch(FreeCAD.ActiveDocument)
        block = Part.makeBox(60, 40, 20).cut(Part.makeBox(30, 20, 8, FreeCAD.Vector(15, 10, 12)))
        add_body(cls.session, "Plate", block)
        err, j = cls.call("cam_job", {"body": "Plate", "tool": {"diameter": 6, "feed": 800, "plunge": 250, "rpm": 15000}, "stock": {"margin": 3, "top": 1}})
        assert not err, j
        err, p = cls.call("cam_op", {"kind": "profile", "side": "outside", "name": "Outline"})
        assert not err, p

    def ok(self, name, args=None):
        err, out = self.call(name, args or {})
        self.assertFalse(err, f"{name}: {out}")
        print(f"\n{name}:", json.dumps(out)[:400])
        return out

    def maybe(self, name, args):
        err, out = self.call(name, args)
        if err and out["error"]["code"] == "unavailable":
            print(f"\n{name}: {out['error']['message']}")
            return None
        self.assertFalse(err, f"{name}: {out}")
        return out

    def test_1_paths(self):
        a = self.ok("cam_path", {"kind": "area", "sources": ["Plate"], "workplane": "Plate.Face6"})
        self.assertEqual(a["kind"], "area")
        st = self.ok("cam_path", {"kind": "stop", "mandatory": True})
        self.assertIn("M0", st["first"][0])
        fx = self.ok("cam_path", {"kind": "fixture", "fixture": "G55"})
        self.assertIn("G55", " ".join(fx["first"]))
        c = self.ok("cam_path", {"kind": "compound", "ops": ["Outline"]})
        self.assertGreater(c.get("commands", 0), 10)
        ar = self.ok("cam_path", {"kind": "array", "ops": ["Outline"], "props": {"Type": "Linear1D", "Copies": 1}})
        self.assertGreater(ar.get("commands", 0), 10)

    def test_2_bag_and_template(self):
        b = self.ok("cam_property_bag", {"properties": [{"name": "Clearance", "type": "Length", "value": 2.5}]})
        self.assertEqual(b["properties"], ["Clearance"])
        t = self.ok("cam_template_export", {"description": "plate setup"})
        with open(t["template"]) as f:
            data = json.load(f)
        self.assertTrue(os.path.basename(t["template"]).startswith("job_"))
        self.assertIn("ToolController", json.dumps(data))

    def test_3_newer_ops(self):
        floor = [{"normal": [0, 0, 1], "near": [30, 20, 12]}]
        for kind in ("pocket3d", "mill_facing", "probe", "custom"):
            args = {"kind": kind}
            if kind == "custom":
                args["props"] = {"Gcode": ["(hello)", "G0 Z10"]}
            if kind == "pocket3d":
                args["faces"] = floor
            err, out = self.call("cam_op", args)
            print(f"\ncam_op {kind}:", json.dumps(out)[:300])
            if err:
                self.assertEqual(out["error"]["code"], "unavailable", out)
            elif kind == "custom":
                self.assertIn("G0 Z10", " ".join(out["first"]))


@unittest.skipUnless(HAVE, "FreeCAD is not importable here")
class DraftAsmMore(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import FreeCAD

        cls.session, srv = server()
        cls.call = staticmethod(caller(srv))
        FreeCAD.newDocument("DraftAsmMore")
        cls.session.switch(FreeCAD.ActiveDocument)

    def ok(self, name, args=None):
        err, out = self.call(name, args or {})
        self.assertFalse(err, f"{name}: {out}")
        return out

    def test_flip_dimension_and_heal(self):
        try:
            import Draft
        except ImportError:
            self.skipTest("no Draft")
        import FreeCAD

        dim = Draft.make_linear_dimension(FreeCAD.Vector(0, 0, 0), FreeCAD.Vector(20, 0, 0), FreeCAD.Vector(10, 5, 0))
        FreeCAD.ActiveDocument.recompute()
        n0 = FreeCAD.Vector(dim.Normal)
        f = self.ok("draft_fix", {"action": "flip_dimension", "dimension": dim.Label})
        self.assertAlmostEqual(f["normal"][2], -(n0.z if n0.Length else 1), places=6)
        self.ok("draft_fix", {"action": "heal"})

    def test_new_part_and_rigid_group(self):
        import Part

        try:
            import JointObject  # noqa: F401
        except ImportError:
            self.skipTest("no Assembly")
        add_body(self.session, "R a", Part.makeBox(10, 10, 10))
        add_body(self.session, "R b", Part.makeBox(10, 10, 10).translated(__import__("FreeCAD").Vector(20, 0, 0)))
        add_body(self.session, "R c", Part.makeBox(10, 10, 10).translated(__import__("FreeCAD").Vector(40, 0, 0)))
        self.ok("asm_new", {"parts": ["R a", "R b", "R c"]})
        n = self.ok("asm_new_part", {"name": "R new"})
        self.assertEqual(n["part"], "R new")
        err, out = self.call("asm_rigid_group", {"parts": ["R b", "R c"]})
        if err:
            self.assertEqual(out["error"]["code"], "unavailable", out)
        else:
            self.assertEqual(len(out["parts"]), 2)


if __name__ == "__main__":
    unittest.main()
