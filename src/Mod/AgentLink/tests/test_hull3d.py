# SPDX-License-Identifier: LGPL-2.1-or-later
# The convex hull of points found again: a cube's corners among points
# inside it, two spheres' points — without scipy too.
import builtins
import importlib.util
import os
import unittest

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("hull3d", os.path.join(HERE, "..", "agentlink", "hull3d.py"))
H = importlib.util.module_from_spec(spec)
spec.loader.exec_module(H)


def no_scipy(fn):
    def run(self):
        real = builtins.__import__

        def imp(name, *a, **k):
            if name.startswith("scipy"):
                raise ImportError(name)
            return real(name, *a, **k)

        builtins.__import__ = imp
        try:
            fn(self)
        finally:
            builtins.__import__ = real
    return run


class Hull(unittest.TestCase):
    @no_scipy
    def test_cube_with_inside_points(self):
        rng = np.random.default_rng(3)
        corners = np.array([[x, y, z] for x in (0, 10) for y in (0, 10) for z in (0, 10)], float)
        P = np.vstack([corners, rng.uniform(1, 9, (300, 3))])
        V, T = H.hull(P)
        self.assertEqual(len(V), 8)
        self.assertAlmostEqual(H.volume(V, T), 1000.0, places=6)

    @no_scipy
    def test_two_spheres(self):
        rng = np.random.default_rng(4)
        d = rng.normal(size=(600, 3))
        d /= np.linalg.norm(d, axis=1)[:, None]
        P = np.vstack([d[:300] * 10, d[300:] * 5 + [30, 0, 0]])
        V, T = H.hull(P)
        vol = H.volume(V, T)
        # a capsule-like hull: more than both balls, less than their bounding box
        self.assertGreater(vol, 4188 + 523)
        self.assertLess(vol, 45 * 20 * 20)
        # every point inside or on it
        a, b, c = V[T[:, 0]], V[T[:, 1]], V[T[:, 2]]
        n = np.cross(b - a, c - a)
        self.assertTrue(((P[:, None, :] - a[None]) * n[None]).sum(-1).max() < 1e-6)

    @no_scipy
    def test_flat(self):
        with self.assertRaises(H.HullError):
            H.hull([[0, 0, 0], [1, 0, 0], [0, 1, 0], [1, 1, 0]])


if __name__ == "__main__":
    unittest.main()
