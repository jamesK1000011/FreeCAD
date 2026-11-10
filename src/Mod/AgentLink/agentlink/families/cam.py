# SPDX-License-Identifier: LGPL-2.1-or-later
# Toolpaths with FreeCAD's CAM workbench: a job round a body with its stock
# and an end mill, operations — profile, pocket, adaptive clearing, facing,
# drilling — each computed and read back (cut length, time, depth), and
# G-code written by one of FreeCAD's post-processors and checked against
# the stock. The mill family checks whether a part can be milled at all;
# this one makes the paths.

import importlib
import math
import os

import FreeCAD

import Path  # noqa: F401  — FreeCAD's CAM workbench
import Path.Main.Job as PathJob
import Path.Tool.Controller as PathToolController

from ..protocol import Tool, ToolError
from ..tools import obj
from . import measure as M
from . import picks

GROUP = "cam"
INFO = "Toolpaths (FreeCAD's CAM workbench): a job with stock and end mill, profile, pocket, adaptive, facing and drilling operations, G-code posted and checked."

OPS = {
    "profile": "Path.Op.Profile",
    "pocket": "Path.Op.PocketShape",
    "adaptive": "Path.Op.Adaptive",
    "face": "Path.Op.MillFace",
    "drill": "Path.Op.Drilling",
}
POSTS = ["grbl", "linuxcnc", "mach3_mach4", "marlin", "smoothie", "centroid", "generic"]
RAPID = 3000.0  # mm/min, for the time estimate where the machine's is not known


def _jobs(doc):
    return [o for o in doc.Objects if o.TypeId == "Path::FeaturePython" and hasattr(o, "Operations") and hasattr(o, "Stock")]


def _job(doc, ref):
    jobs = _jobs(doc)
    if not jobs:
        raise ToolError("not_found", "No CAM job: cam_job { body, tool } makes one.")
    if ref is None:
        if len(jobs) == 1:
            return jobs[0]
        raise ToolError("invalid_input", f"job: say which — {', '.join(j.Label for j in jobs)}.")
    for j in jobs:
        if ref in (j.Label, j.Name):
            return j
    raise ToolError("not_found", f"job {ref!r}: the jobs are {', '.join(j.Label for j in jobs)}.")


def _q(v, unit):
    try:
        return round(float(FreeCAD.Units.Quantity(v).getValueAs(unit)), 3)
    except Exception:
        try:
            return round(float(v), 3)
        except Exception:
            return None


CYCLES = ("G73", "G81", "G82", "G83", "G85", "G86", "G89")


def path_stats(commands, tc):
    """Lengths, depth and an estimated time of a path's moves: cutting at
    the tool controller's feeds, rapids at RAPID."""
    hf = _q(tc.HorizFeed, "mm/min") if tc is not None else None
    vf = _q(tc.VertFeed, "mm/min") if tc is not None else None
    x = y = z = None
    cut = rapid = minutes = 0.0
    zmin, zmax = math.inf, -math.inf
    lo, hi = [math.inf] * 3, [-math.inf] * 3
    retract_initial = True
    for c in commands:
        name = c.Name.upper()
        p = c.Parameters
        if name in CYCLES and None not in (x, y, z) and "Z" in p:
            # A drilling cycle: rapid over the hole, down to R, feed to Z, back up.
            nx, ny, bottom = p.get("X", x), p.get("Y", y), p["Z"]
            r = p.get("R", z)
            top = max(z, r)
            over = math.hypot(nx - x, ny - y) + (top - z) + (top - r)
            depth = max(0.0, r - bottom)
            rapid += over + depth
            minutes += (over + depth) / RAPID + (depth / (vf or hf) if (vf or hf) else 0.0)
            cut += depth
            zmin, zmax = min(zmin, bottom), max(zmax, r)
            for k, v in enumerate((nx, ny, bottom)):
                lo[k], hi[k] = min(lo[k], v), max(hi[k], v)
            for k, v in enumerate((nx, ny, r)):
                lo[k], hi[k] = min(lo[k], v), max(hi[k], v)
            x, y, z = nx, ny, (top if retract_initial else r)
            continue
        if name in ("G98", "G99"):
            retract_initial = name == "G98"
            continue
        if name not in ("G0", "G00", "G1", "G01", "G2", "G02", "G3", "G03"):
            continue
        nx, ny, nz = p.get("X", x), p.get("Y", y), p.get("Z", z)
        if None in (x, y, z):
            x, y, z = nx, ny, nz
            continue
        if None in (nx, ny, nz):
            continue
        d = math.dist((x, y, z), (nx, ny, nz))
        if name in ("G2", "G02", "G3", "G03") and ("I" in p or "J" in p):
            cx, cy = x + p.get("I", 0.0), y + p.get("J", 0.0)
            r = math.hypot(x - cx, y - cy)
            a0, a1 = math.atan2(y - cy, x - cx), math.atan2(ny - cy, nx - cx)
            sweep = (a0 - a1) if name in ("G2", "G02") else (a1 - a0)
            sweep = sweep % (2 * math.pi) or 2 * math.pi
            d = math.hypot(r * sweep, nz - z)
        if name in ("G0", "G00"):
            rapid += d
            minutes += d / RAPID
        else:
            cut += d
            plunge = abs(nz - z) > 1e-9 and math.hypot(nx - x, ny - y) < 1e-9
            feed = (vf if plunge else hf) or hf or vf
            if feed:
                minutes += d / feed
            zmin, zmax = min(zmin, z, nz), max(zmax, z, nz)
            for k, v in enumerate((nx, ny, nz)):
                lo[k], hi[k] = min(lo[k], v), max(hi[k], v)
        x, y, z = nx, ny, nz
    out = {"cutMm": round(cut, 1), "rapidMm": round(rapid, 1), "minutes": round(minutes, 2)}
    if zmin < math.inf:
        out["cutZ"] = [round(zmin, 3), round(zmax, 3)]
        out["cutBox"] = {"min": [round(v, 3) for v in lo], "max": [round(v, 3) for v in hi]}
    return out


def tools(session):
    s = session

    def doc():
        if s.doc is None:
            raise ToolError("invalid_state", "No document: doc_new first.")
        FreeCAD.setActiveDocument(s.doc.Name)
        return s.doc

    def tc_of(job):
        group = job.Tools.Group if hasattr(job, "Tools") else []
        return group[0] if group else None

    def set_tool(tc, t):
        if t.get("diameter") is not None:
            d = picks.num(t["diameter"], "tool.diameter")
            if d <= 0:
                raise ToolError("invalid_input", "tool.diameter: the end mill's, mm, over 0.")
            tc.Tool.Diameter = d
        if t.get("feed") is not None:
            tc.HorizFeed = f"{picks.num(t['feed'], 'tool.feed')} mm/min"
        if t.get("plunge") is not None:
            tc.VertFeed = f"{picks.num(t['plunge'], 'tool.plunge')} mm/min"
        if t.get("rpm") is not None:
            tc.SpindleSpeed = picks.num(t["rpm"], "tool.rpm")
        if t.get("number") is not None:
            tc.ToolNumber = int(t["number"])

    def tool_out(tc):
        if tc is None:
            return None
        return {"name": tc.Label, "number": tc.ToolNumber, "diameter": _q(tc.Tool.Diameter, "mm"), "feed": _q(tc.HorizFeed, "mm/min"), "plunge": _q(tc.VertFeed, "mm/min"), "rpm": tc.SpindleSpeed}

    def stock_out(job):
        bb = job.Stock.Shape.BoundBox
        return {"min": [round(bb.XMin, 3), round(bb.YMin, 3), round(bb.ZMin, 3)], "max": [round(bb.XMax, 3), round(bb.YMax, 3), round(bb.ZMax, 3)], "size": [round(bb.XLength, 3), round(bb.YLength, 3), round(bb.ZLength, 3)]}

    def cam_job(a):
        d = doc()
        o = s.body(a.get("body"))
        tool = a.get("tool") or {}
        if not isinstance(tool, dict) or tool.get("diameter") is None:
            raise ToolError("invalid_input", "tool: { diameter (mm), feed and plunge (mm/min), rpm } — the end mill the person will use; ask if not said.")
        job = PathJob.Create(a.get("name") or "Job", [o], None)
        if a.get("name"):
            job.Label = a["name"]
        st = a.get("stock") or {}
        margin = picks.num(st.get("margin", 2.0), "stock.margin")
        top = picks.num(st.get("top", 1.0), "stock.top")
        bottom = picks.num(st.get("bottom", 0.0), "stock.bottom")
        try:
            job.Stock.ExtXneg = job.Stock.ExtXpos = job.Stock.ExtYneg = job.Stock.ExtYpos = margin
            job.Stock.ExtZpos, job.Stock.ExtZneg = top, bottom
        except Exception:
            pass
        tc = tc_of(job)
        if tc is None:
            tc = PathToolController.Create()
            job.Proxy.addToolController(tc)
        set_tool(tc, {"feed": 600, "plunge": 200, "rpm": 12000, **tool})
        d.recompute()
        return {"job": job.Label, "body": o.Label, "stock": stock_out(job), "tool": tool_out(tc),
                "next": "cam_op { kind: profile | pocket | adaptive | face | drill } — each computed as it is added; then cam_post."}

    def faces_of(o, qs, what):
        names = []
        for q in qs if isinstance(qs, list) else [qs]:
            if isinstance(q, str):
                names.append(q)
                continue
            query = {**q, "body": o.Label}
            query.setdefault("kind", "face")
            _, _, hits = M.query(s, query)
            if not hits:
                raise ToolError("not_found", f"{what}: no face of {o.Label} matches {q} — shape_query lists them.")
            if q.get("near") is not None and len(hits) > 1:
                # near: the one closest to the point, not every match.
                import Part

                pt = picks.vec3(q["near"] if isinstance(q["near"], list) else q["near"].get("point"), "near")
                hits = sorted(hits, key=lambda h: h[1].distToShape(Part.Vertex(pt))[0])[:1]
            names += [d["ref"] for _, _, d in hits]
        return names

    def cam_op(a):
        d = doc()
        job = _job(d, a.get("job"))
        kind = str(a.get("kind", "")).lower()
        if kind not in OPS:
            raise ToolError("invalid_input", f"kind: one of {', '.join(OPS)}.")
        mod = importlib.import_module(OPS[kind])
        model = job.Model.Group[0]
        base_body = s.body(a.get("body")) if a.get("body") else None
        src = base_body or model
        if kind == "drill":
            # FreeCAD 1.0's Drilling.Create looks for holes before the op knows
            # its model and fails; build it the way newer releases do.
            op = d.addObject("Path::FeaturePython", a.get("name") or "Drill")
            op.Proxy = mod.ObjectDrilling(op, a.get("name") or "Drill", job)
            if getattr(op.Proxy, "model", None) is None:
                op.Proxy.job, op.Proxy.model = job, job.Model.Group
        else:
            op = mod.Create(a.get("name") or kind.capitalize(), None, job)
        if a.get("name"):
            op.Label = a["name"]
        if a.get("faces") is not None:
            op.Base = [(model, faces_of(src, a["faces"], "faces"))]
        if kind == "profile":
            op.Side = "Inside" if a.get("side") == "inside" else "Outside"
        if kind == "adaptive":
            op.Side = "Inside" if a.get("side", "inside") == "inside" else "Outside"
            op.OperationType = "Profile" if a.get("finish") else "Clearing"
        if kind == "drill" and a.get("faces") is None:
            op.Proxy.findAllHoles(op)
            if not op.Base:
                raise ToolError("failed", f"No holes the tool ({_q(job.Tools.Group[0].Tool.Diameter, 'mm')} mm) fits, upright, in {model.Label}: give faces, or a smaller tool (cam_tool). Nothing was kept.")
        if a.get("stepDown") is not None and hasattr(op, "StepDown"):
            op.StepDown = f"{picks.num(a['stepDown'], 'stepDown')} mm"
        if a.get("stepOver") is not None and hasattr(op, "StepOver"):
            op.StepOver = picks.num(a["stepOver"], "stepOver")
        if a.get("finalDepth") is not None:
            op.setExpression("FinalDepth", None)
            op.FinalDepth = f"{picks.num(a['finalDepth'], 'finalDepth')} mm"
        if a.get("stockToLeave") is not None and hasattr(op, "StockToLeave"):
            op.StockToLeave = f"{picks.num(a['stockToLeave'], 'stockToLeave')} mm"
        d.recompute()
        cmds = list(op.Path.Commands) if getattr(op, "Path", None) is not None else []
        moves = [c for c in cmds if c.Name.upper() in ("G1", "G2", "G3", "G01", "G02", "G03") + CYCLES]
        if not moves:
            raise ToolError("failed", f"The {kind} made no cutting moves: {'the faces are not ones it can cut' if a.get('faces') is not None else 'nothing found to cut — give faces'}; or the tool ({_q(job.Tools.Group[0].Tool.Diameter, 'mm')} mm) is too big for them. Nothing was kept.")
        out = {"op": op.Label, "kind": kind, "job": job.Label, **path_stats(cmds, op.ToolController),
               "depths": {"start": _q(op.StartDepth, "mm"), "final": _q(op.FinalDepth, "mm"), "stepDown": _q(getattr(op, "StepDown", None), "mm") if hasattr(op, "StepDown") else None}}
        if a.get("faces") is not None:
            out["faces"] = op.Base[0][1] if op.Base else []
        bb = job.Stock.Shape.BoundBox
        if "cutBox" in out and out["cutBox"]["min"][2] < bb.ZMin - 1e-6:
            out["warning"] = f"It cuts down to z {out['cutBox']['min'][2]}, below the stock's bottom ({round(bb.ZMin, 3)}): into the fixture — set finalDepth."
        return out

    def cam_list(a):
        job = _job(doc(), a.get("job"))
        ops = []
        for op in job.Operations.Group:
            cmds = list(op.Path.Commands) if getattr(op, "Path", None) is not None else []
            ops.append({"op": op.Label, "type": op.Proxy.__class__.__name__ if hasattr(op, "Proxy") else op.TypeId, "active": bool(getattr(op, "Active", True)), **path_stats(cmds, getattr(op, "ToolController", None))})
        total = round(sum(o["minutes"] for o in ops), 2)
        return {"job": job.Label, "stock": stock_out(job), "tool": tool_out(tc_of(job)), "ops": ops, "minutes": total}

    def cam_tool(a):
        job = _job(doc(), a.get("job"))
        tc = tc_of(job)
        set_tool(tc, a)
        doc().recompute()
        return {"job": job.Label, "tool": tool_out(tc), "note": "Every operation was computed again with the tool as it is now: cam_list for their paths."}

    def cam_remove(a):
        d = doc()
        job = _job(d, a.get("job"))
        hit = next((op for op in job.Operations.Group if a.get("op") in (op.Label, op.Name)), None)
        if hit is None:
            raise ToolError("not_found", f"op {a.get('op')!r}: cam_list lists them.")
        job.Operations.removeObject(hit)
        d.removeObject(hit.Name)
        d.recompute()
        return {"job": job.Label, "removed": a.get("op"), "ops": [op.Label for op in job.Operations.Group]}

    def cam_post(a):
        d = doc()
        job = _job(d, a.get("job"))
        name = a.get("post", "grbl")
        if name not in POSTS:
            raise ToolError("invalid_input", f"post: one of {', '.join(POSTS)} — the person's machine's controller.")
        ops = [op for op in job.Operations.Group if getattr(op, "Active", True)]
        if not ops:
            raise ToolError("invalid_state", "No operations in the job: cam_op first.")
        try:
            post = importlib.import_module(f"Path.Post.scripts.{name}_post")
            importlib.reload(post)
        except ImportError:
            raise ToolError("unavailable", f"FreeCAD here has no {name} post-processor.")
        path = a.get("path") or os.path.join(s.out_dir, f"{job.Label.replace(' ', '_')}.nc")
        if not os.path.isabs(path):
            path = os.path.join(s.out_dir, path)
        postables = []
        last_tc = None
        for op in ops:
            tc = getattr(op, "ToolController", None)
            if tc is not None and tc is not last_tc:
                postables.append(tc)
                last_tc = tc
            postables.append(op)
        try:
            if hasattr(post, "export"):
                gcode = post.export(postables, "-", "--no-show-editor")
            else:
                # Later releases: post-processors are classes, made for the job.
                from Path.Post.Processor import PostProcessorFactory

                if hasattr(job, "PostProcessorArgs"):
                    job.PostProcessorArgs = "--no-show-editor"
                pp = PostProcessorFactory.get_post_processor(job, name)
                sections = pp.export() or []
                gcode = "\n".join(g for _, g in sections if isinstance(g, str))
        except ToolError:
            raise
        except Exception as e:
            raise ToolError("failed", f"The {name} post-processor stopped: {e}")
        if not isinstance(gcode, str) or not gcode.strip():
            raise ToolError("failed", f"The {name} post-processor wrote nothing.")
        with open(path, "w", encoding="utf-8") as f:
            f.write(gcode)
        cmds = [c for op in ops for c in op.Path.Commands]
        stats = path_stats(cmds, last_tc)
        bb = job.Stock.Shape.BoundBox
        out = {"job": job.Label, "post": name, "path": path, "lines": len(gcode.splitlines()), "bytes": len(gcode.encode()), **stats, "ops": [o.Label for o in ops], "stock": stock_out(job)}
        warnings = []
        if "cutBox" in stats:
            lo, hi = stats["cutBox"]["min"], stats["cutBox"]["max"]
            r = _q(last_tc.Tool.Diameter, "mm") / 2 if last_tc is not None else 0
            if lo[2] < bb.ZMin - 1e-6:
                warnings.append(f"Cuts below the stock's bottom (z {lo[2]} < {round(bb.ZMin, 3)}): into the fixture or the spoil board.")
            if lo[0] - r < bb.XMin - 25 or hi[0] + r > bb.XMax + 25 or lo[1] - r < bb.YMin - 25 or hi[1] + r > bb.YMax + 25:
                warnings.append("Cuts reach far outside the stock: check the operations.")
        if warnings:
            out["warnings"] = warnings
        out["note"] = "Check it in a simulator or the controller's preview before running; zero the machine on the stock as the job sets it (cam_list stock). Running it is the person's."
        return out

    jobref = {"type": "string", "description": "The job's label; may be left out when there is one."}
    faces = {"description": "The faces to work: [\"Face6\"] or queries [{ normal: [0,0,1], z: 12 }] as shape_query takes; left out, the whole part (profile: its outline; drill: every hole)."}
    return [
        Tool("cam_job", "Start a CAM job for a body: the stock round it (stock { margin mm each side, top mm over, bottom mm under }) and the end mill (tool { diameter mm, feed and plunge mm/min, rpm, number }) — ask the person which mill and feeds; the defaults are gentle. Then cam_op.",
             obj({"body": {"type": "string"}, "tool": {"type": "object"}, "stock": {"type": "object"}, "name": {"type": "string"}}, ["tool"]), cam_job, changes=True),
        Tool("cam_op", "Add an operation to the job and compute it: kind profile (round the outline, side outside|inside), pocket (clear down to the faces given — a pocket's floor), adaptive (adaptive clearing, side inside|outside; finish: true for a finishing pass), face (face the top flat), drill (the holes, every one when faces are left out). stepDown mm, stepOver %, finalDepth mm (z), stockToLeave mm. Returns the cut and rapid lengths, minutes and the depth it reaches; refused with nothing kept when it makes no cutting moves.",
             obj({"job": jobref, "kind": {"type": "string", "enum": list(OPS)}, "faces": faces, "body": {"type": "string"}, "side": {"type": "string", "enum": ["outside", "inside"]}, "finish": {"type": "boolean"}, "stepDown": {"type": "number"}, "stepOver": {"type": "number"}, "finalDepth": {"type": "number"}, "stockToLeave": {"type": "number"}, "name": {"type": "string"}}, ["kind"]),
             cam_op, changes=True),
        Tool("cam_list", "The job: stock, tool, each operation with its cut length, minutes and depth, and the total time. Changes nothing.", obj({"job": jobref}), cam_list),
        Tool("cam_tool", "Change the job's end mill or feeds (diameter, feed, plunge, rpm, number); every operation is computed again.",
             obj({"job": jobref, "diameter": {"type": "number"}, "feed": {"type": "number"}, "plunge": {"type": "number"}, "rpm": {"type": "number"}, "number": {"type": "integer"}}), cam_tool, changes=True),
        Tool("cam_remove", "Remove an operation from the job.", obj({"job": jobref, "op": {"type": "string"}}, ["op"]), cam_remove, changes=True),
        Tool("cam_post", "Write the job's G-code with FreeCAD's post-processor for the machine's controller (post grbl — the default —, linuxcnc, mach3_mach4, marlin, smoothie, centroid, generic) to path (the session's folder). Returns lines, cut length, minutes, and warnings for cuts below the stock or far outside it. Running it is the person's.",
             obj({"job": jobref, "post": {"type": "string", "enum": POSTS}, "path": {"type": "string"}}), cam_post),
    ]
