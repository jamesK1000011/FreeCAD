# SPDX-License-Identifier: LGPL-2.1-or-later
# The CalculiX deck's buckling step, its factor table and the VTU writer: no FreeCAD needed.
import os
import sys
import unittest
import xml.dom.minidom

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agentlink import ccx_io as C  # noqa: E402

DAT = """
     B U C K L I N G   F A C T O R   O U T P U T

 MODE NO       BUCKLING
                FACTOR

      1   0.1234567E+02
      2   0.2345678E+02

"""


class CcxMore(unittest.TestCase):
    def test_buckle_deck(self):
        d = C.deck("*NODE\n1, 0, 0, 0", {"E": 2000, "nu": 0.35, "rho": 1240}, {1, 2, 3}, {}, [], None, buckle=3)
        self.assertIn("*BUCKLE\n3", d)
        self.assertNotIn("*STATIC", d)
        self.assertIn("*STATIC", C.deck("*NODE\n1, 0, 0, 0", {"E": 2000, "nu": 0.35, "rho": 1240}, {1}, {}, [], None))

    def test_factors(self):
        self.assertEqual(C.read_buckling(DAT), [(1, 12.34567), (2, 23.45678)])
        self.assertEqual(C.read_buckling("nothing here"), [])

    def test_vtu(self):
        x = C.vtu([(0, 0, i) for i in range(10)], [list(range(10))], {"vm": [1.0] * 10, "u": [(0, 0, 1)] * 10})
        doc = xml.dom.minidom.parseString(x)
        self.assertEqual(doc.getElementsByTagName("Piece")[0].getAttribute("NumberOfCells"), "1")


if __name__ == "__main__":
    unittest.main()
