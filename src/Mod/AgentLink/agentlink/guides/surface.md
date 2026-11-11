<!-- SPDX-License-Identifier: LGPL-2.1-or-later -->
# Surfaces (the Surface workbench)

These features are parametric: each follows the edges it was made from. (`surface_loft`, `surface_points`, `surface_fill` and `surface_thicken` in the draft group make plain surfaces instead.)

## workflow
1. Draw the curves: `part_wire` (B-spline, arc …), sketches, or a body's edges (`"Body.Edge3"`; `shape_query` finds them).
2. Make the surface: `surface_sections` through curves in order, `surface_boundary` from 2–4 edges that meet end to end (`fill: "coons"` is smoothest), or `surface_extend` to run a face on past its edges.
3. Close it up: `surface_sew` sews the pieces into a shell, and `solid: true` makes a closed shell a body. For a single open surface, `surface_thicken` or `part_thickness` gives it a wall.
4. `surface_blend` joins two edges with a smooth curve (`continuity` 1 for tangent, 2 for curvature), which then becomes a boundary for the next surface. `surface_cut` trims one surface by another.

## traps
- Boundary edges must meet end to end within FreeCAD's tolerance. A gap reads as a failure: snap the ends first.
- A sewn shell is solid only if it is closed. The answer says when it is open (a missing face, or a gap wider than `tolerance`).
