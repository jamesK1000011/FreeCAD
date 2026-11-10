# Pictures
<!-- SPDX-License-Identifier: LGPL-2.1-or-later -->

Look at the part after every change that matters, as a person would.
`view_render` draws it headless and hands the picture back.

## Views
- `views: "all"`: iso, top, front and right, each fitted. One name for one view; a list of up to 6.
- `section { axis: "x"|"y"|"z", offset }`: cut open at offset mm (the middle when left out); the half away from the eye is kept. Solid cut faces are hatched red.
- `zoom { at, size }`: frame a spot — a finding's `at`, a map's `worst.at` — `size` mm across.

## Maps
- `wall`: thickness at each spot, straight in from the surface. Red is thin; `reach` (12 mm) reads as thick.
- `stress`: von Mises of the last `fea_solve`; stale when the body changed since.
- `heat`: temperature of the last `fea_heat`.
- Add-ons add theirs: `view_maps` lists them (ScanRuler for FreeCAD: `deviation`, `thickness` of a scan).
- `range: [lo, hi]` fixes the colours, to compare two pictures. The answer gives the worst value and where.

## The window
- `view_snapshot` saves the FreeCAD window's own view, as the person left it (or turned to `view`). Headless it is refused.

## traps
- Grey on a map is unread: an open mesh, a scan past the max distance.
- The picture is the tessellation: a fine curve shows facets; `tolerance` sets the chord.
- Big pictures are big answers: `inline: false` writes the file only.
