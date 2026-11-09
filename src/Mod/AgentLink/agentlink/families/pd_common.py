# SPDX-License-Identifier: LGPL-2.1-or-later
# What the sketch, feature and param families share: finding a PartDesign
# body, its origin planes and axes, its features and sketches; faces and
# edges found by where they are (a point, a normal) rather than by index;
# and one reading of a body after a change. Written for FreeCAD's public
# Python API, tolerant of the property renames between releases
# (Support -> AttachmentSupport, Midplane -> SideType).

import math

import FreeCAD
import Part

from .. import geometry as geo
from ..protocol import ToolError

V = FreeCAD.Vector
PLANES = {"XY": "XY_Plane", "XZ": "XZ_Plane", "YZ": "YZ_Plane"}
AXES = {"X": "X_Axis", "Y": "Y_Axis", "Z": "Z_Axis"}


def short(o):
    return o.TypeId.split("::", 1)[-1]


def num(v):
    """A property's value as JSON: Quantities as their number (mm, degrees)."""
    if hasattr(v, "Value") and not isinstance(v, (int, float)):
        return round(float(v.Value), 4) + 0.0
    if isinstance(v, float):
        return round(v, 4) + 0.0
    return v


def vec3(a, what):
    if not (isinstance(a, (list, tuple)) and len(a) == 3 and all(isinstance(x, (int, float)) for x in a)):
        raise ToolError("invalid_input", f"{what}: [x, y, z] in mm.")
    return V(float(a[0]), float(a[1]), float(a[2]))


def vec2(a, what):
    if not (isinstance(a, (list, tuple)) and len(a) == 2 and all(isinstance(x, (int, float)) for x in a)):
        raise ToolError("invalid_input", f"{what}: [x, y] in mm, in the sketch's own coordinates.")
    return V(float(a[0]), float(a[1]), 0)


def support_prop(o):
    return "AttachmentSupport" if "AttachmentSupport" in o.PropertiesList else "Support"


# ---- bodies, features, sketches ------------------------------------------------------------

def pd_bodies(doc):
    return [o for o in doc.Objects if o.TypeId == "PartDesign::Body"]


def pd_body(doc, ref):
    bs = pd_bodies(doc)
    if not bs:
        raise ToolError("not_found", "No PartDesign body in the document: pd_body_new makes one.")
    if ref is None:
        if len(bs) == 1:
            return bs[0]
        raise ToolError("invalid_input", f"body: say which — {', '.join(o.Label for o in bs)}.")
    for o in bs:
        if ref in (o.Label, o.Name) or o.Label.lower() == str(ref).lower():
            return o
    raise ToolError("not_found", f"body {ref!r}: no such PartDesign body — they are: {', '.join(o.Label for o in bs)}.")


def body_of(o):
    try:
        g = o.getParentGeoFeatureGroup()
    except Exception:
        g = None
    if g is not None and g.TypeId == "PartDesign::Body":
        return g
    for b in pd_bodies(o.Document):
        if b.hasObject(o):
            return b
    return None


def in_body(body, ref, kinds=None, what="feature"):
    """An object of the body by label or name; kinds: TypeId prefixes allowed."""
    items = [o for o in body.Group if kinds is None or any(o.TypeId.startswith(k) for k in kinds)]
    for o in items:
        if ref in (o.Label, o.Name) or o.Label.lower() == str(ref).lower():
            return o
    raise ToolError("not_found", f"{what} {ref!r}: not in {body.Label} — there: {', '.join(o.Label for o in items) or 'none'}.")


def sketch_of(doc, ref, body=None):
    sk = [o for o in doc.Objects if o.TypeId == "Sketcher::SketchObject"]
    if ref is None:
        if body is not None:
            sk = [o for o in sk if body.hasObject(o)]
        if len(sk) == 1:
            return sk[0]
        raise ToolError("invalid_input", f"sketch: say which — {', '.join(o.Label for o in sk) or 'none yet: sketch_new makes one'}.")
    for o in sk:
        if ref in (o.Label, o.Name) or o.Label.lower() == str(ref).lower():
            return o
    raise ToolError("not_found", f"sketch {ref!r}: no such sketch — they are: {', '.join(o.Label for o in sk) or 'none: sketch_new makes one'}.")


def tip_shape(body, need=True):
    tip = body.Tip
    if tip is None or tip.Shape.isNull() or not tip.Shape.Solids:
        if need:
            raise ToolError("invalid_state", f"{body.Label} has no solid yet: pd_pad a closed sketch first.")
        return None, None
    return tip, tip.Shape


# ---- the body's origin ---------------------------------------------------------------------

def origin_feature(body, role):
    org = body.Origin
    feats = list(getattr(org, "OriginFeatures", None) or getattr(org, "Group", None) or org.OutList)
    for f in feats:
        if getattr(f, "Role", "") == role:
            return f
    for f in feats:
        if f.Name.startswith(role) or f.Label.startswith(role):
            return f
    raise ToolError("failed", f"{body.Label}'s origin has no {role}.")


def plane_ref(body, ref, what="plane"):
    """XY | XZ | YZ, a datum plane's name, or a face query {at, normal}: the
    (object, [sub]) a property links to, and what it is."""
    if isinstance(ref, str):
        key = ref.upper().replace("_PLANE", "")
        if key in PLANES:
            return (origin_feature(body, PLANES[key]), [""]), key
        o = in_body(body, ref, ["PartDesign::Plane"], "datum plane")
        return (o, [""]), o.Label
    if isinstance(ref, dict):
        tip, shape = tip_shape(body)
        i, f = find_face(shape, ref)
        return (tip, [f"Face{i + 1}"]), f"face at {geo.vec(f.CenterOfMass)}"
    raise ToolError("invalid_input", f"{what}: XY, XZ, YZ, a datum plane's name, or a face {{ at: [x,y,z], normal: [x,y,z] }}.")


def axis_ref(body, ref, sketch=None, what="axis"):
    """x | y | z (the body's origin axes), H_Axis | V_Axis of the sketch, or a
    sketch geometry id (a line, often construction)."""
    if isinstance(ref, str):
        key = ref.upper().replace("_AXIS", "")
        if key in AXES:
            return (origin_feature(body, AXES[key]), [""]), key.lower()
        if key in ("H", "V") and sketch is not None:
            return (sketch, [f"{key}_Axis"]), f"{sketch.Label} {key}_Axis"
    if isinstance(ref, int) and sketch is not None:
        if not (0 <= ref < len(sketch.Geometry)):
            raise ToolError("invalid_input", f"{what}: geometry {ref} is not in {sketch.Label} (0 to {len(sketch.Geometry) - 1}).")
        return (sketch, [f"Edge{ref + 1}"]), f"{sketch.Label} geometry {ref}"
    raise ToolError("invalid_input", f"{what}: x, y, z (the body's axes), H_Axis or V_Axis (the sketch's), or a line's geometry id in the sketch.")


# ---- faces and edges by where they are -----------------------------------------------------

def _normal_at(face, p):
    try:
        u, v = face.Surface.parameter(p)
        n = face.normalAt(u, v)
        return n if n.Length > 1e-12 else None
    except Exception:
        return None


def _angle(a, b):
    if a is None or a.Length < 1e-12 or b.Length < 1e-12:
        return 180.0
    c = max(-1.0, min(1.0, a.dot(b) / (a.Length * b.Length)))
    return math.degrees(math.acos(c))


def find_face(shape, q, tol=0.5):
    """The face a query names: at [x,y,z] on it (within tol mm), normal
    [x,y,z] outward there (within 15°). Returns (index, face)."""
    if not isinstance(q, dict) or "at" not in q:
        raise ToolError("invalid_input", "face: { at: [x,y,z] on the face, normal: [x,y,z] outward } — a point on it, not its index (indices change after edits).")
    p = vec3(q["at"], "face.at")
    n = vec3(q["normal"], "face.normal") if q.get("normal") is not None else None
    tol = float(q.get("tol", tol))
    vx = Part.Vertex(p)
    cands = []
    for i, f in enumerate(shape.Faces):
        d = f.distToShape(vx)[0]
        if d > max(tol, 5.0) * 4:
            continue
        nn = _normal_at(f, f.distToShape(vx)[1][0][0]) if d > 1e-9 else _normal_at(f, p)
        cands.append((d, i, f, nn))
    cands.sort(key=lambda c: c[0])
    for d, i, f, nn in cands:
        if d <= tol and (n is None or _angle(nn, n) <= 15.0):
            return i, f
    near = [{"center": geo.vec(f.CenterOfMass), "normal": geo.vec(nn) if nn is not None else None, "distance": round(d, 3)} for d, i, f, nn in cands[:5]]
    raise ToolError("not_found", f"No face within {tol} mm of {geo.vec(p)}" + (f" facing {geo.vec(n)}" if n is not None else "") + f". Nearest faces: {near or 'none'} — give a point on the face and its outward normal.")


def find_edges(shape, queries):
    """Edge names (Edge1 …) for queries: { near: [x,y,z] } the edge nearest
    a point (within 1 mm), { face: { at, normal } } every edge of a face,
    { all: true } every edge. kind: line | circle filters any of them."""
    if not isinstance(queries, list) or not queries:
        raise ToolError("invalid_input", "edges: [{ near: [x,y,z] } | { face: { at, normal } } | { all: true }, …], kind: line | circle to filter.")
    edges = shape.Edges
    picked = []
    for q in queries:
        if not isinstance(q, dict):
            raise ToolError("invalid_input", "edges: each one a query object, not an index — indices change after edits.")
        if q.get("all"):
            idx = list(range(len(edges)))
        elif "near" in q:
            p = Part.Vertex(vec3(q["near"], "edges.near"))
            ds = sorted((e.distToShape(p)[0], i) for i, e in enumerate(edges))
            tol = float(q.get("tol", 1.0))
            if not ds or ds[0][0] > tol:
                close = [{"edge": geo.vec(edges[i].CenterOfMass), "distance": round(d, 3)} for d, i in ds[:4]]
                raise ToolError("not_found", f"No edge within {tol} mm of {q['near']}: nearest edges (their mid-points) {close} — give a point on the edge.")
            idx = [ds[0][1]]
        elif "face" in q:
            _, f = find_face(shape, q["face"])
            idx = [i for i, e in enumerate(edges) if any(e.isSame(fe) for fe in f.Edges)]
        else:
            raise ToolError("invalid_input", "edges: each one { near } | { face } | { all: true }.")
        kind = q.get("kind")
        if kind:
            want = {"line": "Line", "circle": "Circle"}.get(kind)
            if not want:
                raise ToolError("invalid_input", "edges.kind: line or circle.")
            idx = [i for i in idx if type(edges[i].Curve).__name__ == want]
        for i in idx:
            if i not in picked:
                picked.append(i)
    if not picked:
        raise ToolError("not_found", "The queries matched no edge (kind filtered them all out?).")
    return [f"Edge{i + 1}" for i in picked]


def find_faces(shape, queries):
    if not isinstance(queries, list) or not queries:
        raise ToolError("invalid_input", "faces: [{ at: [x,y,z], normal: [x,y,z] }, …] — by where they are, not by index.")
    out = []
    for q in queries:
        i, _ = find_face(shape, q)
        if f"Face{i + 1}" not in out:
            out.append(f"Face{i + 1}")
    return out


# ---- after a change ------------------------------------------------------------------------

def broken(doc):
    out = []
    for o in doc.Objects:
        try:
            ok = o.isValid()
        except Exception:
            ok = "Invalid" not in o.State
        if not ok:
            out.append({"object": o.Label, "type": short(o), "error": (o.getStatusString() if hasattr(o, "getStatusString") else "invalid")})
    return out


def body_reading(body):
    s = body.Shape
    if s is None or s.isNull() or not s.Solids:
        return {"body": body.Label, "solid": False}
    return {"body": body.Label, **geo.stats(s)}


def settle(session, body, feat, tool, before=None, must_change=None, hint=""):
    """Recompute; refuse (the call's transaction is rolled back) if the
    feature or anything after it failed; else the body's reading."""
    doc = session.doc
    doc.recompute()
    bad = broken(doc)
    if bad:
        mine = [b for b in bad if feat is not None and b["object"] == feat.Label]
        first = (mine or bad)[0]
        raise ToolError("failed", f"{first['object']} ({first['type']}) failed: {first['error']}" + (f"; also broken: {[b['object'] for b in bad if b is not first]}" if len(bad) > 1 else "") + f" — nothing was kept.{(' ' + hint) if hint else ''}")
    out = {**({"feature": feat.Label, "type": short(feat)} if feat is not None else {}), "valid": True, **body_reading(body)}
    if before is not None and "volume" in out:
        delta = out["volume"] - before
        out["volumeChange"] = round(delta, 2)
        if must_change == "add" and delta <= 1e-6:
            raise ToolError("failed", f"{feat.Label} added no material — it lies inside the body already. Nothing was kept. {hint}".strip())
        if must_change == "cut" and delta >= -1e-6:
            raise ToolError("failed", f"{feat.Label} cut nothing — the profile does not meet the body that way. Nothing was kept. {hint}".strip())
    session.snapshot(tool)
    out["step"] = len(session.steps)
    return out


def volume(body):
    s = body.Shape
    return s.Volume if s is not None and not s.isNull() and s.Solids else 0.0
