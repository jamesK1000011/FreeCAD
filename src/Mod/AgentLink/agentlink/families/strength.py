# SPDX-License-Identifier: LGPL-2.1-or-later
# Strength by finite elements: a linear static solve of one body, meshed by
# Gmsh into ten-node tetrahedra, read into FreeCAD's FemMesh (which finds
# the nodes and element faces on each picked face and writes the CalculiX
# deck's mesh), solved by CalculiX (ccx), and read back from the .frd.
# Headless: no FEM workbench GUI, no Qt. The study (body, material, holds,
# loads) is kept with the document; the last results with the session.

import math
import os
import shutil
import subprocess
import time

import FreeCAD
import Part

from .. import ccx_io
from .. import facepick
from .. import geometry as geo
from .. import store
from ..protocol import Tool, ToolError
from ..tools import obj
from . import material as matfam

GROUP = "strength"
INFO = "Strength by finite elements (Gmsh and CalculiX): set up a body, hold faces, load them, solve for stress, displacement and safety factor."

V = FreeCAD.Vector
KEY = "fea"
LAYER_FACTOR = 0.5  # across-layer strength taken as this share of the yield
G = 9810.0  # mm/s²
TRAPS = "A rigid hold makes stress no real mount does; a coarse mesh under-reads peaks; library figures are typical."


def _study(doc):
    return store.read(doc, KEY, None)


def _save(doc, st):
    store.write(doc, KEY, st)


def _results(session):
    return getattr(session, "_agentlink_fea", None)


def _binary(name, pref_group, pref_key, env):
    p = os.environ.get(env)
    if p and shutil.which(p):
        return shutil.which(p)
    try:
        q = FreeCAD.ParamGet(f"User parameter:BaseApp/Preferences/Mod/Fem/{pref_group}").GetString(pref_key, "")
        if q and shutil.which(q):
            return shutil.which(q)
    except Exception:
        pass
    return shutil.which(name)


def _dir(v):
    if isinstance(v, str):
        m = {"+x": (1, 0, 0), "-x": (-1, 0, 0), "+y": (0, 1, 0), "-y": (0, -1, 0), "+z": (0, 0, 1), "-z": (0, 0, -1)}
        if v.lower() not in m:
            raise ToolError("invalid_input", "buildDir: +x, -x, +y, -y, +z, -z or [x, y, z].")
        return list(m[v.lower()])
    if isinstance(v, list) and len(v) == 3:
        n = math.sqrt(sum(float(x) ** 2 for x in v))
        if n > 0:
            return [float(x) / n for x in v]
    raise ToolError("invalid_input", "buildDir: +x, -x, +y, -y, +z, -z or [x, y, z].")


def _need_study(session):
    st = _study(session.doc)
    if not st:
        raise ToolError("invalid_state", "No strength study: fea_setup { body } first.")
    o = session.body(st["body"])
    return st, o


def _pick_report(shape, q, what):
    picked = facepick.pick(shape, q, what)
    return picked, [facepick.describe(n, f) for n, f in picked]


def tools(session):
    s = session

    def fea_setup(a):
        o = s.body(a.get("body"))
        bb = o.Shape.BoundBox
        least = min(bb.XLength, bb.YLength, bb.ZLength)
        mesh = a.get("mesh")
        if mesh is None:
            mesh = round(max(0.5, min(least / 3.0, bb.DiagonalLength / 15.0)), 2)
        mesh = float(mesh)
        if mesh <= 0:
            raise ToolError("invalid_input", "mesh: the largest element edge, mm, over 0.")
        est = o.Shape.Volume / (mesh ** 3 / 8.0)
        if est > 400000:
            raise ToolError("invalid_input", f"mesh {mesh:g} mm makes some {int(est):,} elements: too many to solve here — {round(mesh * (est / 300000) ** (1 / 3), 1)} mm or more.")
        if a.get("material"):
            c = matfam.card(s.doc, a["material"]) or matfam.card(s.doc, str(a["material"]).lower())
            if c is None:
                raise ToolError("not_found", f"material {a['material']!r}: no such id — material_list finds one.")
            mid, said = c["id"], "given"
        else:
            c, set_ = matfam.of_body(s.doc, o.Label)
            mid, said = c["id"], "the body's" if set_ else "assumed PLA: material_set records the real one"
        if not c.get("E"):
            raise ToolError("invalid_state", f"{c['name']} has no Young's modulus on its card: material_set custom {{ name, density, youngsMPa, yieldMPa }} gives one.")
        setup = store.setup_of(s.doc, o.Label, "print")
        build = None
        if a.get("buildDir") is not None:
            build = _dir(a["buildDir"])
        elif setup:
            build = _dir(setup.get("up", "+z"))
        old = _study(s.doc) or {}
        keep = old.get("body") == o.Label and not a.get("reset")
        st = {"body": o.Label, "material": mid, "mesh": mesh, "buildDir": build, "holds": old.get("holds", []) if keep else [], "loads": old.get("loads", []) if keep else []}
        _save(s.doc, st)
        s.snapshot("fea_setup")
        out = {"study": st, "material": {"id": mid, "name": c["name"], "E": c["E"], "nu": c.get("nu"), "yield": c.get("yield"), "from": said}}
        if build:
            out["printed"] = f"Built along {build}: the safety across the layers is judged at {LAYER_FACTOR}× yield."
        if mesh > least / 2:
            out["warning"] = f"mesh {mesh:g} mm is over half the part's least size ({least:.1f} mm): too coarse to read stress — {round(least / 3, 2)} mm or less."
        out["next"] = "fea_hold the faces it is held by, fea_load what pushes on it, then fea_solve."
        return out

    def fea_hold(a):
        st, o = _need_study(s)
        if a.get("clear"):
            st["holds"] = []
            _save(s.doc, st)
            s.snapshot("fea_hold")
            return {"holds": []}
        q = a.get("faces")
        pk, picked = _pick_report(o.Shape, q, "faces")
        kind = a.get("kind", "fixed")
        h = {"faces": q, "kind": kind}
        AX = {"x": 1, "y": 2, "z": 3}
        if kind == "fixed":
            note = "Held rigidly: no movement at all on these faces. Real bolts and clamps give a little, so stress right at a hold reads high."
        elif kind == "slide":
            n = V(0, 0, 0)
            for _, f in pk:
                n += facepick.outward_normal(f) * f.Area
            if n.Length < 1e-9:
                raise ToolError("invalid_input", "slide: these faces have no one normal — slide holds a flat face (or faces facing one way).")
            n.normalize()
            k = max(range(3), key=lambda i: abs(n[i]))
            if abs(n[k]) < 0.999:
                raise ToolError("invalid_input", f"slide: the faces face {[round(c, 3) for c in n]} — a slide holds along x, y or z; turn the part square or use pin with dofs.")
            h["dofs"], h["values"] = [k + 1], [0.0]
            note = f"Held only along {'xyz'[k]} (a roller or a smooth floor): free to slide in the face. Add another hold or slide so the part cannot drift."
        elif kind == "pin":
            dofs = a.get("dofs")
            if not dofs or not all(d in AX for d in dofs):
                raise ToolError("invalid_input", "pin: dofs [\"x\", \"y\", \"z\"] — the directions held (the rest free).")
            h["dofs"], h["values"] = sorted(AX[d] for d in set(dofs)), [0.0] * len(set(dofs))
            note = f"Held in {', '.join(sorted(set(dofs)))} only."
        elif kind == "move":
            m = a.get("move")
            if not (isinstance(m, list) and len(m) == 3):
                raise ToolError("invalid_input", "move: [dx, dy, dz] mm the faces are pushed to (null leaves that direction free).")
            h["dofs"] = [i + 1 for i in range(3) if m[i] is not None]
            h["values"] = [float(m[i]) for i in range(3) if m[i] is not None]
            if not h["dofs"]:
                raise ToolError("invalid_input", "move: give at least one direction a value.")
            note = "These faces are moved by that much (a set displacement): the answer reads the force it takes as stress."
        else:
            raise ToolError("invalid_input", "kind: fixed | slide (held along the face normal only) | pin (dofs held) | move (set displacement).")
        st["holds"] = ([] if a.get("replace") else st["holds"]) + [h]
        _save(s.doc, st)
        s.snapshot("fea_hold")
        return {"held": picked, "kind": kind, "holds": len(st["holds"]), "note": note}

    TYPICAL_ALPHA = {"pla": 68e-6, "petg": 60e-6, "abs": 90e-6, "asa": 95e-6, "nylon": 110e-6, "pa": 110e-6, "tpu": 150e-6, "pc": 68e-6,
                     "steel": 12e-6, "stainless": 16e-6, "aluminium": 23e-6, "aluminum": 23e-6, "brass": 19e-6, "copper": 17e-6, "titanium": 8.6e-6}

    def _alpha(st, ld):
        if ld.get("alpha") is not None:
            return float(ld["alpha"])
        c = matfam.card(s.doc, st["material"]) or {}
        for k in ("alpha", "cte", "ThermalExpansionCoefficient", "expansion"):
            v = c.get(k)
            if v:
                try:
                    v = float(str(v).split()[0])
                    return v * 1e-6 if v > 1e-3 else v  # µm/m·K given as a whole number
                except ValueError:
                    pass
        key = str(st["material"]).lower()
        return next((v for k, v in TYPICAL_ALPHA.items() if k in key), None)

    def _thermal(st, nodes):
        lds = [ld for ld in st["loads"] if ld["kind"] == "temperature"]
        if not lds:
            return None
        return {"alpha": _alpha(st, lds[-1]), "delta": sum(ld["delta"] for ld in lds), "nodes": list(nodes.keys()) if isinstance(nodes, dict) else list(range(1, len(nodes) + 1))}

    def fea_load(a):
        st, o = _need_study(s)
        if a.get("clear"):
            st["loads"] = []
            _save(s.doc, st)
            s.snapshot("fea_load")
            return {"loads": []}
        kinds = [k for k in ("force", "pressure", "gravity", "temperature", "spin") if k in a]
        if len(kinds) != 1:
            raise ToolError("invalid_input", "One of force (N: [fx, fy, fz] total, or a number pushing into the faces), pressure (MPa, pushing into the faces), gravity (true for 1 g down -z, a number of g, or [gx, gy, gz] in g), temperature (K the whole part warms by, negative cools; alpha 1/K) or spin { rpm, axis, at }.")
        kind = kinds[0]
        ld = {"kind": kind}
        out = {}
        if kind == "spin":
            sp = a["spin"]
            if not isinstance(sp, dict) or not isinstance(sp.get("rpm"), (int, float)):
                raise ToolError("invalid_input", "spin: { rpm, axis [x,y,z] (z), at [x,y,z] a point on the axis (origin) } — a rotor, a fan, a flywheel.")
            ax = V(*[float(c) for c in sp.get("axis", [0, 0, 1])])
            if ax.Length < 1e-9:
                raise ToolError("invalid_input", "spin.axis: a direction.")
            ax = ax.multiply(1.0 / ax.Length)
            ld["spin"] = {"rpm": float(sp["rpm"]), "axis": [ax.x, ax.y, ax.z], "at": [float(c) for c in sp.get("at", [0, 0, 0])]}
            out["note"] = "Spinning loads every bit of the part outward by its mass: hoop stress grows with rpm² — the material needs a density."
        elif kind == "temperature":
            dT = a["temperature"]
            if not isinstance(dT, (int, float)) or isinstance(dT, bool):
                raise ToolError("invalid_input", "temperature: K (°C) the whole part changes by from as it was held — a number, negative to cool.")
            ld["delta"] = float(dT)
            if a.get("alpha") is not None:
                ld["alpha"] = float(a["alpha"])
            alpha = _alpha(st, ld)
            if alpha is None:
                raise ToolError("invalid_input", f"alpha: the material's expansion, 1/K (steel 12e-6, aluminium 23e-6, PLA 68e-6) — {st['material']} carries none.")
            out["alpha"] = alpha
            out["note"] = "Held faces stop it growing: stress builds where they do — a fully held part sees about E·alpha·dT."
        elif kind in ("force", "pressure"):
            q = a.get("faces")
            picked, desc = _pick_report(o.Shape, q, "faces")
            ld["faces"] = q
            v = a[kind]
            if kind == "force":
                if isinstance(v, (int, float)):
                    n = V(0, 0, 0)
                    for _, f in picked:
                        n += facepick.outward_normal(f) * f.Area
                    if n.Length < 1e-9:
                        raise ToolError("invalid_input", "force: these faces have no one direction to push along — give [fx, fy, fz].")
                    n.normalize()
                    v = [-n.x * v, -n.y * v, -n.z * v]
                elif not (isinstance(v, list) and len(v) == 3):
                    raise ToolError("invalid_input", "force: [fx, fy, fz] N in total over the faces, or a number pushing into them.")
                ld["force"] = [float(x) for x in v]
            else:
                if not isinstance(v, (int, float)):
                    raise ToolError("invalid_input", "pressure: MPa (N/mm²), a number; positive pushes into the faces.")
                ld["pressure"] = float(v)
            out["on"] = desc
        else:
            v = a["gravity"]
            if v is True:
                g = [0.0, 0.0, -1.0]
            elif isinstance(v, (int, float)) and not isinstance(v, bool):
                g = [0.0, 0.0, -float(v)]
            elif isinstance(v, list) and len(v) == 3:
                g = [float(x) for x in v]
            else:
                raise ToolError("invalid_input", "gravity: true (1 g down -z), a number of g down -z, or [gx, gy, gz] in g.")
            ld["gravity"] = g
        st["loads"] = ([] if a.get("replace") else st["loads"]) + [ld]
        _save(s.doc, st)
        s.snapshot("fea_load")
        out.update({"load": ld, "loads": len(st["loads"])})
        return out

    def _meshed(o, st, timeout, tag="fea"):
        """(ccx binary, work folder, femmesh, mesh deck text, nodes, elements)
        for a body: Gmsh's second-order tetrahedra read into FemMesh."""
        ccx = _binary("ccx", "Ccx", "ccxBinaryPath", "AGENTLINK_CCX")
        gmsh = _binary("gmsh", "Gmsh", "gmshBinaryPath", "AGENTLINK_GMSH")
        missing = [n for n, b in (("CalculiX (ccx)", ccx), ("Gmsh", gmsh)) if not b]
        if missing:
            raise ToolError("unavailable", f"{' and '.join(missing)} not found: install them (conda install -c conda-forge calculix gmsh; or your system's packages), or set the paths in FreeCAD's FEM preferences or AGENTLINK_CCX / AGENTLINK_GMSH.")
        try:
            import Fem
        except ImportError:
            raise ToolError("unavailable", "FreeCAD's FEM module is not in this build: a FreeCAD with FEM is needed for the solve.")
        shape = o.Shape
        wd = os.path.join(s.out_dir, tag + "_" + "".join(ch if ch.isalnum() else "_" for ch in o.Label))
        os.makedirs(wd, exist_ok=True)
        for n in os.listdir(wd):
            if n.startswith("job.") or n.startswith("part."):
                os.remove(os.path.join(wd, n))

        # The mesh: Gmsh on the body as BREP, second order, nodes on the geometry.
        brep = os.path.join(wd, "part.brep")
        shape.exportBrep(brep)
        mesh = float(st["mesh"])
        # Finer near the faces fea_refine named: a distance field from them,
        # the size growing back to the study's over three element lengths.
        fields = []
        for k, rg in enumerate(st.get("refine") or []):
            ids = [int(n[4:]) for n, _ in facepick.pick(shape, rg["faces"], "faces")]
            d, th = 2 * k + 1, 2 * k + 2
            fields += [f"Field[{d}] = Distance;", f"Field[{d}].SurfacesList = {{{', '.join(map(str, ids))}}};", f"Field[{d}].Sampling = 40;",
                       f"Field[{th}] = Threshold;", f"Field[{th}].InField = {d};", f"Field[{th}].SizeMin = {float(rg['size'])};",
                       f"Field[{th}].SizeMax = {mesh};", f"Field[{th}].DistMin = 0;", f"Field[{th}].DistMax = {3 * mesh};"]
        if fields:
            n = len(st["refine"])
            fields += [f"Field[{2 * n + 1}] = Min;", f"Field[{2 * n + 1}].FieldsList = {{{', '.join(str(2 * k + 2) for k in range(n))}}};", f"Background Field = {2 * n + 1};",
                       "Mesh.MeshSizeExtendFromBoundary = 0;"]
        geo_txt = "\n".join([
            'Merge "part.brep";'] + fields + [
            f"Mesh.MeshSizeMax = {mesh};",
            "Mesh.MeshSizeMin = 0;",
            "Mesh.MeshSizeFromCurvature = 12;",
            "Mesh.MeshSizeFromPoints = 0;",
            "Mesh.ElementOrder = 2;",
            "Mesh.SecondOrderLinear = 0;",
            "Mesh.Algorithm = 2;",
            "Mesh.Algorithm3D = 1;",
            "Mesh.Optimize = 1;",
            "Mesh.HighOrderOptimize = 0;",
            "Mesh 3;",
            "Mesh.SaveAll = 1;",
            'Save "part.unv";',
            "",
        ])
        with open(os.path.join(wd, "part.geo"), "w") as f:
            f.write(geo_txt)
        try:
            r = subprocess.run([gmsh, "-", "part.geo"], cwd=wd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise ToolError("failed", f"Gmsh took over {timeout:g} s: a coarser mesh (fea_setup {{ mesh }}) or a longer timeout.")
        unv = os.path.join(wd, "part.unv")
        if r.returncode != 0 or not os.path.isfile(unv):
            tail = (r.stderr or r.stdout or "").strip().splitlines()[-3:]
            raise ToolError("failed", f"Gmsh could not mesh {o.Label}: {' | '.join(tail)} — body_check it, or try another mesh size.")
        femmesh = Fem.read(unv)
        if femmesh.VolumeCount == 0:
            raise ToolError("failed", "Gmsh made no volume elements: the body may not be a closed solid — body_check it.")
        mesh_inp = os.path.join(wd, "part.inp")
        femmesh.writeABAQUS(mesh_inp, 1, False)
        with open(mesh_inp) as f:
            mesh_text = f.read()
        nodes, elems = ccx_io.read_mesh(mesh_text)
        if not elems:
            raise ToolError("failed", "The mesh has no ten-node tetrahedra: this solve needs them.")
        return ccx, wd, femmesh, mesh_text, nodes, elems

    def _assemble(st, shape, femmesh, nodes, elems):
        """The study's holds and loads on the mesh: (fixed nodes, held face
        names, nodal loads, face pressures, gravity, what was applied)."""
        def resolve(q):
            return facepick.pick(shape, q, "faces")

        fixed = set()
        held_faces = set()
        bounds = {}
        for h in st["holds"]:
            for name, f in resolve(h["faces"]):
                held_faces.add(name)
                ns = femmesh.getNodesByFace(f)
                if h.get("kind", "fixed") == "fixed":
                    fixed.update(ns)
                else:
                    for d, v in zip(h["dofs"], h["values"]):
                        for n in ns:
                            bounds[(n, d)] = v
        bounds = [(n, d, v) for (n, d), v in bounds.items() if n not in fixed]
        if len(fixed) + len({n for n, _, _ in bounds}) < 3:
            raise ToolError("failed", "The held faces hold fewer than 3 mesh nodes: pick bigger faces or a finer mesh.")
        st["_bounds"] = bounds
        cloads, dloads, gravity = {}, [], None
        st["_centrif"] = []
        applied = []
        for ld in st["loads"]:
            if ld["kind"] == "temperature":
                applied.append({"temperature": ld["delta"]})
                continue
            if ld["kind"] == "spin":
                st["_centrif"].append(ld["spin"])
                applied.append({"spin": ld["spin"]})
                continue
            if ld["kind"] == "gravity":
                g = ld["gravity"]
                gravity = [x * G for x in g] if gravity is None else [gravity[i] + g[i] * G for i in range(3)]
                applied.append({"gravity": g})
                continue
            picked = resolve(ld["faces"])
            vf = []
            for _, f in picked:
                vf += [tuple(x) for x in femmesh.getccxVolumesByFace(f)]
            if not vf:
                raise ToolError("failed", f"No element faces found on {[n for n, _ in picked]}: try another mesh size.")
            if ld["kind"] == "pressure":
                dloads += [(e, fn, ld["pressure"]) for e, fn in vf]
                applied.append({"pressure": ld["pressure"], "faces": [n for n, _ in picked]})
            else:
                nl, area = ccx_io.nodal_loads(nodes, elems, vf, ld["force"])
                for n, fv in nl.items():
                    acc = cloads.setdefault(n, [0.0, 0.0, 0.0])
                    for k in range(3):
                        acc[k] += fv[k]
                applied.append({"force": ld["force"], "faces": [n for n, _ in picked], "area": round(area, 2)})
        return fixed, held_faces, cloads, dloads, gravity, applied

    def fea_parts(a):
        """A study of several bodies at once: their solids kept apart in one
        compound (the study's body), each with its own material, joined where
        they touch — tie (bonded: glued, welded, tight press fit) or contact
        (they push but do not pull, may slide: friction)."""
        refs = a.get("bodies")
        if not isinstance(refs, list) or len(refs) < 2:
            raise ToolError("invalid_input", "bodies: two or more parts to study together.")
        objs = [s.body(r) for r in refs]
        how = a.get("join", "tie")
        if how not in ("tie", "contact", "none"):
            raise ToolError("invalid_input", "join: tie (bonded where they touch) | contact (pushing, not pulling; friction) | none.")
        mats = a.get("materials") or {}
        parts = []
        for o in objs:
            if mats.get(o.Label):
                c = matfam.card(s.doc, mats[o.Label]) or matfam.card(s.doc, str(mats[o.Label]).lower())
                if c is None:
                    raise ToolError("not_found", f"materials.{o.Label}: no material {mats[o.Label]!r} — material_list finds one.")
            else:
                c, _ = matfam.of_body(s.doc, o.Label)
            if not c.get("E"):
                raise ToolError("invalid_state", f"{o.Label}'s material {c['name']} has no Young's modulus.")
            if len(o.Shape.Solids) != 1:
                raise ToolError("invalid_state", f"{o.Label} has {len(o.Shape.Solids)} solids: one solid a part (part_compound explode splits it).")
            parts.append({"body": o.Label, "material": c["id"]})
        comp = Part.makeCompound([o.Shape.Solids[0].copy() for o in objs])
        name = a.get("name") or "FEA parts"
        holder = next((x for x in s.doc.Objects if x.Label == name), None)
        if holder is None:
            holder = s.doc.addObject("Part::Feature", "FeaParts")
            holder.Label = s.unique(name, holder)
        holder.Shape = comp
        holder.Visibility = False
        s.doc.recompute()
        tol = float(a.get("tolerance", 0.05))
        # where they touch: faces of two parts that lie on each other
        found = []
        sol = comp.Solids
        for i in range(len(sol)):
            for j in range(i + 1, len(sol)):
                if sol[i].BoundBox.intersect(sol[j].BoundBox) or sol[i].distToShape(sol[j])[0] <= tol:
                    for fa in sol[i].Faces:
                        for fb in sol[j].Faces:
                            if not fa.BoundBox.intersect(fb.BoundBox) and fa.distToShape(fb)[0] > tol:
                                continue
                            try:
                                ar = fa.common(fb).Area
                            except Exception:
                                ar = 0.0
                            if ar > 1e-3:
                                found.append({"a": i, "b": j, "area": round(ar, 2)})
        bb = comp.BoundBox
        least = min(min(x.BoundBox.XLength, x.BoundBox.YLength, x.BoundBox.ZLength) for x in sol)
        mesh = float(a.get("mesh") or round(max(0.5, min(least / 3.0, bb.DiagonalLength / 15.0)), 2))
        st = {"body": holder.Label, "material": parts[0]["material"], "mesh": mesh, "buildDir": None, "holds": [], "loads": [],
              "parts": parts, "join": how, "friction": float(a.get("friction", 0)), "tolerance": tol}
        _save(s.doc, st)
        s.snapshot("fea_parts")
        overlap = [f"{objs[i].Label} / {objs[j].Label}" for i in range(len(sol)) for j in range(i + 1, len(sol)) if sol[i].common(sol[j]).Volume > 1e-3]
        out = {"study": holder.Label, "parts": parts, "join": how, "touching": [{"a": objs[x["a"]].Label, "b": objs[x["b"]].Label, "area": x["area"]} for x in found], "mesh": mesh,
               "next": "fea_hold and fea_load pick faces of the study's body (the parts together), then fea_solve: each part's peak and safety come back."}
        if overlap:
            out["warning"] = f"overlapping, not touching: {', '.join(overlap)} — a tie or contact needs faces that meet, not volumes that cross (asm_check, part_boolean)."
        if how != "none" and not found:
            out["warning"] = "nothing touches within tolerance: each part must be held on its own, or move them together."
        if how == "contact":
            out["note"] = "Contact is nonlinear: the solve runs in steps and takes longer. Each part still needs enough holds not to fly off before contact closes."
        return out

    def _parts_split(st, shape, femmesh, nodes, elems):
        """Elements per part (by their centre inside its solid) and the
        element faces where two parts touch."""
        sol = shape.Solids
        groups = [[] for _ in sol]
        for e, ns in elems.items():
            c = [sum(nodes[n][k] for n in ns[:4]) / 4.0 for k in range(3)]
            p = V(*c)
            hit = None
            for i, so in enumerate(sol):
                if so.BoundBox.isInside(p) and so.isInside(p, 1e-6, True):
                    hit = i
                    break
            if hit is None:
                hit = min(range(len(sol)), key=lambda i: sol[i].distToShape(Part.Vertex(p))[0])
            groups[hit].append(e)
        member = {}
        for i, g in enumerate(groups):
            for e in g:
                member[e] = i
        tol = float(st.get("tolerance", 0.05))
        pairs = []
        for i in range(len(sol)):
            for j in range(i + 1, len(sol)):
                if not sol[i].BoundBox.intersect(sol[j].BoundBox) and sol[i].distToShape(sol[j])[0] > tol:
                    continue
                sa, sb = [], []
                for fa in sol[i].Faces:
                    for fb in sol[j].Faces:
                        if not fa.BoundBox.intersect(fb.BoundBox) and fa.distToShape(fb)[0] > tol:
                            continue
                        try:
                            if fa.common(fb).Area <= 1e-3:
                                continue
                        except Exception:
                            continue
                        sa += [(e, f) for e, f in femmesh.getccxVolumesByFace(fa) if member.get(e) == i]
                        sb += [(e, f) for e, f in femmesh.getccxVolumesByFace(fb) if member.get(e) == j]
                if sa and sb:
                    pairs.append((i, j, sorted(set(sb)), sorted(set(sa))))  # slave on the second part
        return groups, pairs

    def fea_frame(a):
        """A frame of straight members by beam elements: from members given
        (from, to, section, material) or from bodies (box-like BIM members:
        their centre line and their own width and depth)."""
        from .. import frame as FR

        ccx = _binary("ccx", "Ccx", "ccxBinaryPath", "AGENTLINK_CCX")
        if not ccx:
            raise ToolError("unavailable", "CalculiX (ccx) not found: install it, or set its path in FreeCAD's FEM preferences or AGENTLINK_CCX.")
        members, cards = [], {}
        default_mat = a.get("material")

        def mat_of(mid, label=None):
            if mid:
                c = matfam.card(s.doc, mid) or matfam.card(s.doc, str(mid).lower())
                if c is None:
                    raise ToolError("not_found", f"material {mid!r}: material_list finds one.")
            elif label:
                c, _ = matfam.of_body(s.doc, label)
            else:
                raise ToolError("invalid_input", "material: an id (material_list) for the members, or on each member.")
            if not c.get("E"):
                raise ToolError("invalid_state", f"{c['name']} has no Young's modulus.")
            return c

        for k, m in enumerate(a.get("members") or []):
            sec = m.get("section") or {}
            if sec.get("kind", "rect") not in FR.SECTIONS:
                raise ToolError("invalid_input", f"members[{k}].section.kind: {', '.join(FR.SECTIONS)}.")
            c = mat_of(m.get("material") or default_mat)
            name = m.get("name") or f"M{k + 1}"
            cards[name] = c
            members.append({"name": name, "from": m["from"], "to": m["to"], "section": sec, "E": float(c["E"]), "nu": float(c.get("nu") or 0.3),
                            "rho": float(c.get("density") or 0) * 1000})
        for ref in a.get("bodies") or []:
            o = s.body(ref) if not isinstance(ref, str) or not s.doc.getObjectsByLabel(ref) else s.doc.getObjectsByLabel(ref)[0]
            sh = o.Shape
            if not sh.Solids:
                raise ToolError("invalid_state", f"{o.Label} has no solid.")
            rot = o.Placement.Rotation
            loc = sh.copy()
            loc.transformShape(o.Placement.inverse().toMatrix(), True)
            bb = loc.BoundBox
            dims = sorted([(bb.XLength, rot.multVec(V(1, 0, 0))), (bb.YLength, rot.multVec(V(0, 1, 0))), (bb.ZLength, rot.multVec(V(0, 0, 1)))], key=lambda t_: -t_[0])
            run, axis = dims[0]
            rest = sorted(dims[1:], key=lambda t_: -abs(t_[1].z))
            depth, width = rest[0][0], rest[1][0]
            if abs(axis.z) > 0.95:
                depth, width = max(depth, width), min(depth, width)
            cen = sh.BoundBox.Center
            p0, p1 = cen - axis * (run / 2), cen + axis * (run / 2)
            c = mat_of(default_mat, o.Label)
            cards[o.Label] = c
            members.append({"name": o.Label, "from": [p0.x, p0.y, p0.z], "to": [p1.x, p1.y, p1.z], "section": {"kind": "rect", "b": width, "d": depth},
                            "E": float(c["E"]), "nu": float(c.get("nu") or 0.3), "rho": float(c.get("density") or 0) * 1000})
        if not members:
            raise ToolError("invalid_input", "members [{ name, from, to, section { kind rect (b, d) | circ (d) | pipe (d, t) | box (b, d, t) }, material }] or bodies [labels].")
        loads = []
        for ld in a.get("loads") or []:
            if ld.get("member") and ld.get("kPa") is not None:
                w = -float(ld["kPa"]) * 1e-3 * float(ld.get("width", 1000))
                loads.append({"member": ld["member"], "w": [0, 0, w]})
            elif ld.get("member"):
                w = ld.get("w")
                loads.append({"member": ld["member"], "w": [0, 0, -float(w)] if isinstance(w, (int, float)) else w})
            else:
                loads.append(ld)
        tol = float(a.get("tolerance", 1.0 if not a.get("bodies") else max(max(m["section"].get("b", 0), m["section"].get("d", 0)) for m in members)))
        g = [0, 0, -G] if a.get("gravity") else None
        wd = os.path.join(s.out_dir, "frame_" + "".join(ch if ch.isalnum() else "_" for ch in (a.get("name") or "study")))
        try:
            text, _ = FR.build(members, a.get("supports") or [], loads, g, int(a.get("elements", 8)), tol)
        except (ValueError, KeyError) as e:
            raise ToolError("invalid_input", str(e))
        if not a.get("supports"):
            raise ToolError("invalid_input", "supports [{ at [x,y,z] a member's end, kind pinned | fixed | roller }]: a frame with none flies off.")
        try:
            res = FR.solve(ccx, wd, members, a.get("supports"), loads, g, int(a.get("elements", 8)), float(a.get("timeout", 300)))
        except (ValueError, KeyError) as e:
            raise ToolError("invalid_input", str(e))
        except RuntimeError as e:
            raise ToolError("failed", str(e))
        limit = float(a.get("deflectionLimit", 0) or 0)
        for row in res["members"]:
            c = cards.get(row["member"])
            y = c.get("yield") if c else None
            if y and row.get("vonMisesMPa"):
                row["safety"] = round(y / row["vonMisesMPa"], 2) if row["vonMisesMPa"] > 0 else None
            if limit and row.get("spanOverDeflection"):
                row["deflectionOk"] = row["spanOverDeflection"] >= limit
        worst = min((r for r in res["members"] if r.get("safety")), key=lambda r: r["safety"], default=None)
        out = {**res, "members": res["members"], "elements": "B32 beams, expanded to solids by CalculiX", "worst": worst["member"] if worst else None,
               "note": "Joints between members are rigid; pinned and roller supports are held about the member's own axis. A frame model is a sizing look, not the connection design."}
        total = sum(abs(x) for sp in res["supports"] for x in sp.get("reactionN", [])[2:3])
        if total:
            out["reactionTotalZN"] = round(total, 1)
        return out

    def fea_solve(a):
        st, o = _need_study(s)
        if not st["holds"]:
            raise ToolError("invalid_state", "Nothing holds the part: fea_hold the faces it is mounted by — a free body has no static answer.")
        if not st["loads"]:
            raise ToolError("invalid_state", "Nothing loads the part: fea_load a force, pressure or gravity.")
        c = matfam.card(s.doc, st["material"])
        if c is None or not c.get("E"):
            raise ToolError("invalid_state", f"The study's material {st['material']!r} is gone or has no Young's modulus: fea_setup {{ material }} again.")
        shape = o.Shape
        t0 = time.time()
        timeout = float(a.get("timeout", 300))
        ccx, wd, femmesh, mesh_text, nodes, elems = _meshed(o, st, timeout)
        mesh = float(st["mesh"])
        faces = shape.Faces

        fixed, held_faces, cloads, dloads, gravity, applied = _assemble(st, shape, femmesh, nodes, elems)
        mat = {"E": float(c["E"]), "nu": float(c.get("nu") or 0.35), "rho": float(c["density"])}
        text = ccx_io.deck(mesh_text, mat, fixed, cloads, dloads, gravity, thermal=_thermal(st, nodes), bounds=st.pop("_bounds", None), centrif=st.pop("_centrif", None))
        groups = pairs = None
        part_cards = []
        if st.get("parts"):
            groups, pairs = _parts_split(st, shape, femmesh, nodes, elems)
            defs = []
            for k, pinfo in enumerate(st["parts"]):
                pc = matfam.card(s.doc, pinfo["material"]) or c
                part_cards.append(pc)
                defs.append((f"P{k + 1}", groups[k], {"E": float(pc["E"]), "nu": float(pc.get("nu") or 0.35), "rho": float(pc.get("density") or c["density"]),
                                                     **({"alpha": _thermal(st, nodes)["alpha"]} if _thermal(st, nodes) else {})}))
            empty = [st["parts"][k]["body"] for k, g in enumerate(groups) if not g]
            if empty:
                raise ToolError("failed", f"No elements landed in {', '.join(empty)}: a finer mesh (fea_setup is not used here — fea_parts {{ mesh }}).")
            join = [(f"J{n + 1}", sl, ma) for n, (i, j, sl, ma) in enumerate(pairs)]
            Emin = min(float(pc["E"]) for pc in part_cards)
            text = ccx_io.parts_deck(text, defs, ties=join if st.get("join") == "tie" else (), contacts=join if st.get("join") == "contact" else (),
                                     contact_slope=Emin * 50, friction=st.get("friction", 0))
        with open(os.path.join(wd, "job.inp"), "w") as f:
            f.write(text)
        env = dict(os.environ)
        env.setdefault("OMP_NUM_THREADS", str(max(1, min(4, os.cpu_count() or 1))))
        try:
            r = subprocess.run([ccx, "-i", "job"], cwd=wd, capture_output=True, text=True, timeout=timeout, env=env)
        except subprocess.TimeoutExpired:
            raise ToolError("failed", f"CalculiX took over {timeout:g} s: a coarser mesh or a longer timeout.")
        frd = os.path.join(wd, "job.frd")
        log = (r.stdout or "") + (r.stderr or "")
        if "*ERROR" in log or not os.path.isfile(frd):
            err = [l.strip() for l in log.splitlines() if "ERROR" in l][:2] or log.strip().splitlines()[-2:]
            hint = " The part may be free to move: hold more faces." if any("singular" in l.lower() or "zero pivot" in l.lower() for l in log.splitlines()) else ""
            raise ToolError("failed", f"CalculiX refused the job: {' | '.join(err)}.{hint}")
        with open(frd) as f:
            disp, stress = ccx_io.read_frd(f.read())
        if not disp or not stress:
            raise ToolError("failed", "CalculiX wrote no displacements or stresses: see job.frd in " + wd)

        vm = {n: ccx_io.von_mises(sv) for n, sv in stress.items()}
        n_max = max(vm, key=vm.get)
        dmag = {n: math.sqrt(sum(x * x for x in u[:3])) for n, u in disp.items()}
        n_dmax = max(dmag, key=dmag.get)

        def where(n):
            p = nodes.get(n, (0, 0, 0))
            vx = Part.Vertex(V(*p))
            best = min(((vx.distToShape(f)[0], i) for i, f in enumerate(faces)), default=(0, 0))
            return {"at": [round(p[0], 2), round(p[1], 2), round(p[2], 2)], "face": f"Face{best[1] + 1}"}

        smax = vm[n_max]
        y = c.get("yield")
        out = {
            "body": o.Label,
            "material": {"id": c["id"], "name": c["name"], "E": c["E"], "yield": y},
            "mesh": {"size": mesh, "nodes": len(nodes), "elements": len(elems), "type": "C3D10"},
            "loads": applied,
            "held": sorted(held_faces),
            "vonMisesMax": {"MPa": round(smax, 3), **where(n_max)},
            "displacementMax": {"mm": round(dmag[n_dmax], 4), **where(n_dmax)},
        }
        warnings = []
        if y:
            out["safety"] = round(y / smax, 2) if smax > 0 else None
            if smax > y:
                warnings.append(f"Past yield ({smax:.1f} over {y:g} MPa): a linear solve is not right past yield — the part yields or breaks there.")
        else:
            warnings.append("The card has no yield strength: no safety factor — material_set custom with the supplier's yieldMPa.")
        if st.get("buildDir"):
            bd = st["buildDir"]
            sn = {n: ccx_io.normal_stress(sv, bd) for n, sv in stress.items()}
            n_l = max(sn, key=sn.get)
            out["acrossLayers"] = {"tensileMPa": round(max(0.0, sn[n_l]), 3), **where(n_l), "buildDir": bd, "factor": LAYER_FACTOR}
            if y:
                out["acrossLayers"]["safety"] = round(LAYER_FACTOR * y / sn[n_l], 2) if sn[n_l] > 0 else None
            out["acrossLayers"]["note"] = f"Printed layers are weaker across than along: the tension along the build direction is judged against {LAYER_FACTOR}× yield. A simplification — the real factor depends on printer, material and settings."
        if n_max in fixed:
            warnings.append("The peak is on a held face: a rigid hold makes stress no real mount does — read it as high, and look a little away from the hold.")
        bb = shape.BoundBox
        least = min(bb.XLength, bb.YLength, bb.ZLength)
        if mesh > least / 4:
            warnings.append(f"Coarse mesh ({mesh:g} mm against a least size of {least:.1f} mm): peaks are under-read — fea_setup {{ mesh: {round(mesh / 2, 2)} }} and solve again; if the peak rises by more than 10% keep refining.")
        if dmag[n_dmax] > 0.05 * max(bb.XLength, bb.YLength, bb.ZLength):
            warnings.append("The displacement is large for the part: a linear solve is rough here.")
        if not str(c["id"]).startswith("custom:"):
            warnings.append(matfam.TYPICAL)
        if groups is not None:
            rows = []
            for k, pinfo in enumerate(st["parts"]):
                ns = {n for e in groups[k] for n in elems[e]}
                pv = {n: vm[n] for n in ns if n in vm}
                if not pv:
                    continue
                nm = max(pv, key=pv.get)
                yk = part_cards[k].get("yield")
                rows.append({"body": pinfo["body"], "material": part_cards[k]["name"], "vonMisesMaxMPa": round(pv[nm], 3), **where(nm),
                             **({"safety": round(yk / pv[nm], 2)} if yk and pv[nm] > 0 else {})})
            out["parts"] = rows
            out["joined"] = {"how": st.get("join"), "interfaces": len(pairs)}
            if st.get("join") != "none" and not pairs:
                warnings.append("No touching faces were found in the mesh: the parts were solved apart.")
            if y and len(rows) > 1:
                out.pop("safety", None)
        out["warnings"] = warnings
        out["files"] = wd
        out["seconds"] = round(time.time() - t0, 1)
        # Every node's stress and displacement, for a map of the part
        # (view_render map stress): kept with the session, not the file.
        ids = [n for n in vm if n in nodes]
        field = {"points": [nodes[n] for n in ids], "vonMises": [vm[n] for n in ids], "displacement": [list(disp.get(n, (0, 0, 0))[:3]) for n in ids]}
        at = {n: i for i, n in enumerate(ids)}
        field["elements"] = [[at[n] for n in e] for e in elems.values() if all(n in at for n in e)]
        s._agentlink_fea = {"body": o.Label, "stats": geo.stats(shape), "study": st, "result": out, "field": field}
        return out

    def fea_results(a):
        r = _results(s)
        if not r:
            raise ToolError("not_found", "No results in this session: fea_solve first.")
        out = dict(r["result"])
        stale = []
        try:
            o = s.body(r["body"])
            if geo.stats(o.Shape) != r["stats"]:
                stale.append("the body changed since the solve")
        except ToolError:
            stale.append("the body is gone")
        if _study(s.doc) != r["study"]:
            stale.append("the study (holds, loads, material, mesh) changed")
        out["stale"] = bool(stale)
        if stale:
            out["staleWhy"] = stale
            out["next"] = "fea_solve again."
        return out

    def fea_clear(a):
        had = _study(s.doc) is not None
        m = dict(s.doc.Meta or {})
        m.pop(store.KEY + KEY, None)
        s.doc.Meta = m
        s._agentlink_fea = None
        s.snapshot("fea_clear")
        return {"cleared": had}


    def _where(nodes, shape, n):
        p = nodes.get(n, (0, 0, 0))
        vx = Part.Vertex(V(*p))
        best = min(((vx.distToShape(f)[0], i) for i, f in enumerate(shape.Faces)), default=(0, 0))
        return {"at": [round(p[0], 2), round(p[1], 2), round(p[2], 2)], "face": f"Face{best[1] + 1}"}

    def _run(ccx, wd, timeout, what):
        env = dict(os.environ)
        env.setdefault("OMP_NUM_THREADS", str(max(1, min(4, os.cpu_count() or 1))))
        try:
            r = subprocess.run([ccx, "-i", "job"], cwd=wd, capture_output=True, text=True, timeout=timeout, env=env)
        except subprocess.TimeoutExpired:
            raise ToolError("failed", f"CalculiX took over {timeout:g} s: a coarser mesh or a longer timeout.")
        log = (r.stdout or "") + (r.stderr or "")
        if "*ERROR" in log or not os.path.isfile(os.path.join(wd, "job.frd")):
            err = [l.strip() for l in log.splitlines() if "ERROR" in l][:2] or log.strip().splitlines()[-2:]
            raise ToolError("failed", f"CalculiX refused the {what}: {' | '.join(err)}.")
        with open(os.path.join(wd, "job.frd")) as f:
            frd = f.read()
        dat = ""
        if os.path.isfile(os.path.join(wd, "job.dat")):
            with open(os.path.join(wd, "job.dat")) as f:
                dat = f.read()
        return frd, dat

    def fea_modes(a):
        st, o = _need_study(s)
        c = matfam.card(s.doc, st["material"])
        if c is None or not c.get("E") or not c.get("density"):
            raise ToolError("invalid_state", f"The study's material {st['material']!r} needs a Young's modulus and a density: fea_setup {{ material }} again.")
        count = int(a.get("count", 6))
        if not 1 <= count <= 30:
            raise ToolError("invalid_input", "count: modes to find, 1 to 30.")
        shape = o.Shape
        t0 = time.time()
        timeout = float(a.get("timeout", 300))
        ccx, wd, femmesh, mesh_text, nodes, elems = _meshed(o, st, timeout, "modes")
        fixed = set()
        mbounds = set()
        for h in st["holds"]:
            for _, f in facepick.pick(shape, h["faces"], "faces"):
                if h.get("kind", "fixed") == "fixed":
                    fixed.update(femmesh.getNodesByFace(f))
                else:
                    mbounds.update((n, d, 0.0) for n in femmesh.getNodesByFace(f) for d in h["dofs"])
        mbounds = [b for b in mbounds if b[0] not in fixed]
        free = not fixed and not mbounds
        # A free part's first six modes are its rigid motions, near 0 Hz.
        ask = count + (6 if free else 0)
        mat = {"E": float(c["E"]), "nu": float(c.get("nu") or 0.35), "rho": float(c["density"])}
        with open(os.path.join(wd, "job.inp"), "w") as f:
            f.write(ccx_io.deck_modes(mesh_text, mat, fixed, ask, bounds=mbounds))
        frd, dat = _run(ccx, wd, timeout, "frequency step")
        freqs = ccx_io.read_frequencies(dat)
        shapes = ccx_io.read_frd_blocks(frd, "DISP")
        if not freqs:
            raise ToolError("failed", "CalculiX wrote no frequencies: see job.dat in " + wd)
        modes = []
        for k, (no, hz) in enumerate(freqs):
            if free and k < 6:
                continue
            m = {"mode": len(modes) + 1, "hz": round(hz, 2)}
            if k < len(shapes) and shapes[k]:
                mag = {n: math.sqrt(sum(x * x for x in u[:3])) for n, u in shapes[k].items() if n in nodes}
                if mag:
                    n_max = max(mag, key=mag.get)
                    m["moves"] = _where(nodes, shape, n_max)
                    # Which way that place swings, as a unit direction.
                    u = shapes[k][n_max][:3]
                    L = math.sqrt(sum(x * x for x in u)) or 1.0
                    m["moves"]["along"] = [round(x / L, 3) for x in u]
            modes.append(m)
            if len(modes) >= count:
                break
        out = {"body": o.Label, "material": {"id": c["id"], "name": c["name"], "E": c["E"], "density": c["density"]},
               "held": "free (no holds: the six rigid motions left out)" if free else f"{len(st['holds'])} hold(s) from the study",
               "mesh": {"size": st["mesh"], "nodes": len(nodes), "elements": len(elems)}, "modes": modes,
               "note": "Natural frequencies: keep what shakes the part (a motor, a fan, a vehicle) well away from the lowest, by a factor of two or more. moves is where each mode swings most, and which way.",
               "warnings": ([] if free else ["Rigid holds make a part stiffer than a real mount: real frequencies read lower."]) + ([] if str(c["id"]).startswith("custom:") else [matfam.TYPICAL]),
               "files": wd, "seconds": round(time.time() - t0, 1)}
        return out

    def fea_heat(a):
        st, o = _need_study(s)
        c = matfam.card(s.doc, st["material"])
        k = a.get("conductivity") or (c or {}).get("k")
        if not k:
            raise ToolError("invalid_state", f"{(c or {}).get('name', st['material'])} has no thermal conductivity on its card: conductivity W/(m·K) here, or material_set custom with one.")
        k = float(k)
        temps_in = a.get("temperatures") or []
        heat_in = a.get("heat") or []
        if not temps_in and not heat_in:
            raise ToolError("invalid_input", "temperatures [{ faces, celsius }] held, and/or heat [{ faces, watts }] going in: something must drive the heat.")
        air = a.get("air", {"h": 10, "celsius": 25})
        if air is True:
            air = {"h": 10, "celsius": 25}
        shape = o.Shape
        t0 = time.time()
        timeout = float(a.get("timeout", 300))
        ccx, wd, femmesh, mesh_text, nodes, elems = _meshed(o, st, timeout, "heat")
        used, temps, fluxes, films, said = set(), {}, [], [], []
        for i, t in enumerate(temps_in):
            if not isinstance(t, dict) or "celsius" not in t:
                raise ToolError("invalid_input", f"temperatures.{i}: {{ faces, celsius }}.")
            picked = facepick.pick(shape, t.get("faces"), f"temperatures.{i}.faces")
            for name, f in picked:
                used.add(name)
                for n in femmesh.getNodesByFace(f):
                    temps[n] = float(t["celsius"])
            said.append({"held": [n for n, _ in picked], "celsius": float(t["celsius"])})
        for i, h in enumerate(heat_in):
            if not isinstance(h, dict) or "watts" not in h:
                raise ToolError("invalid_input", f"heat.{i}: {{ faces, watts }} going in.")
            picked = facepick.pick(shape, h.get("faces"), f"heat.{i}.faces")
            vf = []
            for name, f in picked:
                used.add(name)
                vf += [tuple(x) for x in femmesh.getccxVolumesByFace(f)]
            area = sum(f.Area for _, f in picked)
            if not vf or area <= 0:
                raise ToolError("failed", f"heat.{i}: no element faces on {[n for n, _ in picked]} — another mesh size.")
            q = float(h["watts"]) * 1000.0 / area  # mW/mm²
            fluxes += [(e, fn, q) for e, fn in vf]
            said.append({"heated": [n for n, _ in picked], "watts": float(h["watts"]), "mWperMm2": round(q, 5)})
        if air:
            hc = float(air.get("h", 10)) * 1e-3  # W/(m²·K) is 1e-3 mW/(mm²·K)
            ta = float(air.get("celsius", 25))
            on = [(n, f) for n, f in ((f"Face{i + 1}", f) for i, f in enumerate(shape.Faces)) if n not in used]
            if air.get("faces") is not None:
                on = facepick.pick(shape, air["faces"], "air.faces")
            for _, f in on:
                films += [(e, fn, hc, ta) for e, fn in (tuple(x) for x in femmesh.getccxVolumesByFace(f))]
            said.append({"air": {"h": float(air.get("h", 10)), "celsius": ta, "faces": len(on)}})
        if not temps and not films:
            raise ToolError("invalid_input", "Heat goes in but nothing takes it away: hold a face at a temperature or leave air on.")
        with open(os.path.join(wd, "job.inp"), "w") as f:
            f.write(ccx_io.deck_heat(mesh_text, k, temps, fluxes, films, start=float((air or {}).get("celsius", 20)) if air else 20.0))
        frd, _ = _run(ccx, wd, timeout, "heat step")
        T = {n: t for n, t in ccx_io.read_temperatures(frd).items() if n in nodes}
        if not T:
            raise ToolError("failed", "CalculiX wrote no temperatures: see job.frd in " + wd)
        hot, cold = max(T, key=T.get), min(T, key=T.get)
        out = {"body": o.Label, "material": {"id": (c or {}).get("id"), "name": (c or {}).get("name"), "conductivity": k, "softensC": (c or {}).get("softens")},
               "boundary": said, "hottest": {"celsius": round(T[hot], 2), **_where(nodes, shape, hot)}, "coolest": {"celsius": round(T[cold], 2), **_where(nodes, shape, cold)},
               "mesh": {"size": st["mesh"], "nodes": len(nodes), "elements": len(elems)}, "files": wd, "seconds": round(time.time() - t0, 1)}
        warnings = []
        soft = (c or {}).get("softens")
        if soft and T[hot] >= soft:
            warnings.append(f"Hotter than {c['name']} takes ({T[hot]:.0f} °C against about {soft} °C where it softens under load): it will sag or creep there — a higher-temperature material, or more cooling.")
        elif soft and T[hot] >= soft - 10:
            warnings.append(f"Within 10 °C of where {c['name']} softens ({soft} °C): little margin.")
        warnings.append("Steady state, conduction and still-air convection only (no radiation); film coefficients are rough — 5 to 25 W/(m²·K) for still air, more with a fan.")
        out["warnings"] = warnings
        # The field for view_render map heat.
        ids = list(T)
        s._agentlink_heat = {"body": o.Label, "stats": geo.stats(shape), "field": {"points": [nodes[n] for n in ids], "celsius": [T[n] for n in ids]}}
        return out

    query = {"type": "object", "description": "Faces by query: { near: [x,y,z], radius }, { normal: [nx,ny,nz], x|y|z: plane, largest }, or { faces: [\"Face3\"] }"}
    # ---- buckling, local refinement, export, FreeCAD's own FEM objects ----

    def fea_buckle(a):
        st, o = _need_study(s)
        if not st["holds"] or not st["loads"]:
            raise ToolError("invalid_state", "Buckling needs the study's holds and loads (the load whose multiple buckles it): fea_hold and fea_load first.")
        c = matfam.card(s.doc, st["material"])
        if c is None or not c.get("E"):
            raise ToolError("invalid_state", "The study's material has no Young's modulus: fea_setup { material }.")
        count = int(a.get("count", 3))
        if not 1 <= count <= 10:
            raise ToolError("invalid_input", "count: modes, 1 … 10.")
        timeout = float(a.get("timeout", 300))
        shape = o.Shape
        t0 = time.time()
        ccx, wd, femmesh, mesh_text, nodes, elems = _meshed(o, st, timeout, "buckle")
        fixed, held_faces, cloads, dloads, gravity, applied = _assemble(st, shape, femmesh, nodes, elems)
        mat = {"E": float(c["E"]), "nu": float(c.get("nu") or 0.35), "rho": float(c["density"])}
        with open(os.path.join(wd, "job.inp"), "w") as f:
            f.write(ccx_io.deck(mesh_text, mat, fixed, cloads, dloads, gravity, buckle=count, thermal=_thermal(st, nodes), bounds=st.pop("_bounds", None), centrif=st.pop("_centrif", None)))
        frd, dat = _run(ccx, wd, timeout, "buckling step")
        factors = ccx_io.read_buckling(dat)
        if not factors:
            raise ToolError("failed", "CalculiX wrote no buckling factors: see job.dat in " + wd)
        shapes = ccx_io.read_frd_blocks(frd, "DISP")
        modes = []
        for k, (no, fac) in enumerate(factors):
            m = {"mode": no, "factor": round(fac, 4)}
            if k < len(shapes) and shapes[k]:
                mag = {n: math.sqrt(sum(x * x for x in u[:3])) for n, u in shapes[k].items() if n in nodes}
                if mag:
                    m["bulges"] = _where(nodes, shape, max(mag, key=mag.get))
            modes.append(m)
        low = modes[0]["factor"]
        out = {"body": o.Label, "loads": applied, "modes": modes, "lowest": low, "seconds": round(time.time() - t0, 1), "files": wd}
        if low < 0:
            out["note"] = "A negative factor: the part buckles with the loads reversed — reverse them and read again."
        elif low < 1:
            out["verdict"] = f"Buckles under these loads: at {low:.2f}× them."
        else:
            out["verdict"] = f"Buckles at {low:.2f}× these loads" + (" — under 3×: little margin; thin walls and columns want more." if low < 3 else ".")
        out["note"] = (out.get("note", "") + " Linear (eigenvalue) buckling: an upper bound — real parts, imperfect and printed, go sooner; keep a margin of 3 or more.").strip()
        return out

    def fea_refine(a):
        st, o = _need_study(s)
        if a.get("clear"):
            st["refine"] = []
        else:
            size = a.get("size")
            if not isinstance(size, (int, float)) or size <= 0:
                raise ToolError("invalid_input", "size: the element edge near the faces, mm, over 0 (smaller than the study's mesh).")
            if size >= float(st["mesh"]):
                raise ToolError("invalid_input", f"size {size:g} is not finer than the study's mesh ({st['mesh']:g} mm).")
            _, picked = _pick_report(o.Shape, a.get("faces"), "faces")
            st["refine"] = list(st.get("refine") or []) + [{"faces": a["faces"], "size": float(size)}]
            out_p = picked
        _save(s.doc, st)
        s.snapshot("fea_refine")
        return {"refine": st.get("refine", []), **({"faces": out_p} if not a.get("clear") else {}), "note": "Finer near those faces, growing back to the study's size over three elements: where a peak sits (a fillet, a hole's edge) — solve again and see the peak settle."}

    def fea_export(a):
        r = _results(s)
        if not r or "field" not in r:
            raise ToolError("not_found", "No solve to write: fea_solve first.")
        f = r["field"]
        base = os.path.join(s.out_dir, (a.get("name") or f"{r['body']}-stress").replace(" ", "-"))
        kinds = a.get("formats") or ["vtu", "csv"]
        files = {}
        if "vtu" in kinds:
            if not f.get("elements"):
                raise ToolError("invalid_state", "This solve kept no elements: fea_solve again, then export.")
            with open(base + ".vtu", "w") as fh:
                fh.write(ccx_io.vtu(f["points"], f["elements"], {"von_Mises_MPa": f["vonMises"], "displacement_mm": f["displacement"]}))
            files["vtu"] = base + ".vtu"
        if "csv" in kinds:
            with open(base + ".csv", "w") as fh:
                fh.write("x,y,z,von_mises_MPa,ux,uy,uz\n")
                for p, vm, u in zip(f["points"], f["vonMises"], f["displacement"]):
                    fh.write(f"{p[0]:.5f},{p[1]:.5f},{p[2]:.5f},{vm:.5f},{u[0]:.6g},{u[1]:.6g},{u[2]:.6g}\n")
            files["csv"] = base + ".csv"
        return {"body": r["body"], "files": files, "nodes": len(f["points"]), "elements": len(f.get("elements") or []),
                "note": "The VTU opens in ParaView (and FreeCAD's FEM post-processing); the CSV has every node."}

    def fea_probe(a):
        """Values of the last solve where asked: points, along a line, or over faces."""
        import numpy as np

        r = _results(s)
        if not r or "field" not in r:
            raise ToolError("not_found", "No solve to read: fea_solve first.")
        f = r["field"]
        P = np.asarray(f["points"], float)
        VM = np.asarray(f["vonMises"], float)
        U = np.asarray(f["displacement"], float)
        um = np.linalg.norm(U, axis=1)
        m_ = r.get("mesh")
        msize = float((m_.get("size") if isinstance(m_, dict) else m_) or 1.0)

        def at(q):
            d = np.linalg.norm(P - np.asarray(q, float), axis=1)
            k = np.argsort(d)[:4]
            w = 1.0 / np.maximum(d[k], 1e-9)
            if d[k[0]] < 1e-6:
                w = (d[k] < 1e-6).astype(float)
            w = w / w.sum()
            return {"at": [round(float(c), 3) for c in q], "vonMisesMPa": round(float(VM[k] @ w), 3),
                    "displacementMm": round(float(um[k] @ w), 5), "nearestNodeMm": round(float(d[k[0]]), 3)}

        out = {"body": r["body"]}
        if a.get("points"):
            out["points"] = [at(q) for q in a["points"]]
        if a.get("line"):
            ln = a["line"]
            p0, p1 = np.asarray(ln.get("from"), float), np.asarray(ln.get("to"), float)
            n = int(ln.get("samples", 11))
            if not 2 <= n <= 500:
                raise ToolError("invalid_input", "line.samples: 2 … 500.")
            rows = [at(p0 + (p1 - p0) * i / (n - 1)) for i in range(n)]
            far = max(x["nearestNodeMm"] for x in rows)
            out["line"] = rows
            if far > msize * 2 and far > 2:
                out["warning"] = f"Some samples lie {far:.1f} mm from any node: the line leaves the part there."
        if a.get("faces") is not None:
            o = s.doc.getObject(r.get("bodyName", "")) if r.get("bodyName") else None
            if o is None:
                o = next((x for x in s.doc.Objects if x.Label == r["body"]), None)
            if o is None:
                raise ToolError("not_found", f"{r['body']}: the body solved is no longer here.")
            picked = facepick.pick(o.Shape, a["faces"], "faces")
            faces = [fc for _, fc in picked]
            tol = max(0.05, 0.02 * msize)
            on = np.zeros(len(P), bool)
            for fc in faces:
                bb = fc.BoundBox
                cand = np.where((P[:, 0] >= bb.XMin - tol) & (P[:, 0] <= bb.XMax + tol) & (P[:, 1] >= bb.YMin - tol) & (P[:, 1] <= bb.YMax + tol)
                                & (P[:, 2] >= bb.ZMin - tol) & (P[:, 2] <= bb.ZMax + tol))[0]
                for i in cand:
                    if not on[i] and fc.distToShape(Part.Vertex(FreeCAD.Vector(*P[i])))[0] <= tol:
                        on[i] = True
            if not on.any():
                raise ToolError("failed", "No mesh nodes found on those faces.")
            i_max = int(np.argmax(np.where(on, VM, -1)))
            out["faces"] = {"faces": [n_ for n_, _ in picked], "nodes": int(on.sum()),
                            "vonMisesMPa": {"max": round(float(VM[on].max()), 3), "mean": round(float(VM[on].mean()), 3), "maxAt": [round(float(c), 3) for c in P[i_max]]},
                            "displacementMm": {"max": round(float(um[on].max()), 5), "mean": round(float(um[on].mean()), 5)}}
        if len(out) == 1:
            raise ToolError("invalid_input", "points [[x,y,z] …], line { from, to, samples }, and/or faces (a query as fea_hold takes).")
        if r.get("stale"):
            out["stale"] = True
        return out

    def fea_native(a):
        st, o = _need_study(s)
        try:
            import ObjectsFem
        except ImportError:
            raise ToolError("unavailable", "FreeCAD's FEM module is not in this build.")
        doc = s.doc
        c = matfam.card(doc, st["material"]) or {}
        an = ObjectsFem.makeAnalysis(doc, "Analysis")
        an.Label = s.unique(a.get("name") or f"{o.Label} analysis")
        made = []
        names = ("makeSolverCalculiX", "makeSolverCalculix", "makeSolverCalculixCcxTools", "makeSolverCcxTools")
        mk_solver = next((getattr(ObjectsFem, n) for n in names if hasattr(ObjectsFem, n)), None)
        if mk_solver is None:
            raise ToolError("unavailable", "This FreeCAD's FEM has no CalculiX solver object to add: " + ", ".join(n for n in dir(ObjectsFem) if n.startswith("makeSolver")))
        solver = mk_solver(doc)
        an.addObject(solver)
        made.append(solver.Label)
        mo = ObjectsFem.makeMaterialSolid(doc, "Material")
        props = {"Name": c.get("name", st["material"])}
        if c.get("E"):
            props["YoungsModulus"] = f"{float(c['E'])} MPa"
        if c.get("nu"):
            props["PoissonRatio"] = str(float(c["nu"]))
        if c.get("density"):
            props["Density"] = f"{float(c['density'])} kg/m^3"
        mo.Material = props
        mo.References = [(o, "")]
        an.addObject(mo)
        made.append(mo.Label)
        mesh = ObjectsFem.makeMeshGmsh(doc, "Mesh")
        mesh.Shape = o
        mesh.CharacteristicLengthMax = f"{float(st['mesh'])} mm"
        try:
            mesh.ElementOrder = "2nd"
        except Exception:
            pass
        an.addObject(mesh)
        made.append(mesh.Label)
        shape = o.Shape
        skipped = []
        for h in st["holds"]:
            if h.get("kind", "fixed") != "fixed":
                skipped.append(f"a {h['kind']} hold (add it as a displacement constraint in the FEM workbench)")
                continue
            fx = ObjectsFem.makeConstraintFixed(doc, "Fixed")
            fx.References = [(o, tuple(n for n, _ in facepick.pick(shape, h["faces"], "faces")))]
            an.addObject(fx)
            made.append(fx.Label)
        for ld in st["loads"]:
            if ld["kind"] == "pressure":
                pr = ObjectsFem.makeConstraintPressure(doc, "Pressure")
                pr.References = [(o, tuple(n for n, _ in facepick.pick(shape, ld["faces"], "faces")))]
                pr.Pressure = f"{float(ld['pressure'])} MPa"
                an.addObject(pr)
                made.append(pr.Label)
            elif ld["kind"] == "temperature":
                try:
                    it = ObjectsFem.makeConstraintInitialTemperature(doc, "InitialTemperature")
                    it.initialTemperature = "293.15 K"
                    an.addObject(it)
                    tc = ObjectsFem.makeConstraintTemperature(doc, "Temperature")
                    tc.References = [(o, tuple(f"Face{i + 1}" for i in range(len(shape.Faces))))]
                    tc.Temperature = f"{293.15 + float(ld['delta'])} K"
                    an.addObject(tc)
                    made += [it.Label, tc.Label]
                    skipped.append("the material's expansion coefficient and a thermomechanical solver setting (set them in the FEM workbench)")
                except Exception:
                    skipped.append("the temperature change (add it in the FEM workbench)")
            elif ld["kind"] == "spin":
                skipped.append("the spin (FreeCAD 1.0's centrifugal constraint is Elmer's; CalculiX takes it as *DLOAD CENTRIF)")
            elif ld["kind"] == "gravity":
                g = ObjectsFem.makeConstraintSelfWeight(doc, "SelfWeight")
                g.Gravity_x, g.Gravity_y, g.Gravity_z = ld["gravity"]
                an.addObject(g)
                made.append(g.Label)
            else:
                # a force's direction is a line in the model: one drawn for it, hidden
                fv = V(*ld["force"])
                ln = doc.addObject("Part::Line", "ForceDirection")
                ln.X2, ln.Y2, ln.Z2 = fv.normalize().x, fv.y, fv.z
                ln.Visibility = False
                fo = ObjectsFem.makeConstraintForce(doc, "Force")
                fo.References = [(o, tuple(n for n, _ in facepick.pick(shape, ld["faces"], "faces")))]
                fo.Force = f"{V(*ld['force']).Length} N"
                try:
                    fo.Direction = (ln, ["Edge1"])
                except Exception:
                    skipped.append("a force's direction (set it in the GUI)")
                an.addObject(fo)
                made.append(fo.Label)
        doc.recompute()
        s.snapshot("fea_native")
        out = {"analysis": an.Label, "objects": made, "next": "In FreeCAD's FEM workbench: mesh it (double-click the mesh), then run the solver — the same study fea_solve runs headless."}
        if skipped:
            out["skipped"] = skipped
        return out

    return [
        Tool("fea_frame", "A frame of straight members by beam elements (CalculiX B32): members [{ name, from, to, section { kind rect (b, d) | circ (d) | pipe (d, t) | box (b, d, t) }, material }] or bodies [box-like BIM members: their centre lines and sizes]; supports [{ at a member's end, kind pinned | fixed | roller }]; loads [{ member, w N/mm down (or [wx,wy,wz]) | kPa with width mm }, { at a joint, force [fx,fy,fz] N }], gravity (own weight). Answers each member's stress along it, von Mises and safety, its deflection (span over deflection; deflectionLimit 360 judges it) and each support's reaction. Beams, rafters, portal frames, trusses, shelving.",
             obj({"members": {"type": "array", "items": {"type": "object"}}, "bodies": {"type": "array", "items": {"type": "string"}}, "material": {"type": "string"}, "supports": {"type": "array", "items": {"type": "object"}},
                  "loads": {"type": "array", "items": {"type": "object"}}, "gravity": {"type": "boolean"}, "deflectionLimit": {"type": "number"}, "tolerance": {"type": "number"},
                  "elements": {"type": "integer"}, "name": {"type": "string"}, "timeout": {"type": "number"}}), fea_frame),
        Tool("fea_parts", "A strength study of several parts together (instead of fea_setup): bodies [labels, one solid each], materials { label: id } (else each body's own), join tie (bonded where they touch: glued, welded, press-fit) | contact (they push but do not pull; friction 0.2 lets them slide) | none, tolerance mm for touching (0.05), mesh mm. Then fea_hold / fea_load on the parts' faces and fea_solve: each part's peak stress and safety.",
             obj({"bodies": {"type": "array", "items": {"type": "string"}}, "materials": {"type": "object"}, "join": {"type": "string", "enum": ["tie", "contact", "none"]},
                  "friction": {"type": "number"}, "tolerance": {"type": "number"}, "mesh": {"type": "number"}, "name": {"type": "string"}}, ["bodies"]), fea_parts, changes=True),
        Tool(
            "fea_setup",
            "Start (or change) a strength study of one body: material (the body's by default), mesh (largest element edge, mm; a third of the least size by default), buildDir for a printed part (+z from its print setup). reset: true drops the holds and loads. " + TRAPS,
            obj({"body": {"type": "string"}, "material": {"type": "string"}, "mesh": {"type": "number"}, "buildDir": {}, "reset": {"type": "boolean"}}),
            fea_setup,
            changes=True,
        ),
        Tool(
            "fea_hold",
            "Hold faces: faces by query — { near: [x,y,z] }, { normal: [0,0,-1], z: 0 }, { faces: [\"Face1\"] }; kind fixed (no movement at all, the default) | slide (held only along the face's normal, x/y/z: a roller, a smooth floor, a symmetry plane) | pin (dofs [\"x\",\"y\",\"z\"] held, the rest free) | move (move [dx,dy,dz] mm, null for free: a set displacement, a press fit). Adds to the holds (replace: true starts again; clear: true drops them). Answers the faces picked, with area and centre. A rigid hold makes stress no real mount does: peaks at a hold read high.",
            obj({"faces": query, "kind": {"type": "string", "enum": ["fixed", "slide", "pin", "move"]}, "dofs": {"type": "array", "items": {"type": "string"}}, "move": {"type": "array", "items": {}}, "replace": {"type": "boolean"}, "clear": {"type": "boolean"}}),
            fea_hold,
            changes=True,
        ),
        Tool(
            "fea_load",
            "Load the part: force on faces (N; [fx, fy, fz] in total spread by area, or a number pushing into the faces), pressure on faces (MPa, positive pushes in), gravity (true is 1 g down -z; a number of g; [gx, gy, gz] in g), or temperature (K the whole part warms by, against its holds; alpha 1/K, else the material's), or spin { rpm, axis [x,y,z], at [x,y,z] } (a rotor's own mass flung outward). Adds to the loads (replace: true starts again; clear: true drops them).",
            obj({"faces": query, "force": {}, "pressure": {"type": "number"}, "gravity": {}, "temperature": {"type": "number"}, "alpha": {"type": "number"}, "spin": {"type": "object"}, "replace": {"type": "boolean"}, "clear": {"type": "boolean"}}),
            fea_load,
            changes=True,
        ),
        Tool(
            "fea_solve",
            "Solve the study: Gmsh meshes the body (second-order tetrahedra), CalculiX solves it linear static. Answers the largest von Mises stress (MPa) and where, the largest displacement (mm) and where, the safety factor against yield, and for a printed part the tension across the layers against half the yield (a simplification), with warnings. " + TRAPS + " Needs ccx and gmsh installed (timeout s, 300).",
            obj({"timeout": {"type": "number"}}),
            fea_solve,
        ),
        Tool(
            "fea_modes",
            "Natural frequencies of the study's body: the lowest count (6) modes, held by the study's holds (none: free, its rigid motions left out). Answers each mode's Hz and where it swings most and which way. Keep what shakes the part well away from the lowest. Needs ccx and gmsh (timeout s, 300).",
            obj({"count": {"type": "integer"}, "timeout": {"type": "number"}}),
            fea_modes,
        ),
        Tool(
            "fea_heat",
            "Steady heat through the study's body: temperatures [{ faces, celsius }] held, heat [{ faces, watts }] going in, air { h W/(m²·K) (10, still air), celsius (25), faces (the rest) } taking it away — air: false for none. Answers the hottest and coolest place and warns when the part passes where its material softens (k from the card, or conductivity here). Conduction and convection only. view_render map heat shows it.",
            obj({"temperatures": {"type": "array", "items": {}}, "heat": {"type": "array", "items": {}}, "air": {}, "conductivity": {"type": "number"}, "timeout": {"type": "number"}}),
            fea_heat,
        ),
        Tool("fea_results", "The last solve's answer again, with stale: true when the body or the study changed since.", obj({}), fea_results),
        Tool("fea_clear", "Drop the strength study and its results.", obj({}), fea_clear, changes=True),
        Tool("fea_buckle", "Linear buckling of the study (its holds and loads): the lowest count (3) buckling factors — the loads times the factor is where each mode buckles — and where each bulges. Thin walls, columns, ribs under compression. Needs ccx and gmsh.",
             obj({"count": {"type": "integer"}, "timeout": {"type": "number"}}), fea_buckle),
        Tool("fea_refine", "A finer mesh near faces (a fillet, a hole's edge, where the peak sits): faces (a query as fea_hold takes), size mm; clear: true drops every region. Kept in the study; the next solve uses it.",
             obj({"faces": {}, "size": {"type": "number"}, "clear": {"type": "boolean"}}), fea_refine, changes=True),
        Tool("fea_probe", "Read the last solve where asked: points [[x,y,z] …] (von Mises and displacement there), line { from, to, samples } (a profile along it), faces (a query as fea_hold takes: max, mean and where on them). Changes nothing.",
             obj({"points": {"type": "array", "items": {}}, "line": {"type": "object"}, "faces": {}}), fea_probe),
        Tool("fea_export", "Write the last solve out: formats [vtu (ParaView, FreeCAD FEM post-processing), csv (every node: position, von Mises, displacement)], name.",
             obj({"formats": {"type": "array", "items": {"type": "string"}}, "name": {"type": "string"}}), fea_export),
        Tool("fea_native", "Build the study as FreeCAD's own FEM analysis (Analysis, CalculiX solver, material, Gmsh mesh, fixed / force / pressure / self-weight constraints), to open and run in the FEM workbench.",
             obj({"name": {"type": "string"}}), fea_native, changes=True),
    ]
