# SPDX-License-Identifier: LGPL-2.1-or-later
# What the doc, model and measure tools share: finding an object by name or
# label, reading a reference to its geometry ("Face12", "Box.Edge3", a
# point), placements from JSON, and values made JSON-safe. Not a family of
# its own (it is not in NAMES).

import math
import re

import FreeCAD
import Part

from .. import geometry as geo
from ..protocol import ToolError

V = FreeCAD.Vector
SUB = re.compile(r"^(Face|Edge|Vertex)(\d+)$")

# g/cm³: the printing plastics the core knows, and the usual stock.
MATERIALS = {
    "pla": 1.24, "petg": 1.27, "abs": 1.04, "asa": 1.07, "pa6-cf": 1.12, "tpu-95a": 1.21,
    "nylon": 1.14, "pc": 1.20, "pom": 1.41, "acrylic": 1.18, "hdpe": 0.95,
    "steel": 7.85, "stainless": 8.00, "aluminium": 2.70, "aluminum": 2.70, "brass": 8.50,
    "copper": 8.96, "titanium": 4.43, "cast-iron": 7.20, "oak": 0.75, "pine": 0.50, "plywood": 0.60,
}


def num(v, name):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise ToolError("invalid_input", f"{name}: a number.")
    return float(v)


def vec3(v, name, default=None):
    if v is None:
        if default is None:
            raise ToolError("invalid_input", f"{name}: [x, y, z], mm.")
        return V(*default)
    if isinstance(v, str):
        axes = {"+x": (1, 0, 0), "-x": (-1, 0, 0), "+y": (0, 1, 0), "-y": (0, -1, 0), "+z": (0, 0, 1), "-z": (0, 0, -1), "x": (1, 0, 0), "y": (0, 1, 0), "z": (0, 0, 1)}
        if v.lower() in axes:
            return V(*axes[v.lower()])
    if isinstance(v, list) and len(v) == 2:
        v = list(v) + [0]
    if not (isinstance(v, list) and len(v) == 3 and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in v)):
        raise ToolError("invalid_input", f"{name}: [x, y, z] (or +x, -z …).")
    return V(*[float(x) for x in v])


def direction(v, name, default=None):
    d = vec3(v, name, default)
    if d.Length < 1e-12:
        raise ToolError("invalid_input", f"{name}: not [0, 0, 0] — a direction.")
    return d.normalize()


def placement(a):
    """at [x,y,z] and a rotation — rotate { axis, angle } or axis and angle
    beside at (degrees) — into a Placement. Tools list one or the other;
    either is taken, so a rotation given is never dropped."""
    pos = vec3(a.get("at"), "at", (0, 0, 0))
    rot = FreeCAD.Rotation()
    r = a.get("rotate")
    if r is None and (a.get("axis") is not None or a.get("angle") is not None):
        r = {"axis": a.get("axis"), "angle": a.get("angle", 0)}
    if r:
        if not isinstance(r, dict):
            raise ToolError("invalid_input", "rotate: { axis: [x,y,z], angle: degrees }.")
        rot = FreeCAD.Rotation(direction(r.get("axis"), "rotate.axis", (0, 0, 1)), num(r.get("angle", 0), "rotate.angle"))
    return FreeCAD.Placement(pos, rot)


def placed(a):
    """Whether a placement was asked for at all: at, axis, angle or rotate."""
    return any(a.get(k) is not None for k in ("at", "axis", "angle", "rotate"))


def placement_json(p):
    ax = p.Rotation.Axis
    return {"at": geo.vec(p.Base), "axis": geo.vec(ax), "angle": round(math.degrees(p.Rotation.Angle), 4)}


# ---- objects ----------------------------------------------------------------------------

def find(doc, ref, what="object"):
    if not isinstance(ref, str) or not ref:
        raise ToolError("invalid_input", f"{what}: an object's name or label.")
    o = doc.getObject(ref)
    if o is not None:
        return o
    hits = doc.getObjectsByLabel(ref) or [x for x in doc.Objects if x.Label.lower() == ref.lower()]
    if hits:
        return hits[0]
    names = ", ".join(x.Label for x in doc.Objects[:30]) or "none"
    raise ToolError("not_found", f"{what} {ref!r}: not here — the objects: {names}.")


def shaped(doc, ref, what="body"):
    o = find(doc, ref, what)
    sh = getattr(o, "Shape", None)
    if sh is None or sh.isNull():
        raise ToolError("invalid_state", f"{o.Label} has no shape ({o.TypeId}): a mesh becomes a body with import_file {{ solid: true }}.")
    return o


def solid_body(session, ref):
    """A body with solids: session.body() for its refusals, by name too."""
    if ref is not None:
        o = shaped(session.doc, ref)
        if not o.Shape.Solids:
            raise ToolError("invalid_state", f"{o.Label} has no solid: close it first (part_extrude a closed profile).")
        return o
    return session.body(None)


def split_ref(ref):
    """"Box.Face3" -> ("Box", "Face3"); "Face3" -> (None, "Face3"); a label -> (label, None)."""
    if SUB.match(ref):
        return None, ref
    if "." in ref:
        head, tail = ref.rsplit(".", 1)
        if SUB.match(tail):
            return head, tail
    return ref, None


def element(shape, sub, owner):
    m = SUB.match(sub)
    kind, i = m.group(1), int(m.group(2))
    have = {"Face": len(shape.Faces), "Edge": len(shape.Edges), "Vertex": len(shape.Vertexes)}[kind]
    if not 1 <= i <= have:
        raise ToolError("not_found", f"{owner}.{sub}: {owner} has {have} {kind.lower()}{'s' if have != 1 else ''} — shape_query finds them again after a change renumbers them.")
    return shape.getElement(sub)


def resolve(session, ref, body=None):
    """A reference into (label, sub or None, shape): a point [x,y,z], an
    object ("Box"), or its element ("Box.Face3", or "Face3" with body)."""
    if isinstance(ref, list):
        p = vec3(ref, "point")
        return "point", None, Part.Vertex(p)
    if isinstance(ref, dict) and "body" in ref and "ref" in ref:
        body, ref = ref["body"], ref["ref"]
    if not isinstance(ref, str):
        raise ToolError("invalid_input", "A reference: \"Face12\" (with body), \"Box.Face12\", an object's name, or a point [x, y, z].")
    head, sub = split_ref(ref)
    if head is None:
        o = shaped(session.doc, body) if body else session.body(None)
    else:
        o = shaped(session.doc, head)
    if sub is None:
        return o.Label, None, o.Shape
    return o.Label, sub, element(o.Shape, sub, o.Label)


def index_of(shape, sub_shape, kind):
    for i, s in enumerate(getattr(shape, kind), 1):
        if s.isSame(sub_shape):
            return i
    return None


# ---- the document -------------------------------------------------------------------------

def bake(session, base, shape, name=None, consumed=(), keep=False):
    """Put a modelling result in the document as a plain shape: into the base
    when it is one (a Part::Feature) and no new name is asked, else as a new
    Part::Feature under the base's label or the name; a PartDesign Body
    gives a new Body on the shape, the old one kept hidden. The inputs are
    then removed, or hidden with keep. Returns the result's object."""
    doc = session.doc
    shape = geo.clean(shape)
    gone = [o for o in consumed if o is not None]
    if base is not None and base.TypeId == "PartDesign::Body":
        # A PartDesign Body is never replaced by a plain shape (nor deleted
        # with its history): the result is a new Body built on the shape
        # (its base feature), so modelling goes on in PartDesign, and the
        # old Body stays, hidden, as the input.
        label = name or base.Label
        if name is None:
            base.Label = base.Label + " (input)"
        holder = doc.addObject("Part::Feature", "BaseShape")
        holder.Shape = shape
        holder.Label = session.unique(f"{label} base shape")
        out = doc.addObject("PartDesign::Body", "Body")
        out.BaseFeature = holder
        out.Label = label if name is None else session.unique(label)
        for o in (holder, base):
            if hasattr(o, "Visibility"):
                o.Visibility = False
        for o in gone:
            if o is base:
                continue
            if keep or o.InList:
                if hasattr(o, "Visibility"):
                    o.Visibility = False
            else:
                doc.removeObject(o.Name)
        doc.recompute()
        return out
    if name is None and base is not None and base.TypeId == "Part::Feature":
        base.Shape = shape
        out = base
        gone = [o for o in gone if o is not base]
    else:
        label = name or (base.Label if base is not None else "Body")
        if name is None and base is not None:
            gone = [base] + [o for o in gone if o is not base]
            base.Label = base.Label + " (input)"  # frees the label
        out = doc.addObject("Part::Feature", "Body")
        out.Shape = shape
        out.Label = label if name is None else session.unique(label)
    for o in gone:
        if keep or o.InList:
            if hasattr(o, "Visibility"):
                o.Visibility = False
        else:
            doc.removeObject(o.Name)
    doc.recompute()
    return out


def repoint(doc, old, new):
    """Every link to old (links, link lists, sub-element links, groups,
    expressions) pointed at new instead, so replacing an object leaves
    nothing dangling. Returns the labels of the objects that changed."""
    if old is None or new is None or old is new:
        return []

    def swap(v):
        if v is old:
            return new, True
        if isinstance(v, (list, tuple)):
            out, hit = [], False
            for x in v:
                y, h = swap(x)
                out.append(y)
                hit = hit or h
            return (type(v)(out) if isinstance(v, tuple) else out), hit
        return v, False

    changed = []
    for p in list(old.InList):
        if p is new:
            continue
        hit_any = False
        for prop in p.PropertiesList:
            try:
                t = p.getTypeIdOfProperty(prop)
            except Exception:
                continue
            if not (t.startswith("App::PropertyLink") or t.startswith("App::PropertyXLink")):
                continue
            try:
                v = p.getPropertyByName(prop)
            except Exception:
                continue
            nv, hit = swap(v)
            if hit:
                try:
                    setattr(p, prop, nv)
                    hit_any = True
                except Exception:
                    pass
        for path, expr in list(getattr(p, "ExpressionEngine", []) or []):
            if re.search(r"(?<![\w<])" + re.escape(old.Name) + r"\.", expr) or ("<<" + old.Label + ">>") in expr:
                e2 = re.sub(r"(?<![\w<])" + re.escape(old.Name) + r"\.", new.Name + ".", expr).replace("<<" + old.Label + ">>", new.Name)
                try:
                    p.setExpression(path, e2)
                    hit_any = True
                except Exception:
                    pass
        if hit_any:
            changed.append(p.Label)
    return changed


def json_safe(v, depth=0):
    """A property's value as JSON: vectors as lists, placements, quantities
    as numbers in the document's units, objects by label, shapes as stats."""
    if depth > 3:
        return str(v)
    if v is None or isinstance(v, (bool, int, str)):
        return v
    if isinstance(v, float):
        return round(v, 6) if math.isfinite(v) else str(v)
    if isinstance(v, FreeCAD.Vector):
        return geo.vec(v)
    if isinstance(v, FreeCAD.Placement):
        return placement_json(v)
    if isinstance(v, FreeCAD.Rotation):
        return {"axis": geo.vec(v.Axis), "angle": round(math.degrees(v.Angle), 4)}
    if hasattr(FreeCAD, "Units") and isinstance(v, FreeCAD.Units.Quantity):
        return round(v.Value, 6)
    if isinstance(v, Part.Shape):
        return geo.stats(v) if v.Solids else {"null": v.isNull(), "faces": len(v.Faces), "edges": len(v.Edges)} if not v.isNull() else None
    if hasattr(v, "TypeId") and hasattr(v, "Label") and hasattr(v, "Document"):
        return v.Label
    if isinstance(v, (list, tuple)):
        out = [json_safe(x, depth + 1) for x in list(v)[:50]]
        if len(v) > 50:
            out.append(f"… {len(v) - 50} more")
        return out
    if isinstance(v, dict):
        return {str(k): json_safe(x, depth + 1) for k, x in list(v.items())[:50]}
    if isinstance(v, bytes):
        return f"<{len(v)} bytes>"
    return str(v)
