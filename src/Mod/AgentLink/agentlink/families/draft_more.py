# SPDX-License-Identifier: LGPL-2.1-or-later
# The Draft workbench's own parametric objects, beside draft.py's lettering,
# outlines and arrays: 2D shapes (wire, rectangle, circle, arc, ellipse,
# polygon, B-spline, Bezier, point), dimensions and labels, link arrays
# (ortho, polar, circular, along a path, at points), clones, the modify
# tools (move, rotate, scale, mirror, offset, upgrade, downgrade, join,
# 2D fillet), layers, hatches, Draft and Sketch each way, 2D views of a
# body and facebinders. Draft runs headless; its objects open in the GUI.

import os

import FreeCAD
import Draft

from ..protocol import Tool, ToolError
from ..tools import obj
from . import picks

GROUP = "draft"
INFO = ("Draft: lettering, SVG/DXF outlines, arrays, and Draft's own parametric 2D shapes, dimensions, labels, link arrays, clones, "
        "move/rotate/scale/mirror/offset, upgrade/downgrade, layers, hatches, Draft↔Sketch, 2D views, facebinders; surfaces and textures.")

V = FreeCAD.Vector


def bad(msg):
    return ToolError("invalid_input", msg)


def v3(a, what, default=None):
    return picks.vec3(a, what, default)


def pl_of(a):
    n = picks.direction(a.get("normal"), "normal", (0, 0, 1))
    return FreeCAD.Placement(v3(a.get("at"), "at", (0, 0, 0)), FreeCAD.Rotation(V(0, 0, 1), n))


def reading(o):
    sh = getattr(o, "Shape", None)
    out = {"object": o.Label, "type": (getattr(getattr(o, "Proxy", None), "Type", None) or o.TypeId.split("::")[-1])}
    if sh is not None and not sh.isNull():
        if sh.Edges:
            out["length"] = round(sum(e.Length for e in sh.Edges), 4)
        if sh.Faces:
            out["area"] = round(sh.Area, 4)
        if sh.Solids:
            out["volume"] = round(sh.Volume, 4)
        bb = sh.BoundBox
        out["box"] = [round(bb.XLength, 3), round(bb.YLength, 3), round(bb.ZLength, 3)]
    return out


def tools(session):
    s = session
    doc = lambda: s.doc  # noqa: E731

    def active():
        # Draft makes objects in the active document
        if FreeCAD.ActiveDocument is not doc():
            FreeCAD.setActiveDocument(doc().Name)

    def named(o, name, default):
        # unique among the others: Draft has already given it a label of its own
        if o is not None:
            want = name or default
            taken = {x.Label for x in doc().Objects if x is not o}
            lab, n = want, 2
            while lab in taken:
                lab = f"{want} {n}"
                n += 1
            o.Label = lab
        return o

    def finish(objs, tool, extra=None):
        doc().recompute()
        objs = [o for o in (objs if isinstance(objs, list) else [objs]) if o is not None]
        if not objs:
            raise ToolError("failed", "Draft made nothing from that.")
        bad_ = [o for o in objs if hasattr(o, "isValid") and not o.isValid()]
        if bad_:
            msg = bad_[0].getStatusString()
            for o in objs:
                try:
                    doc().removeObject(o.Name)
                except Exception:
                    pass
            raise ToolError("failed", f"Draft refused it: {msg} — nothing was kept.")
        s.snapshot(tool)
        out = reading(objs[0]) if len(objs) == 1 else {"objects": [reading(o) for o in objs]}
        out.update(extra or {})
        out["step"] = len(s.steps)
        return out

    def objs_of(refs, what="objects"):
        if isinstance(refs, str):
            refs = [refs]
        if not refs:
            raise bad(f"{what}: [names].")
        return [picks.find(doc(), r) for r in refs]

    # ---- shapes ----

    def draft_shape(a):
        active()
        kind = a.get("kind")
        face = a.get("face")
        pl = pl_of(a)
        if kind in ("wire", "bspline", "bezier"):
            pts = [v3(p, "points") for p in (a.get("points") or [])]
            if len(pts) < 2:
                raise bad("points: two or more [x, y, z].")
            fn = {"wire": Draft.make_wire, "bspline": Draft.make_bspline, "bezier": Draft.make_bezcurve}[kind]
            o = fn(pts, closed=bool(a.get("closed")), face=face)
        elif kind == "rectangle":
            o = Draft.make_rectangle(picks.num(a.get("length"), "length"), picks.num(a.get("height"), "height"), placement=pl, face=face)
        elif kind in ("circle", "arc"):
            r = picks.num(a.get("radius"), "radius")
            kw = {"placement": pl, "face": face}
            if kind == "arc":
                kw.update(startangle=float(a.get("start", 0)), endangle=float(a.get("end", 90)))
            o = Draft.make_circle(r, **kw)
        elif kind == "ellipse":
            o = Draft.make_ellipse(picks.num(a.get("major"), "major") / 2, picks.num(a.get("minor"), "minor") / 2, placement=pl, face=face)
        elif kind == "polygon":
            o = Draft.make_polygon(int(a.get("sides", 6)), picks.num(a.get("radius"), "radius"), inscribed=a.get("inscribed", True), placement=pl, face=face)
        elif kind == "point":
            p = v3(a.get("at"), "at")
            o = Draft.make_point(p.x, p.y, p.z)
        else:
            raise bad("kind: wire, rectangle, circle, arc, ellipse, polygon, bspline, bezier or point.")
        return finish(named(o, a.get("name"), kind.capitalize()), "draft_shape")

    # ---- dimensions and labels ----

    def draft_dimension(a):
        active()
        kind = a.get("kind", "linear")
        line = v3(a.get("at"), "at") if a.get("at") is not None else None
        if kind == "linear":
            if a.get("object"):
                o = picks.find(doc(), a["object"])
                i1, i2 = int(a.get("vertices", [1, 2])[0]), int(a.get("vertices", [1, 2])[1])
                d = Draft.make_linear_dimension_obj(o, i1, i2, line)
            else:
                d = Draft.make_linear_dimension(v3(a.get("from"), "from"), v3(a.get("to"), "to"), line)
        elif kind in ("radius", "diameter"):
            o = picks.find(doc(), a.get("object"))
            d = Draft.make_radial_dimension_obj(o, int(a.get("edge", 1)), kind, line)
        elif kind == "angular":
            d = Draft.make_angular_dimension(v3(a.get("center"), "center", (0, 0, 0)), [float(x) for x in (a.get("angles") or [0, 90])], line or V(10, 10, 0),
                                             picks.direction(a.get("normal"), "normal", (0, 0, 1)))
        else:
            raise bad("kind: linear (from, to — or object and vertices [i, j]), radius / diameter (object, edge), angular (center, angles [a, b]).")
        out = finish(named(d, a.get("name"), "Dimension"), "draft_dimension")
        try:
            out["measures"] = round(float(d.Distance), 4) if hasattr(d, "Distance") else round(float(d.Angle), 4)
        except Exception:
            pass
        return out

    def draft_label(a):
        active()
        tp = v3(a.get("target"), "target")
        at = v3(a.get("at"), "at", (tp.x + 20, tp.y + 20, tp.z))
        kw = {"target_point": tp, "placement": FreeCAD.Placement(at, FreeCAD.Rotation())}
        if a.get("object"):
            label, sub, _ = picks.resolve(s, a["object"])
            kw["target_object"] = picks.find(doc(), label)
            if sub:
                kw["subelements"] = sub
        show = a.get("show", "custom")
        kinds = {"custom": "Custom", "name": "Label", "area": "Area", "length": "Length", "position": "Position", "material": "Material", "tag": "Tag"}
        if show not in kinds:
            raise bad(f"show: {', '.join(kinds)}.")
        kw["label_type"] = kinds[show]
        if show == "custom":
            kw["custom_text"] = str(a.get("text") or "Label").split("\n")
        o = Draft.make_label(**kw)
        doc().recompute()
        text = [str(x) for x in (getattr(o, "Text", []) or []) if str(x)]
        if not text and show in ("area", "length", "position", "name") and kw.get("target_object") is not None:
            # Draft fills these in its window; here the same reading is written in.
            t_o = kw["target_object"]
            el = t_o.Shape.getElement(kw["subelements"]) if kw.get("subelements") else t_o.Shape
            text = [{"area": lambda: f"{el.Area:.2f} mm²", "length": lambda: f"{el.Length:.2f} mm",
                     "position": lambda: "{:.2f}, {:.2f}, {:.2f}".format(*el.BoundBox.Center),
                     "name": lambda: t_o.Label}[show]()]
            o.LabelType = "Custom"
            o.CustomText = text
        return finish(named(o, a.get("name"), "Label"), "draft_label", {"text": text})

    # ---- arrays and clones ----

    def draft_link_array(a):
        active()
        base = picks.find(doc(), a.get("object"))
        kind = a.get("kind", "ortho")
        link = bool(a.get("link", True))
        if kind == "ortho":
            n = [int(x) for x in (a.get("count") or [2, 2, 1])] + [1, 1, 1]
            sp = [float(x) for x in (a.get("spacing") or [10, 10, 10])] + [10, 10, 10]
            o = Draft.make_ortho_array(base, V(sp[0], 0, 0), V(0, sp[1], 0), V(0, 0, sp[2]), n[0], n[1], n[2], use_link=link)
        elif kind == "polar":
            o = Draft.make_polar_array(base, int(a.get("count", 6)), float(a.get("angle", 360)), v3(a.get("center"), "center", (0, 0, 0)), use_link=link)
        elif kind == "circular":
            o = Draft.make_circular_array(base, float(a.get("ringSpacing", 20)), float(a.get("tangentSpacing", 10)), int(a.get("rings", 3)), int(a.get("symmetry", 1)), use_link=link)
        elif kind == "path":
            path = picks.find(doc(), a.get("path"))
            o = Draft.make_path_array(base, path, int(a.get("count", 4)), align=bool(a.get("align")), use_link=link)
        elif kind == "points":
            pts = picks.find(doc(), a.get("points"))
            o = Draft.make_point_array(base, pts, use_link=link)
        else:
            raise bad("kind: ortho (count [nx, ny, nz], spacing [dx, dy, dz]), polar (count, angle, center), circular (rings, ringSpacing, tangentSpacing), path (path, count, align), points (points: a compound or sketch of points).")
        out = finish(named(o, a.get("name"), f"{kind.capitalize()} array"), "draft_link_array")
        out["count"] = getattr(o, "Count", None)
        out["note"] = "Parametric: object_set changes its counts and spacing; the copies follow the original."
        return out

    def draft_clone(a):
        active()
        o = picks.find(doc(), a.get("object"))
        sc = a.get("scale")
        c = Draft.make_clone(o, V(*sc) if isinstance(sc, list) else (V(sc, sc, sc) if isinstance(sc, (int, float)) else None))
        if a.get("move"):
            c.Placement = FreeCAD.Placement(v3(a["move"], "move"), FreeCAD.Rotation()).multiply(c.Placement)
        return finish(named(c, a.get("name"), f"{o.Label} clone"), "draft_clone", {"of": o.Label})

    # ---- modify ----

    def pointed(o):
        if not hasattr(o, "Points"):
            raise bad(f"{o.Label} is not a Draft wire or B-spline (they hold points).")
        pl = o.Placement
        return [pl.multVec(p) for p in o.Points], pl

    def set_points(o, pts, pl):
        inv = pl.inverse()
        o.Points = [inv.multVec(p) for p in pts]

    def edit_points(op, objs, a):
        """The point edits Draft does by hand: split, stretch, trim/extend, wire ↔ B-spline."""
        if op == "split":
            if len(objs) != 1:
                raise bad("split: one wire.")
            o = objs[0]
            pts, pl = pointed(o)
            k = int(a.get("edge", 0))
            at = v3(a.get("at"), "at") if a.get("at") is not None else None
            if not 1 <= k <= len(pts) - 1:
                raise bad(f"edge: which segment, 1 … {len(pts) - 1}.")
            cut = at if at is not None else (pts[k - 1] + pts[k]) * 0.5
            first, second = pts[:k] + [cut], [cut] + pts[k:]
            set_points(o, first, pl)
            if hasattr(o, "Closed"):
                o.Closed = False
            other = Draft.make_wire(second, closed=False)
            other.Label = s.unique(f"{o.Label} split", other)
            return [o, other]
        if op == "stretch":
            box = a.get("box")
            if not (isinstance(box, list) and len(box) == 2):
                raise bad("box: [[xmin, ymin, zmin], [xmax, ymax, zmax]] — the points inside it move.")
            lo, hi = v3(box[0], "box"), v3(box[1], "box")
            by = v3(a.get("by"), "by")
            moved = 0
            for o in objs:
                pts, pl = pointed(o)
                new = []
                for p in pts:
                    inside = all(min(getattr(lo, c), getattr(hi, c)) - 1e-9 <= getattr(p, c) <= max(getattr(lo, c), getattr(hi, c)) + 1e-9 for c in "xyz")
                    new.append(p + by if inside else p)
                    moved += inside
                set_points(o, new, pl)
            if not moved:
                raise ToolError("failed", "No point lies inside the box: nothing was stretched.")
            return objs
        if op == "extend":
            by = float(a.get("length", 0))
            for o in objs:
                pts, pl = pointed(o)
                if len(pts) < 2:
                    continue
                if a.get("end", "end") == "start":
                    d = pts[0] - pts[1]
                    L = d.Length  # before normalize(), which changes d in place
                    if L + by <= 0:
                        raise bad("length: trims past the whole first segment.")
                    pts[0] = pts[1] + d.normalize() * (L + by)
                else:
                    d = pts[-1] - pts[-2]
                    L = d.Length
                    if L + by <= 0:
                        raise bad("length: trims past the whole last segment.")
                    pts[-1] = pts[-2] + d.normalize() * (L + by)
                set_points(o, pts, pl)
            return objs
        made = []
        for o in objs:
            pts, _pl = pointed(o)
            closed = bool(getattr(o, "Closed", False))
            n = Draft.make_bspline(pts, closed=closed) if op == "to_bspline" else Draft.make_wire(pts, closed=closed)
            label = o.Label
            made.append(n)
            if a.get("delete", True):
                doc().removeObject(o.Name)
            n.Label = s.unique(label, n)
        return made

    def draft_modify(a):
        active()
        op = a.get("op")
        objs = objs_of(a.get("objects"))
        copy = bool(a.get("copy"))
        n0 = len(doc().Objects)
        if op == "move":
            r = Draft.move(objs, v3(a.get("by"), "by"), copy=copy)
        elif op == "rotate":
            r = Draft.rotate(objs, float(a.get("angle", 90)), v3(a.get("center"), "center", (0, 0, 0)), picks.direction(a.get("axis"), "axis", (0, 0, 1)), copy=copy)
        elif op == "scale":
            f = a.get("factor", 2)
            r = Draft.scale(objs, V(*f) if isinstance(f, list) else V(f, f, f), v3(a.get("center"), "center", (0, 0, 0)), copy=copy)
        elif op == "mirror":
            r = Draft.mirror(objs, v3(a.get("from"), "from"), v3(a.get("to"), "to"))
        elif op == "offset":
            if len(objs) != 1:
                raise bad("offset: one wire or shape.")
            r = Draft.offset(objs[0], v3(a.get("by"), "by"), copy=copy)
        elif op in ("upgrade", "downgrade"):
            r = (Draft.upgrade if op == "upgrade" else Draft.downgrade)(objs, delete=bool(a.get("delete", True)))
            made_ = list(r[0]) if isinstance(r, (list, tuple)) and r and isinstance(r[0], (list, tuple)) else []
            if not made_:
                raise ToolError("failed", f"Nothing to {op}: {', '.join(o.Label for o in objs)} {'are already as far as they go (a face upgrades to a solid only with others that close a volume)' if op == 'upgrade' else 'cannot be taken apart further'}.")
            r = made_
        elif op == "join":
            ok = Draft.join_wires(objs)
            r = objs[0] if ok else None
        elif op == "fillet":
            if len(objs) != 2:
                raise bad("fillet: two lines or wires that meet.")
            r = Draft.make_fillet(objs, float(a.get("radius", 5)), chamfer=bool(a.get("chamfer")), delete=bool(a.get("delete")))
        elif op in ("split", "stretch", "extend", "to_bspline", "to_wire"):
            r = edit_points(op, objs, a)
        else:
            raise bad("op: move (by), rotate (angle, center, axis), scale (factor, center), mirror (from, to: a line in the plane), offset (by: a vector to the first vertex's side), upgrade, downgrade, join, fillet (radius, chamfer), split (at a point, edge n), stretch (box [[min], [max]], by), extend (length mm, end start | end; negative trims), to_bspline, to_wire.")
        made = [o for o in doc().Objects[n0:]]
        res = made or ([r] if r is not None and not isinstance(r, (list, tuple, bool)) else list(r) if isinstance(r, (list, tuple)) else objs)
        res = [(x[0] if x else None) if isinstance(x, (list, tuple)) else x for x in res if x is not None]
        res = [x for x in res if x is not None]
        res = [x for x in res if hasattr(x, "Label")]
        return finish(res or objs, "draft_modify", {"op": op})

    # ---- layers, hatches ----

    def draft_layer(a):
        active()
        name = a.get("name")
        lay = next((o for o in doc().Objects if getattr(getattr(o, "Proxy", None), "Type", "") == "Layer" and name in (o.Label, o.Name)), None)
        made = lay is None
        if made:
            kw = {"name": name or "Layer"}
            if a.get("lineWidth"):
                kw["line_width"] = float(a["lineWidth"])
            lay = Draft.make_layer(**kw)
        if a.get("objects"):
            grp = list(lay.Group)
            for o in objs_of(a["objects"]):
                if o not in grp:
                    grp.append(o)
            lay.Group = grp
        doc().recompute()
        s.snapshot("draft_layer")
        return {"layer": lay.Label, "made": made, "objects": [o.Label for o in lay.Group], "step": len(s.steps)}

    def draft_hatch(a):
        active()
        base = picks.find(doc(), a.get("object"))
        pat = a.get("file")
        if pat:
            pat = os.path.expanduser(pat)
            if not os.path.isabs(pat):
                pat = os.path.join(s.out_dir, pat)
        else:
            pat = os.path.join(FreeCAD.getResourceDir(), "Mod", "TechDraw", "PAT", "FCPAT.pat")
        if not os.path.isfile(pat):
            raise ToolError("not_found", f"{pat}: no PAT file.")
        name = a.get("pattern", "Diagonal4")
        with open(pat, encoding="utf-8", errors="replace") as f:
            names = [ln[1:].split(",")[0].strip() for ln in f if ln.startswith("*")]
        if name not in names:
            raise bad(f"pattern: one of {', '.join(names[:20])}{' …' if len(names) > 20 else ''} (in {os.path.basename(pat)}).")
        h = Draft.make_hatch(base, pat, name, float(a.get("scale", 1)), float(a.get("rotation", 0)))
        return finish(named(h, a.get("name"), f"{base.Label} hatch"), "draft_hatch", {"pattern": name, "file": os.path.basename(pat)})

    # ---- Draft and Sketch, 2D views, facebinders ----

    def draft_to_sketch(a):
        active()
        objs = objs_of(a.get("objects"))
        sk = Draft.make_sketch(objs, autoconstraints=bool(a.get("constrain", True)), delete=bool(a.get("delete")))
        if sk is None:
            raise ToolError("failed", "Draft could not make a sketch of those (they must be flat, in one plane).")
        return finish(named(sk, a.get("name"), "Sketch"), "draft_to_sketch", {"geometry": len(sk.Geometry), "constraints": len(sk.Constraints)})

    def draft_from_sketch(a):
        active()
        objs = objs_of(a.get("objects"))
        r = Draft.draftify(objs, makeblock=bool(a.get("block")), delete=bool(a.get("delete")))
        res = r if isinstance(r, list) else [r]
        return finish([x for x in res if x is not None], "draft_from_sketch")

    def draft_2d_view(a):
        active()
        base = picks.find(doc(), a.get("object"))
        d = picks.direction(a.get("direction"), "direction", (0, 0, 1))
        mk = getattr(Draft, "make_shape_2d_view", None) or Draft.make_shape2dview
        try:
            v = mk(base, d)
        except TypeError:
            v = mk(base, projection=d)
        if a.get("hidden") and "HiddenLines" in v.PropertiesList:
            v.HiddenLines = True
        if a.get("section") and "ProjectionMode" in v.PropertiesList:
            v.ProjectionMode = "Cutfaces"
        return finish(named(v, a.get("name"), f"{base.Label} 2D view"), "draft_2d_view", {"use": "cut_export or export (DXF) writes it; draft_dimension measures on it."})

    def draft_facebinder(a):
        active()
        sel = []
        for r in a.get("faces") or []:
            label, sub, _ = picks.resolve(s, r)
            if not sub.startswith("Face"):
                raise bad(f"{r}: a face (\"Body.Face6\").")
            sel.append((picks.find(doc(), label), [sub]))
        if not sel:
            raise bad("faces: [\"Body.Face6\", …].")
        try:
            fb = Draft.make_facebinder(sel)
        except Exception:
            fb = Draft.make_facebinder([(o, tuple(sub)) for o, sub in sel])
        if a.get("extrusion") and "Extrusion" in fb.PropertiesList:
            fb.Extrusion = float(a["extrusion"])
        if a.get("offset") and "Offset" in fb.PropertiesList:
            fb.Offset = float(a["offset"])
        return finish(named(fb, a.get("name"), "Facebinder"), "draft_facebinder", {"note": "It follows the faces; extrusion makes a skin of that thickness (a gasket, a pad on a face)."})

    xyz = {"type": "array"}
    return [
        Tool("draft_shape", "A Draft 2D shape (parametric, editable in Draft): kind wire | bspline | bezier (points, closed) | rectangle (length, height) | circle (radius) | arc (radius, start, end) | ellipse (major, minor) | polygon (sides, radius, inscribed) | point (at); at and normal place it; face: true fills a closed one.",
             obj({"kind": {"type": "string"}, "points": {"type": "array"}, "closed": {"type": "boolean"}, "length": {"type": "number"}, "height": {"type": "number"}, "radius": {"type": "number"}, "start": {"type": "number"}, "end": {"type": "number"},
                  "major": {"type": "number"}, "minor": {"type": "number"}, "sides": {"type": "integer"}, "inscribed": {"type": "boolean"}, "face": {"type": "boolean"}, "at": xyz, "normal": xyz, "name": {"type": "string"}}, ["kind"]), draft_shape, changes=True),
        Tool("draft_dimension", "A Draft dimension in the model: kind linear (from, to; or object and vertices [i, j], following it) | radius / diameter (object, edge index) | angular (center, angles [a, b] degrees); at: a point the dimension line passes through.",
             obj({"kind": {"type": "string"}, "from": xyz, "to": xyz, "object": {"type": "string"}, "vertices": {"type": "array"}, "edge": {"type": "integer"}, "center": xyz, "angles": {"type": "array"}, "normal": xyz, "at": xyz, "name": {"type": "string"}}), draft_dimension, changes=True),
        Tool("draft_label", "A Draft label: an arrow from target [x,y,z] to text at [x,y,z]; show custom (text, \\n for lines) | name | area | length | position | material | tag of object (\"Body\" or \"Body.Face6\").",
             obj({"target": xyz, "at": xyz, "object": {"type": "string"}, "show": {"type": "string"}, "text": {"type": "string"}, "name": {"type": "string"}}, ["target"]), draft_label, changes=True),
        Tool("draft_link_array", "A Draft link array of any object (parametric: the copies follow it): kind ortho (count [nx,ny,nz], spacing [dx,dy,dz]) | polar (count, angle, center) | circular (rings, ringSpacing, tangentSpacing, symmetry) | path (path, count, align) | points (points: an object of points); link: false makes real copies.",
             obj({"object": {"type": "string"}, "kind": {"type": "string"}, "count": {}, "spacing": {"type": "array"}, "angle": {"type": "number"}, "center": xyz, "rings": {"type": "integer"}, "ringSpacing": {"type": "number"}, "tangentSpacing": {"type": "number"},
                  "symmetry": {"type": "integer"}, "path": {"type": "string"}, "align": {"type": "boolean"}, "points": {"type": "string"}, "link": {"type": "boolean"}, "name": {"type": "string"}}, ["object"]), draft_link_array, changes=True),
        Tool("draft_clone", "A Draft clone: an exact copy that follows the original; scale (a factor or [x,y,z]), move [x,y,z].",
             obj({"object": {"type": "string"}, "scale": {}, "move": xyz, "name": {"type": "string"}}, ["object"]), draft_clone, changes=True),
        Tool("draft_modify", "Draft's modify tools on objects: op move (by) | rotate (angle, center, axis) | scale (factor, center) | mirror (from, to) | offset (by) | upgrade (wires → faces → solids) | downgrade | join (wires) | fillet (two lines: radius, chamfer) | split (a wire at point at, on segment edge n) | stretch (the points inside box [[min], [max]] moved by) | extend (length mm at end start | end; negative trims) | to_bspline | to_wire; copy: true leaves the originals.",
             obj({"op": {"type": "string"}, "objects": {"type": "array", "items": {"type": "string"}}, "by": xyz, "angle": {"type": "number"}, "center": xyz, "axis": xyz, "factor": {}, "from": xyz, "to": xyz,
                  "radius": {"type": "number"}, "chamfer": {"type": "boolean"}, "copy": {"type": "boolean"}, "delete": {"type": "boolean"}, "at": xyz, "edge": {"type": "integer"}, "box": {"type": "array"}, "length": {"type": "number"}, "end": {"type": "string", "enum": ["start", "end"]}}, ["op", "objects"]), draft_modify, changes=True),
        Tool("draft_layer", "A Draft layer (made if new) and objects put on it: name, objects, lineWidth.",
             obj({"name": {"type": "string"}, "objects": {"type": "array", "items": {"type": "string"}}, "lineWidth": {"type": "number"}}, ["name"]), draft_layer, changes=True),
        Tool("draft_hatch", "Hatch a face or a 2D view with a PAT pattern: object, pattern (default Diagonal4; FreeCAD's FCPAT.pat lists them, or file: your own .pat), scale, rotation.",
             obj({"object": {"type": "string"}, "pattern": {"type": "string"}, "file": {"type": "string"}, "scale": {"type": "number"}, "rotation": {"type": "number"}, "name": {"type": "string"}}, ["object"]), draft_hatch, changes=True),
        Tool("draft_to_sketch", "Draft shapes made into one sketch (constrain: true adds the obvious constraints; delete: true removes the originals).",
             obj({"objects": {"type": "array", "items": {"type": "string"}}, "constrain": {"type": "boolean"}, "delete": {"type": "boolean"}, "name": {"type": "string"}}, ["objects"]), draft_to_sketch, changes=True),
        Tool("draft_from_sketch", "Sketches or shapes made Draft wires (Draft's draftify): objects, block, delete.",
             obj({"objects": {"type": "array", "items": {"type": "string"}}, "block": {"type": "boolean"}, "delete": {"type": "boolean"}}, ["objects"]), draft_from_sketch, changes=True),
        Tool("draft_2d_view", "A 2D view of a body projected along direction (Draft's Shape2DView, parametric): hidden: true adds hidden lines, section: true draws only cut faces.",
             obj({"object": {"type": "string"}, "direction": xyz, "hidden": {"type": "boolean"}, "section": {"type": "boolean"}, "name": {"type": "string"}}, ["object"]), draft_2d_view, changes=True),
        Tool("draft_facebinder", "A facebinder: faces [\"Body.Face6\" …] as a surface that follows them; extrusion mm makes it a skin, offset moves it off.",
             obj({"faces": {"type": "array", "items": {"type": "string"}}, "extrusion": {"type": "number"}, "offset": {"type": "number"}, "name": {"type": "string"}}, ["faces"]), draft_facebinder, changes=True),
    ]
