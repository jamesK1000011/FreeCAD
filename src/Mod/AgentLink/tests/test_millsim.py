# SPDX-License-Identifier: LGPL-2.1-or-later
# The height-map milling simulation, without FreeCAD.
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agentlink import millsim as M  # noqa: E402


def plate_with_block():
    """A 40 x 30 plate 5 mm high with a 20 x 10 block on it to z 10."""
    def box(x0, y0, x1, y1, z):
        return [(x0, y0, z), (x1, y0, z), (x1, y1, z), (x0, y1, z)], [(0, 1, 2), (0, 2, 3)]
    V1, F1 = box(0, 0, 40, 30, 5)
    V2, F2 = box(10, 10, 30, 20, 10)
    return V1 + V2, F1 + [(a + 4, b + 4, c + 4) for a, b, c in F2]


def zigzag(z, skip_block=False, r=2.0):
    cmds = [("G0", {"X": -5, "Y": -5, "Z": 15})]
    y = 0.0
    while y <= 30:
        if skip_block and 10 - r < y < 20 + r:
            # Round the block: up and over it, rapid at a safe height.
            cmds += [("G0", {"X": -5, "Y": y}), ("G1", {"Z": z}), ("G1", {"X": 10 - r}), ("G0", {"Z": 15}), ("G0", {"X": 30 + r}), ("G1", {"Z": z}), ("G1", {"X": 45})]
        else:
            cmds += [("G0", {"X": -5, "Y": y}), ("G1", {"Z": z}), ("G1", {"X": 45})]
        cmds.append(("G0", {"Z": 15}))
        y += 3
    return cmds


class Sim(unittest.TestCase):
    def setUp(self):
        self.g = M.Grid(-5, -5, 45, 35, 0.25)
        V, F = plate_with_block()
        self.part = M.top_of_mesh(self.g, V, F)
        self.H = np.full((self.g.ny, self.g.nx), 10.0)

    def test_facing_through_the_block_gouges(self):
        removed, crashes = M.run(self.g, self.H, M.moves(zigzag(5)), 2.0)
        self.assertFalse(crashes)
        out, _ = M.compare(self.g, self.H, self.part, 0.0)
        self.assertIn("gouge", out)
        self.assertAlmostEqual(out["gouge"]["deepest"], 5.0, places=3)
        self.assertAlmostEqual(out["gouge"]["volumeMm3"], 20 * 10 * 5, delta=10)

    def test_facing_round_the_block_is_clear(self):
        removed, crashes = M.run(self.g, self.H, M.moves(zigzag(5, skip_block=True)), 2.0)
        self.assertFalse(crashes, crashes)
        out, _ = M.compare(self.g, self.H, self.part, 0.0)
        self.assertNotIn("gouge", out, out)
        self.assertLess(out.get("left", {}).get("share", 0), 0.1)

    def test_a_rapid_through_stock_is_a_crash(self):
        _, crashes = M.run(self.g, self.H, M.moves([("G0", {"X": 0, "Y": 0, "Z": 20}), ("G0", {"X": 20, "Y": 15, "Z": 5})]), 2.0)
        self.assertTrue(crashes)
        self.assertGreater(crashes[0][1], 4)

    def test_moves_from_arcs_and_drilling(self):
        segs = M.moves([("G0", {"X": 0, "Y": 0, "Z": 5}), ("G2", {"X": 10, "Y": 0, "Z": 5, "I": 5, "J": 0}), ("G99", {}), ("G81", {"X": 3, "Y": 3, "Z": -2, "R": 1})])
        arc = [s for s in segs if s[0] == "cut" and s[1][2] == 5]
        # Clockwise from (0,0) about (5,0) to (10,0): over the top, y up to 5.
        self.assertGreater(max(p[1][1] for p in arc), 4.9)
        drill = [s for s in segs if s[0] == "cut" and s[2][2] == -2]
        self.assertEqual(len(drill), 1)
        self.assertEqual(drill[0][2][:2], (3, 3))

    def test_ball_end(self):
        z = M.tip_height(np.array([0.0, 1.0, 2.0, 3.0]), 2.0, 0.0, True)
        self.assertAlmostEqual(z[0], 0.0)
        self.assertAlmostEqual(z[2], 2.0)
        self.assertTrue(np.isinf(z[3]))


if __name__ == "__main__":
    unittest.main()
