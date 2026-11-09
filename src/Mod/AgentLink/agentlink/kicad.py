# SPDX-License-Identifier: LGPL-2.1-or-later
# A KiCad board file (.kicad_pcb) read without KiCad: the S-expressions
# parsed here, then the board's outline from Edge.Cuts, its thickness and
# copper layers, its mounting holes and each footprint's place, side and
# courtyard. Written from the file format's published description; no
# FreeCAD needed. Everything comes back in mm with y up (KiCad's y runs
# down the page).

import math
import os
import re


class Sym(str):
    """A bare word of the file (a keyword or a number), as against a quoted
    string."""


def parse(text):
    """The S-expression text as nested lists: words as Sym, quoted strings
    as str. A ValueError says where it breaks."""
    stack, cur = [], []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == "(":
            stack.append(cur)
            cur = []
            i += 1
        elif c == ")":
            if not stack:
                raise ValueError(f"a ')' with no '(' at character {i}.")
            done = cur
            cur = stack.pop()
            cur.append(done)
            i += 1
        elif c.isspace():
            i += 1
        elif c == '"':
            j, buf = i + 1, []
            while j < n and text[j] != '"':
                if text[j] == "\\" and j + 1 < n:
                    j += 1
                    buf.append({"n": "\n", "t": "\t"}.get(text[j], text[j]))
                else:
                    buf.append(text[j])
                j += 1
            if j >= n:
                raise ValueError(f"a string not closed, from character {i}.")
            cur.append("".join(buf))
            i = j + 1
        else:
            j = i
            while j < n and not text[j].isspace() and text[j] not in '()"':
                j += 1
            cur.append(Sym(text[i:j]))
            i = j
    if stack:
        raise ValueError("the file ends inside a list: it is cut short.")
    return cur


def head(node):
    return node[0] if isinstance(node, list) and node and isinstance(node[0], Sym) else None


def kids(node, name):
    return [k for k in node[1:] if head(k) == name]


def kid(node, name):
    for k in node[1:]:
        if head(k) == name:
            return k
    return None


def num(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def xy(node):
    return (num(node[1]), num(node[2])) if node and len(node) >= 3 else (0.0, 0.0)


def layer_of(node):
    lay = kid(node, "layer")
    return str(lay[1]) if lay and len(lay) > 1 else None


# ---- geometry helpers ---------------------------------------------------------------------

def circle3(a, m, b):
    """The centre and radius of the circle through three points, or None."""
    ax, ay = a
    bx, by = m
    cx, cy = b
    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-12:
        return None
    ux = ((ax * ax + ay * ay) * (by - cy) + (bx * bx + by * by) * (cy - ay) + (cx * cx + cy * cy) * (ay - by)) / d
    uy = ((ax * ax + ay * ay) * (cx - bx) + (bx * bx + by * by) * (ax - cx) + (cx * cx + cy * cy) * (bx - ax)) / d
    return (ux, uy), math.hypot(ax - ux, ay - uy)


def arc_points(seg, n=16):
    """Points along a segment: two for a line, n + 1 along an arc, a ring
    for a circle."""
    if seg["type"] == "line":
        return [seg["a"], seg["b"]]
    if seg["type"] == "circle":
        (cx, cy), r = seg["c"], seg["r"]
        return [(cx + r * math.cos(2 * math.pi * i / n), cy + r * math.sin(2 * math.pi * i / n)) for i in range(n + 1)]
    c = circle3(seg["a"], seg["m"], seg["b"])
    if c is None:
        return [seg["a"], seg["b"]]
    (cx, cy), r = c
    t0 = math.atan2(seg["a"][1] - cy, seg["a"][0] - cx)
    tm = math.atan2(seg["m"][1] - cy, seg["m"][0] - cx)
    t1 = math.atan2(seg["b"][1] - cy, seg["b"][0] - cx)

    def ccw(x, y):
        return (y - x) % (2 * math.pi)

    sweep = ccw(t0, t1) if ccw(t0, tm) <= ccw(t0, t1) else -ccw(t1, t0)
    return [(cx + r * math.cos(t0 + sweep * i / n), cy + r * math.sin(t0 + sweep * i / n)) for i in range(n + 1)]


def bbox(points):
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return [min(xs), min(ys), max(xs), max(ys)]


def _transform(at, rot):
    """Footprint-local KiCad coordinates to the board's, y up: KiCad turns a
    footprint counter-clockwise on the page by rot degrees."""
    fx, fy = at
    t = math.radians(rot)
    c, s = math.cos(t), math.sin(t)

    def f(p):
        x, y = p[0], -p[1]
        return (fx + x * c - y * s, -fy + x * s + y * c)

    return f


def _segments(node, tf, prefix):
    """The drawn segments of a graphic item (gr_* or fp_*), y up."""
    h = head(node)
    if h is None or not h.startswith(prefix):
        return []
    kind = h[len(prefix):]

    def p(name):
        k = kid(node, name)
        return tf(xy(k)) if k else None

    if kind == "line":
        return [{"type": "line", "a": p("start"), "b": p("end")}]
    if kind == "arc":
        if kid(node, "mid"):
            return [{"type": "arc", "a": p("start"), "m": p("mid"), "b": p("end")}]
        # KiCad 5: start is the centre, end the arc's first point, angle its sweep.
        c, a0 = p("start"), p("end")
        ang = num(kid(node, "angle")[1]) if kid(node, "angle") else 90.0
        r = math.hypot(a0[0] - c[0], a0[1] - c[1])
        t0 = math.atan2(a0[1] - c[1], a0[0] - c[0])
        # The sweep is clockwise on the y-down page: counter-clockwise with y up.
        tm, t1 = t0 - math.radians(ang) / 2, t0 - math.radians(ang)
        return [{"type": "arc", "a": a0, "m": (c[0] + r * math.cos(tm), c[1] + r * math.sin(tm)), "b": (c[0] + r * math.cos(t1), c[1] + r * math.sin(t1))}]
    if kind == "circle":
        c, e = p("center"), p("end")
        return [{"type": "circle", "c": c, "r": math.hypot(e[0] - c[0], e[1] - c[1])}]
    if kind == "rect":
        a, b = p("start"), p("end")
        q = [a, (b[0], a[1]), b, (a[0], b[1])]
        return [{"type": "line", "a": q[i], "b": q[(i + 1) % 4]} for i in range(4)]
    if kind == "poly":
        pts = kid(node, "pts")
        q = [tf(xy(k)) for k in (pts[1:] if pts else []) if head(k) == "xy"]
        return [{"type": "line", "a": q[i], "b": q[(i + 1) % len(q)]} for i in range(len(q))] if len(q) >= 2 else []
    return []


def loops(segs, tol=0.02):
    """Segments joined end to end into closed loops, each [seg …] in order,
    each segment turned to run on from the last. Circles are loops of
    their own. Ends that meet nothing are given back as open."""
    rings = [[s] for s in segs if s["type"] == "circle"]
    rest = [s for s in segs if s["type"] != "circle"]
    out, open_ = [], []

    def rev(s):
        return {**s, "a": s["b"], "b": s["a"]}

    def near(p, q):
        return abs(p[0] - q[0]) <= tol and abs(p[1] - q[1]) <= tol

    while rest:
        chain = [rest.pop(0)]
        grew = True
        while grew and not near(chain[-1]["b"], chain[0]["a"]):
            grew = False
            for i, s in enumerate(rest):
                if near(s["a"], chain[-1]["b"]):
                    chain.append(rest.pop(i))
                    grew = True
                    break
                if near(s["b"], chain[-1]["b"]):
                    chain.append(rev(rest.pop(i)))
                    grew = True
                    break
        (out if near(chain[-1]["b"], chain[0]["a"]) else open_).append(chain)
    return rings + out, open_


def loop_points(loop):
    pts = []
    for s in loop:
        pts += arc_points(s)
    return pts


def area(points):
    return abs(sum(points[i][0] * points[(i + 1) % len(points)][1] - points[(i + 1) % len(points)][0] * points[i][1] for i in range(len(points)))) / 2


# ---- the board ----------------------------------------------------------------------------

HOLE_NAME = re.compile(r"MountingHole[^:]*?_(\d+(?:\.\d+)?)mm", re.I)


def read_board(text, source=None):
    """What the board is: {thickness, copperLayers, outline: {loops, outer,
    box}, holes, footprints, title}. A ValueError says what is missing."""
    tree = parse(text)
    root = next((n for n in tree if head(n) == "kicad_pcb"), None)
    if root is None:
        raise ValueError("not a KiCad board: no (kicad_pcb …) in it.")
    gen = kid(root, "general")
    th = kid(gen, "thickness") if gen else None
    thickness = num(th[1], 1.6) if th else 1.6
    setup = kid(root, "setup")
    stack = kid(setup, "stackup") if setup else None
    if not th and stack:  # KiCad 6+ may keep it in the stack-up alone
        thickness = sum(num(kid(lay, "thickness")[1]) for lay in kids(stack, "layer") if kid(lay, "thickness")) or 1.6
    layers = kid(root, "layers")
    copper = sorted({str(x[1]) for x in (layers[1:] if layers else []) if isinstance(x, list) and len(x) > 1 and str(x[1]).endswith(".Cu")})
    tb = kid(root, "title_block")
    title = str(kid(tb, "title")[1]) if tb and kid(tb, "title") else None

    ident = _transform((0.0, 0.0), 0.0)
    edge = []
    for n in root[1:]:
        if layer_of(n) == "Edge.Cuts":
            edge += _segments(n, ident, "gr_")
    footprints, holes = [], []
    for fp in root[1:]:
        if head(fp) not in ("footprint", "module"):
            continue
        name = str(fp[1]) if len(fp) > 1 else ""
        at = kid(fp, "at")
        pos = xy(at) if at else (0.0, 0.0)
        rot = num(at[3]) if at and len(at) > 3 else 0.0
        tf = _transform(pos, rot)
        side = "bottom" if (layer_of(fp) or "F.Cu").startswith("B.") else "top"
        props = {str(p[1]): str(p[2]) for p in kids(fp, "property") if len(p) > 2}
        for t in kids(fp, "fp_text"):  # KiCad 5 and 6
            if len(t) > 2 and str(t[1]) in ("reference", "value"):
                props.setdefault(str(t[1]).capitalize(), str(t[2]))
        court, fab = [], []
        for g in fp[1:]:
            lay = layer_of(g) or ""
            segs = _segments(g, tf, "fp_")
            if lay == "Edge.Cuts":
                edge += segs
            elif lay.endswith(".CrtYd"):
                court += segs
            elif lay.endswith(".Fab"):
                fab += segs
        pads, drills = [], []
        for pd in kids(fp, "pad"):
            pat = kid(pd, "at")
            pp = tf(xy(pat)) if pat else tf((0.0, 0.0))
            size = kid(pd, "size")
            sw, sh = (num(size[1]), num(size[2])) if size and len(size) > 2 else (0.0, 0.0)
            dr = kid(pd, "drill")
            dd = num(dr[1]) if dr and len(dr) > 1 and not isinstance(dr[1], list) and str(dr[1]) != "oval" else (num(dr[2]) if dr and len(dr) > 2 else 0.0)
            pads.append({"at": pp, "size": (sw, sh)})
            if dd > 0:
                drills.append((pp, dd, str(pd[2]) if len(pd) > 2 else ""))
        pts = []
        for sgm in court or fab:
            pts += arc_points(sgm, 8)
        if not pts:
            for p in pads:
                hw = max(p["size"]) / 2
                pts += [(p["at"][0] - hw, p["at"][1] - hw), (p["at"][0] + hw, p["at"][1] + hw)]
        model = kid(fp, "model")
        mdl = None
        if model and len(model) > 1:
            sc = kid(model, "scale")
            off = kid(model, "offset")
            mdl = {
                "path": str(model[1]),
                "scale": [num(v, 1.0) for v in kid(sc, "xyz")[1:4]] if sc and kid(sc, "xyz") else [1.0, 1.0, 1.0],
                "offset": [num(v) for v in kid(off, "xyz")[1:4]] if off and kid(off, "xyz") else [0.0, 0.0, 0.0],
            }
        h = props.get("Height")
        entry = {
            "ref": props.get("Reference", ""),
            "value": props.get("Value", ""),
            "footprint": name,
            "side": side,
            "at": [round(tf((0, 0))[0], 4), round(tf((0, 0))[1], 4)],
            "rot": rot,
            "courtyard": [round(v, 4) for v in bbox(pts)] if pts else None,
            "model": mdl,
            "height": num(h, None) if h is not None else None,
        }
        footprints.append(entry)
        m = HOLE_NAME.search(name)
        if m or "mountinghole" in name.lower():
            d = float(m.group(1)) if m else max((dd for (_, dd, _) in drills), default=3.2)
            holes.append({"at": entry["at"], "d": d, "ref": entry["ref"], "kind": "mounting"})
        else:
            for (pp, dd, kind) in drills:
                if kind == "np_thru_hole" and dd >= 2.0:
                    holes.append({"at": [round(pp[0], 4), round(pp[1], 4)], "d": dd, "ref": entry["ref"], "kind": "unplated"})
    if not edge:
        raise ValueError("no board outline: nothing on the Edge.Cuts layer. Draw the outline in KiCad's PCB editor first.")
    closed, open_ = loops(edge)
    if not closed:
        raise ValueError(f"the Edge.Cuts outline does not close: {len(open_)} open run(s) of lines. Close it in KiCad (Inspect › Board Setup › DRC shows the gap).")
    pts = [loop_points(lp) for lp in closed]
    outer = max(range(len(closed)), key=lambda i: area(pts[i]))
    box = bbox(pts[outer])
    return {
        "source": os.path.basename(source) if source else None,
        "title": title,
        "thickness": round(thickness, 4),
        "copperLayers": len(copper) or 2,
        "layers": copper,
        "outline": {"loops": closed, "outer": outer, "box": [round(v, 4) for v in box], "open": len(open_)},
        "size": [round(box[2] - box[0], 3), round(box[3] - box[1], 3)],
        "holes": holes,
        "footprints": footprints,
    }


def edge_connectors(board, near=1.0):
    """Footprints whose courtyard reaches within `near` mm of the board's
    outline box, or over it: each with the side it meets and its span along
    that side, from the board's lower-left corner."""
    x0, y0, x1, y1 = board["outline"]["box"]
    out = []
    for fp in board["footprints"]:
        c = fp.get("courtyard")
        if not c or fp["side"] != "top":
            continue
        sides = []
        if c[1] <= y0 + near:
            sides.append(("front", c[0] - x0, c[2] - x0, y0 - c[1]))
        if c[3] >= y1 - near:
            sides.append(("back", c[0] - x0, c[2] - x0, c[3] - y1))
        if c[0] <= x0 + near:
            sides.append(("left", c[1] - y0, c[3] - y0, x0 - c[0]))
        if c[2] >= x1 - near:
            sides.append(("right", c[1] - y0, c[3] - y0, c[2] - x1))
        if not sides:
            continue
        side = max(sides, key=lambda t: t[3])  # the side it reaches furthest over
        out.append({"ref": fp["ref"], "value": fp["value"], "side": side[0], "from": round(side[1], 3), "to": round(side[2], 3), "over": round(side[3], 3)})
    return out


def screw_for(d):
    """The metric screw a mounting hole of d mm takes."""
    for size, lim in (("M2", 2.5), ("M2.5", 3.0), ("M3", 3.8), ("M4", 99)):
        if d < lim:
            return size
    return "M3"
