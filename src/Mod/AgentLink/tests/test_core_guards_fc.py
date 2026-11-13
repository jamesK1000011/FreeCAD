# SPDX-License-Identifier: LGPL-2.1-or-later
# The guards that need FreeCAD: the window link refusing what is not the
# protocol, settings only the person sets, extensions only they add, an
# export whose name says another format, a restart onto something not FreeCAD.
import os
import socket
import tempfile
import unittest

from _fc import HAVE, caller, server


@unittest.skipUnless(HAVE, "FreeCAD is not importable here")
class CoreGuards(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session, srv = server()
        cls.call = staticmethod(caller(srv))

    def test_web_request_never_reaches_the_tools(self):
        from agentlink import gui_link

        link = gui_link.Link(port=0)
        a, b = socket.socketpair()
        body = '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"session_state","arguments":{}}}\n'
        a.sendall(("POST / HTTP/1.1\r\nHost: 127.0.0.1:7318\r\nContent-Type: text/plain\r\n\r\n" + body).encode())
        a.shutdown(socket.SHUT_WR)
        link._read(b)
        self.assertTrue(link.inbox.empty())
        a2, b2 = socket.socketpair()
        a2.sendall(body.encode())
        a2.shutdown(socket.SHUT_WR)
        link._read(b2)
        self.assertEqual(link.inbox.qsize(), 1)

    def test_settings_the_person_sets(self):
        for path, name, value in (("BaseApp/Preferences/Mod/AgentLink", "CollabAllow", "live"), ("BaseApp/Preferences/Mod/AgentLink", "ExtensionRepos", "x/y"),
                                  ("BaseApp/Preferences/Mod/Fem/Ccx", "ccxBinaryPath", "/tmp/evil")):
            err, e = self.call("pref_set", {"path": path, "name": name, "value": value})
            self.assertTrue(err, name)
            self.assertEqual(e["error"]["code"], "not_allowed")

    def test_bool_text_is_read_as_said(self):
        p = "BaseApp/Preferences/Mod/AgentLinkTest"
        import FreeCAD

        FreeCAD.ParamGet("User parameter:" + p).SetBool("Flag", True)
        err, out = self.call("pref_set", {"path": p, "name": "Flag", "value": "false"})
        self.assertFalse(err, out)
        self.assertFalse(FreeCAD.ParamGet("User parameter:" + p).GetBool("Flag", True))
        err, _ = self.call("pref_set", {"path": p, "name": "Flag", "value": "maybe"})
        self.assertTrue(err)

    def test_extension_needs_the_person(self):
        err, e = self.call("app_extensions", {"action": "add", "repo": "someone/AgentLinkX"})
        self.assertTrue(err)
        self.assertIn("install someone/AgentLinkX", e["error"]["message"])

    def test_export_name_says_the_format(self):
        import Part

        o = self.session.doc.addObject("Part::Feature", "Cube")
        o.Shape = Part.makeBox(5, 5, 5)
        self.session.doc.recompute()
        err, e = self.call("export_file", {"body": "Cube", "path": os.path.join(tempfile.mkdtemp(), "house.FCStd")})
        self.assertTrue(err)
        err, ok = self.call("export_file", {"body": "Cube", "path": os.path.join(tempfile.mkdtemp(), "cube"), "format": "stl"})
        self.assertFalse(err, ok)
        self.assertTrue(ok["path"].endswith(".stl"))

    def test_restart_onto_a_made_up_app(self):
        from agentlink.families import app
        from agentlink.protocol import ToolError

        fake = os.path.join(tempfile.mkdtemp(), "x';touch /tmp/pwn;'.app")
        os.makedirs(os.path.join(fake, "Contents"))
        with self.assertRaises(ToolError):
            app._freecad_bundle(fake)

    def test_macros_and_file_dialogs_refused(self):
        from agentlink.families import app

        for n in ("Std_RecentMacros", "Std_Macro_3", "Part_Export", "Mesh_Import", "Std_Revert", "Std_DlgMacroExecute"):
            self.assertTrue(app.refused(n), n)
        self.assertIsNone(app.refused("Std_ViewFitAll"))


if __name__ == "__main__":
    unittest.main()
