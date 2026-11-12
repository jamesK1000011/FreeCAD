# SPDX-License-Identifier: LGPL-2.1-or-later
# More of the Part workbench, beside model.py: wires and curves (FreeCAD's
# own parametric line, circle, ellipse, polygon, helix, spiral and plane, or
# a B-spline through points), splitting (slice, Boolean fragments, XOR),
# compounds made and taken apart, refine and repair, a face from wires,
# ruled surfaces, projection onto a face, a stack of cross-sections, a
# primitive attached to a face, and a mesh made a solid.

import FreeCAD
import Part

from .. import geometry as geo
from ..protocol import Tool, ToolError
from ..tools import obj
from . import picks

GROUP = "model"
INFO = ("Part modelling: primitives, wires and curves (helix, spiral …), booleans, split / slice / fragments, compounds, move/rotate/mirror/scale/arrays, "
        "fillets and chamfers, offset and shell, extrude, revolve, loft, sweep, ruled surfaces, projection, cross-sections, refine and repair, attachment, mesh to solid.")

V = FreeCAD.Vector


def bad(msg):
    return ToolError("invalid_input", msg)


def frame(a):
    """A placement from at [x, y, z] and normal (the curve's Z), as FreeCAD's
    primitives take one."""
    at = picks.vec3(a.get("at"), "at", (0, 0, 0))
    n = picks.direction(a.get("normal"), "normal", (0, 0, 1))
    return FreeCAD.Placement(at, FreeCAD.Rotation(V(0, 0, 1), n))


def stats_of(sh):
    out = {"edges": len(sh.Edges), "wires": len(sh.Wires), "faces": len(sh.Faces), "solids": len(sh.Solids)}
    if sh.Edges:
        out["length"] = round(sum(e.Length for e in sh.Edges), 4)
    if sh.Faces:
        out["area"] = round(sh.Area, 4)
    if sh.Solids:
        out["volume"] = round(sh.Volume, 4)
    try:
        out["closed"] = bool(sh.Wires and all(w.isClosed() for w in sh.Wires))
    except Exception:
        pass
    bb = sh.BoundBox
    out["box"] = [round(bb.XLength, 3), round(bb.YLength, 3), round(bb.ZLength, 3)]
    return out


def tools(session):
    s = session
    doc = lambda: s.doc  # noqa: E731

    def added(o, tool, extra=None):
        doc().recompute()
        if not o.isValid():
            msg = o.getStatusString()
            doc().removeObject(o.Name)
            raise ToolError("failed", f"FreeCAD refused it: {msg} — nothing was kept.")
        s.snapshot(tool)
        return {"object": o.Label, "type": o.TypeId.split("::")[-1], **stats_of(o.Shape), **(extra or {}), "step": len(s.steps)}

    def new_obj(kind, label):
        lab = s.unique(label)
        o = doc().addObject(kind, label.replace(" ", ""))
        o.Label = lab
        return o

    # ---- wires and curves ----

    def part_wire(a):
        kind = a.get("kind")
        name = a.get("name") or kind.capitalize() if kind else None
        if kind == "line":
            p, q = picks.vec3(a.get("from"), "from"), picks.vec3(a.get("to"), "to")
            if (p - q).Length < 1e-9:
                raise bad("from and to: two different points.")
            o = new_obj("Part::Line", name)
            o.X1, o.Y1, o.Z1 = p.x, p.y, p.z
            o.X2, o.Y2, o.Z2 = q.x, q.y, q.z
        elif kind in ("circle", "arc"):
            r = picks.num(a.get("radius"), "radius")
            if r <= 0:
                raise bad("radius: above 0.")
            o = new_obj("Part::Circle", name)
            o.Radius = r
            if kind == "arc":
                o.Angle1, o.Angle2 = float(a.get("start", 0)), float(a.get("end", 90))
            o.Placement = frame(a)
        elif kind == "ellipse":
            o = new_obj("Part::Ellipse", name)
            o.MajorRadius, o.MinorRadius = picks.num(a.get("major"), "major") / 2, picks.num(a.get("minor"), "minor") / 2
            if o.MinorRadius > o.MajorRadius or o.MinorRadius <= 0:
                raise bad("major ≥ minor > 0 (full sizes, mm).")
            o.Placement = frame(a)
        elif kind == "polygon":
            n = int(a.get("sides", 6))
            if n < 3:
                raise bad("sides: 3 or more.")
            o = new_obj("Part::RegularPolygon", name)
            o.Polygon = n
            o.Circumradius = picks.num(a.get("radius"), "radius")
            o.Placement = frame(a)
        elif kind == "helix":
            o = new_obj("Part::Helix", name)
            o.Pitch = picks.num(a.get("pitch"), "pitch")
            o.Height = picks.num(a.get("height"), "height")
            o.Radius = picks.num(a.get("radius"), "radius")
            if a.get("taper"):
                o.Angle = float(a["taper"])
            if a.get("leftHanded"):
                o.LocalCoord = "Left-handed"
            o.Placement = frame(a)
        elif kind == "spiral":
            o = new_obj("Part::Spiral", name)
            o.Growth = picks.num(a.get("growth"), "growth")
            o.Rotations = picks.num(a.get("turns"), "turns")
            o.Radius = picks.num(a.get("radius", 1), "radius")
            o.Placement = frame(a)
        elif kind == "plane":
            o = new_obj("Part::Plane", name)
            o.Length, o.Width = picks.num(a.get("length"), "length"), picks.num(a.get("width"), "width")
            o.Placement = frame(a)
        elif kind == "bspline":
            pts = [picks.vec3(p, "points") for p in (a.get("points") or [])]
            if len(pts) < 2:
                raise bad("points: two or more [x, y, z] it passes through.")
            c = Part.BSplineCurve()
            c.interpolate(pts, PeriodicFlag=bool(a.get("closed")))
            o = new_obj("Part::Feature", name)
            o.Shape = c.toShape()
        else:
            raise bad("kind: line, circle, arc, ellipse, polygon, helix, spiral, plane or bspline.")
        return added(o, "part_wire", {"use": "A profile or path for part_extrude, part_revolve, part_loft, part_sweep (a helix as a path: a spring or thread), part_ruled."})

    # ---- split, compounds ----

    def part_split(a):
        import BOPTools.SplitFeatures as SF

        mode = a.get("mode", "slice")
        objs = [picks.shaped(doc(), r) for r in (a.get("bodies") or [])]
        if mode == "slice":
            if len(objs) != 1:
                raise bad("slice: bodies [the one to cut], tools [what cuts it: faces, planes, other bodies].")
            tl = [picks.shaped(doc(), r) for r in (a.get("tools") or [])]
            if not tl:
                raise bad("tools: what cuts it — a plane (part_wire kind plane), a face, or bodies.")
            f = SF.makeSlice(name=s.unique(a.get("name") or "Slice").replace(" ", "_"))
            f.Base = objs[0]
            f.Tools = tl
            f.Mode = "Split" if a.get("split", True) else "Standard"
        elif mode in ("fragments", "xor"):
            if len(objs) < 2:
                raise bad(f"{mode}: bodies, two or more.")
            f = (SF.makeBooleanFragments if mode == "fragments" else SF.makeXOR)(name=s.unique(a.get("name") or mode.capitalize()).replace(" ", "_"))
            f.Objects = objs
            if mode == "fragments" and a.get("split", True):
                f.Mode = "Split"
        else:
            raise bad("mode: slice, fragments or xor.")
        doc().recompute()
        if not f.isValid() or f.Shape.isNull():
            msg = f.getStatusString()
            doc().removeObject(f.Name)
            raise ToolError("failed", f"The {mode} failed: {msg} — nothing was kept. The tools must cross the bodies.")
        pieces = [{"volume": round(x.Volume, 3), "center": geo.vec(x.CenterOfMass)} for x in f.Shape.Solids]
        out = {"object": f.Label, "mode": mode, "pieces": len(pieces), "solids": pieces[:20]}
        if a.get("explode"):
            out["bodies"] = explode_into(f, a.get("name") or f.Label)
        for o in objs + (tl if mode == "slice" else []):
            if hasattr(o, "Visibility"):
                o.Visibility = False
        s.snapshot("part_split")
        out["step"] = len(s.steps)
        if not a.get("explode"):
            out["next"] = "explode: true (or part_compound { explode }) makes each piece a body of its own."
        return out

    def explode_into(o, base):
        made = []
        for i, sol in enumerate(o.Shape.Solids, 1):
            n = doc().addObject("Part::Feature", "Body")
            n.Label = s.unique(f"{base} {i}")
            n.Shape = sol.copy()
            made.append(n.Label)
        if hasattr(o, "Visibility"):
            o.Visibility = False
        doc().recompute()
        return made

    def part_compound(a):
        if a.get("explode"):
            o = picks.shaped(doc(), a["explode"])
            if len(o.Shape.Solids) < 2:
                raise bad(f"{o.Label} is one solid: nothing to take apart.")
            made = explode_into(o, a.get("name") or o.Label)
            s.snapshot("part_compound")
            return {"from": o.Label, "bodies": made, "step": len(s.steps)}
        objs = [picks.shaped(doc(), r) for r in (a.get("bodies") or [])]
        if len(objs) < 2:
            raise bad("bodies: two or more to group into one compound, or explode: a body of several solids.")
        c = new_obj("Part::Compound", a.get("name") or "Compound")
        c.Links = objs
        return added(c, "part_compound", {"of": [o.Label for o in objs], "note": "A compound keeps its parts separate (no fusing): one object to move, export or pattern."})

    # ---- refine, repair, attach ----

    def part_refine(a):
        o = picks.solid_body(s, a.get("body"))
        sh = o.Shape
        before = len(sh.Faces)
        tol = float(a.get("tolerance", 0))
        fixed = sh.copy()
        notes = []
        if a.get("fix"):
            try:
                fixed.fix(tol or 1e-3, tol or 1e-3, tol or 1e-3)
                notes.append("fixed")
            except Exception as e:
                notes.append(f"fix failed: {e}")
            if not fixed.Solids and fixed.Shells:
                try:
                    fixed = Part.makeSolid(Part.Shell(fixed.Faces) if len(fixed.Shells) != 1 else fixed.Shells[0])
                    notes.append("closed shell made solid")
                except Exception as e:
                    notes.append(f"no solid from the shell: {e}")
        ref = fixed.removeSplitter()
        o2 = picks.bake(s, o, ref, a.get("name"))
        snd = geo.soundness(o2.Shape)
        s.snapshot("part_refine")
        return {"body": o2.Label, "faces": {"before": before, "after": len(o2.Shape.Faces)}, "done": notes + ["coplanar faces merged"], "sound": snd["ok"], **({"problems": snd["problems"]} if not snd["ok"] else {}), "step": len(s.steps)}

    def part_attach(a):
        o = picks.find(doc(), a.get("body"))
        if "AttachmentOffset" not in o.PropertiesList:
            raise bad(f"{o.Label} is a {o.TypeId}: only FreeCAD's own primitives and wires attach (part_primitive, part_wire); a modelling result is a plain shape.")
        ref = a.get("to")
        if not isinstance(ref, str) or "." not in ref:
            raise bad("to: a face or edge as \"Body.Face6\" (shape_query finds it), or a datum plane's name.")
        label, sub, _ = picks.resolve(s, ref)
        tgt = picks.find(doc(), label)
        if tgt is o:
            raise bad("to: another object than itself.")
        prop = "AttachmentSupport" if "AttachmentSupport" in o.PropertiesList else "Support"
        setattr(o, prop, [(tgt, sub)])
        o.MapMode = a.get("mode") or ("FlatFace" if sub.startswith("Face") else "NormalToEdge" if sub.startswith("Edge") else "ObjectXY")
        o.AttachmentOffset = FreeCAD.Placement(picks.vec3(a.get("offset"), "offset", (0, 0, 0)), FreeCAD.Rotation(V(0, 0, 1), float(a.get("rotate", 0))))
        doc().recompute()
        if not o.isValid():
            raise ToolError("failed", f"{o.Label} would not attach to {ref}: {o.getStatusString()} — nothing was kept (mode: FlatFace for a flat face; NormalToEdge, Concentric for edges).")
        s.snapshot("part_attach")
        return {"body": o.Label, "attachedTo": ref, "mode": o.MapMode, "placement": picks.placement_json(o.Placement), "note": f"It follows {tgt.Label} from now on.", "step": len(s.steps)}

    # ---- faces, ruled surfaces, projection, sections ----

    def part_face(a):
        refs = a.get("from") or []
        wires = []
        for r in refs:
            o = picks.shaped(doc(), r)
            ws = o.Shape.Wires or ([Part.Wire(Part.__sortEdges__(o.Shape.Edges))] if o.Shape.Edges else [])
            wires += ws
        if not wires:
            raise bad("from: [names] of closed wires or sketches — the outline first, holes after.")
        if not all(w.isClosed() for w in wires):
            raise bad("from: every wire must be closed.")
        try:
            face = Part.makeFace(wires, "Part::FaceMakerBullseye")
        except Exception as e:
            raise ToolError("failed", f"No face from those wires ({e}): they must be flat, in one plane, and not cross.")
        o = new_obj("Part::Feature", a.get("name") or "Face")
        o.Shape = face
        return added(o, "part_face")

    def part_ruled(a):
        x, y = picks.shaped(doc(), a.get("a")), picks.shaped(doc(), a.get("b"))
        o = new_obj("Part::RuledSurface", a.get("name") or "Ruled")
        o.Curve1, o.Curve2 = (x, []), (y, [])
        if a.get("reversed"):
            o.Orientation = "Reversed"
        return added(o, "part_ruled", {"note": "A surface: part_thickness or surface_thicken makes it a solid."})

    def part_project(a):
        src = picks.shaped(doc(), a.get("shape"))
        ref = a.get("onto")
        if not isinstance(ref, str) or ".Face" not in ref:
            raise bad("onto: a face as \"Body.Face6\".")
        _, _, face = picks.resolve(s, ref)
        d = picks.direction(a.get("direction"), "direction", (0, 0, -1)) if a.get("direction") is not None else None
        edges = src.Shape.Edges
        if not edges:
            raise bad(f"{src.Label} has no edges to project.")
        # OCCT projects one edge or wire at a time
        got, missed = [], 0
        for w in (src.Shape.Wires or [Part.Wire([e]) for e in edges]):
            try:
                r = face.makeParallelProjection(w, d) if d is not None else face.project(w.Edges)
                if not r.isNull() and r.Edges:
                    got += r.Edges
                else:
                    missed += 1
            except Exception:
                missed += 1
        res = Part.Compound(got) if got else Part.Shape()
        if res.isNull() or not res.Edges:
            raise ToolError("failed", "Nothing landed on the face: the shape misses it in that direction.")
        o = new_obj("Part::Feature", a.get("name") or f"{src.Label} on face")
        o.Shape = res
        extra = {"onto": ref, "how": ("along " + str(geo.vec(d))) if d is not None else "along the face's normals"}
        if missed:
            extra["missed"] = f"{missed} wire(s) did not land on the face"
        return added(o, "part_project", extra)

    def part_slices(a):
        o = picks.solid_body(s, a.get("body"))
        n = picks.direction(a.get("normal"), "normal", (0, 0, 1))
        bb = o.Shape.BoundBox
        span = [bb.XMin * n.x + bb.YMin * n.y + bb.ZMin * n.z, bb.XMax * n.x + bb.YMax * n.y + bb.ZMax * n.z]
        lo, hi = min(span), max(span)
        if a.get("at") is not None:
            ds = [float(x) for x in a["at"]]
        else:
            k = int(a.get("count", 5))
            if not 1 <= k <= 500:
                raise bad("count: 1 … 500.")
            ds = [lo + (hi - lo) * (i + 1) / (k + 1) for i in range(k)]
        wires, rows = [], []
        for dv in ds:
            ws = o.Shape.slice(n, dv)
            wires += ws
            rows.append({"at": round(dv, 4), "wires": len(ws), "length": round(sum(w.Length for w in ws), 3),
                         "area": round(sum(Part.Face(w).Area for w in ws if w.isClosed()), 3) if ws else 0.0})
        if not wires:
            raise ToolError("failed", f"No slice met {o.Label} along {geo.vec(n)} at those distances ({lo:.3g} … {hi:.3g}).")
        c = new_obj("Part::Feature", a.get("name") or f"{o.Label} slices")
        c.Shape = Part.Compound(wires)
        out = added(c, "part_slices", {"normal": geo.vec(n), "range": [round(lo, 3), round(hi, 3)], "slices": rows})
        out["use"] = "cut_export writes them as DXF for a laser; section_to_sketch makes one a sketch."
        return out

    # ---- mesh to solid ----

    def part_from_mesh(a):
        import Mesh

        if a.get("file"):
            import os

            p = os.path.expanduser(a["file"])
            if not os.path.isabs(p):
                p = os.path.join(s.out_dir, p)
            if not os.path.isfile(p):
                raise ToolError("not_found", f"{p}: no such file.")
            m = Mesh.Mesh(p)
            label = os.path.splitext(os.path.basename(p))[0]
        else:
            src = picks.find(doc(), a.get("mesh"))
            if not src.isDerivedFrom("Mesh::Feature"):
                raise bad(f"{src.Label} is not a mesh: mesh or file.")
            m, label = src.Mesh, src.Label
        if m.CountFacets > int(a.get("maxFacets", 60000)):
            raise ToolError("invalid_input", f"{m.CountFacets} triangles: a solid of every one is slow and heavy — reduce it first (mesh_reduce with ScanRuler for FreeCAD), or raise maxFacets.")
        tol = float(a.get("tolerance", 0.05))
        sh = Part.Shape()
        sh.makeShapeFromMesh(m.Topology, tol)
        try:
            sol = Part.makeSolid(Part.makeShell(sh.Faces) if not sh.Shells else sh.Shells[0])
        except Exception as e:
            raise ToolError("failed", f"The mesh does not close into a solid ({e}): fill its holes first (mesh_fill_holes).")
        if a.get("refine", True):
            try:
                sol = sol.removeSplitter()
            except Exception:
                pass
        o = new_obj("Part::Feature", a.get("name") or f"{label} solid")
        o.Shape = sol
        snd = geo.soundness(sol)
        return added(o, "part_from_mesh", {"triangles": m.CountFacets, "sound": snd["ok"], **({"problems": snd["problems"]} if not snd["ok"] else {}),
                                           "note": "Flat triangles merged where they lie in one plane (refine); curved areas stay faceted — for a clean model, fit features instead (ScanRuler's element_fit)."})

    at_n = {"at": {"type": "array", "items": {}}, "normal": {"type": "array", "items": {}}}
    return [
        Tool("part_wire", "A wire, curve or flat face as FreeCAD's own parametric object: kind line (from, to) | circle (radius) | arc (radius, start, end degrees) | ellipse (major, minor) | polygon (sides, radius to corners) | helix (pitch, height, radius, taper, leftHanded) | spiral (growth per turn, turns, radius) | plane (length, width) | bspline (points it passes through, closed); at and normal place it. Profiles and paths for extrude, sweep, loft.",
             obj({"kind": {"type": "string"}, "from": {"type": "array", "items": {}}, "to": {"type": "array", "items": {}}, "radius": {"type": "number"}, "start": {"type": "number"}, "end": {"type": "number"}, "major": {"type": "number"}, "minor": {"type": "number"},
                  "sides": {"type": "integer"}, "pitch": {"type": "number"}, "height": {"type": "number"}, "taper": {"type": "number"}, "leftHanded": {"type": "boolean"}, "growth": {"type": "number"}, "turns": {"type": "number"},
                  "length": {"type": "number"}, "width": {"type": "number"}, "points": {"type": "array", "items": {}}, "closed": {"type": "boolean"}, "name": {"type": "string"}, **at_n}, ["kind"]), part_wire, changes=True),
        Tool("part_split", "Split bodies (Part's BOPTools, parametric): mode slice (bodies [one], tools [planes, faces or bodies that cut it]) | fragments (bodies: every piece where they overlap, kept apart) | xor (bodies: what is in an odd number of them); explode: true makes each piece a body.",
             obj({"mode": {"type": "string", "enum": ["slice", "fragments", "xor"]}, "bodies": {"type": "array", "items": {"type": "string"}}, "tools": {"type": "array", "items": {"type": "string"}}, "split": {"type": "boolean"}, "explode": {"type": "boolean"}, "name": {"type": "string"}}, ["bodies"]), part_split, changes=True),
        Tool("part_compound", "Group bodies into one compound (kept apart, not fused) — bodies [names]; or explode: a body of several solids into a body each.",
             obj({"bodies": {"type": "array", "items": {"type": "string"}}, "explode": {"type": "string"}, "name": {"type": "string"}}), part_compound, changes=True),
        Tool("part_refine", "Merge a body's coplanar and co-cylindrical faces (refine); fix: true also repairs its tolerances and makes a closed shell a solid.",
             obj({"body": {"type": "string"}, "fix": {"type": "boolean"}, "tolerance": {"type": "number"}, "name": {"type": "string"}}), part_refine, changes=True),
        Tool("part_attach", "Attach a primitive or wire to a face or edge so it follows it: to \"Body.Face6\", mode (FlatFace, NormalToEdge, Concentric … default by what it is), offset [x, y, z] and rotate degrees in the face's frame.",
             obj({"body": {"type": "string"}, "to": {"type": "string"}, "mode": {"type": "string"}, "offset": {"type": "array", "items": {}}, "rotate": {"type": "number"}}, ["body", "to"]), part_attach, changes=True),
        Tool("part_face", "A flat face from closed wires or sketches: from [the outline, then holes].", obj({"from": {"type": "array", "items": {"type": "string"}}, "name": {"type": "string"}}, ["from"]), part_face, changes=True),
        Tool("part_ruled", "A ruled surface between two edges or wires (a and b by name): straight lines from one to the other. reversed: true if it twists.",
             obj({"a": {"type": "string"}, "b": {"type": "string"}, "reversed": {"type": "boolean"}, "name": {"type": "string"}}, ["a", "b"]), part_ruled, changes=True),
        Tool("part_project", "Project a wire, sketch or a body's edges onto a face: shape, onto \"Body.Face6\", direction [x, y, z] (left out: along the face's normals). Lettering wrapped onto a curved face, an outline traced onto a surface.",
             obj({"shape": {"type": "string"}, "onto": {"type": "string"}, "direction": {"type": "array", "items": {}}, "name": {"type": "string"}}, ["shape", "onto"]), part_project, changes=True),
        Tool("part_slices", "Cross-sections of a body: normal [x, y, z] (default z), count (evenly through it) or at [distances along the normal]; each slice's wires, length and enclosed area, kept as one compound. Layer outlines, laser-cut stacks, area along a length.",
             obj({"body": {"type": "string"}, "normal": {"type": "array", "items": {}}, "count": {"type": "integer"}, "at": {"type": "array", "items": {"type": "number"}}, "name": {"type": "string"}}), part_slices, changes=True),
        Tool("part_from_mesh", "A mesh made a solid: mesh (an object) or file (STL, OBJ …); tolerance sews it, refine (default) merges flat triangles. For a closed, modest mesh (maxFacets 60000).",
             obj({"mesh": {"type": "string"}, "file": {"type": "string"}, "tolerance": {"type": "number"}, "refine": {"type": "boolean"}, "maxFacets": {"type": "integer"}, "name": {"type": "string"}}), part_from_mesh, changes=True),
    ]
