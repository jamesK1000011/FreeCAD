# Materials

## Choosing one
- `material_list { words, category }`: the printing filaments (pla, petg, abs, asa, pa6-cf, tpu-95a) first, then FreeCAD's library (metals, thermoplastics, wood …).
- `material_get { id }` for the figures: density, E, Poisson, yield, ultimate, and where they come from.
- Ask the person what the part is made of; don't assume PLA. Mass and strength assume PLA when nothing is set, and say so.

## Figures you can trust
- Library and filament figures are typical. For a part that must not fail, ask for the supplier's data sheet and give it: `material_set { custom: { name, density, youngsMPa, yieldMPa, poisson } }`.
- A printed part is weaker across its layers than along them; `fea_solve` judges that separately.

## Where it is kept
- With the document (`materials`, body → id) and, for a library card, on the object's ShapeMaterial.
- part_review and slice read the filament keys only; a library or custom id is read by mass_properties and fea_*.
