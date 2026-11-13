# SPDX-License-Identifier: LGPL-2.1-or-later
# Units and plausibility for facts. Every number a check reads is held in
# one unit per kind: lengths in mm, areas in mm², area loads in kPa, design
# (stress) values in MPa. A fact given with a unit is converted; a unit this
# module does not know is refused, never guessed. A value far outside what
# a house could hold (a span of 3.6 "mm", an Fb of 875 "MPa") is not judged:
# it becomes a question — is this in mm? — until it is given with its unit
# or confirmed (confirmed: true).

import math

LENGTH = {"mm": 1.0, "cm": 10.0, "m": 1000.0, "in": 25.4, "inch": 25.4, "inches": 25.4, '"': 25.4,
          "ft": 304.8, "foot": 304.8, "feet": 304.8, "'": 304.8}
AREA = {"mm2": 1.0, "mm²": 1.0, "cm2": 100.0, "cm²": 100.0, "m2": 1e6, "m²": 1e6, "in2": 645.16, "in²": 645.16,
        "sqin": 645.16, "ft2": 92903.04, "ft²": 92903.04, "sqft": 92903.04}
PRESSURE = {"kpa": 1.0, "kn/m2": 1.0, "kn/m²": 1.0, "pa": 1e-3, "n/m2": 1e-3, "psf": 0.04788026, "lb/ft2": 0.04788026}
STRESS = {"mpa": 1.0, "n/mm2": 1.0, "n/mm²": 1.0, "gpa": 1000.0, "kpa": 1e-3, "psi": 0.00689476, "ksi": 6.89476}
DIMENSIONLESS = ("", "-", "1", "ratio", "none")

KINDS = {"length": ("mm", LENGTH), "area": ("mm2", AREA), "pressure": ("kPa", PRESSURE), "stress": ("MPa", STRESS)}

# Plausible ranges in the held unit. Outside: a question, not a judgement.
BANDS = {
    "span": (300, 15000), "spacing": (200, 2500), "length": (100, 30000), "bearing": (25, 600),
    "b": (12, 1000), "d": (25, 2500),
    "riser_height": (100, 250), "tread_depth": (100, 500), "headroom": (1500, 4000), "rise": (100, 6000),
    "sill_height": (0, 2500), "clear_height": (300, 6000), "min_clear_height": (0, 6000), "clear_width": (200, 3000),
    "width": (300, 5000), "thickness": (10, 1000), "lining_mm": (3, 100), "clear_area": (10000, 2e7),
    "design.Fb": (2, 40), "design.Fv": (0.3, 5), "design.E": (3000, 25000), "design.Fc_perp": (1, 20),
}

# The kind of each fact the engine knows by name (others take the kind of
# the unit they are given in, or none).
NAMED = {**{k: "length" for k in ("span", "spacing", "length", "bearing", "b", "d", "riser_height", "tread_depth", "headroom", "rise",
                                   "sill_height", "clear_height", "min_clear_height", "clear_width", "width", "thickness", "lining_mm")},
         "clear_area": "area", "area": "area"}


class UnitError(ValueError):
    pass


def kind_of(name):
    if name.startswith("design."):
        return "stress"
    if name.startswith("load."):
        return "pressure"
    return NAMED.get(name)


def _key(unit):
    return str(unit).strip().lower().replace(" ", "").replace("^2", "2")


def factor_for(name, unit):
    """(kind, factor to the held unit) for a fact's name and the unit it was
    given in; raises UnitError for a unit this engine cannot place."""
    u = _key(unit)
    kind = kind_of(name)
    if kind:
        held, table = KINDS[kind]
        if u in table:
            return kind, table[u]
        raise UnitError(f"{name}: unit {unit!r} is not a {kind} unit this engine knows ({', '.join(sorted(k for k in table if k.isalnum() or '/' in k))}); give it in {held}")
    for k, (held, table) in KINDS.items():
        if k in ("pressure", "stress"):
            continue  # a pressure or a stress only on facts named for one (load.*, design.*)
        if u in table:
            return k, table[u]
    if u in DIMENSIONLESS:
        return None, 1.0
    raise UnitError(f"{name}: unit {unit!r} is not one this engine knows (lengths mm, cm, m, in, ft; areas mm2, m2, in2, ft2; loads kPa, psf; design values MPa, psi)")


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def normalize(name, f):
    """A fact in the held unit: (fact, problem). problem is None, or
    {"kind": "error" | "doubt", "why", "ask"}. The fact keeps what was given
    under "given"."""
    if not isinstance(f, dict) or "value" not in f:
        f = {"value": f, "provenance": "user_supplied"}
    out = dict(f)
    v, unit = f.get("value"), f.get("unit")
    pm = f.get("pm")
    if pm is not None:
        if not _num(pm) or not math.isfinite(pm):
            return out, {"kind": "error", "why": f"{name}: pm (plus-minus) must be a number, not {pm!r}"}
        if pm < 0:
            return out, {"kind": "error", "why": f"{name}: pm (plus-minus) {pm:g} is below 0 — a tolerance is a size, give it as a positive number"}
    if _num(v) and not math.isfinite(v):
        return out, {"kind": "error", "why": f"{name}: {v!r} is not a finite number"}
    if unit not in (None, ""):
        if not _num(v):
            if isinstance(v, dict):
                return out, None  # a profile or table: its unit is its own
            return out, {"kind": "error", "why": f"{name}: a unit ({unit!r}) was given for a value that is not a number ({v!r})"}
        try:
            kind, k = factor_for(name, unit)
        except UnitError as e:
            return out, {"kind": "error", "why": str(e)}
        if kind:
            held = KINDS[kind][0]
            if _key(unit) != _key(held):
                out["given"] = {"value": v, "unit": unit, **({"pm": pm} if pm else {})}
            out["value"] = v * k
            out["unit"] = held
            if pm:
                out["pm"] = pm * k
    band = BANDS.get(name)
    if band and _num(out.get("value")) and not f.get("confirmed"):
        lo, hi = band
        val = out["value"]
        if not lo <= val <= hi:
            kind = kind_of(name)
            held = KINDS[kind][0] if kind else ""
            hint = {"length": "is this in mm? (a value in m, in or ft wants its unit)", "area": "is this in mm²?",
                    "stress": "is this in MPa? (psi values want unit psi)"}.get(kind, "is its unit right?")
            return out, {"kind": "doubt", "why": f"{name} {val:g} {held} is outside the plausible {lo:g}–{hi:g} {held}",
                         "ask": f"{name} = {val:g}: {hint} Give it with its unit, or confirm it (confirmed: true) if it really is {val:g} {held}."}
    return out, None


def normalize_facts(facts):
    """All of a subject's facts: (facts, problems {name: problem})."""
    out, probs = {}, {}
    for name, f in (facts or {}).items():
        out[name], p = normalize(name, f)
        if p:
            probs[name] = p
    return out, probs


def value_in(name, v, want):
    """A pack value ({value, unit} or a bare number) in the held unit for its
    name (load.* in kPa)."""
    if v is None:
        return None
    f, p = normalize(name, v if isinstance(v, dict) else {"value": v})
    if p and p["kind"] == "error":
        raise UnitError(p["why"])
    val = f.get("value")
    return float(val) if _num(val) else None


# Limits named by their unit's suffix: min_height_mm, min_area_m2, max_rise_in.
SUFFIX = {"mm": ("length", 1.0), "cm": ("length", 10.0), "m": ("length", 1000.0), "in": ("length", 25.4), "ft": ("length", 304.8),
          "mm2": ("area", 1.0), "cm2": ("area", 100.0), "m2": ("area", 1e6), "in2": ("area", 645.16), "ft2": ("area", 92903.04),
          "kpa": ("pressure", 1.0), "psf": ("pressure", 0.04788026), "mpa": ("stress", 1.0), "psi": ("stress", 0.00689476)}


def limit_in(param, lim):
    """A rule's limit in the held unit, read from its parameter's suffix."""
    suf = param.rsplit("_", 1)[-1].lower() if "_" in param else ""
    if suf in SUFFIX:
        return lim * SUFFIX[suf][1], KINDS[SUFFIX[suf][0]][0]
    return lim, None
