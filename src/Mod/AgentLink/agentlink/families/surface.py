# SPDX-License-Identifier: LGPL-2.1-or-later
# The Surface workbench's own parametric features, beside draft.py's plain
# surfaces: a surface through sections, one bounded by edges (stretched,
# Coons, curved), a surface extended past its edges, faces sewn into a shell
# (and a closed one made a solid), surfaces cut by one another, and blend
# curves joining two edges smoothly. Each follows the edges it was made from.

import Part
import Surface  # noqa: F401 — loads the Surface features; the family is left out without it

from .. import geometry as geo
from ..protocol import Tool, ToolError
from ..tools import obj
from . import picks

GROUP = "surface"
INFO = "Surface workbench features (parametric): sections, boundary surfaces (stretched/Coons/curved), extend, sewing into shells and solids, surface cuts, blend curves."


def bad(msg):
    return ToolError("invalid_input", msg)


def reading(o):
    sh = o.Shape
    out = {"object": o.Label, "type": o.TypeId.split("::")[-1], "faces": len(sh.Faces), "edges": len(sh.Edges)}
    if sh.Faces:
        out["area"] = round(sh.Area, 3)
    if sh.Solids:
        out["volume"] = round(sh.Volume, 3)
    out["shells"] = len(sh.Shells)
    out["closed"] = bool(sh.Shells and all(x.isClosed() for x in sh.Shells))
    return out


def chain(edges, refs, tol=1e-4, closed=True):
    """Which boundary edges run backwards, so that each starts where the one before ends
    (the feature takes them as given); refused where two do not meet."""
    def ends(e):
        return e.Vertexes[0].Point, e.Vertexes[-1].Point

    for first in (False, True):
        a0, a1 = ends(edges[0])
        start, cur = (a1, a0) if first else (a0, a1)
        flags, ok = [first], True
        for e in edges[1:]:
            b0, b1 = ends(e)
            if (b0 - cur).Length <= tol:
                flags.append(False)
                cur = b1
            elif (b1 - cur).Length <= tol:
                flags.append(True)
                cur = b0
            else:
                ok = False
                break
        if ok:
            if closed and (cur - start).Length > tol:
                raise bad("edges: the last does not come back to where the first starts — the boundary must close.")
            return flags
    if not closed:
        raise bad("edges: two curves must share an end (a corner); the surface spans between them.")
    raise bad(f"edges: they do not meet end to end in the order given ({', '.join(map(str, refs))}) — list them going round the boundary.")


def tools(session):
    s = session
    doc = lambda: s.doc

    def new(kind, label):
        lab = s.unique(label)
        o = doc().addObject(kind, label.replace(" ", ""))
        o.Label = lab
        return o

    def done(o, tool, extra=None):
        doc().recompute()
        if not o.isValid() or o.Shape.isNull():
            msg, kind = o.getStatusString(), o.TypeId.split("::")[-1]
            doc().removeObject(o.Name)
            raise ToolError("failed", f"{kind} failed: {msg} — nothing was kept.")
        s.snapshot(tool)
        return {**reading(o), **(extra or {}), "step": len(s.steps)}

    def edge_ref(r, what):
        """"Body.Edge3", or a whole wire/sketch by name (all its edges)."""
        if not isinstance(r, str):
            raise bad(f"{what}: \"Body.Edge3\" or a wire's name.")
        label, sub, _ = picks.resolve(s, r)
        o = picks.find(doc(), label)
        if sub:
            if not sub.startswith("Edge"):
                raise bad(f"{what}: an edge, not {sub}.")
            return o, [sub]
        n = len(o.Shape.Edges)
        if not n:
            raise bad(f"{what}: {o.Label} has no edges.")
        return o, [f"Edge{i + 1}" for i in range(n)]

    def surface_sections(a):
        refs = a.get("sections") or []
        if len(refs) < 2:
            raise bad("sections: two or more curves in order (\"Body.Edge3\" or wire names).")
        o = new("Surface::Sections", a.get("name") or "Sections")
        o.NSections = [edge_ref(r, "sections") for r in refs]
        return done(o, "surface_sections")

    def surface_boundary(a):
        refs = a.get("edges") or []
        if not 2 <= len(refs) <= 4:
            raise bad("edges: two to four boundary curves that meet end to end.")
        bl = [edge_ref(r, "edges") for r in refs]
        edges = [ob.Shape.getElement(sub) for ob, subs in bl for sub in subs]
        # Three or four curves go round the surface; two meet at a corner, the surface between them.
        flags = chain(edges, refs, closed=len(edges) > 2)
        o = new("Surface::GeomFillSurface", a.get("name") or "Boundary surface")
        o.BoundaryList = bl
        o.ReversedList = flags
        ft = {"stretched": "Stretched", "coons": "Coons", "curved": "Curved"}.get(a.get("fill", "stretched"))
        if ft is None:
            raise bad("fill: stretched (flattest), coons (smooth across), curved (round).")
        o.FillType = ft
        try:
            return done(o, "surface_boundary")
        except ToolError as e:
            if ft == "Stretched" or "disjoint" not in str(e) or len(edges) < 3:
                raise
            why = e.message
        # FreeCAD's Coons and curved fills need the opposite curves to match
        # (degree, knots) and report any mismatch as disjoint: a smooth
        # N-sided filling through the same edges instead.
        f = new("Surface::Filling", a.get("name") or "Boundary surface")
        f.BoundaryEdges = [(ob, sub) for ob, subs in bl for sub in subs]
        f.BoundaryOrder = [0] * len(edges)
        try:
            out = done(f, "surface_boundary")
        except ToolError:
            raise ToolError("failed", f"{why} — and a filling through the edges failed too; fill: stretched joins them.") from None
        out["note"] = f"Made as Surface Filling (smooth through the edges): FreeCAD's {ft.lower()} fill refused these curves ({why.split(':')[-1].strip()})."
        return out

    def surface_extend(a):
        ref = a.get("face")
        label, sub, _ = picks.resolve(s, ref) if isinstance(ref, str) else (None, None, None)
        if not sub or not sub.startswith("Face"):
            raise bad("face: \"Body.Face2\".")
        o = new("Surface::Extend", a.get("name") or "Extended")
        o.Face = (picks.find(doc(), label), [sub])
        u, v = a.get("u", 0.05), a.get("v", 0.05)
        o.ExtendUSymetric = o.ExtendVSymetric = True
        o.ExtendUNeg, o.ExtendVNeg = float(u), float(v)
        return done(o, "surface_extend", {"note": "u and v: how far past each edge, as a share of the face's length that way (0.05 = 5%)."})

    def surface_sew(a):
        refs = a.get("faces") or []
        links = {}
        for r in refs:
            if not isinstance(r, str):
                raise bad("faces: surfaces' names, or faces as \"Body.Face3\".")
            label, sub, _ = picks.resolve(s, r)
            x = picks.find(doc(), label)
            # The feature sews only the faces listed: a whole object brings all of its own.
            subs = [sub] if sub else [f"Face{i + 1}" for i in range(len(x.Shape.Faces))]
            if not subs:
                raise bad(f"faces: {x.Label} has no faces.")
            links.setdefault(x, []).extend(subs)
        if sum(len(v) for v in links.values()) < 2:
            raise bad("faces: two or more faces (surfaces, or \"Body.Face3\") whose edges meet.")
        o = new("Surface::Sewing", a.get("name") or "Sewn")
        o.ShapeList = list(links.items())
        o.Tolerance = float(a.get("tolerance", 0.01))
        out = done(o, "surface_sew")
        if a.get("solid"):
            sh = o.Shape
            if not (sh.Shells and sh.Shells[0].isClosed()):
                out["solid"] = "not made: the shell is open — a gap wider than tolerance, or a face missing"
            else:
                sol = doc().addObject("Part::Feature", "Body")
                sol.Label = s.unique(a.get("name") and a["name"] + " solid" or f"{o.Label} solid")
                sol.Shape = geo.clean(Part.makeSolid(sh.Shells[0]))
                doc().recompute()
                out["solid"] = {"body": sol.Label, "volume": round(sol.Shape.Volume, 3)}
        return out

    def surface_cut(a):
        x, y = picks.find(doc(), a.get("surface")), picks.find(doc(), a.get("by"))
        o = new("Surface::Cut", a.get("name") or "Surface cut")
        o.ShapeList = [x, y]
        return done(o, "surface_cut")

    def surface_blend(a):
        ends = {}
        for side in ("start", "end"):
            e = a.get(side) or {}
            ob, subs = edge_ref(e.get("edge"), f"{side}.edge")
            c = int(e.get("continuity", 2))
            if not 0 <= c <= 4:
                raise bad(f"{side}.continuity: 0 (touching) … 4; 1 tangent, 2 curvature.")
            at = float(e.get("at", 0 if side == "start" else 1))
            if not 0 <= at <= 1:
                raise bad(f"{side}.at: 0 (the edge's start) … 1 (its end).")
            ends[side] = ((ob, [subs[0]]), c, at, float(e.get("size", 1)))
        o = new("Surface::FeatureBlendCurve", a.get("name") or "Blend curve")
        # Both edges first: changing any other property recomputes the curve at once.
        o.StartEdge, o.EndEdge = ends["start"][0], ends["end"][0]
        for side, (_, c, at, size) in ends.items():
            p = side.capitalize()
            setattr(o, f"{p}Continuity", c)
            setattr(o, f"{p}Parameter", at)
            setattr(o, f"{p}Size", size)
        return done(o, "surface_blend", {"note": "at: where on each edge, 0 (its start) … 1 (its end); size: how far the blend follows each edge's direction."})

    return [
        Tool("surface_sections", "A surface through curves in order (Surface Sections, parametric): sections [\"Body.Edge3\" | a wire or sketch's name …].",
             obj({"sections": {"type": "array", "items": {"type": "string"}}, "name": {"type": "string"}}, ["sections"]), surface_sections, changes=True),
        Tool("surface_boundary", "A surface bounded by 2–4 curves meeting end to end (GeomFillSurface): edges, fill stretched | coons | curved.",
             obj({"edges": {"type": "array", "items": {"type": "string"}}, "fill": {"type": "string"}, "name": {"type": "string"}}, ["edges"]), surface_boundary, changes=True),
        Tool("surface_extend", "Extend a face past its edges (Surface Extend): face \"Body.Face2\", u and v as shares of its size (0.05).",
             obj({"face": {"type": "string"}, "u": {"type": "number"}, "v": {"type": "number"}, "name": {"type": "string"}}, ["face"]), surface_extend, changes=True),
        Tool("surface_sew", "Sew surfaces into a shell (Surface Sewing): faces [surfaces, or \"Body.Face3\"], tolerance mm; solid: true makes a closed shell a solid body.",
             obj({"faces": {"type": "array", "items": {"type": "string"}}, "tolerance": {"type": "number"}, "solid": {"type": "boolean"}, "name": {"type": "string"}}, ["faces"]), surface_sew, changes=True),
        Tool("surface_cut", "Cut one surface by another (Surface Cut): surface, by.", obj({"surface": {"type": "string"}, "by": {"type": "string"}, "name": {"type": "string"}}, ["surface", "by"]), surface_cut, changes=True),
        Tool("surface_blend", "A blend curve joining two edges smoothly: start / end { edge \"Body.Edge3\", at 0…1 along it, continuity 0…4 (2: curvature), size }.",
             obj({"start": {"type": "object"}, "end": {"type": "object"}, "name": {"type": "string"}}, ["start", "end"]), surface_blend, changes=True),
    ]
