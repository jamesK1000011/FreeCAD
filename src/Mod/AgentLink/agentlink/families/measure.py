# SPDX-License-Identifier: LGPL-2.1-or-later
# Reading the geometry: faces and edges found by what they are (the
# references the modelling tools take), measurements between them, a
# section through a body, mass properties and the clearance between two
# bodies. Nothing here changes the document.

import math

import Part

from .. import geometry as geo
from .. import store
from ..protocol import Tool, ToolError
from ..tools import obj
from . import picks

GROUP = "measure"
INFO = "Find faces and edges (the references other tools take), measure between them, sections, mass properties, clearance."

SURFACES = {"Plane": "plane", "Cylinder": "cylinder", "Cone": "cone", "Sphere": "sphere", "Toroid": "torus", "BSplineSurface": "bspline", "BezierSurface": "bezier", "SurfaceOfRevolution": "revolution", "SurfaceOfExtrusion": "extrusion", "OffsetSurface": "offset"}
CURVES = {"Line": "line", "LineSegment": "line", "Circle": "circle", "Ellipse": "ellipse", "BSplineCurve": "bspline", "BezierCurve": "bezier", "Hyperbola": "hyperbola", "Parabola": "parabola", "OffsetCurve": "offset"}
KIND_ATTR = {"face": "Faces", "edge": "Edges", "vertex": "Vertexes"}


def r(x, n=3):
    return round(float(x), n) + 0.0


def surface_type(f):
    try:
        return SURFACES.get(type(f.Surface).__name__, type(f.Surface).__name__.lower())
    except Exception:
        return "unknown"


def curve_type(e):
    try:
        return CURVES.get(type(e.Curve).__name__, type(e.Curve).__name__.lower())
    except Exception:
        return "unknown"


def face_normal(f, at=None):
    """The outward normal (the face's orientation counted) at the point of
    the face nearest `at`, its centroid by default."""
    p = at if at is not None else f.CenterOfMass
    try:
        u, v = f.Surface.parameter(p)
        n = f.normalAt(u, v)
    except Exception:
        u0, u1, v0, v1 = f.ParameterRange
        n = f.normalAt((u0 + u1) / 2, (v0 + v1) / 2)
    return n.normalize() if n.Length > 1e-12 else n


def line_dir(e):
    d = e.Vertexes[-1].Point - e.Vertexes[0].Point if len(e.Vertexes) > 1 else e.tangentAt(e.FirstParameter)
    return d.normalize() if d.Length > 1e-12 else d


def describe_face(f, i):
    t = surface_type(f)
    out = {"ref": f"Face{i}", "type": t, "area": r(f.Area, 2), "centroid": geo.vec(f.CenterOfMass)}
    s = f.Surface
    if t == "plane":
        out["normal"] = geo.vec(face_normal(f))
    elif t == "cylinder":
        out.update(radius=r(s.Radius), axis=geo.vec(s.Axis), center=geo.vec(s.Center))
    elif t == "cone":
        out.update(semiAngle=r(math.degrees(s.SemiAngle), 3), axis=geo.vec(s.Axis), apex=geo.vec(s.Apex))
    elif t == "sphere":
        out.update(radius=r(s.Radius), center=geo.vec(s.Center))
    elif t == "torus":
        out.update(radius=r(s.MajorRadius), minorRadius=r(s.MinorRadius), axis=geo.vec(s.Axis), center=geo.vec(s.Center))
    return out


def describe_edge(e, i):
    t = curve_type(e)
    mid = e.valueAt((e.FirstParameter + e.LastParameter) / 2)
    out = {"ref": f"Edge{i}", "type": t, "length": r(e.Length), "mid": geo.vec(mid)}
    if t == "line":
        out["dir"] = geo.vec(line_dir(e))
    elif t in ("circle", "ellipse"):
        c = e.Curve
        out.update(center=geo.vec(c.Location), axis=geo.vec(c.Axis))
        if t == "circle":
            out["radius"] = r(c.Radius)
            out["closed"] = e.isClosed()
    return out


def rng(v, name):
    if v is None:
        return None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        tol = max(1e-3, abs(v) * 1e-4)
        return (v - tol, v + tol)
    if isinstance(v, list) and len(v) == 2 and all(isinstance(x, (int, float)) for x in v):
        return (min(v), max(v))
    raise ToolError("invalid_input", f"{name}: a number or [least, most].")


def query(session, a, default_kind="face"):
    """The elements of a body that pass every filter asked: [(index, element, description)], and the body."""
    o = picks.solid_body(session, a.get("body")) if a.get("body") or not a.get("of") else None
    of = a.get("of")
    if of:
        label, sub, el = picks.resolve(session, of, a.get("body"))
        if not sub or not sub.startswith("Face"):
            raise ToolError("invalid_input", "of: a face (\"Face6\" or \"Box.Face6\") whose edges or vertices to take.")
        o = picks.shaped(session.doc, label)
    shape = o.Shape
    kind = (a.get("kind") or default_kind).lower()
    if kind not in KIND_ATTR:
        raise ToolError("invalid_input", "kind: face, edge or vertex.")
    types = a.get("type")
    if isinstance(types, str):
        types = [types]
    within = picks.num(a.get("within", 1.0), "within")
    normal = picks.direction(a["normal"], "normal") if a.get("normal") is not None else None
    axis = picks.direction(a["axis"], "axis") if a.get("axis") is not None else None
    radius = rng(a.get("radius"), "radius")
    area = rng(a.get("area"), "area")
    length = rng(a.get("length"), "length")
    near = a.get("near")
    near_p, near_d = None, None
    if near is not None:
        if isinstance(near, dict):
            near_p, near_d = picks.vec3(near.get("point"), "near.point"), picks.num(near.get("within", 1e9), "near.within")
        else:
            near_p, near_d = picks.vec3(near, "near"), 1e9
    box = a.get("inside")
    if box is not None:
        if not (isinstance(box, dict) and "min" in box and "max" in box):
            raise ToolError("invalid_input", "inside: { min: [x,y,z], max: [x,y,z] } — the element's centre in that box.")
        lo, hi = picks.vec3(box["min"], "inside.min"), picks.vec3(box["max"], "inside.max")
    cos_w = math.cos(math.radians(within))
    pool = getattr(shape, KIND_ATTR[kind])
    if of:
        pool_of = getattr(el, KIND_ATTR[kind])
        pool = [x for x in pool if any(x.isSame(y) for y in pool_of)]
    out = []
    for x in pool:
        i = picks.index_of(shape, x, KIND_ATTR[kind])
        if kind == "face":
            d = describe_face(x, i)
            centre = x.CenterOfMass
        elif kind == "edge":
            d = describe_edge(x, i)
            centre = picks.V(*d["mid"])
        else:
            d = {"ref": f"Vertex{i}", "at": geo.vec(x.Point)}
            centre = x.Point
        if types and d.get("type") not in types:
            continue
        if normal is not None:
            if kind != "face":
                raise ToolError("invalid_input", "normal: faces only — for edges, axis gives a line's direction or a circle's axis.")
            n = face_normal(x)
            if n.Length < 1e-9 or n.dot(normal) < cos_w:
                continue
            d.setdefault("normal", geo.vec(n))
        if axis is not None:
            ax = d.get("axis") or d.get("dir")
            if ax is None or abs(picks.V(*ax).normalize().dot(axis)) < cos_w:
                continue
        if radius and not ("radius" in d and radius[0] <= d["radius"] <= radius[1]):
            continue
        if area and not ("area" in d and area[0] <= d["area"] <= area[1]):
            continue
        if length and not ("length" in d and length[0] <= d["length"] <= length[1]):
            continue
        if box is not None and not (lo.x <= centre.x <= hi.x and lo.y <= centre.y <= hi.y and lo.z <= centre.z <= hi.z):
            continue
        if near_p is not None:
            dist = x.distToShape(Part.Vertex(near_p))[0]
            if dist > near_d:
                continue
            d["distance"] = r(dist)
        out.append((i, x, d))
    sort = a.get("sort")
    if sort is None and near_p is not None:
        sort = "distance"
    if sort:
        key, rev = sort.lstrip("-"), sort.startswith("-")
        if key in ("x", "y", "z"):
            out.sort(key=lambda t: (t[2].get("centroid") or t[2].get("mid") or t[2].get("at"))["xyz".index(key)], reverse=rev)
        elif key in ("area", "radius", "length", "distance"):
            out.sort(key=lambda t: t[2].get(key, math.inf if not rev else -math.inf), reverse=rev)
        else:
            raise ToolError("invalid_input", "sort: area, radius, length, distance, x, y or z; a leading - for the largest first.")
    return o, kind, out


def describe(label, sub, sh):
    if label == "point":
        return {"point": geo.vec(sh.Point)}
    if sub is None:
        return {"body": label, "volume": r(sh.Volume, 2), "area": r(sh.Area, 2), **geo.box_of(sh)}
    if sub.startswith("Face"):
        d = describe_face(sh, int(sub[4:]))
    elif sub.startswith("Edge"):
        d = describe_edge(sh, int(sub[4:]))
    else:
        d = {"ref": sub, "at": geo.vec(sh.Point)}
    d["ref"] = f"{label}.{sub}"
    if "radius" in d:
        d["diameter"] = r(2 * d["radius"])
    return d


def angle_between(u, v, undirected=False):
    c = max(-1.0, min(1.0, u.dot(v)))
    deg = math.degrees(math.acos(c))
    if undirected:
        deg = min(deg, 180 - deg)
    return round(deg, 3)


def inertia(shape, rho):
    """Mass, centre and inertia (g, mm, g·mm²) of every solid together, about the centre of mass."""
    solids = shape.Solids
    if not solids:
        raise ToolError("invalid_state", "No solid: mass properties need a closed body.")
    k = rho / 1000.0  # g/cm³ -> g/mm³
    m = sum(s.Volume for s in solids) * k
    c = picks.V()
    for s in solids:
        c += s.CenterOfMass * (s.Volume * k)
    c = c * (1.0 / m)
    I = [[0.0] * 3 for _ in range(3)]
    for s in solids:
        M = s.MatrixOfInertia
        rows = [[M.A11, M.A12, M.A13], [M.A21, M.A22, M.A23], [M.A31, M.A32, M.A33]]
        ms = s.Volume * k
        d = s.CenterOfMass - c
        dv = [d.x, d.y, d.z]
        dd = d.dot(d)
        for i in range(3):
            for j in range(3):
                I[i][j] += rows[i][j] * k + ms * ((dd if i == j else 0.0) - dv[i] * dv[j])
    return m, c, I


def tools(session):
    s = session
    body = {"type": "string", "description": "A body's name or label; may be left out when there is one."}
    ref = {"description": "\"Face12\" / \"Edge3\" (with body), \"Box.Face12\", a body's name, or a point [x, y, z]."}

    def shape_query(a):
        o, kind, hits = query(s, a)
        limit = int(a.get("limit", 20))
        return {"body": o.Label, "kind": kind, "found": len(hits), "items": [d for _, _, d in hits[:limit]], **({"more": len(hits) - limit} if len(hits) > limit else {})}

    def measure(a):
        if "a" not in a:
            raise ToolError("invalid_input", "a: what to measure — a reference or a point; b: a second one for a distance or an angle.")
        la, sa, ea = picks.resolve(s, a["a"], a.get("body"))
        da = describe(la, sa, ea)
        if a.get("b") is None:
            return {"a": da}
        lb, sb, eb = picks.resolve(s, a["b"], a.get("body"))
        db = describe(lb, sb, eb)
        dist, pairs, _ = ea.distToShape(eb)
        out = {"a": da, "b": db, "distance": r(dist), "from": geo.vec(pairs[0][0]), "to": geo.vec(pairs[0][1])}
        fa = sa and sa.startswith("Face") and da.get("type") == "plane"
        fb = sb and sb.startswith("Face") and db.get("type") == "plane"
        la_ = sa and sa.startswith("Edge") and da.get("type") == "line"
        lb_ = sb and sb.startswith("Edge") and db.get("type") == "line"
        if fa and fb:
            na, nb = picks.V(*da["normal"]), picks.V(*db["normal"])
            out["angle"] = angle_between(na, nb)
            if out["angle"] < 0.01 or out["angle"] > 179.99:
                out["gap"] = r(abs((ea.CenterOfMass - eb.CenterOfMass).dot(na)))
                out["note"] = "Parallel faces: gap is the distance between their planes; distance the least between the faces as bounded."
        elif la_ and lb_:
            out["angle"] = angle_between(picks.V(*da["dir"]), picks.V(*db["dir"]), True)
        elif (fa and lb_) or (fb and la_):
            n = picks.V(*(da if fa else db)["normal"])
            dvec = picks.V(*(db if fa else da)["dir"])
            out["angle"] = round(90 - angle_between(n, dvec, True), 3)
            out["angleIs"] = "between the line and the face's plane"
        if "center" in da and "center" in db:
            out["centers"] = r((picks.V(*da["center"]) - picks.V(*db["center"])).Length)
        return out

    def section(a):
        o = picks.solid_body(s, a.get("body"))
        n = picks.direction(a.get("normal"), "normal", (0, 0, 1))
        if a.get("at") is not None:
            p = picks.vec3(a["at"], "at")
        else:
            p = n * picks.num(a.get("offset", 0.0), "offset")
        sh = o.Shape
        bb = sh.BoundBox
        L = bb.DiagonalLength + (p - bb.Center).Length + 1.0
        plane = Part.Plane(p, n)
        cut = sh.common(plane.toShape(-L, L, -L, L))
        faces = cut.Faces
        if not faces:
            raise ToolError("not_found", f"The plane through {geo.vec(p)} normal {geo.vec(n)} misses {o.Label} (box {geo.box_of(sh)['min']} to {geo.box_of(sh)['max']}): move at.")
        tol = picks.num(a.get("tolerance", 0.05), "tolerance")
        outlines = []
        cap = int(a.get("maxPoints", 400))
        for f in faces:
            for w in f.Wires:
                pts = w.discretize(Deflection=tol)
                if len(pts) > cap:
                    pts = w.discretize(Number=cap)
                uv = [[r(x, 3) for x in plane.parameter(q)] for q in pts]
                outlines.append({"outer": w.isSame(f.OuterWire), "length": r(w.Length), "points": uv})
        u = plane.value(1, 0) - plane.value(0, 0)
        v = plane.value(0, 1) - plane.value(0, 0)
        return {
            "body": o.Label,
            "area": r(sum(f.Area for f in faces), 2),
            "regions": len(faces),
            "plane": {"origin": geo.vec(p), "normal": geo.vec(n), "u": geo.vec(u), "v": geo.vec(v)},
            "outlines": outlines,
            "note": "Points are [u, v] in the plane: a point is origin + u·U + v·V. outer: false marks a hole.",
        }

    def mass_properties(a):
        o = picks.solid_body(s, a.get("body"))
        if a.get("density") is not None:
            rho = picks.num(a["density"], "density")
            src = "given"
        else:
            mat = (a.get("material") or store.materials(s.doc).get(o.Label) or "").lower()
            assumed = False
            if not mat and store.setup_of(s.doc, o.Label, "print"):
                # Printed and no material said: PLA, as the review and the
                # strength study assume — and said so.
                mat, assumed = "pla", True
            if not mat:
                raise ToolError("invalid_input", f"material or density: what {o.Label} is made of — {', '.join(picks.MATERIALS)} — or density in g/cm³ (material_set records it with the part).")
            if mat not in picks.MATERIALS:
                raise ToolError("invalid_input", f"material {mat!r}: one of {', '.join(picks.MATERIALS)}, or give density in g/cm³.")
            rho, src = picks.MATERIALS[mat], (f"assumed {mat}: printed, and no material_set" if assumed else mat)
        if rho <= 0:
            raise ToolError("invalid_input", "density: above 0, g/cm³.")
        m, c, I = inertia(o.Shape, rho)
        out = {
            "body": o.Label,
            "density": rho,
            "densityFrom": src,
            "volume": r(o.Shape.Volume, 2),
            "area": r(o.Shape.Area, 2),
            "mass": r(m, 3),
            "centerOfMass": geo.vec(c),
            "inertia": [[r(x, 1) for x in row] for row in I],
            "units": "mm, mm³, mm², g; inertia g·mm² about the centre of mass, in the document's axes",
        }
        if len(o.Shape.Solids) == 1:
            try:
                pp = o.Shape.Solids[0].PrincipalProperties
                k = rho / 1000.0
                out["principal"] = {"moments": [r(x * k, 1) for x in pp["Moments"]], "axes": [geo.vec(pp[f"{n}Axis"]) for n in ("First", "Second", "Third")]}
            except Exception:
                pass
        return out

    def clearance(a):
        oa = picks.solid_body(s, a.get("a"))
        ob = picks.solid_body(s, a.get("b"))
        if oa is ob:
            raise ToolError("invalid_input", "a and b: two different bodies.")
        dist, pairs, _ = oa.Shape.distToShape(ob.Shape)
        out = {"a": oa.Label, "b": ob.Label, "distance": r(dist), "from": geo.vec(pairs[0][0]), "to": geo.vec(pairs[0][1])}
        if dist < 1e-6:
            common = oa.Shape.common(ob.Shape)
            vol = common.Volume if common.Solids else 0.0
            if vol > 1e-6:
                out.update(state="interference", interference=r(vol, 3), interferenceBox=geo.box_of(common))
            else:
                out["state"] = "touching"
        else:
            out["state"] = "clear"
        need = a.get("min")
        if need is not None:
            out["meets"] = out["state"] == "clear" and dist >= picks.num(need, "min")
        return out

    return [
        Tool(
            "shape_query",
            "Find a body's faces, edges or vertices by what they are, as references other tools take (\"Face12\", \"Edge3\"): kind face|edge|vertex; type plane, cylinder, cone, sphere, torus, bspline (faces) or line, circle, ellipse, bspline (edges); normal [x,y,z] (faces, outward) or axis (a line's direction or a circle's axis, either way) within degrees (1 by default); radius, area, length as a number or [least, most]; near [x,y,z] or { point, within } (sorted nearest first); inside { min, max } (the centre); of \"Face6\" takes that face's edges. sort area|radius|length|distance|x|y|z, -key for largest first; limit (20). Trap: numbers change after any modelling step — query again rather than reuse old ones.",
            obj({"body": body, "kind": {"type": "string", "enum": ["face", "edge", "vertex"]}, "type": {}, "normal": {}, "axis": {}, "within": {"type": "number"}, "radius": {}, "area": {}, "length": {}, "near": {}, "inside": {"type": "object"}, "of": {"type": "string"}, "sort": {"type": "string"}, "limit": {"type": "integer"}}),
            shape_query,
        ),
        Tool(
            "measure",
            "Measure one thing or between two: a alone gives a face's area (radius and diameter when round, normal when flat), an edge's length (radius for a circle), a body's volume and box; a and b give the least distance with its two points, and the angle between flat faces or lines (gap between parallel faces' planes; centers between round ones' centres). References: \"Face12\" with body, \"Box.Face12\", a body's name or a point [x,y,z].",
            obj({"a": ref, "b": ref, "body": body}, ["a"]),
            measure,
        ),
        Tool(
            "section",
            "Cut a body with a plane and read it: area, the outlines as [u, v] polylines in the plane (outer, and holes), regions. normal [x,y,z] or +z (default) …; at a point on the plane, or offset along the normal from the origin; tolerance the chord error (0.05 mm). Changes nothing.",
            obj({"body": body, "normal": {}, "at": {"type": "array", "items": {"type": "number"}}, "offset": {"type": "number"}, "tolerance": {"type": "number"}, "maxPoints": {"type": "integer"}}),
            section,
        ),
        Tool(
            "measure_mass",
            "Volume, area, mass, centre of mass and inertia (g·mm² about the centre of mass; principal moments and axes for one solid) of a body solid through: material (" + ", ".join(picks.MATERIALS) + ") or density g/cm³; the part_spec material when neither is given. Trap: a printed part is not solid — part_review estimates its printed mass.",
            obj({"body": body, "material": {"type": "string"}, "density": {"type": "number"}}),
            mass_properties,
        ),
        Tool(
            "clearance",
            "The least distance between two bodies a and b, with where; state clear, touching or interference — with the overlapping volume (mm³) and its box. min mm: meets says whether the gap is at least that.",
            obj({"a": body, "b": body, "min": {"type": "number"}}, ["a", "b"]),
            clearance,
        ),
    ]
