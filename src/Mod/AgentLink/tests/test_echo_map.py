# SPDX-License-Identifier: LGPL-2.1-or-later
# The live echo's map from a tool call to the button a person would press:
# Assembly joints and CAM operations by the kind asked for.
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _fc import HAVE  # noqa: E402

if HAVE:
    from agentlink import echo


@unittest.skipUnless(HAVE, "FreeCAD is not importable here")
class EchoMap(unittest.TestCase):
    def test_kinds(self):
        self.assertEqual(echo.command_for("asm_joint", {"kind": "revolute"}), "Assembly_CreateJointRevolute")
        self.assertEqual(echo.command_for("asm_joint", {"kind": "rackpinion"}), "Assembly_CreateJointRackPinion")
        self.assertEqual(echo.command_for("cam_op", {"kind": "pocket"}), "CAM_Pocket_Shape")
        self.assertEqual(echo.command_for("cam_dressup", {"kind": "dogbone"}), "CAM_DressupDogbone")

    def test_plain(self):
        self.assertEqual(echo.command_for("bim_wall", {}), "BIM_Wall")
        self.assertEqual(echo.command_for("asm_joint", {}), "Assembly_CreateJointFixed")
        self.assertIsNone(echo.command_for("session_state", {}))

    def test_workbenches(self):
        self.assertEqual(echo._workbench_for("asm_joint"), "AssemblyWorkbench")
        self.assertEqual(echo._workbench_for("cam_op"), "CAMWorkbench")
        self.assertEqual(echo._workbench_for("bim_level"), "BIMWorkbench")


if __name__ == "__main__":
    unittest.main()
