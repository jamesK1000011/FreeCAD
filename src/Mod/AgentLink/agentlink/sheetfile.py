# SPDX-License-Identifier: LGPL-2.1-or-later
# Flat profiles as files a cutter takes: DXF (release 12, written here line
# by line) and SVG, from plain primitives in millimetres, and the DXF read
# back to count what it holds. No FreeCAD here: the cut tools turn edges
# into these primitives.
#
# A primitive is one of
#   ("line", x0, y0, x1, y1)
#   ("arc", cx, cy, r, a0, a1)        counter-clockwise from a0 to a1, degrees
#   ("circle", cx, cy, r)
#   ("poly", [(x, y), ...], closed)   a curve that is neither, as points
# each with a layer: OUTER (the profile's outside) or HOLES.

import math

OUTER = "OUTER"
HOLES = "HOLES"


def _pair(code, value):
    return f"{code:>3}\n{value}\n"


def _num(v):
    s = f"{float(v):.6f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def dxf(items, extent=None):
    """The DXF text for [(layer, primitive)]. Release 12 (AC1009): LINE,
    ARC, CIRCLE, and POLYLINE with VERTEX and SEQEND — R12's polyline; the
    lightweight polyline came with R14 and older readers refuse it."""
    out = [_pair(0, "SECTION"), _pair(2, "HEADER"), _pair(9, "$ACADVER"), _pair(1, "AC1009"), _pair(9, "$INSUNITS"), _pair(70, 4)]
    if extent:
        out += [_pair(9, "$EXTMIN"), _pair(10, _num(extent[0])), _pair(20, _num(extent[1])), _pair(9, "$EXTMAX"), _pair(10, _num(extent[2])), _pair(20, _num(extent[3]))]
    out += [_pair(0, "ENDSEC")]
    out += [_pair(0, "SECTION"), _pair(2, "TABLES"), _pair(0, "TABLE"), _pair(2, "LAYER"), _pair(70, 2)]
    for name, colour in ((OUTER, 7), (HOLES, 1)):
        out += [_pair(0, "LAYER"), _pair(2, name), _pair(70, 0), _pair(62, colour), _pair(6, "CONTINUOUS")]
    out += [_pair(0, "ENDTAB"), _pair(0, "ENDSEC")]
    out += [_pair(0, "SECTION"), _pair(2, "ENTITIES")]
    for layer, p in items:
        kind = p[0]
        if kind == "line":
            out += [_pair(0, "LINE"), _pair(8, layer), _pair(10, _num(p[1])), _pair(20, _num(p[2])), _pair(30, 0), _pair(11, _num(p[3])), _pair(21, _num(p[4])), _pair(31, 0)]
        elif kind == "arc":
            out += [_pair(0, "ARC"), _pair(8, layer), _pair(10, _num(p[1])), _pair(20, _num(p[2])), _pair(30, 0), _pair(40, _num(p[3])), _pair(50, _num(p[4] % 360)), _pair(51, _num(p[5] % 360))]
        elif kind == "circle":
            out += [_pair(0, "CIRCLE"), _pair(8, layer), _pair(10, _num(p[1])), _pair(20, _num(p[2])), _pair(30, 0), _pair(40, _num(p[3]))]
        elif kind == "poly":
            out += [_pair(0, "POLYLINE"), _pair(8, layer), _pair(66, 1), _pair(10, 0), _pair(20, 0), _pair(30, 0), _pair(70, 1 if p[2] else 0)]
            for x, y in p[1]:
                out += [_pair(0, "VERTEX"), _pair(8, layer), _pair(10, _num(x)), _pair(20, _num(y)), _pair(30, 0)]
            out += [_pair(0, "SEQEND"), _pair(8, layer)]
        else:
            raise ValueError(f"not a primitive: {kind}")
    out += [_pair(0, "ENDSEC"), _pair(0, "EOF")]
    return "".join(out)


def read_dxf(text):
    """What a DXF's ENTITIES section holds: { LINE: n, ARC: n, … } and per
    layer, read pair by pair."""
    lines = text.splitlines()
    pairs = [(lines[i].strip(), lines[i + 1].strip() if i + 1 < len(lines) else "") for i in range(0, len(lines) - 1, 2)]
    counts, layers = {}, {}
    in_ent = False
    current = None
    for i, (code, val) in enumerate(pairs):
        if code == "2" and i > 0 and pairs[i - 1] == ("0", "SECTION"):
            in_ent = val == "ENTITIES"
            continue
        if not in_ent:
            continue
        if code == "0":
            current = val if val in ("LINE", "ARC", "CIRCLE", "POLYLINE", "LWPOLYLINE") else None
            if current:
                counts[current] = counts.get(current, 0) + 1
        elif code == "8" and current:
            layers[val] = layers.get(val, 0) + 1
    return {"entities": counts, "layers": layers}


def svg(items, width, height, stroke=0.1):
    """An SVG in millimetres, y up as the model has it (flipped into SVG's
    y down). The outside black, holes red, nothing filled."""
    H = height

    def y(v):
        return _num(H - v)

    colour = {OUTER: "#000", HOLES: "#d00"}
    out = [
        '<?xml version="1.0" encoding="UTF-8"?>\n',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{_num(width)}mm" height="{_num(height)}mm" viewBox="0 0 {_num(width)} {_num(height)}">\n',
        f'<g fill="none" stroke-width="{_num(stroke)}">\n',
    ]
    for layer, p in items:
        c = colour.get(layer, "#000")
        kind = p[0]
        if kind == "line":
            out.append(f'<line class="{layer}" stroke="{c}" x1="{_num(p[1])}" y1="{y(p[2])}" x2="{_num(p[3])}" y2="{y(p[4])}"/>\n')
        elif kind == "circle":
            out.append(f'<circle class="{layer}" stroke="{c}" cx="{_num(p[1])}" cy="{y(p[2])}" r="{_num(p[3])}"/>\n')
        elif kind == "arc":
            cx, cy, r, a0, a1 = p[1:]
            sweep = (a1 - a0) % 360 or 360
            x0, y0 = cx + r * math.cos(math.radians(a0)), cy + r * math.sin(math.radians(a0))
            x1, y1 = cx + r * math.cos(math.radians(a1)), cy + r * math.sin(math.radians(a1))
            large = 1 if sweep > 180 else 0
            # Counter-clockwise with y up is the negative-angle sweep once y is flipped.
            out.append(f'<path class="{layer}" stroke="{c}" d="M {_num(x0)} {y(y0)} A {_num(r)} {_num(r)} 0 {large} 0 {_num(x1)} {y(y1)}"/>\n')
        elif kind == "poly":
            pts = " ".join(f"{_num(a)},{y(b)}" for a, b in p[1])
            tag = "polygon" if p[2] else "polyline"
            out.append(f'<{tag} class="{layer}" stroke="{c}" points="{pts}"/>\n')
    out.append("</g>\n</svg>\n")
    return "".join(out)


def read_svg(text):
    import re

    return {k: len(re.findall(rf"<{k}\b", text)) for k in ("line", "circle", "path", "polyline", "polygon")}


def shift(p, dx, dy):
    """A primitive moved by (dx, dy)."""
    k = p[0]
    if k == "line":
        return ("line", p[1] + dx, p[2] + dy, p[3] + dx, p[4] + dy)
    if k == "arc":
        return ("arc", p[1] + dx, p[2] + dy, p[3], p[4], p[5])
    if k == "circle":
        return ("circle", p[1] + dx, p[2] + dy, p[3])
    return ("poly", [(x + dx, y + dy) for x, y in p[1]], p[2])


def shelf(sizes, sheet_w, sheet_h, gap):
    """Rectangles (w, h) laid in rows from the sheet's lower left corner, a
    gap between them and from the edge: their lower left corners, or None
    for those that do not fit. Without a sheet (None), one long row."""
    at = []
    x = y = gap
    row_h = 0.0
    for w, h in sizes:
        if sheet_w is not None and x + w + gap > sheet_w + 1e-9 and x > gap:
            x = gap
            y += row_h + gap
            row_h = 0.0
        fits = sheet_w is None or (x + w + gap <= sheet_w + 1e-9 and y + h + gap <= sheet_h + 1e-9)
        if not fits:
            at.append(None)
            continue
        at.append((x, y))
        x += w + gap
        row_h = max(row_h, h)
    return at


# ---- extent and kerf offset, on the primitives ---------------------------------------------

def _arc_pt(cx, cy, r, a):
    return cx + r * math.cos(math.radians(a)), cy + r * math.sin(math.radians(a))


def bbox(prims):
    """(xmin, ymin, xmax, ymax) of primitives (no layers)."""
    xs, ys = [], []
    for p in prims:
        k = p[0]
        if k == "line":
            xs += [p[1], p[3]]
            ys += [p[2], p[4]]
        elif k == "circle":
            xs += [p[1] - p[3], p[1] + p[3]]
            ys += [p[2] - p[3], p[2] + p[3]]
        elif k == "arc":
            cx, cy, r, a0, a1 = p[1:]
            sweep = (a1 - a0) % 360 or 360
            angs = [a0, a0 + sweep] + [q for q in (0, 90, 180, 270, 360, 450, 540, 630) if a0 < q < a0 + sweep or a0 < q + 360 < a0 + sweep]
            for a in angs:
                x, y = _arc_pt(cx, cy, r, a)
                xs.append(x)
                ys.append(y)
        else:
            xs += [x for x, _ in p[1]]
            ys += [y for _, y in p[1]]
    return min(xs), min(ys), max(xs), max(ys)


def _ends(p):
    if p[0] == "line":
        return (p[1], p[2]), (p[3], p[4])
    cx, cy, r, a0, a1 = p[1:]
    return _arc_pt(cx, cy, r, a0), _arc_pt(cx, cy, r, a1)


def _close(a, b, tol=1e-4):
    return abs(a[0] - b[0]) <= tol and abs(a[1] - b[1]) <= tol


def chain(prims):
    """Lines and arcs of one closed loop, in order: [(prim, forward)] where
    forward says it is walked from its first end (an arc's a0) to its last."""
    left = list(prims)
    out = [(left.pop(0), True)]
    start = _ends(out[0][0])[0]
    while left:
        end = _ends(out[-1][0])[1 if out[-1][1] else 0]
        for i, p in enumerate(left):
            a, b = _ends(p)
            if _close(a, end):
                out.append((left.pop(i), True))
                break
            if _close(b, end):
                out.append((left.pop(i), False))
                break
        else:
            raise ValueError("the loop is not closed")
    if not _close(_ends(out[-1][0])[1 if out[-1][1] else 0], start):
        raise ValueError("the loop is not closed")
    return out


def _walk_dir(p, fwd, at_end):
    """The unit direction of travel at a primitive's start or end."""
    if p[0] == "line":
        dx, dy = p[3] - p[1], p[4] - p[2]
        n = math.hypot(dx, dy)
        dx, dy = dx / n, dy / n
        return (dx, dy) if fwd else (-dx, -dy)
    cx, cy, r, a0, a1 = p[1:]
    a = (a1 if at_end else a0) if fwd else (a0 if at_end else a1)
    tx, ty = -math.sin(math.radians(a)), math.cos(math.radians(a))  # counter-clockwise
    return (tx, ty) if fwd else (-tx, -ty)


def offset_loop(prims, d):
    """A closed loop of lines and arcs (or one circle) offset by d: d > 0
    grows the area it encloses, d < 0 shrinks it. Sharp corners between
    lines are mitred; an arc must meet its neighbours tangent."""
    if len(prims) == 1 and prims[0][0] == "circle":
        p = prims[0]
        if p[3] + d <= 1e-6:
            raise ValueError(f"a hole Ø{2 * p[3]:.2f} closes at this kerf")
        return [("circle", p[1], p[2], p[3] + d)]
    if any(p[0] not in ("line", "arc") for p in prims):
        raise ValueError("only lines and arcs are offset here (a spline is in the profile)")
    loop = chain(prims)
    pts = []
    for p, fwd in loop:
        a, b = _ends(p)
        pts.append(a if fwd else b)
        if p[0] == "arc":
            cx, cy, r, a0, a1 = p[1:]
            pts.append(_arc_pt(cx, cy, r, a0 + ((a1 - a0) % 360 or 360) / 2))
    area = sum(pts[i][0] * pts[(i + 1) % len(pts)][1] - pts[(i + 1) % len(pts)][0] * pts[i][1] for i in range(len(pts))) / 2
    if area < 0:  # walk it counter-clockwise: the inside on the left
        loop = [(p, not f) for p, f in reversed(loop)]
    n = len(loop)
    # Each line moved to its right (out) by d, each arc's radius changed.
    moved = []
    for p, fwd in loop:
        if p[0] == "line":
            tx, ty = _walk_dir(p, fwd, False)
            ox, oy = ty * d, -tx * d
            a, b = _ends(p)
            if not fwd:
                a, b = b, a
            moved.append(["line", [a[0] + ox, a[1] + oy], [b[0] + ox, b[1] + oy]])
        else:
            cx, cy, r, a0, a1 = p[1:]
            r2 = r + d if fwd else r - d  # walked counter-clockwise: bulges out
            if r2 <= 1e-6:
                raise ValueError(f"an arc of radius {r:.2f} closes at this kerf")
            moved.append(["arc", cx, cy, r2, a0, a1])
    for i in range(n):
        j = (i + 1) % n
        (p, f), (q, g) = loop[i], loop[j]
        t1, t2 = _walk_dir(p, f, True), _walk_dir(q, g, False)
        cross = t1[0] * t2[1] - t1[1] * t2[0]
        if abs(cross) < 1e-6 and t1[0] * t2[0] + t1[1] * t2[1] > 0:
            continue  # tangent: the moved ends meet already
        if p[0] != "line" or q[0] != "line":
            raise ValueError("an arc meets a neighbour at a sharp corner")
        (a1, b1), (a2, b2) = moved[i][1:], moved[j][1:]
        # Where the two moved lines cross: the mitred corner.
        den = (b1[0] - a1[0]) * (b2[1] - a2[1]) - (b1[1] - a1[1]) * (b2[0] - a2[0])
        s = ((a2[0] - a1[0]) * (b2[1] - a2[1]) - (a2[1] - a1[1]) * (b2[0] - a2[0])) / den
        x, y = a1[0] + s * (b1[0] - a1[0]), a1[1] + s * (b1[1] - a1[1])
        moved[i][2] = [x, y]
        moved[j][1] = [x, y]
    out = []
    for m in moved:
        if m[0] == "line":
            out.append(("line", m[1][0], m[1][1], m[2][0], m[2][1]))
        else:
            out.append(tuple(m))
    return out
