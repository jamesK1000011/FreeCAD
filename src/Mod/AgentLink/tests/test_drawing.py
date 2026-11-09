# SPDX-License-Identifier: LGPL-2.1-or-later
# The drawing family: sheets, manuals, patent figures. The sheet writers and
# the layout are tested anywhere; the views need FreeCAD and TechDraw.
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
if os.environ.get("FREECAD_LIB"):
    sys.path.append(os.environ["FREECAD_LIB"])
try:
    import FreeCAD  # noqa: F401
    import TechDraw  # noqa: F401

    HAVE = True
except ImportError:
    HAVE = False

from agentlink import sheets  # noqa: E402


class Writers(unittest.TestCase):
    def sheet(self):
        s = sheets.Sheet(297, 210, "Test (1)")
        s.frame()
        s.line([(20, 20), (60, 20), (60, 50)], "visible")
        s.line([(20, 30), (40, 30)], "hidden")
        s.dimension((20, 50), (60, 50), 8, "40")
        s.dimension((60, 50), (60, 20), 8, "30")
        s.text(100, 100, 3, "FRONT — A&B", "middle")
        return s

    def test_svg(self):
        svg = self.sheet().svg()
        self.assertTrue(svg.startswith("<?xml"))
        self.assertIn('width="297mm"', svg)
        self.assertIn("stroke-dasharray", svg)
        self.assertIn("A&amp;B", svg)
        self.assertIn("rotate(-90", svg)

    def test_pdf(self):
        pdf = self.sheet().pdf()
        self.assertTrue(pdf.startswith(b"%PDF-1.4"))
        self.assertTrue(pdf.rstrip().endswith(b"%%EOF"))
        xref = int(pdf[pdf.rindex(b"startxref") + 9:].split()[0])
        self.assertEqual(pdf[xref:xref + 4], b"xref")
        self.assertIn(b"/MediaBox [0 0 841.89 595.28]", pdf)

    def test_dxf(self):
        s = self.sheet()
        lines = sheets.read_dxf_lines(s.dxf())
        self.assertIn(((20.0, 190.0), (60.0, 190.0), "VISIBLE"), lines)
        self.assertTrue(any(layer == "HIDDEN" for (_, _, layer) in lines))

    def test_layout(self):
        sizes = {"front": (80, 30), "top": (80, 60), "right": (60, 30), "iso": (100, 80)}
        first = sheets.layout(sizes, sheets.PAPER["A4"], "first")
        third = sheets.layout(sizes, sheets.PAPER["A4"], "third")
        f, t = first["centres"], third["centres"]
        self.assertGreater(f["top"][1], f["front"][1], "first angle: top under the front")
        self.assertLess(f["right"][0], f["front"][0], "first angle: right on the left")
        self.assertLess(t["top"][1], t["front"][1], "third angle: top over the front")
        self.assertGreater(t["right"][0], t["front"][0])
        self.assertAlmostEqual(f["top"][0], f["front"][0])
        self.assertIn(first["scale"], sheets.SCALES)
        self.assertEqual(sheets.scale_text(0.5), "1:2")
        self.assertEqual(sheets.scale_text(2), "2:1")
        with self.assertRaises(ValueError):
            sheets.layout(sizes, sheets.PAPER["A4"], "first", scale=10)
        big = sheets.layout({"front": (900, 300), "top": (900, 600)}, sheets.PAPER["A3"])
        self.assertLess(big["scale"], 1)


@unittest.skipUnless(HAVE, "FreeCAD with TechDraw is not importable here")
class Drawing(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from agentlink.tools import Session, build_server

        cls.dir = tempfile.mkdtemp()
        cls.session = Session(out_dir=cls.dir)
        cls.server = build_server(cls.session, lean=False)
        err, m = cls.call(cls, "gen_part", {"generator": "project_box", "params": {"length": 80, "width": 60, "height": 30, "bosses": True}, "name": "Box"})
        assert not err, m

    def call(self, name, args=None):
        r = self.server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args or {}}})["result"]
        return r.get("isError", False), json.loads(r["content"][0]["text"])

    def test_1_sheet(self):
        err, d = self.call("drawing_make", {"bodies": ["Box"], "name": "Box"})
        self.assertFalse(err, d)
        print("\ndrawing:", json.dumps({k: d.get(k) for k in ("paper", "scale", "dimensions", "techdrawPage", "page", "pageNote")}))
        for k in ("svg", "pdf", "dxf"):
            self.assertTrue(os.path.getsize(d["files"][k]) > 500, k)
        with open(d["files"]["svg"], encoding="utf-8") as f:
            svg = f.read()
        self.assertGreater(svg.count("<path"), 20, "the views have lines")
        dims = {x["axis"]: x["mm"] for x in d["dimensions"]}
        self.assertAlmostEqual(dims["x"], 80, delta=0.01)
        self.assertAlmostEqual(dims["y"], 60, delta=0.01)
        self.assertAlmostEqual(dims["z"], 30, delta=0.01)
        self.assertIn(d["scale"], ("1:1", "1:2"))
        # The page in the document, when TechDraw could make it here.
        if d["techdrawPage"]:
            self.assertTrue(any(o.TypeId == "TechDraw::DrawPage" for o in self.session.doc.Objects))
        err, e = self.call("drawing_make", {"views": ["side"]})
        self.assertTrue(err)
        self.assertIn("front", e["error"]["message"])
        err, e = self.call("drawing_make", {"bodies": ["Box"], "scale": 5, "paper": "A4"})
        self.assertTrue(err)
        self.assertIn("scale", e["error"]["message"])

    def test_2_exploded(self):
        err, x = self.call("drawing_exploded", {"steps": [{"title": "The box", "add": ["Box"]}, {"title": "The lid", "add": ["Box lid"], "note": "Four M3 screws."}], "name": "box"})
        self.assertFalse(err, x)
        self.assertEqual(len(x["steps"]), 2)
        self.assertEqual([b["item"] for b in x["bom"]], [1, 2])
        with open(x["manual"], encoding="utf-8") as f:
            md = f.read()
        self.assertIn("| 2 | Box lid | 1 |", md)
        self.assertIn("box_step02.svg", md)
        self.assertTrue(os.path.isfile(x["steps"][1]["svg"]))
        err, e = self.call("drawing_exploded", {"steps": [{"add": ["Box"]}, {"add": ["Box"]}]})
        self.assertTrue(err)
        self.assertIn("once", e["error"]["message"])

    def test_3_patent(self):
        err, p = self.call("patent_figures", {"name": "box"})
        self.assertFalse(err, p)
        print("\npatent:", json.dumps({k: p.get(k) for k in ("numerals", "notSeenIn")}))
        self.assertEqual(len(p["figures"]), 4)
        self.assertEqual(p["numerals"], {"Box": 10, "Box lid": 20})
        with open(p["figures"][0]["svg"], encoding="utf-8") as f:
            svg = f.read()
        self.assertIn("FIG. 1", svg)
        self.assertIn(">10<", svg)
        err, d = self.call("patent_draft", {"title": "Project box", "field": "Enclosures for electronics."})
        self.assertFalse(err, d)
        with open(d["draft"], encoding="utf-8") as f:
            md = f.read()
        self.assertIn("NOT FOR FILING", md)
        self.assertIn("box 10", md)
        self.assertIn("FIG. 1 is an isometric view", md)


if __name__ == "__main__":
    unittest.main()
