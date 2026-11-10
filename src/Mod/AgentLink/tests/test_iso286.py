# SPDX-License-Identifier: LGPL-2.1-or-later
# ISO 286 limits against values from the standard's tables.
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agentlink import iso286 as I  # noqa: E402


class Iso286(unittest.TestCase):
    def test_limits(self):
        cases = {
            (10, "H7"): (0.015, 0.0), (10, "g6"): (-0.005, -0.014), (25, "h6"): (0.0, -0.013),
            (20, "p6"): (0.035, 0.022), (6, "js6"): (0.004, -0.004), (50, "H8"): (0.039, 0.0),
            (50, "f7"): (-0.025, -0.050), (8, "k6"): (0.010, 0.001), (100, "n6"): (0.045, 0.023),
            (3, "H7"): (0.010, 0.0), (30, "G7"): (0.028, 0.007),
        }
        for (n, code), want in cases.items():
            got = I.limits(n, code)
            self.assertAlmostEqual(got[0], want[0], places=6, msg=f"{n} {code}")
            self.assertAlmostEqual(got[1], want[1], places=6, msg=f"{n} {code}")

    def test_text_and_refusals(self):
        self.assertEqual(I.text(10, "H7", diameter=True), "⌀10 H7 (+0.015/0)")
        self.assertEqual(I.text(40, plus=0.1), "40 ±0.1")
        self.assertEqual(I.text(25, plus=0.2, minus=0.1), "25 +0.2/-0.1")
        for bad in ((10, "Q7"), (10, "H3"), (600, "H7"), (0, "h6")):
            with self.assertRaises(ValueError):
                I.limits(*bad)


if __name__ == "__main__":
    unittest.main()
