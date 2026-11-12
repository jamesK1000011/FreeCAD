# SPDX-License-Identifier: LGPL-2.1-or-later
# Geometric tolerancing marks as drawn (ISO 1101 / ASME Y14.5): the feature
# control frame and the datum feature symbol, built from plain lines,
# circles and text in millimetres (origin top left, y down), so the same
# figure goes into a TechDraw symbol (as SVG) and onto an exported sheet.
# The characteristic symbols are drawn, not typed: a font need not have
# them. No FreeCAD here.

import math

# characteristic: (needs a datum, may take a datum, may be ⌀ zone)
CHARACTERISTICS = {
    "straightness": (False, False, True),
    "flatness": (False, False, False),
    "circularity": (False, False, False),
    "cylindricity": (False, False, False),
    "profile_line": (False, True, False),
    "profile_surface": (False, True, False),
    "angularity": (True, True, True),
    "perpendicularity": (True, True, True),
    "parallelism": (True, True, True),
    "position": (False, True, True),
    "concentricity": (True, True, True),
    "symmetry": (True, True, False),
    "circular_runout": (True, True, False),
    "total_runout": (True, True, False),
}
MODIFIERS = {"M": "M", "L": "L", "S": "S", "F": "F", "P": "P", "T": "T", "U": "U", "E": "E"}
CIRCLED = ("M", "L", "S", "F", "P", "T", "U", "E")
LETTER_W = 0.62  # a capital's width per unit of text height, Helvetica-ish


class GdtError(ValueError):
    pass


class Figure:
    """Lines [(pts, closed)], circles [(cx, cy, r)], texts [(x, y, h, s,
    anchor)] — anchor start | middle; y is the baseline."""

    def __init__(self):
        self.lines, self.circles, self.texts = [], [], []
        self.filled = []  # closed polygons drawn solid
        self.w = self.h = 0.0

    def line(self, pts, closed=False):
        self.lines.append(([(float(x), float(y)) for x, y in pts], closed))

    def circle(self, cx, cy, r):
        self.circles.append((float(cx), float(cy), float(r)))

    def text(self, x, y, h, s, anchor="start"):
        self.texts.append((float(x), float(y), float(h), str(s), anchor))

    def svg(self, stroke=0.25):
        out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w:.3f}mm" height="{self.h:.3f}mm" viewBox="0 0 {self.w:.3f} {self.h:.3f}">',
               f'<g fill="none" stroke="black" stroke-width="{stroke:g}" stroke-linecap="round" stroke-linejoin="round">']
        for pts, closed in self.lines:
            out.append('<path d="M' + " L".join(f"{x:.3f},{y:.3f}" for x, y in pts) + (" Z" if closed else "") + '"/>')
        for cx, cy, r in self.circles:
            out.append(f'<circle cx="{cx:.3f}" cy="{cy:.3f}" r="{r:.3f}"/>')
        for pts in self.filled:
            out.append('<path fill="black" d="M' + " L".join(f"{x:.3f},{y:.3f}" for x, y in pts) + ' Z"/>')
        out.append("</g>")
        out.append('<g font-family="Helvetica, Arial, sans-serif" fill="black" stroke="none">')
        for x, y, h, s, anchor in self.texts:
            out.append(f'<text x="{x:.3f}" y="{y:.3f}" font-size="{h:.3f}" text-anchor="{anchor}">{_esc(s)}</text>')
        out.append("</g></svg>")
        return "\n".join(out)

    def polylines(self, n=24):
        """Everything as (pts, closed, filled) polylines, circles split."""
        out = [(p, c, False) for p, c in self.lines]
        for cx, cy, r in self.circles:
            out.append(([(cx + r * math.cos(2 * math.pi * i / n), cy + r * math.sin(2 * math.pi * i / n)) for i in range(n)], True, False))
        out += [(p, True, True) for p in self.filled]
        return out


def _esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def text_w(s, h):
    return len(s) * h * LETTER_W


def _symbol(f, kind, x0, y0, c):
    """Draw characteristic `kind` in the cell of width/height c at (x0, y0)."""
    cx, cy, r = x0 + c / 2, y0 + c / 2, c * 0.3
    if kind == "straightness":
        f.line([(cx - r, cy), (cx + r, cy)])
    elif kind == "flatness":
        f.line([(cx - r, cy + r * 0.45), (cx - r * 0.45, cy - r * 0.45), (cx + r, cy - r * 0.45), (cx + r * 0.45, cy + r * 0.45)], closed=True)
    elif kind == "circularity":
        f.circle(cx, cy, r)
    elif kind == "cylindricity":
        f.circle(cx, cy, r * 0.75)
        f.line([(cx - r * 1.05, cy + r * 0.8), (cx - r * 0.15, cy - r * 0.8)])
        f.line([(cx + r * 0.15, cy + r * 0.8), (cx + r * 1.05, cy - r * 0.8)])
    elif kind in ("profile_line", "profile_surface"):
        n = 16
        f.line([(cx + r * math.cos(math.pi * i / n), cy + r * 0.4 - r * math.sin(math.pi * i / n)) for i in range(n + 1)], closed=kind == "profile_surface")
    elif kind == "angularity":
        f.line([(cx + r, cy + r * 0.7), (cx - r, cy + r * 0.7), (cx + r * 0.6, cy - r * 0.8)])
    elif kind == "perpendicularity":
        f.line([(cx - r, cy + r * 0.7), (cx + r, cy + r * 0.7)])
        f.line([(cx, cy + r * 0.7), (cx, cy - r * 0.9)])
    elif kind == "parallelism":
        f.line([(cx - r * 0.75, cy + r * 0.8), (cx - r * 0.15, cy - r * 0.8)])
        f.line([(cx + r * 0.15, cy + r * 0.8), (cx + r * 0.75, cy - r * 0.8)])
    elif kind == "position":
        f.circle(cx, cy, r * 0.6)
        f.line([(cx - r, cy), (cx + r, cy)])
        f.line([(cx, cy - r), (cx, cy + r)])
    elif kind == "concentricity":
        f.circle(cx, cy, r * 0.45)
        f.circle(cx, cy, r * 0.9)
    elif kind == "symmetry":
        f.line([(cx - r * 0.5, cy - r * 0.55), (cx + r * 0.5, cy - r * 0.55)])
        f.line([(cx - r, cy), (cx + r, cy)])
        f.line([(cx - r * 0.5, cy + r * 0.55), (cx + r * 0.5, cy + r * 0.55)])
    elif kind in ("circular_runout", "total_runout"):
        # an arrow up and to the right (two arrows for total runout)
        def arrow(dx):
            a, b = (cx - r * 0.6 + dx, cy + r * 0.8), (cx + r * 0.5 + dx, cy - r * 0.8)
            f.line([a, b])
            ux, uy = b[0] - a[0], b[1] - a[1]
            n = math.hypot(ux, uy)
            ux, uy = ux / n, uy / n
            s = r * 0.45
            f.filled.append([b, (b[0] - ux * s - uy * s * 0.35, b[1] - uy * s + ux * s * 0.35), (b[0] - ux * s + uy * s * 0.35, b[1] - uy * s - ux * s * 0.35)])
        if kind == "circular_runout":
            arrow(0.0)
        else:
            arrow(-r * 0.35)
            arrow(r * 0.35)
            f.line([(cx - r * 1.0, cy + r * 0.8), (cx + r * 0.3, cy + r * 0.8)])
    else:
        raise GdtError(f"characteristic {kind!r}: one of {', '.join(CHARACTERISTICS)}")


def _diameter(f, x, cy, h):
    r = h * 0.38
    f.circle(x + r, cy, r)
    f.line([(x + r - r * 1.2, cy + r * 1.2), (x + r + r * 1.2, cy - r * 1.2)])
    return 2 * r + h * 0.25


def _circled(f, x, cy, h, letter):
    r = h * 0.55
    f.circle(x + r, cy, r)
    f.text(x + r, cy + h * 0.33, h * 0.8, letter, "middle")
    return 2 * r + h * 0.2


def _num(v):
    s = f"{float(v):.4f}".rstrip("0").rstrip(".")
    return s or "0"


def frame(rows, h=3.5):
    """A feature control frame: rows [{ characteristic, tolerance (mm),
    diameter (a cylindrical zone), modifier ("M"|"L"|…), datums ["A",
    "B|M" …] }], stacked (a composite frame). Text height h mm."""
    if not rows:
        raise GdtError("a frame needs at least one row")
    f = Figure()
    c = h * 2.0  # cell height, and the symbol cell's width
    pad = h * 0.4
    widths = []
    for row in rows:
        kind = row.get("characteristic")
        if kind not in CHARACTERISTICS:
            raise GdtError(f"characteristic {kind!r}: one of {', '.join(CHARACTERISTICS)}")
        needs, may, dia = CHARACTERISTICS[kind]
        datums = [str(d).strip() for d in (row.get("datums") or []) if str(d).strip()]
        if needs and not datums:
            raise GdtError(f"{kind} is measured from a datum: give datums, e.g. [\"A\"]")
        if datums and not may:
            raise GdtError(f"{kind} is a form tolerance: it takes no datum")
        if len(datums) > 3:
            raise GdtError("at most three datums: primary, secondary, tertiary")
        if row.get("diameter") and not dia:
            raise GdtError(f"{kind} has no cylindrical zone: leave out diameter")
        tol = row.get("tolerance")
        if not isinstance(tol, (int, float)) or isinstance(tol, bool) or tol <= 0:
            raise GdtError("tolerance: the zone's size in mm, over 0")
        mod = row.get("modifier")
        if mod is not None and mod not in MODIFIERS:
            raise GdtError(f"modifier: one of {', '.join(MODIFIERS)}")
        tw = pad + (h * 0.76 + h * 0.25 if row.get("diameter") else 0) + text_w(_num(tol), h) + (h * 1.3 if mod else 0) + pad
        dws = []
        for d in datums:
            letter, _, dm = d.partition("|")
            if not letter.isalpha() or len(letter) > 2:
                raise GdtError(f"datum {d!r}: a letter, with |M or |L after it for a modifier")
            dws.append(pad + text_w(letter, h) + (h * 1.6 if dm else 0) + pad)
        widths.append((c, max(tw, c), [max(w, c) for w in dws], datums))
    total_w = max(sym + tw + sum(dws) for sym, tw, dws, _ in widths)
    for i, (row, (sym, tw, dws, datums)) in enumerate(zip(rows, widths)):
        y0 = i * c
        w = sym + tw + sum(dws)
        f.line([(0, y0), (w, y0), (w, y0 + c), (0, y0 + c)], closed=True)
        _symbol(f, row["characteristic"], 0, y0, c)
        x = sym
        f.line([(x, y0), (x, y0 + c)])
        cy = y0 + c / 2
        xx = x + pad
        if row.get("diameter"):
            xx += _diameter(f, xx, cy, h)
        f.text(xx, cy + h * 0.36, h, _num(row["tolerance"]))
        xx += text_w(_num(row["tolerance"]), h) + h * 0.15
        if row.get("modifier"):
            _circled(f, xx, cy, h * 0.8, row["modifier"])
        x += tw
        for d, dw in zip(datums, dws):
            f.line([(x, y0), (x, y0 + c)])
            letter, _, dm = d.partition("|")
            xx = x + pad
            f.text(xx, cy + h * 0.36, h, letter)
            if dm:
                _circled(f, xx + text_w(letter, h) + h * 0.35, cy, h * 0.8, dm)
            x += dw
    f.w, f.h = total_w, c * len(rows)
    return f


def datum(letter, h=3.5, side="up"):
    """A datum feature symbol: the letter in a square joined to a filled
    triangle whose base sits on the feature. side: where the letter stands
    from the feature — up (the triangle's base is the figure's bottom
    edge), down, left or right."""
    letter = str(letter or "").strip()
    if not letter.isalpha() or len(letter) > 2:
        raise GdtError("letter: one or two letters (I, O and Q are best avoided)")
    if side not in ("up", "down", "left", "right"):
        raise GdtError("side: up, down, left or right")
    s = h * 2.0
    w = max(s, text_w(letter, h) + h)
    stem, tri = h * 1.2, h * 0.9
    L = s + stem + tri  # box, stem, triangle, along the figure's length
    f = Figure()
    # laid out along +u from the feature (u = 0 at the triangle's base),
    # across v; then turned to the side asked for
    prims = [("line", [(0, -tri * 0.6), (0, tri * 0.6), (tri, 0)], "fill"),
             ("line", [(tri, 0), (tri + stem, 0)], None),
             ("line", [(tri + stem, -w / 2), (L, -w / 2), (L, w / 2), (tri + stem, w / 2)], "closed")]
    if side in ("up", "down"):
        fw, fh = w, L
        def m(u, v):  # noqa: E306
            return (fw / 2 + v, fh - u) if side == "up" else (fw / 2 + v, u)
        centre = m(tri + stem + s / 2, 0)
    else:
        fw, fh = L, w
        def m(u, v):  # noqa: E306
            return (u, fh / 2 + v) if side == "right" else (fw - u, fh / 2 + v)
        centre = m(tri + stem + s / 2, 0)
    for _, pts, how in prims:
        q = [m(u, v) for u, v in pts]
        if how == "fill":
            f.filled.append(q)
        else:
            f.line(q, closed=how == "closed")
    f.text(centre[0], centre[1] + h * 0.36, h, letter, "middle")
    f.w, f.h = fw, fh
    return f


def describe(rows):
    """The frame as words, for the answer: "⌖ ⌀0.1 Ⓜ | A | B"."""
    glyph = {"straightness": "⏤", "flatness": "⏥", "circularity": "○", "cylindricity": "⌭", "profile_line": "⌒", "profile_surface": "⌓",
             "angularity": "∠", "perpendicularity": "⟂", "parallelism": "∥", "position": "⌖", "concentricity": "◎", "symmetry": "⌯",
             "circular_runout": "↗", "total_runout": "⌰"}
    out = []
    for r in rows:
        parts = [glyph[r["characteristic"]], ("⌀" if r.get("diameter") else "") + _num(r["tolerance"]) + (f" ({r['modifier']})" if r.get("modifier") else "")]
        parts += [d.replace("|", " (") + ")" if "|" in d else d for d in (r.get("datums") or [])]
        out.append(" | ".join(parts))
    return " / ".join(out)


# ---- weld symbols (ISO 2553 / AWS A2.4, the common forms) ----------------------

WELDS = ("fillet", "square", "v", "bevel", "u", "j", "plug", "spot", "seam", "flare-v", "flare-bevel", "backing", "surfacing")


def _weld_glyph(f, kind, cx, y0, h, up):
    """One weld symbol on the reference line at x = cx, y0 the line; up: drawn
    above it (the other side) rather than below (the arrow side). y grows down."""
    s = -1 if up else 1
    a = h * 0.9  # the symbol's size
    if kind == "fillet":
        f.line([(cx - a / 2, y0), (cx - a / 2, y0 + s * a), (cx + a / 2, y0)])
    elif kind == "square":
        f.line([(cx - a / 4, y0), (cx - a / 4, y0 + s * a)])
        f.line([(cx + a / 4, y0), (cx + a / 4, y0 + s * a)])
    elif kind == "v":
        f.line([(cx - a / 2, y0 + s * a), (cx, y0), (cx + a / 2, y0 + s * a)])
    elif kind == "bevel":
        f.line([(cx - a / 3, y0), (cx - a / 3, y0 + s * a)])
        f.line([(cx - a / 3, y0), (cx + a / 2, y0 + s * a)])
    elif kind == "u":
        f.line([(cx, y0), (cx, y0 + s * a * 0.3)])
        n, r = 12, a * 0.45  # the U's round bottom, open away from the line
        f.line([(cx + r * math.cos(math.pi * i / n), y0 + s * (a * 0.3 + r - r * math.sin(math.pi * i / n))) for i in range(n + 1)])
    elif kind == "j":
        x0 = cx - a / 3
        f.line([(x0, y0), (x0, y0 + s * a)])
        n, r = 8, a * 0.6  # a quarter round from the upright out, open away from the line
        f.line([(x0 + r + r * math.cos(math.pi - math.pi / 2 * i / n), y0 + s * (a * 0.4 + r * math.sin(math.pi - math.pi / 2 * i / n))) for i in range(n + 1)])
    elif kind == "plug":
        f.line([(cx - a / 2, y0), (cx - a / 2, y0 + s * a * 0.6), (cx + a / 2, y0 + s * a * 0.6), (cx + a / 2, y0)])
    elif kind == "spot":
        f.circle(cx, y0 + s * a * 0.45, a * 0.45)
    elif kind == "seam":
        f.circle(cx, y0 + s * a * 0.45, a * 0.45)
        f.line([(cx - a * 0.7, y0 + s * a * 0.3), (cx + a * 0.7, y0 + s * a * 0.3)])
        f.line([(cx - a * 0.7, y0 + s * a * 0.6), (cx + a * 0.7, y0 + s * a * 0.6)])
    elif kind in ("flare-v", "flare-bevel"):
        n, r = 8, a * 0.8  # quarter rounds bowing in to the joint line
        for k in ((-1, 1) if kind == "flare-v" else (1,)):
            f.line([(cx + k * (a * 0.1 + r - r * math.cos(math.pi / 2 * i / n)), y0 + s * r * math.sin(math.pi / 2 * i / n)) for i in range(n + 1)])
        if kind == "flare-bevel":
            f.line([(cx - a * 0.1, y0), (cx - a * 0.1, y0 + s * r)])
    elif kind == "backing":
        n = 12
        f.line([(cx + a / 2 * math.cos(math.pi * i / n), y0 + s * a / 2 * math.sin(math.pi * i / n)) for i in range(n + 1)])
    elif kind == "surfacing":
        n = 12
        for k in (-1, 1):
            f.line([(cx + k * a / 4 + a / 4 * math.cos(math.pi * i / n), y0 + s * a / 4 * math.sin(math.pi * i / n)) for i in range(n + 1)])


def weld(kind="fillet", size="", length="", side="arrow", all_around=False, field=False, tail="", h=3.5):
    """A weld symbol: the reference line from the leader's end, the symbol on
    the arrow side (below), the other side (above) or both, size to its left,
    length (and pitch) to its right, all-around circle and field flag at the
    junction, a tail with a note. Origin (0, 0) is where the leader meets the
    reference line; the figure runs to +x; y grows down."""
    if kind not in WELDS:
        raise GdtError(f"kind: {', '.join(WELDS)}")
    if side not in ("arrow", "other", "both"):
        raise GdtError("side: arrow (below the line), other (above) or both")
    f = Figure()
    sw, lw = text_w(str(size), h) if size else 0.0, text_w(str(length), h) if length else 0.0
    cx = h * 1.5 + sw + h * 0.6
    L = cx + h * 0.9 + lw + h * 1.5
    f.line([(0, 0), (L, 0)])
    sides = {"arrow": [False], "other": [True], "both": [False, True]}[side]
    for up in sides:
        _weld_glyph(f, kind, cx, 0, h, up)
        ty = -h * 0.35 if up else h * 1.05
        if size:
            f.text(cx - h * 0.9, ty, h, size, "end")
        if length:
            f.text(cx + h * 0.9, ty, h, length, "start")
    if all_around:
        f.circle(0, 0, h * 0.5)
    if field:
        f.line([(0, 0), (0, -h * 2.2)])
        f.filled.append([(0, -h * 2.2), (h * 1.2, -h * 1.85), (0, -h * 1.5)])
    if tail:
        f.line([(L, 0), (L + h, -h)])
        f.line([(L, 0), (L + h, h)])
        f.text(L + h * 1.3, h * 0.35, h, tail, "start")
    f.w, f.h = L + (h * 1.3 + text_w(tail, h) if tail else 0), h * 2.5
    return f


LAYS = {"=": "=", "parallel": "=", "perpendicular": "⊥", "crossed": "X", "multi": "M", "circular": "C", "radial": "R", "particulate": "P"}


def finish(ra="", removal="required", process="", lay="", allowance="", h=3.5):
    """A surface texture symbol (ISO 1302 / 21920): the check mark with a
    bar when material must be removed or a circle when it must not, the
    roughness (Ra …) under the long leg's extension, the process above it,
    the lay to the right. The base point (the surface) is the origin, the
    figure's bottom; y grows down, so the figure runs up into negative y."""
    if removal not in ("required", "prohibited", "any"):
        raise GdtError("removal: required, prohibited or any")
    if lay and lay not in LAYS:
        raise GdtError(f"lay: {', '.join(LAYS)}")
    f = Figure()
    H1, H2 = 1.4 * h, 3.0 * h
    t60 = math.tan(math.radians(60))
    left = (-H1 / t60, -H1)
    top = (H2 / t60, -H2)
    f.line([left, (0, 0), top])
    if removal == "required":
        f.line([left, (H1 / t60, -H1)])
    elif removal == "prohibited":
        r = H1 / (2 * math.sin(math.radians(60))) * 0.55
        f.circle(0, -H1 * 0.62, r)
    text = str(ra) if ra else ""
    if text and not any(text.startswith(k) for k in ("Ra", "Rz", "Rq", "Rt", "Rmax")):
        text = f"Ra {text}"
    width = max(text_w(text, h), text_w(str(process), h), h * 3) + h
    f.line([top, (top[0] + width, top[1])])
    if text:
        f.text(top[0] + h * 0.4, top[1] + h * 1.2, h, text, "start")
    if process:
        f.text(top[0] + h * 0.4, top[1] - h * 0.35, h, str(process), "start")
    if lay:
        f.text(H1 / t60 + h * 0.6, -h * 0.2, h, LAYS[lay], "start")
    if allowance:
        f.text(left[0] - h * 0.3, -h * 0.2, h, str(allowance), "end")
    f.w, f.h = top[0] + width - left[0], H2 + h * 1.2
    f.origin = (left[0], -H2 - h * 1.2)  # the figure's top-left from the base point
    return f
