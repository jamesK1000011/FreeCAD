# SPDX-License-Identifier: LGPL-2.1-or-later
# The measure tools on a real FreeCAD: skipped where FreeCAD cannot be imported.
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
class Measure(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from agentlink.tools import Session, build_server

        cls.session = Session(out_dir=tempfile.mkdtemp())
        cls.server = build_server(cls.session, lean=False)
        cls.call(cls, "part_primitive", {"kind": "box", "size": [10, 20, 30], "name": "Block"})
        cls.call(cls, "part_primitive", {"kind": "cylinder", "radius": 5, "height": 10, "at": [30, 0, 0], "name": "Rod"})

    def call(self, name, args=None, ok=True):
        r = self.server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args or {}}})["result"]
        err, out = r.get("isError", False), json.loads(r["content"][0]["text"])
        if ok is True and err:
            raise AssertionError(f"{name}: {out}")
        if ok is False and not err:
            raise AssertionError(f"{name} should be refused: {out}")
        return out

    def test_query_and_measure(self):
        top = self.call("shape_query", {"body": "Block", "normal": "+z"})["items"][0]
        bottom = self.call("shape_query", {"body": "Block", "normal": "-z"})["items"][0]
        self.assertAlmostEqual(top["area"], 200, delta=1e-6)
        m = self.call("measure", {"a": "Block." + top["ref"], "b": "Block." + bottom["ref"]})
        self.assertAlmostEqual(m["angle"], 180, delta=1e-3)
        self.assertAlmostEqual(m["gap"], 30, delta=1e-6)
        one = self.call("measure", {"a": top["ref"], "body": "Block"})
        self.assertAlmostEqual(one["a"]["area"], 200, delta=1e-6)
        side = self.call("shape_query", {"body": "Rod", "type": "cylinder"})
        self.assertEqual(side["found"], 1)
        d = self.call("measure", {"a": "Rod." + side["items"][0]["ref"]})
        self.assertAlmostEqual(d["a"]["diameter"], 10, delta=1e-6)
        circles = self.call("shape_query", {"body": "Rod", "kind": "edge", "type": "circle", "radius": 5})
        self.assertEqual(circles["found"], 2)
        near = self.call("shape_query", {"body": "Block", "kind": "vertex", "near": {"point": [10, 20, 30], "within": 0.1}})
        self.assertEqual(near["found"], 1)
        pt = self.call("measure", {"a": [0, 0, 100], "b": "Block"})
        self.assertAlmostEqual(pt["distance"], 70, delta=1e-6)
        inside = self.call("shape_query", {"body": "Block", "inside": {"min": [-1, -1, 29], "max": [11, 21, 31]}})
        self.assertEqual(inside["found"], 1)
        bad = self.call("measure", {"a": "Block.Face99"}, ok=False)
        self.assertIn("6 faces", bad["error"]["message"])

    def test_section_mass_clearance(self):
        s = self.call("section", {"body": "Block", "normal": "+z", "offset": 15})
        self.assertAlmostEqual(s["area"], 200, delta=1e-3)
        self.assertEqual(len(s["outlines"]), 1)
        self.assertGreaterEqual(len(s["outlines"][0]["points"]), 4)
        miss = self.call("section", {"body": "Block", "offset": 99}, ok=False)
        self.assertIn("misses", miss["error"]["message"])
        mp = self.call("measure_mass", {"body": "Block", "density": 1.0})
        self.assertAlmostEqual(mp["mass"], 6.0, delta=1e-6)
        for got, want in zip(mp["centerOfMass"], [5, 10, 15]):
            self.assertAlmostEqual(got, want, delta=1e-6)
        self.assertAlmostEqual(mp["inertia"][0][0], 6 * (400 + 900) / 12, delta=0.2)
        self.assertAlmostEqual(mp["inertia"][2][2], 6 * (100 + 400) / 12, delta=0.2)
        steel = self.call("measure_mass", {"body": "Rod", "material": "steel"})
        self.assertAlmostEqual(steel["mass"], math.pi * 25 * 10 * 7.85 / 1000, delta=1e-3)
        self.call("measure_mass", {"body": "Rod", "material": "unobtainium"}, ok=False)
        c = self.call("clearance", {"a": "Block", "b": "Rod"})
        self.assertEqual(c["state"], "clear")
        self.assertAlmostEqual(c["distance"], 15, delta=1e-6)
        self.call("part_primitive", {"kind": "box", "size": [4, 4, 4], "at": [8, 8, 8], "name": "Overlap"})
        c2 = self.call("clearance", {"a": "Block", "b": "Overlap"})
        self.assertEqual(c2["state"], "interference")
        self.assertAlmostEqual(c2["interference"], 2 * 4 * 4, delta=1e-3)


if __name__ == "__main__":
    unittest.main()
