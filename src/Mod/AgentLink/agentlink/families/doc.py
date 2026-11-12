# SPDX-License-Identifier: LGPL-2.1-or-later
# The document: new, open and save; its objects listed, read, set, renamed
# and deleted; recompute with each object's error; files imported and
# exported (read back to check); and a picture of the 3D view, which needs
# the FreeCAD window.

import difflib
import os

import FreeCAD
import numpy as np
import Part

from .. import geometry as geo
from ..protocol import Tool, ToolError
from ..tools import obj
from . import picks

GROUP = "doc"
INFO = "The document: new, open, save; objects listed, read, set, renamed, deleted; recompute; import and export files."

SOLID_FORMATS = {"step": ("step", "stp"), "iges": ("iges", "igs"), "brep": ("brep", "brp")}
MESH_FORMATS = {"stl": ("stl",), "obj": ("obj",), "3mf": ("3mf",), "ply": ("ply",), "off": ("off",), "amf": ("amf",),
                # read by meshformats, not FreeCAD's Mesh module
                "gltf": ("gltf", "glb"), "dae": ("dae",), "wrl": ("wrl", "vrml"), "3ds": ("3ds",)}
MORE_MESH = ("gltf", "dae", "wrl", "3ds")
EXPORTS = ("step", "iges", "brep", "stl", "obj", "3mf", "ply", "off", "wrl", "x3d", "glb", "gltf", "dae", "dxf", "svg")
# Written by FreeCAD's Mesh module as one mesh; by meshformats a mesh (and
# colour) per object; by flat2d a 2D projection.
FC_MESH_OUT = {"stl": ".stl", "obj": ".obj", "3mf": ".3mf", "ply": ".ply", "off": ".off", "wrl": ".wrl", "x3d": ".x3d"}
SCENE_OUT = {"glb": ".glb", "gltf": ".gltf", "dae": ".dae"}
FLAT_OUT = {"dxf": ".dxf", "svg": ".svg"}
# The 2D views: the shape turned so the view looks down -Z; X right, Y up.
VIEWS = {
    "top": ((1, 0, 0), (0, 1, 0)), "bottom": ((1, 0, 0), (0, -1, 0)),
    "front": ((1, 0, 0), (0, 0, 1)), "back": ((-1, 0, 0), (0, 0, 1)),
    "right": ((0, 1, 0), (0, 0, 1)), "left": ((0, -1, 0), (0, 0, 1)),
    "iso": ((0.7071068, 0.7071068, 0), (-0.4082483, 0.4082483, 0.8164966)),
}
SKIP_PROPS = {"Proxy", "ExpressionEngine", "_GroupTouched"}


def colour(v):
    """"#rrggbb", "#rrggbbaa" or [r, g, b(, a)] in 0…1 → (r, g, b, a); None when not given."""
    if v is None:
        return None
    if isinstance(v, str) and v.startswith("#") and len(v) in (7, 9):
        try:
            c = [int(v[i:i + 2], 16) / 255 for i in range(1, len(v), 2)]
        except ValueError:
            c = None
        if c:
            return tuple(c + [1.0] * (4 - len(c)))
    if isinstance(v, (list, tuple)) and len(v) in (3, 4) and all(isinstance(x, (int, float)) and 0 <= x <= 1 for x in v):
        return tuple(list(map(float, v)) + [1.0] * (4 - len(v)))
    raise ToolError("invalid_input", f"colors: \"#rrggbb\" or [r, g, b] in 0…1, not {v!r}.")


def view_matrix(view):
    if view not in VIEWS:
        raise ToolError("invalid_input", f"view: one of {', '.join(VIEWS)}.")
    xa, ya = (FreeCAD.Vector(*v) for v in VIEWS[view])
    za = xa.cross(ya)
    m = FreeCAD.Matrix(xa.x, xa.y, xa.z, 0, ya.x, ya.y, ya.z, 0, za.x, za.y, za.z, 0, 0, 0, 0, 1)
    return m


def edge_items(edges, layer, defl):
    """Projected edges (in the XY plane) as flat2d items: circles and arcs kept, the rest as polylines."""
    import math

    out = []
    for e in edges:
        c = e.Curve
        if isinstance(c, Part.Circle) and abs(abs(c.Axis.z) - 1) < 1e-6:
            cx, cy, r = c.Location.x, c.Location.y, c.Radius
            if e.isClosed():
                out.append({"kind": "circle", "c": [cx, cy], "r": r, "layer": layer})
                continue
            p0, p1 = e.valueAt(e.FirstParameter), e.valueAt(e.LastParameter)
            a0, a1 = (math.degrees(math.atan2(q.y - cy, q.x - cx)) for q in (p0, p1))
            if c.Axis.z < 0:
                a0, a1 = a1, a0
            out.append({"kind": "arc", "c": [cx, cy], "r": r, "a0": a0, "a1": a1, "layer": layer})
            continue
        pts = [[v.x, v.y] for v in e.discretize(Deflection=defl)]
        if len(pts) >= 2:
            out.append({"kind": "line", "points": pts, "layer": layer})
    return out


def flat_export(objs, fmt, path, a):
    """A 2D view of the objects (hidden lines on their own layer when hidden: true), or a section at z."""
    from .. import flat2d

    defl = float(a.get("tolerance", 0.01))
    shape = objs[0].Shape if len(objs) == 1 else Part.Compound([o.Shape for o in objs])
    items = []
    if a.get("section") is not None:
        z = picks.num(a["section"], "section")
        wires = shape.slice(FreeCAD.Vector(0, 0, 1), z)
        if not wires:
            raise ToolError("invalid_input", f"section: nothing at z = {z} mm (the shape runs {shape.BoundBox.ZMin:.2f} … {shape.BoundBox.ZMax:.2f}).")
        for w in wires:
            items += edge_items(w.Edges, "cut", defl)
        what = {"section": z, "wires": len(wires)}
    else:
        import TechDraw

        view = a.get("view", "top")
        sh = shape.transformGeometry(view_matrix(view)) if view != "top" else shape.copy()
        res = TechDraw.projectEx(sh, FreeCAD.Vector(0, 0, 1))
        # hard, smooth and outline edges (seams and isolines left out), visible then hidden
        vis = [e for i in (0, 1, 3) if not res[i].isNull() for e in res[i].Edges]
        hid = [e for i in (5, 6, 8) if not res[i].isNull() for e in res[i].Edges] if a.get("hidden") else []
        items = edge_items(vis, "visible", defl) + edge_items(hid, "hidden", defl)
        what = {"view": view, "visibleEdges": len(vis), **({"hiddenEdges": len(hid)} if a.get("hidden") else {})}
    if not items:
        raise ToolError("failed", "The view came out empty — nothing to draw.")
    text = flat2d.dxf(items) if fmt == "dxf" else flat2d.svg(items)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    x0, y0, x1, y1 = flat2d.bounds(items)
    out = {**what, "size": [round(x1 - x0, 3), round(y1 - y0, 3)], "units": "mm"}
    if fmt == "dxf":
        out["entities"] = flat2d.read_dxf_counts(text)
    return out


def image_size(path):
    """(width, height) in pixels from a PNG, JPEG or BMP header; None if unknown."""
    import struct

    with open(path, "rb") as f:
        head = f.read(64 * 1024)
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return struct.unpack(">II", head[16:24])
    if head[:2] == b"BM":
        w, h = struct.unpack("<ii", head[18:26])
        return w, abs(h)
    if head[:2] == b"\xff\xd8":
        i = 2
        while i + 9 < len(head):
            if head[i] != 0xFF:
                i += 1
                continue
            mk = head[i + 1]
            n = struct.unpack(">H", head[i + 2:i + 4])[0]
            if mk in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                h, w = struct.unpack(">HH", head[i + 5:i + 9])
                return w, h
            i += 2 + n
    return None


def image_plane(d, path, label, a):
    """A picture as an image plane (a reference to trace over): width mm, its height from the picture's shape, on XY."""
    try:
        o = d.addObject("Image::ImagePlane", "ImagePlane")
    except Exception as e:
        raise ToolError("unavailable", f"Image planes are not available here ({e}).")
    px = image_size(path)
    w = picks.num(a.get("width", 100), "width")
    if w <= 0:
        raise ToolError("invalid_input", "width: the picture's width in mm, above 0.")
    o.Label = label
    o.ImageFile = path
    o.XSize = w
    o.YSize = w * px[1] / px[0] if px and px[0] else w
    return o


def fmt_of(path):
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    for k, exts in {**SOLID_FORMATS, **MESH_FORMATS}.items():
        if ext in exts:
            return k
    return ext


def readonly(o, p):
    try:
        if "ReadOnly" in (o.getEditorMode(p) or []):
            return True
    except Exception:
        pass
    try:
        st = o.getPropertyStatus(p)
        return "ReadOnly" in st or "Output" in st
    except Exception:
        return False


def errors(doc):
    out = []
    for o in doc.Objects:
        st = list(getattr(o, "State", []) or [])
        if "Invalid" in st or "Error" in st or (hasattr(o, "isValid") and not o.isValid()):
            try:
                why = o.getStatusString()
            except Exception:
                why = ", ".join(st)
            out.append({"object": o.Label, "type": o.TypeId, "error": why})
    return out


def to_value(o, p, v):
    """A JSON value into what the property takes, by its type."""
    t = o.getTypeIdOfProperty(p)
    try:
        if t in ("App::PropertyBool",):
            if not isinstance(v, bool):
                raise ValueError("true or false")
            return v
        if t.startswith("App::PropertyInteger"):
            if isinstance(v, bool) or not isinstance(v, (int, float)) or int(v) != v:
                raise ValueError("a whole number")
            return int(v)
        if t in ("App::PropertyFloat", "App::PropertyLength", "App::PropertyDistance", "App::PropertyAngle", "App::PropertyFloatConstraint", "App::PropertyPercent", "App::PropertyArea", "App::PropertyVolume", "App::PropertyQuantity", "App::PropertyQuantityConstraint", "App::PropertyPrecision") or t.startswith("App::PropertyFloat"):
            if isinstance(v, str):
                return v  # "10 mm", "2 in": FreeCAD parses the unit
            return float(picks.num(v, p))
        if t in ("App::PropertyString", "App::PropertyFont", "App::PropertyFile", "App::PropertyPath"):
            if not isinstance(v, str):
                raise ValueError("a string")
            return v
        if t == "App::PropertyEnumeration":
            choices = o.getEnumerationsOfProperty(p) or []
            if v not in choices:
                raise ValueError(f"one of {', '.join(map(str, choices))}")
            return v
        if t in ("App::PropertyVector", "App::PropertyVectorDistance", "App::PropertyPosition", "App::PropertyDirection"):
            return picks.vec3(v, p)
        if t == "App::PropertyPlacement":
            if not isinstance(v, dict):
                raise ValueError("{ at: [x,y,z], axis: [x,y,z], angle: degrees }")
            return FreeCAD.Placement(picks.vec3(v.get("at"), f"{p}.at", (0, 0, 0)), FreeCAD.Rotation(picks.direction(v.get("axis"), f"{p}.axis", (0, 0, 1)), picks.num(v.get("angle", 0), f"{p}.angle")))
        if t in ("App::PropertyLink", "App::PropertyLinkGlobal", "App::PropertyLinkChild"):
            return None if v is None else picks.find(o.Document, v)
        if t in ("App::PropertyLinkList", "App::PropertyLinkListGlobal", "App::PropertyLinkListChild"):
            return [picks.find(o.Document, x) for x in v]
        if t in ("App::PropertyColor", "App::PropertyMaterialColor"):
            if isinstance(v, str) and v.startswith("#") and len(v) == 7:
                return tuple(int(v[i:i + 2], 16) / 255.0 for i in (1, 3, 5))
            return tuple(float(x) for x in v)
        if t == "App::PropertyStringList":
            return [str(x) for x in v]
        if t == "App::PropertyFloatList":
            return [float(x) for x in v]
        if t == "App::PropertyIntegerList":
            return [int(x) for x in v]
    except ToolError:
        raise
    except (ValueError, TypeError) as e:
        raise ToolError("invalid_input", f"values.{p} ({t}): {e}.")
    raise ToolError("invalid_input", f"values.{p}: a {t} is not set here — set it in FreeCAD, or another tool makes it.")


def tools(session):
    s = session

    def switch(doc):
        doc.UndoMode = 1
        s.doc = doc
        s.checkpoints = {}
        s.meta = {}
        if FreeCAD.GuiUp:
            try:
                import FreeCADGui

                FreeCAD.setActiveDocument(doc.Name)
                FreeCADGui.getDocument(doc.Name)
            except Exception:
                pass
        s.snapshot("doc")

    def where(path, ext=None):
        if not isinstance(path, str) or not path:
            raise ToolError("invalid_input", "path: a file path (relative ones go in the session's folder).")
        p = os.path.expanduser(path)
        if not os.path.isabs(p):
            p = os.path.join(s.out_dir, p)
        if ext and not p.lower().endswith(ext.lower()):
            p += ext
        return p

    def doc_new(a):
        name = a.get("name") or "Unnamed"
        d = FreeCAD.newDocument(name.replace(" ", "_"))
        d.Label = name
        old = s.doc.Name
        switch(d)
        return {"document": d.Name, "label": d.Label, "previous": old, "note": "The previous document stays open; doc_open { path } returns to it if saved."}

    def doc_open(a):
        p = where(a.get("path"))
        if not os.path.isfile(p):
            raise ToolError("not_found", f"{p}: no such file.")
        if not p.lower().endswith(".fcstd"):
            raise ToolError("invalid_input", f"{os.path.basename(p)}: doc_open opens FreeCAD documents (.FCStd) — import_file brings STEP, STL and the rest into this one.")
        d = next((x for x in FreeCAD.listDocuments().values() if os.path.abspath(x.FileName or "") == os.path.abspath(p)), None)
        if d is None:
            try:
                d = FreeCAD.openDocument(p)
            except Exception as e:
                raise ToolError("failed", f"FreeCAD could not open {p}: {e}")
        d.recompute()
        switch(d)
        return {"document": d.Name, "label": d.Label, "path": p, "objects": len(d.Objects), "bodies": [o.Label for o in s.bodies()], "errors": errors(d)}

    def doc_save(a):
        d = s.doc
        if a.get("path"):
            p = where(a["path"], ".FCStd")
            os.makedirs(os.path.dirname(p), exist_ok=True)
            d.saveAs(p)
        elif d.FileName:
            d.save()
            p = d.FileName
        else:
            p = where(f"{d.Label or d.Name}.FCStd")
            d.saveAs(p)
        return {"document": d.Name, "path": p, "bytes": os.path.getsize(p)}

    def object_list(a):
        kind = (a.get("type") or "").lower()
        out = []
        for o in s.doc.Objects:
            if kind and kind not in o.TypeId.lower():
                continue
            e = {"name": o.Name, "label": o.Label, "type": o.TypeId}
            if hasattr(o, "Visibility"):
                e["visible"] = bool(o.Visibility)
            # an object linked twice (a group that also binds to it) once
            parents = list(dict.fromkeys(p.Label for p in o.InList))
            if parents:
                e["parents"] = parents
            sh = getattr(o, "Shape", None)
            if isinstance(sh, Part.Shape) and not sh.isNull():
                e["shape"] = geo.stats(sh) if sh.Solids else {"faces": len(sh.Faces), "edges": len(sh.Edges), "box": geo.box_of(sh)["size"]}
            elif hasattr(o, "Mesh"):
                e["mesh"] = {"triangles": o.Mesh.CountFacets, "closed": o.Mesh.isSolid()}
            st = list(getattr(o, "State", []) or [])
            if "Invalid" in st or "Error" in st:
                e["error"] = True
            out.append(e)
        return {"document": s.doc.Name, "count": len(out), "objects": out}

    def object_get(a):
        o = picks.find(s.doc, a.get("object"))
        flt = a.get("filter")
        names = [p for p in o.PropertiesList if p not in SKIP_PROPS]
        if isinstance(flt, list):
            names = [p for p in names if p in flt]
        elif isinstance(flt, str) and flt:
            names = [p for p in names if flt.lower() in p.lower() or flt.lower() in (o.getGroupOfProperty(p) or "").lower()]
        props = {}
        for p in names:
            try:
                v = picks.json_safe(getattr(o, p))
            except Exception as e:
                v = f"<unreadable: {e}>"
            entry = {"value": v, "type": o.getTypeIdOfProperty(p).replace("App::Property", "")}
            if readonly(o, p):
                entry["readOnly"] = True
            props[p] = entry
        return {"name": o.Name, "label": o.Label, "type": o.TypeId, "properties": props}

    def object_set(a):
        o = picks.find(s.doc, a.get("object"))
        vals = a.get("values")
        if not isinstance(vals, dict) or not vals:
            raise ToolError("invalid_input", "values: { Property: value, … } — object_get gives the names and types.")
        props = set(o.PropertiesList)
        for p, v in vals.items():
            if p not in props:
                near = difflib.get_close_matches(p, list(props), 3)
                raise ToolError("not_found", f"values.{p}: {o.Label} has no such property{' — did you mean ' + ', '.join(near) if near else ''}. object_get lists them.")
            if readonly(o, p):
                raise ToolError("invalid_input", f"values.{p}: read-only on {o.Label} (FreeCAD computes it).")
        before = {p: picks.json_safe(getattr(o, p)) for p in vals}
        for p, v in vals.items():
            try:
                setattr(o, p, to_value(o, p, v))
            except ToolError:
                raise
            except Exception as e:
                raise ToolError("invalid_input", f"values.{p}: FreeCAD refused {v!r} — {e}")
        s.doc.recompute()
        errs = errors(s.doc)
        if errs:
            raise ToolError("failed", f"The recompute failed after setting it: {errs[0]['object']}: {errs[0]['error']} — nothing was kept.")
        s.snapshot("object_set")
        return {"object": o.Label, "set": {p: {"from": before[p], "to": picks.json_safe(getattr(o, p))} for p in vals}, "step": len(s.steps)}

    def object_delete(a):
        names = a.get("objects")
        if isinstance(names, str):
            names = [names]
        if not names:
            raise ToolError("invalid_input", "objects: the names or labels to delete.")
        objs = [picks.find(s.doc, n) for n in names]
        going = {o.Name for o in objs}
        for o in objs:
            users = [p.Label for p in o.InList if p.Name not in going]
            if users and not a.get("force"):
                raise ToolError("invalid_state", f"{o.Label} is used by {', '.join(users)}: delete those too, or force: true leaves them broken.")
        labels = [o.Label for o in objs]
        for o in objs:
            s.doc.removeObject(o.Name)
        s.doc.recompute()
        s.snapshot("object_delete")
        return {"deleted": labels, "errors": errors(s.doc), "step": len(s.steps)}

    def object_rename(a):
        o = picks.find(s.doc, a.get("object"))
        label = a.get("label")
        if not isinstance(label, str) or not label.strip():
            raise ToolError("invalid_input", "label: the new name.")
        if any(x.Label == label for x in s.doc.Objects if x is not o):
            raise ToolError("invalid_input", f"label {label!r}: taken — another name.")
        old = o.Label
        o.Label = label
        if old in s.meta:
            s.meta[label] = s.meta.pop(old)
        s.snapshot("object_rename")
        return {"name": o.Name, "from": old, "to": o.Label, "note": "Setups, specs and waivers kept by the old label are not renamed with it." if old != o.Label else ""}

    def object_group(a):
        d = s.doc
        action = a.get("action", "add")
        if action == "list":
            out = []
            for g in d.Objects:
                if g.isDerivedFrom("App::DocumentObjectGroup") or g.TypeId in ("App::Part", "App::Link") or hasattr(g, "Group") and g.TypeId.startswith("App::"):
                    row = {"group": g.Label, "type": g.TypeId}
                    if hasattr(g, "Group"):
                        row["holds"] = [x.Label for x in g.Group]
                    if g.TypeId == "App::Link":
                        lo = g.getLinkedObject(False) if hasattr(g, "getLinkedObject") else getattr(g, "LinkedObject", None)
                        row["links"] = lo.Label if lo is not None else None
                    out.append(row)
            return {"groups": out}
        if action == "link":
            src = picks.find(d, a.get("object"))
            n = int(a.get("count", 1))
            made = []
            for i in range(n):
                ln = d.addObject("App::Link", "Link")
                ln.setLink(src)
                ln.Label = s.unique(a.get("name") or f"{src.Label} link")
                step = a.get("step") or [0, 0, 0]
                at = a.get("at") or [0, 0, 0]
                ln.LinkPlacement = FreeCAD.Placement(FreeCAD.Vector(*[float(at[k]) + float(step[k]) * i for k in range(3)]), FreeCAD.Rotation())
                made.append(ln.Label)
            d.recompute()
            s.snapshot("object_group")
            return {"links": made, "of": src.Label, "note": "Links show the original where placed and follow its edits; they weigh nothing in the file."}
        name = a.get("group")
        if not name:
            raise ToolError("invalid_input", "group: the group's name (made if new).")
        g = next((x for x in d.Objects if x.Label == name and hasattr(x, "Group")), None)
        kind = a.get("kind", "folder")
        if g is None:
            g = d.addObject("App::Part" if kind == "part" else "App::DocumentObjectGroup", "Group")
            g.Label = s.unique(name, g)
        objs = [picks.find(d, r) for r in (a.get("objects") or [])]
        if action == "add":
            for o in objs:
                if o is g:
                    continue
                for p in o.InList:
                    if hasattr(p, "Group") and p is not g and o in p.Group and (p.isDerivedFrom("App::DocumentObjectGroup") or p.TypeId == "App::Part"):
                        p.removeObject(o)
                g.addObject(o)
        elif action == "remove":
            for o in objs:
                g.removeObject(o)
        elif action == "dissolve":
            for o in list(g.Group):
                g.removeObject(o)
            d.removeObject(g.Name)
            d.recompute()
            s.snapshot("object_group")
            return {"dissolved": name}
        else:
            raise ToolError("invalid_input", "action: add | remove (objects to or from group) | dissolve | link (object, count, at, step) | list.")
        if a.get("visible") is not None:
            for o in g.Group:
                try:
                    o.Visibility = bool(a["visible"])
                except Exception:
                    pass
        d.recompute()
        s.snapshot("object_group")
        return {"group": g.Label, "type": g.TypeId, "holds": [x.Label for x in g.Group]}

    def recompute(a):
        if a.get("force"):
            for o in s.doc.Objects:
                o.touch()
        n = s.doc.recompute()
        errs = errors(s.doc)
        s.snapshot("recompute")
        return {"recomputed": n, "ok": not errs, "errors": errs}

    def import_file(a):
        p = where(a.get("path"))
        if not os.path.isfile(p):
            raise ToolError("not_found", f"{p}: no such file.")
        fmt = fmt_of(p)
        d = s.doc
        before = {o.Name for o in d.Objects}
        base = a.get("name") or os.path.splitext(os.path.basename(p))[0]
        made = []
        took = None
        if fmt in SOLID_FORMATS:
            sh = Part.Shape()
            try:
                sh.read(p)
            except Exception as e:
                raise ToolError("failed", f"{os.path.basename(p)} could not be read as {fmt.upper()}: {e}")
            pieces = sh.Solids if (a.get("split") and len(sh.Solids) > 1) else [sh]
            for i, piece in enumerate(pieces):
                o = d.addObject("Part::Feature", "Import")
                o.Label = s.unique(base if len(pieces) == 1 else f"{base} {i + 1}")
                o.Shape = piece
                made.append(o)
        elif fmt in MESH_FORMATS:
            import Mesh

            try:
                if fmt in MORE_MESH:
                    from .. import meshformats

                    V, F, took = meshformats.read(p, a.get("units"), a.get("up"))
                    m = Mesh.Mesh()
                    m.addFacets([[tuple(V[i]) for i in t] for t in F.tolist()])
                else:
                    m = Mesh.Mesh(p)
            except Exception as e:
                raise ToolError("failed", f"{os.path.basename(p)} could not be read as a mesh: {e}")
            if m.CountFacets == 0:
                raise ToolError("failed", f"{os.path.basename(p)}: no triangles read.")
            want_solid = a.get("solid", m.CountFacets <= 20000)
            if want_solid:
                if m.CountFacets > 200000:
                    raise ToolError("invalid_input", f"solid: {m.CountFacets} triangles is too many to turn into a solid here — import it as a mesh (solid: false), or decimate it first.")
                sh = Part.Shape()
                sh.makeShapeFromMesh(m.Topology, 0.05)
                try:
                    sh = Part.Solid(Part.Shell(sh.Faces))
                    if sh.Volume < 0:
                        sh.reverse()
                    sh = geo.clean(sh)
                except Exception:
                    pass
                o = d.addObject("Part::Feature", "Import")
                o.Shape = sh
            else:
                o = d.addObject("Mesh::Feature", "Import")
                o.Mesh = m
            o.Label = s.unique(base)
            made.append(o)
        elif fmt == "dxf":
            try:
                import Import

                Import.readDXF(p, d.Name)
            except Exception as e:
                raise ToolError("unavailable", f"DXF import failed here ({e}): open it in the FreeCAD window (Draft's importer).")
            made = [o for o in d.Objects if o.Name not in before]
        elif fmt == "fcstd":
            raise ToolError("invalid_input", "A FreeCAD document: doc_open { path } opens it.")
        elif fmt == "svg":
            try:
                import importSVG

                importSVG.insert(p, d.Name)
            except Exception as e:
                raise ToolError("unavailable", f"SVG import failed here ({e}): open it in the FreeCAD window (Draft's importer).")
            made = [o for o in d.Objects if o.Name not in before]
            if not made:
                raise ToolError("failed", f"{os.path.basename(p)}: no paths, shapes or text read.")
        elif fmt == "jt":
            try:
                import JtReader
            except ImportError:
                raise ToolError("unavailable", "JT needs FreeCAD's JtReader, which this build leaves out: export STEP from the program that made it.") from None
            try:
                JtReader.insert(p, d.Name)
            except Exception as e:
                raise ToolError("failed", f"{os.path.basename(p)} could not be read as JT: {e}") from None
            made = [o for o in d.Objects if o.Name not in before]
        elif fmt in ("png", "jpg", "jpeg", "bmp"):
            made = [image_plane(d, p, s.unique(base), a)]
        elif fmt in ("pdf", "dwg", "ifc"):
            raise ToolError("unavailable", f"{fmt.upper()} comes in through the FreeCAD window's importers (Draft, BIM, image planes), not headless here: open it there.")
        else:
            raise ToolError("invalid_input", f"{fmt or 'no extension'}: import_file reads STEP, IGES, BREP, STL, OBJ, 3MF, PLY, OFF, AMF, glTF/GLB, Collada, VRML, 3DS, DXF, SVG and PNG/JPG/BMP pictures; FBX: export it as glTF from the program that made it.")
        d.recompute()
        s.snapshot("import_file")
        out = []
        for o in made:
            e = {"name": o.Name, "label": o.Label, "type": o.TypeId}
            sh = getattr(o, "Shape", None)
            if isinstance(sh, Part.Shape) and not sh.isNull():
                e.update(geo.stats(sh) if sh.Solids else {"faces": len(sh.Faces), "edges": len(sh.Edges)})
                if fmt in MESH_FORMATS:
                    e["closed"] = bool(sh.Solids)
                    e["note"] = "Made of the mesh's triangles: measure and boolean work; fillets and face queries do not see round faces."
            elif hasattr(o, "Mesh"):
                e["mesh"] = {"triangles": o.Mesh.CountFacets, "closed": o.Mesh.isSolid()}
                e["note"] = "A mesh, not a body: import_file { solid: true } makes a body of it."
            out.append(e)
        return {"path": p, "format": fmt, **({"read": took} if took else {}), "objects": out, "count": len(out), "step": len(s.steps)}

    def export(a):
        names = a.get("objects")
        if isinstance(names, str):
            names = [names]
        objs = [picks.shaped(s.doc, n) for n in names] if names else [s.body(None)]
        fmt = (a.get("format") or "").lower()
        if not fmt and a.get("path"):
            fmt = fmt_of(a["path"])
        fmt = fmt or "step"
        if fmt not in EXPORTS:
            raise ToolError("invalid_input", f"format: one of {', '.join(EXPORTS)}.")
        ext = {"step": ".step", "iges": ".iges", "brep": ".brep", **FC_MESH_OUT, **SCENE_OUT, **FLAT_OUT}[fmt]
        p = where(a.get("path") or (objs[0].Label.replace(" ", "_") + ext))
        known = {*(SOLID_FORMATS.get(fmt) or ()), *(MESH_FORMATS.get(fmt) or ()), ext.lstrip(".")}
        if os.path.splitext(p)[1].lower().lstrip(".") not in known:
            p += ext
        os.makedirs(os.path.dirname(p), exist_ok=True)
        shape = objs[0].Shape if len(objs) == 1 else Part.Compound([o.Shape for o in objs])
        vol = sum(o.Shape.Volume for o in objs if o.Shape.Solids)
        if fmt in SOLID_FORMATS:
            {"step": shape.exportStep, "iges": shape.exportIges, "brep": shape.exportBrep}[fmt](p)
            back = Part.Shape()
            back.read(p)
            verify = {"volume": round(back.Volume, 2), "volumeDiff": round(abs(back.Volume - vol), 3), "solids": len(back.Solids), "faces": len(back.Faces)}
            if fmt == "iges" and not back.Solids:
                verify["note"] = "IGES carries faces, not solids, as most readers take it."
        elif fmt in FLAT_OUT:
            verify = flat_export(objs, fmt, p, a)
        else:
            import Mesh
            import MeshPart

            tol = picks.num(a.get("tolerance", 0.01), "tolerance")
            if tol <= 0:
                raise ToolError("invalid_input", "tolerance: the chord error, above 0 mm.")

            def mesh_of(sh):
                return MeshPart.meshFromShape(Shape=sh, LinearDeflection=tol, AngularDeflection=0.0873, Relative=False)

            if fmt in SCENE_OUT:
                from .. import meshformats

                parts, tris = [], 0
                cols = a.get("colors") or {}
                for o in objs:
                    m = mesh_of(o.Shape)
                    pts, fs = m.Topology
                    rgba = colour(cols.get(o.Label) or cols.get(o.Name))
                    parts.append({"name": o.Label, "V": [tuple(v) for v in pts], "F": fs, **({"rgba": rgba} if rgba else {})})
                    tris += len(fs)
                for q in parts:
                    q["V"] = np.array(q["V"], float).reshape(-1, 3)
                    q["F"] = np.array(q["F"], int).reshape(-1, 3)
                meshformats.write(p, parts)
                V, F, info = meshformats.read(p)
                span = V.max(axis=0) - V.min(axis=0)
                want = shape.BoundBox
                verify = {"triangles": int(len(F)), "trianglesWritten": tris, "objects": info["nodes"],
                          "size": [round(float(x), 2) for x in span], "sizeOfBodies": [round(want.XLength, 2), round(want.YLength, 2), round(want.ZLength, 2)],
                          "note": "Metres, Y up, as glTF and Collada keep them; a mesh and colour per object (colors {label: \"#rrggbb\"})."}
            else:
                m = mesh_of(shape)
                m.write(p)
                try:
                    if fmt == "wrl":
                        from .. import meshformats

                        V, F, _info = meshformats.read(p, "mm", "z")  # FreeCAD writes it as the document has it
                        back = Mesh.Mesh()
                        back.addFacets([[tuple(V[i]) for i in tr] for tr in F.tolist()])
                    else:
                        back = Mesh.Mesh(p)
                    verify = {"triangles": back.CountFacets, "volume": round(back.Volume, 2), "volumeDiff": round(abs(back.Volume - vol), 2), "closed": back.isSolid()}
                except Exception:
                    verify = {"triangles": m.CountFacets, "readBack": f"written; FreeCAD does not read {fmt.upper()} back to check it"}
        return {"objects": [o.Label for o in objs], "format": fmt, "path": p, "bytes": os.path.getsize(p), "verify": verify}

    objref = {"type": "string", "description": "An object's name or label."}
    return [
        Tool("doc_new", "Start a new, empty FreeCAD document and work in it from now on; the one before stays open (save it first: doc_save). Not undoable, and not in a batch's rollback.", obj({"name": {"type": "string"}}), doc_new),
        Tool("doc_open", "Open a FreeCAD document (.FCStd, path) and work in it from now on — recomputed, with any object's error listed. Other formats come in with import_file. Not undoable.", obj({"path": {"type": "string"}}, ["path"]), doc_open),
        Tool("doc_save", "Save the document as .FCStd: path (relative goes in the session's folder), else where it was saved, else <label>.FCStd there. What setup and part_spec recorded is saved with it.", obj({"path": {"type": "string"}}), doc_save),
        Tool("object_list", "Every object in the document: name, label, type, visible, parents (what uses it) and its shape (volume, area, faces, solids, size for a solid; faces and edges else) or mesh. type: a part of the type to keep (\"Part::\", \"Sketch\").", obj({"type": {"type": "string"}}), object_list),
        Tool("object_get", "One object's properties: value (mm, degrees; placements as { at, axis, angle }; links by label; a shape as its stats), type, readOnly. filter: a word in the name or group (\"Placement\", \"Base\"), or a list of names.", obj({"object": objref, "filter": {}}, ["object"]), object_get),
        Tool("object_set", "Set properties of an object by name: values { Length: 20, Placement: { at, axis, angle }, Refine: true, Base: \"Box\", Mode: \"…\" } — each converted to its type (numbers in mm and degrees, or strings with a unit \"1 in\"); enumerations checked; read-only ones refused. Recomputes; refused and undone when that fails. Trap: a plain shape (Part::Feature from a boolean) has no sizes to set — only placement.", obj({"object": objref, "values": {"type": "object"}}, ["object", "values"]), object_set, changes=True),
        Tool("object_delete", "Delete objects (objects: names or labels). Refused while another object uses one (force: true deletes it anyway and the user breaks); recomputes and lists any error.", obj({"objects": {}, "force": {"type": "boolean"}}, ["objects"]), object_delete, changes=True),
        Tool("object_rename", "Give an object a new label (its internal name stays). Trap: setups, specs and waivers keep the old label.", obj({"object": objref, "label": {"type": "string"}}, ["object", "label"]), object_rename, changes=True),
        Tool("object_group", "Organise the document: action add | remove (objects into or out of group — made if new; kind folder (a plain group) | part (an App::Part container that moves as one)) | dissolve (the group, its objects kept) | link (count App::Link copies of object at, step [dx,dy,dz] apart — light copies that follow it) | list; visible: false hides a whole group's objects.",
             obj({"action": {"type": "string", "enum": ["add", "remove", "dissolve", "link", "list"]}, "group": {"type": "string"}, "kind": {"type": "string", "enum": ["folder", "part"]}, "objects": {"type": "array", "items": {"type": "string"}},
                  "object": {"type": "string"}, "count": {"type": "integer"}, "at": {"type": "array"}, "step": {"type": "array"}, "visible": {"type": "boolean"}, "name": {"type": "string"}}), object_group, changes=True),
        Tool("recompute", "Recompute the document (force: true recomputes everything) and list each object that failed, with FreeCAD's reason.", obj({"force": {"type": "boolean"}}), recompute, changes=True),
        Tool("import_file", "Bring a file into the document: STEP, IGES, BREP as bodies (split: true makes a body of each solid); STL, OBJ, 3MF, PLY, OFF, AMF — and glTF/GLB, Collada, VRML 2, 3DS, every mesh node flattened into one in mm, Z up (units and up override what the format says; read says what was taken) — as a body made of its triangles when closed and up to 20000 triangles (solid: false keeps a mesh, solid: true forces a body up to 200000); DXF and SVG as Draft geometry; JT where this FreeCAD has its reader; PNG, JPG, BMP as an image plane width mm wide on XY, to trace over. PDF, DWG, IFC need the FreeCAD window; FBX: export it as glTF. .FCStd: doc_open.", obj({"path": {"type": "string"}, "name": {"type": "string"}, "split": {"type": "boolean"}, "solid": {"type": "boolean"}, "units": {"type": "string", "enum": ["mm", "cm", "m", "in"]}, "up": {"type": "string", "enum": ["x", "y", "z"]}, "width": {"type": "number"}}, ["path"]), import_file, changes=True),
        Tool("export", "Write objects (the one body when left out; several go as one compound) and read it back (verify). Solids: STEP, IGES, BREP. One mesh: STL, OBJ, 3MF, PLY, OFF, VRML (wrl), X3D. A scene, a mesh per object with colors {label: \"#rrggbb\"}: glTF (glb, gltf), Collada (dae) — metres, Y up. 2D: DXF (R12) or SVG of a view (top | bottom | front | back | right | left | iso; hidden: true adds hidden lines on their own layer) or of a section at z mm (section) — circles and arcs kept as such, for laser and plasma cutting. format, or from path's extension; tolerance: the chord error, 0.01 mm.", obj({"objects": {}, "format": {"type": "string", "enum": list(EXPORTS)}, "path": {"type": "string"}, "tolerance": {"type": "number"}, "colors": {"type": "object"}, "view": {"type": "string", "enum": list(VIEWS)}, "hidden": {"type": "boolean"}, "section": {"type": "number"}}), export),
    ]
