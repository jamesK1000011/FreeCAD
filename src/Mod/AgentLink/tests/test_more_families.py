# SPDX-License-Identifier: LGPL-2.1-or-later
# The later families on a real FreeCAD, each answer checked against what the
# shapes must give: exports, point clouds, Surface, BIM, OpenSCAD, Robot,
# gears, Assembly couplings, TechDraw marks and hatching, FEM probes, CAM
# tool sets. A family this build leaves out is skipped.
import json
import os
import unittest

from _pd_base import HAVE, Base


@unittest.skipUnless(HAVE, "FreeCAD with PartDesign and Sketcher is not importable here")
class MoreFamilies(Base):
    def have(self, tool):
        r = self.server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})["result"]
        names = {t["name"] for t in r["tools"]}
        if tool not in names:
            self.skipTest(f"{tool} is not in this build")

    def unavailable_skip(self, name, args):
        err, out = self.call(name, args)
        if err and out["error"]["code"] == "unavailable":
            self.skipTest(out["error"]["message"])
        self.assertFalse(err, f"{name}: {out}")
        return out

    def box(self, name, size, at=None):
        args = {"kind": "box", "size": size, "name": name}
        if at:
            args["at"] = at
        return self.ok("part_primitive", args)

    def test_exports(self):
        self.box("Ex plate", [60, 30, 10])
        for ext in ("glb", "gltf", "dae", "ply", "off", "wrl"):
            out = self.ok("export", {"objects": ["Ex plate"], "path": f"ex.{ext}"})
            self.assertTrue(os.path.getsize(out["path"]) > 0, ext)
        g = self.ok("export", {"objects": ["Ex plate"], "path": "ex2.glb", "colors": {"Ex plate": "#3366cc"}})
        self.assertEqual(g["verify"]["size"], [60.0, 30.0, 10.0])
        d = self.ok("export", {"objects": ["Ex plate"], "path": "ex.dxf", "view": "front", "hidden": True})
        self.assertEqual(d["verify"]["size"], [60.0, 10.0])
        s = self.ok("export", {"objects": ["Ex plate"], "path": "ex.svg", "section": 5})
        self.assertEqual(s["verify"]["size"], [60.0, 30.0])
        self.refused("export", {"objects": ["Ex plate"], "path": "ex3.dxf", "section": 50})

    def test_points(self):
        self.have("points_fit")
        self.box("Pt block", [40, 40, 20], [200, 0, 0])
        self.ok("points_sample", {"object": "Pt block", "count": 3000, "noise": 0.01, "name": "Pt cloud"})
        top = self.ok("points_crop", {"points": "Pt cloud", "box": [[200, 0, 19.9], [240, 40, 20.1]], "replace": False, "name": "Pt top"})
        self.assertGreater(top["points"], 100)
        f = self.ok("points_fit", {"points": "Pt top", "shape": "plane", "tolerance": 0.1})
        self.assertGreater(abs(f["normal"][2]), 0.999)
        self.assertEqual(f["verdict"], "fits")
        e = self.ok("points_export", {"points": "Pt top", "path": "top.ply"})
        back = self.ok("points_import", {"path": e["path"], "name": "Pt back"})
        self.assertEqual(back["points"], top["points"])

    def test_surface(self):
        self.have("surface_sew")
        self.box("Sf cube", [20, 20, 20], [300, 0, 0])
        out = self.ok("surface_sew", {"faces": [f"Sf cube.Face{i}" for i in range(1, 7)], "solid": True})
        self.assertTrue(out["closed"])
        self.assertAlmostEqual(out["solid"]["volume"], 8000, delta=0.5)

    def test_bim(self):
        self.have("bim_wall")
        w = self.ok("bim_wall", {"points": [[0, 1000], [5000, 1000]], "width": 200, "height": 3000, "name": "B wall"})
        self.assertAlmostEqual(w["volume_m3"], 3.0, delta=0.01)
        o = self.ok("bim_opening", {"wall": "B wall", "kind": "door", "at": 1000})
        self.assertGreater(o["wallCut_m3"], 0.3)
        self.refused("bim_opening", {"wall": "B wall", "kind": "window", "at": 4800, "width": 1000})
        slab = self.ok("bim_structure", {"kind": "slab", "points": [[0, 0], [5000, 0], [5000, 3000], [0, 3000]], "thickness": 200, "name": "B slab"})
        self.assertAlmostEqual(slab["volume_m3"], 3.0, delta=0.01)
        r = self.ok("bim_rebar", {"structure": "B slab", "along": "x", "spacing": 200})
        self.assertLessEqual(r["spacing"], 200)
        sch = self.ok("bim_schedule", {})
        kinds = {row["kind"] for row in sch["rows"]}
        self.assertTrue({"wall", "slab", "door", "rebar"} <= kinds)

    def test_openscad(self):
        self.have("scad_op")
        self.ok("part_primitive", {"kind": "sphere", "radius": 10, "at": [500, 0, 0], "name": "Sc a"})
        self.ok("part_primitive", {"kind": "sphere", "radius": 5, "at": [530, 0, 0], "name": "Sc b"})
        h = self.unavailable_skip("scad_op", {"op": "hull", "objects": ["Sc a", "Sc b"]})
        self.assertGreater(h["volume"], 4188 + 523)
        self.box("Sc box", [10, 20, 30], [600, 0, 0])
        e = self.ok("scad_export", {"objects": ["Sc box"], "path": "box.csg"})
        with open(e["path"]) as f:
            self.assertIn("cube", f.read())

    def test_robot(self):
        self.have("robot_new")
        self.ok("robot_new", {"model": "puma", "name": "R1"})
        m = self.ok("robot_move", {"robot": "R1", "tcp": {"at": [1000, 200, 800], "ypr": [0, 90, 0]}})
        self.assertTrue(m["reached"])
        self.ok("robot_path", {"name": "R path", "waypoints": [{"at": [1000, 0, 800], "ypr": [0, 90, 0], "speed": 100}, {"at": [1000, 300, 800], "ypr": [0, 90, 0], "speed": 100}]})
        run = self.ok("robot_run", {"robot": "R1", "trajectory": "R path", "samples": 10})
        self.assertEqual(run["verdict"], "reaches all")
        k = self.ok("robot_export", {"robot": "R1", "trajectory": "R path", "path": "r.src"})
        self.assertEqual(k["moves"], 2)

    def test_gear(self):
        self.have("pd_gear")
        self.ok("pd_body_new", {"name": "Gear body"})
        g = self.ok("pd_gear", {"body": "Gear body", "teeth": 20, "module": 2, "thickness": 8, "bore": 8})
        self.assertAlmostEqual(g["pitchDiameter"], 40)
        self.assertAlmostEqual(g["box"][0], 44, delta=0.1)

    def test_fea_probe(self):
        self.have("fea_probe")
        import shutil

        if not (shutil.which("ccx") and shutil.which("gmsh")):
            self.skipTest("ccx and gmsh are not installed")
        self.box("Fe beam", [100, 20, 10], [0, 500, 0])
        self.ok("fea_setup", {"body": "Fe beam", "material": "PLA", "mesh": 3})
        self.ok("fea_hold", {"faces": {"normal": [-1, 0, 0]}})
        self.ok("fea_load", {"faces": {"normal": [1, 0, 0]}, "force": [0, 0, -20]})
        self.ok("fea_solve", {})
        p = self.ok("fea_probe", {"points": [[50, 510, 10]]})
        # beam theory: 20 N × 50 mm × 5 mm / (20 × 10³ / 12) = 3.0 MPa at the top fibre
        self.assertAlmostEqual(p["points"][0]["vonMisesMPa"], 3.0, delta=0.4)

    def test_techdraw_marks_hatch(self):
        self.have("td_hatch")
        self.box("Td plate", [60, 40, 10], [0, 1000, 0])
        self.ok("part_primitive", {"kind": "cylinder", "radius": 6, "height": 10, "at": [30, 1020, 0], "name": "Td hole"})
        self.ok("part_boolean", {"op": "cut", "base": "Td plate", "tools": ["Td hole"], "name": "Td holed"})
        self.ok("td_page", {"action": "new", "paper": "A4", "name": "Td sheet"})
        self.ok("td_view", {"bodies": ["Td holed"], "direction": "top", "name": "Td top"})
        m = self.ok("td_centermarks", {"views": ["Td top"]})
        self.assertEqual(m["views"][0]["circles"], 1)
        top = self.ok("shape_query", {"body": "Td holed", "normal": [0, 0, 1]})["items"][0]["ref"]
        h = self.ok("td_hatch", {"view": "Td top", "faces": [top]})
        self.assertNotIn("warning", h)
        ex = self.ok("td_export", {"page": "Td sheet", "formats": ["svg"], "name": "td"})
        with open(ex["files"]["svg"]["path"]) as f:
            svg = f.read()
        self.assertIn("8 1.5 1.5 1.5", svg)  # the centre marks' chain line

    def test_cam_library(self):
        self.have("cam_library")
        self.box("Cm block", [60, 40, 15], [0, 2000, 0])
        self.ok("cam_job", {"body": "Cm block", "tool": {"diameter": 6}, "name": "Cm job"})
        e = self.ok("cam_library", {"job": "Cm job", "action": "export", "path": "tools.json"})
        with open(e["path"]) as f:
            data = json.load(f)
        self.assertEqual(data["tools"][0]["diameter"], 6.0)


if __name__ == "__main__":
    unittest.main()
