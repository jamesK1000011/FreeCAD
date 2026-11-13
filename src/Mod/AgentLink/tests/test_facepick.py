# SPDX-License-Identifier: LGPL-2.1-or-later
# Faces picked by query: a normal with near takes the nearest face that way,
# not every face facing it (an open box's outside wall and the inside of
# the wall opposite both face +x).
import unittest

from _fc import HAVE


@unittest.skipUnless(HAVE, "FreeCAD is not importable here")
class FacePick(unittest.TestCase):
    def test_normal_with_near(self):
        import FreeCAD
        import Part

        from agentlink import facepick

        box = Part.makeBox(80, 60, 30).cut(Part.makeBox(76, 56, 29, FreeCAD.Vector(2, 2, 1)))
        both = facepick.pick(box, {"normal": [1, 0, 0]})
        self.assertEqual(len(both), 2)
        one = facepick.pick(box, {"normal": [1, 0, 0], "near": [80, 30, 15]})
        self.assertEqual(len(one), 1)
        self.assertAlmostEqual(one[0][1].BoundBox.XMin, 80, places=6)
        again = facepick.pick(box, {"normal": [1, 0, 0], "x": 80})
        self.assertEqual([n for n, _ in again], [n for n, _ in one])


if __name__ == "__main__":
    unittest.main()
