# SPDX-License-Identifier: LGPL-2.1-or-later
# The convex hull of 3D points, numpy only (scipy where it is there): an
# incremental hull — a starting tetrahedron, then each point outside the
# hull replaces the faces it sees by faces to their horizon. Triangles come
# out wound outward.

import numpy as np


class HullError(ValueError):
    pass


def hull(P, eps=1e-9):
    """(points, triangles): the hull's vertices and outward triangles over them."""
    P = np.unique(np.asarray(P, float).reshape(-1, 3), axis=0)
    try:
        from scipy.spatial import ConvexHull

        h = ConvexHull(P)
        tris = []
        for s, eq in zip(h.simplices, h.equations):
            a, b, c = P[s]
            tris.append(list(s) if np.cross(b - a, c - a) @ eq[:3] >= 0 else [s[0], s[2], s[1]])
        return _compact(P, np.array(tris))
    except ImportError:
        pass
    if len(P) < 4:
        raise HullError("a hull needs four points or more, not all in one plane")
    scale = float(np.ptp(P, axis=0).max()) or 1.0
    tol = eps * scale * 1e3
    # a first tetrahedron from points far apart
    i0 = int(np.argmin(P[:, 0]))
    i1 = int(np.argmax(np.linalg.norm(P - P[i0], axis=1)))
    line = P[i1] - P[i0]
    d2 = np.linalg.norm(np.cross(P - P[i0], line), axis=1)
    i2 = int(np.argmax(d2))
    if d2[i2] <= tol * np.linalg.norm(line):
        raise HullError("the points lie on a line")
    n = np.cross(P[i1] - P[i0], P[i2] - P[i0])
    d3 = (P - P[i0]) @ n
    i3 = int(np.argmax(np.abs(d3)))
    if abs(d3[i3]) <= tol * np.linalg.norm(n):
        raise HullError("the points lie in one plane")
    faces = [[i0, i1, i2], [i0, i3, i1], [i1, i3, i2], [i2, i3, i0]]
    if d3[i3] > 0:
        faces = [[f[0], f[2], f[1]] for f in faces]
    centre = P[[i0, i1, i2, i3]].mean(axis=0)

    def plane(f):
        a, b, c = P[f]
        nn = np.cross(b - a, c - a)
        ln = np.linalg.norm(nn)
        nn = nn / ln if ln > 0 else nn
        if (centre - a) @ nn > 0:  # keep outward
            nn = -nn
            f[1], f[2] = f[2], f[1]
        return nn, nn @ a

    planes = [plane(f) for f in faces]
    rest = [i for i in range(len(P)) if i not in (i0, i1, i2, i3)]
    # farthest points first: fewer faces made and unmade
    rest.sort(key=lambda i: -np.linalg.norm(P[i] - centre))
    for i in rest:
        p = P[i]
        seen = [k for k, (nn, d) in enumerate(planes) if nn @ p - d > tol]
        if not seen:
            continue
        edges = {}
        for k in seen:
            a, b, c = faces[k]
            for e in ((a, b), (b, c), (c, a)):
                if (e[1], e[0]) in edges:
                    del edges[(e[1], e[0])]
                else:
                    edges[e] = True
        keep = [k for k in range(len(faces)) if k not in set(seen)]
        faces = [faces[k] for k in keep]
        planes = [planes[k] for k in keep]
        for a, b in edges:
            f = [a, b, i]
            faces.append(f)
            planes.append(plane(f))
    return _compact(P, np.array(faces))


def _compact(P, T):
    used = np.unique(T)
    remap = -np.ones(len(P), int)
    remap[used] = np.arange(len(used))
    return P[used], remap[T]


def volume(V, T):
    a, b, c = V[T[:, 0]], V[T[:, 1]], V[T[:, 2]]
    return float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum() / 6.0)
