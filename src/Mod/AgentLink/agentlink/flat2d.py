# SPDX-License-Identifier: LGPL-2.1-or-later
# 2D drawings written without FreeCAD: lines, polylines, circles and arcs on
# named layers, as DXF (R12 ASCII, read by every CAD program and laser or
# plasma cutter) or SVG (mm, Y up turned to SVG's Y down). Pure Python.

import math

# item: {"kind": "line", "points": [[x, y], …], "closed": bool, "layer": str}
#     | {"kind": "circle", "c": [x, y], "r": r, "layer": str}
#     | {"kind": "arc", "c": [x, y], "r": r, "a0": deg, "a1": deg, "layer": str}  (counter-clockwise a0 → a1)

LAYERS = {"visible": 7, "hidden": 8, "outline": 7, "cut": 1, "engrave": 5}


def bounds(items):
    xs, ys = [], []
    for it in items:
        if it["kind"] == "line":
            xs += [p[0] for p in it["points"]]
            ys += [p[1] for p in it["points"]]
        else:
            (cx, cy), r = it["c"], it["r"]
            xs += [cx - r, cx + r]
            ys += [cy - r, cy + r]
    if not xs:
        return 0.0, 0.0, 0.0, 0.0
    return min(xs), min(ys), max(xs), max(ys)


def _g(code, val):
    if isinstance(val, float):
        val = f"{val:.6f}".rstrip("0").rstrip(".") if abs(val) > 1e-12 else "0"
    return f"{code:>3}\n{val}\n"


def dxf(items):
    """DXF R12 text: one LAYER table entry per layer used, entities after."""
    layers = sorted({it.get("layer", "visible") for it in items})
    out = [_g(0, "SECTION"), _g(2, "HEADER"), _g(9, "$INSUNITS"), _g(70, 4), _g(9, "$MEASUREMENT"), _g(70, 1), _g(0, "ENDSEC")]
    out += [_g(0, "SECTION"), _g(2, "TABLES"), _g(0, "TABLE"), _g(2, "LTYPE"), _g(70, 2)]
    out += [_g(0, "LTYPE"), _g(2, "CONTINUOUS"), _g(70, 0), _g(3, "Solid line"), _g(72, 65), _g(73, 0), _g(40, 0.0)]
    out += [_g(0, "LTYPE"), _g(2, "DASHED"), _g(70, 0), _g(3, "__ __ __"), _g(72, 65), _g(73, 2), _g(40, 4.5), _g(49, 3.0), _g(49, -1.5)]
    out += [_g(0, "ENDTAB"), _g(0, "TABLE"), _g(2, "LAYER"), _g(70, len(layers))]
    for name in layers:
        out += [_g(0, "LAYER"), _g(2, name.upper()), _g(70, 0), _g(62, LAYERS.get(name, 7)), _g(6, "DASHED" if name == "hidden" else "CONTINUOUS")]
    out += [_g(0, "ENDTAB"), _g(0, "ENDSEC"), _g(0, "SECTION"), _g(2, "ENTITIES")]
    for it in items:
        lay = it.get("layer", "visible").upper()
        k = it["kind"]
        if k == "circle":
            (cx, cy), r = it["c"], it["r"]
            out += [_g(0, "CIRCLE"), _g(8, lay), _g(10, float(cx)), _g(20, float(cy)), _g(30, 0.0), _g(40, float(r))]
        elif k == "arc":
            (cx, cy), r = it["c"], it["r"]
            out += [_g(0, "ARC"), _g(8, lay), _g(10, float(cx)), _g(20, float(cy)), _g(30, 0.0), _g(40, float(r)),
                    _g(50, float(it["a0"]) % 360), _g(51, float(it["a1"]) % 360)]
        else:
            pts = it["points"]
            if len(pts) == 2 and not it.get("closed"):
                (x0, y0), (x1, y1) = pts
                out += [_g(0, "LINE"), _g(8, lay), _g(10, float(x0)), _g(20, float(y0)), _g(30, 0.0), _g(11, float(x1)), _g(21, float(y1)), _g(31, 0.0)]
                continue
            out += [_g(0, "POLYLINE"), _g(8, lay), _g(66, 1), _g(10, 0.0), _g(20, 0.0), _g(30, 0.0), _g(70, 1 if it.get("closed") else 0)]
            for x, y in pts:
                out += [_g(0, "VERTEX"), _g(8, lay), _g(10, float(x)), _g(20, float(y)), _g(30, 0.0)]
            out += [_g(0, "SEQEND"), _g(8, lay)]
    out += [_g(0, "ENDSEC"), _g(0, "EOF")]
    return "".join(out)


def svg(items, margin=5.0, stroke=0.35):
    """SVG in mm, the drawing's lowest-left corner at the margin."""
    x0, y0, x1, y1 = bounds(items)
    w, h = (x1 - x0) + 2 * margin, (y1 - y0) + 2 * margin

    def X(x):
        return x - x0 + margin

    def Y(y):
        return y1 - y + margin

    groups = {}
    for it in items:
        k = it["kind"]
        if k == "circle":
            (cx, cy), r = it["c"], it["r"]
            el = f'<circle cx="{X(cx):.4f}" cy="{Y(cy):.4f}" r="{r:.4f}"/>'
        elif k == "arc":
            (cx, cy), r = it["c"], it["r"]
            a0, a1 = math.radians(it["a0"]), math.radians(it["a1"])
            span = (it["a1"] - it["a0"]) % 360
            sx, sy = cx + r * math.cos(a0), cy + r * math.sin(a0)
            ex, ey = cx + r * math.cos(a1), cy + r * math.sin(a1)
            # Counter-clockwise in Y up is clockwise once Y points down: sweep 0.
            el = f'<path d="M {X(sx):.4f} {Y(sy):.4f} A {r:.4f} {r:.4f} 0 {1 if span > 180 else 0} 0 {X(ex):.4f} {Y(ey):.4f}"/>'
        else:
            pts = " ".join(f"{X(x):.4f},{Y(y):.4f}" for x, y in it["points"])
            el = f'<{"polygon" if it.get("closed") else "polyline"} points="{pts}"/>'
        groups.setdefault(it.get("layer", "visible"), []).append(el)
    body = []
    for name, els in sorted(groups.items()):
        dash = ' stroke-dasharray="3 1.5"' if name == "hidden" else ""
        colour = "#888" if name == "hidden" else "#000"
        body.append(f'<g id="{name}" fill="none" stroke="{colour}" stroke-width="{stroke}"{dash} stroke-linecap="round" stroke-linejoin="round">{"".join(els)}</g>')
    return (f'<?xml version="1.0" encoding="UTF-8"?>\n<svg xmlns="http://www.w3.org/2000/svg" width="{w:.3f}mm" height="{h:.3f}mm" '
            f'viewBox="0 0 {w:.3f} {h:.3f}">{"".join(body)}</svg>\n')


def read_dxf_counts(text):
    """Entities by type in a DXF's ENTITIES section — for checking a file read back."""
    lines = [s.strip() for s in text.splitlines()]
    counts, inside = {}, False
    for i in range(0, len(lines) - 1, 2):
        code, val = lines[i], lines[i + 1]
        if code == "2" and val == "ENTITIES":
            inside = True
        elif code == "0" and val == "ENDSEC":
            inside = False
        elif inside and code == "0" and val not in ("VERTEX", "SEQEND"):
            counts[val] = counts.get(val, 0) + 1
    return counts
