# SPDX-License-Identifier: LGPL-2.1-or-later
# The tools on a real FreeCAD: skipped where FreeCAD cannot be imported.
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

BRIEF = {"x": 2, "y": 1, "units": 4, "magnets": True, "dividersX": 1, "label": 10}


@unittest.skipUnless(HAVE, "FreeCAD is not importable here")
class Tools(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from agentlink.protocol import Server
        from agentlink.tools import INSTRUCTIONS, Session, make_tools

        cls.dir = tempfile.mkdtemp()
        cls.session = Session(out_dir=cls.dir)
        cls.server = Server("t", "0", make_tools(cls.session), INSTRUCTIONS)

    def call(self, name, args=None):
        r = self.server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args or {}}})["result"]
        return r.get("isError", False), json.loads(r["content"][0]["text"])

    def test_1_measure_makes_nothing(self):
        err, m = self.call("gen_make", {"params": BRIEF, "measure": True})
        self.assertFalse(err, m)
        size = m["parts"][0]["size"]
        for got, want in zip(size, [83.5, 41.5, 32.4]):
            self.assertAlmostEqual(got, want, delta=0.01)
        self.assertEqual(self.call("session_state")[1]["bodies"], [])

    def test_2_make_is_sound_and_matches_the_spec(self):
        err, made = self.call("gen_make", {"params": BRIEF, "name": "Bin"})
        self.assertFalse(err, made)
        self.assertEqual(made["bodies"], ["Bin"])
        self.assertIn("8 6x2 magnets", made["encased"]["what"])
        err, c = self.call("body_check", {"body": "Bin"})
        self.assertTrue(c["valid"] and c["closed"] and c["solids"] == 1, c)
        self.assertEqual(c["narrow"], [], "no sliver faces")
        # ScanRuler makes this brief 30233 mm³; the two must agree closely.
        self.assertAlmostEqual(c["volume"], 30233, delta=30233 * 0.01)

    def test_3_review_and_waive(self):
        err, r = self.call("part_review", {"body": "Bin", "spec": {"minWall": 0.8, "massMax": 40}})
        self.assertFalse(err, r)
        self.assertIn(r["verdict"], ("pass", "fix"))
        self.assertGreater(r["figures"]["mass"], 20)
        self.assertLess(r["figures"]["mass"], 40)
        thin = [f for f in r["findings"] if f["source"] == "wall" and f["severity"] == "error"]
        print("\nreview:", json.dumps(r["findings"]), "\nfigures:", json.dumps(r["figures"]))
        if thin:
            err, r2 = self.call("part_review", {"body": "Bin", "spec": {"minWall": 0.8, "massMax": 40}, "waive": [{"at": thin[0]["at"], "reason": "stacking lip"}]})
            print("waived:", json.dumps(r2["findings"]))
            # The run waived comes back as info, not an error.
            self.assertTrue(any(f["severity"] == "info" and "waived" in f["says"] for f in r2["findings"]), r2["findings"])
            self.assertNotIn(thin[0]["at"], [f.get("at") for f in r2["findings"] if f["severity"] == "error"])

    def test_4_export_reads_back(self):
        err, s = self.call("export_file", {"body": "Bin", "format": "step"})
        self.assertFalse(err, s)
        self.assertLess(s["verify"]["volumeDiff"], 1)
        err, m = self.call("export_file", {"body": "Bin", "format": "stl"})
        self.assertFalse(err, m)
        self.assertTrue(m["verify"]["closed"])

    def test_5_diff_and_refusals(self):
        err, d = self.call("history_diff", {"since": 0})
        self.assertTrue(d["changed"])
        self.assertIn("Bin", d["bodies"])
        err, e = self.call("gen_make", {"params": {"x": 0}})
        self.assertTrue(err)
        self.assertIn("x:", e["error"]["message"])
        err, e = self.call("body_check", {"body": "Nope"})
        self.assertTrue(err)
        self.assertIn("Bin", e["error"]["message"])


if __name__ == "__main__":
    unittest.main()
