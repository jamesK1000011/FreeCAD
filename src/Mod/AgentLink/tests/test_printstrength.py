# SPDX-License-Identifier: LGPL-2.1-or-later
# The slicer's settings read back as strength estimates (printstrength.py).

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from agentlink import printstrength as PS  # noqa: E402


class _BB:
    XLength, YLength, ZLength = 40.0, 40.0, 40.0


class Cube:
    Volume, Area, BoundBox = 64000.0, 9600.0, _BB()


class Estimates(unittest.TestCase):
    def test_more_walls_and_infill_are_stronger(self):
        weak = PS.describe({"wall_loops": "2", "sparse_infill_density": "15%", "layer_height": "0.2"}, Cube)
        strong = PS.describe({"wall_loops": "6", "sparse_infill_density": "60%", "sparse_infill_pattern": "gyroid", "layer_height": "0.2"}, Cube)
        self.assertLess(weak["strengthFactor"], strong["strengthFactor"])
        self.assertLess(weak["stiffnessFactor"], strong["stiffnessFactor"])
        self.assertLess(weak["solidShare"], strong["solidShare"])
        self.assertTrue(weak["advice"])

    def test_full_infill_is_solid(self):
        d = PS.describe({"wall_loops": 2, "sparse_infill_density": "100%"}, Cube)
        self.assertAlmostEqual(d["solidShare"], 1.0, places=3)
        self.assertGreater(d["strengthFactor"], 0.99)

    def test_layers(self):
        self.assertGreater(PS.layer_factor(0.12, 0.45), PS.layer_factor(0.3, 0.45))
        self.assertLessEqual(PS.layer_factor(0.01, 1), 0.7)

    def test_widths_by_percent(self):
        st = PS.settings({"nozzle_diameter": ["0.6"], "outer_wall_line_width": "100%"})
        self.assertAlmostEqual(st["outerWidth"], 0.6)


if __name__ == "__main__":
    unittest.main()
