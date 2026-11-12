# SPDX-License-Identifier: LGPL-2.1-or-later
# Sketch tools: a PartDesign body, sketches on its planes or on a face found
# by where it is, geometry added by kind, constraints in FreeCAD's own
# terms (geometry id + vertex 1 start / 2 end / 3 centre), the solver's
# reading, and edits. Coordinates are the sketch's own, in mm; angles in
# degrees.

import math

import FreeCAD
import Part
import Sketcher

from .. import geometry as geo
from ..protocol import Tool, ToolError
from . import pd_common as pc

GROUP = "sketch"
INFO = "PartDesign bodies and sketches: geometry, constraints, the solver's reading (DoF, conflicts, closed wires) and edits."

V = FreeCAD.Vector
SOLVE = {0: "solved", -1: "the solver failed", -2: "redundant constraints", -3: "conflicting constraints", -4: "over-constrained", -5: "malformed constraints"}


def obj(props, required=()):
    return {"type": "object", "properties": props, "required": list(required), "additionalProperties": False}


def frame(pl):
    r = pl.Rotation
    return {"origin": geo.vec(pl.Base), "xAxis": geo.vec(r.multVec(V(1, 0, 0))), "yAxis": geo.vec(r.multVec(V(0, 1, 0))), "normal": geo.vec(r.multVec(V(0, 0, 1)))}


def _geo_kind(g):
    return type(g).__name__.replace("ArcOfCircle", "Arc").replace("LineSegment", "Line")


def _pt(v):
    return [geo.r3(v.x), geo.r3(v.y)]


def describe_geo(sk, i):
    g = sk.Geometry[i]
    k = _geo_kind(g)
    out = {"id": i, "kind": k.lower()}
    if sk.getConstruction(i):
        out["construction"] = True
    if k == "Line":
        out["from"], out["to"] = _pt(g.StartPoint), _pt(g.EndPoint)
    elif k == "Circle":
        out["center"], out["radius"] = _pt(g.Center), geo.r3(g.Radius)
    elif k == "Arc":
        out["center"], out["radius"] = _pt(g.Center), geo.r3(g.Radius)
        out["start"], out["end"] = _pt(g.StartPoint), _pt(g.EndPoint)
    elif k == "Point":
        out["at"] = [geo.r3(g.X), geo.r3(g.Y)]
    return out


def describe_con(sk, i):
    c = sk.Constraints[i]
    out = {"id": i, "type": c.Type}
    if c.Name:
        out["name"] = c.Name
    if c.Type in ("Distance", "DistanceX", "DistanceY", "Radius", "Diameter"):
        out["value"] = round(c.Value, 4)
    elif c.Type == "Angle":
        out["value"] = round(math.degrees(c.Value), 4)
    refs = [[c.First, c.FirstPos]]
    if c.Second != -2000:
        refs.append([c.Second, c.SecondPos])
    if getattr(c, "Third", -2000) != -2000:
        refs.append([c.Third, c.ThirdPos])
    out["refs"] = refs
    if not c.Driving:
        out["driving"] = False
    return out


def state_of(sk):
    code = sk.solve()
    conflicting = [i - 1 for i in (getattr(sk, "ConflictingConstraints", None) or [])]
    redundant = [i - 1 for i in (getattr(sk, "RedundantConstraints", None) or [])]
    partial = [i - 1 for i in (getattr(sk, "PartiallyRedundantConstraints", None) or [])]
    malformed = [i - 1 for i in (getattr(sk, "MalformedConstraints", None) or [])]
    dof = getattr(sk, "DoF", None)
    wires = sk.Shape.Wires if not sk.Shape.isNull() else []
    closed = sum(1 for w in wires if w.isClosed())
    try:
        openv = [_pt(v) for v in (sk.OpenVertices or [])]
    except Exception:
        openv = []
    if not openv and wires and closed < len(wires):
        # Older releases leave OpenVertices empty: the ends of the open wires.
        inv = sk.Placement.inverse()
        for w in wires:
            if not w.isClosed():
                vs = [v for v in w.Vertexes if sum(1 for e in w.Edges for ev in e.Vertexes if ev.isSame(v)) == 1]
                openv += [_pt(inv.multVec(v.Point)) for v in vs]
    out = {
        "sketch": sk.Label,
        "solver": SOLVE.get(code, f"code {code}"),
        "dof": dof,
        "fullyConstrained": dof == 0 if dof is not None else None,
        "wires": len(wires),
        "closedWires": closed,
    }
    if openv:
        out["openVertices"] = openv[:12]
    for k, v in (("conflicting", conflicting), ("redundant", redundant), ("partiallyRedundant", partial), ("malformed", malformed)):
        if v:
            out[k] = v
    ready = code == 0 and not conflicting and not redundant and not malformed and closed > 0 and not openv
    out["readyForFeature"] = ready
    if not ready:
        why = []
        if conflicting or redundant or malformed or code != 0:
            why.append("fix the constraints named (sketch_edit deleteConstraints)")
        if openv or (wires and closed < len(wires)):
            why.append("close the open wires: coincident constraints at the open vertices")
        if not wires:
            why.append("no profile yet: sketch_add")
        out["todo"] = "; ".join(why) or "the sketch has no closed wire"
    elif dof:
        out["note"] = f"{dof} degrees of freedom: it pads as drawn, but edits may move it — dimension and fix it for a parametric part."
    return out


# ---- geometry builders: (geometry, construction) lists and their constraints -----------------

def _line(a, b):
    return Part.LineSegment(a, b)


def _arc(c, r, a0, a1):
    return Part.ArcOfCircle(Part.Circle(c, V(0, 0, 1), r), a0, a1)


def build(item):
    """One item -> (geometries, constraints (as tuples with local ids), dims (name, tuple), report)."""
    k = item.get("kind")
    C = []
    D = []
    if k == "line":
        return [_line(pc.vec2(item.get("from"), "from"), pc.vec2(item.get("to"), "to"))], C, D
    if k == "polyline":
        pts = [pc.vec2(p, "points[]") for p in item.get("points") or []]
        if len(pts) < 2:
            raise ToolError("invalid_input", "polyline.points: two or more [x, y].")
        closed = bool(item.get("closed"))
        if closed and (pts[0] - pts[-1]).Length < 1e-9:
            pts = pts[:-1]
        segs = list(zip(pts, pts[1:] + ([pts[0]] if closed else [])))
        G = [_line(a, b) for a, b in segs]
        for i in range(len(G) - 1):
            C.append(("Coincident", i, 2, i + 1, 1))
        if closed:
            C.append(("Coincident", len(G) - 1, 2, 0, 1))
        return G, C, D
    if k == "circle":
        r = float(item.get("radius") or item.get("diameter", 0) / 2.0)
        if r <= 0:
            raise ToolError("invalid_input", "circle: radius (or diameter), mm.")
        G = [Part.Circle(pc.vec2(item.get("center", [0, 0]), "center"), V(0, 0, 1), r)]
        D.append(("d", ("Diameter", 0, 2 * r)))
        return G, C, D
    if k == "arc":
        r = float(item.get("radius", 0))
        if r <= 0:
            raise ToolError("invalid_input", "arc: center, radius, start and end angles (degrees, counter-clockwise from +x).")
        a0, a1 = math.radians(float(item.get("start", 0))), math.radians(float(item.get("end", 90)))
        if a1 <= a0:
            a1 += 2 * math.pi
        D.append(("r", ("Radius", 0, r)))
        return [_arc(pc.vec2(item.get("center", [0, 0]), "center"), r, a0, a1)], C, D
    if k == "point":
        p = pc.vec2(item.get("at"), "at")
        return [Part.Point(p)], C, D
    if k in ("rectangle", "rounded_rectangle"):
        w, h = (item.get("size") or [None, None])[:2]
        if not (isinstance(w, (int, float)) and isinstance(h, (int, float)) and w > 0 and h > 0):
            raise ToolError("invalid_input", f"{k}: size [w, h] (mm), and center [x, y] or corner [x, y] (its lower left).")
        if "corner" in item:
            c0 = pc.vec2(item["corner"], "corner")
            x0, y0 = c0.x, c0.y
        else:
            c = pc.vec2(item.get("center", [0, 0]), "center")
            x0, y0 = c.x - w / 2.0, c.y - h / 2.0
        x1, y1 = x0 + w, y0 + h
        if k == "rectangle":
            p = [V(x0, y0, 0), V(x1, y0, 0), V(x1, y1, 0), V(x0, y1, 0)]
            G = [_line(p[i], p[(i + 1) % 4]) for i in range(4)]
            C += [("Coincident", i, 2, (i + 1) % 4, 1) for i in range(4)]
            C += [("Horizontal", 0), ("Horizontal", 2), ("Vertical", 1), ("Vertical", 3)]
            D += [("width", ("DistanceX", 0, 1, 0, 2, w)), ("height", ("DistanceY", 1, 1, 1, 2, h))]
            return G, C, D
        r = float(item.get("radius", 0))
        if not (0 < r < min(w, h) / 2.0):
            raise ToolError("invalid_input", f"rounded_rectangle.radius: over 0 and under half the short side ({min(w, h) / 2.0} mm).")
        G = [
            _line(V(x0 + r, y0, 0), V(x1 - r, y0, 0)),  # 0 bottom
            _line(V(x1, y0 + r, 0), V(x1, y1 - r, 0)),  # 1 right
            _line(V(x1 - r, y1, 0), V(x0 + r, y1, 0)),  # 2 top
            _line(V(x0, y1 - r, 0), V(x0, y0 + r, 0)),  # 3 left
            _arc(V(x1 - r, y0 + r, 0), r, -math.pi / 2, 0),  # 4 lower right
            _arc(V(x1 - r, y1 - r, 0), r, 0, math.pi / 2),  # 5 upper right
            _arc(V(x0 + r, y1 - r, 0), r, math.pi / 2, math.pi),  # 6 upper left
            _arc(V(x0 + r, y0 + r, 0), r, math.pi, 1.5 * math.pi),  # 7 lower left
        ]
        for ln, arc in ((0, 4), (1, 5), (2, 6), (3, 7)):
            C.append(("Tangent", ln, 2, arc, 1))
            C.append(("Tangent", arc, 2, (ln + 1) % 4, 1))
        C += [("Horizontal", 0), ("Horizontal", 2), ("Vertical", 1), ("Vertical", 3), ("Equal", 4, 5), ("Equal", 4, 6), ("Equal", 4, 7)]
        D += [("width", ("DistanceX", 3, 1, 1, 1, w)), ("height", ("DistanceY", 0, 1, 2, 1, h)), ("r", ("Radius", 4, r))]
        return G, C, D
    if k == "polygon":
        n = int(item.get("sides", 6))
        R = float(item.get("radius", 0))
        if n < 3 or R <= 0:
            raise ToolError("invalid_input", "polygon: sides (3 or more), radius (to the corners, mm), center, rotation (degrees).")
        c = pc.vec2(item.get("center", [0, 0]), "center")
        rot = math.radians(float(item.get("rotation", 0)))
        pts = [V(c.x + R * math.cos(rot + 2 * math.pi * i / n), c.y + R * math.sin(rot + 2 * math.pi * i / n), 0) for i in range(n)]
        G = [_line(pts[i], pts[(i + 1) % n]) for i in range(n)]
        G.append(("construction", Part.Circle(c, V(0, 0, 1), R)))
        C += [("Coincident", i, 2, (i + 1) % n, 1) for i in range(n)]
        C += [("PointOnObject", i, 1, n) for i in range(n)]
        C += [("Equal", 0, i) for i in range(1, n)]
        D += [("d", ("Diameter", n, 2 * R))]
        return G, C, D
    if k == "slot":
        p1, p2 = pc.vec2(item.get("from"), "from"), pc.vec2(item.get("to"), "to")
        r = float(item.get("radius", 0))
        d = p2 - p1
        if r <= 0 or d.Length < 1e-6:
            raise ToolError("invalid_input", "slot: from and to (its two end centres, apart), radius (half its width), mm.")
        t = math.atan2(d.y, d.x)
        n = V(-math.sin(t), math.cos(t), 0)
        G = [
            _arc(p1, r, t + math.pi / 2, t + 1.5 * math.pi),  # 0 arc at from: +n to -n
            _arc(p2, r, t - math.pi / 2, t + math.pi / 2),  # 1 arc at to: -n to +n
            _line(p1 - n * r, p2 - n * r),  # 2
            _line(p2 + n * r, p1 + n * r),  # 3
        ]
        C += [("Tangent", 0, 2, 2, 1), ("Tangent", 2, 2, 1, 1), ("Tangent", 1, 2, 3, 1), ("Tangent", 3, 2, 0, 1), ("Equal", 0, 1)]
        if abs(d.y) < 1e-9:
            C.append(("Horizontal", 2))
        elif abs(d.x) < 1e-9:
            C.append(("Vertical", 2))
        D += [("r", ("Radius", 0, r)), ("length", ("Distance", 0, 3, 1, 3, d.Length))]
        return G, C, D
    if k == "bspline":
        pts = [pc.vec2(p, "points[]") for p in item.get("points") or []]
        closed = bool(item.get("closed"))
        if len(pts) < (3 if closed else 2):
            raise ToolError("invalid_input", "bspline.points: [x, y] it passes through (interpolate, the default) or its control poles (interpolate: false) — 2 or more, 3 closed.")
        bs = Part.BSplineCurve()
        if item.get("interpolate", True):
            if closed and (pts[0] - pts[-1]).Length < 1e-9:
                pts = pts[:-1]
            bs.interpolate(pts, PeriodicFlag=closed)
        else:
            deg = int(item.get("degree", 3))
            if not 1 <= deg <= min(len(pts) - 1, 9):
                raise ToolError("invalid_input", f"bspline.degree: 1 to {min(len(pts) - 1, 9)} for {len(pts)} poles.")
            bs.buildFromPoles(pts, closed, deg)
        return [bs], C, D
    if k == "ellipse":
        c = pc.vec2(item.get("center", [0, 0]), "center")
        a_, b_ = float(item.get("major", 0)) / 2, float(item.get("minor", 0)) / 2
        if a_ <= 0 or b_ <= 0 or b_ > a_:
            raise ToolError("invalid_input", "ellipse: center, major and minor (its full width and height, mm, major the larger), rotation (deg).")
        t = math.radians(float(item.get("rotation", 0)))
        e = Part.Ellipse(c + V(math.cos(t), math.sin(t), 0) * a_, c + V(-math.sin(t), math.cos(t), 0) * b_, c)
        return [e], C, D
    if k in ("arc3", "circle3"):
        pts = [pc.vec2(p, f"{k}.points") for p in item.get("points") or []]
        if len(pts) != 3:
            raise ToolError("invalid_input", f"{k}.points: three [x, y] — {'start, a point on it, end' if k == 'arc3' else 'on the circle'}.")
        try:
            c = Part.Circle(*pts)
        except Exception:
            raise ToolError("invalid_input", f"{k}.points: three points not on one line.") from None
        if k == "circle3":
            D.append(("d", ("Diameter", 0, 2 * c.Radius)))
            return [Part.Circle(c.Location, V(0, 0, 1), c.Radius)], C, D
        D.append(("r", ("Radius", 0, c.Radius)))
        lo, hi = _ccw(c, pts[0], pts[2], pts[1])
        return [_arc(c.Location, c.Radius, lo, hi)], C, D
    if k == "parabola":
        v = pc.vec2(item.get("vertex", [0, 0]), "vertex")
        f = float(item.get("focal", 0))
        if f <= 0:
            raise ToolError("invalid_input", "parabola: vertex, focal (distance from vertex to focus, mm), rotation (deg: the way it opens, 0 = +x), from and to (y along it, mm).")
        t = math.radians(float(item.get("rotation", 0)))
        pb = Part.Parabola()
        pb.Focal = f
        pb.Center = v
        if t:
            pb.rotate(FreeCAD.Placement(V(), FreeCAD.Rotation(V(0, 0, 1), math.degrees(t)), v))
        u0, u1 = float(item.get("from", -2 * f)), float(item.get("to", 2 * f))
        return [Part.ArcOfParabola(pb, min(u0, u1), max(u0, u1))], C, D
    if k == "hyperbola":
        c = pc.vec2(item.get("center", [0, 0]), "center")
        a_, b_ = float(item.get("major", 0)), float(item.get("minor", 0))
        if a_ <= 0 or b_ <= 0:
            raise ToolError("invalid_input", "hyperbola: center, major and minor (semi-axes, mm), rotation (deg), from and to (its parameter, -1 … 1 by default).")
        t = math.radians(float(item.get("rotation", 0)))
        h = Part.Hyperbola(c + V(math.cos(t), math.sin(t), 0) * a_, c + V(-math.sin(t), math.cos(t), 0) * b_, c)
        u0, u1 = float(item.get("from", -1)), float(item.get("to", 1))
        return [Part.ArcOfHyperbola(h, min(u0, u1), max(u0, u1))], C, D
    raise ToolError("invalid_input", "kind: line, polyline, arc, arc3, circle, circle3, ellipse, parabola, hyperbola, bspline, rectangle, rounded_rectangle, polygon, slot or point.")


def _ccw(cu, start, end, mid):
    """The parameters of a circle's arc from start to end through mid,
    counter-clockwise as Sketcher keeps arcs (start and end swapped if the
    three run clockwise)."""
    c = cu.Location

    def ang(p):
        return math.atan2(p.y - c.y, p.x - c.x)

    a0, a1, am = ang(start), ang(end), ang(mid)

    def norm(x):
        while x < 0:
            x += 2 * math.pi
        return x
    if norm(am - a0) <= norm(a1 - a0):
        lo, hi = a0, a0 + norm(a1 - a0)
    else:
        lo, hi = a1, a1 + norm(a0 - a1)
    return lo, hi


def _remap(t, ids):
    """A constraint tuple with local geometry ids -> the sketch's ids."""
    kind = t[0]
    a = list(t[1:])
    shape = {
        "Coincident": "gpgp", "Horizontal": "g", "Vertical": "g", "Equal": "gg", "PointOnObject": "gpg",
        "Diameter": "gv", "Radius": "gv", "DistanceX": "gpgpv", "DistanceY": "gpgpv", "Distance": "gpgpv",
    }.get(kind)
    if kind == "Tangent":
        shape = "gpgp"
    out = []
    for s, v in zip(shape, a):
        out.append(ids[v] if s == "g" else v)
    return (kind, *out)


# ---- constraints by name ---------------------------------------------------------------------

def _ref(r, what):
    """[geo] or [geo, pos]; 'origin' is the sketch's origin point."""
    if r == "origin":
        return (-1, 1)
    if isinstance(r, int):
        return (r, None)
    if isinstance(r, list) and r and all(isinstance(x, int) for x in r) and len(r) <= 2:
        return (r[0], r[1] if len(r) == 2 else None)
    raise ToolError("invalid_input", f"{what}: a geometry id, [id, vertex] (vertex 1 start, 2 end, 3 centre) or \"origin\". The axes: -1 the horizontal, -2 the vertical.")


def make_constraint(sk, c):
    t = str(c.get("type", "")).lower().replace("_", "").replace("-", "")
    refs = [_ref(r, f"refs[{i}]") for i, r in enumerate(c.get("refs") or [])]
    val = c.get("value")
    pts = [r for r in refs if r[1] is not None]
    n = len(refs)

    def need(ok, form):
        if not ok:
            raise ToolError("invalid_input", f"{t}: refs {form}.")

    def mm():
        if not isinstance(val, (int, float)):
            raise ToolError("invalid_input", f"{t}: value, mm.")
        return float(val)

    if t == "coincident":
        need(n == 2 and len(pts) == 2, "two points [[g, v], [g, v]]")
        return [("Coincident", refs[0][0], refs[0][1], refs[1][0], refs[1][1])]
    if t in ("horizontal", "vertical"):
        k = t.capitalize()
        if n == 1 and not pts:
            return [(k, refs[0][0])]
        need(n == 2 and len(pts) == 2, "one line [g], or two points [[g, v], [g, v]]")
        return [(k, refs[0][0], refs[0][1], refs[1][0], refs[1][1])]
    if t in ("parallel", "perpendicular", "equal"):
        need(n == 2 and not pts, "two geometries [g, g]")
        return [(t.capitalize(), refs[0][0], refs[1][0])]
    if t == "tangent":
        if n == 2 and not pts:
            return [("Tangent", refs[0][0], refs[1][0])]
        need(n == 2 and len(pts) == 2, "two curves [g, g], or two end points joined [[g, v], [g, v]]")
        return [("Tangent", refs[0][0], refs[0][1], refs[1][0], refs[1][1])]
    if t == "symmetric":
        need(n == 3 and refs[0][1] and refs[1][1], "two points and a line [[g, v], [g, v], g] or a point [[g, v], [g, v], [g, v]]")
        if refs[2][1]:
            return [("Symmetric", refs[0][0], refs[0][1], refs[1][0], refs[1][1], refs[2][0], refs[2][1])]
        return [("Symmetric", refs[0][0], refs[0][1], refs[1][0], refs[1][1], refs[2][0])]
    if t == "pointonobject":
        need(n == 2 and refs[0][1] and not refs[1][1], "a point and a curve [[g, v], g]")
        return [("PointOnObject", refs[0][0], refs[0][1], refs[1][0])]
    if t in ("distance", "distancex", "distancey"):
        k = {"distance": "Distance", "distancex": "DistanceX", "distancey": "DistanceY"}[t]
        v = mm()
        if n == 1 and not pts:
            return [(k, refs[0][0], v)]
        if n == 1 and pts and k != "Distance":
            return [(k, refs[0][0], refs[0][1], v)]
        if n == 2 and len(pts) == 2:
            return [(k, refs[0][0], refs[0][1], refs[1][0], refs[1][1], v)]
        if n == 2 and refs[0][1] and not refs[1][1] and k == "Distance":
            return [(k, refs[0][0], refs[0][1], refs[1][0], v)]
        need(False, "a line [g] (its length), a point [[g, v]] (from the origin, X/Y only), two points, or a point and a line (distance)")
    if t in ("radius", "diameter"):
        need(n == 1 and not pts, "one circle or arc [g]")
        return [(t.capitalize(), refs[0][0], mm())]
    if t == "angle":
        if not isinstance(val, (int, float)):
            raise ToolError("invalid_input", "angle: value, degrees.")
        a = math.radians(float(val))
        if n == 1 and not pts:
            return [("Angle", refs[0][0], a)]
        need(n == 2 and not pts, "one line [g] (from +x) or two lines [g, g]")
        return [("Angle", refs[0][0], refs[1][0], a)]
    if t in ("fix", "lock", "block"):
        need(n == 1, "one point [[g, v]] (held where it is) or one geometry [g] (blocked)")
        g, p = refs[0]
        if p is None:
            return [("Block", g)]
        at = sk.getPoint(g, p)
        return [("DistanceX", g, p, at.x), ("DistanceY", g, p, at.y)]
    raise ToolError("invalid_input", "type: coincident, horizontal, vertical, parallel, perpendicular, tangent, equal, symmetric, point_on_object, distance, distance_x, distance_y, radius, diameter, angle, fix.")


def con_index(sk, ref):
    if isinstance(ref, int):
        if 0 <= ref < len(sk.Constraints):
            return ref
    elif isinstance(ref, str):
        for i, c in enumerate(sk.Constraints):
            if c.Name == ref:
                return i
    raise ToolError("not_found", f"constraint {ref!r}: not in {sk.Label} — sketch_state lists them (ids from 0, or their names).")


# ---- the tools -------------------------------------------------------------------------------

def tools(session):
    s = session
    doc = lambda: s.doc  # noqa: E731

    def pd_body_new(a):
        label = s.unique(a.get("name") or "Body")
        b = doc().addObject("PartDesign::Body", "Body")
        b.Label = label
        doc().recompute()
        s.snapshot("pd_body_new")
        return {"body": b.Label, "planes": ["XY", "XZ", "YZ"], "axes": ["x", "y", "z"], "next": "sketch_new { body, plane: \"XY\" }", "step": len(s.steps)}

    def sketch_new(a):
        b = pc.pd_body(doc(), a.get("body"))
        if ("plane" in a) == ("face" in a):
            raise ToolError("invalid_input", "Give plane (XY, XZ, YZ or a datum plane's name) or face ({ at, normal } on the body), one of them.")
        label = s.unique(a.get("name") or "Sketch")
        sk = b.newObject("Sketcher::SketchObject", "Sketch")
        sk.Label = label
        sup = pc.support_prop(sk)
        link, what = pc.plane_ref(b, a["plane"] if "plane" in a else a["face"], "plane")
        setattr(sk, sup, [(link[0], link[1][0])])
        sk.MapMode = "FlatFace"
        if a.get("flip"):
            sk.MapReversed = True
        off = float(a.get("offset", 0))
        doc().recompute()
        if "face" in a:
            # Line the sketch up with the world: its origin the world origin's
            # foot on the face's plane, its x along the world axis that lies
            # most in the plane — so coordinates read as the part's own.
            P = sk.Placement
            n = P.Rotation.multVec(V(0, 0, 1))
            o = n * P.Base.dot(n)
            best = max((V(1, 0, 0), V(0, 1, 0), V(0, 0, 1)), key=lambda e: 1 - abs(e.dot(n)))
            x = best - n * best.dot(n)
            x.normalize()
            y = n.cross(x)
            D = FreeCAD.Placement(o, FreeCAD.Rotation(x, y, n, "ZXY"))
            sk.AttachmentOffset = P.inverse().multiply(D)
            doc().recompute()
        if off:
            ao = sk.AttachmentOffset
            sk.AttachmentOffset = FreeCAD.Placement(ao.Base + ao.Rotation.multVec(V(0, 0, off)), ao.Rotation)
            doc().recompute()
        if not sk.isValid():
            raise ToolError("failed", f"The sketch would not attach to {what}: {sk.getStatusString()}.")
        s.snapshot("sketch_new")
        return {"sketch": sk.Label, "body": b.Label, "on": what, **({"offset": off} if off else {}), "frame": frame(sk.Placement), "note": "Coordinates are the sketch's: x along xAxis, y along yAxis, from origin (world mm).", "step": len(s.steps)}

    def sketch_add(a):
        sk = pc.sketch_of(doc(), a.get("sketch"))
        items = a.get("items")
        if not isinstance(items, list) or not items:
            raise ToolError("invalid_input", "items: [{ kind, … }, …].")
        out = []
        for n, it in enumerate(items):
            if not isinstance(it, dict):
                raise ToolError("invalid_input", f"items[{n}]: an object {{ kind, … }}.")
            G, C, D = build(it)
            cons = bool(it.get("construction"))
            ids = []
            for g in G:
                if isinstance(g, tuple):
                    ids.append(sk.addGeometry(g[1], True))
                else:
                    ids.append(sk.addGeometry(g, cons))
            cids = []
            for t in C:
                cids.append(sk.addConstraint(Sketcher.Constraint(*_remap(t, ids))))
            dims = {}
            if it.get("dims"):
                for name, t in D:
                    ci = sk.addConstraint(Sketcher.Constraint(*_remap(t, ids)))
                    if it.get("name"):
                        sk.renameConstraint(ci, f"{it['name']}_{name}")
                    dims[name] = ci
            r = {"kind": it.get("kind"), "ids": ids}
            if cids:
                r["constraints"] = [cids[0], cids[-1]] if len(cids) > 2 else cids
            if dims:
                r["dims"] = {(f"{it['name']}_{k}" if it.get("name") else k): v for k, v in dims.items()}
            out.append(r)
        doc().recompute()
        st = state_of(sk)
        s.snapshot("sketch_add")
        return {"sketch": sk.Label, "added": out, "state": st, "step": len(s.steps)}

    def _geo_of_edge(e):
        """An edge as sketch geometry: a segment, an arc or circle, else a
        B-spline through it."""
        c = e.Curve
        if isinstance(c, Part.Line):
            return Part.LineSegment(e.Vertexes[0].Point, e.Vertexes[-1].Point)
        if isinstance(c, Part.Circle):
            if e.isClosed():
                return Part.Circle(c.Location, V(0, 0, 1), c.Radius)
            # By three points on it: an offset's arc can come with its axis
            # down, which the sketch reads as the rest of the circle.
            f, l_ = e.FirstParameter, e.LastParameter
            return Part.ArcOfCircle(e.valueAt(f), e.valueAt((f + l_) / 2), e.valueAt(l_))
        return e.toNurbs().Edges[0].Curve

    def sketch_offset(a):
        sk = pc.sketch_of(doc(), a.get("sketch"))
        d = a.get("distance")
        if not isinstance(d, (int, float)) or isinstance(d, bool) or abs(d) < 1e-9:
            raise ToolError("invalid_input", "distance: mm, out from the profile (negative: in).")
        doc().recompute()
        geos = sk.Geometry
        ids = a.get("geometry")
        if ids is None:
            ids = [i for i, g in enumerate(geos) if not sk.getConstruction(i)]
        edges = []
        for i in ids:
            if not isinstance(i, int) or not 0 <= i < len(geos):
                raise ToolError("invalid_input", f"geometry: ids 0 to {len(geos) - 1} (sketch_state lists them).")
            edges.append(geos[i].toShape())
        try:
            wires = [Part.Wire(w) for w in Part.sortEdges(edges)]
        except Exception as e:
            raise ToolError("invalid_input", f"The geometry does not join into profiles ({e}): offset a closed profile or a chain.")
        d = float(d)
        join = {"arc": 0, "tangent": 1, "intersection": 2}.get(a.get("join", "arc"))
        if join is None:
            raise ToolError("invalid_input", "join: arc (round corners, the default), tangent or intersection (sharp).")
        added = []
        for w in wires:
            try:
                off = w.makeOffset2D(d, join, False, not w.isClosed(), False)
                # Out is out whichever way the profile runs: a closed offset
                # meant to grow that came back smaller is done the other way.
                if w.isClosed() and off.Wires and off.Wires[0].isClosed():
                    a0, a1 = Part.Face(w).Area, Part.Face(off.Wires[0]).Area
                    if (a1 - a0) * d < 0:
                        off = w.makeOffset2D(-d, join, False, False, False)
            except ToolError:
                raise
            except Exception as e:
                raise ToolError("failed", f"The offset of {d:g} mm failed ({e}): a distance inward bigger than the profile allows, or a profile crossing itself.")
            for e in off.Edges:
                added.append(sk.addGeometry(_geo_of_edge(e), bool(a.get("construction"))))
        # The new edges meet end to end as made; no constraints are added
        # between them (on 1.0 the solver can pull an offset's arcs apart).
        doc().recompute()
        s.snapshot("sketch_offset")
        return {"sketch": sk.Label, "from": ids, "distance": d, "added": added, "state": state_of(sk), "step": len(s.steps),
                "note": "The offset is geometry, not tied to the profile: constrain it (sketch_constrain distance) if it must follow edits."}

    def sketch_external(a):
        sk = pc.sketch_of(doc(), a.get("sketch"))
        body = pc.body_of(sk)
        if body is None:
            raise ToolError("invalid_state", f"{sk.Label} is in no body: outside geometry comes from the body's own features.")
        tip = body.Tip
        if tip is None or tip.Shape.isNull():
            raise ToolError("invalid_state", f"{body.Label} has no solid yet to take edges from.")
        from . import measure as M

        qs = a.get("edges")
        if not isinstance(qs, list) or not qs:
            raise ToolError("invalid_input", "edges: [\"Edge7\" or a query { type, radius, axis, near, inside } on the body], the edges to bring in.")
        names = []
        for q in qs:
            if isinstance(q, str):
                names.append(q.split(".")[-1])
                continue
            query = {**q, "body": body.Label, "kind": q.get("kind", "edge")}
            _, _, hits = M.query(s, query)
            if not hits:
                raise ToolError("not_found", f"edges: nothing on {body.Label} matches {q} — shape_query lists them.")
            if q.get("near") is not None and len(hits) > 1:
                pt = pc.vec3(q["near"], "near")
                hits = sorted(hits, key=lambda h: h[1].distToShape(Part.Vertex(pt))[0])[:1]
            names += [d["ref"] for _, _, d in hits]
        before = len(sk.ExternalGeometry)
        for n in names:
            try:
                sk.addExternal(tip.Name, n)
            except Exception as e:
                raise ToolError("failed", f"{tip.Label}.{n} could not be brought in: {e}")
        doc().recompute()
        got = len(sk.ExternalGeometry) - before
        ext_ids = [-3 - k for k in range(before, before + got)]
        s.snapshot("sketch_external")
        return {"sketch": sk.Label, "from": f"{tip.Label}", "edges": names, "ids": ext_ids, "state": state_of(sk), "step": len(s.steps),
                "note": "Outside geometry is referred to by these negative ids in sketch_constrain (-1 and -2 are the sketch's axes): a constraint to it follows the edge when the part changes."}

    def sketch_constrain(a):
        sk = pc.sketch_of(doc(), a.get("sketch"))
        cs = a.get("constraints")
        if not isinstance(cs, list) or not cs:
            raise ToolError("invalid_input", "constraints: [{ type, refs, value, name }, …].")
        before = len(sk.Constraints)
        ng = len(sk.Geometry)
        added = []
        try:
            for i, c in enumerate(cs):
                if not isinstance(c, dict):
                    raise ToolError("invalid_input", f"constraints[{i}]: {{ type, refs, value, name }}.")
                for g, _p in [_ref(r, "refs") for r in c.get("refs") or []]:
                    if g >= ng:
                        raise ToolError("invalid_input", f"constraints[{i}]: geometry {g} is not in {sk.Label} (0 to {ng - 1}; -1 and -2 are the axes).")
                ts = make_constraint(sk, c)
                for j, t in enumerate(ts):
                    ci = sk.addConstraint(Sketcher.Constraint(*t))
                    added.append(ci)
                    if c.get("name"):
                        sk.renameConstraint(ci, c["name"] if len(ts) == 1 else f"{c['name']}_{'xy'[j]}")
            st = state_of(sk)
            bad = [x for x in st.get("conflicting", []) + st.get("redundant", []) + st.get("malformed", []) if x >= before]
            if bad or st["solver"] != "solved":
                clash = sorted(set(st.get("conflicting", []) + st.get("redundant", [])) - set(bad))
                raise ToolError("invalid_state", f"These constraints over-constrain {sk.Label} ({st['solver']}): new {bad or added}, against existing {clash or '—'}. Nothing was added — drop one, or sketch_edit deleteConstraints the old one first.")
        except Exception:
            for ci in sorted(range(before, len(sk.Constraints)), reverse=True):
                sk.delConstraint(ci)
            sk.solve()
            raise
        doc().recompute()
        s.snapshot("sketch_constrain")
        return {"sketch": sk.Label, "added": added, "state": st, "step": len(s.steps)}

    def sketch_state(a):
        sk = pc.sketch_of(doc(), a.get("sketch"))
        st = state_of(sk)
        st["frame"] = frame(sk.Placement)
        if a.get("detail", True):
            st["geometry"] = [describe_geo(sk, i) for i in range(len(sk.Geometry))]
            st["constraints"] = [describe_con(sk, i) for i in range(len(sk.Constraints))]
        if not sk.isValid():
            st["error"] = sk.getStatusString()
        return st

    def sketch_edit(a):
        sk = pc.sketch_of(doc(), a.get("sketch"))
        done = []
        ng = len(sk.Geometry)

        def gid(g):
            if not isinstance(g, int) or not (0 <= g < len(sk.Geometry)):
                raise ToolError("invalid_input", f"geometry {g!r}: not in {sk.Label} (0 to {len(sk.Geometry) - 1}).")
            return g

        if "move" in a:
            m = a["move"]
            g = gid(m.get("geo"))
            p = int(m.get("vertex", 0 if _geo_kind(sk.Geometry[g]) == "Line" else 3))
            to = pc.vec2(m.get("to"), "move.to")
            sk.moveGeometry(g, p, to, False)
            done.append(f"moved {g}.{p}")
        if "set" in a:
            for x in a["set"] if isinstance(a["set"], list) else [a["set"]]:
                ci = con_index(sk, x.get("constraint"))
                c = sk.Constraints[ci]
                v = x.get("value")
                if not isinstance(v, (int, float)):
                    raise ToolError("invalid_input", "set.value: mm (degrees for an angle).")
                if c.Type not in ("Distance", "DistanceX", "DistanceY", "Radius", "Diameter", "Angle"):
                    raise ToolError("invalid_input", f"constraint {ci} is {c.Type}: it has no value to set.")
                bound = [p for p, _e in sk.ExpressionEngine if p in (f"Constraints[{ci}]", f".Constraints[{ci}]", f"Constraints.{c.Name}", f".Constraints.{c.Name}")]
                if bound:
                    raise ToolError("invalid_state", f"constraint {ci} is bound to an expression: param_set its parameter, or param_bind it to null first.")
                sk.setDatum(ci, FreeCAD.Units.Quantity(f"{v} deg") if c.Type == "Angle" else FreeCAD.Units.Quantity(f"{v} mm"))
                done.append(f"set {ci} = {v}")
        if "construction" in a:
            x = a["construction"]
            g = gid(x.get("geo"))
            sk.setConstruction(g, bool(x.get("on", True)))
            done.append(f"construction {g} {bool(x.get('on', True))}")
        if "rename" in a:
            x = a["rename"]
            ci = con_index(sk, x.get("constraint"))
            sk.renameConstraint(ci, str(x.get("name", "")))
            done.append(f"renamed {ci}")
        if "deleteConstraints" in a:
            ids = sorted({con_index(sk, c) for c in a["deleteConstraints"]}, reverse=True)
            for ci in ids:
                sk.delConstraint(ci)
            done.append(f"deleted constraints {sorted(ids)}")
        if "delete" in a:
            ids = sorted({gid(g) for g in a["delete"]})
            sk.delGeometries(ids)
            done.append(f"deleted geometry {ids} (later ids shift down by the count before them)")
        if "fillet" in a:
            # A corner rounded: at a shared end point [g, v], or between two
            # curves near the points given; the curves are trimmed to the arc.
            x = a["fillet"]
            r = float(x.get("radius", 0))
            if r <= 0:
                raise ToolError("invalid_input", "fillet.radius: mm, over 0.")
            n0 = len(sk.Geometry)
            try:
                if x.get("at") is not None:
                    g, v = x["at"]
                    sk.fillet(gid(g), int(v), r, True, False)
                else:
                    g1, g2 = (gid(g) for g in x.get("geos") or [])
                    p1, p2 = (FreeCAD.Vector(*pc.vec2(q, "fillet.near"), 0) for q in x.get("near") or [])
                    sk.fillet(g1, g2, p1, p2, r, True, False)
            except ValueError:
                raise ToolError("invalid_input", "fillet: at [geo, vertex] (a corner where two curves meet), or geos [g1, g2] with near [[x, y], [x, y]] on each, and radius.")
            except Exception as e:
                raise ToolError("failed", f"No fillet of {r:g} mm fits there: {e} — a smaller radius.")
            if len(sk.Geometry) == n0:
                raise ToolError("failed", f"No fillet of {r:g} mm fits there — a smaller radius, or a corner where two curves meet.")
            done.append(f"fillet r{r:g}: arc {len(sk.Geometry) - 1}")
        if "trim" in a:
            x = a["trim"]
            g = gid(x.get("geo"))
            at = pc.vec2(x.get("at"), "trim.at")
            n0 = len(sk.Geometry)
            try:
                sk.trim(g, FreeCAD.Vector(at[0], at[1], 0))
            except Exception as e:
                raise ToolError("failed", f"Geometry {g} could not be trimmed at {at}: {e} — the piece to cut off lies between where other curves cross it.")
            done.append(f"trimmed {g} at {list(at)} ({len(sk.Geometry) - n0:+d} geometry)")
        if "split" in a:
            x = a["split"]
            g = gid(x.get("geo"))
            at = pc.vec2(x.get("at"), "split.at")
            try:
                sk.split(g, FreeCAD.Vector(at[0], at[1], 0))
            except Exception as e:
                raise ToolError("failed", f"Geometry {g} could not be split at {at}: {e}.")
            done.append(f"split {g} at {list(at)}: its second part is {len(sk.Geometry) - 1}")
        if "mirror" in a:
            # Copies mirrored about a line, an axis, or the origin, kept
            # symmetric by constraints.
            x = a["mirror"]
            geos = [gid(g) for g in x.get("geos") or []]
            if not geos:
                raise ToolError("invalid_input", "mirror.geos: the geometry ids to mirror.")
            about = x.get("about", "y")
            ref = {"x": (-1, None), "y": (-2, None), "origin": (-1, 1)}.get(about) if isinstance(about, str) else (gid(about), None)
            if ref is None:
                raise ToolError("invalid_input", "mirror.about: \"x\" (the horizontal axis), \"y\" (the vertical), \"origin\", or a line's id.")
            n0 = len(sk.Geometry)
            try:
                if ref[1] is None:
                    sk.addSymmetric(geos, ref[0])
                else:
                    sk.addSymmetric(geos, ref[0], ref[1])
            except Exception as e:
                raise ToolError("failed", f"The geometry could not be mirrored: {e}.")
            done.append(f"mirrored {geos} about {about}: new {list(range(n0, len(sk.Geometry)))}")
        if not done:
            raise ToolError("invalid_input", "Give one or more of: move { geo, vertex, to }, set [{ constraint, value }], construction { geo, on }, rename { constraint, name }, deleteConstraints [ids|names], delete [geometry ids], fillet { at [g, v] | geos, near; radius }, trim { geo, at }, split { geo, at }, mirror { geos, about }.")
        doc().recompute()
        st = state_of(sk)
        b = pc.body_of(sk)
        bad = pc.broken(doc())
        if bad:
            raise ToolError("failed", f"After the edit {bad[0]['object']} fails: {bad[0]['error']} — nothing was kept.")
        s.snapshot("sketch_edit")
        out = {"sketch": sk.Label, "done": done, "state": st, "geometryCount": len(sk.Geometry), "step": len(s.steps)}
        if b is not None and ng and not b.Shape.isNull():
            out["bodyAfter"] = pc.body_reading(b)
        return out

    body = {"type": "string", "description": "The PartDesign body's name; may be left out when there is one."}
    sketch = {"type": "string", "description": "The sketch's name; may be left out when there is one."}
    xyz = {"type": "array", "items": {"type": "number"}}
    face = {"type": "object", "description": "{ at: [x,y,z] a point on the face, normal: [x,y,z] its outward normal }", "properties": {"at": xyz, "normal": xyz, "tol": {"type": "number"}}, "required": ["at"], "additionalProperties": False}
    return [
        Tool("pd_body_new", "Make an empty PartDesign body: the container a parametric part's features go in, with its own XY/XZ/YZ planes and x/y/z axes. Next: sketch_new on one of its planes.", obj({"name": {"type": "string"}}), pd_body_new, changes=True),
        Tool(
            "sketch_new",
            "A new sketch in a body, on plane XY | XZ | YZ | a datum plane's name, or on a body face found by face { at: a point on it, normal: its outward normal } — never by face index, which changes after edits. offset mm along the normal; flip reverses it. A face sketch is lined up with the world: its x along the world axis that lies most in the face. The answer's frame says where sketch x/y lie in the world.",
            obj({"body": body, "plane": {"type": "string"}, "face": face, "offset": {"type": "number"}, "flip": {"type": "boolean"}, "name": {"type": "string"}}),
            sketch_new,
            changes=True,
        ),
        Tool(
            "sketch_add",
            "Add geometry to a sketch, in its own x/y (mm): items [{ kind, …, construction, dims, name }]. kinds: line {from, to}; polyline {points, closed}; arc {center, radius, start, end (deg, ccw)}; arc3 {points: start, through, end}; circle {center, radius|diameter}; circle3 {points: three on it}; parabola {vertex, focal, rotation, from, to}; hyperbola {center, major, minor (semi-axes), rotation, from, to}; ellipse {center, major, minor (full sizes), rotation}; bspline {points it passes through, closed — or interpolate: false, points its poles, degree}; rectangle {center|corner, size [w,h]}; rounded_rectangle {…, radius}; polygon {center, radius (to corners), sides, rotation}; slot {from, to (end centres), radius}; point {at}. Shapes come with their own coincident/horizontal/vertical/tangent/equal constraints; dims: true adds their sizes as constraints (named <name>_width … when named — what param_bind binds). Answers the geometry ids per item and the solver's state. Lines: vertex 1 start, 2 end; arcs and circles: 3 centre.",
            obj({"sketch": sketch, "items": {"type": "array", "items": {"type": "object"}}}, ["items"]),
            sketch_add,
            changes=True,
        ),
        Tool(
            "sketch_offset",
            "Offset a sketch's profile by distance mm (out; negative in): its geometry ids (all but construction when left out), joined end to end; join arc (round corners, the default), tangent or intersection (sharp). Adds the offset as new geometry, joined, and answers its ids — a wall's inside from its outside, a gasket's band, a clearance round a part.",
            obj({"sketch": sketch, "distance": {"type": "number"}, "geometry": {"type": "array", "items": {"type": "integer"}}, "join": {"type": "string", "enum": ["arc", "tangent", "intersection"]}, "construction": {"type": "boolean"}}, ["distance"]),
            sketch_offset,
            changes=True,
        ),
        Tool(
            "sketch_external",
            "Bring edges of the body's solid into a sketch as outside geometry, to constrain to: edges [\"Edge7\" or a query { type: circle|line, radius, axis, near, inside } as shape_query takes]. Answers their ids (negative, -3 down); a constraint to them follows the part when it changes — a hole lined up with a boss, a pocket kept off a wall.",
            obj({"sketch": sketch, "edges": {"type": "array"}}, ["edges"]),
            sketch_external,
            changes=True,
        ),
        Tool(
            "sketch_constrain",
            "Constrain a sketch: constraints [{ type, refs, value, name }]. refs as FreeCAD has them: a geometry id g, a point [g, vertex] (1 start, 2 end, 3 centre), \"origin\"; -1 the horizontal axis, -2 the vertical. types: coincident, horizontal, vertical, parallel, perpendicular, tangent, equal, symmetric, point_on_object, distance, distance_x, distance_y, radius, diameter (mm), angle (deg), fix (a point held where it is; a geometry blocked). name it to bind it to a parameter later. One that conflicts or is redundant is refused and nothing added.",
            obj({"sketch": sketch, "constraints": {"type": "array", "items": {"type": "object"}}}, ["constraints"]),
            sketch_constrain,
            changes=True,
        ),
        Tool(
            "sketch_state",
            "The solver's reading of a sketch — check it before a feature: dof (0 is fully constrained), conflicting / redundant / malformed constraint ids, closed wires and open vertices, readyForFeature with what to do if not; its frame in the world; detail (default true) lists geometry and constraints with ids. A pad on a sketch with dof > 0 works but moves when edited.",
            obj({"sketch": sketch, "detail": {"type": "boolean"}}),
            sketch_state,
        ),
        Tool(
            "sketch_edit",
            "Edit a sketch: move { geo, vertex (0 the whole, 1/2/3), to [x,y] }, set [{ constraint: id|name, value mm|deg }], construction { geo, on }, rename { constraint, name }, deleteConstraints [id|name], delete [geometry ids — later ids shift down, and their constraints go], fillet { at: [g, v] a corner, or geos [g1, g2] near [[x,y],[x,y]]; radius } (round a corner, the curves trimmed to it), trim { geo, at [x,y] } (cut off the piece there, between crossings), split { geo, at }, mirror { geos, about: \"x\" | \"y\" | \"origin\" | a line id } (symmetric copies). Features built on it recompute; a feature it breaks is refused with FreeCAD's error and nothing kept.",
            obj({"sketch": sketch, "move": {"type": "object"}, "set": {}, "construction": {"type": "object"}, "rename": {"type": "object"}, "deleteConstraints": {"type": "array"}, "delete": {"type": "array", "items": {"type": "integer"}},
                 "fillet": {"type": "object"}, "trim": {"type": "object"}, "split": {"type": "object"}, "mirror": {"type": "object"}}),
            sketch_edit,
            changes=True,
        ),
    ]
