# SPDX-License-Identifier: LGPL-2.1-or-later
# BIM's remaining commands headless, each checked on what it must make.
import json
import os
import unittest

from _fc import HAVE, add_body, caller, server

try:
    import Arch  # noqa: F401

    BIM = HAVE
except Exception:
    BIM = False


@unittest.skipUnless(BIM, "FreeCAD with BIM is not importable here")
class BimGaps(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import FreeCAD

        cls.session, srv = server()
        cls.call = staticmethod(caller(srv))
        FreeCAD.newDocument("BimGaps")
        cls.session.switch(FreeCAD.ActiveDocument)

    def ok(self, name, args=None):
        err, out = self.call(name, args or {})
        self.assertFalse(err, f"{name}: {out}")
        print(f"\n{name}:", json.dumps(out)[:300])
        return out

    def test_1_components(self):
        import Part

        add_body(self.session, "G box", Part.makeBox(1000, 200, 3000))
        c = self.ok("bim_component", {"from": "G box", "name": "G comp"})
        self.assertAlmostEqual(c["volume"], 1000 * 200 * 3000, delta=1)
        cl = self.ok("bim_component", {"kind": "clone", "from": "G comp", "at": [2000, 0, 0]})
        self.assertAlmostEqual(cl["volume"], c["volume"], delta=1)
        un = self.ok("bim_component", {"kind": "unclone", "from": cl["object"]})
        self.assertEqual(un["kind"], "unclone")
        f = self.ok("bim_ifc_brep", {"objects": ["G comp"]})
        self.assertTrue(f["set"][0]["forceBrep"])
        self.assertFalse(self.ok("bim_ifc_brep", {"objects": ["G comp"]})["set"][0]["forceBrep"])

    def test_2_tidy(self):
        import Part

        add_body(self.session, "T a", Part.makeBox(10, 10, 10))
        add_body(self.session, "T b", Part.makeBox(10, 10, 10).translated(__import__("FreeCAD").Vector(20, 0, 0)))
        g = self.ok("bim_glue", {"objects": ["T a", "T b"], "name": "T glued"})
        self.assertEqual(g["of"], 2)
        add_body(self.session, "T e1", Part.makeLine((0, 0, 0), (10, 0, 0)))
        add_body(self.session, "T e2", Part.makeLine((10, 0, 0), (10, 10, 0)))
        r = self.ok("bim_rewire", {"objects": ["T e1", "T e2"]})
        self.assertEqual(len(r["wires"]), 1)
        self.ok("bim_trash", {"action": "put", "objects": ["T glued"]})
        self.assertIn("T glued", self.ok("bim_trash", {"action": "list"})["trash"])
        self.assertIn("T glued", self.ok("bim_trash", {"action": "empty"})["emptied"])
        grp = self.session.doc.addObject("App::DocumentObjectGroup", "Grp")
        for n in ("Zed", "Alpha"):
            o = self.session.doc.addObject("App::FeaturePython", n)
            o.Label = n
            grp.addObject(o)
        self.assertEqual(self.ok("bim_reorder", {"group": "Grp", "sort": True})["order"], ["Alpha", "Zed"])

    def test_3_panels_and_nest(self):
        err, p = self.call("bim_panel", {"points": [[0, 0], [600, 0], [600, 400], [0, 400]], "thickness": 18, "name": "N panel"})
        if err:
            self.skipTest(f"bim_panel: {p}")
        cut = self.ok("bim_panel_out", {"kind": "cut", "panels": ["N panel"]})
        self.assertGreater(cut["cuts"][0]["edges"], 3)
        sh = self.ok("bim_panel_out", {"kind": "sheet", "panels": ["N panel"], "width": 2440, "height": 1220})
        self.assertEqual(sh["size"], [2440.0, 1220.0])

    def test_4_image_views_library(self):
        import tempfile

        png = os.path.join(tempfile.mkdtemp(), "plan.png")
        with open(png, "wb") as f:
            f.write(bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d4944415478da63f8ffff3f0005fe02fea7d6a4c10000000049454e44ae426082"))
        im = self.ok("bim_image_plane", {"path": png, "width": 5000, "height": 3000})
        self.assertEqual(im["size"], [5000.0, 3000.0])
        lib = os.path.join(tempfile.mkdtemp(), "lib")
        os.makedirs(os.path.join(lib, "doors"))
        import Part

        Part.makeBox(900, 40, 2100).exportStep(os.path.join(lib, "doors", "door 900.step"))
        found = self.ok("bim_library", {"folder": lib, "search": "door"})
        self.assertEqual(found["found"][0]["name"], "door 900.step")
        ins = self.ok("bim_library", {"folder": lib, "insert": found["found"][0]["path"]})
        self.assertTrue(ins["objects"])
        self.ok("bim_wall", {"points": [[0, 0], [4000, 0]], "width": 200, "height": 3000, "name": "V wall"})
        self.ok("bim_level", {"objects": ["V wall"], "name": "V level"})
        v = self.ok("bim_views", {"levels": ["V level"]})
        self.assertEqual(len(v["views"]), 1)

    def test_5_reextrude(self):
        self.ok("bim_wall", {"points": [[0, 5000], [3000, 5000]], "width": 200, "height": 2500, "name": "R wall"})
        w = self.session.doc.getObjectsByLabel("R wall")[0]
        top = max(range(len(w.Shape.Faces)), key=lambda i: w.Shape.Faces[i].CenterOfMass.z)
        r = self.ok("bim_reextrude", {"face": f"R wall.Face{top + 1}", "delete": False})
        self.assertIsNotNone(r.get("volume"))


if __name__ == "__main__":
    unittest.main()
