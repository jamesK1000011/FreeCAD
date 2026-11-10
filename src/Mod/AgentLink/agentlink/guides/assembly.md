# Assemblies: parts put together
<!-- SPDX-License-Identifier: LGPL-2.1-or-later -->

FreeCAD's own Assembly workbench, its solver and its joints. Parts are bodies already made; an assembly says where they go relative to one another.

## The workflow
1. `asm_new { parts }`: the bodies into a new assembly. The first is **grounded** (held fixed) unless `ground` names another; the solver moves only the others.
2. `asm_joint` between two parts, one at a time, each solved as it is made. Read `moved` (which part went where) and `overlaps` after every joint.
3. `view_render { views: "all" }` — look. A part upside down or inside another is a face picked the wrong way: `history_undo`, then the joint again with `reverse: true`.
4. `asm_check { clearance }` at the end: solves, overlaps (volume and where), pairs closer than the clearance, parts joined to nothing. **verdict clear** is the goal.
5. `drawing_exploded` makes the assembly manual from where the parts now sit; `export_file` each part.

## Joints
- `fixed` — no motion between them: a lid screwed down, a bracket bolted on.
- `revolute` — turns about an axis: a hinge, a wheel on an axle. Pick round edges or faces (the axis is theirs).
- `cylindrical` — turns and slides: a pin in a hole with no end stop.
- `slider` — slides along one direction only: a drawer, a carriage on a rail.
- `ball` — turns any way about a point.
- `distance` — the two elements `distance` mm apart: a gap, a spacer.
- `parallel`, `perpendicular`, `angle` (`angle` degrees) — orientation only.

## Picking the elements
- Each end is `{ part, element }`. The element is a query as `shape_query` takes it: `{ kind: "face", normal: [0, 0, 1] }` (the flat face facing up), `{ kind: "edge", type: "circle", radius: 3 }` (a 6 mm hole's rim), `near: [x, y, z]` to take the closest of several. A name ("Face6") works but changes when the part is edited.
- A flat face: the joint sits at its middle, along its normal. A round edge or a cylindrical face: on its axis — the right pick for anything that turns or slides on a shaft.
- Faces that meet face to face (a lid's underside on a box's rim) have opposite normals: up on one part, down on the other. `offset` mm moves b off along the normal (a gap); `rotate` degrees turns it about it.

## traps
- Only `b`'s part moves. Make joints from the grounded part outwards.
- A joint that holds what another already holds is **redundant** and the solve refuses it: nothing is kept. Fixed after a revolute on the same pair is the common one.
- An assembly needs one grounded part: `asm_ground`.
- The solve moves parts; it does not check they fit. `asm_check` does — a press fit reads as an overlap of its interference volume.
