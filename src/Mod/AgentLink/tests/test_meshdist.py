# SPDX-License-Identifier: LGPL-2.1-or-later
# The nearest triangle within a reach: exact against brute force, and the
# signed distance to a box's faces.
import importlib.util
import os
import unittest

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("meshdist", os.path.join(HERE, "..", "agentlink", "meshdist.py"))
M = importlib.util.module_from_spec(spec)
spec.loader.exec_module(M)


def box(n=6, s=10.0):
    V, F = [], []
    g = np.linspace(-s, s, n + 1)
    for ax in range(3):
        for sign in (-1, 1):
            u, v = (ax + 1) % 3, (ax + 2) % 3
            base = len(V)
            for a in g:
                for b in g:
                    p = [0.0, 0.0, 0.0]
                    p[ax], p[u], p[v] = sign * s, a, b
                    V.append(p)
            for i in range(n):
                for j in range(n):
                    a, b, c, d = base + i * (n + 1) + j, base + (i + 1) * (n + 1) + j, base + (i + 1) * (n + 1) + j + 1, base + i * (n + 1) + j + 1
                    F += [[a, b, c], [a, c, d]] if sign > 0 else [[a, c, b], [a, d, c]]
    return np.array(V), np.array(F)


class Nearest(unittest.TestCase):
    def test_box(self):
        V, F = box()
        P = np.random.default_rng(0).uniform(-12, 12, (20000, 3))
        best, d, s = M.nearest(P, V, F, 1.0)
        q = np.abs(P) - 10
        true = np.linalg.norm(np.maximum(q, 0), axis=1) + np.minimum(q.max(1), 0)
        near = np.abs(true) <= 1
        self.assertTrue((best[near] >= 0).all())
        self.assertTrue((best[~near] < 0).all())
        self.assertLess(np.abs(s[near] - true[near]).max(), 1e-9)

    def test_brute(self):
        rng = np.random.default_rng(1)
        V = rng.uniform(-5, 5, (60, 3))
        F = rng.integers(0, 60, (40, 3))
        F = F[(F[:, 0] != F[:, 1]) & (F[:, 1] != F[:, 2]) & (F[:, 0] != F[:, 2])]
        P = rng.uniform(-6, 6, (500, 3))
        _b, d, _s = M.nearest(P, V, F, 100.0)
        T = V[F]
        for i in range(0, 500, 7):
            Q = M.closest_on_triangles(np.repeat(P[i][None], len(T), 0), T[:, 0], T[:, 1], T[:, 2])
            self.assertAlmostEqual(d[i], np.linalg.norm(P[i] - Q, axis=1).min(), 9)


if __name__ == "__main__":
    unittest.main()
