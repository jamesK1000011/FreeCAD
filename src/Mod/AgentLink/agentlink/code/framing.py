# SPDX-License-Identifier: LGPL-2.1-or-later
# A wood member checked as an engineer first checks one: a simple span under
# a uniform load from its tributary width, bending, shear (at a depth from
# the support), deflection under live and total load, and bearing — each as
# a ratio of what it sees to what it is allowed (above 1 fails). Allowable
# stresses are the reference design values times their adjustment factors;
# the reference values come from the person (the grade stamp and its table,
# or a supplier's sheet): none are shipped here. Input tolerances give a
# range for each ratio, and a range that crosses 1 is an engineer's call.
# SI throughout: mm, N, MPa (N/mm²), kPa for area loads.

KPA = 1e-3  # kPa in N/mm²

# Nominal sawn-lumber sizes to their dressed (dry) sizes, mm.
DRESSED = {"2x3": (38, 64), "2x4": (38, 89), "2x6": (38, 140), "2x8": (38, 184), "2x10": (38, 235), "2x12": (38, 286),
           "3x4": (64, 89), "3x6": (64, 140), "3x8": (64, 184), "3x10": (64, 235), "3x12": (64, 286),
           "4x4": (89, 89), "4x6": (89, 140), "4x8": (89, 184), "4x10": (89, 235), "4x12": (89, 286),
           "6x6": (140, 140), "6x8": (140, 191), "6x10": (140, 241), "6x12": (140, 292)}
FACTORS = ("CD", "CM", "Ct", "CL", "CF", "Cfu", "Ci", "Cr", "Cb")
IN, FT, PSF, PSI = 25.4, 304.8, 0.04788026, 0.00689476  # to mm, mm, kPa, MPa


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


def check_member(member, loads, design, factors=None, limits=None, tol=None):
    """A simple-span member's ratios. member { size | b, d, span (mm, clear
    between supports), spacing (mm on centre, or tributary width), bearing
    (mm, 38) }; loads { live, dead, snow (kPa) }; design { Fb, Fv, E, Fc_perp
    (MPa, reference) }; factors { CD, CM, Ct, CL, CF, Cfu, Ci, Cr, Cb };
    limits { live: 360, total: 240 } (span over deflection); tol { span,
    d (mm) } plus-minus. Answers ratios, the governing one, and the range."""
    factors = dict(factors or {})
    limits = {"live": 360, "total": 240, **(limits or {})}
    b, d = size_of(member)
    L = float(member["span"])
    s = float(member["spacing"])
    lb = float(member.get("bearing", 38))
    live = float(loads.get("live", 0)) + float(loads.get("snow", 0))
    dead = float(loads.get("dead", 0))
    if L <= 0 or s <= 0:
        raise ValueError("span and spacing: above 0 mm.")
    if not design or any(design.get(k) is None for k in ("Fb", "Fv", "E")):
        raise KeyError("design")
    if factors.get("Cr") is None:
        # repetitive members: three or more at 24 in (610 mm) or less, sharing load
        factors["Cr"] = 1.15 if member.get("repetitive", s <= 610) else 1.0

    def ratios(L, d):
        w = (live + dead) * KPA * s  # N/mm along the member
        wl = live * KPA * s
        S = b * d * d / 6.0
        I = b * d ** 3 / 12.0
        M = w * L * L / 8.0
        V = w * max(L / 2.0 - d, 0.0)
        R = w * L / 2.0
        Fb = float(design["Fb"]) * factor(factors, "CD") * factor(factors, "CM") * factor(factors, "Ct") * factor(factors, "CL") * factor(factors, "CF") * factor(factors, "Cfu") * factor(factors, "Ci") * factor(factors, "Cr")
        Fv = float(design["Fv"]) * factor(factors, "CD") * factor(factors, "CM") * factor(factors, "Ct") * factor(factors, "Ci")
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
        detail = {"w_N_per_mm": round(w, 4), "M_kNm": round(M / 1e6, 3), "V_kN": round(V / 1e3, 3), "R_kN": round(R / 1e3, 3),
                  "fb_MPa": round(M / S, 2), "Fb_allowed_MPa": round(Fb, 2), "fv_MPa": round(1.5 * V / (b * d), 3), "Fv_allowed_MPa": round(Fv, 3),
                  "deflection_live_mm": round(5 * wl * L ** 4 / (384 * E * I), 2), "deflection_total_mm": round(5 * w * L ** 4 / (384 * E * I), 2),
                  "limit_live_mm": round(L / limits["live"], 2), "limit_total_mm": round(L / limits["total"], 2)}
        return out, detail

    nominal, detail = ratios(L, d)
    tol = tol or {}
    tL, td = float(tol.get("span", 0)), float(tol.get("d", 0))
    worst, _ = ratios(L + tL, d - td)
    best, _ = ratios(max(L - tL, 1.0), d + td)
    gov = max(nominal, key=nominal.get)
    return {"b": b, "d": d, "span": L, "spacing": s, "ratios": {k: round(v, 3) for k, v in nominal.items()},
            "governs": gov, "ratio": round(nominal[gov], 3),
            "range": [round(max(best.values()), 3), round(max(worst.values()), 3)],
            "factors": {k: factors.get(k, 1.0) for k in FACTORS if factors.get(k, 1.0) != 1.0 or k in ("CD", "Cr")},
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


def imperial(member=None, loads=None, design=None):
    """Feet, inches, psf and psi to mm, kPa and MPa (a copy each)."""
    m = dict(member or {})
    for k in ("span",):
        if k in m and isinstance(m[k], (int, float)):
            m[k] = m[k] * FT
    for k in ("spacing", "bearing", "b", "d"):
        if k in m and isinstance(m[k], (int, float)):
            m[k] = m[k] * IN
    ld = {k: (v * PSF if isinstance(v, (int, float)) else v) for k, v in (loads or {}).items()}
    ds = {k: (v * PSI if isinstance(v, (int, float)) else v) for k, v in (design or {}).items()}
    return m, ld, ds
