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
    "engrave": "Path.Op.Engrave",
    "helix": "Path.Op.Helix",
    "slot": "Path.Op.Slot",
    "deburr": "Path.Op.Deburr",
    "vcarve": "Path.Op.Vcarve",
    "thread": "Path.Op.ThreadMilling",
    "surface": "Path.Op.Surface",
    "waterline": "Path.Op.Waterline",
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

    HINTS = {"helix": "helix bores round holes: give the hole's wall (a cylinder face), with a tool smaller than the hole",
             "thread": "thread milling needs a round hole's wall and a thread-mill tool",
             "drill": "drilling needs round holes, upright",
             "deburr": "deburr follows edges: a chamfer or V tool (cam_tool_add)",
             "vcarve": "V-carving follows letters or a sketch (base) with a V-bit (cam_tool_add)",
             "engrave": "engraving follows letters, a sketch or a wire (base)"}

    def cam_op(a):
        d = doc()
        before = {o.Name for o in d.Objects}
        try:
            return cam_op_(a)
        except ToolError:
            raise
        except Exception as e:
            job = _job(d, a.get("job"))
            for o in [o for o in d.Objects if o.Name not in before]:
                if o in job.Operations.Group:
                    job.Operations.removeObject(o)
                d.removeObject(o.Name)
            d.recompute()
            kind = str(a.get("kind", "")).lower()
            import traceback

            fr = traceback.extract_tb(e.__traceback__)[-1]
            e = f"{e} ({os.path.basename(fr.filename)}:{fr.lineno})"
            raise ToolError("failed", f"The {kind} would not compute: {e}" + (f" — {HINTS[kind]}." if kind in HINTS else ".") + " Nothing was kept.") from None

    def make_op(d, mod, kind, a, job):
        cls = {"drill": "ObjectDrilling", "helix": "ObjectHelix", "thread": "ObjectThreadMilling"}.get(kind)
        if cls and hasattr(mod, cls):
            # FreeCAD 1.0's hole ops look for holes in Create before the op knows
            # its model, and fail; built the way newer releases do.
            nm = a.get("name") or kind.capitalize()
            op = d.addObject("Path::FeaturePython", nm)
            op.Proxy = getattr(mod, cls)(op, nm, job)
            if getattr(op.Proxy, "model", None) is None:
                op.Proxy.job, op.Proxy.model = job, job.Model.Group
            return op
        return mod.Create(a.get("name") or kind.capitalize(), None, job)

    def cam_op_(a):
        d = doc()
        job = _job(d, a.get("job"))
        kind = str(a.get("kind", "")).lower()
        if kind not in OPS:
            raise ToolError("invalid_input", f"kind: one of {', '.join(OPS)}.")
        try:
            mod = importlib.import_module(OPS[kind])
        except ImportError as e:
            raise ToolError("unavailable", f"{kind} is not in this FreeCAD's CAM ({e})" + (": 3D surface and waterline need OpenCamLib (ocl) installed." if kind in ("surface", "waterline") else "."))
        model = job.Model.Group[0]
        base_body = s.body(a.get("body")) if a.get("body") else None
        src = base_body or model
        want_tc = None
        if a.get("tool"):
            want_tc = next((x for x in job.Tools.Group if a["tool"] in (x.Label, x.Name)), None)
            if want_tc is None:
                raise ToolError("not_found", f"tool {a['tool']!r}: the job's tools are {', '.join(x.Label for x in job.Tools.Group)} (cam_tool_add adds one).")
        # With more than one tool FreeCAD asks which in a dialog (and headless
        # fails): the op is made with the one asked for, else the job's first.
        import PathScripts.PathUtils as PathUtils

        chosen = want_tc or job.Tools.Group[0]
        orig_find = PathUtils.findToolController
        PathUtils.findToolController = lambda *args, **kw: chosen
        try:
            op = make_op(d, mod, kind, a, job)
        finally:
            PathUtils.findToolController = orig_find
        if a.get("name"):
            op.Label = a["name"]
        if want_tc is not None:
            op.ToolController = want_tc
        if a.get("base"):
            # what an engraving or V-carve follows: letters (draft_text's ShapeString), a sketch, a wire
            objs = [picks.find(d, r) for r in a["base"]]
            if kind == "engrave" and hasattr(op, "BaseShapes"):
                op.BaseShapes = objs
            else:
                op.Base = [(x, [f"Face{i + 1}" for i in range(len(x.Shape.Faces))] or [f"Edge{i + 1}" for i in range(len(x.Shape.Edges))]) for x in objs]
        if a.get("faces") is not None:
            op.Base = [(model, faces_of(src, a["faces"], "faces"))]
        for k, v in (a.get("props") or {}).items():
            if k not in op.PropertiesList:
                raise ToolError("invalid_input", f"props.{k}: {op.Label} has no such property (object_get lists them).")
            try:
                setattr(op, k, v)
            except Exception as e:
                raise ToolError("invalid_input", f"props.{k} = {v!r}: {e}")
        if kind == "profile":
            op.Side = "Inside" if a.get("side") == "inside" else "Outside"
        if kind == "adaptive":
            op.Side = "Inside" if a.get("side", "inside") == "inside" else "Outside"
            op.OperationType = "Profile" if a.get("finish") else "Clearing"
        if kind in ("drill", "helix", "thread") and a.get("faces") is None:
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
            ops.append({"op": op.Label, "type": op.Proxy.__class__.__name__ if hasattr(op, "Proxy") else op.TypeId, "active": bool(getattr(op, "Active", True)), **path_stats(cmds, tc_for(op))})
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


    def cam_sim(a):
        import numpy as np

        from .. import millsim, render

        job = _job(doc(), a.get("job"))
        ops = [op for op in job.Operations.Group if getattr(op, "Active", True) and getattr(op, "Path", None) is not None]
        if not ops:
            raise ToolError("invalid_state", f"{job.Label} has no operations: cam_op first.")
        sb = job.Stock.Shape.BoundBox
        tools_of = []
        for op in ops:
            tc = tc_for(op) or tc_of(job)
            tool = tc.Tool
            d = float(_q(tool.Diameter, "mm"))
            shape = str(getattr(tool, "ShapeName", "") or getattr(tool, "ShapeType", "") or getattr(tool, "BitShape", "") or tool.Label).lower()
            tools_of.append((op, d, "ball" in shape))
        res = a.get("resolution")
        if res is None:
            res = max(min(d for _, d, _ in tools_of) / 6.0, math.sqrt(sb.XLength * sb.YLength / 250000.0), 0.05)
        res = float(res)
        if res <= 0:
            raise ToolError("invalid_input", "resolution: mm per cell, over 0.")
        pad = max(d for _, d, _ in tools_of)
        grid = millsim.Grid(sb.XMin - pad, sb.YMin - pad, sb.XMax + pad, sb.YMax + pad, res)
        sp, sf = job.Stock.Shape.tessellate(0.05)
        H = millsim.top_of_mesh(grid, [(v.x, v.y, v.z) for v in sp], sf, floor=sb.ZMin)
        H = np.maximum(H, sb.ZMin)
        part = None
        for m in job.Model.Group:
            pp, pf = m.Shape.tessellate(0.02)
            top = millsim.top_of_mesh(grid, [(v.x, v.y, v.z) for v in pp], pf)
            part = top if part is None else np.maximum(part, top)
        removed, crashes, per = 0.0, [], []
        here = None  # the tool's place at the end of the last op
        for op, d, ball in tools_of:
            segs = millsim.moves([(c.Name, dict(c.Parameters)) for c in op.Path.Commands], here)
            if segs:
                here = segs[-1][2]
            rem, cr = millsim.run(grid, H, segs, d / 2.0, ball)
            removed += rem
            per.append({"op": op.Label, "tool": d, **({"ballEnd": True} if ball else {}), "removedMm3": round(rem, 1), **({"crashes": len(cr)} if cr else {})})
            crashes += [{"op": op.Label, "at": [round(x, 2) for x in c[0]], "intoStockMm": round(c[1], 2)} for c in cr]
        out = {"job": job.Label, "resolution": round(res, 3), "ops": per, "removedMm3": round(removed, 1)}
        cmp_, diff = millsim.compare(grid, H, part if part is not None else np.full_like(H, -np.inf), sb.ZMin, tol=max(0.02, res / 10))
        out["part"] = cmp_
        findings = []
        if crashes:
            out["crashes"] = crashes[:5]
            findings.append(f"{len(crashes)} rapid move(s) go through stock — a crash on the machine: the first in {crashes[0]['op']} at {crashes[0]['at']}. Raise the clearance or safe height.")
        if "gouge" in cmp_:
            g = cmp_["gouge"]
            findings.append(f"It cuts into the part: {g['deepest']} mm deep at {g['at'][:2]}, over {g['areaMm2']} mm². Check that operation's faces, side and depths.")
        low = float(np.min(H))
        if low < sb.ZMin - 0.05:
            findings.append(f"It cuts {round(sb.ZMin - low, 2)} mm under the stock's bottom: into the spoilboard or vise — set finalDepth.")
        if "left" in cmp_ and cmp_["left"]["share"] > 0.02:
            l = cmp_["left"]
            out["note"] = f"Material is left over the part on {round(100 * l['share'])}% of it, up to {l['most']} mm (at {l['at'][:2]}): a face op for a stock top, a finishing pass or a smaller tool for corners — or none, if that is meant."
        out["verdict"] = "fix" if findings else "clear"
        out["findings"] = findings
        # The picture: from above, each spot of the part coloured by what is
        # left on it (blue gouged, green right, red left over); off the part
        # the cut stock in grey, lighter higher.
        span = max(0.1, abs(cmp_.get("gouge", {}).get("deepest", 0.0)), cmp_.get("left", {}).get("most", 0.0))
        t = (diff + span) / (2 * span)
        img = render.jet(np.nan_to_num(t, nan=0.5))
        hz = (H - sb.ZMin) / max(1e-6, sb.ZLength)
        grey = 0.25 + 0.6 * np.clip(hz, 0, 1)
        off = ~np.isfinite(diff)
        img[off] = np.stack([grey[off]] * 3, axis=-1)
        img = img[::-1]
        k = max(1, int(round(760 / max(grid.nx, grid.ny))))
        img = np.repeat(np.repeat(img, k, axis=0), k, axis=1)
        h, w = img.shape[:2]
        canvas = np.full((h + 70, max(w, 420), 3), 0.12)
        canvas[:h, :w] = img
        render.legend(canvas, -span, span, "mm", "jet", "LEFT ON THE PART", h + 40)
        data = render.png(canvas)
        path = a.get("path") or os.path.join(s.out_dir, f"{job.Label}_sim.png")
        with open(path, "wb") as f:
            f.write(data)
        out["picture"] = path
        if a.get("inline", True):
            out["_png"] = data
        return out

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
            tc = tc_for(op)
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
    # ---- more tools, dressups, setup ----

    SHAPES = ("endmill", "ballend", "bullnose", "v-bit", "chamfer", "drill", "thread-mill", "radius", "dovetail", "reamer", "tap", "slittingsaw", "taperedballnose")

    def make_bit(shape, name):
        """A tool bit of a shape from CAM's shape library, across the tool
        systems of 1.0 and later."""
        shape_file = None
        import Path as _P

        mod_dir = os.path.dirname(os.path.dirname(os.path.abspath(_P.__file__)))  # …/Mod/CAM
        roots = [os.path.join(mod_dir, "Tools", "Shape")]
        for base in (FreeCAD.getResourceDir(), FreeCAD.getHomePath(), os.path.join(FreeCAD.getHomePath(), "share")):
            roots += [os.path.join(base, "Mod", m, "Tools", "Shape") for m in ("CAM", "Path")]
        for root in roots:
            f = os.path.join(root, f"{shape}.fcstd")
            if os.path.isfile(f):
                shape_file = f
                break
        try:
            import Path.Tool.Bit as PathToolBit  # 1.0
        except ImportError:
            PathToolBit = None
        if PathToolBit is not None:
            if shape_file is None:
                raise ToolError("not_found", f"No {shape} shape in this FreeCAD's CAM tool shapes.")
            return PathToolBit.Factory.Create(name, shape_file)
        try:
            from Path.Tool.toolbit import ToolBit  # later releases

            return ToolBit.from_shape_id(shape).attach_to_doc(FreeCAD.ActiveDocument)
        except Exception as e:
            raise ToolError("unavailable", f"This FreeCAD's tool bits could not be made here ({e}): cam_tool changes the job's first tool instead.")

    def cam_tool_add(a):
        d = doc()
        job = _job(d, a.get("job"))
        shape = a.get("shape", "endmill")
        if shape not in SHAPES:
            raise ToolError("invalid_input", f"shape: one of {', '.join(SHAPES)}.")
        if a.get("diameter") is None:
            raise ToolError("invalid_input", "diameter: the tool's, mm — ask the person which tool.")
        name = a.get("name") or f"{shape} {a['diameter']} mm"
        bit = make_bit(shape, name)
        bit.Diameter = f"{picks.num(a['diameter'], 'diameter')} mm"
        for k, prop, unit in (("angle", "CuttingEdgeAngle", "°"), ("length", "Length", "mm"), ("cuttingLength", "CuttingEdgeHeight", "mm"), ("tipDiameter", "TipDiameter", "mm"), ("cornerRadius", "TorusRadius", "mm")):
            if a.get(k) is not None and prop in bit.PropertiesList:
                setattr(bit, prop, f"{float(a[k])} {unit}")
        if a.get("flutes") is not None and "Flutes" in bit.PropertiesList:
            bit.Flutes = int(a["flutes"])
        tc = PathToolController.Create(name, bit)
        job.Proxy.addToolController(tc)
        set_tool(tc, {"feed": 600, "plunge": 200, "rpm": 12000, "number": len(job.Tools.Group), **{k: a[k] for k in ("feed", "plunge", "rpm", "number") if a.get(k) is not None}})
        d.recompute()
        return {"job": job.Label, "tool": tool_out(tc), "shape": shape, "tools": [x.Label for x in job.Tools.Group], "next": f"cam_op {{ …, tool: \"{tc.Label}\" }} uses it."}

    def bit_of(tc):
        """A tool controller's tool as the plain record cam_tool_add takes."""
        b = tc.Tool
        shape = None
        for k in ("ShapeName", "ShapeFile", "BitShape"):
            v = getattr(b, k, None)
            if v:
                shape = os.path.splitext(os.path.basename(str(v)))[0]
                break
        shape = shape or "endmill"
        if shape not in SHAPES:
            alt = shape.replace("_", "-").lower()
            shape = alt if alt in SHAPES else shape.replace("-", "").replace("_", "").lower()
        rec = {"name": tc.Label, "shape": shape, "diameter": _q(b.Diameter, "mm"), "number": tc.ToolNumber,
               "feed": _q(tc.HorizFeed, "mm/min"), "plunge": _q(tc.VertFeed, "mm/min"), "rpm": tc.SpindleSpeed}
        for k, prop, unit in (("angle", "CuttingEdgeAngle", "°"), ("tipDiameter", "TipDiameter", "mm"), ("cornerRadius", "TorusRadius", "mm"),
                              ("length", "Length", "mm"), ("cuttingLength", "CuttingEdgeHeight", "mm")):
            if hasattr(b, prop):
                try:
                    rec[k] = _q(getattr(b, prop), unit)
                except Exception:
                    pass
        if int(getattr(b, "Flutes", 0) or 0) > 0:
            rec["flutes"] = int(b.Flutes)
        return rec

    def cam_library(a):
        """The job's tools out to a file, or a file's tools into the job: the
        person's own tool set, kept outside any one document."""
        import json

        d = doc()
        job = _job(d, a.get("job"))
        act = a.get("action", "export")
        p = a.get("path") or "cam-tools.json"
        p = p if os.path.isabs(p) else os.path.join(s.out_dir, p)
        if act == "export":
            tools_ = [bit_of(tc) for tc in job.Tools.Group]
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                json.dump({"agentlink": "cam-tools", "version": 1, "tools": tools_}, f, indent=1)
            return {"job": job.Label, "path": p, "tools": tools_}
        if act == "import":
            try:
                with open(p, encoding="utf-8") as f:
                    data = json.load(f)
            except (OSError, ValueError) as e:
                raise ToolError("invalid_input", f"path: {p} is not a tool file ({e}).") from None
            recs = data.get("tools") if isinstance(data, dict) else data
            if not isinstance(recs, list) or not recs:
                raise ToolError("invalid_input", "path: a file of tools — { tools: [{ name, shape, diameter, feed, plunge, rpm, number … }] }.")
            have = {x.Label for x in job.Tools.Group}
            added, skipped = [], []
            for r in recs:
                if r.get("name") in have and not a.get("replace"):
                    skipped.append(r.get("name"))
                    continue
                args = {k: v for k, v in r.items() if k in ("shape", "diameter", "angle", "tipDiameter", "cornerRadius", "flutes", "feed", "plunge", "rpm", "number", "name")}
                args["job"] = job.Label
                added.append(cam_tool_add(args)["tool"])
            return {"job": job.Label, "path": p, "added": added, **({"skipped": skipped, "note": "Already in the job by name: replace: true adds them again."} if skipped else {})}
        raise ToolError("invalid_input", "action: export (the job's tools to path) | import (path's tools into the job).")

    DRESSUPS = {"tags": "Path.Dressup.Tags", "dogbone": "Path.Dressup.DogboneII", "boundary": "Path.Dressup.Boundary", "array": "Path.Dressup.Array",
                "ramp": "Path.Dressup.Gui.RampEntry", "leadinout": "Path.Dressup.Gui.LeadInOut"}

    def base_op(x):
        """The operation under a stack of dressups."""
        seen = 0
        while getattr(x, "Base", None) is not None and not isinstance(x.Base, list) and hasattr(x.Base, "Path") and seen < 20:
            x, seen = x.Base, seen + 1
        return x

    def tc_for(x):
        return getattr(base_op(x), "ToolController", None) or getattr(x, "ToolController", None)

    def cam_dressup(a):
        d = doc()
        job = _job(d, a.get("job"))
        want = a.get("op")
        # The job lists the outermost of each stack: a dressup goes on top of any already there.
        top = None
        for x in job.Operations.Group:
            y, chain = x, [x]
            while getattr(y, "Base", None) is not None and not isinstance(y.Base, list) and hasattr(y.Base, "Path"):
                y = y.Base
                chain.append(y)
            if any(want in (c.Label, c.Name) for c in chain):
                top = x
                break
        if top is None:
            raise ToolError("not_found", f"op {want!r}: cam_list lists them.")
        op = top
        kind = a.get("kind")
        if kind not in DRESSUPS:
            raise ToolError("invalid_input", f"kind: {', '.join(DRESSUPS)} — tags (holding tabs: count, width, height), dogbone (inside corners cleared), boundary (moves kept inside the stock), array (copies), ramp (entry instead of plunging), leadinout.")
        try:
            mod = importlib.import_module(DRESSUPS[kind])
        except ImportError as e:
            raise ToolError("unavailable", f"The {kind} dressup lives in FreeCAD's GUI and is not there headless ({e}) — add it in the CAM workbench (adaptive ops enter helically anyway).")
        before = path_stats(list(op.Path.Commands), tc_for(op))
        try:
            if hasattr(mod, "Create"):
                dr = mod.Create(op)
            elif hasattr(mod, "ObjectDressup"):
                # Ramp entry and lead in/out keep their Create with their commands; made as those do.
                dr = d.addObject("Path::FeaturePython", f"{kind.capitalize()}Dressup")
                proxy = mod.ObjectDressup(dr)
                dr.Base = op
                job.Proxy.addOperation(dr, op)
                if hasattr(proxy, "setup"):
                    try:
                        proxy.setup(dr)
                    except TypeError:
                        proxy.setup(dr, True)
            else:
                raise AttributeError("no way to make it here")
        except Exception as e:
            raise ToolError("failed", f"The {kind} dressup would not go on {op.Label}: {e}")
        if dr not in job.Operations.Group:
            try:
                job.Proxy.addOperation(dr, op, True)
            except TypeError:
                job.Proxy.addOperation(dr, op)
        if op in job.Operations.Group:
            job.Operations.removeObject(op)
        if kind == "tags":
            for k, prop, unit in (("width", "Width", "mm"), ("height", "Height", "mm"), ("angle", "Angle", "°")):
                if a.get(k) is not None and prop in dr.PropertiesList:
                    setattr(dr, prop, f"{float(a[k])} {unit}")
            if a.get("count") and hasattr(dr.Proxy, "setup"):
                try:
                    dr.Proxy.setup(dr, int(a["count"]))
                except Exception:
                    pass
        if kind == "boundary" and getattr(dr, "Stock", None) is not None:
            # FreeCAD's boundary starts as the model's own box, which keeps
            # nothing of a path round the outside: made the job's stock (or
            # margin mm round the model) unless props give another.
            js = job.Stock
            m = a.get("margin")
            for k in ("ExtXneg", "ExtXpos", "ExtYneg", "ExtYpos", "ExtZneg", "ExtZpos"):
                if hasattr(dr.Stock, k):
                    v = float(m) if m is not None else (float(getattr(js, k)) if hasattr(js, k) else 0.0)
                    setattr(dr.Stock, k, v + (1.0 if k == "ExtZpos" else 0.0))
        if kind == "dogbone" and a.get("style") and "Style" in dr.PropertiesList:
            dr.Style = a["style"]
        for k, v in (a.get("props") or {}).items():
            if k in dr.PropertiesList:
                setattr(dr, k, v)
        d.recompute()
        cmds = list(dr.Path.Commands) if getattr(dr, "Path", None) is not None else []
        after = path_stats(cmds, tc_for(dr))

        def undo(why):
            job.Operations.removeObject(dr)
            if op not in job.Operations.Group:
                job.Proxy.addOperation(op)
            d.removeObject(dr.Name)
            d.recompute()
            raise ToolError("failed", why + " Nothing was kept.")

        if not cmds:
            undo(f"The {kind} dressup made no path from {op.Label}.")
        if before["cutMm"] > 0 and after["cutMm"] <= 0:
            undo(f"The {kind} dressup left no cutting at all on {op.Label}" + (" — every move is outside its boundary (the stock): the path runs past the stock's edge; give the stock more margin (cam_job stock.margin) or a boundary of your own (props)." if kind == "boundary" else "."))
        out = {"dressup": dr.Label, "kind": kind, "on": op.Label, "before": {k: before[k] for k in ("cutMm", "minutes")}, "after": {k: after[k] for k in ("cutMm", "minutes")}}
        if kind == "tags" and hasattr(dr, "Positions"):
            out["tags"] = len(dr.Positions)
        out["note"] = "cam_post writes the dressed path; cam_sim checks it."
        return out

    def cam_setup(a):
        d = doc()
        job = _job(d, a.get("job"))
        out = {"job": job.Label}
        if a.get("fixtures"):
            fx = [str(x).upper() for x in a["fixtures"]]
            if not all(x in ("G54", "G55", "G56", "G57", "G58", "G59") for x in fx):
                raise ToolError("invalid_input", "fixtures: work offsets G54 … G59 (each one runs the job again in that offset).")
            job.Fixtures = fx
            out["fixtures"] = fx
        if a.get("coolant"):
            mode = {"none": "None", "flood": "Flood", "mist": "Mist"}.get(a["coolant"])
            if mode is None:
                raise ToolError("invalid_input", "coolant: none, flood or mist.")
            n = 0
            for op in job.Operations.Group:
                if "CoolantMode" in op.PropertiesList:
                    op.CoolantMode = mode
                    n += 1
            out["coolant"] = {"mode": mode, "ops": n}
        sh = getattr(job, "SetupSheet", None)
        if sh is not None:
            if a.get("safeHeight") is not None and "SafeHeightOffset" in sh.PropertiesList:
                sh.SafeHeightOffset = f"{float(a['safeHeight'])} mm"
                out["safeHeightOffset"] = a["safeHeight"]
            if a.get("clearance") is not None and "ClearanceHeightOffset" in sh.PropertiesList:
                sh.ClearanceHeightOffset = f"{float(a['clearance'])} mm"
                out["clearanceOffset"] = a["clearance"]
        if a.get("order"):
            ops = list(job.Operations.Group)
            byname = {o.Label: o for o in ops}
            want = [byname[n] for n in a["order"] if n in byname]
            if len(want) != len(ops) or len(set(a["order"])) != len(ops):
                raise ToolError("invalid_input", f"order: every op once — {', '.join(byname)}.")
            job.Operations.Group = want
            out["order"] = [o.Label for o in want]
        d.recompute()
        return out

    return [
        Tool("cam_job", "Start a CAM job for a body: the stock round it (stock { margin mm each side, top mm over, bottom mm under }) and the end mill (tool { diameter mm, feed and plunge mm/min, rpm, number }) — ask the person which mill and feeds; the defaults are gentle. Then cam_op.",
             obj({"body": {"type": "string"}, "tool": {"type": "object"}, "stock": {"type": "object"}, "name": {"type": "string"}}, ["tool"]), cam_job, changes=True),
        Tool("cam_op", "Add an operation to the job and compute it: kind profile (round the outline, side outside|inside), pocket (clear down to the faces given — a pocket's floor), adaptive (adaptive clearing, side inside|outside; finish: true for a finishing pass), face (face the top flat), drill (the holes, every one when faces are left out), engrave (follows base: letters from draft_text, a sketch), helix (holes, helically), slot, deburr (edges chamfered with a V or chamfer tool), vcarve (base letters, a V-bit), thread (thread milling a hole: props ThreadName …), surface / waterline (3D finishing; need OpenCamLib). tool: one of the job's tools by name (cam_tool_add); props { Property: value } sets any of the op's own. stepDown mm, stepOver %, finalDepth mm (z), stockToLeave mm. Returns the cut and rapid lengths, minutes and the depth it reaches; refused with nothing kept when it makes no cutting moves.",
             obj({"job": jobref, "kind": {"type": "string", "enum": list(OPS)}, "faces": faces, "tool": {"type": "string"}, "base": {"type": "array", "items": {"type": "string"}}, "props": {"type": "object"}, "body": {"type": "string"}, "side": {"type": "string", "enum": ["outside", "inside"]}, "finish": {"type": "boolean"}, "stepDown": {"type": "number"}, "stepOver": {"type": "number"}, "finalDepth": {"type": "number"}, "stockToLeave": {"type": "number"}, "name": {"type": "string"}}, ["kind"]),
             cam_op, changes=True),
        Tool("cam_list", "The job: stock, tool, each operation with its cut length, minutes and depth, and the total time. Changes nothing.", obj({"job": jobref}), cam_list),
        Tool("cam_tool", "Change the job's end mill or feeds (diameter, feed, plunge, rpm, number); every operation is computed again.",
             obj({"job": jobref, "diameter": {"type": "number"}, "feed": {"type": "number"}, "plunge": {"type": "number"}, "rpm": {"type": "number"}, "number": {"type": "integer"}}), cam_tool, changes=True),
        Tool("cam_tool_add", "Add a tool to the job: shape endmill | ballend | bullnose | v-bit | chamfer | drill | thread-mill | radius | dovetail | …, diameter mm, angle (V-bit, chamfer), tipDiameter, cornerRadius, flutes, feed, plunge (mm/min), rpm, number, name. Ask the person which tools they have.",
             obj({"job": jobref, "shape": {"type": "string"}, "diameter": {"type": "number"}, "angle": {"type": "number"}, "tipDiameter": {"type": "number"}, "cornerRadius": {"type": "number"}, "flutes": {"type": "integer"},
                  "length": {"type": "number"}, "cuttingLength": {"type": "number"}, "feed": {"type": "number"}, "plunge": {"type": "number"}, "rpm": {"type": "number"}, "number": {"type": "integer"}, "name": {"type": "string"}}, ["diameter"]), cam_tool_add, changes=True),
        Tool("cam_library", "The person's tool set as a file: action export (the job's tools, feeds and speeds to path, JSON) | import (path's tools into the job; replace: true adds ones it already has by name).",
             obj({"job": jobref, "action": {"type": "string", "enum": ["export", "import"]}, "path": {"type": "string"}, "replace": {"type": "boolean"}}), cam_library, changes=True),
        Tool("cam_dressup", "Dress an operation's path: kind tags (holding tabs so the part stays in the stock: count, width, height, angle), dogbone (inside corners cleared: style), boundary (moves kept within the stock, or margin mm round the part), array, ramp / leadinout (where this FreeCAD has them headless); props for the dressup's own.",
             obj({"job": jobref, "op": {"type": "string"}, "kind": {"type": "string"}, "count": {"type": "integer"}, "width": {"type": "number"}, "height": {"type": "number"}, "angle": {"type": "number"}, "style": {"type": "string"}, "margin": {"type": "number"}, "props": {"type": "object"}}, ["op", "kind"]), cam_dressup, changes=True),
        Tool("cam_setup", "The job's setup: fixtures [G54 …] (the job run in each offset), coolant none | flood | mist (every op), safeHeight / clearance offsets mm, order [op labels] to run them in.",
             obj({"job": jobref, "fixtures": {"type": "array", "items": {"type": "string"}}, "coolant": {"type": "string"}, "safeHeight": {"type": "number"}, "clearance": {"type": "number"}, "order": {"type": "array", "items": {"type": "string"}}}), cam_setup, changes=True),
        Tool("cam_remove", "Remove an operation from the job.", obj({"job": jobref, "op": {"type": "string"}}, ["op"]), cam_remove, changes=True),
        Tool("cam_sim", "Simulate the job's operations on the stock (3-axis, from above): what each removes, rapid moves that go through stock (a crash), cuts into the part (gouges) or under the stock, and what is left over the part — with a picture from above coloured by what is left on the part (blue gouged, green right, red left over). verdict fix or clear. resolution mm per cell (a sixth of the tool by default). Before cam_post.",
             obj({"job": jobref, "resolution": {"type": "number"}, "inline": {"type": "boolean"}, "path": {"type": "string"}}), cam_sim),
        Tool("cam_post", "Write the job's G-code with FreeCAD's post-processor for the machine's controller (post grbl — the default —, linuxcnc, mach3_mach4, marlin, smoothie, centroid, generic) to path (the session's folder). Returns lines, cut length, minutes, and warnings for cuts below the stock or far outside it. Running it is the person's.",
             obj({"job": jobref, "post": {"type": "string", "enum": POSTS}, "path": {"type": "string"}}), cam_post),
    ]
