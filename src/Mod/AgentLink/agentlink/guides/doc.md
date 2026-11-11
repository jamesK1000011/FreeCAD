# The document: files, objects, properties
<!-- SPDX-License-Identifier: LGPL-2.1-or-later -->

## workflow
- `object_list` to see what is there (names, labels, types, what uses what); `object_get { object, filter }` for one object's properties.
- `object_set { object, values }` changes sizes of parametric objects (a primitive's `Length`, `Radius`) and any object's `Placement`; it recomputes and is refused, and undone, when the recompute fails.
- `import_file` brings STEP, IGES, BREP (bodies); STL, OBJ, 3MF, PLY, OFF, AMF, glTF, Collada, VRML, 3DS (bodies of triangles, or meshes); DXF and SVG (Draft geometry); and PNG/JPG/BMP pictures as an image plane `width` mm wide to trace over.
- `export` writes and reads back — check `verify`:
  - solids: STEP, IGES, BREP (`verify.volumeDiff`);
  - one mesh: STL, OBJ, 3MF, PLY, OFF, VRML (wrl), X3D;
  - a scene for viewers, web pages and render programs, a mesh and colour per object: glTF (glb, gltf) and Collada (dae), with `colors {label: "#rrggbb"}`;
  - 2D for laser, plasma and waterjet cutters or another CAD program: DXF (R12) or SVG of a `view` (top, front, right, … iso; `hidden: true` puts hidden lines on their own dashed layer), or of a `section` at z mm. Circles and arcs stay true circles and arcs. For a dimensioned drawing, use TechDraw (`td_export`).
- `doc_save` before `doc_new` or `doc_open`: those switch the session to another document.

## traps
- `doc_new` and `doc_open` are not undo steps and a batch's rollback does not reach past them.
- A body imported from a mesh is made of triangles: measure, section and booleans work; fillets and `shape_query` by radius do not see round faces.
- Booleans and fillets bake a parametric primitive into a plain shape: after that only `Placement` can be set.
- `object_rename` does not carry setups, specs or waivers kept under the old label.
- A picture of the parts is `view_render` (the view group), headless or not.
