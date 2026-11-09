# SPDX-License-Identifier: LGPL-2.1-or-later
# CalculiX's files (anywhere) and the strength tools (on a real FreeCAD;
# the solve where ccx and gmsh are on PATH).
import json
import math
import shutil
import unittest

from _fc import HAVE, add_body, caller, server

from agentlink import ccx_io

FRD = """    1C
    2C                            12                                     1
 -1         1 0.00000E+00 0.00000E+00 0.00000E+00
 -3
  100CL  101 1.000000000           2                     0    1           1
 -4  DISP        4    1
 -5  D1          1    2    1    0
 -1         1 1.00000E-01-2.00000E-02 0.00000E+00
 -1         2-1.50000E+00 0.00000E+00 3.00000E-03
 -3
  100CL  101 1.000000000           2                     0    1           1
 -4  STRESS      6    1
 -5  SXX         1    4    1    1
 -1         1 1.00000E+01 0.00000E+00 0.00000E+00 0.00000E+00 0.00000E+00 0.00000E+00
 -1         2 0.00000E+00 0.00000E+00 0.00000E+00 5.00000E+00 0.00000E+00 0.00000E+00
 -3
 9999
"""


class Files(unittest.TestCase):
    def test_frd(self):
        disp, stress = ccx_io.read_frd(FRD)
        self.assertEqual(disp[2], (-1.5, 0.0, 0.003))
        self.assertEqual(len(stress[1]), 6)
        self.assertAlmostEqual(ccx_io.von_mises(stress[1]), 10.0)
        self.assertAlmostEqual(ccx_io.von_mises(stress[2]), 5 * math.sqrt(3))
        self.assertAlmostEqual(ccx_io.normal_stress(stress[1], (1, 0, 0)), 10.0)
        self.assertAlmostEqual(ccx_io.normal_stress(stress[1], (0, 0, 1)), 0.0)

    def test_mesh_and_loads(self):
        deck = "*Node, NSET=Nall\n1, 0, 0, 0\n2, 2, 0, 0\n3, 0, 2, 0\n4, 0, 0, 2\n5, 1, 0, 0\n6, 1, 1, 0\n7, 0, 1, 0\n8, 0, 0, 1\n9, 1, 0, 1\n10, 0, 1, 1\n\n*Element, TYPE=C3D10, ELSET=Evolumes\n1, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10\n"
        nodes, elems = ccx_io.read_mesh(deck)
        self.assertEqual(len(nodes), 10)
        self.assertEqual(elems[1], tuple(range(1, 11)))
        loads, area = ccx_io.nodal_loads(nodes, elems, [(1, 1)], [0, 0, -9])
        self.assertAlmostEqual(area, 2.0)
        self.assertEqual(sorted(loads), [5, 6, 7])
        self.assertAlmostEqual(sum(f[2] for f in loads.values()), -9)
        text = ccx_io.deck(deck, {"E": 3500, "nu": 0.36, "rho": 1.24}, {1, 2, 3}, loads, [(1, 2, 0.5)], [0, 0, -9810])
        for s in ("*BOUNDARY", "AGFIX, 1, 3", "*CLOAD", "1, P2, 0.5", "Evolumes, GRAV, 9810", "*EL FILE"):
            self.assertIn(s, text)


@unittest.skipUnless(HAVE, "FreeCAD is not importable here")
class Strength(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import Part

        cls.session, srv = server()
        cls.call = staticmethod(caller(srv))
        add_body(cls.session, "Beam", Part.makeBox(100, 10, 10))
        cls.call("material_set", {"id": "pla"})

    def test_1_setup_and_refusals(self):
        err, r = self.call("fea_solve")
        self.assertTrue(err)
        self.assertEqual(r["error"]["code"], "invalid_state")
        err, r = self.call("fea_setup", {"body": "Beam", "mesh": 2.5, "buildDir": "+z"})
        self.assertFalse(err, r)
        self.assertEqual(r["material"]["id"], "pla")
        err, r = self.call("fea_solve")
        self.assertTrue(err)
        self.assertIn("holds", r["error"]["message"])
        err, r = self.call("fea_hold", {"faces": {"normal": [0, 0, 1], "z": 99}})
        self.assertTrue(err)
        self.assertEqual(r["error"]["code"], "not_found")

    def test_2_cantilever(self):
        err, h = self.call("fea_hold", {"faces": {"normal": [-1, 0, 0], "x": 0}})
        self.assertFalse(err, h)
        self.assertEqual(len(h["held"]), 1)
        self.assertAlmostEqual(h["held"][0]["area"], 100.0)
        err, ld = self.call("fea_load", {"faces": {"near": [100, 5, 5]}, "force": [0, 0, -10]})
        self.assertFalse(err, ld)
        if not (shutil.which("ccx") and shutil.which("gmsh")):
            err, r = self.call("fea_solve")
            self.assertEqual(r["error"]["code"], "unavailable")
            self.skipTest("ccx and gmsh are not on PATH")
        err, r = self.call("fea_solve")
        print("\nsolve:", json.dumps(r))
        self.assertFalse(err, r)
        # Beam theory: F L³ / 3 E I = 10·100³ / (3·3500·833.3) = 1.14 mm;
        # the bending stress at the root 6 MPa (higher in the hold's corners).
        self.assertGreater(r["displacementMax"]["mm"], 0.95)
        self.assertLess(r["displacementMax"]["mm"], 1.35)
        self.assertGreater(r["displacementMax"]["at"][0], 99)
        self.assertGreater(r["vonMisesMax"]["MPa"], 4.5)
        self.assertLess(r["vonMisesMax"]["MPa"], 30)
        self.assertLess(r["vonMisesMax"]["at"][0], 15)
        self.assertAlmostEqual(r["safety"], 50 / r["vonMisesMax"]["MPa"], delta=0.02)
        self.assertIn("acrossLayers", r)
        self.assertFalse(any("large" in w for w in r["warnings"]), r["warnings"])
        self.assertEqual(r["acrossLayers"]["factor"], 0.5)
        err, again = self.call("fea_results")
        self.assertFalse(again["stale"])
        # The stress as a picture: hottest at the held root.
        import os

        out = os.environ.get("AGENTLINK_RENDERS")
        err, pic = self.call("view_render", {"map": "stress", "views": "all", "inline": False, **({"path": os.path.join(out, "beam_stress.png")} if out else {})})
        self.assertFalse(err, pic)
        print("\nstress map:", json.dumps(pic["map"]))
        self.assertLess(pic["map"]["worst"]["at"][0], 15)
        self.assertAlmostEqual(pic["map"]["max"], r["vonMisesMax"]["MPa"], delta=0.05 * r["vonMisesMax"]["MPa"])
        # Gravity alone on the same hold: a tiny sag, the right way.
        self.call("fea_load", {"gravity": True, "replace": True})
        err, results = self.call("fea_results")
        self.assertTrue(results["stale"])
        err, g = self.call("fea_solve")
        self.assertFalse(err, g)
        self.assertLess(g["displacementMax"]["mm"], 0.05)
        err, c = self.call("fea_clear")
        self.assertTrue(c["cleared"])


if __name__ == "__main__":
    unittest.main()
