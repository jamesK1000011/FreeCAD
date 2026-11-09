# SPDX-License-Identifier: LGPL-2.1-or-later
# Gridfinity bins, to the open system's published dimensions (Zack
# Freedman's Gridfinity: a 42 mm grid, 7 mm height units).
#
# Bin base, per cell, from the bottom: a 0.8 mm 45° chamfer, 1.8 mm
# straight, a 2.15 mm 45° chamfer — 4.75 mm, ending 41.5 mm square with
# 3.75 mm corners (half a millimetre short of the grid, for clearance).
# Stacking lip, inward from the top edge: 0.7 mm at 45°, 1.8 mm straight,
# 1.9 mm at 45° — 4.4 mm tall, on a 45° support out from the wall.
# Magnets 6 x 2 mm in 6.5 x 2.4 mm holes, 13 mm each way from each cell's
# centre, four to a cell.

from . import geometry as g

GRID = 42.0
UNIT = 7.0
CLEAR = 0.5
BASE_H = 4.75
LIP_H = 4.4
OUTER_R = 3.75
LIP_IN = 2.6
MAGNET_R = 3.25
MAGNET_DEPTH = 2.4
MAGNET_T = 2.0
MAGNET_AT = 13.0
SCREW_R = 1.5
# A cutter overshoots what it cuts by EPS, so no face is left coincident;
# a sloped cutter runs on along its slope, or the ruled loft leaves an EPS
# ledge where the slope should have met the edge.
EPS = 0.01

PARAMS = {
    "x": {"kind": "int", "min": 1, "max": 10, "default": 1, "tip": "Cells across (42 mm each)."},
    "y": {"kind": "int", "min": 1, "max": 10, "default": 1, "tip": "Cells deep (42 mm each)."},
    "units": {"kind": "int", "min": 2, "max": 30, "default": 3, "tip": "Height in 7 mm units, base included, lip not."},
    "lip": {"kind": "bool", "default": True, "tip": "The stacking lip."},
    "magnets": {"kind": "bool", "default": False, "tip": "6 x 2 mm magnet holes, four under each cell."},
    "screws": {"kind": "bool", "default": False, "tip": "M3 screw holes, four under each cell."},
    "wall": {"kind": "num", "min": 0, "max": 5, "default": 1.2, "tip": "Wall, mm; 0 for a solid block."},
    "floor": {"kind": "num", "min": 0.4, "max": 10, "default": 1.0, "tip": "Floor over the base, mm."},
    "dividersX": {"kind": "int", "min": 0, "max": 10, "default": 0, "tip": "Walls splitting it left to right."},
    "dividersY": {"kind": "int", "min": 0, "max": 10, "default": 0, "tip": "Walls splitting it front to back."},
    "label": {"kind": "num", "min": 0, "max": 20, "default": 0, "tip": "A label ledge along the back, mm deep; 0 for none."},
    "holes": {"kind": "num", "min": -0.5, "max": 1, "default": 0, "tip": "Added to hole diameters, mm: printed holes come out small."},
}


def values(given):
    """The parameters, defaults filled in, each checked: a ValueError names
    the first one wrong and its range."""
    unknown = sorted(set(given) - set(PARAMS))
    if unknown:
        raise ValueError(f"params: {', '.join(unknown)} not known — the keys: {', '.join(PARAMS)}.")
    out = {}
    for k, spec in PARAMS.items():
        v = given.get(k, spec["default"])
        if spec["kind"] == "bool":
            if not isinstance(v, bool):
                raise ValueError(f"{k}: true or false.")
        else:
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                raise ValueError(f"{k}: a number, {spec['min']} to {spec['max']}.")
            if spec["kind"] == "int" and int(v) != v:
                raise ValueError(f"{k}: a whole number, {spec['min']} to {spec['max']}.")
            if not (spec["min"] <= v <= spec["max"]):
                raise ValueError(f"{k}: {spec['min']} to {spec['max']}, not {v}.")
        out[k] = v
    if out["wall"] != 0 and out["wall"] < 0.8:
        raise ValueError("wall: 0 (solid) or 0.8 to 5.")
    if out["floor"] > out["units"] * UNIT - BASE_H - 1:
        raise ValueError("floor: 0.4 up to what the height leaves.")
    return out


def centres(p):
    return [((i - (p["x"] - 1) / 2.0) * GRID, (j - (p["y"] - 1) / 2.0) * GRID) for i in range(p["x"]) for j in range(p["y"])]


def magnets(p):
    """The magnets as placed: each one's centre (resting on the bed, in its
    hole open underneath), the holes' top, and the layer to pause at to drop
    them in and print over them."""
    if not p["magnets"]:
        return None
    pts = [[cx + sx * MAGNET_AT, cy + sy * MAGNET_AT, MAGNET_T / 2] for (cx, cy) in centres(p) for sx in (-1, 1) for sy in (-1, 1)]
    return {"what": "6x2 magnets", "count": len(pts), "centres": pts, "top": MAGNET_DEPTH, "hole": 2 * MAGNET_R + p["holes"]}


def bin_shape(p):
    """A bin, centred on the origin in X and Y, standing on z = 0."""
    W = p["x"] * GRID - CLEAR
    D = p["y"] * GRID - CLEAR
    H = p["units"] * UNIT
    cell = GRID - CLEAR
    feet = [
        g.stepped(cx, cy, cell, cell, [(0.0, 2.95, 0.8), (0.8, 2.15, 1.6), (2.6, 2.15, 1.6), (BASE_H, 0.0, OUTER_R)])
        for (cx, cy) in centres(p)
    ]
    body = g.fuse_all(feet + [g.prism(0, 0, W, D, OUTER_R, BASE_H, H)])
    if p["lip"]:
        cutter = g.stepped(0, 0, W, D, [
            (H - EPS, LIP_IN + EPS, max(0.5, OUTER_R - LIP_IN - EPS)),
            (H + LIP_H - 0.7 - 1.8, 0.7, max(0.5, OUTER_R - 0.7)),
            (H + LIP_H - 0.7, 0.7, max(0.5, OUTER_R - 0.7)),
            (H + LIP_H + EPS, -EPS, OUTER_R + EPS),
        ])
        ring = g.prism(0, 0, W, D, OUTER_R, H, H + LIP_H).cut(cutter)
        body = g.fuse_all([body, ring])
    if p["wall"] > 0:
        wall = p["wall"]
        floor_z = BASE_H + p["floor"]
        in_r = max(0.5, OUTER_R - wall)
        if p["lip"] and wall < LIP_IN:
            # Under the lip, a 45° support from the wall in to the lip's
            # foot, so nothing overhangs flat; above it, up through the lip.
            z = max(floor_z + EPS, H - (LIP_IN - wall))
            lip_r = max(0.5, OUTER_R - LIP_IN)
            inner = g.stepped(0, 0, W, D, [(floor_z, wall, in_r), (z, wall, in_r), (H, LIP_IN, lip_r), (H + 1, LIP_IN, lip_r)])
        else:
            inner = g.prism(0, 0, W - 2 * wall, D - 2 * wall, in_r, floor_z, H if p["lip"] else H + 1)
        body = g.cut_all(body, [inner])
        adds = []
        iw, idp = W - 2 * wall, D - 2 * wall
        for i in range(1, p["dividersX"] + 1):
            x = -iw / 2 + iw * i / (p["dividersX"] + 1)
            adds.append(g.prism(x, 0, wall, idp, 0, floor_z - EPS, H))
        for j in range(1, p["dividersY"] + 1):
            y = -idp / 2 + idp * j / (p["dividersY"] + 1)
            adds.append(g.prism(0, y, iw, wall, 0, floor_z - EPS, H))
        if p["label"] > 0:
            # A ledge along the back wall, its top 1 mm under the rim, a 45°
            # underside so it prints without support.
            t = 1.2
            top = H - 1
            back = idp / 2 + EPS
            y0 = back - p["label"]
            pts = [(-iw / 2, back, top), (-iw / 2, y0, top), (-iw / 2, y0, top - t), (-iw / 2, back, top - t - p["label"])]
            vs = [g.V(*a) for a in pts]
            wire = g.Part.makePolygon(vs + [vs[0]])
            adds.append(g.Part.Face(wire).extrude(g.V(iw, 0, 0)))
        if adds:
            body = g.fuse_all([body] + adds)
    holes = []
    for (cx, cy) in centres(p):
        for sx in (-1, 1):
            for sy in (-1, 1):
                if p["magnets"]:
                    holes.append(g.cylinder(MAGNET_R + p["holes"] / 2, MAGNET_DEPTH + EPS, cx + sx * MAGNET_AT, cy + sy * MAGNET_AT, -EPS))
                if p["screws"]:
                    holes.append(g.cylinder(SCREW_R + p["holes"] / 2, 6 + EPS, cx + sx * MAGNET_AT, cy + sy * MAGNET_AT, -EPS))
    body = g.cut_all(body, holes)
    return body


def name_of(p):
    return f"Gridfinity bin {p['x']}x{p['y']}x{p['units']}"
