# SPDX-License-Identifier: LGPL-2.1-or-later
# More of the Assembly workbench, beside assembly.py's joints and checks:
# fits — the hole and the shaft a joint puts together, found by their axes,
# measured and held against an ISO 286 fit; poses and joint limits — a
# joint moved and left there, inside the limits FreeCAD keeps on it; an
# exploded layout that collapses again (kept in the document); and FreeCAD's
# own bill of materials.

import json
import math

import FreeCAD
import Part

from .. import iso286
from .. import store
from ..protocol import Tool, ToolError
from ..tools import DENSITY, obj
from . import assembly as A

GROUP = "assembly"
INFO = ("Assemblies (FreeCAD's Assembly workbench): parts put together, one grounded, joints solved; overlaps, sweeps, "
        "fits (hole and shaft against H7/g6 …), poses and joint limits, exploded layouts, and the bill of materials.")

V = FreeCAD.Vector
EXPLODED = "AgentLinkExploded"
POSE = "AgentLinkPose"


def bad(msg):
    return ToolError("invalid_input", msg)


def r4(v):
    return round(float(v), 4) + 0.0


def vec(v):
    return [r4(v.x), r4(v.y), r4(v.z)]


# ---- elements and axes ----


def ref_element(doc, r):
    """A joint reference's part and element, either form: [asm,
    ["Part.Face6", …]] (1.0) or [part, ["Face6", …]]."""
    if not r:
        return None, None, None
    o, subs = r[0], (r[1] if len(r) > 1 else [])
    sub = subs[0] if subs else ""
    path = sub.split(".")
    part = o
    if o.TypeId == "Assembly::AssemblyObject" and len(path) > 1:
        part = doc.getObject(path[0])
    name = path[-1]
    try:
        return part, part.Shape.getElement(name), name
    except Exception:
        return part, None, name


def axis_of(el):
    """(point, direction) of a round element: a cylindrical face, a circle
    edge; for a flat face its middle and normal."""
    if el is None:
        return None
    if isinstance(el, Part.Face):
        srf = el.Surface
        if hasattr(srf, "Axis") and hasattr(srf, "Center") and hasattr(srf, "Radius"):
            return V(srf.Center), V(srf.Axis)
        if isinstance(srf, Part.Plane):
            u0, u1, v0, v1 = el.ParameterRange
            return el.CenterOfMass, el.normalAt((u0 + u1) / 2, (v0 + v1) / 2)
    if isinstance(el, Part.Edge) and hasattr(el.Curve, "Axis") and hasattr(el.Curve, "Radius"):
        return V(el.Curve.Center), V(el.Curve.Axis)
    return None


def cylinders(part, pt, d, tol_off=None):
    """The part's cylindrical faces on the line (pt, d): each (face, radius,
    convex, [lo, hi] along the axis)."""
    d = V(d)
    d.normalize()
    out = []
    for f in part.Shape.Faces:
        s = f.Surface
        if not (hasattr(s, "Radius") and hasattr(s, "Axis") and hasattr(s, "Center")) or isinstance(s, Part.Sphere):
            continue
        if type(s).__name__ not in ("Cylinder",):
            continue
        ax = V(s.Axis)
        ax.normalize()
        if abs(abs(ax.dot(d)) - 1) > 1e-3:
            continue
        w = V(s.Center) - pt
        off = (w - d * w.dot(d)).Length
        if off > (tol_off if tol_off is not None else max(0.5, 0.15 * s.Radius)):
            continue
        u0, u1, v0, v1 = f.ParameterRange
        p = f.valueAt((u0 + u1) / 2, (v0 + v1) / 2)
        n = f.normalAt((u0 + u1) / 2, (v0 + v1) / 2)
        q = p - V(s.Center)
        radial = q - ax * q.dot(ax)
        convex = n.dot(radial) > 0
        span = [(v.Point - pt).dot(d) for v in f.Vertexes] or [0.0]
        out.append((f, float(s.Radius), convex, [min(span), max(span)], off))
    return out


def fit_of(code):
    hole, _, shaft = str(code).partition("/")
    if not hole or not shaft:
        raise bad("fit: hole/shaft, e.g. \"H7/g6\", \"H7/p6\", \"H8/f7\".")
    return hole.strip(), shaft.strip()


def tools(session):
    s = session

    def doc():
        return s.doc

    def joint_of(asm, name):
        j = next((x for x in A._joints(asm).Group if name in (x.Label, x.Name)), None)
        if j is None or getattr(j, "ObjectToGround", None) is not None:
            raise ToolError("not_found", f"joint {name!r}: not in {asm.Label} — asm_list names them.")
        return j

    # ---- fits ----

    def pair_fit(pa, ea, pb, eb, fit, size):
        def round_axis(el):
            g = getattr(el, "Surface", None) if isinstance(el, Part.Face) else getattr(el, "Curve", None)
            return axis_of(el) if el is not None and hasattr(g, "Radius") else None

        ax = round_axis(ea) or round_axis(eb)
        if ax is None:
            # two flat faces: how they sit on one another
            if isinstance(ea, Part.Face) and isinstance(eb, Part.Face):
                d, pts, _ = ea.distToShape(eb)
                na, nb = axis_of(ea)[1], axis_of(eb)[1]
                ang = math.degrees(math.acos(max(-1.0, min(1.0, abs(na.dot(nb)) / (na.Length * nb.Length or 1)))))
                return {"kind": "faces", "parts": [pa.Label, pb.Label], "gap": r4(d), "angle": r4(ang), "contact": d < 1e-4 and ang < 0.01,
                        "note": "Flat faces: the gap between them and how far from parallel." if d > 1e-4 else "Flat faces in contact."}
            raise ToolError("invalid_input", f"{pa.Label} and {pb.Label}: neither element is round (a hole's face or rim, a pin's face), and they are not two flat faces.")
        pt, d = ax
        ca, cb = cylinders(pa, pt, d), cylinders(pb, pt, d)
        best = None
        for fa in ca:
            for fb in cb:
                if fa[2] == fb[2]:
                    continue  # both holes, or both shafts
                hole, shaft = (fa, fb) if not fa[2] else (fb, fa)
                D, dd = 2 * hole[1], 2 * shaft[1]
                if abs(D - dd) > max(0.5, 0.2 * D):
                    continue
                lo, hi = max(fa[3][0], fb[3][0]), min(fa[3][1], fb[3][1])
                eng = max(0.0, hi - lo)
                key = (abs(D - dd), -eng)
                if best is None or key < best[0]:
                    best = (key, hole, shaft, eng, (pa if hole is fa else pb), (pb if hole is fa else pa))
        if best is None:
            raise ToolError("not_found", f"No hole and shaft of about one size on the axis through {vec(pt)} between {pa.Label} and {pb.Label}.")
        _, hole, shaft, eng, ph, ps = best
        D, dd = 2 * hole[1], 2 * shaft[1]
        c = D - dd
        out = {"kind": "round", "hole": {"part": ph.Label, "diameter": r4(D)}, "shaft": {"part": ps.Label, "diameter": r4(dd)},
               "clearance": r4(c), "as": "clearance" if c > 1e-6 else "interference" if c < -1e-6 else "line to line",
               "engagement": r4(eng), "axisOffset": r4(abs(hole[4] - shaft[4]))}
        if fit:
            hc, sc = fit_of(fit)
            N = float(size) if size else round((D + dd) / 2, 2)
            try:
                hes, hei = iso286.limits(N, hc)
                ses, sei = iso286.limits(N, sc)
            except ValueError as e:
                raise bad(f"fit: {e}")
            lo_c, hi_c = (N + hei) - (N + ses), (N + hes) - (N + sei)
            kind = "clearance" if lo_c >= 0 else "interference" if hi_c <= 0 else "transition"
            hole_ok = N + hei - 1e-9 <= D <= N + hes + 1e-9
            shaft_ok = N + sei - 1e-9 <= dd <= N + ses + 1e-9
            out["fit"] = {"code": f"{hc}/{sc}", "nominal": r4(N), "type": kind, "clearanceRange": [r4(lo_c), r4(hi_c)],
                          "hole": {"limits": [r4(N + hei), r4(N + hes)], "ok": hole_ok},
                          "shaft": {"limits": [r4(N + sei), r4(N + ses)], "ok": shaft_ok}}
            out["verdict"] = "pass" if hole_ok and shaft_ok else "fix"
            if not (hole_ok and shaft_ok):
                tips = []
                if not hole_ok:
                    tips.append(f"the hole is ⌀{D:g}, {hc} wants {N + hei:g} … {N + hes:g}")
                if not shaft_ok:
                    tips.append(f"the shaft is ⌀{dd:g}, {sc} wants {N + sei:g} … {N + ses:g}")
                out["fix"] = "; ".join(tips) + " — modelled at nominal sizes is usual: size them into the band, or say the fit is for the drawing only."
        return out

    def asm_fits(a):
        asm = A._assembly(doc(), a.get("assembly"))
        fit, size = a.get("fit"), a.get("size")
        rows = []
        if a.get("pairs"):
            for i, p in enumerate(a["pairs"]):
                ends = []
                for side in ("a", "b"):
                    e = (p or {}).get(side)
                    if not isinstance(e, dict) or "part" not in e:
                        raise bad(f"pairs[{i}].{side}: {{ part, element }}.")
                    part = A._part(asm, e["part"])
                    el = A._element(s, part, e.get("element", {"kind": "face"}), f"pairs[{i}].{side}.element")
                    ends.append((part, part.Shape.getElement(el)))
                rows.append(pair_fit(ends[0][0], ends[0][1], ends[1][0], ends[1][1], p.get("fit", fit), p.get("size", size)))
        else:
            js = [joint_of(asm, a["joint"])] if a.get("joint") else [j for j in A._joints(asm).Group if getattr(j, "ObjectToGround", None) is None]
            for j in js:
                pa, ea, _ = ref_element(doc(), getattr(j, "Reference1", None))
                pb, eb, _ = ref_element(doc(), getattr(j, "Reference2", None))
                if pa is None or pb is None:
                    continue
                try:
                    r = pair_fit(pa, ea, pb, eb, fit, size)
                except ToolError as e:
                    if a.get("joint"):
                        raise
                    r = {"skipped": e.message}
                rows.append({"joint": j.Label, "kind": str(getattr(j, "JointType", "")).lower(), **r})
        if not rows:
            raise ToolError("not_found", f"No joints in {asm.Label} to read fits from: name pairs [{{ a, b }}] instead.")
        verdicts = [r.get("verdict") for r in rows if r.get("verdict")]
        out = {"assembly": asm.Label, "fits": rows}
        if verdicts:
            out["verdict"] = "pass" if all(v == "pass" for v in verdicts) else "fix"
        return out

    # ---- poses and limits ----

    def limits_of(j):
        kind = str(getattr(j, "JointType", "")).lower()
        out = {}
        if getattr(j, "EnableAngleMin", False):
            out["angleMin"] = r4(j.AngleMin)
        if getattr(j, "EnableAngleMax", False):
            out["angleMax"] = r4(j.AngleMax)
        if getattr(j, "EnableLengthMin", False):
            out["lengthMin"] = r4(j.LengthMin)
        if getattr(j, "EnableLengthMax", False):
            out["lengthMax"] = r4(j.LengthMax)
        return kind, out

    def asm_limits(a):
        asm = A._assembly(doc(), a.get("assembly"))
        j = joint_of(asm, a.get("joint"))
        kind = str(getattr(j, "JointType", "")).lower()
        if "EnableAngleMin" not in j.PropertiesList:
            raise ToolError("unavailable", f"{j.Label} has no limits in this FreeCAD (joint limits came with the 1.0 Assembly workbench).")
        turning = kind == "revolute" or (kind == "cylindrical" and a.get("turn"))
        if kind not in ("revolute", "slider", "cylindrical"):
            raise bad(f"{j.Label} is {kind}: limits are for revolute (degrees), slider (mm) and cylindrical joints.")
        pre = "Angle" if turning else "Length"
        for end in ("min", "max"):
            if end in a:
                v = a[end]
                setattr(j, f"Enable{pre}{end.capitalize()}", v is not None)
                if v is not None:
                    setattr(j, f"{pre}{end.capitalize()}", float(v))
        lo, hi = getattr(j, f"{pre}Min"), getattr(j, f"{pre}Max")
        if getattr(j, f"Enable{pre}Min") and getattr(j, f"Enable{pre}Max") and float(lo) > float(hi):
            raise bad("min is over max.")
        doc().recompute()
        code, said = A._status(asm)
        if code != 0:
            raise ToolError("failed", f"With those limits {asm.Label} does not solve: {said} — nothing was kept. The parts may sit outside them now: asm_pose them in first.")
        s.snapshot("asm_limits")
        return {"joint": j.Label, "kind": kind, "limits": limits_of(j)[1], "unit": "degrees" if turning else "mm", "solve": said, "step": len(s.steps)}

    def moving_group(asm, j):
        p1, e1, _ = ref_element(doc(), j.Reference1)
        p2, e2, _ = ref_element(doc(), j.Reference2)
        grounded = A._grounded(asm)
        fixed_side, moving, el = (p1, p2, e2) if p2 not in grounded else (p2, p1, e1)
        ax = axis_of(el) if el is not None else None
        if ax is None:
            ax = axis_of(e1 if el is e2 else e2)
        if ax is None or moving is None:
            raise ToolError("failed", f"The axis of {j.Label} could not be read from its elements.")
        group, todo = {moving.Name: moving}, [moving]
        others = [x for x in A._joints(asm).Group if x is not j and getattr(x, "ObjectToGround", None) is None]
        while todo:
            cur = todo.pop()
            for x in others:
                q1, _, _ = ref_element(doc(), getattr(x, "Reference1", None))
                q2, _, _ = ref_element(doc(), getattr(x, "Reference2", None))
                for here, there in ((q1, q2), (q2, q1)):
                    if here is not None and there is not None and here.Name == cur.Name and there.Name not in group and there not in grounded and there is not fixed_side:
                        group[there.Name] = there
                        todo.append(there)
        return group, ax

    def asm_pose(a):
        asm = A._assembly(doc(), a.get("assembly"))
        j = joint_of(asm, a.get("joint"))
        kind = str(getattr(j, "JointType", "")).lower()
        if kind not in ("revolute", "slider", "cylindrical"):
            raise bad(f"{j.Label} is {kind}: a pose turns a revolute joint (degrees) or slides a slider or cylindrical one (mm).")
        turning = kind == "revolute" or (kind == "cylindrical" and a.get("turn"))
        if POSE not in j.PropertiesList:
            j.addProperty("App::PropertyFloat", POSE, "AgentLink", "How far asm_pose has moved this joint from where it was made (degrees or mm)")
        now = float(getattr(j, POSE))
        if a.get("home"):
            target = 0.0
        elif a.get("to") is not None:
            target = float(a["to"])
        elif a.get("by") is not None:
            target = now + float(a["by"])
        else:
            raise bad("to (from where the joint was made), by (from where it is), or home: true.")
        _, lim = limits_of(j)
        lo = lim.get("angleMin" if turning else "lengthMin")
        hi = lim.get("angleMax" if turning else "lengthMax")
        if (lo is not None and target < lo - 1e-9) or (hi is not None and target > hi + 1e-9):
            raise bad(f"{target:g} is outside {j.Label}'s limits [{lo if lo is not None else '-'} … {hi if hi is not None else '-'}].")
        group, (centre, axis) = moving_group(asm, j)
        axis = V(axis)
        axis.normalize()
        step = target - now
        if turning:
            rot = FreeCAD.Rotation(axis, step)
            move = FreeCAD.Placement(centre - rot.multVec(centre), rot)
        else:
            move = FreeCAD.Placement(axis * step, FreeCAD.Rotation())
        for o in group.values():
            o.Placement = move.multiply(o.Placement)
        setattr(j, POSE, target)
        doc().recompute()
        code, said = A._status(asm)
        if code != 0:
            raise ToolError("failed", f"Posed, {asm.Label} does not solve: {said} — nothing was kept.")
        hits, _ = A._overlaps(A._parts(asm))
        if hits and not a.get("allowOverlap"):
            raise ToolError("failed", f"At {target:g} {'°' if turning else 'mm'} parts overlap ({', '.join('/'.join(h['parts']) for h in hits)}) — nothing was kept. asm_sweep finds the clear range; allowOverlap: true keeps it anyway.")
        s.snapshot("asm_pose")
        out = {"joint": j.Label, "pose": r4(target), "unit": "degrees" if turning else "mm", "moved": sorted(o.Label for o in group.values()),
               "parts": {o.Label: A._placement(o) for o in group.values()}, "solve": said, "step": len(s.steps)}
        if hits:
            out["overlaps"] = hits
        return out

    # ---- exploded layout ----

    def asm_explode(a):
        asm = A._assembly(doc(), a.get("assembly"))
        parts = A._parts(asm)
        if a.get("collapse"):
            if EXPLODED not in asm.PropertiesList or not asm.getPropertyByName(EXPLODED):
                raise ToolError("invalid_state", f"{asm.Label} is not exploded.")
            kept = json.loads(asm.getPropertyByName(EXPLODED))
            for o in parts:
                if o.Name in kept:
                    m = kept[o.Name]
                    o.Placement = FreeCAD.Placement(V(*m["base"]), FreeCAD.Rotation(*m["q"]))
            setattr(asm, EXPLODED, "")
            doc().recompute()
            code, said = A._status(asm)
            s.snapshot("asm_explode")
            return {"assembly": asm.Label, "collapsed": True, "solve": said, "step": len(s.steps)}
        if EXPLODED in asm.PropertiesList and asm.getPropertyByName(EXPLODED):
            raise ToolError("invalid_state", f"{asm.Label} is already exploded: collapse: true first.")
        grounded = A._grounded(asm)
        which = [A._part(asm, p) for p in a["parts"]] if a.get("parts") else [o for o in parts if o not in grounded]
        if not which:
            raise bad("Nothing to move: every part is grounded — name parts.")
        factor = float(a.get("factor", 1.6))
        if not 1.0 < factor <= 10:
            raise bad("factor: how far out, over 1 (1.6 spreads the parts by 60% of where they sit from the middle).")
        bb = FreeCAD.BoundBox()
        for o in parts:
            bb.add(o.Shape.BoundBox)
        C = bb.Center
        kept = {o.Name: {"base": [o.Placement.Base.x, o.Placement.Base.y, o.Placement.Base.z], "q": list(o.Placement.Rotation.Q)} for o in parts}
        moves = {}
        if a.get("axis") is not None:
            ax = a["axis"]
            d = V(*{"x": (1, 0, 0), "y": (0, 1, 0), "z": (0, 0, 1)}[ax]) if isinstance(ax, str) and ax in "xyz" and len(ax) == 1 else V(*ax)
            if d.Length < 1e-9:
                raise bad("axis: \"x\" | \"y\" | \"z\" or [x, y, z].")
            d.normalize()
            # stacked along the axis in the order they sit, each a gap past the last
            order = sorted(which, key=lambda o: (o.Shape.BoundBox.Center - C).dot(d))
            size = max((max(abs(o.Shape.BoundBox.XLength * d.x), abs(o.Shape.BoundBox.YLength * d.y), abs(o.Shape.BoundBox.ZLength * d.z)) for o in parts), default=10.0)
            gap = float(a.get("gap") or size * (factor - 1))
            mid = (len(order) - 1) / 2.0
            ref_ground = [(o.Shape.BoundBox.Center - C).dot(d) for o in grounded] or [0.0]
            g0 = sum(ref_ground) / len(ref_ground)
            for i, o in enumerate(order):
                side = (o.Shape.BoundBox.Center - C).dot(d) - g0
                k = (i - mid) if not grounded else (i + 1 if side >= 0 else -(len(order) - i))
                moves[o.Name] = d * (k * gap)
        else:
            for o in which:
                v = o.Shape.BoundBox.Center - C
                if v.Length < 1e-6:
                    v = V(0, 0, 1) * (bb.ZLength * 0.5 + 1)
                moves[o.Name] = v * (factor - 1)
        for o in which:
            o.Placement = FreeCAD.Placement(o.Placement.Base + moves[o.Name], o.Placement.Rotation)
        if EXPLODED not in asm.PropertiesList:
            asm.addProperty("App::PropertyString", EXPLODED, "AgentLink", "Where the parts sat before asm_explode, to collapse to (JSON)")
        setattr(asm, EXPLODED, json.dumps(kept))
        doc().recompute()
        s.snapshot("asm_explode")
        return {"assembly": asm.Label, "exploded": {o.Label: vec(moves[o.Name]) for o in which}, "step": len(s.steps),
                "note": "Not solved, so the joints do not pull the parts back: view_render to look, drawing_exploded for a manual, then asm_explode { collapse: true }. Kept in the file, so it collapses after reopening too."}

    # ---- bill of materials ----

    def asm_bom(a):
        asm = A._assembly(doc(), a.get("assembly"))
        cols = ["Index", "Name", "Quantity"] + [c for c in (a.get("columns") or []) if isinstance(c, str)]
        bom = None
        try:
            grp = next((o for o in asm.Group if o.TypeId == "Assembly::BomGroup"), None) or asm.newObject("Assembly::BomGroup", "Bills of Materials")
            bom = grp.newObject("Assembly::BomObject", "Bill of Materials")
            native = True
        except Exception:
            native = False
        if bom is None:
            bom = doc().addObject("Spreadsheet::Sheet", "BOM")
        bom.Label = s.unique(a.get("name") or f"{asm.Label} BOM")
        mats = store.materials(doc())
        rows = []
        for o in A._parts(asm):
            m = mats.get(o.Label)
            vol = o.Shape.Volume / 1000.0
            rows.append({"name": o.Label, "material": m or "", "mass": (round(vol * DENSITY[m], 2) if m in DENSITY else ""), "volume": round(vol, 3)})
        if native:
            bom.columnsNames = cols + [c for c in ("Material", "Mass (g)", "Volume (cm3)") if c not in cols]
            doc().recompute()
            # custom columns: filled by name, kept by FreeCAD across regenerations
            from .. import cells as Cc

            hdr = [bom.getContents(Cc.name(1, c)) for c in range(1, len(bom.columnsNames) + 1)]
            name_col = hdr.index("Name") + 1 if "Name" in hdr else 2
            r = 2
            while bom.getContents(Cc.name(r, name_col)):
                nm = bom.getContents(Cc.name(r, name_col)).lstrip("'")
                row = next((x for x in rows if x["name"] == nm), None)
                if row:
                    for colname, key in (("Material", "material"), ("Mass (g)", "mass"), ("Volume (cm3)", "volume")):
                        if colname in hdr and row[key] != "":
                            bom.set(Cc.name(r, hdr.index(colname) + 1), str(row[key]) if key != "material" else "'" + row[key])
                r += 1
            doc().recompute()
        else:
            from .. import cells as Cc

            hdr = ["Index", "Name", "Quantity", "Material", "Mass (g)", "Volume (cm3)"]
            for c, h in enumerate(hdr, 1):
                bom.set(Cc.name(1, c), "'" + h)
            for i, row in enumerate(rows, 2):
                vals = [str(i - 1), row["name"], "1", row["material"], row["mass"], row["volume"]]
                for c, v in enumerate(vals, 1):
                    if v != "":
                        bom.set(Cc.name(i, c), ("'" + v) if c in (2, 4) else str(v))
            doc().recompute()
        s.snapshot("asm_bom")
        out = {"bom": bom.Label, "sheet": bom.Name, "native": native, "parts": rows, "step": len(s.steps),
               "next": "td_table { sheet } puts it on a drawing; sheet_read reads it."}
        if any(not r["material"] for r in rows):
            out["note"] = "Parts with no material recorded have no mass: part_spec { material, bodies } records one."
        return out

    asm_ref = {"type": "string", "description": "The assembly's label; may be left out when there is one."}
    end = {"type": "object"}
    return [
        Tool("asm_fits", "Fits: for each joint (or joint, or pairs [{ a: { part, element }, b }]), the hole and the shaft it puts together — found as round faces on the joint's axis — their diameters, clearance (minus: interference), engagement length and axis offset; with fit \"H7/g6\" (size: the nominal, else read off the parts) each held to ISO 286: the fit's type and clearance range, each part in or out of its band. Flat faces: their gap and angle. Changes nothing.",
             obj({"assembly": asm_ref, "joint": {"type": "string"}, "pairs": {"type": "array", "items": {"type": "object", "properties": {"a": end, "b": end, "fit": {"type": "string"}, "size": {"type": "number"}}}},
                  "fit": {"type": "string"}, "size": {"type": "number"}}), asm_fits),
        Tool("asm_pose", "Move a revolute joint (degrees) or slider / cylindrical one (mm; turn: true turns a cylindrical) and leave it there: to (from where the joint was made), by (from where it is), or home: true. Respects the joint's limits; refused if parts would overlap (allowOverlap: true keeps it).",
             obj({"assembly": asm_ref, "joint": {"type": "string"}, "to": {"type": "number"}, "by": {"type": "number"}, "home": {"type": "boolean"}, "turn": {"type": "boolean"}, "allowOverlap": {"type": "boolean"}}, ["joint"]), asm_pose, changes=True),
        Tool("asm_limits", "A joint's limits, FreeCAD's own: min and max (degrees for revolute, mm for slider; turn: true for a cylindrical's angle); null clears one. asm_sweep sweeps between them when from/to are left out.",
             obj({"assembly": asm_ref, "joint": {"type": "string"}, "min": {"type": ["number", "null"]}, "max": {"type": ["number", "null"]}, "turn": {"type": "boolean"}}, ["joint"]), asm_limits, changes=True),
        Tool("asm_explode", "Spread an assembly's parts out to see how it goes together: factor (1.6: 60% further from the middle), or along an axis \"z\" | [x,y,z] stacked in order with gap mm; parts (default every one not grounded). collapse: true puts them back (kept in the file). Not solved while exploded.",
             obj({"assembly": asm_ref, "factor": {"type": "number"}, "axis": {"type": ["string", "array"]}, "gap": {"type": "number"}, "parts": {"type": "array", "items": {"type": "string"}}, "collapse": {"type": "boolean"}}), asm_explode, changes=True),
        Tool("asm_bom", "The bill of materials: FreeCAD's own BOM object (a spreadsheet the Assembly workbench keeps up to date) with Index, Name, Quantity, and Material, mass and volume from part_spec's materials; columns adds more (\".Label2\" reads a property). td_table puts it on a drawing.",
             obj({"assembly": asm_ref, "columns": {"type": "array", "items": {"type": "string"}}, "name": {"type": "string"}}), asm_bom, changes=True),
    ]
