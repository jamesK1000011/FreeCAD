# Parts encased mid-print

## The loop
1. The body needs a print setup with its layer height: the pause lands on its layer grid.
2. `hw_embed { kind, size, at: [x, y, z] }`: at is the centre of the pocket's floor. The pocket is sized with the printer's fit calibration (`fit calibrate` first if it is known) and its top rounded up to a layer.
3. `hw_pauses` lists every pause, layer numbers and heights, merged when two fall on one layer.
4. `slice_with` writes the pauses into the G-code.

## Traps
- The part goes in from above, upright. A pocket that would open the part, or whose roof is under 2 layers, is refused: lower it or thicken the part.
- `hw_unembed` fills the pocket again; `keepPocket: true` leaves a hole to press the part into after printing.
- history_undo of an hw_embed drops its pause too: the lists read the body as it is.
