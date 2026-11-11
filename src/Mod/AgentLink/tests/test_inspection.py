# SPDX-License-Identifier: LGPL-2.1-or-later
# The inspection tools on a real FreeCAD: skipped where it cannot be imported.
import os
import unittest

from _fc import HAVE, add_body, caller, server

try:
    import Inspection  # noqa: F401

    HAVE_I = HAVE
except ImportError:
    HAVE_I = False


@unittest.skipUnless(HAVE_I, "FreeCAD with Inspection is not importable here")
class InspectionTools(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import Part

        cls.session, srv = server()
        cls.call = staticmethod(caller(srv))
        add_body(cls.session, "Nominal", Part.makeBox(60, 40, 8))
        add_body(cls.session, "Made", Part.makeBox(60.3, 40, 8.1))

    def ok(self, n, a=None):
        err, out = self.call(n, a)
        self.assertFalse(err, f"{n}: {out}")
        return out

    def test_1_run(self):
        r = self.ok("insp_run", {"actual": "Made", "nominals": ["Nominal"], "search_radius": 1, "tolerance": 0.2, "deflection": 0.5})
        self.assertGreater(r["points"], 0)
        self.assertAlmostEqual(r["max"], 0.3, delta=0.02)
        self.assertEqual(r["verdict"], "fail")
        self.assertTrue(r["worst"])
        self.assertAlmostEqual(r["worst"][0]["distance"], 0.3, delta=0.02)

    def test_2_update_and_export(self):
        r = self.ok("insp_update", {"tolerance": 0.35})
        self.assertEqual(r["verdict"], "pass")
        e = self.ok("insp_export", {})
        self.assertTrue(os.path.isfile(e["files"]["csv"]["path"]))
        self.assertEqual(self.ok("insp_list")["inspections"][0]["verdict"], "pass")

    def test_3_refusals(self):
        err, _ = self.call("insp_run", {"actual": "Made", "nominals": []})
        self.assertTrue(err)
        err, _ = self.call("insp_run", {"actual": "Made", "nominals": ["Nominal"], "search_radius": 0.1, "tolerance": 0.5})
        self.assertTrue(err)


if __name__ == "__main__":
    unittest.main()
