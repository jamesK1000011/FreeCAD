# SPDX-License-Identifier: LGPL-2.1-or-later
# The rest of FreeCAD's FEM workbench, headless: any boundary condition,
# material, element, equation, solver or mesh refinement FEM has, added to an
# analysis by kind on faces, edges or solids picked by name or by query; a
# Netgen mesh; a FEM mesh back to a plain mesh; clearing mesh groups; and the
# stress linearization through a wall (membrane and bending) from a result.

import FreeCAD

from .. import facepick
from ..protocol import Tool, ToolError
from ..tools import obj
from . import picks

GROUP = "fem_more"
INFO = "FEM: every boundary condition, material, element, equation, solver and mesh refinement by kind; Netgen meshes, FEM mesh to mesh, mesh groups cleared; stress linearization."

# kind → (ObjectsFem factory, what it needs: None | "mesh" | "solver")
KINDS = {
    # boundary conditions
    "bearing": ("makeConstraintBearing", None),
    "body_heat_source": ("makeConstraintBodyHeatSource", None),
    "current_density": ("makeConstraintCurrentDensity", None),
    "electric_charge_density": ("makeConstraintElectricChargeDensity", None),
    "electromagnetic": ("makeConstraintElectromagnetic", None),
    "flow_velocity": ("makeConstraintFlowVelocity", None),
    "fluid_boundary": ("makeConstraintFluidBoundary", None),
    "gear": ("makeConstraintGear", None),
    "initial_flow_velocity": ("makeConstraintInitialFlowVelocity", None),
    "initial_pressure": ("makeConstraintInitialPressure", None),
    "initial_temperature": ("makeConstraintInitialTemperature", None),
    "magnetization": ("makeConstraintMagnetization", None),
    "plane_rotation": ("makeConstraintPlaneRotation", None),
    "pulley": ("makeConstraintPulley", None),
    "rigid_body": ("makeConstraintRigidBody", None),
    "section_print": ("makeConstraintSectionPrint", None),
    "spring": ("makeConstraintSpring", None),
    "transform": ("makeConstraintTransform", None),
    "centrifugal": ("makeConstraintCentrif", None),
    "contact": ("makeConstraintContact", None),
    "tie": ("makeConstraintTie", None),
    "displacement": ("makeConstraintDisplacement", None),
    "heatflux": ("makeConstraintHeatflux", None),
    "temperature": ("makeConstraintTemperature", None),
    "vacuum_permittivity": ("makeConstantVacuumPermittivity", None),
    # elements and materials
    "fluid_1d": ("makeElementFluid1D", None),
    "rotation_1d": ("makeElementRotation1D", None),
    "geometry_1d": ("makeElementGeometry1D", None),
    "geometry_2d": ("makeElementGeometry2D", None),
    "material_fluid": ("makeMaterialFluid", None),
    "material_reinforced": ("makeMaterialReinforced", None),
    "material_nonlinear": ("makeMaterialMechanicalNonlinear", None),
    # solvers and equations
    "solver_mystran": ("makeSolverMystran", None),
    "solver_z88": ("makeSolverZ88", None),
    "solver_elmer": ("makeSolverElmer", None),
    "solver_calculix": ("makeSolverCalculiX", None),
    "eq_magnetodynamic2d": ("makeEquationMagnetodynamic2D", "solver"),
    "eq_magnetodynamic": ("makeEquationMagnetodynamic", "solver"),
    "eq_electrostatic": ("makeEquationElectrostatic", "solver"),
    "eq_heat": ("makeEquationHeat", "solver"),
    "eq_flow": ("makeEquationFlow", "solver"),
    "eq_elasticity": ("makeEquationElasticity", "solver"),
    # mesh refinements (on a Gmsh mesh)
    "mesh_region": ("makeMeshRegion", "mesh"),
    "mesh_boundary_layer": ("makeMeshBoundaryLayer", "mesh"),
    "mesh_group": ("makeMeshGroup", "mesh"),
    "mesh_distance": ("makeMeshDistance", "mesh"),
    "mesh_manipulate": ("makeMeshManipulate", "mesh"),
    "mesh_advanced": ("makeMeshAdvanced", "mesh"),
    "mesh_shape": ("makeMeshShape", "mesh"),
    "mesh_transfinite_curve": ("makeMeshTransfiniteCurve", "mesh"),
    "mesh_transfinite_surface": ("makeMeshTransfiniteSurface", "mesh"),
    "mesh_transfinite_volume": ("makeMeshTransfiniteVolume", "mesh"),
}


def bad(msg):
    return ToolError("invalid_input", msg)


def _fem():
    try:
        import ObjectsFem

        return ObjectsFem
    except ImportError:
        raise ToolError("unavailable", "FreeCAD's FEM workbench is not in this build.")


def tools(s):
    def d():
        if s.doc is None:
            raise ToolError("invalid_state", "No document open: doc_new first.")
        return s.doc

    def analysis(ref):
        ans = [o for o in d().Objects if o.TypeId == "Fem::FemAnalysis" or o.isDerivedFrom("Fem::FemAnalysis")]
        if not ans:
            raise ToolError("not_found", "No FEM analysis: fea_native makes one (or kind solver_* with a new analysis: true).")
        if ref is None:
            if len(ans) == 1:
                return ans[0]
            raise bad(f"analysis: which — {', '.join(x.Label for x in ans)}.")
        for x in ans:
            if ref in (x.Label, x.Name):
                return x
        raise ToolError("not_found", f"analysis {ref!r}: they are {', '.join(x.Label for x in ans)}.")

    def refs_of(a):
        """refs: ["Body.Face3", …], or { body, faces: [queries as facepick takes] }."""
        out = {}
        r = a.get("refs")
        if not r:
            return []
        if isinstance(r, dict):
            o = picks.shaped(d(), r.get("body"), "refs.body")
            qs = r.get("faces") or {}
            qs = qs if isinstance(qs, list) else [qs]
            names = list(dict.fromkeys(n for q in qs for n, _ in facepick.pick(o.Shape, q, "refs.faces")))
            return [(o, tuple(names))]
        for x in r:
            head, sub = picks.split_ref(x)
            if head is None or sub is None:
                raise bad(f"refs: \"Body.Face3\", not {x!r}.")
            o = picks.shaped(d(), head)
            picks.element(o.Shape, sub, o.Label)
            out.setdefault(o.Name, (o, []))[1].append(sub)
        return [(o, tuple(v)) for o, v in out.values()]

    def set_props(o, props):
        for k, v in (props or {}).items():
            if k not in o.PropertiesList:
                raise bad(f"props.{k}: {o.Label} has no such property — it has {', '.join(p for p in o.PropertiesList if p[0].isupper())[:600]}.")
            try:
                cur = getattr(o, k)
                if isinstance(cur, FreeCAD.Units.Quantity) and isinstance(v, (int, float)):
                    v = FreeCAD.Units.Quantity(v, cur.Unit)
                elif isinstance(cur, FreeCAD.Vector) and isinstance(v, (list, tuple)):
                    v = FreeCAD.Vector(*v)
                setattr(o, k, v)
            except Exception as e:
                raise bad(f"props.{k} = {v!r}: {e}")

    def fem_add(a):
        F = _fem()
        kind = a.get("kind")
        if kind not in KINDS:
            raise bad("kind: " + ", ".join(KINDS) + ".")
        fn, need = KINDS[kind]
        if not hasattr(F, fn):
            raise ToolError("unavailable", f"{kind} is not in FreeCAD {'.'.join(FreeCAD.Version()[:3])}'s FEM.")
        if a.get("new") and kind.startswith("solver_"):
            an = F.makeAnalysis(d(), "Analysis")
            an.Label = s.unique(a.get("name") or "Analysis")
        else:
            an = analysis(a.get("analysis"))
        if need == "mesh":
            m = picks.find(d(), a["mesh"], "mesh") if a.get("mesh") else next((x for x in an.Group if hasattr(x, "FemMesh") and "Gmsh" in x.TypeId + getattr(getattr(x, "Proxy", None), "Type", "")), None)
            if m is None:
                m = next((x for x in an.Group if hasattr(x, "FemMesh")), None)
            if m is None:
                raise ToolError("not_found", f"{an.Label} has no mesh to refine: fea_native makes one (Gmsh).")
            o = getattr(F, fn)(d(), m)
        elif need == "solver":
            sv = picks.find(d(), a["solver"], "solver") if a.get("solver") else next((x for x in an.Group if "Elmer" in getattr(getattr(x, "Proxy", None), "Type", "") or "Elmer" in x.Label), None)
            if sv is None:
                raise ToolError("not_found", f"{an.Label} has no Elmer solver for equations: fem_add kind solver_elmer first.")
            o = getattr(F, fn)(d(), sv)
        else:
            o = getattr(F, fn)(d())
            an.addObject(o)
        rf = refs_of(a)
        if rf:
            if not hasattr(o, "References"):
                raise bad(f"{kind} takes no references.")
            o.References = rf
        set_props(o, a.get("props"))
        if a.get("name"):
            o.Label = s.unique(a["name"])
        d().recompute()
        s.snapshot("fem_add")
        out = {"added": o.Label, "kind": kind, "analysis": an.Label, "type": o.TypeId}
        if rf:
            out["refs"] = [f"{x.Label}.{n}" for x, ns in rf for n in ns]
        settable = [p for p in o.PropertiesList if p[0].isupper() and p not in ("Label", "Label2", "Proxy", "ExpressionEngine", "Visibility", "References", "Placement", "Shape")]
        out["props"] = {p: _show(getattr(o, p)) for p in settable[:30]}
        return out

    def fem_mesh(a):
        F = _fem()
        act = a.get("action")
        if act == "netgen":
            b = picks.shaped(d(), a.get("body"), "body")
            an = analysis(a.get("analysis")) if a.get("analysis") or any(o.TypeId == "Fem::FemAnalysis" for o in d().Objects) else None
            m = F.makeMeshNetgen(d(), "MeshNetgen")
            m.Shape = b
            set_props(m, a.get("props"))
            if an is not None:
                an.addObject(m)
            try:
                d().recompute()
                from femmesh import netgentools  # noqa: F401
            except Exception:
                pass
            nodes = m.FemMesh.NodeCount if m.FemMesh is not None else 0
            if not nodes:
                try:
                    import Fem  # noqa: F401
                    from femtools import membertools  # noqa: F401
                    import femmesh.netgentools as NT

                    t = NT.NetgenTools(m)
                    t.run()
                    t.update_properties()
                    nodes = m.FemMesh.NodeCount
                except Exception as e:
                    d().removeObject(m.Name)
                    raise ToolError("unavailable", f"Netgen did not mesh here ({e}): fea_native's Gmsh mesh does.")
            s.snapshot("fem_mesh")
            return {"mesh": m.Label, "nodes": nodes, "volumes": m.FemMesh.VolumeCount}
        m = picks.find(d(), a.get("mesh"), "mesh")
        if not hasattr(m, "FemMesh"):
            raise bad(f"{m.Label} is not a FEM mesh.")
        if act == "to_mesh":
            from femmesh.femmesh2mesh import femmesh_2_mesh

            res = picks.find(d(), a["result"], "result") if a.get("result") else None
            out_mesh = femmesh_2_mesh(m.FemMesh, res) if res is not None else femmesh_2_mesh(m.FemMesh)
            import Mesh

            o = d().addObject("Mesh::Feature", "Mesh")
            o.Mesh = Mesh.Mesh(out_mesh)
            o.Label = s.unique(a.get("name") or f"{m.Label} surface")
            d().recompute()
            s.snapshot("fem_mesh")
            return {"mesh": o.Label, "facets": o.Mesh.CountFacets, **({"deformed": res.Label} if res is not None else {})}
        if act == "clear_groups":
            fm = m.FemMesh
            ids = list(fm.Groups)
            for g in ids:
                fm.removeGroup(g)
            m.FemMesh = fm
            s.snapshot("fem_mesh")
            return {"mesh": m.Label, "cleared": len(ids), "groups": fm.GroupCount}
        raise bad("action: netgen { body, props } | to_mesh { mesh, result } | clear_groups { mesh }.")

    def fem_linearize(a):
        """Membrane and bending stress through a wall, ASME VIII-2 style: von
        Mises (or a component) along a line from one face to the other."""
        src = picks.find(d(), a.get("result"), "result")
        p0 = FreeCAD.Vector(*picks.vec3(a.get("from"), "from"))
        p1 = FreeCAD.Vector(*picks.vec3(a.get("to"), "to"))
        n = int(a.get("points", 41))
        L = (p1 - p0).Length
        if L <= 0 or n < 5:
            raise bad("from and to: the line through the wall, apart; points 5 or more.")
        field = a.get("field", "von Mises Stress")
        if src.isDerivedFrom("Fem::FemPostPipeline"):
            f = d().addObject("Fem::FemPostDataAlongLineFilter", "Linearized")
            src.addObject(f) if hasattr(src, "addObject") else None
            f.Point1, f.Point2, f.Resolution = p0, p1, n - 1
            f.PlotData = field
            d().recompute()
            xs = list(f.XAxisData)
            ys = list(f.YAxisData)
            if not ys:
                raise ToolError("failed", f"No {field!r} along the line: inside the part, through the wall; field names are the pipeline's.")
        elif hasattr(src, "vonMises") and hasattr(src, "Mesh"):
            # A CalculiX result object: nearest node per sample.
            fm = src.Mesh.FemMesh
            vals = list(src.vonMises) if field.lower().startswith("von") else list(getattr(src, field))
            ids = list(src.NodeNumbers)
            pos = {i: fm.getNodeById(i) for i in ids}
            xs, ys = [], []
            for k in range(n):
                t = k / (n - 1)
                q = p0 + (p1 - p0) * t
                j = min(range(len(ids)), key=lambda m: (pos[ids[m]] - q).Length)
                xs.append(t * L)
                ys.append(vals[j])
        else:
            raise bad(f"{src.Label}: a FEM result (CalculiX result or a post pipeline).")
        # Membrane: the mean; bending: the linear part, at the surfaces.
        T = xs[-1] - xs[0] or L
        h = [xs[i + 1] - xs[i] for i in range(len(xs) - 1)]
        mem = sum((ys[i] + ys[i + 1]) / 2 * h[i] for i in range(len(h))) / T
        # the linear part by least squares: its slope × half the thickness
        mx = sum(xs) / len(xs)
        my = sum(ys) / len(ys)
        slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sum((x - mx) ** 2 for x in xs) or 1)
        bend = slope * T / 2
        peak = max(abs(y) for y in ys)
        return {"result": src.Label, "field": field, "thickness": round(T, 4), "membrane": round(mem, 4), "bending": round(abs(bend), 4),
                "membranePlusBending": round(abs(mem) + abs(bend), 4), "peak": round(peak, 4), "points": len(ys),
                "note": "Stress linearization along the line (membrane = mean, bending from the first moment): compare with the code's allowables; FEA is a feasibility check, not a sign-off."}

    st = {"type": "string"}
    return [
        Tool("fem_add", "Add anything FEM has to an analysis: kind (" + ", ".join(KINDS) + "); refs [\"Body.Face3\", …] or { body, faces: [queries] }; props { its FreeCAD properties (numbers in its units) } — the answer lists them; mesh / solver where a refinement or equation needs one; new: true makes a new analysis for a solver.",
             obj({"kind": {"type": "string", "enum": list(KINDS)}, "analysis": st, "refs": {}, "props": {"type": "object"}, "mesh": st, "solver": st, "new": {"type": "boolean"}, "name": st}, ["kind"]), fem_add, changes=True),
        Tool("fem_mesh", "FEM meshes: action netgen { body, props } (Netgen's mesh) | to_mesh { mesh, result } (the FEM mesh's outside as a plain mesh, deformed by a result) | clear_groups { mesh }.",
             obj({"action": {"type": "string", "enum": ["netgen", "to_mesh", "clear_groups"]}, "body": st, "mesh": st, "result": st, "analysis": st, "props": {"type": "object"}, "name": st}, ["action"]), fem_mesh, changes=True),
        Tool("fem_linearize", "Stress linearization through a wall from a FEM result (CalculiX result or post pipeline): from and to [x,y,z] across the wall, points, field (von Mises by default) → membrane, bending, membrane + bending, peak.",
             obj({"result": st, "from": {"type": "array", "items": {"type": "number"}}, "to": {"type": "array", "items": {"type": "number"}}, "points": {"type": "integer"}, "field": st}, ["result", "from", "to"]), fem_linearize),
    ]


def _show(v):
    if isinstance(v, FreeCAD.Units.Quantity):
        return str(v)
    if isinstance(v, FreeCAD.Vector):
        return [round(v.x, 6), round(v.y, 6), round(v.z, 6)]
    if isinstance(v, (int, float, str, bool)) or v is None:
        return v
    if isinstance(v, (list, tuple)):
        return [_show(x) for x in v][:10]
    return getattr(v, "Label", str(v))[:80]

