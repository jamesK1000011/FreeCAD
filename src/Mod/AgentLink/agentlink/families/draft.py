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
INFO = "Text and surfaces: lettering raised from or cut into a face from a TrueType font; a surface filled across edges and thickened into a solid."

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

    face_q = {"description": "The face: \"Face6\", or a query { normal, near, area … } as shape_query takes (the largest match, or the nearest with near). Default: the largest face facing up."}
    return [
        Tool("draft_text", "Letter a flat face of a body: text from a TrueType font (font: a name or a .ttf path; draft_fonts lists them), size mm (letter height), depth mm, mode emboss (raised, default) or engrave (cut in), centred on the face or at [x,y,z] on it, rotate degrees. The body becomes the lettered solid (one undo step). Warns when the letters run off the face, and when their strokes are thinner than a printed line.",
             obj({"body": {"type": "string"}, "text": {"type": "string"}, "face": face_q, "at": {"type": "array", "items": {"type": "number"}}, "size": {"type": "number"}, "depth": {"type": "number"},
                  "mode": {"type": "string", "enum": ["emboss", "engrave"]}, "font": {"type": "string"}, "rotate": {"type": "number"}, "tracking": {"type": "number"}, "name": {"type": "string"}}, ["text"]),
             draft_text, changes=True),
        Tool("draft_fonts", "The TrueType fonts on this computer (name filters), and the one draft_text takes by default.", obj({"name": {"type": "string"}}), draft_fonts),
        Tool("surface_fill", "A surface filled across a closed boundary — edges (\"Body.Edge3\") or a sketch's outline — bent as smoothly as the edges allow; thickness mm makes it a solid (negative: the other side). A cover over an opening, a smooth transition, a shell between profiles.",
             obj({"edges": {"type": "array"}, "body": {"type": "string"}, "thickness": {"type": "number"}, "name": {"type": "string"}}, ["edges"]), surface_fill, changes=True),
    ]
