# Electronics: boards, enclosures, orders, channels

## From a KiCad board to its box
1. `kicad_import { path }`. Read `noHeight`: components with no height known get no box and no headroom. Give `heights { ref: mm }` for the tall ones and import again.
2. `board_enclosure`. It opens the walls for the edge connectors found; check `openings` against the board, and `openingHeightGuessed`.
3. `part_review` each body, then `drawing_make` or `export_file`.

## Ordering boards
- `pcb_order_package { files, vendor }` with the fabrication outputs' full paths. It packages and checks; it never uploads or orders.
- If DRC was not run, say so plainly and point to KiCad's Design Rules Checker.

## Channels for wire and traces
- `printed_channel { body, path }`: ends on the part's faces open, ends inside are blind. Keep the path at least half the section's size plus a wall away from every face, or the channel breaks out.
- Conductive filament resistance is a typical figure and rises several times across layers. Measure a printed sample before relying on it.
- `fill: "wire"` with `awg`: `fits` says whether the wire goes through; insulation is extra.
