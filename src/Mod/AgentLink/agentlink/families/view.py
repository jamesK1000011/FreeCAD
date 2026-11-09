# SPDX-License-Identifier: LGPL-2.1-or-later
# Pictures to check the work by. view_render draws the parts without a
# screen (headless too): shaded, from any standard view or several at once,
# cut open, zoomed to a spot, painted with a map — the wall, the stress of a
# solve, or what another add-on registers (a scan's deviation). The picture
# comes back to the agent as an image as well as a file. view_snapshot
# saves the FreeCAD window's own 3D view, when there is one.

import os

import numpy as np

from .. import render
from ..protocol import Tool, ToolError
from ..tools import obj

GROUP = "view"
INFO = "Pictures: the parts drawn from any view, cut open, zoomed, with maps (wall, stress, a scan's deviation) — headless; the FreeCAD window's own view."

ALIASES = {"back": "rear", "isometric": "iso"}
GRID = ["iso", "top", "front", "right"]


def _views(a):
    v = a.get("views", a.get("view", "iso"))
    if v == "all":
        v = GRID
    names = [v] if isinstance(v, str) else list(v or [])
    out = []
    for n in names:
        n = ALIASES.get(str(n).lower(), str(n).lower())
        if n not in render.VIEWS:
            raise ToolError("invalid_input", f"view: one of {', '.join(render.VIEWS)}, a list of up to 6, or \"all\" (iso, top, front, right).")
        out.append(n)
    if not 1 <= len(out) <= 6:
        raise ToolError("invalid_input", "views: 1 to 6.")
    return out


def _vec(v, what):
    if not (isinstance(v, (list, tuple)) and len(v) == 3):
        raise ToolError("invalid_input", f"{what}: [x, y, z] in mm.")
    return [float(x) for x in v]


def _bodies(s, a):
    refs = a.get("objects")
    if refs is None:
        return s.bodies() if s.doc else []
    if isinstance(refs, str):
        refs = [refs]
    return [s.body(r) for r in refs]


# ---- the maps Agent Link has ---------------------------------------------------------

def _wall(s, a):
    reach = float(a.get("reach", 12.0))
    layers = []
    for i, o in enumerate(_bodies(s, a)):
        L = render.body_layer(o, i, tol=float(a.get("tolerance", 0.05)))
        V, F, vals = render.wall_map(L["V"], L["F"], reach=reach, cap=int(a.get("detail", 40000)))
        layers.append({"name": o.Label, "V": V, "F": F, "values": vals, "creases": True})
    # Colours spent on the thin end: up to twice the typical wall.
    allv = np.concatenate([l["values"] for l in layers]) if layers else np.zeros(0)
    fin = allv[np.isfinite(allv)]
    hi = min(reach, max(2.0, 2 * float(np.median(fin)))) if len(fin) else reach
    return {"layers": layers, "unit": "mm", "cmap": "jet_r", "worst": "min", "label": "WALL", "range": [0.0, round(hi, 1)],
            "note": f"The wall at each spot: straight in from the surface to where the material ends; {reach:g} mm or more reads {reach:g}. Red is thin."}


def _stress(s, a):
    r = getattr(s, "_agentlink_fea", None)
    if not r or "field" not in r:
        raise ToolError("not_found", "No stress to show: fea_solve first (a solve in this session).")
    o = s.body(r["body"])
    from .. import geometry as geo

    stale = geo.stats(o.Shape) != r["stats"]
    f = r["field"]
    P, vm = np.asarray(f["points"], dtype=float), np.asarray(f["vonMises"], dtype=float)
    L = render.body_layer(o, 0, tol=float(a.get("tolerance", 0.05)))
    V, F = render.subdivide(L["V"], L["F"], max(np.ptp(L["V"], axis=0).max() / 60.0, 0.3), cap=int(a.get("detail", 40000)))
    vals = render.nearest_values(V, P, vm)
    y = (r["result"].get("material") or {}).get("yield")
    out = {"layers": [{"name": o.Label, "V": V, "F": F, "values": vals, "creases": True}], "unit": "MPa", "cmap": "jet", "worst": "max", "label": "VON MISES",
           "note": "Von Mises stress from the last solve, at the nearest mesh node." + (f" Yield {y:g} MPa." if y else "")}
    if stale:
        out["note"] += " The body changed since the solve: stale — fea_solve again."
        out["stale"] = True
    return out


render.register_map("wall", _wall, "The wall thickness at each spot of the parts (red thin).")
render.register_map("stress", _stress, "Von Mises stress of the last fea_solve.")


def tools(session):
    s = session

    def where(path, ext):
        p = os.path.expanduser(path)
        if not os.path.isabs(p):
            p = os.path.join(s.out_dir, p)
        if not p.lower().endswith(ext):
            p += ext
        return p

    def view_render(a):
        views = _views(a)
        w, h = int(a.get("width", 900 if len(views) > 1 else 800)), int(a.get("height", 700 if len(views) > 1 else 600))
        if not (64 <= w <= 3000 and 64 <= h <= 3000):
            raise ToolError("invalid_input", "width and height: 64 to 3000 px.")
        section = a.get("section")
        if section is not None:
            if not isinstance(section, dict):
                raise ToolError("invalid_input", "section: { axis: x|y|z or [x, y, z], offset (mm, along it; the middle when left out), flip }.")
            ax = section.get("axis", "x")
            if not (ax in ("x", "y", "z") or (isinstance(ax, list) and len(ax) == 3 and any(float(c) for c in ax))):
                raise ToolError("invalid_input", "section axis: x, y, z or [x, y, z].")
        frame = None
        if a.get("zoom") is not None:
            z = a["zoom"]
            if not isinstance(z, dict) or "at" not in z:
                raise ToolError("invalid_input", "zoom: { at: [x, y, z], size: mm across (20) } — e.g. a finding's at.")
            frame = {"at": _vec(z["at"], "zoom at"), "size": float(z.get("size", 20))}
            if frame["size"] <= 0:
                raise ToolError("invalid_input", "zoom size: mm across, over 0.")

        layers, shown = [], []
        mapname = a.get("map")
        mapinfo = None
        if mapname:
            if mapname not in render.MAPS:
                raise ToolError("invalid_input", f"map: one of {', '.join(render.MAPS) or 'none here'}.")
            m = render.MAPS[mapname][0](s, a)
            vals = np.concatenate([np.asarray(l["values"], dtype=float) for l in m["layers"]]) if m["layers"] else np.zeros(0)
            fin = vals[np.isfinite(vals)]
            if not len(fin):
                raise ToolError("failed", f"The {mapname} map read nothing here.")
            rng = a.get("range") or m.get("range")
            if rng:
                lo, hi = float(rng[0]), float(rng[1])
            elif m.get("symmetric"):
                hi = float(np.percentile(np.abs(fin), 99)) or 1e-6
                lo = -hi
            else:
                lo, hi = float(np.percentile(fin, 1)), float(np.percentile(fin, 99))
                if m.get("worst") == "min":
                    lo = min(0.0, float(fin.min()))
            if hi <= lo:
                hi = lo + 1e-6
            for l in m["layers"]:
                l["colors"] = render.colours_for(l["values"], lo, hi, m.get("cmap", "jet"))
                layers.append(l)
                shown.append({"layer": l["name"], "triangles": int(len(l["F"])), "map": mapname})
            # The worst spot: where to look (zoom: { at } it).
            allV = np.concatenate([np.asarray(l["V"], dtype=float) for l in m["layers"]])
            key = {"min": vals, "max": -vals, "abs": -np.abs(vals)}.get(m.get("worst", "max"), -vals)
            key = np.where(np.isfinite(vals), key, np.inf)
            k = int(np.argmin(key))
            mapinfo = {"range": [lo, hi], "unit": m.get("unit", ""), "cmap": m.get("cmap", "jet"), "label": m.get("label", mapname.upper())}
            summary = {"name": mapname, "unit": m.get("unit", ""), "range": [round(lo, 4), round(hi, 4)], "min": round(float(fin.min()), 4), "max": round(float(fin.max()), 4),
                       "worst": {"value": round(float(vals[k]), 4), "at": [round(float(c), 2) for c in allV[k]]}, "note": m.get("note", "")}
            unread = float((~np.isfinite(vals)).mean())
            if unread > 0:
                summary["unread"] = f"{unread:.0%} (grey)"
            for kk in ("stale", "warning"):
                if kk in m:
                    summary[kk] = m[kk]
        else:
            summary = None
            for i, o in enumerate(_bodies(s, a)):
                l = render.body_layer(o, i, tol=float(a.get("tolerance", 0.05)))
                layers.append(l)
                shown.append({"layer": o.Label, "triangles": int(len(l["F"]))})
        want = a.get("layers")
        for name, (fn, _) in render.LAYERS.items():
            if want is not None and name not in want:
                continue
            if mapname and any(sh["layer"] == name for sh in shown):
                continue
            for l in fn(s, a) or []:
                if mapname and l.get("mapped"):
                    continue
                layers.append(l)
                shown.append({"layer": l["name"], "triangles": int(len(l["F"]))})
        if want is not None:
            unknown = [x for x in want if x not in render.LAYERS]
            if unknown:
                raise ToolError("invalid_input", f"layers: {', '.join(render.LAYERS) or 'none here'} (the parts are always drawn; objects picks which).")
        if not layers:
            raise ToolError("not_found", "Nothing to draw: no body is shown, and no scan or other layer is loaded.")

        img, across, drawn = render.compose(layers, views, w, h, frame=frame, section=section, mapinfo=mapinfo)
        data = render.png(img)
        p = where(a.get("path") or f"render_{'_'.join(views)}{'_' + mapname if mapname else ''}.png", ".png")
        os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
        with open(p, "wb") as f:
            f.write(data)
        out = {"path": p, "views": views, "mmAcross": across, "layers": shown, "width": w, "height": h, "bytes": len(data)}
        if summary:
            out["map"] = summary
        if section:
            out["section"] = section
        if frame:
            out["zoom"] = frame
        if not drawn:
            out["warning"] = "Nothing landed in the picture: the zoom or the cut leaves the parts out of view."
        if a.get("inline", True):
            out["_png"] = data
        return out

    def view_maps(a):
        return {"maps": [{"name": n, "about": about} for n, (_, about) in render.MAPS.items()],
                "layers": [{"name": n, "about": about} for n, (_, about) in render.LAYERS.items()],
                "views": list(render.VIEWS)}

    def view_snapshot(a):
        import FreeCAD

        if not FreeCAD.GuiUp:
            raise ToolError("unavailable", "view_snapshot is the FreeCAD window's own view: there is no window (headless). view_render draws the parts without one.")
        import FreeCADGui

        gd = FreeCADGui.getDocument(s.doc.Name)
        view = gd.activeView() if gd else None
        if view is None or not hasattr(view, "saveImage"):
            raise ToolError("unavailable", "No 3D view open for this document: open one in FreeCAD, then again — or view_render.")
        which = a.get("view")
        if which:
            which = ALIASES.get(which, which)
            turn = {"iso": "viewIsometric", "front": "viewFront", "rear": "viewRear", "top": "viewTop", "bottom": "viewBottom", "left": "viewLeft", "right": "viewRight"}
            if which not in turn:
                raise ToolError("invalid_input", f"view: one of {', '.join(turn)}, or left out to keep the person's view.")
            getattr(view, turn[which])()
            if a.get("fit", True):
                view.fitAll()
        w, h = int(a.get("width", 800)), int(a.get("height", 600))
        if not (16 <= w <= 4096 and 16 <= h <= 4096):
            raise ToolError("invalid_input", "width and height: 16 to 4096 px.")
        p = where(a.get("path") or f"snapshot_{which or 'current'}.png", ".png")
        view.saveImage(p, w, h, a.get("background", "Current"))
        out = {"path": p, "view": which or "as the person left it", "width": w, "height": h, "bytes": os.path.getsize(p)}
        if a.get("inline", True):
            with open(p, "rb") as f:
                out["_png"] = f.read()
        return out

    vschema = {"description": "A view (iso, top, bottom, front, rear, left, right, iso-front-left, iso-rear-left, iso-rear-right), a list of up to 6 drawn side by side, or \"all\"."}
    return [
        Tool(
            "view_render",
            "Draw the parts as a picture and look at it — works headless. views: one or several (\"all\": iso, top, front, right, each fitted). "
            "map: wall (thickness, red thin), stress (the last fea_solve), or one an add-on adds (view_maps lists them) — with a legend, and the worst spot's at; range [lo, hi] fixes the colours. "
            "section { axis x|y|z, offset mm, flip } cuts it open (solid cut faces hatched). zoom { at, size mm } frames a spot — a finding's at. "
            "objects: which bodies (all shown when left out); layers: which add-on layers (a scan …). The PNG comes back as an image (inline false: the file only) and is written to path.",
            obj({
                "view": vschema, "views": vschema,
                "map": {"type": "string"}, "range": {"type": "array", "items": {"type": "number"}},
                "section": {"type": "object"}, "zoom": {"type": "object"},
                "objects": {}, "layers": {"type": "array", "items": {"type": "string"}},
                "reach": {"type": "number"}, "tolerance": {"type": "number"}, "detail": {"type": "integer"},
                "width": {"type": "integer"}, "height": {"type": "integer"}, "inline": {"type": "boolean"}, "path": {"type": "string"},
            }),
            view_render,
        ),
        Tool("view_maps", "The maps and layers view_render can draw here (add-ons add theirs), and the views.", obj({}), view_maps),
        Tool("view_snapshot", "Save the FreeCAD window's own 3D view as the person sees it (view: turn it to a standard view first, fit true) and look at it. Needs the window; view_render draws without one.", obj({"view": {"type": "string"}, "fit": {"type": "boolean"}, "width": {"type": "integer"}, "height": {"type": "integer"}, "background": {"type": "string"}, "inline": {"type": "boolean"}, "path": {"type": "string"}}), view_snapshot),
    ]
