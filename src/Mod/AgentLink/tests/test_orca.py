# SPDX-License-Identifier: LGPL-2.1-or-later
# OrcaSlicer headless, end to end on FreeCAD with the real command line (the
# bake-off unpacks its AppImage): a project made from two bodies, changed at
# each level, painted by face, sliced, read back, the body changed and its
# part replaced, sliced again.

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


@unittest.skipUnless(HAVE, "FreeCAD is not importable here")
class OrcaLoop(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import Part

        from agentlink.tools import Session, build_server

        cls.dir = tempfile.mkdtemp()
        cls.session = Session(out_dir=cls.dir)
        cls.server = build_server(cls.session, lean=False)
        for label, shape in (("Plate", Part.makeBox(30, 20, 6)), ("Peg", Part.makeBox(10, 10, 20))):
            o = cls.session.doc.addObject("Part::Feature", label)
            o.Label = label
            o.Shape = shape
        cls.session.doc.recompute()

    def call(self, name, args=None):
        r = self.server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args or {}}})["result"]
        return r.get("isError", False), json.loads(r["content"][0]["text"])

    def ok(self, name, args=None):
        err, r = self.call(name, args)
        self.assertFalse(err, r)
        return r

    def test_settings_listing(self):
        r = self.ok("orca_project", {"action": "settings", "query": "infill_density"})
        self.assertTrue(any(x["key"] == "sparse_infill_density" for x in r["settings"]))

    def test_loop(self):
        if not slicers.find("orcaslicer"):
            self.skipTest("no OrcaSlicer on this machine (the bake-off unpacks its AppImage)")
        r = self.ok("orca_project", {"action": "new", "bodies": ["Plate", "Peg"], "path": "loop.3mf"})
        p = r["project"]
        print("\norca new:", json.dumps(r))
        info = self.ok("orca_project", {"action": "info", "path": p})
        self.assertEqual([o["size"] for o in info["objects"]], [[30, 20, 6], [10, 10, 20]])
        # Every level.
        self.ok("orca_project", {"action": "set", "path": p, "settings": {"sparse_infill_density": 25, "wall_loops": 2}})
        self.ok("orca_project", {"action": "set", "path": p, "object": "Peg", "key": "wall_loops", "value": 4})
        self.ok("orca_project", {"action": "set", "path": p, "scope": "range", "object": "Peg", "zmin": 10, "zmax": 15, "settings": {"sparse_infill_density": "80%"}})
        self.ok("orca_project", {"action": "part_add", "path": p, "object": "Plate", "kind": "modifier_part", "box": [8, 8, 6], "at": [15, 10, 3], "name": "dense", "settings": {"sparse_infill_density": "100%"}})
        self.ok("orca_project", {"action": "gcode", "path": p, "z": 3.0, "kind": "custom", "gcode": "M117 agentlink-layer"})
        err, e = self.call("orca_project", {"action": "set", "path": p, "key": "sparse_infill_density", "value": 300})
        self.assertTrue(err)
        # Paint the plate's top face's seam (Face6 of a Part box is its top).
        r = self.ok("orca_paint", {"path": p, "object": "Plate", "kind": "seam", "faces": ["Face6"], "state": "block"})
        self.assertGreater(r["triangles"], 0)
        info = self.ok("orca_project", {"action": "info", "path": p, "object": "Peg"})
        self.assertEqual(info["effective"]["wall_loops"], "4")
        # Slice as it stands.
        s1 = self.ok("orca_slice", {"path": p})
        print("orca slice:", json.dumps({k: v for k, v in s1.items() if k != "gcode"}))
        with open(s1["gcode"][0]) as f:
            text = f.read()
        self.assertIn("M117 agentlink-layer", text)
        g = self.ok("gcode_read", {"path": s1["gcode"][0], "z": 4.0, "view": "feature", "svg": True})
        self.assertGreater(g["layers"], 20)
        self.assertTrue(os.path.isfile(g["svg"]))
        # The return leg: the peg grows in FreeCAD; its part follows, its settings stay.
        import Part

        self.session.body("Peg").Shape = Part.makeBox(10, 10, 30)
        self.session.doc.recompute()
        self.ok("orca_project", {"action": "replace", "path": p, "object": "Peg", "body": "Peg"})
        info = self.ok("orca_project", {"action": "info", "path": p, "object": "Peg"})
        self.assertEqual(info["size"], [10, 10, 30])
        self.assertEqual(info["settings"]["wall_loops"], "4")
        s2 = self.ok("orca_slice", {"path": p})
        self.assertGreater(s2["layers"], s1["layers"])

    def test_slice_with_two_bodies(self):
        # Minimal profiles, two filaments: OrcaSlicer 2.5 refused these when
        # a per-filament list was shorter than the filament count.
        if not slicers.find("orcaslicer"):
            self.skipTest("no OrcaSlicer on this machine")
        self.call("setup", {"action": "add", "type": "print", "bodies": ["Plate", "Peg"], "settings": {"layerHeight": 0.2}})
        r = self.ok("slice_with", {"bodies": ["Plate", "Peg"], "slicer": "orcaslicer"})
        self.assertGreater(r["layers"], 10)


if __name__ == "__main__":
    unittest.main()
