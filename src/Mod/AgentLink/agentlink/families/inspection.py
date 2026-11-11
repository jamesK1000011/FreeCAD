# SPDX-License-Identifier: LGPL-2.1-or-later
# Inspection: FreeCAD's own Inspection feature — an actual (a mesh, points,
# or a body meshed for the purpose, or a scan read from a file) measured
# against nominals (bodies, meshes, points) point by point, signed where
# the nominal is a solid — run, read back as numbers a person can act on
# (RMS, spread, how much is in tolerance, where the worst spots are),
# coloured, and written out. The feature stays in the document, so the
# person sees the same colours in FreeCAD's Inspection workbench.

import csv
import json
import math
import os

import Inspection  # noqa: F401 — the family stands on the module; left out without it
import Mesh

import numpy as np

from .. import render
from ..protocol import Tool, ToolError

GROUP = "inspection"
INFO = "FreeCAD's Inspection: an actual (mesh, points, body or scan file) against nominals, point by point, with tolerance, statistics, worst spots, a colour map and a report."

TOL_PROP = ("ToleranceLow", "ToleranceHigh")
BIG = 1e30  # Inspection marks a point out of reach with ±FLT_MAX


def obj(props, required=()):
    return {"type": "object", "properties": props, "required": list(required), "additionalProperties": False}


def bad(msg):
    return ToolError("invalid_input", msg)


def r4(v):
    return round(float(v), 4) + 0.0


def kind_of(o):
    if o.isDerivedFrom("Mesh::Feature"):
        return "mesh"
    if o.isDerivedFrom("Points::Feature"):
        return "points"
    if o.isDerivedFrom("Part::Feature"):
        return "shape"
    return None


def points_of(o):
    """The actual's points in the order Inspection measures them."""
    k = kind_of(o)
    if k == "mesh":
        return np.array([[p.x, p.y, p.z] for p in o.Mesh.Points], dtype=float)
    if k == "points":
        return np.array([[p.x, p.y, p.z] for p in o.Points.Points], dtype=float)
    return None


def stats(d, lo=None, hi=None, bins=10):
    """Numbers from Inspection's distances (a numpy array)."""
    d = np.asarray(d, dtype=float)
    reach = np.abs(d) < BIG
    m = d[reach]
    out = {"points": int(d.size), "measured": int(m.size), "outOfReach": int(d.size - m.size)}
    if d.size:
        out["outOfReachPercent"] = r4(100.0 * (d.size - m.size) / d.size)
    if not m.size:
        return out
    out.update({"min": r4(m.min()), "max": r4(m.max()), "mean": r4(m.mean()), "rms": r4(math.sqrt(float(np.mean(m * m)))),
                "stdDev": r4(m.std()), "p95abs": r4(np.percentile(np.abs(m), 95))})
    if lo is not None and hi is not None:
        inside = (m >= lo) & (m <= hi)
        out["tolerance"] = [r4(lo), r4(hi)]
        out["inTolerancePercent"] = r4(100.0 * inside.sum() / m.size)
        out["over"] = int((m > hi).sum())
        out["under"] = int((m < lo).sum())
        out["verdict"] = "pass" if inside.all() and not out["outOfReach"] else "fail" if not inside.all() else "unknown"
    edges = np.linspace(m.min(), m.max(), bins + 1) if m.max() > m.min() else np.array([m.min() - 1e-6, m.max() + 1e-6])
    counts, edges = np.histogram(m, edges)
    out["histogram"] = [{"from": r4(edges[i]), "to": r4(edges[i + 1]), "count": int(c)} for i, c in enumerate(counts)]
    return out


def worst(P, d, n=5, lo=None, hi=None, spacing=None):
    """The worst spots: the largest |distance| (or furthest outside the
    band), spread so the same bump is not named five times."""
    d = np.asarray(d, dtype=float)
    ok = np.abs(d) < BIG
    if P is None or not ok.any():
        return []
    idx = np.where(ok)[0]
    if lo is not None and hi is not None:
        score = np.maximum(d[idx] - hi, lo - d[idx])
    else:
        score = np.abs(d[idx])
    order = idx[np.argsort(-score)]
    span = float(np.linalg.norm(P.max(0) - P.min(0))) if len(P) else 1.0
    gap = spacing if spacing else max(span * 0.05, 1e-6)
    out = []
    for i in order:
        p = P[i]
        if any(np.linalg.norm(p - np.array(o["at"])) < gap for o in out):
            continue
        out.append({"at": [r4(p[0]), r4(p[1]), r4(p[2])], "distance": r4(d[i])})
        if len(out) >= n:
            break
    return out


def inspections(doc):
    return [o for o in doc.Objects if o.TypeId == "Inspection::Feature"]


def band(o):
    if all(p in o.PropertiesList for p in TOL_PROP):
        lo, hi = float(o.ToleranceLow), float(o.ToleranceHigh)
        if hi > lo:
            return lo, hi
    return None, None


def _map(s, a):
    """view_render's "inspection" map: the actual coloured by its distances."""
    doc = s.doc
    ins = inspections(doc)
    ref = a.get("inspection")
    o = next((x for x in ins if ref in (x.Label, x.Name)), None) if ref else (ins[-1] if ins else None)
    if o is None:
        raise ToolError("not_found", "No inspection to show: insp_run first" + (f" ({ref!r} is not one)" if ref else "") + ".")
    d = np.asarray(o.Distances, dtype=float)
    vals = np.where(np.abs(d) < BIG, d, np.nan)
    act = o.Actual
    lo, hi = band(o)
    r = float(o.SearchRadius)
    rng = [lo, hi] if lo is not None else [-r, r]
    if kind_of(act) == "mesh":
        m = act.Mesh
        V = np.array([[p.x, p.y, p.z] for p in m.Points], dtype=float)
        F = np.array([list(f.PointIndices) for f in m.Facets], dtype=int)
        layer = {"name": act.Label, "V": V, "F": F, "values": vals, "creases": True}
    else:
        # points: shown on the first nominal's surface, each spot the nearest point's value
        nom = (o.Nominals or [None])[0]
        if nom is None or not hasattr(nom, "Shape") and kind_of(nom) != "mesh":
            raise ToolError("unavailable", "Points are coloured on a nominal body or mesh, and this inspection has none.")
        L = render.body_layer(nom, 0) if kind_of(nom) == "shape" else {"V": np.array([[p.x, p.y, p.z] for p in nom.Mesh.Points]), "F": np.array([list(f.PointIndices) for f in nom.Mesh.Facets])}
        V, F = L["V"], L["F"]
        P = points_of(act)
        good = np.isfinite(vals)
        layer = {"name": nom.Label, "V": V, "F": F, "values": render.nearest_values(V, P[good], vals[good]), "creases": True}
    return {"layers": [layer], "unit": "mm", "cmap": "jet", "symmetric": lo is None, "range": rng, "worst": "abs", "label": "DEVIATION",
            "note": f"{o.Label}: distance to the nominal at each point ({'tolerance' if lo is not None else '± the search radius'} spans the colours; grey is out of reach)."}


render.register_map("inspection", _map, "An Inspection feature's distances on its actual (insp_run): inspection names which, else the last.")


def tools(session):
    s = session
    doc = lambda: s.doc  # noqa: E731

    def find(ref, what="object"):
        for o in doc().Objects:
            if ref in (o.Label, o.Name):
                return o
        for o in doc().Objects:
            if o.Label.lower() == str(ref).lower():
                return o
        raise ToolError("not_found", f"{what} {ref!r}: not here — session_state and object_list name what there is.")

    def ins_of(ref):
        all_ = inspections(doc())
        if ref is None:
            if len(all_) == 1:
                return all_[0]
            raise (ToolError("not_found", "No inspection: insp_run makes one.") if not all_ else bad(f"inspection: say which — {', '.join(o.Label for o in all_)}."))
        o = next((x for x in all_ if ref in (x.Label, x.Name)), None)
        if o is None:
            raise ToolError("not_found", f"inspection {ref!r}: there are {', '.join(x.Label for x in all_) or 'none'}.")
        return o

    def tol_of(a):
        t = a.get("tolerance")
        if t is None:
            return None, None
        if isinstance(t, (int, float)) and not isinstance(t, bool):
            if t <= 0:
                raise bad("tolerance: a band [low, high] in mm, or ±t as a number over 0.")
            return -float(t), float(t)
        if isinstance(t, list) and len(t) == 2 and all(isinstance(x, (int, float)) for x in t) and t[0] < t[1]:
            return float(t[0]), float(t[1])
        raise bad("tolerance: ±t (a number) or [low, high] mm, low < high.")

    def mesh_of_body(o, deflection, spacing, cap):
        # Tessellated, then split until no edge is over spacing: a flat face
        # is measured across it, not only at its corners.
        L = render.body_layer(o, 0, tol=deflection)
        T, F = render.subdivide(L["V"], L["F"], spacing, cap=cap)
        m = Mesh.Mesh([[tuple(T[i]) for i in tri] for tri in F])
        f = doc().addObject("Mesh::Feature", "Inspected")
        f.Label = s.unique(f"{o.Label} as inspected")
        f.Mesh = m
        try:
            f.Visibility = False
        except Exception:
            pass
        return f

    def reading(o, n_worst=5):
        lo, hi = band(o)
        d = np.asarray(o.Distances, dtype=float)
        out = {"inspection": o.Label, "name": o.Name, "actual": o.Actual.Label if o.Actual else None, "nominals": [x.Label for x in o.Nominals],
               "searchRadius": r4(o.SearchRadius), "thickness": r4(o.Thickness), **stats(d, lo, hi)}
        P = points_of(o.Actual) if o.Actual is not None else None
        if P is not None and len(P) == len(d):
            out["worst"] = worst(P, d, n_worst, lo, hi)
        if out.get("outOfReachPercent", 0) > 5:
            out["note"] = (f"{out['outOfReachPercent']:g}% of the points are further than the search radius ({r4(o.SearchRadius)} mm) from every nominal: "
                           "a larger search_radius, or align the actual first (align_auto with ScanRuler for FreeCAD).")
        return out

    def insp_run(a):
        if a.get("file"):
            p = os.path.expanduser(a["file"])
            if not os.path.isabs(p):
                p = os.path.join(s.out_dir, p)
            if not os.path.isfile(p):
                raise ToolError("not_found", f"{p}: no such file.")
            ext = os.path.splitext(p)[1].lower()
            if ext in (".stl", ".obj", ".ply", ".off", ".3mf", ".amf"):
                act = doc().addObject("Mesh::Feature", "Actual")
                act.Mesh = Mesh.Mesh(p)
            elif ext in (".asc", ".pcd", ".xyz", ".txt", ".e57"):
                import Points

                act = doc().addObject("Points::Feature", "Actual")
                act.Points = Points.Points(p) if hasattr(Points, "Points") else Points.read(p)
            else:
                raise bad("file: a mesh (stl, obj, ply, off, 3mf, amf) or points (asc, pcd, xyz).")
            act.Label = s.unique(os.path.splitext(os.path.basename(p))[0])
        elif a.get("actual"):
            act = find(a["actual"], "actual")
        else:
            raise bad("actual: the measured thing (a mesh, points or a body), or file: a scan to read.")
        k = kind_of(act)
        if k is None:
            raise bad(f"{act.Label} is a {act.TypeId}: the actual is a mesh, points or a body.")
        meshed = None
        if k == "shape":
            # A body is measured at the vertices of a mesh of it, kept in the
            # document (hidden): each distance then has its point.
            defl = float(a.get("deflection", 0.1))
            if not 0.001 <= defl <= 5:
                raise bad("deflection: how closely the body's mesh follows it, 0.001 … 5 mm.")
            diag = act.Shape.BoundBox.DiagonalLength
            spacing = float(a.get("spacing") or max(diag / 100.0, 0.2))
            if spacing <= 0:
                raise bad("spacing: mm between measured points on the body, over 0.")
            cap = int(a.get("cap", 60000))
            meshed = mesh_of_body(act, defl, spacing, max(1000, min(cap, 400000)))
            act_used = meshed
        else:
            act_used = act
        noms = [find(n, "nominal") for n in (a.get("nominals") or [])]
        if not noms:
            raise bad("nominals: what it should be — bodies, meshes or points (a list of names).")
        for n in noms:
            if kind_of(n) is None:
                raise bad(f"nominal {n.Label} is a {n.TypeId}: bodies, meshes or points.")
            if n is act:
                raise bad("A nominal cannot be the actual itself.")
        r = float(a.get("search_radius", 1.0))
        if r <= 0:
            raise bad("search_radius: mm, over 0 — points further than this from every nominal are out of reach.")
        lo, hi = tol_of(a)
        if lo is not None and max(abs(lo), abs(hi)) > r:
            raise bad(f"tolerance reaches past search_radius ({r:g} mm): points out of tolerance would read as out of reach. Raise search_radius.")
        f = doc().addObject("Inspection::Feature", "Inspection")
        f.Label = s.unique(a.get("name") or f"Inspection of {act.Label}")
        f.Actual = act_used
        f.Nominals = noms
        f.SearchRadius = r
        if a.get("thickness") is not None:
            f.Thickness = float(a["thickness"])
        for p in TOL_PROP:
            f.addProperty("App::PropertyFloat", p, "Tolerance", "The band a distance must fall in, mm (set by Agent Link)")
        if lo is not None:
            f.ToleranceLow, f.ToleranceHigh = lo, hi
        doc().recompute()
        if not len(f.Distances):
            raise ToolError("failed", f"Inspection measured no points: {act.Label} holds none" + (" (the file read as empty — is it a mesh FreeCAD reads?)" if a.get("file") else "") + " — nothing was kept.")
        s.snapshot("insp_run")
        out = reading(f, int(a.get("worst", 5)))
        if meshed is not None:
            out["measuredOn"] = f"{meshed.Label}: {len(meshed.Mesh.Points)} points about {spacing:.3g} mm apart (spacing), hidden"
        out["next"] = "view_render { map: \"inspection\" } to see it; insp_export for a report."
        out["step"] = len(s.steps)
        return out

    def insp_update(a):
        o = ins_of(a.get("inspection"))
        if a.get("search_radius") is not None:
            r = float(a["search_radius"])
            if r <= 0:
                raise bad("search_radius: over 0.")
            o.SearchRadius = r
        if a.get("thickness") is not None:
            o.Thickness = float(a["thickness"])
        if "tolerance" in a:
            if a["tolerance"] is None:
                o.ToleranceLow = o.ToleranceHigh = 0.0
            else:
                lo, hi = tol_of(a)
                o.ToleranceLow, o.ToleranceHigh = lo, hi
        if a.get("nominals"):
            o.Nominals = [find(n, "nominal") for n in a["nominals"]]
        o.touch()
        doc().recompute()
        s.snapshot("insp_update")
        out = reading(o, int(a.get("worst", 5)))
        out["step"] = len(s.steps)
        return out

    def insp_list(a):
        out = []
        for o in inspections(doc()):
            r = reading(o, 0)
            out.append({k: r[k] for k in ("inspection", "actual", "nominals", "points", "rms", "inTolerancePercent", "verdict", "outOfReachPercent") if k in r})
        return {"inspections": out} if out else {"inspections": [], "note": "None yet: insp_run { actual, nominals }."}

    def insp_read(a):
        return reading(ins_of(a.get("inspection")), int(a.get("worst", 10)))

    def insp_export(a):
        o = ins_of(a.get("inspection"))
        base = (a.get("name") or o.Label).replace(" ", "-")
        d = np.asarray(o.Distances, dtype=float)
        P = points_of(o.Actual)
        lo, hi = band(o)
        rep = reading(o, int(a.get("worst", 10)))
        csv_path = os.path.join(s.out_dir, base + ".csv")
        with open(csv_path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["x", "y", "z", "distance_mm", "status"])
            for i, v in enumerate(d):
                x, y, z = (P[i] if P is not None and i < len(P) else (float("nan"),) * 3)
                if abs(v) >= BIG:
                    st, val = "out_of_reach", ""
                elif lo is not None:
                    st, val = ("over" if v > hi else "under" if v < lo else "ok"), f"{v:.5f}"
                else:
                    st, val = "", f"{v:.5f}"
                w.writerow([f"{x:.5f}", f"{y:.5f}", f"{z:.5f}", val, st])
        json_path = os.path.join(s.out_dir, base + ".json")
        with open(json_path, "w", encoding="utf-8") as fh:
            json.dump(rep, fh, indent=2)
        return {"inspection": o.Label, "files": {"csv": {"path": csv_path, "rows": int(d.size)}, "json": {"path": json_path}},
                "summary": {k: rep[k] for k in ("rms", "min", "max", "inTolerancePercent", "verdict") if k in rep}}

    return [
        Tool("insp_run", "Inspect: actual (a mesh, points or a body — a body is sampled every spacing mm (default 1% of its size), kept hidden as a mesh) or file (a scan: stl, obj, ply, asc, pcd …) against nominals [bodies, meshes, points]; search_radius mm (further reads out of reach), thickness, tolerance ±t or [low, high]. FreeCAD's own Inspection feature, left in the document. Answers RMS, spread, % in tolerance and the worst spots.",
             obj({"actual": {"type": "string"}, "file": {"type": "string"}, "nominals": {"type": "array", "items": {"type": "string"}}, "search_radius": {"type": "number"}, "thickness": {"type": "number"},
                  "tolerance": {"type": ["number", "array"]}, "deflection": {"type": "number"}, "spacing": {"type": "number"}, "cap": {"type": "integer"}, "name": {"type": "string"}, "worst": {"type": "integer"}}, ["nominals"]), insp_run, changes=True),
        Tool("insp_update", "Change an inspection and measure again: search_radius, thickness, tolerance (null clears), nominals.",
             obj({"inspection": {"type": "string"}, "search_radius": {"type": "number"}, "thickness": {"type": "number"}, "tolerance": {"type": ["number", "array", "null"]}, "nominals": {"type": "array", "items": {"type": "string"}}, "worst": {"type": "integer"}}), insp_update, changes=True),
        Tool("insp_read", "One inspection's numbers: RMS, min, max, mean, spread, histogram, in/out of tolerance, out of reach, worst spots (worst: how many). Changes nothing.",
             obj({"inspection": {"type": "string"}, "worst": {"type": "integer"}}), insp_read),
        Tool("insp_list", "The inspections in the document, each in a line. Changes nothing.", obj({}), insp_list),
        Tool("insp_export", "Write an inspection out: a CSV of every point (x, y, z, distance, status) and a JSON report, to the out folder.",
             obj({"inspection": {"type": "string"}, "name": {"type": "string"}, "worst": {"type": "integer"}}), insp_export),
    ]
