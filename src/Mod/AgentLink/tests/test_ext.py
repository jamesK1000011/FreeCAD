# SPDX-License-Identifier: LGPL-2.1-or-later
# Extensions (agentlink/ext.py): fetched from a git repository with the
# machine's own git, their AgentLink… folders installed beside Agent Link,
# and loaded by the next server: install(server, session) adds its tool.
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _fc import HAVE  # noqa: E402

GIT = shutil.which("git")


@unittest.skipUnless(HAVE and GIT, "FreeCAD or git is not here")
class Ext(unittest.TestCase):
    def test_sync_installs_and_loads(self):
        import FreeCAD

        from agentlink import ext
        from agentlink.tools import Session, build_server

        # A repository holding one extension folder, as a private one would.
        src = tempfile.mkdtemp()
        pkg = os.path.join(src, "AgentLinkHello", "agentlink_hello_ext")
        os.makedirs(pkg)
        with open(os.path.join(pkg, "__init__.py"), "w") as f:
            f.write("from agentlink.protocol import Tool\n\n\ndef install(server, session):\n"
                    "    server.add(Tool('hello_ext', 'Says hello.', {'type': 'object', 'properties': {}}, lambda a: {'hello': True}))\n")
        for c in (["init", "-q", "-b", "main"], ["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "x"]):
            subprocess.run([GIT, "-C", src] + c, check=True)
        # Installed into a Mod folder of its own (a copy of this add-on's).
        mod = tempfile.mkdtemp()
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        prefs = FreeCAD.ParamGet(ext.PREFS)
        old = prefs.GetString("ExtensionRepos", "")
        orig_file = ext.__file__
        try:
            prefs.SetString("ExtensionRepos", "file://" + src + "@main")
            ext.__file__ = os.path.join(mod, "AgentLink", "agentlink", "ext.py")
            out = ext.sync(cache=tempfile.mkdtemp())
            self.assertEqual(out["failed"], [], out)
            self.assertEqual(out["installed"][0]["folders"], ["AgentLinkHello"])
            self.assertTrue(os.path.isfile(os.path.join(mod, "AgentLinkHello", "agentlink_hello_ext", "__init__.py")))
            # Found by its folder beside the add-on, by no name given.
            server = build_server(Session(out_dir=tempfile.mkdtemp()), lean=False)
            r = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "hello_ext", "arguments": {}}})["result"]
            self.assertEqual(json.loads(r["content"][0]["text"])["hello"], True)
            lst = json.loads(server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "app_extensions", "arguments": {"action": "list"}}})["result"]["content"][0]["text"])
            self.assertIn("agentlink_hello_ext", lst["loaded"])
        finally:
            ext.__file__ = orig_file
            prefs.SetString("ExtensionRepos", old)
        self.assertTrue(here)

    def test_unreachable_repo_says_why(self):
        import FreeCAD

        from agentlink import ext

        prefs = FreeCAD.ParamGet(ext.PREFS)
        old = prefs.GetString("ExtensionRepos", "")
        try:
            prefs.SetString("ExtensionRepos", "file:///nowhere/at/all")
            out = ext.sync(cache=tempfile.mkdtemp())
            self.assertEqual(out["installed"], [])
            self.assertTrue(out["failed"][0]["why"])
        finally:
            prefs.SetString("ExtensionRepos", old)


if __name__ == "__main__":
    unittest.main()
