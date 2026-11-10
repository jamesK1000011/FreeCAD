# Lettering and surfaces
<!-- SPDX-License-Identifier: LGPL-2.1-or-later -->

## Lettering
- `draft_text { body, text, size, depth }` raises text from the largest face facing up; `face` picks another (`{ normal: [0, -1, 0] }` the front), `at` a place on it, `rotate` turns it, `mode: "engrave"` cuts it in.
- For printing: letters at least 5 mm tall, raised 0.6–1 mm or cut 0.4–0.6 mm. On a face that prints facing down (on the bed) emboss reads mirrored and engrave bridges: put text on top or on a wall.
- `draft_fonts` lists the fonts; a bold, simple face (sans) prints best. The answer warns when the strokes are thinner than a printed line, and when the text runs off the face.
- Look at it: `view_render { zoom: { at, size } }` on the answer's box.

## Surfaces
- `surface_fill { edges, thickness }` — a smooth surface across a closed boundary (edges of bodies, or a sketch's outline), thickened into a solid when `thickness` is given. Then `part_boolean` joins it to a part.
