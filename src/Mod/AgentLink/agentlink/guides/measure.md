# Measuring and picking geometry
<!-- SPDX-License-Identifier: LGPL-2.1-or-later -->

## workflow
- `shape_query` finds faces, edges and vertices by type, normal or axis, radius, area, length, nearness to a point or a box: the references ("Face12", "Edge3") the modelling tools take. `of: "Face6"` gives that face's edges.
- `measure { a }` reads one thing (area, radius, diameter, length, volume); `measure { a, b }` the least distance, and the angle between flat faces or lines (and the gap between parallel faces).
- `section` cuts the body with a plane: area and outlines as [u, v] points in the plane.
- `measure_mass` with a material or density (solid; the mass group's `mass_properties` weighs a part as printed); `clearance` between two bodies says clear, touching or interference with the overlap's volume.

## traps
- References go stale after a change: query again.
- References are "Face12" with `body`, or "Box.Face12"; a bare body name means the whole body, a list [x, y, z] a point.
- Normals are outward; `axis` matches either way along a line or a hole's axis.
- `measure_mass` treats the body as solid; a printed part with infill weighs less — `part_review` estimates that.
