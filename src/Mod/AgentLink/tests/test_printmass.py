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


try:
    import FreeCAD  # noqa: F401
    import Part

    HAVE = True
except Exception:
    HAVE = False


@unittest.skipUnless(HAVE, "FreeCAD is not importable here")
class Sliced(unittest.TestCase):
    def test_box_and_hollow(self):
        import time

        from agentlink.printmass import sliced

        setup = {"layerHeight": 0.2, "topLayers": 5, "bottomLayers": 4}
        t0 = time.time()
        solid, sparse = sliced(Part.makeBox(40, 20, 10), setup, 2)
        band = 0.45 + 0.41
        inner = (40 - 2 * band) * (20 - 2 * band) * (10 - 1.8)
        print(f"\nbox: solid {solid:.0f} sparse {sparse:.0f} exact sparse {inner:.0f} in {time.time() - t0:.2f} s")
        self.assertAlmostEqual(solid + sparse, 8000, delta=8000 * 0.02)
        self.assertAlmostEqual(sparse, inner, delta=inner * 0.05)
        # A thin-walled tray: all perimeter and skin, nothing sparse.
        tray = Part.makeBox(60, 40, 20).cut(Part.makeBox(58, 38, 19, FreeCAD.Vector(1, 1, 1)))
        solid, sparse = sliced(tray, setup, 2)
        print(f"tray: solid {solid:.0f} sparse {sparse:.0f} of {tray.Volume:.0f}")
        self.assertLess(sparse, 0.02 * tray.Volume)
