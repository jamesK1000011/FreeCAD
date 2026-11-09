# SPDX-License-Identifier: LGPL-2.1-or-later
# Is a part printable by FDM, as it stands on the bed (+z up): thin walls
# against the nozzle, overhangs past the angle, flat ceilings (bridges),
# upright holes too small to come out open, too little on the bed, a part
# too tall for its base, and the printer's build volume. Each finding has an
# id (F1 …), the rule, its severity, the value against the limit, where it
# is, and what to do. A finding the person waived comes back waived.

import math

from . import geometry as geo

PRINTERS = {
    # Build volumes, mm. Typical figures; the maker's page is the authority.
    "generic": (220, 220, 250),
    "bed-slinger": (220, 220, 250),
    "corexy": (256, 256, 256),
    "h2d": (325, 320, 325),
}


def rules_for(nozzle=0.4, printer=None):
    n = float(nozzle)
    return {
        "nozzle": n,
        "line": n,  # one extrusion line: a wall under it cannot be laid down
        "minWall": max(0.8, 2 * n),  # two lines: under it, it prints as one
        "maxOverhangDeg": 50.0,
        "maxBridge": 10.0,
        "minHole": max(2.0, 5 * n),
        "minBedShare": 0.05,
        "maxRatio": 8.0,
        "printer": printer or "generic",
        "volume": PRINTERS.get(printer or "generic", PRINTERS["generic"]),
    }


def _regions(shape, keep, tol=0.05):
    """Triangles of the shape's tessellation that `keep(normal, centroid)`
    accepts, joined into regions by shared corners: each with its area, its
    centroid, its box, and the worst value keep gave."""
    pts, tris = shape.tessellate(tol)
    key = lambda p: (round(p.x, 3), round(p.y, 3), round(p.z, 3))
    ids = [key(p) for p in pts]
    chosen = []
    for t in tris:
        a, b, c = pts[t[0]], pts[t[1]], pts[t[2]]
        n = (b - a).cross(c - a)
        area = n.Length / 2
        if area < 1e-9:
            continue
        n.normalize()
        cen = (a + b + c) * (1.0 / 3)
        v = keep(n, cen)
        if v is not None:
            chosen.append((t, area, cen, v))
    parent = list(range(len(chosen)))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    by_vertex = {}
    for i, (t, _, _, _) in enumerate(chosen):
        for k in t:
            j = by_vertex.setdefault(ids[k], i)
            if j != i:
                parent[root(i)] = root(j)
    out = {}
    for i, (t, area, cen, v) in enumerate(chosen):
        r = out.setdefault(root(i), {"area": 0.0, "sum": [0.0, 0.0, 0.0], "worst": v, "lo": [1e9] * 3, "hi": [-1e9] * 3})
        r["area"] += area
        for k, x in enumerate((cen.x, cen.y, cen.z)):
            r["sum"][k] += x * area
            r["lo"][k] = min(r["lo"][k], x)
            r["hi"][k] = max(r["hi"][k], x)
        r["worst"] = max(r["worst"], v)
    regions = []
    for r in out.values():
        a = r["area"]
        regions.append({"area": a, "at": [round(s / a, 2) for s in r["sum"]], "worst": r["worst"], # A bridge spans the short way across, between the walls on either side
        # of it: a long narrow strip is a short bridge.
        "span": min(r["hi"][0] - r["lo"][0], r["hi"][1] - r["lo"][1])})
    return sorted(regions, key=lambda r: -r["area"])


def check(shape, rules, waived=(), min_area=1.0):
    """The findings, worst first, and the wall read: { findings, wall, rules }."""
    findings = []
    bb = shape.BoundBox
    z0 = bb.ZMin

    def add(rule, severity, value, limit, unit, at, says, area=None):
        findings.append({"rule": rule, "severity": severity, "value": round(value, 2), "limit": limit, "unit": unit, "at": [round(x, 2) for x in at], **({"areaMm2": round(area, 1)} if area else {}), "says": says})

    # Thin walls, from the rays: under one line an error, under the minimum
    # a warning — it prints as a single line.
    w = geo.least_wall(shape)
    if w.get("least") is not None:
        for run in geo.thin_runs(w["readings"], rules["minWall"], 2.5 * w["spacing"]):
            sev = "error" if run["least"] < rules["line"] else "warning"
            lim = rules["line"] if sev == "error" else rules["minWall"]
            add("thin-wall", sev, run["least"], lim, "mm", run["at"], f"a wall {run['least']:.2f} mm thick over {run['count']} readings — " + ("thinner than one line, it can't be printed: thicken it" if sev == "error" else f"under {rules['minWall']} mm it prints as a single line: thicken it if it must be strong"))
            findings[-1]["points"] = run["points"]

    # Overhangs past the angle (from vertical), and flat ceilings: a
    # surface facing down, off the bed.
    maxd = rules["maxOverhangDeg"]

    def overhang(n, c):
        if n.z >= 0 or c.z < z0 + 0.05:
            return None
        deg = math.degrees(math.asin(min(1.0, -n.z)))
        return deg if maxd < deg < 89.0 else None

    for r in _regions(shape, overhang):
        if r["area"] >= min_area:
            add("overhang", "warning", r["worst"], maxd, "°", r["at"], f"{r['area']:.0f} mm² facing down at {r['worst']:.0f}° from vertical, past {maxd:.0f}°: support it, chamfer it to 45°, or turn the part", r["area"])

    def ceiling(n, c):
        return 90.0 if n.z <= -math.cos(math.radians(1.0)) and c.z > z0 + 0.05 else None

    for r in _regions(shape, ceiling):
        if r["area"] >= min_area and r["span"] > rules["maxBridge"]:
            add("bridge", "warning", r["span"], rules["maxBridge"], "mm", r["at"], f"a flat ceiling about {r['span']:.1f} mm across, {r['area']:.0f} mm²: past the {rules['maxBridge']:.0f} mm bridge limit — support it, add a rib, or arch it", r["area"])

    # Upright holes too small to print open: a cylinder about +z, the
    # material outside it (its normal points in to the axis).
    for f in shape.Faces:
        s = f.Surface
        if type(s).__name__ != "Cylinder" or abs(abs(s.Axis.z) - 1) > 1e-3:
            continue
        u0, u1, v0, v1 = f.ParameterRange
        p = f.valueAt((u0 + u1) / 2, (v0 + v1) / 2)
        n = f.normalAt((u0 + u1) / 2, (v0 + v1) / 2)
        to_axis = s.Center - p
        to_axis.z = 0
        if n.dot(to_axis) <= 0:
            continue  # a boss, not a hole
        d = 2 * s.Radius
        if d < rules["minHole"]:
            add("small-hole", "warning", d, rules["minHole"], "mm", [p.x, p.y, p.z], f"an upright hole Ø{d:.2f} mm, under {rules['minHole']} mm: it may print closed — drill it after, or make it bigger")

    # On the bed: the flat faces at the bottom against the footprint.
    bed = sum(f.Area for f in shape.Faces if type(f.Surface).__name__ == "Plane" and abs(f.BoundBox.ZMax - z0) < 1e-3 and abs(f.BoundBox.ZMin - z0) < 1e-3)
    foot = bb.XLength * bb.YLength or 1.0
    if bed / foot < rules["minBedShare"]:
        add("bed-contact", "warning", bed / foot, rules["minBedShare"], "share", [bb.Center.x, bb.Center.y, z0], f"{bed:.0f} mm² on the bed, {100 * bed / foot:.0f}% of its footprint: add a brim, or stand it on a flatter face")
    ratio = bb.ZLength / max(1e-6, min(bb.XLength, bb.YLength))
    if ratio > rules["maxRatio"]:
        add("tall-thin", "warning", ratio, rules["maxRatio"], "ratio", [bb.Center.x, bb.Center.y, bb.ZMax], f"{ratio:.1f} times as tall as its base is narrow: it may wobble or snap — widen the base or lay it down")
    vx, vy, vz = rules["volume"]
    fits = (bb.XLength <= vx and bb.YLength <= vy) or (bb.XLength <= vy and bb.YLength <= vx)
    if not fits or bb.ZLength > vz:
        add("build-volume", "error", max(bb.XLength, bb.YLength, bb.ZLength), max(rules["volume"]), "mm", [bb.Center.x, bb.Center.y, bb.Center.z], f"{bb.XLength:.0f} × {bb.YLength:.0f} × {bb.ZLength:.0f} mm does not fit the {rules['printer']} ({vx} × {vy} × {vz} mm): split it or turn it")

    order = {"error": 0, "warning": 1, "info": 2}
    findings.sort(key=lambda f: (order[f["severity"]], f["rule"]))
    for i, f in enumerate(findings, 1):
        f["id"] = f"F{i}"
        w_ = _waived_by(f, waived)
        if w_:
            f["waived"] = w_.get("reason", "waived")
    for f in findings:
        f.pop("points", None)
    return {"findings": findings, "wall": {"least": w.get("least"), "at": w.get("at"), "read": w.get("read")}, "rules": {k: v for k, v in rules.items() if k != "volume"}}


def _waived_by(f, waived, near=1.5):
    pts = f.get("points") or [f["at"]]
    for x in waived:
        if x.get("rule") != f["rule"] or not isinstance(x.get("at"), list):
            continue
        a = x["at"]
        reach = near + (3.0 if f["rule"] == "thin-wall" else 0.0)
        if any(sum((a[i] - p[i]) ** 2 for i in range(3)) <= reach * reach for p in pts):
            return x
    return None
