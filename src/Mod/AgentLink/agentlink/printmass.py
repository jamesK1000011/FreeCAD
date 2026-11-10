# SPDX-License-Identifier: LGPL-2.1-or-later
"""How much of a printed part is plastic: laid down layer by layer on a
raster as a slicer does (raster_sliced, the first choice, plain numpy and
the same on every release), sectioned with FreeCAD (sliced), or from its
wall readings (printed_fraction)."""

import math


def solid_share(reads, nz, side_shell, skin_shell):
    """The share of a part's solid volume a slicer fills with plastic, from
    wall readings taken straight in from its faces (each standing for its
    patch of surface, so summing them weighs by volume).

    Readings in from the sides find the perimeters; those in from the top
    and bottom the solid skins. Each direction sees the whole volume but only
    its own shells, so the shares are not averaged but added up as the print
    has them: a reading from an x face misses the perimeters along y, so the
    sides' share is about twice their mean, and skins and perimeters overlap
    as a union. What the shells leave narrower than about three lines the
    slicer fills solid (gap fill, solid infill in small areas)."""
    sums = {"side": [0.0, 0.0], "skin": [0.0, 0.0]}
    for t, z in zip(reads, nz):
        flat = min(1.0, max(0.0, (z - 0.5) / 0.4))  # under 30° off flat: skin
        for key, w, shell in (("skin", flat, skin_shell), ("side", 1 - flat, side_shell)):
            if w > 0:
                sums[key][0] += w * (t if t - shell < 1.4 else shell)
                sums[key][1] += w * t
    side = min(1.0, 2 * sums["side"][0] / sums["side"][1]) if sums["side"][1] else 0.0
    skin = sums["skin"][0] / sums["skin"][1] if sums["skin"][1] else 0.0
    return 1 - (1 - side) * (1 - skin)


def printed_fraction(reads, nz, side_shell, skin_shell, infill):
    """The share filled: the solid shells, and the infill's share of the rest."""
    solid = solid_share(reads, nz, side_shell, skin_shell)
    return solid + infill * (1 - solid)


def shells(setup, walls):
    """(side_shell, skin_shell) mm through a part from a print setup: the
    perimeters at both ends of a ray in from a side (PrusaSlicer's perimeter
    for a 0.4 mm nozzle is 0.45 mm wide), the top and bottom layers of one in
    from the top or bottom."""
    setup = setup or {}
    lh = float(setup.get("layerHeight", 0.2))
    tops = int(setup.get("topLayers", 5))
    bottoms = int(setup.get("bottomLayers", 4))
    return 2 * walls * 0.45, (tops + bottoms) * lh


def readings(wall):
    """Thicknesses and how far each faces up or down, from geometry.least_wall
    read with a reach across the whole part: a ray it could not finish is
    left out, and leaving out the long ones (mostly infill) reads solid."""
    reads = [r[0] for r in (wall.get("readings") or [])]
    nz = [abs(n[2]) for n in (wall.get("normals") or [])] or [0.0] * len(reads)
    return reads, nz


def sliced(shape, setup, walls, levels=40, small=70.0, stats=None):
    """(solid mm³, sparse mm³) of a part as a slicer lays it down, from
    sections at up to `levels` heights: on each, the perimeters a band
    `walls` lines in from the outline, the top and bottom skins where the
    section is not also part within the skins' depth above and below, and
    the rest sparse infill — save islands under `small` mm², which PrusaSlicer
    fills solid. None when FreeCAD cannot section or offset the part; the
    wall readings stand in then."""
    import FreeCAD
    import Part

    setup = setup or {}
    lh = float(setup.get("layerHeight", 0.2))
    top_d = int(setup.get("topLayers", 5)) * lh
    bot_d = int(setup.get("bottomLayers", 4)) * lh
    # PrusaSlicer's perimeters for a 0.4 mm nozzle: the outer 0.45 mm wide,
    # the next ones spaced 0.41 mm.
    band = 0.45 + max(0, walls - 1) * 0.41
    bb = shape.BoundBox
    if bb.ZLength <= 0:
        return None
    n = max(1, min(levels, int(round(bb.ZLength / lh))))
    dz = bb.ZLength / n
    cache = {}

    def section(z):
        key = round(z, 4)
        if key in cache:
            return cache[key]
        f = None
        if bb.ZMin < z < bb.ZMax:
            wires = shape.slice(FreeCAD.Vector(0, 0, 1), z)
            if wires:
                f = Part.makeFace(wires, "Part::FaceMakerBullseye")
                if f.Area < 1e-9:
                    f = None
        cache[key] = f
        return f

    solid = sparse = 0.0
    st = stats if stats is not None else {}
    for k in ("levels", "empty", "skin", "offsetFailed", "inner", "small"):
        st[k] = 0
    try:
        for i in range(n):
            z = bb.ZMin + (i + 0.5) * dz
            f = section(z)
            st["levels"] += 1
            if f is None:
                st["empty"] += 1
                continue
            # Inside the skins: part at every height checked within their
            # depth, above for the top skin and below for the bottom one.
            core = f
            for zz in (z + top_d / 2, z + top_d, z - bot_d / 2, z - bot_d):
                g = section(zz)
                if g is None:
                    core = None
                    break
                core = core.common(g.translated(FreeCAD.Vector(0, 0, z - zz)))
                if core.Area < 1e-9:
                    core = None
                    break
            free = 0.0
            if core is not None:
                try:
                    inner = f.makeOffset2D(-band, 0, False, False, False)
                except Exception:
                    inner = None  # nothing left inside the perimeters
                    st["offsetFailed"] += 1
                if inner is not None and inner.Area > 1e-9:
                    st["inner"] += 1
                    for face in inner.common(core).Faces:
                        if face.Area >= small:
                            free += face.Area
                        else:
                            st["small"] += 1
            else:
                st["skin"] += 1
            solid += (f.Area - free) * dz
            sparse += free * dz
    except Exception:
        return None
    if solid + sparse <= 0:
        return None
    return solid, sparse


def solid_of(shape, setup, walls):
    """(the share of the part's volume printed solid, how it was found):
    laid down layer by layer on a raster as a slicer would, else sectioned
    with FreeCAD, else from the wall readings, else None."""
    try:
        pts, tris = shape.tessellate(0.02)
        if tris:
            cut = raster_sliced([(p.x, p.y, p.z) for p in pts], tris, setup, walls)
            if cut is not None:
                return cut[0] / (cut[0] + cut[1]), "layers"
    except Exception:
        pass
    cut = sliced(shape, setup, walls)
    if cut is not None:
        return cut[0] / (cut[0] + cut[1]), "sections"
    from . import geometry as geo

    reads, nz = readings(geo.least_wall(shape, reach=shape.BoundBox.DiagonalLength + 1.0))
    if reads:
        return solid_share(reads, nz, *shells(setup, walls)), "walls read"
    return None, None


# ---- a slicer's layers on a raster -----------------------------------------------------

def _layer_mask(T, z, x0, y0, cell, nx, ny):
    """The part's cross-section at height z as cells (even-odd fill of the
    triangles' cut through z), from triangles T (n, 3, 3)."""
    import numpy as np

    zs = T[:, :, 2]
    hit = (zs.min(1) < z) & (zs.max(1) > z)
    if not hit.any():
        return np.zeros((ny, nx), dtype=bool)
    A = T[hit]
    P = np.zeros((len(A), 3, 2))
    C = np.zeros((len(A), 3), dtype=bool)
    for e, (i, j) in enumerate(((0, 1), (1, 2), (2, 0))):
        p, q = A[:, i], A[:, j]
        zp, zq = p[:, 2], q[:, 2]
        cross = (zp - z) * (zq - z) < 0
        t = np.where(cross, (z - zp) / np.where(cross, zq - zp, 1.0), 0.0)
        P[:, e] = p[:, :2] + (q[:, :2] - p[:, :2]) * t[:, None]
        C[:, e] = cross
    two = C.sum(1) == 2
    if not two.any():
        return np.zeros((ny, nx), dtype=bool)
    order = np.argsort(~C[two], axis=1, kind="stable")[:, :2]
    S = P[two][np.arange(int(two.sum()))[:, None], order]  # (m, 2, 2)
    ya, yb = S[:, 0, 1], S[:, 1, 1]
    lo, hi = np.minimum(ya, yb), np.maximum(ya, yb)
    # The rows whose centres the segment spans, [lo, hi).
    r0 = np.ceil((lo - y0) / cell - 0.5).astype(np.int64)
    r1 = np.ceil((hi - y0) / cell - 0.5).astype(np.int64)
    cnt = np.clip(r1 - r0, 0, None)
    total = int(cnt.sum())
    toggles = np.zeros((ny, nx + 1), dtype=np.int32)
    if total:
        seg = np.repeat(np.arange(len(S)), cnt)
        start = np.repeat(np.cumsum(cnt) - cnt, cnt)
        rows = r0[seg] + (np.arange(total) - start)
        y = y0 + (rows + 0.5) * cell
        s = S[seg]
        t = (y - s[:, 0, 1]) / (s[:, 1, 1] - s[:, 0, 1])
        x = s[:, 0, 0] + (s[:, 1, 0] - s[:, 0, 0]) * t
        col = np.clip(np.ceil((x - x0) / cell - 0.5).astype(np.int64), 0, nx)
        ok = (rows >= 0) & (rows < ny)
        np.add.at(toggles, (rows[ok], col[ok]), 1)
    return (np.cumsum(toggles, axis=1)[:, :nx] % 2).astype(bool)


def _erode(m, r_cells):
    """Eroded by about r cells: square and cross steps in turn, an octagon."""
    import numpy as np

    out = m.copy()
    for k in range(int(round(r_cells))):
        e = out.copy()
        e[1:, :] &= out[:-1, :]
        e[:-1, :] &= out[1:, :]
        e[:, 1:] &= out[:, :-1]
        e[:, :-1] &= out[:, 1:]
        if k % 2:
            e[1:, 1:] &= out[:-1, :-1]
            e[:-1, :-1] &= out[1:, 1:]
            e[1:, :-1] &= out[:-1, 1:]
            e[:-1, 1:] &= out[1:, :-1]
        out = e
    e = out
    e[0, :] = e[-1, :] = False
    e[:, 0] = e[:, -1] = False
    return e


def _dilate(m, r_cells):
    return ~_erode(~m, r_cells)


def _islands(m):
    """Labels of the 4-connected islands of m and their sizes in cells (scipy
    when there; else a flood of run-lengths)."""
    import numpy as np

    try:
        from scipy import ndimage

        lab, n = ndimage.label(m)
        return lab, np.bincount(lab.ravel(), minlength=n + 1)
    except ImportError:
        pass
    lab = np.zeros(m.shape, dtype=np.int64)
    n = 0
    ny, nx = m.shape
    for j in range(ny):
        for i in range(nx):
            if m[j, i] and not lab[j, i]:
                n += 1
                stack = [(j, i)]
                lab[j, i] = n
                while stack:
                    a, b = stack.pop()
                    for c, d in ((a + 1, b), (a - 1, b), (a, b + 1), (a, b - 1)):
                        if 0 <= c < ny and 0 <= d < nx and m[c, d] and not lab[c, d]:
                            lab[c, d] = n
                            stack.append((c, d))
    return lab, np.bincount(lab.ravel(), minlength=n + 1)


def raster_sliced(V, F, setup, walls, cell=None, sample=60, small=70.0, stats=None):
    """(solid mm³, sparse mm³) of a triangle mesh as a slicer lays it down,
    on a raster: every layer of the setup's height cut and filled; on each,
    the perimeters a band in from the outline (`walls` lines: the outer
    0.45 mm, the next 0.41), what the band leaves narrower than a line gap
    fill, the top and bottom skins where the layers within their count
    above and below do not all have part, infill islands under `small` mm²
    solid, the rest sparse. Plain numpy, the same on every release."""
    import numpy as np

    setup = setup or {}
    lh = float(setup.get("layerHeight", 0.2))
    tops = int(setup.get("topLayers", 5))
    bots = int(setup.get("bottomLayers", 4))
    V = np.asarray(V, dtype=float)
    F = np.asarray(F, dtype=np.int64)
    T = V[F]
    lo, hi = V.min(0), V.max(0)
    if cell is None:
        cell = max(0.1, math.sqrt((hi[0] - lo[0]) * (hi[1] - lo[1]) / 250000.0))
    x0, y0 = lo[0] - 2 * cell, lo[1] - 2 * cell
    nx = int(math.ceil((hi[0] - lo[0]) / cell)) + 4
    ny = int(math.ceil((hi[1] - lo[1]) / cell)) + 4
    n = max(1, int(round((hi[2] - lo[2]) / lh)))
    lh_eff = (hi[2] - lo[2]) / n
    zs = lo[2] + (np.arange(n) + 0.5) * lh_eff
    layers = [_layer_mask(T, z, x0, y0, cell, nx, ny) for z in zs]
    stack = np.array(layers)
    band = (0.45 + max(0, walls - 1) * 0.41) / cell
    line = 0.45 / cell
    small_cells = small / (cell * cell)
    step = max(1, int(math.ceil(n / sample)))
    solid = sparse = 0.0
    st = stats if stats is not None else {}
    st.update({"layers": n, "read": 0, "cell": round(cell, 3)})
    area = cell * cell
    for i in range(0, n, step):
        m = stack[i]
        if not m.any():
            continue
        st["read"] += 1
        # Inside the skins: part in every layer within their count.
        core = m.copy()
        for k in range(1, tops + 1):
            core &= stack[i + k] if i + k < n else False
        for k in range(1, bots + 1):
            core &= stack[i - k] if i - k >= 0 else False
        inner = _erode(m, band)
        # What the perimeters leave narrower than a line: gap fill, solid.
        roomy = _dilate(_erode(inner, line / 2), line / 2) & inner
        free = roomy & core
        if free.any():
            lab, sizes = _islands(free)
            free &= sizes[lab] >= small_cells
        w = step if i + step <= n else n - i
        solid += float((m & ~free).sum()) * area * lh_eff * w
        sparse += float(free.sum()) * area * lh_eff * w
    if solid + sparse <= 0:
        return None
    return solid, sparse
