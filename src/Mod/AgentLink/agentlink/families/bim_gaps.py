# SPDX-License-Identifier: LGPL-2.1-or-later
# BIM's remaining commands, headless: generic components, clones and
# unclones, external references, structural systems, pipe connectors, panel
# cut sheets and nesting, the IFC B-rep flag, 2D views of the levels, image
# planes, the objects library, re-extrusion from a face, rewiring, glue,
# trash and reordering a group's children.

import glob
import os

import FreeCAD
import Part

import Arch  # the family is left out without BIM

from ..protocol import Tool, ToolError
from ..tools import obj
from . import picks
from .bim import headless_call

GROUP = "bim_gaps"
INFO = "BIM components, clones, references, structural systems, pipe connectors, panel cuts and sheets, nesting, IFC B-rep flag, 2D level views, image planes, library, re-extrude, rewire, glue, trash, reorder."

V = FreeCAD.Vector


def bad(msg):
    return ToolError("invalid_input", msg)


def tools(s):
    def d():
        if s.doc is None:
            raise ToolError("invalid_state", "No document open: doc_new first.")
        return s.doc

    def objs(refs, what="objects"):
        if isinstance(refs, str):
            refs = [refs]
        if not refs:
            raise bad(f"{what}: one or more objects by label.")
        return [picks.find(d(), r, what) for r in refs]

    def done(tool, out):
        d().recompute()
        s.snapshot(tool)
        return out

    def named(o, name):
        if name:
            o.Label = s.unique(name)
        return o

    def reading(o):
        d().recompute()
        sh = getattr(o, "Shape", None)
        if sh is None or sh.isNull():
            return {}
        return {"volume": round(sh.Volume, 3) if sh.Solids else None, "box": [round(sh.BoundBox.XLength, 3), round(sh.BoundBox.YLength, 3), round(sh.BoundBox.ZLength, 3)]}

    def bim_component(a):
        kind = a.get("kind", "component")
        if kind == "component":
            base = picks.shaped(d(), a.get("from"), "from") if a.get("from") else None
            o = headless_call(Arch.makeComponent, base)
            if a.get("ifcType") and hasattr(o, "IfcType"):
                o.IfcType = a["ifcType"]
        elif kind == "clone":
            src = picks.find(d(), a.get("from"), "from")
            o = headless_call(Arch.cloneComponent, src)
            if a.get("at") is not None:
                o.Placement = picks.placement(a)
        elif kind == "unclone":
            o = picks.find(d(), a.get("from"), "from")
            src = getattr(o, "CloneOf", None)
            if src is None:
                raise ToolError("invalid_state", f"{o.Label} is not a clone.")
            pl = FreeCAD.Placement(o.Placement)
            o.CloneOf = None
            skip = {"Objects", "CloneOf", "ExpressionEngine", "HorizontalArea", "Area", "VerticalArea", "PerimeterLength", "Placement", "Proxy", "Shape", "Label", "Label2"}
            for p in src.PropertiesList:
                if p not in skip and p in o.PropertiesList:
                    try:
                        setattr(o, p, getattr(src, p))
                    except Exception:
                        pass
            o.Placement = pl
        else:
            raise bad("kind: component { from, ifcType } | clone { from, at } | unclone { from }.")
        named(o, a.get("name"))
        return done("bim_component", {"object": o.Label, "kind": kind, **reading(o)})

    def bim_reference(a):
        p = os.path.expanduser(a.get("path") or "")
        if not os.path.isfile(p):
            raise ToolError("not_found", f"path {p!r}: no such file — an .FCStd (or .ifc) to reference.")
        o = headless_call(Arch.makeReference, p, a.get("part"))
        named(o, a.get("name"))
        d().recompute()
        s.snapshot("bim_reference")
        return {"reference": o.Label, "file": os.path.basename(p), "part": getattr(o, "Part", None) or None, **reading(o),
                "note": "Shown from the other file and kept up to date with it; not copied in."}

    def bim_remove_shape(a):
        targets = objs(a.get("objects"))
        headless_call(Arch.removeShape, targets)
        d().recompute()
        s.snapshot("bim_remove_shape")
        return {"turned": [o.Label for o in targets], "note": "Shape-only BIM objects rebuilt from what they are (a box as a structure's profile and height) where Arch can read them."}

    def bim_structural_system(a):
        structs = objs(a.get("structures"), "structures")
        axes = objs(a.get("axes"), "axes")
        o = headless_call(Arch.makeStructuralSystem, structs, axes)
        named(o, a.get("name"))
        return done("bim_structural_system", {"system": o.Label, "copies": len(o.Shape.Solids) if not o.Shape.isNull() else 0, **reading(o)})

    def bim_pipe_connector(a):
        pipes = objs(a.get("pipes"), "pipes")
        if not 2 <= len(pipes) <= 3:
            raise bad("pipes: two (an elbow) or three (a tee) that meet.")
        o = headless_call(Arch.makePipeConnector, pipes, float(a.get("radius", 0)))
        named(o, a.get("name"))
        return done("bim_pipe_connector", {"connector": o.Label, "pipes": [p.Label for p in pipes], **reading(o)})

    def bim_panel_out(a):
        kind = a.get("kind", "cut")
        panels = objs(a.get("panels"), "panels")
        if kind == "cut":
            outs = [named(headless_call(Arch.makePanelCut, p), None) for p in panels]
            d().recompute()
            s.snapshot("bim_panel_out")
            return {"cuts": [{"panel": p.Label, "cut": o.Label, "edges": len(o.Shape.Edges) if not o.Shape.isNull() else 0} for p, o in zip(panels, outs)],
                    "note": "Flat outlines to cut, with the panel's tag; export_file to DXF or SVG for the cutter."}
        if kind == "sheet":
            cuts = [headless_call(Arch.makePanelCut, p) for p in panels]
            sh = headless_call(Arch.makePanelSheet, cuts)
            if a.get("width"):
                sh.Width = float(a["width"])
            if a.get("height"):
                sh.Height = float(a["height"])
            named(sh, a.get("name"))
            return done("bim_panel_out", {"sheet": sh.Label, "panels": len(cuts), "size": [float(sh.Width), float(sh.Height)]})
        raise bad("kind: cut | sheet { width, height }.")

    def bim_nest(a):
        import ArchNesting

        cont = picks.shaped(d(), a.get("container"), "container")
        pieces = objs(a.get("pieces"), "pieces")
        n = ArchNesting.Nester()
        n.addContainer(cont)
        n.addObjects(pieces)
        if a.get("rotations"):
            n.rotations = [int(r) for r in a["rotations"]]
        if a.get("margin") is not None:
            n.margin = float(a["margin"])
        res = n.run()
        if not n.results:
            raise ToolError("failed", "The pieces would not nest into the container: a bigger sheet, fewer pieces, or more rotations.")
        n.apply()
        d().recompute()
        s.snapshot("bim_nest")
        sheets = n.results[-1]
        return {"sheets": len(sheets), "placed": sum(len(x) for x in sheets), "of": len(pieces), "ran": bool(res is not False)}

    def bim_ifc_brep(a):
        out = []
        for o in objs(a.get("objects")):
            if not hasattr(o, "IfcData"):
                raise bad(f"{o.Label} has no IFC data (not a BIM object).")
            data = dict(o.IfcData)
            on = a.get("on")
            if on is None:
                on = data.get("FlagForceBrep") != "True"
            if on:
                data["FlagForceBrep"] = "True"
            else:
                data.pop("FlagForceBrep", None)
            o.IfcData = data
            out.append({"object": o.Label, "forceBrep": bool(on)})
        return done("bim_ifc_brep", {"set": out, "note": "Forced B-rep objects export to IFC as plain geometry, not parametric extrusions."})

    def bim_views(a):
        import Draft

        levels = objs(a["levels"], "levels") if a.get("levels") else [o for o in d().Objects if getattr(o, "IfcType", "") == "Building Storey"]
        if not levels:
            raise ToolError("not_found", "No levels: bim_level makes them, or give levels.")
        cut = float(a.get("cutHeight", 1100))
        out = []
        for lv in levels:
            z = lv.Placement.Base.z + cut
            sp = headless_call(Arch.makeSectionPlane, [lv])
            sp.Placement = FreeCAD.Placement(V(0, 0, z), FreeCAD.Rotation())
            sp.Label = s.unique(f"{lv.Label} plan cut")
            v = Draft.make_shape2dview(sp)
            if hasattr(v, "ProjectionMode"):
                v.ProjectionMode = "Cutfaces" if a.get("cutOnly") else "Solid"
            v.Label = s.unique(f"{lv.Label} plan")
            out.append({"level": lv.Label, "section": sp.Label, "view": v.Label, "at": z})
        return done("bim_views", {"views": out, "note": "2D plan views at the cut height; techdraw_page puts them on a sheet."})

    def bim_image_plane(a):
        p = os.path.expanduser(a.get("path") or "")
        if not os.path.isfile(p):
            raise ToolError("not_found", f"path {p!r}: no such image.")
        o = d().addObject("Image::ImagePlane", "ImagePlane")
        o.ImageFile = p
        w = a.get("width")
        h = a.get("height")
        if w or h:
            try:
                from PIL import Image  # noqa: F401  — the aspect from the file where Pillow is there

                with Image.open(p) as im:
                    ratio = im.height / im.width
            except Exception:
                ratio = None
            o.XSize = float(w or (float(h) / ratio if ratio else h))
            o.YSize = float(h or (float(w) * ratio if ratio else w))
        if a.get("at") is not None:
            o.Placement = picks.placement(a)
        named(o, a.get("name") or os.path.splitext(os.path.basename(p))[0])
        return done("bim_image_plane", {"image": o.Label, "size": [float(o.XSize), float(o.YSize)], "note": "A picture to trace over: a plan, an elevation, a photo with a known length."})

    def bim_library(a):
        roots = [a["folder"]] if a.get("folder") else []
        try:
            pref = FreeCAD.ParamGet("User parameter:Plugins/parts_library").GetString("destination", "")
            if pref:
                roots.append(pref)
        except Exception:
            pass
        roots.append(os.path.join(FreeCAD.getUserAppDataDir(), "Mod", "parts_library"))
        roots = [os.path.expanduser(r) for r in roots if r and os.path.isdir(os.path.expanduser(r))]
        if not roots:
            raise ToolError("not_found", "No objects library here: give folder (a clone of FreeCAD's parts library), or install parts_library from the Addon Manager.")
        q = str(a.get("search") or "").lower()
        hits = []
        for r in roots:
            for f in glob.glob(os.path.join(r, "**", "*"), recursive=True):
                if f.lower().endswith((".fcstd", ".step", ".stp", ".iges", ".igs", ".stl", ".brep")) and all(w in f.lower() for w in q.split()):
                    hits.append(f)
                    if len(hits) >= 200:
                        break
        if a.get("insert"):
            p = a["insert"] if os.path.isabs(a["insert"]) else next((h for h in hits if os.path.basename(h) == a["insert"]), None)
            if not p or not os.path.isfile(p):
                raise ToolError("not_found", f"insert {a['insert']!r}: not found — search first.")
            before = {o.Name for o in d().Objects}
            if p.lower().endswith(".fcstd"):
                d().mergeProject(p)
            else:
                Part.insert(p, d().Name)
            d().recompute()
            new = [o.Label for o in d().Objects if o.Name not in before]
            s.snapshot("bim_library")
            return {"inserted": os.path.basename(p), "objects": new}
        return {"found": [{"name": os.path.basename(h), "path": h} for h in hits[:50]], "more": max(0, len(hits) - 50), "roots": roots}

    def bim_reextrude(a):
        import Draft

        label, sub, fac = picks.resolve(s, a.get("face"))
        if sub is None or not sub.startswith("Face"):
            raise bad("face: \"Wall.Face3\" — the face to extrude from again.")
        o = picks.find(d(), label)
        straight = len(fac.Wires) == 1 and all(e.tangentAt(e.FirstParameter).getAngle(e.tangentAt(e.LastParameter)) < 1e-4 for e in fac.Edges)
        if straight:
            base = Draft.make_wire([v.Point for v in fac.Wires[0].OrderedVertexes], closed=True)
        else:
            base = d().addObject("Part::Feature", "Face")
            base.Shape = fac
        t = Draft.getType(o)
        new = headless_call(Arch.makeWall if t == "Wall" else Arch.makePanel if t == "Panel" else Arch.makeStructure, base)
        norm = fac.normalAt(0, 0).negative()
        new.Normal = norm
        for e in o.Shape.Edges:
            if abs(e.tangentAt(0).getAngle(norm)) < 1e-4 or abs(e.tangentAt(0).getAngle(norm) - 3.141592653589793) < 1e-4:
                if hasattr(new, "Thickness"):
                    new.Thickness = e.Length
                else:
                    new.Height = e.Length
                break
        for p in ("Material", "IfcType", "StandardCode"):
            if hasattr(o, p) and hasattr(new, p):
                setattr(new, p, getattr(o, p))
        new.Label = s.unique(a.get("name") or o.Label)
        if a.get("delete", True):
            for parent in o.InList:
                if hasattr(parent, "Group") and o in parent.Group:
                    parent.addObject(new)
            d().removeObject(o.Name)
        return done("bim_reextrude", {"object": new.Label, "from": label, "face": sub, "wire": straight, **reading(new)})

    def bim_rewire(a):
        import Draft
        import DraftGeomUtils

        targets = objs(a.get("objects"))
        edges = [e for o in targets for e in getattr(o.Shape, "Edges", [])]
        if not edges:
            raise bad("objects: lines or wires with edges.")
        made = []
        for w in DraftGeomUtils.findWires(edges):
            if DraftGeomUtils.hasCurves(w):
                n = d().addObject("Part::Feature", "Wire")
                n.Shape = w
            else:
                n = Draft.make_wire([v.Point for v in w.OrderedVertexes], closed=w.isClosed())
            made.append(n.Label)
        for o in targets:
            d().removeObject(o.Name)
        return done("bim_rewire", {"wires": made, "from": len(targets)})

    def bim_glue(a):
        targets = [o for o in objs(a.get("objects")) if o.isDerivedFrom("Part::Feature") and not o.Shape.isNull()]
        if len(targets) < 2:
            raise bad("objects: two or more shapes to glue into one.")
        c = d().addObject("Part::Feature", "Glue")
        c.Shape = Part.makeCompound([o.Shape for o in targets])
        c.Label = s.unique(a.get("name") or "Glue")
        for o in targets:
            d().removeObject(o.Name)
        return done("bim_glue", {"object": c.Label, "of": len(targets), "note": "One plain shape of them all, no longer parametric."})

    def bim_trash(a):
        act = a.get("action", "put")
        trash = d().getObject("Trash")
        if act == "put":
            if trash is None or not trash.isDerivedFrom("App::DocumentObjectGroup"):
                trash = d().addObject("App::DocumentObjectGroup", "Trash")
            for o in objs(a.get("objects")):
                for par in list(o.InList):
                    if par is not trash and hasattr(par, "Group") and o in par.Group:
                        par.removeObject(o) if hasattr(par, "removeObject") else setattr(par, "Group", [x for x in par.Group if x is not o])
                trash.addObject(o)
                if hasattr(o, "Visibility"):
                    o.Visibility = False
            return done("bim_trash", {"trash": [o.Label for o in trash.Group]})
        if act == "empty":
            if trash is None:
                return {"emptied": []}
            gone = []

            def kids(o):
                out = []
                for c in o.OutList:
                    if len(c.InList) == 1 and c.InList[0] is o:
                        out += [c] + kids(c)
                return out

            for o in list(trash.Group):
                if len(o.InList) == 1 and o.InList[0] is trash:
                    for x in [o] + kids(o):
                        gone.append(x.Label)
                        d().removeObject(x.Name)
            return done("bim_trash", {"emptied": gone})
        if act == "list":
            return {"trash": [o.Label for o in trash.Group] if trash is not None else []}
        raise bad("action: put { objects } | list | empty.")

    def bim_reorder(a):
        g = picks.find(d(), a.get("group"), "group")
        if not hasattr(g, "Group"):
            raise bad(f"{g.Label} has no children.")
        kids = list(g.Group)
        if a.get("sort"):
            new = sorted(kids, key=lambda o: o.Label.lower())
        else:
            order = a.get("order") or []
            pick = [picks.find(d(), r) for r in order]
            if any(o not in kids for o in pick):
                raise bad(f"order: only {g.Label}'s children — {', '.join(o.Label for o in kids)}.")
            new = pick + [o for o in kids if o not in pick]
        g.Group = new
        return done("bim_reorder", {"group": g.Label, "order": [o.Label for o in g.Group]})

    st = {"type": "string"}
    arr = {"type": "array", "items": {"type": "string"}}
    num = {"type": "number"}
    v3 = {"type": "array", "items": num}
    return [
        Tool("bim_component", "A generic BIM component: kind component { from (a shape), ifcType } | clone { from, at } (follows the original) | unclone { from } (an independent copy of what it cloned).",
             obj({"kind": {"type": "string", "enum": ["component", "clone", "unclone"]}, "from": st, "ifcType": st, "at": v3, "axis": v3, "angle": num, "name": st}, ["from"]), bim_component, changes=True),
        Tool("bim_reference", "An external reference: another file's objects shown here (part: one of its parts), updated when it changes.", obj({"path": st, "part": st, "name": st}, ["path"]), bim_reference, changes=True),
        Tool("bim_remove_shape", "Turn shape-only BIM objects into what they are (Remove Shape from BIM): a cubic shape becomes a structure with a profile and height.", obj({"objects": arr}, ["objects"]), bim_remove_shape, changes=True),
        Tool("bim_structural_system", "A structural system: structures repeated on the axes' crossings (a column on every grid node).", obj({"structures": arr, "axes": arr, "name": st}, ["structures", "axes"]), bim_structural_system, changes=True),
        Tool("bim_pipe_connector", "A pipe connector where two pipes meet (an elbow) or three (a tee); radius of the bend.", obj({"pipes": arr, "radius": num, "name": st}, ["pipes"]), bim_pipe_connector, changes=True),
        Tool("bim_panel_out", "Panels for the cutter: kind cut (each panel's flat outline with its tag) | sheet { width, height } (the cuts laid on a stock sheet).",
             obj({"kind": {"type": "string", "enum": ["cut", "sheet"]}, "panels": arr, "width": num, "height": num, "name": st}, ["panels"]), bim_panel_out, changes=True),
        Tool("bim_nest", "Nest flat pieces into a container face (a sheet): rotations [0, 90, …], margin mm; the pieces are moved into place.", obj({"container": st, "pieces": arr, "rotations": {"type": "array", "items": num}, "margin": num}, ["container", "pieces"]), bim_nest, changes=True),
        Tool("bim_ifc_brep", "Toggle IFC export as B-rep (plain geometry) for BIM objects: on true/false, toggled when left out.", obj({"objects": arr, "on": {"type": "boolean"}}, ["objects"]), bim_ifc_brep, changes=True),
        Tool("bim_views", "2D plan views of the levels (Create 2D Views): a section plane per level at cutHeight mm (1100 by default) and its 2D view; cutOnly for the cut faces alone.",
             obj({"levels": arr, "cutHeight": num, "cutOnly": {"type": "boolean"}}), bim_views, changes=True),
        Tool("bim_image_plane", "An image plane: a picture (path) at width/height mm (one gives the other by the image's shape) and placement, to trace over.", obj({"path": st, "width": num, "height": num, "at": v3, "axis": v3, "angle": num, "name": st}, ["path"]), bim_image_plane, changes=True),
        Tool("bim_library", "The objects library (FreeCAD's parts library, or folder): search words → files; insert a file's name or path to bring it in.", obj({"search": st, "folder": st, "insert": st}), bim_library, changes=True),
        Tool("bim_reextrude", "Re-extrude a BIM object from one of its faces (face \"Wall.Face3\"): a new wall, panel or structure on that face with the same thickness; delete: false keeps the old.",
             obj({"face": st, "delete": {"type": "boolean"}, "name": st}, ["face"]), bim_reextrude, changes=True),
        Tool("bim_rewire", "Rebuild loose lines and wires as joined Draft wires (Rewire).", obj({"objects": arr}, ["objects"]), bim_rewire, changes=True),
        Tool("bim_glue", "Glue shapes into one plain compound (BIM Glue); the originals go.", obj({"objects": arr, "name": st}, ["objects"]), bim_glue, changes=True),
        Tool("bim_trash", "BIM's trash: action put { objects } (hidden in a Trash group, recoverable) | list | empty (deleted, with children nothing else uses).",
             obj({"action": {"type": "string", "enum": ["put", "list", "empty"]}, "objects": arr}), bim_trash, changes=True),
        Tool("bim_reorder", "Reorder a group's children: order [labels first], or sort: true by name.", obj({"group": st, "order": arr, "sort": {"type": "boolean"}}, ["group"]), bim_reorder, changes=True),
    ]
