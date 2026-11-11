<!-- SPDX-License-Identifier: LGPL-2.1-or-later -->
# Buildings (BIM / Arch)

All sizes are mm; quantities come back in m, m² and m³. Z is up; a level's elevation is its floor.

## workflow
1. Walls: `bim_wall { points, closed, width, height, align }` — or several walls, one a side. A wall along a sketch follows it (`base`).
2. Openings: `bim_opening { wall, kind, preset, width, height, at, sill }`. `at` is the opening's left edge, in mm along the wall from its first point. The opening is cut through the wall; `wallCut_m3` shows it was.
3. Structure: `bim_structure` makes a `column`, a `beam` from–to, a `slab` over an outline, or a `footing` under one.
4. Roof, stairs and rooms: `bim_roof` over a closed outline at the eaves (set 90 on an edge for a gable end); `bim_stairs` (check `blondel`, 600–650 mm); `bim_space` for each room's floor area.
5. Order: `bim_level` for each storey (its `objects` and `elevation`), then `bim_building { levels }`, which puts it on a site too.
6. Check: `bim_schedule` gives quantities with totals; `sheet: true` writes them to a spreadsheet. `bim_list` shows the tree.
7. Exchange: `bim_export` writes IFC and `bim_import` reads it. Both need IfcOpenShell; without it they say so — installing it is the person's step.

## traps
- `at` + width must stay on one straight stretch of the wall; an opening across a corner is refused.
- Look at the result with `view_render`; `box` and `min` in each answer give where it landed.

## more elements
- `bim_curtain_wall`: glazing along points, with mullions every `vertical` / `horizontal` mm.
- `bim_rebar`: straight bars in a slab, beam, column or footing (`along`, `layer`, `cover`, `spacing`). It answers the steel's total length and mass.
- `bim_panel` (sheet material), `bim_pipe` (along points), `bim_equipment` (any object made a fixture or furniture), `bim_grid` (structural grid lines).
- `bim_material` makes a material with its density and puts it on objects. `bim_section` makes a section plane for TechDraw; `export { section: z }` writes a plan cut as DXF/SVG.
- `bim_member` puts a library section (HEB200, IPE300, RHS …; `bim_profiles` lists them) between two points: a beam across, a column upright. It answers mass and kg/m (root radii left out, so a little light).
