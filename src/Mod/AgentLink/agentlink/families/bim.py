# SPDX-License-Identifier: LGPL-2.1-or-later
# FreeCAD's BIM (Arch) workbench headless: walls along points or a sketch,
# windows and doors set into a wall (an opening cut through it), columns,
# beams and slabs, roofs, stairs, spaces with their floor area, levels in a
# building on a site, a schedule of quantities (into a sheet when asked), and
# IFC out and in where IfcOpenShell is installed.

import os

import FreeCAD
import Part

import Arch  # FreeCAD's BIM workbench; the family is left out without it

from ..protocol import Tool, ToolError
from ..tools import obj
from . import picks

GROUP = "bim"
INFO = "BIM (Arch): walls, windows and doors in walls, columns, beams, slabs, roofs, stairs, spaces, levels, building and site, quantities, IFC in and out."

V = FreeCAD.Vector
UP = V(0, 0, 1)
WINDOWS = ("Fixed", "Open 1-pane", "Open 2-pane", "Sash 2-pane", "Sliding 2-pane", "Sliding 4-pane", "Awning")
DOORS = ("Simple door", "Glass door")
ROLES = {"column": "Column", "beam": "Beam", "slab": "Slab", "footing": "Footing"}


def bad(msg):
    return ToolError("invalid_input", msg)


def vec(p, what):
    if not (isinstance(p, (list, tuple)) and len(p) in (2, 3) and all(isinstance(c, (int, float)) for c in p)):
        raise bad(f"{what}: [x, y] or [x, y, z] in mm.")
    return V(float(p[0]), float(p[1]), float(p[2]) if len(p) == 3 else 0.0)


def pos(a, k, default):
    x = picks.num(a.get(k, default), k)
    if x <= 0:
        raise bad(f"{k}: above 0 mm.")
    return x


def frame(x, y):
    """A rotation taking local X, Y to x, y (Z their cross product)."""
    x, y = V(x).normalize(), V(y).normalize()
    z = x.cross(y)
    m = FreeCAD.Matrix(x.x, y.x, z.x, 0, x.y, y.y, z.y, 0, x.z, y.z, z.z, 0, 0, 0, 0, 1)
    return FreeCAD.Rotation(m)


def headless_call(fn, *args, **kw):
    """An Arch call that reaches for its dialogs when FreeCAD says a window is up
    (Agent Link's headless renderer loads FreeCADGui): called again as console
    mode would; a failure says where it came from."""
    import traceback

    try:
        return fn(*args, **kw)
    except Exception as e:
        if "Gui" not in str(e):
            where = traceback.extract_tb(e.__traceback__)[-1]
            raise ToolError("failed", f"{e} ({os.path.basename(where.filename)}:{where.lineno})") from None
        first = e
    up = FreeCAD.GuiUp
    try:
        FreeCAD.GuiUp = False
        return fn(*args, **kw)
    except Exception as e:
        where = traceback.extract_tb(e.__traceback__)[-1]
        w0 = traceback.extract_tb(first.__traceback__)[-1]
        raise ToolError("failed", f"{e} ({os.path.basename(where.filename)}:{where.lineno}); first {first} ({os.path.basename(w0.filename)}:{w0.lineno})") from None
    finally:
        FreeCAD.GuiUp = up


def is_bim(o):
    return hasattr(o, "IfcType") and not o.isDerivedFrom("Spreadsheet::Sheet")


def ifc_type(o):
    return str(getattr(o, "IfcType", "") or getattr(o, "IfcRole", "") or o.TypeId)


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
            out["min"] = [round(bb.XMin, 1), round(bb.YMin, 1), round(bb.ZMin, 1)]
            if sh.Solids:
                out["volume_m3"] = round(sh.Volume / 1e9, 4)
        elif not getattr(o, "Group", None):
            out["warning"] = "no shape came out — check its base and sizes."
        st = o.getStatusString() if hasattr(o, "getStatusString") else ""
        if st and st not in ("Valid", "Touched"):
            out["status"] = st
        s.snapshot(tool)
        return {**out, **(extra or {}), "step": len(s.steps)}

    def named(o, label):
        o.Label = s.unique(label, o)
        return o

    def baseline(points, label, closed=False):
        if not (isinstance(points, list) and len(points) >= 2):
            raise bad("points: two or more [x, y(, z)] in mm.")
        P = [vec(p, "points") for p in points]
        if closed and (P[0] - P[-1]).Length > 1e-6:
            P.append(P[0])
        w = Part.makePolygon(P)
        o = doc().addObject("Part::Feature", "Baseline")
        o.Shape = w
        o.Label = s.unique(label)
        return o

    def walls():
        return [o for o in doc().Objects if ifc_type(o) == "Wall" or (hasattr(o, "Proxy") and type(getattr(o, "Proxy", None)).__name__ == "_Wall")]

    # ---- walls and openings ----------------------------------------------------

    def bim_wall(a):
        width, height = pos(a, "width", 200), pos(a, "height", 3000)
        align = {"center": "Center", "left": "Left", "right": "Right"}.get(a.get("align", "center"))
        if align is None:
            raise bad("align: center (the line runs down the middle), left or right (the wall to that side of the line).")
        name = a.get("name") or "Wall"
        if a.get("base"):
            base = picks.find(doc(), a["base"])
            if not getattr(base, "Shape", None) or not base.Shape.Edges:
                raise bad(f"base: {base.Label} has no edges to run a wall along.")
        else:
            base = baseline(a.get("points"), f"{name} line", bool(a.get("closed")))
        w = Arch.makeWall(base, width=width, height=height, align=align)
        named(w, name)
        length = sum(e.Length for e in base.Shape.Edges)
        return finish(w, "bim_wall", {"length": round(length, 1), "width": width, "height": height, "align": align.lower(), "base": base.Label})

    def bim_opening(a):
        wall = picks.find(doc(), a.get("wall"), "wall")
        kind = a.get("kind", "window")
        preset = a.get("preset") or ("Simple door" if kind == "door" else "Open 1-pane")
        if preset not in WINDOWS + DOORS:
            raise bad(f"preset: a window — {', '.join(WINDOWS)} — or a door — {', '.join(DOORS)}.")
        width = pos(a, "width", 900 if kind == "door" else 1000)
        height = pos(a, "height", 2100 if kind == "door" else 1200)
        sill = picks.num(a.get("sill", 0 if kind == "door" else 900), "sill")
        at = picks.num(a.get("at", 0), "at")
        base = getattr(wall, "Base", None)
        edges = base.Shape.Edges if base is not None and getattr(base, "Shape", None) else []
        if not edges:
            raise bad(f"wall: {wall.Label} has no baseline to place the opening along.")
        # Which straight run of the wall: the one holding distance at.
        run, left = None, at
        for e in edges:
            if left <= e.Length + 1e-6:
                run = e
                break
            left -= e.Length
        if run is None:
            total = sum(e.Length for e in edges)
            raise bad(f"at: the opening's left edge, 0 … {total - width:.0f} mm along {wall.Label}.")
        p0, p1 = run.Vertexes[0].Point, run.Vertexes[-1].Point
        d = p1 - p0
        if d.Length < 1e-9:
            raise bad("The wall's run there has no length.")
        if left + width > run.Length + 1e-6:
            raise bad(f"The opening runs past the end of that stretch of wall ({run.Length:.0f} mm long, the opening {left:.0f} … {left + width:.0f}).")
        d.normalize()
        # Across the wall: where its middle sits off the line (align), and how thick it is.
        n = UP.cross(d)
        n.normalize()
        W = float(getattr(wall, "Width", 200))
        off = {"Left": W / 2, "Right": -W / 2}.get(str(getattr(wall, "Align", "Center")), 0.0)
        pl = FreeCAD.Placement(p0 + d * left + n * off + V(0, 0, sill), frame(d, n))
        fw, depth = float(a.get("frame", 50)), float(a.get("depth", 100))
        door = preset in DOORS
        if 2 * fw >= min(width, height):
            raise bad("frame: narrower than half the opening.")
        # The opening, cut out of the wall (the wall's Subtractions), a little deeper than it.
        hole = doc().addObject("Part::Feature", "Opening")
        hole.Label = s.unique(f"{a.get('name') or ('Door' if door else 'Window')} opening")
        hole.Shape = Part.makeBox(width, W + 20, height, V(0, -(W + 20) / 2, 0))
        hole.Placement = pl
        wall.Subtractions = list(wall.Subtractions) + [hole]
        # The window itself: a frame round a pane (a door's panel), as an Arch component typed Window or Door.
        outer = Part.makeBox(width, depth, height, V(0, -depth / 2, 0))
        inner = Part.makeBox(width - 2 * fw, depth + 2, height - (fw if door else 2 * fw), V(fw, -depth / 2 - 1, 0 if door else fw))
        pane_t = 40.0 if door else 10.0
        pane = Part.makeBox(width - 2 * fw, pane_t, height - (fw if door else 2 * fw), V(fw, -pane_t / 2, 0 if door else fw))
        shape = Part.makeCompound([outer.cut(inner), pane])
        base = doc().addObject("Part::Feature", "WindowShape")
        base.Label = s.unique(f"{a.get('name') or ('Door' if door else 'Window')} shape")
        base.Shape = shape
        base.Placement = pl
        mk = getattr(Arch, "makeComponent", None)
        win = headless_call(mk, base) if mk else None
        if win is None:
            win = base
            win.addProperty("App::PropertyString", "IfcType", "IFC", "IFC type")
        named(win, a.get("name") or ("Door" if door else "Window"))
        try:
            win.IfcType = "Door" if door else "Window"
        except Exception:
            pass
        for k, v in (("Width", width), ("Height", height)):
            if not hasattr(win, k):
                win.addProperty("App::PropertyLength", k, "Window", "")
            setattr(win, k, v)
        if not hasattr(win, "Hosts"):
            win.addProperty("App::PropertyLinkList", "Hosts", "Window", "The wall it is set into")
        win.Hosts = [wall]
        built = f"{'door' if door else 'window'}: a {fw:g} mm frame round a {'panel' if door else 'pane'} (FreeCAD's window presets need its window)"
        before = wall.Shape.Volume if wall.Shape.Solids else 0
        doc().recompute()
        after = wall.Shape.Volume if wall.Shape.Solids else 0
        out = finish(win, "bim_opening", {"wall": wall.Label, "preset": built, "width": width, "height": height, "sill": sill, "at": at})
        out["wallCut_m3"] = round((before - after) / 1e9, 4)
        out["opening"] = hole.Label
        if before - after <= 0:
            out["warning"] = "The wall was not cut — the opening may be outside it."
        return out

    # ---- structure -------------------------------------------------------------

    def bim_structure(a):
        kind = a.get("kind", "column")
        if kind not in ROLES:
            raise bad(f"kind: {', '.join(ROLES)}.")
        name = a.get("name") or kind.capitalize()
        if kind in ("slab", "footing"):
            t = pos(a, "thickness", 200 if kind == "slab" else 500)
            if a.get("base"):
                base = picks.find(doc(), a["base"])
            else:
                pts = a.get("points")
                if not pts:
                    raise bad("points: the outline, [x, y(, z)] each, closed for you — or base, a closed sketch or wire.")
                base = baseline(pts, f"{name} outline", closed=True)
                base.Shape = Part.Face(base.Shape)
            o = Arch.makeStructure(base, height=t)
            if kind == "footing":
                o.Placement.Base = o.Placement.Base - V(0, 0, t)
        elif kind == "column":
            w, dp, h = pos(a, "width", 300), pos(a, "depth", 300), pos(a, "height", 3000)
            o = Arch.makeStructure(None, length=w, width=dp, height=h)
            o.Placement = FreeCAD.Placement(vec(a.get("at", [0, 0, 0]), "at"), FreeCAD.Rotation())
        else:
            p0, p1 = vec(a.get("from"), "from"), vec(a.get("to"), "to")
            d = p1 - p0
            if d.Length < 1e-6:
                raise bad("from and to: the beam's two ends, apart.")
            w, h = pos(a, "width", 200), pos(a, "depth", 400)
            o = Arch.makeStructure(None, length=w, width=h, height=d.Length)
            dn = V(d).normalize()
            side = UP.cross(dn)
            if side.Length < 1e-9:
                raise bad("A beam runs across, not straight up: kind column for that.")
            side.normalize()
            # The section across (X sideways, Y up), extruded along its local Z: the beam's run.
            o.Placement = FreeCAD.Placement(p0, frame(side, dn.cross(side)))
        named(o, name)
        if hasattr(o, "IfcType"):
            try:
                o.IfcType = ROLES[kind]
            except Exception:
                pass
        return finish(o, "bim_structure", {"kind": kind})

    def bim_roof(a):
        name = a.get("name") or "Roof"
        if a.get("base"):
            base = picks.find(doc(), a["base"])
        else:
            base = baseline(a.get("points"), f"{name} outline", closed=True)
        n = len(base.Shape.Edges)
        ang = a.get("angle", 30)
        angles = ang if isinstance(ang, list) else [float(ang)] * n
        if len(angles) != n:
            raise bad(f"angle: one number, or one per edge ({n}); 90 makes a gable.")
        t = pos(a, "thickness", 150)
        over = picks.num(a.get("overhang", 300), "overhang")
        # Each slope's run: half the outline's depth away from its edge — the ridge midway.
        verts = [v.Point for v in base.Shape.Vertexes]
        runs = []
        for e in base.Shape.Edges:
            p0, p1 = e.Vertexes[0].Point, e.Vertexes[-1].Point
            d = V(p1 - p0)
            d.z = 0
            d.normalize()
            far = max(abs((q - p0).x * d.y - (q - p0).y * d.x) for q in verts)
            runs.append(round(far / 2, 3))
        if a.get("run") is not None:
            r = a["run"]
            runs = [float(x) for x in r] if isinstance(r, list) else [float(r)] * n
        o = Arch.makeRoof(base, 0, [float(x) for x in angles], runs, [-1] * n, [t] * n, [over] * n)
        named(o, name)
        return finish(o, "bim_roof", {"edges": n, "angles": angles, "runs": runs})

    def bim_stairs(a):
        length, width, height = pos(a, "length", 4500), pos(a, "width", 1000), pos(a, "height", 3000)
        steps = int(a.get("steps", max(3, round(height / 175))))
        if steps < 2:
            raise bad("steps: 2 or more.")
        o = Arch.makeStairs(None, length=length, width=width, height=height, steps=steps)
        d = vec(a.get("direction", [1, 0]), "direction")
        d.z = 0
        if d.Length < 1e-9:
            raise bad("direction: which way the flight rises, [x, y].")
        d.normalize()
        # Steps along local X, its width along Y, rising up Z.
        o.Placement = FreeCAD.Placement(vec(a.get("at", [0, 0, 0]), "at"), frame(d, UP.cross(d)))
        named(o, a.get("name") or "Stairs")
        riser, going = height / steps, length / max(steps - 1, 1)
        out = finish(o, "bim_stairs", {"steps": steps, "riser": round(riser, 1), "going": round(going, 1), "blondel": round(2 * riser + going, 1)})
        if not 600 <= 2 * riser + going <= 650:
            out["note"] = "2 risers + 1 going is outside 600–650 mm (Blondel): the flight will feel steep or long."
        return out

    def bim_space(a):
        name = a.get("name") or "Space"
        if a.get("base"):
            base = picks.find(doc(), a["base"])
        else:
            base = baseline(a.get("points"), f"{name} outline", closed=True)
            h = pos(a, "height", 2600)
            face = Part.Face(base.Shape)
            base.Shape = face.extrude(V(0, 0, h))
        o = Arch.makeSpace([base])
        named(o, name)
        if a.get("use") and hasattr(o, "SpaceType"):
            try:
                o.SpaceType = a["use"]
            except Exception:
                pass
        doc().recompute()
        area = getattr(o, "Area", None)
        a_m2 = float(area.Value if hasattr(area, "Value") else area or 0) / 1e6
        return finish(o, "bim_space", {"area_m2": round(a_m2, 3)})

    # ---- levels, building, site ---------------------------------------------------

    def bim_level(a):
        objs = [picks.find(doc(), r) for r in (a.get("objects") or [])]
        o = Arch.makeFloor(objs)
        named(o, a.get("name") or "Level")
        z = picks.num(a.get("elevation", 0), "elevation")
        o.Placement.Base = V(0, 0, z)
        if hasattr(o, "Height") and a.get("height"):
            o.Height = float(a["height"])
        return finish(o, "bim_level", {"elevation": z, "holds": [x.Label for x in objs]})

    def bim_building(a):
        objs = [picks.find(doc(), r) for r in (a.get("levels") or [])]
        b = Arch.makeBuilding(objs)
        named(b, a.get("name") or "Building")
        out = finish(b, "bim_building", {"levels": [x.Label for x in objs]})
        if a.get("site", True):
            site = Arch.makeSite([b])
            named(site, a.get("siteName") or "Site")
            doc().recompute()
            out["site"] = site.Label
        return out

    # ---- quantities ----------------------------------------------------------------

    def quantities():
        rows = []
        for o in doc().Objects:
            t = ifc_type(o)
            sh = getattr(o, "Shape", None)
            if not isinstance(sh, Part.Shape) or sh.isNull():
                continue
            vol = sh.Volume / 1e9 if sh.Solids else 0.0
            row = None
            if o in walls():
                base = getattr(o, "Base", None)
                length = sum(e.Length for e in base.Shape.Edges) / 1000 if base is not None and getattr(base, "Shape", None) else 0
                h = float(getattr(o, "Height", 0)) / 1000
                w = float(getattr(o, "Width", 0)) / 1000
                row = {"kind": "wall", "length_m": round(length, 3), "height_m": round(h, 3), "width_m": round(w, 3),
                       "area_m2": round(vol / w, 3) if w else 0, "volume_m3": round(vol, 4)}
            elif t in ("Window", "Door") or type(getattr(o, "Proxy", None)).__name__ == "_Window":
                w, h = float(getattr(o, "Width", 0)), float(getattr(o, "Height", 0))
                row = {"kind": "door" if t == "Door" else "window", "width_m": round(w / 1000, 3), "height_m": round(h / 1000, 3), "area_m2": round(w * h / 1e6, 3)}
            elif t in ("Column", "Beam", "Slab", "Footing"):
                row = {"kind": t.lower(), "volume_m3": round(vol, 4)}
                if t in ("Slab", "Footing"):
                    th = float(getattr(o, "Height", 0) or 0)
                    row["area_m2"] = round(vol * 1000 / th, 3) if th else 0
            elif t == "Space" or type(getattr(o, "Proxy", None)).__name__ == "_Space":
                ar = getattr(o, "Area", 0)
                row = {"kind": "space", "area_m2": round(float(getattr(ar, "Value", ar) or 0) / 1e6, 3), "use": str(getattr(o, "SpaceType", ""))}
            elif t in ("Reinforcing Bar", "Rebar") or type(getattr(o, "Proxy", None)).__name__ == "_Rebar":
                row = {"kind": "rebar", "volume_m3": round(vol, 5), "mass_kg": round(vol * 7850, 1)}
            elif t in ("Roof", "Stair"):
                row = {"kind": t.lower(), "volume_m3": round(vol, 4)}
            if row:
                rows.append({"object": o.Label, **row})
        return rows

    def bim_schedule(a):
        rows = quantities()
        totals = {}
        for r in rows:
            t = totals.setdefault(r["kind"], {"count": 0})
            t["count"] += 1
            for k in ("length_m", "area_m2", "volume_m3", "mass_kg"):
                if k in r:
                    t[k] = round(t.get(k, 0) + r[k], 4)
        out = {"rows": rows, "totals": totals}
        if a.get("sheet"):
            sh = doc().addObject("Spreadsheet::Sheet", "Schedule")
            sh.Label = s.unique(a.get("sheet") if isinstance(a.get("sheet"), str) else "Schedule")
            cols = ["object", "kind", "length_m", "height_m", "width_m", "area_m2", "volume_m3", "use"]
            for j, c in enumerate(cols):
                sh.set(f"{chr(65 + j)}1", c)
            for i, r in enumerate(rows, start=2):
                for j, c in enumerate(cols):
                    if c in r and r[c] != "":
                        v = r[c]
                        sh.set(f"{chr(65 + j)}{i}", str(v) if isinstance(v, (int, float)) else f"'{v}")
            doc().recompute()
            s.snapshot("bim_schedule")
            out["sheet"] = sh.Label
            out["step"] = len(s.steps)
        return out

    # ---- IFC -------------------------------------------------------------------------

    def need_ifc():
        try:
            import ifcopenshell  # noqa: F401
        except ImportError:
            raise ToolError("unavailable", "IFC needs IfcOpenShell, which this FreeCAD does not have: the person installs it (the BIM workbench's IFC setup, or pip install ifcopenshell into FreeCAD's Python).")

    def ifc_module(name):
        for m in (f"importers.{name}", name):
            try:
                return __import__(m, fromlist=["x"])
            except ImportError:
                continue
        raise ToolError("unavailable", f"FreeCAD's {name} is not found in this build.")

    def where(p, default):
        p = p or default
        return p if os.path.isabs(p) else os.path.join(s.out_dir, p)

    def bim_export(a):
        need_ifc()
        names = a.get("objects")
        if names:
            objs = [picks.find(doc(), n) for n in names]
        else:
            tops = [o for o in doc().Objects if ifc_type(o) in ("Site", "Building") or type(getattr(o, "Proxy", None)).__name__ in ("_Site", "_Building")]
            objs = tops[:1] or [o for o in doc().Objects if is_bim(o) and not any(is_bim(p) for p in o.InList)]
            if not objs:
                raise bad("Nothing to write: no BIM objects here (bim_wall …), or objects to name.")
        p = where(a.get("path"), (doc().Label or "model") + ".ifc")
        if not p.lower().endswith(".ifc"):
            p += ".ifc"
        os.makedirs(os.path.dirname(p), exist_ok=True)
        ex = ifc_module("exportIFC")
        try:
            ex.export(objs, p)
        except Exception as e:
            raise ToolError("failed", f"IFC export failed: {e}")
        import ifcopenshell

        f = ifcopenshell.open(p)
        counts = {k: len(f.by_type(k)) for k in ("IfcWall", "IfcWindow", "IfcDoor", "IfcSlab", "IfcColumn", "IfcBeam", "IfcSpace", "IfcBuildingStorey", "IfcRoof", "IfcStair")}
        return {"path": p, "bytes": os.path.getsize(p), "schema": f.schema, "objects": [o.Label for o in objs], "readBack": {k: v for k, v in counts.items() if v}}

    def ifc_bodies(p):
        import ifcopenshell
        import ifcopenshell.geom

        f = ifcopenshell.open(p)
        st = ifcopenshell.geom.settings()
        for key in ("use-world-coords", getattr(st, "USE_WORLD_COORDS", None)):
            try:
                st.set(key, True)
                break
            except Exception:
                continue
        unit = 1000.0  # IfcOpenShell gives metres
        n, first = 0, None
        for el in f.by_type("IfcProduct"):
            if not getattr(el, "Representation", None) or el.is_a("IfcOpeningElement") or el.is_a("IfcSpatialStructureElement") and not el.is_a("IfcSpace"):
                continue
            try:
                sh_ = ifcopenshell.geom.create_shape(st, el)
                g = getattr(sh_, "geometry", sh_)
                vs, fs = list(g.verts), list(g.faces)
            except Exception as e:
                first = first or f"{el.is_a()}: {e}"
                continue
            if not fs:
                continue
            P = [V(vs[i] * unit, vs[i + 1] * unit, vs[i + 2] * unit) for i in range(0, len(vs), 3)]
            faces = []
            for i in range(0, len(fs), 3):
                a_, b_, c_ = P[fs[i]], P[fs[i + 1]], P[fs[i + 2]]
                if (b_ - a_).cross(c_ - a_).Length > 1e-9:
                    faces.append(Part.Face(Part.makePolygon([a_, b_, c_, a_])))
            if not faces:
                continue
            sh = Part.makeShell(faces)
            try:
                sol = Part.makeSolid(sh)
                sh = sol.removeSplitter() if sol.isValid() else sh
            except Exception:
                pass
            o = doc().addObject("Part::Feature", "Ifc")
            o.Shape = sh
            o.Label = s.unique(el.Name or el.is_a()[3:])
            o.addProperty("App::PropertyString", "IfcType", "IFC", "IFC class")
            o.IfcType = el.is_a()[3:]
            n += 1
        if not n:
            raise ToolError("failed", f"IFC import: no product geometry could be read (IfcOpenShell {getattr(ifcopenshell, 'version', '?')}; {first or 'no products with geometry'}).")

    def bim_import(a):
        need_ifc()
        p = where(a.get("path"), "")
        if not os.path.isfile(p):
            raise ToolError("not_found", f"{p}: no such file.")
        before = {o.Name for o in doc().Objects}
        how = "FreeCAD's IFC importer"
        try:
            im = ifc_module("importIFC")
            im.insert(p, doc().Name)
        except Exception as e:
            # FreeCAD's importer and the IfcOpenShell installed can disagree on
            # settings: each product's geometry read here instead, as a body.
            for o in [o for o in doc().Objects if o.Name not in before]:
                doc().removeObject(o.Name)
            how = f"each product's geometry, read with IfcOpenShell (FreeCAD's importer: {e})"
            ifc_bodies(p)
        doc().recompute()
        made = [o for o in doc().Objects if o.Name not in before]
        kinds = {}
        for o in made:
            kinds[ifc_type(o)] = kinds.get(ifc_type(o), 0) + 1
        s.snapshot("bim_import")
        return {"path": p, "objects": len(made), "byType": kinds, "read": how, "step": len(s.steps)}

    def bim_list(a):
        out = []
        for o in doc().Objects:
            t = ifc_type(o)
            if not is_bim(o):
                continue
            e = {"object": o.Label, "ifc": t}
            if getattr(o, "Group", None):
                e["holds"] = [x.Label for x in o.Group]
            if getattr(o, "Hosts", None):
                e["in"] = [x.Label for x in o.Hosts]
            out.append(e)
        return {"objects": out, "count": len(out)}

    # ---- more building elements ---------------------------------------------------

    def setprops(o, vals):
        """Set those of vals the object has; answer what was set."""
        done = {}
        for k, v in vals.items():
            if v is not None and hasattr(o, k):
                try:
                    setattr(o, k, v)
                    done[k] = v
                except Exception:
                    pass
        return done

    def bim_curtain_wall(a):
        name = a.get("name") or "Curtain wall"
        pts = a.get("points")
        h = pos(a, "height", 3000)
        line = baseline(pts, f"{name} line")
        face = line.Shape.extrude(V(0, 0, h))
        line.Shape = face
        mk = getattr(Arch, "makeCurtainWall", None)
        if mk is None:
            raise ToolError("unavailable", "This FreeCAD's BIM has no curtain wall.")
        o = headless_call(mk, line)
        named(o, name)
        length = sum(e.Length for e in Part.makePolygon([vec(p, "points") for p in pts]).Edges)
        vs, hs = pos(a, "vertical", 1500), pos(a, "horizontal", 1500)
        mw, md = pos(a, "mullion", 50), pos(a, "depth", 100)
        set_ = setprops(o, {"VerticalSections": max(1, round(length / vs)), "HorizontalSections": max(1, round(h / hs)),
                            "VerticalMullionWidth": mw, "VerticalMullionHeight": md, "HorizontalMullionWidth": mw, "HorizontalMullionHeight": md,
                            "PanelThickness": float(a.get("glass", 20))})
        return finish(o, "bim_curtain_wall", {"length": round(length, 1), "height": h, "set": set_})

    def bim_rebar(a):
        host = picks.find(doc(), a.get("structure"), "structure")
        sh = getattr(host, "Shape", None)
        if not isinstance(sh, Part.Shape) or not sh.Solids:
            raise bad(f"structure: {host.Label} has no solid to reinforce.")
        mk = getattr(Arch, "makeRebar", None)
        if mk is None:
            raise ToolError("unavailable", "This FreeCAD's BIM has no rebar.")
        bb = sh.BoundBox
        cover = picks.num(a.get("cover", 30), "cover")
        dia = pos(a, "diameter", 12)
        along = a.get("along", "x")
        layer = a.get("layer", "bottom")
        z = bb.ZMin + cover + dia / 2 if layer == "bottom" else bb.ZMax - cover - dia / 2
        if along == "x":
            p0, p1, spread, width = V(bb.XMin + cover, bb.YMin, z), V(bb.XMax - cover, bb.YMin, z), V(0, 1, 0), bb.YLength
        elif along == "y":
            p0, p1, spread, width = V(bb.XMin, bb.YMin + cover, z), V(bb.XMin, bb.YMax - cover, z), V(1, 0, 0), bb.XLength
        elif along == "z":
            y = bb.YMin + cover + dia / 2
            p0, p1, spread, width = V(bb.XMin, y, bb.ZMin + cover), V(bb.XMin, y, bb.ZMax - cover), V(1, 0, 0), bb.XLength
        else:
            raise bad("along: x | y | z — the bars' own direction.")
        if (p1 - p0).Length <= 0:
            raise bad("cover: more than the structure is long.")
        span = width - 2 * cover - dia
        if span <= 0:
            raise bad("cover: more than the structure is wide.")
        if a.get("spacing"):
            n = int(span // picks.num(a["spacing"], "spacing")) + 1
        else:
            n = int(a.get("amount", 4))
        if n < 1:
            raise bad("amount: 1 or more bars.")
        bar = doc().addObject("Part::Feature", "RebarLine")
        bar.Label = s.unique(f"{a.get('name') or 'Rebar'} line")
        bar.Shape = Part.makePolygon([p0, p1])
        try:
            o = headless_call(mk, host, bar, diameter=dia, amount=n, offset=cover + dia / 2)
            setprops(o, {"Direction": spread})
            how = "FreeCAD's rebar"
        except ToolError:
            # FreeCAD's rebar loads its GUI here: the same bars made as solids,
            # an Arch component typed Reinforcing Bar.
            d = p1 - p0
            first = cover + dia / 2
            step = span / (n - 1) if n > 1 else 0
            bars = [Part.makeCylinder(dia / 2, d.Length, p0 + spread * (first + i * step), d) for i in range(n)]
            bar.Shape = Part.makeCompound(bars)
            mkc = getattr(Arch, "makeComponent", None)
            o = headless_call(mkc, bar) if mkc else bar
            if not hasattr(o, "IfcType"):
                o.addProperty("App::PropertyString", "IfcType", "IFC", "IFC type")
            try:
                o.IfcType = "Reinforcing Bar"
            except Exception:
                pass
            how = "bars made here (FreeCAD's rebar needs its window)"
        named(o, a.get("name") or "Rebar")
        out = finish(o, "bim_rebar", {"made": how, "structure": host.Label, "bars": n, "diameter": dia, "along": along, "layer": layer,
                                       "spacing": round(span / (n - 1), 1) if n > 1 else None})
        length = (p1 - p0).Length
        out["totalLength_m"] = round(n * length / 1000, 3)
        out["mass_kg"] = round(n * length * 3.14159 * (dia / 2) ** 2 * 7.85e-6, 2)
        return out

    def bim_panel(a):
        mk = getattr(Arch, "makePanel", None)
        if mk is None:
            raise ToolError("unavailable", "This FreeCAD's BIM has no panel.")
        t = pos(a, "thickness", 18)
        if a.get("points"):
            base = baseline(a["points"], f"{a.get('name') or 'Panel'} outline", closed=True)
            base.Shape = Part.Face(base.Shape)
            o = headless_call(mk, base, thickness=t)
        else:
            o = headless_call(mk, None, length=pos(a, "length", 1000), width=pos(a, "width", 500), thickness=t)
            o.Placement.Base = vec(a.get("at", [0, 0, 0]), "at")
        named(o, a.get("name") or "Panel")
        return finish(o, "bim_panel", {"thickness": t})

    def bim_pipe(a):
        mk = getattr(Arch, "makePipe", None)
        if mk is None:
            raise ToolError("unavailable", "This FreeCAD's BIM has no pipe.")
        path = baseline(a.get("points"), f"{a.get('name') or 'Pipe'} path")
        d = pos(a, "diameter", 50)
        o = headless_call(mk, path, d)
        named(o, a.get("name") or "Pipe")
        setprops(o, {"Diameter": d, "WallThickness": a.get("wall")})
        return finish(o, "bim_pipe", {"diameter": d, "length": round(path.Shape.Length, 1)})

    def bim_equipment(a):
        mk = getattr(Arch, "makeEquipment", None)
        if mk is None:
            raise ToolError("unavailable", "This FreeCAD's BIM has no equipment.")
        base = picks.find(doc(), a.get("object"))
        o = headless_call(mk, base)
        named(o, a.get("name") or f"{base.Label} equipment")
        setprops(o, {"Model": a.get("model"), "Url": a.get("url"), "EquipmentPower": a.get("watts")})
        if a.get("ifc"):
            setprops(o, {"IfcType": a["ifc"]})
        return finish(o, "bim_equipment", {"from": base.Label})

    def bim_grid(a):
        mk = getattr(Arch, "makeAxis", None)
        if mk is None:
            raise ToolError("unavailable", "This FreeCAD's BIM has no axes.")
        made = []
        for dirn, k in (("x", "xSpacing"), ("y", "ySpacing")):
            sp = a.get(k) or a.get("spacing")
            if not sp:
                continue
            sp = [float(x) for x in sp] if isinstance(sp, list) else [float(sp)] * (int(a.get("count", 4)) - 1)
            ax = headless_call(mk, len(sp) + 1, float(a.get("length", 10000)))
            ax.Distances = [0.0] + sp
            if dirn == "y":
                ax.Placement = FreeCAD.Placement(V(0, 0, 0), FreeCAD.Rotation(V(0, 0, 1), 90))
            named(ax, f"{a.get('name') or 'Grid'} {dirn.upper()}")
            made.append(ax.Label)
        if not made:
            raise bad("spacing (or xSpacing / ySpacing): mm between grid lines, one number or a list.")
        doc().recompute()
        s.snapshot("bim_grid")
        return {"axes": made, "step": len(s.steps)}

    def bim_section(a):
        mk = getattr(Arch, "makeSectionPlane", None)
        if mk is None:
            raise ToolError("unavailable", "This FreeCAD's BIM has no section plane.")
        objs = [picks.find(doc(), r) for r in (a.get("objects") or [])] or [o for o in doc().Objects if is_bim(o) and not any(is_bim(p) for p in o.InList)]
        o = headless_call(mk, objs)
        n = vec(a.get("normal", [0, 0, 1]), "normal")
        o.Placement = FreeCAD.Placement(vec(a.get("at", [0, 0, 1200]), "at"), FreeCAD.Rotation(V(0, 0, 1), n))
        named(o, a.get("name") or "Section")
        doc().recompute()
        s.snapshot("bim_section")
        return {"object": o.Label, "cuts": [x.Label for x in objs], "note": "A section plane for TechDraw (td_view of it) and the FreeCAD window; export { section: z } writes a plan cut as DXF/SVG headless.", "step": len(s.steps)}

    def bim_material(a):
        mk = getattr(Arch, "makeMaterial", None)
        if mk is None:
            raise ToolError("unavailable", "This FreeCAD's BIM has no materials.")
        name = a.get("name") or "Material"
        mat = next((o for o in doc().Objects if o.Label == name and type(getattr(o, "Proxy", None)).__name__ == "_ArchMaterial"), None)
        if mat is None:
            mat = headless_call(mk, name)
            named(mat, name)
        data = dict(getattr(mat, "Material", {}) or {})
        for k, key in (("density", "Density"), ("description", "Description"), ("color", "Color")):
            if a.get(k) is not None:
                data[key] = (f"{a[k]} kg/m^3" if k == "density" else str(a[k]))
        mat.Material = data
        objs = [picks.find(doc(), r) for r in (a.get("objects") or [])]
        for o in objs:
            if hasattr(o, "Material"):
                o.Material = mat
        doc().recompute()
        s.snapshot("bim_material")
        return {"material": mat.Label, "data": data, "on": [o.Label for o in objs if hasattr(o, "Material")], "step": len(s.steps)}

    pt = {"type": "array", "items": {"type": "number"}}
    pts = {"type": "array", "items": pt}
    return [
        Tool("bim_wall", "A wall along points [[x, y(, z)] …] (closed: true joins the last to the first) or a base sketch/wire: width (200), height (3000) mm, align center | left | right of the line.",
             obj({"points": pts, "closed": {"type": "boolean"}, "base": {"type": "string"}, "width": {"type": "number"}, "height": {"type": "number"},
                  "align": {"type": "string", "enum": ["center", "left", "right"]}, "name": {"type": "string"}}), bim_wall, changes=True),
        Tool("bim_opening", "A window or door set into a wall, its opening cut through (the wall's subtraction): kind window | door — a frame (frame mm wide, depth mm deep) round a glass pane or a door panel — width, height, at (its left edge, mm along the wall from its start), sill (mm above the wall's foot: 900 window, 0 door), frame and depth mm.",
             obj({"wall": {"type": "string"}, "kind": {"type": "string", "enum": ["window", "door"]}, "preset": {"type": "string"}, "width": {"type": "number"}, "height": {"type": "number"},
                  "at": {"type": "number"}, "sill": {"type": "number"}, "frame": {"type": "number"}, "depth": {"type": "number"}, "name": {"type": "string"}}, ["wall"]), bim_opening, changes=True),
        Tool("bim_structure", "Structure: column { at, width, depth, height }, beam { from, to, width, depth }, slab or footing { points outline or base, thickness (slab on top of the outline, footing below it) }.",
             obj({"kind": {"type": "string", "enum": list(ROLES)}, "at": pt, "from": pt, "to": pt, "points": pts, "base": {"type": "string"}, "width": {"type": "number"}, "depth": {"type": "number"},
                  "height": {"type": "number"}, "thickness": {"type": "number"}, "name": {"type": "string"}}), bim_structure, changes=True),
        Tool("bim_roof", "A roof over a closed outline (points, at the eaves' height, or base): angle degrees (one, or one per edge; 90 makes a gable end), thickness, overhang mm.",
             obj({"points": pts, "base": {"type": "string"}, "angle": {}, "run": {}, "thickness": {"type": "number"}, "overhang": {"type": "number"}, "name": {"type": "string"}}), bim_roof, changes=True),
        Tool("bim_stairs", "A straight flight: at (the foot, on its left side going up), direction [x, y] it climbs, length (its run), width, height (floor to floor), steps (height / 175 by default). Answers riser, going and 2R + G.",
             obj({"at": pt, "direction": pt, "length": {"type": "number"}, "width": {"type": "number"}, "height": {"type": "number"}, "steps": {"type": "integer"}, "name": {"type": "string"}}), bim_stairs, changes=True),
        Tool("bim_space", "A space (a room) from its floor outline (points, height) or a base solid: its floor area; use the space's purpose.",
             obj({"points": pts, "base": {"type": "string"}, "height": {"type": "number"}, "use": {"type": "string"}, "name": {"type": "string"}}), bim_space, changes=True),
        Tool("bim_level", "A level (building storey) holding objects, at elevation mm (height: floor to floor).",
             obj({"objects": {"type": "array", "items": {"type": "string"}}, "elevation": {"type": "number"}, "height": {"type": "number"}, "name": {"type": "string"}}), bim_level, changes=True),
        Tool("bim_building", "A building holding levels, on a site (site: false for none).",
             obj({"levels": {"type": "array", "items": {"type": "string"}}, "site": {"type": "boolean"}, "siteName": {"type": "string"}, "name": {"type": "string"}}), bim_building, changes=True),
        Tool("bim_curtain_wall", "A curtain wall along points, height mm: mullions every vertical / horizontal mm (1500), mullion width and depth, glass thickness.",
             obj({"points": pts, "height": {"type": "number"}, "vertical": {"type": "number"}, "horizontal": {"type": "number"}, "mullion": {"type": "number"},
                  "depth": {"type": "number"}, "glass": {"type": "number"}, "name": {"type": "string"}}, ["points"]), bim_curtain_wall, changes=True),
        Tool("bim_rebar", "Straight reinforcing bars in a structure (slab, beam, column, footing): along x | y | z (the bars' direction), layer bottom | top, diameter (12), cover (30), spacing or amount. Answers bars, total length and steel mass.",
             obj({"structure": {"type": "string"}, "along": {"type": "string", "enum": ["x", "y", "z"]}, "layer": {"type": "string", "enum": ["bottom", "top"]}, "diameter": {"type": "number"},
                  "cover": {"type": "number"}, "spacing": {"type": "number"}, "amount": {"type": "integer"}, "name": {"type": "string"}}, ["structure"]), bim_rebar, changes=True),
        Tool("bim_panel", "A panel (sheet material: plywood, cladding): an outline (points) or length × width at, thickness (18).",
             obj({"points": pts, "length": {"type": "number"}, "width": {"type": "number"}, "thickness": {"type": "number"}, "at": pt, "name": {"type": "string"}}), bim_panel, changes=True),
        Tool("bim_pipe", "A pipe along points: diameter, wall (thickness) mm.", obj({"points": pts, "diameter": {"type": "number"}, "wall": {"type": "number"}, "name": {"type": "string"}}, ["points"]), bim_pipe, changes=True),
        Tool("bim_equipment", "An object made BIM equipment (furniture, fixtures, appliances): object, ifc (e.g. Furniture, Sanitary Terminal), model, url, watts.",
             obj({"object": {"type": "string"}, "ifc": {"type": "string"}, "model": {"type": "string"}, "url": {"type": "string"}, "watts": {"type": "number"}, "name": {"type": "string"}}, ["object"]), bim_equipment, changes=True),
        Tool("bim_grid", "Structural grid lines: spacing mm (one number with count, or a list), or xSpacing / ySpacing each; length of the lines.",
             obj({"spacing": {}, "xSpacing": {}, "ySpacing": {}, "count": {"type": "integer"}, "length": {"type": "number"}, "name": {"type": "string"}}), bim_grid, changes=True),
        Tool("bim_section", "A section plane through objects (all BIM objects when left out): at, normal ([0,0,1] for a plan cut).",
             obj({"objects": {"type": "array", "items": {"type": "string"}}, "at": pt, "normal": pt, "name": {"type": "string"}}), bim_section, changes=True),
        Tool("bim_material", "A BIM material (made if new): name, density kg/m³, description, color; put on objects.",
             obj({"name": {"type": "string"}, "density": {"type": "number"}, "description": {"type": "string"}, "color": {"type": "string"}, "objects": {"type": "array", "items": {"type": "string"}}}, ["name"]), bim_material, changes=True),
        Tool("bim_schedule", "Quantities: every wall (length, height, area, volume), window and door (size, area), column, beam, slab, roof, stairs (volume) and space (floor area), with totals by kind; sheet: true (or a name) writes them to a spreadsheet.",
             obj({"sheet": {}}), bim_schedule),
        Tool("bim_list", "The BIM objects: IFC type, what each holds, which wall each opening is in. Changes nothing.", obj({}), bim_list),
        Tool("bim_export", "Write IFC (path .ifc): the site or building, or objects; read back by type. Needs IfcOpenShell.",
             obj({"objects": {"type": "array", "items": {"type": "string"}}, "path": {"type": "string"}}), bim_export),
        Tool("bim_import", "Read an IFC file into the document (path). Needs IfcOpenShell.", obj({"path": {"type": "string"}}, ["path"]), bim_import, changes=True),
    ]
