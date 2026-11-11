<!-- SPDX-License-Identifier: LGPL-2.1-or-later -->
# PartDesign features

## workflow
1. Make a sketch ready (`sketch_state` reads `readyForFeature`), then `pd_pad`, `pd_pocket`, `pd_revolution`, `pd_groove`, `pd_hole`, `pd_loft` or `pd_pipe`.
2. Finish with `pd_fillet` / `pd_chamfer` (edges), `pd_draft` / `pd_thickness` (faces), and `pd_mirrored` / `pd_linear_pattern` / `pd_polar_pattern` (features by name).
3. Each answer gives the body's volume, faces and size after the change, and the volume change.
4. `feature_list` shows the history. `feature_edit` changes values. `feature_suppress` and `feature_remove` take features out.

## traps
- Name faces and edges by where they are: `{ at, normal }` for a face, `{ near }` for an edge. Do not use an index. Indices change after every edit, which is the topological naming problem.
- A pocket cuts against its sketch's normal. One that cuts nothing is refused: use `reversed: true`, or put the sketch on the face the pocket enters.
- A pad that adds nothing (inside the body already) is refused.
- Holes drill at the centre of each circle in the sketch, against the sketch's normal. Put the sketch on the face the holes enter.
- A fillet or chamfer larger than a face beside it fails. Try a smaller value or fewer edges.
- A failed feature is refused with FreeCAD's own error and nothing is kept. The document stays as it was.
- To change a property bound to a parameter, use `param_set`. `feature_edit` refuses it.

## More features
- `pd_primitive { kind: "cylinder", size: { radius, height }, at, rotate }` adds a box, cylinder, sphere, cone, ellipsoid, torus, prism or wedge without a sketch. `subtractive: true` cuts it out instead: a quick bore or slot.
- `pd_helix { profile, axis, pitch, height }` makes a spring or a coil; `subtractive: true` cuts a thread or a groove. Give two of pitch, height and turns. The profile must not cross the axis, and its height should be under the pitch.
- `pd_multitransform { features, steps }` chains patterns. A grid is two linear steps; `scale` steps grow the copies.
- `pd_datum` makes a line, point or coordinate system: placed from the origin, or `on: { near }` a round edge (its axis or centre), which then follows the model. A datum line serves as an axis by name.
- `pd_binder { from: "Other body", faces }` brings another body's faces in to sketch on (sketch_external). `pd_boolean { op, with }` fuses, cuts or keeps the common part of other bodies.
