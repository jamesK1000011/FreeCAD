# SPDX-License-Identifier: LGPL-2.1-or-later
# TechDraw, piece by piece: a page on a standard template and its title
# block, views and projection groups, section and detail views, the
# geometry of a view read back so a dimension can be put on a real edge or
# vertex, dimensions with tolerances and ISO 286 fits, geometric tolerance
# frames and datum symbols, notes, balloons and a spreadsheet as a table —
# all TechDraw's own objects, so the person carries on in the GUI — and the
# page written out (DXF by TechDraw; SVG and PDF drawn here from the same
# objects, as a page's own export needs the window).
#
# Page coordinates are TechDraw's: millimetres from the bottom-left corner,
# y up. A view's geometry is read in its own millimetres on the paper.

import glob
import json
import math
import os

import FreeCAD
import TechDraw  # noqa: F401 — the family stands on TechDraw; left out without it

from .. import gdt, iso286, sheets
from ..protocol import Tool, ToolError

GROUP = "techdraw"
INFO = "TechDraw pages piece by piece: template and title block, views, projection groups, sections, details, dimensions on real edges with tolerances and fits, GD&T frames, datums, notes, balloons, tables, export to SVG, PDF and DXF."

V = FreeCAD.Vector
DIRS = {
    "front": ((0, -1, 0), (1, 0, 0)), "rear": ((0, 1, 0), (-1, 0, 0)), "top": ((0, 0, 1), (1, 0, 0)), "bottom": ((0, 0, -1), (1, 0, 0)),
    "right": ((1, 0, 0), (0, 1, 0)), "left": ((-1, 0, 0), (0, -1, 0)), "iso": ((1, -1, 1), (1, 1, 0)),
}
PROJ_ITEMS = {"front": "Front", "top": "Top", "bottom": "Bottom", "right": "Right", "left": "Left", "rear": "Rear",
              "iso": "FrontTopRight", "isoLeft": "FrontTopLeft", "isoBottomRight": "FrontBottomRight", "isoBottomLeft": "FrontBottomLeft"}
DIM_TYPES = ("Distance", "DistanceX", "DistanceY", "Radius", "Diameter", "Angle")
PAPERS = {  # name: (width, height) landscape, mm
    "A0": (1189, 841), "A1": (841, 594), "A2": (594, 420), "A3": (420, 297), "A4": (297, 210),
    "ANSIA": (279.4, 215.9), "Letter": (279.4, 215.9), "ANSIB": (431.8, 279.4), "Tabloid": (431.8, 279.4),
    "ANSIC": (558.8, 431.8), "ANSID": (863.6, 558.8), "ANSIE": (1117.6, 863.6),
}
GDT_PROP = "AgentLinkGdt"


def obj(props, required=()):
    return {"type": "object", "properties": props, "required": list(required), "additionalProperties": False}


def bad(msg):
    return ToolError("invalid_input", msg)


def num(v, nd=4):
    return round(float(v), nd) + 0.0


def vec(v, what):
    if isinstance(v, (list, tuple)) and len(v) == 3 and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in v):
        return V(*[float(x) for x in v])
    raise bad(f"{what}: [x, y, z] in mm.")


def pt2(v, what):
    if isinstance(v, (list, tuple)) and len(v) == 2 and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in v):
        return float(v[0]), float(v[1])
    raise bad(f"{what}: [x, y] in mm.")


# ---- templates ----


def template_root():
    return os.path.join(FreeCAD.getResourceDir(), "Mod", "TechDraw", "Templates")


def templates():
    return sorted(glob.glob(os.path.join(template_root(), "**", "*.svg"), recursive=True))


def find_template(paper, portrait=False, blank=False, standard=None):
    """The build's template for a paper: one with a title block unless
    blank. Template names differ from release to release, so they are
    matched by paper and orientation and ranked."""
    paper_key = {"Letter": "USLetter", "Tabloid": "ANSIB"}.get(paper, paper)
    orient = "Portrait" if portrait else "Landscape"
    cands = [p for p in templates() if os.path.basename(p).lower().startswith(paper_key.lower()) and (orient.lower() in os.path.basename(p).lower() or paper_key in ("ANSIB",) and not portrait)]
    if paper_key == "USLetter":
        cands += [p for p in templates() if os.path.basename(p).startswith("ANSIA_" + orient)]
    if standard:
        pref = [p for p in cands if f"{os.sep}{standard.upper()}{os.sep}" in p]
        cands = pref or cands

    def rank(p):
        b = os.path.basename(p).lower()
        if blank:
            return (0 if "blank" in b else 1 if "notitleblock" in b else 2, b)
        return (3 if "blank" in b or "notitleblock" in b else 0 if ("minimal" in b or "_td" in b or "7200" in b) else 1, b)

    cands.sort(key=rank)
    if not cands and paper == "A4" and not portrait and not blank:
        d = os.path.join(template_root(), "Default_Template_A4_Landscape.svg")
        cands = [d] if os.path.isfile(d) else []
    return cands[0] if cands else None


def presets_path():
    d = os.path.join(FreeCAD.getUserAppDataDir(), "AgentLink")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "titleblocks.json")


def load_presets(path=None):
    p = path or presets_path()
    if not os.path.isfile(p):
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_presets(data, path=None):
    with open(path or presets_path(), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ---- finding things ----


def set_enum(o, prop, want):
    """Set an enumeration by its item, matched without case or spaces:
    releases spell them differently ("First angle", "First Angle")."""
    key = want.lower().replace(" ", "").replace("_", "")
    for item in o.getEnumerationsOfProperty(prop) or []:
        if item.lower().replace(" ", "").replace("_", "") == key:
            setattr(o, prop, item)
            return item
    raise bad(f"{prop} {want!r}: this build offers {', '.join(o.getEnumerationsOfProperty(prop) or [])}.")


def ref_names(d):
    """A dimension's references as names: releases give (view, "Edge4") or
    (view, ("Edge4",))."""
    out = []
    for _, sub in d.References2D:
        out += list(sub) if isinstance(sub, (list, tuple)) else [sub]
    return out


def template_defaults(path):
    """The example values a template's editable fields carry in the file."""
    import re

    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return {}
    out = {}
    for m in re.finditer(r'freecad:editable="([^"]+)"[^>]*>\s*(?:<tspan[^>]*>)?([^<]*)', text):
        out[m.group(1)] = m.group(2).strip()
    return out


def pages(doc):
    return [o for o in doc.Objects if o.TypeId == "TechDraw::DrawPage"]


def page_of(doc, ref):
    all_ = pages(doc)
    if ref is None:
        if len(all_) == 1:
            return all_[0]
        raise (ToolError("not_found", "No TechDraw page: td_page { action: \"new\" } makes one.") if not all_ else bad(f"page: say which — {', '.join(p.Label for p in all_)}."))
    for p in all_:
        if ref in (p.Label, p.Name):
            return p
    raise ToolError("not_found", f"page {ref!r}: not here — the pages: {', '.join(p.Label for p in all_) or 'none'}.")


def obj_of(doc, ref, types=None, what="object"):
    for o in doc.Objects:
        if ref in (o.Label, o.Name) and (types is None or any(o.isDerivedFrom(t) for t in types)):
            return o
    raise ToolError("not_found", f"{what} {ref!r}: not here — td_list names what each page holds.")


def view_of(doc, ref):
    return obj_of(doc, ref, ["TechDraw::DrawViewPart"], "view")


def page_holding(o):
    for p in o.InList:
        if p.TypeId == "TechDraw::DrawPage":
            return p
        if p.isDerivedFrom("TechDraw::DrawProjGroup"):
            return page_holding(p)
    try:
        return o.findParentPage()
    except Exception:
        return None


def paper_size(page):
    t = page.Template
    try:
        return float(t.Width), float(t.Height)
    except Exception:
        return 297.0, 210.0


def wait(views_):
    from .drawing import _wait_views

    return _wait_views([v for v in views_ if v.isDerivedFrom("TechDraw::DrawViewPart")])


def edges_of(v):
    out, i = [], 0
    while i < 20000:
        try:
            e = v.getEdgeByIndex(i)
        except Exception:
            break
        if e is None:
            break
        out.append(e)
        i += 1
    return out


def vertices_of(v):
    out, i = [], 0
    while i < 20000:
        try:
            g = v.getVertexByIndex(i)
        except Exception:
            break
        if g is None:
            break
        out.append(g.Point if hasattr(g, "Point") else g)
        i += 1
    return out


def view_scale(v):
    try:
        return float(v.getScale()) if hasattr(v, "getScale") else float(v.Scale)
    except Exception:
        return float(v.Scale)


def source_shapes(v):
    import Part

    shapes = []
    for o in list(getattr(v, "Source", []) or []) + list(getattr(v, "XSource", []) or []):
        sh = getattr(o, "Shape", None)
        if sh is not None and not sh.isNull():
            shapes.append(sh)
    return Part.makeCompound(shapes) if shapes else None


def frame_of(v, cache):
    """How a 3D point lands in the view's geometry: (scale, sy, tx, ty)
    with view = (s·px + tx, s·sy·py + ty) for px, py = projectPoint(p).
    Releases centre, scale and flip a view's geometry their own ways, so the
    frame is fitted on the source's vertices against the view's."""
    if v.Name in cache:
        return cache[v.Name]
    sh = source_shapes(v)
    verts = vertices_of(v)
    if sh is None or not verts:
        cache[v.Name] = None
        return None
    pts3 = [x.Point for x in sh.Vertexes][:400] or [sh.BoundBox.Center]
    proj = [v.projectPoint(p) for p in pts3]
    try:
        c = v.projectPoint(v.getGeometricCenter() if hasattr(v, "getGeometricCenter") else sh.BoundBox.Center)
    except Exception:
        c = v.projectPoint(sh.BoundBox.Center)
    sc = view_scale(v)
    best = None
    # TechDraw keeps a view's geometry centred on the source, scaled and
    # with y inverted (as Qt draws): that first, and another reading only
    # if it fits clearly better — a symmetric part fits a flipped frame as
    # well, and would put a datum on the opposite edge.
    for s in (sc, 1.0):
        for sy in (-1, 1):
            for centred in (True, False):
                tx = -s * c.x if centred else 0.0
                ty = -s * sy * c.y if centred else 0.0
                err = 0.0
                for p in proj:
                    X, Y = s * p.x + tx, s * sy * p.y + ty
                    err += min(math.hypot(X - q.x, Y - q.y) for q in verts)
                cand = (err / len(proj), (s, sy, tx, ty))
                if best is None or cand[0] < best[0] - 1e-3 * max(1.0, sc):
                    best = cand
    cache[v.Name] = best[1]
    return best[1]


def gscale(v, cache):
    """Geometry units per mm on the part, as the view's indexed geometry
    holds them: some releases keep it scaled to the paper, some not (the
    visible edges are always on the paper)."""
    fr = frame_of(v, cache)
    return fr[0] if fr else view_scale(v)


def to_paper(v, cache, xy):
    """A point of the view's indexed geometry in paper mm from its centre."""
    k = view_scale(v) / gscale(v, cache)
    return xy[0] * k, xy[1] * k


def to_view(fr, v, p3):
    p = v.projectPoint(p3)
    s, sy, tx, ty = fr
    return s * p.x + tx, s * sy * p.y + ty


def edge_info(e, sc):
    cu = getattr(e, "Curve", None)
    name = type(cu).__name__ if cu is not None else "Edge"
    out = {"kind": {"Line": "line", "LineSegment": "line", "Circle": "circle", "ArcOfCircle": "arc", "Ellipse": "ellipse", "BSplineCurve": "spline"}.get(name, name.lower())}
    try:
        a, b = e.Vertexes[0].Point, e.Vertexes[-1].Point
        if out["kind"] == "line":
            out["from"], out["to"] = [num(a.x / sc, 3), num(a.y / sc, 3)], [num(b.x / sc, 3), num(b.y / sc, 3)]
            out["length"] = num(e.Length / sc, 3)
    except Exception:
        pass
    if hasattr(cu, "Radius") and hasattr(cu, "Center"):
        out["center"] = [num(cu.Center.x / sc, 3), num(cu.Center.y / sc, 3)]
        out["diameter"] = num(2 * cu.Radius / sc, 3)
        if abs(e.Length - 2 * math.pi * cu.Radius) < 1e-6 * max(1.0, cu.Radius):
            out["kind"] = "circle"
        elif out["kind"] == "circle":
            out["kind"] = "arc"
    return out


def resolve_ref(v, r, cache, want="any"):
    """A view geometry reference: "Edge3" | "Vertex5", or { point: [x,y,z] }
    (the nearest vertex), { circle: { diameter, near } }, { edge: { through:
    [p, q] } } (the edge nearest both points' middle) — returns "EdgeN" /
    "VertexN"."""
    if isinstance(r, str):
        if r.startswith(("Edge", "Vertex")) and r[4 if r.startswith("Edge") else 6:].isdigit():
            n = int(r[4:] if r.startswith("Edge") else r[6:])
            lst = edges_of(v) if r.startswith("Edge") else vertices_of(v)
            if n >= len(lst):
                raise ToolError("not_found", f"{r}: {v.Label} has {len(lst)} {'edges' if r.startswith('Edge') else 'vertices'} (td_geometry lists them).")
            return r
        raise bad(f"reference {r!r}: \"Edge3\", \"Vertex5\", or {{ point }}, {{ circle }}, {{ edge }}.")
    if not isinstance(r, dict):
        raise bad("reference: \"Edge3\" | \"Vertex5\" | { point: [x,y,z] } | { circle: { diameter, near } } | { edge: { through: [p, q] } }.")
    fr = frame_of(v, cache)
    if fr is None:
        raise ToolError("unavailable", f"{v.Label} has no geometry yet (recompute, or a hidden view).")
    sc = fr[0]
    if "point" in r:
        q = to_view(fr, v, vec(r["point"], "point"))
        verts = vertices_of(v)
        k = min(range(len(verts)), key=lambda i: math.hypot(verts[i].x - q[0], verts[i].y - q[1]))
        d = math.hypot(verts[k].x - q[0], verts[k].y - q[1]) / sc
        if d > max(0.05, 0.002 * (source_shapes(v).BoundBox.DiagonalLength)):
            raise ToolError("not_found", f"No vertex of {v.Label} at {r['point']} (nearest {d:.3g} mm off): a hidden point, or one this view does not draw.")
        return f"Vertex{k}"
    if "circle" in r:
        c = r["circle"] or {}
        dia = c.get("diameter")
        near = to_view(fr, v, vec(c["near"], "near")) if c.get("near") is not None else None
        hits = []
        for i, e in enumerate(edges_of(v)):
            cu = getattr(e, "Curve", None)
            if not (hasattr(cu, "Radius") and hasattr(cu, "Center")):
                continue
            if dia is not None and abs(2 * cu.Radius / sc - float(dia)) > max(0.01, 0.002 * float(dia)):
                continue
            hits.append((math.hypot(cu.Center.x - near[0], cu.Center.y - near[1]) if near else 0.0, i))
        if not hits:
            raise ToolError("not_found", f"No circle{f' of ⌀{dia}' if dia else ''} in {v.Label} — td_geometry {{ kind: \"circles\" }} lists them.")
        hits.sort()
        if len(hits) > 1 and near is None and len({h[0] for h in hits}) == 1:
            raise bad(f"{len(hits)} circles match: add near: [x, y, z].")
        return f"Edge{hits[0][1]}"
    if "edge" in r:
        th = (r["edge"] or {}).get("through")
        if not (isinstance(th, list) and len(th) == 2):
            raise bad("edge: { through: [[x,y,z], [x,y,z]] }, two points on it.")
        a, b = to_view(fr, v, vec(th[0], "through")), to_view(fr, v, vec(th[1], "through"))
        best = None
        import Part

        for i, e in enumerate(edges_of(v)):
            d = sum(e.distToShape(Part.Vertex(V(x, y, 0)))[0] for x, y in (a, b))
            if best is None or d < best[0]:
                best = (d, i)
        if best is None or best[0] / sc > 0.1:
            raise ToolError("not_found", f"No edge of {v.Label} through those points.")
        return f"Edge{best[1]}"
    raise bad("reference: one of point, circle, edge.")


# ---- the page as drawn here (export) ----


def page_sheet(doc, page):
    """The page as lines and text for sheets.Sheet: views (visible and
    hidden edges), dimensions, symbols we made, leaders, notes, balloons,
    tables and a frame with the title block's fields."""
    W, H = paper_size(page)
    sh = sheets.Sheet(W, H, page.Label)
    sh.frame(10.0)
    fields = {}
    try:
        fields = dict(page.Template.EditableTexts)
    except Exception:
        pass
    vals = [(k, v) for k, v in fields.items() if str(v).strip()][:10]
    if vals:
        x1, y1 = W - 10, H - 10
        w, ch = 150.0, 7.0
        rows = (len(vals) + 1) // 2
        x0, y0 = x1 - w, y1 - ch * rows
        sh.line([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], "visible", closed=True)
        for r in range(1, rows):
            sh.line([(x0, y0 + r * ch), (x1, y0 + r * ch)], "thin")
        sh.line([(x0 + w / 2, y0), (x0 + w / 2, y1)], "thin")
        for i, (k, v) in enumerate(vals):
            cx, cy = x0 + (i % 2) * w / 2, y0 + (i // 2) * ch
            sh.text(cx + 1.2, cy + 2.2, 1.5, k.upper()[:28])
            sh.text(cx + 1.2, cy + ch - 1.3, 2.4, str(v)[:34])
    skipped = []
    for o in page.Views:
        items = [o] + (list(o.Views) if o.isDerivedFrom("TechDraw::DrawProjGroup") else [])
        for v in items:
            try:
                draw_item(doc, sh, v, H)
            except Exception as e:
                skipped.append(f"{v.Label}: {e}")
    return sh, skipped


def abs_xy(v):
    """A view's centre on the page (mm, y up), through a group it sits in."""
    x, y = float(v.X), float(v.Y)
    for p in v.InList:
        if p.isDerivedFrom("TechDraw::DrawProjGroup"):
            return x + float(p.X), y + float(p.Y)
    return x, y


def draw_item(doc, sh, v, H):
    t = v.TypeId
    if v.isDerivedFrom("TechDraw::DrawProjGroup"):
        return
    if v.isDerivedFrom("TechDraw::DrawViewPart"):
        X, Y = abs_xy(v)
        # geometry is Qt-inverted (y down), the page's svg too: y = H - Y + gy
        for kind, getter in (("hidden", "getHiddenEdges"), ("visible", "getVisibleEdges")):
            if kind == "hidden" and not getattr(v, "HardHidden", False) and not getattr(v, "SmoothHidden", False):
                continue
            for e in getattr(v, getter)() or []:
                pts = [(X + p.x, H - Y + p.y) for p in e.discretize(Deflection=0.05)] if e.Length > 1e-6 else []
                if len(pts) >= 2:
                    sh.line(pts, kind)
        for o in v.InList:
            try:
                if o.isDerivedFrom("TechDraw::DrawViewSection") and o.BaseView == v:
                    cut_line(sh, v, o, X, Y, H)
                elif o.isDerivedFrom("TechDraw::DrawViewDetail") and o.BaseView == v:
                    sc_ = view_scale(v)
                    c = (X + float(o.AnchorPoint.x) * sc_, H - (Y + float(o.AnchorPoint.y) * sc_))
                    r = float(o.Radius) * sc_
                    sh.circle(c[0], c[1], r, "thin", n=48)
                    sh.text(c[0] + r * 0.75 + 1.5, c[1] - r * 0.75 - 1.0, 3.5, str(o.Reference))
            except Exception:
                pass
        cap = getattr(v, "Caption", "") or ""
        label = ""
        if v.isDerivedFrom("TechDraw::DrawViewSection"):
            label = f"SECTION {v.SectionSymbol}-{v.SectionSymbol}"
        elif v.isDerivedFrom("TechDraw::DrawViewDetail"):
            label = f"DETAIL {v.Reference}"
        if label or cap:
            try:
                bb = v.getVisibleEdges() and __import__("Part").makeCompound(v.getVisibleEdges()).BoundBox
                below = (H - Y + bb.YMax + 6) if bb else H - Y + 20
            except Exception:
                below = H - Y + 20
            sc = view_scale(v)
            sh.text(X, below, 3.0, (label + (f"  SCALE {sheets.scale_text(sc)}" if label else "")) or cap, "middle")
        return
    if t == "TechDraw::DrawViewDimension":
        draw_dim(sh, v, H)
        return
    if t == "TechDraw::DrawViewSpreadsheet":
        draw_table(doc, sh, v, H)
        return
    if v.isDerivedFrom("TechDraw::DrawViewSymbol"):
        rows = getattr(v, GDT_PROP, "") if GDT_PROP in v.PropertiesList else ""
        if not rows:
            raise ValueError("a symbol not made by these tools is left out of the file")
        data = json.loads(rows)
        fig = gdt.frame(data["rows"], data.get("h", 3.5)) if data.get("kind") == "frame" else gdt.datum(data["letter"], data.get("h", 3.5), data.get("side", "up"))
        X, Y = abs_xy(v)
        ox, oy = X - fig.w / 2, H - Y - fig.h / 2
        for pts, closed, filled in fig.polylines():
            sh.line([(ox + x, oy + y) for x, y in pts], "thin", closed=closed, filled=filled)
        for x, y, h, s, anchor in fig.texts:
            sh.text(ox + x, oy + y, h, s, anchor)
        return
    if t == "TechDraw::DrawLeaderLine":
        par = v.LeaderParent
        PX, PY = abs_xy(par) if par is not None else (0.0, 0.0)
        sc = view_scale(par) if par is not None and hasattr(par, "Scale") else 1.0
        ox, oy = PX + float(v.X) * sc, PY + float(v.Y) * sc
        pts = [(ox + p.x, H - (oy + p.y)) for p in v.WayPoints]
        if len(pts) >= 2:
            sh.line(pts, "thin")
            if str(getattr(v, "StartSymbol", "")).lower() not in ("none", "no symbol", "-1"):
                sh.arrow(pts[0], pts[1])
        return
    if t == "TechDraw::DrawViewAnnotation":
        X, Y = abs_xy(v)
        size = float(getattr(v, "TextSize", 5.0))
        for i, line in enumerate(list(v.Text)):
            sh.text(X, H - Y + i * size * 1.3, size * 0.75, line, "middle")
        return
    if t == "TechDraw::DrawViewBalloon":
        par = v.SourceView
        PX, PY = abs_xy(par)
        bx, by = PX + float(v.X), H - (PY + float(v.Y))
        tx, ty = PX + float(v.OriginX), H - (PY + float(v.OriginY))
        r = 4.0
        sh.circle(bx, by, r, "thin")
        d = math.hypot(tx - bx, ty - by) or 1
        sh.line([(bx + (tx - bx) / d * r, by + (ty - by) / d * r), (tx, ty)], "thin")
        sh.circle(tx, ty, 0.5, "thin")
        sh.text(bx, by + 1.2, 3.5, str(v.Text), "middle")
        return
    if t == "TechDraw::DrawViewSpreadsheet":
        draw_table(doc, sh, v, H)
        return
    raise ValueError(f"{t} is left out of the file")


def cut_line(sh, base, sv, X, Y, H):
    """A section's cutting line across its base view: chain-dotted, thick
    ends, arrows the way the section is looked at, the letter at each end."""
    import Part

    fr = frame_of(base, {})
    if fr is None:
        return
    o = V(sv.SectionOrigin)
    n = V(sv.SectionNormal)
    k = view_scale(base) / fr[0]
    a0 = [c * k for c in to_view(fr, base, o)]
    a1 = [c * k for c in to_view(fr, base, o + n * 10)]
    nx, ny = a1[0] - a0[0], a1[1] - a0[1]
    ln = math.hypot(nx, ny)
    if ln < 1e-9:
        return
    nx, ny = nx / ln, ny / ln
    dx, dy = -ny, nx  # along the cut, in the view's plane
    bb = Part.makeCompound(base.getVisibleEdges()).BoundBox
    half = 0.5 * math.hypot(bb.XLength, bb.YLength) + 4

    def P(x, y):
        return (X + x, H - Y + y)

    p, q = (a0[0] - dx * half, a0[1] - dy * half), (a0[0] + dx * half, a0[1] + dy * half)
    sh.line([P(*p), P(*q)], "trail")
    for e, sgn in ((p, 1), (q, -1)):
        inner = (e[0] + dx * sgn * 5, e[1] + dy * sgn * 5)
        sh.line([P(*e), P(*inner)], "visible")
        tail = (e[0] + dx * sgn * 2.5, e[1] + dy * sgn * 2.5)
        tip = (tail[0] - nx * 7, tail[1] - ny * 7)
        sh.line([P(*tail), P(*tip)], "thin")
        sh.arrow(P(*tip), P(*tail))
        sh.text(*P(tip[0] - nx * 3, tip[1] - ny * 3 + 1.2), 4.0, str(sv.SectionSymbol), "middle")


def draw_dim(sh, d, H):
    refs = d.References2D
    if not refs:
        raise ValueError("no references")
    v = refs[0][0]
    X, Y = abs_xy(v)

    def P(p):  # view geometry (y down) → svg page
        return (X + p.x, H - Y + p.y)

    text = d.getText() if hasattr(d, "getText") else f"{d.getRawValue():.2f}"
    tol = ""
    if not getattr(d, "Arbitrary", False):
        try:
            over, under = float(d.OverTolerance.Value), float(d.UnderTolerance.Value)
            if d.TheoreticalExact:
                pass
            elif d.EqualTolerance and abs(over) > 0:
                tol = f" ±{iso286._num(over)}"
            elif not d.EqualTolerance and (abs(over) > 0 or abs(under) > 0):
                tol = f" {iso286._dev(over)}/{iso286._dev(under)}"
        except Exception:
            pass
    label = text if (not tol or "±" in text or tol.strip() in text) else text + tol
    # the label's place: the dimension's X, Y (paper mm, y up) from the view's centre
    L = (X + float(d.X), H - (Y + float(d.Y)))
    typ = d.Type
    sc = view_scale(v)
    k = sc / gscale(v, {})
    if typ in ("Distance", "DistanceX", "DistanceY"):
        lp = d.getLinearPoints()[:2]
        # on the paper, or the part's mm in some releases: told by length
        dx_, dy_ = abs(lp[1].x - lp[0].x), abs(lp[1].y - lp[0].y)
        span = dx_ if typ == "DistanceX" else dy_ if typ == "DistanceY" else math.hypot(dx_, dy_)
        raw = float(d.getRawValue())
        f = sc if (abs(span - raw) < 1e-3 * max(1, raw) and abs(sc - 1) > 1e-9) else 1.0
        a, b = [P(V(p.x * f, p.y * f, 0)) for p in lp]
        if typ == "DistanceX":
            a2, b2 = (a[0], L[1]), (b[0], L[1])
        elif typ == "DistanceY":
            a2, b2 = (L[0], a[1]), (L[0], b[1])
        else:
            dx, dy = b[0] - a[0], b[1] - a[1]
            n = math.hypot(dx, dy) or 1
            nx, ny = -dy / n, dx / n
            off = (L[0] - a[0]) * nx + (L[1] - a[1]) * ny
            a2, b2 = (a[0] + nx * off, a[1] + ny * off), (b[0] + nx * off, b[1] + ny * off)
        sh.line([a, a2], "dim")
        sh.line([b, b2], "dim")
        sh.line([a2, b2], "dim")
        sh.arrow(a2, b2)
        sh.arrow(b2, a2)
        vertical = abs(a2[0] - b2[0]) < abs(a2[1] - b2[1])
        sh.text(L[0], L[1] - 1.0, 3.5, label, "vertical" if vertical else "middle")
        if getattr(d, "TheoreticalExact", False):
            # a basic size is boxed
            hw, hh = len(label) * 3.5 * 0.31 + 1.0, 2.6
            cx, cy = L[0], L[1] - 2.2
            if vertical:
                hw, hh, cx, cy = hh, hw, L[0] - 2.2 + 1.0, L[1]
            sh.line([(cx - hw, cy - hh), (cx + hw, cy - hh), (cx + hw, cy + hh), (cx - hw, cy + hh)], "thin", closed=True)
    elif typ in ("Radius", "Diameter"):
        e = v.getEdgeBySelection(ref_names(d)[0]) if hasattr(v, "getEdgeBySelection") else None
        cu = getattr(e, "Curve", None)
        if cu is not None and hasattr(cu, "Center"):
            c = P(V(cu.Center.x * k, cu.Center.y * k, 0))
            r = cu.Radius * k
            dx, dy = L[0] - c[0], L[1] - c[1]
            n = math.hypot(dx, dy) or 1
            ux, uy = dx / n, dy / n
            tip = (c[0] + ux * r, c[1] + uy * r)
            start = (c[0] - ux * r, c[1] - uy * r) if typ == "Diameter" else c
            sh.line([start, L], "dim")
            sh.arrow(tip, L if n > r else c)
            if typ == "Diameter":
                sh.arrow(start, c)
        sh.text(L[0], L[1] - 1.0, 3.5, label, "middle")
    else:
        sh.text(L[0], L[1] - 1.0, 3.5, label, "middle")


def draw_table(doc, sh, v, H):
    from .. import cells as C

    src = v.Source
    if src is None:
        raise ValueError("no sheet")
    (r0, c0), (r1, c1) = C.parse_range(f"{v.CellStart}:{v.CellEnd}")
    X, Y = abs_xy(v)
    size = float(getattr(v, "TextSize", 3.5))
    rh = size * 1.8
    cols = []
    for c in range(c0, c1 + 1):
        w = 0.0
        for r in range(r0, r1 + 1):
            try:
                val = src.get(C.name(r, c))
            except Exception:
                val = ""
            w = max(w, len(str(val)) * size * 0.55)
        cols.append(max(w + size, size * 3))
    tw, th = sum(cols), rh * (r1 - r0 + 1)
    x0, y0 = X - tw / 2, H - Y - th / 2
    for i in range(r1 - r0 + 2):
        sh.line([(x0, y0 + i * rh), (x0 + tw, y0 + i * rh)], "thin")
    x = x0
    for w in cols + [0]:
        sh.line([(x, y0), (x, y0 + th)], "thin")
        x += w
    for i, r in enumerate(range(r0, r1 + 1)):
        x = x0
        for j, c in enumerate(range(c0, c1 + 1)):
            try:
                val = src.get(C.name(r, c))
            except Exception:
                val = ""
            if hasattr(val, "Value"):
                val = str(val)
            elif isinstance(val, float):
                val = f"{val:g}"
            sh.text(x + size * 0.4, y0 + i * rh + rh * 0.7, size * 0.8, str(val))
            x += cols[j]


def tools(session):
    s = session
    doc = lambda: s.doc  # noqa: E731
    frames = {}

    def recompute(objs=()):
        doc().recompute()
        frames.clear()
        if objs:
            wait(objs)
            doc().recompute()
            settle()

    def settle(seconds=0.15):
        # TechDraw finishes a view's lines (and a section's faces) on worker
        # threads; let them land before the next change touches the views.
        try:
            from PySide import QtCore
        except ImportError:
            try:
                from PySide2 import QtCore
            except ImportError:
                return
        import time

        app = QtCore.QCoreApplication.instance()
        if app is None:
            return
        t0 = time.time()
        while time.time() - t0 < seconds:
            app.processEvents()
            time.sleep(0.01)

    def done(tool, out):
        s.snapshot(tool)
        out["step"] = len(s.steps)
        return out

    def place_xy(page, at, default):
        if at is None:
            return default
        x, y = pt2(at, "at")
        W, H = paper_size(page)
        if not (0 <= x <= W and 0 <= y <= H):
            raise bad(f"at: on the paper, 0..{W:g} by 0..{H:g} mm from its bottom-left corner.")
        return x, y

    def view_reading(v):
        out = {"view": v.Label, "name": v.Name, "type": v.TypeId.split("::")[-1], "at": [num(v.X, 2), num(v.Y, 2)]}
        if v.isDerivedFrom("TechDraw::DrawViewPart"):
            out["scale"] = num(view_scale(v), 4)
            try:
                es = v.getVisibleEdges() or []
                if es:
                    import Part

                    bb = Part.makeCompound(es).BoundBox
                    out["size"] = [num(bb.XLength, 2), num(bb.YLength, 2)]
                out["edges"], out["vertices"] = len(edges_of(v)), len(vertices_of(v))
            except Exception:
                pass
        return out

    def bodies_for(refs):
        objs = [s.body(r) for r in refs] if refs else s.bodies()
        if not objs:
            raise ToolError("not_found", "No body in the document to draw.")
        return objs

    def direction(d, what="direction"):
        if isinstance(d, str):
            if d not in DIRS:
                raise bad(f"{what}: one of {', '.join(DIRS)}, or [x, y, z] (from the part toward the viewer).")
            z, x = DIRS[d]
            return V(*z), V(*x)
        z = vec(d, what)
        if z.Length < 1e-9:
            raise bad(f"{what}: not zero.")
        z.normalize()
        x = V(1, 0, 0) if abs(z.x) < 0.9 else V(0, 1, 0)
        x = x - z * x.dot(z)
        x.normalize()
        return z, x

    # ---- pages and title blocks ----

    def td_page(a):
        act = a.get("action", "list")
        if act == "templates":
            root = template_root()
            return {"root": root, "templates": [os.path.relpath(p, root) for p in templates()]}
        if act == "list":
            return {"pages": [page_reading(p) for p in pages(doc())]} if pages(doc()) else {"pages": [], "note": "None yet: td_page { action: \"new\", paper: \"A4\" }."}
        if act == "read":
            return page_reading(page_of(doc(), a.get("page")), full=True)
        if act != "new":
            raise bad("action: new, list, read or templates.")
        paper = a.get("paper", "A4")
        tpl = a.get("template")
        if tpl:
            p = os.path.expanduser(tpl)
            if not os.path.isabs(p):
                p = os.path.join(template_root(), p) if os.path.isfile(os.path.join(template_root(), p)) else os.path.join(s.out_dir, p)
            if not os.path.isfile(p):
                raise ToolError("not_found", f"template {tpl!r}: no such file — td_page {{ action: \"templates\" }} lists the build's own.")
        else:
            if paper not in PAPERS:
                raise bad(f"paper: one of {', '.join(PAPERS)}.")
            p = find_template(paper, a.get("orientation") == "portrait", bool(a.get("blank")), a.get("standard"))
            std_note = None
            if p and a.get("standard") and f"{os.sep}{a['standard'].upper()}{os.sep}" not in p:
                std_note = f"No {a['standard'].upper()} template for {paper}: {os.path.basename(p)} is used (ASME sizes: Letter, ANSIB … ANSIE)."
            if p is None:
                raise ToolError("not_found", f"No {paper} {a.get('orientation', 'landscape')} template in this build — td_page {{ action: \"templates\" }} lists them; pass template.")
        page = doc().addObject("TechDraw::DrawPage", "Page")
        page.Label = s.unique(a.get("name") or "Drawing")
        t = doc().addObject("TechDraw::DrawSVGTemplate", "Template")
        t.Template = p
        page.Template = t
        if a.get("projection") in ("first", "third"):
            set_enum(page, "ProjectionType", "First angle" if a["projection"] == "first" else "Third angle")
        if a.get("scale"):
            page.Scale = float(a["scale"])
        recompute()
        # The template's own example values (names, numbers) are not this
        # drawing's: cleared. What FreeCAD fills in itself (today's date,
        # the scale) differs from them and stays.
        try:
            texts = dict(t.EditableTexts)
            samples = template_defaults(p)
            cleared = [k for k, v in texts.items() if v and samples.get(k) == v and "scale" not in k.lower()]
            if cleared:
                t.EditableTexts = {k: ("" if k in cleared else v) for k, v in texts.items()}
                recompute()
        except Exception:
            cleared = []
        out = page_reading(page, full=True)
        if cleared:
            out["cleared"] = cleared
        if not tpl and std_note:
            out["note"] = std_note
        out["next"] = "td_title { fields } fills the title block (ask the person for names and numbers); td_view or td_projection puts the part on."
        return done("td_page", out)

    def page_reading(p, full=False):
        W, H = paper_size(p)
        out = {"page": p.Label, "name": p.Name, "paper": [num(W, 1), num(H, 1)], "template": os.path.basename(getattr(p.Template, "Template", "") or ""),
               "projection": getattr(p, "ProjectionType", ""), "views": []}
        for v in p.Views:
            out["views"].append(view_reading(v))
            if v.isDerivedFrom("TechDraw::DrawProjGroup"):
                out["views"][-1]["items"] = [view_reading(x) for x in v.Views]
        if full:
            try:
                out["titleBlock"] = dict(p.Template.EditableTexts)
            except Exception:
                out["titleBlock"] = {}
        return out

    def td_title(a):
        page = page_of(doc(), a.get("page"))
        t = page.Template
        try:
            texts = dict(t.EditableTexts)
        except Exception:
            raise ToolError("invalid_state", f"{page.Label}'s template has no editable fields (a blank one).")
        pre = a.get("preset")
        if a.get("export"):
            data = load_presets()
            path = os.path.expanduser(a["export"])
            if not os.path.isabs(path):
                path = os.path.join(s.out_dir, path)
            save_presets(data, path)
            return {"exported": path, "presets": sorted(data)}
        if a.get("import"):
            path = os.path.expanduser(a["import"])
            if not os.path.isabs(path):
                path = os.path.join(s.out_dir, path)
            got = load_presets(path)
            if not got:
                raise ToolError("not_found", f"{path}: no presets in it.")
            data = load_presets()
            data.update(got)
            save_presets(data)
            return {"imported": sorted(got), "presets": sorted(data)}
        fields = dict(a.get("fields") or {})
        if pre and a.get("save"):
            data = load_presets()
            data[pre] = {k: v for k, v in texts.items() if str(v).strip()} | {k: str(v) for k, v in fields.items()}
            save_presets(data)
        elif pre:
            data = load_presets()
            if pre not in data:
                raise ToolError("not_found", f"preset {pre!r}: there are {', '.join(sorted(data)) or 'none'} (save one with preset and save: true).")
            fields = {**data[pre], **fields}
        if not fields:
            return {"page": page.Label, "fields": texts, "presets": sorted(load_presets()), "note": "Set with fields { name: value } — the names are the template's own."}
        low = {k.lower(): k for k in texts}
        unknown = []
        for k, v in fields.items():
            key = k if k in texts else low.get(k.lower())
            if key is None:
                unknown.append(k)
                continue
            texts[key] = str(v)
        if unknown and not a.get("preset"):
            raise bad(f"No field {', '.join(unknown)} in this template — it has: {', '.join(sorted(texts))}.")
        t.EditableTexts = texts
        recompute()
        out = {"page": page.Label, "fields": dict(t.EditableTexts)}
        if unknown:
            out["notInThisTemplate"] = unknown
        if pre and a.get("save"):
            out["saved"] = pre
        return done("td_title", out)

    # ---- views ----

    def td_view(a):
        page = page_of(doc(), a.get("page"))
        objs = bodies_for(a.get("bodies"))
        z, x = direction(a.get("direction", "front"))
        v = doc().addObject("TechDraw::DrawViewPart", "View")
        page.addView(v)
        v.Label = s.unique(a.get("name") or (a.get("direction") if isinstance(a.get("direction"), str) else "View").capitalize())
        v.Source = objs
        v.Direction = z
        v.XDirection = x
        if a.get("scale"):
            v.ScaleType = "Custom"
            v.Scale = float(a["scale"])
        W, H = paper_size(page)
        v.X, v.Y = place_xy(page, a.get("at"), (W * 0.35, H * 0.55))
        if a.get("hidden"):
            v.HardHidden = True
        recompute([v])
        return done("td_view", view_reading(v))

    def td_projection(a):
        page = page_of(doc(), a.get("page"))
        objs = bodies_for(a.get("bodies"))
        want = a.get("views") or ["front", "top", "right"]
        bad_ = [w for w in want if w not in PROJ_ITEMS]
        if bad_:
            raise bad(f"views: any of {', '.join(PROJ_ITEMS)}.")
        g = doc().addObject("TechDraw::DrawProjGroup", "ProjGroup")
        page.addView(g)
        g.Source = objs
        if a.get("projection") in ("first", "third"):
            set_enum(g, "ProjectionType", "First angle" if a["projection"] == "first" else "Third angle")
        elif getattr(page, "ProjectionType", ""):
            try:
                set_enum(g, "ProjectionType", page.ProjectionType)
            except ToolError:
                pass
        g.addProjection("Front")
        if a.get("scale"):
            g.ScaleType = "Custom"
            g.Scale = float(a["scale"])
        for w in want:
            if PROJ_ITEMS[w] != "Front":
                g.addProjection(PROJ_ITEMS[w])
        try:
            anchor = g.Anchor
            z, x = direction(a.get("front", "front"), "front")
            anchor.Direction = z
            anchor.XDirection = x
        except Exception:
            pass
        W, H = paper_size(page)
        g.X, g.Y = place_xy(page, a.get("at"), (W * 0.4, H * 0.55))
        g.AutoDistribute = True
        recompute(list(g.Views))
        out = view_reading(g)
        out["projection"] = g.ProjectionType
        out["items"] = [view_reading(x) for x in g.Views]
        return done("td_projection", out)

    def td_section(a):
        base = view_of(doc(), a.get("view"))
        page = page_holding(base)
        normal = vec(a.get("normal"), "normal") if isinstance(a.get("normal"), list) else None
        if normal is None:
            if a.get("normal") not in ("x", "y", "z", "-x", "-y", "-z"):
                raise bad("normal: the cut plane's normal, pointing toward the viewer of the section — \"x\" | \"-y\" | … or [x, y, z].")
            sgn = -1.0 if a["normal"].startswith("-") else 1.0
            normal = V(*[sgn if c == a["normal"][-1] else 0.0 for c in "xyz"])
        normal.normalize()
        if abs(normal.dot(V(base.Direction))) > 0.99:
            raise bad("normal: the plane must cut across the base view, not lie in its paper — choose a normal along the base view's paper.")
        sh = source_shapes(base)
        origin = vec(a["through"], "through") if a.get("through") is not None else sh.BoundBox.Center
        sym = str(a.get("symbol") or next_letter(doc(), "section"))
        sv = doc().addObject("TechDraw::DrawViewSection", "Section")
        page.addView(sv)
        sv.Label = s.unique(f"Section {sym}")
        sv.BaseView = base
        sv.Source = list(base.Source)
        sv.ScaleType = "Custom"
        sv.Scale = view_scale(base)
        sv.SectionNormal = normal
        sv.SectionOrigin = origin
        sv.SectionSymbol = sym
        sv.Direction = normal
        bx = V(base.XDirection)
        x = bx - normal * bx.dot(normal)
        if x.Length < 1e-6:
            x = V(base.Direction).cross(normal)
        x.normalize()
        sv.XDirection = x
        by = V(base.Direction).cross(bx)
        side = "Right" if normal.dot(bx) > 0.7 else "Left" if normal.dot(bx) < -0.7 else "Up" if normal.dot(by) > 0.7 else "Down" if normal.dot(by) < -0.7 else None
        if side:
            try:
                set_enum(sv, "SectionDirection", side)
            except Exception:
                pass
        W, H = paper_size(page)
        bxp, byp = abs_xy(base)
        off = {"Right": (W * 0.25, 0), "Left": (-W * 0.25, 0), "Up": (0, H * 0.3), "Down": (0, -H * 0.3)}.get(side or "Right", (W * 0.25, 0))
        sv.X, sv.Y = place_xy(page, a.get("at"), (min(W - 20, max(20, bxp + off[0])), min(H - 20, max(20, byp + off[1]))))
        if a.get("hatch") is False:
            try:
                sv.CutSurfaceDisplay = "None"
            except Exception:
                pass
        recompute([base, sv])
        out = view_reading(sv)
        out.update({"symbol": sym, "base": base.Label, "through": [num(origin.x, 3), num(origin.y, 3), num(origin.z, 3)], "normal": [num(normal.x), num(normal.y), num(normal.z)]})
        if not out.get("edges"):
            out["warning"] = "The section has no geometry: the plane may miss the part (through)."
        return done("td_section", out)

    def next_letter(d, kind):
        used = set()
        for o in d.Objects:
            if o.isDerivedFrom("TechDraw::DrawViewSection"):
                used.add(o.SectionSymbol)
            elif o.isDerivedFrom("TechDraw::DrawViewDetail"):
                used.add(o.Reference)
        for ch in "ABCDEFGHJKLMNPRSTUVWXYZ":
            if ch not in used:
                return ch
        return "Z"

    def td_detail(a):
        base = view_of(doc(), a.get("view"))
        page = page_holding(base)
        r = float(a.get("radius", 0) or 0)
        if r <= 0:
            raise bad("radius: the circle drawn around the detail, mm on the part, over 0.")
        sym = str(a.get("symbol") or next_letter(doc(), "detail"))
        dv = doc().addObject("TechDraw::DrawViewDetail", "Detail")
        page.addView(dv)
        dv.Label = s.unique(f"Detail {sym}")
        dv.BaseView = base
        dv.Source = list(base.Source)
        dv.Direction = V(base.Direction)
        dv.XDirection = V(base.XDirection)
        if a.get("at3d") is not None or a.get("center") is not None:
            p3 = vec(a.get("center", a.get("at3d")), "center")
            fr = frame_of(base, frames)
            if fr is None:
                recompute([base])
                fr = frame_of(base, frames)
            q = to_view(fr, base, p3)
            sc = fr[0]
            # AnchorPoint: the base view's own, unscaled, y up
            anchor = V(q[0] / sc, -q[1] / sc if fr[1] < 0 else q[1] / sc, 0)
        else:
            anchor = V(*pt2(a.get("anchor", [0, 0]), "anchor"), 0)
        dv.AnchorPoint = anchor
        dv.Radius = r
        dv.Reference = sym
        dv.ScaleType = "Custom"
        dv.Scale = float(a.get("scale") or view_scale(base) * 2)
        W, H = paper_size(page)
        bxp, byp = abs_xy(base)
        dv.X, dv.Y = place_xy(page, a.get("at"), (min(W - 30, bxp + W * 0.3), max(30, byp - H * 0.25)))
        recompute([base, dv])
        out = view_reading(dv)
        out.update({"symbol": sym, "base": base.Label, "anchor": [num(anchor.x, 3), num(anchor.y, 3)], "radius": r})
        if not out.get("edges"):
            out["warning"] = "The detail is empty: its circle misses the part. Give center as a point on the part [x, y, z], or anchor [x, y] from td_geometry."
        return done("td_detail", out)

    def td_geometry(a):
        v = view_of(doc(), a.get("view"))
        if not edges_of(v):
            recompute([v])
        kind = a.get("kind", "edges")
        sc = gscale(v, frames)
        cap = int(a.get("limit", 120))
        if kind == "vertices":
            vs = vertices_of(v)
            items = [{"ref": f"Vertex{i}", "at": [num(p.x / sc, 3), num(p.y / sc, 3)]} for i, p in enumerate(vs)]
        elif kind in ("edges", "circles", "lines"):
            items = []
            for i, e in enumerate(edges_of(v)):
                info = edge_info(e, sc)
                if kind == "circles" and info["kind"] not in ("circle", "arc"):
                    continue
                if kind == "lines" and info["kind"] != "line":
                    continue
                items.append({"ref": f"Edge{i}", **info})
        else:
            raise bad("kind: edges, circles, lines or vertices.")
        out = {"view": v.Label, "scale": num(view_scale(v)), "units": "mm on the part, in the view's plane (x right, y as TechDraw keeps it)", "items": items[:cap]}
        if len(items) > cap:
            out["more"] = f"{len(items) - cap} more: kind narrows, or limit."
        out["byPoint"] = "Or name geometry by the part instead: { point: [x,y,z] }, { circle: { diameter, near } }, { edge: { through: [p, q] } }."
        return out

    # ---- dimensions and tolerances ----

    def td_dim(a):
        v = view_of(doc(), a.get("view"))
        page = page_holding(v)
        typ = a.get("type", "Distance")
        typ = {"distance": "Distance", "horizontal": "DistanceX", "vertical": "DistanceY", "radius": "Radius", "diameter": "Diameter", "angle": "Angle"}.get(typ, typ)
        if typ not in DIM_TYPES:
            raise bad("type: distance | horizontal | vertical | radius | diameter | angle.")
        refs = a.get("refs") or []
        if not edges_of(v):
            recompute([v])
        names = [resolve_ref(v, r, frames) for r in refs]
        if typ in ("Radius", "Diameter") and not (len(names) == 1 and names[0].startswith("Edge")):
            raise bad(f"{typ.lower()}: one circle or arc — refs [{{ circle: {{ diameter, near }} }}] or [\"Edge7\"].")
        if typ == "Angle" and not (len(names) == 2 and all(n.startswith("Edge") for n in names)):
            raise bad("angle: two straight edges.")
        if typ.startswith("Distance") and not (len(names) == 2 or (len(names) == 1 and names[0].startswith("Edge"))):
            raise bad(f"{typ}: two vertices (or an edge and a vertex), or one straight edge.")
        d = doc().addObject("TechDraw::DrawViewDimension", "Dimension")
        d.Type = typ
        d.MeasureType = "Projected"
        d.References2D = [(v, n) for n in names]
        page.addView(d)
        if a.get("at") is not None:
            ox, oy = pt2(a["at"], "at")
            d.X, d.Y = ox, oy
        recompute()
        try:
            raw = float(d.getRawValue())
        except Exception as e:
            doc().removeObject(d.Name)
            raise ToolError("failed", f"TechDraw cannot measure that: {e} — nothing was kept.")
        apply_tol(d, a, raw)
        recompute()
        return done("td_dim", dim_reading(d))

    def apply_tol(d, a, raw):
        if a.get("format"):
            d.FormatSpec = str(a["format"])
        if a.get("label") is not None:
            d.Arbitrary = bool(a["label"])
            if a["label"]:
                d.FormatSpec = str(a["label"])
        if a.get("basic") is not None:
            d.TheoreticalExact = bool(a["basic"])
        if a.get("fit"):
            try:
                up, lo = iso286.limits(raw, a["fit"])
            except ValueError as e:
                raise bad(f"fit: {e}")
            d.EqualTolerance = False
            d.OverTolerance = up
            d.UnderTolerance = lo
            spec = d.FormatSpec
            if a["fit"] not in spec:
                d.FormatSpec = f"{spec} {a['fit']}"
        elif a.get("plus") is not None or a.get("minus") is not None:
            p = float(a.get("plus") or 0)
            m = a.get("minus")
            m = -abs(float(m)) if m is not None else -p
            if abs(p + m) < 1e-12:
                d.EqualTolerance = True
                d.OverTolerance = p
            else:
                d.EqualTolerance = False
                d.OverTolerance = p
                d.UnderTolerance = m

    def dim_reading(d):
        out = {"dimension": d.Name, "label": d.Label, "type": d.Type, "refs": ref_names(d), "view": d.References2D[0][0].Label if d.References2D else None}
        try:
            out["measured"] = num(d.getRawValue(), 4)
        except Exception as e:
            out["error"] = str(e)
        try:
            out["text"] = d.getText()
        except Exception:
            pass
        if d.TheoreticalExact:
            out["basic"] = True
        try:
            over, under = float(d.OverTolerance.Value), float(d.UnderTolerance.Value)
            if d.EqualTolerance and over:
                out["tolerance"] = f"±{iso286._num(over)}"
            elif not d.EqualTolerance and (over or under):
                out["tolerance"] = f"{iso286._dev(over)}/{iso286._dev(under)}"
        except Exception:
            pass
        return out

    def td_tolerance(a):
        d = obj_of(doc(), a.get("dimension"), ["TechDraw::DrawViewDimension"], "dimension")
        raw = float(d.getRawValue())
        if a.get("clear"):
            d.EqualTolerance = True
            d.OverTolerance = 0
            d.UnderTolerance = 0
            d.TheoreticalExact = False
            d.Arbitrary = False
            d.FormatSpec = "%.2f" if d.Type in ("Distance", "DistanceX", "DistanceY", "Radius", "Diameter") else d.FormatSpec
        apply_tol(d, a, raw)
        recompute()
        return done("td_tolerance", dim_reading(d))

    # ---- GD&T, notes, balloons, tables ----

    def leader(view, p_from, to_page):
        """A leader from a point on the view (view mm, y as geometry) to a
        page point: TechDraw's own leader line, owned by the view."""
        page = page_holding(view)
        ln = doc().addObject("TechDraw::DrawLeaderLine", "Leader")
        page.addView(ln)
        ln.LeaderParent = view
        sc = view_scale(view)
        vx, vy = abs_xy(view)
        # anchored at the feature: X, Y in the view's unscaled frame, y up
        ln.X, ln.Y = p_from[0] / sc, -p_from[1] / sc
        start = V(0, 0, 0)
        end = V(to_page[0] - (vx + p_from[0]), to_page[1] - (vy - p_from[1]), 0)
        # a slanted run, then a short horizontal shoulder into the frame, as
        # drawn by hand; TechDraw would flatten a two-point leader whole
        shoulder = 5.0 if end.x >= 0 else -5.0
        elbow = V(end.x - shoulder, end.y, 0) if abs(end.x) > 6 else V(end.x, end.y, 0)
        ln.WayPoints = [start, elbow, end] if elbow != end else [start, end]
        try:
            ln.AutoHorizontal = False
        except Exception:
            pass
        for prop, want in (("StartSymbol", ("Filled Arrow", "FILLED_ARROW")), ("EndSymbol", ("None", "No Symbol", "NONE"))):
            for w in want:
                try:
                    set_enum(ln, prop, w)
                    break
                except Exception:
                    continue
        return ln

    def ref_point(v, r):
        """A view-geometry point for a reference: a vertex, an edge's middle,
        a circle's rim nearest the part's outside."""
        n = resolve_ref(v, r, frames)
        if n.startswith("Vertex"):
            p = vertices_of(v)[int(n[6:])]
            return n, to_paper(v, frames, (p.x, p.y))
        e = edges_of(v)[int(n[4:])]
        m = e.valueAt((e.FirstParameter + e.LastParameter) / 2)
        return n, to_paper(v, frames, (m.x, m.y))

    def symbol(page, fig, data, at, label):
        sym = doc().addObject("TechDraw::DrawViewSymbol", "Symbol")
        page.addView(sym)
        sym.Label = s.unique(label)
        sym.Symbol = fig.svg()
        sym.addProperty("App::PropertyString", GDT_PROP, "AgentLink", "What this symbol is, for export and editing")
        setattr(sym, GDT_PROP, json.dumps(data))
        sym.X, sym.Y = at
        try:
            sym.Scale = 1.0
            sym.ScaleType = "Custom"
        except Exception:
            pass
        return sym

    def td_gdt(a):
        rows = a.get("frame") or ([a] if a.get("characteristic") else [])
        rows = [{k: r.get(k) for k in ("characteristic", "tolerance", "diameter", "modifier", "datums") if r.get(k) is not None} for r in rows]
        try:
            fig = gdt.frame(rows, float(a.get("height", 3.5)))
        except gdt.GdtError as e:
            raise bad(str(e))
        if a.get("dimension"):
            d = obj_of(doc(), a["dimension"], ["TechDraw::DrawViewDimension"], "dimension")
            v = d.References2D[0][0]
            page = page_holding(v)
            vx, vy = abs_xy(v)
            at = place_xy(page, a.get("at"), (vx + float(d.X), vy + float(d.Y) - fig.h - 2))
            sym = symbol(page, fig, {"kind": "frame", "rows": rows, "h": float(a.get("height", 3.5))}, at, "Frame")
            ln = None
        else:
            v = view_of(doc(), a.get("view"))
            page = page_holding(v)
            if a.get("on") is None:
                raise bad("on: the feature the frame points to (\"Edge3\", { point }, { circle }…), or dimension: a dimension to sit under.")
            if not edges_of(v):
                recompute([v])
            ref, p = ref_point(v, a["on"])
            vx, vy = abs_xy(v)
            at = place_xy(page, a.get("at"), (vx + p[0] + 25, vy - p[1] + 15))
            sym = symbol(page, fig, {"kind": "frame", "rows": rows, "h": float(a.get("height", 3.5)), "on": ref}, at, "Frame")
            ln = leader(v, p, (at[0] - fig.w / 2, at[1]))
        recompute()
        return done("td_gdt", {"symbol": sym.Name, "reads": gdt.describe(rows), "at": [num(sym.X, 2), num(sym.Y, 2)], "size": [num(fig.w, 2), num(fig.h, 2)], **({"leader": ln.Name} if ln else {})})

    def td_datum(a):
        v = view_of(doc(), a.get("view"))
        page = page_holding(v)
        try:
            fig = gdt.datum(a.get("letter"), float(a.get("height", 3.5)))
        except gdt.GdtError as e:
            raise bad(str(e))
        if not edges_of(v):
            recompute([v])
        ref, p = ref_point(v, a.get("on"))
        vx, vy = abs_xy(v)
        # The triangle stands on the feature, the letter outward from the
        # view's middle (page y up; the geometry's y is inverted).
        ox, oy = p[0], -p[1]
        side = ("right" if ox > 0 else "left") if abs(ox) > abs(oy) else ("up" if oy > 0 else "down")
        try:
            fig = gdt.datum(a.get("letter"), float(a.get("height", 3.5)), side)
        except gdt.GdtError as e:
            raise bad(str(e))
        fx, fy = vx + ox, vy + oy  # the triangle's base, on the page
        cx = fx + {"right": fig.w / 2, "left": -fig.w / 2}.get(side, 0.0)
        cy = fy + {"up": fig.h / 2, "down": -fig.h / 2}.get(side, 0.0)
        at = place_xy(page, a.get("at"), (cx, cy))
        sym = symbol(page, fig, {"kind": "datum", "letter": a["letter"], "h": float(a.get("height", 3.5)), "side": side, "on": ref}, at, f"Datum {a['letter']}")
        recompute()
        return done("td_datum", {"symbol": sym.Name, "datum": a["letter"], "on": ref, "side": side, "at": [num(sym.X, 2), num(sym.Y, 2)]})

    def td_note(a):
        page = page_of(doc(), a.get("page"))
        text = a.get("text")
        if not text:
            raise bad("text: the note, lines split by \\n.")
        n = doc().addObject("TechDraw::DrawViewAnnotation", "Note")
        page.addView(n)
        n.Text = str(text).split("\n")
        n.TextSize = float(a.get("size", 5.0))
        W, H = paper_size(page)
        n.X, n.Y = place_xy(page, a.get("at"), (40, 40))
        recompute()
        return done("td_note", {"note": n.Name, "lines": len(n.Text), "at": [num(n.X, 2), num(n.Y, 2)]})

    def td_balloon(a):
        v = view_of(doc(), a.get("view"))
        page = page_holding(v)
        if not edges_of(v):
            recompute([v])
        ref, p = ref_point(v, a.get("on"))
        b = doc().addObject("TechDraw::DrawViewBalloon", "Balloon")
        b.SourceView = v
        page.addView(b)
        b.OriginX, b.OriginY = p[0], -p[1]
        text = a.get("text")
        if text is None:
            try:
                text = str(page.NextBalloonIndex)
                page.NextBalloonIndex = page.NextBalloonIndex + 1
            except Exception:
                text = "1"
        b.Text = str(text)
        if a.get("at") is not None:
            ox, oy = pt2(a["at"], "at")
            b.X, b.Y = ox, oy
        else:
            b.X, b.Y = p[0] + 15, -p[1] + 15
        recompute()
        return done("td_balloon", {"balloon": b.Name, "text": b.Text, "on": ref})

    def td_table(a):
        from . import sheet as SH
        from .. import cells as C

        page = page_of(doc(), a.get("page"))
        ss = SH.find(doc(), a.get("sheet"))
        rng = a.get("range") or C.bounds(SH.filled(ss))
        if not rng:
            raise ToolError("invalid_state", f"{ss.Label} is empty.")
        try:
            (r0, c0), (r1, c1) = C.parse_range(rng)
        except C.AddressError as e:
            raise bad(f"range: {e}.")
        t = doc().addObject("TechDraw::DrawViewSpreadsheet", "Table")
        page.addView(t)
        t.Source = ss
        t.CellStart, t.CellEnd = C.name(r0, c0), C.name(r1, c1)
        t.TextSize = float(a.get("size") or 3.5)
        W, H = paper_size(page)
        t.X, t.Y = place_xy(page, a.get("at"), (W * 0.75, H * 0.75))
        recompute()
        return done("td_table", {"table": t.Name, "sheet": ss.Label, "range": C.range_text((r0, c0), (r1, c1)), "at": [num(t.X, 2), num(t.Y, 2)], "note": "It follows the sheet: change cells with sheet_set."})

    # ---- reading and writing out ----

    def td_list(a):
        page = page_of(doc(), a.get("page"))
        out = page_reading(page, full=True)
        dims, syms, other = [], [], []
        for o in page.Views:
            if o.TypeId == "TechDraw::DrawViewDimension":
                dims.append(dim_reading(o))
            elif o.isDerivedFrom("TechDraw::DrawViewSymbol") and GDT_PROP in o.PropertiesList:
                data = json.loads(getattr(o, GDT_PROP))
                syms.append({"symbol": o.Name, "kind": data["kind"], "reads": gdt.describe(data["rows"]) if data["kind"] == "frame" else f"datum {data['letter']}", "on": data.get("on")})
            elif not o.isDerivedFrom("TechDraw::DrawViewPart") and not o.isDerivedFrom("TechDraw::DrawProjGroup"):
                other.append({"object": o.Name, "type": o.TypeId.split("::")[-1]})
        out["views"] = [x for x in out["views"] if x["type"] not in ("DrawViewDimension", "DrawViewSymbol", "DrawLeaderLine", "DrawViewAnnotation", "DrawViewBalloon", "DrawViewSpreadsheet")]
        out["dimensions"] = dims
        if syms:
            out["gdt"] = syms
        if other:
            out["other"] = other
        return out

    def td_export(a):
        page = page_of(doc(), a.get("page"))
        kinds = a.get("formats") or ["svg", "pdf", "dxf"]
        if any(k not in ("svg", "pdf", "dxf") for k in kinds):
            raise bad("formats: any of svg, pdf, dxf.")
        recompute([v for v in page.Views])
        base = os.path.join(s.out_dir, (a.get("name") or page.Label).replace(" ", "-"))
        files, notes = {}, []
        sheet_, skipped = page_sheet(doc(), page)
        if skipped:
            notes += skipped
        for k in kinds:
            p = f"{base}.{k}"
            if k == "dxf":
                try:
                    TechDraw.writeDXFPage(page, p)
                    if os.path.isfile(p) and os.path.getsize(p) > 0:
                        files[k] = {"path": p, "bytes": os.path.getsize(p), "by": "TechDraw"}
                        continue
                except Exception as e:
                    notes.append(f"TechDraw's DXF writer failed ({e}): written here instead")
            data = {"svg": sheet_.svg, "pdf": sheet_.pdf, "dxf": sheet_.dxf}[k]()
            with open(p, "wb" if isinstance(data, bytes) else "w", **({} if isinstance(data, bytes) else {"encoding": "utf-8"})) as f:
                f.write(data)
            files[k] = {"path": p, "bytes": os.path.getsize(p)}
        out = {"page": page.Label, "files": files}
        if notes:
            out["notes"] = notes
        return out

    ref_schema = {"type": ["string", "object"]}
    return [
        Tool("td_page", "TechDraw pages: action new { paper A4|A3|A2|A1|A0|Letter|ANSIB… , orientation landscape|portrait, standard iso|asme, blank, template (a path), projection first|third, scale, name } on the build's own templates; list; read (views and title-block fields); templates.",
             obj({"action": {"type": "string", "enum": ["new", "list", "read", "templates"]}, "page": {"type": "string"}, "paper": {"type": "string"}, "orientation": {"type": "string"}, "standard": {"type": "string"}, "blank": {"type": "boolean"},
                  "template": {"type": "string"}, "projection": {"type": "string"}, "scale": {"type": "number"}, "name": {"type": "string"}}), td_page, changes=True),
        Tool("td_title", "The title block: fields { name: value } by the template's own field names (no fields: lists them); preset \"mine\" fills from a saved one, with save: true saves the page's as it; import / export a presets file (JSON).",
             obj({"page": {"type": "string"}, "fields": {"type": "object"}, "preset": {"type": "string"}, "save": {"type": "boolean"}, "import": {"type": "string"}, "export": {"type": "string"}}), td_title, changes=True),
        Tool("td_view", "Put a view of bodies on a page: direction front|top|right|left|rear|bottom|iso or [x, y, z] (part toward viewer), scale, at [x, y] page mm (bottom-left origin), hidden (dashed hidden lines), name.",
             obj({"page": {"type": "string"}, "bodies": {"type": "array", "items": {"type": "string"}}, "direction": {"type": ["string", "array"]}, "scale": {"type": "number"}, "at": {"type": "array"}, "hidden": {"type": "boolean"}, "name": {"type": "string"}}), td_view, changes=True),
        Tool("td_projection", "A projection group (views kept aligned): views [front, top, right, left, rear, bottom, iso …], projection first|third (default the page's), front (which way is front: a direction), scale, at.",
             obj({"page": {"type": "string"}, "bodies": {"type": "array", "items": {"type": "string"}}, "views": {"type": "array", "items": {"type": "string"}}, "projection": {"type": "string"}, "front": {"type": ["string", "array"]}, "scale": {"type": "number"}, "at": {"type": "array"}}), td_projection, changes=True),
        Tool("td_section", "A section view cut from a view: normal of the cut plane, toward who looks at the section (\"x\" | \"-y\" | [x,y,z]; it lies across the base view), through [x, y, z] a point of the plane (default the part's middle), symbol (A …), at, hatch false.",
             obj({"view": {"type": "string"}, "normal": {"type": ["string", "array"]}, "through": {"type": "array"}, "symbol": {"type": "string"}, "at": {"type": "array"}, "hatch": {"type": "boolean"}}, ["view", "normal"]), td_section, changes=True),
        Tool("td_detail", "A detail view: a circle of radius (mm on the part) around center [x, y, z] (a point on the part) or anchor [x, y] (td_geometry's view mm), drawn larger (scale, default twice the base), symbol (B …), at.",
             obj({"view": {"type": "string"}, "center": {"type": "array"}, "anchor": {"type": "array"}, "radius": {"type": "number"}, "scale": {"type": "number"}, "symbol": {"type": "string"}, "at": {"type": "array"}}, ["view", "radius"]), td_detail, changes=True),
        Tool("td_geometry", "A view's geometry, to dimension against: kind edges | circles | lines | vertices, each with its ref (Edge3, Vertex5), kind, ends or centre and diameter (mm on the part). Changes nothing.",
             obj({"view": {"type": "string"}, "kind": {"type": "string"}, "limit": {"type": "integer"}}, ["view"]), td_geometry),
        Tool("td_dim", "A dimension on a view, measured by TechDraw so it follows the part: type distance | horizontal | vertical | radius | diameter | angle; refs [\"Vertex1\", \"Vertex4\"] or by the part: { point: [x,y,z] }, { circle: { diameter, near } }, { edge: { through: [p, q] } }; at [dx, dy] (its text from the view's centre, page mm); tolerance at once: plus/minus, fit \"H7\", basic, label, format \"%.1f\".",
             obj({"view": {"type": "string"}, "type": {"type": "string"}, "refs": {"type": "array", "items": ref_schema}, "at": {"type": "array"}, "plus": {"type": "number"}, "minus": {"type": "number"}, "fit": {"type": "string"},
                  "basic": {"type": "boolean"}, "label": {"type": "string"}, "format": {"type": "string"}}, ["view", "refs"]), td_dim, changes=True),
        Tool("td_tolerance", "Tolerance a dimension: plus/minus (mm; equal = ±), fit \"H7\" | \"g6\" (ISO 286 deviations for its measured size), basic: true (a boxed theoretically exact size, for GD&T), label (text instead of the value), format \"%.2f\"; clear: true first resets it.",
             obj({"dimension": {"type": "string"}, "plus": {"type": "number"}, "minus": {"type": "number"}, "fit": {"type": "string"}, "basic": {"type": "boolean"}, "label": {"type": "string"}, "format": {"type": "string"}, "clear": {"type": "boolean"}}, ["dimension"]), td_tolerance, changes=True),
        Tool("td_gdt", "A feature control frame (ISO 1101 / ASME Y14.5): frame [{ characteristic (flatness, straightness, circularity, cylindricity, profile_line, profile_surface, parallelism, perpendicularity, angularity, position, concentricity, symmetry, circular_runout, total_runout), tolerance mm, diameter (⌀ zone), modifier M|L|…, datums [\"A\", \"B|M\"] }] (rows stack: composite); with a leader to on (a view ref) or under dimension; at.",
             obj({"view": {"type": "string"}, "on": ref_schema, "dimension": {"type": "string"}, "frame": {"type": "array", "items": {"type": "object"}}, "characteristic": {"type": "string"}, "tolerance": {"type": "number"}, "diameter": {"type": "boolean"},
                  "modifier": {"type": "string"}, "datums": {"type": "array", "items": {"type": "string"}}, "at": {"type": "array"}, "height": {"type": "number"}}), td_gdt, changes=True),
        Tool("td_datum", "A datum feature symbol: letter, on the feature (\"Edge3\" or { edge: { through } }, { point }), at.",
             obj({"view": {"type": "string"}, "letter": {"type": "string"}, "on": ref_schema, "at": {"type": "array"}, "height": {"type": "number"}}, ["view", "letter", "on"]), td_datum, changes=True),
        Tool("td_note", "A note on the page: text (\\n splits lines), size (mm), at.", obj({"page": {"type": "string"}, "text": {"type": "string"}, "size": {"type": "number"}, "at": {"type": "array"}}, ["text"]), td_note, changes=True),
        Tool("td_balloon", "A balloon pointing at a feature of a view: on (a ref), text (default the page's next number), at [dx, dy] from the view's centre.",
             obj({"view": {"type": "string"}, "on": ref_schema, "text": {"type": "string"}, "at": {"type": "array"}}, ["view", "on"]), td_balloon, changes=True),
        Tool("td_table", "A spreadsheet's cells as a table on the page (a parts list, a revision table): sheet, range (default its used cells), size, at. It follows the sheet.",
             obj({"page": {"type": "string"}, "sheet": {"type": "string"}, "range": {"type": "string"}, "size": {"type": "number"}, "at": {"type": "array"}}), td_table, changes=True),
        Tool("td_list", "A page's views (with size and scale), dimensions (measured, text, tolerance), GD&T, other objects and title block. Changes nothing.", obj({"page": {"type": "string"}}), td_list),
        Tool("td_export", "Write a page out: formats [svg, pdf, dxf] (DXF by TechDraw; SVG and PDF drawn here from the same objects, no window needed), name. Files go to the out folder.",
             obj({"page": {"type": "string"}, "formats": {"type": "array", "items": {"type": "string"}}, "name": {"type": "string"}}), td_export),
    ]
