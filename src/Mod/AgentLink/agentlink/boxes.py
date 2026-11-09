# SPDX-License-Identifier: LGPL-2.1-or-later
# Gridfinity baseplates, project boxes and board enclosures, made to size.
#
# Baseplate, to the open Gridfinity system's published profile: 42 mm
# pockets, each from its floor up a 0.7 mm 45° chamfer, 1.8 mm straight and a
# 2.15 mm 45° chamfer — 4.65 mm — opening 42 mm square with 4 mm corners at
# the top. Open (a frame) or on a floor with 6 x 2 mm magnet holes 13 mm each
# way from each pocket's centre, where a bin's magnets sit.
#
# Boxes: the walls and floor one solid, the lid laid beside it top down, its
# lip going into the box at a clearance all round. Screws go through the lid
# into bosses in the corners (or standoffs under a board) whose pilot holes
# the screw taps; every hole takes the printer's fit calibration.

import FreeCAD
import Part

from . import fits_hw
from . import geometry as g
from .gridfinity import EPS, GRID, MAGNET_AT

V = FreeCAD.Vector

BP_H = 4.65
BP_R = 4.0
BP_LOW = 0.7  # the lower chamfer
BP_MID = 1.8  # straight
BP_TOP = 2.15  # the upper chamfer

# Bosses and standoffs for self-tapping screws into printed plastic:
# (outer diameter, pilot hole as modelled before calibration).
BOSS = {"M2": (5.0, 1.6), "M2.5": (6.0, 2.1), "M3": (7.0, 2.5), "M4": (9.0, 3.3)}


def check(table, given):
    """The parameters, defaults filled in, each checked: a ValueError names
    the first one wrong and what it takes."""
    unknown = sorted(set(given) - set(table))
    if unknown:
        raise ValueError(f"params: {', '.join(unknown)} not known — the keys: {', '.join(table)}.")
    out = {}
    for k, spec in table.items():
        v = given.get(k, spec.get("default"))
        kind = spec["kind"]
        if v is None and spec.get("optional"):
            out[k] = None
            continue
        if kind == "bool":
            if not isinstance(v, bool):
                raise ValueError(f"{k}: true or false.")
        elif kind == "enum":
            if v not in spec["values"]:
                raise ValueError(f"{k}: one of {', '.join(spec['values'])}.")
        elif kind in ("int", "num"):
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                raise ValueError(f"{k}: a number, {spec['min']} to {spec['max']}.")
            if kind == "int" and int(v) != v:
                raise ValueError(f"{k}: a whole number, {spec['min']} to {spec['max']}.")
            if not (spec["min"] <= v <= spec["max"]):
                raise ValueError(f"{k}: {spec['min']} to {spec['max']}, not {v}.")
        elif kind == "points":
            if not isinstance(v, list) or not all(isinstance(p, (list, tuple)) and len(p) == 2 and all(isinstance(c, (int, float)) for c in p) for p in v):
                raise ValueError(f"{k}: a list of [x, y] points, mm.")
        elif kind == "list":
            if not isinstance(v, list):
                raise ValueError(f"{k}: a list.")
        out[k] = v
    return out


def tips(table):
    return "; ".join(f"{k}: {v['tip']}" for k, v in table.items())


# ---- Gridfinity baseplate -----------------------------------------------------------------

BASEPLATE = {
    "x": {"kind": "int", "min": 1, "max": 12, "default": 3, "tip": "Pockets across (42 mm each)."},
    "y": {"kind": "int", "min": 1, "max": 12, "default": 3, "tip": "Pockets deep (42 mm each)."},
    "style": {"kind": "enum", "values": ["open", "magnets"], "default": "open", "tip": "open: a frame, the pockets open underneath; magnets: the pockets on a floor with four 6x2 magnet holes each."},
    "floor": {"kind": "num", "min": 2.6, "max": 10, "default": 3.0, "tip": "The magnet floor's thickness under the pockets, mm (magnets style)."},
}


def baseplate_shape(p, cal):
    """A baseplate standing on z = 0, centred in X and Y, and what it holds."""
    W, D = p["x"] * GRID, p["y"] * GRID
    fl = p["floor"] if p["style"] == "magnets" else 0.0
    H = fl + BP_H
    plate = g.prism(0, 0, W, D, BP_R, 0, H)
    # The pockets are pushed out by d all round, so neighbours overlap above
    # the top instead of meeting on a line there; their ridge stands d lower.
    d = EPS
    low = BP_LOW + BP_TOP  # the inset at the pocket's floor
    first = (fl - EPS, low - d + EPS, BP_R - low + d - EPS) if fl == 0 else (fl, low - d, BP_R - low + d)
    stations = [
        first,
        (fl + BP_LOW, BP_TOP - d, BP_R - BP_TOP + d),
        (fl + BP_LOW + BP_MID, BP_TOP - d, BP_R - BP_TOP + d),
        (H, -d, BP_R + d),
        (H + EPS, -d - EPS, BP_R + d + EPS),
    ]
    cx0, cy0 = -(p["x"] - 1) / 2.0 * GRID, -(p["y"] - 1) / 2.0 * GRID
    centres = [(cx0 + i * GRID, cy0 + j * GRID) for i in range(p["x"]) for j in range(p["y"])]
    cutters = [g.stepped(cx, cy, GRID, GRID, stations) for (cx, cy) in centres]
    magnets = None
    if fl:
        sizes = None
        for (cx, cy) in centres:
            for sx in (-1, 1):
                for sy in (-1, 1):
                    tool, sizes = fits_hw.hole_tool("magnet", "6x2", [cx + sx * MAGNET_AT, cy + sy * MAGNET_AT, fl], "-z", 0, "normal", cal)
                    cutters.append(tool)
        if sizes["depth"] > fl - 0.4:
            raise ValueError(f"floor: at least {sizes['depth'] + 0.4:.1f} mm, so 0.4 mm stays under the magnet holes ({sizes['depth']} mm deep).")
        magnets = {"count": 4 * len(centres), "hole": sizes["hole"], "depth": sizes["depth"], "what": "6x2 magnets, pressed in from above"}
    shape = g.cut_all(plate, cutters)
    return shape, {"pockets": len(centres), "height": round(H, 3), "magnets": magnets}


# ---- the lid both boxes share -------------------------------------------------------------

def lid_shape(L, D, r, w, lid, lip, cl, notches=(), holes=(), at=(0.0, 0.0)):
    """A lid laid top down beside the box: a plate L x D x lid and a lip
    ring standing up from it that goes into the box at cl all round.
    notches: [(x, y, radius)] cut from the lip (round bosses it clears);
    holes: [(x, y, diameter)] through the plate. Both in the box's own
    X and Y: the lid turns over about X to go on, so y is mirrored here."""
    ax, ay = at
    plate = g.prism(ax, ay, L, D, r, 0, lid)
    parts = [plate]
    if lip > 0:
        t = min(1.6, w)
        ow, od = L - 2 * w - 2 * cl, D - 2 * w - 2 * cl
        ro = max(0.0, r - w - cl)
        ring = g.prism(ax, ay, ow, od, ro, lid - 0.5, lid + lip)
        ring = ring.cut(g.prism(ax, ay, ow - 2 * t, od - 2 * t, max(0.0, ro - t), lid - 0.5 - EPS, lid + lip + EPS))
        cuts = [g.cylinder(rad, lip + 0.5 + 2 * EPS, ax + x, ay - y, lid - 0.5 - EPS) for (x, y, rad) in notches]
        ring = g.cut_all(ring, cuts)
        parts.append(ring)
    shape = g.fuse_all(parts)
    if holes:
        shape = g.cut_all(shape, [g.cylinder(dia / 2.0, lid + 2 * EPS, ax + x, ay - y, -EPS) for (x, y, dia) in holes])
    return shape


def _screw(name, cal):
    if name not in BOSS:
        raise ValueError(f"screw: one of {', '.join(BOSS)}.")
    boss, pilot = BOSS[name]
    clear = fits_hw.M[name]["clear"][1] + cal["hole"]
    return boss, pilot + cal["hole"], clear


# ---- project box --------------------------------------------------------------------------

PROJECT_BOX = {
    "length": {"kind": "num", "min": 20, "max": 400, "default": 80, "tip": "Outside, along X, mm."},
    "width": {"kind": "num", "min": 20, "max": 400, "default": 60, "tip": "Outside, along Y, mm."},
    "height": {"kind": "num", "min": 8, "max": 300, "default": 30, "tip": "The box outside, floor included, lid not, mm."},
    "wall": {"kind": "num", "min": 1.2, "max": 6, "default": 2.0, "tip": "Walls, mm."},
    "floor": {"kind": "num", "min": 0.8, "max": 6, "default": 2.0, "tip": "Floor, mm."},
    "radius": {"kind": "num", "min": 0, "max": 30, "default": 3.0, "tip": "Outside corner radius, mm."},
    "lid": {"kind": "num", "min": 0.8, "max": 6, "default": 2.0, "tip": "The lid's plate, mm."},
    "lip": {"kind": "num", "min": 0, "max": 15, "default": 3.0, "tip": "How far the lid's lip goes into the box, mm; 0 for a flat lid."},
    "clearance": {"kind": "num", "min": 0.05, "max": 1, "default": 0.2, "tip": "Gap between the lip and the walls, each side, mm."},
    "bosses": {"kind": "bool", "default": False, "tip": "Screw bosses in the four corners, with pilot holes, and clearance holes in the lid."},
    "screw": {"kind": "enum", "values": list(BOSS), "default": "M3", "tip": "The lid screws."},
    "gap": {"kind": "num", "min": 2, "max": 50, "default": 10, "tip": "Between the box and the lid laid beside it, mm."},
}


def project_box(p, cal):
    """(box, lid, what they are): the box centred on the origin standing on
    z = 0, the lid beside it in +X, top down."""
    L, D, H, w, fl, r = p["length"], p["width"], p["height"], p["wall"], p["floor"], p["radius"]
    if r >= min(L, D) / 2 - 1:
        raise ValueError(f"radius: under {min(L, D) / 2 - 1:.1f} mm for a box this size.")
    if H - fl < 4:
        raise ValueError("height: at least 4 mm more than the floor.")
    if p["lip"] > H - fl - 1:
        raise ValueError(f"lip: at most {H - fl - 1:.1f} mm, 1 mm short of the floor.")
    r_in = max(0.0, r - w)
    cavity = g.prism(0, 0, L - 2 * w, D - 2 * w, r_in, fl, H + EPS)
    info = {"outside": [L, D, H], "inside": [round(L - 2 * w, 3), round(D - 2 * w, 3), round(H - fl, 3)]}
    notches, lid_holes, pilots = [], [], []
    if p["bosses"]:
        if w < 1.6:
            raise ValueError("wall: 1.6 mm at least with bosses.")
        bd, pilot, clear = _screw(p["screw"], cal)
        br = bd / 2.0
        # The boss runs 1 mm into both walls, so it meets them across, not
        # on a tangent; with a big inner corner it stands on the corner's own
        # centre, 1 mm wider than the corner.
        if r_in <= br - 1:
            a = br - 1
        else:
            a, br = r_in, max(br, r_in + 1)
        if 2 * (w + a + br) > min(L, D) - 4:
            raise ValueError("length and width: too small for corner bosses this size — a bigger box or a smaller screw.")
        cx, cy = L / 2 - w - a, D / 2 - w - a
        at = [(sx * cx, sy * cy) for sx in (-1, 1) for sy in (-1, 1)]
        cavity = cavity.cut([g.cylinder(br, H - fl + 2, x, y, fl - 1) for (x, y) in at])
        depth = min(3.5 * float(p["screw"][1:]), H - fl)
        pilots = [g.cylinder(pilot / 2.0, depth + EPS, x, y, H - depth) for (x, y) in at]
        notches = [(x, y, br + p["clearance"]) for (x, y) in at]
        lid_holes = [(x, y, clear) for (x, y) in at]
        info["bosses"] = {"screw": p["screw"], "at": [[round(x, 3), round(y, 3)] for (x, y) in at], "boss": round(2 * br, 3), "pilot": round(pilot, 3), "pilotDepth": round(depth, 3), "lidHole": round(clear, 3)}
    box = g.cut_all(g.prism(0, 0, L, D, r, 0, H), [cavity] + pilots)
    lid = lid_shape(L, D, r, w, p["lid"], p["lip"], p["clearance"], notches, lid_holes, at=(L + p["gap"], 0.0))
    info["lid"] = {"plate": p["lid"], "lip": p["lip"], "clearance": p["clearance"], "at": [L + p["gap"], 0.0], "note": "Printed top down; turn it over about X to put it on."}
    return box, lid, info


# ---- board enclosure ----------------------------------------------------------------------

BOARD_BOX = {
    "board": {"kind": "list", "default": [50.0, 30.0], "tip": "The board's size [x, y], mm; board coordinates start at its lower-left corner."},
    "thickness": {"kind": "num", "min": 0.4, "max": 4, "default": 1.6, "tip": "The board, mm."},
    "holes": {"kind": "points", "default": [], "tip": "Mounting holes [[x, y] …] in board coordinates: a standoff under each."},
    "screw": {"kind": "enum", "values": list(BOSS), "default": "M3", "tip": "The screws through the board's holes."},
    "standoff": {"kind": "num", "min": 1, "max": 40, "default": 5, "tip": "Floor to the board's underside, mm."},
    "headroom": {"kind": "num", "min": 1, "max": 150, "default": 15, "tip": "The board's top to the rim, mm: the tallest part, and a margin."},
    "gap": {"kind": "num", "min": 0.2, "max": 20, "default": 1.0, "tip": "The board's edge to the walls, mm."},
    "wall": {"kind": "num", "min": 1.2, "max": 6, "default": 2.0, "tip": "Walls, mm."},
    "floor": {"kind": "num", "min": 0.8, "max": 6, "default": 2.0, "tip": "Floor, mm."},
    "radius": {"kind": "num", "min": 0, "max": 20, "default": 2.0, "tip": "Outside corner radius, mm."},
    "lid": {"kind": "num", "min": 0.8, "max": 6, "default": 2.0, "tip": "The lid's plate, mm."},
    "lip": {"kind": "num", "min": 0, "max": 15, "default": 2.0, "tip": "How far the lid's lip goes in, mm; 0 for a flat lid."},
    "clearance": {"kind": "num", "min": 0.05, "max": 1, "default": 0.2, "tip": "Lip to walls, each side, mm."},
    "openings": {"kind": "list", "default": [], "tip": "Openings for edge connectors: [{ side: front|back|left|right, at: from the board's corner along that side (x for front/back, y for left/right) to the connector's middle, width, height, z: its bottom over the board's top (0) }], mm."},
    "margin": {"kind": "num", "min": 0, "max": 3, "default": 0.5, "tip": "Added round each opening, mm."},
    "gapLid": {"kind": "num", "min": 2, "max": 50, "default": 10, "tip": "Between the box and the lid laid beside it, mm."},
}

SIDES = ("front", "back", "left", "right")


def board_box(p, cal):
    """(box, lid, what they are): the box centred on the origin, floor on
    z = 0; boardAt is where the board's lower-left underside corner sits."""
    try:
        bw, bd = (float(v) for v in p["board"])
    except (TypeError, ValueError):
        raise ValueError("board: [x, y], the board's size, mm.")
    if not (5 <= bw <= 400 and 5 <= bd <= 400):
        raise ValueError("board: each side 5 to 400 mm.")
    w, fl, r, gap = p["wall"], p["floor"], p["radius"], p["gap"]
    iw, idp = bw + 2 * gap, bd + 2 * gap
    L, D = iw + 2 * w, idp + 2 * w
    zb = fl + p["standoff"]  # the board's underside
    zt = zb + p["thickness"]
    H = zt + p["headroom"]
    if p["lip"] > p["headroom"] - 0.5:
        raise ValueError(f"lip: at most {p['headroom'] - 0.5:.1f} mm, so it stays clear of the board — or more headroom.")
    if r >= min(L, D) / 2 - 1:
        raise ValueError(f"radius: under {min(L, D) / 2 - 1:.1f} mm for a box this size.")
    r_in = max(0.0, r - w)
    x0, y0 = -bw / 2.0, -bd / 2.0  # the board's corner in the box
    box = g.cut_all(g.prism(0, 0, L, D, r, 0, H), [g.prism(0, 0, iw, idp, r_in, fl, H + EPS)])
    info = {"outside": [round(L, 3), round(D, 3), round(H, 3)], "inside": [round(iw, 3), round(idp, 3), round(H - fl, 3)], "boardAt": [round(x0, 3), round(y0, 3), round(zb, 3)]}

    adds, cuts = [], []
    if p["holes"]:
        bdia, pilot, _ = _screw(p["screw"], cal)
        rad = bdia / 2.0
        depth = min(p["standoff"] + fl - 0.8, 3.5 * float(p["screw"][1:]))
        made = []
        for (hx, hy) in p["holes"]:
            if not (0 <= hx <= bw and 0 <= hy <= bd):
                raise ValueError(f"holes: [{hx}, {hy}] is off the board (0..{bw}, 0..{bd}).")
            x, y = x0 + hx, y0 + hy
            # A standoff that only just reaches a wall would meet it on a
            # tangent: shrink it a little instead.
            near = min(x - (-iw / 2), iw / 2 - x, y - (-idp / 2), idp / 2 - y)
            if rad - near > w - 0.8:
                raise ValueError(f"holes: [{hx}, {hy}] is so near the edge its standoff would come through the wall — gap {rad - near - w + 0.8 + gap:.1f} mm or more.")
            rr = rad - 0.1 if abs(near - rad) < 0.1 else rad
            adds.append(g.cylinder(rr, p["standoff"] + 0.5, x, y, fl - 0.5))
            cuts.append(g.cylinder(pilot / 2.0, depth + EPS, x, y, zb - depth))
            made.append([round(x, 3), round(y, 3)])
        info["standoffs"] = {"screw": p["screw"], "at": made, "diameter": bdia, "pilot": round(pilot, 3), "pilotDepth": round(depth, 3)}
    if adds:
        box = g.fuse_all([box] + adds)

    opened = []
    m = p["margin"]
    for i, o in enumerate(p["openings"]):
        if not isinstance(o, dict) or o.get("side") not in SIDES:
            raise ValueError(f"openings.{i}.side: one of {', '.join(SIDES)}.")
        try:
            at, wd, ht, z = float(o["at"]), float(o["width"]), float(o["height"]), float(o.get("z", 0))
        except (KeyError, TypeError, ValueError):
            raise ValueError(f"openings.{i}: at, width and height, mm (z optional).")
        if wd <= 0 or ht <= 0:
            raise ValueError(f"openings.{i}: width and height above 0.")
        z0 = zt + z - m
        z1 = zt + z + ht + m
        if z0 < fl + 0.4:
            raise ValueError(f"openings.{i}: its bottom is {z0 - fl:.1f} mm over the floor — a taller standoff, or z higher.")
        if z1 > H - 0.8:
            z1 = H + 1  # into the rim: a notch open at the top
        side = o["side"]
        if side in ("front", "back"):
            c = x0 + at
            lo, hi = c - wd / 2 - m, c + wd / 2 + m
            if lo < -iw / 2 + 0.4 or hi > iw / 2 - 0.4:
                raise ValueError(f"openings.{i}: runs into the corner — at and width keep it inside the {side} wall.")
            ys = (-D / 2 - 1, -idp / 2 + 1) if side == "front" else (idp / 2 - 1, D / 2 + 1)
            cuts.append(Part.makeBox(hi - lo, ys[1] - ys[0], z1 - z0, V(lo, ys[0], z0)))
        else:
            c = y0 + at
            lo, hi = c - wd / 2 - m, c + wd / 2 + m
            if lo < -idp / 2 + 0.4 or hi > idp / 2 - 0.4:
                raise ValueError(f"openings.{i}: runs into the corner — at and width keep it inside the {side} wall.")
            xs = (-L / 2 - 1, -iw / 2 + 1) if side == "left" else (iw / 2 - 1, L / 2 + 1)
            cuts.append(Part.makeBox(xs[1] - xs[0], hi - lo, z1 - z0, V(xs[0], lo, z0)))
        opened.append({"side": side, "from": round(lo, 3), "to": round(hi, 3), "z": [round(z0, 3), round(min(z1, H), 3)], **({"note": "open at the rim"} if z1 > H else {})})
    if cuts:
        box = g.cut_all(box, cuts)
    if opened:
        info["openings"] = opened
    lid = lid_shape(L, D, r, w, p["lid"], p["lip"], p["clearance"], at=(L + p["gapLid"], 0.0))
    info["lid"] = {"plate": p["lid"], "lip": p["lip"], "clearance": p["clearance"], "at": [round(L + p["gapLid"], 3), 0.0], "note": "Printed top down; turn it over about X to put it on."}
    return box, lid, info
