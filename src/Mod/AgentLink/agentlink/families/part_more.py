# SPDX-License-Identifier: LGPL-2.1-or-later
# The rest of the Part workbench headless: section curves where bodies or a
# plane meet a body, 2D offsets of wires and flat faces, elements copied out
# as bodies of their own (and shells and solids built from faces), faces
# defeatured away, shapes reversed or closed into solids, and datums (point,
# line, plane, coordinate system) to attach and measure from.

import FreeCAD
import Part

from ..protocol import Tool, ToolError
from ..tools import obj
from . import picks

GROUP = "model"
INFO = "Part extras: sections, 2D offsets, elements copied out, shells/solids from faces, defeaturing, reverse/solid fixes, datums."

V = FreeCAD.Vector


def bad(msg):
    return ToolError("invalid_input", msg)


def doc(s):
    return s.doc


def plane_face(spec, size):
    n = picks.direction(spec.get("normal", [0, 0, 1]), "plane.normal")
    at = picks.vec3(spec.get("at", [0, 0, 0]), "plane.at")
    pl = Part.makePlane(size * 2, size * 2, V(), n)
    c = pl.BoundBox.Center
    pl.translate(at - c - n * n.dot(at - c))
    return pl


def shape_stats(sh):
    out = {"edges": len(sh.Edges), "faces": len(sh.Faces), "solids": len(sh.Solids)}
    if sh.Edges:
        out["length_mm"] = round(sum(e.Length for e in sh.Edges), 3)
    if sh.Faces:
        out["area_mm2"] = round(sh.Area, 3)
    if sh.Solids:
        out["volume_mm3"] = round(sh.Volume, 3)
    loops = 0
    try:
        for es in Part.sortEdges(list(sh.Edges)):
            if Part.Wire(es).isClosed():
                loops += 1
    except Exception:
        loops = sum(1 for w in sh.Wires if w.isClosed())
    out["closedLoops"] = loops
    return out


def add(s, shape, label):
    o = doc(s).addObject("Part::Feature", "Shape")
    o.Shape = shape
    o.Label = s.unique(label)
    doc(s).recompute()
    return o


def elements(s, refs, what):
    if isinstance(refs, str):
        refs = [refs]
    if not isinstance(refs, list) or not refs:
        raise bad(f"{what}: [\"Box.Face3\", …] or whole objects by name.")
    out = []
    for r in refs:
        label, sub, sh = picks.resolve(s, r)
        if sub is None:
            out += list(sh.Faces) if what == "faces" else [sh]
        else:
            out.append(sh)
    return out


def tools(s):
    def section(a):
        b = picks.shaped(doc(s), a.get("body"), "body")
        size = b.Shape.BoundBox.DiagonalLength
        if a.get("plane") is not None:
            other = plane_face(a["plane"], size)
            tool_o = None
        elif a.get("with"):
            tool_o = picks.shaped(doc(s), a["with"], "with")
            other = tool_o.Shape
        else:
            raise bad("with (a body) or plane { normal, at }.")
        if tool_o is not None and a.get("parametric", True):
            o = doc(s).addObject("Part::Section", "Section")
            o.Base, o.Tool = b, tool_o
            o.Approximation = bool(a.get("approximate", False))
            o.Label = s.unique(a.get("name") or f"{b.Label} section")
            doc(s).recompute()
            sh = o.Shape
        else:
            sh = b.Shape.section(other)
            if not sh.Edges:
                raise ToolError("invalid_state", f"The plane misses {b.Label}: no section.")
            o = add(s, sh, a.get("name") or f"{b.Label} section")
        if not sh.Edges:
            raise ToolError("invalid_state", f"{b.Label} and {a.get('with')} do not meet: no section curves.")
        out = {"section": o.Label, **shape_stats(sh)}
        if a.get("face") and sh.Edges:
            try:
                wires = [Part.Wire(es) for es in Part.sortEdges(sh.Edges)]
                closed = [w for w in wires if w.isClosed()]
                f = Part.makeFace(closed, "Part::FaceMakerBullseye")
                o.Shape = f if o.TypeId == "Part::Feature" else o.Shape
                if o.TypeId != "Part::Feature":
                    o2 = add(s, f, f"{o.Label} face")
                    out["face"] = o2.Label
                out["area_mm2"] = round(f.Area, 3)
            except Exception as e:
                out["faceNote"] = f"no face from the curves: {e}"
        s.snapshot("part_section")
        return out

    def offset2d(a):
        src = picks.shaped(doc(s), a.get("shape"), "shape")
        if a.get("distance") is None:
            raise bad("distance: mm (+ out, - in).")
        d = float(a["distance"])
        sh = src.Shape
        if not (sh.Wires or sh.Faces):
            raise ToolError("invalid_state", f"{src.Label}: a wire, sketch or flat face to offset.")
        join = {"arc": 0, "tangent": 1, "intersection": 2}.get(a.get("join", "arc"))
        if join is None:
            raise bad("join: arc | tangent | intersection.")
        loops = [w for w in sh.Wires if w.isClosed()] if not sh.Faces else []
        if loops and len(loops) == len(sh.Wires):
            # FreeCAD's Offset2D of a bare closed wire goes astray: offset the
            # face it bounds and take its outline (the band with fill)
            try:
                face = Part.makeFace(loops, "Part::FaceMakerBullseye")
                grown = face.makeOffset2D(d, join, False, False, bool(a.get("intersection", False)))
            except Exception as e:
                raise ToolError("invalid_state", f"No offset of {src.Label} by {d} mm: {e} — a loop shrunk past its own width vanishes.")
            if a.get("fill"):
                res = grown.cut(face) if d > 0 else face.cut(grown)
            else:
                res = grown.Faces[0].OuterWire if len(grown.Faces) == 1 else Part.makeCompound([f.OuterWire for f in grown.Faces])
            o = add(s, res, a.get("name") or f"{src.Label} offset")
            s.snapshot("part_offset2d")
            return {"offset": o.Label, **shape_stats(res), "note": "a plain shape (a closed wire's offset is taken through its face)"}
        o = doc(s).addObject("Part::Offset2D", "Offset2D")
        o.Source = src
        o.Value = d
        o.Join = join
        o.Fill = bool(a.get("fill", False))
        o.Intersection = bool(a.get("intersection", False))
        o.Label = s.unique(a.get("name") or f"{src.Label} offset")
        doc(s).recompute()
        if o.Shape.isNull() or not (o.Shape.Edges):
            msg = "; ".join(str(x) for x in (o.getStatusString(),)) if hasattr(o, "getStatusString") else ""
            doc(s).removeObject(o.Name)
            raise ToolError("invalid_state", f"No offset of {src.Label} by {d} mm ({msg}) — a wire shrunk past its own width vanishes; a non-flat wire cannot be offset in 2D.")
        if not a.get("parametric", True):
            sh2 = o.Shape.copy()
            doc(s).removeObject(o.Name)
            o = add(s, sh2, a.get("name") or f"{src.Label} offset")
        s.snapshot("part_offset2d")
        return {"offset": o.Label, **shape_stats(o.Shape)}

    def extract(a):
        build = a.get("build", "copy")
        parts = elements(s, a.get("refs"), "faces" if build in ("shell", "solid") else "refs")
        if build == "copy":
            sh = parts[0].copy() if len(parts) == 1 else Part.makeCompound([p.copy() for p in parts])
        elif build in ("shell", "solid"):
            faces = [f for p in parts for f in p.Faces]
            if not faces:
                raise bad("refs: faces for a shell or solid.")
            sh = Part.makeShell(faces)
            tol = float(a.get("tolerance", 0.01))
            if not sh.isClosed():
                sew = Part.Shape(sh)
                sew.sewShape() if hasattr(sew, "sewShape") else None
                sh = sew if sew.Shells else sh
            if build == "solid":
                shells = sh.Shells or [sh]
                if not shells[0].isClosed():
                    raise ToolError("invalid_state", f"The {len(faces)} faces leave a gap: a solid needs a closed shell (tolerance {tol} mm did not close it).")
                sh = Part.makeSolid(shells[0])
                if sh.Volume < 0:
                    sh.reverse()
        elif build == "wire":
            edges = [e for p in parts for e in p.Edges]
            ws = [Part.Wire(es) for es in Part.sortEdges(edges)]
            sh = ws[0] if len(ws) == 1 else Part.makeCompound(ws)
        elif build == "face":
            edges = [e for p in parts for e in p.Edges]
            ws = [Part.Wire(es) for es in Part.sortEdges(edges)]
            if not all(w.isClosed() for w in ws):
                raise ToolError("invalid_state", "The edges are not closed loops: no face.")
            sh = Part.makeFace(ws, "Part::FaceMakerBullseye")
        else:
            raise bad("build: copy | wire | face | shell | solid.")
        o = add(s, sh, a.get("name") or ("Copy" if build == "copy" else build.capitalize()))
        s.snapshot("part_extract")
        return {"shape": o.Label, "build": build, **shape_stats(sh), "valid": bool(sh.isValid())}

    def defeature(a):
        b = picks.solid_body(s, a.get("body"))
        faces = a.get("faces")
        if not faces:
            raise bad("faces: [\"Face12\", …] of the body — the bosses, holes, fillets to take away.")
        subs = []
        for f in faces:
            head, sub = picks.split_ref(f)
            if sub is None or not sub.startswith("Face"):
                raise bad(f"{f}: a face reference.")
            subs.append(picks.element(b.Shape, sub, b.Label))
        before = b.Shape
        try:
            res = before.defeaturing(subs)
        except Exception as e:
            raise ToolError("invalid_state", f"Defeaturing refused: {e} — pick every face of the feature (a hole's wall, a fillet's band).")
        if res.isNull() or not res.Solids or res.isSame(before):
            raise ToolError("invalid_state", "Defeaturing left the body as it was: pick all faces of one feature together.")
        out = picks.bake(s, b, res, a.get("name"))
        s.snapshot("part_defeature")
        return {"body": out.Label, "facesBefore": len(before.Faces), "facesAfter": len(res.Faces),
                "volumeBefore_mm3": round(before.Volume, 3), "volume_mm3": round(res.Volume, 3)}

    def fix(a):
        b = picks.shaped(doc(s), a.get("body"), "body")
        op = a.get("op")
        sh = b.Shape.copy()
        notes = []
        if op == "reverse":
            sh.reverse()
            if sh.Solids and sh.Volume < 0:
                notes.append("a solid turned inside out has a negative volume — reverse it back if that was not the aim")
        elif op == "solid":
            shells = sh.Shells
            if not shells:
                try:
                    shells = [Part.makeShell(sh.Faces)]
                except Exception:
                    shells = []
            closed = [x for x in shells if x.isClosed()]
            if not closed:
                raise ToolError("invalid_state", f"{b.Label} has no closed shell: sew it first (fix op sew) or close the gaps.")
            solids = [Part.makeSolid(x) for x in closed]
            for x in solids:
                if x.Volume < 0:
                    x.reverse()
            sh = solids[0] if len(solids) == 1 else Part.makeCompound(solids)
        elif op == "sew":
            tol = float(a.get("tolerance", 0.01))
            sh.sewShape() if hasattr(sh, "sewShape") else None
            try:
                sh = sh.copy()
                sh.fix(tol, tol, tol)
            except Exception:
                pass
            notes.append(f"sewn at {tol} mm")
        elif op == "fix":
            tol = float(a.get("tolerance", 0.001))
            sh.fix(tol, tol, tol)
        else:
            raise bad("op: reverse | solid | sew | fix.")
        if op == "reverse":
            if b.TypeId == "Part::Feature" and not a.get("name"):
                b.Shape = sh
                o = b
            else:
                o = add(s, sh, a.get("name") or f"{b.Label} reversed")
            doc(s).recompute()
        else:
            o = picks.bake(s, b, sh, a.get("name"))
        sh = o.Shape
        s.snapshot("part_fix")
        out = {"body": o.Label, "op": op, **shape_stats(sh), "valid": bool(sh.isValid()), "closed": bool(sh.Shells and all(x.isClosed() for x in sh.Shells))}
        if notes:
            out["notes"] = notes
        return out

    def datum(a):
        kind = a.get("kind", "plane")
        types = {"point": ["Part::DatumPoint", "PartDesign::Point"], "line": ["Part::DatumLine", "PartDesign::Line"],
                 "plane": ["Part::DatumPlane", "PartDesign::Plane"],
                 "csys": ["Part::LocalCoordinateSystem", "PartDesign::CoordinateSystem"]}
        if kind not in types:
            raise bad("kind: point | line | plane | csys.")
        pl = picks.placement(a)
        if a.get("normal") is not None and kind in ("plane", "line"):
            n = picks.direction(a["normal"], "normal")
            pl.Rotation = FreeCAD.Rotation(V(0, 0, 1), n)
        o = None
        tried = []
        for t in types[kind]:
            try:
                o = doc(s).addObject(t, kind.capitalize())
                break
            except Exception as e:
                tried.append(f"{t}: {e}")
                continue
        made = "datum"
        if o is None:
            # an older FreeCAD: a plain shape stands in
            made = "shape"
            if kind == "point":
                sh = Part.Vertex(V())
            elif kind == "line":
                sh = Part.makeLine(V(0, 0, -50), V(0, 0, 50))
            elif kind == "plane":
                sh = Part.makePlane(100, 100, V(-50, -50, 0))
            else:
                sh = Part.makeCompound([Part.makeLine(V(), V(20, 0, 0)), Part.makeLine(V(), V(0, 20, 0)), Part.makeLine(V(), V(0, 0, 20))])
            o = doc(s).addObject("Part::FeaturePython", kind.capitalize())
            try:
                o.addExtension("Part::AttachExtensionPython")
            except Exception as e:
                tried.append(f"attach: {e}")
            o.Shape = sh
        o.Placement = pl
        if a.get("to") and not (hasattr(o, "AttachmentSupport") or hasattr(o, "Support")):
            raise ToolError("invalid_state", "This FreeCAD cannot attach a datum here: " + "; ".join(tried))
        if a.get("to"):
            label, sub, _ = picks.resolve(s, a["to"])
            target = picks.find(doc(s), label)
            prop = "AttachmentSupport" if hasattr(o, "AttachmentSupport") else "Support"
            setattr(o, prop, [(target, sub or "")])
            mode = a.get("mode")
            if not mode or mode == "Deactivated":
                elt = (sub or "")[:4]
                mode = {("point", "Vert"): "Vertex", ("point", "Edge"): "CenterOfCurvature", ("point", "Face"): "CenterOfMass",
                        ("line", "Edge"): "TwoPointLine", ("line", "Face"): "NormalToSurface", ("line", "Vert"): "ObjectZ",
                        ("plane", "Face"): "FlatFace", ("plane", "Edge"): "NormalToEdge", ("plane", "Vert"): "Translate",
                        ("csys", "Face"): "FlatFace", ("csys", "Edge"): "NormalToEdge", ("csys", "Vert"): "Translate"}.get((kind, elt), "ObjectXY")
            try:
                o.MapMode = mode
            except Exception:
                raise bad(f"mode {mode!r}: not a mode for a {kind} datum — one of {', '.join(o.getEnumerationsOfProperty('MapMode')[:30])}.")
            o.AttachmentOffset = pl
        o.Label = s.unique(a.get("name") or f"Datum {kind}")
        doc(s).recompute()
        if a.get("to") and getattr(o, "MapMode", "") == "Deactivated":
            tried.append("left unattached")
        s.snapshot("part_datum")
        return {"datum": o.Label, "kind": kind, "type": o.TypeId, "made": made,
                "placement": picks.placement_json(o.Placement), **({"mode": o.MapMode} if a.get("to") else {}),
                **({"notes": tried} if a.get("to") and getattr(o, "MapMode", "") == "Deactivated" else {})}

    body = {"type": "string"}
    return [
        Tool("part_section", "Section curves where a body meets another body (with, parametric Part::Section) or a plane { normal, at }: the curves' edges, length, closed loops; face: true also fills the closed loops into a face (its area). Cut-lines, intersection checks, outlines at a height.",
             obj({"body": body, "with": body, "plane": {"type": "object"}, "face": {"type": "boolean"}, "approximate": {"type": "boolean"}, "parametric": {"type": "boolean"}, "name": body}, ["body"]), section),
        Tool("part_offset2d", "Offset a wire, sketch or flat face in its plane (Part::Offset2D): distance mm (+ out, − in), join arc | tangent | intersection, fill: true for the band between the two, intersection: true to merge loops that meet. Kerf and clearance outlines, gasket bands.",
             obj({"shape": body, "distance": {"type": "number"}, "join": {"type": "string", "enum": ["arc", "tangent", "intersection"]}, "fill": {"type": "boolean"}, "intersection": {"type": "boolean"}, "parametric": {"type": "boolean"}, "name": body}, ["shape", "distance"]), offset2d),
        Tool("part_extract", "Copy elements out as a body of their own (Part's element copy and shape builder): refs [\"Box.Face3\", \"Box.Edge2\", whole objects]; build copy (as they are) | wire (edges joined) | face (closed edges filled) | shell (faces sewn) | solid (a closed shell made a solid).",
             obj({"refs": {"type": "array", "items": {}}, "build": {"type": "string", "enum": ["copy", "wire", "face", "shell", "solid"]}, "tolerance": {"type": "number"}, "name": body}, ["refs"]), extract),
        Tool("part_defeature", "Take features off a solid by their faces (Part defeaturing): faces [\"Face12\", …] — every face of a hole, boss or fillet together; the gap closes as if the feature was never there. For simplifying imported STEP bodies before FEA or CAM.",
             obj({"body": body, "faces": {"type": "array", "items": {"type": "string"}}, "name": body}, ["faces"]), defeature),
        Tool("part_fix", "Repair a shape: op reverse (flip its orientation) | solid (a closed shell or faces made a solid) | sew (faces joined at tolerance mm) | fix (ShapeFix tolerances). Reports valid and closed.",
             obj({"body": body, "op": {"type": "string", "enum": ["reverse", "solid", "sew", "fix"]}, "tolerance": {"type": "number"}, "name": body}, ["body", "op"]), fix),
        Tool("part_datum", "A datum: kind point | line | plane | csys, at [x,y,z], normal (plane/line) or axis + angle, or attached to a face/edge/vertex (to \"Box.Face6\", mode a FreeCAD MapMode) with the placement as its offset. Reference geometry for attachments, sketches and measurements.",
             obj({"kind": {"type": "string", "enum": ["point", "line", "plane", "csys"]}, "at": {"type": "array", "items": {"type": "number"}}, "normal": {}, "axis": {}, "angle": {"type": "number"}, "to": body, "mode": body, "name": body}), datum),
    ]
