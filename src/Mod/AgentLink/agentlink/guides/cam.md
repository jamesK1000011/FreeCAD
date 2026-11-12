# Toolpaths and G-code (CAM)
<!-- SPDX-License-Identifier: LGPL-2.1-or-later -->

FreeCAD's CAM workbench makes the paths; these tools set it up, read each path back and write the G-code. `mill_check` first says whether the part can be milled at all (reach, inside corners, the least tool).

## The workflow
1. Ask the person: the machine's controller (grbl, linuxcnc …), the end mill (diameter), feeds and speeds for the material, how the stock is held and where it is zeroed.
2. `cam_job { body, tool, stock }` — the stock round the part with a margin and a little on top; the end mill.
3. Operations, roughest first, each computed as it is added — read its `cutZ` and `minutes`:
   - `face` — the top flat, when the stock has some on top.
   - `adaptive` — clearing a lot of material steadily (inside a pocket, or outside round the part).
   - `pocket { faces }` — down to a pocket's floor: give the floor face.
   - `profile { side: "outside" }` — round the outline, last, to size.
   - `drill` — every hole, or the faces given. FreeCAD drills with the job's end mill unless given another tool; a hole the tool is not smaller than is refused.
4. `cam_list` — every operation and the total time.
5. `cam_sim` — the operations run on the stock (3-axis, from above): rapids through stock (crashes), cuts into the part (gouges) or under the stock, and what is left over the part, with a picture from above (blue gouged, green right, red left over). Fix anything in `findings` before posting.
6. `cam_post { post }` — the G-code file. Read its `warnings`.

## traps
- Z is the part's: the stock's top is `stock.max[2]`. Zero the machine where the job does.
- A tool bigger than a pocket's corners or a hole leaves them uncut — the op answers with fewer cutting moves or refuses. `cam_tool` changes the mill and recomputes everything.
- `profile` cuts to the part's bottom: with the stock on a spoil board that is right; on a vice set `finalDepth` above the jaws and hold the part with tabs or leave a skin.
- The time is an estimate at the feeds given, rapids at 3000 mm/min.
- The G-code is checked against the stock, not simulated. The person checks it in their controller's preview before it runs; running it is theirs.

## More operations, tools and dressups
- More kinds of `cam_op`: `engrave` and `vcarve` follow `base` (letters from `draft_text`, a sketch); `helix` mills holes; `slot`; `deburr`; `thread` mills a thread (`props` for its size). `surface` and `waterline` are 3D finishing, and need OpenCamLib.
- More than one tool: `cam_tool_add { shape: "v-bit", diameter, angle }`, then `cam_op { tool }` per operation. Ask the person what is in their tool rack; never assume.
- `cam_dressup { op, kind: "tags" }` adds holding tabs, so a profiled part stays in the stock. `dogbone` clears inside corners so a square part fits; `boundary` keeps moves inside the stock. Ramp and lead-in/out need FreeCAD's window; adaptive ops enter helically anyway.
- `cam_setup` sets work offsets (`fixtures`), coolant, safe heights and the order the ops run in.
- `cam_edit` copies an op (props on the copy), turns one off, drops a comment or a stop (M0/M1) into the G-code after an op, or sets an op's start point.
- `props` on any op sets its own FreeCAD properties by name (object_get lists them): for what these tools don't name.
