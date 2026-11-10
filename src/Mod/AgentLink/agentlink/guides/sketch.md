<!-- SPDX-License-Identifier: LGPL-2.1-or-later -->
# Sketches in a PartDesign body

## workflow
1. `pd_body_new`, then `sketch_new { plane: "XY" }` — or on a face of the body: `face: { at: [x,y,z], normal: [0,0,1] }`. The answer's `frame` says where sketch x/y lie in the world.
2. `sketch_add` the profile: rectangle, rounded_rectangle, circle, polygon, slot, polyline … Shapes come with their own coincident/horizontal/vertical constraints. `dims: true, name: "plate"` adds their sizes as named constraints (`plate_width`).
3. `sketch_constrain` the rest: refs are FreeCAD's — geometry id, `[id, vertex]` (1 start, 2 end, 3 centre), `"origin"`; -1 the horizontal axis, -2 the vertical.
4. `sketch_state` before a feature: `readyForFeature`, `dof`, conflicts, open vertices.

## traps
- A pad works on a sketch with dof > 0, but the profile can slide when a value changes. Fix it to the origin and dimension it for a parametric part.
- A constraint that conflicts or is redundant is refused and nothing is added. Read the constraint ids in the message and change or delete one of them.
- Deleting geometry renumbers every later id. Read `sketch_state` again before you constrain more.
- A sketch on a face follows that face. If an edit removes the face, the sketch breaks: `feature_list` shows it invalid.
- Angles are in degrees, counter-clockwise from +x. Lengths are in mm.

## Curves, offsets, outside edges
- Free curves: `bspline { points }` passes through the points (`closed: true` for a loop); `interpolate: false` takes them as control poles with `degree`. `ellipse { center, major, minor, rotation }`.
- `sketch_offset { distance }` — the profile moved out (negative: in) as new geometry: a wall's inside, a lid's lip, a clearance band. Round corners by default; `join: "intersection"` keeps them sharp. The offset does not follow later edits unless constrained.
- `sketch_external { edges }` brings the body's own edges in (negative ids from -3) so a new profile is constrained to what is already there — a hole on a boss's axis, a rib off a wall — and follows when the part changes.
