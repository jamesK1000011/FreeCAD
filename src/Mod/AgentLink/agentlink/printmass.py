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
    """Thicknesses and how far each faces up or down, from geometry.least_wall."""
    reads = [r[0] for r in (wall.get("readings") or [])]
    nz = [abs(n[2]) for n in (wall.get("normals") or [])] or [0.0] * len(reads)
    return reads, nz
