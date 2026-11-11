# SPDX-License-Identifier: LGPL-2.1-or-later
# Part wires, splitting, compounds, slices, projection, mesh to solid.
import unittest

from _fc import HAVE, caller, server


@unittest.skipUnless(HAVE, "FreeCAD is not importable here")
class ModelMore(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session, srv = server()
        cls.call = staticmethod(caller(srv))

    def ok(self, n, a=None):
        err, out = self.call(n, a)
        self.assertFalse(err, f"{n}: {out}")
        return out

    def test_1_wires(self):
        self.assertAlmostEqual(self.ok("part_wire", {"kind": "helix", "pitch": 3, "height": 15, "radius": 8})["box"][2], 15, places=3)
        self.assertTrue(self.ok("part_wire", {"kind": "polygon", "sides": 6, "radius": 6, "name": "Hex"})["closed"])
        err, _ = self.call("part_wire", {"kind": "line", "from": [0, 0, 0], "to": [0, 0, 0]})
        self.assertTrue(err)

    def test_2_split(self):
        self.ok("part_primitive", {"kind": "box", "size": [40, 30, 20], "name": "Brick"})
        self.ok("part_wire", {"kind": "plane", "length": 80, "width": 80, "at": [-20, -20, 10], "name": "Cutplane"})
        r = self.ok("part_split", {"mode": "slice", "bodies": ["Brick"], "tools": ["Cutplane"], "explode": True})
        self.assertEqual([round(x["volume"]) for x in r["solids"]], [12000, 12000])
        self.ok("part_primitive", {"kind": "box", "size": [20, 20, 20], "at": [100, 0, 0], "name": "A"})
        self.ok("part_primitive", {"kind": "box", "size": [20, 20, 20], "at": [110, 10, 0], "name": "B"})
        self.assertEqual(self.ok("part_split", {"mode": "fragments", "bodies": ["A", "B"]})["pieces"], 3)

    def test_3_slices_project_face(self):
        self.ok("part_primitive", {"kind": "cylinder", "radius": 15, "height": 30, "name": "Drum"})
        s = self.ok("part_slices", {"body": "Drum", "count": 3})
        self.assertAlmostEqual(s["slices"][0]["area"], 706.858, delta=0.01)
        self.ok("part_wire", {"kind": "circle", "radius": 5, "name": "Ring"})
        self.ok("part_project", {"shape": "Ring", "onto": "Drum.Face2", "direction": [0, 0, 1]})
        self.assertGreater(self.ok("part_face", {"from": ["Hex"]})["area"], 0)

    def test_4_mesh_to_solid(self):
        self.ok("export_file", {"body": "Drum", "path": "drum-t.stl", "tolerance": 0.5})
        m = self.ok("part_from_mesh", {"file": "drum-t.stl"})
        self.assertEqual(m["solids"], 1)
        self.assertAlmostEqual(m["volume"], 21199, delta=50)


if __name__ == "__main__":
    unittest.main()
