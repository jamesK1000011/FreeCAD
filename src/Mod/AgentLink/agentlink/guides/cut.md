# Cutting parts from sheet

## The loop
1. Ask how it is cut, then `setup add { type: "cut", bodies, settings: { machine, thickness, kerf } }`. machine: laser, waterjet, plasma or router (a router's kerf is its cutter's diameter).
2. `cut_check` after every change: is it a sheet part, does its thickness match the sheet, are holes, webs and slots big enough, a router's inside corners round enough. Give `material` and `sheet: [w, h]` when you know them.
3. `cut_export { bodies, copies, sheet }` for DXF and SVG, laid out in rows. Read back counts come with it.

## Traps
- A sheet part has two flat faces and every side square to them. A chamfer, a counterbore or a pocket is not cut from sheet: the check names the faces.
- The least features are rules of thumb, under `assumed`. The shop's own figures win: ask for them when it matters.
- `compensate: true` offsets by half the kerf. Most cutting software does that itself: compensate once, not twice.
- One thickness per file. Holes are on layer HOLES, to be cut before the outside.
