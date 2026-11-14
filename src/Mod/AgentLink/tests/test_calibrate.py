# SPDX-License-Identifier: LGPL-2.1-or-later
# The calibration prints' plans (agentlink/calibrate.py): what each sets up,
# per firmware, and the retraction tower's G-code rewrite. Pure Python.

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from agentlink import calibrate as C  # noqa: E402


class Plans(unittest.TestCase):
    def test_every_kind(self):
        for k in C.KINDS:
            p = C.plan(k, {}, {"gcode_flavor": "marlin", "nozzle_diameter": ["0.4"]})
            self.assertTrue(p["model"].startswith(k.split("_")[0][:4]) or "/" in p["model"], k)
            self.assertTrue(p["read"])

    def test_pressure_advance_by_firmware(self):
        for flavor, line in (("klipper", "SET_PRESSURE_ADVANCE ADVANCE=0.004"), ("marlin", "M900 K0.004"), ("reprapfirmware", "M572 D0 S0.004")):
            p = C.plan("pressure_advance", {"start": 0, "end": 0.01, "step": 0.002}, {"gcode_flavor": flavor})
            self.assertEqual(len(p["gcodes"]), 6)
            self.assertTrue(p["gcodes"][2][1].startswith(line), (flavor, p["gcodes"][2]))
            self.assertAlmostEqual(p["trim"][1], 6, places=2)

    def test_temperature_blocks(self):
        p = C.plan("temperature", {"start": 230, "end": 210}, {})
        self.assertEqual([g[0] for g in p["gcodes"]], [10, 20, 30, 40])
        self.assertIn("M104 S225", p["gcodes"][0][1])
        self.assertEqual(p["slot"]["nozzle_temperature"], 230)
        with self.assertRaises(ValueError):
            C.plan("temperature", {"step": 3}, {})

    def test_flow_from_names(self):
        self.assertEqual(C.flow_for("flowrate_m5", {"linear": False, "current": 0.98}), 0.95)
        self.assertEqual(C.flow_for("flowrate_10", {"linear": False, "current": 1}), 1.1)
        self.assertAlmostEqual(C.flow_for("flowrate_0.02", {"linear": True, "current": 1.0}), 1.02)
        self.assertIsNone(C.flow_for("something", {"linear": False, "current": 1}))

    def test_too_many_steps(self):
        with self.assertRaises(ValueError):
            C.plan("pressure_advance", {"start": 0, "end": 1, "step": 0.001}, {})

    def test_retract_bands(self):
        g = "M83\n;Z:0.2\nG1 X1 Y1 E1\nG1 E-.8 F2100\nG1 X5\nG1 E.8 F2100\n;Z:1.2\nG1 E-.8 F2100\nG1 E.8\n"
        p = os.path.join(tempfile.mkdtemp(), "t.gcode")
        with open(p, "w") as f:
            f.write(g)
        n = C.retract_bands(p, [[0, 1, 0.2], [1, 2, 0.4]])
        self.assertEqual(n, 2)
        with open(p) as f:
            text = f.read()
        self.assertIn("G1 E-0.20000 F2100 ; retraction tower 0.2", text)
        self.assertIn("G1 E0.20000 F2100 ; unretract 0.2", text)
        self.assertIn("G1 E-0.40000 F2100", text)
        self.assertIn("G1 X1 Y1 E1", text)

    def test_calib_dir(self):
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "squashfs-root", "resources", "calib"))
        exe = os.path.join(d, "squashfs-root", "AppRun")
        open(exe, "w").close()
        self.assertTrue(C.calib_dir(exe).endswith(os.path.join("resources", "calib")))
        self.assertIsNone(C.calib_dir(None))


if __name__ == "__main__":
    unittest.main()
