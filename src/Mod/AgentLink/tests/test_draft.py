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


    def test_5_outline_from_svg(self):
        """A logo from an SVG: a 20 x 10 plate with a round hole, a triangle,
        and an open stroke left out. In millimetres (width 100mm, viewBox 100)."""
        import math
        import tempfile

        svg = os.path.join(tempfile.mkdtemp(), "logo.svg")
        with open(svg, "w") as f:
            f.write('<svg xmlns="http://www.w3.org/2000/svg" width="100mm" height="50mm" viewBox="0 0 100 50">'
                    '<path d="M 10 10 L 30 10 L 30 20 L 10 20 Z"/>'
                    '<circle cx="20" cy="15" r="3"/>'
                    '<path d="M 40 10 L 50 10 L 45 20 Z"/>'
                    '<path d="M 60 10 L 70 20" fill="none" stroke="black"/></svg>')
        err, o = self.call("draft_outline", {"path": svg, "mode": "extrude", "depth": 2, "name": "Logo"})
        print("\noutline:", json.dumps(o))
        self.assertFalse(err, o)
        # The circle inside the plate is a hole in it (FaceMakerBullseye).
        want = (200 - math.pi * 9 + 50) * 2
        self.assertAlmostEqual(o["volume"], want, delta=want * 0.01)
        self.assertEqual(o["openCurvesLeftOut"], 1)
        # Raised on the tag's top, scaled to 30 mm wide.
        err, e = self.call("draft_outline", {"path": svg, "body": "Tag", "width": 30, "depth": 0.8})
        print("on the tag:", json.dumps(e))
        self.assertFalse(err, e)
        self.assertEqual(e["mode"], "emboss")
        self.assertAlmostEqual(e["size"][0], 30, delta=0.01)
        self.assertGreater(e["volumeChange"], 0)
        err, x = self.call("draft_outline", {"path": "/nonexistent.svg"})
        self.assertTrue(err)


    def test_6_arrays(self):
        import FreeCAD
        import Part

        from _fc import add_body

        add_body(self.session, "Coin", Part.makeCylinder(5, 2))
        # A 3 x 2 grid 2 mm apart: separate solids, so a batch of bodies.
        err, g = self.call("draft_array", {"body": "Coin", "kind": "grid", "count": [3, 2], "separate": True})
        print("\ngrid:", json.dumps(g))
        self.assertFalse(err, g)
        self.assertEqual(len(g["bodies"]), 6)
        last = next(o for o in self.session.doc.Objects if o.Label == g["bodies"][-1]).Shape.BoundBox
        self.assertAlmostEqual(last.XMin, 2 * 12 - 5, delta=1e-6)
        # Six spokes round the middle, joined into one wheel.
        add_body(self.session, "Spoke", Part.makeBox(20, 2, 2, FreeCAD.Vector(0, -1, 0)))
        err, w = self.call("draft_array", {"body": "Spoke", "kind": "polar", "count": 6})
        print("polar:", json.dumps(w))
        self.assertFalse(err, w)
        self.assertEqual(w["solids"], 1)
        self.assertAlmostEqual(w["size"][0], 40, delta=0.2)
        # Along a path of points, turned with it.
        add_body(self.session, "Bead", Part.makeSphere(1.5))
        err, p = self.call("draft_array", {"body": "Bead", "kind": "path", "count": 5, "path": {"points": [[0, 0, 0], [20, 10, 0], [40, 0, 0]]}, "align": True})
        self.assertFalse(err, p)
        self.assertEqual(p["solids"], 5)
        self.assertIn("note", p)
        err, x = self.call("draft_array", {"body": "Bead", "kind": "spiral"})
        self.assertTrue(err)


    def test_7_texture_prepare(self):
        """A block ready for BumpMesh: the top's triangles as the mask, the
        STL closed and outward, the mask's count the STL's."""
        import struct

        import Part

        from _fc import add_body

        add_body(self.session, "Grip", Part.makeBox(30, 20, 10))
        err, t = self.call("texture_prepare", {"body": "Grip", "faces": [{"normal": [0, 0, 1]}], "mode": "include"})
        print("\ntexture:", json.dumps({k: t.get(k) for k in ("triangles", "maskTriangles", "faces", "mode")}))
        self.assertFalse(err, t)
        with open(t["mask"]) as f:
            m = json.load(f)
        self.assertEqual(m["stlTriangles"], t["triangles"])
        with open(t["stl"], "rb") as f:
            data = f.read()
        n = struct.unpack("<I", data[80:84])[0]
        self.assertEqual(n, t["triangles"])
        up = 0
        for k in m["triangles"]:
            nx, ny, nz = struct.unpack("<3f", data[84 + 50 * k:96 + 50 * k])
            up += nz > 0.99
        self.assertEqual(up, len(m["triangles"]))
        # Every triangle's normal points out of the block.
        for k in range(n):
            vals = struct.unpack("<12f", data[84 + 50 * k:132 + 50 * k])
            c = [sum(vals[3 + i::3][:3]) / 3 for i in range(3)]
            nrm = vals[:3]
            out = sum((c[i] - (15, 10, 5)[i]) * nrm[i] for i in range(3))
            self.assertGreater(out, 0, k)
        self.assertEqual(t["next"]["tool"], "bumpmesh_texture_file")

    def test_8_texture_check(self):
        """BumpMesh stood in for: the prepared STL split finely and a pattern
        raised on the masked faces only. The check reads the top textured,
        the rest plain and the mesh closed; a texture that also reaches a
        side is caught as leaked."""
        import numpy as np
        import Part

        from _fc import add_body
        from agentlink.families.draft import read_stl

        add_body(self.session, "Knurl", Part.makeBox(30, 20, 10))
        err, t = self.call("texture_prepare", {"body": "Knurl", "faces": [{"normal": [0, 0, 1]}], "mode": "include"})
        self.assertFalse(err, t)
        with open(t["mask"]) as f:
            m = json.load(f)
        T = read_stl(t["stl"])
        masked = np.zeros(len(T), bool)
        masked[m["triangles"]] = True
        for _ in range(4):
            a, b, c = T[:, 0], T[:, 1], T[:, 2]
            ab, bc, ca = (a + b) / 2, (b + c) / 2, (c + a) / 2
            T = np.concatenate([np.stack(x, 1) for x in ((a, ab, ca), (ab, b, bc), (ca, bc, c), (ab, bc, ca))])
            masked = np.tile(masked, 4)

        def write(path, raise_on):
            P, inv = np.unique(np.round(T.reshape(-1, 3), 6), axis=0, return_inverse=True)
            inv = inv.reshape(-1, 3)
            ok = np.ones(len(P), bool)
            np.logical_and.at(ok, inv[~raise_on].ravel(), False)
            h = 0.3 * (1 + np.sin(P[:, 0]) * np.sin(P[:, 1])) / 2
            Q = P.copy()
            n = np.cross(T[:, 1] - T[:, 0], T[:, 2] - T[:, 0])
            n /= np.linalg.norm(n, axis=1)[:, None]
            N = np.zeros_like(P)
            for k in range(3):
                np.add.at(N, inv[:, k], n)
            N /= np.linalg.norm(N, axis=1)[:, None]
            Q[ok] += (h[ok])[:, None] * N[ok]
            import struct

            with open(path, "wb") as f:
                f.write(b" " * 80 + struct.pack("<I", len(inv)))
                for tri in inv:
                    f.write(struct.pack("<12fH", 0, 0, 0, *Q[tri[0]], *Q[tri[1]], *Q[tri[2]], 0))

        good = t["stl"].replace("-for-texture", "-textured")
        write(good, masked)
        err, o = self.call("texture_check", {"body": "Knurl", "path": good})
        print("\ntexture_check:", json.dumps({k: o.get(k) for k in ("verdict", "depth", "texturedFaces", "leaked", "mesh", "note")}))
        self.assertFalse(err, o)
        self.assertEqual(o["verdict"], "ok", o)
        top = next(iter(o["texturedFaces"].values()))
        self.assertGreater(top["moved"], 0.5)
        self.assertLessEqual(top["out"], 0.31)
        side = T[:, :, 1].max(1) < 0.001  # the face at y = 0
        bad = t["stl"].replace("-for-texture", "-leaky")
        write(bad, masked | side)
        err, o = self.call("texture_check", {"body": "Knurl", "path": bad, "mask": t["mask"]})
        self.assertFalse(err, o)
        self.assertEqual(o["verdict"], "check")
        self.assertEqual(len(o["leaked"]["faces"]), 1, o["leaked"])


if __name__ == "__main__":
    unittest.main()
