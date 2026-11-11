# SPDX-License-Identifier: LGPL-2.1-or-later
# The TechDraw tools on a real FreeCAD: skipped where it cannot be imported.
import os
import unittest

from _pd_base import HAVE, Base

try:
    import TechDraw  # noqa: F401

    HAVE_TD = HAVE
except ImportError:
    HAVE_TD = False


@unittest.skipUnless(HAVE_TD, "FreeCAD with TechDraw is not importable here")
class TechDrawTools(Base):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        call = lambda n, a: cls.server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": n, "arguments": a}})  # noqa: E731
        call("pd_body_new", {"name": "Plate"})
        call("sketch_new", {"plane": "XY", "name": "Outline"})
        call("sketch_add", {"sketch": "Outline", "items": [{"kind": "rectangle", "corner": [0, 0], "size": [60, 40], "dims": True, "name": "plate"},
                                                           {"kind": "circle", "center": [30, 20], "diameter": 10}]})
        call("pd_pad", {"sketch": "Outline", "length": 8})

    def test_1_page_and_title(self):
        p = self.ok("td_page", {"action": "new", "paper": "A4", "projection": "first", "name": "Plate drawing"})
        self.assertEqual(p["page"], "Plate drawing")
        fields = self.ok("td_title", {"page": "Plate drawing"})["fields"]
        if fields:
            k = sorted(fields)[0]
            out = self.ok("td_title", {"page": "Plate drawing", "fields": {k: "AL-001"}})
            self.assertEqual(out["fields"][k], "AL-001")
        self.refused("td_title", {"page": "Plate drawing", "fields": {"nosuchfield": "x"}})

    def test_2_views_and_dims(self):
        g = self.ok("td_projection", {"page": "Plate drawing", "views": ["front", "top"], "scale": 1})
        self.assertGreaterEqual(len(g["items"]), 2)
        top = next(x["view"] for x in g["items"] if "Top" in x["view"])
        circles = self.ok("td_geometry", {"view": top, "kind": "circles"})["items"]
        self.assertTrue(any(abs(c.get("diameter", 0) - 10) < 0.01 for c in circles))
        d = self.ok("td_dim", {"view": top, "type": "diameter", "refs": [{"circle": {"diameter": 10}}], "fit": "H7"})
        self.assertAlmostEqual(d["measured"], 10, places=3)
        self.assertIn("tolerance", d)
        w = self.ok("td_dim", {"view": top, "type": "horizontal", "refs": [{"point": [0, 0, 8]}, {"point": [60, 0, 8]}], "plus": 0.1})
        self.assertAlmostEqual(w["measured"], 60, places=2)
        self.refused("td_dim", {"view": top, "type": "radius", "refs": [{"point": [0, 0, 8]}]})
        self.refused("td_dim", {"view": top, "type": "diameter", "refs": [{"circle": {"diameter": 99}}]})

    def test_3_section_detail_gdt(self):
        page = self.ok("td_list", {"page": "Plate drawing"})
        top = next(i["view"] for v in page["views"] for i in v.get("items", []) if "Top" in i["view"])
        s = self.ok("td_section", {"view": top, "normal": "-y", "through": [30, 20, 4]})
        self.assertGreater(s["edges"], 0)
        dt = self.ok("td_detail", {"view": top, "center": [0, 0, 8], "radius": 8})
        self.assertGreater(dt["edges"], 0)
        self.ok("td_datum", {"view": top, "letter": "A", "on": {"edge": {"through": [[0, 0, 8], [60, 0, 8]]}}})
        f = self.ok("td_gdt", {"view": top, "on": {"circle": {"diameter": 10}}, "frame": [{"characteristic": "position", "tolerance": 0.1, "diameter": True, "datums": ["A"]}]})
        self.assertIn("⌖", f["reads"])
        self.refused("td_gdt", {"view": top, "on": "Edge0", "frame": [{"characteristic": "perpendicularity", "tolerance": 0.1}]})

    def test_4_notes_tables_export(self):
        self.ok("td_note", {"page": "Plate drawing", "text": "Break sharp edges\nAll dims mm"})
        self.ok("sheet_set", {"sheet": self.ok("sheet_new", {"name": "Parts"})["made"], "rows": [["Item", "Part", "Qty"], [1, "Plate", 1]], "at": "A1"})
        self.ok("td_table", {"page": "Plate drawing", "sheet": "Parts"})
        e = self.ok("td_export", {"page": "Plate drawing"})
        for k in ("svg", "pdf", "dxf"):
            self.assertTrue(os.path.getsize(e["files"][k]["path"]) > 500, k)
        lst = self.ok("td_list", {"page": "Plate drawing"})
        self.assertGreaterEqual(len(lst["dimensions"]), 2)
        self.assertEqual(len(lst["gdt"]), 2)


if __name__ == "__main__":
    unittest.main()
