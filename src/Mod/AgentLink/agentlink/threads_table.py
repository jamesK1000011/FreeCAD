# SPDX-License-Identifier: LGPL-2.1-or-later
# Screw thread sizes from the standards' tables, in millimetres: ISO metric
# coarse and fine (ISO 261 / ISO 724 basic profile, tap drills after
# ISO 2306) and Unified UNC / UNF (ASME B1.1 basic sizes, the usual 75 %
# tap drills). No FreeCAD here, so it is tested anywhere.

INCH = 25.4

# The basic profile's heights, as fractions of the pitch (ISO 68-1).
PITCH_DIA = 0.649519  # d2 = d - 0.649519 P
MINOR_INT = 1.082532  # D1 = D - 1.082532 P (the nut's minor)
MINOR_EXT = 1.226869  # d3 = d - 1.226869 P (the bolt's minor)

# (nominal mm, coarse pitch, tap drill mm)
METRIC_COARSE = [
    (1.6, 0.35, 1.25), (2.0, 0.4, 1.6), (2.5, 0.45, 2.05), (3.0, 0.5, 2.5), (3.5, 0.6, 2.9),
    (4.0, 0.7, 3.3), (5.0, 0.8, 4.2), (6.0, 1.0, 5.0), (8.0, 1.25, 6.8), (10.0, 1.5, 8.5),
    (12.0, 1.75, 10.2), (14.0, 2.0, 12.0), (16.0, 2.0, 14.0), (18.0, 2.5, 15.5),
    (20.0, 2.5, 17.5), (22.0, 2.5, 19.5), (24.0, 3.0, 21.0),
]
# (nominal mm, fine pitch): the preferred fine pitches of ISO 261; tap drill D - P.
METRIC_FINE = [
    (3.0, 0.35), (4.0, 0.5), (5.0, 0.5), (6.0, 0.75), (8.0, 1.0), (10.0, 1.25), (10.0, 1.0),
    (12.0, 1.5), (12.0, 1.25), (14.0, 1.5), (16.0, 1.5), (18.0, 1.5), (18.0, 2.0),
    (20.0, 1.5), (20.0, 2.0), (22.0, 1.5), (22.0, 2.0), (24.0, 2.0), (24.0, 1.5),
]
# (name, major inch, threads per inch, series, tap drill inch, the drill's name)
UNIFIED = [
    ("#4", 0.112, 40, "UNC", 0.0890, "#43"), ("#4", 0.112, 48, "UNF", 0.0935, "#42"),
    ("#5", 0.125, 40, "UNC", 0.1015, "#38"), ("#5", 0.125, 44, "UNF", 0.1040, "#37"),
    ("#6", 0.138, 32, "UNC", 0.1065, "#36"), ("#6", 0.138, 40, "UNF", 0.1130, "#33"),
    ("#8", 0.164, 32, "UNC", 0.1360, "#29"), ("#8", 0.164, 36, "UNF", 0.1360, "#29"),
    ("#10", 0.190, 24, "UNC", 0.1495, "#25"), ("#10", 0.190, 32, "UNF", 0.1590, "#21"),
    ("#12", 0.216, 24, "UNC", 0.1770, "#16"), ("#12", 0.216, 28, "UNF", 0.1820, "#14"),
    ("1/4", 0.250, 20, "UNC", 0.2010, "#7"), ("1/4", 0.250, 28, "UNF", 0.2130, "#3"),
    ("5/16", 0.3125, 18, "UNC", 0.2570, "F"), ("5/16", 0.3125, 24, "UNF", 0.2720, "I"),
    ("3/8", 0.375, 16, "UNC", 0.3125, "5/16"), ("3/8", 0.375, 24, "UNF", 0.3320, "Q"),
    ("7/16", 0.4375, 14, "UNC", 0.3680, "U"), ("7/16", 0.4375, 20, "UNF", 0.3906, "25/64"),
    ("1/2", 0.500, 13, "UNC", 0.4219, "27/64"), ("1/2", 0.500, 20, "UNF", 0.4531, "29/64"),
]
SERIES = ("metric", "metric-fine", "unc", "unf")


def _num(x):
    return f"{x:g}"


def _metric(d, p, fine, tap=None):
    tap = tap if tap is not None else round(d - p, 2)
    return {
        "size": f"M{_num(d)}" + (f"x{_num(p)}" if fine else ""),
        "series": "metric-fine" if fine else "metric",
        "major": d,
        "pitch": p,
        "pitchDia": round(d - PITCH_DIA * p, 3),
        "minorInt": round(d - MINOR_INT * p, 3),
        "minorExt": round(d - MINOR_EXT * p, 3),
        "tapDrill": tap,
        "freecad": _fc_metric(d, p),
    }


def _fc_metric(d, p):
    """The size as FreeCAD's Hole lists it: M6x1.0, M10x1.25."""
    ps = f"{p:.2f}".rstrip("0")
    if ps.endswith("."):
        ps += "0"
    return f"M{_num(d)}x{ps}"


def _unified(name, maj, tpi, series, tap, drill):
    p = INCH / tpi
    d = maj * INCH
    return {
        "size": f"{name}-{tpi}",
        "series": series.lower(),
        "major": round(d, 3),
        "pitch": round(p, 4),
        "tpi": tpi,
        "pitchDia": round(d - PITCH_DIA * p, 3),
        "minorInt": round(d - MINOR_INT * p, 3),
        "minorExt": round(d - MINOR_EXT * p, 3),
        "tapDrill": round(tap * INCH, 3),
        "tapDrillName": drill,
        "majorInch": maj,
        "freecad": name,
    }


def table(series=None):
    out = []
    if series in (None, "all", "metric"):
        out += [_metric(d, p, False, t) for d, p, t in METRIC_COARSE]
    if series in (None, "all", "metric-fine"):
        out += [_metric(d, p, True) for d, p in METRIC_FINE]
    if series in (None, "all", "unc", "unf"):
        out += [_unified(*u) for u in UNIFIED if series in (None, "all") or u[3].lower() == series]
    return out


def _norm(s):
    return str(s).strip().lower().replace(" ", "").replace("×", "x").replace("unc", "").replace("unf", "").rstrip("-")


def lookup(size):
    """One size by name: M6, M10x1.25, 1/4-20, #10-32, 1/4 (the coarse one)."""
    key = _norm(size)
    rows = table()
    for r in rows:
        if _norm(r["size"]) == key:
            return r
    # M8x1.25 names the coarse pitch; 1/4 or #10 alone means UNC.
    for r in rows:
        if r["series"].startswith("metric") and key in (_norm(r["size"]) + "x" + _num(r["pitch"]), _norm(r["freecad"])):
            return r
    for r in rows:
        if r["series"] == "unc" and key == _norm(r["freecad"]):
            return r
    return None


def nearest(diameter, inside, count=3):
    """The standard sizes nearest a measured diameter. A hole is read as a
    tap drill (to be tapped), the internal minor (a thread drawn
    cosmetically) or the major (a modelled thread's crest); a shaft as the
    major. Each with how far off, signed (measured − standard)."""
    out = []
    for r in table():
        if inside:
            cands = [("tapDrill", r["tapDrill"]), ("minorInt", r["minorInt"]), ("major", r["major"])]
        else:
            cands = [("major", r["major"])]
        as_, ref = min(cands, key=lambda c: abs(diameter - c[1]))
        out.append({"size": r["size"], "series": r["series"], "pitch": r["pitch"], "as": as_, "standard": ref, "off": round(diameter - ref, 3)})
    rank = {"tapDrill": 0, "minorInt": 1, "major": 2}
    out.sort(key=lambda x: (round(abs(x["off"]), 2), rank[x["as"]], x["series"] != "metric"))
    return out[:count]


def tie(near, within=0.02, limit=0.15):
    """The pitches to ask about when the nearest sizes share a diameter and
    differ by pitch alone (M10 coarse or fine, 1/4-20 or 1/4-28)."""
    if len(near) < 2 or abs(near[0]["off"]) > limit:
        return None
    same = [n for n in near if abs(abs(n["off"]) - abs(near[0]["off"])) <= within and n["as"] == near[0]["as"]]
    if len(same) < 2 or len({n["pitch"] for n in same}) < 2:
        return None
    return same
