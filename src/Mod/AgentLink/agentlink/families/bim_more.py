# SPDX-License-Identifier: LGPL-2.1-or-later
# More of FreeCAD's BIM workbench headless: frames (a library section along
# a path), trusses, fences, coverings (tiles, finishes), layered walls and
# slabs (multi-materials), IFC property sets and classification, quantity
# take-off by IFC type and material, an IFC preflight of the model, and
# component edits (additions, subtractions, plane cuts, joined walls, clones).

import FreeCAD
import Part

import Arch  # the family is left out without BIM

from ..protocol import Tool, ToolError
from ..tools import obj
from . import picks
from .bim import UP, bad, contained, frame, headless_call, ifc_type, is_bim, vec  # noqa: F401

GROUP = "bim"
INFO = "BIM extras: frames, trusses, fences, coverings, layered walls (multi-materials), IFC properties and classification, quantity take-off, IFC preflight, additions/subtractions/cuts/joins."

V = FreeCAD.Vector


def proxy_name(o):
    return type(getattr(o, "Proxy", None)).__name__


def setsome(o, vals):
    done, skipped = {}, []
    for k, v in vals.items():
        if v is None:
            continue
        if hasattr(o, k):
            try:
                setattr(o, k, v)
                done[k] = v
            except Exception as e:
                skipped.append(f"{k}: {e}")
        else:
            skipped.append(f"{k}: not on this FreeCAD's {proxy_name(o)}")
    return done, skipped


def settype(o, t):
    try:
        o.IfcType = t
    except Exception:
        pass


def qty(v):
    return float(getattr(v, "Value", v) or 0)


def tools(session):
    s = session
    doc = lambda: s.doc  # noqa: E731

    def finish(o, tool, extra=None):
        doc().recompute()
        sh = getattr(o, "Shape", None)
        out = {"object": o.Label, "ifc": ifc_type(o)}
        if isinstance(sh, Part.Shape) and not sh.isNull():
            bb = sh.BoundBox
            out["box"] = [round(bb.XLength, 1), round(bb.YLength, 1), round(bb.ZLength, 1)]
            if sh.Solids:
                out["volume_m3"] = round(sh.Volume / 1e9, 4)
                out["solids"] = len(sh.Solids)
        else:
            out["warning"] = "no shape came out — check its base and sizes."
        st = o.getStatusString() if hasattr(o, "getStatusString") else ""
        if st and st not in ("Valid", "Touched"):
            out["status"] = st
        s.snapshot(tool)
        return {**out, **(extra or {}), "step": len(s.steps)}

    def path_obj(a, label):
        if a.get("path"):
            return picks.find(doc(), a["path"], "path")
        pts = a.get("points")
        if not (isinstance(pts, list) and len(pts) >= 2):
            raise bad("path (a wire, sketch or line) or points [[x,y,z] …].")
        o = doc().addObject("Part::Feature", "Path")
        o.Shape = Part.makePolygon([vec(p, "points") for p in pts])
        o.Label = s.unique(label)
        o.Visibility = False
        return o

    def library_profile(name):
        import ArchProfile
        rows = headless_call(ArchProfile.readPresets)
        row = next((r for r in rows if str(r[2]).lower() == str(name).lower()), None)
        if row is None:
            raise bad(f"profile: {name!r} is not in the library (bim_profiles lists them).")
        p = headless_call(Arch.makeProfile, row)
        p.Label = s.unique(f"{row[2]} section", p)
        return p, row

    def arch_material(name, density=None):
        m = next((o for o in doc().Objects if o.Label == name and proxy_name(o) == "_ArchMaterial"), None)
        if m is None:
            m = headless_call(Arch.makeMaterial, name)
            m.Label = s.unique(name, m)
        if density is not None:
            d = dict(getattr(m, "Material", {}) or {})
            d["Density"] = f"{density} kg/m^3"
            m.Material = d
        return m

    def density_of(o):
        m = getattr(o, "Material", None)
        if m is None:
            return None
        mats = getattr(m, "Materials", None)
        if mats:  # a multi-material: thickness-weighted
            th = [qty(t) for t in getattr(m, "Thicknesses", [])]
            ds = [density_of_mat(x) for x in mats]
            if th and all(d is not None for d in ds) and sum(th) > 0:
                return sum(d * t for d, t in zip(ds, th)) / sum(th)
            return None
        return density_of_mat(m)

    def density_of_mat(m):
        d = (getattr(m, "Material", {}) or {}).get("Density")
        if not d:
            return None
        try:
            return float(str(d).split()[0])
        except ValueError:
            return None

    # ---- members along paths ------------------------------------------------

    def bim_frame(a):
        prof, row = library_profile(a.get("profile", ""))
        base = path_obj(a, "Frame path")
        o = headless_call(Arch.makeFrame, base, prof)
        if o is None:
            raise ToolError("failed", "FreeCAD made no frame.")
        if a.get("rotate"):
            setsome(o, {"Rotation": float(a["rotate"])})
        if a.get("align") is not None:
            setsome(o, {"Align": bool(a["align"])})
        settype(o, a.get("type") or "Beam")
        o.Label = s.unique(a.get("name") or f"{row[2]} frame", o)
        out = finish(o, "bim_frame", {"profile": row[2], "pathLength_mm": round(sum(e.Length for e in base.Shape.Edges), 1)})
        if o.Shape.Solids:
            dens = 500 if str(row[1]).upper() in ("TIMBER", "WOOD") else 7850
            out["mass_kg"] = round(o.Shape.Volume / 1e9 * dens, 1)
        return out

    def bim_truss(a):
        p0, p1 = vec(a.get("from"), "from"), vec(a.get("to"), "to")
        if (p1 - p0).Length < 1:
            raise bad("from and to: the truss's two ends, apart.")
        line = doc().addObject("Part::Feature", "TrussLine")
        line.Shape = Part.makeLine(p0, p1)
        line.Visibility = False
        mk = getattr(Arch, "makeTruss", None)
        if mk is None:
            raise ToolError("unavailable", "This FreeCAD's BIM has no truss (FreeCAD 1.0 or newer).")
        o = headless_call(mk, line)
        h = a.get("height", 1000)
        strut = float(a.get("strut", 100))
        done, skipped = setsome(o, {"HeightStart": a.get("heightStart", h), "HeightEnd": a.get("heightEnd", h),
                                    "StrutHeight": strut, "StrutWidth": strut,
                                    "RodSize": float(a.get("rod", 20)), "RodSections": a.get("panels")})
        if a.get("pattern"):
            modes = o.getEnumerationsOfProperty("RodMode") if hasattr(o, "RodMode") else []
            want = {"pratt": "/|/|/", "warren": "/\\/\\/\\", "howe": "/|\\|/|\\"}[a["pattern"]]
            pick = next((m for m in modes if m.replace(" ", "") == want), None)
            if pick is None and modes:
                pick = modes[min(["pratt", "warren", "howe"].index(a["pattern"]), len(modes) - 1)]
            if pick:
                o.RodMode = pick
                done["RodMode"] = pick
        settype(o, "Beam")
        o.Label = s.unique(a.get("name") or "Truss", o)
        out = finish(o, "bim_truss", {"span_mm": round((p1 - p0).Length, 1), "set": done})
        if skipped:
            out["skipped"] = skipped
        if o.Shape.Solids:
            out["mass_kg"] = round(o.Shape.Volume / 1e9 * float(a.get("density", 7850)), 1)
        return out

    def bim_fence(a):
        mk = getattr(Arch, "makeFence", None)
        if mk is None:
            raise ToolError("unavailable", "This FreeCAD's BIM has no fence.")
        base = path_obj(a, "Fence path")
        h = float(a.get("height", 1200))
        post = float(a.get("post", 100))
        gap = float(a.get("spacing", 2000))
        th = float(a.get("panel", 20))
        po = doc().addObject("Part::Box", "FencePost")
        po.Length, po.Width, po.Height = post, post, h
        po.Placement.Base = V(-post / 2, -post / 2, 0)
        sec = doc().addObject("Part::Box", "FencePanel")
        sec.Length, sec.Width, sec.Height = gap - post, th, h * 0.8
        sec.Placement.Base = V(0, -th / 2, h * 0.1)
        po.Visibility = sec.Visibility = False
        o = headless_call(mk, sec, po, base)
        settype(o, "Railing")
        o.Label = s.unique(a.get("name") or "Fence", o)
        out = finish(o, "bim_fence")
        out.update({k: int(getattr(o, k)) for k in ("NumberOfPosts", "NumberOfSections") if hasattr(o, k)})
        return out

    def bim_covering(a):
        label, sub, face = picks.resolve(s, a.get("face"))
        if not sub or not sub.startswith("Face"):
            raise bad("face: \"Slab.Face6\" — the face to cover.")
        target = picks.find(doc(), label)
        finish_kind = a.get("finish", "tiles")
        th = float(a.get("thickness", 10))
        mk = getattr(Arch, "makeCovering", None)
        if mk is not None:
            o = headless_call(mk, (target, [sub]))
            mode = {"tiles": "Solid Tiles", "solid": "Monolithic", "pattern": "Parametric Pattern", "hatch": "Hatch Pattern"}.get(finish_kind)
            done, skipped = setsome(o, {"FinishMode": mode, "TileLength": a.get("tile", [None, None])[0] if a.get("tile") else None,
                                        "TileWidth": a.get("tile", [None, None])[1] if a.get("tile") else None,
                                        "TileThickness": th, "JointWidth": a.get("joint")})
            o.Label = s.unique(a.get("name") or "Covering", o)
            return finish(o, "bim_covering", {"set": done, **({"skipped": skipped} if skipped else {})})
        # FreeCAD 1.0 has no covering object: the tiles (or the slab of finish) built here as a covering component
        if face.findPlane() is None:
            raise bad("face: a flat face.")
        u0, u1, v0, v1 = face.ParameterRange
        n = face.normalAt((u0 + u1) / 2, (v0 + v1) / 2)
        slab = face.extrude(n * th)
        note = None
        if finish_kind == "tiles":
            tl, tw = [float(x) for x in (a.get("tile") or [300, 300])]
            j = float(a.get("joint", 3))
            u = face.Edges[0].tangentAt(face.Edges[0].FirstParameter) if face.Edges else V(1, 0, 0)
            u = (u - n * u.dot(n))
            u.normalize()
            v = n.cross(u)
            bb = face.BoundBox
            span = bb.DiagonalLength
            c = face.CenterOfMass
            tiles = []
            k = int(span / (tl + j)) + 2
            m = int(span / (tw + j)) + 2
            if k * m > 20000:
                raise bad(f"tiles: {k * m} would be laid — bigger tiles, or finish solid.")
            full = tl * tw * th
            whole = cut = 0
            for i in range(-k, k):
                for jj in range(-m, m):
                    o0 = c + u * (i * (tl + j)) + v * (jj * (tw + j))
                    b = Part.makeBox(tl, tw, th)
                    b.Placement = FreeCAD.Placement(o0, FreeCAD.Rotation(u, v, n, "ZXY"))
                    piece = b.common(slab)
                    if piece.isNull() or piece.Volume < 1e-6:
                        continue
                    tiles.append(piece)
                    if piece.Volume > full * 0.999:
                        whole += 1
                    else:
                        cut += 1
            shape = Part.makeCompound(tiles)
            note = {"tiles": len(tiles), "whole": whole, "cut": cut, "tile": [tl, tw], "joint": j}
        else:
            shape = slab
        base = doc().addObject("Part::Feature", "CoveringShape")
        base.Shape = shape
        base.Visibility = False
        o = headless_call(Arch.makeComponent, base) if getattr(Arch, "makeComponent", None) else base
        settype(o, "Covering")
        o.Label = s.unique(a.get("name") or "Covering", o)
        out = finish(o, "bim_covering", {"on": f"{label}.{sub}", "area_m2": round(face.Area / 1e6, 3), "thickness": th, "made": "here (this FreeCAD has no covering object)"})
        if note:
            out.update(note)
        return out

    # ---- layers ---------------------------------------------------------------

    def bim_layers(a):
        layers = a.get("layers")
        if not (isinstance(layers, list) and layers and all(isinstance(x, dict) and x.get("thickness") for x in layers)):
            raise bad("layers: [{ material, thickness mm, density kg/m³ (optional), name }] outside to inside.")
        mk = getattr(Arch, "makeMultiMaterial", None)
        if mk is None:
            raise ToolError("unavailable", "This FreeCAD's BIM has no multi-materials.")
        mm = headless_call(mk)
        mm.Label = s.unique(a.get("name") or "Layers", mm)
        mats = [arch_material(str(x.get("material", f"Layer {i + 1}")), x.get("density")) for i, x in enumerate(layers)]
        mm.Materials = mats
        mm.Names = [str(x.get("name") or x.get("material", f"Layer {i + 1}")) for i, x in enumerate(layers)]
        mm.Thicknesses = [float(x["thickness"]) for x in layers]
        total = sum(float(x["thickness"]) for x in layers)
        on = []
        for r in a.get("objects") or []:
            o = picks.find(doc(), r)
            if not hasattr(o, "Material"):
                continue
            o.Material = mm
            if hasattr(o, "Width") and o in [w for w in doc().Objects if ifc_type(w) == "Wall"]:
                o.Width = total
            on.append(o.Label)
        doc().recompute()
        s.snapshot("bim_layers")
        return {"layers": mm.Label, "total_mm": round(total, 1), "build-up": [{"material": m.Label, "thickness": float(x["thickness"])} for m, x in zip(mats, layers)],
                "on": on, "note": "Walls on it take its total as their width; IFC export writes the layer set.", "step": len(s.steps)}

    # ---- IFC data ---------------------------------------------------------------

    def bim_ifc(a):
        objs = [picks.find(doc(), r) for r in (a.get("objects") or [])]
        if not objs:
            raise bad("objects: the BIM objects to read or tag.")
        out = []
        for o in objs:
            if not is_bim(o):
                raise bad(f"{o.Label} is not a BIM object (no IFC type).")
            if a.get("type"):
                try:
                    o.IfcType = a["type"]
                except Exception:
                    raise bad(f"type {a['type']!r}: not an IFC type FreeCAD knows — e.g. Wall, Slab, Beam, Column, Covering, Railing, Building Element Proxy.")
            if a.get("classification") is not None:
                for k in ("StandardCode", "Classification"):
                    if hasattr(o, k):
                        setattr(o, k, str(a["classification"]))
            props = a.get("properties") or {}
            if props:
                if not hasattr(o, "IfcProperties"):
                    raise ToolError("unavailable", f"{o.Label} carries no IFC properties here.")
                cur = dict(o.IfcProperties or {})
                for full, v in props.items():
                    if "." not in full:
                        raise bad(f"{full}: \"Pset_WallCommon.FireRating\" — the property set, a dot, the property.")
                    pset, name = full.split(".", 1)
                    t = "IfcBoolean" if isinstance(v, bool) else "IfcReal" if isinstance(v, float) else "IfcInteger" if isinstance(v, int) else "IfcLabel"
                    cur[name] = f"{pset};;{t};;{v}"
                o.IfcProperties = cur
            row = {"object": o.Label, "ifc": ifc_type(o)}
            for k in ("StandardCode", "Classification"):
                if getattr(o, k, ""):
                    row["classification"] = getattr(o, k)
            ps = {}
            for name, val in (getattr(o, "IfcProperties", {}) or {}).items():
                parts = str(val).split(";;")
                if len(parts) >= 3:
                    ps.setdefault(parts[0], {})[name] = parts[2]
            if ps:
                row["properties"] = ps
            out.append(row)
        doc().recompute()
        if a.get("type") or a.get("properties") or a.get("classification") is not None:
            s.snapshot("bim_ifc")
        return {"objects": out}

    def bim_quantities(a):
        by = a.get("by", "type")
        groups = {}
        rows = []
        for o in doc().Objects:
            if not is_bim(o) or not isinstance(getattr(o, "Shape", None), Part.Shape) or o.Shape.isNull():
                continue
            if getattr(o, "Group", None) and not o.Shape.Solids:
                continue  # a container
            sh = o.Shape
            vol = sh.Volume / 1e9 if sh.Solids else 0.0
            if vol <= 0 and ifc_type(o) not in ("Space",):
                continue
            mat = getattr(o, "Material", None)
            dens = density_of(o)
            row = {"object": o.Label, "ifc": ifc_type(o), "material": mat.Label if mat is not None else None, "volume_m3": round(vol, 4)}
            for k, key in (("VerticalArea", "verticalArea_m2"), ("HorizontalArea", "horizontalArea_m2"), ("PerimeterLength", "perimeter_m")):
                if hasattr(o, k):
                    v = qty(getattr(o, k))
                    if v:
                        row[key] = round(v / (1e6 if "Area" in k else 1e3), 3)
            if dens:
                row["mass_kg"] = round(vol * dens, 1)
            rows.append(row)
            key = row["ifc"] if by == "type" else (row["material"] or "(no material)") if by == "material" else f"{row['ifc']} / {row['material'] or '-'}"
            g = groups.setdefault(key, {"count": 0, "volume_m3": 0.0, "mass_kg": 0.0, "massKnown": True})
            g["count"] += 1
            g["volume_m3"] += vol
            if dens:
                g["mass_kg"] += vol * dens
            else:
                g["massKnown"] = False
        for g in groups.values():
            g["volume_m3"] = round(g["volume_m3"], 4)
            g["mass_kg"] = round(g["mass_kg"], 1) if g["massKnown"] else None
            del g["massKnown"]
        out = {"by": by, "groups": groups, "objects": len(rows)}
        if a.get("detail"):
            out["rows"] = rows
        known = [r["mass_kg"] for r in rows if r.get("mass_kg")]
        out["totalMass_kg"] = round(sum(known), 1)
        if len(known) < len(rows):
            out["note"] = f"{len(rows) - len(known)} objects have no material density: bim_material or bim_layers gives them one."
        return out

    def bim_preflight(a):
        issues = []
        bims = [o for o in doc().Objects if is_bim(o)]
        if not bims:
            return {"ok": False, "issues": [{"what": "no BIM objects in the document"}]}
        kinds = {ifc_type(o) for o in bims}
        if not ({"Building", "Building Storey", "Site"} & kinds or any(proxy_name(o) in ("_Building", "_Floor", "_Site", "BuildingPart") for o in bims)):
            issues.append({"what": "no site, building or storey: IFC needs a spatial structure (bim_building, bim_level)"})
        seen = {}
        for o in bims:
            t = ifc_type(o)
            pn = proxy_name(o)
            if pn in ("_Building", "_Floor", "_Site", "BuildingPart") or t in ("Building", "Building Storey", "Site", "Project"):
                continue
            if pn in ("_ArchMaterial", "_ArchMultiMaterial") or o.isDerivedFrom("Spreadsheet::Sheet"):
                continue
            row = []
            if t in ("", "Undefined", "Building Element Proxy"):
                row.append("no IFC type (exported as a proxy): bim_ifc { type }")
            sh = getattr(o, "Shape", None)
            if isinstance(sh, Part.Shape):
                if sh.isNull():
                    row.append("no shape")
                elif t not in ("Space", "Grid", "Axis", "Section Plane", "Annotation") and pn not in ("_Space", "_Axis", "_SectionPlane", "_Grid"):
                    if not sh.Solids:
                        row.append("not a solid (exported as a surface model or skipped)")
                    elif not sh.isValid():
                        row.append("invalid shape")
            hosts = [p for p in o.InList if is_bim(p) and o in (list(getattr(p, "Subtractions", []) or []) + list(getattr(p, "Additions", []) or []))]
            if not contained(o) and not hosts and pn != "_Window" and t not in ("Window", "Door"):
                row.append("in no storey or building (bim_level { objects })")
            if pn == "_Window" and not getattr(o, "Hosts", None):
                row.append("a window or door in no wall")
            if hasattr(o, "Material") and o.Material is None and t not in ("Space",) and pn not in ("_Space", "_Window"):
                row.append("no material")
            if o.Label in seen:
                row.append(f"label also on {seen[o.Label]}")
            seen[o.Label] = o.Name
            if row:
                issues.append({"object": o.Label, "ifc": t, "issues": row})
        sev = sum(1 for i in issues if any(w in " ".join(i.get("issues", [i.get("what", "")])) for w in ("no shape", "invalid", "no site")))
        return {"ok": not issues, "objects": len(bims), "issues": issues[:60], "blocking": sev, **({"more": len(issues) - 60} if len(issues) > 60 else {}),
                "note": "Blocking: no shape, invalid shapes, no spatial structure. The rest exports, but loses meaning in other BIM tools."}

    # ---- edits --------------------------------------------------------------------

    def bim_edit(a):
        op = a.get("op")
        host = picks.find(doc(), a.get("object"), "object") if a.get("object") else None
        if op in ("add", "subtract"):
            if host is None or not hasattr(host, "Additions"):
                raise bad("object: the wall, slab or structure to change.")
            others = [picks.find(doc(), r) for r in (a.get("with") or [])]
            if not others:
                raise bad("with: [objects] to join to it (add) or cut from it (subtract).")
            prop = "Additions" if op == "add" else "Subtractions"
            setattr(host, prop, list(getattr(host, prop)) + others)
            for x in others:
                x.Visibility = False
            return finish(host, "bim_edit", {"op": op, "with": [x.Label for x in others]})
        if op == "cut":
            if host is None or not hasattr(host, "Subtractions"):
                raise bad("object: the component to cut.")
            n = picks.direction(a.get("normal", [0, 0, 1]), "normal")
            at = picks.vec3(a.get("at", [0, 0, 0]), "at")
            bb = host.Shape.BoundBox
            big = max(bb.DiagonalLength * 3, 1000)
            box = Part.makeBox(big, big, big, V(-big / 2, -big / 2, 0))
            box.Placement = FreeCAD.Placement(at, FreeCAD.Rotation(V(0, 0, 1), n))
            cutter = doc().addObject("Part::Feature", "CutVolume")
            cutter.Shape = box
            cutter.Visibility = False
            cutter.Label = s.unique(f"{host.Label} cut")
            before = host.Shape.Volume
            host.Subtractions = list(host.Subtractions) + [cutter]
            doc().recompute()
            out = finish(host, "bim_edit", {"op": "cut", "removed_m3": round((before - host.Shape.Volume) / 1e9, 4)})
            if host.Shape.Volume >= before - 1e-6:
                out["warning"] = "nothing was cut: the plane misses it, or the normal points away (it removes the side the normal points to)."
            return out
        if op == "join":
            ws = [picks.find(doc(), r) for r in (a.get("walls") or [])]
            if len(ws) < 2:
                raise bad("walls: two or more walls to join into one.")
            res = headless_call(Arch.joinWalls, ws, delete=bool(a.get("delete", True)))
            if res is None:
                raise ToolError("failed", "FreeCAD could not join them: same width, height and alignment, and ends that meet.")
            return finish(res, "bim_edit", {"op": "join", "joined": len(ws)})
        if op == "clone":
            if host is None:
                raise bad("object: the component to clone.")
            mk = getattr(Arch, "cloneComponent", None)
            c = headless_call(mk, host) if mk else None
            if c is None:
                import Draft
                c = headless_call(Draft.make_clone, host)
            settype(c, ifc_type(host))
            if a.get("move"):
                c.Placement.Base = c.Placement.Base + picks.vec3(a["move"], "move")
            c.Label = s.unique(a.get("name") or f"{host.Label} clone", c)
            return finish(c, "bim_edit", {"op": "clone", "of": host.Label})
        raise bad("op: add | subtract (with) | cut (at, normal: the side it points to goes) | join (walls) | clone (move).")

    pt = {"type": "array", "items": {"type": "number"}}
    s_ = {"type": "string"}
    return [
        Tool("bim_frame", "A library section (bim_profiles: HEB200, IPE300, RHS … ) swept along a path — a wire, sketch or points: portal frames, purlins along a roof, handrails, steel along a curve. Answers the path length, volume and mass.",
             obj({"profile": s_, "path": s_, "points": {"type": "array", "items": pt}, "rotate": {"type": "number"}, "align": {"type": "boolean"}, "name": s_}, ["profile"]), bim_frame, changes=True),
        Tool("bim_truss", "A truss from one end to the other (Arch truss): height (heightStart, heightEnd for a pitched one), strut size mm, rod size mm, panels, pattern warren | pratt | howe. Answers span, volume, mass.",
             obj({"from": pt, "to": pt, "height": {"type": "number"}, "heightStart": {"type": "number"}, "heightEnd": {"type": "number"}, "strut": {"type": "number"},
                  "rod": {"type": "number"}, "panels": {"type": "integer"}, "pattern": {"type": "string", "enum": ["warren", "pratt", "howe"]}, "density": {"type": "number"}, "name": s_}, ["from", "to"]), bim_truss, changes=True),
        Tool("bim_fence", "A fence along a path (or points): height, post size, spacing between posts, panel thickness (mm). Answers the posts and sections.",
             obj({"path": s_, "points": {"type": "array", "items": pt}, "height": {"type": "number"}, "post": {"type": "number"}, "spacing": {"type": "number"}, "panel": {"type": "number"}, "name": s_}), bim_fence, changes=True),
        Tool("bim_covering", "Cover a face (a floor, a wall face) with a finish: finish tiles (tile [length, width], joint mm) | solid | pattern, thickness mm.",
             obj({"face": s_, "finish": {"type": "string", "enum": ["tiles", "solid", "pattern", "hatch"]}, "tile": pt, "thickness": {"type": "number"}, "joint": {"type": "number"}, "name": s_}, ["face"]), bim_covering, changes=True),
        Tool("bim_layers", "A layered build-up (multi-material): layers [{ material, thickness mm, density kg/m³ }] outside to inside, put on objects (a wall takes the total as its width). For wall and slab assemblies, U-value and fire checks downstream.",
             obj({"layers": {"type": "array", "items": {"type": "object"}}, "objects": {"type": "array", "items": s_}, "name": s_}, ["layers"]), bim_layers, changes=True),
        Tool("bim_ifc", "Read or set objects' IFC data: type (IFC class), classification (Uniclass / OmniClass / your code), properties { \"Pset_WallCommon.FireRating\": \"REI60\", \"Pset_WallCommon.IsExternal\": true } — answers each object's type, classification and property sets.",
             obj({"objects": {"type": "array", "items": s_}, "type": s_, "classification": s_, "properties": {"type": "object"}}, ["objects"]), bim_ifc, changes=True),
        Tool("bim_quantities", "Quantity take-off of the BIM model: by type | material | both — counts, volume m³ and mass kg (from material densities) per group; detail: true lists every object with its areas and perimeter. Changes nothing.",
             obj({"by": {"type": "string", "enum": ["type", "material", "both"]}, "detail": {"type": "boolean"}}), bim_quantities),
        Tool("bim_preflight", "Check the model before IFC export (as BIM's preflight does): spatial structure, IFC types, solids, containment in a storey, hosted windows, materials, duplicate labels. Changes nothing.",
             obj({}), bim_preflight),
        Tool("bim_edit", "Change BIM components: op add (with: join objects to it) | subtract (with: cut them out) | cut (at, normal: the side the normal points to is removed — a wall under a roof pitch) | join (walls: into one) | clone (move [x,y,z]).",
             obj({"op": {"type": "string", "enum": ["add", "subtract", "cut", "join", "clone"]}, "object": s_, "with": {"type": "array", "items": s_}, "walls": {"type": "array", "items": s_},
                  "at": pt, "normal": pt, "move": pt, "delete": {"type": "boolean"}, "name": s_}, ["op"]), bim_edit, changes=True),
    ]
