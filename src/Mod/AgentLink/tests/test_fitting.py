# SPDX-License-Identifier: LGPL-2.1-or-later
# Shapes fitted through noisy points found again.
import importlib.util
import os
import unittest

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("fitting", os.path.join(HERE, "..", "agentlink", "fitting.py"))
FT = importlib.util.module_from_spec(spec)
spec.loader.exec_module(FT)

RNG = np.random.default_rng(7)


def noise(n, s=0.01):
    return RNG.normal(0, s, (n, 3))


class Fits(unittest.TestCase):
    def test_plane(self):
        uv = RNG.uniform(-20, 20, (400, 2))
        n = FT.unit([0.1, -0.2, 1])
        u, v = FT._basis(n)
        P = np.array([5, 6, 7]) + uv[:, :1] * u + uv[:, 1:] * v + noise(400)
        f = FT.plane(P)
        self.assertGreater(abs(f["normal"] @ n), 0.9999)
        self.assertLess(f["rms"], 0.02)
        self.assertGreater(f["normal"][2], 0)

    def test_sphere(self):
        d = RNG.normal(size=(500, 3))
        d /= np.linalg.norm(d, axis=1)[:, None]
        d = d[d[:, 2] > 0]  # a cap only, as a scan sees it
        P = np.array([1, 2, 3]) + 12.5 * d + noise(len(d))
        f = FT.sphere(P)
        np.testing.assert_allclose(f["centre"], [1, 2, 3], atol=0.02)
        self.assertAlmostEqual(f["radius"], 12.5, delta=0.02)

    def test_cylinder_tilted(self):
        a = FT.unit([0.3, 0.1, 1])
        u, v = FT._basis(a)
        th = RNG.uniform(0, np.pi, 600)  # half round, as a scan sees it
        t = RNG.uniform(0, 30, 600)
        P = np.array([10, -4, 2]) + np.outer(t, a) + 6 * (np.outer(np.cos(th), u) + np.outer(np.sin(th), v)) + noise(600)
        f = FT.cylinder(P)
        self.assertGreater(abs(f["axis"] @ a), 0.9999)
        self.assertAlmostEqual(f["radius"], 6, delta=0.02)
        self.assertAlmostEqual(f["length"], 30, delta=0.3)
        self.assertLess(f["rms"], 0.02)

    def test_short_wide_cylinder(self):
        # a disc-like ring: the longest spread is not the axis
        th = RNG.uniform(0, 2 * np.pi, 500)
        t = RNG.uniform(0, 3, 500)
        P = np.column_stack([25 * np.cos(th), 25 * np.sin(th), t]) + noise(500)
        f = FT.cylinder(P)
        self.assertGreater(abs(f["axis"][2]), 0.9999)
        self.assertAlmostEqual(f["radius"], 25, delta=0.02)

    def test_too_few(self):
        with self.assertRaises(FT.FitError):
            FT.sphere([[0, 0, 0]] * 3)
        with self.assertRaises(FT.FitError):
            FT.plane([[0, 0, 0], [1, 0, 0], [2, 0, 0]])


if __name__ == "__main__":
    unittest.main()
