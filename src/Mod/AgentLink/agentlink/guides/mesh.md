<!-- SPDX-License-Identifier: LGPL-2.1-or-later -->
# Meshes in the document

These tools work on FreeCAD's mesh objects: an imported STL, a body meshed with `meshobj_from_shape`, a split piece. A scan in ScanRuler for FreeCAD's session has its own tools (`mesh-tools`).

## workflow
1. Get a mesh: `import_file` an STL/OBJ/PLY, or `meshobj_from_shape { body, method }`. Use `mefisto` for even triangles (for FEM or textures), `standard` for a close fit.
2. `meshobj_info` evaluates it as the Mesh workbench does. If there are problems, `meshobj_repair`. `fix: ["holes"]` and `["islands"]` are opt-in, because filling and dropping change the shape.
3. Change it: `meshobj_decimate` (lighter), `meshobj_refine` (denser), `meshobj_smooth` (taubin keeps the size), `meshobj_offset`, `meshobj_trim` by a plane, `meshobj_transform`, `meshobj_boolean` (closed meshes only).
4. Read it: `meshobj_sections` for outlines along an axis, `meshobj_segments` for its flat regions (on a scan, where to put a sketch), `meshobj_curvature`, and `view_render { map: "curvature" }`.
5. `meshobj_export` writes it out; `part_from_mesh` turns a closed one into a solid.

## traps
- Each change replaces the mesh in place: `copy: true` keeps the original beside it.
- A mesh boolean needs both meshes closed (`meshobj_info` → `solid: true`).
- `meshobj_trim` leaves a hole where it cut: `close: true` fills it.

## laid flat
`meshobj_unwrap { of: "Body.Face3" }` lays a surface flat as a cutting pattern (sheet metal, fabric, leather). Cylinder and cone sides roll out exactly: a cone frustum gives the two radii and the angle of its fan. Other surfaces go through FreeCAD's unwrapping where it works, which reports how much they stretch. `path` writes the pattern as SVG or DXF.
