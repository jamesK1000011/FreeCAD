# SPDX-License-Identifier: LGPL-2.1-or-later
# The document-level commands still left to the window, done headless: links
# (make, group, to an element, replace, unlink, import), App::Part
# containers, duplicates, merging another file in, the document's
# information, revert and save all, appearance (colour, transparency, a
# colour per face, random colours) and text documents; Part's compound
# tools, join features and curve-network import; PartDesign's clone, moving
# features between bodies and in the tree, the tip, and a stepped shaft.

import os
import random

import FreeCAD
import Part

from ..protocol import Tool, ToolError
from ..tools import obj
from . import pd_common as pdc
from . import picks
from .doc import colour

GROUP = "std_more"
INFO = "Links, App::Part, duplicates, merge files, document info, revert, save all, appearance and face colours, text documents; compounds, join features; PartDesign clone, move feature, reorder, tip, stepped shaft; OpenSCAD tree tidying, structured points, polynomial surface, robot tool, merged cells."

V = FreeCAD.Vector
# Kept on the object itself, so a colour set headless survives the save and
# shows when the file opens in the window (and export colours can read it).
COLOR_PROP = "AgentLinkColor"
FACE_PROP = "AgentLinkFaceColors"


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

    # ---- links ---------------------------------------------------------------------------

    def link_make(a):
        kind = a.get("kind", "link")
        if kind == "group":
            g = d().addObject("App::LinkGroup", "LinkGroup")
            g.Label = s.unique(a.get("name") or "Link group", g)
            members = objs(a.get("objects") or [], "objects") if a.get("objects") else []
            g.setLink(members) if members else None
            return done("link_make", {"link": g.Label, "kind": "group", "elements": [m.Label for m in members]})
        target = a.get("to")
        if not target:
            raise bad("to: the object to link — or \"Box.Face3\" for a link to one element (kind: element).")
        head, sub = picks.split_ref(target) if isinstance(target, str) else (None, None)
        src = picks.find(d(), head or target, "to")
        lk = d().addObject("App::Link", "Link")
        if sub:
            lk.LinkedObject = (src, [sub])
        else:
            lk.LinkedObject = src
        lk.Label = s.unique(a.get("name") or f"{src.Label} link", lk)
        if picks.placed(a):
            lk.Placement = picks.placement(a)
        if a.get("count"):
            lk.ElementCount = int(a["count"])
        return done("link_make", {"link": lk.Label, "to": src.Label, **({"element": sub} if sub else {}),
                                  **({"count": int(a["count"])} if a.get("count") else {}),
                                  "note": "A link shows the object again without a copy: change the original and every link follows."})

    def link_edit(a):
        act = a.get("action")
        if act in ("import", "import_all"):
            links = [o for o in d().Objects if o.TypeId.startswith("App::Link")] if act == "import_all" else objs(a.get("links"), "links")

            def target(o):
                t = getattr(o, "LinkedObject", None)
                return t[0] if isinstance(t, tuple) else t

            outside = [o for o in links if target(o) is not None and target(o).Document is not d()]
            if not outside:
                return {"imported": [], "note": "No link here points into another document."}
            got = d().importLinks(outside)
            d().recompute()
            s.snapshot("link_edit")
            return {"imported": [o.Label for o in got or []], "links": [o.Label for o in outside], "note": "The other documents' objects are copied in; the links now point at the copies."}
        lk = objs(a.get("link"), "link")[0]
        if not lk.TypeId.startswith("App::Link"):
            raise bad(f"{lk.Label} is not a link ({lk.TypeId}).")
        if act == "replace":
            to = picks.find(d(), a.get("to"), "to")
            lk.LinkedObject = to
            return done("link_edit", {"link": lk.Label, "now": to.Label})
        if act == "unlink":
            # A real copy in the link's place, keeping its placement.
            src = lk.LinkedObject[0] if isinstance(lk.LinkedObject, tuple) else lk.LinkedObject
            if src is None:
                raise ToolError("invalid_state", f"{lk.Label} links to nothing.")
            o = d().addObject("Part::Feature", "Body")
            sh = Part.getShape(lk)
            o.Shape = sh
            o.Label = s.unique(a.get("name") or src.Label, o)
            for p in lk.InList:
                if hasattr(p, "Group") and lk in p.Group:
                    p.Group = [o if x is lk else x for x in p.Group]
            d().removeObject(lk.Name)
            return done("link_edit", {"copy": o.Label, "from": src.Label, "volume": round(sh.Volume, 3) if sh.Solids else None})
        raise bad("action: replace { link, to } | unlink { link } | import { links } | import_all.")

    def part_container(a):
        p = d().addObject("App::Part", "Part")
        p.Label = s.unique(a.get("name") or "Part", p)
        members = objs(a["objects"]) if a.get("objects") else []
        for m in members:
            for g in [x for x in m.InList if x.TypeId == "App::Part"]:
                g.removeObject(m)
            p.addObject(m)
        if picks.placed(a):
            p.Placement = picks.placement(a)
        return done("part_container", {"part": p.Label, "objects": [m.Label for m in members], "note": "An App::Part moves everything in it together, by its placement."})

    def object_duplicate(a):
        out = []
        for o in objs(a.get("objects")):
            if a.get("simple"):
                # Part's Simple copy: the shape alone, no history.
                c = d().addObject("Part::Feature", "Copy")
                c.Shape = Part.getShape(o)
                c.Label = s.unique(a.get("name") or f"{o.Label} copy", c)
                out.append({"from": o.Label, "copy": c.Label, "simple": True})
                continue
            c = d().copyObject(o, bool(a.get("dependencies")))
            c.Label = s.unique(a.get("name") or f"{o.Label} copy", c)
            out.append({"from": o.Label, "copy": c.Label})
        return done("object_duplicate", {"duplicated": out})

    # ---- the document --------------------------------------------------------------------

    def doc_merge(a):
        p = os.path.expanduser(a.get("path") or "")
        if not os.path.isfile(p):
            raise ToolError("not_found", f"path {p!r}: no such file — an .FCStd to bring in.")
        before = {o.Name for o in d().Objects}
        d().mergeProject(p)
        d().recompute()
        new = [o.Label for o in d().Objects if o.Name not in before]
        s.snapshot("doc_merge")
        return {"merged": os.path.basename(p), "objects": new}

    INFO_PROPS = {"author": "CreatedBy", "company": "Company", "comment": "Comment", "license": "License", "licenseUrl": "LicenseURL", "lastModifiedBy": "LastModifiedBy", "id": "Id", "uid": "Uid", "label": "Label"}

    def doc_info(a):
        dd = d()
        for k, prop in INFO_PROPS.items():
            if k in a and k not in ("id", "uid"):
                setattr(dd, prop, str(a[k]))
        return {"document": dd.Name, "file": dd.FileName or None, **{k: str(getattr(dd, p, "")) for k, p in INFO_PROPS.items()},
                "created": str(getattr(dd, "CreationDate", "")), "modified": str(getattr(dd, "LastModifiedDate", "")),
                "objects": len(dd.Objects), "bytes": os.path.getsize(dd.FileName) if dd.FileName and os.path.isfile(dd.FileName) else None}

    def doc_revert(a):
        dd = d()
        if not dd.FileName or not os.path.isfile(dd.FileName):
            raise ToolError("invalid_state", f"{dd.Label} was never saved: nothing to go back to.")
        path, label = dd.FileName, dd.Label
        FreeCAD.closeDocument(dd.Name)
        nd = FreeCAD.openDocument(path)
        s.switch(nd)
        return {"document": nd.Name, "label": label, "reverted": path, "objects": len(nd.Objects), "note": "Back to the saved file: unsaved changes are gone."}

    def doc_save_all(a):
        saved, unsaved = [], []
        for name, dd in FreeCAD.listDocuments().items():
            if dd.FileName:
                dd.save()
                saved.append(dd.FileName)
            else:
                unsaved.append(dd.Label)
        return {"saved": saved, **({"neverSaved": unsaved, "note": "Never saved, so no file to save to: doc_save { path } for each."} if unsaved else {})}

    def text_document(a):
        name = a.get("name") or "Notes"
        t = next((o for o in d().Objects if o.TypeId == "App::TextDocument" and o.Label == name), None)
        if t is None:
            t = d().addObject("App::TextDocument", "Text")
            t.Label = s.unique(name, t)
        if "text" in a:
            t.Text = (t.Text + "\n" if a.get("append") and t.Text else "") + str(a["text"])
        return done("text_document", {"text": t.Label, "chars": len(t.Text), "lines": t.Text.count("\n") + 1 if t.Text else 0})

    # ---- appearance ----------------------------------------------------------------------

    def keep_colour(o, prop, kind, value):
        if prop not in o.PropertiesList:
            o.addProperty(kind, prop, "Agent Link", "Colour set by Agent Link")
        setattr(o, prop, value)

    def object_appearance(a):
        targets = objs(a.get("objects"))
        out = []
        for o in targets:
            c = colour(a.get("color"))
            if a.get("random"):
                c = (random.random() * 0.8 + 0.1, random.random() * 0.8 + 0.1, random.random() * 0.8 + 0.1, 1.0)
            vo = getattr(o, "ViewObject", None)
            e = {"object": o.Label}
            if c is not None:
                keep_colour(o, COLOR_PROP, "App::PropertyColor", c[:3])
                if vo is not None and hasattr(vo, "ShapeColor"):
                    vo.ShapeColor = c[:3]
                e["color"] = "#" + "".join(f"{int(round(x * 255)):02x}" for x in c[:3])
            if a.get("transparency") is not None:
                t = int(a["transparency"])
                if not 0 <= t <= 100:
                    raise bad("transparency: 0 (solid) to 100 (clear).")
                keep_colour(o, "AgentLinkTransparency", "App::PropertyInteger", t)
                if vo is not None and hasattr(vo, "Transparency"):
                    vo.Transparency = t
                e["transparency"] = t
            faces = a.get("faces")
            if faces:
                sh = getattr(o, "Shape", None)
                if sh is None or not sh.Faces:
                    raise ToolError("invalid_state", f"{o.Label} has no faces to colour.")
                base = c or (getattr(vo, "ShapeColor", None) if vo is not None else None) or (0.8, 0.8, 0.8)
                cols = [tuple(base[:3]) + (1.0,)] * len(sh.Faces)
                for f, v in faces.items():
                    i = int(str(f).replace("Face", "")) if str(f).replace("Face", "").isdigit() else None
                    if i is None or not 1 <= i <= len(sh.Faces):
                        raise bad(f"faces: {f!r} — {o.Label} has Face1…Face{len(sh.Faces)}.")
                    cols[i - 1] = colour(v)
                keep_colour(o, FACE_PROP, "App::PropertyStringList", ["#" + "".join(f"{int(round(x * 255)):02x}" for x in col[:3]) for col in cols])
                if vo is not None and hasattr(vo, "DiffuseColor"):
                    vo.DiffuseColor = cols
                e["faces"] = len(faces)
            e["shown"] = vo is not None
            out.append(e)
        d().recompute()
        return {"set": out, **({"note": "No window here: the colours are kept on the objects and show when the file opens in FreeCAD."} if not any(x["shown"] for x in out) else {})}

    # ---- Part: compounds, joins, curve networks ------------------------------------------

    def compound_explode(a):
        import CompoundTools.Explode as X

        o = picks.shaped(d(), a.get("compound"), "compound")
        if len(o.Shape.childShapes()) < 2:
            raise ToolError("invalid_state", f"{o.Label} is one shape, nothing to explode — a compound of several is.")
        g, children = X.explodeCompound(o)
        d().recompute()
        s.snapshot("compound_explode")
        return {"group": g.Label, "pieces": [{"name": c.Label, "volume": round(c.Shape.Volume, 3) if c.Shape.Solids else None} for c in children]}

    def compound_filter(a):
        import CompoundTools.CompoundFilter as CF

        o = picks.shaped(d(), a.get("compound"), "compound")
        f = CF.makeCompoundFilter(s.unique(a.get("name") or f"{o.Label} filtered"))
        f.Base = o
        how = a.get("keep", "items")
        if how == "items":
            f.FilterType = "specific items"
            f.items = ";".join(str(int(i)) for i in (a.get("items") or [0]))
        elif how in ("collision-pass", "collision-fail", "window-volume", "window-area", "window-length", "window-distribution"):
            f.FilterType = how
            if a.get("stencil"):
                f.Stencil = picks.find(d(), a["stencil"], "stencil")
            if a.get("from") is not None:
                f.WindowFrom = float(a["from"])
            if a.get("to") is not None:
                f.WindowTo = float(a["to"])
        else:
            raise bad("keep: items | collision-pass | collision-fail | window-volume | window-area | window-length | window-distribution.")
        if getattr(o, "ViewObject", None) is not None:
            o.ViewObject.Visibility = False
        d().recompute()
        if f.Shape.isNull() or "Invalid" in (getattr(f, "State", None) or []):
            raise ToolError("failed", f"The filter keeps nothing of {o.Label} ({how}" + ("" if a.get("stencil") or not how.startswith("collision") else ", and collision filters need a stencil") + ") — nothing was made.")
        s.snapshot("compound_filter")
        return {"filter": f.Label, "kept": len(f.Shape.childShapes())}

    def part_join(a):
        import BOPTools.JoinFeatures as J

        kind = a.get("kind")
        if kind == "connect":
            j = J.makeConnect(s.unique(a.get("name") or "Connect"))
            j.Objects = objs(a.get("objects"))
        elif kind in ("embed", "cutout"):
            j = (J.makeEmbed if kind == "embed" else J.makeCutout)(s.unique(a.get("name") or kind.title()))
            j.Base = picks.shaped(d(), a.get("base"), "base")
            j.Tool = picks.shaped(d(), a.get("tool"), "tool")
        else:
            raise bad("kind: connect { objects } (walls fused, insides kept) | embed { base, tool } (tool's wall set into base) | cutout { base, tool } (base cut to fit tool's wall).")
        d().recompute()
        sh = j.Shape
        s.snapshot("part_join")
        return {"join": j.Label, "kind": kind, "valid": sh.isValid(), "solids": len(sh.Solids), "volume": round(sh.Volume, 3) if sh.Solids else None}

    def part_import_curves(a):
        p = os.path.expanduser(a.get("path") or "")
        if not os.path.isfile(p):
            raise ToolError("not_found", f"path {p!r}: no such file — a STEP, IGES or BREP with curves.")
        sh = Part.read(p)
        edges = sh.Edges
        if not edges:
            raise ToolError("invalid_input", f"{os.path.basename(p)} has no curves.")
        o = d().addObject("Part::Feature", "CurveNet")
        o.Shape = Part.Compound(edges)
        o.Label = s.unique(a.get("name") or os.path.splitext(os.path.basename(p))[0], o)
        return done("part_import_curves", {"curves": o.Label, "edges": len(edges), "note": "Edges only, as Part's curve-network import: surface_filling or surface_sections builds faces from them."})

    # ---- PartDesign ------------------------------------------------------------------------

    def pd_clone(a):
        src = picks.shaped(d(), a.get("from"), "from")
        b = d().addObject("PartDesign::Body", "Body")
        b.Label = s.unique(a.get("name") or f"{src.Label} clone", b)
        b.BaseFeature = src
        if picks.placed(a):
            b.Placement = picks.placement(a)
        d().recompute()
        s.snapshot("pd_clone")
        return {"body": b.Label, "from": src.Label, "volume": round(b.Shape.Volume, 3) if b.Shape.Solids else None,
                "note": "A body built on the other's shape: it follows the original; add features on top."}

    def pd_move_feature(a):
        f = picks.find(d(), a.get("feature"), "feature")
        src = pdc.body_of(f)
        to = pdc.pd_body(d(), a.get("to"))
        if src is to:
            raise bad(f"{f.Label} is in {to.Label} already.")
        broken0 = set(pdc.broken(d()))
        moved = [f]
        # A sketch's feature takes its sketch along, as the GUI's Move does.
        prof = getattr(f, "Profile", None)
        sk = prof[0] if isinstance(prof, tuple) else prof
        if sk is not None and sk.TypeId == "Sketcher::SketchObject" and src is not None and src.hasObject(sk):
            moved.insert(0, sk)
        for o in moved:
            if src is not None:
                src.removeObject(o)
            to.addObject(o)
            sp = pdc.support_prop(o)
            sup = getattr(o, sp, None)
            if o.TypeId == "Sketcher::SketchObject" and src is not None and sup:
                # Origin planes are per body: re-attach to the same plane of the new body.
                ref = sup[0][0] if isinstance(sup, list) else None
                if ref is not None and src.Origin.hasObject(ref):
                    role = ref.Role if hasattr(ref, "Role") else ref.Label
                    new = next((x for x in to.Origin.OriginFeatures if getattr(x, "Role", x.Label) == role), None)
                    if new is not None:
                        setattr(o, sp, [(new, "")])
        d().recompute()
        new_broken = [x for x in pdc.broken(d()) if x not in broken0]
        if new_broken:
            # Raised inside the tool's transaction: the move is taken back.
            raise ToolError("invalid_state", f"Moving {f.Label} to {to.Label} breaks {', '.join(map(str, new_broken))} — nothing was moved. What it is built on must go with it, or it be rebuilt there.")
        s.snapshot("pd_move_feature")
        return {"moved": [o.Label for o in moved], "from": src.Label if src else None, "to": to.Label, "tip": to.Tip.Label if to.Tip else None}

    def pd_reorder(a):
        f = picks.find(d(), a.get("feature"), "feature")
        b = pdc.body_of(f)
        if b is None:
            raise ToolError("invalid_state", f"{f.Label} is not in a PartDesign body.")
        where = "after" if a.get("after") else "before"
        target = pdc.in_body(b, a.get(where), what=where) if a.get(where) else None
        if target is None:
            raise bad("after or before: the feature to put it next to.")
        broken0 = set(pdc.broken(d()))
        tip_was = b.Tip
        b.removeObject(f)
        b.insertObject(f, target, where == "after")
        # Put after the tip, a feature would drop out of the body's shape
        # with nothing said: the tip goes with it, as in the window.
        moved_tip = False
        if where == "after" and tip_was is target:
            b.Tip = f
            moved_tip = True
        elif tip_was is f:
            b.Tip = f
        d().recompute()
        new_broken = [x for x in pdc.broken(d()) if x not in broken0]
        if new_broken:
            raise ToolError("invalid_state", f"{f.Label} {where} {target.Label} breaks {', '.join(map(str, new_broken))} — nothing was moved: it uses something that would come after it.")
        s.snapshot("pd_reorder")
        order = [o.Label for o in b.Group if not o.TypeId.startswith("App::Origin")]
        return {"body": b.Label, "order": order, "tip": b.Tip.Label if b.Tip else None, **({"tipMoved": True} if moved_tip else {}),
                "volume": round(b.Shape.Volume, 3) if not b.Shape.isNull() and b.Shape.Solids else None}

    def pd_tip(a):
        b = pdc.pd_body(d(), a.get("body"))
        if a.get("feature"):
            b.Tip = pdc.in_body(b, a["feature"])
            d().recompute()
            s.snapshot("pd_tip")
        return {"body": b.Label, "tip": b.Tip.Label if b.Tip else None, "volume": round(b.Shape.Volume, 3) if not b.Shape.isNull() and b.Shape.Solids else None,
                "note": "Features after the tip are kept but not in the body's shape until the tip moves past them."}

    def pd_shaft(a):
        segs = a.get("segments") or []
        if not segs:
            raise bad("segments: [{ length, diameter }, …] from one end; a segment may also give diameterEnd for a taper.")
        pts, x = [V(0, 0, 0)], 0.0
        for i, sg in enumerate(segs):
            L, D = float(sg.get("length", 0)), float(sg.get("diameter", 0))
            D2 = float(sg.get("diameterEnd", D))
            if L <= 0 or D <= 0 or D2 <= 0:
                raise bad(f"segment {i + 1}: length and diameter over 0.")
            pts += [V(x, D / 2, 0), V(x + L, D2 / 2, 0)]
            x += L
        pts.append(V(x, 0, 0))
        clean = [pts[0]]
        for p in pts[1:]:
            if (p - clean[-1]).Length > 1e-9:
                clean.append(p)
        clean.append(pts[0])
        face = Part.Face(Part.makePolygon(clean))
        solid = face.revolve(V(0, 0, 0), V(1, 0, 0), 360)
        solid = Part.Solid(Part.Shell(solid.Faces)) if solid.ShapeType != "Solid" else solid
        if a.get("axis", "x") != "x":
            solid.rotate(V(0, 0, 0), V(0, 1, 0), -90) if a["axis"] == "z" else solid.rotate(V(0, 0, 0), V(0, 0, 1), 90)
        o = d().addObject("Part::Feature", "Shaft")
        o.Shape = solid.removeSplitter()
        o.Label = s.unique(a.get("name") or "Shaft", o)
        if picks.placed(a):
            o.Placement = picks.placement(a)
        return done("pd_shaft", {"shaft": o.Label, "length": round(x, 3), "segments": len(segs), "volume": round(o.Shape.Volume, 3),
                                 "note": "Shoulders are sharp: pd_fillet/part chamfer them; strength_check or fem for loads."})

    # ---- other workbenches' leftovers ---------------------------------------------------

    def scad_tidy(a):
        act = a.get("action")
        targets = objs(a.get("objects")) if a.get("objects") else []
        if act == "color_code":
            # OpenSCAD's Color Shapes: by validity and what the shape most is.
            pal = {"invalid": "#ff0000", "Solid": "#cccccc", "Shell": "#ffaa00", "Face": "#ffff00", "Wire": "#00ccff", "Edge": "#0066ff", "Vertex": "#aa00ff", "empty": "#555555"}
            rows = []
            for o in targets or [x for x in d().Objects if getattr(x, "Shape", None) is not None]:
                sh = o.Shape
                kind = "empty" if sh.isNull() else ("invalid" if not sh.isValid() else next((k for k, lst in (("Solid", sh.Solids), ("Shell", sh.Shells), ("Face", sh.Faces), ("Wire", sh.Wires), ("Edge", sh.Edges), ("Vertex", sh.Vertexes)) if lst), "empty"))
                c = colour(pal[kind])
                keep_colour(o, COLOR_PROP, "App::PropertyColor", c[:3])
                vo = getattr(o, "ViewObject", None)
                if vo is not None and hasattr(vo, "ShapeColor"):
                    vo.ShapeColor = c[:3]
                rows.append({"object": o.Label, "is": kind, "color": pal[kind]})
            return {"coloured": rows, "key": pal}
        if act == "explode":
            out = []
            for o in targets:
                if not (o.isDerivedFrom("Part::Fuse") or o.isDerivedFrom("Part::MultiFuse") or o.isDerivedFrom("Part::Compound")):
                    raise bad(f"{o.Label} is not a fusion or compound ({o.TypeId}).")
                if o.InList:
                    raise bad(f"{o.Label} is used by {o.InList[0].Label}: only a top-level one explodes.")
                plm, kids = o.Placement, list(o.OutList)
                d().removeObject(o.Name)
                for k in kids:
                    if not plm.isNull():
                        k.Placement = plm.multiply(k.Placement)
                    if getattr(k, "ViewObject", None) is not None:
                        k.ViewObject.Visibility = True
                out.append({"from": o.Label, "pieces": [k.Label for k in kids]})
            return done("scad_tidy", {"exploded": out})
        if act == "expand_placements":
            import expandplacements

            for o in targets:
                expandplacements.expandplacements(o, FreeCAD.Placement())
            return done("scad_tidy", {"expanded": [o.Label for o in targets], "note": "Placements pushed down to the leaves of the tree."})
        if act == "tolerance":
            import OpenSCADFeatures

            out = []
            for o in targets:
                n = d().addObject("Part::FeaturePython", "tolerance")
                OpenSCADFeatures.IncreaseTolerance(n, o)
                for k in ("vertex", "edge", "face"):
                    if a.get(k) is not None:
                        setattr(n, k.title(), float(a[k]))
                n.Label = s.unique(f"tolerance_{o.Label}", n)
                if getattr(o, "ViewObject", None) is not None:
                    o.ViewObject.Visibility = False
                out.append(n.Label)
            return done("scad_tidy", {"made": out})
        if act == "remove_subtree":
            import OpenSCADUtils

            before = {o.Label for o in d().Objects}
            OpenSCADUtils.removesubtree(targets)
            return done("scad_tidy", {"removed": sorted(before - {o.Label for o in d().Objects})})
        if act == "replace":
            import replaceobj

            parent, old, new = (picks.find(d(), a.get(k), k) for k in ("parent", "old", "new"))
            replaceobj.replaceobj(parent, old, new)
            return done("scad_tidy", {"parent": parent.Label, "replaced": old.Label, "with": new.Label})
        raise bad("action: color_code | explode | expand_placements | tolerance { vertex, edge, face } | remove_subtree | replace { parent, old, new }.")

    def points_structure(a):
        o = picks.find(d(), a.get("points"), "points")
        if not o.isDerivedFrom("Points::Feature"):
            raise bad(f"{o.Label} is not a point cloud.")
        n = o.Points.CountPoints
        w = int(a.get("width") or 0)
        h = int(a.get("height") or (n // w if w else 0))
        if w <= 0 or w * h != n:
            raise bad(f"width × height must be the point count ({n}): a scanner's rows × columns.")
        st_ = d().addObject("Points::Structured", "Structured")
        st_.Points = o.Points
        st_.Width, st_.Height = w, h
        st_.Label = s.unique(a.get("name") or f"{o.Label} structured", st_)
        return done("points_structure", {"points": st_.Label, "width": w, "height": h})

    def surface_polynomial(a):
        import numpy as np

        o = picks.find(d(), a.get("from"), "from")
        if hasattr(o, "Mesh"):
            P = np.array([[p.x, p.y, p.z] for p in o.Mesh.Points])
        elif o.isDerivedFrom("Points::Feature"):
            P = np.array([[p.x, p.y, p.z] for p in o.Points.Points])
        else:
            raise bad(f"{o.Label}: a mesh or a point cloud.")
        if len(P) < 9:
            raise bad("At least 9 points.")
        # As Reen's Polynomial Surface: a quadric z(u, v) over the points'
        # best plane, made a 3×3 Bézier patch over their box.
        c = P.mean(0)
        _, _, vt = np.linalg.svd(P - c)
        ex, ey, ez = vt[0], vt[1], vt[2]
        L = (P - c) @ np.array([ex, ey, ez]).T
        u, v, z = L[:, 0], L[:, 1], L[:, 2]
        A = np.c_[np.ones_like(u), u, v, u * u, u * v, v * v]
        k, *_ = np.linalg.lstsq(A, z, rcond=None)
        u0, u1, v0, v1 = u.min(), u.max(), v.min(), v.max()
        f = lambda x, y: k[0] + k[1] * x + k[2] * y + k[3] * x * x + k[4] * x * y + k[5] * y * y  # noqa: E731
        # A quadric over a box is a degree-2 Bézier patch: its 9 poles by least squares on a grid.
        B = lambda t: np.array([(1 - t) ** 2, 2 * t * (1 - t), t * t])  # noqa: E731
        ts = np.linspace(0, 1, 7)
        rows, rhs = [], []
        for tu in ts:
            for tv in ts:
                bu, bv = B(tu), B(tv)
                rows.append(np.outer(bu, bv).ravel())
                rhs.append(f(u0 + (u1 - u0) * tu, v0 + (v1 - v0) * tv))
        zp, *_ = np.linalg.lstsq(np.array(rows), np.array(rhs), rcond=None)
        poles = []
        for i in range(3):
            row = []
            for j in range(3):
                uu = u0 + (u1 - u0) * i / 2
                vv = v0 + (v1 - v0) * j / 2
                w = c + ex * uu + ey * vv + ez * zp[i * 3 + j]
                row.append(V(*w))
            poles.append(row)
        bz = Part.BezierSurface()
        bz.increase(2, 2)
        for i in range(3):
            for j in range(3):
                bz.setPole(i + 1, j + 1, poles[i][j])
        sp = d().addObject("Part::Spline", "Bezier")
        sp.Shape = bz.toShape()
        sp.Label = s.unique(a.get("name") or f"{o.Label} polynomial", sp)
        res = z - f(u, v)
        return done("surface_polynomial", {"surface": sp.Label, "points": len(P), "rms": round(float(np.sqrt((res ** 2).mean())), 4), "max": round(float(np.abs(res).max()), 4)})

    def robot_tool(a):
        r = picks.find(d(), a.get("robot"), "robot")
        if not r.isDerivedFrom("Robot::RobotObject"):
            raise bad(f"{r.Label} is not a robot.")
        t = picks.shaped(d(), a.get("tool"), "tool")
        r.ToolShape = t
        if picks.placed(a):
            r.ToolBase = picks.placement(a)
        return done("robot_tool", {"robot": r.Label, "tool": t.Label, "note": "The tool shape follows the robot's flange (its ToolBase offset)."})

    def sheet_cells(a):
        sh = picks.find(d(), a.get("sheet"), "sheet")
        if not sh.isDerivedFrom("Spreadsheet::Sheet"):
            raise bad(f"{sh.Label} is not a spreadsheet.")
        if a.get("merge"):
            sh.mergeCells(str(a["merge"]))
        if a.get("split"):
            sh.splitCell(str(a["split"]))
        if not a.get("merge") and not a.get("split"):
            raise bad("merge: \"A1:C1\" (one cell across them) or split: \"A1\" (a merged cell back apart).")
        cell = str(a.get("merge") or a.get("split")).split(":")[0]
        return done("sheet_cells", {"sheet": sh.Label, "merged": bool(a.get("merge")), "span": list(sh.getCellSpan(cell)) if hasattr(sh, "getCellSpan") else None})

    st = {"type": "string"}
    arr = {"type": "array", "items": {"type": "string"}}
    num = {"type": "number"}
    v3 = {"type": "array", "items": num}
    return [
        Tool("link_make", "A link (App::Link): to an object (or \"Box.Face3\" for one element), at a placement, count for an array of links; kind: group makes an App::LinkGroup of objects. Shows the object again without copying it.",
             obj({"to": st, "kind": {"type": "string", "enum": ["link", "group"]}, "objects": arr, "at": v3, "axis": v3, "angle": num, "count": {"type": "integer"}, "name": st}), link_make, changes=True),
        Tool("link_edit", "Change links: action replace { link, to } | unlink { link } (a real copy in its place) | import { links } / import_all (objects linked from another document copied in).",
             obj({"action": {"type": "string", "enum": ["replace", "unlink", "import", "import_all"]}, "link": st, "links": arr, "to": st, "name": st}, ["action"]), link_edit, changes=True),
        Tool("part_container", "An App::Part container holding objects, moved together by its placement (Std Part).",
             obj({"objects": arr, "at": v3, "axis": v3, "angle": num, "name": st}), part_container, changes=True),
        Tool("object_duplicate", "Copy objects in the document; dependencies: true copies what each is built from too; simple: true copies the shape alone (no history).",
             obj({"objects": arr, "dependencies": {"type": "boolean"}, "simple": {"type": "boolean"}, "name": st}, ["objects"]), object_duplicate, changes=True),
        Tool("doc_merge", "Bring another .FCStd's objects into this document (Merge document).", obj({"path": st}, ["path"]), doc_merge, changes=True),
        Tool("doc_info", "The document's information: author, company, comment, license, licenseUrl, label — given ones are set; created and modified dates, file, size.",
             obj({k: st for k in ("author", "company", "comment", "license", "licenseUrl", "lastModifiedBy", "label")}), doc_info),
        Tool("doc_revert", "Go back to the document's saved file, dropping unsaved changes.", obj({}), doc_revert),
        Tool("doc_save_all", "Save every open document that has a file.", obj({}), doc_save_all),
        Tool("text_document", "A text document in the file (App::TextDocument): name, text (append: true adds to it). Notes that travel with the model.",
             obj({"name": st, "text": st, "append": {"type": "boolean"}}), text_document, changes=True),
        Tool("object_appearance", "Colour objects: color \"#rrggbb\" or random: true, transparency 0–100, faces { \"Face3\": \"#ff0000\" } for a colour per face. Kept on the object, shown in the window.",
             obj({"objects": arr, "color": {}, "random": {"type": "boolean"}, "transparency": {"type": "integer"}, "faces": {"type": "object"}}, ["objects"]), object_appearance, changes=True),
        Tool("compound_explode", "Split a compound into its pieces, each an object of its own (Part Explode compound).", obj({"compound": st}, ["compound"]), compound_explode, changes=True),
        Tool("compound_filter", "Keep some pieces of a compound (Part Compound filter): keep items { items: [0, 2] } | collision-pass / collision-fail { stencil } | window-volume / -area / -length / -distribution { from, to } as percents.",
             obj({"compound": st, "keep": st, "items": {"type": "array", "items": {"type": "integer"}}, "stencil": st, "from": num, "to": num, "name": st}, ["compound"]), compound_filter, changes=True),
        Tool("part_join", "Join walled objects (Part join features): kind connect { objects } fuses walls keeping the insides; embed { base, tool } sets tool into base; cutout { base, tool } cuts base to fit tool's wall. Pipes and tanks.",
             obj({"kind": {"type": "string", "enum": ["connect", "embed", "cutout"]}, "objects": arr, "base": st, "tool": st, "name": st}, ["kind"]), part_join, changes=True),
        Tool("part_import_curves", "Import a curve network (the edges of a STEP, IGES or BREP) as one object, for surfacing.", obj({"path": st, "name": st}, ["path"]), part_import_curves, changes=True),
        Tool("pd_clone", "A new PartDesign body on another object's shape (PartDesign Clone): it follows the original; features go on top.", obj({"from": st, "at": v3, "axis": v3, "angle": num, "name": st}, ["from"]), pd_clone, changes=True),
        Tool("pd_move_feature", "Move a PartDesign feature (with its sketch) to another body.", obj({"feature": st, "to": st}, ["feature", "to"]), pd_move_feature, changes=True),
        Tool("pd_reorder", "Move a feature in its body's tree: after or before another feature.", obj({"feature": st, "after": st, "before": st}, ["feature"]), pd_reorder, changes=True),
        Tool("pd_tip", "Read or set a body's tip (the feature its shape stops at): feature.", obj({"body": st, "feature": st}), pd_tip, changes=True),
        Tool("scad_tidy", "OpenSCAD workbench's tree tools: action color_code (shapes coloured by validity and type) | explode (a fusion or compound back into its pieces) | expand_placements | tolerance { vertex, edge, face mm } (a looser-tolerance copy) | remove_subtree (objects and children nothing else uses) | replace { parent, old, new }.",
             obj({"action": {"type": "string", "enum": ["color_code", "explode", "expand_placements", "tolerance", "remove_subtree", "replace"]}, "objects": arr, "vertex": num, "edge": num, "face": num, "parent": st, "old": st, "new": st}, ["action"]), scad_tidy, changes=True),
        Tool("points_structure", "Make a point cloud structured (rows × columns, as a scanner's grid): width, height.", obj({"points": st, "width": {"type": "integer"}, "height": {"type": "integer"}, "name": st}, ["points", "width"]), points_structure, changes=True),
        Tool("surface_polynomial", "A polynomial (quadric) surface fitted to a mesh or point cloud, as a Bézier patch (Reverse Engineering's Polynomial Surface): its rms and max distance from the points.",
             obj({"from": st, "name": st}, ["from"]), surface_polynomial, changes=True),
        Tool("robot_tool", "Put a tool shape on a robot's flange (Robot's Add tool shape): tool, at for its offset.", obj({"robot": st, "tool": st, "at": v3, "axis": v3, "angle": num}, ["robot", "tool"]), robot_tool, changes=True),
        Tool("sheet_cells", "Merge spreadsheet cells (merge \"A1:C1\") or split a merged one (split \"A1\").", obj({"sheet": st, "merge": st, "split": st}, ["sheet"]), sheet_cells, changes=True),
        Tool("pd_shaft", "A stepped shaft (the shaft wizard's geometry): segments [{ length, diameter, diameterEnd }] from one end along axis x (or y, z).",
             obj({"segments": {"type": "array", "items": {"type": "object"}}, "axis": {"type": "string", "enum": ["x", "y", "z"]}, "at": v3, "name": st}, ["segments"]), pd_shaft, changes=True),
    ]
