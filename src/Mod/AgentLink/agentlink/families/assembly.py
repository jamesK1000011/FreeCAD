# SPDX-License-Identifier: LGPL-2.1-or-later
# Assemblies with FreeCAD's own Assembly workbench (the Ondsel solver): the
# parts put in an assembly, one held fixed, joints between their faces and
# edges — fixed, revolute, cylindrical, slider, ball, distance, parallel,
# perpendicular, angle — solved, and the result checked: where each part
# went, how many ways it can still move, and whether any two parts overlap.
# Faces and edges are picked by what they are (shape_query's queries), so a
# joint does not depend on FreeCAD's numbering.

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
SOLVE = {0: "solved", -1: "solver error", -2: "redundant constraints", -3: "conflicting constraints", -4: "over-constrained", -5: "malformed constraints", -6: "no part is grounded"}


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
        off = a.get("offset")
        rot = a.get("rotate")
        if off is not None or rot is not None:
            j.Offset2 = FreeCAD.Placement(V(0, 0, picks.num(off or 0, "offset")), FreeCAD.Rotation(V(0, 0, 1), picks.num(rot or 0, "rotate")))
        refs = [[part, [el, el]] for part, el in ends]
        try:
            j.Proxy.setJointConnectors(j, refs)
        except Exception as e:
            raise ToolError("failed", f"FreeCAD could not make the {kind} joint between {ends[0][0].Label}.{ends[0][1]} and {ends[1][0].Label}.{ends[1][1]}: {e}")
        if a.get("reverse"):
            try:
                j.Proxy.flipOnePart(j)
            except Exception:
                pass
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
                return f"{o.Label}.{subs[0]}" if subs else o.Label

            joints.append({"joint": j.Label, "kind": str(getattr(j, "JointType", "?")).lower(), "a": ref(r1), "b": ref(r2), "suppressed": bool(getattr(j, "Suppressed", False))})
        code, said = _status(asm)
        return {"assembly": asm.Label, "parts": [{"part": o.Label, "placement": _placement(o), "grounded": o in _grounded(asm)} for o in _parts(asm)], "joints": joints, "solve": said}

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
             "Join two parts and solve: kind fixed (no motion), revolute (turns about the elements' axis), cylindrical (turns and slides), slider (slides only), ball, distance (distance mm between them), parallel, perpendicular, angle (angle degrees). a and b each a part and a face or edge on it — for a flat face the joint sits at its middle with its normal; for a round edge or face, on its axis. offset mm along the joint's normal and rotate degrees about it; reverse: true flips b over (faces meeting the wrong way). Returns which parts moved where, and overlaps the solve left. Trap: the part of b moves; a grounded part never does.",
             obj({"assembly": asm_ref, "kind": {"type": "string", "enum": OFFERED}, "a": part_end, "b": part_end, "offset": {"type": "number"}, "rotate": {"type": "number"}, "distance": {"type": "number"}, "angle": {"type": "number"}, "reverse": {"type": "boolean"}, "name": {"type": "string"}}, ["kind", "a", "b"]),
             asm_joint, changes=True),
        Tool("asm_list", "An assembly: its parts with their placements and which are grounded, its joints with what each joins, and whether it solves. Changes nothing.", obj({"assembly": asm_ref}), asm_list),
        Tool("asm_joint_remove", "Remove a joint (by label, asm_list lists them) and solve again.", obj({"assembly": asm_ref, "joint": {"type": "string"}}, ["joint"]), asm_joint_remove, changes=True),
        Tool("asm_check", "Check an assembly: whether it solves, which parts overlap (the volume and where), with clearance mm the pairs closer than it, and parts joined to nothing grounded. verdict clear or fix. view_render shows it; section cuts it open at a fit.",
             obj({"assembly": asm_ref, "clearance": {"type": "number"}}), asm_check),
    ]
