# SPDX-License-Identifier: LGPL-2.1-or-later
# The protocol layer alone: no FreeCAD needed.
import io
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from agentlink.protocol import Server, Tool, ToolError, serve_lines  # noqa: E402


def server():
    def echo(a):
        return {"said": a.get("x")}

    def refuse(a):
        raise ToolError("not_found", "nothing here — make one first")

    def broken(a):
        raise RuntimeError("boom")

    schema = {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"], "additionalProperties": False}
    tools = [Tool("echo", "Echo.", schema, echo), Tool("refuse", "Refuse.", {"type": "object", "properties": {}}, refuse), Tool("broken", "Break.", {"type": "object", "properties": {}}, broken)]
    return Server("t", "0", tools, "hi")


def call(s, name, args, mid=1):
    return s.handle({"jsonrpc": "2.0", "id": mid, "method": "tools/call", "params": {"name": name, "arguments": args}})


class Protocol(unittest.TestCase):
    def test_initialize_and_list(self):
        s = server()
        r = s.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        self.assertEqual(r["result"]["serverInfo"]["name"], "t")
        self.assertEqual(r["result"]["instructions"], "hi")
        self.assertIsNone(s.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))
        names = [t["name"] for t in s.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]]
        self.assertEqual(names, ["echo", "refuse", "broken"])

    def test_call_answers_compact_json(self):
        r = call(server(), "echo", {"x": "a"})["result"]
        self.assertNotIn("isError", r)
        self.assertEqual(json.loads(r["content"][0]["text"])["said"], "a")

    def test_refusals_say_what_and_why(self):
        s = server()
        for name, args, code, words in [
            ("echo", {}, "invalid_input", "input.x: required"),
            ("echo", {"x": "a", "y": 1}, "invalid_input", "input.y: not a known field"),
            ("refuse", {}, "not_found", "make one first"),
            ("broken", {}, "failed", "boom"),
            ("nope", {}, "not_found", "the tools: echo"),
        ]:
            r = call(s, name, args)["result"]
            self.assertTrue(r["isError"], name)
            e = json.loads(r["content"][0]["text"])["error"]
            self.assertEqual(e["code"], code)
            self.assertIn(words, e["message"])

    def test_lines_in_lines_out(self):
        s = server()
        inp = io.StringIO('{"jsonrpc":"2.0","id":1,"method":"ping"}\n\nnot json\n{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
        out = []
        serve_lines(s, inp.readline, out.append)
        self.assertEqual(len(out), 2)
        self.assertEqual(json.loads(out[0])["result"], {})
        self.assertEqual(json.loads(out[1])["error"]["code"], -32700)
        self.assertTrue(all(o.endswith("\n") and o.count("\n") == 1 for o in out))

    def test_types_checked_and_coerced(self):
        got = {}

        def fn(a):
            got.update(a)
            return {"ok": True}

        schema = {"type": "object", "properties": {"n": {"type": "number"}, "k": {"type": "integer"}, "b": {"type": "boolean"}, "xs": {"type": "array"}, "o": {"type": "object"}, "m": {"type": "string", "enum": ["wall", "stress"]}}, "additionalProperties": False}
        srv = Server("t", "0", [Tool("t", "t", schema, fn)])

        def call(args):
            r = srv.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "t", "arguments": args}})["result"]
            return r.get("isError", False), json.loads(r["content"][0]["text"])

        err, _ = call({"n": "2.5", "k": "3", "b": "true", "xs": "[1, 2]", "o": '{"a": 1}', "m": "Wall"})
        self.assertFalse(err)
        self.assertEqual(got, {"n": 2.5, "k": 3, "b": True, "xs": [1, 2], "o": {"a": 1}, "m": "wall"})
        err, out = call({"n": "wide"})
        self.assertTrue(err)
        self.assertIn("input.n: a number", out["error"]["message"])
        err, out = call({"m": "colour"})
        self.assertIn("one of wall, stress", out["error"]["message"])
        err, out = call({"k": 2.5})
        self.assertIn("a whole number", out["error"]["message"])


if __name__ == "__main__":
    unittest.main()


class Synonyms(unittest.TestCase):
    def test_other_names_taken(self):
        from agentlink.protocol import check_input

        sch = {"type": "object", "properties": {"center": {"type": "array"}, "object": {"type": "string"}}, "required": ["object"], "additionalProperties": False}
        args = {"centre": [0, 0, 0], "body": "Box"}
        check_input(sch, args)
        self.assertEqual(args, {"center": [0, 0, 0], "object": "Box"})
