# SPDX-License-Identifier: LGPL-2.1-or-later
# PartDesign features, one call each: pad, pocket, revolution, groove,
# hole, fillet, chamfer, draft, thickness, mirror and patterns, loft, pipe,
# datum planes; and the body's history — list, edit, remove, suppress.
# Faces and edges are named by where they are, not by index. A feature
# that fails is refused with FreeCAD's own error and nothing is kept.

import FreeCAD

from ..protocol import Tool, ToolError
from . import pd_common as pc

GROUP = "feature"
INFO = "PartDesign features (pad, pocket, revolve, hole, fillet, chamfer, draft, shell, patterns, loft, pipe, datum planes) and the body's feature history."

V = FreeCAD.Vector
KEYS = ("SideType", "Type", "Type2", "Length", "Length2", "Midplane", "Reversed", "Angle", "Angle2", "Radius", "Size", "Value", "Occurrences",
        "Diameter", "Depth", "DepthType", "Threaded", "ThreadType", "ThreadSize", "HoleCutType", "HoleCutDiameter", "HoleCutDepth", "Ruled", "Closed", "Mode", "Suppressed", "Refine")


def obj(props, required=()):
    return {"type": "object", "properties": props, "required": list(required), "additionalProperties": False}


def has(o, p):
    return p in o.PropertiesList


def set_sides(f, side, length2=None):
    """one | two | symmetric, across releases: SideType where it exists, else
    Midplane / Type TwoLengths."""
    if side in (None, "one"):
        return
    if has(f, "SideType"):
        f.SideType = {"two": "Two sides", "symmetric": "Symmetric"}[side]
    elif side == "symmetric":
        f.Midplane = True
    else:
        f.Type = "TwoLengths"
    if side == "two" and length2 is not None:
        f.Length2 = float(length2)


def key_values(f):
    out = {}
    for k in KEYS:
        if has(f, k):
            try:
                v = getattr(f, k)
            except Exception:
                continue
            v = pc.num(v)
            if k in ("Suppressed", "Refine", "Midplane", "Reversed", "Threaded", "Ruled", "Closed") and not v:
                continue
            if k in ("Length2", "Type2", "Angle2") and not (has(f, "SideType") and f.SideType == "Two sides"):
                continue
            out[k[0].lower() + k[1:]] = v
    for k in ("Profile",):
        if has(f, k) and getattr(f, k):
            p = getattr(f, k)
            out["profile"] = (p[0] if isinstance(p, tuple) else p).Label
    if has(f, "Originals") and f.Originals:
        out["originals"] = [o.Label for o in f.Originals]
    if has(f, "Base") and f.isDerivedFrom("PartDesign::DressUp") and f.Base:
        out["on"] = list(f.Base[1])
    bound = {p.lstrip("."): e for p, e in getattr(f, "ExpressionEngine", [])}
    if bound:
        out["bound"] = bound
    return out


def tools(session):
    s = session
    doc = lambda: s.doc  # noqa: E731

    def body_sketch(a, key="sketch"):
        b = pc.pd_body(doc(), a.get("body"))
        sk = pc.sketch_of(doc(), a.get(key), b)
        if not b.hasObject(sk):
            raise ToolError("invalid_input", f"{sk.Label} is not in {b.Label}: a feature takes a sketch of its own body.")
        return b, sk

    def sketch_note(sk):
        dof = getattr(sk, "DoF", None)
        return {"sketchDof": dof} if dof else {}

    def new(b, kind, label):
        label = s.unique(label)
        f = b.newObject(kind, kind.split("::")[-1])
        f.Label = label
        # Some releases leave the tip where it was for a pattern: the body
        # is the new feature's shape from here on.
        if kind != "PartDesign::Plane" and b.Tip != f:
            b.Tip = f
        return f

    def sketched(kind, additive):
        tool = {"Pad": "pd_pad", "Pocket": "pd_pocket"}[kind]

        def run(a):
            b, sk = body_sketch(a)
            before = pc.volume(b)
            tip = b.Tip if (b.Tip is not None and not b.Tip.Shape.isNull() and b.Tip.Shape.Solids) else None
            if not additive and tip is None:
                raise ToolError("invalid_state", f"{b.Label} has no solid to pocket: pd_pad first.")
            up_face = None
            if a.get("upToFace") is not None:
                if tip is None:
                    raise ToolError("invalid_state", "upToFace: the body has no faces yet.")
                up_face = pc.find_faces(tip.Shape, [a["upToFace"]])[0]
            f = new(b, f"PartDesign::{kind}", kind)
            f.Profile = sk
            typ = a.get("type", "length")
            types = {"length": "Length", "through_all": "ThroughAll", "up_to_face": "UpToFace", "up_to_first": "UpToFirst", "up_to_last": "UpToLast"}
            if typ not in types or (additive and typ == "through_all") or (not additive and typ == "up_to_last"):
                raise ToolError("invalid_input", f"type: length, {'up_to_last' if additive else 'through_all'}, up_to_face, up_to_first.")
            if typ == "up_to_face":
                if up_face is None:
                    raise ToolError("invalid_input", "type up_to_face: give upToFace { at, normal }.")
                f.Type = "UpToFace"
                f.UpToFace = (tip, [up_face])
            elif typ != "length":
                f.Type = types[typ]
            if typ == "length":
                L = a.get("length")
                if not isinstance(L, (int, float)) or L <= 0:
                    raise ToolError("invalid_input", "length: mm, over 0 (or type through_all / up_to_face).")
                f.Length = float(L)
            set_sides(f, a.get("sides"), a.get("length2"))
            if a.get("reversed"):
                f.Reversed = True
            if a.get("taper") and has(f, "TaperAngle"):
                f.TaperAngle = float(a["taper"])
            out = pc.settle(s, b, f, tool, before, "add" if additive else "cut",
                            hint=("Check the sketch with sketch_state (closed wires, no open vertices)" + ("; reversed: true goes the other way into the body." if not additive else "; reversed: true goes the other way.")))
            out.update(sketch_note(sk))
            return out

        return run

    def revolved(kind):
        def run(a):
            b, sk = body_sketch(a)
            before = pc.volume(b)
            if kind == "Groove" and before <= 0:
                raise ToolError("invalid_state", f"{b.Label} has no solid to groove: pd_pad or pd_revolution first.")
            f = new(b, f"PartDesign::{kind}", kind)
            f.Profile = sk
            f.ReferenceAxis = pc.axis_ref(b, a.get("axis", "V_Axis"), sk)[0]
            f.Angle = float(a.get("angle", 360))
            set_sides(f, a.get("sides"))
            if a.get("reversed"):
                f.Reversed = True
            out = pc.settle(s, b, f, "pd_" + kind.lower(), before, "add" if kind == "Revolution" else "cut",
                            hint="The profile must lie wholly on one side of the axis.")
            out.update(sketch_note(sk))
            return out

        return run

    def pd_hole(a):
        b, sk = body_sketch(a)
        tip, _ = pc.tip_shape(b)
        before = pc.volume(b)
        circles = [g for g in sk.Geometry if type(g).__name__ in ("Circle", "ArcOfCircle")]
        if not circles:
            raise ToolError("invalid_input", f"{sk.Label} has no circles: a hole is drilled at each circle's centre — sketch_add circles on the face first.")
        f = new(b, "PartDesign::Hole", "Hole")
        f.Profile = sk
        thread = a.get("thread")
        if thread:
            f.ThreadType = "ISOMetricProfile"
            sizes = list(f.getEnumerationsOfProperty("ThreadSize"))
            # "M5" is the coarse M5 (named "M5" or "M5x0.8" by release); a pitch given picks it.
            pick = next((z for z in sizes if z == thread), None) or next((z for z in sizes if z.startswith(f"{thread}x")), None)
            if pick is None:
                f.ThreadType = "ISOMetricFineProfile"
                fine = list(f.getEnumerationsOfProperty("ThreadSize"))
                pick = next((z for z in fine if z == thread), None)
                if pick is None:
                    raise ToolError("invalid_input", f"thread {thread!r}: not an ISO size — e.g. {', '.join(sizes[6:16])} …; fine: {', '.join(fine[10:14])} …")
            f.ThreadSize = pick
            f.Threaded = bool(a.get("threaded", True))
            if f.Threaded and has(f, "ModelThread"):
                f.ModelThread = bool(a.get("modelThread", False))
            if not f.Threaded and a.get("fit") and has(f, "ThreadFit"):
                fits = list(f.getEnumerationsOfProperty("ThreadFit"))
                want = {"close": ("Fine", "Close"), "normal": ("Medium", "Normal"), "loose": ("Coarse", "Loose", "Wide")}.get(a["fit"], ())
                pick = next((x for x in want if x in fits), None)
                if pick:
                    f.ThreadFit = pick
        else:
            d = a.get("diameter")
            if not isinstance(d, (int, float)) or d <= 0:
                raise ToolError("invalid_input", "diameter: mm (or thread: \"M5\" for a standard size).")
            f.Diameter = float(d)
        depth = a.get("depth", "through")
        if depth == "through":
            f.DepthType = "ThroughAll"
        else:
            f.DepthType = "Dimension"
            f.Depth = float(depth)
        f.DrillPoint = "Angled" if a.get("drillPoint", "flat") == "angled" else "Flat"
        cut = a.get("cut")
        if cut:
            opts = list(f.getEnumerationsOfProperty("HoleCutType"))
            want = cut.capitalize()
            if want not in opts:
                raise ToolError("invalid_input", f"cut: {', '.join(o.lower() for o in opts if o != 'None')}.")
            f.HoleCutType = want
            if any(k in a for k in ("cutDiameter", "cutDepth", "countersinkAngle")) or not thread:
                if has(f, "HoleCutCustomValues"):
                    f.HoleCutCustomValues = True
                if "cutDiameter" in a:
                    f.HoleCutDiameter = float(a["cutDiameter"])
                elif not thread:
                    raise ToolError("invalid_input", "cutDiameter: mm — needed for a counterbore/countersink on a plain diameter (a thread size gives its own).")
                if "cutDepth" in a:
                    f.HoleCutDepth = float(a["cutDepth"])
                if "countersinkAngle" in a:
                    f.HoleCutCountersinkAngle = float(a["countersinkAngle"])
        if a.get("reversed"):
            f.Reversed = True
        out = pc.settle(s, b, f, "pd_hole", before, "cut", hint="The hole goes against the sketch's normal: put the sketch on the face it enters, or reversed: true.")
        out["holes"] = len(circles)
        out["diameter"] = pc.num(f.Diameter)
        if thread:
            out["thread"] = f.ThreadSize + (" (cosmetic)" if f.Threaded and not getattr(f, "ModelThread", False) else "")
        return out

    def dressup(kind):
        def run(a):
            b = pc.pd_body(doc(), a.get("body"))
            tip, shape = pc.tip_shape(b)
            before = pc.volume(b)
            if kind in ("Fillet", "Chamfer"):
                subs = pc.find_edges(shape, a.get("edges"))
            else:
                subs = pc.find_faces(shape, a.get("faces"))
            f = new(b, f"PartDesign::{kind}", kind)
            f.Base = (tip, subs)
            tool = {"Fillet": "pd_fillet", "Chamfer": "pd_chamfer", "Draft": "pd_draft", "Thickness": "pd_thickness"}[kind]
            if kind == "Fillet":
                f.Radius = float(a.get("radius", 1))
                hint = "A radius larger than a face beside the edge fails: try smaller, or fewer edges."
            elif kind == "Chamfer":
                f.Size = float(a.get("size", 1))
                if "size2" in a:
                    f.ChamferType = "Two distances"
                    f.Size2 = float(a["size2"])
                elif "angle" in a:
                    f.ChamferType = "Distance and Angle"
                    f.Angle = float(a["angle"])
                hint = "A chamfer wider than a face beside the edge fails: try smaller."
            elif kind == "Draft":
                f.Angle = float(a.get("angle", 1.5))
                f.NeutralPlane = pc.plane_ref(b, a.get("neutral", "XY"), "neutral")[0]
                f.PullDirection = pc.axis_ref(b, a.get("pull", "z"), None, "pull")[0]
                if a.get("reversed"):
                    f.Reversed = True
                hint = "Draft takes faces that meet the neutral plane; reversed: true tilts them the other way."
            else:
                f.Value = float(a.get("thickness", 1))
                f.Reversed = not a.get("outward", False)
                f.Join = "Intersection" if a.get("join") == "intersection" else "Arc"
                hint = "A wall thicker than a fillet's radius or a thin feature fails: thinner, or join: intersection."
            change = "cut" if kind in ("Fillet", "Chamfer") or (kind == "Thickness" and not a.get("outward")) else None
            out = pc.settle(s, b, f, tool, before, change, hint=hint)
            out["on"] = subs
            return out

        return run

    def originals(b, refs):
        if not isinstance(refs, list) or not refs:
            raise ToolError("invalid_input", "features: [names] — the features to repeat (feature_list names them).")
        return [pc.in_body(b, r, ["PartDesign::"]) for r in refs]

    def transformed(kind):
        def run(a):
            b = pc.pd_body(doc(), a.get("body"))
            pc.tip_shape(b)
            before = pc.volume(b)
            orig = originals(b, a.get("features"))
            f = new(b, f"PartDesign::{kind}", kind)
            f.Originals = orig
            if kind == "Mirrored":
                f.MirrorPlane = pc.plane_ref(b, a.get("plane", "YZ"), "plane")[0]
                tool = "pd_mirrored"
            else:
                n = int(a.get("occurrences", 2))
                if n < 2:
                    raise ToolError("invalid_input", "occurrences: 2 or more, the original counted.")
                f.Occurrences = n
                if kind == "LinearPattern":
                    f.Direction = pc.axis_ref(b, a.get("direction", "x"), None, "direction")[0]
                    if "spacing" in a:
                        f.Length = float(a["spacing"]) * (n - 1)
                    elif "length" in a:
                        f.Length = float(a["length"])
                    else:
                        raise ToolError("invalid_input", "spacing (mm between copies) or length (mm, first to last).")
                    tool = "pd_linear_pattern"
                else:
                    f.Axis = pc.axis_ref(b, a.get("axis", "z"), None, "axis")[0]
                    f.Angle = float(a.get("angle", 360))
                    tool = "pd_polar_pattern"
                if a.get("reversed"):
                    f.Reversed = True
            out = pc.settle(s, b, f, tool, before, None, hint="Each copy must meet the body (a pattern of a pocket must land on material).")
            return out

        return run

    def swept(kind):
        def run(a):
            b, sk = body_sketch(a, "profile")
            before = pc.volume(b)
            sub = bool(a.get("subtractive"))
            if sub and before <= 0:
                raise ToolError("invalid_state", f"{b.Label} has no solid to cut: pad something first.")
            f = new(b, f"PartDesign::{'Subtractive' if sub else 'Additive'}{kind}", kind)
            f.Profile = sk
            if kind == "Loft":
                secs = [pc.sketch_of(doc(), r, b) for r in (a.get("sections") or [])]
                if not secs:
                    raise ToolError("invalid_input", "sections: [sketch names] — the profiles after the first, in order.")
                f.Sections = secs
                f.Ruled = bool(a.get("ruled", False))
                f.Closed = bool(a.get("closed", False))
                hint = "Sections must be closed and should have the same number of edges; ruled: true for straight sides."
            else:
                path = pc.sketch_of(doc(), a.get("path"), b)
                f.Spine = (path, [f"Edge{i + 1}" for i in range(len(path.Shape.Edges))])
                hint = "The profile should sit at the path's start, across it; a path that turns tighter than the profile fails."
            out = pc.settle(s, b, f, f"pd_{kind.lower()}", before, "cut" if sub else "add", hint=hint)
            return out

        return run

    def pd_datum_plane(a):
        b = pc.pd_body(doc(), a.get("body"))
        base, what = pc.plane_ref(b, a.get("on", "XY"), "on")
        f = new(b, "PartDesign::Plane", a.get("name") or "DatumPlane")
        setattr(f, pc.support_prop(f), [(base[0], base[1][0])])
        f.MapMode = "FlatFace"
        rot = FreeCAD.Rotation()
        if a.get("rotate"):
            r = a["rotate"]
            ax = pc.vec3(r.get("axis", [1, 0, 0]), "rotate.axis")
            rot = FreeCAD.Rotation(ax, float(r.get("angle", 0)))
        f.AttachmentOffset = FreeCAD.Placement(V(0, 0, float(a.get("offset", 0))), rot)
        doc().recompute()
        if not f.isValid():
            raise ToolError("failed", f"The datum plane would not attach to {what}: {f.getStatusString()}.")
        s.snapshot("pd_datum_plane")
        pl = f.Placement
        return {"plane": f.Label, "on": what, "origin": pc.geo.vec(pl.Base), "normal": pc.geo.vec(pl.Rotation.multVec(V(0, 0, 1))), "next": f"sketch_new {{ plane: \"{f.Label}\" }}", "step": len(s.steps)}

    def feature_list(a):
        b = pc.pd_body(doc(), a.get("body"))
        out = []
        for o in b.Group:
            if o.TypeId.startswith("App::Origin"):
                continue
            ok = o.isValid()
            e = {"name": o.Label, "type": pc.short(o), "valid": ok}
            if not ok:
                e["error"] = o.getStatusString()
            if o.TypeId == "Sketcher::SketchObject":
                e["dof"] = getattr(o, "DoF", None)
                e["geometry"] = len(o.Geometry)
            else:
                e.update(key_values(o))
            if o == b.Tip:
                e["tip"] = True
            out.append(e)
        return {"body": b.Label, "features": out, **pc.body_reading(b)}

    def feature_edit(a):
        b = pc.pd_body(doc(), a.get("body"))
        f = pc.in_body(b, a.get("feature"), ["PartDesign::"])
        changes = a.get("set")
        if not isinstance(changes, dict) or not changes:
            raise ToolError("invalid_input", f"set: {{ property: value }} — {f.Label}'s: {', '.join(k for k in KEYS if has(f, k))} (FreeCAD's property names).")
        before = pc.volume(b)
        refs = pc.ref_snapshot(b)
        bound = {p.lstrip("."): e for p, e in getattr(f, "ExpressionEngine", [])}
        done = {}
        for k, v in changes.items():
            if not has(f, k):
                raise ToolError("invalid_input", f"{k}: not a property of {f.Label} — e.g. {', '.join(x for x in KEYS if has(f, x))}.")
            if k in bound:
                raise ToolError("invalid_state", f"{f.Label}.{k} is bound to {bound[k]!r}: param_set the parameter, or param_bind {{ target: \"{f.Label}.{k}\", expression: null }} first.")
            if k in ("Profile", "Placement", "BaseFeature", "Originals", "Base") or f.getTypeIdOfProperty(k).startswith("App::PropertyLink"):
                raise ToolError("invalid_input", f"{k}: links are not edited here — remove the feature and make it again.")
            try:
                enums = f.getEnumerationsOfProperty(k)
            except Exception:
                enums = None
            if enums and v not in enums:
                raise ToolError("invalid_input", f"{k}: one of {', '.join(e for e in enums if not e.startswith('?'))}.")
            try:
                setattr(f, k, v)
            except Exception as e:
                raise ToolError("invalid_input", f"{k} = {v!r}: {e}")
            done[k] = pc.num(getattr(f, k))
        doc().recompute()
        repaired = pc.repair_refs(doc(), refs) if pc.broken(doc()) and refs else None
        out = pc.settle(s, b, f, "feature_edit", before)
        out["set"] = done
        if repaired:
            out["refsFound"] = repaired
            out["note"] = "The change renamed edges or faces later features use: each was found again by what it is (kind, size, place, direction). Check them with view_render."
        return out

    def feature_remove(a):
        b = pc.pd_body(doc(), a.get("body"))
        f = pc.in_body(b, a.get("feature"), ["PartDesign::", "Sketcher::"])
        users = [o.Label for o in f.InList if o != b and o.TypeId != "PartDesign::Body" and not (getattr(o, "BaseFeature", None) == f)]
        if users and not a.get("force"):
            raise ToolError("invalid_state", f"{f.Label} is used by {', '.join(users)}: remove those first (or force: true and see what breaks).")
        prof = getattr(f, "Profile", None)
        sk = (prof[0] if isinstance(prof, tuple) else prof) if prof else None
        before = pc.volume(b)
        label = f.Label
        b.removeObject(f)
        doc().removeObject(f.Name)
        removed = [label]
        if sk is not None and not a.get("keepSketch") and sk.TypeId == "Sketcher::SketchObject" and not [o for o in sk.InList if o != b]:
            l2 = sk.Label
            b.removeObject(sk)
            doc().removeObject(sk.Name)
            removed.append(l2)
        out = pc.settle(s, b, None, "feature_remove", before, None)
        out["removed"] = removed
        return out

    def feature_suppress(a):
        b = pc.pd_body(doc(), a.get("body"))
        f = pc.in_body(b, a.get("feature"), ["PartDesign::"])
        if not has(f, "Suppressed"):
            raise ToolError("unavailable", f"This FreeCAD ({'.'.join(FreeCAD.Version()[:3])}) has no Suppressed property on features: feature_remove it, or history_checkpoint then remove and history_restore.")
        before = pc.volume(b)
        f.Suppressed = bool(a.get("suppressed", True))
        out = pc.settle(s, b, f, "feature_suppress", before)
        out["suppressed"] = bool(f.Suppressed)
        return out

    body = {"type": "string", "description": "The PartDesign body; may be left out when there is one."}
    sketch = {"type": "string", "description": "The sketch, in the body."}
    xyz = {"type": "array", "items": {"type": "number"}}
    face = {"type": "object", "description": "{ at: [x,y,z] a point on the face, normal: [x,y,z] outward }", "properties": {"at": xyz, "normal": xyz, "tol": {"type": "number"}}, "required": ["at"], "additionalProperties": False}
    faces = {"type": "array", "items": face, "description": "faces by where they are: [{ at, normal }]"}
    plane = {"description": "XY | XZ | YZ, a datum plane's name, or a face { at, normal }"}
    feats = {"type": "array", "items": {"type": "string"}}
    reading = " Answers the body's volume (mm³), faces and size after; a failure is refused with FreeCAD's error and nothing kept."
    ext = {"body": body, "sketch": sketch, "type": {"type": "string"}, "length": {"type": "number"}, "length2": {"type": "number"}, "sides": {"type": "string", "enum": ["one", "two", "symmetric"]},
           "reversed": {"type": "boolean"}, "upToFace": face, "taper": {"type": "number"}}
    return [
        Tool("pd_pad", "Pad a closed sketch into the body: type length (length mm) | up_to_last | up_to_first | up_to_face (upToFace { at, normal }); sides one | two (length2) | symmetric; reversed; taper deg. A pad inside the body already adds nothing and is refused; an open sketch fails — sketch_state first." + reading, obj(ext), sketched("Pad", True), changes=True),
        Tool("pd_pocket", "Pocket a closed sketch out of the body: type length | through_all | up_to_first | up_to_face; sides; reversed. It cuts against the sketch's normal: a pocket that cuts nothing (pointing away from the material) is refused — reversed: true, or sketch on the face it enters." + reading, obj(ext), sketched("Pocket", False), changes=True),
        Tool("pd_revolution", "Revolve a sketch about an axis: axis V_Axis (default) | H_Axis (the sketch's) | x | y | z (the body's) | a line's geometry id; angle deg (360); sides; reversed. The profile must not cross the axis." + reading,
             obj({"body": body, "sketch": sketch, "axis": {"type": ["string", "integer"]}, "angle": {"type": "number"}, "sides": {"type": "string"}, "reversed": {"type": "boolean"}}), revolved("Revolution"), changes=True),
        Tool("pd_groove", "Cut by revolving a sketch about an axis (as pd_revolution). A groove that cuts nothing is refused." + reading,
             obj({"body": body, "sketch": sketch, "axis": {"type": ["string", "integer"]}, "angle": {"type": "number"}, "sides": {"type": "string"}, "reversed": {"type": "boolean"}}), revolved("Groove"), changes=True),
        Tool("pd_hole", "Holes at the centre of every circle of a sketch on the face they enter, drilled against its normal: diameter mm, or thread \"M5\" (ISO; \"M8x1\" fine) — threaded (default true; cosmetic unless modelThread) or a clearance hole (threaded: false, fit close|normal|loose); depth mm | \"through\"; drillPoint flat|angled; cut counterbore|countersink with cutDiameter, cutDepth, countersinkAngle (a thread size gives standard ones)." + reading,
             obj({"body": body, "sketch": sketch, "diameter": {"type": "number"}, "thread": {"type": "string"}, "threaded": {"type": "boolean"}, "modelThread": {"type": "boolean"}, "fit": {"type": "string"}, "depth": {"type": ["number", "string"]}, "drillPoint": {"type": "string"}, "cut": {"type": "string"},
                  "cutDiameter": {"type": "number"}, "cutDepth": {"type": "number"}, "countersinkAngle": {"type": "number"}, "reversed": {"type": "boolean"}}), pd_hole, changes=True),
        Tool("pd_fillet", "Round edges of the body: edges [{ near: [x,y,z] } the edge nearest a point | { face: { at, normal } } every edge of a face | { all: true }, kind: line|circle to filter], radius mm. Refer to edges by where they are — never by index, which changes after every edit." + reading,
             obj({"body": body, "edges": {"type": "array", "items": {"type": "object"}}, "radius": {"type": "number"}}, ["edges"]), dressup("Fillet"), changes=True),
        Tool("pd_chamfer", "Bevel edges of the body: edges as pd_fillet; size mm; size2 for two distances or angle deg for distance and angle." + reading,
             obj({"body": body, "edges": {"type": "array", "items": {"type": "object"}}, "size": {"type": "number"}, "size2": {"type": "number"}, "angle": {"type": "number"}}, ["edges"]), dressup("Chamfer"), changes=True),
        Tool("pd_draft", "Taper faces for moulding: faces [{ at, normal }], angle deg, neutral plane (XY default, a datum plane, or a face query), pull direction x|y|z, reversed." + reading,
             obj({"body": body, "faces": faces, "angle": {"type": "number"}, "neutral": plane, "pull": {"type": "string"}, "reversed": {"type": "boolean"}}, ["faces"]), dressup("Draft"), changes=True),
        Tool("pd_thickness", "Shell the body: faces [{ at, normal }] to open (removed), thickness mm of the wall, inward unless outward: true; join arc|intersection." + reading,
             obj({"body": body, "faces": faces, "thickness": {"type": "number"}, "outward": {"type": "boolean"}, "join": {"type": "string"}}, ["faces"]), dressup("Thickness"), changes=True),
        Tool("pd_mirrored", "Mirror features (by name) across a plane: XY | XZ | YZ, a datum plane, or a face query. The copies must meet the body." + reading,
             obj({"body": body, "features": feats, "plane": plane}, ["features"]), transformed("Mirrored"), changes=True),
        Tool("pd_linear_pattern", "Repeat features in a line: direction x|y|z, occurrences (the original counted), spacing mm between copies or length mm first to last, reversed." + reading,
             obj({"body": body, "features": feats, "direction": {"type": "string"}, "occurrences": {"type": "integer"}, "spacing": {"type": "number"}, "length": {"type": "number"}, "reversed": {"type": "boolean"}}, ["features"]), transformed("LinearPattern"), changes=True),
        Tool("pd_polar_pattern", "Repeat features around an axis: axis x|y|z (the body's), occurrences, angle deg (360 spreads them evenly round), reversed." + reading,
             obj({"body": body, "features": feats, "axis": {"type": "string"}, "occurrences": {"type": "integer"}, "angle": {"type": "number"}, "reversed": {"type": "boolean"}}, ["features"]), transformed("PolarPattern"), changes=True),
        Tool("pd_loft", "Loft through sketches: profile (the first), sections [the rest, in order], ruled, closed; subtractive: true cuts it. Sketches should have the same edge count." + reading,
             obj({"body": body, "profile": sketch, "sections": {"type": "array", "items": {"type": "string"}}, "ruled": {"type": "boolean"}, "closed": {"type": "boolean"}, "subtractive": {"type": "boolean"}}, ["profile", "sections"]), swept("Loft"), changes=True),
        Tool("pd_pipe", "Sweep a profile sketch along a path sketch (all its edges); subtractive: true cuts it. The profile should sit across the path's start." + reading,
             obj({"body": body, "profile": sketch, "path": sketch, "subtractive": {"type": "boolean"}}, ["profile", "path"]), swept("Pipe"), changes=True),
        Tool("pd_datum_plane", "A datum plane in the body, on XY | XZ | YZ | a face query, offset mm along its normal, rotate { axis [x,y,z] (its own), angle deg }: for sketches off the body's planes.",
             obj({"body": body, "on": plane, "offset": {"type": "number"}, "rotate": {"type": "object"}, "name": {"type": "string"}}), pd_datum_plane, changes=True),
        Tool("feature_list", "The body's features in order: type, key values (lengths mm, angles deg, types), what each is bound to, valid or not with FreeCAD's error, the tip; sketches with their dof. Changes nothing.", obj({"body": body}), feature_list),
        Tool("feature_edit", "Change a feature's properties by FreeCAD's names: set { Length: 20, Type: \"ThroughAll\", Reversed: true, Radius: 2, Occurrences: 4, … } (feature_list shows them); recomputes. A change that breaks it or a later feature is refused with FreeCAD's error and undone. A property bound to a parameter is changed with param_set.",
             obj({"body": body, "feature": {"type": "string"}, "set": {"type": "object"}}, ["feature", "set"]), feature_edit, changes=True),
        Tool("feature_remove", "Remove a feature (and its sketch unless keepSketch, when nothing else uses it). One that others use (a sketch on its face, a pattern of it) is refused unless force. Later features that break are refused and nothing kept.",
             obj({"body": body, "feature": {"type": "string"}, "keepSketch": {"type": "boolean"}, "force": {"type": "boolean"}}, ["feature"]), feature_remove, changes=True),
        Tool("feature_suppress", "Suppress a feature (suppressed: false brings it back): the body is computed as if it were not there, and it stays in the history.",
             obj({"body": body, "feature": {"type": "string"}, "suppressed": {"type": "boolean"}}, ["feature"]), feature_suppress, changes=True),
    ]
