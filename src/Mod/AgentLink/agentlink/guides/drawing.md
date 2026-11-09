# Drawing: sheets, manuals, patent figures

## A drawing sheet
- `drawing_make` gives SVG, PDF and DXF files and a TechDraw page with the same views. Ask which projection the person works in: first angle (ISO, Europe) or third angle (ASME, North America).
- Only the overall sizes are dimensioned. Holes, slots and fits want dimensions placed in TechDraw by the person, on the page made.
- `scale` is refused when it does not fit; leave it out for the largest standard scale that does.

## An assembly manual
- `drawing_exploded { steps: [{ title, add: [bodies], note }] }`, in build order. Bodies the same shape are counted as one item in the bill of materials.
- Record materials (`part_spec`) and setups first, or the bill has no mass or process for them.

## Patent figures and a draft
- `patent_figures` then `patent_draft`, so the draft uses the same numerals and figures.
- The draft is a starting point for a registered patent attorney or agent. Never present it as ready to file, and never file, send or upload it: that is the person's and counsel's.
- Formal drawing rules change, and an examiner may ask for changes. The figures follow the USPTO's margins and lettering size; say that is all they promise.
