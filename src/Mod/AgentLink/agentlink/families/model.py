# SPDX-License-Identifier: LGPL-2.1-or-later
# Part workbench modelling: primitives (FreeCAD's own parametric ones, so
# object_set can change a size later), booleans, moves and copies, fillets
# and chamfers on edges picked by reference or by query, offsets and shells,
# and solids from profiles (extrude, revolve, loft, sweep). A result that
# consumes bodies is kept as a plain shape under the first body's label, so
# setups, specs and history follow it.

import FreeCAD
import Part

from .. import geometry as geo
from ..protocol import Tool, ToolError
from ..tools import obj
from . import measure as ms
from . import picks

GROUP = "model"
INFO = "Part modelling: primitives, booleans, move/rotate/mirror/scale/arrays, fillets and chamfers, offset and shell, extrude, revolve, loft, sweep."

V = FreeCAD.Vector

PRIMS = {
    "box": "Part::Box", "cylinder": "Part::Cylinder", "sphere": "Part::Sphere", "cone": "Part::Cone",
    "torus": "Part::Torus", "wedge": "Part::Wedge", "prism": "Part::Prism", "tube": None,
}
WEDGE = {"xmin": 0, "ymin": 0, "zmin": 0, "x2min": 2, "z2min": 2, "xmax": 10, "ymax": 10, "zmax": 10, "x2max": 8, "z2max": 8}


def positive(a, key, default):
    v = picks.num(a.get(key, default), key)
    if v <= 0:
        raise ToolError("invalid_input", f"{key}: above 0, mm.")
    return v


def changed(before, after):
    """Whether a boolean did anything: the volume or the face count moved."""
    tol = max(1e-6, 1e-7 * max(before.Volume, 1.0))
    return abs(after.Volume - before.Volume) > tol or len(after.Faces) != len(before.Faces)


def report(session, o, tool, extra=None):
    s = geo.soundness(o.Shape)
    session.snapshot(tool)
    out = {"body": o.Label, **geo.stats(o.Shape), "sound": s["ok"], **(extra or {}), "step": len(session.steps)}
    if not s["ok"]:
        out["problems"] = s["problems"]
    return out


# ---- profiles: what extrude, revolve, loft and sweep take ----------------------------------

def profile(session, spec, body=None, what="profile", closed_default=True):
    """A profile as a shape (a face or a wire) and the object it came from:
    a reference ("Sketch", "Box.Face6", "Box.Edge3"), { points: [[x,y,z] …],
    closed }, or { circle: { center, radius, normal } }."""
    if isinstance(spec, dict) and "points" in spec:
        pts = [picks.vec3(p, f"{what}.points") for p in spec["points"]]
        if len(pts) < 2:
            raise ToolError("invalid_input", f"{what}.points: two or more [x, y, z].")
        closed = spec.get("closed", closed_default)
        if closed and len(pts) < 3:
            raise ToolError("invalid_input", f"{what}.points: three or more for a closed outline.")
        if closed and (pts[0] - pts[-1]).Length > 1e-9:
            pts.append(pts[0])
        return Part.makePolygon(pts), None
    if isinstance(spec, dict) and "circle" in spec:
        c = spec["circle"] or {}
        rad = picks.num(c.get("radius"), f"{what}.circle.radius")
        if rad <= 0:
            raise ToolError("invalid_input", f"{what}.circle.radius: above 0.")
        return Part.Wire([Part.makeCircle(rad, picks.vec3(c.get("center"), f"{what}.circle.center", (0, 0, 0)), picks.direction(c.get("normal"), f"{what}.circle.normal", (0, 0, 1)))]), None
    if not isinstance(spec, str):
        raise ToolError("invalid_input", f"{what}: a sketch or wire's name, a face or edge (\"Box.Face6\"), {{ points: [[x,y,z] …], closed }} or {{ circle: {{ center, radius, normal }} }}.")
    label, sub, sh = picks.resolve(session, spec, body)
    if sub:
        if sub.startswith("Edge"):
            return Part.Wire([sh]), None
        if sub.startswith("Face"):
            return sh, None
        raise ToolError("invalid_input", f"{what}: a vertex is not a profile.")
    o = picks.find(session.doc, label)
    if sh.Solids:
        raise ToolError("invalid_input", f"{what}: {o.Label} is a solid — name one of its faces (\"{o.Label}.Face6\"; shape_query finds it).")
    if sh.Faces and len(sh.Faces) == 1:
        return sh.Faces[0], o
    wires = sh.Wires or ([Part.Wire(Part.__sortEdges__(sh.Edges))] if sh.Edges else [])
    if not wires:
        raise ToolError("invalid_input", f"{what}: {o.Label} has no wire.")
    if len(wires) == 1:
        return wires[0], o
    return Part.Compound(wires), o


def as_face(sh, what="profile"):
    """A closed profile as a face (holes kept), for a solid."""
    if sh.ShapeType == "Face":
        return sh
    wires = sh.Wires
    if not wires or not all(w.isClosed() for w in wires):
        raise ToolError("invalid_input", f"{what}: not closed, so it makes no solid — close the outline, or solid: false for a surface.")
    try:
        return Part.makeFace(wires, "Part::FaceMakerBullseye")
    except Exception:
        try:
            return Part.Face(wires[0])
        except Exception as e:
            raise ToolError("failed", f"{what}: no face from it ({e}) — is it flat?")


def profile_normal(sh):
    if sh.ShapeType == "Face":
        return ms.face_normal(sh)
    try:
        pl = sh.findPlane()
        if pl is not None:
            return pl.Axis
    except Exception:
        pass
    return V(0, 0, 1)


def tools(session):
    s = session
    doc = lambda: s.doc  # noqa: E731 — doc_open may change the session's document
    body = {"type": "string", "description": "A body's name or label; may be left out when there is one."}
    prof = {"description": "A sketch or wire's name, a face or edge (\"Box.Face6\"), { points: [[x,y,z] …], closed: true } or { circle: { center, radius, normal } }."}
    common_in = {"into": body, "op": {"type": "string", "enum": ["new", "fuse", "cut", "common"]}, "name": {"type": "string"}}

    def finish(result, a, tool, consumed=()):
        """A made solid: a new body, or fused into / cut from / common with `into`."""
        op = a.get("op") or ("fuse" if a.get("into") else "new")
        if op == "new":
            o = doc().addObject("Part::Feature", "Body")
            o.Label = s.unique(a.get("name") or tool.split("_")[1].capitalize())
            o.Shape = geo.clean(result)
            for c in consumed:
                if c is not None and hasattr(c, "Visibility"):
                    c.Visibility = False
            doc().recompute()
            return report(s, o, tool)
        if not a.get("into"):
            raise ToolError("invalid_input", f"op {op}: into names the body it acts on.")
        tgt = picks.solid_body(s, a["into"])
        before = tgt.Shape
        after = {"fuse": lambda: before.fuse(result), "cut": lambda: before.cut(result), "common": lambda: before.common(result)}[op]()
        if not changed(before, after):
            raise ToolError("failed", f"op {op} into {tgt.Label} changed nothing: the made solid does not reach it (least distance {before.distToShape(result)[0]:.3f} mm) — check the direction and length.")
        o = picks.bake(s, tgt, after, a.get("name"))
        for c in consumed:
            if c is not None and hasattr(c, "Visibility"):
                c.Visibility = False
        return report(s, o, tool, {"op": op, "volumeChange": round(o.Shape.Volume - before.Volume, 2)})

    # ---- primitives ----

    def part_primitive(a):
        kind = a.get("kind")
        if kind not in PRIMS:
            raise ToolError("invalid_input", f"kind: one of {', '.join(PRIMS)}.")
        pl = picks.placement(a)
        label = s.unique(a.get("name") or kind.capitalize())
        d = doc()
        if kind == "box":
            size = a.get("size", [10, 10, 10])
            if not (isinstance(size, list) and len(size) == 3 and all(isinstance(x, (int, float)) and x > 0 for x in size)):
                raise ToolError("invalid_input", "size: [x, y, z], each above 0 mm.")
            o = d.addObject("Part::Box", "Box")
            o.Length, o.Width, o.Height = [float(x) for x in size]
        elif kind == "cylinder":
            o = d.addObject("Part::Cylinder", "Cylinder")
            o.Radius, o.Height = positive(a, "radius", 5), positive(a, "height", 10)
        elif kind == "sphere":
            o = d.addObject("Part::Sphere", "Sphere")
            o.Radius = positive(a, "radius", 5)
        elif kind == "cone":
            r1, r2 = positive(a, "radius", 5), picks.num(a.get("radius2", 0), "radius2")
            if r2 < 0 or abs(r1 - r2) < 1e-9:
                raise ToolError("invalid_input", "radius2: the top radius, 0 or more and not equal to radius (that is a cylinder).")
            o = d.addObject("Part::Cone", "Cone")
            o.Radius1, o.Radius2, o.Height = r1, r2, positive(a, "height", 10)
        elif kind == "torus":
            r1, r2 = positive(a, "radius", 10), positive(a, "radius2", 2)
            if r2 >= r1:
                raise ToolError("invalid_input", "radius2: the tube's radius, under radius (the ring's).")
            o = d.addObject("Part::Torus", "Torus")
            o.Radius1, o.Radius2 = r1, r2
        elif kind == "wedge":
            w = dict(WEDGE)
            bad = [k for k in (a.get("wedge") or {}) if k not in WEDGE]
            if bad:
                raise ToolError("invalid_input", f"wedge.{bad[0]}: the keys are {', '.join(WEDGE)}.")
            w.update({k: picks.num(v, f"wedge.{k}") for k, v in (a.get("wedge") or {}).items()})
            if not (w["xmax"] > w["xmin"] and w["ymax"] > w["ymin"] and w["zmax"] > w["zmin"] and w["x2max"] >= w["x2min"] and w["z2max"] >= w["z2min"]):
                raise ToolError("invalid_input", "wedge: each max above its min (x2/z2 may be equal: a ridge).")
            o = d.addObject("Part::Wedge", "Wedge")
            for k, v in w.items():
                setattr(o, k[0].upper() + k[1:], v)
        elif kind == "prism":
            n = int(a.get("sides", 6))
            if n < 3:
                raise ToolError("invalid_input", "sides: 3 or more.")
            o = d.addObject("Part::Prism", "Prism")
            o.Polygon, o.Circumradius, o.Height = n, positive(a, "radius", 5), positive(a, "height", 10)
        else:  # tube
            ro, ri, h = positive(a, "radius", 5), picks.num(a.get("radius2", 2), "radius2"), positive(a, "height", 10)
            if not 0 < ri < ro:
                raise ToolError("invalid_input", "radius2: the bore's radius, above 0 and under radius.")
            before = {x.Name for x in d.Objects}
            try:
                from BasicShapes import Shapes

                o = Shapes.addTube(d, "Tube")
                o.OuterRadius, o.InnerRadius, o.Height = ro, ri, h
            except Exception:  # an older or trimmed build: a plain shape
                for x in [x for x in d.Objects if x.Name not in before]:
                    d.removeObject(x.Name)
                o = d.addObject("Part::Feature", "Tube")
                o.Shape = Part.makeCylinder(ro, h).cut(Part.makeCylinder(ri, h))
        o.Label = label
        o.Placement = pl
        d.recompute()
        if o.Shape.isNull() or not o.Shape.Solids:
            raise ToolError("failed", f"The {kind} came out empty — check its sizes.")
        return report(s, o, "part_primitive", {"kind": kind, "type": o.TypeId, "placement": picks.placement_json(o.Placement)})

    # ---- booleans ----

    def part_boolean(a):
        op = a.get("op")
        if op not in ("fuse", "cut", "common"):
            raise ToolError("invalid_input", "op: fuse, cut or common.")
        base = picks.solid_body(s, a.get("base"))
        names = a.get("tools")
        if isinstance(names, str):
            names = [names]
        if not names:
            raise ToolError("invalid_input", "tools: the bodies to fuse, cut away or keep in common with base.")
        tl = [picks.solid_body(s, n) for n in names]
        if any(t is base for t in tl):
            raise ToolError("invalid_input", "tools: base is among them — a body with itself changes nothing.")
        bs = base.Shape
        shapes = [t.Shape for t in tl]
        res = {"fuse": bs.fuse, "cut": bs.cut, "common": bs.common}[op](shapes if len(shapes) > 1 else shapes[0])
        res = geo.clean(res)
        if op == "common" and res.Volume < 1e-6:
            raise ToolError("failed", f"common: {base.Label} and {', '.join(t.Label for t in tl)} do not overlap (least distance {bs.distToShape(shapes[0])[0]:.3f} mm) — nothing would be left.")
        if not changed(bs, res):
            dist = min(bs.distToShape(x)[0] for x in shapes)
            why = "they do not touch" if dist > 1e-6 else ("the tools lie inside it" if op == "fuse" else "they only touch it")
            raise ToolError("failed", f"{op} changed nothing in {base.Label}: {why} (least distance {dist:.3f} mm). Move a tool into the base (part_transform), then again.")
        o = picks.bake(s, base, res, a.get("name"), consumed=tl, keep=bool(a.get("keep")))
        extra = {"op": op, "volumeChange": round(o.Shape.Volume - bs.Volume, 2), ("kept" if a.get("keep") else "removed"): [t.Label for t in tl]}
        if len(o.Shape.Solids) > 1:
            extra["note"] = f"{len(o.Shape.Solids)} separate solids: pieces that do not touch."
        return report(s, o, "part_boolean", extra)

    # ---- transforms ----

    def part_transform(a):
        o = picks.solid_body(s, a.get("body"))
        op = a.get("op")
        sh = o.Shape
        about = picks.vec3(a.get("about"), "about", (0, 0, 0)) if a.get("about") is not None else None
        if op in ("move", "rotate"):
            if op == "move":
                pl = FreeCAD.Placement(picks.vec3(a.get("by"), "by"), FreeCAD.Rotation())
            else:
                c = about if about is not None else sh.BoundBox.Center
                pl = FreeCAD.Placement(V(), FreeCAD.Rotation(picks.direction(a.get("axis"), "axis", (0, 0, 1)), picks.num(a.get("angle"), "angle")), c)
            if a.get("copy"):
                new = sh.copy()
                new.Placement = pl.multiply(new.Placement)
                n = doc().addObject("Part::Feature", "Body")
                n.Label = s.unique(a.get("name") or o.Label)
                n.Shape = new
                doc().recompute()
                return report(s, n, "part_transform", {"op": op, "from": o.Label})
            o.Placement = pl.multiply(o.Placement)
            doc().recompute()
            return report(s, o, "part_transform", {"op": op, "placement": picks.placement_json(o.Placement)})
        if op == "mirror":
            c = about if about is not None else sh.BoundBox.Center
            new = sh.mirror(c, picks.direction(a.get("normal"), "normal", (1, 0, 0)))
            if a.get("fuse"):
                res = sh.fuse(new)
                return report(s, picks.bake(s, o, res, a.get("name")), "part_transform", {"op": "mirror", "fused": True})
        elif op == "scale":
            f = a.get("factor")
            c = about if about is not None else V(0, 0, 0)
            if isinstance(f, list):
                fx, fy, fz = [picks.num(x, "factor") for x in (f + [1, 1, 1])[:3]]
                if min(fx, fy, fz) <= 0:
                    raise ToolError("invalid_input", "factor: each above 0.")
                m = FreeCAD.Matrix()
                m.move(c * -1)
                m2 = FreeCAD.Matrix()
                m2.scale(fx, fy, fz)
                m = m2.multiply(m)
                m3 = FreeCAD.Matrix()
                m3.move(c)
                new = sh.transformGeometry(m3.multiply(m))
            else:
                fac = picks.num(f, "factor")
                if fac <= 0:
                    raise ToolError("invalid_input", "factor: above 0 — a number, or [fx, fy, fz] for each axis.")
                new = sh.copy()
                new.scale(fac, c)
        elif op in ("linear", "polar"):
            n = int(a.get("count", 2))
            if n < 2:
                raise ToolError("invalid_input", "count: 2 or more, the original counted.")
            copies = [sh]
            if op == "linear":
                step = picks.vec3(a.get("step"), "step")
                for i in range(1, n):
                    c2 = sh.copy()
                    c2.translate(step * i)
                    copies.append(c2)
            else:
                axis = picks.direction(a.get("axis"), "axis", (0, 0, 1))
                c = about if about is not None else V(0, 0, 0)
                total = picks.num(a.get("angle", 360), "angle")
                each = total / n if abs(abs(total) - 360) < 1e-9 else total / (n - 1)
                for i in range(1, n):
                    c2 = sh.copy()
                    c2.rotate(c, axis, each * i)
                    copies.append(c2)
            if a.get("fuse", True):
                res = copies[0].fuse(copies[1:])
            else:
                res = Part.Compound(copies)
            o2 = picks.bake(s, o, res, a.get("name"))
            extra = {"op": op, "count": n}
            if len(o2.Shape.Solids) > 1:
                extra["note"] = f"{len(o2.Shape.Solids)} separate solids: the copies do not touch — one body holding them."
            return report(s, o2, "part_transform", extra)
        else:
            raise ToolError("invalid_input", "op: move { by }, rotate { axis, angle, about }, mirror { normal, about, fuse }, scale { factor, about }, linear { count, step }, polar { count, axis, about, angle }.")
        n = doc().addObject("Part::Feature", "Body")
        n.Label = s.unique(a.get("name") or f"{o.Label} {op}")
        n.Shape = geo.clean(new)
        doc().recompute()
        return report(s, n, "part_transform", {"op": op, "from": o.Label})

    # ---- fillets and chamfers ----

    def pick_edges(o, a):
        sh = o.Shape
        out = []
        refs = a.get("edges")
        if refs == "all":
            return list(sh.Edges)
        for ref in refs or []:
            head, sub = picks.split_ref(ref) if isinstance(ref, str) else (None, None)
            if not sub:
                raise ToolError("invalid_input", f"edges: {ref!r} is not \"Edge3\" or \"Face6\" (a face takes all its edges).")
            if head and picks.find(doc(), head) is not o:
                raise ToolError("invalid_input", f"edges: {ref} is on another body than {o.Label}.")
            el = picks.element(sh, sub, o.Label)
            out.extend([sh.Edges[picks.index_of(sh, e, "Edges") - 1] for e in el.Edges] if sub.startswith("Face") else [sh.Edges[int(sub[4:]) - 1]])
        if a.get("query"):
            q = dict(a["query"])
            q.update(body=o.Name, kind="edge")
            _, _, hits = ms.query(s, q, "edge")
            out.extend(x for _, x, _ in hits)
        uniq = []
        for e in out:
            if not any(e.isSame(u) for u in uniq):
                uniq.append(e)
        if not uniq:
            raise ToolError("invalid_input", "No edge picked: edges [\"Edge3\", \"Face6\" …] or \"all\", or query { type, axis, radius, near, inside, of } as shape_query takes.")
        return uniq

    def round_edges(kind):
        def run(a):
            o = picks.solid_body(s, a.get("body"))
            size = positive(a, "radius" if kind == "fillet" else "size", 1)
            edges = pick_edges(o, a)
            sh = o.Shape
            try:
                res = sh.makeFillet(size, edges) if kind == "fillet" else sh.makeChamfer(size, edges)
            except Exception as e:
                raise ToolError("failed", f"The {kind} of {size} mm on {len(edges)} edges failed ({e}): try a smaller size, fewer edges at once, or the edges of one face.")
            if res.isNull() or not res.isValid() or not res.Solids:
                raise ToolError("failed", f"The {kind} left a broken shape: {size} mm is likely more than a face beside an edge can take — smaller, or fewer edges.")
            o2 = picks.bake(s, o, res, a.get("name"))
            return report(s, o2, f"part_{kind}", {"edges": len(edges), "volumeChange": round(o2.Shape.Volume - sh.Volume, 2)})

        return run

    # ---- offset and shell ----

    def part_offset(a):
        o = picks.solid_body(s, a.get("body"))
        d = picks.num(a.get("distance"), "distance")
        if abs(d) < 1e-6:
            raise ToolError("invalid_input", "distance: not 0 — out (+) or in (-), mm.")
        join = {"arc": 0, "intersection": 2}.get(a.get("join", "arc"))
        if join is None:
            raise ToolError("invalid_input", "join: arc (rounded corners) or intersection (sharp).")
        try:
            res = o.Shape.makeOffsetShape(d, 1e-6, join=join, fill=False)
        except Exception as e:
            raise ToolError("failed", f"The offset failed ({e}): a smaller distance, or join: arc.")
        if not res.Solids:
            try:
                res = Part.Solid(Part.Shell(res.Faces)) if res.Faces else res
            except Exception:
                pass
        if res.isNull() or not res.Solids:
            raise ToolError("failed", f"The offset of {d} mm left no solid: inward past half the thinnest wall, or a fillet smaller than it.")
        o2 = picks.bake(s, o, res, a.get("name"))
        return report(s, o2, "part_offset", {"distance": d})

    def part_thickness(a):
        o = picks.solid_body(s, a.get("body"))
        t = positive(a, "thickness", 1)
        sh = o.Shape
        faces = []
        for ref in a.get("open") or []:
            head, sub = picks.split_ref(ref)
            if not sub or not sub.startswith("Face"):
                raise ToolError("invalid_input", f"open: {ref!r} — faces to remove (\"Face6\").")
            faces.append(picks.element(sh, sub, o.Label))
        if a.get("query"):
            q = dict(a["query"])
            q.update(body=o.Name, kind="face")
            _, _, hits = ms.query(s, q, "face")
            faces.extend(x for _, x, _ in hits)
        if not faces:
            raise ToolError("invalid_input", "open: the faces to remove, at least one (\"Face6\"), or query { normal: \"+z\", sort: \"-z\", limit: 1 }.")
        inward = a.get("inward", True)
        try:
            res = sh.makeThickness(faces, -t if inward else t, 1e-3, False, False, 0, {"arc": 0, "intersection": 2}.get(a.get("join", "intersection"), 2))
        except Exception as e:
            raise ToolError("failed", f"The shell failed ({e}): a thinner wall, or join: arc, or remove fewer faces.")
        if res.isNull() or not res.Solids or res.Volume < 1e-6:
            raise ToolError("failed", f"The shell of {t} mm left no solid: thinner than half the part's smallest size.")
        if not res.isValid():
            raise ToolError("failed", f"The shell of {t} mm is not valid: a thinner wall, or fillets larger than {t} mm first.")
        o2 = picks.bake(s, o, res, a.get("name"))
        return report(s, o2, "part_thickness", {"thickness": t, "inward": bool(inward), "opened": len(faces), "volumeChange": round(o2.Shape.Volume - sh.Volume, 2)})

    # ---- solids from profiles ----

    def part_extrude(a):
        sh, src = profile(s, a.get("profile"), a.get("body"))
        solid = a.get("solid", True)
        base = as_face(sh) if solid else sh
        if a.get("direction") is not None:
            vec = picks.vec3(a["direction"], "direction")
            if a.get("length") is not None:
                vec = picks.direction(a["direction"], "direction") * picks.num(a["length"], "length")
        else:
            vec = profile_normal(sh) * picks.num(a.get("length", 10), "length")
        if vec.Length < 1e-9:
            raise ToolError("invalid_input", "length: not 0.")
        if a.get("symmetric"):
            base = base.copy()
            base.translate(vec * -0.5)
        res = base.extrude(vec)
        if solid and not res.Solids:
            raise ToolError("failed", "The extrusion made no solid: the profile must be a closed, flat outline.")
        return finish(res, a, "part_extrude", [src])

    def part_revolve(a):
        sh, src = profile(s, a.get("profile"), a.get("body"))
        solid = a.get("solid", True)
        base = as_face(sh) if solid else sh
        ang = picks.num(a.get("angle", 360), "angle")
        res = base.revolve(picks.vec3(a.get("at"), "at", (0, 0, 0)), picks.direction(a.get("axis"), "axis", (0, 0, 1)), ang)
        if solid and not res.Solids:
            try:
                res = Part.Solid(Part.Shell(res.Faces))
            except Exception:
                raise ToolError("failed", "The revolve made no solid: the profile must be closed and not cross the axis.")
        if solid and res.Volume < 0:
            res.reverse()
        return finish(res, a, "part_revolve", [src])

    def part_loft(a):
        specs = a.get("profiles") or []
        if len(specs) < 2:
            raise ToolError("invalid_input", "profiles: two or more, in order.")
        wires, srcs = [], []
        for i, p in enumerate(specs):
            sh, src = profile(s, p, a.get("body"), f"profiles.{i}")
            if sh.ShapeType == "Face":
                sh = sh.OuterWire
            elif sh.ShapeType != "Wire":
                if len(sh.Wires) != 1:
                    raise ToolError("invalid_input", f"profiles.{i}: one wire each — {len(sh.Wires)} here.")
                sh = sh.Wires[0]
            wires.append(sh)
            srcs.append(src)
        try:
            res = Part.makeLoft(wires, bool(a.get("solid", True)), bool(a.get("ruled", False)), bool(a.get("closed", False)))
        except Exception as e:
            raise ToolError("failed", f"The loft failed ({e}): profiles each closed (or each open), in order, not crossing; the same number of edges helps.")
        return finish(res, a, "part_loft", srcs)

    def part_sweep(a):
        sh, src = profile(s, a.get("profile"), a.get("body"))
        if sh.ShapeType == "Face":
            sh = sh.OuterWire
        path_spec = a.get("path")
        if isinstance(path_spec, dict) and "points" in path_spec:
            path_spec = {**path_spec, "closed": path_spec.get("closed", False)}
        path, psrc = profile(s, path_spec, a.get("body"), "path", closed_default=False)
        if path.ShapeType == "Face":
            path = path.OuterWire
        if path.ShapeType != "Wire":
            path = Part.Wire(Part.__sortEdges__(path.Edges))
        solid = bool(a.get("solid", True))
        try:
            res = path.makePipeShell([sh], solid, bool(a.get("frenet", False)), {"none": 0, "right": 1, "round": 2}.get(a.get("corners", "right"), 1))
        except Exception as e:
            raise ToolError("failed", f"The sweep failed ({e}): the profile at the path's start, across it; a path without sharp turns tighter than the profile.")
        if solid and not res.Solids:
            raise ToolError("failed", "The sweep made no solid: a closed profile.")
        return finish(res, a, "part_sweep", [src, psrc])

    placement_in = {"at": {"type": "array", "items": {"type": "number"}}, "rotate": {"type": "object", "description": "{ axis: [x,y,z], angle: degrees } about at"}}
    return [
        Tool(
            "part_primitive",
            "Add a FreeCAD primitive as a body: box { size [x,y,z] } (a corner at at), cylinder { radius, height }, cone { radius, radius2 (top), height }, prism { sides, radius (to a corner), height } and tube { radius, radius2 (bore), height } (base centred at at, up +z), sphere { radius } and torus { radius, radius2 } (centred), wedge { wedge: { xmin … z2max } as FreeCAD's Part Wedge }. at [x,y,z] and rotate { axis, angle } place it. Parametric: object_set changes a size later (Length, Radius …) until a boolean bakes it.",
            obj({"kind": {"type": "string", "enum": list(PRIMS)}, "size": {"type": "array", "items": {"type": "number"}}, "radius": {"type": "number"}, "radius2": {"type": "number"}, "height": {"type": "number"}, "sides": {"type": "integer"}, "wedge": {"type": "object"}, **placement_in, "name": {"type": "string"}}, ["kind"]),
            part_primitive,
            changes=True,
        ),
        Tool(
            "part_boolean",
            "fuse, cut or common base with tools (bodies). The result keeps base's name (name: a new one) as a plain shape, coplanar faces merged; the tools are removed (keep: true hides them — a hidden body is still a body to the other tools). Refused when nothing would change (not touching, or inside for fuse) and for common with no overlap. Answers whether the result is sound (no sliver faces).",
            obj({"op": {"type": "string", "enum": ["fuse", "cut", "common"]}, "base": body, "tools": {"description": "A body or a list of them."}, "keep": {"type": "boolean"}, "name": {"type": "string"}}, ["op", "tools"]),
            part_boolean,
            changes=True,
        ),
        Tool(
            "part_transform",
            "Move or copy a body: move { by [dx,dy,dz] }, rotate { axis, angle degrees, about [x,y,z] (its box centre by default) } — in place, copy: true for a new body; mirror { normal, about (box centre) } and scale { factor or [fx,fy,fz], about (origin) } make a new body (mirror fuse: true joins the mirror to the original); linear { count, step [dx,dy,dz] } and polar { count, axis, about, angle (360 shares out evenly) } array the body into itself, fused (fuse: false keeps the copies apart in one body). Trap: face numbers change after this; shape_query again.",
            obj({"op": {"type": "string", "enum": ["move", "rotate", "mirror", "scale", "linear", "polar"]}, "body": body, "by": {"type": "array", "items": {"type": "number"}}, "axis": {}, "angle": {"type": "number"}, "about": {"type": "array", "items": {"type": "number"}}, "normal": {}, "factor": {}, "count": {"type": "integer"}, "step": {"type": "array", "items": {"type": "number"}}, "copy": {"type": "boolean"}, "fuse": {"type": "boolean"}, "name": {"type": "string"}}, ["op"]),
            part_transform,
            changes=True,
        ),
        Tool(
            "part_fillet",
            "Round edges of a body: radius mm; edges [\"Edge3\", \"Face6\" (all its edges) …] or \"all\", and/or query { type, axis, radius, near, inside, of: \"Face6\" … } as shape_query takes for edges. Refused with a broken result: a radius larger than a face beside the edge allows. Trap: fillet last — a later boolean renumbers edges and may not keep the round tidy.",
            obj({"body": body, "radius": {"type": "number"}, "edges": {}, "query": {"type": "object"}, "name": {"type": "string"}}, ["radius"]),
            round_edges("fillet"),
            changes=True,
        ),
        Tool(
            "part_chamfer",
            "Bevel edges of a body by size mm, the edges picked as part_fillet picks them (edges refs, \"all\", or query).",
            obj({"body": body, "size": {"type": "number"}, "edges": {}, "query": {"type": "object"}, "name": {"type": "string"}}, ["size"]),
            round_edges("chamfer"),
            changes=True,
        ),
        Tool(
            "part_offset",
            "Grow (+) or shrink (-) a solid by distance mm all round; join arc (rounded corners, default) or intersection (sharp). Refused when it leaves no solid (shrunk past half the thinnest part).",
            obj({"body": body, "distance": {"type": "number"}, "join": {"type": "string", "enum": ["arc", "intersection"]}, "name": {"type": "string"}}, ["distance"]),
            part_offset,
            changes=True,
        ),
        Tool(
            "part_thickness",
            "Shell a solid: open the faces named (open [\"Face6\"] or query { normal: \"+z\", sort: \"-z\", limit: 1 }) and leave walls of thickness mm, inward by default (inward: false grows outward). join intersection (sharp, default) or arc. Trap: fillets smaller than the wall break it — shell first, or a thinner wall.",
            obj({"body": body, "thickness": {"type": "number"}, "open": {"type": "array", "items": {"type": "string"}}, "query": {"type": "object"}, "inward": {"type": "boolean"}, "join": {"type": "string", "enum": ["arc", "intersection"]}, "name": {"type": "string"}}, ["thickness"]),
            part_thickness,
            changes=True,
        ),
        Tool(
            "part_extrude",
            "Extrude a profile into a solid: length along its normal (a face's outward one; +z for a flat outline), or direction [x,y,z] (its length, or length given); symmetric splits it both ways. op new (default without into) | fuse (default with into) | cut | common with into: a boss or a pocket in one call. Refused when the cut or fuse misses into. solid: false gives a surface from an open profile. The source sketch is hidden.",
            obj({"profile": prof, "body": body, "length": {"type": "number"}, "direction": {"type": "array", "items": {"type": "number"}}, "symmetric": {"type": "boolean"}, "solid": {"type": "boolean"}, **common_in}, ["profile"]),
            part_extrude,
            changes=True,
        ),
        Tool(
            "part_revolve",
            "Revolve a profile about an axis: axis [x,y,z] (+z) through at [x,y,z] (origin), angle degrees (360). The profile must not cross the axis. op / into as part_extrude.",
            obj({"profile": prof, "body": body, "axis": {}, "at": {"type": "array", "items": {"type": "number"}}, "angle": {"type": "number"}, "solid": {"type": "boolean"}, **common_in}, ["profile"]),
            part_revolve,
            changes=True,
        ),
        Tool(
            "part_loft",
            "A solid through two or more profiles in order (each a sketch, face, { points } or { circle }); ruled: straight between them; closed: back to the first. Trap: profiles with the same number of edges, started at matching corners, loft without a twist. op / into as part_extrude.",
            obj({"profiles": {"type": "array"}, "body": body, "ruled": {"type": "boolean"}, "closed": {"type": "boolean"}, "solid": {"type": "boolean"}, **common_in}, ["profiles"]),
            part_loft,
            changes=True,
        ),
        Tool(
            "part_sweep",
            "Sweep a closed profile along a path (a wire or sketch, an edge \"Box.Edge3\", or { points } — open by default): frenet: true keeps a helix's profile upright; corners right (default), round or none at sharp turns. Trap: put the profile at the path's start, across it. op / into as part_extrude.",
            obj({"profile": prof, "path": prof, "body": body, "frenet": {"type": "boolean"}, "corners": {"type": "string", "enum": ["right", "round", "none"]}, "solid": {"type": "boolean"}, **common_in}, ["profile", "path"]),
            part_sweep,
            changes=True,
        ),
    ]
