# SPDX-License-Identifier: LGPL-2.1-or-later
# CAM's remaining commands, headless: path objects (area, from a shape, path
# from edges, compound, array), program flow (stop, fixture offset, custom
# G-code), the property bag, and the job template export; Draft's dimension
# flip and heal; Assembly's rigid groups and a new empty part. The ops CAM
# makes like any other (3D pocket, tapping, mill facing, probe, flute) are
# cam_op kinds.

import json
import os

import FreeCAD

from ..protocol import Tool, ToolError
from ..tools import obj
from . import picks

GROUP = "cam_more"
INFO = "CAM paths from areas and shapes, compound, array, stop, fixture offsets, property bags, job templates; Draft dimension flip and heal; Assembly rigid groups and new parts."


def bad(msg):
    return ToolError("invalid_input", msg)


def _cam(what):
    try:
        import Path  # noqa: F401

        return True
    except ImportError:
        raise ToolError("unavailable", f"{what} needs FreeCAD's CAM workbench, not in this build.")


def tools(s):
    def d():
        if s.doc is None:
            raise ToolError("invalid_state", "No document open: doc_new first.")
        return s.doc

    def job_of(ref):
        from .cam import _job

        return _job(d(), ref)

    def into_job(o, job):
        if job is not None:
            job.Operations.addObject(o)

    def gcode(o):
        p = getattr(o, "Path", None)
        return [c.toGCode() for c in p.Commands] if p is not None else []

    def cam_path(a):
        _cam("A CAM path")
        kind = a.get("kind")
        job = job_of(a["job"]) if a.get("job") else None
        name = a.get("name")
        if kind == "area":
            # Path::FeatureArea: sections and offsets of shapes, the engine CAM's ops use.
            o = d().addObject("Path::FeatureArea", "Area")
            o.Sources = [picks.shaped(d(), r, "sources") for r in (a.get("sources") or [])]
            if not o.Sources:
                raise bad("sources: the shapes to work from.")
            if a.get("workplane"):
                label, sub, sh = picks.resolve(s, a["workplane"])
                o.WorkPlane = sh
            for k, v in (a.get("props") or {}).items():
                setattr(o, k, v)
        elif kind == "from_shape":
            o = d().addObject("Path::FeatureShape", "PathShape")
            o.Sources = [picks.shaped(d(), r, "sources") for r in (a.get("sources") or [])]
            if not o.Sources:
                raise bad("sources: wires or edges to turn into a path.")
            into_job(o, job)
        elif kind == "edges":
            try:
                from Path.Op.Gui.PathShape import ObjectPathShape
            except ImportError:
                raise ToolError("unavailable", f"Path from Shape (edges as a tool path with a tool controller) is not in FreeCAD {'.'.join(FreeCAD.Version()[:3])}: kind from_shape does it without one.")
            o = d().addObject("Path::FeaturePython", "PathShape")
            ObjectPathShape(o, job)
            o.Base = [(picks.shaped(d(), r, "sources"), []) for r in (a.get("sources") or [])]
            into_job(o, job)
        elif kind == "compound":
            o = d().addObject("Path::FeatureCompound", "Compound")
            o.Group = [picks.find(d(), r, "ops") for r in (a.get("ops") or [])]
            into_job(o, job)
        elif kind == "array":
            from Path.Op.Gui.Array import ObjectArray

            o = d().addObject("Path::FeaturePython", "Array")
            ObjectArray(o)
            o.Base = [picks.find(d(), r, "ops") for r in (a.get("ops") or [])]
            if not o.Base:
                raise bad("ops: the operations to repeat.")
            o.ToolController = o.Base[0].ToolController
            for k, v in (a.get("props") or {}).items():
                setattr(o, k, v)
            into_job(o, job)
        elif kind == "stop":
            from Path.Op.Gui.Stop import Stop

            o = d().addObject("Path::FeaturePython", "Stop")
            Stop(o)
            o.Stop = "Mandatory" if a.get("mandatory") else "Optional"
            into_job(o, job)
        elif kind == "fixture":
            from Path.Main.Gui.Fixture import Fixture

            o = d().addObject("Path::FeaturePython", "Fixture")
            Fixture(o)
            o.Fixture = a.get("fixture", "G54")
            o.Active = True
            into_job(o, job)
        else:
            raise bad("kind: area { sources, workplane, props } | from_shape { sources } | edges { sources, job } | compound { ops } | array { ops, props: { Type, Copies, Offset … } } | stop { mandatory } | fixture { fixture: G54…G59.9 }.")
        if name:
            o.Label = s.unique(name, o)
        d().recompute()
        s.snapshot("cam_path")
        out = {"object": o.Label, "kind": kind, **({"job": job.Label} if job is not None else {})}
        g = gcode(o)
        if g:
            out["commands"] = len(g)
            out["first"] = g[:6]
        sh = getattr(o, "Shape", None)
        if kind == "area" and sh is not None and not sh.isNull():
            out["wires"] = len(sh.Wires)
            out["area"] = round(sum(f.Area for f in sh.Faces), 3) if sh.Faces else None
        return out

    def cam_property_bag(a):
        _cam("A property bag")
        from Path.Base import PropertyBag

        bag = PropertyBag.Create(a.get("name") or "PropertyBag")
        for p in a.get("properties") or []:
            nm, kind = p.get("name"), p.get("type", "Float")
            if not nm:
                raise bad("properties: [{ name, type (Float, Length, Angle, Integer, String, Bool), value }].")
            bag.Proxy.addCustomProperty(f"App::Property{kind}", nm, p.get("group", "Custom"), p.get("info", ""))
            if "value" in p:
                setattr(bag, nm, p["value"])
        d().recompute()
        s.snapshot("cam_property_bag")
        return {"bag": bag.Label, "properties": [p["name"] for p in a.get("properties") or []]}

    def cam_template_export(a):
        _cam("A job template")
        import Path.Main.Job as PathJob
        import Path.Main.Stock as PathStock

        job = job_of(a.get("job"))
        attrs = job.Proxy.templateAttrs(job)
        if a.get("description"):
            attrs[PathJob.JobTemplate.Description] = a["description"]
        attrs[PathJob.JobTemplate.ToolController] = [tc.Proxy.templateAttrs(tc) for tc in job.Tools.Group]
        attrs[PathJob.JobTemplate.Stock] = PathStock.TemplateAttributes(job.Stock)
        attrs[PathJob.JobTemplate.SetupSheet] = job.Proxy.setupSheet.templateAttributes(True, True, True)
        enc = job.Proxy.setupSheet.encodeTemplateAttributes(attrs)
        path = os.path.expanduser(a.get("path") or os.path.join(s.out_dir, f"job_{job.Label}.json"))
        if not os.path.basename(path).startswith("job_"):
            path = os.path.join(os.path.dirname(path), "job_" + os.path.basename(path))
        if not path.endswith(".json"):
            path += ".json"
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w") as fp:
            json.dump(enc, fp, sort_keys=True, indent=2)
        return {"template": path, "bytes": os.path.getsize(path), "tools": len(job.Tools.Group),
                "note": "A job_*.json in CAM's template folder is offered when a job is made; cam_job { template } uses it."}

    # ---- Draft ---------------------------------------------------------------------------

    def draft_fix(a):
        act = a.get("action")
        if act == "flip_dimension":
            o = picks.find(d(), a.get("dimension"), "dimension")
            if not hasattr(o, "Normal"):
                raise bad(f"{o.Label} is not a Draft dimension.")
            o.Normal = o.Normal.negative() if o.Normal.Length else FreeCAD.Vector(0, 0, -1)
            d().recompute()
            return {"dimension": o.Label, "normal": [round(x, 6) for x in o.Normal], "note": "Its text now reads from the other side."}
        if act == "heal":
            import Draft

            targets = [picks.find(d(), r) for r in a["objects"]] if a.get("objects") else None
            Draft.heal(targets)
            d().recompute()
            return {"healed": [o.Label for o in targets] if targets else "all Draft objects", "note": "Old-version Draft objects brought up to date."}
        raise bad("action: flip_dimension { dimension } | heal { objects }.")

    # ---- Assembly ------------------------------------------------------------------------

    def asm_rigid_group(a):
        from .assembly import _assembly, _joints, _part

        asm = _assembly(d(), a.get("assembly"))
        parts = [_part(asm, r) for r in (a.get("parts") or [])]
        if len(parts) < 2:
            raise bad("parts: two or more, held fixed to each other.")
        import JointObject

        if not hasattr(JointObject, "RigidGroupJoint"):
            raise ToolError("unavailable", f"Rigid groups came in FreeCAD 1.2; this is {'.'.join(FreeCAD.Version()[:3])}. asm_joint kind fixed between each pair does the same.")
        rg = _joints(asm).newObject("App::FeaturePython", "RigidGroupJoint")
        JointObject.RigidGroupJoint(rg, parts)
        if a.get("name"):
            rg.Label = s.unique(a["name"], rg)
        d().recompute()
        s.snapshot("asm_rigid_group")
        return {"joint": rg.Label, "parts": [p.Label for p in parts]}

    def asm_new_part(a):
        from .assembly import _assembly

        asm = _assembly(d(), a.get("assembly"))
        name = a.get("name") or "Part"
        part = d().addObject("App::Part", "Part")
        part.Label = s.unique(name, part)
        body = part.newObject("PartDesign::Body", "Body")
        body.Label = s.unique(f"{part.Label} body", body)
        lk = asm.newObject("App::Link", "Link")
        lk.LinkedObject = part
        lk.Label = s.unique(part.Label, lk)
        if a.get("at") is not None:
            lk.Placement = picks.placement(a)
        d().recompute()
        s.snapshot("asm_new_part")
        return {"part": part.Label, "body": body.Label, "inAssembly": lk.Label,
                "note": "An empty part in the assembly: sketch_new and pd_pad in its body build it in place."}

    st = {"type": "string"}
    arr = {"type": "array", "items": {"type": "string"}}
    num = {"type": "number"}
    v3 = {"type": "array", "items": num}
    return [
        Tool("cam_path", "CAM path objects: kind area { sources, workplane face, props } (Path Area: sections and offsets) | from_shape { sources } (a path along wires) | edges { sources, job } (Path from Shape with a tool controller) | compound { ops } | array { ops, props: Type, Copies, Offset, Angle … } | stop { mandatory } (M0/M1) | fixture { fixture: G54 … G59.9 }; job puts it in a job. Answers the G-code's first lines.",
             obj({"kind": {"type": "string", "enum": ["area", "from_shape", "edges", "compound", "array", "stop", "fixture"]}, "job": st, "sources": arr, "workplane": st, "ops": arr, "props": {"type": "object"},
                  "mandatory": {"type": "boolean"}, "fixture": st, "name": st}, ["kind"]), cam_path, changes=True),
        Tool("cam_property_bag", "A CAM property bag: properties [{ name, type (Float, Length, Angle, Integer, String, Bool), value, group }] that ops' expressions can use.",
             obj({"name": st, "properties": {"type": "array", "items": {"type": "object"}}}), cam_property_bag, changes=True),
        Tool("cam_template_export", "Write a CAM job as a template (job_*.json): its tool controllers, stock, setup sheet and post settings, for the next job.", obj({"job": st, "path": st, "description": st}), cam_template_export),
        Tool("draft_fix", "Draft fixes: action flip_dimension { dimension } (text to the other side) | heal { objects } (old Draft objects brought up to date; all by default).",
             obj({"action": {"type": "string", "enum": ["flip_dimension", "heal"]}, "dimension": st, "objects": arr}, ["action"]), draft_fix, changes=True),
        Tool("asm_rigid_group", "Hold parts of an assembly fixed to each other as one group (Assembly rigid group, FreeCAD 1.2).", obj({"assembly": st, "parts": arr, "name": st}, ["parts"]), asm_rigid_group, changes=True),
        Tool("asm_new_part", "A new empty part (App::Part with a PartDesign body) linked into an assembly, to build in place.", obj({"assembly": st, "name": st, "at": v3, "axis": v3, "angle": num}), asm_new_part, changes=True),
    ]
