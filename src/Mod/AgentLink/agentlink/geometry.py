# SPDX-License-Identifier: LGPL-2.1-or-later
# Shape building and reading on FreeCAD's Part module: rounded-rectangle
# prisms and ruled lofts (what the generators are made of), and the readings
# the checks give — sound or not, sliver faces, the least wall by rays.
# Everything in millimetres, the document's own unit.

import math

import FreeCAD
import Part

V = FreeCAD.Vector


def round_rect(cx, cy, w, d, r, z):
    """A w x d rectangle centred on (cx, cy) at height z, corners of radius r,
    as a closed wire. Always eight edges when r > 0, so ruled lofts between
    stations pair edge for edge."""
    hw, hd = w / 2.0, d / 2.0
    R = max(0.0, min(r, hw - 1e-3, hd - 1e-3))
    if R < 1e-3:
        p = [V(cx - hw, cy - hd, z), V(cx + hw, cy - hd, z), V(cx + hw, cy + hd, z), V(cx - hw, cy + hd, z)]
        return Part.Wire([Part.LineSegment(p[i], p[(i + 1) % 4]).toShape() for i in range(4)])
    x0, x1, y0, y1 = cx - hw, cx + hw, cy - hd, cy + hd
    c = R * (1 - math.sqrt(0.5))
    e = [
        Part.LineSegment(V(x0 + R, y0, z), V(x1 - R, y0, z)).toShape(),
        Part.Arc(V(x1 - R, y0, z), V(x1 - c, y0 + c, z), V(x1, y0 + R, z)).toShape(),
        Part.LineSegment(V(x1, y0 + R, z), V(x1, y1 - R, z)).toShape(),
        Part.Arc(V(x1, y1 - R, z), V(x1 - c, y1 - c, z), V(x1 - R, y1, z)).toShape(),
        Part.LineSegment(V(x1 - R, y1, z), V(x0 + R, y1, z)).toShape(),
        Part.Arc(V(x0 + R, y1, z), V(x0 + c, y1 - c, z), V(x0, y1 - R, z)).toShape(),
        Part.LineSegment(V(x0, y1 - R, z), V(x0, y0 + R, z)).toShape(),
        Part.Arc(V(x0, y0 + R, z), V(x0 + c, y0 + c, z), V(x0 + R, y0, z)).toShape(),
    ]
    return Part.Wire(e)


def prism(cx, cy, w, d, r, z0, z1):
    """A rounded-rectangle prism from z0 up to z1."""
    return Part.Face(round_rect(cx, cy, w, d, r, z0)).extrude(V(0, 0, z1 - z0))


def stepped(cx, cy, w, d, stations):
    """A ruled solid through rounded rectangles stacked up: each station
    (z, inset, r) of a w x d outline, the inset taken from every side."""
    wires = [round_rect(cx, cy, w - 2 * inset, d - 2 * inset, r, z) for (z, inset, r) in stations]
    return Part.makeLoft(wires, True, True)


def cylinder(r, h, x, y, z):
    return Part.makeCylinder(r, h, V(x, y, z))


def fuse_all(shapes):
    out = shapes[0]
    if len(shapes) > 1:
        out = out.fuse(shapes[1:])
    return clean(out)


def cut_all(shape, tools):
    if not tools:
        return shape
    return clean(shape.cut(tools))


def clean(shape):
    """One solid, coplanar faces merged: a boolean leaves faces split where
    its pieces met, which reads as clutter and as short edges."""
    try:
        shape = shape.removeSplitter()
    except Exception:  # removeSplitter refuses some compounds; the shape stands
        pass
    if shape.ShapeType == "Compound" and len(shape.Solids) == 1:
        shape = shape.Solids[0]
    return shape


# ---- readings -----------------------------------------------------------------------------

def r3(v):
    return round(float(v), 3) + 0.0


def vec(v):
    return [r3(v.x), r3(v.y), r3(v.z)]


def box_of(shape):
    b = shape.BoundBox
    return {"min": [r3(b.XMin), r3(b.YMin), r3(b.ZMin)], "max": [r3(b.XMax), r3(b.YMax), r3(b.ZMax)], "size": [r3(b.XLength), r3(b.YLength), r3(b.ZLength)]}


def stats(shape):
    """What a body is, in a few numbers: what a diff compares."""
    return {
        "volume": round(shape.Volume, 2),
        "area": round(shape.Area, 2),
        "faces": len(shape.Faces),
        "solids": len(shape.Solids),
        "box": box_of(shape)["size"],
    }


def narrow_faces(shape, width_mm=0.01):
    """Faces under width_mm wide, by 2 x area / perimeter — the width of a
    strip — a sliver two faces a hair apart leave, which a round beside it
    stumbles on."""
    out = []
    for i, f in enumerate(shape.Faces):
        p = sum(e.Length for e in f.Edges)
        if p <= 0:
            continue
        w = 2.0 * f.Area / p
        if w < width_mm:
            out.append({"index": i, "width": round(w, 5), "area": round(f.Area, 4), "centroid": vec(f.CenterOfMass)})
    return sorted(out, key=lambda x: x["width"])


def short_edges(shape, length_mm=0.02):
    out = []
    for i, e in enumerate(shape.Edges):
        if e.Length < length_mm:
            out.append({"index": i, "length": round(e.Length, 5), "at": vec(e.CenterOfMass)})
    return out


def soundness(shape):
    """Valid, closed, one solid, no slivers: as a body check reads it."""
    shells = shape.Shells
    closed = bool(shells) and all(s.isClosed() for s in shells)
    narrow = narrow_faces(shape)
    short = short_edges(shape)
    problems = []
    if not shape.isValid():
        problems.append("The shape is not valid: a boolean or loft left it broken. Rebuild the last step.")
    if not closed:
        problems.append("The shape is not closed: it has open edges, so it has no inside to print or weigh.")
    if len(shape.Solids) != 1:
        problems.append(f"{len(shape.Solids)} solids where one is meant: pieces that do not touch, or none.")
    if narrow:
        problems.append(
            f"{len(narrow)} face{'s' if len(narrow) != 1 else ''} under 0.01 mm wide — a sliver two faces a hair apart leave: "
            + ", ".join(f"{n['index']} ({n['width']} mm at {n['centroid']})" for n in narrow[:5])
            + ". Overlap the pieces clearly, or carry a cutter on along its slope."
        )
    return {
        "valid": shape.isValid(),
        "closed": closed,
        "solids": len(shape.Solids),
        "narrow": narrow[:10],
        "shortEdges": short[:10],
        "problems": problems,
        "ok": not problems,
    }


def least_wall(shape, spacing=3.0, reach=12.0, cap=600):
    """The least wall, by rays: from points spread over every face, straight
    in along the face's normal to where the ray leaves the material. Gives
    the least, where, and how many samples read under each limit asked.
    Points within half a spacing of an edge are skipped: a ray there grazes
    the next face and reads its corner, not a wall."""
    solid = shape.Solids[0] if shape.Solids else shape
    samples = []
    faces = sorted(shape.Faces, key=lambda f: -f.Area)
    total_area = sum(f.Area for f in faces) or 1.0
    budget = cap
    for f in faces:
        n = max(1, min(budget, int(round(cap * f.Area / total_area))))
        if budget <= 0:
            break
        u0, u1, v0, v1 = f.ParameterRange
        k = max(1, int(math.sqrt(n)))
        # How far apart this face's points land: the run-joining in
        # thin_runs goes by it, since a big face is sampled sparsely.
        bb = f.BoundBox
        step = max(bb.XLength, bb.YLength, bb.ZLength) / k
        taken = 0
        for i in range(k):
            for j in range(k):
                u = u0 + (u1 - u0) * (i + 0.5) / k
                v = v0 + (v1 - v0) * (j + 0.5) / k
                try:
                    p = f.valueAt(u, v)
                    if not f.isInside(p, 1e-6, True):
                        continue
                    vx = Part.Vertex(p)
                    if min(w.distToShape(vx)[0] for w in f.Wires) < spacing / 2:
                        continue
                    nrm = f.normalAt(u, v)
                except Exception:
                    continue
                if nrm.Length < 1e-9:
                    continue
                nrm.normalize()
                # The ray goes into the material: a reversed face's normal
                # can point either way depending on the version, so test it.
                if not solid.isInside(p - nrm * 0.05, 1e-6, False):
                    nrm = nrm * -1
                    if not solid.isInside(p - nrm * 0.05, 1e-6, False):
                        continue
                samples.append((p, nrm, step))
                taken += 1
        budget -= taken
    least = None
    readings = []
    hits = _ray_hits(shape, [(p, nrm) for (p, nrm, _) in samples], reach)
    for (p, nrm, step), t in zip(samples, hits):
        if t is None:
            continue
        readings.append((t, vec(p), step))
        if least is None or t < least[0]:
            least = (t, p)
    if least is None:
        return {"least": None, "samples": len(samples), "read": 0}
    return {"least": round(least[0], 3), "at": vec(least[1]), "samples": len(samples), "read": len(readings), "readings": readings, "spacing": spacing}


def thin_runs(readings, limit, link):
    """The readings under `limit` joined into runs, one per thin place: two
    are one run when within 1.6 of their faces' sample spacing (or `link`,
    the least) of each other. Each run: its least and where,
    and how many readings it holds. A lip thin all the way round is one run,
    so one waiver covers it."""
    thin = [(t, p, st) for (t, p, st) in readings if t < limit]
    parent = list(range(len(thin)))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(thin)):
        for j in range(i + 1, len(thin)):
            a, b = thin[i][1], thin[j][1]
            reach = max(link, 1.6 * max(thin[i][2], thin[j][2]))
            if (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2 <= reach * reach:
                parent[root(i)] = root(j)
    runs = {}
    for i, (t, p, _) in enumerate(thin):
        r = runs.setdefault(root(i), {"least": t, "at": p, "count": 0, "points": []})
        r["count"] += 1
        r["points"].append(p)
        if t < r["least"]:
            r["least"], r["at"] = t, p
    return sorted(runs.values(), key=lambda r: r["least"])


def _ray_hits(shape, rays, reach, tol=0.01):
    """For each ray (start, inward normal), how far in it leaves the
    material: the nearest crossing of the shape's surface past 2 µm, within
    reach, or None. All rays against one fine tessellation at once
    (Möller–Trumbore, numpy), in place of a boolean per ray."""
    import numpy as np

    pts, tris = shape.tessellate(tol)
    if not tris or not rays:
        return [None] * len(rays)
    P = np.array([[q.x, q.y, q.z] for q in pts], dtype=float)
    T = np.array(tris)
    v0, v1, v2 = P[T[:, 0]], P[T[:, 1]], P[T[:, 2]]
    e1, e2 = v1 - v0, v2 - v0
    out = []
    for p, n in rays:
        o = np.array([p.x, p.y, p.z], dtype=float)
        d = -np.array([n.x, n.y, n.z], dtype=float)
        h = np.cross(d, e2)
        a = np.einsum("ij,ij->i", e1, h)
        ok = np.abs(a) > 1e-12
        f = np.zeros_like(a)
        f[ok] = 1.0 / a[ok]
        s_ = o - v0
        u = f * np.einsum("ij,ij->i", s_, h)
        q = np.cross(s_, e1)
        v = f * (q @ d)
        t = f * np.einsum("ij,ij->i", e2, q)
        m = ok & (u >= -1e-9) & (v >= -1e-9) & (u + v <= 1 + 1e-9) & (t > 2e-3) & (t <= reach)
        out.append(round(float(t[m].min()), 3) if m.any() else None)
    return out
