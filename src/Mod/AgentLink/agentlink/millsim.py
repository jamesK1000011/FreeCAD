# SPDX-License-Identifier: LGPL-2.1-or-later
"""A 3-axis milling simulation on a height map: the stock's top over a grid,
cut down by the tool along each move, then laid against the part's own top.
Plain numpy, no FreeCAD, so it is tested anywhere and is the same on every
release. Seen from above: what a 3-axis machine can reach, no undercuts."""

import math

import numpy as np

CUTS = ("G1", "G01", "G2", "G02", "G3", "G03")
RAPIDS = ("G0", "G00")
CYCLES = ("G73", "G81", "G82", "G83", "G85", "G86", "G89")


class Grid:
    """Cells of `res` mm over [x0, x1] x [y0, y1]; heights per cell."""

    def __init__(self, x0, y0, x1, y1, res):
        self.x0, self.y0, self.res = float(x0), float(y0), float(res)
        self.nx = max(1, int(math.ceil((x1 - x0) / res)))
        self.ny = max(1, int(math.ceil((y1 - y0) / res)))
        self.xs = self.x0 + (np.arange(self.nx) + 0.5) * res
        self.ys = self.y0 + (np.arange(self.ny) + 0.5) * res

    def span(self, lo, hi, axis):
        """Cell index range [i0, i1) covering [lo, hi] along x (0) or y (1)."""
        o, n = (self.x0, self.nx) if axis == 0 else (self.y0, self.ny)
        i0 = max(0, int(math.floor((lo - o) / self.res)))
        i1 = min(n, int(math.ceil((hi - o) / self.res)) + 1)
        return i0, i1

    def cell(self, x, y):
        return (min(self.ny - 1, max(0, int((y - self.y0) / self.res))), min(self.nx - 1, max(0, int((x - self.x0) / self.res))))

    def at(self, j, i):
        return float(self.xs[i]), float(self.ys[j])


def top_of_mesh(grid, V, F, floor=-np.inf):
    """The highest surface over each cell of a triangle mesh (the part seen
    from above); `floor` where there is none."""
    H = np.full((grid.ny, grid.nx), floor, dtype=float)
    V = np.asarray(V, dtype=float)
    for a, b, c in np.asarray(F, dtype=int):
        p, q, r = V[a], V[b], V[c]
        i0, i1 = grid.span(min(p[0], q[0], r[0]), max(p[0], q[0], r[0]), 0)
        j0, j1 = grid.span(min(p[1], q[1], r[1]), max(p[1], q[1], r[1]), 1)
        if i0 >= i1 or j0 >= j1:
            continue
        X, Y = np.meshgrid(grid.xs[i0:i1], grid.ys[j0:j1])
        d = (q[1] - r[1]) * (p[0] - r[0]) + (r[0] - q[0]) * (p[1] - r[1])
        if abs(d) < 1e-12:
            continue  # seen edge on from above
        w1 = ((q[1] - r[1]) * (X - r[0]) + (r[0] - q[0]) * (Y - r[1])) / d
        w2 = ((r[1] - p[1]) * (X - r[0]) + (p[0] - r[0]) * (Y - r[1])) / d
        w3 = 1 - w1 - w2
        inside = (w1 >= -1e-9) & (w2 >= -1e-9) & (w3 >= -1e-9)
        if not inside.any():
            continue
        z = w1 * p[2] + w2 * q[2] + w3 * r[2]
        sub = H[j0:j1, i0:i1]
        np.maximum(sub, np.where(inside, z, -np.inf), out=sub)
    return H


def _arc(p0, p1, params, clockwise, step=0.5):
    """An arc in XY from p0 to p1 about (I, J) from p0, as points."""
    cx, cy = p0[0] + params.get("I", 0.0), p0[1] + params.get("J", 0.0)
    r = math.hypot(p0[0] - cx, p0[1] - cy)
    a0, a1 = math.atan2(p0[1] - cy, p0[0] - cx), math.atan2(p1[1] - cy, p1[0] - cx)
    sweep = (a0 - a1) if clockwise else (a1 - a0)
    sweep = sweep % (2 * math.pi) or 2 * math.pi
    n = max(2, int(math.ceil(r * sweep / step)))
    out = []
    for k in range(1, n + 1):
        t = k / n
        a = a0 - sweep * t if clockwise else a0 + sweep * t
        out.append((cx + r * math.cos(a), cy + r * math.sin(a), p0[2] + (p1[2] - p0[2]) * t))
    return out


def moves(commands, start=None):
    """Path commands [(name, {X, Y, Z, I, J, R})] as straight moves
    [(kind cut|rapid, (x, y, z), (x, y, z))], arcs and drilling cycles
    broken into lines."""
    out = []
    pos = list(start) if start else None
    initial = True  # G98: retract to the start height
    for name, p in commands:
        name = name.upper()
        if name in ("G98", "G99"):
            initial = name == "G98"
            continue
        if name not in CUTS + RAPIDS + CYCLES:
            continue
        if pos is None:
            if all(k in p for k in "XYZ"):
                pos = [p["X"], p["Y"], p["Z"]]
            continue
        if name in CYCLES:
            if "Z" not in p:
                continue
            x, y = p.get("X", pos[0]), p.get("Y", pos[1])
            r = p.get("R", pos[2])
            top = max(pos[2], r)
            if top > pos[2]:
                out.append(("rapid", tuple(pos), (pos[0], pos[1], top)))
            out.append(("rapid", (pos[0], pos[1], top), (x, y, top)))
            out.append(("rapid", (x, y, top), (x, y, r)))
            out.append(("cut", (x, y, r), (x, y, p["Z"])))
            out.append(("rapid", (x, y, p["Z"]), (x, y, top if initial else r)))
            pos = [x, y, top if initial else r]
            continue
        nxt = [p.get("X", pos[0]), p.get("Y", pos[1]), p.get("Z", pos[2])]
        if name in RAPIDS:
            out.append(("rapid", tuple(pos), tuple(nxt)))
        elif name in ("G1", "G01"):
            out.append(("cut", tuple(pos), tuple(nxt)))
        else:
            prev = tuple(pos)
            for q in _arc(pos, nxt, p, name in ("G2", "G02")):
                out.append(("cut", prev, q))
                prev = q
        pos = nxt
    return out


def _segment_cells(grid, p0, p1, r):
    """The cells within r of the segment's XY track: (j0, i0, mask, t),
    t the place along the segment (0..1) nearest each cell."""
    i0, i1 = grid.span(min(p0[0], p1[0]) - r, max(p0[0], p1[0]) + r, 0)
    j0, j1 = grid.span(min(p0[1], p1[1]) - r, max(p0[1], p1[1]) + r, 1)
    if i0 >= i1 or j0 >= j1:
        return None
    X, Y = np.meshgrid(grid.xs[i0:i1], grid.ys[j0:j1])
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    L2 = dx * dx + dy * dy
    t = np.zeros_like(X) if L2 < 1e-18 else np.clip(((X - p0[0]) * dx + (Y - p0[1]) * dy) / L2, 0, 1)
    d = np.hypot(X - (p0[0] + t * dx), Y - (p0[1] + t * dy))
    return j0, i0, d, t


def tip_height(d, r, z, ball):
    """Where the tool's bottom is at distance d from its axis, tip at z."""
    if not ball:
        return np.where(d <= r, z, np.inf)
    return np.where(d <= r, z + r - np.sqrt(np.maximum(r * r - d * d, 0.0)), np.inf)


def run(grid, H, segments, radius, ball=False, floor=None):
    """Cut H (the stock's top per cell) along the moves; rapids cut nothing
    but are checked against it. Returns (removed mm³, crashes [(at, depth
    mm)])."""
    removed = 0.0
    crashes = []
    area = grid.res * grid.res
    for kind, p0, p1 in segments:
        got = _segment_cells(grid, p0, p1, radius)
        if got is None:
            continue
        j0, i0, d, t = got
        z = p0[2] + (p1[2] - p0[2]) * t
        bottom = tip_height(d, radius, z, ball)
        sub = H[j0:j0 + d.shape[0], i0:i0 + d.shape[1]]
        if kind == "rapid":
            into = sub - bottom
            k = np.unravel_index(int(np.argmax(into)), into.shape)
            if into[k] > 0.05:
                crashes.append((grid.at(j0 + k[0], i0 + k[1]) + (float(bottom[k]),), float(into[k])))
            continue
        lower = np.minimum(sub, bottom)
        if floor is not None:
            lower = np.maximum(lower, floor)
        removed += float(np.sum(sub - lower)) * area
        sub[...] = lower
    return removed, crashes


def compare(grid, H, part_top, stock_bottom, tol=0.02):
    """The cut stock against the part, cell by cell over the part's
    footprint: gouges (cut below the part) and what is left above it.
    Returns a dict, and the per-cell difference (mm, NaN off the part)."""
    on = np.isfinite(part_top) & (part_top > stock_bottom + 1e-6)
    diff = np.where(on, H - part_top, np.nan)
    area = grid.res * grid.res
    out = {"cells": int(on.sum()), "cell": grid.res}
    # A cell on the part's outline straddles it: a pass that runs exactly
    # along the edge cuts its centre. Gouges are read a cell inside.
    inner = on.copy()
    inner[1:, :] &= on[:-1, :]
    inner[:-1, :] &= on[1:, :]
    inner[:, 1:] &= on[:, :-1]
    inner[:, :-1] &= on[:, 1:]
    g = inner & (diff < -tol)
    if g.any():
        k = np.unravel_index(int(np.nanargmin(np.where(g, diff, np.nan))), diff.shape)
        x, y = grid.at(*k)
        out["gouge"] = {"deepest": round(float(-diff[k]), 3), "at": [round(x, 2), round(y, 2), round(float(H[k]), 3)],
                        "areaMm2": round(float(g.sum()) * area, 1), "volumeMm3": round(float(-np.sum(diff[g])) * area, 1)}
    left = on & (diff > tol)
    if left.any():
        k = np.unravel_index(int(np.nanargmax(np.where(left, diff, np.nan))), diff.shape)
        x, y = grid.at(*k)
        out["left"] = {"most": round(float(diff[k]), 3), "at": [round(x, 2), round(y, 2), round(float(H[k]), 3)],
                       "areaMm2": round(float(left.sum()) * area, 1), "volumeMm3": round(float(np.sum(diff[left])) * area, 1),
                       "share": round(float(left.sum()) / max(1, int(on.sum())), 3)}
    return out, diff
