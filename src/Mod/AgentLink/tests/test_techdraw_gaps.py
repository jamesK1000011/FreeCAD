# SPDX-License-Identifier: LGPL-2.1-or-later
# TechDraw's remaining commands headless: dimension text, special and
# aligned dimensions, repair, more views, line styles and cosmetic edits.
import json
import os
import tempfile
import unittest

from _pd_base import HAVE, Base

try:
    import TechDraw  # noqa: F401

    HAVE_TD = HAVE
except ImportError:
    HAVE_TD = False


@unittest.skipUnless(HAVE_TD, "FreeCAD with TechDraw is not importable here")
class TechDrawGaps(Base):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        call = lambda n, a: cls.server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": n, "arguments": a}})  # noqa: E731
        call("pd_body_new", {"name": "Plate"})
        call("sketch_new", {"plane": "XY", "name": "Outline"})
        call("sketch_add", {"sketch": "Outline", "items": [{"kind": "rectangle", "corner": [0, 0], "size": [60, 40]}, {"kind": "circle", "center": [30, 20], "diameter": 10}]})
        call("pd_pad", {"sketch": "Outline", "length": 8})
        call("td_page", {"action": "new", "paper": "A4", "name": "Gaps"})

    def dim(self, view, a, b, typ="horizontal"):
        return self.ok("td_dim", {"view": view, "type": typ, "refs": [{"point": a}, {"point": b}]})

    def test_1_dims(self):
        v = self.ok("td_view", {"page": "Gaps", "bodies": ["Plate"], "direction": "top", "scale": 1, "at": [80, 120], "name": "G top"})["view"]
        w = self.dim(v, [0, 0, 8], [60, 0, 8])
        h = self.dim(v, [60, 0, 8], [60, 40, 8], "vertical")
        f = self.ok("td_dim_format", {"dims": [w["dimension"]], "decimals": 1, "prefix": "count", "count": 4})
        self.assertIn("4×", f["dims"][0]["format"])
        self.assertIn("%.1", f["dims"][0]["format"])
        f = self.ok("td_dim_format", {"dims": [w["dimension"]], "removePrefix": True, "more": 1})
        self.assertTrue(f["dims"][0]["format"].startswith("%.2"))
        self.ok("td_dim_format", {"dims": [h["dimension"]], "prefix": "diameter"})
        e = self.ok("td_dim_special", {"view": v, "kind": "extent_h"})
        self.assertAlmostEqual(e["measured"], 60, delta=0.1)
        w2 = self.dim(v, [0, 40, 8], [60, 40, 8])
        a = self.ok("td_dim_align", {"dims": [w["dimension"], w2["dimension"]], "direction": "horizontal"})
        self.assertEqual(a["dims"][0]["at"][1], a["dims"][1]["at"][1])
        r = self.ok("td_dim_repair", {"dimension": h["dimension"], "refs": [{"point": [0, 0, 8]}, {"point": [0, 40, 8]}]})
        self.assertAlmostEqual(r["measured"], 40, delta=0.05)

    def test_2_views(self):
        v = self.ok("td_view", {"page": "Gaps", "bodies": ["Plate"], "direction": "front", "scale": 1, "at": [200, 120], "name": "G front"})["view"]
        c = self.ok("td_view_more", {"page": "Gaps", "kind": "clip", "width": 40, "height": 30, "at": [60, 50], "name": "G clip"})
        self.ok("td_view_more", {"kind": "clip_add", "clip": c["view"], "view": v})
        svg = os.path.join(tempfile.mkdtemp(), "mark.svg")
        with open(svg, "w") as f:
            f.write('<svg xmlns="http://www.w3.org/2000/svg" width="10mm" height="10mm"><circle cx="5" cy="5" r="4"/></svg>')
        self.ok("td_view_more", {"page": "Gaps", "kind": "symbol", "path": svg, "at": [20, 20]})
        self.ok("td_page", {"action": "new", "paper": "A4", "name": "Gaps 2"})
        sh = self.ok("td_view_more", {"page": "Gaps 2", "kind": "share", "view": v})
        self.assertEqual(sh["alsoOn"], "Gaps 2")

    def test_3_lines(self):
        v = self.ok("td_view", {"page": "Gaps", "bodies": ["Plate"], "direction": "top", "scale": 1, "at": [80, 60], "name": "G lines"})["view"]
        from agentlink.families import techdraw as T

        view = self.session.doc.getObjectsByLabel(v)[0]
        # a slanted line, so a flipped y would show
        native = T.native_ok()
        c = self.ok("td_cosmetic", {"view": v, "items": [{"kind": "line", "from": [0, 10, 8], "to": [60, 30, 8]}]})
        self.assertEqual(c["native"], 1 if native else 0, "FreeCAD's own cosmetic edge, not only our export's")
        self.assertEqual(len(view.CosmeticEdges), 1 if native else 0)

        def lands():
            self.session.doc.recompute()
            keys = T.cosmetic_keys(view)
            return any(T.edge_key(e) in keys for e in view.getVisibleEdges())

        if native:
            self.assertTrue(lands(), "the native edge lies where the figure does")
        x = self.ok("td_lines", {"view": v, "action": "extend", "items": [0], "by": 3})
        self.assertEqual(x["extended"], [0])
        if native:
            self.assertEqual(len(view.CosmeticEdges), 1)
            self.assertTrue(lands())
        self.ok("td_lines", {"view": v, "action": "style", "refs": [{"edge": {"through": [[0, 0, 8], [60, 0, 8]]}}], "style": "dashed", "weight": 0.5})
        t = self.ok("td_lines", {"view": v, "action": "align_v", "refs": [{"point": [0, 0, 8]}, {"point": [60, 40, 8]}]})
        self.assertNotEqual(t["turned"], 0)
        er = self.ok("td_lines", {"view": v, "action": "erase"})
        self.assertEqual(er["left"], 0)
        self.assertEqual(len(view.CosmeticEdges), 0)

    def test_4_special_lengths_stay_true(self):
        from agentlink.families import techdraw as T

        v = self.ok("td_view", {"page": "Gaps", "bodies": ["Plate"], "direction": "top", "scale": 1, "at": [200, 60], "name": "G arc"})["view"]
        view = self.session.doc.getObjectsByLabel(v)[0]
        arc = next(f"Edge{i}" for i, e in enumerate(T.edges_of(view)) if hasattr(getattr(e, "Curve", None), "Radius"))
        r = self.ok("td_dim_special", {"view": v, "kind": "arc_length", "refs": [arc]})
        self.assertIn("31.42", r["text"] if "text" in r else self.session.doc.getObject(r["dimension"]).FormatSpec)
        # a new scale: the length on the part stays the same
        view.ScaleType = "Custom"
        view.Scale = 2
        self.ok("td_dim_format", {"dims": [r["dimension"]], "decimals": 2})
        self.assertIn("31.42", self.session.doc.getObject(r["dimension"]).FormatSpec)

        iso = self.ok("td_view", {"page": "Gaps", "bodies": ["Plate"], "direction": "iso", "scale": 1, "at": [200, 200], "name": "G iso"})["view"]
        iv = self.session.doc.getObjectsByLabel(iso)[0]
        lines = [(i, e) for i, e in enumerate(T.edges_of(iv)) if e.Curve.__class__.__name__ in ("Line", "LineSegment")]
        longest = max(lines, key=lambda t: t[1].Length)
        other = next(t for t in lines if t[0] != longest[0])
        r = self.ok("td_dim_special", {"view": iso, "kind": "axo_length", "refs": [f"Edge{longest[0]}", f"Edge{other[0]}"]})
        self.assertIn(self.session.doc.getObject(r["dimension"]).FormatSpec, ("60.00", "40.00"))

if __name__ == "__main__":
    unittest.main()
