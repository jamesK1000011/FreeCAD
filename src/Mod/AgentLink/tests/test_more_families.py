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

    def test_points_mesh_curve(self):
        self.have("points_mesh")
        self.box("Pm plate", [60, 40, 10], [0, 300, 0])
        self.ok("points_sample", {"object": "Pm plate", "count": 6000, "name": "Pm cloud"})
        self.ok("points_crop", {"points": "Pm cloud", "box": [[0, 300, 9.9], [60, 340, 10.1]], "replace": False, "name": "Pm top"})
        m = self.ok("points_mesh", {"points": "Pm top"})
        self.assertGreater(m["area"], 2400 * 0.85)
        self.assertLess(m["area"], 2400 * 1.02)
        self.ok("part_primitive", {"kind": "cylinder", "radius": 20, "height": 30, "at": [200, 300, 0], "name": "Pm cyl"})
        self.ok("points_sample", {"object": "Pm cyl", "count": 8000, "name": "Pm cc"})
        self.ok("points_crop", {"points": "Pm cc", "box": [[150, 270, 14.8], [250, 330, 15.2]], "replace": False, "name": "Pm ring"})
        c = self.ok("points_fit", {"points": "Pm ring", "shape": "curve", "tolerance": 0.3})
        self.assertTrue(c["closed"])
        self.assertAlmostEqual(c["length"], 2 * 3.14159 * 20, delta=2)

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
        # two boxes apart: their hull is the box round both, 40 × 10 × 10
        self.box("Sc a", [10, 10, 10], [500, 0, 0])
        self.box("Sc b", [10, 10, 10], [530, 0, 0])
        h = self.unavailable_skip("scad_op", {"op": "hull", "objects": ["Sc a", "Sc b"]})
        self.assertAlmostEqual(h["volume"], 4000, delta=1)
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

    def test_fea_thermal(self):
        self.have("fea_probe")
        import shutil

        if not (shutil.which("ccx") and shutil.which("gmsh")):
            self.skipTest("ccx and gmsh are not installed")
        self.box("Th bar", [100, 10, 10], [0, 700, 0])
        self.ok("fea_setup", {"body": "Th bar", "material": "PLA", "mesh": 3})
        self.ok("fea_hold", {"faces": {"normal": [-1, 0, 0]}})
        self.ok("fea_load", {"temperature": 50, "alpha": 68e-6})
        free = self.ok("fea_solve", {})
        self.assertAlmostEqual(free["displacementMax"]["mm"], 100 * 68e-6 * 50, delta=0.02)  # L·alpha·dT
        self.ok("fea_hold", {"faces": {"normal": [1, 0, 0]}})
        self.ok("fea_solve", {})
        mid = self.ok("fea_probe", {"points": [[50, 705, 5]]})["points"][0]["vonMisesMPa"]
        self.assertAlmostEqual(mid, 3500 * 68e-6 * 50, delta=1.5)  # E·alpha·dT

    def test_fea_spin(self):
        self.have("fea_probe")
        import shutil

        if not (shutil.which("ccx") and shutil.which("gmsh")):
            self.skipTest("ccx and gmsh are not installed")
        # a quarter of a spinning PLA disk on symmetry slides: centre (3+nu)/8 rho w2 R2
        self.ok("part_primitive", {"kind": "cylinder", "radius": 100, "height": 5, "name": "Sp disk"})
        self.box("Sp q", [120, 120, 10], [0, 0, -2])
        self.ok("part_boolean", {"op": "common", "base": "Sp disk", "tools": ["Sp q"]})
        self.ok("fea_setup", {"body": "Sp disk", "material": "PLA", "mesh": 5})
        for n in ([-1, 0, 0], [0, -1, 0], [0, 0, -1]):
            self.ok("fea_hold", {"faces": {"normal": n}, "kind": "slide"})
        self.ok("fea_load", {"spin": {"rpm": 10000}})
        r = self.ok("fea_solve", {})
        w2 = (10000 * 2 * 3.14159265 / 60) ** 2
        rho = 1.24e-9
        self.assertAlmostEqual(r["vonMisesMax"]["MPa"], (3.36 / 8) * rho * w2 * 1e4, delta=0.6)

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

    def test_part_more(self):
        self.box("Pm B", [40, 30, 20], [500, 0, 0])
        self.ok("part_primitive", {"kind": "cylinder", "radius": 5, "height": 40, "at": [520, 15, -10], "name": "Pm C"})
        self.ok("part_boolean", {"op": "cut", "base": "Pm B", "tools": ["Pm C"]})
        sec = self.ok("part_section", {"body": "Pm B", "plane": {"normal": [0, 0, 1], "at": [0, 0, 10]}, "face": True})
        self.assertAlmostEqual(sec["area_mm2"], 1200 - 78.54, delta=0.1)
        self.ok("part_wire", {"kind": "polygon", "sides": 6, "radius": 10, "name": "Pm hex"})
        band = self.ok("part_offset2d", {"shape": "Pm hex", "distance": 2, "fill": True})
        self.assertAlmostEqual(band["area_mm2"], 120 + 3.14159 * 4, delta=0.1)
        d = self.ok("part_defeature", {"body": "Pm B", "faces": ["Face7"], "name": "Pm plain"})
        self.assertAlmostEqual(d["volume_mm3"], 24000, delta=0.5)
        faces = [f"Pm plain.Face{i}" for i in range(1, 7)]
        sol = self.ok("part_extract", {"refs": faces, "build": "solid", "name": "Pm rebuilt"})
        self.assertEqual(sol["solids"], 1)
        r = self.ok("part_fix", {"body": "Pm rebuilt", "op": "reverse"})
        self.assertLess(r["volume_mm3"], 0)
        dt = self.ok("part_datum", {"kind": "csys", "to": "Pm plain.Face6", "at": [5, 0, 0]})
        self.assertEqual(dt["placement"]["at"][2], 20.0)

    def test_mesh_more(self):
        self.have("meshobj_boundary")
        c = self.ok("meshobj_primitive", {"kind": "cylinder", "radius": 5, "height": 20, "at": [0, 900, 0], "name": "Mm cyl"})
        self.assertEqual(c["box"], [10.0, 10.0, 20.0])
        t = self.ok("meshobj_primitive", {"kind": "torus", "radius": 10, "radius2": 2, "at": [50, 900, 0], "name": "Mm torus"})
        self.assertAlmostEqual(t["box"][2], 4.0, delta=0.01)
        self.ok("meshobj_trim", {"mesh": "Mm cyl", "at": [0, 900, 15], "normal": [0, 0, 1]})
        b = self.ok("meshobj_boundary", {"mesh": "Mm cyl"})
        self.assertEqual(b["loops"], 1)
        self.assertAlmostEqual(b["boundaries"][0]["centre"][2], 15.0, delta=0.01)
        m = self.ok("meshobj_merge", {"meshes": ["Mm cyl", "Mm torus"]})
        self.assertEqual(m["components"], 2)

    def test_bim_more(self):
        self.have("bim_preflight")
        self.ok("bim_wall", {"points": [[0, 3000], [5000, 3000]], "height": 3000, "width": 200, "name": "Bm wall"})
        self.ok("bim_structure", {"kind": "slab", "points": [[0, 5000], [3000, 5000], [3000, 7000], [0, 7000]], "thickness": 200, "name": "Bm slab"})
        c = self.unavailable_skip("bim_covering", {"face": "Bm slab.Face6", "tile": [300, 300], "thickness": 10, "joint": 3})
        if "tiles" in c:
            self.assertAlmostEqual(c["volume_m3"], 6.0 * 0.98 * 0.01, delta=0.002)
        lay = self.ok("bim_layers", {"layers": [{"material": "Bm brick", "thickness": 215, "density": 1900}, {"material": "Bm plaster", "thickness": 13, "density": 1200}], "objects": ["Bm wall"]})
        self.assertEqual(lay["total_mm"], 228.0)
        q = self.ok("bim_quantities", {"by": "material"})
        self.assertIn("Layers", q["groups"])
        i = self.ok("bim_ifc", {"objects": ["Bm wall"], "properties": {"Pset_WallCommon.FireRating": "REI60"}})
        self.assertEqual(i["objects"][0]["properties"]["Pset_WallCommon"]["FireRating"], "REI60")
        cut = self.ok("bim_edit", {"op": "cut", "object": "Bm wall", "at": [0, 0, 2500], "normal": [0, 0, 1]})
        self.assertGreater(cut["removed_m3"], 0.2)
        pf = self.ok("bim_preflight", {})
        self.assertFalse(pf["ok"])

    def test_techdraw_cosmetics(self):
        self.have("td_cosmetic")
        self.ok("part_primitive", {"kind": "cylinder", "radius": 10, "height": 60, "at": [0, 0, 1200], "name": "Tc shaft"})
        self.ok("part_transform", {"op": "rotate", "body": "Tc shaft", "axis": [0, 1, 0], "angle": 90, "about": [0, 0, 1200]})
        self.ok("td_page", {"action": "new", "paper": "A4", "name": "Tc page"})
        self.ok("td_view", {"page": "Tc page", "bodies": ["Tc shaft"], "direction": "front", "scale": 2, "at": [110, 150], "name": "Tc front"})
        self.ok("td_view", {"page": "Tc page", "bodies": ["Tc shaft"], "direction": "right", "scale": 2, "at": [220, 150], "name": "Tc end"})
        g = self.ok("td_geometry", {"view": "Tc front", "kind": "lines"})
        lines = [i["ref"] for i in g["items"] if i["kind"] == "line"]
        c = self.ok("td_cosmetic", {"view": "Tc front", "items": [{"kind": "centerline", "edges": lines[:2]}, {"kind": "thread", "edges": lines[:2], "length": 25}]})
        self.assertEqual(c["made"][1]["note"], "M20x2.5")
        self.ok("td_finish", {"view": "Tc front", "on": lines[0], "ra": "0.8", "process": "ground"})
        a = self.ok("td_area", {"view": "Tc end", "face": "Tc shaft.Face2"})
        self.assertAlmostEqual(a["area"], 314.159, delta=0.01)
        out = self.ok("td_export", {"page": "Tc page", "formats": ["svg"], "name": "tc"})
        with open(out["files"]["svg"]["path"]) as f:
            self.assertIn("Ra 0.8", f.read())

    def test_cam_edit(self):
        self.have("cam_edit")
        self.box("Ce block", [40, 30, 10], [0, 1500, 0])
        self.ok("cam_job", {"body": "Ce block", "tool": {"diameter": 6}, "name": "Ce job"})
        self.ok("cam_op", {"job": "Ce job", "kind": "profile", "side": "outside", "name": "Ce outline"})
        self.ok("cam_edit", {"job": "Ce job", "action": "copy", "op": "Ce outline", "name": "Ce rough"})
        st = self.ok("cam_edit", {"job": "Ce job", "action": "stop", "text": "check part", "after": "Ce rough"})
        self.assertIn("M0", st["gcode"])
        off = self.ok("cam_edit", {"job": "Ce job", "action": "active", "op": "Ce rough", "on": False})
        self.assertIn("Ce rough (off)", off["ops"])
        post = self.ok("cam_post", {"job": "Ce job", "path": "ce.nc"})
        with open(post["path"]) as f:
            self.assertIn("M0", f.read())

    def test_sketch_more(self):
        self.have("sketch_merge")
        self.ok("pd_body_new", {"name": "Sm body"})
        self.ok("sketch_new", {"body": "Sm body", "plane": "XY", "name": "Sm s1"})
        r = self.ok("sketch_add", {"sketch": "Sm s1", "items": [{"kind": "arc3", "points": [[0, 0], [10, 10], [20, 0]]}, {"kind": "parabola", "vertex": [0, 40], "focal": 5},
                                                                 {"kind": "bspline", "points": [[0, 80], [20, 90], [40, 80], [60, 95]]}]})
        ov = r["state"]["openVertices"]
        self.assertIn([5.0, 50.0], ov)
        b = self.ok("sketch_bspline", {"sketch": "Sm s1", "geo": 2, "op": "degree", "by": 1})
        self.assertEqual(b["degree"], 4)
        self.ok("sketch_new", {"body": "Sm body", "plane": "XY", "name": "Sm s2"})
        self.ok("sketch_add", {"sketch": "Sm s2", "items": [{"kind": "rectangle", "corner": [100, 0], "size": [20, 10]}]})
        m = self.ok("sketch_merge", {"sketches": ["Sm s1", "Sm s2"]})
        self.assertEqual(m["geometry"], 7)
        self.ok("sketch_attach", {"sketch": "Sm s2", "plane": "XZ", "offset": 5})

    def test_robot_trajectory(self):
        self.have("robot_trajectory")
        self.ok("robot_new", {"name": "Rt robot"})
        self.ok("robot_path", {"waypoints": [{"at": [400, 0, 400], "type": "PTP"}, {"at": [400, 200, 400], "type": "LIN", "speed": 100}], "name": "Rt t1"})
        self.ok("robot_path", {"waypoints": [{"at": [400, 200, 300], "type": "LIN"}, {"at": [400, 0, 300], "type": "LIN", "speed": 100}], "name": "Rt t2"})
        j = self.ok("robot_trajectory", {"op": "join", "trajectories": ["Rt t1", "Rt t2"]})
        self.assertEqual(j["waypoints"], 4)
        d = self.ok("robot_trajectory", {"op": "dress", "trajectory": "Rt t1", "speed": 50, "move": [0, 0, -100], "name": "Rt dressed"})
        r = self.ok("robot_run", {"robot": "Rt robot", "trajectory": "Rt dressed"})
        for ax in r["axes"]:
            self.assertGreaterEqual(ax["from"], -180.0)
            self.assertLessEqual(ax["to"], 180.0)
