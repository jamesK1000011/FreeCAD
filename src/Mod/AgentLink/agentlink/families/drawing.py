# SPDX-License-Identifier: LGPL-2.1-or-later
# Drawings and documents from the bodies: a dimensioned sheet of standard
# views (a TechDraw page in the document, and SVG, PDF and DXF files), an
# assembly manual with exploded steps and a bill of materials, and patent
# figures with reference numerals and a draft to start a patent attorney's
# work from. The views are Open CASCADE's exact hidden-line removal (what
# TechDraw itself uses), written by sheets.py, so the files come out the same
# with or without the GUI.

import glob
import math
import os
import re

import FreeCAD
import TechDraw  # noqa: F401 — the family stands on TechDraw; left out without it

from .. import geometry as geo
from .. import sheets, store, views
from ..protocol import Tool, ToolError

GROUP = "drawing"
INFO = "Drawings and documents: dimensioned views as a TechDraw page with SVG, PDF and DXF; assembly manuals with exploded steps and a bill of materials; patent figures and a draft."

V = FreeCAD.Vector
ORTHO = ("front", "back", "top", "bottom", "right", "left")


def _obj(props, required=()):
    return {"type": "object", "properties": props, "required": list(required), "additionalProperties": False}


def _safe(name):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_") or "drawing"


def _num(v):
    return f"{v:.1f}".rstrip("0").rstrip(".")


def _template(paper):
    """TechDraw's blank template for the paper, where this build keeps it."""
    base = os.path.join(FreeCAD.getResourceDir(), "Mod", "TechDraw", "Templates")
    want = {"A4": "A4_Landscape_blank.svg", "A3": "A3_Landscape_blank.svg", "A4P": "A4_Portrait_blank.svg"}[paper]
    hits = glob.glob(os.path.join(base, "**", want), recursive=True)
    return hits[0] if hits else None


def _bodies(s, refs):
    objs = [s.body(r) for r in refs] if refs else s.bodies()
    if not objs:
        raise ToolError("not_found", "No body in the document: make one first (gen_make, gen_part).")
    seen, out = set(), []
    for o in objs:
        if o.Label not in seen:
            seen.add(o.Label)
            out.append(o)
    return out


def _write(s, base, sheet, kinds):
    paths = {}
    for k in kinds:
        p = os.path.join(s.out_dir, f"{base}.{k}")
        data = {"svg": sheet.svg, "pdf": sheet.pdf, "dxf": sheet.dxf}[k]()
        with open(p, "wb" if isinstance(data, bytes) else "w", **({} if isinstance(data, bytes) else {"encoding": "utf-8"})) as f:
            f.write(data)
        paths[k] = p
    return paths


def _techdraw_page(doc, objs, placed, scale, paper, label, dims):
    """A TechDraw page holding the same views, for the person to carry on
    with in the GUI: (page label, what was made) or (None, why not)."""
    try:
        page = doc.addObject("TechDraw::DrawPage", "Page")
        page.Label = label
        tpl = doc.addObject("TechDraw::DrawSVGTemplate", "Template")
        path = _template(paper)
        if path:
            tpl.Template = path
        page.Template = tpl
        H = sheets.PAPER[paper][1]
        made, dimmed = [], 0
        for view, (x, y) in placed:
            z, xd = views.VIEWS[view]
            v = doc.addObject("TechDraw::DrawViewPart", "View")
            page.addView(v)
            v.Label = view.capitalize()
            v.Source = objs
            v.Direction = V(*z)
            v.XDirection = V(*xd)
            v.ScaleType = "Custom"
            v.Scale = scale
            v.X = x
            v.Y = H - y
            made.append(v)
        doc.recompute()
        if dims:
            for v in made:
                if v.Label.lower() in ("front", "top"):
                    for direction in (0, 1):
                        try:
                            if TechDraw.makeExtentDim(v, [], direction) is not None:
                                dimmed += 1
                        except Exception:  # an extent TechDraw cannot read headless: the files carry it
                            pass
            doc.recompute()
        return page.Label, {"views": len(made), "extentDimensions": dimmed, "template": os.path.basename(path) if path else None}
    except Exception as e:
        return None, f"the TechDraw page could not be made here ({e}); the files stand on their own"


def tools(session):
    s = session

    # ---- drawing_make ----------------------------------------------------------------------

    def drawing_make(a):
        objs = _bodies(s, a.get("bodies"))
        want = a.get("views") or ["front", "top", "right", "iso"]
        bad = [v for v in want if v not in views.VIEWS]
        if bad:
            raise ToolError("invalid_input", f"views: {bad[0]} is not one — they are: {', '.join(views.VIEWS)}.")
        want = list(dict.fromkeys(want))
        projection = a.get("projection", "first")
        if projection not in sheets.GRID:
            raise ToolError("invalid_input", "projection: first (ISO) or third (ASME).")
        paper = a.get("paper", "auto")
        if paper not in ("A4", "A3", "auto"):
            raise ToolError("invalid_input", "paper: A4, A3 or auto.")
        shapes = [o.Shape for o in objs]
        drawn = {v: views.project(shapes, v, hidden=bool(a.get("hidden"))) for v in want}
        sizes = {v: (d["box"][2] - d["box"][0], d["box"][3] - d["box"][1]) for v, d in drawn.items()}
        sc = a.get("scale")
        if sc is not None and (not isinstance(sc, (int, float)) or sc <= 0):
            raise ToolError("invalid_input", "scale: a number over 0 — 0.5 for 1:2, 2 for 2:1. Left out, the largest standard scale that fits.")
        tries = ["A4", "A3"] if paper == "auto" else [paper]
        lay, err = None, None
        for pp in tries:
            try:
                got = sheets.layout(sizes, sheets.PAPER[pp], projection, sc)
            except ValueError as e:
                err = str(e)
                continue
            # auto: A3 only when A4 would need a smaller standard scale.
            if lay is None or got["scale"] > lay[1]["scale"]:
                lay = (pp, got)
        if lay is None:
            raise ToolError("invalid_input", err)
        paper, L = lay
        scale = L["scale"]
        name = a.get("name") or (objs[0].Label if len(objs) == 1 else "Assembly")
        sheet = sheets.Sheet(*sheets.PAPER[paper], title=name)
        sheet.frame()
        bb = shapes[0].BoundBox
        for sh in shapes[1:]:
            bb.add(sh.BoundBox)
        extent = {"x": bb.XLength, "y": bb.YLength, "z": bb.ZLength}
        done, dims = set(), []
        for v in want:
            d = drawn[v]
            cx, cy = L["centres"][v]
            sheet.place(d["lines"], d["box"], (cx, cy), scale)
            w, h = sizes[v][0] * scale, sizes[v][1] * scale
            sheet.text(cx, cy - h / 2 - 4, 3.0, v.upper(), "middle")
            if a.get("dimensions", True) and v in views.AXES:
                ha, va = views.AXES[v]
                left, right, top, bottom = cx - w / 2, cx + w / 2, cy - h / 2, cy + h / 2
                if ha not in done:
                    sheet.dimension((left, bottom), (right, bottom), 8, _num(extent[ha]))
                    done.add(ha)
                    dims.append({"view": v, "axis": ha, "mm": round(extent[ha], 3)})
                if va not in done:
                    sheet.dimension((right, bottom), (right, top), 8, _num(extent[va]))
                    done.add(va)
                    dims.append({"view": v, "axis": va, "mm": round(extent[va], 3)})
        sheets.title_block(sheet, [name, f"Scale {sheets.scale_text(scale)}   {projection.capitalize()} angle projection   mm", f"{paper}   {', '.join(o.Label for o in objs)}"[:70]])
        paths = _write(s, _safe(a.get("file") or name), sheet, ("svg", "pdf", "dxf"))
        page, made = (None, "not asked for")
        if a.get("page", True):
            page, made = _techdraw_page(s.doc, objs, [(v, L["centres"][v]) for v in want], scale, paper, f"{name} drawing", a.get("dimensions", True))
        s.snapshot("drawing_make")
        out = {
            "files": paths,
            "paper": paper,
            "scale": sheets.scale_text(scale),
            "projection": projection,
            "views": [{"view": v, "at": [round(c, 1) for c in L["centres"][v]], "size": [round(sizes[v][0] * scale, 1), round(sizes[v][1] * scale, 1)]} for v in want],
            "dimensions": dims,
            "techdrawPage": page,
            "step": len(s.steps),
        }
        out["page" if page else "pageNote"] = made
        if a.get("dimensions", True):
            out["note"] = "Overall sizes only, each once. The PDF and DXF are written from the same lines as the SVG, with or without the GUI."
        return out

    # ---- drawing_exploded ------------------------------------------------------------------

    def drawing_exploded(a):
        from ..tools import DENSITY

        steps = a.get("steps")
        if not isinstance(steps, list) or not steps or not all(isinstance(x, dict) and x.get("add") for x in steps):
            raise ToolError("invalid_input", "steps: [{ title, add: [bodies], note }], one or more, in the order they go together.")
        order, seen = [], {}
        for i, st in enumerate(steps):
            for b in st["add"]:
                o = s.body(b)
                if o.Label in seen:
                    raise ToolError("invalid_input", f"steps.{i}: {o.Label} goes in at step {seen[o.Label] + 1} already — a body is added once.")
                seen[o.Label] = i
                order.append(o)
        missing = [o.Label for o in s.bodies() if o.Label not in seen]
        direction = a.get("direction", "up")
        if direction not in ("up", "out"):
            raise ToolError("invalid_input", "direction: up (each new part lifted over its place) or out (moved away from the middle).")
        bb = order[0].Shape.BoundBox
        for o in order[1:]:
            bb.add(o.Shape.BoundBox)
        explode = float(a.get("explode") or max(10.0, 0.6 * max(bb.XLength, bb.YLength, bb.ZLength)))
        mid = bb.Center

        # The bill of materials: bodies the same shape are one item.
        items, item_of = [], {}
        mats = store.materials(s.doc)
        for o in order:
            st = geo.stats(o.Shape)
            key = (st["volume"], tuple(sorted(st["box"])), st["faces"])
            hit = next((it for it in items if it["key"] == key), None)
            if hit is None:
                m = mats.get(o.Label)
                setup = store.setup_of(s.doc, o.Label)
                hit = {
                    "key": key,
                    "item": len(items) + 1,
                    "part": o.Label,
                    "bodies": [],
                    "size": [round(v, 1) for v in st["box"]],
                    "material": m or "",
                    "massG": round(o.Shape.Volume * DENSITY[m] / 1000.0, 1) if m in DENSITY else None,
                    "madeBy": setup["type"] if setup else "",
                }
                items.append(hit)
            hit["bodies"].append(o.Label)
            item_of[o.Label] = hit["item"]

        base = _safe(a.get("name") or "assembly")
        sheet_w, sheet_h = 210.0, 148.0
        files, made_steps = [], []
        for i, st in enumerate(steps):
            new = [s.body(b) for b in st["add"]]
            before = [o for o in order if seen[o.Label] < i]
            moved = []
            for o in new:
                if direction == "up":
                    off = V(0, 0, explode)
                else:
                    c = o.Shape.BoundBox.Center
                    d = V(c.x - mid.x, c.y - mid.y, 0)
                    off = d.normalize() * explode if d.Length > 1e-6 else V(0, 0, explode)
                sh = o.Shape.copy()
                sh.translate(off)
                moved.append((o, sh, off))
            shapes = [o.Shape for o in before] + [m[1] for m in moved]
            d = views.project(shapes, "iso")
            bw, bh = d["box"][2] - d["box"][0], d["box"][3] - d["box"][1]
            scale = 0.85 * min((sheet_w - 30) / max(bw, 1e-6), (sheet_h - 36) / max(bh, 1e-6))
            sheet = sheets.Sheet(sheet_w, sheet_h, title=f"Step {i + 1}")
            centre = (sheet_w / 2, sheet_h / 2 + 4)
            sheet.place(d["lines"], d["box"], centre, scale)
            bx, by = (d["box"][0] + d["box"][2]) / 2, (d["box"][1] + d["box"][3]) / 2

            def paper(p):
                x, y = views.point(p, "iso")
                return (centre[0] + (x - bx) * scale, centre[1] - (y - by) * scale)

            for o, sh, off in moved:
                home = o.Shape.BoundBox.Center
                a0, a1 = paper(home + off), paper(home)
                sheet.line([a0, a1], "trail")
                sheet.arrow(a1, a0, 2.0)
                # A balloon with the item number, off to the upper right.
                bxy = (a0[0] + 12, a0[1] - 10)
                sheet.circle(bxy[0], bxy[1], 3.5)
                sheet.line([a0, (bxy[0] - 2.5, bxy[1] + 2.5)], "thin")
                sheet.text(bxy[0], bxy[1] + 1.2, 3.2, str(item_of[o.Label]), "middle")
            sheet.text(10, 12, 5, f"Step {i + 1}" + (f" — {st['title']}" if st.get("title") else ""))
            p = os.path.join(s.out_dir, f"{base}_step{i + 1:02d}.svg")
            with open(p, "w", encoding="utf-8") as f:
                f.write(sheet.svg())
            files.append(p)
            counts = {}
            for o in new:
                counts[item_of[o.Label]] = counts.get(item_of[o.Label], 0) + 1
            made_steps.append({"step": i + 1, "title": st.get("title", ""), "adds": [{"item": k, "part": items[k - 1]["part"], "qty": n} for k, n in counts.items()], "svg": p})

        md = [f"# {a.get('title') or a.get('name') or 'Assembly'}", ""]
        md += ["## Bill of materials", "", "| Item | Part | Qty | Size (mm) | Material | Mass (g) | Made by |", "|---|---|---|---|---|---|---|"]
        for it in items:
            md.append(f"| {it['item']} | {it['part']} | {len(it['bodies'])} | {' x '.join(_num(v) for v in it['size'])} | {it['material'] or '—'} | {it['massG'] if it['massG'] is not None else '—'} | {it['madeBy'] or '—'} |")
        md.append("")
        for ms, st in zip(made_steps, steps):
            md += [f"## Step {ms['step']}" + (f" — {ms['title']}" if ms["title"] else ""), "", f"![Step {ms['step']}]({os.path.basename(ms['svg'])})", ""]
            md.append("Add " + ", ".join(f"{x['qty']} x {x['part']} (item {x['item']})" for x in ms["adds"]) + ".")
            if st.get("note"):
                md += ["", st["note"]]
            md.append("")
        mp = os.path.join(s.out_dir, f"{base}_assembly.md")
        with open(mp, "w", encoding="utf-8") as f:
            f.write("\n".join(md))
        out = {
            "manual": mp,
            "steps": made_steps,
            "bom": [{k: v for k, v in it.items() if k != "key"} for it in items],
            "explode": round(explode, 1),
        }
        if missing:
            out["notInAnyStep"] = missing
        return out

    # ---- patent_figures --------------------------------------------------------------------

    def patent_figures(a):
        objs = _bodies(s, a.get("bodies"))
        want = a.get("views") or ["iso", "front", "top", "right"]
        bad = [v for v in want if v not in views.VIEWS]
        if bad:
            raise ToolError("invalid_input", f"views: {bad[0]} is not one — they are: {', '.join(views.VIEWS)}.")
        given = a.get("numerals") or {}
        numerals = {}
        step = 10 if len(objs) <= 9 else 2
        n = 10
        for o in objs:
            g = given.get(o.Label)
            if g is not None:
                if not isinstance(g, int) or g <= 0:
                    raise ToolError("invalid_input", f"numerals.{o.Label}: a whole number over 0.")
                numerals[o.Label] = g
        used = set(numerals.values())
        if len(used) != len(numerals):
            raise ToolError("invalid_input", "numerals: each part its own number.")
        for o in objs:
            if o.Label not in numerals:
                while n in used:
                    n += step
                numerals[o.Label] = n
                used.add(n)
        unknown = [k for k in given if k not in numerals]
        if unknown:
            raise ToolError("not_found", f"numerals: {unknown[0]} is not among the bodies drawn — {', '.join(numerals)}.")
        base = _safe(a.get("name") or "patent")
        # 37 CFR 1.84 margins on A4: top 2.5 cm, left 2.5, right 1.5, bottom 1.0.
        W, H = sheets.PAPER["A4P"]
        left, top, right, bottom = 25.0, 25.0, 15.0, 10.0
        aw, ah = W - left - right, H - top - bottom - 20
        figs, unseen = [], {}
        shapes = [o.Shape for o in objs]
        for k, v in enumerate(want, 1):
            d = views.project(shapes, v)
            bw, bh = d["box"][2] - d["box"][0], d["box"][3] - d["box"][1]
            scale = min((aw - 60) / max(bw, 1e-6), (ah - 60) / max(bh, 1e-6))
            centre = (left + aw / 2, top + ah / 2)
            sheet = sheets.Sheet(W, H, title=f"FIG. {k}")
            sheet.place([(kk, pts) for (kk, pts) in d["lines"] if kk == "visible"], d["box"], centre, scale)
            bx, by = (d["box"][0] + d["box"][2]) / 2, (d["box"][1] + d["box"][3]) / 2
            half_w, half_h = bw * scale / 2, bh * scale / 2
            labels = []
            for o in objs:
                others = [x.Shape for x in objs if x is not o]
                p = views.visible_point(o.Shape, others, v)
                if p is None:
                    unseen.setdefault(o.Label, []).append(k)
                    continue
                x, y = views.point(p, v)
                px, py = centre[0] + (x - bx) * scale, centre[1] - (y - by) * scale
                ang = math.atan2(py - centre[1], px - centre[0])
                for _ in range(24):
                    # Out along the line from the middle through the point, past the view's box.
                    ux, uy = math.cos(ang), math.sin(ang)
                    t = min(((half_w + 14) / abs(ux)) if abs(ux) > 1e-9 else 1e9, ((half_h + 14) / abs(uy)) if abs(uy) > 1e-9 else 1e9)
                    lx, ly = centre[0] + ux * t, centre[1] + uy * t
                    if all(math.hypot(lx - qx, ly - qy) > 9 for (qx, qy) in labels):
                        break
                    ang += math.radians(9)
                labels.append((lx, ly))
                sheet.text(lx, ly + 1.75, 3.5, str(numerals[o.Label]), "middle")
                dx, dy = px - lx, py - ly
                dl = math.hypot(dx, dy) or 1.0
                sheet.line([(lx + dx / dl * 3.5, ly + dy / dl * 3.5), (px, py)], "thin")
            sheet.text(centre[0], H - bottom - 8, 6, f"FIG. {k}", "middle")
            fp = os.path.join(s.out_dir, f"{base}_fig{k}")
            with open(fp + ".svg", "w", encoding="utf-8") as f:
                f.write(sheet.svg())
            with open(fp + ".pdf", "wb") as f:
                f.write(sheet.pdf())
            figs.append({"fig": k, "view": v, "svg": fp + ".svg", "pdf": fp + ".pdf"})
        lines = [f"# Reference numerals — {a.get('name') or 'figures'}", ""]
        lines += [f"- FIG. {f['fig']} is {views.WORDS[f['view']]}." for f in figs]
        lines += [""] + [f"- {num} — {lbl}" for lbl, num in sorted(numerals.items(), key=lambda t: t[1])]
        np_ = os.path.join(s.out_dir, f"{base}_numerals.md")
        with open(np_, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        s._patent = {"name": a.get("name"), "numerals": numerals, "figures": [{"fig": f["fig"], "view": f["view"]} for f in figs]}
        out = {"figures": figs, "numerals": numerals, "list": np_, "note": "Black line art, no shading or hidden lines, on A4 within the USPTO's margins; numerals 3.5 mm high with lead lines to a face of each part."}
        if unseen:
            out["notSeenIn"] = unseen
        return out

    # ---- patent_draft ----------------------------------------------------------------------

    def patent_draft(a):
        title = a.get("title")
        if not title:
            raise ToolError("invalid_input", "title: what the invention is, a few words.")
        pat = getattr(s, "_patent", None)
        objs = _bodies(s, a.get("bodies") or (list(pat["numerals"]) if pat else None))
        numerals = dict(pat["numerals"]) if pat else {}
        n = max(numerals.values(), default=0)
        for o in objs:
            if o.Label not in numerals:
                n += 10
                numerals[o.Label] = n
        figs = pat["figures"] if pat else []
        name = lambda o: o.Label.lower()  # noqa: E731
        touch = []
        for i, o1 in enumerate(objs):
            for o2 in objs[i + 1:]:
                try:
                    if o1.Shape.distToShape(o2.Shape)[0] < 0.05:
                        touch.append((o1, o2))
                except Exception:  # a pair the distance cannot be read for: not claimed to touch
                    pass
        bb = objs[0].Shape.BoundBox
        for o in objs[1:]:
            bb.add(o.Shape.BoundBox)
        md = [
            "> **DRAFT — NOT FOR FILING.** A starting point for review by a registered patent attorney or agent. "
            "It has not been filed, sent or searched against prior art, and nothing in it is legal advice. "
            "Every bracketed passage is to be written or checked with counsel.",
            "",
            f"# {title}",
            "",
            "## Cross-reference to related applications",
            "",
            "[None, or the provisional application this claims the benefit of.]",
            "",
            "## Field",
            "",
            a.get("field") or "[The technical field, one sentence.]",
            "",
            "## Background",
            "",
            a.get("background") or "[The problem, and how it is met today — without admitting anything is prior art.]",
            "",
            "## Summary",
            "",
            "[A short statement of the invention, following claim 1 once the claims are settled.]",
            "",
            "## Brief description of the drawings",
            "",
        ]
        if figs:
            md += [f"FIG. {f['fig']} is {views.WORDS[f['view']]} of the {title.lower()}." for f in figs]
        else:
            md.append("[No figures yet: patent_figures makes them, with the numerals used here.]")
        md += ["", "## Detailed description", ""]
        md.append(f"Referring to the figures, the {title.lower()} comprises " + ", ".join(f"a {name(o)} {numerals[o.Label]}" for o in objs) + ".")
        md.append("")
        for o in objs:
            b = o.Shape.BoundBox
            mat = store.materials(s.doc).get(o.Label)
            md.append(
                f"The {name(o)} {numerals[o.Label]} measures about {_num(b.XLength)} mm by {_num(b.YLength)} mm by {_num(b.ZLength)} mm"
                + (f" and may be made of {mat.upper()}" if mat else "")
                + ". [What it does, and how.]"
            )
        for o1, o2 in touch:
            md.append(f"The {name(o1)} {numerals[o1.Label]} adjoins the {name(o2)} {numerals[o2.Label]}. [How they are joined or how one moves against the other.]")
        md += [
            "",
            f"Overall the {title.lower()} measures about {_num(bb.XLength)} mm by {_num(bb.YLength)} mm by {_num(bb.ZLength)} mm. "
            "The sizes are of the embodiment drawn; [say which are not limiting].",
            "",
            "## Claims (skeleton)",
            "",
            f"1. A {title.lower()}, comprising:",
        ]
        for i, o in enumerate(objs):
            rel = next((f" adjoining the {name(p)}" for (q, p) in touch if q is o), "")
            md.append(f"   a {name(o)}{rel};" if i < len(objs) - 1 else f"   a {name(o)}{rel}; and")
        md.append("   [wherein — the feature that sets it apart, to be written with counsel].")
        md.append("")
        for i, o in enumerate(objs[:3], 2):
            md.append(f"{i}. The {title.lower()} of claim 1, wherein the {name(o)} [a narrower feature: a size, a material, a shape].")
            md.append("")
        md += ["## Abstract", "", "[At most 150 words, written last.]", ""]
        md.append(f"Reference numerals: " + "; ".join(f"{numerals[o.Label]} {name(o)}" for o in objs) + ".")
        p = os.path.join(s.out_dir, f"{_safe(a.get('name') or title)}_patent_draft.md")
        with open(p, "w", encoding="utf-8") as f:
            f.write("\n".join(md) + "\n")
        return {"draft": p, "numerals": numerals, "figures": len(figs), "adjoining": [[o1.Label, o2.Label] for o1, o2 in touch], "note": "A draft for an attorney's review, written to the output folder only — nothing is filed or sent."}

    bodies = {"type": "array", "items": {"type": "string"}, "description": "Bodies to draw; all when left out."}
    return [
        Tool(
            "drawing_make",
            "A drawing sheet of bodies: standard views (front, top, right, iso by default; back, bottom, left too), first-angle (ISO) or third-angle (ASME) projection, "
            "at the largest standard scale that fits A4 or A3 (paper auto picks A3 only when it allows a bigger scale), overall sizes dimensioned once each "
            "(dimensions: false leaves them off), hidden: true dashes the hidden edges. Writes SVG, PDF and DXF to the output folder and adds a TechDraw page "
            "with the same views to the document (page: false leaves it out). The sizes are overall only: a part's holes and features want dimensioning in "
            "TechDraw itself.",
            _obj({
                "bodies": bodies,
                "views": {"type": "array", "items": {"type": "string", "enum": list(views.VIEWS)}},
                "projection": {"type": "string", "enum": ["first", "third"]},
                "paper": {"type": "string", "enum": ["A4", "A3", "auto"]},
                "scale": {"type": "number", "description": "0.5 for 1:2, 2 for 2:1; left out, the largest standard one that fits."},
                "dimensions": {"type": "boolean"},
                "hidden": {"type": "boolean"},
                "page": {"type": "boolean"},
                "name": {"type": "string", "description": "The title on the sheet."},
                "file": {"type": "string", "description": "The files' name, without extension."},
            }),
            drawing_make,
            changes=True,
        ),
        Tool(
            "drawing_exploded",
            "An assembly manual: steps [{ title, add: [bodies], note }] in the order the parts go together; each step an isometric view (SVG) with the parts "
            "already in place and the new ones moved off by explode mm (up over their places, or out from the middle), a trail to where each goes and a "
            "balloon with its item number. Writes the steps and <name>_assembly.md with a bill of materials (bodies the same shape are one item, with the "
            "material and mass where part_spec recorded one, and how each is made from its setup). A body goes in once.",
            _obj({
                "steps": {"type": "array", "items": {"type": "object"}},
                "explode": {"type": "number"},
                "direction": {"type": "string", "enum": ["up", "out"]},
                "name": {"type": "string"},
                "title": {"type": "string"},
            }, ["steps"]),
            drawing_exploded,
        ),
        Tool(
            "patent_figures",
            "Patent-style figures FIG. 1 … (iso, front, top, right by default): black line art on A4 within the USPTO's margins, no hidden lines or shading, "
            "each part's reference numeral (10, 20, 30 … or numerals { body: n }) with a lead line to a face of it the viewer sees. Writes an SVG and PDF per "
            "figure and a list of the numerals. A part hidden behind others in a view gets no numeral there (notSeenIn).",
            _obj({"bodies": bodies, "views": {"type": "array", "items": {"type": "string", "enum": list(views.VIEWS)}}, "numerals": {"type": "object"}, "name": {"type": "string"}}),
            patent_figures,
        ),
        Tool(
            "patent_draft",
            "A Markdown draft of a patent description and a claims skeleton from the bodies — their sizes, which adjoin which, the numerals and figures of the "
            "last patent_figures — marked as a starting point for a patent attorney's review, with what only the inventor and counsel can say left in brackets. "
            "Written to the output folder; never filed or sent anywhere.",
            _obj({"title": {"type": "string"}, "field": {"type": "string"}, "background": {"type": "string"}, "bodies": bodies, "name": {"type": "string"}}, ["title"]),
            patent_draft,
        ),
    ]
