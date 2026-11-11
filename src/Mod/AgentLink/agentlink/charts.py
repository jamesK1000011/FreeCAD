# SPDX-License-Identifier: LGPL-2.1-or-later
# Charts as SVG without matplotlib (what FreeCAD's Plot workbench drew in its
# window): line, bar and scatter of one or more series, with axes, ticks,
# a legend and labels. Pure Python.

import math

PALETTE = ["#2f6db5", "#d9822b", "#3a9a5b", "#c2423b", "#7a5bb5", "#8c6d3f", "#d36aa6", "#6b7280"]


def _esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def nice_ticks(lo, hi, n=5):
    """Round tick values covering lo … hi."""
    if hi <= lo:
        hi = lo + 1.0
    raw = (hi - lo) / max(n, 1)
    mag = 10 ** math.floor(math.log10(raw))
    step = min((m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw), default=10 * mag)
    start = math.floor(lo / step) * step
    ticks, v = [], start
    while v <= hi + step * 0.5:
        ticks.append(round(v, 10))
        v += step
    return ticks


def _fmt(v):
    return f"{v:g}" if abs(v) < 1e6 else f"{v:.3g}"


def svg(series, x=None, kind="line", title="", xlabel="", ylabel="", width=640, height=400):
    """series: [{"name", "values": [..]}] (scatter: "points": [[x, y] …]); x: labels or numbers shared by line and bar."""
    if kind not in ("line", "bar", "scatter"):
        raise ValueError("kind: line, bar or scatter")
    if not series:
        raise ValueError("series: one or more")
    L, R, T, B = 64, 20 + (110 if len(series) > 1 else 0), 40 if title else 20, 50
    pw, ph = width - L - R, height - T - B
    if kind == "scatter":
        xs = [p[0] for s in series for p in s["points"]]
        ys = [p[1] for s in series for p in s["points"]]
    else:
        n = max(len(s["values"]) for s in series)
        x = list(x) if x is not None else list(range(1, n + 1))
        numeric_x = kind == "line" and all(isinstance(v, (int, float)) for v in x)
        xs = [float(v) for v in x] if numeric_x else list(range(len(x)))
        ys = [float(v) for s in series for v in s["values"] if v is not None]
    if not ys:
        raise ValueError("no values to chart")
    ylo, yhi = min(ys), max(ys)
    if kind == "bar":
        ylo = min(0.0, ylo)
    yt = nice_ticks(ylo, yhi)
    ylo, yhi = yt[0], yt[-1]
    if kind == "bar":
        xlo, xhi = -0.5, len(xs) - 0.5
        xt = None
    else:
        xlo, xhi = min(xs), max(xs)
        if xhi == xlo:
            xhi = xlo + 1
        xt = nice_ticks(xlo, xhi) if (kind == "scatter" or numeric_x) else None
        if xt:
            xlo, xhi = xt[0], xt[-1]

    def X(v):
        return L + (v - xlo) / (xhi - xlo) * pw

    def Y(v):
        return T + ph - (v - ylo) / (yhi - ylo) * ph

    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" font-family="Helvetica, Arial, sans-serif" font-size="11">',
           f'<rect width="{width}" height="{height}" fill="white"/>']
    if title:
        out.append(f'<text x="{width / 2:.1f}" y="22" text-anchor="middle" font-size="14" font-weight="bold">{_esc(title)}</text>')
    for v in yt:
        y = Y(v)
        out.append(f'<line x1="{L}" y1="{y:.1f}" x2="{L + pw}" y2="{y:.1f}" stroke="#e5e7eb"/>')
        out.append(f'<text x="{L - 6}" y="{y + 4:.1f}" text-anchor="end" fill="#374151">{_fmt(v)}</text>')
    if xt:
        for v in xt:
            xx = X(v)
            out.append(f'<line x1="{xx:.1f}" y1="{T + ph}" x2="{xx:.1f}" y2="{T + ph + 4}" stroke="#374151"/>')
            out.append(f'<text x="{xx:.1f}" y="{T + ph + 17}" text-anchor="middle" fill="#374151">{_fmt(v)}</text>')
    elif kind != "scatter":
        every = max(1, math.ceil(len(x) / 12))
        for i, lab in enumerate(x):
            if i % every == 0:
                out.append(f'<text x="{X(i):.1f}" y="{T + ph + 17}" text-anchor="middle" fill="#374151">{_esc(lab)}</text>')
    out.append(f'<line x1="{L}" y1="{T + ph}" x2="{L + pw}" y2="{T + ph}" stroke="#374151"/><line x1="{L}" y1="{T}" x2="{L}" y2="{T + ph}" stroke="#374151"/>')
    nser = len(series)
    for k, sr in enumerate(series):
        col = PALETTE[k % len(PALETTE)]
        if kind == "line":
            pts = [(X(xs[i]), Y(float(v))) for i, v in enumerate(sr["values"]) if v is not None and i < len(xs)]
            out.append(f'<polyline fill="none" stroke="{col}" stroke-width="2" points="{" ".join(f"{a:.1f},{b:.1f}" for a, b in pts)}"/>')
            out += [f'<circle cx="{a:.1f}" cy="{b:.1f}" r="2.5" fill="{col}"/>' for a, b in pts]
        elif kind == "bar":
            bw = pw / len(xs) * 0.8 / nser
            for i, v in enumerate(sr["values"]):
                if v is None:
                    continue
                x0 = X(i) - pw / len(xs) * 0.4 + k * bw
                y0, y1 = Y(max(float(v), 0.0)), Y(min(float(v), 0.0))
                out.append(f'<rect x="{x0:.1f}" y="{y0:.1f}" width="{bw:.1f}" height="{max(y1 - y0, 0.5):.1f}" fill="{col}"/>')
        else:
            out += [f'<circle cx="{X(p[0]):.1f}" cy="{Y(p[1]):.1f}" r="3" fill="{col}" fill-opacity="0.8"/>' for p in sr["points"]]
        if nser > 1:
            ly = T + 10 + k * 18
            out.append(f'<rect x="{L + pw + 14}" y="{ly - 9}" width="12" height="12" fill="{col}"/><text x="{L + pw + 32}" y="{ly + 1}">{_esc(sr.get("name", f"series {k + 1}"))}</text>')
    if xlabel:
        out.append(f'<text x="{L + pw / 2:.1f}" y="{height - 10}" text-anchor="middle" fill="#111827">{_esc(xlabel)}</text>')
    if ylabel:
        out.append(f'<text x="14" y="{T + ph / 2:.1f}" text-anchor="middle" fill="#111827" transform="rotate(-90 14 {T + ph / 2:.1f})">{_esc(ylabel)}</text>')
    out.append("</svg>")
    return "\n".join(out) + "\n"
