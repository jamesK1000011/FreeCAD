# Lettering and surfaces
<!-- SPDX-License-Identifier: LGPL-2.1-or-later -->

## Lettering
- `draft_text { body, text, size, depth }` raises text from the largest face facing up; `face` picks another (`{ normal: [0, -1, 0] }` the front), `at` a place on it, `rotate` turns it, `mode: "engrave"` cuts it in.
- For printing: letters at least 5 mm tall, raised 0.6–1 mm or cut 0.4–0.6 mm. On a face that prints facing down (on the bed) emboss reads mirrored and engrave bridges: put text on top or on a wall.
- `draft_fonts` lists the fonts; a bold, simple face (sans) prints best. The answer warns when the strokes are thinner than a printed line, and when the text runs off the face.
- Look at it: `view_render { zoom: { at, size } }` on the answer's box.

## A logo or shape from a file
- `draft_outline { path, body, width, depth, mode }` — the closed outlines of an SVG or DXF (holes stay holes), scaled to `width` or `height` mm: `emboss` or `engrave` on a body's face (as lettering), or `extrude` into a part of its own (a sign, a stencil, a badge).
- Text in the file must be paths first (Inkscape: Path → Object to Path); images are not outlines (trace them first). Open curves are left out and counted — close them if they matter.
- `mirror: true` for artwork printed face down on the bed, or read through clear material.

## Surfaces
- `surface_fill { edges, thickness }` — a smooth surface across a closed boundary (edges of bodies, or a sketch's outline), thickened into a solid when `thickness` is given. Then `part_boolean` joins it to a part.
- `surface_loft { curves, thickness, side }` — a surface through curves in order: edges, sketches, or `{ points: [[x,y,z] …] }` for a smooth curve through them. Open curves make a sheet (a curved panel), closed ones a tube (a duct between two outlines). `ruled` for straight lines between them.
- `surface_points { rows, tolerance }` — a smooth surface through a grid of points, row by row: points measured off something, or a shape worked out from numbers. `farthestPoint` says how close it came; `tolerance` trades closeness for smoothness.
- `surface_thicken { surface, thickness, side }` — any surface (or a body's face) as a solid: `side` out along its normal, in, or both. At least 0.8 mm for a 0.4 mm nozzle; a surface bent tighter than the thickness cannot be thickened that way.
- A surface alone does not print: thicken it, then `part_review` it like any part.

## Copies of a part
- `draft_array { body, kind: "grid", count: [3, 2] }` — copies side by side with a `gap` (2 mm) between, or `spacing [dx, dy, dz]`; `polar { count, center, axis, angle }` round a circle; `path { path, count, align }` along a curve.
- Joined into the body by default (a wheel's spokes, a row of teeth); `separate: true` makes each copy its own body — a batch to lay out for printing. A feature repeated inside one parametric part is `pd_linear_pattern` / `pd_polar_pattern` instead: it stays editable.

## A surface texture (BumpMesh)
- Name the faces that must not change first — mating faces, press-fits, threads, a face another part slides on — and say them back to the person.
- `texture_prepare { body, faces, mode }` writes the part as an STL and a mask of exactly those faces' triangles: `include` textures only them (a grip), `exclude` all but them. It answers the `bumpmesh_texture_file` call to make (BumpMesh's own engine, its library textures, depth `textureHeight`, tile `scaleU`).
- Check what came back: `texture_check { body, path }` — the depth on each textured face, `leaked` (a face meant to stay plain that moved: fix the mask, texture again), `bare` (meant for texture, barely moved: the texture is finer than `refineLength`), and whether the mesh is closed. Only an `ok` goes to the slicer. A textured part is a mesh: slice it as it is.
