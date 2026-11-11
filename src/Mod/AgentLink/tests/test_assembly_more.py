# SPDX-License-Identifier: LGPL-2.1-or-later
# Fits, poses, limits, exploded layouts and the BOM on a real FreeCAD.
import unittest

from _pd_base import HAVE, Base

try:
    import JointObject  # noqa: F401

    HAVE_A = HAVE
except ImportError:
    HAVE_A = False


@unittest.skipUnless(HAVE_A, "FreeCAD with the Assembly workbench is not importable here")
class AssemblyMore(Base):
    def test_1_build(self):
        self.ok("pd_body_new", {"name": "Plate"})
        self.ok("sketch_new", {"body": "Plate", "plane": "XY", "name": "P"})
        self.ok("sketch_add", {"sketch": "P", "items": [{"kind": "rectangle", "corner": [0, 0], "size": [60, 40]}, {"kind": "circle", "center": [30, 20], "diameter": 10}]})
        self.ok("pd_pad", {"body": "Plate", "sketch": "P", "length": 8})
        self.ok("pd_body_new", {"name": "Pin"})
        self.ok("sketch_new", {"body": "Pin", "plane": "XY", "name": "Q"})
        self.ok("sketch_add", {"sketch": "Q", "items": [{"kind": "circle", "center": [100, 0], "diameter": 9.98}]})
        self.ok("pd_pad", {"body": "Pin", "sketch": "Q", "length": 20})
        self.ok("asm_new", {"parts": ["Plate", "Pin"]})
        self.ok("asm_joint", {"kind": "cylindrical", "a": {"part": "Plate", "element": {"kind": "face", "type": "cylinder"}}, "b": {"part": "Pin", "element": {"kind": "face", "type": "cylinder"}}})

    def test_2_fits(self):
        f = self.ok("asm_fits", {"fit": "H7/g6"})["fits"][0]
        self.assertAlmostEqual(f["clearance"], 0.02, places=4)
        self.assertEqual(f["fit"]["nominal"], 10.0)
        self.assertTrue(f["fit"]["hole"]["ok"])
        self.assertFalse(f["fit"]["shaft"]["ok"])
        self.assertEqual(self.ok("asm_fits", {"fit": "H7/p6"})["fits"][0]["fit"]["type"], "interference")

    def test_3_limits_pose_sweep(self):
        self.ok("asm_limits", {"joint": "Cylindrical", "min": -5, "max": 10})
        self.ok("asm_pose", {"joint": "Cylindrical", "to": 6})
        self.refused("asm_pose", {"joint": "Cylindrical", "to": 12})
        sw = self.ok("asm_sweep", {"joint": "Cylindrical", "steps": 3})
        self.assertEqual([sw["from"], sw["to"]], [-5.0, 10.0])
        self.assertEqual(self.ok("asm_pose", {"joint": "Cylindrical", "home": True})["pose"], 0.0)

    def test_4_explode_bom(self):
        e = self.ok("asm_explode", {"axis": "z"})
        self.assertIn("Pin", e["exploded"])
        self.refused("asm_explode", {"axis": "z"})
        self.ok("asm_explode", {"collapse": True})
        b = self.ok("asm_bom")
        self.assertEqual(sorted(p["name"] for p in b["parts"]), ["Pin", "Plate"])
        self.ok("sheet_read", {"sheet": b["bom"]})


if __name__ == "__main__":
    unittest.main()
