# SPDX-License-Identifier: LGPL-2.1-or-later
# Views of shapes, as a drawing has them: Open CASCADE's exact hidden-line
# removal through Part.HLRBRep, the edges seen and the edges hidden, as 2D
# polylines in the view's own millimetres (x right, y up). The directions
# are TechDraw's: Z points from the part to the viewer.

import FreeCAD
import Part

V = FreeCAD.Vector

# name: (towards the viewer, the view's x on the paper)
VIEWS = {
    "front": ((0, -1, 0), (1, 0, 0)),
    "back": ((0, 1, 0), (-1, 0, 0)),
    "top": ((0, 0, 1), (1, 0, 0)),
    "bottom": ((0, 0, -1), (1, 0, 0)),
    "right": ((1, 0, 0), (0, 1, 0)),
    "left": ((-1, 0, 0), (0, -1, 0)),
    "iso": ((1, -1, 1), (1, 1, 0)),
}
# Which model axes a view's horizontal and vertical show (orthographic only).
AXES = {"front": ("x", "z"), "back": ("x", "z"), "top": ("x", "y"), "bottom": ("x", "y"), "right": ("y", "z"), "left": ("y", "z")}
WORDS = {
    "front": "a front elevation view",
    "back": "a rear elevation view",
    "top": "a top plan view",
    "bottom": "a bottom plan view",
    "right": "a right side elevation view",
    "left": "a left side elevation view",
    "iso": "an isometric view",
}


def frame(view):
    z, x = VIEWS[view]
    Z, X = V(*z), V(*x)
    Z.normalize()
    X.normalize()
    Y = Z.cross(X)
    Y.normalize()
    return Z, X, Y


def point(p, view):
    """A 3D point's place in a view, (x, y) mm."""
    _, X, Y = frame(view)
    return (p.dot(X), p.dot(Y))


def _polys(comp, kind, out, deflection):
    if comp is None or comp.isNull():
        return
    for e in comp.Edges:
        try:
            pts = e.discretize(Deflection=deflection)
        except Exception:  # a degenerate edge HLR left: nothing to draw
            continue
        if len(pts) >= 2:
            out.append((kind, [(p.x, p.y) for p in pts]))


def project(shapes, view, hidden=False, deflection=0.02):
    """The view of shapes, each hiding the others: {lines: [(kind, [(x, y)
    …])], box: (xmin, ymin, xmax, ymax)}. Kinds: visible, hidden (when
    asked), thin (the seams where faces meet smoothly)."""
    Z, X, _ = frame(view)
    algo = Part.HLRBRep.Algo()
    for sh in shapes:
        algo.add(sh)
    algo.setProjector(Origin=V(0, 0, 0), ZDir=Z, XDir=X)
    algo.update()
    algo.hide()
    hs = Part.HLRBRep.HLRToShape(algo)
    lines = []
    _polys(hs.vCompound(), "visible", lines, deflection)
    _polys(hs.outLineVCompound(), "visible", lines, deflection)
    _polys(hs.Rg1LineVCompound(), "thin", lines, deflection)
    if hidden:
        _polys(hs.hCompound(), "hidden", lines, deflection)
        _polys(hs.outLineHCompound(), "hidden", lines, deflection)
    xs = [x for (_, pts) in lines for (x, _) in pts]
    ys = [y for (_, pts) in lines for (_, y) in pts]
    if not xs:
        # Nothing came back: the corners of the shapes' boxes give the extent.
        pts = []
        for sh in shapes:
            b = sh.BoundBox
            pts += [point(V(x, y, z), view) for x in (b.XMin, b.XMax) for y in (b.YMin, b.YMax) for z in (b.ZMin, b.ZMax)]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return {"lines": lines, "box": (min(xs), min(ys), max(xs), max(ys))}


def visible_point(shape, others, view, tries=8):
    """A point on a shape's face that the viewer sees in this view, other
    shapes in front counted: the faces turned most to the viewer, biggest
    first. None when every face tried is hidden."""
    from .geometry import _ray_hits

    Z, _, _ = frame(view)
    solid = shape.Solids[0] if shape.Solids else None
    faces = []
    for f in shape.Faces:
        try:
            u0, u1, v0, v1 = f.ParameterRange
            u, v = (u0 + u1) / 2, (v0 + v1) / 2
            n = f.normalAt(u, v)
            p = f.valueAt(u, v)
        except Exception:
            continue
        if n.Length < 1e-9:
            continue
        n.normalize()
        # Outward: which way a reversed face's normal points varies by
        # version, so test it against the solid.
        if solid is not None and solid.isInside(p + n * 0.05, 1e-6, False):
            n = n * -1
        facing = n.dot(Z)
        if facing > 0.15:
            faces.append((f.Area * facing, f))
    faces.sort(key=lambda t: -t[0])
    blockers = [shape] + list(others)
    comp = Part.makeCompound(blockers)
    for _, f in faces[:tries]:
        u0, u1, v0, v1 = f.ParameterRange
        for (fu, fv) in ((0.5, 0.5), (0.3, 0.3), (0.7, 0.7), (0.3, 0.7), (0.7, 0.3)):
            u, v = u0 + (u1 - u0) * fu, v0 + (v1 - v0) * fv
            try:
                p = f.valueAt(u, v)
                if not f.isInside(p, 1e-6, True):
                    continue
            except Exception:
                continue
            # A ray from just off the face towards the viewer: nothing hit, seen.
            start = p + Z * 0.02
            hit = _ray_hits(comp, [(start, Z * -1)], 1e5, tol=0.05)[0]
            if hit is None:
                return p
    return None
