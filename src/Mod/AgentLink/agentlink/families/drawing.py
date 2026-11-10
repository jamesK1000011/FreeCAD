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
from .. import iso286, sheets, store, views
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


def _techdraw_page(doc, objs, placed, scale, paper, label, dims, specs=(), drawn=None, section=None):
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
            made.append((view, v))
        doc.recompute()
        sec_note = {}
        if section and section.get("cutLineIn"):
            # The section view on the page: TechDraw's own, based on the view
            # that shows the cutting plane edge on, where the plain view was.
            try:
                base = dict(made)[section["cutLineIn"]]
                plain = dict(made)[section["view"]]
                z, xd = views.VIEWS[section["view"]]
                sv = doc.addObject("TechDraw::DrawViewSection", "Section")
                page.addView(sv)
                sv.BaseView = base
                sv.Source = objs
                sv.ScaleType = "Custom"
                sv.Scale = scale
                sv.SectionNormal = V(*z)
                sv.Direction = V(*z)
                sv.XDirection = V(*xd)
                sv.SectionOrigin = section["origin"]
                sv.SectionSymbol = section["label"].split("-")[0]
                sv.X, sv.Y = plain.X, plain.Y
                page.removeView(plain)
                doc.removeObject(plain.Name)
                made = [(n, v) for n, v in made if v is not plain] + [(section["view"], sv)]
                doc.recompute()
                sec_note = {"section": sv.Label}
            except Exception as e:
                sec_note = {"sectionNote": f"the section view could not go on the page here ({e}); the files have it"}
        if dims:
            for view, v in made:
                if view in ("front", "top"):
                    for direction in (0, 1):
                        try:
                            if TechDraw.makeExtentDim(v, [], direction) is not None:
                                dimmed += 1
                        except Exception:  # an extent TechDraw cannot read headless: the files carry it
                            pass
            doc.recompute()
        out = {"views": len(made), "extentDimensions": dimmed, "template": os.path.basename(path) if path else None, **sec_note}
        if specs:
            try:
                waited = _wait_views([v for _, v in made])
                done, missed = _page_dims(doc, page, made, drawn, specs)
                out["dimensions"] = done
                if missed:
                    out["notOnPage"] = missed
                if waited is not None:
                    out["viewsReadyIn"] = waited
            except Exception as e:  # the page stands without them
                out["notOnPage"] = [{"why": f"TechDraw could not take dimensions here: {e}"}]
        return page.Label, out
    except Exception as e:
        return None, f"the TechDraw page could not be made here ({e}); the files stand on their own"


# ---- dimensions on the TechDraw page ---------------------------------------------------

def _wait_views(views_, timeout=30.0):
    """TechDraw works out a view's lines on another thread and takes them up
    when Qt's event loop says it is done; with no window nothing runs that
    loop, so it is run here until every view has its lines. Seconds waited,
    or None when they were there at once."""
    import time

    def ready():
        try:
            return all(v.getVisibleEdges() for v in views_)
        except Exception:
            return False

    if ready():
        return None
    try:
        from PySide import QtCore
    except ImportError:
        try:
            from PySide2 import QtCore
        except ImportError:
            return None
    app = QtCore.QCoreApplication.instance() or QtCore.QCoreApplication([])
    t0 = time.time()
    while time.time() - t0 < timeout:
        app.processEvents()
        if ready():
            break
        time.sleep(0.02)
    for v in views_:
        try:
            v.recompute()
        except Exception:
            pass
    return round(time.time() - t0, 2)


def _view_frame(v, box):
    """How a view's own 2D geometry lies against views.project's: (scale,
    sx, sy, tx, ty) with td = (s·sx·x + tx, s·sy·y + ty), fitted on the
    visible extents and the sign of y checked against the vertices. Each
    release centres, scales and flips its geometry its own way."""
    import Part

    edges = list(v.getVisibleEdges() or [])
    if not edges:
        return None
    bb = Part.makeCompound(edges).BoundBox
    w, h = box[2] - box[0], box[3] - box[1]
    if max(w, h) < 1e-9:
        return None
    s = (bb.XLength / w) if w >= h else (bb.YLength / h)
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    best = None
    pts = [(p.x, p.y) for p in _vertices(v)]
    for sx in (1, -1):
        for sy in (1, -1):
            fr = (s, sx, sy, bb.Center.x - s * sx * cx, bb.Center.y - s * sy * cy)
            best = min(best or (math.inf, fr), (_fit_score(fr, pts, box), fr))
    return best[1]


def _fit_score(fr, pts, box):
    # The box's corners mapped should land on the view's vertices for a
    # part with corners; a round one ties, and any frame does.
    s, sx, sy, tx, ty = fr
    if not pts:
        return 0.0
    total = 0.0
    for x, y in ((box[0], box[1]), (box[2], box[3]), (box[0], box[3]), (box[2], box[1])):
        X, Y = s * sx * x + tx, s * sy * y + ty
        total += min(math.hypot(X - px, Y - py) for px, py in pts)
    return total


def _vertices(v):
    out, i = [], 0
    while True:
        try:
            g = v.getVertexByIndex(i)
        except Exception:
            break
        if g is None:
            break
        out.append(g.Point if hasattr(g, "Point") else g)
        i += 1
        if i > 20000:
            break
    return out


def _edges(v):
    out, i = [], 0
    while True:
        try:
            g = v.getEdgeByIndex(i)
        except Exception:
            break
        if g is None:
            break
        out.append(g)
        i += 1
        if i > 20000:
            break
    return out


def _to_view(fr, p2):
    s, sx, sy, tx, ty = fr
    return (s * sx * p2[0] + tx, s * sy * p2[1] + ty)


def _page_dims(doc, page, made, drawn, specs):
    """The dimensions and hole callouts asked for, on the TechDraw page as
    its own dimensions on the views' vertices and circles — measured by
    TechDraw, so they follow the part — each read back against the value the
    files carry. Returns (dims made, what could not be)."""
    import Part

    # By the view's name: a second drawing's labels come out "Front001".
    by_view = dict(made)
    frames, done, missed = {}, [], []
    for sp in specs:
        view = sp["view"]
        v = by_view.get(view)
        if v is None:
            missed.append({**_brief(sp), "why": "the view is not on the page"})
            continue
        if view not in frames:
            frames[view] = _view_frame(v, drawn[view]["box"])
        fr = frames[view]
        if fr is None:
            missed.append({**_brief(sp), "why": "the view has no geometry headless"})
            continue
        tol = max(0.05, 0.002 * fr[0] * max(drawn[view]["box"][2] - drawn[view]["box"][0], drawn[view]["box"][3] - drawn[view]["box"][1]))
        refs = []
        if sp["type"] in ("Diameter", "Radius"):
            c = _to_view(fr, sp["centre"])
            r = sp["radius"] * fr[0]
            for i, e in enumerate(_edges(v)):
                cu = getattr(e, "Curve", None)
                if isinstance(cu, (Part.Circle, Part.ArcOfCircle)) or hasattr(cu, "Radius"):
                    try:
                        if abs(cu.Radius - r) < tol and math.hypot(cu.Center.x - c[0], cu.Center.y - c[1]) < tol:
                            refs = [(v, f"Edge{i}")]
                            break
                    except Exception:
                        continue
        else:
            verts = _vertices(v)
            for p2 in sp["points"]:
                q = _to_view(fr, p2)
                near = min(range(len(verts)), key=lambda k: math.hypot(verts[k].x - q[0], verts[k].y - q[1]), default=None)
                if near is None or math.hypot(verts[near].x - q[0], verts[near].y - q[1]) > tol:
                    refs = []
                    break
                refs.append((v, f"Vertex{near}"))
        if not refs:
            missed.append({**_brief(sp), "why": "no vertex or circle of the view there (a hidden line, or a point TechDraw does not draw)"})
            continue
        try:
            dim = doc.addObject("TechDraw::DrawViewDimension", "Dimension")
            dim.Type = sp["type"]
            dim.MeasureType = "Projected"
            dim.References2D = refs
            page.addView(dim)
            if sp.get("label"):
                dim.Arbitrary = True
                dim.FormatSpec = sp["label"]
            else:
                spec = dim.FormatSpec
                if sp.get("fit"):
                    spec = f"{spec} {sp['fit']}"
                if sp.get("prefix"):
                    spec = sp["prefix"] + spec
                dim.FormatSpec = spec
            if sp.get("over") is not None or sp.get("under") is not None:
                dim.EqualTolerance = False
                dim.OverTolerance = f"{sp.get('over') or 0} mm"
                dim.UnderTolerance = f"{sp.get('under') or 0} mm"
            doc.recompute()
            raw = float(dim.getRawValue())
        except Exception as e:
            missed.append({**_brief(sp), "why": f"TechDraw refused it: {e}"})
            continue
        ok = abs(raw - sp["value"]) <= max(0.01, 0.002 * sp["value"])
        done.append({**_brief(sp), "dimension": dim.Name, "measured": round(raw, 4), **({} if ok else {"warning": f"TechDraw measures {raw:.4g}, not {sp['value']:.4g}: check its references in the GUI"})})
    return done, missed


def _brief(sp):
    return {"view": sp["view"], "type": sp["type"], "value": round(sp["value"], 4)}


def _tolerance(nominal, d):
    """(over, under) mm from a fit or plus/minus; (None, None) for neither."""
    if d.get("fit"):
        return iso286.limits(nominal, d["fit"])
    if d.get("plus") is not None or d.get("minus") is not None:
        p = float(d.get("plus") or 0)
        m = d.get("minus")
        m = -abs(float(m)) if m is not None else -p
        return p, m
    return None, None


# ---- feature dimensions, hole callouts, the title block --------------------------------

def _to_paper(p2, box, centre, scale):
    bx, by = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    return (centre[0] + (p2[0] - bx) * scale, centre[1] - (p2[1] - by) * scale)


def _ref_point(session, objs, r, what):
    """A 3D place a dimension measures from: a point [x, y, z], or a face or
    edge query on a body ({ body, kind, type, radius, near, normal … }) —
    a circle's centre, an edge's middle, a face's centre. Returns (point,
    the circle edge when it is one)."""
    import Part
    from . import measure as M
    from . import picks

    if isinstance(r, list):
        return picks.vec3(r, what), None
    if not isinstance(r, dict):
        raise ToolError("invalid_input", f"{what}: a point [x, y, z] or a query {{ body, kind: edge|face, type, radius, near }}.")
    q = dict(r)
    q.setdefault("body", objs[0].Label)
    q.setdefault("kind", "edge")
    _, _, hits = M.query(session, q)
    if not hits:
        raise ToolError("not_found", f"{what}: nothing on {q['body']} matches {r} — shape_query lists them.")
    if len(hits) > 1:
        if q.get("near") is None:
            raise ToolError("invalid_input", f"{what}: {len(hits)} match — add near: [x, y, z] to take the closest.")
        pt = picks.vec3(q["near"] if isinstance(q["near"], list) else q["near"]["point"], "near")
        hits = sorted(hits, key=lambda h: h[1].distToShape(Part.Vertex(pt))[0])
    el = hits[0][1]
    if hasattr(el, "Curve") and isinstance(el.Curve, (Part.Circle,)):
        return FreeCAD.Vector(el.Curve.Center), el
    if hasattr(el, "Curve"):
        return el.valueAt((el.FirstParameter + el.LastParameter) / 2), el
    return el.CenterOfMass, el


def _callout(nominal, d, diameter=False):
    try:
        return d.get("label") or iso286.text(nominal, d.get("fit"), d.get("plus"), d.get("minus"), diameter)
    except ValueError as e:
        raise ToolError("invalid_input", f"fit: {e}")


def _holes(objs, view):
    """The round holes seen end on in a view, grouped by diameter: [(d,
    [centres])]. A hole is a circle edge on a cylindrical face that goes
    into the part, its axis along the view."""
    import Part

    Z, _, _ = views.frame(view)
    seen = {}
    for o in objs:
        sh = o.Shape
        for f in sh.Faces:
            if not isinstance(f.Surface, Part.Cylinder):
                continue
            ax = f.Surface.Axis
            if abs(abs(ax.dot(Z)) - 1) > 1e-6:
                continue
            # A hole's material is outside its cylinder: the face's normal points to the axis.
            u0, u1, v0, v1 = f.ParameterRange
            p = f.valueAt((u0 + u1) / 2, (v0 + v1) / 2)
            n = f.normalAt((u0 + u1) / 2, (v0 + v1) / 2)
            c = f.Surface.Center
            radial = p - c - ax * (p - c).dot(ax)
            if radial.Length < 1e-9 or n.dot(radial) > 0:
                continue
            dia = round(2 * f.Surface.Radius, 3)
            key = (round(c.dot(views.frame(view)[1]), 2), round(c.dot(views.frame(view)[2]), 2))
            got = seen.setdefault(dia, {}).setdefault(key, [c, 0.0])
            got[1] += abs(u1 - u0)
    # A hole goes all the way round (in one face or two); a cavity's rounded
    # inside corner is a quarter turn and is no hole.
    out = []
    for d, v in seen.items():
        centres = [c for c, span in v.values() if span >= 0.95 * 2 * math.pi]
        if centres:
            out.append((d, centres))
    return sorted(out, key=lambda x: -x[0])


def title_block_grid(sheet, fields, margin=10.0, width=130.0):
    """A title block of labelled cells in the bottom-right corner: the
    title across the top, then pairs of fields."""
    rows = [("TITLE", fields.get("title", ""))]
    pairs = [k for k in ("partNumber", "revision", "material", "finish", "drawnBy", "date", "checkedBy", "company", "scale", "projection", "sheet", "generalTolerance") if fields.get(k)]
    names = {"partNumber": "PART No.", "revision": "REV", "material": "MATERIAL", "finish": "FINISH", "drawnBy": "DRAWN", "date": "DATE", "checkedBy": "CHECKED",
             "company": "COMPANY", "scale": "SCALE", "projection": "PROJECTION", "sheet": "SHEET", "generalTolerance": "GENERAL TOL."}
    cell_h = 7.0
    n_rows = 1 + (len(pairs) + 1) // 2
    x1, y1 = sheet.w - margin, sheet.h - margin
    x0, y0 = x1 - width, y1 - cell_h * n_rows
    sheet.line([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], "visible", closed=True)
    half = width / 2
    for r in range(1, n_rows):
        y = y0 + r * cell_h
        sheet.line([(x0, y), (x1, y)], "thin")
    if n_rows > 1:
        sheet.line([(x0 + half, y0 + cell_h), (x0 + half, y1)], "thin")
    sheet.text(x0 + 1.5, y0 + 2.4, 1.6, "TITLE")
    sheet.text(x0 + 1.5, y0 + cell_h - 1.4, 3.2, str(rows[0][1])[:44])
    for i, k in enumerate(pairs):
        r, c = 1 + i // 2, i % 2
        cx, cy = x0 + c * half, y0 + r * cell_h
        sheet.text(cx + 1.5, cy + 2.4, 1.6, names[k])
        sheet.text(cx + 1.5, cy + cell_h - 1.4, 2.6, str(fields[k])[:30])
    return (x0, y0, x1, y1)


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
        sec_view = a.get("section")
        if sec_view is not None and sec_view not in want:
            raise ToolError("invalid_input", f"section: one of the views drawn ({', '.join(want)}) to show cut through.")
        if sec_view == "iso":
            raise ToolError("invalid_input", "section: a straight view (front, top, right …), not iso.")
        sec_label = str(a.get("sectionLabel") or "A")[:2].upper()
        drawn = {}
        for v in want:
            if v == sec_view:
                try:
                    drawn[v] = views.section(shapes, v, float(a.get("sectionAt", 0)), hidden=bool(a.get("hidden")))
                except ValueError as e:
                    raise ToolError("invalid_input", f"sectionAt: {e} — a smaller offset from the middle.")
            else:
                drawn[v] = views.project(shapes, v, hidden=bool(a.get("hidden")))
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
            lines = d["lines"]
            if v == sec_view:
                # The cut faces hatched 2.5 mm apart on the paper.
                lines = lines + [("thin", [p, q]) for p, q in views.hatch(d["cut"], 2.5 / scale)]
            sheet.place(lines, d["box"], (cx, cy), scale)
            w, h = sizes[v][0] * scale, sizes[v][1] * scale
            sheet.text(cx, cy - h / 2 - 4, 3.0, f"SECTION {sec_label}-{sec_label}" if v == sec_view else v.upper(), "middle")
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
        section_out = None
        if sec_view:
            # The cutting plane seen edge on in another view: a chain line
            # across it, past its ends, lettered at both.
            Zs, _, _ = views.frame(sec_view)
            d_plane = drawn[sec_view]["plane"]
            shown = None
            for v in want:
                if v in (sec_view, "iso") or v not in views.VIEWS:
                    continue
                Zv, Xv, Yv = views.frame(v)
                if abs(Zv.dot(Zs)) < 1e-6:
                    shown = v
                    break
            section_out = {"view": sec_view, "label": f"{sec_label}-{sec_label}", "plane": round(d_plane, 3), "cutFaces": len(drawn[sec_view]["cut"])}
            bbs = shapes[0].BoundBox
            for sh in shapes[1:]:
                bbs.add(sh.BoundBox)
            sec_origin = bbs.Center + Zs * (d_plane - bbs.Center.dot(Zs))
            if shown:
                Zv, Xv, Yv = views.frame(shown)
                box, centre = drawn[shown]["box"], L["centres"][shown]
                along_x = abs(Zs.dot(Xv)) > 0.5
                c = Zs * d_plane
                q = views.point(c, shown)
                if along_x:
                    pa, pb = (q[0], box[1] - 4 / scale), (q[0], box[3] + 4 / scale)
                else:
                    pa, pb = (box[0] - 4 / scale, q[1]), (box[2] + 4 / scale, q[1])
                ppa, ppb = _to_paper(pa, box, centre, scale), _to_paper(pb, box, centre, scale)
                sheet.line([ppa, ppb], "trail")
                for pp in (ppa, ppb):
                    sheet.text(pp[0] + 1.5, pp[1] - 1.5, 3.5, sec_label)
                section_out["cutLineIn"] = shown
        # Feature dimensions asked for, each in its view: lengths between two
        # places, diameters and radii of round edges, with a tolerance or fit.
        placed, specs = [], []
        for i, d in enumerate(a.get("dims") or []):
            if not isinstance(d, dict) or d.get("view") not in drawn:
                raise ToolError("invalid_input", f"dims.{i}: {{ view (one drawn: {', '.join(want)}), kind: length|diameter|radius, a, b, offset, fit | plus/minus }}.")
            v = d["view"]
            kind = d.get("kind", "length")
            box, centre = drawn[v]["box"], L["centres"][v]
            pa3, ea = _ref_point(s, objs, d.get("a"), f"dims.{i}.a")
            pa = _to_paper(views.point(pa3, v), box, centre, scale)
            if kind == "length":
                pb3, _ = _ref_point(s, objs, d.get("b"), f"dims.{i}.b")
                pb = _to_paper(views.point(pb3, v), box, centre, scale)
                axis = d.get("along")
                a2, b2 = views.point(pa3, v), views.point(pb3, v)
                if axis == "h":
                    value, pb = abs(b2[0] - a2[0]), (pb[0], pa[1])
                elif axis == "v":
                    value, pb = abs(b2[1] - a2[1]), (pa[0], pb[1])
                else:
                    value = math.hypot(b2[0] - a2[0], b2[1] - a2[1])
                label = _callout(value, d)
                sheet.dimension(pa, pb, float(d.get("offset", 8)), label)
                over, under = _tolerance(value, d)
                specs.append({"view": v, "type": {"h": "DistanceX", "v": "DistanceY"}.get(axis, "Distance"), "points": [a2, b2], "value": value,
                              "fit": d.get("fit"), "over": over, "under": under, "label": d.get("label")})
            elif kind in ("diameter", "radius"):
                import Part

                if ea is None or not isinstance(getattr(ea, "Curve", None), Part.Circle):
                    raise ToolError("invalid_input", f"dims.{i}.a: a round edge ({{ kind: \"edge\", type: \"circle\", radius, near }}) for a {kind}.")
                rr = ea.Curve.Radius
                value = 2 * rr if kind == "diameter" else rr
                label = _callout(value, d, diameter=kind == "diameter")
                if kind == "radius":
                    label = "R" + label
                over, under = _tolerance(value, d)
                specs.append({"view": v, "type": kind.capitalize(), "centre": views.point(pa3, v), "radius": rr, "value": value,
                              "fit": d.get("fit"), "over": over, "under": under, "label": d.get("label")})
                ang = math.radians(float(d.get("angle", 45)))
                rp = rr * scale
                tip = (pa[0] + rp * math.cos(ang), pa[1] - rp * math.sin(ang))
                elbow = (tip[0] + 8 * math.cos(ang), tip[1] - 8 * math.sin(ang))
                tail = (elbow[0] + (10 if math.cos(ang) >= 0 else -10), elbow[1])
                sheet.line([tip, elbow, tail], "dim")
                sheet.arrow(tip, elbow)
                sheet.text(tail[0] + (1 if math.cos(ang) >= 0 else -1), tail[1] - 1, 3.0, label, "start" if math.cos(ang) >= 0 else "end")
            else:
                raise ToolError("invalid_input", f"dims.{i}.kind: length, diameter or radius.")
            placed.append({"view": v, "kind": kind, "value": round(value, 4), "label": label})
        # Every round hole seen end on, called out once per size: "4× ⌀3.4".
        holes_out = []
        if a.get("holes"):
            hv = a["holes"] if isinstance(a["holes"], str) else next((v for v in ("top", "front", "right") if v in drawn), want[0])
            if hv not in drawn:
                raise ToolError("invalid_input", f"holes: a view drawn ({', '.join(want)}), or true for the top.")
            box, centre = drawn[hv]["box"], L["centres"][hv]
            for k, (dia, centres) in enumerate(_holes(objs, hv)):
                c0 = min(centres, key=lambda c: (views.point(c, hv)[0], -views.point(c, hv)[1]))
                pa = _to_paper(views.point(c0, hv), box, centre, scale)
                fit = (a.get("holeFits") or {}).get(str(dia)) or (a.get("holeFits") or {}).get(f"{dia:g}")
                label = (f"{len(centres)}× " if len(centres) > 1 else "") + _callout(dia, {"fit": fit}, diameter=True)
                rp = dia / 2 * scale
                ang = math.radians(135 + 20 * k)
                tip = (pa[0] + rp * math.cos(ang), pa[1] - rp * math.sin(ang))
                elbow = (tip[0] + 9 * math.cos(ang), tip[1] - 9 * math.sin(ang))
                tail = (elbow[0] - 12, elbow[1])
                sheet.line([tip, elbow, tail], "dim")
                sheet.arrow(tip, elbow)
                sheet.text(tail[0] - 1, tail[1] - 1, 3.0, label, "end")
                holes_out.append({"view": hv, "diameter": dia, "count": len(centres), "label": label})
                over, under = _tolerance(dia, {"fit": fit}) if fit else (None, None)
                specs.append({"view": hv, "type": "Diameter", "centre": views.point(c0, hv), "radius": dia / 2, "value": dia,
                              "fit": fit, "over": over, "under": under, "prefix": f"{len(centres)}× " if len(centres) > 1 else ""})
        tb = dict(a.get("titleBlock") or {})
        tb.setdefault("title", name)
        tb.setdefault("scale", sheets.scale_text(scale))
        tb.setdefault("projection", f"{projection} angle")
        tb.setdefault("generalTolerance", "ISO 2768-m")
        if not tb.get("material"):
            mat = store.materials(s.doc).get(objs[0].Label) if len(objs) == 1 else None
            if mat:
                tb["material"] = str(mat).upper()
        tb.setdefault("sheet", f"{paper}  mm")
        title_block_grid(sheet, tb)
        paths = _write(s, _safe(a.get("file") or name), sheet, ("svg", "pdf", "dxf"))
        page, made = (None, "not asked for")
        if a.get("page", True):
            page, made = _techdraw_page(s.doc, objs, [(v, L["centres"][v]) for v in want], scale, paper, f"{name} drawing", a.get("dimensions", True), specs, drawn,
                                         {**section_out, "origin": sec_origin} if section_out else None)
        s.snapshot("drawing_make")
        out = {
            "files": paths,
            "paper": paper,
            "scale": sheets.scale_text(scale),
            "projection": projection,
            "views": [{"view": v, "at": [round(c, 1) for c in L["centres"][v]], "size": [round(sizes[v][0] * scale, 1), round(sizes[v][1] * scale, 1)]} for v in want],
            "dimensions": dims + placed,
            **({"section": section_out} if section_out else {}),
            **({"holes": holes_out} if a.get("holes") else {}),
            "titleBlock": tb,
            "techdrawPage": page,
            "step": len(s.steps),
        }
        out["page" if page else "pageNote"] = made
        if a.get("dimensions", True):
            out["note"] = "Overall sizes once each, and the dims and hole callouts asked for. The PDF and DXF are written from the same lines as the SVG, with or without the GUI; the TechDraw page carries them too, as TechDraw dimensions measured on its views (page.dimensions; any it could not place under page.notOnPage)."
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
        # Each part is lifted over where it is in the document: one lying
        # apart from everything before it (printed beside the box, say) is
        # lifted over the wrong place, and the arrow points at nothing.
        apart = []
        for o in order:
            prior = [p for p in order if seen[p.Label] < seen[o.Label]]
            if not prior:
                continue
            b = o.Shape.BoundBox
            if not any(b.XMin < p.Shape.BoundBox.XMax and p.Shape.BoundBox.XMin < b.XMax and b.YMin < p.Shape.BoundBox.YMax and p.Shape.BoundBox.YMin < b.YMax for p in prior):
                apart.append(o.Label)
        if apart:
            out["warnings"] = [f"{', '.join(apart)} lie apart from the parts before them, not where they go: each part is lifted over its place in the document, so put them there first (object_set Placement — a lid printed top down turns over about X), then again. view_render shows where they are."]
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
        md.append("Reference numerals: " + "; ".join(f"{numerals[o.Label]} {name(o)}" for o in objs) + ".")
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
            "with the same views to the document (page: false leaves it out). dims adds feature dimensions with tolerances or ISO 286 fits (limits written in), "
            "holes calls out every round hole by size, titleBlock fills a title block of labelled cells. section: a straight view drawn as a full section "
            "(cut square to it through the middle, sectionAt mm towards the viewer; the cut faces hatched, the cutting line lettered sectionLabel, A, in a view that sees it edge on) — for bores, pockets and walls inside.",
            _obj({
                "bodies": bodies,
                "views": {"type": "array", "items": {"type": "string", "enum": list(views.VIEWS)}},
                "section": {"type": "string", "enum": list(views.VIEWS)},
                "sectionAt": {"type": "number"},
                "sectionLabel": {"type": "string"},
                "projection": {"type": "string", "enum": ["first", "third"]},
                "paper": {"type": "string", "enum": ["A4", "A3", "auto"]},
                "scale": {"type": "number", "description": "0.5 for 1:2, 2 for 2:1; left out, the largest standard one that fits."},
                "dimensions": {"type": "boolean"},
                "hidden": {"type": "boolean"},
                "page": {"type": "boolean"},
                "name": {"type": "string", "description": "The title on the sheet."},
                "file": {"type": "string", "description": "The files' name, without extension."},
                "dims": {"type": "array", "items": {"type": "object"}, "description": "Feature dimensions: [{ view, kind: length (a, b, along h|v, offset mm) | diameter | radius (a round edge), fit \"H7\" or plus/minus mm, label }]; a and b a point [x,y,z] or a query { body, kind, type, radius, near } (a circle's centre, an edge's middle)."},
                "holes": {"description": "true: every round hole seen end on in the top view called out by size (\"4× ⌀3.4\"); or the view's name."},
                "holeFits": {"type": "object", "description": "A fit per hole diameter for the callouts: { \"6\": \"H7\" }."},
                "titleBlock": {"type": "object", "description": "{ title, partNumber, revision, material, finish, drawnBy, date, checkedBy, company, generalTolerance (ISO 2768-m to start) } — ask the person for theirs."},
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
