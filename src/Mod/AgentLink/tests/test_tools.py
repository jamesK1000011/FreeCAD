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
        from agentlink.tools import Session, build_server

        cls.dir = tempfile.mkdtemp()
        cls.session = Session(out_dir=cls.dir)
        cls.server = build_server(cls.session, lean=False)

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
        err, d = self.call("dfm_check", {"body": "Bin"})
        self.assertFalse(any(f["rule"] == "bridge" for f in d["findings"]), "no bridge: the strip between the feet is a short span")

    def test_3_review_follows_the_setup(self):
        # In no setup: printing findings are information only.
        err, r = self.call("part_review", {"body": "Bin", "spec": {"minWall": 0.8}})
        self.assertFalse(err, r)
        self.assertEqual(r["process"], "unknown")
        self.assertIn("ifPrinted", r)
        # Recorded as printed, with the spec kept: printing findings judge it.
        err, a = self.call("setup", {"action": "add", "type": "print", "bodies": ["Bin"], "settings": {"printer": "h2d", "nozzle": 0.4, "layerHeight": 0.2}})
        self.assertFalse(err, a)
        self.assertEqual(a["added"]["id"], "S1")
        err, dup = self.call("setup", {"action": "add", "type": "print", "bodies": ["Bin"]})
        self.assertTrue(dup)
        self.assertIn("S1", dup["error"]["message"])
        self.call("part_spec", {"spec": {"minWall": 0.8, "massMax": 40}, "material": "pla"})
        err, r = self.call("part_review", {"body": "Bin"})
        self.assertEqual(r["process"], "printed")
        print("\nreview:", json.dumps(r["findings"]), "\nfigures:", json.dumps(r["figures"]), "\nprinting:", json.dumps(r.get("printing")))
        self.assertEqual(r["printing"]["pauses"][0]["afterLayer"], 12)
        lip = [f for f in r["findings"] if "thin-wall" in f["says"] and f["at"][2] >= 28]
        self.assertTrue(lip, "the lip's thin top is found")
        err, r2 = self.call("part_review", {"body": "Bin", "waive": [{"rule": "thin-wall", "at": f["at"], "reason": "stacking lip"} for f in lip]})
        print("waived:", json.dumps(r2["findings"]), r2["verdict"])
        self.assertFalse(any("thin-wall" in f["says"] and f["at"][2] >= 28 for f in r2["findings"]))
        # The waivers are kept: a check now says them waived.
        err, d = self.call("dfm_check", {"body": "Bin"})
        self.assertTrue(any(f.get("waived") for f in d["findings"]), d["findings"])
        self.assertEqual(d["setup"], "Print (S1)")

    def test_35_fits_and_hardware(self):
        err, f = self.call("fit", {"action": "size", "nominal": 8.0, "of": "hole"})
        self.assertFalse(err, f)
        self.assertAlmostEqual(f["model"]["slide"], 8.4, places=3)  # 8 + 0.2 gap + 0.2 the typical printer
        err, c = self.call("fit", {"action": "calibrate", "hole": 0.15, "shaft": 0.05})
        err, f = self.call("fit", {"action": "size", "nominal": 8.0, "of": "shaft", "kind": "press"})
        self.assertAlmostEqual(f["model"]["press"], 7.95, places=3)
        err, st = self.call("fit", {"action": "stack", "items": [{"nominal": 10, "tol": 0.1}, {"nominal": -9.6, "tol": 0.1}], "target": [0.1, 0.8]})
        self.assertEqual(st["worstCase"], [0.2, 0.6])
        self.assertTrue(st["worstCaseOk"])
        self.call("fit", {"action": "calibrate", "hole": 0.2, "shaft": 0.0})
        before = self.call("session_state")[1]["bodies"][0]["volume"]
        err, h = self.call("hw_hole", {"body": "Bin", "kind": "clearance", "size": "M3", "at": [-20, -5, 5.75], "dir": "-z", "depth": "through"})
        self.assertFalse(err, h)
        print("\nhole:", json.dumps(h))
        self.assertTrue(h["sound"])
        self.assertAlmostEqual(h["sizes"]["clearance"], 3.6, places=3)
        self.assertAlmostEqual(h["removed"], 3.14159 * 1.8 ** 2 * 5.75, delta=2.0)
        self.assertLess(self.call("session_state")[1]["bodies"][0]["volume"], before)
        err, miss = self.call("hw_hole", {"body": "Bin", "kind": "clearance", "size": "M3", "at": [0, 0, 200]})
        self.assertTrue(miss)
        self.assertIn("cut nothing", miss["error"]["message"])

    def test_4_export_reads_back(self):
        err, s = self.call("export_file", {"body": "Bin", "format": "step"})
        self.assertFalse(err, s)
        self.assertLess(s["verify"]["volumeDiff"], 1)
        err, m = self.call("export_file", {"body": "Bin", "format": "stl"})
        self.assertFalse(err, m)
        self.assertTrue(m["verify"]["closed"])

    def test_45_slice_with_the_magnet_pause(self):
        from agentlink import slicer

        if not slicer.find_slicer():
            self.skipTest("no slicer on this machine")
        err, r = self.call("slice", {"body": "Bin"})
        self.assertFalse(err, r)
        print("\nslice:", json.dumps(r))
        self.assertGreater(r["layers"], 100)
        self.assertTrue(r["filamentG"] and 10 < r["filamentG"] < 60, r)
        self.assertEqual(len(r["pauses"]), 1)
        self.assertGreater(r["pauses"][0]["z"], 2.4)
        self.assertLess(r["pauses"][0]["z"], 2.8)
        # The review's printed weight within a sixth of the slicer's.
        err, rv = self.call("part_review", {"body": "Bin"})
        print("review mass:", rv["figures"]["mass"], "slicer:", r["filamentG"], "+ encased", rv["figures"].get("encasedMass"))
        self.assertAlmostEqual(rv["figures"]["mass"], r["filamentG"] + rv["figures"].get("encasedMass", 0), delta=(r["filamentG"] + 3.4) / 10)
        with open(r["gcode"]) as f:
            self.assertIn("M601 ; Insert 8 6x2 magnets", f.read())

    def test_6_undo_batch_groups_guides(self):
        state = lambda: self.call("session_state")[1]
        n = len(state()["bodies"])
        err, m = self.call("gen_make", {"params": {"x": 1, "y": 1, "units": 2}, "name": "Small"})
        self.assertFalse(err, m)
        self.assertEqual(len(state()["bodies"]), n + 1)
        err, u = self.call("history_undo")
        self.assertFalse(err, u)
        self.assertEqual(u["steps"], ["gen_make"])
        self.assertEqual(len(state()["bodies"]), n)
        self.call("history_redo")
        self.assertEqual(len(state()["bodies"]), n + 1)
        self.call("history_undo")
        # A batch stands or falls whole.
        err, b = self.call("batch", {"calls": [{"tool": "gen_make", "arguments": {"params": {"x": 1, "y": 1, "units": 2}, "name": "B1"}}, {"tool": "gen_make", "arguments": {"params": {"x": 99}}}]})
        self.assertFalse(err, b)
        self.assertEqual(b["stopped"], 1)
        self.assertEqual(b["undone"], 1)
        self.assertEqual(len(state()["bodies"]), n)
        err, g = self.call("tool_groups")
        self.assertIn("core", [x["group"] for x in g["groups"]])
        err, gi = self.call("guide_read")
        self.assertTrue(gi["guides"])
        lst = self.server.handle({"jsonrpc": "2.0", "id": 9, "method": "resources/list"})["result"]["resources"]
        self.assertTrue(any(r["uri"].startswith("agentlink://guide/") for r in lst))

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
