<!-- SPDX-License-Identifier: LGPL-2.1-or-later -->
# TechDraw, piece by piece

`drawing_make` makes a whole sheet in one call. These tools build a TechDraw page one object at a time, so the drawing says exactly what the person asks and they can carry on with it in the GUI.

## workflow
1. Ask about the projection first: first angle (ISO, Europe) or third angle (ASME, North America). Then `td_page { action: "new", paper: "A3", projection: "first" }`. `td_page { action: "templates" }` lists the build's templates; `template` takes any SVG.
2. Fill in the title block with `td_title { fields }`. With no fields it lists the template's own field names. Ask the person for the names, numbers and dates; nothing is filled in for them. A preset saves their usual fields: `td_title { preset: "mine", save: true }`, then `{ preset: "mine" }` on the next page. `export` and `import` move presets between machines as JSON.
3. Put the part on with `td_projection { views: ["front", "top", "right", "iso"] }`, or a single view with `td_view { direction, scale, at }`.
4. Add sections and details: `td_section { view: "Front", normal: "y", through: [x, y, z] }` and `td_detail { view, center: [x, y, z], radius }`.
5. Dimension: `td_geometry { view, kind: "circles" }` shows what can be measured, then `td_dim { view, type, refs }`. Name geometry by the part where you can: `{ circle: { diameter: 6, near: [x, y, z] } }`, `{ point: [x, y, z] }`, `{ edge: { through: [p, q] } }`.
6. Tolerance it: `td_dim { …, fit: "H7" }`, or plus and minus, or `basic: true`; `td_tolerance { dimension }` changes one later.
7. Add GD&T: `td_datum { view, letter: "A", on }`, then `td_gdt { view, on, frame: [{ characteristic: "position", tolerance: 0.1, diameter: true, modifier: "M", datums: ["A", "B"] }] }`.
8. `td_list` reads the page back. `td_export { formats: ["pdf", "dxf"] }` writes it out.

## coordinates
- Page `at` is in millimetres from the paper's bottom-left corner, y up.
- A dimension's or balloon's `at` is its text's offset from its view's centre, in page millimetres.
- `td_geometry` gives millimetres on the part, in the view's own plane.

## traps
- A view's lines are worked out in the background. A view that has just been made answers with its edge count; if it is 0, the source is empty or hidden.
- A section's `normal` points toward the person looking at the section, and it must lie across the base view (along the paper, not out of it).
- A point that is hidden in a view has no vertex there. Dimension it in a view that sees it, or in a section.
- Form tolerances (flatness, straightness, circularity, cylindricity) take no datum. Orientation, runout, concentricity and symmetry need one. A refusal says which.
- SVG and PDF are drawn here from the page's objects: views, dimensions, frames, leaders, notes, balloons and tables. Hatching and the template's artwork are left out (the title block's fields are kept). DXF comes from TechDraw itself.

## centre marks and hatching
- `td_centermarks` puts thin chain crosses on the holes and round edges of views; `extend` is how far past the circle they run.
- A section's cut faces are hatched in exports by themselves. `td_hatch { view, faces, spacing, angle }` hatches other faces of the part; holes stay clear. A face seen edge-on has nothing to hatch, and the answer says so.
- `td_weld` puts a weld symbol on a leader from a feature (fillet, V, bevel, square, U, J, plug, spot, seam, flare, backing, surfacing), with size, length or pitch, arrow or other side, all-around, field weld and a tail note.
- `td_cosmetic` adds centrelines between a shaft's or hole's sides, thread marks (side and end views, ISO coarse pitch by default), and cosmetic lines and circles. `td_leader` points a note at a feature. `td_finish` puts an ISO 1302 surface texture symbol on a surface (Ra, machining required or not, process, lay). `td_area` writes a face's area with a leader.
- `td_dimset` dimensions several points at once along one direction: `chain` (each to the next) or `baseline` (each from the first, in stacked lanes). A hole is dimensioned to its centre with `{ center: { diameter, near } }`, which also works in `td_dim`.
