# SPDX-License-Identifier: LGPL-2.1-or-later
# Assemblies with FreeCAD's own Assembly workbench (the Ondsel solver): the
# parts put in an assembly, one held fixed, joints between their faces and
# edges — fixed, revolute, cylindrical, slider, ball, distance, parallel,
# perpendicular, angle — solved, and the result checked: where each part
# went, how many ways it can still move, and whether any two parts overlap.
# Faces and edges are picked by what they are (shape_query's queries), so a
# joint does not depend on FreeCAD's numbering.

import math

import FreeCAD
import Part

import JointObject  # FreeCAD's Assembly workbench
import UtilsAssembly  # noqa: F401  — the joints' own helpers load through it

from .. import geometry as geo
from ..protocol import Tool, ToolError
from ..tools import obj
from . import measure as M
from . import picks

GROUP = "assembly"
INFO = "Assemblies (FreeCAD's Assembly workbench): parts put together, one grounded, joints between faces and edges solved; positions, freedom left and overlaps checked."

V = FreeCAD.Vector
KINDS = {k.lower(): k for k in JointObject.JointTypes}
# Joints between two elements; gears, belts and the like need more than this
# family asks for yet.
OFFERED = ["fixed", "revolute", "cylindrical", "slider", "ball", "distance", "parallel", "perpendicular", "angle"]
# the motion couplings, where this FreeCAD has them
OFFERED += [k for k in ("rackpinion", "screw", "gears", "belt") if k in KINDS]
SOLVE = {0: "solved", -1: "solver error", -2: "redundant constraints", -3: "conflicting constraints", -4: "over-constrained", -5: "malformed constraints", -6: "no part is grounded"}


def _release():
    v = FreeCAD.Version()
    try:
        return int(v[0]), int(v[1])
    except (ValueError, IndexError):
        return (1, 1)


def _assemblies(doc):
    return [o for o in doc.Objects if o.TypeId == "Assembly::AssemblyObject"]


def _assembly(doc, ref):
    asms = _assemblies(doc)
    if not asms:
        raise ToolError("not_found", "No assembly in the document: asm_new { parts } makes one.")
    if ref is None:
        if len(asms) == 1:
            return asms[0]
        raise ToolError("invalid_input", f"assembly: say which — {', '.join(a.Label for a in asms)}.")
    for a in asms:
        if ref in (a.Label, a.Name) or str(ref).lower() == a.Label.lower():
            return a
    raise ToolError("not_found", f"assembly {ref!r}: the assemblies are {', '.join(a.Label for a in asms)}.")


def _joints(asm):
    g = next((o for o in asm.Group if o.TypeId == "Assembly::JointGroup"), None)
    if g is None:
        g = asm.newObject("Assembly::JointGroup", "Joints")
    return g


def _parts(asm):
    return [o for o in asm.Group if o.TypeId != "Assembly::JointGroup" and hasattr(o, "Shape") and not o.Shape.isNull()]


def _part(asm, ref):
    for o in _parts(asm):
        if ref in (o.Label, o.Name) or str(ref).lower() == o.Label.lower():
            return o
    raise ToolError("not_found", f"part {ref!r} is not in {asm.Label}: its parts are {', '.join(o.Label for o in _parts(asm)) or 'none'} (asm_add puts one in).")


def _grounded(asm):
    out = []
    for j in _joints(asm).Group:
        g = getattr(j, "ObjectToGround", None)
        if g is not None:
            out.append(g)
    return out


def _placement(o):
    p = o.Placement
    axis, ang = p.Rotation.Axis, p.Rotation.Angle
    return {"at": geo.vec(p.Base), "axis": geo.vec(axis), "angle": round(ang * 180 / 3.141592653589793, 4)}


def _element(session, part, q, what):
    """One face or edge of a part: its name ("Face6", "Edge3") or a query as
    shape_query takes it ({ kind, normal, near, radius, type, … }); with
    several matching, near picks the closest."""
    if isinstance(q, str):
        try:
            part.Shape.getElement(q)
        except Exception:
            raise ToolError("invalid_input", f"{what}: {part.Label} has no {q}.")
        return q
    if not isinstance(q, dict):
        raise ToolError("invalid_input", f"{what}: a face or edge — \"Face6\", or a query {{ kind: face|edge, normal, near, radius, type }} as shape_query takes.")
    query = {k: v for k, v in q.items() if k not in ("part",)}
    query["body"] = part.Label
    _, _, hits = M.query(session, query)
    if not hits:
        raise ToolError("not_found", f"{what}: no {query.get('kind', 'face')} of {part.Label} matches {q} — shape_query {{ body: \"{part.Label}\" }} lists them.")
    if len(hits) > 1:
        near = q.get("near")
        if near is None:
            names = ", ".join(d.get("ref", "?") for _, _, d in hits[:8])
            raise ToolError("invalid_input", f"{what}: {len(hits)} match ({names}{' …' if len(hits) > 8 else ''}) — narrow it, or add near: [x, y, z] to take the closest.")
        p = picks.vec3(near if isinstance(near, list) else near.get("point"), "near")
        hits = sorted(hits, key=lambda h: h[1].distToShape(Part.Vertex(p))[0])
    return hits[0][2]["ref"]


def _status(asm):
    try:
        code = asm.solve()
    except Exception as e:
        return -1, f"solver error: {e}"
    return code, SOLVE.get(code, f"code {code}")


def _overlaps(parts, clearance=0.0):
    """Pairs of parts that overlap (their common volume), and, with
    clearance, those closer than it."""
    out, near = [], []
    for i, a in enumerate(parts):
        for b in parts[i + 1:]:
            sa, sb = a.Shape, b.Shape
            if not sa.BoundBox.intersect(sb.BoundBox) and clearance <= 0:
                continue
            try:
                common = sa.common(sb)
                vol = common.Volume if common.Solids else 0.0
            except Exception:
                vol = 0.0
            if vol > 1e-3:
                c = common.BoundBox.Center
                out.append({"parts": [a.Label, b.Label], "volume": round(vol, 3), "at": geo.vec(c)})
                continue
            if clearance > 0:
                d, pts, _ = sa.distToShape(sb)
                if d < clearance:
                    near.append({"parts": [a.Label, b.Label], "gap": round(d, 4), "at": geo.vec(pts[0][0])})
    return out, near


def tools(session):
    s = session

    def doc():
        if s.doc is None:
            raise ToolError("invalid_state", "No document: doc_new first.")
        return s.doc

    def asm_new(a):
        refs = a.get("parts") or []
        if not isinstance(refs, list) or not refs:
            raise ToolError("invalid_input", "parts: the bodies to put together, by label — session_state lists them.")
        parts = [s.body(r) for r in refs]
        asm = doc().addObject("Assembly::AssemblyObject", (a.get("name") or "Assembly").replace(" ", "_"))
        asm.Label = a.get("name") or "Assembly"
        _joints(asm)
        for o in parts:
            for p in list(o.InList):
                if p.TypeId == "Assembly::AssemblyObject" and p is not asm:
                    raise ToolError("invalid_state", f"{o.Label} is in {p.Label} already: a part is in one assembly.")
            asm.addObject(o)
        ground = a.get("ground", parts[0].Label)
        if ground:
            g = _joints(asm).newObject("App::FeaturePython", "GroundedJoint")
            JointObject.GroundedJoint(g, _part(asm, ground))
        doc().recompute()
        return {"assembly": asm.Label, "parts": [o.Label for o in parts], "grounded": [o.Label for o in _grounded(asm)],
                "next": "asm_joint between the parts' faces and edges; each joint solves the assembly."}

    def asm_add(a):
        asm = _assembly(doc(), a.get("assembly"))
        added = []
        for r in a.get("parts") or []:
            o = s.body(r)
            if o in asm.Group:
                continue
            asm.addObject(o)
            added.append(o.Label)
        if not added:
            raise ToolError("invalid_input", "parts: bodies not in the assembly yet.")
        doc().recompute()
        return {"assembly": asm.Label, "added": added, "parts": [o.Label for o in _parts(asm)]}

    def asm_ground(a):
        asm = _assembly(doc(), a.get("assembly"))
        o = _part(asm, a.get("part"))
        want = a.get("grounded", True)
        jg = _joints(asm)
        have = [j for j in jg.Group if getattr(j, "ObjectToGround", None) == o]
        if want and not have:
            g = jg.newObject("App::FeaturePython", "GroundedJoint")
            JointObject.GroundedJoint(g, o)
        if not want:
            for j in have:
                doc().removeObject(j.Name)
        doc().recompute()
        return {"assembly": asm.Label, "grounded": [x.Label for x in _grounded(asm)]}

    def asm_joint(a):
        asm = _assembly(doc(), a.get("assembly"))
        kind = str(a.get("kind", "")).lower()
        if kind not in OFFERED:
            raise ToolError("invalid_input", f"kind: one of {', '.join(OFFERED)}.")
        if not _grounded(asm):
            raise ToolError("invalid_state", f"No part of {asm.Label} is grounded: asm_ground {{ part }} the one that stays put first.")
        ends = []
        for side in ("a", "b"):
            e = a.get(side)
            if not isinstance(e, dict) or "part" not in e:
                raise ToolError("invalid_input", f"{side}: {{ part, element }} — element \"Face6\" or a query {{ kind: face|edge, normal, near, radius, type }}.")
            part = _part(asm, e["part"])
            el = _element(s, part, e.get("element", {"kind": "face", **{k: v for k, v in e.items() if k not in ('part', 'element')}}), f"{side}.element")
            ends.append((part, el))
        if ends[0][0] is ends[1][0]:
            raise ToolError("invalid_input", "a and b are on the same part: a joint is between two parts.")
        before = {o.Label: o.Placement.copy() for o in _parts(asm)}
        j = _joints(asm).newObject("App::FeaturePython", a.get("name") or KINDS[kind])
        JointObject.Joint(j, JointObject.JointTypes.index(KINDS[kind]))
        if a.get("name"):
            j.Label = a["name"]
        if kind == "distance":
            j.Distance = picks.num(a.get("distance", 0), "distance")
        if kind == "angle":
            j.Angle = picks.num(a.get("angle", 90), "angle")
        if kind in ("rackpinion", "screw", "gears", "belt"):
            # the couplings' figures: a pitch radius, a lead, or each wheel's radius
            # — read off round elements when not given
            def radius_of(part, el):
                try:
                    g = part.Shape.getElement(el)
                    c = getattr(g, "Curve", None) or getattr(g, "Surface", None)
                    return float(c.Radius)
                except Exception:
                    return None

            r1 = a.get("radius1") if a.get("radius1") is not None else (a.get("pitch") if kind == "screw" else radius_of(*ends[0]))
            if r1 is None or float(r1) <= 0:
                raise ToolError("invalid_input", {"rackpinion": "radius1: the pinion's pitch radius, mm (or give its round edge).",
                                                  "screw": "pitch: mm the nut moves per turn.",
                                                  "gears": "radius1: the first gear's pitch radius, mm (or give its round edge).",
                                                  "belt": "radius1: the first pulley's radius, mm (or give its round edge)."}[kind])
            j.Distance = float(r1)
            if kind in ("gears", "belt"):
                r2 = a.get("radius2") if a.get("radius2") is not None else radius_of(*ends[1])
                if r2 is None or float(r2) <= 0:
                    raise ToolError("invalid_input", "radius2: the second wheel's radius, mm (or give its round edge).")
                j.Distance2 = float(r2)
        off = a.get("offset")
        rot = a.get("rotate")

        if off is not None or rot is not None:
            j.Offset2 = FreeCAD.Placement(V(0, 0, picks.num(off or 0, "offset")), FreeCAD.Rotation(V(0, 0, 1), picks.num(rot or 0, "rotate")))

        def turn_over():
            # As the task panel's Reverse button does: the part turned over on
            # the joint, then solved; the solver keeps the nearer way round.
            j.Proxy.flipOnePart(j)
            doc().recompute()
            return _status(asm)

        # 1.0 reads a reference from the assembly down (the part's name, then
        # the element); later releases from the part itself.
        if _release() < (1, 1):
            refs = [[asm, [f"{part.Name}.{el}", f"{part.Name}.{el}"]] for part, el in ends]
        else:
            refs = [[part, [el, el]] for part, el in ends]
        try:
            j.Proxy.setJointConnectors(j, refs)
        except Exception as e:
            raise ToolError("failed", f"FreeCAD could not make the {kind} joint between {ends[0][0].Label}.{ends[0][1]} and {ends[1][0].Label}.{ends[1][1]}: {e}")
        # The joint's frames on each element, worked out again here: later
        # releases leave that to the window's selection, which there is none
        # of headless.
        try:
            j.Proxy.updateJCSPlacements(j)
        except Exception:
            pass
        doc().recompute()
        code, said = _status(asm)
        flipped = False
        if code == 0 and a.get("reverse"):
            code, said = turn_over()
            flipped = True
        # FreeCAD lines the joint's frames up as the elements' geometry
        # runs, which can put a lid into its box instead of on it. Two parts
        # overlapping after the solve, and no reverse said: the other way is
        # tried, and kept when it overlaps less.
        if code == 0 and a.get("reverse") is None:
            first, _ = _overlaps(_parts(asm))
            if first:
                vol0 = sum(h["volume"] for h in first)
                kept = {o.Name: o.Placement.copy() for o in _parts(asm)}
                code2, said2 = turn_over()
                second, _ = _overlaps(_parts(asm))
                if code2 == 0 and sum(h["volume"] for h in second) < vol0 - 1e-6:
                    code, said, flipped = code2, said2, True
                else:
                    for o in _parts(asm):
                        o.Placement = kept[o.Name]
                    doc().recompute()
                    code, said = _status(asm)
        if code != 0:
            raise ToolError("failed", f"The {kind} joint leaves {asm.Label} unsolved: {said} — nothing was kept. A joint that repeats what another holds is redundant; asm_list shows the joints.")
        moved = {}
        for o in _parts(asm):
            b = before[o.Label]
            if not o.Placement.isSame(b, 1e-6):
                moved[o.Label] = {"from": geo.vec(b.Base), "to": _placement(o), "by": round((o.Placement.Base - b.Base).Length, 4)}
        hits, _ = _overlaps(_parts(asm))
        out = {"joint": j.Label, "kind": kind, "a": f"{ends[0][0].Label}.{ends[0][1]}", "b": f"{ends[1][0].Label}.{ends[1][1]}", "solve": said, "moved": moved}
        if flipped:
            out["flipped"] = True if a.get("reverse") else "turned over to stop the parts overlapping (reverse: false keeps it as FreeCAD first put it)"
        if hits:
            out["overlaps"] = hits
            out["warning"] = "Parts overlap after the solve: a face picked the wrong way round, or the parts do not fit — reverse: true flips the second part, or check the sizes."
        return out

    def asm_list(a):
        asm = _assembly(doc(), a.get("assembly"))
        joints = []
        for j in _joints(asm).Group:
            if getattr(j, "ObjectToGround", None) is not None:
                continue
            r1, r2 = getattr(j, "Reference1", None), getattr(j, "Reference2", None)

            def ref(r):
                if not r:
                    return None
                o, subs = r[0], (r[1] if len(r) > 1 else [])
                if not subs:
                    return o.Label
                path = subs[0].split(".")
                if o.TypeId == "Assembly::AssemblyObject" and len(path) > 1:
                    p = doc().getObject(path[0])
                    return f"{p.Label if p else path[0]}.{path[-1]}"
                return f"{o.Label}.{path[-1]}"

            joints.append({"joint": j.Label, "kind": str(getattr(j, "JointType", "?")).lower(), "a": ref(r1), "b": ref(r2), "suppressed": bool(getattr(j, "Suppressed", False))})
        code, said = _status(asm)
        return {"assembly": asm.Label, "parts": [{"part": o.Label, "placement": _placement(o), "grounded": o in _grounded(asm)} for o in _parts(asm)], "joints": joints, "solve": said}

    def _ref_element(r):
        """A joint reference's part and element shape, either form: [asm,
        ["Part.Face6", …]] (1.0) or [part, ["Face6", …]]."""
        if not r:
            return None, None
        o, subs = r[0], (r[1] if len(r) > 1 else [])
        sub = subs[0] if subs else ""
        path = sub.split(".")
        part = o
        if o.TypeId == "Assembly::AssemblyObject" and len(path) > 1:
            part = doc().getObject(path[0])
        el = path[-1]
        try:
            return part, part.Shape.getElement(el)
        except Exception:
            return part, None

    def _axis(el):
        """Where a joint on this element turns and slides, as asm_joint puts
        it: a flat face's middle and normal; a round edge's or face's axis."""
        import Part

        if isinstance(el, Part.Face):
            srf = el.Surface
            if isinstance(srf, Part.Plane):
                u0, u1, v0, v1 = el.ParameterRange
                n = el.normalAt((u0 + u1) / 2, (v0 + v1) / 2)
                return el.CenterOfMass, n
            if hasattr(srf, "Axis") and hasattr(srf, "Center"):
                return srf.Center, srf.Axis
        if isinstance(el, Part.Edge) and hasattr(el.Curve, "Axis"):
            return el.Curve.Center, el.Curve.Axis
        if isinstance(el, Part.Edge):
            p0 = el.valueAt(el.FirstParameter)
            return p0, el.valueAt(el.LastParameter) - p0
        return None

    def asm_sweep(a):
        asm = _assembly(doc(), a.get("assembly"))
        name = a.get("joint")
        j = next((x for x in _joints(asm).Group if x.Label == name or x.Name == name), None)
        if j is None or getattr(j, "ObjectToGround", None) is not None:
            raise ToolError("not_found", f"joint {name!r}: not in {asm.Label} — asm_list names them.")
        kind = str(getattr(j, "JointType", "")).lower()
        if kind not in ("revolute", "slider", "cylindrical"):
            raise ToolError("invalid_input", f"{j.Label} is {kind}: a sweep moves a revolute (turning, degrees), slider or cylindrical (sliding, mm) joint.")
        turning = kind == "revolute" or (kind == "cylindrical" and a.get("turn"))
        # left out, the joint's own limits (asm_limits) bound the sweep
        pre = "Angle" if turning else "Length"
        lim_lo = getattr(j, f"{pre}Min", None) if getattr(j, f"Enable{pre}Min", False) else None
        lim_hi = getattr(j, f"{pre}Max", None) if getattr(j, f"Enable{pre}Max", False) else None
        lo = float(a.get("from", lim_lo if lim_lo is not None else 0))
        hi = float(a.get("to", lim_hi if lim_hi is not None else (90 if turning else 10)))
        steps = int(a.get("steps", 12))
        if not 1 <= steps <= 72:
            raise ToolError("invalid_input", "steps: 1 to 72.")
        clearance = float(a.get("clearance", 0))
        p1, e1 = _ref_element(j.Reference1)
        p2, e2 = _ref_element(j.Reference2)
        grounded = _grounded(asm)
        # The part that moves: the second, unless it is the grounded one.
        fixed_side, moving, el = (p1, p2, e1) if p2 not in grounded else (p2, p1, e2)
        ax = _axis(el) if el is not None else None
        if ax is None or moving is None:
            raise ToolError("failed", f"The axis of {j.Label} could not be read from its elements.")
        centre, axis = ax
        axis = V(axis)
        axis.normalize()
        # What moves with it: parts joined to it by other joints, short of
        # grounded parts and the part it turns on.
        group, todo = {moving.Name: moving}, [moving]
        COUPLE = ("gears", "belt", "rackpinion", "screw")
        all_j = [x for x in _joints(asm).Group if x is not j and getattr(x, "ObjectToGround", None) is None]
        couplings = [x for x in all_j if str(getattr(x, "JointType", "")).lower() in COUPLE]
        # a coupling drives its other part, it does not carry it along
        others = [x for x in all_j if x not in couplings]
        while todo:
            cur = todo.pop()
            for x in others:
                q1, _ = _ref_element(getattr(x, "Reference1", None))
                q2, _ = _ref_element(getattr(x, "Reference2", None))
                for here, there in ((q1, q2), (q2, q1)):
                    if here is not None and there is not None and here.Name == cur.Name and there.Name not in group and there not in grounded and there is not fixed_side:
                        group[there.Name] = there
                        todo.append(there)
        # Parts driven through a coupling: each on its own joint (revolute or
        # slider to something that stays), moved by the coupling's ratio.
        driven = []
        for c in couplings:
            q1, _ = _ref_element(getattr(c, "Reference1", None))
            q2, _ = _ref_element(getattr(c, "Reference2", None))
            if q1 is None or q2 is None:
                continue
            if q1.Name in group and q2.Name not in group:
                dvn, first = q2, True
            elif q2.Name in group and q1.Name not in group:
                dvn, first = q1, False
            else:
                continue
            own = None
            for x in others:
                r1_, e1_ = _ref_element(getattr(x, "Reference1", None))
                r2_, e2_ = _ref_element(getattr(x, "Reference2", None))
                if str(getattr(x, "JointType", "")).lower() not in ("revolute", "slider", "cylindrical"):
                    continue
                if r1_ is not None and r1_.Name == dvn.Name and (r2_ is None or r2_.Name not in group):
                    own = (x, e1_)
                elif r2_ is not None and r2_.Name == dvn.Name and (r1_ is None or r1_.Name not in group):
                    own = (x, e2_)
                if own:
                    break
            if own is None or own[1] is None:
                continue
            dax = _axis(own[1])
            if dax is None:
                continue
            ck = str(c.JointType).lower()
            r1v, r2v = float(getattr(c, "Distance", 0) or 0), float(getattr(c, "Distance2", 0) or 0)
            if ck in ("gears", "belt"):
                if r1v <= 0 or r2v <= 0:
                    continue
                ratio = (r1v / r2v) if first else (r2v / r1v)
                factor, how = (-ratio if ck == "gears" else ratio), "turn"
            elif ck == "rackpinion":
                factor, how = (r1v * math.pi / 180.0), "slide"  # mm a degree of the pinion
            else:
                factor, how = (r1v / 360.0), "slide"  # screw: mm a degree
            if not turning and how == "turn":
                continue
            dc, dv = dax
            dv = V(dv)
            dv.normalize()
            driven.append({"part": dvn, "how": how, "factor": factor, "centre": dc, "axis": dv, "via": c.Label})
        kept = {o.Name: o.Placement.copy() for o in _parts(asm)}
        out_steps = []
        try:
            # values from where the joint was made: an asm_pose since is taken off
            posed = float(getattr(j, "AgentLinkPose", 0.0) or 0.0)
            for k in range(steps + 1):
                v = lo + (hi - lo) * k / steps
                if turning:
                    r = FreeCAD.Rotation(axis, v - posed)
                    move = FreeCAD.Placement(centre - r.multVec(centre), r)
                else:
                    move = FreeCAD.Placement(axis * (v - posed), FreeCAD.Rotation())
                for o in group.values():
                    o.Placement = move.multiply(kept[o.Name])
                row = {"at": round(v, 3)}
                for d in driven:
                    amount = (v - posed) * d["factor"]
                    if d["how"] == "turn":
                        r = FreeCAD.Rotation(d["axis"], amount)
                        dm = FreeCAD.Placement(d["centre"] - r.multVec(d["centre"]), r)
                    else:
                        dm = FreeCAD.Placement(d["axis"] * amount, FreeCAD.Rotation())
                    d["part"].Placement = dm.multiply(kept[d["part"].Name])
                    row.setdefault("driven", {})[d["part"].Label] = round(amount, 3)
                doc().recompute()
                hits, near = _overlaps(_parts(asm), clearance)
                if hits:
                    row["overlaps"] = [{"parts": h["parts"], "volume": h["volume"]} for h in hits]
                if clearance > 0 and near:
                    row["closer"] = near
                out_steps.append(row)
        finally:
            for o in _parts(asm):
                if o.Name in kept:
                    o.Placement = kept[o.Name]
            doc().recompute()
            _status(asm)
        unit = "degrees" if turning else "mm"
        bad = [r["at"] for r in out_steps if r.get("overlaps")]
        clear_run = []
        for r in out_steps:
            if r.get("overlaps"):
                break
            clear_run.append(r["at"])
        out = {"joint": j.Label, "kind": kind, "unit": unit, "from": lo, "to": hi, "moves": sorted(o.Label for o in group.values()),
               **({"driven": [{"part": d["part"].Label, "via": d["via"], d["how"] + "s": "degrees" if d["how"] == "turn" else "mm",
                               "per": round(d["factor"], 5)} for d in driven]} if driven else {}),
               "axis": {"through": [round(c, 3) for c in centre], "along": [round(c, 4) for c in axis]}, "steps": out_steps,
               "verdict": "collides" if bad else "clear",
               "note": f"{' and '.join(sorted(o.Label for o in group.values()))} turned (or slid) about the joint's axis and put back after. Steps are {abs(hi - lo) / steps:g} {unit} apart: a collision between two steps is not seen — more steps to look closer. A chain of moving joints beyond this one is carried along rigidly, not solved."}
        if bad:
            out["collidesAt"] = bad
            out["clearFromStart"] = [clear_run[0], clear_run[-1]] if clear_run else None
        return out

    def asm_joint_remove(a):
        asm = _assembly(doc(), a.get("assembly"))
        name = a.get("joint")
        hit = next((j for j in _joints(asm).Group if name in (j.Label, j.Name)), None)
        if hit is None:
            raise ToolError("not_found", f"joint {name!r}: asm_list lists them.")
        doc().removeObject(hit.Name)
        doc().recompute()
        code, said = _status(asm)
        return {"assembly": asm.Label, "removed": name, "solve": said}

    def asm_check(a):
        asm = _assembly(doc(), a.get("assembly"))
        clearance = picks.num(a.get("clearance", 0), "clearance")
        parts = _parts(asm)
        code, said = _status(asm)
        hits, near = _overlaps(parts, clearance)
        free = [o.Label for o in parts if o not in _grounded(asm) and not asm.isPartConnected(o)] if hasattr(asm, "isPartConnected") else []
        out = {"assembly": asm.Label, "solve": said, "parts": len(parts), "overlaps": hits}
        if clearance > 0:
            out["closerThan"] = {"clearance": clearance, "pairs": near}
        if free:
            out["notConnected"] = free
            out["note"] = "Parts joined to nothing grounded float free: a joint to the grounded part, or asm_ground."
        out["verdict"] = "clear" if not hits and said == "solved" else "fix"
        return out

    part_end = {"type": "object", "description": "{ part, element } — element \"Face6\" or a query { kind: face|edge, normal, near, radius, type } as shape_query takes; near picks the closest of several."}
    asm_ref = {"type": "string", "description": "The assembly's label; may be left out when there is one."}
    return [
        Tool("asm_new", "Put bodies together in a new assembly (FreeCAD's Assembly workbench): parts by label, the first grounded (held fixed) unless ground names another or is false. Then asm_joint between them.",
             obj({"parts": {"type": "array", "items": {"type": "string"}}, "name": {"type": "string"}, "ground": {}}, ["parts"]), asm_new, changes=True),
        Tool("asm_add", "Put more bodies into an assembly.", obj({"assembly": asm_ref, "parts": {"type": "array", "items": {"type": "string"}}}, ["parts"]), asm_add, changes=True),
        Tool("asm_ground", "Hold a part fixed (grounded: true, the default) or let it move again (false). An assembly needs one grounded part to solve.", obj({"assembly": asm_ref, "part": {"type": "string"}, "grounded": {"type": "boolean"}}, ["part"]), asm_ground, changes=True),
        Tool("asm_joint",
             "Join two parts and solve: kind fixed (no motion), revolute (turns about the elements' axis), cylindrical (turns and slides), slider (slides only), ball, distance (distance mm between them), parallel, perpendicular, angle (angle degrees); couplings, which tie one motion to another for asm_sweep: rackpinion (radius1, the pinion's pitch radius), screw (pitch mm a turn), gears and belt (radius1, radius2 — read off round edges when left out). a and b each a part and a face or edge on it — for a flat face the joint sits at its middle with its normal; for a round edge or face, on its axis. offset mm along the joint's normal and rotate degrees about it; reverse: true turns b over (false keeps FreeCAD's way; left out, b is turned over when that stops the parts overlapping). Returns which parts moved where, and overlaps the solve left. Trap: the part of b moves; a grounded part never does.",
             obj({"assembly": asm_ref, "kind": {"type": "string", "enum": OFFERED}, "a": part_end, "b": part_end, "offset": {"type": "number"}, "rotate": {"type": "number"}, "distance": {"type": "number"}, "angle": {"type": "number"}, "radius1": {"type": "number"}, "radius2": {"type": "number"}, "pitch": {"type": "number"}, "reverse": {"type": "boolean"}, "name": {"type": "string"}}, ["kind", "a", "b"]),
             asm_joint, changes=True),
        Tool("asm_list", "An assembly: its parts with their placements and which are grounded, its joints with what each joins, and whether it solves. Changes nothing.", obj({"assembly": asm_ref}), asm_list),
        Tool("asm_joint_remove", "Remove a joint (by label, asm_list lists them) and solve again.", obj({"assembly": asm_ref, "joint": {"type": "string"}}, ["joint"]), asm_joint_remove, changes=True),
        Tool("asm_sweep", "Move a revolute joint through angles (from, to degrees; 0 to 90) or a slider or cylindrical one along its axis (mm; turn: true turns a cylindrical) in steps (12), solving at each and checking the parts for overlaps (clearance mm also lists those closer). Answers each step, collidesAt and the clear range from the start, and puts every part back. Does the lid open fully, does the arm swing past the post.",
             obj({"assembly": asm_ref, "joint": {"type": "string"}, "from": {"type": "number"}, "to": {"type": "number"}, "steps": {"type": "integer"}, "clearance": {"type": "number"}, "turn": {"type": "boolean"}}, ["joint"]), asm_sweep),
        Tool("asm_check", "Check an assembly: whether it solves, which parts overlap (the volume and where), with clearance mm the pairs closer than it, and parts joined to nothing grounded. verdict clear or fix. view_render shows it; section cuts it open at a fit.",
             obj({"assembly": asm_ref, "clearance": {"type": "number"}}), asm_check),
    ]
