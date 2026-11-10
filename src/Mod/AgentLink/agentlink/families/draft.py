# SPDX-License-Identifier: LGPL-2.1-or-later
# Text and free surfaces on parts: lettering raised from or cut into a face
# (a label on a bin, a part number, a logo's words) from a TrueType font,
# and a surface filled across a boundary of edges and thickened into a
# solid — what Draft's ShapeString and the Surface workbench's Filling do,
# by Part's own functions so they run without the window.

import glob
import math
import os

import FreeCAD
import Part

from ..protocol import Tool, ToolError
from ..tools import obj
from . import measure as M
from . import picks

GROUP = "draft"
INFO = "Text and surfaces: lettering raised from or cut into a face from a TrueType font; surfaces filled across edges, lofted through curves or fitted to points, thickened into solids."

V = FreeCAD.Vector


def fonts():
    """TrueType fonts this computer has, FreeCAD's own first."""
    roots = [os.path.join(FreeCAD.getResourceDir(), "Mod"), os.path.join(FreeCAD.getHomePath(), "Mod"),
             "/System/Library/Fonts", "/Library/Fonts", os.path.expanduser("~/Library/Fonts"),
             "/usr/share/fonts", "C:/Windows/Fonts"]
    out = []
    for r in roots:
        out += sorted(glob.glob(os.path.join(r, "**", "*.ttf"), recursive=True))[:200]
    return list(dict.fromkeys(out))


def font_file(name):
    if name and os.path.isfile(os.path.expanduser(name)):
        return os.path.expanduser(name)
    have = fonts()
    if not have:
        raise ToolError("unavailable", "No TrueType font found on this computer: give font, the path to a .ttf.")
    if name:
        hit = next((f for f in have if name.lower() in os.path.basename(f).lower()), None)
        if hit is None:
            raise ToolError("not_found", f"font {name!r}: none by that name — e.g. {', '.join(os.path.basename(f) for f in have[:8])}.")
        return hit
    pref = ("DejaVuSans.ttf", "Arial.ttf", "Helvetica", "LiberationSans-Regular", "osifont-lgpl3fe", "osifont")
    for p in pref:
        hit = next((f for f in have if p.lower() in os.path.basename(f).lower() and "italic" not in os.path.basename(f).lower()), None)
        if hit:
            return hit
    return have[0]


def text_faces(text, font, size, tracking=0.0):
    """The lettering as planar faces in XY, the baseline along X from 0."""
    chars = Part.makeWireString(text, font, size, tracking)
    faces = []
    for wires in chars:
        ws = [w if isinstance(w, Part.Wire) else Part.Wire(w) for w in wires]
        ws = [w for w in ws if w.isClosed()]
        if not ws:
            continue
        try:
            f = Part.makeFace(ws, "Part::FaceMakerBullseye")
        except Exception:
            f = Part.makeFace(ws, "Part::FaceMakerCheese")
        faces += f.Faces
    if not faces:
        raise ToolError("failed", f"The font made no closed letters of {text!r}.")
    return Part.makeCompound(faces)


def face_frame(face, point=None):
    """A frame on a planar face, as one reads it: origin the point (else the
    face's centre), Z its outward normal; on a face facing up or down X is
    world X, on a wall X runs level, left to right seen from outside."""
    if not isinstance(face.Surface, Part.Plane):
        raise ToolError("invalid_input", "face: lettering goes on a flat face.")
    c = point if point is not None else face.CenterOfMass
    u0, u1, v0, v1 = face.ParameterRange
    n = face.normalAt((u0 + u1) / 2, (v0 + v1) / 2)
    n.normalize()
    if abs(n.z) > 0.99:
        x = V(1, 0, 0) if n.z > 0 else V(-1, 0, 0)
    else:
        x = V(0, 0, 1).cross(n)
        x.normalize()
    y = n.cross(x)
    m = FreeCAD.Matrix(x.x, y.x, n.x, c.x, x.y, y.y, n.y, c.y, x.z, y.z, n.z, c.z, 0, 0, 0, 1)
    return FreeCAD.Placement(m), n


def outline_faces(path):
    """The closed outlines of an SVG or DXF file as planar faces in XY (holes
    kept as holes), and how many open curves were left out."""
    path = os.path.expanduser(path)
    if not os.path.isfile(path):
        raise ToolError("not_found", f"path: no file at {path}.")
    ext = os.path.splitext(path)[1].lower()
    keep = FreeCAD.ActiveDocument
    try:
        tmp = FreeCAD.newDocument("AgentLinkOutline", hidden=True)
    except TypeError:
        tmp = FreeCAD.newDocument("AgentLinkOutline")
    try:
        if ext == ".svg":
            import importSVG

            importSVG.insert(path, tmp.Name)
        elif ext == ".dxf":
            import Import

            try:
                Import.readDXF(path, tmp.Name)
            except Exception as e:
                raise ToolError("failed", f"FreeCAD could not read the DXF: {e} — save it as DXF R12 or 2000, or as SVG.")
        else:
            raise ToolError("invalid_input", "path: an .svg or .dxf file.")
        tmp.recompute()
        edges = []
        for o in tmp.Objects:
            sh = getattr(o, "Shape", None)
            if sh is not None and not sh.isNull():
                edges += sh.Edges
    finally:
        FreeCAD.closeDocument(tmp.Name)
        if keep is not None and keep.Name != tmp.Name:
            FreeCAD.setActiveDocument(keep.Name)
    if not edges:
        raise ToolError("failed", "The file has no curves FreeCAD reads: paths and shapes, not images or text (turn text into paths first).")
    closed, open_ = [], 0
    for group in Part.sortEdges(edges):
        try:
            w = Part.Wire(group)
        except Exception:
            open_ += 1
            continue
        if w.isClosed():
            closed.append(w)
        else:
            open_ += 1
    if not closed:
        raise ToolError("failed", f"No closed outline in the file ({open_} open curve(s)): close the paths, or trace the artwork as filled shapes.")
    # Flat on XY whatever plane the file put them on.
    bb = Part.makeCompound(closed).BoundBox
    if bb.ZLength > 1e-6:
        raise ToolError("failed", "The outlines are not flat in one plane.")
    try:
        f = Part.makeFace(closed, "Part::FaceMakerBullseye")
    except Exception:
        f = Part.makeFace(closed, "Part::FaceMakerCheese")
    f.translate(V(0, 0, -bb.ZMin))
    return f, open_


def place_on(o, face, flat, at, rotate, depth, mode):
    """Flat faces in XY, centred, turned and stood on a body's face: the
    solid to join (emboss) or cut (engrave), and its box."""
    pl, n = face_frame(face, at)
    bb = flat.BoundBox
    flat = flat.copy()
    flat.translate(V(-(bb.XMin + bb.XMax) / 2, -(bb.YMin + bb.YMax) / 2, 0))
    if rotate:
        flat.rotate(V(0, 0, 0), V(0, 0, 1), float(rotate))
    flat.transformShape(pl.toMatrix())
    solid = flat.extrude(n * (depth if mode == "emboss" else -depth))
    if mode == "engrave":
        # Started a hair above the face, so the cut opens cleanly.
        solid.translate(n * 0.01)
    return solid, flat.BoundBox


def tools(session):
    s = session

    def pick_face(o, q):
        if isinstance(q, str):
            try:
                return o.Shape.getElement(q)
            except Exception:
                raise ToolError("invalid_input", f"face: {o.Label} has no {q}.")
        query = {**q, "body": o.Label, "kind": "face"}
        query.pop("at", None)
        _, _, hits = M.query(s, query)
        if not hits:
            raise ToolError("not_found", f"face: no face of {o.Label} matches {q} — shape_query lists them.")
        if len(hits) > 1:
            near = q.get("near")
            if near is None:
                hits = sorted(hits, key=lambda h: -h[1].Area)
            else:
                p = picks.vec3(near if isinstance(near, list) else near.get("point"), "near")
                hits = sorted(hits, key=lambda h: h[1].distToShape(Part.Vertex(p))[0])
        return hits[0][1]

    def draft_text(a):
        o = s.body(a.get("body"))
        text = a.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ToolError("invalid_input", "text: the lettering.")
        size = picks.num(a.get("size", 6), "size")
        depth = picks.num(a.get("depth", 0.6), "depth")
        if size <= 0 or depth <= 0:
            raise ToolError("invalid_input", "size (letter height) and depth: mm, over 0.")
        mode = a.get("mode", "emboss")
        if mode not in ("emboss", "engrave"):
            raise ToolError("invalid_input", "mode: emboss (raised) or engrave (cut in).")
        face = pick_face(o, a.get("face") or {"normal": [0, 0, 1]})
        at = picks.vec3(a["at"], "at") if a.get("at") is not None else None
        pl, n = face_frame(face, at)
        font = font_file(a.get("font"))
        letters = text_faces(text, font, size, float(a.get("tracking", 0)))
        bb = letters.BoundBox
        # Centred on the place, turned by rotate degrees about the face's normal.
        letters.translate(V(-(bb.XMin + bb.XMax) / 2, -(bb.YMin + bb.YMax) / 2, 0))
        if a.get("rotate"):
            letters.rotate(V(0, 0, 0), V(0, 0, 1), float(a["rotate"]))
        letters = letters.copy()
        letters.transformShape(pl.toMatrix())
        lb = letters.BoundBox
        solid = letters.extrude(n * (depth if mode == "emboss" else -depth))
        if mode == "engrave":
            # Started a hair above the face, so the cut opens cleanly.
            solid.translate(n * 0.01)
        before = o.Shape.Volume
        # Letters running off the face: said, not refused — a label may wrap an edge on purpose.
        out_of_face = [p for p in (V(lb.XMin, lb.YMin, lb.ZMin), V(lb.XMax, lb.YMax, lb.ZMax)) if face.distToShape(Part.Vertex(p))[0] > 0.05]
        try:
            res = o.Shape.fuse(solid) if mode == "emboss" else o.Shape.cut(solid)
            res = res.removeSplitter()
        except Exception as e:
            raise ToolError("failed", f"The lettering could not be {'joined to' if mode == 'emboss' else 'cut into'} {o.Label}: {e}")
        if not res.isValid() or not res.Solids:
            raise ToolError("failed", "The result is not a valid solid: smaller letters, a shallower depth, or another font.")
        done = picks.bake(s, o, res, a.get("name"))
        s.doc.recompute()
        s.snapshot("draft_text")
        out = {"body": done.Label, "text": text, "mode": mode, "font": os.path.basename(font), "size": size, "depth": depth,
               "volumeChange": round(done.Shape.Volume - before, 2), "box": {"min": [round(v, 2) for v in (lb.XMin, lb.YMin, lb.ZMin)], "max": [round(v, 2) for v in (lb.XMax, lb.YMax, lb.ZMax)]},
               "step": len(s.steps)}
        if out_of_face:
            out["warning"] = "The lettering runs past the face's edge: smaller size, or shorter text."
        stroke = size * 0.12
        if stroke < 0.4:
            out["printNote"] = f"Strokes about {stroke:.2f} mm wide: under a 0.4 mm nozzle's line — print letters at least {math.ceil(0.4 / 0.12)} mm tall, or expect them to blur."
        out["view"] = "view_render { zoom: { at: the box's middle, size } } to look at it."
        return out

    def draft_outline(a):
        if not a.get("path"):
            raise ToolError("invalid_input", "path: an .svg or .dxf with closed outlines (a logo, a shape).")
        flat, open_ = outline_faces(a["path"])
        bb = flat.BoundBox
        w, h = picks.num(a["width"], "width") if a.get("width") is not None else None, picks.num(a["height"], "height") if a.get("height") is not None else None
        k = 1.0
        if w:
            k = w / bb.XLength
        elif h:
            k = h / bb.YLength
        if k != 1.0:
            flat = flat.copy()
            flat.scale(k)
        if a.get("mirror"):
            flat = flat.mirror(V(0, 0, 0), V(1, 0, 0))
        bb = flat.BoundBox
        depth = picks.num(a.get("depth", 0.6), "depth")
        if depth <= 0:
            raise ToolError("invalid_input", "depth: mm, over 0.")
        mode = a.get("mode", "emboss" if a.get("body") else "extrude")
        if mode not in ("emboss", "engrave", "extrude"):
            raise ToolError("invalid_input", "mode: emboss (raised on a face), engrave (cut in), or extrude (a part of its own).")
        out = {"outlines": len(flat.Faces), "size": [round(bb.XLength, 2), round(bb.YLength, 2)], "scale": round(k, 5), "mode": mode, "depth": depth}
        if open_:
            out["openCurvesLeftOut"] = open_
        # The narrowest stroke: what an inward offset of half a line leaves.
        try:
            thin = flat.makeOffset2D(-0.2, 0, False, False, False)
            lost = 1 - (thin.Area / flat.Area) if flat.Area else 0
            if lost > 0.5:
                out["printNote"] = "Much of the artwork is under 0.4 mm across: thin strokes will blur or vanish — scale it up (width), or thicken the artwork."
        except Exception:
            out["printNote"] = "Parts of the artwork are under 0.4 mm across (thinner than a printed line): scale it up, or expect them to blur."
        if mode == "extrude":
            solid = flat.extrude(V(0, 0, depth))
            o = s.doc.addObject("Part::Feature", (a.get("name") or "Outline").replace(" ", "_"))
            o.Label = a.get("name") or "Outline"
            o.Shape = solid
            s.doc.recompute()
            s.snapshot("draft_outline")
            out.update({"body": o.Label, "volume": round(solid.Volume, 2), "step": len(s.steps)})
            return out
        o = s.body(a.get("body"))
        face = pick_face(o, a.get("face") or {"normal": [0, 0, 1]})
        at = picks.vec3(a["at"], "at") if a.get("at") is not None else None
        solid, lb = place_on(o, face, flat, at, a.get("rotate"), depth, mode)
        before = o.Shape.Volume
        off = [p for p in (V(lb.XMin, lb.YMin, lb.ZMin), V(lb.XMax, lb.YMax, lb.ZMax)) if face.distToShape(Part.Vertex(p))[0] > 0.05]
        try:
            res = (o.Shape.fuse(solid) if mode == "emboss" else o.Shape.cut(solid)).removeSplitter()
        except Exception as e:
            raise ToolError("failed", f"The outline could not be {'joined to' if mode == 'emboss' else 'cut into'} {o.Label}: {e}")
        if not res.isValid() or not res.Solids:
            raise ToolError("failed", "The result is not a valid solid: a smaller size or a shallower depth.")
        done = picks.bake(s, o, res, a.get("name"))
        s.doc.recompute()
        s.snapshot("draft_outline")
        out.update({"body": done.Label, "volumeChange": round(done.Shape.Volume - before, 2),
                    "box": {"min": [round(v, 2) for v in (lb.XMin, lb.YMin, lb.ZMin)], "max": [round(v, 2) for v in (lb.XMax, lb.YMax, lb.ZMax)]}, "step": len(s.steps),
                    "view": "view_render { zoom: { at: the box's middle, size } } to look at it."})
        if off:
            out["warning"] = "The outline runs past the face's edge: a smaller width."
        return out

    def draft_fonts(a):
        have = fonts()
        q = (a.get("name") or "").lower()
        hit = [f for f in have if q in os.path.basename(f).lower()] if q else have
        return {"fonts": [{"name": os.path.basename(f), "path": f} for f in hit[:60]], "found": len(hit), "default": os.path.basename(font_file(None)) if have else None}

    def surface_fill(a):
        edges = []
        for i, r in enumerate(a.get("edges") or []):
            label, sub, el = picks.resolve(s, r, a.get("body"))
            if not isinstance(el, Part.Edge) and not (hasattr(el, "Edges") and el.Edges and sub is None):
                raise ToolError("invalid_input", f"edges.{i}: an edge (\"Box.Edge3\") or an object whose edges bound it (a sketch, a wire).")
            edges += [el] if isinstance(el, Part.Edge) else list(el.Edges)
        if len(edges) < 1:
            raise ToolError("invalid_input", "edges: the boundary — edges (\"Body.Edge3\") or a sketch whose closed outline bounds the surface.")
        try:
            w = Part.Wire(Part.sortEdges(edges)[0])
        except Exception:
            w = None
        if w is None or not w.isClosed():
            raise ToolError("invalid_input", "The edges do not close into one boundary: add the missing ones.")
        try:
            face = Part.makeFilledFace(w.Edges)
        except Exception as e:
            raise ToolError("failed", f"No surface fills that boundary: {e}")
        t = a.get("thickness")
        if t is not None:
            t = picks.num(t, "thickness")
            try:
                solid = face.makeOffsetShape(t, 1e-3, fill=True)
            except Exception as e:
                raise ToolError("failed", f"The surface could not be thickened by {t:g} mm: {e} — a thinner one, or the other side (negative).")
            shape = solid
        else:
            shape = face
        o = s.doc.addObject("Part::Feature", (a.get("name") or "Surface").replace(" ", "_"))
        o.Label = a.get("name") or "Surface"
        o.Shape = shape
        s.doc.recompute()
        s.snapshot("surface_fill")
        out = {"object": o.Label, "area": round(face.Area, 2), "solid": bool(shape.Solids), "step": len(s.steps)}
        if shape.Solids:
            out["volume"] = round(shape.Volume, 2)
        return out


    def _curve(r, what, body=None):
        """An edge or wire from a reference ("Body.Edge3", a sketch) or
        { points: [[x,y,z] …], closed } (a smooth curve through them)."""
        if isinstance(r, dict) and "points" in r:
            pts = [FreeCAD.Vector(*picks.vec3(p, f"{what}.points")) for p in r["points"]]
            if len(pts) < 2:
                raise ToolError("invalid_input", f"{what}.points: two or more [x, y, z].")
            closed = bool(r.get("closed"))
            if len(pts) == 2:
                return Part.Wire([Part.LineSegment(pts[0], pts[1]).toShape()])
            bs = Part.BSplineCurve()
            bs.interpolate(pts, PeriodicFlag=closed)
            return Part.Wire([bs.toShape()])
        label, sub, el = picks.resolve(s, r, body)
        edges = [el] if isinstance(el, Part.Edge) else list(getattr(el, "Edges", []) or [])
        if not edges:
            raise ToolError("invalid_input", f"{what}: an edge (\"Box.Edge3\"), a sketch or wire, or {{ points: [[x,y,z] …] }}.")
        try:
            return Part.Wire(Part.sortEdges(edges)[0])
        except Exception:
            raise ToolError("invalid_input", f"{what}: its edges do not join into one curve.")

    def _thicken(shape, t, side, what="the surface"):
        t = picks.num(t, "thickness")
        if t <= 0:
            raise ToolError("invalid_input", "thickness: mm, over 0 (side picks which way).")
        side = side or "out"
        if side not in ("out", "in", "both"):
            raise ToolError("invalid_input", "side: out (along the surface's normal), in, or both (half each way).")
        try:
            if side == "both":
                base = shape.makeOffsetShape(-t / 2.0, 1e-3)
                solid = base.makeOffsetShape(t, 1e-3, fill=True)
            else:
                solid = shape.makeOffsetShape(t if side == "out" else -t, 1e-3, fill=True)
        except Exception as e:
            raise ToolError("failed", f"{what} could not be thickened {t:g} mm: {e} — thinner, or another side (it cannot be thicker than its tightest bend is round).")
        if not solid.Solids:
            try:
                solid = Part.makeSolid(solid)
            except Exception:
                pass
        return solid, t

    def _finish(shape, a, tool, extra):
        t = a.get("thickness")
        warnings = []
        surf = shape
        if t is not None:
            shape, t = _thicken(shape, t, a.get("side"))
            if t < 0.8:
                warnings.append(f"{t:g} mm is under two printed lines (0.8 mm with a 0.4 mm nozzle): it may print with gaps or not at all.")
            if not shape.isValid():
                warnings.append("The thickened solid is not valid: body_check it; a thinner thickness or a gentler surface usually mends it.")
        o = s.doc.addObject("Part::Feature", (a.get("name") or "Surface").replace(" ", "_"))
        o.Label = a.get("name") or "Surface"
        o.Shape = shape
        s.doc.recompute()
        s.snapshot(tool)
        out = {"object": o.Label, "area": round(surf.Area, 2), "solid": bool(shape.Solids), **extra, "step": len(s.steps)}
        if shape.Solids:
            out["volume"] = round(shape.Volume, 2)
        bb = shape.BoundBox
        out["size"] = [round(bb.XLength, 2), round(bb.YLength, 2), round(bb.ZLength, 2)]
        if warnings:
            out["warnings"] = warnings
        if not shape.Solids:
            out["next"] = "thickness: mm to make it a solid to print — or surface_thicken { surface } later."
        return out

    def surface_loft(a):
        curves = a.get("curves") or []
        if len(curves) < 2:
            raise ToolError("invalid_input", "curves: two or more, in order — edges (\"Body.Edge3\"), sketches, or { points: [[x,y,z] …], closed }.")
        wires = [_curve(r, f"curves.{i}", a.get("body")) for i, r in enumerate(curves)]
        closed = [w.isClosed() for w in wires]
        if len(set(closed)) > 1:
            raise ToolError("invalid_input", f"curves: all open or all closed — curves {[i for i, c in enumerate(closed) if c]} are closed, the rest open.")
        try:
            shape = Part.makeLoft(wires, False, bool(a.get("ruled")), bool(a.get("closed")))
        except Exception as e:
            raise ToolError("failed", f"No surface goes through those curves: {e} — curves that run the same way, in order, with the same number of pieces each, loft best.")
        return _finish(shape, a, "surface_loft", {"curves": len(wires), "through": "closed curves (a tube)" if closed[0] else "open curves (a sheet)"})

    def surface_points(a):
        rows = a.get("rows")
        if not isinstance(rows, list) or len(rows) < 2 or not all(isinstance(r, list) for r in rows):
            raise ToolError("invalid_input", "rows: a grid of points, row by row — [[[x,y,z] …], [[x,y,z] …] …], two or more rows of the same length.")
        n = len(rows[0])
        if n < 2 or any(len(r) != n for r in rows):
            raise ToolError("invalid_input", f"rows: each row the same length ({n}), two or more points each.")
        pts = [[FreeCAD.Vector(*picks.vec3(p, f"rows.{i}.{j}")) for j, p in enumerate(r)] for i, r in enumerate(rows)]
        tol = float(a.get("tolerance", 0.0))
        bs = Part.BSplineSurface()
        deg = min(3, len(rows) - 1, n - 1)
        try:
            if tol > 0:
                bs.approximate(Points=pts, DegMin=deg, DegMax=deg, Tolerance=tol)
            else:
                bs.interpolate(pts)
        except Exception as e:
            raise ToolError("failed", f"No surface fits those points: {e} — rows that run the same way, without points on top of each other.")
        face = bs.toShape()
        worst = max(face.distToShape(Part.Vertex(p))[0] for r in pts for p in r)
        return _finish(face, a, "surface_points", {"grid": [len(rows), n], "fitted": "through every point" if tol <= 0 else f"within {tol:g} mm", "farthestPoint": round(worst, 4)})

    def surface_thicken(a):
        ref = a.get("surface")
        if not ref:
            raise ToolError("invalid_input", "surface: an object (\"Surface\") or a body's face (\"Body.Face3\").")
        label, sub, el = picks.resolve(s, ref, a.get("body"))
        shape = el if isinstance(el, (Part.Face, Part.Shell)) else getattr(el, "Shape", el)
        if not getattr(shape, "Faces", None):
            raise ToolError("invalid_input", f"surface: {ref} has no faces to thicken.")
        if a.get("thickness") is None:
            raise ToolError("invalid_input", "thickness: mm.")
        return _finish(shape if isinstance(shape, Part.Face) or len(shape.Faces) == 1 else Part.Shell(shape.Faces), a, "surface_thicken", {"from": ref})

    face_q = {"description": "The face: \"Face6\", or a query { normal, near, area … } as shape_query takes (the largest match, or the nearest with near). Default: the largest face facing up."}
    return [
        Tool("draft_text", "Letter a flat face of a body: text from a TrueType font (font: a name or a .ttf path; draft_fonts lists them), size mm (letter height), depth mm, mode emboss (raised, default) or engrave (cut in), centred on the face or at [x,y,z] on it, rotate degrees. The body becomes the lettered solid (one undo step). Warns when the letters run off the face, and when their strokes are thinner than a printed line.",
             obj({"body": {"type": "string"}, "text": {"type": "string"}, "face": face_q, "at": {"type": "array", "items": {"type": "number"}}, "size": {"type": "number"}, "depth": {"type": "number"},
                  "mode": {"type": "string", "enum": ["emboss", "engrave"]}, "font": {"type": "string"}, "rotate": {"type": "number"}, "tracking": {"type": "number"}, "name": {"type": "string"}}, ["text"]),
             draft_text, changes=True),
        Tool("draft_outline", "Bring a logo or shape from an SVG or DXF file (closed outlines; holes kept): scaled to width or height mm, then mode emboss (raised on a body's face, the largest facing up by default), engrave (cut in), or extrude (a part of its own, depth mm thick — a sign, a stencil, a cookie cutter's outline). face, at, rotate as draft_text; mirror flips it. Says how many open curves were left out, and when strokes are under a printed line.",
             obj({"path": {"type": "string"}, "body": {"type": "string"}, "face": face_q, "at": {"type": "array", "items": {"type": "number"}}, "width": {"type": "number"}, "height": {"type": "number"},
                  "depth": {"type": "number"}, "mode": {"type": "string", "enum": ["emboss", "engrave", "extrude"]}, "rotate": {"type": "number"}, "mirror": {"type": "boolean"}, "name": {"type": "string"}}, ["path"]),
             draft_outline, changes=True),
        Tool("draft_fonts", "The TrueType fonts on this computer (name filters), and the one draft_text takes by default.", obj({"name": {"type": "string"}}), draft_fonts),
        Tool("surface_loft", "A surface through curves in order — edges (\"Body.Edge3\"), sketches, or { points: [[x,y,z] …], closed } for a smooth curve through them: open curves make a sheet, closed ones a tube. ruled for straight lines between; thickness mm makes it a solid (side out, in or both). A curved panel, a duct, a shell for an organic shape.",
             obj({"curves": {"type": "array"}, "body": {"type": "string"}, "ruled": {"type": "boolean"}, "closed": {"type": "boolean"}, "thickness": {"type": "number"}, "side": {"type": "string", "enum": ["out", "in", "both"]}, "name": {"type": "string"}}, ["curves"]), surface_loft, changes=True),
        Tool("surface_points", "A smooth surface through a grid of points, row by row (rows: [[[x,y,z] …] …], every row the same length) — through every point, or within tolerance mm of them for a smoother one; answers how far the farthest point is. thickness makes it a solid. Points measured off a real thing, a shape from numbers.",
             obj({"rows": {"type": "array"}, "tolerance": {"type": "number"}, "thickness": {"type": "number"}, "side": {"type": "string", "enum": ["out", "in", "both"]}, "name": {"type": "string"}}, ["rows"]), surface_points, changes=True),
        Tool("surface_thicken", "Make a surface a solid: surface an object (\"Surface\") or a body's face (\"Body.Face3\"), thickness mm, side out (along its normal), in, or both. A new object; warns under two printed lines.",
             obj({"surface": {"type": "string"}, "body": {"type": "string"}, "thickness": {"type": "number"}, "side": {"type": "string", "enum": ["out", "in", "both"]}, "name": {"type": "string"}}, ["surface", "thickness"]), surface_thicken, changes=True),
        Tool("surface_fill", "A surface filled across a closed boundary — edges (\"Body.Edge3\") or a sketch's outline — bent as smoothly as the edges allow; thickness mm makes it a solid (negative: the other side). A cover over an opening, a smooth transition, a shell between profiles.",
             obj({"edges": {"type": "array"}, "body": {"type": "string"}, "thickness": {"type": "number"}, "name": {"type": "string"}}, ["edges"]), surface_fill, changes=True),
    ]
