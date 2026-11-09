# SPDX-License-Identifier: LGPL-2.1-or-later
# The electronics family. The KiCad reader and the order packing are
# tested anywhere; the board, enclosure and channels need FreeCAD.
import json
import os
import shutil
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
if os.environ.get("FREECAD_LIB"):
    sys.path.append(os.environ["FREECAD_LIB"])
try:
    import FreeCAD  # noqa: F401

    HAVE = True
except ImportError:
    HAVE = False

from agentlink import kicad, packing  # noqa: E402

# A 50 x 30 mm two-layer board, one corner rounded (r 3), two M3 mounting
# holes, a USB-C connector over the near edge, a tall capacitor, a part
# underneath and a footprint turned 90° with a 2.2 mm unplated hole.
BOARD = """(kicad_pcb (version 20240108) (generator "pcbnew")
  (general (thickness 1.6) (legacy_teardrops no))
  (paper "A4")
  (title_block (title "Test board"))
  (layers (0 "F.Cu" signal) (31 "B.Cu" signal) (44 "Edge.Cuts" user) (49 "F.Fab" user))
  (gr_line (start 100 100) (end 147 100) (stroke (width 0.05) (type default)) (layer "Edge.Cuts"))
  (gr_arc (start 147 100) (mid 149.1213 100.8787) (end 150 103) (stroke (width 0.05) (type default)) (layer "Edge.Cuts"))
  (gr_line (start 150 103) (end 150 130) (layer "Edge.Cuts"))
  (gr_line (start 100 130) (end 150 130) (layer "Edge.Cuts"))
  (gr_line (start 100 130) (end 100 100) (layer "Edge.Cuts"))
  (footprint "MountingHole:MountingHole_3.2mm_M3" (layer "F.Cu") (at 103.5 103.5)
    (property "Reference" "H1") (property "Value" "MountingHole")
    (pad "" np_thru_hole circle (at 0 0) (size 3.2 3.2) (drill 3.2) (layers "*.Cu" "*.Mask")))
  (footprint "MountingHole:MountingHole_3.2mm_M3" (layer "F.Cu") (at 146.5 126.5)
    (property "Reference" "H2") (property "Value" "MountingHole")
    (pad "" np_thru_hole circle (at 0 0) (size 3.2 3.2) (drill 3.2) (layers "*.Cu" "*.Mask")))
  (footprint "Connector_USB:USB_C_Receptacle" (layer "F.Cu") (at 125 129)
    (property "Reference" "J1") (property "Value" "USB_C") (property "Height" "3.3")
    (fp_rect (start -4.5 -3.5) (end 4.5 2) (stroke (width 0.05) (type default)) (layer "F.CrtYd"))
    (pad "A1" smd rect (at -3 -2) (size 0.3 1) (layers "F.Cu"))
    (model "${KICAD8_3DMODEL_DIR}/Connector_USB.3dshapes/USB_C_Receptacle.wrl" (offset (xyz 0 0 0)) (scale (xyz 1 1 1)) (rotate (xyz 0 0 0))))
  (footprint "Capacitor_THT:CP_Radial_D8.0mm" (layer "F.Cu") (at 110 115)
    (property "Reference" "C1") (property "Value" "470u") (property "Height" "11")
    (fp_circle (center 0 0) (end 4.25 0) (stroke (width 0.05) (type default)) (layer "F.CrtYd"))
    (pad "1" thru_hole circle (at 0 0) (size 1.6 1.6) (drill 0.8) (layers "*.Cu")))
  (footprint "Resistor_SMD:R_0603" (layer "B.Cu") (at 120 110)
    (property "Reference" "R1") (property "Value" "10k")
    (fp_rect (start -1.5 -0.75) (end 1.5 0.75) (layer "B.CrtYd")))
  (footprint "Custom:Turned" (layer "F.Cu") (at 130 110 90)
    (property "Reference" "U1") (property "Value" "Turned")
    (pad "" np_thru_hole circle (at 2 0) (size 2.2 2.2) (drill 2.2) (layers "*.Cu")))
)
"""

GERBER = """G04 outline*
%FSLAX46Y46*%
%MOMM*%
%ADD10C,0.050000*%
D10*
X100000000Y-100000000D02*
X150000000Y-100000000D01*
X150000000Y-130000000D01*
X100000000Y-130000000D01*
X100000000Y-100000000D01*
M02*
"""


class Reader(unittest.TestCase):
    def test_parse(self):
        t = kicad.parse('(a (b 1 "two \\" words") c)')
        self.assertEqual(t, [["a", ["b", "1", 'two " words'], "c"]])
        self.assertIsInstance(t[0][2], kicad.Sym)
        self.assertNotIsInstance(t[0][1][2], kicad.Sym)
        with self.assertRaises(ValueError):
            kicad.parse("(a (b)")
        with self.assertRaises(ValueError):
            kicad.parse('(a "b)')

    def test_board(self):
        b = kicad.read_board(BOARD, "/x/test.kicad_pcb")
        self.assertEqual(b["thickness"], 1.6)
        self.assertEqual(b["copperLayers"], 2)
        self.assertEqual(b["title"], "Test board")
        self.assertEqual(b["size"], [50.0, 30.0])
        self.assertEqual(b["outline"]["box"], [100.0, -130.0, 150.0, -100.0])
        self.assertEqual(len(b["outline"]["loops"]), 1)
        self.assertEqual(len(b["outline"]["loops"][0]), 5)
        holes = sorted((h["ref"], h["at"], h["d"]) for h in b["holes"])
        self.assertEqual(holes[0], ("H1", [103.5, -103.5], 3.2))
        self.assertEqual(holes[1], ("H2", [146.5, -126.5], 3.2))
        # Turned 90° counter-clockwise: the pad 2 mm to its right ends up 2 mm up the page.
        self.assertEqual(holes[2][0], "U1")
        self.assertAlmostEqual(holes[2][1][0], 130.0, places=6)
        self.assertAlmostEqual(holes[2][1][1], -108.0, places=6)
        fps = {f["ref"]: f for f in b["footprints"]}
        self.assertEqual(fps["J1"]["courtyard"], [120.5, -131.0, 129.5, -125.5])
        self.assertEqual(fps["J1"]["height"], 3.3)
        self.assertEqual(fps["R1"]["side"], "bottom")
        self.assertIn("USB_C_Receptacle.wrl", fps["J1"]["model"]["path"])
        c1 = fps["C1"]["courtyard"]
        self.assertAlmostEqual(c1[2] - c1[0], 8.5, delta=0.1)
        edge = kicad.edge_connectors(b)
        self.assertEqual([(e["ref"], e["side"]) for e in edge], [("J1", "front")])
        self.assertAlmostEqual(edge[0]["from"], 20.5)
        self.assertAlmostEqual(edge[0]["to"], 29.5)
        self.assertAlmostEqual(edge[0]["over"], 1.0)
        self.assertEqual(kicad.screw_for(3.2), "M3")
        self.assertEqual(kicad.screw_for(2.7), "M2.5")

    def test_open_outline_refused(self):
        broken = BOARD.replace('(gr_line (start 100 130) (end 100 100) (layer "Edge.Cuts"))', "")
        with self.assertRaises(ValueError) as e:
            kicad.read_board(broken)
        self.assertIn("does not close", str(e.exception))
        with self.assertRaises(ValueError):
            kicad.read_board("(kicad_sch)")

    def test_arcs(self):
        (c, r) = kicad.circle3((0, 1), (1, 0), (0, -1))
        self.assertAlmostEqual(c[0], 0)
        self.assertAlmostEqual(r, 1)
        pts = kicad.arc_points({"type": "arc", "a": (147, -100), "m": (149.1213, -100.8787), "b": (150, -103)}, 4)
        self.assertAlmostEqual(pts[2][0], 149.1213, places=3)
        self.assertTrue(all(abs((x - 147) ** 2 + (y + 103) ** 2 - 9) < 1e-3 for (x, y) in pts))


class Orders(unittest.TestCase):
    def test_classify_and_size(self):
        files = ["b-F_Cu.gbr", "b-B_Cu.gbr", "b-Edge_Cuts.gbr", "b.drl", "b-bom.csv", "b-top-pos.csv", "notes.pdf", "b.gtl", "b.g2"]
        c = packing.classify(files)
        self.assertEqual(len(c["gerbers"]), 5)
        self.assertEqual(c["drill"], ["b.drl"])
        self.assertEqual(c["bom"], ["b-bom.csv"])
        self.assertEqual(c["cpl"], ["b-top-pos.csv"])
        self.assertEqual(c["other"], ["notes.pdf"])
        self.assertTrue(packing.is_outline("b-Edge_Cuts.gbr"))
        self.assertEqual(sum(1 for p in c["gerbers"] if packing.is_copper(p)), 4)
        self.assertEqual(packing.gerber_box(GERBER), [100.0, -130.0, 150.0, -100.0])
        self.assertEqual(packing.gerber_box(GERBER.replace("%MOMM*%", "%MOIN*%").replace("X150000000", "X102000000"))[2], 102 * 25.4)

    def test_sheet_and_zip(self):
        sheet = packing.order_sheet("oshpark", {"name": "b", "size": [50, 30], "layers": 2, "thickness": 1.6}, {"gerbers": ["x.gbr"]}, quantity=4)
        self.assertIn("50.00 x 30.00 mm", sheet)
        self.assertIn("| Quantity | 6 |", sheet)  # sets of three
        self.assertIn("Not run: kicad-cli", sheet)
        d = tempfile.mkdtemp()
        src = os.path.join(d, "a.gbr")
        with open(src, "w") as f:
            f.write(GERBER)
        z = packing.write_zip(os.path.join(d, "o.zip"), [("a.gbr", src), ("/abs/ORDER.md", b"hi")])
        with zipfile.ZipFile(z) as zf:
            self.assertEqual(zf.namelist(), ["a.gbr", "abs/ORDER.md"])
            self.assertEqual(zf.read("a.gbr").decode(), GERBER)


@unittest.skipUnless(HAVE, "FreeCAD is not importable here")
class Electronics(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from agentlink.tools import Session, build_server

        cls.dir = tempfile.mkdtemp()
        cls.session = Session(out_dir=cls.dir)
        cls.server = build_server(cls.session, lean=False)
        cls.pcb = os.path.join(cls.dir, "test.kicad_pcb")
        with open(cls.pcb, "w") as f:
            f.write(BOARD)

    def call(self, name, args=None):
        r = self.server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args or {}}})["result"]
        return r.get("isError", False), json.loads(r["content"][0]["text"])

    def sound(self, label):
        err, c = self.call("body_check", {"body": label})
        self.assertFalse(err, c)
        self.assertTrue(c["valid"] and c["closed"] and c["solids"] == 1, (label, c))
        self.assertEqual(c["narrow"], [], f"{label}: no sliver faces")
        return c

    def test_1_import(self):
        err, b = self.call("kicad_import", {"path": self.pcb})
        self.assertFalse(err, b)
        print("\nimport:", json.dumps({k: b.get(k) for k in ("board", "tall", "noHeight", "edgeConnectors", "moved")}))
        self.assertEqual(b["board"], "PCB test")
        self.assertEqual(sorted(t["ref"] for t in b["tall"]), ["C1", "J1"])
        self.assertIn("R1", b["noHeight"]["refs"])
        c = self.sound("PCB test")
        self.assertEqual(c["box"], [50, 30, 1.6])
        # The rounded corner and three holes taken off a 50 x 30 x 1.6 slab.
        slab = 50 * 30 * 1.6
        corner = (9 - 3.14159 * 9 / 4) * 1.6
        holes = 3.14159 * (2 * 1.6 ** 2 + 1.1 ** 2) * 1.6
        self.assertAlmostEqual(c["volume"], slab - corner - holes, delta=1.0)
        for lb in b["bodies"][1:]:
            self.sound(lb)
        err, e = self.call("kicad_import", {"path": "/no/such.kicad_pcb"})
        self.assertTrue(err)

    def test_2_enclosure(self):
        err, e = self.call("board_enclosure", {})
        self.assertFalse(err, e)
        print("\nenclosure:", json.dumps({k: e.get(k) for k in ("outside", "standoff", "headroom", "standoffs", "openings")}))
        self.assertEqual(len(e["standoffs"]["at"]), 2)
        self.assertEqual(e["standoffs"]["screw"], "M3")
        self.assertEqual([o["side"] for o in e["openings"]], ["front"])
        self.assertEqual(e["headroom"], 13.0)  # the 11 mm capacitor and 2 mm over it
        for lb in e["bodies"]:
            self.sound(lb)
        # Placed round the board: the board inside the box's footprint.
        st = {x["name"]: x for x in self.call("session_state")[1]["bodies"]}
        self.assertGreater(st[e["bodies"][0]]["box"][0], 50)

    def test_3_order(self):
        names = ["test-F_Cu.gbr", "test-B_Cu.gbr", "test-Edge_Cuts.gbr", "test.drl", "test-bom.csv", "test-pos.csv"]
        paths = []
        for n in names:
            p = os.path.join(self.dir, n)
            with open(p, "w") as f:
                f.write(GERBER if n.endswith(".gbr") else "x\n")
            paths.append(p)
        err, o = self.call("pcb_order_package", {"files": paths, "board": "PCB test", "vendor": "jlcpcb"})
        self.assertFalse(err, o)
        print("\norder:", json.dumps(o))
        self.assertEqual(o["board"]["size"], [50.0, 30.0])
        self.assertEqual(o["board"]["layers"], 2)
        with zipfile.ZipFile(o["package"]) as z:
            self.assertIn("ORDER.md", z.namelist())
            self.assertIn("assembly/test-pos.csv", z.namelist())
        with zipfile.ZipFile(o["gerberZip"]) as z:
            self.assertEqual(len(z.namelist()), 4)
        if not shutil.which("kicad-cli"):
            self.assertIn("not run", o["drc"])
        err, o2 = self.call("pcb_order_package", {"files": paths[3:], "size": [10, 10]})
        self.assertTrue(err)

    def test_4_channel(self):
        import Part

        o = self.session.doc.addObject("Part::Feature", "Body")
        o.Label = "Block"
        o.Shape = Part.makeBox(60, 30, 20)
        self.session.doc.recompute()
        err, c = self.call("printed_channel", {"body": "Block", "path": [[0, 15, 10], [30, 15, 10], [30, 30, 10]], "section": {"shape": "round", "size": 2.0}, "fill": "filament", "awg": 22})
        self.assertFalse(err, c)
        print("\nchannel:", json.dumps(c))
        self.assertEqual(c["ends"], ["open", "open"])
        self.sound("Block")
        self.sound("Block trace")
        self.assertAlmostEqual(c["conductor"]["wire"]["diameter"], 0.644, places=3)
        ohms = 30.0 * (c["length"] / 10) / (3.14159265 * 0.01)
        self.assertAlmostEqual(c["conductor"]["ohms"], ohms, delta=ohms * 0.01)
        self.assertTrue(44 < c["length"] < 48, c["length"])
        err, e = self.call("printed_channel", {"body": "Block", "path": [[100, 100, 100], [120, 100, 100]]})
        self.assertTrue(err)
        self.assertIn("outside", e["error"]["message"])


if __name__ == "__main__":
    unittest.main()
