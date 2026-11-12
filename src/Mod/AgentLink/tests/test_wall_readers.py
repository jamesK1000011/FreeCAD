# SPDX-License-Identifier: LGPL-2.1-or-later
# Add-ons may read wall thickness for the printability check: the first that
# answers is used, a reader that fails or declines is passed over.
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
if os.environ.get("FREECAD_LIB"):
    sys.path.append(os.environ["FREECAD_LIB"])
try:
    import FreeCAD  # noqa: F401
    import Part

    HAVE = True
except ImportError:
    HAVE = False


@unittest.skipUnless(HAVE, "FreeCAD is not importable here")
class WallReaders(unittest.TestCase):
    def tearDown(self):
        from agentlink import printability

        printability.WALL_READERS.clear()

    def test_built_in_when_none(self):
        from agentlink import printability

        w = printability.wall(Part.makeBox(30, 30, 2))
        self.assertAlmostEqual(w["least"], 2.0, delta=0.05)
        self.assertNotIn("by", w)

    def test_first_answer_wins(self):
        from agentlink import printability

        def broken(shape):
            raise RuntimeError("no")

        printability.WALL_READERS.extend([broken, lambda shape: None,
                                          lambda shape: {"least": 1.9, "at": [0, 0, 0], "read": 1, "readings": [(1.9, [0, 0, 0], 1.0)], "spacing": 1.0, "by": "test"}])
        w = printability.wall(Part.makeBox(30, 30, 2))
        self.assertEqual(w["by"], "test")


if __name__ == "__main__":
    unittest.main()
