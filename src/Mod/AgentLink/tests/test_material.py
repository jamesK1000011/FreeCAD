# SPDX-License-Identifier: LGPL-2.1-or-later
# The material tools on a real FreeCAD: skipped where FreeCAD cannot be imported.
import unittest

from _fc import HAVE, add_body, caller, server


@unittest.skipUnless(HAVE, "FreeCAD is not importable here")
class Material(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import Part

        cls.session, srv = server()
        cls.call = staticmethod(caller(srv))
        add_body(cls.session, "Block", Part.makeBox(40, 20, 10))

    def test_1_list_and_get(self):
        err, r = self.call("material_list", {"words": "pla"})
        self.assertFalse(err, r)
        self.assertEqual(r["materials"][0]["id"], "pla")
        err, g = self.call("material_get", {"id": "pla"})
        self.assertFalse(err, g)
        self.assertAlmostEqual(g["density"], 1.24)
        self.assertGreater(g["E"], 1000)
        self.assertIn("typical", g["note"])
        err, steel = self.call("material_list", {"words": "steel", "limit": 5})
        print("\nsteel:", steel["found"], steel["materials"][:2])
        if steel["found"]:
            c = steel["materials"][0]
            err, g = self.call("material_get", {"id": c["id"]})
            self.assertFalse(err, g)
            self.assertGreater(g["density"], 7.0)
            self.assertGreater(g["E"], 150000)
        err, miss = self.call("material_get", {"id": "unobtainium"})
        self.assertTrue(miss)

    def test_2_set_custom_and_clear(self):
        err, r = self.call("material_set", {"custom": {"name": "Supplier PETG", "density": 1.27, "youngsMPa": 2000, "yieldMPa": 48}})
        self.assertFalse(err, r)
        self.assertEqual(r["bodies"], ["Block"])
        self.assertEqual(r["material"]["id"], "custom:supplier-petg")
        err, g = self.call("material_get", {"id": "custom:supplier-petg"})
        self.assertEqual(g["yield"], 48)
        err, m = self.call("mass_properties", {"body": "Block"})
        self.assertAlmostEqual(m["bodies"][0]["mass"], 8000 * 1.27 / 1000, places=2)
        err, c = self.call("material_clear", {})
        self.assertEqual(c["cleared"], ["Block"])
        err, bad = self.call("material_set", {"custom": {"name": "x", "density": 99}})
        self.assertTrue(bad)

    def test_3_library_card_on_the_object(self):
        err, lib = self.call("material_list", {"words": "pla", "category": "thermoplast"})
        if not lib["found"]:
            self.skipTest("no material library in this build")
        self.assertIn("PLA", lib["materials"][0]["name"])
        err, r = self.call("material_set", {"id": lib["materials"][0]["id"]})
        self.assertFalse(err, r)
        print("\nlibrary set:", r)
        self.assertTrue(r["shapeMaterial"]["Block"])
        self.call("material_set", {"id": "pla"})


if __name__ == "__main__":
    unittest.main()
