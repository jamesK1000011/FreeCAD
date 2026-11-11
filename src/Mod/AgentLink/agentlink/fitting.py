# SPDX-License-Identifier: LGPL-2.1-or-later
# Least-squares shapes through points (numpy only): a plane, a sphere, a
# cylinder and a circle in a plane — for points from a scan or a probe. Each
# answers its parameters with the rms and largest distance of the points from
# it, so a fit that does not describe the points says so.

import numpy as np


class FitError(ValueError):
    pass


def _need(P, n, what):
    P = np.asarray(P, float).reshape(-1, 3)
    if len(P) < n:
        raise FitError(f"{what}: {n} points or more, not {len(P)}.")
    return P


def _stats(d):
    d = np.abs(d)
    return {"rms": float(np.sqrt(np.mean(d * d))), "max": float(d.max()), "points": int(len(d))}


def unit(v):
    v = np.asarray(v, float)
    n = np.linalg.norm(v)
    if n == 0:
        raise FitError("a direction of zero length.")
    return v / n


def plane(P):
    """centre, normal (sign: towards +z, else +y, else +x), and the spread."""
    P = _need(P, 3, "plane")
    c = P.mean(axis=0)
    _u, sv, vt = np.linalg.svd(P - c, full_matrices=False)
    n = vt[2]
    for k in (2, 1, 0):
        if abs(n[k]) > 1e-9:
            if n[k] < 0:
                n = -n
            break
    if sv[1] < 1e-9 * max(sv[0], 1e-12):
        raise FitError("plane: the points lie on a line.")
    return {"centre": c, "normal": n, "axes": (vt[0], vt[1]), **_stats((P - c) @ n)}


def sphere(P):
    """centre, radius: the algebraic fit, then refined (Gauss–Newton) on true distances."""
    P = _need(P, 4, "sphere")
    A = np.column_stack([2 * P, np.ones(len(P))])
    b = (P * P).sum(axis=1)
    sol, *_ = np.linalg.lstsq(A, b, rcond=None)
    c = sol[:3]
    r = float(np.sqrt(max(sol[3] + c @ c, 0.0)))
    for _ in range(20):
        D = P - c
        dist = np.linalg.norm(D, axis=1)
        dist[dist == 0] = 1e-12
        res = dist - r
        J = np.column_stack([-D / dist[:, None], -np.ones(len(P))])
        step, *_ = np.linalg.lstsq(J, -res, rcond=None)
        c = c + step[:3]
        r += float(step[3])
        if np.linalg.norm(step) < 1e-10:
            break
    if r <= 0:
        raise FitError("sphere: no sphere through these points.")
    return {"centre": c, "radius": r, **_stats(np.linalg.norm(P - c, axis=1) - r)}


def _basis(a):
    a = unit(a)
    t = np.array([1.0, 0, 0]) if abs(a[0]) < 0.9 else np.array([0, 1.0, 0])
    u = unit(np.cross(a, t))
    return u, np.cross(a, u)


def circle2d(Q):
    """A circle through 2D points: centre (x, y), radius (algebraic, then refined)."""
    Q = np.asarray(Q, float).reshape(-1, 2)
    if len(Q) < 3:
        raise FitError(f"circle: 3 points or more, not {len(Q)}.")
    A = np.column_stack([2 * Q, np.ones(len(Q))])
    b = (Q * Q).sum(axis=1)
    sol, *_ = np.linalg.lstsq(A, b, rcond=None)
    c = sol[:2]
    r = float(np.sqrt(max(sol[2] + c @ c, 0.0)))
    for _ in range(20):
        D = Q - c
        dist = np.linalg.norm(D, axis=1)
        dist[dist == 0] = 1e-12
        J = np.column_stack([-D / dist[:, None], -np.ones(len(Q))])
        step, *_ = np.linalg.lstsq(J, -(dist - r), rcond=None)
        c = c + step[:2]
        r += float(step[2])
        if np.linalg.norm(step) < 1e-10:
            break
    return c, r


def _cyl_residual(P, p0, a, r):
    D = P - p0
    radial = D - np.outer(D @ a, a)
    return np.linalg.norm(radial, axis=1) - r


def cylinder(P, axis=None):
    """axis point and direction, radius, and the length the points span along it.
    axis: a hint for the direction; else every principal direction is tried."""
    P = _need(P, 6, "cylinder")
    c = P.mean(axis=0)
    _u, _sv, vt = np.linalg.svd(P - c, full_matrices=False)
    tries = [unit(axis)] if axis is not None else [vt[0], vt[1], vt[2]]
    best = None
    for a0 in tries:
        a = unit(a0)
        u, v = _basis(a)
        Q = np.column_stack([(P - c) @ u, (P - c) @ v])
        try:
            cc, r = circle2d(Q)
        except (FitError, np.linalg.LinAlgError):
            continue
        p0 = c + cc[0] * u + cc[1] * v
        # Gauss–Newton over the axis tilt (2), its offset (2) and the radius.
        for _ in range(30):
            u, v = _basis(a)
            res = _cyl_residual(P, p0, a, r)
            J = np.empty((len(P), 5))
            h = 1e-6
            for k in range(5):
                da, dp, dr = np.zeros(3), np.zeros(3), 0.0
                if k == 0:
                    da = u * h
                elif k == 1:
                    da = v * h
                elif k == 2:
                    dp = u * h
                elif k == 3:
                    dp = v * h
                else:
                    dr = h
                J[:, k] = (_cyl_residual(P, p0 + dp, unit(a + da), r + dr) - res) / h
            step, *_ = np.linalg.lstsq(J, -res, rcond=None)
            a = unit(a + step[0] * u + step[1] * v)
            p0 = p0 + step[2] * u + step[3] * v
            r += float(step[4])
            if np.linalg.norm(step) < 1e-10:
                break
        res = _cyl_residual(P, p0, a, r)
        st = _stats(res)
        if r > 0 and (best is None or st["rms"] < best[0]["rms"]):
            best = (st, p0, a, r)
    if best is None:
        raise FitError("cylinder: no cylinder through these points.")
    st, p0, a, r = best
    t = (P - p0) @ a
    # The axis point moved to where the points start along it.
    for k in (2, 1, 0):
        if abs(a[k]) > 1e-9:
            if a[k] < 0:
                a, t = -a, -t
            break
    start = p0 + a * t.min()
    return {"base": start, "axis": a, "radius": r, "length": float(t.max() - t.min()), **st}
