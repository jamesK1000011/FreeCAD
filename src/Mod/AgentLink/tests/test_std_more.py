# SPDX-License-Identifier: LGPL-2.1-or-later
# The document-level, Part and PartDesign commands that were left to the
# window, each checked on a real FreeCAD against what the shapes must give.
import os

import unittest

from _pd_base import HAVE, Base


@unittest.skipUnless(HAVE, "FreeCAD with PartDesign and Sketcher is not importable here")
class StdMore(Base):
    def box(self, name, size, at=None):
        return self.ok("part_primitive", {"kind": "box", "size": size, "name": name, **({"at": at} if at else {})})

    def test_1_links(self):
        self.box("L box", [10, 10, 10])
        lk = self.ok("link_make", {"to": "L box", "at": [20, 0, 0]})
        o = self.session.doc.getObjectsByLabel(lk["link"])[0]
        self.assertEqual(o.LinkedObject.Label, "L box")
        self.box("L other", [5, 5, 5])
        self.ok("link_edit", {"action": "replace", "link": lk["link"], "to": "L other"})
        self.assertEqual(o.LinkedObject.Label, "L other")
        u = self.ok("link_edit", {"action": "unlink", "link": lk["link"], "name": "L real"})
        self.assertAlmostEqual(u["volume"], 125.0, places=3)
        g = self.ok("link_make", {"kind": "group", "objects": ["L box", "L other"], "name": "L group"})
        self.assertEqual(g["elements"], ["L box", "L other"])
        el = self.ok("link_make", {"to": "L box.Face1"})
        self.assertEqual(el["element"], "Face1")
        self.assertEqual(self.ok("link_edit", {"action": "import_all"})["imported"], [])

    def test_2_container_duplicate_text(self):
        self.box("C a", [4, 4, 4])
        p = self.ok("part_container", {"objects": ["C a"], "at": [0, 0, 50], "name": "C part"})
        self.assertEqual(p["objects"], ["C a"])
        d = self.ok("object_duplicate", {"objects": ["C a"]})
        self.assertEqual(d["duplicated"][0]["copy"], "C a copy")
        t = self.ok("text_document", {"name": "C notes", "text": "first"})
        t = self.ok("text_document", {"name": "C notes", "text": "second", "append": True})
        self.assertEqual(t["lines"], 2)

    def test_3_document(self):
        self.ok("doc_info", {"author": "Agent Link test", "comment": "made in CI"})
        i = self.ok("doc_info")
        self.assertEqual((i["author"], i["comment"]), ("Agent Link test", "made in CI"))
        self.box("D keep", [3, 3, 3])
        path = os.path.join(self.dir, "std_more.FCStd")
        self.ok("doc_save", {"path": path})
        self.box("D drop", [3, 3, 3])
        r = self.ok("doc_revert")
        self.assertEqual(r["reverted"], path)
        labels = {o.Label for o in self.session.doc.Objects}
        self.assertIn("D keep", labels)
        self.assertNotIn("D drop", labels)
        self.assertIn(path, self.ok("doc_save_all")["saved"])
        m = self.ok("doc_merge", {"path": path})
        self.assertTrue(any(x.startswith("D keep") for x in m["objects"]))

    def test_4_appearance(self):
        self.box("A box", [10, 10, 10])
        r = self.ok("object_appearance", {"objects": ["A box"], "color": "#ff0000", "transparency": 30, "faces": {"Face1": "#00ff00"}})
        self.assertEqual(r["set"][0]["color"], "#ff0000")
        o = self.session.doc.getObjectsByLabel("A box")[0]
        self.assertEqual(o.AgentLinkFaceColors[0], "#00ff00")
        self.assertEqual(o.AgentLinkFaceColors[1], "#ff0000")
        self.ok("object_appearance", {"objects": ["A box"], "random": True})
        err, out = self.call("object_appearance", {"objects": ["A box"], "faces": {"Face9": "#000000"}})
        self.assertTrue(err)

    def test_5_compounds_and_joins(self):
        self.box("K a", [10, 10, 10])
        self.box("K b", [10, 10, 10], [30, 0, 0])
        self.box("K c", [20, 20, 20], [60, 0, 0])
        self.ok("part_compound", {"bodies": ["K a", "K b", "K c"], "name": "K comp"})
        f = self.ok("compound_filter", {"compound": "K comp", "keep": "window-volume", "from": 50, "to": 100})
        self.assertEqual(f["kept"], 1)
        f2 = self.ok("compound_filter", {"compound": "K comp", "items": [0, 1]})
        self.assertEqual(f2["kept"], 2)
        e = self.ok("compound_explode", {"compound": "K comp"})
        self.assertEqual(len(e["pieces"]), 3)
        import Part

        from _fc import add_body

        add_body(self.session, "J pipe", Part.makeCylinder(10, 40).cut(Part.makeCylinder(8, 40)))
        side = Part.makeCylinder(5, 40, __import__("FreeCAD").Vector(0, -20, 20), __import__("FreeCAD").Vector(0, 1, 0))
        add_body(self.session, "J branch", side.cut(Part.makeCylinder(3, 40, __import__("FreeCAD").Vector(0, -20, 20), __import__("FreeCAD").Vector(0, 1, 0))))
        j = self.ok("part_join", {"kind": "connect", "objects": ["J pipe", "J branch"]})
        self.assertTrue(j["valid"])
        self.assertEqual(j["solids"], 1)

    def test_6_curves(self):
        import Part

        path = os.path.join(self.dir, "curves.brep")
        Part.Compound([Part.makeCircle(10), Part.makeLine((0, 0, 0), (10, 0, 5))]).exportBrep(path)
        c = self.ok("part_import_curves", {"path": path})
        self.assertEqual(c["edges"], 2)

    def test_7_partdesign(self):
        b = self.ok("pd_body_new", {"name": "P one"})
        self.ok("sketch_new", {"body": "P one", "plane": "XY", "name": "P sk"})
        self.ok("sketch_add", {"sketch": "P sk", "items": [{"kind": "rectangle", "center": [0, 0], "size": [20, 10]}]})
        pad = self.ok("pd_pad", {"sketch": "P sk", "length": 5})
        self.ok("sketch_new", {"body": "P one", "plane": "XY", "name": "P sk2"})
        self.ok("sketch_add", {"sketch": "P sk2", "items": [{"kind": "circle", "center": [5, 2], "radius": 2}]})
        pocket = self.ok("pd_pocket", {"sketch": "P sk2", "type": "through_all", "reversed": True})
        feats = [o.Label for o in self.session.doc.getObjectsByLabel("P one")[0].Group if o.TypeId.startswith("PartDesign::P")]
        self.assertEqual(len(feats), 2)
        t = self.ok("pd_tip", {"body": "P one", "feature": feats[0]})
        self.assertAlmostEqual(t["volume"], 1000.0, places=2)
        self.ok("pd_tip", {"body": "P one", "feature": feats[1]})
        r = self.ok("pd_reorder", {"feature": "P sk2", "before": feats[0]})
        self.assertLess(r["order"].index("P sk2"), r["order"].index(feats[0]))
        c = self.ok("pd_clone", {"from": "P one", "name": "P clone"})
        self.assertGreater(c["volume"], 900)
        self.ok("pd_body_new", {"name": "P two"})
        # A pocket moved into an empty body has nothing to cut: refused, and
        # nothing moved (it used to go, leaving a broken body and saying so).
        err, e = self.call("pd_move_feature", {"feature": feats[1], "to": "P two"})
        self.assertTrue(err, e)
        self.assertIn("breaks", e["error"]["message"])
        self.assertEqual(len([o for o in self.session.doc.getObjectsByLabel("P two")[0].Group if o.TypeId.startswith("PartDesign::P")]), 0)
        self.ok("pd_body_new", {"name": "P three"})
        self.ok("sketch_new", {"body": "P three", "plane": "XY", "name": "P sk3"})
        self.ok("sketch_add", {"sketch": "P sk3", "items": [{"kind": "rectangle", "center": [0, 0], "size": [5, 5]}]})
        self.ok("pd_pad", {"body": "P three", "sketch": "P sk3", "length": 3})
        pad3 = next(o.Label for o in self.session.doc.getObjectsByLabel("P three")[0].Group if o.TypeId == "PartDesign::Pad")
        m = self.ok("pd_move_feature", {"feature": pad3, "to": "P two"})
        self.assertIn("P sk3", m["moved"])
        del b, pad, pocket

    def test_8_shaft(self):
        s = self.ok("pd_shaft", {"segments": [{"length": 20, "diameter": 10}, {"length": 30, "diameter": 16}, {"length": 10, "diameter": 16, "diameterEnd": 10}]})
        import math

        want = math.pi * (5 ** 2 * 20 + 8 ** 2 * 30) + math.pi * 10 / 3 * (8 ** 2 + 8 * 5 + 5 ** 2)
        self.assertAlmostEqual(s["volume"], want, delta=want * 1e-3)
        self.assertEqual(s["length"], 60.0)

    def test_9_other_workbenches(self):
        import FreeCAD
        import Mesh
        import Part

        from _fc import add_body

        # OpenSCAD tidy: colour by type, explode a fusion.
        self.box("S a", [10, 10, 10])
        self.box("S b", [10, 10, 10], [5, 0, 0])
        f = self.session.doc.addObject("Part::MultiFuse", "Fusion")
        f.Shapes = [self.session.doc.getObjectsByLabel(n)[0] for n in ("S a", "S b")]
        f.Label = "S fusion"
        self.session.doc.recompute()
        cc = self.ok("scad_tidy", {"action": "color_code", "objects": ["S fusion"]})
        self.assertEqual(cc["coloured"][0]["is"], "Solid")
        ex = self.ok("scad_tidy", {"action": "explode", "objects": ["S fusion"]})
        self.assertEqual(sorted(ex["exploded"][0]["pieces"]), ["S a", "S b"])
        # A quadric patch from points on z = 0.01 (x² + y²).
        m = Mesh.Mesh()
        pts = [FreeCAD.Vector(x, y, 0.01 * (x * x + y * y)) for x in range(-10, 11, 2) for y in range(-10, 11, 2)]
        tris = []
        n = 11
        for i in range(n - 1):
            for j in range(n - 1):
                a, b, c, d = i * n + j, (i + 1) * n + j, (i + 1) * n + j + 1, i * n + j + 1
                tris += [[pts[a], pts[b], pts[c]], [pts[a], pts[c], pts[d]]]
        m.addFacets(tris)
        mo = self.session.doc.addObject("Mesh::Feature", "Bowl")
        mo.Mesh = m
        mo.Label = "S bowl"
        poly = self.ok("surface_polynomial", {"from": "S bowl"})
        self.assertLess(poly["max"], 0.01)
        # Spreadsheet cells.
        sh = self.session.doc.addObject("Spreadsheet::Sheet", "Sheet")
        sh.Label = "S sheet"
        c = self.ok("sheet_cells", {"sheet": "S sheet", "merge": "A1:C1"})
        self.assertTrue(c["merged"])
        self.ok("sheet_cells", {"sheet": "S sheet", "split": "A1"})
        # Structured points: 4 × 3.
        try:
            import Points
        except ImportError:
            return
        pk = Points.Points()
        pk.addPoints([(x, y, 0) for y in range(3) for x in range(4)])
        po = self.session.doc.addObject("Points::Feature", "Pts")
        po.Points = pk
        po.Label = "S pts"
        self.assertEqual(self.ok("points_structure", {"points": "S pts", "width": 4})["height"], 3)
        err, _ = self.call("points_structure", {"points": "S pts", "width": 5})
        self.assertTrue(err)
        del add_body, Part

    def test_10_resorted_from_the_window(self):
        # Std commands the unattended re-sort (O) moved from window-only to headless
        self.box("G base", [10, 10, 10])
        g = self.ok("doc_graph", {"path": "graph"})
        self.assertTrue(g["path"].endswith(".dot") and os.path.isfile(g["path"]))
        self.assertIn("G base", g["roots"])
        u = self.ok("units_convert", {"value": "2 in", "to": "mm"})
        self.assertAlmostEqual(u["result"], 50.8, places=6)
        self.assertIn("different", self.refused("units_convert", {"value": "2 in", "to": "kg"})["message"])
        import FreeCAD

        if FreeCAD.GuiUp:
            fr = self.ok("object_freeze", {"objects": ["G base"]})
            self.assertEqual(fr.get("frozen"), ["G base"])
            self.ok("object_freeze", {"objects": ["G base"], "frozen": False})
        else:
            self.assertEqual(self.refused("object_freeze", {"objects": ["G base"]})["code"], "unavailable")
        sk = self.ok("object_freeze", {"skipRecompute": True})
        self.assertTrue(sk["skipRecompute"])
        self.ok("object_freeze", {"skipRecompute": False})
        self.box("G moved", [10, 10, 10])
        # turn 90 degrees about z and move by (5, 0, 0)
        P = [[0, 0, 0], [10, 0, 0], [0, 10, 0], [0, 0, 10]]
        Q = [[5 - y, x, z] for x, y, z in P]
        r = self.ok("object_align", {"object": "G moved", "pairs": [[p, q] for p, q in zip(P, Q)]})
        self.assertLess(r["rms"], 1e-6)
        self.assertAlmostEqual(abs(r["placement"]["angle"]), 90.0, places=3)
        o = self.session.doc.getObjectsByLabel("G moved")[0]
        bb = o.Shape.BoundBox
        self.assertAlmostEqual(bb.XMin, -5.0, places=4)
        self.assertAlmostEqual(bb.YMin, 0.0, places=4)
        self.assertIn("recent", self.ok("doc_open", {"recent": True}))


if __name__ == "__main__":
    unittest.main()
