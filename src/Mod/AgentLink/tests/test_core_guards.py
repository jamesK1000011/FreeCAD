# SPDX-License-Identifier: LGPL-2.1-or-later
# The guards around the tools, no FreeCAD needed: names that are paths, app
# settings folders, documents written over, a session folder at the root,
# and numbers JSON has no words for.
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agentlink import paths  # noqa: E402
from agentlink.protocol import Server, Tool, ToolError, compact  # noqa: E402


class _T:
    def __init__(self, n):
        self.name = n


def refused(tool, args, out):
    try:
        paths.guard(_T(tool), args, out)
    except ToolError:
        return True
    return False


class Guards(unittest.TestCase):
    def setUp(self):
        self.out = tempfile.mkdtemp()

    def test_a_name_is_not_a_path(self):
        for bad in ("../../Library/LaunchAgents/x", "/tmp/x", "a\\b", "~x", ".."):
            self.assertTrue(refused("slice", {"name": bad}, self.out), bad)
        self.assertFalse(refused("slice", {"name": "Box lid"}, self.out))

    def test_app_settings_folder(self):
        self.assertTrue(refused("export_file", {"path": "~/Library/Application Support/Claude/claude_desktop_config.json"}, self.out))

    def test_existing_document_not_written_over(self):
        doc = os.path.join(os.path.expanduser("~"), f"agentlink-test-{os.getpid()}.FCStd")
        open(doc, "w").close()
        try:
            self.assertTrue(refused("export_file", {"path": doc}, self.out))
            self.assertFalse(refused("doc_save", {"path": doc}, self.out))
        finally:
            os.remove(doc)

    def test_session_folder_at_the_root(self):
        self.assertTrue(refused("export_file", {"path": "/etc/x.step"}, "/"))

    def test_no_nan_on_the_wire(self):
        t = Tool("double", "", {"type": "object", "properties": {"x": {"type": "number"}}, "additionalProperties": False}, lambda a: {"x": a["x"] * 2, "y": float("inf")})
        srv = Server("t", "1", [t])
        for arg in ('{"x": NaN}', '{"x": "inf"}'):
            r = srv.handle(json.loads('{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"double","arguments":%s}}' % arg))["result"]
            self.assertTrue(r.get("isError"), arg)
        r = srv.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "double", "arguments": {"x": 2}}})["result"]
        out = json.loads(r["content"][0]["text"], parse_constant=lambda c: self.fail(c))
        self.assertIsNone(out["y"])
        self.assertEqual(compact({"a": float("nan")}), '{"a":null}')


if __name__ == "__main__":
    unittest.main()
