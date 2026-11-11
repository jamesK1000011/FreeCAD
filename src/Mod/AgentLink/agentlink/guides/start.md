# Start here: Agent Link for FreeCAD

## The shape of a session
- `session_state` first: what the document holds and the step count.
- Tools come in groups. `tool_groups` lists them and turns them on. Every tool can be called by name whether it is listed or not.
- Every change is one undo step: `history_undo`, `history_redo`, `history_checkpoint` then `history_restore`. `history_diff` says what a change did, so you don't have to describe the part again.
- `batch` runs several calls in one, rolled back whole if one is refused. Use it for a run of edits you can predict.
- Answers are in millimetres, grams and degrees. A refusal names what to change.

## A part, made and judged
1. Make it, with `gen_make` or the modelling tools. Check the size first with `gen_make { measure: true }`.
2. Ask how it will be made, then record it: `setup add { type: "print", bodies, settings }`. Don't assume a part is printed.
3. Ask what it is made of and what it must meet: `part_spec { spec, material }`.
4. `part_review` after every change. **pass** means every check ran and nothing needs putting right. **fix** lists the errors worst first. **unknown** means something could not be judged. Look too: `view_render { views: "all" }`, and `{ map: "wall" }` or `{ zoom: { at } }` on a finding.
5. Waive a finding only when the person says it is meant (a stacking lip, say): `part_review { waive }` or `dfm_check { waive }`.
6. Write `export_file` for STEP or STL, or `slice` for G-code. Starting a print is the person's call.

## What is here, by task
| The person wants | Tools | Guide |
|---|---|---|
| A standard part to size (bin, box, bracket) | `gen_make`, `gen_part` | `parts` |
| A parametric part they can change later | `pd_body_new`, `sketch_new`, `sketch_add`, `pd_pad` … , `param_set` | `model`, `sketch`, `feature`, `param` |
| A table of values, sizes in variants (S/M/L), a CSV in or out | `sheet_set`, `sheet_read`, `sheet_table`, `sheet_import` | `sheet` |
| Lettering, a logo from SVG/DXF, copies of a part | `draft_text`, `draft_outline`, `draft_array` | `draft` |
| A curved panel, a shape through points, a surface made solid | `surface_loft`, `surface_points`, `surface_fill`, `surface_thicken` | `draft` |
| Will it break, shake or get too hot | `fea_setup` … `fea_solve`, `fea_modes`, `fea_heat` | `strength` |
| What it weighs as printed, ballast to a target | `mass_properties`, `mass_target`, `mass_ballast` | `mass` |
| A drawing to send (dims, fits, holes, a section) | `drawing_make`, `drawing_exploded` | `drawing` |
| A TechDraw page built piece by piece: sections, details, tolerances, GD&T, title block | `td_page`, `td_view`, `td_section`, `td_dim`, `td_gdt`, `td_export` | `techdraw` |
| How far a made part or a scan is from the design, in tolerance or not | `insp_run`, `insp_read`, `view_render { map: "inspection" }`, `insp_export` | `inspection` |
| Parts put together, a lid on its box | `asm_new`, `asm_joint`, `asm_check` | `assembly` |
| Toolpaths and G-code for a mill | `cam_job`, `cam_op`, `cam_sim`, `cam_post` | `cam` |
| A print: slice it, pause for magnets | `setup`, `slice`, `slice_with` | `slice` |

## Scans
- With ScanRuler for FreeCAD installed, scans are measured, aligned, compared with a reference and cleaned up here too: read `measure-scan`, and `mesh-tools` for cleaning one up.
- A scan lives in the session, not in the FreeCAD file; `section_to_sketch` turns a section of it into a sketch to model from.

## Refusals
Every refusal is `{ error: { code, message } }`; the message says what to change. By code:

| Code | Means | Do |
|---|---|---|
| `invalid_input` | An argument is wrong: a type, a value out of range, an unknown field, a name that is not there | Read the message, correct that argument, call again |
| `invalid_state` | The call is right but not now: no study set up, no print setup, a parameter bound | Do the step it names first |
| `not_found` | No body, feature, face, guide or tool by that name | `session_state`, `object_list`, `feature_list` or `tool_groups` for the names there are |
| `unavailable` | Something this computer lacks: a slicer, the FreeCAD window, a module | Say so to the person; the message names what to install or set |
| `failed` | It ran and did not work: a boolean, a fillet, a mesh, a solve. Nothing was kept | Change the input (a smaller radius, another face, a coarser mesh); never send the same call again unchanged |

- A batch stops at the first refusal and rolls back what it did (`rollback: false` keeps it): fix that call and send the batch again.
- If FreeCAD itself stops (the answer says it was started again), the document was lost: `session_state`, then make the part again or `doc_open` what was saved. The same call doing it again means the call is the cause.

## Keeping answers small
- `view_render` pictures at the default size; `inline: false` writes the file only.
- `object_get` with `filter`; `scan_query` with `limit`; `report_get` once at the end.
- `batch` for runs of predictable calls: one answer instead of many.
