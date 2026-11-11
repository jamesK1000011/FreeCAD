<!-- SPDX-License-Identifier: LGPL-2.1-or-later -->
# Inspection

FreeCAD's own Inspection feature measures an actual against nominals, point by point. It stays in the document, so the person sees the same result in FreeCAD's Inspection workbench.

## workflow
1. Get the actual into the document. That is a mesh, a point cloud, or a body (meshed for the purpose and kept hidden), or `file:` reads a scan (STL, OBJ, PLY, ASC, PCD …).
2. Put it where the nominal is first. Inspection does not align anything. With ScanRuler for FreeCAD, `align_auto` does it; a scan straight off a scanner is in its own frame.
3. Run `insp_run { actual, nominals: ["Plate"], search_radius: 1, tolerance: 0.1 }`. Ask the person for the tolerance; don't assume one.
4. Read the answer: `rms`, `min` and `max`, `inTolerancePercent`, `verdict`, and the `worst` spots by position. Then look with `view_render { map: "inspection" }`.
5. `insp_update` changes the radius or the tolerance and measures again. `insp_export` writes a CSV of every point plus a JSON report.

## reading it
- A distance is signed where the nominal is a solid: plus is outside (material added), minus is inside (material missing).
- Out of reach means further than `search_radius` from every nominal. A large share of those means the actual is not aligned, the radius is too small, or the actual has parts the nominal lacks.
- `verdict` is **pass** only if every point is in tolerance and none is out of reach; **unknown** if some are out of reach and the rest pass.

## traps
- `search_radius` must reach past the tolerance, or points out of tolerance read as out of reach. A refusal says so.
- A body as the actual is sampled every `spacing` mm (default 1% of its size). Finer finds smaller faults and takes longer.
- Comparing two versions of a design works the same way: the new body as the actual, the old one as the nominal.
- A mesh is measured at its vertices. A scan has plenty of them, but an STL exported from CAD has only a few, so a cylinder's wall may go unmeasured. Inspect the body itself instead; it is sampled every `spacing` mm.

## face by face
`insp_faces` splits an inspection by the nominal's faces: each face's mean offset, min, max and RMS, and how many points are out of tolerance. The faces that are off come first. A face that sits off evenly is moved; one whose min and max are far apart is out of form.
