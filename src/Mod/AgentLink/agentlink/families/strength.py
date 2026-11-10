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
        _, picked = _pick_report(o.Shape, q, "faces")
        st["holds"] = ([] if a.get("replace") else st["holds"]) + [{"faces": q, "kind": "fixed"}]
        _save(s.doc, st)
        s.snapshot("fea_hold")
        return {"held": picked, "holds": len(st["holds"]), "note": "Held rigidly: no movement at all on these faces. Real bolts and clamps give a little, so stress right at a hold reads high."}

    def fea_load(a):
        st, o = _need_study(s)
        if a.get("clear"):
            st["loads"] = []
            _save(s.doc, st)
            s.snapshot("fea_load")
            return {"loads": []}
        kinds = [k for k in ("force", "pressure", "gravity") if k in a]
        if len(kinds) != 1:
            raise ToolError("invalid_input", "One of force (N: [fx, fy, fz] total, or a number pushing into the faces), pressure (MPa, pushing into the faces) or gravity (true for 1 g down -z, a number of g, or [gx, gy, gz] in g).")
        kind = kinds[0]
        ld = {"kind": kind}
        out = {}
        if kind in ("force", "pressure"):
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
        geo_txt = "\n".join([
            'Merge "part.brep";',
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

        def resolve(q):
            return facepick.pick(shape, q, "faces")

        fixed = set()
        held_faces = set()
        for h in st["holds"]:
            for name, f in resolve(h["faces"]):
                held_faces.add(name)
                fixed.update(femmesh.getNodesByFace(f))
        if len(fixed) < 3:
            raise ToolError("failed", "The held faces hold fewer than 3 mesh nodes: pick bigger faces or a finer mesh.")
        cloads, dloads, gravity = {}, [], None
        applied = []
        for ld in st["loads"]:
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
        mat = {"E": float(c["E"]), "nu": float(c.get("nu") or 0.35), "rho": float(c["density"])}
        with open(os.path.join(wd, "job.inp"), "w") as f:
            f.write(ccx_io.deck(mesh_text, mat, fixed, cloads, dloads, gravity))
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
        out["warnings"] = warnings
        out["files"] = wd
        out["seconds"] = round(time.time() - t0, 1)
        # Every node's stress and displacement, for a map of the part
        # (view_render map stress): kept with the session, not the file.
        ids = [n for n in vm if n in nodes]
        field = {"points": [nodes[n] for n in ids], "vonMises": [vm[n] for n in ids], "displacement": [list(disp.get(n, (0, 0, 0))[:3]) for n in ids]}
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
        for h in st["holds"]:
            for _, f in facepick.pick(shape, h["faces"], "faces"):
                fixed.update(femmesh.getNodesByFace(f))
        free = not fixed
        # A free part's first six modes are its rigid motions, near 0 Hz.
        ask = count + (6 if free else 0)
        mat = {"E": float(c["E"]), "nu": float(c.get("nu") or 0.35), "rho": float(c["density"])}
        with open(os.path.join(wd, "job.inp"), "w") as f:
            f.write(ccx_io.deck_modes(mesh_text, mat, fixed, ask))
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
    return [
        Tool(
            "fea_setup",
            "Start (or change) a strength study of one body: material (the body's by default), mesh (largest element edge, mm; a third of the least size by default), buildDir for a printed part (+z from its print setup). reset: true drops the holds and loads. " + TRAPS,
            obj({"body": {"type": "string"}, "material": {"type": "string"}, "mesh": {"type": "number"}, "buildDir": {}, "reset": {"type": "boolean"}}),
            fea_setup,
            changes=True,
        ),
        Tool(
            "fea_hold",
            "Hold faces fixed (no movement at all): faces by query — { near: [x,y,z] }, { normal: [0,0,-1], z: 0 }, { faces: [\"Face1\"] }. Adds to the holds (replace: true starts again; clear: true drops them). Answers the faces picked, with area and centre. A rigid hold makes stress no real mount does: peaks at a hold read high.",
            obj({"faces": query, "replace": {"type": "boolean"}, "clear": {"type": "boolean"}}),
            fea_hold,
            changes=True,
        ),
        Tool(
            "fea_load",
            "Load the part: force on faces (N; [fx, fy, fz] in total spread by area, or a number pushing into the faces), pressure on faces (MPa, positive pushes in), or gravity (true is 1 g down -z; a number of g; [gx, gy, gz] in g). Adds to the loads (replace: true starts again; clear: true drops them).",
            obj({"faces": query, "force": {}, "pressure": {"type": "number"}, "gravity": {}, "replace": {"type": "boolean"}, "clear": {"type": "boolean"}}),
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
            obj({"temperatures": {"type": "array"}, "heat": {"type": "array"}, "air": {}, "conductivity": {"type": "number"}, "timeout": {"type": "number"}}),
            fea_heat,
        ),
        Tool("fea_results", "The last solve's answer again, with stale: true when the body or the study changed since.", obj({}), fea_results),
        Tool("fea_clear", "Drop the strength study and its results.", obj({}), fea_clear, changes=True),
    ]
