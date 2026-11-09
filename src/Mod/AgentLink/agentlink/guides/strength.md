# Strength (finite elements)

## The study
1. `fea_setup { body, material, mesh, buildDir }`. The material is the body's (material_set) unless given.
2. `fea_hold { faces }`: where it is mounted. Ask the person how it is held.
3. `fea_load { faces, force | pressure }` or `{ gravity }`: what pushes on it, in N or MPa.
4. `fea_solve`: von Mises peak and where, displacement, safety factor against yield; for a printed part the tension across the layers against half the yield.

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
