# SPDX-License-Identifier: LGPL-2.1-or-later
# slice_with: the G-code reading and pausing anywhere; slicing on FreeCAD
# with whichever slicers are installed (PrusaSlicer in the bake-off; OrcaSlicer
# and Bambu Studio are not installed there, so they are not run by these tests).
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

from agentlink import slicers  # noqa: E402

BAMBU = "; total filament weight [g] : 3.21\n; total estimated time: 12m 3s\n; CHANGE_LAYER\n; Z_HEIGHT: 0.2\nG1 Z.2\n; CHANGE_LAYER\n; Z_HEIGHT: 0.4\nG1 Z.4\n; CHANGE_LAYER\n; Z_HEIGHT: 0.6\nG1 Z.6\n"
PRUSA = ";LAYER_CHANGE\n;Z:0.2\nG1 Z.2\n;LAYER_CHANGE\n;Z:0.4\nG1 Z.4\n; filament used [g] = 1.50\n; estimated printing time (normal mode) = 5m 2s\n"


class GcodeAnySlicer(unittest.TestCase):
    def write(self, text):
        p = os.path.join(tempfile.mkdtemp(), "x.gcode")
        with open(p, "w") as f:
            f.write(text)
        return p

    def test_bambu_marks(self):
        p = self.write(BAMBU)
        r = slicers.read_gcode(p)
        self.assertEqual(r["layers"], 3)
        self.assertEqual(r["filamentG"], 3.21)
        self.assertEqual(r["time"], "12m 3s")
        done = slicers.insert_pauses(p, [{"z": 0.4, "note": "Insert magnet"}], "M400 U1")
        self.assertEqual(done, [{"z": 0.6, "note": "Insert magnet"}])
        with open(p) as f:
            text = f.read()
        self.assertLess(text.index("M400 U1 ; Insert magnet"), text.index("; Z_HEIGHT: 0.6"))
        self.assertGreater(text.index("M400 U1"), text.index("; Z_HEIGHT: 0.4"))

    def test_prusa_marks(self):
        p = self.write(PRUSA)
        r = slicers.read_gcode(p)
        self.assertEqual((r["layers"], r["filamentG"]), (2, 1.5))
        self.assertEqual(slicers.insert_pauses(p, [{"z": 0.2, "note": "n"}], "M601"), [{"z": 0.4, "note": "n"}])

    def test_minimal_profiles(self):
        d = tempfile.mkdtemp()
        out = slicers.minimal_profiles({"layerHeight": 0.16, "nozzle": "0.4", "walls": 3, "infill": 20, "volume": (256, 256, 256)}, d)
        with open(out["process"]) as f:
            proc = json.load(f)
        self.assertEqual(proc["layer_height"], "0.16")
        self.assertEqual(proc["wall_loops"], "3")
        with open(out["machine"]) as f:
            self.assertEqual(json.load(f)["printable_height"], "256")


@unittest.skipUnless(HAVE, "FreeCAD is not importable here")
class SliceWith(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import Part

        from agentlink.tools import Session, build_server

        cls.dir = tempfile.mkdtemp()
        cls.session = Session(out_dir=cls.dir)
        cls.server = build_server(cls.session, lean=False)
        o = cls.session.doc.addObject("Part::Feature", "Body")
        o.Label = "Puck"
        o.Shape = Part.makeCylinder(15, 8)
        cls.session.doc.recompute()

    def call(self, name, args=None):
        r = self.server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args or {}}})["result"]
        return r.get("isError", False), json.loads(r["content"][0]["text"])

    def test_slice_with(self):
        err, r = self.call("slice_with", {"body": "Puck"})
        self.assertTrue(err)
        self.assertEqual(r["error"]["code"], "invalid_state")
        self.call("setup", {"action": "add", "type": "print", "bodies": ["Puck"], "settings": {"layerHeight": 0.2}})
        err, e = self.call("hw_embed", {"body": "Puck", "kind": "magnet", "size": "10x3", "at": [0, 0, 2]})
        self.assertFalse(err, e)
        for name in ("orcaslicer", "bambustudio"):
            if not slicers.find(name):
                err, r = self.call("slice_with", {"body": "Puck", "slicer": name})
                self.assertTrue(err)
                self.assertEqual(r["error"]["code"], "unavailable")
        if not slicers.find("prusaslicer"):
            self.skipTest("no PrusaSlicer on this machine")
        err, r = self.call("slice_with", {"body": "Puck", "slicer": "prusaslicer"})
        self.assertFalse(err, r)
        print("\nslice_with:", json.dumps(r))
        self.assertEqual(len(r["pauses"]), 1)
        self.assertAlmostEqual(r["pauses"][0]["z"], e["pause"]["z"] + 0.2, places=3)
        with open(r["gcode"]) as f:
            self.assertIn("M601 ; Insert the 10x3 magnet", f.read())


    def test_orcaslicer(self):
        if not slicers.find("orcaslicer"):
            self.skipTest("no OrcaSlicer on this machine (the bake-off unpacks its AppImage)")
        import Part

        o = self.session.doc.addObject("Part::Feature", "Disc")
        o.Label = "Disc"
        o.Shape = Part.makeBox(30, 20, 6)
        self.session.doc.recompute()
        self.call("setup", {"action": "add", "type": "print", "bodies": ["Disc"], "settings": {"layerHeight": 0.2}})
        err, r = self.call("slice_with", {"body": "Disc", "slicer": "orcaslicer", "project": True, "name": "Disc-orca"})
        self.assertFalse(err, r)
        print("\nslice_with orcaslicer:", json.dumps(r))
        self.assertGreater(r["layers"], 3)
        self.assertTrue(os.path.getsize(r["project"]) > 0)

if __name__ == "__main__":
    unittest.main()
