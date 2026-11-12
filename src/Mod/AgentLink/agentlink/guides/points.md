<!-- SPDX-License-Identifier: LGPL-2.1-or-later -->
# Point clouds (Points, Reverse Engineering)

## workflow
1. `points_import` reads a cloud: .asc/.xyz/.txt/.csv (x y z a line), .pcd, .ply, .e57. `every` thins a huge file as it comes in; `scale` turns metres or inches to mm.
2. `points_info` gives the count, extent and typical spacing; `view_render` shows it.
3. `points_crop` isolates one feature before a fit: a `box`, a ball (`near` + `radius`), the side of a `plane`; `outside: true` keeps the rest instead; `thin` (mm) keeps one point per cell.
4. `points_fit` through the cropped points:
   - curve: a B-spline through a line of points (an edge scan); a loop round a centre closes itself.
5. `points_mesh` makes a mesh: height (one side, gridded), hull, or poisson. `points_merge` joins clouds.
   - `plane`: centre and normal (a face's flatness is its `max`);
   - `sphere`: centre and diameter;
   - `cylinder`: base, axis, diameter and length — a bore or a shaft; `axis` is a hint for a short one;
   - `surface`: a free B-spline (Reverse Engineering), always made a body.
   Read `rms` and `max`: the points' distance from the shape. A large `max` means the crop holds more than the one feature. `tolerance` gives a verdict; `make: true` makes the shape a body to model on.
5. `points_export` writes .ply (binary), .pcd, .asc/.xyz or .csv.

## testing a fit
`points_sample` spreads points over a body or mesh, with `noise` in mm, so a fit can be tried on a known shape first.

## traps
- A cylinder fit needs the points of that cylinder only. A floor or a chamfer left in pulls it off.
- A scan from ScanRuler for FreeCAD lives in the session, not in the document. Its own tools measure it; export it as a cloud to use these.
