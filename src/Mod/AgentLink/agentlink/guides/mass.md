# Mass and ballast

## Reading it
- `mass_properties`: mass, centre of mass and inertia per body. In a print setup the mass is as printed (walls and infill, an estimate, the skin's mass on the surface); otherwise solid. Magnets from gen_make and ballast cut here are counted.
- Weigh a test print when the mass matters: the slicer (`slice`) and the scale beat the estimate.

## Reaching a target
1. `mass_target { mass, com }` says how far off it is, and what ballast (mass and centre) would meet it. `com: [x, null, null]` leaves y and z free.
2. `mass_ballast { mass, com, fill, diameter, minWall }` lays out closed upright pockets; check before and after, then `cut: true`.
3. Ballast only adds. A part already too heavy needs material taken away.

## Traps
- Pockets are closed: the print needs a pause at `printing.pauseAt` to fill them. Weigh each pocket's fill.
- Loose shot fills about 0.6 of a pocket (`packing`). A cast slug or a glued bar is nearer 1.
- The wall kept around a pocket is the spec's least wall (part_spec minWall) unless given.
