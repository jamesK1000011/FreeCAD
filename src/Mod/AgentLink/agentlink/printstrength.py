# SPDX-License-Identifier: LGPL-2.1-or-later
# What a slicer's settings do to a printed part's strength, as a feasibility
# estimate: the solid shell its walls, tops and bottoms make, the infill
# inside, and from those the share of solid material and rough knock-downs on
# stiffness and strength. The strength study is solved on the solid body; these
# say how far the print falls short of it. Never a sign-off.


def _num(v, default=None):
    if v is None:
        return default
    if isinstance(v, list):
        v = v[0] if v else None
        if v is None:
            return default
    s = str(v).strip().rstrip("%")
    try:
        return float(s)
    except ValueError:
        return default


def settings(eff):
    """The numbers that matter from a project's effective settings (orca3mf
    keys)."""
    nz = _num(eff.get("nozzle_diameter"), 0.4)
    lw = _num(eff.get("line_width"), 0) or nz * 1.125

    def width(k):
        v = eff.get(k)
        if v is None:
            return lw
        s = str(v[0] if isinstance(v, list) else v)
        if s.endswith("%"):
            return nz * float(s[:-1]) / 100
        f = _num(s, 0)
        return f or lw

    return {
        "layerHeight": _num(eff.get("layer_height"), 0.2),
        "walls": int(_num(eff.get("wall_loops"), 2)),
        "outerWidth": width("outer_wall_line_width"),
        "innerWidth": width("inner_wall_line_width"),
        "top": int(_num(eff.get("top_shell_layers"), 4)),
        "bottom": int(_num(eff.get("bottom_shell_layers"), 3)),
        "infill": _num(eff.get("sparse_infill_density"), 15) / 100.0,
        "pattern": str(eff.get("sparse_infill_pattern") or "grid"),
        "filament": eff.get("filament"),
    }


# How an infill pattern carries load for its density, relative to the
# Gibson-Ashby power law (1 = bending-dominated foam; 3D patterns do better).
PATTERN = {"gyroid": 1.15, "cubic": 1.1, "adaptivecubic": 1.1, "quartercubic": 1.1, "3dhoneycomb": 1.1, "tpmsd": 1.15, "tpmsfk": 1.15,
           "honeycomb": 1.05, "grid": 1.0, "triangles": 1.05, "tri-hexagon": 1.05, "rectilinear": 0.85, "alignedrectilinear": 0.85,
           "line": 0.7, "zigzag": 0.85, "crosszag": 0.9, "lockedzag": 0.95, "lightning": 0.2, "concentric": 0.8}


def estimate(st, volume, area, least):
    """st from settings(); volume (mm³), surface area (mm²) and least size
    (mm) of the body. Returns the shell thickness, the solid share and the
    stiffness and strength factors (1 = a solid part)."""
    side = st["outerWidth"] + max(0, st["walls"] - 1) * st["innerWidth"] if st["walls"] else 0.0
    caps = (st["top"] + st["bottom"]) / 2 * st["layerHeight"]
    t = min(least / 2, (side * 0.7 + caps * 0.3))  # walls take most of the skin; tops and bottoms the rest
    shell = min(volume, area * t) if volume else 0.0
    core = max(0.0, volume - shell)
    rho = st["infill"]
    k = PATTERN.get(st["pattern"], 1.0)
    phi = (shell + rho * core) / volume if volume else 1.0
    # The skin carries bending and axial load at its full stiffness; the core
    # as a cellular solid (Gibson-Ashby: E ~ rho^2, strength ~ rho^1.5).
    core_e = min(1.0, k * rho ** 2)
    core_s = min(1.0, k * rho ** 1.5)
    shell_share = shell / volume if volume else 1.0
    stiff = shell_share + (1 - shell_share) * core_e
    strong = shell_share + (1 - shell_share) * core_s
    # Thin parts are all shell.
    if t >= least / 2:
        stiff = strong = 1.0
    return {"shellMm": round(t, 2), "solidShare": round(phi, 3), "stiffnessFactor": round(max(0.05, stiff), 3), "strengthFactor": round(max(0.05, strong), 3),
            "layerFactor": round(layer_factor(st["layerHeight"], st["outerWidth"]), 2)}


def layer_factor(lh, width):
    """Across-layer strength as a share of along-layer: lower for tall
    layers on narrow lines (less bonded area per layer)."""
    r = lh / max(width, 1e-6)
    return max(0.3, min(0.7, 0.75 - 0.5 * r))


def advice(st, est):
    """What would make the print stronger, from what is weak."""
    out = []
    if est["strengthFactor"] < 0.6:
        if st["walls"] < 4:
            out.append(f"wall_loops {st['walls']} → {st['walls'] + 2}: the skin carries most of the load")
        if st["infill"] < 0.4:
            out.append(f"sparse_infill_density {round(st['infill'] * 100)}% → {min(100, round(st['infill'] * 200))}%")
        if PATTERN.get(st["pattern"], 1) < 1:
            out.append(f"sparse_infill_pattern {st['pattern']} → gyroid or cubic (load in every direction)")
    if est["layerFactor"] < 0.5:
        out.append(f"layer_height {st['layerHeight']} → {round(st['layerHeight'] * 0.75, 2)}: better bonded layers")
    return out


def describe(eff, shape):
    st = settings(eff)
    bb = shape.BoundBox
    est = estimate(st, shape.Volume, shape.Area, min(bb.XLength, bb.YLength, bb.ZLength))
    return {"settings": st, **est, "advice": advice(st, est),
            "note": "Estimates for feasibility: the skin from walls, tops and bottoms; the core as a cellular solid by its infill. Test prints decide."}

