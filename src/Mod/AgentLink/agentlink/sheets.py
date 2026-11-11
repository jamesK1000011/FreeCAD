# SPDX-License-Identifier: LGPL-2.1-or-later
# A drawing sheet of lines and text, written as SVG, PDF and DXF. Nothing
# here needs FreeCAD: the views come in as 2D polylines, in millimetres on
# the paper, origin top left, y down (as SVG has it); PDF and DXF turn y up.

import math
import zlib

PAPER = {"A4": (297.0, 210.0), "A3": (420.0, 297.0), "A4P": (210.0, 297.0)}
# Line weights, mm, by kind (ISO 128: thick 0.5, thin 0.25).
WEIGHT = {"visible": 0.5, "hidden": 0.25, "thin": 0.25, "dim": 0.18, "frame": 0.7, "cut": 0.7}
DASH = {"hidden": (3.0, 1.5), "trail": (6.0, 1.5, 1.0, 1.5)}
SCALES = [10, 5, 2, 1, 0.5, 0.2, 0.1, 0.05, 0.02, 0.01]


def scale_text(s):
    if s >= 1:
        return f"{s:g}:1"
    return f"1:{1 / s:g}"


def best_scale(fit):
    """The largest standard scale not over `fit`."""
    for s in SCALES:
        if s <= fit + 1e-9:
            return s
    return None


class Sheet:
    def __init__(self, width, height, title=""):
        self.w = float(width)
        self.h = float(height)
        self.title = title
        self.lines = []  # (kind, [(x, y) …], closed, filled)
        self.texts = []  # (x, y, size, text, anchor)

    def line(self, pts, kind="visible", closed=False, filled=False):
        if len(pts) >= 2:
            self.lines.append((kind, [(float(x), float(y)) for (x, y) in pts], closed, filled))

    def text(self, x, y, size, text, anchor="start"):
        self.texts.append((float(x), float(y), float(size), str(text), anchor))

    def frame(self, margin=10.0):
        m = margin
        self.line([(m, m), (self.w - m, m), (self.w - m, self.h - m), (m, self.h - m)], "frame", closed=True)

    def circle(self, cx, cy, r, kind="thin", n=36):
        self.line([(cx + r * math.cos(2 * math.pi * i / n), cy + r * math.sin(2 * math.pi * i / n)) for i in range(n)], kind, closed=True)

    def place(self, lines, box, centre, scale, kind=None):
        """Lines of a view (its own mm, y up) onto the paper: the view's box
        centred on `centre`, at `scale`."""
        bx, by = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        cx, cy = centre
        for k, pts in lines:
            self.line([(cx + (x - bx) * scale, cy - (y - by) * scale) for (x, y) in pts], kind or k)

    def arrow(self, tip, toward, size=2.5):
        """A filled arrowhead at tip, pointing from `toward` to tip."""
        dx, dy = tip[0] - toward[0], tip[1] - toward[1]
        n = math.hypot(dx, dy) or 1.0
        ux, uy = dx / n, dy / n
        bx, by = tip[0] - ux * size, tip[1] - uy * size
        hw = size * 0.3
        self.line([tip, (bx - uy * hw, by + ux * hw), (bx + uy * hw, by - ux * hw)], "dim", closed=True, filled=True)

    def dimension(self, a, b, offset, label, size=3.0):
        """A linear dimension between points a and b, its line `offset` to
        the side (positive: to the left going a to b), the value over it."""
        dx, dy = b[0] - a[0], b[1] - a[1]
        n = math.hypot(dx, dy)
        if n < 1e-6:
            return
        ux, uy = dx / n, dy / n
        nx, ny = -uy, ux  # left normal, in y-down coordinates it is to the right on screen
        sgn = 1 if offset >= 0 else -1
        pa = (a[0] + nx * offset, a[1] + ny * offset)
        pb = (b[0] + nx * offset, b[1] + ny * offset)
        ext = 1.5 * sgn
        self.line([(a[0] + nx * sgn * 1.0, a[1] + ny * sgn * 1.0), (pa[0] + nx * ext, pa[1] + ny * ext)], "dim")
        self.line([(b[0] + nx * sgn * 1.0, b[1] + ny * sgn * 1.0), (pb[0] + nx * ext, pb[1] + ny * ext)], "dim")
        self.line([pa, pb], "dim")
        self.arrow(pa, pb)
        self.arrow(pb, pa)
        mx, my = (pa[0] + pb[0]) / 2, (pa[1] + pb[1]) / 2
        if abs(ux) > 0.5:  # along the page: the value over the line
            self.texts.append((mx, my - 1.0, size, label, "middle"))
        else:  # up the page: the value to its left, read from the right
            self.texts.append((mx - 1.0, my, size, label, "vertical"))

    # ---- writers -------------------------------------------------------------------------

    def svg(self):
        out = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w:g}mm" height="{self.h:g}mm" viewBox="0 0 {self.w:g} {self.h:g}">',
            f"<title>{_esc(self.title)}</title>",
            '<rect width="100%" height="100%" fill="white"/>',
            '<g fill="none" stroke="black" stroke-linecap="round" stroke-linejoin="round">',
        ]
        for kind, pts, closed, filled in self.lines:
            d = "M" + " L".join(f"{x:.3f},{y:.3f}" for (x, y) in pts) + (" Z" if closed else "")
            dash = DASH.get(kind)
            extra = f' stroke-dasharray="{" ".join(f"{v:g}" for v in dash)}"' if dash else ""
            fill = ' fill="black"' if filled else ""
            out.append(f'<path d="{d}" stroke-width="{WEIGHT.get(kind, 0.25):g}"{extra}{fill}/>')
        out.append("</g>")
        out.append('<g font-family="Helvetica, Arial, sans-serif" fill="black" stroke="none">')
        for x, y, size, text, anchor in self.texts:
            if anchor == "vertical":
                out.append(f'<text x="{x:.3f}" y="{y:.3f}" font-size="{size:g}" text-anchor="middle" transform="rotate(-90 {x:.3f} {y:.3f})">{_esc(text)}</text>')
            else:
                out.append(f'<text x="{x:.3f}" y="{y:.3f}" font-size="{size:g}" text-anchor="{anchor}">{_esc(text)}</text>')
        out.append("</g></svg>")
        return "\n".join(out) + "\n"

    def pdf(self):
        """One page, its lines and Helvetica text: a PDF 1.4 file's bytes."""
        k = 72.0 / 25.4  # points per mm
        H = self.h
        ops = ["1 J 1 j"]
        for kind, pts, closed, filled in self.lines:
            dash = DASH.get(kind)
            ops.append(f"{WEIGHT.get(kind, 0.25) * k:.3f} w")
            ops.append(("[" + " ".join(f"{v * k:.2f}" for v in dash) + "] 0 d") if dash else "[] 0 d")
            seg = [f"{pts[0][0] * k:.3f} {(H - pts[0][1]) * k:.3f} m"] + [f"{x * k:.3f} {(H - y) * k:.3f} l" for (x, y) in pts[1:]]
            ops.append(" ".join(seg) + (" h" if closed else "") + (" b" if filled else " S"))
        for x, y, size, text, anchor in self.texts:
            fs = size * k
            width = 0.5 * fs * len(text)  # Helvetica averages about half an em
            if anchor == "vertical":
                ops.append(f"BT /F1 {fs:.2f} Tf 0 1 -1 0 {x * k:.3f} {(H - y) * k - width / 2:.3f} Tm ({_pdfstr(text)}) Tj ET")
                continue
            shift = {"middle": width / 2, "end": width}.get(anchor, 0.0)
            ops.append(f"BT /F1 {fs:.2f} Tf {x * k - shift:.3f} {(H - y) * k:.3f} Td ({_pdfstr(text)}) Tj ET")
        content = zlib.compress("\n".join(ops).encode("latin-1", "replace"))
        objs = [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {self.w * k:.2f} {self.h * k:.2f}] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>".encode(),
            b"<< /Length " + str(len(content)).encode() + b" /Filter /FlateDecode >>\nstream\n" + content + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
            f"<< /Title ({_pdfstr(self.title)}) /Producer (FreeCAD Agent Link) >>".encode("latin-1", "replace"),
        ]
        buf = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = []
        for i, o in enumerate(objs, 1):
            offsets.append(len(buf))
            buf += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
        xref = len(buf)
        buf += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
        for off in offsets:
            buf += f"{off:010d} 00000 n \n".encode()
        buf += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R /Info {len(objs)} 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
        return bytes(buf)

    def dxf(self):
        """AutoCAD R12 DXF: LINE and TEXT entities on a layer per kind, in mm,
        y up."""
        H = self.h
        out = ["0", "SECTION", "2", "HEADER", "9", "$ACADVER", "1", "AC1009", "9", "$INSUNITS", "70", "4", "0", "ENDSEC"]
        kinds = sorted({k for (k, _, _, _) in self.lines} | {"text"})
        out += ["0", "SECTION", "2", "TABLES", "0", "TABLE", "2", "LAYER", "70", str(len(kinds))]
        for kd in kinds:
            out += ["0", "LAYER", "2", kd.upper(), "70", "0", "62", "7", "6", "CONTINUOUS"]
        out += ["0", "ENDTAB", "0", "ENDSEC", "0", "SECTION", "2", "ENTITIES"]
        for kind, pts, closed, _ in self.lines:
            seq = pts + ([pts[0]] if closed else [])
            for (x0, y0), (x1, y1) in zip(seq, seq[1:]):
                out += ["0", "LINE", "8", kind.upper(), "10", f"{x0:.4f}", "20", f"{H - y0:.4f}", "30", "0.0", "11", f"{x1:.4f}", "21", f"{H - y1:.4f}", "31", "0.0"]
        for x, y, size, text, anchor in self.texts:
            rot = 90 if anchor == "vertical" else 0
            out += ["0", "TEXT", "8", "TEXT", "10", f"{x:.4f}", "20", f"{H - y:.4f}", "30", "0.0", "40", f"{size:g}", "1", text.replace("\u2300", "%%c").replace("\u00b1", "%%p").replace("\u00d7", "x"), "50", str(rot)]
            if anchor in ("middle", "vertical", "end"):
                out += ["72", "2" if anchor == "end" else "1", "11", f"{x:.4f}", "21", f"{H - y:.4f}", "31", "0.0"]
        out += ["0", "ENDSEC", "0", "EOF"]
        return "\n".join(out) + "\n"


# ---- laying views out ---------------------------------------------------------------------

# Where each view goes in the grid, front at (0, 0), rows counted down the
# page. First angle (ISO): the top view under the front, the right view on
# its left. Third angle (ASME): the top view over it, the right on its right.
GRID = {
    "first": {"front": (0, 0), "top": (0, 1), "bottom": (0, -1), "right": (-1, 0), "left": (1, 0), "back": (2, 0)},
    "third": {"front": (0, 0), "top": (0, -1), "bottom": (0, 1), "right": (1, 0), "left": (-1, 0), "back": (2, 0)},
}
ISO_CELLS = [(1, -1), (-1, -1), (1, 1), (-1, 1), (2, -1), (2, 1)]


def layout(sizes, paper, projection="first", scale=None, margin=10.0, title_h=18.0, gap=20.0):
    """Views in their places on the paper: sizes {view: (w, h)} in model mm;
    paper (W, H). Gives {scale, centres: {view: (x, y)}} in paper mm (y down)
    at the largest standard scale that fits, or the scale given. A
    ValueError says when it cannot fit."""
    cells = {}
    taken = set()
    for v in sizes:
        if v != "iso":
            cells[v] = GRID[projection][v]
            taken.add(cells[v])
    if "iso" in sizes:
        cells["iso"] = next(c for c in ISO_CELLS if c not in taken)
    cols = sorted({c for (c, _) in cells.values()})
    rows = sorted({r for (_, r) in cells.values()})
    cw = {c: max(sizes[v][0] for v in cells if cells[v][0] == c) for c in cols}
    rh = {r: max(sizes[v][1] for v in cells if cells[v][1] == r) for r in rows}
    W, H = paper
    aw = W - 2 * margin - 2 * gap
    ah = H - 2 * margin - title_h - 2 * gap
    sw, sh = sum(cw.values()), sum(rh.values())
    if sw <= 0 or sh <= 0:
        raise ValueError("The views are empty: nothing to draw.")
    fit = min((aw - gap * (len(cols) - 1)) / sw, (ah - gap * (len(rows) - 1)) / sh)
    if scale is None:
        scale = best_scale(fit)
        if scale is None:
            raise ValueError("Too big for the paper even at 1:100 — A3, or fewer views.")
    elif scale > fit + 1e-9:
        raise ValueError(f"scale: at most {fit:.3g} fits this paper ({best_scale(fit) and scale_text(best_scale(fit))} the largest standard one).")
    tw = sum(cw.values()) * scale + gap * (len(cols) - 1)
    th = sum(rh.values()) * scale + gap * (len(rows) - 1)
    x0 = margin + gap + (aw - tw) / 2
    y0 = margin + gap + (ah - th) / 2
    cx, x = {}, x0
    for c in cols:
        cx[c] = x + cw[c] * scale / 2
        x += cw[c] * scale + gap
    cy, y = {}, y0
    for r in rows:
        cy[r] = y + rh[r] * scale / 2
        y += rh[r] * scale + gap
    return {"scale": scale, "fit": fit, "centres": {v: (cx[c], cy[r]) for v, (c, r) in cells.items()}}


def title_block(sheet, lines, margin=10.0, width=110.0, height=18.0):
    """A box in the bottom-right corner with a line of text per row."""
    x1, y1 = sheet.w - margin, sheet.h - margin
    x0, y0 = x1 - width, y1 - height
    sheet.line([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], "thin", closed=True)
    step = height / max(1, len(lines))
    for i, t in enumerate(lines):
        sheet.text(x0 + 2, y0 + step * (i + 1) - step * 0.3, 3.0 if i == 0 else 2.5, t)


def _esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _pdfstr(s):
    # WinAnsi has Ø, ± and × but no ⌀: the diameter sign is drawn as Ø.
    s = str(s).replace("\u2014", "-").replace("\u2013", "-").replace("\u2300", "\u00d8")
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def read_dxf_lines(text):
    """The LINE entities of a DXF, [((x0, y0), (x1, y1), layer)]: for checking
    what was written."""
    rows = text.splitlines()
    pairs = list(zip(rows[0::2], rows[1::2]))
    out, cur = [], None
    for code, val in pairs:
        code = code.strip()
        if code == "0":
            if cur and cur.get("type") == "LINE":
                out.append(((cur["10"], cur["20"]), (cur["11"], cur["21"]), cur.get("8")))
            cur = {"type": val.strip()}
        elif cur is not None and code in ("10", "20", "11", "21"):
            cur[code] = float(val)
        elif cur is not None and code == "8":
            cur["8"] = val.strip()
    return out
