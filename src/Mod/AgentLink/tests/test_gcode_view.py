# SPDX-License-Identifier: LGPL-2.1-or-later
# The headless G-code preview (agentlink/gcode_view.py) on a small file
# written the way OrcaSlicer marks layers and features. No FreeCAD needed.
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agentlink import gcode_view as G  # noqa: E402

SAMPLE = """; HEADER_BLOCK_START
; estimated printing time (normal mode) = 12m 3s
; filament used [mm] = 1234.5
; HEADER_BLOCK_END
M104 S210
G90
M83
;LAYER_CHANGE
;Z:0.2
;HEIGHT:0.2
G1 Z.2 F600
M106 S0
;TYPE:Outer wall
;WIDTH:0.45
G1 X10 Y0 E0.5 F1800
G1 X10 Y10 E0.5
;TYPE:Sparse infill
;WIDTH:0.5
G1 X0 Y10 E0.5 F6000
G0 X0 Y0 F12000
;LAYER_CHANGE
;Z:0.4
;HEIGHT:0.2
G1 Z.4
M106 S255
M104 S205
;TYPE:Outer wall
;WIDTH:0.45
G2 X10 Y0 I5 J0 E0.8 F1800
;PAUSE_PRINT
M601
T1
;TYPE:Inner wall
G1 X10 Y10 E0.5
"""


class GcodeView(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".gcode")
        with os.fdopen(fd, "w") as f:
            f.write(SAMPLE)

    def tearDown(self):
        os.remove(self.path)

    def test_summary(self):
        d = G.read(self.path)
        s = G.summary(d)
        self.assertEqual(s["layers"], 2)
        self.assertEqual(s["z"], [0.2, 0.4])
        self.assertEqual(s["slicer"]["time"], "12m 3s")
        self.assertIn("Outer wall", s["features"])
        self.assertEqual(s["tools"], [0, 1])
        self.assertEqual(s["ranges"]["temperature"], [205.0, 210.0])
        self.assertEqual(s["ranges"]["fan"], [0.0, 100.0])
        self.assertTrue(any("pause" in e["event"].lower() for e in s["events"]))

    def test_layer_and_arc(self):
        d = G.read(self.path)
        ly = G.layer(d, z=0.4)
        self.assertEqual(ly["layer"], 1)
        # a half circle of radius 5 from (0,0) to (10,0), counter-clockwise flag G2: clockwise
        self.assertAlmostEqual(ly["features"]["Outer wall"]["lengthMm"], round(math.pi * 5, 1), places=1)
        self.assertEqual(G.layer(d, index=-1)["layer"], 1)
        first = G.layer(d, 0)
        self.assertAlmostEqual(first["travelMm"], 10.2, places=3)  # the 0.2 mm lift counts
        self.assertEqual(first["width"], [0.45, 0.5])

    def test_legend_and_svg(self):
        d = G.read(self.path, keep_paths=[0])
        leg = G.legend(d, "feature")
        self.assertAlmostEqual(sum(b["share"] for b in leg["bands"]), 1.0, places=2)
        self.assertEqual(G.legend(d, "speed")["view"], "speed")
        with self.assertRaises(ValueError):
            G.legend(d, "nope")
        svg = G.svg(d, 0, "feature")
        self.assertIn("<svg", svg)
        self.assertEqual(svg.count("<line"), 3)
        self.assertIn("Sparse infill", svg)
        with self.assertRaises(ValueError):
            G.svg(d, 1)


import math  # noqa: E402

if __name__ == "__main__":
    unittest.main()
