# SPDX-License-Identifier: LGPL-2.1-or-later
# Charts written as SVG: they parse, carry each series, and their ticks are round.
import importlib.util
import os
import unittest
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("charts", os.path.join(HERE, "..", "agentlink", "charts.py"))
CH = importlib.util.module_from_spec(spec)
spec.loader.exec_module(CH)
NS = "{http://www.w3.org/2000/svg}"


class Charts(unittest.TestCase):
    def test_ticks(self):
        self.assertEqual(CH.nice_ticks(0, 9.3), [0, 2, 4, 6, 8, 10])
        t = CH.nice_ticks(-3, 47)
        self.assertLessEqual(t[0], -3)
        self.assertGreaterEqual(t[-1], 47)

    def test_line_two_series(self):
        s = CH.svg([{"name": "a", "values": [1, 4, 9]}, {"name": "b", "values": [2, 3, 5]}], x=[0, 1, 2], title="t", xlabel="x", ylabel="y")
        root = ET.fromstring(s)
        self.assertEqual(len(root.findall(f"{NS}polyline")), 2)
        self.assertIn("a", [e.text for e in root.iter(f"{NS}text")])

    def test_bar_and_scatter(self):
        b = ET.fromstring(CH.svg([{"values": [3, -1, 2]}], x=["p", "q", "r"], kind="bar"))
        self.assertEqual(len([r for r in b.findall(f"{NS}rect")]) - 1, 3)  # the background is a rect too
        sc = ET.fromstring(CH.svg([{"points": [[0, 0], [1, 2], [3, 1]]}], kind="scatter"))
        self.assertEqual(len(sc.findall(f"{NS}circle")), 3)
        with self.assertRaises(ValueError):
            CH.svg([], kind="line")


if __name__ == "__main__":
    unittest.main()
