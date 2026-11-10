<!-- SPDX-License-Identifier: LGPL-2.1-or-later -->
# Agent Link for FreeCAD (spike)

An agent drives FreeCAD over the Model Context Protocol, in ScanRuler's
manner: one call per job, small answers in millimetres and grams, and
refusals that say what to do instead. There are 145 tools in 27 groups.
The lean list (the default) shows the core and the make-check-verdict
loop; `tool_groups` shows the rest. Any tool can be called by name
whether it is listed or not. Every change is one FreeCAD undo step.

| Group | Tools |
|---|---|
| core | `session_state`, `tool_groups`, `guide_read`, `batch` (rolls back whole), `history_undo` / `_redo` / `_checkpoint` / `_restore` / `_diff`, `body_check`, `export_file` |
| doc | documents, objects and their properties, recompute, import and export (STEP, IGES, BREP, STL, OBJ, 3MF, DXF) |
| view | `view_render`: the parts drawn headless — any view or four at once, cut open, zoomed to a spot, with maps (wall thickness, FEA stress, a scan's deviation) and a legend — returned to the agent as an image; `view_snapshot` of the FreeCAD window |
| model | Part primitives, booleans, transforms and arrays, fillet, chamfer, offset, shell, extrude, revolve, loft, sweep |
| measure | `shape_query` (faces and edges picked by kind, normal, radius, place: the references other tools take), measure, section, `measure_mass`, clearance |
| sketch, feature, param | PartDesign bodies, sketches and constraints with a ready-for-feature check; pad, pocket, revolve, groove, hole (ISO threads), fillet, chamfer, draft, shell, mirror, patterns, loft, pipe; spreadsheet parameters bound to features |
| parts | Gridfinity bins (`gen_make`) and baseplates, project boxes and board enclosures (`gen_part`) |
| make | setups (print, cut, mill), part spec and material, printability (`dfm_check`), `part_review`, `slice` |
| fits, embed | fits, printer calibration, tolerance stacks, hardware holes; parts sealed in mid-print with their pauses |
| material, mass, strength, thread | FreeCAD's material library and custom materials; mass as printed, centre of mass, ballast to a target; FEM with CalculiX: strength (print-aware), natural frequencies, steady heat against where the material softens; thread tables, identification and cutting |
| draft | `draft_text` (lettering raised or cut into a flat face from a TrueType font), `draft_fonts`, `draft_outline` (a logo or shape from SVG or DXF, raised, cut in or extruded), `draft_array` (copies of a body in a grid, round a circle or along a path), `surface_fill` (a smooth surface across a boundary), `surface_loft` (through curves), `surface_points` (fitted to a grid of points), `surface_thicken` (any surface made a solid) |
| assembly | FreeCAD's Assembly workbench: `asm_new` (parts, one grounded), `asm_joint` (fixed, revolute, cylindrical, slider, ball, distance, parallel, perpendicular, angle between faces and edges picked by query, solved), `asm_check` (overlaps, clearances, parts left floating), `asm_list`, `asm_ground`, `asm_add`, `asm_joint_remove` |
| cam | FreeCAD's CAM workbench: `cam_job` (stock and end mill), `cam_op` (profile, pocket, adaptive, facing, drilling — each computed and read back), `cam_sim` (the operations run on a height map of the stock: crashes, gouges, what is left, a picture), `cam_post` (G-code by FreeCAD's post-processors, checked against the stock), `cam_list`, `cam_tool`, `cam_remove` |
| cut, mill | sheet-part checks with DXF/SVG nested on the sheet; milling checks with STEP and a setup sheet |
| slice, printer | PrusaSlicer, OrcaSlicer and Bambu Studio command lines with pauses written in; OctoPrint, Moonraker and PrusaLink printers. Starting, pausing or stopping a print needs the person's typed confirmation |
| drawing, electronics, share | TechDraw sheets (feature dims, fits and hole callouts on the page as TechDraw's own dimensions), exploded assembly steps with a bill of materials, patent figures and a draft for an attorney's review; KiCad board import, board enclosures, PCB order packages, printed wiring channels; a library pack with licence, credits and private paths removed |

Each group has a guide (`guide_read`).

## Two ways to run it

**Headless.** Use a Python that can import FreeCAD (conda-forge's `freecad`,
or `FREECAD_LIB` pointing at FreeCAD's `lib`), or FreeCAD's own command line:

```
python src/Mod/AgentLink/agentlink_headless.py --out ~/Documents/AgentLink
AGENTLINK_OUT=~/Documents/AgentLink FreeCADCmd src/Mod/AgentLink/agentlink_headless.py
```

**In the FreeCAD window, so you can watch.** Copy or link this folder into
FreeCAD's `Mod` folder. Pick the Agent Link workbench and press **Start
link**; it listens on 127.0.0.1:7318 only. Then the MCP client starts the
relay:

```
python src/Mod/AgentLink/agentlink_headless.py --connect 7318
```

Each call runs on FreeCAD's own thread, in the document you have open.

## Tests and the bake-off

`python -m unittest discover -s src/Mod/AgentLink/tests` runs the protocol tests
anywhere. The tool tests need FreeCAD and are skipped without it.

The workflow `.github/workflows/freecad-bakeoff.yml` runs the tool tests
and the bake-off on GitHub's machines on every push to `freecad-spike`.
The bake-off is one Gridfinity brief (`scripts/bakeoff/brief.mjs`) given to
this add-on headless and to ScanRuler in headless Chrome. ScanRuler's
Deviation workspace then compares the two bins both ways, and the
scorecard goes to the run's summary page.

## Licence

LGPL-2.1-or-later (see `LICENSE`), so it can sit beside FreeCAD's
own code. It uses only FreeCAD's public Python API and copies none of
ScanRuler's code. The Gridfinity dimensions come from the open
specification.

FreeCAD's `AI_POLICY.md` does not accept AI-generated pull requests. This
add-on is not for upstreaming as it is.
