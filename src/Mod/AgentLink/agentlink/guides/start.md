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
4. `part_review` after every change. **pass** means every check ran and nothing needs putting right. **fix** lists the errors worst first. **unknown** means something could not be judged.
5. Waive a finding only when the person says it is meant (a stacking lip, say): `part_review { waive }` or `dfm_check { waive }`.
6. Write `export_file` for STEP or STL, or `slice` for G-code. Starting a print is the person's call.
