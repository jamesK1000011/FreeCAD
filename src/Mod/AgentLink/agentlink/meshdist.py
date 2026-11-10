# SPDX-License-Identifier: LGPL-2.1-or-later
# The nearest triangle to each of many points, within a reach: the
# triangles binned on a grid by their boxes grown by the reach, each point
# tested against its cell's triangles exactly (the closest point on a
# triangle by its Voronoi regions, as in Ericson's Real-Time Collision
# Detection, 5.1.5). Numpy only: FreeCAD's bundles don't all carry scipy.

import numpy as np

PAIRS = 2_000_000  # point-triangle tests per batch


def closest_on_triangles(P, A, B, C):
    """The closest point on triangle (A, B, C) to P, row by row."""
    ab, ac, ap = B - A, C - A, P - A
    d1, d2 = (ab * ap).sum(1), (ac * ap).sum(1)
    bp = P - B
    d3, d4 = (ab * bp).sum(1), (ac * bp).sum(1)
    cp = P - C
    d5, d6 = (ab * cp).sum(1), (ac * cp).sum(1)
    va = d3 * d6 - d5 * d4
    vb = d5 * d2 - d1 * d6
    vc = d1 * d4 - d3 * d2
    out = np.empty_like(P)
    done = np.zeros(len(P), bool)

    def put(mask, q):
        m = mask & ~done
        out[m] = q[m]
        done[m] = True

    put((d1 <= 0) & (d2 <= 0), A)
    put((d3 >= 0) & (d4 <= d3), B)
    with np.errstate(divide="ignore", invalid="ignore"):
        v = d1 / (d1 - d3)
        put((vc <= 0) & (d1 >= 0) & (d3 <= 0), A + v[:, None] * ab)
        put((d6 >= 0) & (d5 <= d6), C)
        w = d2 / (d2 - d6)
        put((vb <= 0) & (d2 >= 0) & (d6 <= 0), A + w[:, None] * ac)
        w = (d4 - d3) / ((d4 - d3) + (d5 - d6))
        put((va <= 0) & ((d4 - d3) >= 0) & ((d5 - d6) >= 0), B + w[:, None] * (C - B))
        den = va + vb + vc
        den = np.where(den == 0, 1, den)
        v, w = vb / den, vc / den
    rest = A + v[:, None] * ab + w[:, None] * ac
    put(np.ones(len(P), bool), rest)
    return out


def nearest(P, V, F, reach):
    """For each point: the index of the nearest triangle within reach (-1
    where none), the distance, and the distance signed by that triangle's
    normal (+ on the side it faces)."""
    P = np.asarray(P, float)
    V = np.asarray(V, float)
    F = np.asarray(F, np.int64)
    T = V[F]
    lo = T.min(1) - reach
    hi = T.max(1) + reach
    edge = float(np.median(np.linalg.norm(T[:, 1] - T[:, 0], axis=1))) if len(T) else 1.0
    h = max(2 * reach, edge, 1e-6)
    origin = lo.min(0)
    c0 = np.floor((lo - origin) / h).astype(np.int64)
    c1 = np.floor((hi - origin) / h).astype(np.int64)
    dims = c1.max(0) + 1

    def key(c):
        return (c[..., 0] * dims[1] + c[..., 1]) * dims[2] + c[..., 2]

    # Every (cell, triangle) the grown boxes cover.
    span = c1 - c0 + 1
    count = span.prod(1)
    tri = np.repeat(np.arange(len(T)), count)
    off = np.arange(count.sum()) - np.repeat(np.cumsum(count) - count, count)
    sy, sz = np.repeat(span[:, 1], count), np.repeat(span[:, 2], count)
    cell = np.repeat(c0, count, axis=0) + np.column_stack([off // (sy * sz), (off // sz) % sy, off % sz])
    keys = key(cell)
    order = np.argsort(keys, kind="stable")
    keys, tri = keys[order], tri[order]

    best = np.full(len(P), -1, np.int64)
    dist = np.full(len(P), np.inf)
    signed = np.full(len(P), np.nan)
    cp = np.floor((P - origin) / h).astype(np.int64)
    inside = ((cp >= 0) & (cp < dims)).all(1)
    pk = np.full(len(P), -1, np.int64)
    pk[inside] = key(cp[inside])
    start = np.searchsorted(keys, pk, "left")
    stop = np.searchsorted(keys, pk, "right")
    n = np.where(inside, stop - start, 0)
    idx = np.nonzero(n)[0]
    N = np.cross(T[:, 1] - T[:, 0], T[:, 2] - T[:, 0])
    ln = np.linalg.norm(N, axis=1)
    N /= np.where(ln == 0, 1, ln)[:, None]
    # In batches of point-triangle pairs.
    csum = np.cumsum(n[idx])
    s = 0
    while s < len(idx):
        base = csum[s - 1] if s else 0
        e = int(np.searchsorted(csum, base + PAIRS, "right"))
        e = max(e, s + 1)
        pts = idx[s:e]
        k = n[pts]
        pi = np.repeat(pts, k)
        o = np.arange(k.sum()) - np.repeat(np.cumsum(k) - k, k)
        ti = tri[np.repeat(start[pts], k) + o]
        Q = closest_on_triangles(P[pi], T[ti, 0], T[ti, 1], T[ti, 2])
        d = np.linalg.norm(P[pi] - Q, axis=1)
        # The least per point: sort pairs by (point, distance), first of each.
        order = np.lexsort((d, pi))
        first = np.ones(len(order), bool)
        first[1:] = pi[order][1:] != pi[order][:-1]
        sel = order[first]
        p_sel, d_sel, t_sel = pi[sel], d[sel], ti[sel]
        ok = d_sel <= reach
        p_sel, d_sel, t_sel, q_sel = p_sel[ok], d_sel[ok], t_sel[ok], Q[sel][ok]
        best[p_sel], dist[p_sel] = t_sel, d_sel
        side = ((P[p_sel] - q_sel) * N[t_sel]).sum(1)
        signed[p_sel] = np.where(side < 0, -d_sel, d_sel)
        s = e
    return best, dist, signed
