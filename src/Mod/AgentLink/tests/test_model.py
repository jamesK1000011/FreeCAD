# SPDX-License-Identifier: LGPL-2.1-or-later
# The model tools on a real FreeCAD: skipped where FreeCAD cannot be imported.
import json
import math
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


@unittest.skipUnless(HAVE, "FreeCAD is not importable here")
class Model(unittest.TestCase):
    def setUp(self):
        from agentlink.tools import Session, build_server

        self.session = Session(out_dir=tempfile.mkdtemp())
        self.server = build_server(self.session, lean=False)

    def call(self, name, args=None, ok=True):
        r = self.server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args or {}}})["result"]
        err, out = r.get("isError", False), json.loads(r["content"][0]["text"])
        if ok is True:
            self.assertFalse(err, f"{name}: {out}")
        elif ok is False:
            self.assertTrue(err, f"{name} should be refused: {out}")
        return out

    def labels(self):
        return sorted(b["name"] for b in self.call("session_state")["bodies"])

    def test_boolean_query_fillet(self):
        b = self.call("part_primitive", {"kind": "box", "size": [20, 20, 10], "name": "Plate"})
        self.assertAlmostEqual(b["volume"], 4000, delta=0.01)
        self.assertTrue(b["sound"])
        self.call("part_primitive", {"kind": "cylinder", "radius": 3, "height": 20, "at": [10, 10, -5], "name": "Pin"})
        c = self.call("part_boolean", {"op": "cut", "base": "Plate", "tools": "Pin"})
        self.assertAlmostEqual(c["volume"], 4000 - math.pi * 9 * 10, delta=0.05)
        self.assertEqual(c["removed"], ["Pin"])
        self.assertEqual(self.labels(), ["Plate"])
        # Nothing to cut: refused, and nothing changes.
        self.call("part_primitive", {"kind": "box", "size": [5, 5, 5], "at": [100, 0, 0], "name": "Far"})
        e = self.call("part_boolean", {"op": "cut", "base": "Plate", "tools": ["Far"]}, ok=False)
        self.assertIn("changed nothing", e["error"]["message"])
        self.assertEqual(self.labels(), ["Far", "Plate"])
        self.call("object_delete", {"objects": "Far"})
        # The top face, the hole, then the top's outer edges rounded.
        q = self.call("shape_query", {"body": "Plate", "type": "plane", "normal": "+z"})
        self.assertEqual(q["found"], 1, q)
        top = q["items"][0]
        self.assertAlmostEqual(top["centroid"][2], 10, delta=1e-6)
        self.assertAlmostEqual(top["area"], 400 - math.pi * 9, delta=0.05)
        h = self.call("shape_query", {"body": "Plate", "type": "cylinder"})
        self.assertEqual(h["found"], 1)
        self.assertAlmostEqual(h["items"][0]["radius"], 3, delta=1e-6)
        f = self.call("part_fillet", {"body": "Plate", "radius": 1, "query": {"of": top["ref"], "type": "line"}})
        self.assertEqual(f["edges"], 4)
        self.assertLess(f["volumeChange"], 0)
        self.assertTrue(f["sound"], f)
        # One undo step each: the fillet goes, the cut stays.
        self.call("history_undo")
        self.assertAlmostEqual(self.call("body_check", {"body": "Plate"})["volume"], c["volume"], delta=0.05)
        ch = self.call("part_chamfer", {"body": "Plate", "size": 0.5, "edges": [top["ref"]]})
        self.assertIn(ch["edges"], (5, 6))  # four sides and the hole's rim (one edge or two)

    def test_shell_offset_transform(self):
        self.call("part_primitive", {"kind": "box", "size": [30, 30, 30], "name": "Cup"})
        t = self.call("part_thickness", {"body": "Cup", "thickness": 2, "query": {"normal": "+z"}})
        self.assertAlmostEqual(t["volume"], 27000 - 26 * 26 * 28, delta=1)
        self.call("part_primitive", {"kind": "box", "size": [10, 10, 10], "at": [50, 0, 0], "name": "Cube"})
        o = self.call("part_offset", {"body": "Cube", "distance": 1})
        self.assertAlmostEqual(o["volume"], 1000 + 600 + 12 * 10 * math.pi / 4 + 4 / 3 * math.pi, delta=1)
        a = self.call("part_transform", {"op": "linear", "body": "Cup", "count": 3, "step": [0, 40, 0]})
        self.assertEqual(a["solids"], 3)
        self.assertAlmostEqual(a["volume"], 3 * t["volume"], delta=3)
        m = self.call("part_transform", {"op": "mirror", "body": "Cube", "normal": "+x", "about": [0, 0, 0]})
        self.assertLess(m["box"][0], 13)
        self.assertIn("Cube mirror", self.labels())
        mv = self.call("part_transform", {"op": "move", "body": "Cube", "by": [0, 0, 5]})
        self.assertAlmostEqual(mv["placement"]["at"][2], 5, delta=1e-9)
        sc = self.call("part_transform", {"op": "scale", "body": "Cube", "factor": 2})
        self.assertAlmostEqual(sc["volume"], 8 * o["volume"], delta=5)
        p = self.call("part_primitive", {"kind": "cylinder", "radius": 1, "height": 5, "at": [100, 0, 0], "name": "Peg"})
        pol = self.call("part_transform", {"op": "polar", "body": "Peg", "count": 4, "axis": "+z", "about": [90, 0, 0]})
        self.assertEqual(pol["solids"], 4)
        self.assertAlmostEqual(pol["volume"], 4 * p["volume"], delta=0.1)

    def test_profiles(self):
        r = self.call("part_revolve", {"profile": {"points": [[5, 0, 0], [10, 0, 0], [10, 0, 10], [5, 0, 10]]}, "axis": "+z", "name": "Ring"})
        self.assertAlmostEqual(r["volume"], math.pi * (100 - 25) * 10, delta=0.5)
        lo = self.call("part_loft", {"profiles": [{"circle": {"center": [40, 0, 0], "radius": 5}}, {"circle": {"center": [40, 0, 10], "radius": 5}}], "name": "Loft"})
        self.assertAlmostEqual(lo["volume"], math.pi * 25 * 10, delta=0.5)
        sw = self.call("part_sweep", {"profile": {"circle": {"center": [0, 40, 0], "radius": 1, "normal": "+x"}}, "path": {"points": [[0, 40, 0], [20, 40, 0]]}, "name": "Rod"})
        self.assertAlmostEqual(sw["volume"], math.pi * 20, delta=0.2)
        self.call("part_primitive", {"kind": "box", "size": [20, 20, 10], "at": [0, 80, 0], "name": "Block"})
        q = self.call("shape_query", {"body": "Block", "normal": "+z"})
        boss = self.call("part_extrude", {"profile": "Block." + q["items"][0]["ref"], "length": 5, "into": "Block"})
        self.assertAlmostEqual(boss["volume"], 6000, delta=0.01)
        pocket = self.call("part_extrude", {"profile": {"points": [[5, 85, 15], [15, 85, 15], [15, 95, 15], [5, 95, 15]]}, "direction": [0, 0, -4], "into": "Block", "op": "cut"})
        self.assertAlmostEqual(pocket["volumeChange"], -400, delta=0.01)
        miss = self.call("part_extrude", {"profile": {"points": [[0, 0, 50], [1, 0, 50], [1, 1, 50]]}, "length": 1, "into": "Block", "op": "cut"}, ok=False)
        self.assertIn("changed nothing", miss["error"]["message"])
        for kind, args in [("sphere", {"radius": 2}), ("cone", {"radius": 2, "radius2": 1, "height": 3}), ("torus", {"radius": 5, "radius2": 1}), ("wedge", {}), ("prism", {"sides": 6, "radius": 2, "height": 2}), ("tube", {"radius": 3, "radius2": 2, "height": 4})]:
            out = self.call("part_primitive", {"kind": kind, "at": [0, -50, 0], **args})
            self.assertTrue(out["sound"], (kind, out))
        tube = self.call("body_check", {"body": "Tube"})
        self.assertAlmostEqual(tube["volume"], math.pi * (9 - 4) * 4, delta=0.01)


if __name__ == "__main__":
    unittest.main()
