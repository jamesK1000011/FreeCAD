# SPDX-License-Identifier: LGPL-2.1-or-later
# Mass, centre of mass and inertia of each body in its material — as
# printed when it is in a print setup (walls and infill), else solid — with
# the parts encased in it (magnets from gen_make, ballast cut here) counted;
# how far a body is from a target; and ballast pockets that bring it there.

import math
import re

import FreeCAD
import Part

from .. import geometry as geo
from .. import printmass, store
from ..protocol import Tool, ToolError
from ..tools import LINE, NDFEB, obj
from . import material as matfam

GROUP = "mass"
INFO = "Mass, centre of mass and inertia (as printed or solid, encased parts counted), distance from a target, ballast pockets to reach it."

V = FreeCAD.Vector
# Fill densities, g/cm³, of the metal itself; a pocket of shot holds packing × that.
FILLS = {"steel-bb": 7.85, "lead": 11.3, "tungsten": 18.0}
PACKING = 0.6  # loose round shot settles near 0.6 of the pocket


def _r(v, n=3):
    return round(float(v), n) + 0.0


def _vl(v):
    return [_r(v.x), _r(v.y), _r(v.z)]


def _zero3():
    return [[0.0] * 3 for _ in range(3)]


def _mat_of(m):
    return [[m.A11, m.A12, m.A13], [m.A21, m.A22, m.A23], [m.A31, m.A32, m.A33]]


def _shift(I, m, d):
    """Inertia about a point d away from the centre of mass (parallel axes)."""
    dd = d.x * d.x + d.y * d.y + d.z * d.z
    r = [d.x, d.y, d.z]
    return [[I[i][j] + m * ((dd if i == j else 0.0) - r[i] * r[j]) for j in range(3)] for i in range(3)]


def _add(A, B):
    return [[A[i][j] + B[i][j] for j in range(3)] for i in range(3)]


def _scale(A, k):
    return [[A[i][j] * k for j in range(3)] for i in range(3)]


def _combine(parts):
    """parts: [(mass g, com Vector, inertia about own com g·mm²)] →
    (mass, com, inertia about the whole's com)."""
    m = sum(p[0] for p in parts)
    if m <= 0:
        return 0.0, V(0, 0, 0), _zero3()
    c = V(0, 0, 0)
    for pm, pc, _ in parts:
        c += pc * (pm / m)
    I = _zero3()
    for pm, pc, pI in parts:
        I = _add(I, _shift(pI, pm, pc - c))
    return m, c, I


def _solid_parts(shape):
    """(volume mm³, com, inertia per unit density mm⁵) of the solid."""
    items = []
    for so in shape.Solids or [shape]:
        items.append((so.Volume, so.CenterOfMass, _mat_of(so.MatrixOfInertia)))
    return _combine(items)


def _surface_parts(shape):
    """(area mm², centroid, inertia per unit area mm⁴) of the skin."""
    items = []
    for f in shape.Faces:
        try:
            items.append((f.Area, f.CenterOfMass, _mat_of(f.MatrixOfInertia)))
        except Exception:
            items.append((f.Area, f.CenterOfMass, _zero3()))
    return _combine(items)


def body_mass(shape, rho, setup):
    """The body alone: (mass g, com, inertia g·mm², how). rho g/cm³."""
    rho_mm = rho / 1000.0
    vol, c_v, I_v = _solid_parts(shape)
    if not setup:
        return vol * rho_mm, c_v, _scale(I_v, rho_mm), "solid", vol
    walls = int(setup.get("walls", 2))
    infill = float(setup.get("infill", 15)) / 100.0
    # The same estimate as part_review: the solid share as a slicer lays it
    # (perimeters, top and bottom skins), the rest at the infill's share. The shells' mass sits on the surface.
    area, c_a, I_a = _surface_parts(shape)
    share, _ = printmass.solid_of(shape, setup, walls)
    if share is not None:
        shell = vol * share
    else:
        shell = min(vol, area * walls * LINE)
    core = vol - shell
    m_shell = shell * rho_mm
    m_core = core * infill * rho_mm
    k_core = m_core / vol if vol > 0 else 0.0
    parts = [(m_shell, c_a, _scale(I_a, m_shell / area if area else 0.0)), (m_core, c_v, _scale(I_v, k_core))]
    m, c, I = _combine(parts)
    return m, c, I, f"printed: {walls} walls, {setup.get('topLayers', 5)} top and {setup.get('bottomLayers', 4)} bottom layers, {infill * 100:.0f}% infill (an estimate; the shells' mass on the surface)", vol


def _disc_size(what):
    m = re.search(r"(\d+(?:\.\d+)?)\s*x\s*(\d+(?:\.\d+)?)", what or "")
    return (float(m.group(1)), float(m.group(2))) if m else (6.0, 2.0)


def encased_parts(meta):
    """[(mass, com, inertia, label)] for what is held inside: magnets, ballast."""
    out = []
    mags = (meta or {}).get("encased")
    if mags:
        d, t = _disc_size(mags.get("what"))
        each = math.pi * (d / 2) ** 2 * t * NDFEB / 1000.0
        Ic = [[each * (3 * (d / 2) ** 2 + t * t) / 12, 0, 0], [0, each * (3 * (d / 2) ** 2 + t * t) / 12, 0], [0, 0, each * (d / 2) ** 2 / 2]]
        for p in mags.get("centres") or []:
            out.append((each, V(*p), Ic, mags.get("what", "magnets")))
    bal = (meta or {}).get("ballast")
    if bal:
        for p in bal["pockets"]:
            out.append((p["fill"], V(*p["centroid"]), _zero3(), "ballast"))
    return out


CALIBRATION = "massCalibration"


def calibration_key(setup, material):
    """What a calibration is for: the print setup (its printer, settings and
    spool), else solid in this material."""
    return f"setup:{setup['id']}" if setup else f"solid:{material}"


def properties(session, o, rho=None, shape=None, meta=None, calibrated=True):
    doc = session.doc
    c, set_ = matfam.of_body(doc, o.Label)
    if rho is not None:
        c = dict(c, density=rho)
    setup = store.setup_of(doc, o.Label, "print")
    m_b, c_b, I_b, how, vol = body_mass(shape or o.Shape, c["density"], setup)
    cal = store.read(doc, CALIBRATION, {}).get(calibration_key(setup, c.get("id"))) if calibrated else None
    if cal and cal.get("scale"):
        # A weighed print of this setup: every reading scaled to match it.
        k = float(cal["scale"])
        m_b, I_b = m_b * k, _scale(I_b, k)
        how += f"; calibrated ×{k:.3f} to a weighed print of {cal.get('body')} ({cal.get('grams')} g)"
    enc = encased_parts(session.meta.get(o.Label) if meta is None else meta)
    m, com, I = _combine([(m_b, c_b, I_b)] + [(e[0], e[1], e[2]) for e in enc])
    return {
        "mass": m, "com": com, "I": I, "bodyMass": m_b, "volume": vol, "as": how,
        "material": c, "materialSet": set_, "encased": enc, "printed": bool(setup),
    }


def _principal(I):
    try:
        import numpy as np

        w = np.linalg.eigvalsh(np.array(I))
        return [round(float(x), 1) for x in sorted(w)]
    except Exception:
        return None


def report(o, p):
    out = {
        "body": o.Label,
        "mass": _r(p["mass"], 2),
        "com": _vl(p["com"]),
        "volume": _r(p["volume"], 1),
        "material": p["material"]["id"] + ("" if p["materialSet"] else " (assumed: material_set records it)"),
        "density": p["material"]["density"],
        "as": p["as"],
        "inertia": {"aboutCom": [[_r(x, 1) for x in row] for row in p["I"]], "units": "g·mm²"},
    }
    pr = _principal(p["I"])
    if pr:
        out["inertia"]["principal"] = pr
    if p["encased"]:
        groups = {}
        for e in p["encased"]:
            g = groups.setdefault(e[3], [0, 0.0])
            g[0] += 1
            g[1] += e[0]
        out["encased"] = [{"what": k, "count": v[0], "mass": _r(v[1], 2)} for k, v in groups.items()]
        out["bodyMass"] = _r(p["bodyMass"], 2)
    return out


def _target_com(a, now):
    t = a.get("com")
    if t is None:
        return None, None
    if not (isinstance(t, list) and len(t) == 3):
        raise ToolError("invalid_input", "com: [x, y, z] mm; null for an axis that does not matter, e.g. [null, null, 10].")
    mask = [x is not None for x in t]
    if not any(mask):
        return None, None
    return V(*[float(t[i]) if mask[i] else (now.x, now.y, now.z)[i] for i in range(3)]), mask


def _pocket_runs(shape, x, y, r_out, zlo, zhi, dz):
    """Heights where a disc of radius r_out at (x, y) is wholly in material,
    as runs [(z_start, z_end)]."""
    pts = [(x, y)] + [(x + r_out * math.cos(k * math.pi / 4), y + r_out * math.sin(k * math.pi / 4)) for k in range(8)]
    runs, start, prev = [], None, None
    n = int((zhi - zlo) / dz)
    for i in range(1, n):
        z = zlo + i * dz
        ok = all(shape.isInside(V(px, py, z), 0.001, False) for px, py in pts)
        if ok and start is None:
            start = z
        if not ok and start is not None:
            runs.append((start, prev))
            start = None
        prev = z
    if start is not None:
        runs.append((start, prev))
    return runs


def tools(session):
    s = session

    def mass_properties(a):
        bodies = [s.body(a["body"])] if a.get("body") else s.bodies()
        if not bodies:
            raise ToolError("not_found", "No body in the document: gen_make makes one.")
        out = [report(o, properties(s, o)) for o in bodies]
        res = {"bodies": out, "units": "mm, g, g·mm²"}
        if any("assumed" in b["material"] for b in out):
            res["note"] = "A body with no material is taken as PLA: material_set says what it is."
        if any(b["as"].startswith("printed") for b in out):
            res["printedNote"] = "As printed is an estimate from walls and infill; a slicer's figure (slice) is closer."
        return res

    def mass_target(a):
        o = s.body(a.get("body"))
        p = properties(s, o)
        M, C = p["mass"], p["com"]
        Ct, mask = _target_com(a, C)
        Mt = a.get("mass")
        if Mt is None and Ct is None:
            raise ToolError("invalid_input", "mass (g) and/or com ([x, y, z] mm, null for an axis that does not matter): the target.")
        out = {"body": o.Label, "now": {"mass": _r(M, 2), "com": _vl(C)}}
        if Mt is not None:
            out["mass"] = {"target": Mt, "off": _r(M - Mt, 2), "offPct": _r((M - Mt) / Mt * 100, 1)}
        if Ct is not None:
            d = C - Ct
            out["com"] = {"target": [_r(v) if mask[i] else None for i, v in enumerate((Ct.x, Ct.y, Ct.z))], "off": [_r(v) if mask[i] else None for i, v in enumerate((d.x, d.y, d.z))], "distance": _r(math.sqrt(sum(((d.x, d.y, d.z)[i]) ** 2 for i in range(3) if mask[i])), 2)}
        tol_m = float(a.get("massTol", 0.5))
        tol_c = float(a.get("comTol", 0.5))
        ok_m = Mt is None or abs(M - Mt) <= tol_m
        ok_c = Ct is None or out["com"]["distance"] <= tol_c
        out["met"] = ok_m and ok_c
        # What would get there: a ballast of mass m with its centre at q.
        if not out["met"]:
            if Mt is not None and Mt > M + 1e-9:
                m_add = Mt - M
                if Ct is not None:
                    q = Ct + (Ct - C) * (M / m_add)
                    inside = o.Shape.isInside(q, 0.01, True)
                    out["ballast"] = {"mass": _r(m_add, 2), "centre": _vl(q), "insideBody": inside, "note": "One ballast of that mass with its centre there meets both" + ("" if inside else "; the centre is outside the body — the targets cannot both be met by adding mass") + ". mass_ballast lays out pockets."}
                else:
                    out["ballast"] = {"mass": _r(m_add, 2), "note": "mass_ballast lays out pockets for it."}
            elif Mt is not None and Mt < M - 1e-9:
                out["remove"] = {"mass": _r(M - Mt, 2), "note": "Heavier than the target: take material away (thinner walls, less infill, pockets left empty) — ballast only adds."}
            elif Ct is not None:
                mom = (Ct - C) * M
                out["moment"] = {"needed": _vl(mom), "units": "g·mm", "note": "A ballast of m grams moves the centre by this ÷ (mass + m) toward where it sits; mass_ballast { com } finds the pockets."}
        return out

    def mass_ballast(a):
        o = s.body(a.get("body"))
        shape = o.Shape
        p0 = properties(s, o)
        M0, C0 = p0["mass"], p0["com"]
        Ct, mask = _target_com(a, C0)
        Mt = a.get("mass")
        if Mt is None and Ct is None:
            raise ToolError("invalid_input", "mass (g) and/or com ([x, y, z], null for an axis that does not matter): what the ballast should reach.")
        if Mt is not None and Mt <= M0:
            raise ToolError("invalid_state", f"{o.Label} weighs {M0:.1f} g already, over the target {Mt} g: ballast only adds — take material away instead.")
        fill = a.get("fill", "steel-bb")
        if isinstance(fill, (int, float)):
            rho_fill = float(fill)
            fill_name = f"{rho_fill:g} g/cm³"
        elif fill in FILLS:
            rho_fill, fill_name = FILLS[fill], fill
        else:
            raise ToolError("invalid_input", f"fill: {', '.join(FILLS)}, or a density in g/cm³.")
        packing = float(a.get("packing", PACKING))
        if not 0.1 <= packing <= 1.0:
            raise ToolError("invalid_input", "packing: the share of a pocket the fill takes, 0.1 to 1 (0.6 for loose shot, 1 for a cast slug).")
        d = float(a.get("diameter", 8.0))
        spec = store.spec(s.doc)
        wall = float(a.get("minWall", spec.get("minWall", 1.2)))
        if d < 2 or wall < 0.4:
            raise ToolError("invalid_input", "diameter at least 2 mm, minWall at least 0.4 mm.")
        max_n = int(a.get("maxPockets", 12))
        r = d / 2.0
        setup = store.setup_of(s.doc, o.Label, "print")
        rho = p0["material"]["density"] / 1000.0
        removed_k = rho * (float(setup.get("infill", 15)) / 100.0 if setup else 1.0)
        net_k = rho_fill / 1000.0 * packing - removed_k  # g per mm³ of pocket
        if net_k <= 0:
            raise ToolError("invalid_input", "That fill is no denser than the part as made: pick a heavier fill.")

        # Candidates: upright pockets on a grid, each as tall as the material allows.
        bb = shape.BoundBox
        step = d + wall
        xs = [bb.XMin + r + wall + i * step for i in range(int(max(0.0, bb.XLength - 2 * (r + wall)) / step) + 1)]
        ys = [bb.YMin + r + wall + j * step for j in range(int(max(0.0, bb.YLength - 2 * (r + wall)) / step) + 1)]
        if len(xs) * len(ys) > 600:
            raise ToolError("invalid_input", f"{len(xs) * len(ys)} places to try: a larger diameter tries fewer.")
        dz = max(0.25, min(1.0, bb.ZLength / 60))
        cands = []
        for x in xs:
            for y in ys:
                for z0, z1 in _pocket_runs(shape, x, y, r + wall, bb.ZMin, bb.ZMax, dz):
                    lo, hi = z0 + wall, z1 - wall
                    if hi - lo >= min(2.0, r):
                        cands.append({"x": x, "y": y, "lo": lo, "hi": hi})
        if not cands:
            raise ToolError("invalid_state", f"No room for a {d:g} mm pocket with {wall:g} mm of wall anywhere in {o.Label}: a smaller diameter or thinner wall, or thicken the part.")

        L = max(bb.DiagonalLength, 1.0)

        def cost(m, mom):
            c = mom * (1.0 / m)
            j = 0.0
            if Mt is not None:
                j += ((m - Mt) / Mt) ** 2 * 100
            if Ct is not None:
                dv = c - Ct
                j += sum(((dv.x, dv.y, dv.z)[i] / L) ** 2 for i in range(3) if mask[i]) * 100
            else:
                # No centre asked for: keep it where it was, as near as the pockets allow.
                dv = c - C0
                j += (dv.x ** 2 + dv.y ** 2 + dv.z ** 2) / (L * L) * 10
            return j

        def pocket(cd, h):
            v = math.pi * r * r * h
            return {"x": cd["x"], "y": cd["y"], "lo": cd["lo"], "h": h, "v": v, "dm": v * net_k, "c": V(cd["x"], cd["y"], cd["lo"] + h / 2)}

        M, mom = M0, C0 * M0
        chosen, used = [], set()
        J = cost(M, mom)
        while len(chosen) < max_n:
            best = None
            for i, cd in enumerate(cands):
                if i in used:
                    continue
                full = cd["hi"] - cd["lo"]
                opts = [full]
                if Mt is not None:
                    need = Mt - M
                    h = need / (math.pi * r * r * net_k)
                    if 0.5 <= h < full:
                        opts.append(h)
                for h in opts:
                    pk = pocket(cd, h)
                    j = cost(M + pk["dm"], mom + pk["c"] * pk["dm"])
                    if best is None or j < best[0]:
                        best = (j, i, pk)
            if best is None or best[0] >= J - 1e-9:
                break
            J, i, pk = best
            used.add(i)
            chosen.append(pk)
            M += pk["dm"]
            mom += pk["c"] * pk["dm"]
            if Mt is not None and M >= Mt - 0.05:
                break
        if not chosen:
            raise ToolError("invalid_state", "No pocket brings it nearer the target: the room left in the part is on the wrong side — say a different com or allow a thinner wall.")

        # The exact answer: cut them on a copy and weigh it.
        cyls = [Part.makeCylinder(r, pk["h"], V(pk["x"], pk["y"], pk["lo"])) for pk in chosen]
        cut = geo.clean(shape.cut(Part.makeCompound(cyls)))
        rho_eff = rho_fill * packing / 1000.0
        pockets = [{"at": [_r(pk["x"]), _r(pk["y"]), _r(pk["lo"])], "diameter": d, "depth": _r(pk["h"], 2), "fill": _r(pk["v"] * rho_eff, 2), "centroid": _vl(pk["c"])} for pk in chosen]
        meta = dict(s.meta.get(o.Label) or {})
        meta["ballast"] = {"what": fill_name, "pockets": pockets}
        p1 = properties(s, o, shape=cut, meta=meta)
        out = {
            "body": o.Label,
            "fill": {"what": fill_name, "density": rho_fill, "packing": packing, "pocketDensity": _r(rho_fill * packing, 2)},
            "pockets": [{k: v for k, v in pk.items() if k != "centroid"} for pk in pockets],
            "before": {"mass": _r(M0, 2), "com": _vl(C0)},
            "after": {"mass": _r(p1["mass"], 2), "com": _vl(p1["com"])},
            "fillMass": _r(sum(pk["fill"] for pk in pockets), 2),
            "minWall": wall,
        }
        if Mt is not None:
            out["massOff"] = _r(p1["mass"] - Mt, 2)
        if Ct is not None:
            dv = p1["com"] - Ct
            out["comOff"] = _r(math.sqrt(sum(((dv.x, dv.y, dv.z)[i]) ** 2 for i in range(3) if mask[i])), 2)
        top = max(pk["lo"] + pk["h"] for pk in chosen)
        out["printing"] = {"pauseAt": _r(top, 2), "note": "Closed pockets: pause at the layer that reaches pauseAt, fill them, and print over. Weigh the fill for each pocket."}
        if a.get("cut"):
            o.Shape = cut
            s.doc.recompute()
            s.meta.setdefault(o.Label, {})["ballast"] = meta["ballast"]
            s.snapshot("mass_ballast")
            sound = geo.soundness(cut)
            out.update({"cut": True, "sound": sound["ok"], "step": len(s.steps)})
        else:
            out["cut"] = False
            out["next"] = "cut: true cuts these pockets (one undo step)."
        return out

    def mass_calibrate(a):
        o = s.body(a.get("body"))
        c, _ = matfam.of_body(s.doc, o.Label)
        setup = store.setup_of(s.doc, o.Label, "print")
        key = calibration_key(setup, c.get("id"))
        cals = store.read(s.doc, CALIBRATION, {})
        if a.get("reset"):
            gone = cals.pop(key, None)
            store.write(s.doc, CALIBRATION, cals)
            return {"reset": key, "was": gone, "note": "Readings go back to the material's own figures."}
        g = a.get("grams")
        if not isinstance(g, (int, float)) or g <= 0:
            raise ToolError("invalid_input", "grams: what the print weighed on the scales (with anything encased in it).")
        p = properties(s, o, calibrated=False)
        enc = sum(e[0] for e in p["encased"])
        body_g = g - enc
        if body_g <= 0:
            raise ToolError("invalid_input", f"{g} g is less than what is encased in it ({enc:.2f} g): weigh it with them in.")
        k = body_g / p["bodyMass"] if p["bodyMass"] > 0 else 0
        if not 0.3 <= k <= 3.0:
            raise ToolError("invalid_input", f"The print weighed {g} g against {p['mass']:.2f} g expected (×{k:.2f}): too far to be the same part and settings — check the body, its setup and its material.")
        cals[key] = {"scale": round(k, 4), "grams": g, "body": o.Label, "expected": round(p["mass"], 2), "encased": round(enc, 2)}
        store.write(s.doc, CALIBRATION, cals)
        return {"calibrated": key, "scale": round(k, 4), "expected": round(p["mass"], 2), "weighed": g, "encased": round(enc, 2),
                "for": f"print setup {setup['name']} ({setup['id']})" if setup else f"solid {c.get('id')}",
                "note": "Every mass reading of bodies in this setup (mass_properties, part_review, ballast) now matches the scales; reset: true undoes it."}

    tgt = {"type": "array", "items": {"type": ["number", "null"]}, "description": "[x, y, z] mm; null for an axis that does not matter"}
    return [
        Tool(
            "mass_calibrate",
            "Calibrate mass to a weighed print: grams (what it weighed, anything encased in it included) for a body; every reading for its print setup (or, unprinted, its material) is then scaled to match — the printer, spool and settings as they really are. Kept with the document; reset: true goes back to the material's figures.",
            obj({"body": {"type": "string"}, "grams": {"type": "number"}, "reset": {"type": "boolean"}}),
            mass_calibrate,
            changes=True,
        ),
        Tool(
            "mass_properties",
            "Each body's mass (g) in its material — as printed (walls and infill, an estimate) when it is in a print setup, else solid — its centre of mass (mm) and inertia about it (g·mm², principal moments too), with encased parts counted: gen_make's magnets and ballast cut by mass_ballast. A body with no material is taken as PLA and says so.",
            obj({"body": {"type": "string"}}),
            mass_properties,
        ),
        Tool(
            "mass_target",
            "How far a body is from a target mass (g) and/or centre of mass (com [x, y, z], null for an axis that does not matter): the differences, whether it is met (massTol g, comTol mm, 0.5 each), and what would meet it — the ballast's mass and where its centre must be, or the mass to take away.",
            obj({"body": {"type": "string"}, "mass": {"type": "number"}, "com": tgt, "massTol": {"type": "number"}, "comTol": {"type": "number"}}),
            mass_target,
        ),
        Tool(
            "mass_ballast",
            "Lay out closed upright ballast pockets inside a body, minWall (the spec's, else 1.2 mm) clear of every surface, to reach a target mass and/or move the centre of mass (com, null for a free axis): fill steel-bb, lead, tungsten or a density g/cm³, packing (0.6 loose shot), diameter mm (8), maxPockets (12). Answers each pocket, the fill's mass, and the mass and centre before and after (as made: printed or solid). cut: true cuts them (one undo step) and counts the fill in mass_properties; the print needs a pause to fill them.",
            obj({"body": {"type": "string"}, "mass": {"type": "number"}, "com": tgt, "fill": {}, "packing": {"type": "number"}, "diameter": {"type": "number"}, "minWall": {"type": "number"}, "maxPockets": {"type": "integer"}, "cut": {"type": "boolean"}}),
            mass_ballast,
            changes=True,
        ),
    ]
