# SPDX-License-Identifier: LGPL-2.1-or-later
# Fits and hardware holes for made parts. Sizes are the standards' (ISO 273
# clearance holes, ISO 4762 socket heads, ISO 10642 countersunk heads, ISO
# 4032 nuts; ISO 2009 for the countersunk heads under M3); heat-set inserts are typical figures, the maker's sheet is
# the authority. A printed hole comes out small and a printed peg big: the
# printer's calibration (hole, shaft: how much, diametral, mm) is added to
# every size here, 0.2 and 0 when the printer is not calibrated.

import math

import FreeCAD
import Part

V = FreeCAD.Vector

# ---- fits ---------------------------------------------------------------------------------

# Diametral gap between hole and peg as modelled, for a calibrated printer.
FITS = {"press": 0.0, "snug": 0.1, "slide": 0.2, "free": 0.35}
DEFAULT_CAL = {"hole": 0.2, "shaft": 0.0}


def size_fit(kind, nominal, of, cal):
    """The size to model a hole or a peg at for a fit on `nominal` (the
    bought part's, or the mating part's): a hole opened by the gap and by
    what the printer takes off a hole; a peg shrunk by what it adds."""
    if kind not in FITS:
        raise ValueError(f"kind: one of {', '.join(FITS)}.")
    if of == "hole":
        return nominal + FITS[kind] + cal["hole"]
    if of == "shaft":
        return nominal - cal["shaft"]
    raise ValueError("of: hole or shaft.")


def stack(items):
    """A tolerance stack: each item's nominal and ± tolerance (sign of the
    nominal says which way it adds). Worst case and root-sum-square."""
    nom = sum(i["nominal"] for i in items)
    wc = sum(abs(i.get("tol", 0)) for i in items)
    rss = math.sqrt(sum(i.get("tol", 0) ** 2 for i in items))
    return {"nominal": round(nom, 3), "worstCase": [round(nom - wc, 3), round(nom + wc, 3)], "rss": [round(nom - rss, 3), round(nom + rss, 3)]}


# ---- hardware -----------------------------------------------------------------------------

M = {
    # size: clearance (close, normal, loose), socket head (d, k), countersunk head d, nut (AF, m), insert (hole d, depth)
    "M2": {"clear": (2.2, 2.4, 2.6), "head": (3.8, 2.0), "csk": 3.8, "nut": (4.0, 1.6), "insert": (3.2, 4.0)},
    "M2.5": {"clear": (2.7, 2.9, 3.1), "head": (4.5, 2.5), "csk": 4.7, "nut": (5.0, 2.0), "insert": (3.6, 4.0)},
    "M3": {"clear": (3.2, 3.4, 3.6), "head": (5.5, 3.0), "csk": 6.72, "nut": (5.5, 2.4), "insert": (4.0, 5.7)},
    "M4": {"clear": (4.3, 4.5, 4.8), "head": (7.0, 4.0), "csk": 8.96, "nut": (7.0, 3.2), "insert": (5.6, 8.1)},
    "M5": {"clear": (5.3, 5.5, 5.8), "head": (8.5, 5.0), "csk": 11.2, "nut": (8.0, 4.7), "insert": (6.4, 9.5)},
    "M6": {"clear": (6.4, 6.6, 7.0), "head": (10.0, 6.0), "csk": 13.44, "nut": (10.0, 5.2), "insert": (8.0, 12.7)},
    "M8": {"clear": (8.4, 9.0, 10.0), "head": (13.0, 8.0), "csk": 17.92, "nut": (13.0, 6.8), "insert": (9.6, 12.7)},
}
KINDS = ("clearance", "counterbore", "countersink", "nut_trap", "insert", "magnet")
FIT_INDEX = {"close": 0, "normal": 1, "loose": 2}


def _dir(d):
    if isinstance(d, str):
        table = {"+x": V(1, 0, 0), "-x": V(-1, 0, 0), "+y": V(0, 1, 0), "-y": V(0, -1, 0), "+z": V(0, 0, 1), "-z": V(0, 0, -1)}
        if d not in table:
            raise ValueError("dir: +x, -x, +y, -y, +z, -z or [x, y, z].")
        return table[d]
    v = V(*d)
    if v.Length < 1e-9:
        raise ValueError("dir: not a zero vector.")
    v.normalize()
    return v


def _hex(af, h, at, d):
    r = af / math.sqrt(3)  # the corner radius of a hexagon af across flats
    pts = [V(r * math.cos(math.radians(30 + 60 * i)), r * math.sin(math.radians(30 + 60 * i)), 0) for i in range(6)]
    prism = Part.Face(Part.makePolygon(pts + [pts[0]])).extrude(V(0, 0, h))
    prism.Placement = FreeCAD.Placement(at, FreeCAD.Rotation(V(0, 0, 1), d))
    return prism


def hole_tool(kind, size, at, d, depth, fit, cal):
    """The solid a hole cuts, entering at `at` and going `d`, and the sizes
    it was made with (mm, as modelled — the calibration added)."""
    add = cal["hole"]
    at = V(*at)
    d = _dir(d)
    back = at - d * 0.05  # start a hair outside, so the entry face is cut clean
    if kind == "magnet":
        try:
            dia, t = (float(x) for x in str(size).lower().split("x"))
        except ValueError:
            raise ValueError("size: a magnet as diameter x thickness, mm — \"6x2\".")
        hd, hdep = dia + add, t + 0.2
        return Part.makeCylinder(hd / 2, hdep + 0.05, back, d), {"hole": round(hd, 3), "depth": round(hdep, 3)}
    if size not in M:
        raise ValueError(f"size: one of {', '.join(M)}.")
    m = M[size]
    if kind == "insert":
        hd, hdep = m["insert"][0] + add, m["insert"][1] + 0.5
        return Part.makeCylinder(hd / 2, hdep + 0.05, back, d), {"hole": round(hd, 3), "depth": round(hdep, 3), "note": "Typical insert figures; check the insert's sheet."}
    clear = m["clear"][FIT_INDEX.get(fit, 1)] + add
    shaft = Part.makeCylinder(clear / 2, depth + 0.05, back, d)
    sizes = {"clearance": round(clear, 3), "depth": round(depth, 3)}
    if kind == "clearance":
        return shaft, sizes
    if kind == "counterbore":
        hd, k = m["head"]
        cb_d, cb_h = hd + 0.5 + add, k + 0.3
        return shaft.fuse(Part.makeCylinder(cb_d / 2, cb_h + 0.05, back, d)), {**sizes, "counterbore": round(cb_d, 3), "counterboreDepth": round(cb_h, 3)}
    if kind == "countersink":
        top = m["csk"] + 0.3 + add
        h = (top - clear) / 2  # 90°
        return shaft.fuse(Part.makeCone(top / 2, clear / 2, h + 0.05, back, d)), {**sizes, "countersink": round(top, 3), "angle": 90}
    if kind == "nut_trap":
        af, mh = m["nut"]
        a, h = af + add, mh + 0.3
        return shaft.fuse(_hex(a, h + 0.05, back, d)), {**sizes, "nutAcrossFlats": round(a, 3), "nutDepth": round(h, 3)}
    raise ValueError(f"kind: one of {', '.join(KINDS)}.")
