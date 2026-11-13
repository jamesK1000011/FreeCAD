# SPDX-License-Identifier: LGPL-2.1-or-later
# A wood member's first hand check: a simple span under
# a uniform load from its tributary width, bending, shear (at a depth from
# the support), deflection under live and total load, and bearing — each as
# a ratio of what it sees to what it is allowed (above 1 fails). Allowable
# stresses are the reference design values times their adjustment factors;
# the reference values come from the person (the grade stamp and its table,
# or a supplier's sheet): none are shipped here. Input tolerances give a
# range for each ratio, and a range that crosses 1 is an engineer's call.
# Two load cases: dead plus live (or snow) at the given CD, and dead alone
# at CD 0.9; the worse governs. Cr (repetitive member) only for dimension
# lumber (2x to 4x, 89 mm or thinner a ply) at 610 mm or closer. Every value
# the engine fills in itself (bearing length, Cr, CD, the deflection limits)
# is listed in the answer's defaults. Not checked here: connections,
# ledgers, notches and holes, lateral stability and bracing, uplift, point
# loads, continuous or cantilevered spans, vibration.
# SI throughout: mm, N, MPa (N/mm²), kPa for area loads.

import math

KPA = 1e-3  # kPa in N/mm²

# Nominal sawn-lumber sizes to their dressed (dry) sizes, mm.
DRESSED = {"2x3": (38, 64), "2x4": (38, 89), "2x6": (38, 140), "2x8": (38, 184), "2x10": (38, 235), "2x12": (38, 286),
           "3x4": (64, 89), "3x6": (64, 140), "3x8": (64, 184), "3x10": (64, 235), "3x12": (64, 286),
           "4x4": (89, 89), "4x6": (89, 140), "4x8": (89, 184), "4x10": (89, 235), "4x12": (89, 286),
           "6x6": (140, 140), "6x8": (140, 191), "6x10": (140, 241), "6x12": (140, 292)}
FACTORS = ("CD", "CM", "Ct", "CL", "CF", "Cfu", "Ci", "Cr", "Cb")
IN, FT, PSF, PSI = 25.4, 304.8, 0.04788026, 0.00689476  # to mm, mm, kPa, MPa
# The adjustment factors' ranges in the NDS (a value outside is refused).
FACTOR_RANGE = {"CD": (0.9, 2.0), "CM": (0.5, 1.0), "Ct": (0.4, 1.0), "CL": (0.0, 1.0), "CF": (0.4, 1.5), "Cfu": (1.0, 1.2),
                "Ci": (0.8, 1.0), "Cr": (1.0, 1.15), "Cb": (1.0, 1.75)}
CD_DEAD = 0.9
DIMENSION_MAX_B = 89.0  # mm a ply: 2x to 4x is dimension lumber; thicker is a timber
NOT_CHECKED = ["connections and fasteners", "ledgers", "notches and holes", "lateral stability and bracing", "uplift",
               "point loads and load paths", "continuous, cantilevered or multi-span members", "vibration", "fire resistance", "decay and moisture"]


def size_of(member):
    """(b, d) mm from size \"2x8\" (dressed) or b, d given."""
    if member.get("size"):
        s = str(member["size"]).lower().replace(" ", "")
        if s not in DRESSED:
            raise ValueError(f"size: a nominal size ({', '.join(DRESSED)}) or b and d in mm.")
        b, d = DRESSED[s]
        plies = int(member.get("plies", 1))
        return b * plies, d
    b, d = member.get("b"), member.get("d")
    if not (isinstance(b, (int, float)) and isinstance(d, (int, float)) and b > 0 and d > 0):
        raise ValueError("member: size (\"2x8\") or b and d (mm).")
    return float(b), float(d)


def factor(factors, name, default=1.0):
    v = (factors or {}).get(name)
    return float(v) if v is not None else default


def check_factors(factors):
    """Each adjustment factor a number within its NDS range; raises ValueError."""
    for k, v in (factors or {}).items():
        if v is None:
            continue
        if k not in FACTOR_RANGE:
            raise ValueError(f"factor {k}: one of {', '.join(FACTORS)}")
        if not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v):
            raise ValueError(f"factor {k}: a number, not {v!r}")
        lo, hi = FACTOR_RANGE[k]
        if not lo <= float(v) <= hi:
            raise ValueError(f"factor {k} {v:g} is outside its NDS range {lo:g}–{hi:g}: refused")


def check_member(member, loads, design, factors=None, limits=None, tol=None):
    """A simple-span member's ratios. member { size | b, d, span (mm, clear
    between supports), spacing (mm on centre, or tributary width), bearing
    (mm, 38 if not given) }; loads { live, dead, snow (kPa) }; design { Fb,
    Fv, E, Fc_perp (MPa, reference) }; factors { CD, CM, Ct, CL, CF, Cfu, Ci,
    Cr, Cb } within their NDS ranges; limits { live: 360, total: 240 } (span
    over deflection); tol { span, d (mm) } plus-minus, 0 or more. Two cases:
    D + L (or S) at CD, and D alone at CD 0.9; the worse governs. Answers
    ratios, the governing one and its case, the range, the defaults the
    engine filled in, and what it did not check. Raises ValueError for
    inputs it cannot use."""
    factors = dict(factors or {})
    check_factors(factors)
    defaults = []
    given_limits = dict(limits or {})
    limits = {"live": 360, "total": 240, **given_limits}
    for k in ("live", "total"):
        if k not in given_limits:
            defaults.append({"fact": f"limit.{k}", "value": limits[k], "provenance": "defaulted", "note": f"deflection limit L/{limits[k]} (engine default)"})
        if not isinstance(limits[k], (int, float)) or limits[k] <= 0:
            raise ValueError(f"limits.{k}: span over deflection, above 0 (360).")
    b, d = size_of(member)
    L = float(member["span"])
    s = float(member["spacing"])
    if member.get("bearing") is None:
        defaults.append({"fact": "bearing", "value": 38, "provenance": "defaulted", "note": "bearing length 38 mm (engine default)"})
    lb = float(member.get("bearing", 38))
    live = float(loads.get("live", 0) or 0) + float(loads.get("snow", 0) or 0)
    dead = float(loads.get("dead", 0) or 0)
    if L <= 0 or s <= 0:
        raise ValueError("span and spacing: above 0 mm.")
    if lb <= 0:
        raise ValueError("bearing: above 0 mm.")
    if live < 0 or dead < 0:
        raise ValueError("loads: 0 or more kPa.")
    if not design or any(design.get(k) is None for k in ("Fb", "Fv", "E")):
        raise KeyError("design")
    for k in ("Fb", "Fv", "E", "Fc_perp"):
        if design.get(k) is not None and not float(design[k]) > 0:
            raise ValueError(f"design.{k}: above 0 MPa.")
    plies = int(member.get("plies", 1) or 1) if member.get("size") else 1
    if factors.get("Cr") is None:
        # repetitive members: dimension lumber (2x to 4x), three or more at 24 in (610 mm) or less, sharing load
        rep = member.get("repetitive", s <= 610) and b / plies <= DIMENSION_MAX_B
        factors["Cr"] = 1.15 if rep else 1.0
        defaults.append({"fact": "factor.Cr", "value": factors["Cr"], "provenance": "defaulted",
                         "note": "repetitive member: dimension lumber at 610 mm or closer" if rep else "not a repetitive member (a timber, or spaced over 610 mm)"})
    if factors.get("CD") is None:
        defaults.append({"fact": "factor.CD", "value": 1.0, "provenance": "defaulted", "note": "load duration 1.0 (normal) for dead plus live"})
    defaults.append({"fact": "factor.CD (dead only)", "value": CD_DEAD, "provenance": "defaulted", "note": "load duration 0.9 (permanent) for dead load alone"})
    tol = tol or {}
    tL, td = float(tol.get("span", 0) or 0), float(tol.get("d", 0) or 0)
    if tL < 0 or td < 0:
        raise ValueError("tol: plus-minus sizes are 0 or more mm.")
    if d - td <= 0:
        raise ValueError(f"tol.d {td:g} mm takes the depth {d:g} mm to 0 or less: measure the depth again, or give a smaller tolerance.")

    def ratios(L, d, case):
        cd = CD_DEAD if case == "D" else factor(factors, "CD")
        lv = 0.0 if case == "D" else live
        w = (lv + dead) * KPA * s  # N/mm along the member
        wl = lv * KPA * s
        S = b * d * d / 6.0
        I = b * d ** 3 / 12.0
        M = w * L * L / 8.0
        V = w * max(L / 2.0 - d, 0.0)
        R = w * L / 2.0
        Fb = float(design["Fb"]) * cd * factor(factors, "CM") * factor(factors, "Ct") * factor(factors, "CL") * factor(factors, "CF") * factor(factors, "Cfu") * factor(factors, "Ci") * factor(factors, "Cr")
        Fv = float(design["Fv"]) * cd * factor(factors, "CM") * factor(factors, "Ct") * factor(factors, "Ci")
        E = float(design["E"]) * factor(factors, "CM") * factor(factors, "Ct") * factor(factors, "Ci")
        out = {
            "bending": (M / S) / Fb,
            "shear": (1.5 * V / (b * d)) / Fv,
            "deflection_live": (5 * wl * L ** 4 / (384 * E * I)) / (L / limits["live"]) if wl > 0 else 0.0,
            "deflection_total": (5 * w * L ** 4 / (384 * E * I)) / (L / limits["total"]),
        }
        if design.get("Fc_perp"):
            Fc = float(design["Fc_perp"]) * factor(factors, "CM") * factor(factors, "Ct") * factor(factors, "Ci") * factor(factors, "Cb")
            out["bearing"] = (R / (b * lb)) / Fc
        detail = {"case": "D" if case == "D" else "D+L", "CD": cd, "w_N_per_mm": round(w, 4), "M_kNm": round(M / 1e6, 3), "V_kN": round(V / 1e3, 3), "R_kN": round(R / 1e3, 3),
                  "fb_MPa": round(M / S, 2), "Fb_allowed_MPa": round(Fb, 2), "fv_MPa": round(1.5 * V / (b * d), 3), "Fv_allowed_MPa": round(Fv, 3),
                  "deflection_live_mm": round(5 * wl * L ** 4 / (384 * E * I), 2), "deflection_total_mm": round(5 * w * L ** 4 / (384 * E * I), 2),
                  "limit_live_mm": round(L / limits["live"], 2), "limit_total_mm": round(L / limits["total"], 2)}
        return out, detail

    def both(L, d):
        dl, det = ratios(L, d, "DL")
        do, det_d = ratios(L, d, "D")
        worst = {k: max(dl[k], do[k]) for k in dl}
        case = {k: ("D" if do[k] > dl[k] else "D+L") for k in dl}
        return worst, case, det, det_d, dl, do

    nominal, case, detail, detail_d, dl, do = both(L, d)
    hi, *_ = both(L + tL, d - td)
    lo, *_ = both(max(L - tL, 1.0), d + td)
    gov = max(nominal, key=nominal.get)
    detail = {**detail, "dead_only": detail_d}
    return {"b": b, "d": d, "span": L, "spacing": s, "ratios": {k: round(v, 3) for k, v in nominal.items()},
            "governs": gov, "ratio": round(nominal[gov], 3), "governing_case": case[gov],
            "cases": {"D+L": {k: round(v, 3) for k, v in dl.items()}, "D": {k: round(v, 3) for k, v in do.items()}},
            "range": [round(max(lo.values()), 3), round(max(hi.values()), 3)],
            "factors": {k: factors.get(k, 1.0) for k in FACTORS if factors.get(k, 1.0) != 1.0 or k in ("CD", "Cr")},
            "defaults": defaults, "not_checked": list(NOT_CHECKED),
            "detail": detail}


def verdict(res):
    """The state a member's result earns: over 1 fails, under 1 holds, a
    range that crosses 1 is for an engineer."""
    lo, hi = res["range"]
    if lo > 1.0:
        return "non_compliant"
    if hi <= 1.0:
        return "compliant"
    return "manual_review_required"


def imperial(member=None, loads=None, design=None, tol=None):
    """Feet, inches, psf and psi to mm, kPa and MPa (a copy each). With tol
    { span (ft), d (in) } answers a fourth: the tolerances in mm."""
    m = dict(member or {})
    for k in ("span",):
        if k in m and isinstance(m[k], (int, float)):
            m[k] = m[k] * FT
    for k in ("spacing", "bearing", "b", "d"):
        if k in m and isinstance(m[k], (int, float)):
            m[k] = m[k] * IN
    ld = {k: (v * PSF if isinstance(v, (int, float)) else v) for k, v in (loads or {}).items()}
    ds = {k: (v * PSI if isinstance(v, (int, float)) else v) for k, v in (design or {}).items()}
    if tol is None:
        return m, ld, ds
    t = dict(tol)
    if isinstance(t.get("span"), (int, float)):
        t["span"] = t["span"] * FT
    if isinstance(t.get("d"), (int, float)):
        t["d"] = t["d"] * IN
    return m, ld, ds, t
