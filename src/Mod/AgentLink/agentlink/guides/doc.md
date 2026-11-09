# The document: files, objects, properties
<!-- SPDX-License-Identifier: LGPL-2.1-or-later -->

## workflow
- `object_list` to see what is there (names, labels, types, what uses what); `object_get { object, filter }` for one object's properties.
- `object_set { object, values }` changes sizes of parametric objects (a primitive's `Length`, `Radius`) and any object's `Placement`; it recomputes and is refused, and undone, when the recompute fails.
- `import_file` brings STEP, IGES, BREP (bodies), STL, OBJ, 3MF (bodies of triangles, or meshes) and DXF in; `export` writes STEP, IGES, BREP, STL, OBJ, 3MF and reads the file back — check `verify.volumeDiff`.
- `doc_save` before `doc_new` or `doc_open`: those switch the session to another document.

## traps
- `doc_new` and `doc_open` are not undo steps and a batch's rollback does not reach past them.
- A body imported from a mesh is made of triangles: measure, section and booleans work; fillets and `shape_query` by radius do not see round faces.
- Booleans and fillets bake a parametric primitive into a plain shape: after that only `Placement` can be set.
- `object_rename` does not carry setups, specs or waivers kept under the old label.
- A picture of the parts is `view_render` (the view group), headless or not.
