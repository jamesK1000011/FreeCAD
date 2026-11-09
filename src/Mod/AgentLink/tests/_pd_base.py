# SPDX-License-Identifier: LGPL-2.1-or-later
# Shared set-up for the PartDesign family tests: a server on a fresh
# session, skipped where FreeCAD, PartDesign or Sketcher cannot be imported.
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
if os.environ.get("FREECAD_LIB"):
    sys.path.append(os.environ["FREECAD_LIB"])
try:
    import FreeCAD  # noqa: F401
    import Part  # noqa: F401
    import Sketcher  # noqa: F401
    import PartDesign  # noqa: F401

    HAVE = True
except ImportError:
    HAVE = False


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from agentlink.tools import Session, build_server

        cls.dir = tempfile.mkdtemp()
        cls.session = Session(out_dir=cls.dir)
        cls.server = build_server(cls.session, lean=False)

    @classmethod
    def tearDownClass(cls):
        try:
            FreeCAD.closeDocument(cls.session.doc.Name)
        except Exception:
            pass

    def call(self, name, args=None):
        r = self.server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args or {}}})["result"]
        return r.get("isError", False), json.loads(r["content"][0]["text"])

    def ok(self, name, args=None):
        err, out = self.call(name, args)
        self.assertFalse(err, f"{name}: {out}")
        return out

    def refused(self, name, args=None):
        err, out = self.call(name, args)
        self.assertTrue(err, f"{name} should be refused: {out}")
        return out["error"]
