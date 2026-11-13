# SPDX-License-Identifier: LGPL-2.1-or-later
# pref_import: another settings file merged in, the file's values winning,
# this FreeCAD's own kept settings kept, a backup written first.
import os
import tempfile
import unittest

from _pd_base import HAVE, Base


@unittest.skipUnless(HAVE, "FreeCAD with PartDesign and Sketcher is not importable here")
class PrefImport(Base):
    def test_merge(self):
        import FreeCAD

        g = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/AgentLinkTest")
        n = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/NotificationArea")
        g.SetInt("Kept", 1)
        g.SetInt("Changed", 7)
        g.SetInt("Added", 3)
        n.SetBool("NonIntrusiveNotificationsEnabled", True)
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "old.cfg")
            with open(f, "w") as fh:
                fh.write('<?xml version="1.0" encoding="UTF-8"?>\n<FCParameters><FCParamGroup Name="Root"><FCParamGroup Name="BaseApp"><FCParamGroup Name="Preferences">'
                         '<FCParamGroup Name="AgentLinkTest"><FCInt Name="Changed" Value="7"/><FCInt Name="Added" Value="3"/></FCParamGroup>'
                         '<FCParamGroup Name="NotificationArea"><FCBool Name="NonIntrusiveNotificationsEnabled" Value="1"/></FCParamGroup>'
                         '</FCParamGroup></FCParamGroup></FCParamGroup></FCParameters>\n')
            g.SetInt("Changed", 1)
            g.RemInt("Added")
            n.SetBool("NonIntrusiveNotificationsEnabled", False)
            out = self.ok("pref_import", {"path": f, "macros": False})
        try:
            self.assertEqual(g.GetInt("Changed"), 7)
            self.assertEqual(g.GetInt("Added"), 3)
            self.assertEqual(g.GetInt("Kept"), 1)
            self.assertFalse(n.GetBool("NonIntrusiveNotificationsEnabled", True))
            self.assertEqual(out["groups"], ["BaseApp"])
        finally:
            if out.get("backup") and os.path.exists(out["backup"]):
                os.remove(out["backup"])
            FreeCAD.ParamGet("User parameter:BaseApp/Preferences").RemGroup("AgentLinkTest")
            n.RemBool("NonIntrusiveNotificationsEnabled")

    def test_missing(self):
        self.refused("pref_import", {"path": "/nope/user.cfg"})


if __name__ == "__main__":
    unittest.main()
