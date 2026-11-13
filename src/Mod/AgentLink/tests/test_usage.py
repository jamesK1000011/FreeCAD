# SPDX-License-Identifier: LGPL-2.1-or-later
# Usage kept on this side: calls and lists metered, the report read back.
import json
import os
import struct
import sys
import tempfile
import unittest
import zlib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agentlink import protocol, usage  # noqa: E402
from agentlink.protocol import Server, Tool  # noqa: E402


def png(w, h):
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))

    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IEND", b"")


class Usage(unittest.TestCase):
    def test_estimates(self):
        self.assertEqual(usage.est_tokens(350), 100)
        self.assertEqual(usage.png_tokens(png(750, 100)), 100)
        self.assertEqual(usage.png_tokens(png(3136, 1568)), round(1568 * 784 / 750))
        self.assertEqual(usage.png_tokens(b"nope"), 0)

    def test_meter_and_report(self):
        where = tempfile.mkdtemp()
        tools = [Tool("echo", "Say it back.", {"type": "object", "properties": {"x": {"type": "string"}}}, lambda a: {"x": a.get("x"), "_png": png(750, 100)}),
                 Tool("fail", "Refuse.", {"type": "object", "properties": {}}, lambda a: (_ for _ in ()).throw(protocol.ToolError("invalid_input", "no")))]
        srv = Server("t", "1", tools, "be brief")
        srv.meter = usage.Meter(where)
        srv.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        for _ in range(3):
            srv.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "echo", "arguments": {"x": "a" * 700}}})
        srv.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "fail", "arguments": {}}})
        t = srv.meter.session_totals()
        self.assertEqual(t["calls"], 4)
        self.assertEqual(t["errors"], 1)
        self.assertEqual(t["imageTokens"], 300)
        self.assertEqual(t["byTool"][0]["tool"], "echo")
        self.assertEqual(t["toolList"]["tools"], 2)
        r = usage.report(1, where)
        self.assertEqual(r["calls"], 4)
        self.assertEqual(r["sessions"], 1)
        self.assertGreater(r["toolListTokens"], 0)
        self.assertEqual(r["perDay"][0]["calls"], 4)
        json.dumps(r)


if __name__ == "__main__":
    unittest.main()
