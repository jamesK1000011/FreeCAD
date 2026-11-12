# Modelling with Part
<!-- SPDX-License-Identifier: LGPL-2.1-or-later -->

## workflow
1. Block the part out with `part_primitive` (box, cylinder …; `at` and `rotate` place it) or `part_extrude` / `part_revolve` a profile (`{ points }`, `{ circle }`, a sketch, or a body's face "Box.Face6").
2. Add and remove material: `part_boolean { op, base, tools }`, or in one call `part_extrude { profile, into, op: "cut" }` for a pocket or `op: "fuse"` for a boss.
3. Repeat and mirror with `part_transform` (linear, polar, mirror { fuse: true }).
4. Shell with `part_thickness`, then round and bevel last: `part_fillet` / `part_chamfer` with edges picked by `shape_query` or a `query`.
5. `body_check` or `part_review` after each change; `history_diff` says what it did.

## traps
- Face and edge numbers change after every modelling step: query again (`shape_query`) rather than reuse old references.
- A result keeps the base body's label, so setups and specs follow it; the tools are removed (keep: true hides them, and a hidden body is still listed).
- A refusal for "changed nothing" means the pieces do not overlap: move them (`part_transform move`) and try again.
- Fillets bigger than a face beside the edge, or shells thinner than a fillet, fail: smaller sizes, or the shell before the fillet.
- Loft profiles with different edge counts twist; start each at the matching corner.

## Wires, splitting, repair
- `part_wire` makes FreeCAD's own parametric curves: a helix as a sweep path (a spring, a thread), a spiral, a polygon, a plane to slice with, a B-spline through points.
- `part_split { mode: "slice", bodies: [one], tools: [a plane or bodies] }` cuts a body into pieces; `explode: true` makes each its own body. `fragments` keeps every overlap region apart (for multi-material prints); `xor` keeps what is in an odd number.
- `part_compound` groups bodies without fusing them, or `explode`s a multi-solid body.
- `part_refine` merges faces split by booleans; `fix: true` also repairs tolerances and closes a shell into a solid.
- `part_attach` makes a primitive follow a face. `part_face` is a face from closed wires, `part_ruled` a surface between two edges, `part_project` puts a wire onto a face.
- `part_slices` gives a stack of cross-sections: outlines and areas. `part_from_mesh` turns a closed mesh into a solid. Curved areas stay faceted; for a clean model, fit features instead.
- `part_section` gives the curves where a body meets another body or a plane (face: true fills closed loops). `part_offset2d` grows or shrinks a flat outline in its plane (fill: true makes the band). `part_extract` copies faces, edges or whole shapes out as a body of their own, or builds a wire, face, shell or solid from them. `part_defeature` takes a hole, boss or fillet off a solid by all its faces. `part_fix` reverses, sews, closes into a solid or repairs. `part_datum` makes datum points, lines, planes and coordinate systems, attached so they follow.
