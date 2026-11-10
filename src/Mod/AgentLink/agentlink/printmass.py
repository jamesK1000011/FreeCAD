# SPDX-License-Identifier: LGPL-2.1-or-later
"""How much of a printed part is plastic, from its wall readings."""


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


def sliced(shape, setup, walls, levels=40, small=70.0):
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
    try:
        for i in range(n):
            z = bb.ZMin + (i + 0.5) * dz
            f = section(z)
            if f is None:
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
                if inner is not None and inner.Area > 1e-9:
                    for face in inner.common(core).Faces:
                        if face.Area >= small:
                            free += face.Area
            solid += (f.Area - free) * dz
            sparse += free * dz
    except Exception:
        return None
    if solid + sparse <= 0:
        return None
    return solid, sparse


def solid_of(shape, setup, walls):
    """(the share of the part's volume printed solid, how it was found):
    sectioned as a slicer would, else from the wall readings, else None."""
    cut = sliced(shape, setup, walls)
    if cut is not None:
        return cut[0] / (cut[0] + cut[1]), "sections"
    from . import geometry as geo

    reads, nz = readings(geo.least_wall(shape, reach=shape.BoundBox.DiagonalLength + 1.0))
    if reads:
        return solid_share(reads, nz, *shells(setup, walls)), "walls read"
    return None, None
