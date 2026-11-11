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
- A joint between two flat faces puts their middles together: a post joined to a plate's top moves to the plate's middle. To keep a part where it stands, `asm_ground` it instead of joining it, or join it by an element that is where it should meet (a hole's edge, a pin's face).
- Only `b`'s part moves. Make joints from the grounded part outwards.
- A joint that holds what another already holds is **redundant** and the solve refuses it: nothing is kept. Fixed after a revolute on the same pair is the common one.
- An assembly needs one grounded part: `asm_ground`.
- The solve moves parts; it does not check they fit. `asm_check` does — a press fit reads as an overlap of its interference volume.

## Moving it through its range
- `asm_sweep { joint, from, to, steps }` turns a revolute joint through degrees (a slider or cylindrical one along its axis, mm), solving and checking for overlaps at each step, then puts every part back. `collidesAt` lists where parts run into each other; `clearFromStart` the range that is clear from the start.
- Does a lid open to 110°, does an arm swing past a post, does a drawer slide out: sweep it before printing. Steps are a sample — a thin part can pass between two: more steps to look closer, `clearance` to list near misses too.

## Fits
- `asm_fits` reads the hole and the shaft each joint puts together: both diameters, the clearance (minus is interference), how far they engage, and how far their axes are apart. Pass `joint` for one, or `pairs: [{ a, b }]` for parts with no joint.
- `fit: "H7/g6"` holds them to ISO 286, giving the fit's type and clearance range and whether each part is in its band. The nominal is read off the parts unless `size` says it.
- A model drawn at nominal sizes fails a shaft band like g6 (it wants the shaft a little under). Say so, then size the part into the band, or keep the fit as a callout for the drawing (`td_dim { fit }`).

## Poses and limits
- `asm_limits { joint, min, max }` sets FreeCAD's own limits on a joint. `asm_sweep` then sweeps between them by default.
- `asm_pose { joint, to }` moves a joint and leaves it there: the lid open at 90° for a render or a drawing. Values count from where the joint was made, as in `asm_sweep`. `home: true` returns it.

## Exploded layout and parts list
- `asm_explode { factor }` spreads the parts out from the middle; `axis: "z"` stacks them along an axis in the order they sit. Then render it, or make `drawing_exploded`, and `asm_explode { collapse: true }` puts them back. Don't solve or add joints while exploded: the solver pulls the parts home.
- `asm_bom` is FreeCAD's own bill of materials, with material, mass and volume from `part_spec`. `td_table { sheet }` puts it on a drawing.

## Sub-assemblies
- Build the sub-assembly as an assembly of its own, then `asm_sub { assembly: "Machine", sub: "Hinge" }` puts it in. It is rigid by default (one part to the parent); `rigid: false` lets its own joints keep moving.
- Joint to it like any part: `b: { part: "<its name in the parent>", element }`. Its faces are numbered across all of its parts, so pick by query with `near`.
- `asm_bom` itemises a sub-assembly's parts under it (2, 2.1, 2.1.1 …), as FreeCAD's BOM does.

## couplings
`asm_joint` kinds `gears`, `belt`, `rackpinion` and `screw` tie one motion to another, for `asm_sweep`. Give `radius1`/`radius2`, or the wheels' round edges and the radii are read off them; for a screw, `pitch` is mm per turn. They need each part's own joint (revolute, slider) as well.
