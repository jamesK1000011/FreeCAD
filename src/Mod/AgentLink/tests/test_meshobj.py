# SPDX-License-Identifier: LGPL-2.1-or-later
# Document meshes: made, evaluated, changed, cut, segmented.
import unittest

from _fc import HAVE, caller, server

try:
    import Mesh  # noqa: F401
    import MeshPart  # noqa: F401

    HAVE_M = HAVE
except ImportError:
    HAVE_M = False


@unittest.skipUnless(HAVE_M, "FreeCAD with Mesh and MeshPart is not importable here")
class MeshObj(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session, srv = server()
        cls.call = staticmethod(caller(srv))
        cls.call("part_primitive", {"kind": "box", "size": [40, 30, 20], "name": "Brick"})
        cls.call("part_primitive", {"kind": "sphere", "radius": 12, "at": [40, 15, 20], "name": "Ball"})

    def ok(self, n, a=None):
        err, out = self.call(n, a)
        self.assertFalse(err, f"{n}: {out}")
        return out

    def test_1_make_and_evaluate(self):
        b = self.ok("meshobj_from_shape", {"body": "Brick", "method": "mefisto", "maxLength": 3})
        self.assertTrue(b["solid"])
        self.ok("meshobj_from_shape", {"body": "Ball", "deflection": 0.05})
        self.assertEqual(self.ok("meshobj_info", {"mesh": "Brick mesh"})["verdict"], "clean")

    def test_2_boolean_and_shape(self):
        d = self.ok("meshobj_boolean", {"a": "Brick mesh", "b": "Ball mesh", "op": "difference"})
        self.assertTrue(d["solid"])
        self.assertAlmostEqual(d["volume"], 24000 - 7238.23 / 4, delta=40)
        t = self.ok("meshobj_trim", {"mesh": "Ball mesh", "at": [40, 15, 20], "normal": [0, 0, 1], "close": True, "copy": True})
        self.assertAlmostEqual(t["volume"], 7195.55 / 2, delta=20)
        seg = self.ok("meshobj_segments", {"mesh": "Brick mesh"})
        self.assertEqual(seg["segments"], 6)

    def test_3_read(self):
        c = self.ok("meshobj_curvature", {"mesh": "Ball mesh"})
        self.assertAlmostEqual(c["medianRadius"], 12, delta=0.2)
        s = self.ok("meshobj_sections", {"mesh": "Ball mesh", "count": 1})
        self.assertAlmostEqual(s["slices"][0]["length"], 2 * 3.14159 * 12, delta=1.0)


if __name__ == "__main__":
    unittest.main()
