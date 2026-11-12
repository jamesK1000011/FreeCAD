# SPDX-License-Identifier: LGPL-2.1-or-later
# More Sketcher, beside sketch.py: copy, move, rotate and scale geometry,
# rectangular and polar arrays, extend a curve, join two, a carbon copy of
# another sketch, edges projected from binders and other sketches, B-spline
# conversion, and validate — the Sketcher's own repair: missing
# coincidences, degenerate geometry, redundant and invalid constraints,
# and its auto-constrainer.

import math

import FreeCAD
import Part

from ..protocol import Tool, ToolError
from . import pd_common as pc

GROUP = "sketch"
INFO = ("Sketches: bodies, sketches on planes and faces, geometry and constraints, offset, external edges, fillet/trim/split/mirror, "
        "copy/move/rotate/scale, arrays, extend, join, carbon copy, projection from binders, B-spline conversion, validate and repair, the solver's state.")

V = FreeCAD.Vector


def obj(props, required=()):
    return {"type": "object", "properties": props, "required": list(required), "additionalProperties": False}


def bad(msg):
    return ToolError("invalid_input", msg)


def ids_of(sk, geos, what="geometry"):
    n = len(sk.Geometry)
    if geos is None:
        return [i for i in range(n) if not sk.getConstruction(i)]
    if not isinstance(geos, list) or not geos or not all(isinstance(g, int) and not isinstance(g, bool) for g in geos):
        raise bad(f"{what}: [geometry ids] — sketch_state lists them.")
    out = [g for g in geos if 0 <= g < n]
    if len(out) != len(geos):
        raise bad(f"{what}: ids 0 … {n - 1} in {sk.Label}.")
    return out


def state(sk):
    try:
        code = sk.solve()
    except Exception:
        code = -1
    out = {"sketch": sk.Label, "geometry": len(sk.Geometry), "constraints": len(sk.Constraints), "solver": "solved" if code == 0 else f"code {code}"}
    for k, p in (("dof", "DoF"), ("conflicting", "Conflicting"), ("redundant", "Redundant"), ("malformed", "MalformedConstraints")):
        v = getattr(sk, p, None)
        if v not in (None, [], ()):
            out[k] = list(v) if isinstance(v, (list, tuple)) else v
    try:
        w = sk.Shape.Wires
        out["wires"] = len(w)
        out["closedWires"] = sum(1 for x in w if x.isClosed())
    except Exception:
        pass
    return out


def tools(session):
    s = session
    doc = lambda: s.doc  # noqa: E731

    def sketch(a):
        return pc.sketch_of(doc(), a.get("sketch"), None)

    def kept(sk):
        """The sketch as it is, to put back if a change fails: some Sketcher
        calls change it outside the document's undo."""
        return ([g.copy() for g in sk.Geometry], [sk.getConstruction(i) for i in range(len(sk.Geometry))], [c.clone() if hasattr(c, "clone") else c for c in sk.Constraints])

    def restore(sk, snap):
        geo, cons_flags, cons = snap
        try:
            sk.deleteAllConstraints()
            sk.deleteAllGeometry()
            if geo:
                sk.addGeometry(geo, False)
                for i, f in enumerate(cons_flags):
                    if f:
                        sk.setConstruction(i, True)
            if cons:
                sk.addConstraint(cons)
            doc().recompute()
        except Exception:
            pass

    def done(sk, tool, extra=None):
        doc().recompute()
        b = pc.broken(doc())
        if b:
            raise ToolError("failed", f"That breaks {b[0]['object']}: {b[0]['error']} — nothing was kept.")
        s.snapshot(tool)
        return {**state(sk), **(extra or {}), "step": len(s.steps)}

    def new_ids(sk, before):
        return list(range(before, len(sk.Geometry)))

    # ---- copy, move, rotate, scale ----

    def transformed_copies(sk, geos, m):
        out = []
        for g in geos:
            gg = sk.Geometry[g].copy()
            gg.transform(m)
            out.append(gg)
        return out

    def sketch_copy(a):
        sk = sketch(a)
        geos = ids_of(sk, a.get("geometry"))
        before = len(sk.Geometry)
        keep = not a.get("move")
        if a.get("by") is not None:
            d = pc.vec2(a["by"], "by")
            v = V(d[0], d[1], 0)
            if keep:
                sk.addCopy(geos, v, bool(a.get("clone")))
            else:
                sk.addMove(geos, v)
            what = f"by {[d.x, d.y]}"
        elif a.get("rotate") is not None or a.get("scale") is not None:
            if a.get("rotate") is not None:
                r = a["rotate"]
                c = pc.vec2(r.get("center", [0, 0]), "rotate.center")
                ang = float(r.get("angle", 90))
                m = FreeCAD.Placement(V(), FreeCAD.Rotation(V(0, 0, 1), ang), V(c[0], c[1], 0)).toMatrix()
                what = f"rotated {ang:g}° about {[c.x, c.y]}"
            else:
                sc = a["scale"]
                c = pc.vec2(sc.get("center", [0, 0]), "scale.center")
                f = float(sc.get("factor", 2))
                if f <= 0:
                    raise bad("scale.factor: above 0.")
                m = FreeCAD.Matrix()
                m.move(V(-c[0], -c[1], 0))
                m2 = FreeCAD.Matrix()
                m2.scale(f, f, 1)
                m = m2.multiply(m)
                m3 = FreeCAD.Matrix()
                m3.move(V(c[0], c[1], 0))
                m = m3.multiply(m)
                what = f"scaled ×{f:g} about {[c.x, c.y]}"
            copies = transformed_copies(sk, geos, m)
            sk.addGeometry(copies, False)
            if not keep:
                sk.delGeometries(sorted(geos))
                before -= len(geos)
        else:
            raise bad("by [dx, dy], rotate { center, angle }, or scale { center, factor }.")
        made = new_ids(sk, before) if keep else "the originals, moved (ids renumbered when rotated or scaled)"
        return done(sk, "sketch_copy", {"did": ("copied " if keep else "moved ") + what, "new": made})

    def sketch_array(a):
        sk = sketch(a)
        geos = ids_of(sk, a.get("geometry"))
        before = len(sk.Geometry)
        if a.get("polar"):
            p = a["polar"]
            c = pc.vec2(p.get("center", [0, 0]), "polar.center")
            n = int(p.get("count", 6))
            span = float(p.get("angle", 360))
            if n < 2:
                raise bad("polar.count: 2 or more, the original counted.")
            step = span / n if abs(span - 360) < 1e-9 else span / (n - 1)
            for k in range(1, n):
                m = FreeCAD.Placement(V(), FreeCAD.Rotation(V(0, 0, 1), step * k), V(c[0], c[1], 0)).toMatrix()
                sk.addGeometry(transformed_copies(sk, geos, m), False)
            what = f"{n} round {[c.x, c.y]} over {span:g}°"
        else:
            rows, cols = int(a.get("rows", 1)), int(a.get("cols", 2))
            if rows < 1 or cols < 1 or rows * cols < 2:
                raise bad("rows and cols: at least two copies in all.")
            d = pc.vec2(a.get("spacing", [10, 10]), "spacing")
            # FreeCAD's rows run along the vector's perpendicular, scaled by perpscale
            ratio = (d[1] / d[0]) if abs(d[0]) > 1e-9 else 1.0
            sk.addRectangularArray(geos, V(d[0], 0, 0) if abs(d[0]) > 1e-9 else V(0, d[1], 0), bool(a.get("clone")), rows, cols, bool(a.get("constrain")), ratio if abs(d[0]) > 1e-9 else 1.0)
            what = f"{rows} × {cols} at {[d.x, d.y]}"
        return done(sk, "sketch_array", {"did": what, "new": new_ids(sk, before)})

    # ---- extend, join ----

    def sketch_extend(a):
        sk = sketch(a)
        g = a.get("geo")
        if not isinstance(g, int) or not 0 <= g < len(sk.Geometry):
            raise bad("geo: a line's or arc's id.")
        end = int(a.get("end", 2))
        if end not in (1, 2):
            raise bad("end: 1 (start) or 2 (end).")
        if a.get("to") is not None:
            # as far as another curve: measured along the line to where it meets it
            tgt = sk.Geometry[int(a["to"])].toShape()
            p = sk.getPoint(g, end)
            other = sk.getPoint(g, 1 if end == 2 else 2)
            dvec = p - other
            dvec.normalize()
            far = Part.LineSegment(p, p + dvec * 10000).toShape()
            dist, pts, _ = far.distToShape(tgt)
            if dist > 1e-6:
                raise ToolError("not_found", f"geometry {g} does not reach {a['to']} going that way.")
            inc = (pts[0][0] - p).Length
        else:
            inc = float(a.get("by", 0))
            if inc == 0:
                raise bad("by (mm, negative shortens) or to: the id of a curve to reach.")
        sk.extend(g, inc, end)
        return done(sk, "sketch_extend", {"extended": g, "by": round(inc, 4)})

    def sketch_join(a):
        sk = sketch(a)
        x, y = a.get("a"), a.get("b")
        if not (isinstance(x, list) and isinstance(y, list) and len(x) == len(y) == 2):
            raise bad("a and b: [geo, vertex] each — the two ends to join into one B-spline.")
        try:
            sk.join(int(x[0]), int(x[1]), int(y[0]), int(y[1]), int(a.get("continuity", 0)))
        except Exception as e:
            raise ToolError("failed", f"The join failed: {e} — both ends must be curves' ends that meet.")
        return done(sk, "sketch_join", {"joined": [x, y]})

    # ---- carbon copy, projection ----

    def sketch_carbon(a):
        sk = sketch(a)
        src = pc.sketch_of(doc(), a.get("from"), None)
        if src is sk:
            raise bad("from: another sketch.")
        before = len(sk.Geometry)
        try:
            sk.carbonCopy(src.Name, bool(a.get("construction", False)))
        except Exception as e:
            raise ToolError("failed", f"No carbon copy from {src.Label}: {e} — the sketches must be parallel, in the same body (or allow cross-links).")
        return done(sk, "sketch_carbon", {"from": src.Label, "new": new_ids(sk, before), "note": "Geometry and constraints copied; the copy stays linked to its source's values by expression."})

    def sketch_project(a):
        sk = sketch(a)
        src = next((o for o in doc().Objects if a.get("from") in (o.Label, o.Name)), None)
        if src is None or not hasattr(src, "Shape"):
            raise ToolError("not_found", f"from {a.get('from')!r}: a binder (pd_binder), a datum or another sketch in this body.")
        edges = a.get("edges")
        names = []
        if edges:
            for e in edges:
                if isinstance(e, str) and e.startswith(("Edge", "Vertex")):
                    names.append(e)
                elif isinstance(e, dict) and "near" in e:
                    names += pc.find_edges(src.Shape, [e])
                else:
                    raise bad("edges: \"Edge3\" or { near: [x, y, z], kind } each; left out, every edge.")
        else:
            names = [f"Edge{i + 1}" for i in range(len(src.Shape.Edges))]
        if not names:
            raise bad(f"{src.Label} has no edges.")
        before = len(sk.ExternalGeometry) if hasattr(sk, "ExternalGeometry") else 0
        added = 0
        for n in names:
            try:
                if a.get("defining"):
                    sk.addExternal(src.Name, n, True)
                else:
                    sk.addExternal(src.Name, n)
                added += 1
            except Exception as e:
                raise ToolError("failed", f"{src.Label}.{n} would not come in: {e} — from another body, bring it in with pd_binder first.")
        out = done(sk, "sketch_project", {"from": src.Label, "edges": added, "ids": [-(3 + before + i) for i in range(added)],
                                          "note": "External ids count down from -3; constrain to them with sketch_constrain. defining: true makes them part of the profile (where this FreeCAD allows)."})
        return out

    def sketch_to_bspline(a):
        sk = sketch(a)
        geos = ids_of(sk, a.get("geometry"))
        for g in sorted(geos, reverse=True):
            try:
                sk.convertToNURBS(g)
            except Exception as e:
                raise ToolError("failed", f"geometry {g} would not convert: {e}")
        return done(sk, "sketch_to_bspline", {"converted": geos})

    # ---- validate and repair ----

    def sketch_validate(a):
        sk = sketch(a)
        prec = float(a.get("precision", 0.01))
        found = {}
        try:
            found["degenerate"] = int(sk.detectDegeneratedGeometries(prec))
        except Exception:
            pass
        try:
            found["missingCoincidences"] = int(sk.detectMissingPointOnPointConstraints(prec, False) or 0)
        except Exception:
            try:
                sk.detectMissingPointOnPointConstraints(prec)
                found["missingCoincidences"] = len(sk.MissingPointOnPointConstraints) if hasattr(sk, "MissingPointOnPointConstraints") else None
            except Exception:
                pass
        try:
            # True when they are all valid, whatever its doc string says
            found["invalidConstraints"] = not bool(sk.evaluateConstraints())
        except Exception:
            pass
        st = state(sk)
        for k in ("redundant", "conflicting", "malformed"):
            if st.get(k):
                found[k] = st[k]
        fixed = []
        if a.get("fix"):
            snap = kept(sk)
            try:
                return repair(sk, prec, found, st, fixed, a)
            except ToolError:
                restore(sk, snap)
                raise
            except Exception as e:
                restore(sk, snap)
                raise ToolError("failed", f"Repair stopped: {e} — the sketch is as it was. Close the gaps by hand (sketch_constrain coincident), or drop autoconstrain.")
        out = {**st, "found": found, "next": "fix: true repairs what can be (autoconstrain: true adds coincident/horizontal/vertical where the geometry already is)."}
        return out

    def repair(sk, prec, found, st, fixed, a):
        if found.get("degenerate"):
            sk.removeDegeneratedGeometries(prec)
            fixed.append("degenerate geometry removed")
        if found.get("missingCoincidences"):
            try:
                sk.makeMissingPointOnPointCoincident(False)
                fixed.append("missing coincidences added")
            except Exception as e:
                fixed.append(f"coincidences not added: {e}")
        if found.get("invalidConstraints"):
            sk.validateConstraints()
            fixed.append("invalid constraints removed")
        if st.get("redundant"):
            sk.autoRemoveRedundants(True)
            fixed.append("redundant constraints removed")
        if a.get("autoconstrain"):
            # the auto-constrainer's steps one by one, leaving out equality
            # (FreeCAD's whole run can fail there and stop the rest)
            n0 = len(sk.Constraints)
            sk.detectMissingPointOnPointConstraints(prec, False)
            sk.makeMissingPointOnPointCoincident(True)
            sk.detectMissingVerticalHorizontalConstraints(math.radians(float(a.get("angle", 1))))
            sk.makeMissingVerticalHorizontal(True)
            fixed.append(f"{len(sk.Constraints) - n0} coincident / horizontal / vertical constraints added")
        sk.solve()
        if getattr(sk, "Redundant", None):
            sk.autoRemoveRedundants(True)
            fixed.append("redundant constraints removed")
        if getattr(sk, "Conflicting", None):
            raise ToolError("failed", f"Constraints {list(sk.Conflicting)} conflict after the repair — nothing was kept; sketch_edit deleteConstraints the one that is wrong.")
        return done(sk, "sketch_validate", {"found": found, "fixed": fixed})

    geo_ids = {"type": "array", "items": {"type": "integer"}}
    sk_ref = {"type": "string", "description": "The sketch's name; may be left out when there is one."}
    # ---- B-spline editing, merging, mirroring, attachment ----

    def sketch_bspline(a):
        sk = sketch(a)
        g = a.get("geo")
        if not isinstance(g, int) or not 0 <= g < len(sk.Geometry) or "BSpline" not in type(sk.Geometry[g]).__name__:
            raise bad("geo: a B-spline's id (sketch_to_bspline converts other curves).")
        op = a.get("op")
        snap = kept(sk)
        try:
            if op == "degree":
                by = int(a.get("by", 1))
                if by > 0:
                    sk.increaseBSplineDegree(g, by)
                elif by < 0:
                    if not hasattr(sk, "decreaseBSplineDegree"):
                        raise ToolError("unavailable", "This FreeCAD cannot lower a B-spline's degree.")
                    sk.decreaseBSplineDegree(g, -by)
            elif op == "knot":
                if a.get("param") is not None:
                    sk.insertBSplineKnot(g, float(a["param"]), int(a.get("multiplicity", 1)))
                else:
                    sk.modifyBSplineKnotMultiplicity(g, int(a.get("knot", 1)), int(a.get("by", 1)))
            else:
                raise bad("op: degree (by +1 / -1) | knot (param: insert one there, multiplicity; or knot index, by: its multiplicity up or down).")
        except ToolError:
            raise
        except Exception as e:
            restore(sk, snap)
            raise ToolError("failed", f"Sketcher refused: {e}") from None
        doc().recompute()
        bs = [x for x in sk.Geometry if "BSpline" in type(x).__name__]
        geo = sk.Geometry[g] if g < len(sk.Geometry) and "BSpline" in type(sk.Geometry[g]).__name__ else bs[-1]
        return done(sk, "sketch_bspline", {"geo": g, "degree": int(geo.Degree), "poles": int(geo.NbPoles), "knots": [round(k, 4) for k in geo.getKnots()],
                                           "multiplicities": list(geo.getMultiplicities())})

    def copy_frame(src, sk):
        sup = pc.support_prop(src)
        if getattr(src, sup, None):
            setattr(sk, pc.support_prop(sk), getattr(src, sup))
            sk.MapMode = src.MapMode
            sk.MapReversed = src.MapReversed
            sk.AttachmentOffset = src.AttachmentOffset
        else:
            sk.Placement = src.Placement

    def new_beside(src):
        body = next((p for p in src.InList if p.TypeId == "PartDesign::Body"), None)
        return body.newObject("Sketcher::SketchObject", "Sketch") if body else doc().addObject("Sketcher::SketchObject", "Sketch")

    def sketch_merge(a):
        refs = a.get("sketches")
        if not isinstance(refs, list) or len(refs) < 2:
            raise bad("sketches: two or more to merge into a new one (on the first one's plane).")
        srcs = [pc.sketch_of(doc(), r, None) for r in refs]
        first = srcs[0]
        sk = new_beside(first)
        copy_frame(first, sk)
        sk.Label = s.unique(a.get("name") or "Merged sketch")
        inv = first.Placement.inverse()
        for src in srcs:
            base = len(sk.Geometry)
            m = inv.multiply(src.Placement).toMatrix()
            gs = []
            for gg in src.Geometry:
                c = gg.copy()
                c.transform(m)
                gs.append(c)
            if gs:
                sk.addGeometry(gs, False)
                for i in range(len(gs)):
                    if src.getConstruction(i):
                        sk.setConstruction(base + i, True)
            for c in src.Constraints:
                try:
                    cc = c.clone() if hasattr(c, "clone") else c
                    if cc.First >= 0:
                        cc.First += base
                    if getattr(cc, "Second", -2000) >= 0:
                        cc.Second += base
                    if getattr(cc, "Third", -2000) >= 0:
                        cc.Third += base
                    sk.addConstraint(cc)
                except Exception:
                    pass
        if a.get("remove"):
            for src in srcs:
                doc().removeObject(src.Name)
        return done(sk, "sketch_merge", {"sketch": sk.Label, "from": [x.Label for x in srcs]})

    def sketch_mirror(a):
        src = sketch(a)
        about = a.get("about", "y")
        M = FreeCAD.Matrix()
        if about == "y":
            M.A11 = -1
        elif about == "x":
            M.A22 = -1
        elif about == "origin":
            M.A11 = M.A22 = -1
        else:
            raise bad("about: x (flip over the x axis) | y | origin.")
        sk = new_beside(src)
        copy_frame(src, sk)
        gs = []
        for gg in src.Geometry:
            c = gg.copy()
            c.transform(M)
            gs.append(c)
        if gs:
            sk.addGeometry(gs, False)
            for i in range(len(gs)):
                if src.getConstruction(i):
                    sk.setConstruction(i, True)
        sk.Label = s.unique(a.get("name") or f"{src.Label} mirrored")
        return done(sk, "sketch_mirror", {"sketch": sk.Label, "from": src.Label, "about": about, "note": "Geometry only: constrain the copy (sketch_validate autoconstrain) to edit it by sizes."})

    def sketch_attach(a):
        sk = sketch(a)
        b = next((p for p in sk.InList if p.TypeId == "PartDesign::Body"), None)
        if b is None:
            raise ToolError("invalid_state", f"{sk.Label} is in no body: attach works on a body's sketch.")
        if ("plane" in a) == ("face" in a):
            raise bad("plane (XY, XZ, YZ, a datum plane) or face { at, normal }: one.")
        link, what = pc.plane_ref(b, a["plane"] if "plane" in a else a["face"], "plane")
        setattr(sk, pc.support_prop(sk), [(link[0], link[1][0])])
        sk.MapMode = "FlatFace"
        sk.MapReversed = bool(a.get("flip", False))
        off = float(a.get("offset", 0))
        rot = float(a.get("rotate", 0))
        sk.AttachmentOffset = FreeCAD.Placement(V(0, 0, off), FreeCAD.Rotation(V(0, 0, 1), rot))
        doc().recompute()
        if not sk.isValid():
            raise ToolError("failed", f"{sk.Label} would not attach to {what}: {sk.getStatusString()}.")
        return done(sk, "sketch_attach", {"sketch": sk.Label, "on": what, "offset": off, "rotate": rot})

    return [
        Tool("sketch_copy", "Copy geometry in a sketch (move: true moves it instead): geometry [ids] (default every non-construction one), and by [dx, dy] (clone: true keeps copies equal to the originals), rotate { center, angle } or scale { center, factor }.",
             obj({"sketch": sk_ref, "geometry": geo_ids, "by": {"type": "array"}, "rotate": {"type": "object"}, "scale": {"type": "object"}, "move": {"type": "boolean"}, "clone": {"type": "boolean"}}), sketch_copy, changes=True),
        Tool("sketch_array", "An array of geometry in a sketch: rows, cols and spacing [dx, dy] (constrain: true keeps the spacing as constraints; clone: true keeps copies equal), or polar { center, count, angle }.",
             obj({"sketch": sk_ref, "geometry": geo_ids, "rows": {"type": "integer"}, "cols": {"type": "integer"}, "spacing": {"type": "array"}, "constrain": {"type": "boolean"}, "clone": {"type": "boolean"}, "polar": {"type": "object"}}), sketch_array, changes=True),
        Tool("sketch_extend", "Lengthen (or shorten) a line or arc at one end: geo, end 1 | 2, by mm, or to: another curve's id it should reach.",
             obj({"sketch": sk_ref, "geo": {"type": "integer"}, "end": {"type": "integer"}, "by": {"type": "number"}, "to": {"type": "integer"}}, ["geo"]), sketch_extend, changes=True),
        Tool("sketch_join", "Join two curves' ends into one B-spline: a [geo, vertex], b [geo, vertex], continuity 0 (corner) or 1 (smooth).",
             obj({"sketch": sk_ref, "a": {"type": "array"}, "b": {"type": "array"}, "continuity": {"type": "integer"}}, ["a", "b"]), sketch_join, changes=True),
        Tool("sketch_carbon", "Copy another sketch's geometry and constraints into this one (Sketcher's carbon copy): from, construction: true brings it as construction.",
             obj({"sketch": sk_ref, "from": {"type": "string"}, "construction": {"type": "boolean"}}, ["from"]), sketch_carbon, changes=True),
        Tool("sketch_project", "Bring edges of a binder, datum or another sketch into this sketch as external geometry: from, edges [\"Edge3\" | { near, kind }] (default all), defining: true makes them part of the profile.",
             obj({"sketch": sk_ref, "from": {"type": "string"}, "edges": {"type": "array"}, "defining": {"type": "boolean"}}, ["from"]), sketch_project, changes=True),
        Tool("sketch_to_bspline", "Convert geometry to B-splines (to reshape by poles, or join): geometry [ids].", obj({"sketch": sk_ref, "geometry": geo_ids}), sketch_to_bspline, changes=True),
        Tool("sketch_bspline", "Edit a B-spline in a sketch: geo, op degree (by: +1 raises, -1 lowers) | knot (param: insert a knot there, multiplicity; or knot: its index and by: raise/lower its multiplicity — a corner at 0 continuity).",
             obj({"sketch": {"type": "string"}, "geo": {"type": "integer"}, "op": {"type": "string", "enum": ["degree", "knot"]}, "by": {"type": "integer"}, "param": {"type": "number"},
                  "knot": {"type": "integer"}, "multiplicity": {"type": "integer"}}, ["geo", "op"]), sketch_bspline, changes=True),
        Tool("sketch_merge", "Merge sketches into a new one on the first's plane, geometry and constraints kept: sketches [names]; remove: true deletes the originals.",
             obj({"sketches": {"type": "array", "items": {"type": "string"}}, "remove": {"type": "boolean"}, "name": {"type": "string"}}, ["sketches"]), sketch_merge, changes=True),
        Tool("sketch_mirror", "A mirrored copy of a whole sketch as a new sketch on the same plane: about y (left-right) | x (up-down) | origin.",
             obj({"sketch": {"type": "string"}, "about": {"type": "string", "enum": ["x", "y", "origin"]}, "name": {"type": "string"}}), sketch_mirror, changes=True),
        Tool("sketch_attach", "Move a sketch onto another plane (Sketcher's map/reorient): plane XY | XZ | YZ | a datum plane, or face { at, normal }; offset mm along its normal, rotate deg in it, flip.",
             obj({"sketch": {"type": "string"}, "plane": {"type": "string"}, "face": {"type": "object"}, "offset": {"type": "number"}, "rotate": {"type": "number"}, "flip": {"type": "boolean"}}), sketch_attach, changes=True),
        Tool("sketch_validate", "Check a sketch the way Sketcher's Validate does: degenerate geometry, ends that touch with no coincidence, invalid, redundant and conflicting constraints (precision mm). fix: true repairs what it can; autoconstrain: true also adds the obvious constraints.",
             obj({"sketch": sk_ref, "precision": {"type": "number"}, "fix": {"type": "boolean"}, "autoconstrain": {"type": "boolean"}, "angle": {"type": "number"}}), sketch_validate, changes=True),
    ]
