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
