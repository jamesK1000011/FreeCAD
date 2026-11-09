# SPDX-License-Identifier: LGPL-2.1-or-later
# Sheet parts: the file writers anywhere, the cut tools on a real FreeCAD.
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

    HAVE = True
except ImportError:
    HAVE = False

from agentlink import sheetfile as sf  # noqa: E402


class SheetFiles(unittest.TestCase):
    ITEMS = [
        (sf.OUTER, ("line", 0, 0, 10, 0)),
        (sf.OUTER, ("arc", 10, 5, 5, -90, 90)),
        (sf.HOLES, ("circle", 5, 5, 1.5)),
        (sf.HOLES, ("poly", [(1, 1), (2, 1), (2, 2)], True)),
    ]

    def test_dxf_r12_reads_back(self):
        text = sf.dxf(self.ITEMS, (0, 0, 20, 10))
        self.assertIn("AC1009", text)
        self.assertNotIn("LWPOLYLINE", text)
        back = sf.read_dxf(text)
        self.assertEqual(back["entities"], {"LINE": 1, "ARC": 1, "CIRCLE": 1, "POLYLINE": 1})
        self.assertEqual(back["layers"], {"OUTER": 2, "HOLES": 2})
        self.assertTrue(text.endswith("  0\nEOF\n"))
        # The arc's angles as DXF has them: counter-clockwise, 0 to 360.
        self.assertIn(" 50\n270\n 51\n90\n", text)

    def test_svg(self):
        text = sf.svg(self.ITEMS, 20, 10)
        self.assertIn('width="20mm"', text)
        self.assertEqual(sf.read_svg(text), {"line": 1, "circle": 1, "path": 1, "polyline": 0, "polygon": 1})
        # y flipped: the line on y = 0 is at the bottom.
        self.assertIn('y1="10"', text)

    def test_shelf(self):
        at = sf.shelf([(100, 60)] * 3, 250, 150, 3)
        self.assertEqual(at, [(3, 3), (106, 3), (3, 66)])
        self.assertEqual(sf.shelf([(100, 60)] * 3, 150, 100, 3)[1:], [None, None])
        self.assertEqual(sf.shelf([(10, 10)] * 2, None, None, 2), [(2, 2), (14, 2)])


@unittest.skipUnless(HAVE, "FreeCAD is not importable here")
class CutTools(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import FreeCAD
        import Part

        from agentlink.tools import Session, build_server

        V = FreeCAD.Vector
        cls.dir = tempfile.mkdtemp()
        cls.session = Session(out_dir=cls.dir)
        cls.server = build_server(cls.session, lean=False)
        doc = cls.session.doc

        def add(label, shape):
            o = doc.addObject("Part::Feature", "Body")
            o.Label = label
            o.Shape = shape
            return o

        plate = Part.makeBox(100, 60, 3)
        plate = plate.cut(Part.makeCylinder(1.0, 5, V(50, 30, -1)))  # Ø2
        plate = plate.cut(Part.makeCylinder(4.0, 5, V(5.5, 30, -1)))  # Ø8, 1.5 mm from the edge
        add("Plate", plate)
        box = Part.makeBox(40, 30, 3)
        add("Chamfered", box.makeChamfer(1.0, [e for e in box.Edges if abs(e.BoundBox.ZMin - 3) < 1e-6][:1]))
        pts = [V(0, 0, 0), V(60, 0, 0), V(60, 20, 0), V(20, 20, 0), V(20, 50, 0), V(0, 50, 0), V(0, 0, 0)]
        add("Ell", Part.Face(Part.makePolygon(pts)).extrude(V(0, 0, 6)))
        doc.recompute()

    def call(self, name, args=None):
        r = self.server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args or {}}})["result"]
        return r.get("isError", False), json.loads(r["content"][0]["text"])

    def test_1_needs_a_cut_setup(self):
        err, r = self.call("cut_check", {"body": "Plate"})
        self.assertTrue(err)
        self.assertEqual(r["error"]["code"], "invalid_state")
        err, a = self.call("setup", {"action": "add", "type": "cut", "bodies": ["Plate", "Chamfered"], "settings": {"machine": "laser", "thickness": 3, "kerf": 0.2}})
        self.assertFalse(err, a)
        err, a = self.call("setup", {"action": "add", "type": "cut", "bodies": ["Ell"], "settings": {"machine": "router", "thickness": 6, "kerf": 6.35}})
        self.assertFalse(err, a)

    def test_2_laser_plate(self):
        err, r = self.call("cut_check", {"body": "Plate", "material": "steel", "sheet": [1000, 500]})
        self.assertFalse(err, r)
        print("\ncut_check:", json.dumps(r))
        self.assertEqual(r["sheet"]["thickness"], 3.0)
        self.assertEqual(r["profile"]["holes"], 2)
        rules = {f["rule"] for f in r["findings"]}
        self.assertIn("small-hole", rules)
        self.assertIn("thin-web", rules)
        web = [f for f in r["findings"] if f["rule"] == "thin-web"][0]
        self.assertAlmostEqual(web["value"], 1.5, delta=0.01)
        err, small = self.call("cut_check", {"body": "Plate", "sheet": [80, 50]})
        self.assertIn("sheet-size", {f["rule"] for f in small["findings"]})
        self.assertEqual(small["verdict"], "fix")

    def test_3_plasma_hole_under_thickness(self):
        err, r = self.call("cut_check", {"body": "Plate", "machine": "plasma"})
        holes = [f for f in r["findings"] if f["rule"] == "small-hole" and f["severity"] == "error"]
        self.assertTrue(holes, r["findings"])
        self.assertIn("thickness", holes[0]["says"])

    def test_4_not_a_sheet(self):
        err, r = self.call("cut_check", {"body": "Chamfered"})
        self.assertFalse(err, r)
        self.assertEqual(r["verdict"], "fix")
        self.assertEqual(r["findings"][0]["rule"], "not-sheet")
        self.assertTrue(r["sheet"]["notSquare"])
        self.assertTrue(all(n.startswith("Face") for n in r["sheet"]["notSquare"]))

    def test_5_router_inside_corner(self):
        err, r = self.call("cut_check", {"body": "Ell"})
        self.assertFalse(err, r)
        corners = [f for f in r["findings"] if f["rule"] == "inside-corner"]
        self.assertEqual(len(corners), 1, r["findings"])
        self.assertIn("assumed", r)  # the material

    def test_6_export(self):
        err, x = self.call("cut_export", {"body": "Plate", "copies": 3, "sheet": [250, 150]})
        self.assertFalse(err, x)
        print("\ncut_export:", json.dumps(x))
        self.assertEqual(x["placed"], {"Plate": 3})
        self.assertEqual(x["files"]["dxf"]["readBack"], {"LINE": 12, "CIRCLE": 6})
        self.assertTrue(x["files"]["dxf"]["matches"])
        self.assertEqual(x["files"]["svg"]["readBack"], {"line": 12, "circle": 6})
        err, c = self.call("cut_export", {"body": "Plate", "copies": 3, "sheet": [150, 100], "compensate": True, "name": "comp"})
        self.assertFalse(err, c)
        self.assertEqual(c["placed"], {"Plate": 1})
        self.assertEqual(c["left"]["bodies"], {"Plate": 2})
        with open(c["files"]["dxf"]["path"]) as f:
            text = f.read()
        self.assertIn("\n40\n0.9\n", text.replace(" ", ""))  # the Ø2 hole less half the kerf each side
        err, e = self.call("cut_export", {"bodies": ["Plate", "Ell"]})
        self.assertTrue(err)
        self.assertIn("one sheet per file", e["error"]["message"])


if __name__ == "__main__":
    unittest.main()
