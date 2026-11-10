# SPDX-License-Identifier: LGPL-2.1-or-later
# The guides say what the tools are: every tool a guide names exists, every
# group the instructions name is a group here, and every guide has sections.
import re
import unittest

from _fc import HAVE, server


@unittest.skipUnless(HAVE, "FreeCAD is not importable here")
class Guides(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session, cls.srv = server()

    def call(self, name, args=None):
        import json

        r = self.srv.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args or {}}})["result"]
        return r.get("isError", False), json.loads(r["content"][0]["text"])

    def test_every_tool_named_exists(self):
        names = set(self.srv.tools)
        prefixes = {n.split("_")[0] for n in names}
        err, idx = self.call("guide_read")
        self.assertFalse(err)
        missing = {}
        for g in idx["guides"]:
            text = self.call("guide_read", {"topic": g["topic"]})[1]["text"]
            used = {m for m in re.findall(r"`([a-z]+_[a-z_]+)", text) if m.split("_")[0] in prefixes}
            bad = sorted(used - names)
            if bad:
                missing[g["topic"]] = bad
        self.assertEqual(missing, {})

    def test_instructions_name_real_groups(self):
        init = self.srv.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})["result"]
        text = init["instructions"]
        groups = {t.group for t in self.srv.tools.values()}
        named = re.search(r"\(([a-z, ]+) …\)", text)
        self.assertIsNotNone(named, text)
        for g in [x.strip() for x in named.group(1).split(",")]:
            self.assertIn(g, groups | {"scan", "align"}, g)
        for t in re.findall(r"\b([a-z]+_[a-z_]+)\b", text):
            if t.split("_")[0] in {n.split("_")[0] for n in self.srv.tools} and t not in ("ToolError",):
                self.assertIn(t, self.srv.tools, t)


if __name__ == "__main__":
    unittest.main()
