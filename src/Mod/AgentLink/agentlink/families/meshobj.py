# SPDX-License-Identifier: LGPL-2.1-or-later
# FreeCAD's Mesh and MeshPart workbenches on the document's mesh objects
# (ScanRuler for FreeCAD's mesh tools work on the session's scan instead):
# a body meshed three ways, a mesh evaluated and repaired as the Mesh
# workbench's Evaluate & Repair does, decimated, refined, smoothed, offset,
# trimmed by a plane, transformed, joined by booleans, cut into
# cross-sections, split into its pieces and into planar or curved segments,
# its curvature measured (and drawn), and a shape projected onto it.

import math
import os

import FreeCAD
import Mesh  # noqa: F401 — the family stands on the Mesh module; left out without it
import Part

import numpy as np

from .. import geometry as geo
from .. import render
from ..protocol import Tool, ToolError

GROUP = "mesh"
INFO = ("FreeCAD's Mesh workbench on document meshes: mesh a body, evaluate and repair, decimate, refine, smooth, offset, trim, transform, "
        "booleans, cross-sections, split, planar and curvature segments, curvature, projection onto a mesh.")

V = FreeCAD.Vector


def obj(props, required=()):
    return {"type": "object", "properties": props, "required": list(required), "additionalProperties": False}


def bad(msg):
    return ToolError("invalid_input", msg)


def meshes(doc):
    return [o for o in doc.Objects if o.isDerivedFrom("Mesh::Feature")]


def reading(m):
    bb = m.BoundBox
    out = {"points": m.CountPoints, "facets": m.CountFacets, "solid": bool(m.isSolid()), "components": int(m.countComponents()),
           "area": round(m.Area, 3), "box": [round(bb.XLength, 3), round(bb.YLength, 3), round(bb.ZLength, 3)]}
    if out["solid"]:
        out["volume"] = round(m.Volume, 3)
    return out


def evaluation(m):
    """The Mesh workbench's Evaluate & Repair checks, read."""
    e = {}
    checks = (("nonManifolds", "hasNonManifolds"), ("selfIntersections", "hasSelfIntersections"), ("flippedNormals", "hasNonUniformOrientedFacets"),
              ("corruptedFacets", "hasCorruptedFacets"), ("invalidPoints", "hasInvalidPoints"), ("pointsOnEdges", "hasPointsOnEdge"),
              ("indicesOutOfRange", "hasFacetsOutOfRange"), ("invalidNeighbours", "hasInvalidNeighbourhood"))
    for k, f in checks:
        try:
            e[k] = bool(getattr(m, f)())
        except Exception:
            pass
    try:
        e["flippedCount"] = int(m.countNonUniformOrientedFacets())
    except Exception:
        pass
    try:
        e["selfIntersectionPairs"] = len(m.getSelfIntersections())
    except Exception:
        pass
    e["open"] = not m.isSolid()
    problems = [k for k, v in e.items() if v is True and k != "open"] + (["open (holes)"] if e["open"] else [])
    return e, problems


def _curv_map(s, a):
    ref = a.get("mesh") or (a.get("objects") or [None])[0]
    ms = meshes(s.doc)
    o = next((x for x in ms if ref in (x.Label, x.Name)), None) if ref else (ms[-1] if ms else None)
    if o is None:
        raise ToolError("not_found", "No mesh to show: meshobj_from_shape or import_file one.")
    m = o.Mesh
    V_ = np.array([[p.x, p.y, p.z] for p in m.Points], dtype=float)
    F_ = np.array([list(f.PointIndices) for f in m.Facets], dtype=int)
    c = np.array([(k[0], k[1]) for k in m.getCurvaturePerVertex()], dtype=float)  # k1, k2 (and their directions) per point
    kind = a.get("curvature", "mean")
    vals = {"mean": (c[:, 0] + c[:, 1]) / 2, "gauss": c[:, 0] * c[:, 1], "max": np.max(np.abs(c), axis=1)}.get(kind, (c[:, 0] + c[:, 1]) / 2)
    lo5, hi95 = float(np.percentile(vals, 5)), float(np.percentile(vals, 95))
    if lo5 < 0 < hi95:  # concave and convex: zero in the middle
        m_ = max(-lo5, hi95) or 1.0
        rng = [-m_, m_]
    else:  # all one way: the colours spent on its own spread
        rng = [lo5, hi95] if hi95 > lo5 else [lo5 - 1e-6, hi95 + 1e-6]
    return {"layers": [{"name": o.Label, "V": V_, "F": F_, "values": vals, "creases": True}], "unit": "1/mm", "cmap": "jet", "range": rng,
            "worst": "abs", "label": f"CURVATURE ({kind.upper()})", "note": f"{o.Label}: {kind} curvature at each point (1 / radius); 5% beyond the colours' ends."}


render.register_map("curvature", _curv_map, "A document mesh's curvature (mesh names which; curvature mean | gauss | max).")


def tools(session):
    s = session
    doc = lambda: s.doc  # noqa: E731

    def mesh_of(ref):
        ms = meshes(doc())
        if ref is None:
            if len(ms) == 1:
                return ms[0]
            raise (ToolError("not_found", "No mesh in the document: meshobj_from_shape makes one, import_file reads one.") if not ms else bad(f"mesh: say which — {', '.join(o.Label for o in ms)}."))
        o = next((x for x in ms if ref in (x.Label, x.Name)), None)
        if o is None:
            raise ToolError("not_found", f"mesh {ref!r}: the meshes are {', '.join(x.Label for x in ms) or 'none'}.")
        return o

    def keep(o, m, tool, a, extra=None, before=None):
        """The changed mesh: into the object, or a new one with copy: true."""
        if a.get("copy"):
            n = doc().addObject("Mesh::Feature", "Mesh")
            n.Label = s.unique(a.get("name") or f"{o.Label} ({tool.split('_', 1)[1]})")
            n.Mesh = m
            target = n
        else:
            o.Mesh = m
            target = o
        doc().recompute()
        s.snapshot(tool)
        out = {"mesh": target.Label, **reading(target.Mesh), **(extra or {}), "step": len(s.steps)}
        if before is not None:
            out["before"] = before
        return out

    def new_mesh(m, label):
        n = doc().addObject("Mesh::Feature", "Mesh")
        n.Label = s.unique(label)
        n.Mesh = m
        return n

    # ---- making and reading ----

    def meshobj_from_shape(a):
        import MeshPart

        src = next((o for o in doc().Objects if a.get("body") in (o.Label, o.Name) and hasattr(o, "Shape")), None) if a.get("body") else (s.bodies()[0] if len(s.bodies()) == 1 else None)
        if src is None:
            raise bad("body: the shape to mesh (session_state names them).")
        how = a.get("method", "standard")
        kw = {"Shape": src.Shape}
        if how == "standard":
            kw.update(LinearDeflection=float(a.get("deflection", 0.05)), AngularDeflection=math.radians(float(a.get("angle", 15))), Relative=False)
        elif how == "mefisto":
            kw.update(MaxLength=float(a.get("maxLength", 2.0)))
        elif how == "netgen":
            kw.update(Fineness=int({"verycoarse": 0, "coarse": 1, "moderate": 2, "fine": 3, "veryfine": 4}.get(a.get("fineness", "moderate"), 2)), SecondOrder=0, Optimize=1, AllowQuad=0)
        else:
            raise bad("method: standard (deflection, angle), mefisto (maxLength: even triangles) or netgen (fineness).")
        try:
            m = MeshPart.meshFromShape(**kw)
        except Exception as e:
            raise ToolError("unavailable" if how == "netgen" else "failed", f"{how} meshing failed: {e}" + (" — this FreeCAD may lack Netgen; mefisto gives even triangles too." if how == "netgen" else ""))
        n = new_mesh(m, a.get("name") or f"{src.Label} mesh")
        doc().recompute()
        s.snapshot("meshobj_from_shape")
        return {"mesh": n.Label, "from": src.Label, "method": how, **reading(m), "step": len(s.steps)}

    def meshobj_info(a):
        o = mesh_of(a.get("mesh"))
        e, problems = evaluation(o.Mesh)
        return {"mesh": o.Label, **reading(o.Mesh), "evaluation": e, "problems": problems, **({"next": "meshobj_repair fixes them."} if problems else {"verdict": "clean"})}

    # ---- repair ----

    REPAIRS = ("normals", "duplicates", "nonmanifolds", "selfintersections", "degenerate", "folds", "indices", "holes", "islands")

    def meshobj_repair(a):
        o = mesh_of(a.get("mesh"))
        m = o.Mesh.copy()
        e0, p0 = evaluation(m)
        want = a.get("fix") or [r for r in REPAIRS if r not in ("holes", "islands")]
        unknown = [w for w in want if w not in REPAIRS]
        if unknown:
            raise bad(f"fix: any of {', '.join(REPAIRS)}.")
        did = []
        for w in want:
            try:
                if w == "indices":
                    m.fixIndices()
                    m.rebuildNeighbourHood()
                elif w == "duplicates":
                    m.removeDuplicatedPoints()
                    m.removeDuplicatedFacets()
                elif w == "degenerate":
                    m.fixDegenerations(float(a.get("tolerance", 1e-6)))
                    m.removeNeedles(float(a.get("tolerance", 1e-6)) * 1000)
                elif w == "nonmanifolds":
                    m.removeNonManifolds()
                    m.removeNonManifoldPoints()
                elif w == "selfintersections":
                    m.fixSelfIntersections()
                elif w == "folds":
                    m.removeFoldsOnSurface()
                elif w == "normals":
                    m.harmonizeNormals()
                    if m.isSolid() and m.Volume < 0:
                        m.flipNormals()
                elif w == "holes":
                    m.fillupHoles(int(a.get("maxHoleEdges", 50)))
                elif w == "islands":
                    m.removeComponents(int(a.get("minFacets", 10)))
                did.append(w)
            except Exception as ex:
                did.append(f"{w}: failed ({ex})")
        e1, p1 = evaluation(m)
        return keep(o, m, "meshobj_repair", a, {"did": did, "problems": p1, "fixed": [p for p in p0 if p not in p1]}, before={"problems": p0, "facets": o.Mesh.CountFacets})

    # ---- reshape ----

    def meshobj_decimate(a):
        o = mesh_of(a.get("mesh"))
        m = o.Mesh.copy()
        n0 = m.CountFacets
        if a.get("facets"):
            target = int(a["facets"])
            if not 4 <= target < n0:
                raise bad(f"facets: fewer than now ({n0}), 4 or more.")
            red = 1 - target / n0
        else:
            red = float(a.get("reduction", 0.5))
            if not 0 < red < 1:
                raise bad("reduction: the share of triangles to remove, 0 … 1 (or facets: how many to keep).")
        m.decimate(float(a.get("tolerance", 0.1)), red)
        return keep(o, m, "meshobj_decimate", a, {"reduced": f"{n0} → {m.CountFacets} triangles"}, before={"facets": n0})

    def meshobj_refine(a):
        o = mesh_of(a.get("mesh"))
        m = o.Mesh.copy()
        n0 = m.CountFacets
        rounds = int(a.get("rounds", 1))
        if not 1 <= rounds <= 3:
            raise bad("rounds: 1 … 3 (each splits every triangle in four).")
        for _ in range(rounds):
            m.refine()
        if a.get("optimize"):
            m.optimizeTopology(-1.0)
        return keep(o, m, "meshobj_refine", a, {"refined": f"{n0} → {m.CountFacets} triangles"})

    def meshobj_smooth(a):
        o = mesh_of(a.get("mesh"))
        m = o.Mesh.copy()
        meth = a.get("method", "taubin")
        it = int(a.get("iterations", 3))
        if meth == "laplace":
            m.smooth(Method="Laplace", Iteration=it, Lambda=float(a.get("lambda", 0.6307)))
        elif meth == "taubin":
            m.smooth(Method="Taubin", Iteration=it, Lambda=float(a.get("lambda", 0.6307)), Micro=float(a.get("mu", 0.0424)))
        else:
            raise bad("method: taubin (keeps the volume) or laplace (shrinks).")
        vol0 = o.Mesh.Volume if o.Mesh.isSolid() else None
        extra = {"method": meth, "iterations": it}
        if vol0:
            extra["volumeChange"] = round(m.Volume - vol0, 3)
        return keep(o, m, "meshobj_smooth", a, extra)

    def meshobj_offset(a):
        o = mesh_of(a.get("mesh"))
        m = o.Mesh.copy()
        d = float(a.get("distance", 0))
        if d == 0:
            raise bad("distance: mm along the normals (negative: inward).")
        m.offset(d)
        return keep(o, m, "meshobj_offset", a, {"offset": d})

    def meshobj_trim(a):
        o = mesh_of(a.get("mesh"))
        m = o.Mesh.copy()
        p = V(*[float(x) for x in (a.get("at") or [0, 0, 0])])
        n = V(*[float(x) for x in (a.get("normal") or [0, 0, 1])])
        if n.Length < 1e-9:
            raise bad("normal: not zero — the side it points to is removed.")
        n0 = m.CountFacets
        m.trimByPlane(p, n)
        if a.get("close") and not m.isSolid():
            m.fillupHoles(int(a.get("maxHoleEdges", 1000)))
        return keep(o, m, "meshobj_trim", a, {"removed": n0 - m.CountFacets, "note": "Cut open where the plane passed; close: true fills the cut."})

    def meshobj_transform(a):
        o = mesh_of(a.get("mesh"))
        m = o.Mesh.copy()
        mat = FreeCAD.Matrix()
        if a.get("scale") is not None:
            f = a["scale"]
            fx, fy, fz = (f, f, f) if isinstance(f, (int, float)) else tuple(float(x) for x in f)
            mat.scale(fx, fy, fz)
        if a.get("rotate"):
            r = a["rotate"]
            rot = FreeCAD.Rotation(V(*r.get("axis", [0, 0, 1])), float(r.get("angle", 0)))
            mat = rot.toMatrix().multiply(mat)
        if a.get("move"):
            t = FreeCAD.Matrix()
            t.move(V(*a["move"]))
            mat = t.multiply(mat)
        if a.get("mirror"):
            mm = FreeCAD.Matrix()
            ax = {"x": (0, 0), "y": (1, 1), "z": (2, 2)}[a["mirror"]]
            setattr(mm, f"A{ax[0] + 1}{ax[1] + 1}", -1.0)
            mat = mm.multiply(mat)
        m.transform(mat)
        if a.get("mirror"):
            m.flipNormals()
        return keep(o, m, "meshobj_transform", a)

    def meshobj_boolean(a):
        x, y = mesh_of(a.get("a")), mesh_of(a.get("b"))
        op = a.get("op", "union")
        if not (x.Mesh.isSolid() and y.Mesh.isSolid()):
            raise ToolError("invalid_state", "Mesh booleans need closed meshes: meshobj_repair { fix: [\"holes\"] } first.")
        if op not in ("union", "intersection", "difference"):
            raise bad("op: union, intersection or difference.")
        mx = x.Mesh.copy()
        how = "mesh"
        try:
            res = {"union": mx.unite, "intersection": mx.intersect, "difference": mx.difference}[op](y.Mesh)
        except Exception:
            res = None
        # The Mesh module's own booleans often leave an open, broken mesh:
        # then through solids (OpenCASCADE) and meshed again.
        if res is None or res.CountFacets == 0 or not res.isSolid() or evaluation(res)[1]:
            if max(x.Mesh.CountFacets, y.Mesh.CountFacets) > int(a.get("maxFacets", 60000)):
                raise ToolError("failed", f"The mesh {op} came out broken, and the meshes are too dense to go through solids (maxFacets): meshobj_decimate them first.")
            import MeshPart

            def solid(m):
                sh = Part.Shape()
                sh.makeShapeFromMesh(m.Topology, 0.01)
                return Part.makeSolid(Part.makeShell(sh.Faces))

            try:
                sa, sb = solid(x.Mesh), solid(y.Mesh)
                r = {"union": sa.fuse, "intersection": sa.common, "difference": sa.cut}[op](sb)
            except Exception as e:
                raise ToolError("failed", f"The {op} failed both as meshes and as solids: {e}")
            if r.isNull() or not r.Solids or r.Volume < 1e-9:
                raise ToolError("failed", f"The {op} left nothing: the meshes do not overlap that way.")
            res = MeshPart.meshFromShape(Shape=r, LinearDeflection=float(a.get("deflection", 0.05)), AngularDeflection=0.3, Relative=False)
            how = "through solids (the mesh boolean came out broken)"
        n = new_mesh(res, a.get("name") or f"{x.Label} {op} {y.Label}")
        doc().recompute()
        s.snapshot("meshobj_boolean")
        return {"mesh": n.Label, "op": op, "how": how, **reading(res), "step": len(s.steps)}

    # ---- sections, pieces, segments, curvature ----

    def meshobj_sections(a):
        o = mesh_of(a.get("mesh"))
        m = o.Mesh
        n = V(*[float(x) for x in (a.get("normal") or [0, 0, 1])])
        n.normalize()
        bb = m.BoundBox
        span = [bb.XMin * n.x + bb.YMin * n.y + bb.ZMin * n.z, bb.XMax * n.x + bb.YMax * n.y + bb.ZMax * n.z]
        lo, hi = min(span), max(span)
        if a.get("at"):
            ds = [float(d) for d in a["at"]]
        else:
            k = int(a.get("count", 5))
            ds = [lo + (hi - lo) * (i + 1) / (k + 1) for i in range(k)]
        planes = [(n * d, n) for d in ds]
        polys = m.crossSections(planes, float(a.get("tolerance", 1e-6)), True)
        wires, rows = [], []
        for d, sec in zip(ds, polys):
            ws = []
            for pl in sec:
                pts = [V(p) for p in pl]
                if len(pts) >= 2:
                    try:
                        ws.append(Part.makePolygon(pts))
                    except Exception:
                        pass
            wires += ws
            rows.append({"at": round(d, 4), "loops": len(ws), "length": round(sum(w.Length for w in ws), 3)})
        if not wires:
            raise ToolError("failed", f"No section met {o.Label} at those distances ({lo:.3g} … {hi:.3g} along {geo.vec(n)}).")
        c = doc().addObject("Part::Feature", "Sections")
        c.Label = s.unique(a.get("name") or f"{o.Label} sections")
        c.Shape = Part.Compound(wires)
        doc().recompute()
        s.snapshot("meshobj_sections")
        return {"sections": c.Label, "normal": geo.vec(n), "slices": rows, "use": "section_to_sketch or cut_export takes them; part_face makes one a face.", "step": len(s.steps)}

    def meshobj_split(a):
        o = mesh_of(a.get("mesh"))
        parts = o.Mesh.getSeparateComponents()
        if len(parts) < 2:
            raise bad(f"{o.Label} is one piece.")
        made = []
        for i, p in enumerate(sorted(parts, key=lambda x: -x.CountFacets), 1):
            if p.CountFacets < int(a.get("minFacets", 1)):
                continue
            made.append(new_mesh(p, f"{o.Label} {i}").Label)
        if hasattr(o, "Visibility"):
            o.Visibility = False
        doc().recompute()
        s.snapshot("meshobj_split")
        return {"from": o.Label, "pieces": made, "step": len(s.steps)}

    def meshobj_segments(a):
        o = mesh_of(a.get("mesh"))
        m = o.Mesh
        kind = a.get("kind", "planar")
        if kind == "planar":
            segs = m.getPlanarSegments(float(a.get("deviation", 0.01)), int(a.get("minFacets", 20)))
        elif kind == "curvature":
            tol = float(a.get("tolerance", 0.01))
            try:
                segs = m.getSegmentsByCurvature([("Plane", tol, tol, int(a.get("minFacets", 20)))])
            except Exception as e:
                raise ToolError("failed", f"Curvature segmentation failed here: {e} — kind planar finds flat regions.")
        else:
            raise bad("kind: planar or curvature.")
        rows = []
        for seg in segs:
            sub = m.meshFromSegment(list(seg))
            nrm = V()
            for f in sub.Facets:
                nrm += V(f.Normal) * f.Area
            if nrm.Length > 1e-12:
                nrm.normalize()
            rows.append({"facets": len(seg), "area": round(sub.Area, 3), "center": geo.vec(sub.BoundBox.Center), "normal": geo.vec(nrm)})
        rows.sort(key=lambda r: -r["area"])
        out = {"mesh": o.Label, "kind": kind, "segments": len(rows), "largest": rows[: int(a.get("top", 12))]}
        if a.get("keep"):
            made = []
            for i, seg in enumerate(segs[: int(a.get("top", 12))], 1):
                made.append(new_mesh(m.meshFromSegment(list(seg)), f"{o.Label} segment {i}").Label)
            doc().recompute()
            s.snapshot("meshobj_segments")
            out["kept"] = made
            out["step"] = len(s.steps)
        out["use"] = "A flat segment's normal and centre place a sketch (sketch_new { face }) or a datum plane on a scan."
        return out

    def meshobj_curvature(a):
        o = mesh_of(a.get("mesh"))
        c = np.array([(k[0], k[1]) for k in o.Mesh.getCurvaturePerVertex()], dtype=float)
        k1, k2 = c[:, 0], c[:, 1]
        mean = (k1 + k2) / 2
        kmax = np.max(np.abs(c), axis=1)
        flat = float(np.mean(kmax < float(a.get("flat", 1e-3))) * 100)
        r = 1 / np.maximum(kmax, 1e-12)
        return {"mesh": o.Label, "points": int(len(c)), "flatPercent": round(flat, 2),
                "tightestRadius": round(float(np.min(r)), 4), "medianRadius": round(float(np.median(r[kmax > 1e-6])) if (kmax > 1e-6).any() else math.inf, 3),
                "meanCurvature": {"min": round(float(mean.min()), 5), "max": round(float(mean.max()), 5)},
                "next": "view_render { map: \"curvature\", objects: [the mesh] } colours it."}

    def meshobj_project(a):
        import MeshPart

        o = mesh_of(a.get("mesh"))
        src = next((x for x in doc().Objects if a.get("shape") in (x.Label, x.Name) and hasattr(x, "Shape")), None)
        if src is None or not src.Shape.Edges:
            raise bad("shape: a wire, sketch or body with edges to project.")
        d = V(*[float(x) for x in (a.get("direction") or [0, 0, -1])])
        try:
            polys = MeshPart.projectShapeOnMesh(src.Shape, o.Mesh, d)
        except Exception as e:
            raise ToolError("failed", f"The projection failed: {e}")
        wires = [Part.makePolygon([V(p) for p in pl]) for pl in polys if len(pl) >= 2]
        if not wires:
            raise ToolError("failed", "Nothing landed on the mesh in that direction.")
        c = doc().addObject("Part::Feature", "Projection")
        c.Label = s.unique(a.get("name") or f"{src.Label} on {o.Label}")
        c.Shape = Part.Compound(wires)
        doc().recompute()
        s.snapshot("meshobj_project")
        return {"projection": c.Label, "wires": len(wires), "length": round(sum(w.Length for w in wires), 3), "step": len(s.steps)}

    def meshobj_export(a):
        o = mesh_of(a.get("mesh"))
        p = os.path.expanduser(a.get("path") or f"{o.Label}.stl")
        if not os.path.isabs(p):
            p = os.path.join(s.out_dir, p)
        try:
            o.Mesh.write(p)
        except Exception as e:
            raise ToolError("failed", f"Not written: {e} — formats by extension: stl, obj, ply, off, amf, 3mf, smf, asy …")
        return {"mesh": o.Label, "file": p, "bytes": os.path.getsize(p)}

    mref = {"type": "string", "description": "The mesh's label; may be left out when there is one."}
    cp = {"copy": {"type": "boolean"}, "name": {"type": "string"}}
    def unwrap_apart(P, F, iters, weight):
        import subprocess
        import sys
        import tempfile

        d = tempfile.mkdtemp(prefix="agentlink-unwrap-")
        src, dst = os.path.join(d, "in.npz"), os.path.join(d, "out.npz")
        np.savez(src, P=P, F=F)
        code = (
            "import sys, numpy as np\n"
            "import FreeCAD, Part, Mesh  # flatmesh stands on them: loaded first\n"
            "import flatmesh\n"
            "z = np.load(sys.argv[1])\n"
            "fl = flatmesh.FaceUnwrapper(np.ascontiguousarray(z['P'], dtype=np.float64), np.ascontiguousarray(z['F'], dtype=np.int64))\n"
            f"fl.findFlatNodes({iters}, {weight})\n"
            "b = fl.getFlatBoundaryNodes()\n"
            "np.savez(sys.argv[2], Q=np.asarray(fl.ze_nodes, float), n=np.array([len(x) for x in b]), B=np.concatenate([np.asarray(x, float) for x in b]) if b else np.zeros((0, 3)))\n"
        )
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join([x for x in sys.path if x])
        exe = sys.executable if sys.executable and "python" in os.path.basename(sys.executable).lower() else None
        if exe is None:
            raise ToolError("unavailable", "No Python to run the unwrapping in apart from FreeCAD here.")
        try:
            r = subprocess.run([exe, "-c", code, src, dst], env=env, capture_output=True, text=True, timeout=300)
        except subprocess.TimeoutExpired:
            raise ToolError("failed", "The unwrapping ran over 5 minutes: a coarser tolerance, or a smaller surface.") from None
        if r.returncode != 0 or not os.path.isfile(dst):
            why = (r.stderr or "").strip().splitlines()[-1:] or [f"it stopped (code {r.returncode})"]
            raise ToolError("failed", f"FreeCAD's unwrapping could not lay this surface flat: {why[0]} — split it into simpler faces (a cone or cylinder side unwraps; a whole sphere does not).")
        z = np.load(dst)
        Q = np.asarray(z["Q"], float)[:, :2]
        n, B = list(z["n"]), np.asarray(z["B"], float)
        bounds, k = [], 0
        for c in n:
            bounds.append(B[k:k + int(c), :2])
            k += int(c)
        return Q, bounds

    def meshobj_unwrap(a):
        """A curved surface laid flat (MeshPart's unwrap): its outline as a flat
        pattern to cut from sheet, with how much it had to stretch."""
        import importlib.util

        if importlib.util.find_spec("flatmesh") is None:
            raise ToolError("unavailable", "This FreeCAD has no flatmesh (MeshPart's unwrapping): it was built without it.")
        from .. import flat2d
        from . import picks as _p

        ref = a.get("of")
        if not isinstance(ref, str):
            raise bad("of: a mesh's name, or a body's face \"Body.Face3\".")
        label, sub, el = _p.resolve(s, ref)
        if sub and sub.startswith("Face"):
            vs, ts = el.tessellate(float(a.get("tolerance", 0.1)))
            P = np.array([(v.x, v.y, v.z) for v in vs], float)
            F = np.array(ts, int)
        else:
            m = mesh_of(label).Mesh
            P = np.array([(p.x, p.y, p.z) for p in m.Points], float)
            F = np.array([list(f) for f in m.Topology[1]], int)
        if len(F) < 2:
            raise bad("of: a surface with area.")
        # flatmesh can bring FreeCAD down on a surface it cannot handle:
        # it runs in a Python of its own, and a crash there is only a refusal
        Q, bounds = unwrap_apart(P, F, int(a.get("iterations", 5)), float(a.get("weight", 0.95)))
        def area(X, T):
            A, B, C = X[T[:, 0]], X[T[:, 1]], X[T[:, 2]]
            if X.shape[1] == 2:
                return 0.5 * np.abs((B[:, 0] - A[:, 0]) * (C[:, 1] - A[:, 1]) - (B[:, 1] - A[:, 1]) * (C[:, 0] - A[:, 0]))
            return 0.5 * np.linalg.norm(np.cross(B - A, C - A), axis=1)
        a3, a2 = area(P, F), area(Q, F)
        stretch = np.where(a3 > 1e-12, a2 / np.maximum(a3, 1e-12) - 1, 0.0)
        items = [{"kind": "line", "points": b.tolist(), "closed": True, "layer": "cut"} for b in bounds if len(b) >= 3]
        out = {"of": ref, "area3d": round(float(a3.sum()), 3), "areaFlat": round(float(a2.sum()), 3),
               "stretch": {"mean": round(float(np.abs(stretch).mean()) * 100, 3), "max": round(float(np.abs(stretch).max()) * 100, 3), "unit": "%"},
               "outlines": len(items)}
        x0, y0, x1, y1 = flat2d.bounds(items)
        out["size"] = [round(x1 - x0, 3), round(y1 - y0, 3)]
        if a.get("path"):
            pth = a["path"] if os.path.isabs(a["path"]) else os.path.join(s.out_dir, a["path"])
            os.makedirs(os.path.dirname(pth), exist_ok=True)
            with open(pth, "w", encoding="utf-8") as f:
                f.write(flat2d.dxf(items) if pth.lower().endswith(".dxf") else flat2d.svg(items))
            out["path"] = pth
        if a.get("make", True):
            o = doc().addObject("Part::Feature", "Pattern")
            o.Label = s.unique(a.get("name") or f"{label} flat")
            o.Shape = Part.makeCompound([Part.makePolygon([FreeCAD.Vector(x, y, 0) for x, y in b.tolist()] + [FreeCAD.Vector(*b[0].tolist(), 0)]) for b in bounds if len(b) >= 3])
            doc().recompute()
            s.snapshot("meshobj_unwrap")
            out["pattern"] = o.Label
        if out["stretch"]["max"] > 5:
            out["note"] = "Doubly curved: it cannot lie flat without stretching — the most stretched spots are where a cut or dart would help."
        return out

    return [
        Tool("meshobj_unwrap", "Lay a curved surface flat (MeshPart's unwrap): of a body's face \"Body.Face3\" or a mesh — its flat outline (a pattern to cut from sheet, fabric, leather), the area before and after and how much it stretched (mean, max %); path .svg or .dxf writes the pattern; make: false keeps no object.",
             obj({"of": {"type": "string"}, "path": {"type": "string"}, "make": {"type": "boolean"}, "tolerance": {"type": "number"}, "iterations": {"type": "integer"}, "weight": {"type": "number"}, "name": {"type": "string"}}, ["of"]), meshobj_unwrap, changes=True),
        Tool("meshobj_from_shape", "Mesh a body (MeshPart): method standard (deflection mm, angle deg) | mefisto (maxLength: even triangles) | netgen (fineness verycoarse … veryfine). Kept as a mesh object.",
             obj({"body": {"type": "string"}, "method": {"type": "string"}, "deflection": {"type": "number"}, "angle": {"type": "number"}, "maxLength": {"type": "number"}, "fineness": {"type": "string"}, "name": {"type": "string"}}), meshobj_from_shape, changes=True),
        Tool("meshobj_info", "A mesh's size and the Mesh workbench's evaluation: closed or open, components, non-manifolds, self-intersections, flipped normals, corrupt facets. Changes nothing.", obj({"mesh": mref}), meshobj_info),
        Tool("meshobj_repair", "Repair a mesh as Evaluate & Repair does: fix any of normals, duplicates, nonmanifolds, selfintersections, degenerate, folds, indices (default those), holes (maxHoleEdges), islands (minFacets); answers what it fixed and what is left. copy: true leaves the original.",
             obj({"mesh": mref, "fix": {"type": "array", "items": {"type": "string"}}, "tolerance": {"type": "number"}, "maxHoleEdges": {"type": "integer"}, "minFacets": {"type": "integer"}, **cp}), meshobj_repair, changes=True),
        Tool("meshobj_decimate", "Fewer triangles: reduction (share to remove) or facets (how many to keep), tolerance mm it may move the surface.",
             obj({"mesh": mref, "reduction": {"type": "number"}, "facets": {"type": "integer"}, "tolerance": {"type": "number"}, **cp}), meshobj_decimate, changes=True),
        Tool("meshobj_refine", "More triangles: rounds (each splits every triangle in four), optimize: true evens their shapes.", obj({"mesh": mref, "rounds": {"type": "integer"}, "optimize": {"type": "boolean"}, **cp}), meshobj_refine, changes=True),
        Tool("meshobj_smooth", "Smooth a mesh: method taubin (keeps its size; lambda, mu) | laplace (lambda; shrinks), iterations.",
             obj({"mesh": mref, "method": {"type": "string"}, "iterations": {"type": "integer"}, "lambda": {"type": "number"}, "mu": {"type": "number"}, **cp}), meshobj_smooth, changes=True),
        Tool("meshobj_offset", "Move a mesh's surface along its normals by distance mm (negative inward).", obj({"mesh": mref, "distance": {"type": "number"}, **cp}, ["distance"]), meshobj_offset, changes=True),
        Tool("meshobj_trim", "Cut a mesh with a plane: at [x,y,z], normal (the side it points to goes); close: true fills the cut.",
             obj({"mesh": mref, "at": {"type": "array"}, "normal": {"type": "array"}, "close": {"type": "boolean"}, "maxHoleEdges": {"type": "integer"}, **cp}), meshobj_trim, changes=True),
        Tool("meshobj_transform", "Scale (a factor or [x,y,z]), rotate { axis, angle }, move [x,y,z] or mirror \"x\" | \"y\" | \"z\" a mesh's points, in that order.",
             obj({"mesh": mref, "scale": {"type": ["number", "array"]}, "rotate": {"type": "object"}, "move": {"type": "array"}, "mirror": {"type": "string"}, **cp}), meshobj_transform, changes=True),
        Tool("meshobj_boolean", "A boolean of two closed meshes: a, b, op union | intersection | difference; a new mesh.",
             obj({"a": {"type": "string"}, "b": {"type": "string"}, "op": {"type": "string"}, "name": {"type": "string"}}, ["a", "b"]), meshobj_boolean, changes=True),
        Tool("meshobj_sections", "Cross-sections of a mesh: normal (default z), count or at [distances]; each slice's loops and length, kept as wires.",
             obj({"mesh": mref, "normal": {"type": "array"}, "count": {"type": "integer"}, "at": {"type": "array"}, "tolerance": {"type": "number"}, "name": {"type": "string"}}), meshobj_sections, changes=True),
        Tool("meshobj_split", "Split a mesh into its separate pieces, a mesh each (minFacets drops crumbs).", obj({"mesh": mref, "minFacets": {"type": "integer"}}), meshobj_split, changes=True),
        Tool("meshobj_segments", "Find a mesh's regions: kind planar (deviation mm, minFacets) | curvature; each with its area, centre and normal, largest first; keep: true makes the top ones meshes. On a scan: where the flat faces are.",
             obj({"mesh": mref, "kind": {"type": "string"}, "deviation": {"type": "number"}, "tolerance": {"type": "number"}, "minFacets": {"type": "integer"}, "top": {"type": "integer"}, "keep": {"type": "boolean"}}), meshobj_segments, changes=True),
        Tool("meshobj_curvature", "A mesh's curvature: how much is flat, the tightest and the median radius, mean curvature's range. view_render { map: \"curvature\" } colours it. Changes nothing.",
             obj({"mesh": mref, "flat": {"type": "number"}}), meshobj_curvature),
        Tool("meshobj_project", "Project a wire, sketch or body's edges onto a mesh along direction [x,y,z] (default down); kept as wires on its surface.",
             obj({"mesh": mref, "shape": {"type": "string"}, "direction": {"type": "array"}, "name": {"type": "string"}}, ["shape"]), meshobj_project, changes=True),
        Tool("meshobj_export", "Write a document mesh to a file by extension (stl, obj, ply, off, amf, 3mf …): path (default <label>.stl in the out folder).",
             obj({"mesh": mref, "path": {"type": "string"}}), meshobj_export),
    ]
