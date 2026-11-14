# SPDX-License-Identifier: LGPL-2.1-or-later
# OrcaSlicer projects changed headless (agentlink/orca3mf.py), on a project
# OrcaSlicer 2.5's command line made (data/orca-two-boxes.json, its members as
# text, zipped here: a 20x10x5 box
# and an 8x8x20 one, arranged, minimal profiles). Pure Python; the slicer's
# own reading of the changed files is test_orca.py's.

import os
import shutil
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from agentlink import orca3mf as O  # noqa: E402

SAMPLE = os.path.join(os.path.dirname(__file__), "data", "orca-two-boxes.json")


def sample(path):
    import json

    with open(SAMPLE, encoding="utf-8") as f:
        members = json.load(f)["members"]
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for n, text in members.items():
            z.writestr(n, text)


def box(x, y, z, at=(0, 0, 0)):
    v = [(0, 0, 0), (x, 0, 0), (x, y, 0), (0, y, 0), (0, 0, z), (x, 0, z), (x, y, z), (0, y, z)]
    v = [(a + at[0], b + at[1], c + at[2]) for a, b, c in v]
    f = [(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4), (1, 2, 6), (1, 6, 5), (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7)]
    return [[v[a], v[b], v[c]] for a, b, c in f]


class Project(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.p = os.path.join(self.d, "p.3mf")
        sample(self.p)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def reopen(self):
        return O.Project(self.p)

    def test_reads_objects_parts_plates(self):
        info = O.Project(self.p).info()
        names = [o["name"] for o in info["objects"]]
        self.assertEqual(names, ["box.stl", "tall.stl"])
        box_ = info["objects"][0]
        self.assertEqual(box_["size"], [20, 10, 5])
        self.assertEqual(box_["parts"][0]["kind"], "normal_part")
        self.assertEqual(box_["parts"][0]["triangles"], 12)
        self.assertEqual(box_["instances"][0]["at"], [100, 110, 0])
        self.assertEqual(info["plates"][0]["objects"], ["2", "4"])
        self.assertEqual(info["filaments"][0]["type"], "PLA")
        self.assertTrue(info["sliced"])

    def test_object_keeps_source_coordinates(self):
        pr = O.Project(self.p)
        o = pr.object("tall")
        pts = [O.apply(o["parts"][0]["transform"], v) for v in pr.mesh(o["parts"][0])[0]]
        self.assertEqual([round(x, 6) for x in O.bbox(pts)], [0, 0, 0, 8, 8, 20])

    def test_settings_checked(self):
        pr = O.Project(self.p)
        pr.set("sparse_infill_density", 35)
        pr.set("wall_loops", "4")
        pr.set("enable_support", True)
        pr.set("support_type", "tree(auto)")
        for bad in (("wall_loops", 2.5), ("sparse_infill_density", 150), ("support_type", "trees"), ("no_such_key", 1)):
            with self.assertRaises(O.ProjectError, msg=bad):
                pr.set(*bad)
        with self.assertRaises(O.ProjectError):
            pr.set_object("box", "nozzle_diameter", 0.6)  # a printer setting, not per object
        pr.save()
        cfg = self.reopen().config
        self.assertEqual(cfg["sparse_infill_density"], "35%")
        self.assertEqual(cfg["wall_loops"], "4")
        self.assertEqual(cfg["enable_support"], "1")
        self.assertIn("sparse_infill_density", cfg["different_settings_to_system"][0].split(";"))

    def test_per_filament_slot(self):
        pr = O.Project(self.p)
        n = len(pr.config["filament_settings_id"])
        pr.set("nozzle_temperature", 215, slot=1)
        self.assertEqual(len(pr.config["nozzle_temperature"]), max(n, 1))
        self.assertEqual(pr.config["nozzle_temperature"][0], "215")

    def test_object_and_part_settings(self):
        pr = O.Project(self.p)
        pr.set_object("box", "sparse_infill_density", "60%")
        pr.set_object(2, "wall_loops", 5)
        pr.set_part("box", 1, "top_shell_layers", 7)
        pr.save()
        objs = self.reopen().objects()
        self.assertEqual(objs[0]["settings"]["sparse_infill_density"], "60%")
        self.assertEqual(objs[1]["settings"]["wall_loops"], "5")
        self.assertEqual(objs[0]["parts"][0]["settings"]["top_shell_layers"], "7")
        # Unsliced now: the stale G-code and its notes are gone.
        with zipfile.ZipFile(self.p) as z:
            self.assertNotIn("Metadata/plate_1.gcode", z.namelist())

    def test_modifier_and_negative_parts(self):
        pr = O.Project(self.p)
        r = pr.add_part("box", box(6, 6, 5, at=(2, 2, 0)), "modifier_part", "dense", {"sparse_infill_density": "100%"})
        pr.add_part("box", box(2, 2, 10, at=(15, 4, -2)), "negative_part", "hole")
        pr.save()
        pr = self.reopen()
        o = pr.object("box")
        kinds = [(p["name"], p["kind"]) for p in o["parts"]]
        self.assertEqual(kinds, [("box.stl", "normal_part"), ("dense", "modifier_part"), ("hole", "negative_part")])
        self.assertEqual(o["parts"][1]["settings"], {"sparse_infill_density": "100%"})
        self.assertEqual(pr.mesh(o["parts"][1])[1].__len__(), 12)
        # The size counts solid parts only.
        self.assertEqual(pr.info()["objects"][0]["size"], [20, 10, 5])
        with zipfile.ZipFile(self.p) as z:
            rels = z.read("3D/_rels/3dmodel.model.rels").decode()
        self.assertIn(f"agentlink_part_{r['id']}.model", rels)
        pr.remove_part("box", "hole")
        pr.save()
        self.assertEqual(len(self.reopen().object("box")["parts"]), 2)
        with self.assertRaises(O.ProjectError):
            self.reopen().remove_part("box", 1)

    def test_replace_mesh_keeps_settings(self):
        pr = O.Project(self.p)
        pr.set_part("tall", 1, "wall_loops", 3)
        pr.replace_mesh("tall", 1, box(8, 8, 30))
        pr.save()
        pr = self.reopen()
        o = pr.object("tall")
        pts = [O.apply(o["parts"][0]["transform"], v) for v in pr.mesh(o["parts"][0])[0]]
        self.assertEqual([round(x, 6) for x in O.bbox(pts)], [0, 0, 0, 8, 8, 30])
        self.assertEqual(o["parts"][0]["settings"]["wall_loops"], "3")

    def test_paint(self):
        pr = O.Project(self.p)
        o = pr.object("box")
        _, tris, _ = pr.mesh(o["parts"][0])
        states = ["enforce" if i < 2 else "none" for i in range(len(tris))]
        self.assertEqual(pr.paint("box", 1, "supports", states), 2)
        pr.paint("box", 1, "seam", ["block"] * len(tris))
        pr.save()
        pr = self.reopen()
        _, _, attrs = pr.mesh(pr.object("box")["parts"][0])
        self.assertEqual(attrs["paint_supports"][:3], ["4", "4", ""])
        self.assertEqual(set(attrs["paint_seam"]), {"8"})
        self.assertEqual(pr.info()["objects"][0]["parts"][0]["painted"], ["seam", "supports"])

    def test_paint_states(self):
        for n in range(1, 33):
            self.assertEqual(O.paint_value(O.paint_state(n)), n)
        self.assertEqual(O.paint_state(3), "0C")
        self.assertEqual(O.paint_state(17), "EC")
        self.assertEqual(O.paint_state(18), "0FC")

    def test_place(self):
        pr = O.Project(self.p)
        r = pr.place("tall", rotate=[90, 0, 0], at=[50, 60])
        self.assertAlmostEqual(r["placed"][2], 0)  # dropped on the bed
        self.assertAlmostEqual((r["placed"][0] + r["placed"][3]) / 2, 50)
        self.assertAlmostEqual(r["placed"][5] - r["placed"][2], 8)  # lying down
        pr.save()
        self.assertAlmostEqual(self.reopen().info()["objects"][1]["size"][1], 20)

    def test_ranges(self):
        pr = O.Project(self.p)
        pr.set_range("tall", 5, 10, {"sparse_infill_density": "80%", "wall_loops": 4})
        with self.assertRaises(O.ProjectError):
            pr.set_range("tall", 8, 12, {"wall_loops": 2})
        pr.save()
        r = self.reopen().ranges()
        self.assertEqual(r[2][0]["min"], 5)
        self.assertEqual(r[2][0]["settings"]["sparse_infill_density"], "80%")
        pr = self.reopen()
        pr.set_range("tall", 5, 10, {})
        pr.save()
        self.assertEqual(self.reopen().ranges(), {})

    def test_gcodes(self):
        pr = O.Project(self.p)
        pr.set_gcode(3.0, "pause")
        pr.set_gcode(1.0, "custom", gcode="M117 hello")
        items = pr.set_gcode(2.0, "color_change")
        self.assertEqual([i["z"] for i in items], [1.0, 2.0, 3.0])
        pr.save()
        g = self.reopen().gcodes()[1]
        self.assertEqual([i["type"] for i in g["items"]], ["custom", "color_change", "pause"])
        with zipfile.ZipFile(self.p) as z:
            root = ET.fromstring(z.read(O.GCODES))
        self.assertEqual(root.find("plate/plate_info").get("id"), "1")
        self.assertEqual(root.find("plate")[-1].tag, "mode")

    def test_plates(self):
        pr = O.Project(self.p)
        x = pr.object("tall")["instances"][0]["transform"][0][3]
        pr.move_to_plate("tall", 2)
        self.assertGreater(pr.object("tall")["instances"][0]["transform"][0][3], x + 100)
        pr.set_plate(2, "name", "tall one")
        pr.set_plate(1, "bed_type", "Textured PEI Plate")
        with self.assertRaises(O.ProjectError):
            pr.set_plate(1, "bed_type", "glass")
        pr.save()
        pl = self.reopen().plates()
        self.assertEqual([p["objects"] for p in pl], [["2"], ["4"]])
        self.assertEqual(pl[1]["settings"]["plater_name"], "tall one")
        self.assertEqual(pl[0]["settings"]["curr_bed_type"], "Textured PEI Plate")

    def test_remove_object(self):
        pr = O.Project(self.p)
        pr.set_range("tall", 1, 2, {"wall_loops": 3})
        pr.remove_object("box")
        pr.save()
        pr = self.reopen()
        self.assertEqual([o["name"] for o in pr.objects()], ["tall.stl"])
        self.assertIn(1, pr.ranges())  # tall is first now

    def test_namespaces_kept(self):
        pr = O.Project(self.p)
        pr.set_object(1, "wall_loops", 3)
        pr.save()
        with zipfile.ZipFile(self.p) as z:
            text = z.read(O.MODEL).decode()
        self.assertIn('xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/06"', text)
        self.assertIn("p:path=", text)
        self.assertNotIn("ns0:", text)
        self.assertNotIn("ns1:", text)

    def test_add_object_and_instances(self):
        pr = O.Project(self.p)
        r = pr.add_object(box(5, 5, 5, at=(100, 100, 0)), "cube.stl")
        self.assertEqual(r["index"], 3)
        pr.add_instance("cube")
        pr.add_instance("cube", [0, 20])
        pr.set_printable("cube", False, 2)
        pr.save()
        pr = self.reopen()
        o = pr.object("cube")
        self.assertEqual(len(o["instances"]), 3)
        self.assertFalse(o["instances"][2]["printable"])
        self.assertEqual(sorted(pr.plates()[0]["objects"]), sorted(["2", "4", o["id"], o["id"], o["id"]]))
        # Its source coordinates are kept, like Orca's own.
        pts = [O.apply(o["parts"][0]["transform"], v) for v in pr.mesh(o["parts"][0])[0]]
        self.assertEqual([round(x, 6) for x in O.bbox(pts)], [100, 100, 0, 105, 105, 5])
        pr.remove_instance("cube", 0)
        self.assertEqual(len(pr.object("cube")["instances"]), 2)

    def test_layer_heights(self):
        pr = O.Project(self.p)
        pr.set_layer_heights("tall", [[0, 0.2], [10, 0.1], [20, 0.25]])
        with self.assertRaises(O.ProjectError):
            pr.set_layer_heights("tall", [[0, 0.2], [10, 0.1]])
        with self.assertRaises(O.ProjectError):
            pr.set_layer_heights("tall", [[0, 0.2], [10, 3], [20, 0.2]])
        pr.save()
        with zipfile.ZipFile(self.p) as z:
            text = z.read("Metadata/layer_heights_profile.txt").decode()
        self.assertTrue(text.startswith("object_id=2|0.000000;0.200000;10.000000;0.100000"))
        self.assertEqual(self.reopen().layer_heights()[2][1], [10, 0.1])

    def test_brim_ears(self):
        pr = O.Project(self.p)
        pr.set_brim_ears("box", [[0, 0], [20, 10]], radius=4)
        pr.save()
        pr = self.reopen()
        self.assertEqual(pr.brim_ears()[1], [[0, 0, 0, 4], [20, 10, 0, 4]])
        self.assertEqual(pr.object("box")["settings"]["brim_type"], "brim_ears")
        with zipfile.ZipFile(self.p) as z:
            self.assertTrue(z.read("Metadata/brim_ear_points.txt").startswith(b"brim_points_format_version=0\n"))

    def test_filaments(self):
        pr = O.Project(self.p)
        n = len(pr.config["filament_settings_id"])
        slot = pr.add_filament(1, colour="#00ff00", type_="PETG")
        self.assertEqual(slot, n + 1)
        for k in pr.filament_keys():
            self.assertEqual(len(pr.config[k]), n + 1, k)
        self.assertEqual(pr.config["filament_colour"][-1], "#00FF00")
        self.assertEqual(pr.config["filament_type"][-1], "PETG")
        m = pr.config.get("flush_volumes_matrix")
        if isinstance(m, list):
            self.assertEqual(len(m), (n + 1) ** 2)
        pr.set_object("tall", "extruder", slot)
        pr.remove_filament(1)
        self.assertEqual(len(pr.config["filament_settings_id"]), n)
        self.assertEqual(pr.object("tall")["extruder"], str(slot - 1))
        with self.assertRaises(O.ProjectError):
            pr.set_filament(1, colour="green")

    def test_plate_add_remove(self):
        pr = O.Project(self.p)
        self.assertEqual(pr.add_plate("spare"), 2)
        pr.move_to_plate("tall", 2)
        pr.set_gcode(2.0, "pause", plate=2)
        pr.remove_plate(2)
        pr.save()
        pr = self.reopen()
        self.assertEqual(len(pr.plates()), 1)
        self.assertEqual([o["name"] for o in pr.objects()], ["box.stl"])
        self.assertNotIn(2, pr.gcodes())

    def test_custom_gcode_in_extra(self):
        pr = O.Project(self.p)
        pr.set_gcode(1.0, "custom", gcode="M117 hi")
        pr.save()
        with zipfile.ZipFile(self.p) as z:
            layer = ET.fromstring(z.read(O.GCODES)).find("plate/layer")
        self.assertEqual(layer.get("extra"), "M117 hi")  # Orca reads it from extra
        self.assertEqual(self.reopen().gcodes()[1]["items"][0]["gcode"], "M117 hi")

    def test_lay_flat(self):
        pr = O.Project(self.p)
        r = pr.lay_flat("tall", [1, 0, 0])  # its +X side down
        size = [round(r["placed"][i + 3] - r["placed"][i], 4) for i in range(3)]
        self.assertEqual(size, [20, 8, 8])
        self.assertAlmostEqual(r["placed"][2], 0)

    def test_export_obj(self):
        pr = O.Project(self.p)
        out = pr.export_obj(os.path.join(self.d, "o.obj"))
        with open(out) as f:
            text = f.read()
        self.assertEqual(text.count("\no "), 2)
        self.assertEqual(sum(1 for ln in text.splitlines() if ln.startswith("f ")), 24)

    def test_copies_and_plates(self):
        pr = O.Project(self.p)
        pr.set_object("box", "wall_loops", 6)
        pr.add_part("box", box(4, 4, 5, at=(2, 2, 0)), "modifier_part", "m", {"sparse_infill_density": "90%"})
        r = pr.copy_object("box")
        c = pr.object(r["index"])
        self.assertEqual(c["settings"]["wall_loops"], "6")
        self.assertEqual([p["kind"] for p in c["parts"]], ["normal_part", "modifier_part"])
        self.assertEqual(c["parts"][1]["settings"], {"sparse_infill_density": "90%"})
        self.assertEqual(c["instances"][0]["transform"], pr.object("box")["instances"][0]["transform"])
        d = pr.duplicate_plate(1)
        self.assertEqual(d["plate"], 2)
        self.assertEqual(len(pr.plates()[1]["objects"]), 3)
        # Plate 2 sits beside plate 1, a bed and a fifth along X: its copies with it.
        bed = pr.bed()
        w = bed[2] - bed[0]
        x1 = pr.object("box")["instances"][0]["transform"][0][3]
        x2 = pr.object(4)["instances"][0]["transform"][0][3]
        self.assertAlmostEqual(x2 - x1, 1.2 * w, places=3)
        self.assertEqual(pr.bed(2)[0], bed[0] + 1.2 * w)
        order = pr.move_plate(2, 1)
        self.assertAlmostEqual(pr.object(4)["instances"][0]["transform"][0][3], x1, places=3)
        self.assertEqual(order, [2, 1])
        self.assertEqual(len(pr.plates()[0]["objects"]), 3)
        pr.save()
        self.assertEqual(len(self.reopen().plates()), 2)

    def test_instance_to_object(self):
        pr = O.Project(self.p)
        pr.add_instance("tall")
        r = pr.instance_to_object("tall", 1)
        self.assertEqual(len(pr.object("tall")["instances"]), 1)
        self.assertEqual(len(pr.objects()), 3)
        self.assertEqual(pr.object(r["index"])["name"], "tall.stl")

    def test_fit_fill_grid(self):
        pr = O.Project(self.p)
        bed = pr.bed()
        self.assertTrue(bed)
        r = pr.scale_to_fit("tall")
        self.assertGreater(r["scale"], 1)
        pr = O.Project(self.p)
        f = pr.fill_bed("box", spacing=5)
        self.assertGreater(f["copies"], 20)
        g = pr.grid("tall", 3, 2)
        self.assertEqual(g["copies"], 6)
        xs = sorted({round(i["transform"][0][3], 3) for i in pr.object("tall")["instances"]})
        self.assertEqual(len(xs), 3)

    def test_subdivide(self):
        pr = O.Project(self.p)
        r = pr.subdivide("box")
        self.assertEqual(r["triangles"], 48)
        self.assertEqual(pr.info()["objects"][0]["size"], [20, 10, 5])

    def test_decompose_color(self):
        pr = O.Project(self.p)
        n = len(pr.mesh(pr.object("box")["parts"][0])[1])
        pr.paint("box", 1, "color", [2 if i < 4 else 0 for i in range(n)])
        r = pr.decompose_color("box")
        o = pr.object("box")
        self.assertEqual(len(o["parts"]), 2)
        self.assertEqual(o["parts"][1]["extruder"], "2")
        self.assertEqual(len(pr.mesh(o["parts"][1])[1]), 4)
        self.assertEqual(r["parts"], ["box.stl colour 2"])

    def test_apply_presets(self):
        pr = O.Project(self.p)
        pr.apply_presets({"name": "Big printer", "printable_height": "400", "nozzle_diameter": ["0.6"]}, {"name": "Strong", "wall_loops": "5"},
                         [{"name": "PETG blue", "filament_type": ["PETG"], "filament_colour": ["#0000FF"]}])
        self.assertEqual(pr.config["printer_settings_id"], "Big printer")
        self.assertEqual(pr.config["wall_loops"], "5")
        self.assertEqual(pr.config["filament_type"][0], "PETG")
        self.assertEqual(pr.config["filament_settings_id"][0], "PETG blue")

    def test_export_amf_and_obj_reader(self):
        pr = O.Project(self.p)
        a = pr.export_amf(os.path.join(self.d, "o.amf"))
        root = ET.parse(a).getroot()
        self.assertEqual(len(root.findall("object")), 2)
        o = pr.export_obj(os.path.join(self.d, "o.obj"))
        self.assertEqual(len(O.read_obj(o)), 24)

    def test_options_table(self):
        t = O.options()["options"]
        self.assertGreater(len(t), 700)
        self.assertEqual(t["sparse_infill_density"]["type"], "percent")
        self.assertIn("tree(auto)", t["support_type"]["values"])
        # Keys and limits only: no labels or help text in the table.
        self.assertFalse(any("label" in v or "tooltip" in v for v in t.values()))


if __name__ == "__main__":
    unittest.main()
