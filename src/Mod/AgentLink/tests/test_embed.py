# SPDX-License-Identifier: LGPL-2.1-or-later
# Parts encased mid-print: the layer grid anywhere, the tools on FreeCAD.
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

    HAVE = True
except ImportError:
    HAVE = False

from agentlink import embeds as E  # noqa: E402


class Grid(unittest.TestCase):
    def test_layers(self):
        self.assertEqual(E.layer_at_or_above(5.2, 0.2, 0.2), (26, 5.2))
        self.assertEqual(E.layer_at_or_above(5.21, 0.2, 0.2), (27, 5.4))
        self.assertEqual(E.layer_at_or_above(0.1, 0.3, 0.2), (1, 0.3))
        self.assertEqual(E.layer_at_or_above(2.4, 0.3, 0.2), (12, 2.5))

    def test_merged(self):
        ps = E.pauses([{"id": "E1", "layer": 26, "note": "a"}, {"id": "E2", "layer": 28, "note": "b"}, {"id": "E3", "layer": 26, "note": "c"}, {"id": "E4", "layer": 26, "note": "c"}], 0.2, 0.2)
        self.assertEqual([p["afterLayer"] for p in ps], [26, 28])
        self.assertTrue(ps[0]["merged"])
        self.assertEqual(ps[0]["note"], "a; c (x2)")
        self.assertEqual(ps[0]["ids"], ["E1", "E3", "E4"])
        self.assertEqual(ps[1]["z"], 5.6)


@unittest.skipUnless(HAVE, "FreeCAD is not importable here")
class EmbedTools(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import Part

        from agentlink.tools import Session, build_server

        cls.dir = tempfile.mkdtemp()
        cls.session = Session(out_dir=cls.dir)
        cls.server = build_server(cls.session, lean=False)
        o = cls.session.doc.addObject("Part::Feature", "Body")
        o.Label = "Block"
        o.Shape = Part.makeBox(40, 40, 10)
        cls.session.doc.recompute()
        cls.vol = o.Shape.Volume

    def call(self, name, args=None):
        r = self.server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args or {}}})["result"]
        return r.get("isError", False), json.loads(r["content"][0]["text"])

    def test_1_needs_a_print_setup(self):
        err, r = self.call("hw_embed", {"body": "Block", "kind": "magnet", "size": "6x2", "at": [20, 20, 3]})
        self.assertTrue(err)
        self.assertEqual(r["error"]["code"], "invalid_state")
        err, a = self.call("setup", {"action": "add", "type": "print", "bodies": ["Block"], "settings": {"layerHeight": 0.2, "firstLayer": 0.2}})
        self.assertFalse(err, a)

    def test_2_embed_and_pauses(self):
        err, m = self.call("hw_embed", {"body": "Block", "kind": "magnet", "size": "6x2", "at": [20, 20, 3]})
        self.assertFalse(err, m)
        print("\nhw_embed:", json.dumps(m))
        self.assertAlmostEqual(m["pocket"]["size"][0], 6.4, places=3)  # 6 + 0.2 slide + 0.2 the typical printer
        self.assertEqual(m["pause"]["afterLayer"], 26)
        self.assertAlmostEqual(m["pause"]["z"], 5.2, places=3)
        self.assertAlmostEqual(m["roof"], 4.8, delta=0.02)
        err, n = self.call("hw_embed", {"body": "Block", "kind": "nut", "size": "M3", "at": [10, 10, 3], "turn": 30})
        self.assertFalse(err, n)
        self.assertEqual(n["pause"]["afterLayer"], 28)
        err, m2 = self.call("hw_embed", {"body": "Block", "kind": "magnet", "size": "6x2", "at": [30, 30, 3]})
        self.assertFalse(err, m2)
        err, p = self.call("hw_pauses", {"body": "Block"})
        self.assertFalse(err, p)
        print("hw_pauses:", json.dumps(p))
        self.assertEqual(p["count"], 2)
        self.assertEqual(p["pauses"][0]["ids"], ["E1", "E3"])
        self.assertTrue(p["pauses"][0]["merged"])
        self.assertEqual(self.session.meta["Block"]["embeds"][0]["pauseZ"], 5.2)
        from agentlink import store

        self.assertEqual(len(store.read(self.session.doc, "embeds", {})["Block"]), 3)

    def test_3_refusals(self):
        err, r = self.call("hw_embed", {"body": "Block", "kind": "magnet", "size": "6x2", "at": [20, 30, 7.5]})
        self.assertTrue(err)
        self.assertIn("roof", r["error"]["message"])
        err, r = self.call("hw_embed", {"body": "Block", "kind": "magnet", "size": "6x2", "at": [1, 20, 3]})
        self.assertTrue(err)
        self.assertIn("open the part", r["error"]["message"])
        err, r = self.call("hw_embed", {"body": "Block", "kind": "bearing", "size": "999", "at": [20, 20, 1]})
        self.assertTrue(err)
        self.assertEqual(r["error"]["code"], "invalid_input")

    def test_4_unembed_and_undo(self):
        err, r = self.call("hw_unembed", {"body": "Block", "id": "E3"})
        self.assertFalse(err, r)
        self.assertEqual(r["pocket"], "filled")
        err, lst = self.call("hw_embed_list", {"body": "Block"})
        self.assertEqual([e["id"] for e in lst["embeds"]], ["E1", "E2"])
        err, w = self.call("hw_embed", {"body": "Block", "kind": "weight", "size": "8x3", "at": [30, 10, 2]})
        self.assertFalse(err, w)
        self.call("history_undo")
        err, lst = self.call("hw_embed_list", {"body": "Block"})
        self.assertEqual([e["id"] for e in lst["embeds"]], ["E1", "E2"])
        self.assertEqual(lst["dropped"]["ids"], [w["id"]])


if __name__ == "__main__":
    unittest.main()
