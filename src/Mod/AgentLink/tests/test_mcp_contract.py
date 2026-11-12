# SPDX-License-Identifier: LGPL-2.1-or-later
# The MCP contract across every tool, on a real FreeCAD: names, schemas that
# a client can read and that match what the tools take, descriptions within
# a client's budget, read-only tools that say so, the lean listing small, and
# every tool refusing nonsense with a plain refusal — never a crash, a stack
# trace or a call it runs anyway. Summaries are printed for the log.
import json
import re
import sys
import unittest

from _pd_base import HAVE, Base

# Tools that act on things outside the document when called with nothing:
# never fuzzed here.
SKIP_FUZZ = {"doc_open", "agentlink_update", "printer_stop", "printer_pause", "printer_status", "printer_list", "patent_search"}
READ_WORDS = ("_list", "_get", "_read", "_info", "_state", "_status", "_results", "_probe", "_geometry", "_query", "_preflight", "_quantities", "_check", "_bom", "_lookup", "_identify", "_review", "_fits", "_limits")
DESC_MAX = 1500   # characters: a description longer than this costs every turn it is listed
LEAN_MAX = 90_000  # bytes: the lean listing, all of it sent at the start of a session


@unittest.skipUnless(HAVE, "FreeCAD with PartDesign and Sketcher is not importable here")
class McpContract(Base):
    def listing(self):
        return self.server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})["result"]["tools"]

    def test_names_and_schemas(self):
        tools = self.listing()
        names = [t["name"] for t in tools]
        self.assertEqual(len(names), len(set(names)))
        bad = []
        for t in tools:
            if not re.fullmatch(r"[a-z][a-z0-9_]{1,63}", t["name"]):
                bad.append(f"{t['name']}: name")
            sch = t.get("inputSchema") or {}
            if sch.get("type") != "object" or not isinstance(sch.get("properties"), dict):
                bad.append(f"{t['name']}: schema is not an object with properties")
                continue
            for r in sch.get("required", []):
                if r not in sch["properties"]:
                    bad.append(f"{t['name']}: requires {r}, which it does not list")
            if sch.get("additionalProperties") is not False:
                bad.append(f"{t['name']}: additionalProperties not false (a misspelt field would pass silently)")
            for k, v in sch["properties"].items():
                if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", k):
                    bad.append(f"{t['name']}.{k}: field name")
                if isinstance(v, dict) and v.get("type") == "array" and "items" not in v:
                    bad.append(f"{t['name']}.{k}: array without items")
            json.dumps(sch)  # serialisable
        self.assertEqual(bad, [], "\n".join(bad))

    def test_descriptions(self):
        tools = self.listing()
        long_ = sorted(((len(t["description"]), t["name"]) for t in tools), reverse=True)
        print("\nlongest descriptions:", long_[:8], file=sys.stderr)
        too = [f"{n}: {l} chars" for l, n in long_ if l > DESC_MAX]
        self.assertEqual(too, [], "\n".join(too))
        empty = [t["name"] for t in tools if len(t["description"]) < 20]
        self.assertEqual(empty, [])

    def test_read_only_tools_say_so(self):
        wrong = [n for n, t in self.server.tools.items() if any(n.endswith(w) or (w + "_") in n for w in READ_WORDS) and t.changes and n not in ("asm_check", "asm_limits", "asm_bom")]
        print("\nread-sounding tools marked as changing:", wrong, file=sys.stderr)
        # a tool that only reads must not take an undo step or a history snapshot
        self.assertEqual(wrong, [])

    def test_listing_sizes(self):
        from agentlink.tools import LEAN

        groups = {}
        for t in self.server.tools.values():
            groups.setdefault(t.group, []).append(len(json.dumps(t.listing())))
        rows = sorted(((sum(v), g, len(v)) for g, v in groups.items()), reverse=True)
        print("\nlisting bytes by group:", rows, file=sys.stderr)
        lean = sum(sum(v) for g, v in groups.items() if g in LEAN)
        print(f"lean listing: {lean} bytes over {sum(len(v) for g, v in groups.items() if g in LEAN)} tools; all: {sum(r[0] for r in rows)} bytes over {len(self.server.tools)}", file=sys.stderr)
        self.assertLess(lean, LEAN_MAX)

    def test_unknown_tool_refusal_is_short(self):
        r = self.server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "part_extrud", "arguments": {}}})["result"]
        self.assertTrue(r["isError"])
        msg = json.loads(r["content"][0]["text"])["error"]["message"]
        self.assertLess(len(msg), 600, msg)
        self.assertIn("part_extrude", msg)

    def test_misspelt_field_is_refused(self):
        err, out = self.call("part_primitive", {"kind": "box", "sise": [1, 2, 3]})
        self.assertTrue(err)
        self.assertEqual(out["error"]["code"], "invalid_input")

    def test_fuzz_every_tool(self):
        """Every tool called with nothing, and with a wrong-typed field: a
        refusal or an answer, never an unhandled failure."""
        self.ok("doc_new", {})
        self.ok("part_primitive", {"kind": "box", "size": [10, 10, 10], "name": "Fz box"})
        crashes = []
        for name, t in sorted(self.server.tools.items()):
            if name in SKIP_FUZZ or name.startswith("printer_"):
                continue
            props = t.schema.get("properties", {})
            probes = [{}]
            # one field with the wrong type: a number where a string is wanted, else a string
            for k, v in props.items():
                want = v.get("type") if isinstance(v, dict) else None
                probes.append({k: 12345 if want == "string" else "not-this"})
                break
            for args in probes:
                err, out = self.call(name, args)
                if err and out["error"]["code"] == "failed" and out["error"]["message"].startswith(f"{name} failed:"):
                    crashes.append(f"{name} {json.dumps(args)}: {out['error']['message'][:160]}")
        print(f"\nfuzz: {len(crashes)} unhandled", file=sys.stderr)
        self.assertEqual(crashes, [], "\n".join(crashes))


if __name__ == "__main__":
    unittest.main()
