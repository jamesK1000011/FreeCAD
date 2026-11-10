# SPDX-License-Identifier: LGPL-2.1-or-later
# The printed share of a part from its wall readings, against the exact
# shell volume of boxes.
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agentlink.printmass import printed_fraction  # noqa: E402


def readings(L, W, H):
    """Readings straight in from each face of an L x W x H box, one per mm²."""
    reads, nz = [], []
    for area, t, z in ((W * H, L, 0.0), (L * H, W, 0.0), (L * W, H, 1.0)):
        n = int(2 * area)
        reads += [t] * n
        nz += [z] * n
    return reads, nz


def exact(L, W, H, side=0.9, skin=0.9, infill=0.15):
    inner = max(0, L - 2 * side) * max(0, W - 2 * side) * max(0, H - 2 * skin)
    V = L * W * H
    return (V - inner + infill * inner) / V


class PrintedFraction(unittest.TestCase):
    def test_boxes(self):
        # A flat slab, a project box's block, a cube, a bar: perimeters from
        # the sides and skins from top and bottom add up, not average out.
        for dims in ((42, 42, 5), (80, 60, 30), (20, 20, 20), (100, 10, 10), (40, 20, 10)):
            r, z = readings(*dims)
            self.assertAlmostEqual(printed_fraction(r, z, 1.8, 1.8, 0.15), exact(*dims), delta=0.01, msg=str(dims))

    def test_thin_is_solid(self):
        r, z = readings(60, 1.2, 20)
        self.assertGreater(printed_fraction(r, z, 1.8, 1.8, 0.15), 0.95)


if __name__ == "__main__":
    unittest.main()
