# SPDX-License-Identifier: LGPL-2.1-or-later
# Cell addresses and contents: no FreeCAD needed.
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agentlink import cells as C  # noqa: E402


class Cells(unittest.TestCase):
    def test_columns(self):
        for n, s in [(1, "A"), (26, "Z"), (27, "AA"), (52, "AZ"), (702, "ZZ")]:
            self.assertEqual(C.col_name(n), s)
            self.assertEqual(C.col_num(s), n)
        with self.assertRaises(C.AddressError):
            C.col_name(703)

    def test_addresses(self):
        self.assertEqual(C.parse("b12"), (12, 2))
        self.assertEqual(C.parse("$C$3"), (3, 3))
        for bad in ("A0", "AAA1", "1A", "A16385", "", "width"):
            self.assertFalse(C.is_cell(bad), bad)

    def test_ranges(self):
        self.assertEqual(C.parse_range("C3:A1"), ((1, 1), (3, 3)))
        self.assertEqual(C.cells_in("A1:B2"), ["A1", "B1", "A2", "B2"])
        self.assertEqual(C.size("B2:D5"), 12)
        self.assertEqual(C.bounds(["C4", "A2", "B9"]), "A2:C9")
        self.assertIsNone(C.bounds([]))
        self.assertEqual(C.range_text((2, 2), (2, 2)), "B2")

    def test_content(self):
        self.assertEqual(C.content(12), "12")
        self.assertEqual(C.content(2.0), "2")
        self.assertEqual(C.content(0.25), "0.25")
        self.assertEqual(C.content("=A1*2"), "=A1*2")
        self.assertEqual(C.content("12", text=True), "'12")
        self.assertEqual(C.content(None), "")
        with self.assertRaises(C.AddressError):
            C.content(True)

    def test_grid(self):
        g = C.grid("B2", [[1, 2], [3, None]])
        self.assertEqual(g, {"B2": 1, "C2": 2, "B3": 3, "C3": None})

    def test_colours(self):
        self.assertEqual(C.hex_rgba("#ff0000"), (1.0, 0.0, 0.0, 1.0))
        self.assertEqual(C.rgba_hex((1.0, 0.0, 0.0, 1.0)), "#ff0000")
        self.assertEqual(C.rgba_hex(C.hex_rgba("#11223380")), "#11223380")
        with self.assertRaises(C.AddressError):
            C.hex_rgba("red")


if __name__ == "__main__":
    unittest.main()
