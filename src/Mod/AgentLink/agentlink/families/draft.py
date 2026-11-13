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
from .. import facepick

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
        tmp_name = tmp.Name
        keep_name = keep.Name if keep is not None else None
        FreeCAD.closeDocument(tmp_name)
        if keep_name and keep_name != tmp_name:
            FreeCAD.setActiveDocument(keep_name)
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


def face_triangles(shape, tol):
    """One mesh of the whole part (faces meet edge to edge), face by face:
    the triangles, each wound outward as its face is in the solid, and each
    face's [start, end) among them. What texture_prepare writes and
    texture_check reads back against."""
    shape.tessellate(tol)
    tris, per_face = [], {}
    for i, f in enumerate(shape.Faces):
        pts, ft = f.tessellate(tol)
        if not ft:
            continue
        P = [(p.x, p.y, p.z) for p in pts]
        u0, u1, v0, v1 = f.ParameterRange
        n = f.normalAt((u0 + u1) / 2, (v0 + v1) / 2)
        a0, b0, c0 = (FreeCAD.Vector(*P[k]) for k in ft[0])
        flip = (b0 - a0).cross(c0 - a0).dot(n) < 0 and abs((b0 - a0).cross(c0 - a0).Length) > 1e-12
        start = len(tris)
        for t in ft:
            tri = [P[k] for k in t]
            tris.append(tri[::-1] if flip else tri)
        per_face[f"Face{i + 1}"] = [start, len(tris)]
    return tris, per_face


def read_stl(path):
    """An STL's triangles as an (n, 3, 3) array, binary or ASCII."""
    import numpy as np

    with open(path, "rb") as fh:
        data = fh.read()
    if len(data) >= 84:
        n = int.from_bytes(data[80:84], "little")
        if 84 + 50 * n == len(data):
            rec = np.frombuffer(data, dtype=[("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")], count=n, offset=84)
            return rec["v"].astype(float)
    words = data.decode("ascii", "replace").split()
    vals = []
    for k, w in enumerate(words):
        if w == "vertex":
            vals += [float(words[k + 1]), float(words[k + 2]), float(words[k + 3])]
    return np.array(vals, float).reshape(-1, 3, 3)


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

    def draft_array(a):
        o = s.body(a.get("body"))
        base = o.Shape
        kind = a.get("kind", "grid")
        places = []
        if kind == "grid":
            n = [int(x) for x in (a.get("count") or [2, 1, 1])] + [1, 1]
            n = n[:3]
            if any(k < 1 for k in n) or n[0] * n[1] * n[2] < 2:
                raise ToolError("invalid_input", "count: [nx, ny, nz], each 1 or more, two copies or more in all.")
            bb = base.BoundBox
            gap = picks.num(a.get("gap", 2), "gap")
            step = a.get("spacing")
            step = picks.vec3(step, "spacing") if step is not None else V(bb.XLength + gap, bb.YLength + gap, bb.ZLength + gap)
            for i in range(n[0]):
                for j in range(n[1]):
                    for k in range(n[2]):
                        places.append(FreeCAD.Placement(V(i * step.x, j * step.y, k * step.z), FreeCAD.Rotation()))
        elif kind == "polar":
            count = int(a.get("count", 6))
            if count < 2:
                raise ToolError("invalid_input", "count: 2 or more copies round the circle.")
            angle = float(a.get("angle", 360))
            centre = picks.vec3(a["center"], "center") if a.get("center") is not None else V(0, 0, 0)
            axis = picks.vec3(a["axis"], "axis") if a.get("axis") is not None else V(0, 0, 1)
            each = angle / count if abs(angle) >= 360 - 1e-9 else angle / (count - 1)
            for i in range(count):
                r = FreeCAD.Rotation(axis, each * i)
                places.append(FreeCAD.Placement(centre - r.multVec(centre), r))
        elif kind == "path":
            ref = a.get("path")
            if not ref:
                raise ToolError("invalid_input", "path: an edge (\"Body.Edge3\"), a sketch or wire, or { points: [[x,y,z] …] }.")
            w = _curve(ref, "path", a.get("bodyOfPath"))
            count = int(a.get("count", 5))
            if count < 2:
                raise ToolError("invalid_input", "count: 2 or more copies along the path.")
            pts = w.discretize(Number=count if not w.isClosed() else count + 1)[:count]
            start = pts[0]
            align = bool(a.get("align"))
            e0 = w.Edges[0]
            t0 = e0.tangentAt(e0.FirstParameter)
            for p in pts:
                if align:
                    best = min(w.Edges, key=lambda e: e.distToShape(Part.Vertex(p))[0])
                    t = best.tangentAt(best.Curve.parameter(p))
                    rot = FreeCAD.Rotation(t0, t)
                    places.append(FreeCAD.Placement(p - rot.multVec(start), rot))
                else:
                    places.append(FreeCAD.Placement(p - start, FreeCAD.Rotation()))
        else:
            raise ToolError("invalid_input", "kind: grid (count [nx, ny, nz], gap or spacing), polar (count, angle, center, axis) or path (path, count, align).")
        copies = []
        for pl in places:
            c = base.copy()
            c.transformShape(pl.toMatrix())
            copies.append(c)
        out = {"kind": kind, "copies": len(copies)}
        if a.get("separate"):
            made = []
            for i, c in enumerate(copies[1:], 2):
                n = s.doc.addObject("Part::Feature", f"{o.Name}_{i}")
                n.Label = f"{o.Label} {i}"
                n.Shape = c
                made.append(n.Label)
            s.doc.recompute()
            s.snapshot("draft_array")
            out.update({"bodies": [o.Label] + made, "step": len(s.steps)})
            return out
        try:
            res = copies[0].fuse(copies[1:]).removeSplitter() if len(copies) > 1 else copies[0]
        except Exception as e:
            raise ToolError("failed", f"The copies could not be joined: {e} — separate: true keeps them apart.")
        apart = len(res.Solids) > 1
        if apart and not a.get("allowApart"):
            out["note"] = f"The copies do not touch: one body of {len(res.Solids)} separate solids. separate: true makes each its own body (to print or move apart)."
        done = picks.bake(s, o, res, a.get("name"))
        s.doc.recompute()
        s.snapshot("draft_array")
        bb = done.Shape.BoundBox
        out.update({"body": done.Label, "solids": len(res.Solids), "volume": round(done.Shape.Volume, 2), "size": [round(bb.XLength, 2), round(bb.YLength, 2), round(bb.ZLength, 2)], "step": len(s.steps)})
        return out

    def texture_prepare(a):
        """The part for BumpMesh: an STL written face by face from one mesh of
        the whole part (shared edges meet exactly), and the triangles of the
        faces asked as a mask file the texture tool reads."""
        import json
        import struct

        o = s.body(a.get("body"))
        shape = o.Shape
        mode = a.get("mode", "include")
        if mode not in ("include", "exclude"):
            raise ToolError("invalid_input", "mode: include (texture only these faces) or exclude (texture all but these).")
        q = a.get("faces")
        want = set()
        for k, item in enumerate(q if isinstance(q, list) else [q]):
            item = {"faces": [item]} if isinstance(item, str) else item
            want |= {n for n, _ in facepick.pick(shape, item, f"faces.{k}" if isinstance(q, list) else "faces")}
        tol = picks.num(a.get("tolerance", 0.02), "tolerance")
        tris, per_face = face_triangles(shape, tol)
        mask = [k for name in sorted(want) if name in per_face for k in range(*per_face[name])]
        stem = (a.get("name") or o.Label).replace(" ", "_")
        stl = a.get("path") or os.path.join(s.out_dir, stem + "-for-texture.stl")
        with open(stl, "wb") as fh:
            fh.write(b"Agent Link texture_prepare".ljust(80, b" "))
            fh.write(struct.pack("<I", len(tris)))
            for t in tris:
                p0, p1, p2 = (V(*q) for q in t)
                nn = (p1 - p0).cross(p2 - p0)
                if nn.Length > 0:
                    nn.normalize()
                fh.write(struct.pack("<12fH", nn.x, nn.y, nn.z, *t[0], *t[1], *t[2], 0))
        mask_path = os.path.splitext(stl)[0] + "-mask.json"
        with open(mask_path, "w", encoding="utf-8") as fh:
            json.dump({"triangles": mask, "stlTriangles": len(tris), "mode": mode, "faces": sorted(want), "body": o.Label}, fh)
        out_path = os.path.splitext(stl)[0].replace("-for-texture", "") + "-textured.stl"
        bb = shape.BoundBox
        return {
            "body": o.Label, "stl": stl, "triangles": len(tris), "mask": mask_path, "maskTriangles": len(mask), "mode": mode, "faces": sorted(want),
            "next": {"tool": "bumpmesh_texture_file", "arguments": {"path": stl, "output": out_path, "layers": [{"texture": "a library name (bumpmesh_list_textures)", "settings": {"textureHeight": 0.5, "scaleU": 5},
                     "mask": {"mode": mode, "trianglesFile": mask_path}}], "settings": {"refineLength": round(max(0.05, min(5, bb.DiagonalLength / 250)), 2)}}},
            "note": f"{'Only' if mode == 'include' else 'All but'} {', '.join(sorted(want))} get the texture; say the faces that must not change back to the person first. Then texture_check {{ body: \"{o.Label}\", path: <the output> }}: the depth on each textured face, any face it leaked onto, and whether the mesh is closed.",
        }

    def texture_check(a):
        """The textured STL measured against the body face by face: how deep
        the texture stands on the faces it was for, whether it leaked onto
        any other, and whether the mesh stayed closed."""
        import json

        import numpy as np

        from .. import meshdist

        o = s.body(a.get("body"))
        path = a.get("path")
        if not path or not os.path.isfile(path):
            raise ToolError("not_found", f"path {path!r}: the textured STL bumpmesh_texture_file wrote.")
        mask_path = a.get("mask") or os.path.splitext(path)[0].replace("-textured", "") + "-for-texture-mask.json"
        if not os.path.isfile(mask_path):
            raise ToolError("not_found", f"mask {mask_path!r}: the -mask.json texture_prepare wrote (give mask).")
        with open(mask_path, encoding="utf-8") as fh:
            m = json.load(fh)
        tol = picks.num(a.get("tolerance", 0.02), "tolerance")
        tris, per_face = face_triangles(o.Shape, tol)
        if len(tris) != m.get("stlTriangles"):
            raise ToolError("invalid_state", f"{o.Label} tessellates to {len(tris)} triangles, the mask was made from {m.get('stlTriangles')}: the body changed since texture_prepare, or a different tolerance — give the one texture_prepare used.")
        T = np.array(tris, float)
        Vb = T.reshape(-1, 3)
        Fb = np.arange(len(Vb)).reshape(-1, 3)
        face_of = np.empty(len(T), dtype=object)
        names = sorted(per_face, key=lambda k: per_face[k][0])
        for name in names:
            face_of[per_face[name][0]:per_face[name][1]] = name
        listed = set(m.get("faces") or [])
        textured = listed if m.get("mode", "include") == "include" else set(names) - listed
        is_tex = np.array([f in textured for f in face_of])
        Tx = read_stl(path)
        if not len(Tx):
            raise ToolError("failed", f"{path}: no triangles.")
        P, inv = np.unique(np.round(Tx.reshape(-1, 3), 5), axis=0, return_inverse=True)
        inv = inv.reshape(-1)
        Ft = inv.reshape(-1, 3)
        bb = o.Shape.BoundBox
        reach = float(a.get("reach") or max(2.0, 0.02 * bb.DiagonalLength))
        best, dist, signed = meshdist.nearest(P, Vb, Fb, reach)
        found = best >= 0
        flat = picks.num(a.get("flat", 0.01), "flat")
        near_tex = np.zeros(len(P), bool)
        if is_tex.any():
            onto = found & is_tex[np.maximum(best, 0)]
            depth = float(np.percentile(np.abs(signed[onto]), 99)) if onto.any() else 0.0
            # A displaced point by a corner can lie nearer the next face: it is
            # the texture's while it is within its depth of a textured face.
            sub = np.nonzero(is_tex)[0]
            b2, d2, _s2 = meshdist.nearest(P, Vb, Fb[sub], depth + flat)
            near_tex = b2 >= 0
        else:
            depth = 0.0
        faces = {}
        for name in names:
            sel = found & (face_of[np.maximum(best, 0)] == name)
            if not sel.any():
                continue
            d = signed[sel]
            moved = np.abs(d) > flat
            row = {"points": int(sel.sum()), "moved": round(float(moved.mean()), 3)}
            if moved.any():
                row.update(mean=round(float(d.mean()), 3), p95=round(float(np.percentile(np.abs(d), 95)), 3), out=round(float(d.max()), 3), into=round(float(d.min()), 3))
            faces[name] = row
        tex_rows = {k: v for k, v in faces.items() if k in textured}
        leak = found & ~is_tex[np.maximum(best, 0)] & (np.abs(signed) > flat) & ~near_tex
        leaked = sorted({face_of[b] for b in best[leak]})
        # The mesh closed: every edge on two triangles.
        e = np.sort(np.concatenate([Ft[:, [0, 1]], Ft[:, [1, 2]], Ft[:, [2, 0]]]), axis=1)
        _u, counts = np.unique(e, axis=0, return_counts=True)
        open_edges = int((counts == 1).sum())
        bad_edges = int((counts > 2).sum())
        bare = [k for k in textured if k in per_face and faces.get(k, {}).get("moved", 0) < 0.2]
        ok = not leaked and not bare and not open_edges and not bad_edges and int((~found).sum()) == 0
        out = {"body": o.Label, "file": path, "verdict": "ok" if ok else "check", "triangles": int(len(Tx)), "points": int(len(P)),
               "texturedFaces": tex_rows, "depth": round(depth, 3)}
        if leaked:
            out["leaked"] = {"faces": leaked, "points": int(leak.sum()), "worst": round(float(np.abs(signed[leak]).max()), 3)}
        if bare:
            out["bare"] = sorted(bare)
        if (~found).any():
            out["far"] = {"points": int((~found).sum()), "reach": round(reach, 3), "note": "Farther than reach from the body: a texture deeper than reach (give reach), or the STL is not of this body."}
        if open_edges or bad_edges:
            out["mesh"] = {"openEdges": open_edges, "overSharedEdges": bad_edges, "note": "Not closed: a slicer may refuse it or fill it wrong — repair before printing."}
        notes = []
        if leaked:
            notes.append(f"The texture reached {', '.join(leaked)}, which were to stay plain: check the mask (mode, faces) and run the texture again.")
        if bare:
            notes.append(f"{', '.join(sorted(bare))} were to be textured but under a fifth of their points moved: a texture too fine for refineLength, or a mask that missed them.")
        if ok:
            notes.append(f"Textured on {', '.join(sorted(tex_rows))} only, {depth:.3g} mm deep at most; the mesh is closed.")
        out["note"] = " ".join(notes)
        return out

    def draft_fonts(a):
        have = fonts()
        q = (a.get("name") or "").lower()
        hit = [f for f in have if q in os.path.basename(f).lower()] if q else have
        # Names only (font takes a name), 25 at a time: 60 names with full
        # paths were ~1,700 tokens. name: filters; paths: true adds them.
        lim = int(a.get("limit", 25))
        out = {"fonts": [os.path.basename(f) for f in hit[:lim]], "found": len(hit), "default": os.path.basename(font_file(None)) if have else None}
        if a.get("paths"):
            out["paths"] = hit[:lim]
        if len(hit) > lim:
            out["more"] = f"{len(hit) - lim} more: name filters (\"Sans\"), limit raises it."
        return out

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
        Tool("draft_array", "Copies of a whole body: kind grid (count [nx, ny, nz]; gap mm between them, 2, or spacing [dx, dy, dz]), polar (count round center about axis, angle degrees, 360), or path (count along a curve — an edge, a sketch or { points } — align to turn each with it). Joined into the body by default (one undo step); separate: true makes each copy its own body instead — a batch to print. For a feature repeated inside one part, pd_linear_pattern or pd_polar_pattern.",
             obj({"body": {"type": "string"}, "kind": {"type": "string", "enum": ["grid", "polar", "path"]}, "count": {}, "gap": {"type": "number"}, "spacing": {"type": "array", "items": {"type": "number"}},
                  "angle": {"type": "number"}, "center": {"type": "array", "items": {"type": "number"}}, "axis": {"type": "array", "items": {"type": "number"}}, "path": {}, "bodyOfPath": {"type": "string"},
                  "align": {"type": "boolean"}, "separate": {"type": "boolean"}, "name": {"type": "string"}}), draft_array, changes=True),
        Tool("texture_prepare", "A body made ready for a surface texture by BumpMesh: an STL written face by face from one mesh of the whole part, and a mask file of the triangles of faces (names or queries) — mode include (texture only them: a grip) or exclude (all but them: keep a mating face plain). Answers the bumpmesh_texture_file call to make with it, then how to check the result against the body.",
             obj({"body": {"type": "string"}, "faces": {}, "mode": {"type": "string", "enum": ["include", "exclude"]}, "tolerance": {"type": "number"}, "path": {"type": "string"}, "name": {"type": "string"}}, ["faces"]),
             texture_prepare),
        Tool("texture_check", "Check a textured STL against the body it was made from, face by face: on each face the texture was for, the share of points moved and how deep (p95, out, into); leaked — faces meant to stay plain that moved (a mating face, a datum); bare — faces meant for texture that barely moved; and whether the mesh is closed. path: the textured STL; mask: texture_prepare's -mask.json (found beside it by name); tolerance as texture_prepare used. verdict ok or check. Changes nothing.",
             obj({"body": {"type": "string"}, "path": {"type": "string"}, "mask": {"type": "string"}, "tolerance": {"type": "number"}, "flat": {"type": "number", "description": "mm a point may move and count as unmoved (0.01)."}, "reach": {"type": "number"}}, ["path"]),
             texture_check),
        Tool("draft_fonts", "The TrueType fonts on this computer by name (name filters; limit, 25; paths: true adds their paths), and the one draft_text takes by default.", obj({"name": {"type": "string"}, "limit": {"type": "integer"}, "paths": {"type": "boolean"}}), draft_fonts),
        Tool("surface_loft", "A surface through curves in order — edges (\"Body.Edge3\"), sketches, or { points: [[x,y,z] …], closed } for a smooth curve through them: open curves make a sheet, closed ones a tube. ruled for straight lines between; thickness mm makes it a solid (side out, in or both). A curved panel, a duct, a shell for an organic shape.",
             obj({"curves": {"type": "array", "items": {}}, "body": {"type": "string"}, "ruled": {"type": "boolean"}, "closed": {"type": "boolean"}, "thickness": {"type": "number"}, "side": {"type": "string", "enum": ["out", "in", "both"]}, "name": {"type": "string"}}, ["curves"]), surface_loft, changes=True),
        Tool("surface_points", "A smooth surface through a grid of points, row by row (rows: [[[x,y,z] …] …], every row the same length) — through every point, or within tolerance mm of them for a smoother one; answers how far the farthest point is. thickness makes it a solid. Points measured off a real thing, a shape from numbers.",
             obj({"rows": {"type": "array", "items": {}}, "tolerance": {"type": "number"}, "thickness": {"type": "number"}, "side": {"type": "string", "enum": ["out", "in", "both"]}, "name": {"type": "string"}}, ["rows"]), surface_points, changes=True),
        Tool("surface_thicken", "Make a surface a solid: surface an object (\"Surface\") or a body's face (\"Body.Face3\"), thickness mm, side out (along its normal), in, or both. A new object; warns under two printed lines.",
             obj({"surface": {"type": "string"}, "body": {"type": "string"}, "thickness": {"type": "number"}, "side": {"type": "string", "enum": ["out", "in", "both"]}, "name": {"type": "string"}}, ["surface", "thickness"]), surface_thicken, changes=True),
        Tool("surface_fill", "A surface filled across a closed boundary — edges (\"Body.Edge3\") or a sketch's outline — bent as smoothly as the edges allow; thickness mm makes it a solid (negative: the other side). A cover over an opening, a smooth transition, a shell between profiles.",
             obj({"edges": {"type": "array", "items": {}}, "body": {"type": "string"}, "thickness": {"type": "number"}, "name": {"type": "string"}}, ["edges"]), surface_fill, changes=True),
    ]
