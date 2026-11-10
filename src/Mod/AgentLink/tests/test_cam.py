# SPDX-License-Identifier: LGPL-2.1-or-later
# Toolpaths with FreeCAD's CAM workbench, on a real FreeCAD: a job, a
# profile, a pocket and the holes, posted as G-code.
import json
import unittest

from _fc import HAVE, add_body, caller, server

try:
    import Path.Main.Job  # noqa: F401

    CAM = HAVE
except Exception:
    CAM = False


@unittest.skipUnless(CAM, "FreeCAD with the CAM workbench is not importable here")
class Cam(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import FreeCAD
        import Part

        cls.session, srv = server()
        cls.call = staticmethod(caller(srv))
        FreeCAD.newDocument("Cam")
        cls.session.switch(FreeCAD.ActiveDocument)
        block = Part.makeBox(60, 40, 20)
        block = block.cut(Part.makeBox(30, 20, 8, FreeCAD.Vector(15, 10, 12)))
        block = block.cut(Part.makeCylinder(3, 20, FreeCAD.Vector(7, 7, 0)))
        add_body(cls.session, "Plate", block)

    def test_1_job_ops_post(self):
        err, r = self.call("cam_job", {"body": "Plate"})
        self.assertTrue(err)
        self.assertIn("tool", r["error"]["message"])
        err, j = self.call("cam_job", {"body": "Plate", "tool": {"diameter": 6, "feed": 800, "plunge": 250, "rpm": 15000}, "stock": {"margin": 3, "top": 1}})
        self.assertFalse(err, j)
        print("\njob:", json.dumps(j))
        self.assertAlmostEqual(j["stock"]["size"][0], 66, delta=0.01)
        self.assertEqual(j["tool"]["diameter"], 6)
        err, p = self.call("cam_op", {"kind": "profile", "side": "outside"})
        print("profile:", json.dumps(p))
        self.assertFalse(err, p)
        self.assertGreater(p["cutMm"], 2 * (60 + 40))
        # Outside the part by the tool's radius.
        self.assertLess(p["cutBox"]["min"][0], -2.9)
        err, k = self.call("cam_op", {"kind": "pocket", "faces": [{"normal": [0, 0, 1], "near": [30, 20, 12]}]})
        print("pocket:", json.dumps(k))
        self.assertFalse(err, k)
        self.assertAlmostEqual(k["cutZ"][0], 12, delta=0.05)
        err, ls = self.call("cam_list")
        self.assertEqual(len(ls["ops"]), 2)
        self.assertGreater(ls["minutes"], 0)
        err, g = self.call("cam_post", {"post": "grbl"})
        print("post:", json.dumps(g))
        self.assertFalse(err, g)
        with open(g["path"]) as f:
            text = f.read()
        self.assertIn("G21", text)
        self.assertNotIn("warnings", g)
        err, x = self.call("cam_op", {"kind": "pocket", "faces": ["Face1"], "name": "Nothing"})
        self.assertTrue(err)
        # Simulated on the stock: no crash, no gouge; the stock's top 1 mm
        # is left over the part (no face op), and the 6 mm mill leaves the
        # pocket's square corners 8 mm deep under it.
        err, sim = self.call("cam_sim", {"inline": False})
        print("sim:", json.dumps(sim))
        self.assertFalse(err, sim)
        self.assertEqual(sim["verdict"], "clear", sim)
        self.assertNotIn("crashes", sim)
        self.assertNotIn("gouge", sim["part"])
        self.assertGreater(sim["removedMm3"], 30 * 20 * 8 * 0.8)
        left = sim["part"]["left"]
        self.assertAlmostEqual(left["most"], 9.0, delta=0.2)
        at = left["at"]
        self.assertTrue(min(abs(at[0] - 15), abs(at[0] - 45)) < 3 and min(abs(at[1] - 10), abs(at[1] - 30)) < 3, at)
        self.assertGreater(left["share"], 0.6)
        import os

        self.assertGreater(os.path.getsize(sim["picture"]), 1000)

    def test_2_drill_finds_the_hole(self):
        # FreeCAD 1.0's Drilling.Create fails headless. An 8 mm tool is too
        # big for the 6 mm hole, so first the refusal, then a 3 mm tool.
        self.call("cam_tool", {"diameter": 8})
        err, r = self.call("cam_op", {"kind": "drill"})
        self.assertTrue(err)
        self.assertIn("smaller tool", r["error"]["message"], r)
        err, t = self.call("cam_tool", {"diameter": 3})
        self.assertFalse(err, t)
        err, d = self.call("cam_op", {"kind": "drill"})
        print("\ndrill:", json.dumps(d))
        self.assertFalse(err, d)
        self.assertGreater(d["cutMm"], 19)
        self.assertAlmostEqual(d["cutBox"]["min"][0], 7, delta=0.01)
        self.assertAlmostEqual(d["cutBox"]["min"][1], 7, delta=0.01)
        err, g = self.call("cam_post", {"post": "grbl"})
        self.assertFalse(err, g)
        self.assertIn("Drill", g["ops"])
        drill = next(o for o in self.session.doc.Objects if o.Label == "Drill")
        print("drill path:", [(c.Name, dict(c.Parameters)) for c in drill.Path.Commands][:14])
        err, sim = self.call("cam_sim", {"inline": False})
        print("drill sim:", json.dumps({k: sim.get(k) for k in ("ops", "crashes", "findings")}))
        self.assertFalse(err, sim)
        self.assertNotIn("crashes", sim)
        drilled = next(o for o in sim["ops"] if o["op"] == "Drill")
        self.assertGreater(drilled["removedMm3"], 3.14 * 1.5 ** 2 * 15)


if __name__ == "__main__":
    unittest.main()
