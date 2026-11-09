# SPDX-License-Identifier: LGPL-2.1-or-later
# Pictures: the rasteriser on its own (numpy only), and view_render on a
# real FreeCAD. With AGENTLINK_RENDERS set, the pictures are kept there to
# look at.
import base64
import json
import os
import tempfile
import unittest

import numpy as np

from _fc import HAVE, add_body, caller, server

from agentlink import render

OUT = os.environ.get("AGENTLINK_RENDERS") or tempfile.mkdtemp()
os.makedirs(OUT, exist_ok=True)


def box(a, b, c, o=(0, 0, 0)):
    V = np.array([[x, y, z] for x in (0, a) for y in (0, b) for z in (0, c)], float) + o
    F = np.array([[0, 2, 3], [0, 3, 1], [4, 5, 7], [4, 7, 6], [0, 1, 5], [0, 5, 4], [2, 6, 7], [2, 7, 3], [0, 4, 6], [0, 6, 2], [1, 3, 7], [1, 7, 5]])[:, ::-1]
    return V, F


class Raster(unittest.TestCase):
    def test_box_fills_its_view(self):
        V, F = box(40, 30, 20)
        img, across, drawn = render.compose([{"name": "b", "V": V, "F": F, "color": (0.8, 0.5, 0.3)}], ["top"], 200, 200)
        self.assertTrue(drawn)
        part = (np.abs(img - render.BG).sum(-1) > 0.05)
        part[:24] = False  # the view's label
        ys, xs = np.nonzero(part)
        # 40 x 30 fitted into 172 px: 4:3, wider than tall.
        w, h = np.ptp(xs) + 1, np.ptp(ys) + 1
        self.assertAlmostEqual(w / h, 40 / 30, delta=0.06)
        self.assertAlmostEqual(across["top"], 200 / (172 / 40), delta=0.5)

    def test_png_is_a_png(self):
        img = np.zeros((7, 9, 3))
        img[3, 4] = 1
        data = render.png(img)
        self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
        import struct
        import zlib

        w, h = struct.unpack(">II", data[16:24])
        self.assertEqual((w, h), (9, 7))
        raw = zlib.decompress(data[data.index(b"IDAT") + 4:-16])
        self.assertEqual(len(raw), 7 * (1 + 9 * 3))

    def test_wall_of_a_hollow_box(self):
        V1, F1 = box(40, 30, 20)
        V2, F2 = box(36, 24, 16, (2, 4, 2))
        V = np.concatenate([V1, V2])
        F = np.concatenate([F1, F2[:, ::-1] + len(V1)])
        SV, SF, vals = render.wall_map(V, F, reach=12)
        fin = vals[np.isfinite(vals)]
        self.assertAlmostEqual(fin.min(), 2.0, delta=0.01)
        # The middle of the +y outside face: the 2 mm side wall; of the -y
        # outside face: 4 mm.
        c = SV[SF].mean(1)
        n = vals[::3]
        mid_y = (np.abs(c[:, 1] - 30) < 1e-6) & (np.abs(c[:, 0] - 20) < 6) & (np.abs(c[:, 2] - 10) < 4)
        mid_y0 = (np.abs(c[:, 1]) < 1e-6) & (np.abs(c[:, 0] - 20) < 6) & (np.abs(c[:, 2] - 10) < 4)
        self.assertTrue(mid_y.any() and mid_y0.any())
        self.assertAlmostEqual(float(np.median(n[mid_y])), 2.0, delta=0.01)
        self.assertAlmostEqual(float(np.median(n[mid_y0])), 4.0, delta=0.01)

    def test_section_shows_the_inside(self):
        V, F = box(20, 20, 20)
        img, _, _ = render.compose([{"name": "b", "V": V, "F": F, "color": (0.8, 0.8, 0.8)}], ["iso"], 300, 300, section={"axis": "y", "offset": 10})
        # The hatched inside is reddish: more red than green in some pixels.
        red = (img[..., 0] - img[..., 1]) > 0.2
        self.assertGreater(red.sum(), 500)


@unittest.skipUnless(HAVE, "FreeCAD is not importable here")
class ViewRender(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import FreeCAD
        import Part

        cls.session, cls.srv = server()
        cls.call = staticmethod(caller(cls.srv))
        FreeCAD.newDocument("Pictures")
        cls.session.switch(FreeCAD.ActiveDocument)
        outer = Part.makeBox(40, 30, 20)
        cup = outer.cut(Part.makeBox(36, 26, 19, FreeCAD.Vector(2, 2, 1.0)))
        # A thin spot: a pocket from outside leaves 0.6 mm at x = 0.
        cup = cup.cut(Part.makeBox(1.4, 8, 8, FreeCAD.Vector(0, 11, 6)))
        add_body(cls.session, "Cup", cup)

    def raw(self, args):
        return self.srv.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "view_render", "arguments": args}})["result"]

    def test_plain_render_comes_back_as_an_image(self):
        r = self.raw({"views": "all", "path": os.path.join(OUT, "cup_views.png")})
        self.assertFalse(r.get("isError"), r)
        meta = json.loads(r["content"][0]["text"])
        self.assertEqual(meta["views"], ["iso", "top", "front", "right"])
        self.assertEqual(r["content"][1]["type"], "image")
        self.assertEqual(base64.b64decode(r["content"][1]["data"])[:4], b"\x89PNG")
        self.assertTrue(os.path.isfile(meta["path"]))
        r = self.raw({"inline": False, "path": os.path.join(OUT, "cup_iso.png")})
        self.assertEqual(len(r["content"]), 1)

    def test_wall_map_finds_the_thin_spot(self):
        err, r = self.call("view_render", {"map": "wall", "views": ["iso-front-left", "left"], "inline": False, "path": os.path.join(OUT, "cup_wall.png")})
        self.assertFalse(err, r)
        print("\nwall map:", json.dumps(r["map"]))
        self.assertAlmostEqual(r["map"]["min"], 0.6, delta=0.05)
        at = r["map"]["worst"]["at"]
        self.assertLess(at[0], 1.5)
        self.assertTrue(11 <= at[1] <= 19 and 6 <= at[2] <= 14, at)
        err, z = self.call("view_render", {"map": "wall", "view": "left", "zoom": {"at": at, "size": 16}, "inline": False, "path": os.path.join(OUT, "cup_wall_zoom.png")})
        self.assertFalse(err, z)
        self.assertGreaterEqual(z["mmAcross"]["left"], 16.0)
        self.assertLess(z["mmAcross"]["left"], 30.0)

    def test_section_and_refusals(self):
        err, r = self.call("view_render", {"section": {"axis": "x", "offset": 20}, "inline": False, "path": os.path.join(OUT, "cup_section.png")})
        self.assertFalse(err, r)
        err, r = self.call("view_render", {"view": "sideways"})
        self.assertTrue(err)
        err, r = self.call("view_render", {"map": "nope"})
        self.assertTrue(err)
        self.assertIn("wall", r["error"]["message"])
        err, r = self.call("view_render", {"map": "stress"})
        self.assertTrue(err)
        self.assertEqual(r["error"]["code"], "not_found")
        err, m = self.call("view_maps")
        self.assertIn("wall", [x["name"] for x in m["maps"]])
        import FreeCAD

        err, r = self.call("view_snapshot")
        if not FreeCAD.GuiUp:
            self.assertEqual(r["error"]["code"], "unavailable")


if __name__ == "__main__":
    unittest.main()
