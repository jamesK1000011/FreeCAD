# SPDX-License-Identifier: LGPL-2.1-or-later
# The parts family: baseplates, project boxes, board boxes. Skipped where
# FreeCAD cannot be imported.
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


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from agentlink.tools import Session, build_server

        cls.dir = tempfile.mkdtemp()
        cls.session = Session(out_dir=cls.dir)
        cls.server = build_server(cls.session, lean=False)

    def call(self, name, args=None):
        r = self.server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args or {}}})["result"]
        return r.get("isError", False), json.loads(r["content"][0]["text"])

    def sound(self, label):
        err, c = self.call("body_check", {"body": label})
        self.assertFalse(err, c)
        self.assertTrue(c["valid"] and c["closed"] and c["solids"] == 1, (label, c))
        self.assertEqual(c["narrow"], [], f"{label}: no sliver faces")
        return c


@unittest.skipUnless(HAVE, "FreeCAD is not importable here")
class Parts(Base):
    def test_1_baseplate_open(self):
        err, m = self.call("gen_part", {"generator": "gridfinity_baseplate", "params": {"x": 2, "y": 2}, "measure": True})
        self.assertFalse(err, m)
        for got, want in zip(m["parts"][0]["size"], [84, 84, 4.65]):
            self.assertAlmostEqual(got, want, delta=0.011)
        self.assertEqual(self.call("session_state")[1]["bodies"], [])
        err, made = self.call("gen_part", {"generator": "gridfinity_baseplate", "params": {"x": 2, "y": 2}, "name": "Plate"})
        self.assertFalse(err, made)
        self.assertEqual(made["bodies"], ["Plate"])
        c = self.sound("Plate")
        # A frame: far less than the solid block it is cut from.
        self.assertLess(c["volume"], 84 * 84 * 4.65 * 0.4)

    def test_2_baseplate_magnets(self):
        err, made = self.call("gen_part", {"generator": "gridfinity_baseplate", "params": {"x": 1, "y": 2, "style": "magnets"}, "name": "Magnet plate"})
        self.assertFalse(err, made)
        self.assertEqual(made["magnets"]["count"], 8)
        self.assertAlmostEqual(made["magnets"]["hole"], 6.2, places=3)  # 6 mm + the typical printer's 0.2
        self.assertAlmostEqual(made["parts"][0]["size"][2], 3.0 + 4.65, delta=0.011)
        self.sound("Magnet plate")
        err, e = self.call("gen_part", {"generator": "gridfinity_baseplate", "params": {"style": "magnets", "floor": 2.5}})
        self.assertTrue(err)
        self.assertIn("floor", e["error"]["message"])

    def test_3_project_box_with_bosses(self):
        self.call("fit", {"action": "calibrate", "hole": 0.3, "shaft": 0.0})
        err, made = self.call("gen_part", {"generator": "project_box", "params": {"length": 80, "width": 60, "height": 30, "bosses": True}, "name": "Box"})
        self.assertFalse(err, made)
        print("\nproject box:", json.dumps({k: made[k] for k in ("bosses", "lid", "sound")}))
        self.assertEqual(made["bodies"], ["Box", "Box lid"])
        self.assertEqual(made["printerFit"], "calibrated")
        self.assertAlmostEqual(made["bosses"]["pilot"], 2.8, places=3)  # 2.5 + 0.3
        self.assertAlmostEqual(made["bosses"]["lidHole"], 3.7, places=3)  # 3.4 + 0.3
        box, lid = self.sound("Box"), self.sound("Box lid")
        self.assertEqual(box["box"], [80, 60, 30])
        self.assertAlmostEqual(lid["box"][2], 2 + 3, delta=0.01)
        self.call("fit", {"action": "calibrate", "hole": 0.2, "shaft": 0.0})

    def test_4_project_box_round_corners_no_bosses(self):
        err, made = self.call("gen_part", {"generator": "project_box", "params": {"length": 50, "width": 40, "height": 20, "radius": 8, "bosses": True, "screw": "M2.5", "lip": 0}})
        self.assertFalse(err, made)
        for b in made["bodies"]:
            self.sound(b)

    def test_5_board_box(self):
        params = {
            "board": [60, 40], "holes": [[3.5, 3.5], [56.5, 3.5], [3.5, 36.5], [56.5, 36.5]], "headroom": 12,
            "openings": [{"side": "front", "at": 30, "width": 9, "height": 3.3}, {"side": "right", "at": 20, "width": 12, "height": 11, "z": 0}],
        }
        err, made = self.call("gen_part", {"generator": "board_box", "params": params, "name": "Enclosure"})
        self.assertFalse(err, made)
        print("\nboard box:", json.dumps({k: made.get(k) for k in ("outside", "standoffs", "openings")}))
        self.assertEqual(len(made["standoffs"]["at"]), 4)
        self.assertEqual(len(made["openings"]), 2)
        self.assertEqual(made["outside"][:2], [66, 46])
        self.sound("Enclosure")
        self.sound("Enclosure lid")
        err, e = self.call("gen_part", {"generator": "board_box", "params": {"openings": [{"side": "top", "at": 1, "width": 1, "height": 1}]}})
        self.assertTrue(err)
        self.assertIn("side", e["error"]["message"])
        err, e = self.call("gen_part", {"generator": "nope"})
        self.assertTrue(err)
        self.assertIn("gridfinity_baseplate", e["error"]["message"])

    def test_6_undo(self):
        n = len(self.call("session_state")[1]["bodies"])
        self.call("gen_part", {"generator": "project_box"})
        self.assertEqual(len(self.call("session_state")[1]["bodies"]), n + 2)
        err, u = self.call("history_undo")
        self.assertEqual(u["steps"], ["gen_part"])
        self.assertEqual(len(self.call("session_state")[1]["bodies"]), n)


class Params(unittest.TestCase):
    """The parameter checks alone, where FreeCAD is missing too."""

    @unittest.skipUnless(HAVE, "boxes imports FreeCAD")
    def test_checks(self):
        from agentlink import boxes

        p = boxes.check(boxes.PROJECT_BOX, {"length": 100})
        self.assertEqual(p["width"], 60)
        with self.assertRaises(ValueError):
            boxes.check(boxes.PROJECT_BOX, {"lenght": 100})
        with self.assertRaises(ValueError):
            boxes.check(boxes.BASEPLATE, {"style": "glued"})


if __name__ == "__main__":
    unittest.main()
