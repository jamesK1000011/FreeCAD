# Strength (finite elements)

## The study
1. `fea_setup { body, material, mesh, buildDir }`. The material is the body's (material_set) unless given.
2. `fea_hold { faces }`: where it is mounted. Ask the person how it is held.
3. `fea_load { faces, force | pressure }` or `{ gravity }`: what pushes on it, in N or MPa.
4. `fea_solve`: von Mises peak and where, displacement, safety factor against yield; for a printed part the tension across the layers against half the yield.
5. `view_render { map: "stress", views: "all" }`: look at where it is hot — a peak at a held face is the hold's, not the part's.

## Shaking: natural frequencies
- `fea_modes { count }` on the same study: the lowest frequencies (Hz) with its holds, and where each mode swings most and which way. No holds: the part free, its six rigid motions left out.
- Keep what shakes it (a motor's speed in rev/s, a fan, a vehicle) well away from the lowest — by a factor of two or more. To raise it: stiffen or shorten where it swings, or take weight off there.

## Heat
- `fea_heat { temperatures: [{ faces, celsius }], heat: [{ faces, watts }], air }`: steady temperature through the study's body. Hold faces at a temperature (a heated bed, a hot pipe), put watts in (an electronics board, a motor), and air takes it away: `{ h, celsius }`, 10 W/(m²·K) and 25 °C on the faces left over by default (`air: false` for none).
- It warns when the hottest spot passes where the material softens (PLA about 55 °C, PETG 70, ABS and ASA 95). `view_render { map: "heat" }` shows it.
- Conduction and convection only, steady: no radiation, no warm-up time. Still air is 5 to 25 W/(m²·K), a fan 25 to 100.

## Faces by query
- `{ normal: [0,0,-1], z: 0 }`: flat faces facing down at z = 0.
- `{ near: [x,y,z] }`: the nearest face; `radius` takes every face that close.
- Each answer lists the faces picked with area and centre: check them.

## Traps
- A rigid hold makes stress no real mount does: a peak on a held face reads high. Look a little away from it, or hold a wider face.
- A coarse mesh under-reads peaks. Halve `mesh` and solve again; if the peak rises by more than 10 %, keep going.
- Library figures are typical. For a part that must not fail, use the supplier's (material_set custom) and keep a safety factor of 2 or more.
- Across the layers is a simplification (0.5 × yield): a real printed part can be weaker still, and is weakest where layers are pulled apart.
- A linear solve is wrong past yield or for large deflections; the warnings say when.
- Needs CalculiX (`ccx`) and Gmsh (`gmsh`) installed; without them fea_solve says what to install.

## More from a study
- `fea_buckle` runs a linear buckling analysis with the same holds and loads, giving the factor at which each mode buckles. Keep the lowest at 3 or more: real, imperfect parts buckle sooner.
- `fea_refine { faces, size }` makes the mesh finer where a peak sits. Solve again and watch the peak settle; if it keeps rising, it is a sharp corner (a singularity), not a real stress.
- `fea_export` writes VTU (ParaView, FreeCAD's FEM post-processing) and a CSV of every node.
- `fea_native` builds the study as FreeCAD's own FEM analysis, so the person can open, mesh and run it in the FEM workbench.

## heat and expansion
`fea_load { temperature: dT }` warms (or, negative, cools) the whole part against its holds; `alpha` is the expansion per K, else the material's. A bar free at one end grows by L·alpha·dT; one held at both ends sees about E·alpha·dT of stress.
