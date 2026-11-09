# Parts: standard parts made to size

## Which generator
- A Gridfinity bin: `gen_make`. Its baseplate: `gen_part { generator: "gridfinity_baseplate", params: { x, y, style } }`. `style: "magnets"` puts the pockets on a floor with 6x2 magnet holes where a bin's magnets land; the magnets press in from above, no pause needed.
- A box with a lid: `project_box`. `bosses: true` adds corner screw bosses, pilot holes and lid holes for the screw named.
- A box round a board: `board_box` by hand, or `board_enclosure` after `kicad_import`, which reads the holes, heights and edge connectors for you.

## Traps
- Sizes are outside sizes. `measure: true` first when the person gave an inside size, then adjust.
- Holes are sized with the printer's calibration (`fit calibrate`). Without one the typical printer is assumed; say so when it matters (press-fit magnets, self-tapping screws).
- The lid is laid beside the box top down, as it prints. Its holes and notches are mirrored in Y for that; don't mirror them again.
- A board_box opening that would reach within 0.8 mm of the rim is opened through the rim instead. The lid's lip then crosses it from inside: lower `lip` if the connector needs the room.
