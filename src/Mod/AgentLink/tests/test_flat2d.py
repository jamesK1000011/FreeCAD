# SPDX-License-Identifier: LGPL-2.1-or-later
# 2D drawings as DXF and SVG, and the scene formats written and read back.
import importlib.util
import os
import tempfile
import unittest
import xml.etree.ElementTree as ET

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))


def load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, "..", "agentlink", f"{name}.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


F2 = load("flat2d")
MF = load("meshformats")

PLATE = [
    {"kind": "line", "points": [[0, 0], [60, 0], [60, 30], [0, 30]], "closed": True},
    {"kind": "circle", "c": [15, 15], "r": 4},
    {"kind": "arc", "c": [45, 15], "r": 6, "a0": 0, "a1": 270},
    {"kind": "line", "points": [[0, 10], [60, 10]], "layer": "hidden"},
]


class Flat(unittest.TestCase):
    def test_dxf_entities_and_layers(self):
        text = F2.dxf(PLATE)
        self.assertEqual(F2.read_dxf_counts(text), {"POLYLINE": 1, "CIRCLE": 1, "ARC": 1, "LINE": 1})
        self.assertIn("HIDDEN", text)
        self.assertIn("DASHED", text)
        self.assertTrue(text.rstrip().endswith("EOF"))
        # group codes and values alternate: an even count of lines
        self.assertEqual(len(text.splitlines()) % 2, 0)

    def test_bounds(self):
        self.assertEqual(F2.bounds(PLATE), (0, 0, 60, 30))

    def test_svg_mm_and_y_down(self):
        root = ET.fromstring(F2.svg(PLATE, margin=5))
        self.assertEqual(root.get("width"), "70.000mm")
        self.assertEqual(root.get("viewBox"), "0 0 70.000 40.000")
        ns = "{http://www.w3.org/2000/svg}"
        c = root.find(f".//{ns}circle")
        # y 15 of 30, flipped: 30 - 15 + 5
        self.assertAlmostEqual(float(c.get("cy")), 20.0)
        hid = [g for g in root.findall(f"{ns}g") if g.get("id") == "hidden"][0]
        self.assertEqual(hid.get("stroke-dasharray"), "3 1.5")
        arc = root.find(f".//{ns}path").get("d")
        self.assertIn(" 1 0 ", arc)  # 270 degrees: the large arc


class Scenes(unittest.TestCase):
    V = np.array([[x, y, z] for x in (0, 40) for y in (0, 20) for z in (0, 10)], float)
    F = np.array([[0, 1, 3], [0, 3, 2], [4, 6, 7], [4, 7, 5], [0, 4, 5], [0, 5, 1], [2, 3, 7], [2, 7, 6], [0, 2, 6], [0, 6, 4], [1, 5, 7], [1, 7, 3]])

    def test_round_trip(self):
        d = tempfile.mkdtemp()
        for ext in MF.WRITES:
            p = os.path.join(d, "two." + ext)
            MF.write(p, [{"name": "A box", "V": self.V, "F": self.F, "rgba": (1, 0, 0, 1)}, {"name": "B", "V": self.V + [100, 0, 0], "F": self.F}])
            V, F, info = MF.read(p)
            self.assertEqual(info["nodes"], 2, ext)
            self.assertEqual(len(F), 24, ext)
            np.testing.assert_allclose(V.min(axis=0), [0, 0, 0], atol=1e-4)
            np.testing.assert_allclose(V.max(axis=0), [140, 20, 10], atol=1e-4)

    def test_unknown(self):
        with self.assertRaises(MF.FormatError):
            MF.write("x.fbx", [])


if __name__ == "__main__":
    unittest.main()
