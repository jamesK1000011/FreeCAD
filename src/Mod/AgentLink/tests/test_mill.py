# SPDX-License-Identifier: LGPL-2.1-or-later
# Milled parts on a real FreeCAD: skipped where FreeCAD cannot be imported.
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


@unittest.skipUnless(HAVE, "FreeCAD is not importable here")
class MillTools(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import FreeCAD
        import Part

        from agentlink.tools import Session, build_server

        V = FreeCAD.Vector
        cls.dir = tempfile.mkdtemp()
        cls.session = Session(out_dir=cls.dir)
        cls.server = build_server(cls.session, lean=False)
        block = Part.makeBox(60, 40, 20)
        block = block.cut(Part.makeBox(20, 10, 15.01, V(10, 15, 5)))  # a pocket, sharp corners
        block = block.cut(Part.makeCylinder(1.5, 22, V(50, 30, -1)))  # Ø3 through, 20 deep
        block = block.cut(Part.makeCylinder(3, 10.01, V(45, -0.01, 8), V(0, 1, 0)))  # a blind side hole
        o = cls.session.doc.addObject("Part::Feature", "Body")
        o.Label = "Block"
        o.Shape = block.removeSplitter()
        cls.session.doc.recompute()

    def call(self, name, args=None):
        r = self.server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args or {}}})["result"]
        return r.get("isError", False), json.loads(r["content"][0]["text"])

    def test_1_three_axis(self):
        err, r = self.call("mill_check", {"body": "Block"})
        self.assertTrue(err)
        self.assertEqual(r["error"]["code"], "invalid_state")
        err, a = self.call("setup", {"action": "add", "type": "mill", "bodies": ["Block"], "settings": {"axes": "3", "minTool": 3}})
        self.assertFalse(err, a)
        err, r = self.call("mill_check", {"body": "Block", "material": "aluminium"})
        self.assertFalse(err, r)
        print("\nmill_check 3:", json.dumps(r))
        rules = [f["rule"] for f in r["findings"]]
        self.assertIn("undercut", rules)
        self.assertIn("inside-corner", rules)
        self.assertIn("deep-hole", rules)
        self.assertEqual(r["verdict"], "fix")
        self.assertEqual(r["directions"], ["+z"])
        under = [f for f in r["findings"] if f["rule"] == "undercut"][0]
        self.assertTrue(under["faces"])
        corners = [f for f in r["findings"] if f["rule"] == "inside-corner"]
        self.assertEqual(len(corners), 4, corners)

    def test_2_three_plus_two(self):
        err, r = self.call("mill_check", {"body": "Block", "directions": ["+z", "-y"]})
        self.assertFalse(err, r)
        print("\nmill_check 3+2:", json.dumps(r))
        self.assertNotIn("undercut", [f["rule"] for f in r["findings"]])
        # The pocket's floor corners run along y, but no tool comes at them from -y.
        self.assertEqual(len([f for f in r["findings"] if f["rule"] == "inside-corner"]), 4)
        err, r = self.call("mill_check", {"body": "Block", "stock": [50, 50, 50]})
        self.assertIn("stock", [f["rule"] for f in r["findings"]])

    def test_3_export(self):
        err, x = self.call("mill_export", {"body": "Block", "directions": ["+z", "-y"], "material": "aluminium"})
        self.assertFalse(err, x)
        self.assertLess(x["step"]["volumeDiff"], 1)
        with open(x["setupSheet"]["path"]) as f:
            text = f.read()
        self.assertIn("## Findings", text)
        self.assertIn("+z, -y", text)


if __name__ == "__main__":
    unittest.main()
