# SPDX-License-Identifier: LGPL-2.1-or-later
# Parts milled from stock: can the setup's tool directions reach every face
# (one not reachable from any is an undercut), are the inside vertical
# corners as round as the least tool, are holes and pockets within the
# tool's reach and not too deep for their width, are the walls thick enough
# for the material, does the part fit the stock. Then STEP and a setup
# sheet. A face lying on the stock's own outside counts as the stock's.
# Rules of thumb, said as assumed: the machinist's own figures win.

import math
import os

import FreeCAD
import Part

from .. import geometry as geo
from .. import store
from ..protocol import Tool, ToolError

GROUP = "mill"
INFO = "Milled parts: reach from the setup's tool directions (undercuts), inside corners, deep holes and pockets, thin walls, stock; STEP and a setup sheet."

V = FreeCAD.Vector
DIRS = {"+x": V(1, 0, 0), "-x": V(-1, 0, 0), "+y": V(0, 1, 0), "-y": V(0, -1, 0), "+z": V(0, 0, 1), "-z": V(0, 0, -1)}
WALL = {"aluminium": 1.0, "aluminum": 1.0, "steel": 0.8, "stainless": 0.8, "brass": 0.8, "copper": 1.0, "titanium": 1.0, "plastic": 1.5, "pom": 1.5, "wood": 3.0}
DEEP = 4.0  # a hole or pocket deeper than this many times its width wants a long, thin tool


def obj(props, required=()):
    return {"type": "object", "properties": props, "required": list(required), "additionalProperties": False}


def _samples(f, solid, k=3):
    """Points on a face with its outward normal (out of the material)."""
    u0, u1, v0, v1 = f.ParameterRange
    out = []
    for i in range(k):
        for j in range(k):
            u = u0 + (u1 - u0) * (i + 0.5) / k
            v = v0 + (v1 - v0) * (j + 0.5) / k
            try:
                p = f.valueAt(u, v)
                if not f.isInside(p, 1e-6, True):
                    continue
                n = f.normalAt(u, v)
            except Exception:
                continue
            if n.Length < 1e-9:
                continue
            n.normalize()
            if solid.isInside(p + n * 0.02, 1e-6, False):
                n = n * -1
            out.append((p, n))
    return out


def _share(solid, p, axis, eps=0.05):
    """How much of a small circle round p, square to axis, is material, and
    a point on the circle off the material (None when there is none)."""
    a = axis.cross(V(1, 0, 0)) if abs(axis.x) < 0.9 else axis.cross(V(0, 1, 0))
    a.normalize()
    b = axis.cross(a)
    hits, empty = 0, []
    for i in range(24):
        q = p + a * (eps * math.cos(i * math.pi / 12)) + b * (eps * math.sin(i * math.pi / 12))
        if solid.isInside(q, 1e-6, True):
            hits += 1
        else:
            empty.append(q)
    return hits / 24, (empty[len(empty) // 2] if empty else None)


def check(shape, dirs, tool, reach, wall, stock):
    solid = shape.Solids[0] if shape.Solids else shape
    bb = shape.BoundBox
    findings = []

    def find(rule, severity, says, value=None, limit=None, at=None, faces=None):
        findings.append({"rule": rule, "severity": severity, **({"value": round(value, 3)} if value is not None else {}), **({"limit": limit} if limit is not None else {}), **({"at": [round(x, 2) for x in at]} if at else {}), **({"faces": faces} if faces else {}), "says": says})

    def on_stock(f):
        if type(f.Surface).__name__ != "Plane":
            return False
        fb = f.BoundBox
        return any(abs(lo - hi) < 1e-4 and (abs(lo - b0) < 1e-4 or abs(lo - b1) < 1e-4) for lo, hi, b0, b1 in ((fb.XMin, fb.XMax, bb.XMin, bb.XMax), (fb.YMin, fb.YMax, bb.YMin, bb.YMax), (fb.ZMin, fb.ZMax, bb.ZMin, bb.ZMax)))

    # Reach: every sample point seen from some direction, within the tool's reach.
    pts = []
    for i, f in enumerate(shape.Faces):
        if on_stock(f):
            continue
        for p, n in _samples(f, solid):
            pts.append((i, p, n))
    seen = [False] * len(pts)
    deep = [None] * len(pts)
    for name, d in dirs.items():
        ext = max(v.Point.dot(d) for v in shape.Vertexes)
        idx = [k for k, (_, p, n) in enumerate(pts) if not seen[k] and n.dot(d) >= -1e-3]
        rays = [(pts[k][1] + pts[k][2] * 0.02, d * -1) for k in idx]
        hits = geo._ray_hits(shape, rays, bb.DiagonalLength + 1) if rays else []
        for k, h in zip(idx, hits):
            if h is None:
                depth = ext - pts[k][1].dot(d)
                if depth <= reach + 1e-6:
                    seen[k] = True
                    deep[k] = None
                elif deep[k] is None or depth < deep[k][0]:
                    deep[k] = (depth, name)
    by_face = {}
    for k, (i, p, n) in enumerate(pts):
        st = by_face.setdefault(i, {"seen": 0, "all": 0, "deep": None, "at": p})
        st["all"] += 1
        st["seen"] += seen[k]
        if not seen[k] and deep[k]:
            st["deep"] = deep[k]
    under, far = [], []
    for i, st in sorted(by_face.items()):
        if st["seen"] == st["all"]:
            continue
        (far if st["deep"] else under).append((i, st))
    if under:
        find("undercut", "error", f"{len(under)} face{'s' if len(under) > 1 else ''} no tool direction reaches ({', '.join(dirs)}): an undercut — add a direction (setup axes \"3+2\"), a second setup, or change the shape.", faces=[f"Face{i + 1}" for i, _ in under[:12]], at=list(under[0][1]["at"]))
    for i, st in far[:4]:
        dep, name = st["deep"]
        find("reach", "error", f"Face{i + 1} is {dep:.1f} mm in from {name}, past the tool's {reach:g} mm reach: a longer tool, or come at it from another side.", dep, reach, list(st["at"]), [f"Face{i + 1}"])

    # Inside corners along each direction, and holes along it.
    r_tool = tool / 2
    corners, holes = [], []
    for name, d in dirs.items():
        cand = []
        for e in shape.Edges:
            if type(e.Curve).__name__ not in ("Line", "LineSegment"):
                continue
            a, b = e.Vertexes[0].Point, e.Vertexes[-1].Point
            t = b - a
            if t.Length < 1e-6:
                continue
            t.normalize()
            if abs(abs(t.dot(d)) - 1) > 1e-6:
                continue
            mid = (a + b) * 0.5
            share, off = _share(solid, mid, d)
            if share > 0.55 and off is not None:
                cand.append((mid, off))
        # Only a corner the tool can come down into along d: from beside it,
        # off the material, nothing in the way out along d.
        if cand:
            free = geo._ray_hits(shape, [(off, d * -1) for _, off in cand], bb.DiagonalLength + 1)
            corners += [(0.0, mid, name) for (mid, _), h in zip(cand, free) if h is None]
        for i, f in enumerate(shape.Faces):
            srf = f.Surface
            if type(srf).__name__ != "Cylinder" or abs(abs(srf.Axis.dot(d)) - 1) > 1e-6:
                continue
            u0, u1, v0, v1 = f.ParameterRange
            p = f.valueAt((u0 + u1) / 2, (v0 + v1) / 2)
            to_axis = srf.Center + srf.Axis * (p - srf.Center).dot(srf.Axis) - p
            if solid.isInside(p + to_axis * (0.02 / max(to_axis.Length, 1e-9)), 1e-6, False):
                continue  # material on the axis side: a boss, not a hole or round corner
            full = (u1 - u0) > 2 * math.pi - 1e-3 or len([w for w in f.Wires]) == 2
            fb = f.BoundBox
            length = abs(fb.XLength * d.x) + abs(fb.YLength * d.y) + abs(fb.ZLength * d.z)
            if full:
                holes.append((2 * srf.Radius, length, p, name, i))
            elif srf.Radius < r_tool - 1e-6:
                corners.append((srf.Radius, p, name))
    done = set()
    for rad, p, name in corners:
        key = (round(p.x, 1), round(p.y, 1), round(p.z, 1))
        if key in done:
            continue
        done.add(key)
        if len(done) > 6:
            break
        find("inside-corner", "warning", f"An inside corner of radius {rad:.2f} mm along {name}, tighter than the least tool's {r_tool:.2f} mm: round it to {r_tool * 1.1:.2f} mm or more (a little over the tool's), or EDM it.", rad, round(r_tool, 3), [p.x, p.y, p.z])
    seen_h = set()
    for dia, length, p, name, i in holes:
        if i in seen_h:
            continue
        seen_h.add(i)
        if dia < tool - 1e-6:
            find("small-hole", "warning", f"A hole Ø{dia:.2f} mm, under the least end mill's Ø{tool:g}: drill or ream it.", dia, tool, [p.x, p.y, p.z], [f"Face{i + 1}"])
        if length > reach + 1e-6:
            find("deep-hole", "error", f"A hole Ø{dia:.2f} mm {length:.1f} mm deep, past the tool's {reach:g} mm reach: drill it from both sides, or a longer drill.", length, reach, [p.x, p.y, p.z], [f"Face{i + 1}"])
        elif length > DEEP * dia:
            find("deep-hole", "warning", f"A hole Ø{dia:.2f} mm {length:.1f} mm deep, over {DEEP:g} times its width: a long, thin tool chatters and breaks — drill it, or make it shallower or wider.", length / dia, DEEP, [p.x, p.y, p.z], [f"Face{i + 1}"])

    # Pockets: floors facing a direction, below the stock's top that way, walled round.
    for name, d in dirs.items():
        ext = max(v.Point.dot(d) for v in shape.Vertexes)
        a = d.cross(V(1, 0, 0)) if abs(d.x) < 0.9 else d.cross(V(0, 1, 0))
        a.normalize()
        b = d.cross(a)
        for i, f in enumerate(shape.Faces):
            if type(f.Surface).__name__ != "Plane":
                continue
            ss = _samples(f, solid, 1) or _samples(f, solid, 2)
            if not ss:
                continue
            p, n = ss[0]
            if n.dot(d) < 1 - 1e-6:
                continue
            depth = ext - p.dot(d)
            if depth < 1e-3:
                continue
            q = p + d * min(0.1, depth / 2)
            hits = geo._ray_hits(shape, [(q, -a), (q, a), (q, -b), (q, b)], bb.DiagonalLength + 1)
            if any(h is None for h in hits):
                continue  # open to a side: a step, not a pocket
            width = min(hits[0] + hits[1], hits[2] + hits[3])
            if depth > reach + 1e-6:
                find("deep-pocket", "error", f"A pocket {depth:.1f} mm deep from {name}, past the tool's {reach:g} mm reach: a longer tool, or make it shallower.", depth, reach, [p.x, p.y, p.z], [f"Face{i + 1}"])
            elif depth > DEEP * width:
                find("deep-pocket", "warning", f"A pocket {width:.1f} mm wide and {depth:.1f} mm deep, over {DEEP:g} times its width: the tool that fits is too long to cut well — widen it or make it shallower.", depth / width, DEEP, [p.x, p.y, p.z], [f"Face{i + 1}"])

    # Thin walls.
    w = geo.least_wall(shape)
    if w.get("least") is not None:
        for run in geo.thin_runs(w["readings"], wall, 2.5 * w["spacing"])[:3]:
            find("thin-wall", "warning" if run["least"] >= wall / 2 else "error", f"A wall {run['least']:.2f} mm thick, under {wall:g} mm for the material: it chatters and bends under the tool — thicken it.", run["least"], wall, run["at"])

    # Stock.
    size = sorted([bb.XLength, bb.YLength, bb.ZLength])
    if stock:
        sx, sy, sz = (float(v) for v in stock)
        if not (bb.XLength <= sx + 1e-6 and bb.YLength <= sy + 1e-6 and bb.ZLength <= sz + 1e-6):
            fits_turned = all(p <= q + 1e-6 for p, q in zip(size, sorted([sx, sy, sz])))
            find("stock", "error" if not fits_turned else "warning", f"{bb.XLength:.1f} x {bb.YLength:.1f} x {bb.ZLength:.1f} mm does not fit the {sx:g} x {sy:g} x {sz:g} mm stock as it stands" + (": it fits turned — turn the stock or the part." if fits_turned else ": bigger stock."), max(size), max(sx, sy, sz))
    order = {"error": 0, "warning": 1}
    findings.sort(key=lambda f: order[f["severity"]])
    return findings, w.get("least")


def tools(session):
    s = session

    def mill_setup(o):
        setup = store.setup_of(s.doc, o.Label, "mill")
        if not setup:
            raise ToolError("invalid_state", f"{o.Label} is in no mill setup: setup add {{ type: \"mill\", bodies: [\"{o.Label}\"], settings: {{ axes, minTool }} }} once the person says it is milled.")
        return setup

    def gather(a, o, setup):
        assumed = {}
        if a.get("directions"):
            names = [str(x).lower() for x in a["directions"]]
            bad = [x for x in names if x not in DIRS]
            if bad:
                raise ToolError("invalid_input", f"directions: from {', '.join(DIRS)} — the side the tool comes from.")
        else:
            axes = str(setup.get("axes") or "3")
            if not setup.get("axes"):
                assumed["axes"] = "3 (the setup does not say): the tool from +z only"
            names = ["+z"] if axes == "3" else list(DIRS)
        tool = float(a.get("tool") or setup.get("minTool") or 0) or None
        if tool is None:
            tool = 3.0
            assumed["tool"] = "Ø3 mm end mill (the setup's minTool is not set)"
        reach = a.get("reach")
        if reach is None:
            reach = max(5 * tool, 20.0)
            assumed["reach"] = f"{reach:g} mm, five times the tool or 20 mm (a standard length)"
        mat = (a.get("material") or "").lower()
        if not mat:
            mat = "aluminium"
            assumed["material"] = "aluminium (not said)"
        if mat not in WALL:
            raise ToolError("invalid_input", f"material: one of {', '.join(WALL)}.")
        stock = a.get("stock")
        bb = o.Shape.BoundBox
        if not stock:
            stock = [round(bb.XLength + 4, 1), round(bb.YLength + 4, 1), round(bb.ZLength + 2, 1)]
            assumed["stock"] = f"{stock[0]:g} x {stock[1]:g} x {stock[2]:g} mm: the part's box and 2 mm a side, 1 mm top and bottom"
        return {n: DIRS[n] for n in names}, tool, float(reach), mat, stock, assumed

    def run_check(a):
        o = s.body(a.get("body"))
        setup = mill_setup(o)
        dirs, tool, reach, mat, stock, assumed = gather(a, o, setup)
        findings, least = check(o.Shape, dirs, tool, reach, WALL[mat], stock)
        verdict = "fix" if any(f["severity"] == "error" for f in findings) else ("pass" if not findings else "pass with warnings")
        out = {
            "body": o.Label, "verdict": verdict, "directions": list(dirs), "tool": tool, "reach": reach, "material": mat, "stock": stock,
            "findings": findings[:10], "leastWall": least, "setup": f"{setup['name']} ({setup['id']})",
            **({"grade": setup["grade"]} if setup.get("grade") else {}),
        }
        if assumed:
            out["assumed"] = assumed
        return o, setup, out

    def mill_check(a):
        return run_check(a)[2]

    def mill_export(a):
        o, setup, r = run_check(a)
        base = os.path.join(s.out_dir, a.get("name") or o.Label.replace(" ", "_") + "-mill")
        os.makedirs(os.path.dirname(base) or ".", exist_ok=True)
        step = base + ".step"
        o.Shape.exportStep(step)
        back = Part.read(step)
        bb = o.Shape.BoundBox
        lines = [
            f"# Setup sheet: {o.Label}",
            "",
            f"- Setup: {r['setup']}, axes {setup.get('axes', '3')}" + (f", IT grade {setup['grade']}" if setup.get("grade") else ""),
            f"- Part: {bb.XLength:.2f} x {bb.YLength:.2f} x {bb.ZLength:.2f} mm, {o.Shape.Volume / 1000:.2f} cm³",
            f"- Stock: {' x '.join(f'{v:g}' for v in r['stock'])} mm, {r['material']}",
            f"- Tool directions: {', '.join(r['directions'])}",
            f"- Least end mill: Ø{r['tool']:g} mm, reach {r['reach']:g} mm",
            f"- Least wall read: {r['leastWall']} mm" if r["leastWall"] is not None else "- Least wall: not read",
            f"- Verdict: {r['verdict']}",
            "",
            "## Findings",
            "",
        ]
        if r["findings"]:
            for f in r["findings"]:
                lines.append(f"- **{f['severity']}** {f['rule']}: {f['says']}" + (f" ({', '.join(f['faces'])})" if f.get("faces") else ""))
        else:
            lines.append("- None.")
        if r.get("assumed"):
            lines += ["", "## Assumed", ""] + [f"- {k}: {v}" for k, v in r["assumed"].items()]
        lines += ["", f"STEP: {os.path.basename(step)}", ""]
        sheet = base + "-setup.md"
        with open(sheet, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        return {
            "body": o.Label, "verdict": r["verdict"],
            "step": {"path": step, "bytes": os.path.getsize(step), "volumeDiff": round(abs(back.Volume - o.Shape.Volume), 3), "solids": len(back.Solids)},
            "setupSheet": {"path": sheet, "bytes": os.path.getsize(sheet)},
            "findings": len(r["findings"]),
        }

    body = {"type": "string", "description": "A body's name; may be left out when there is one."}
    schema = obj({
        "body": body,
        "directions": {"type": "array", "items": {"type": "string", "enum": list(DIRS)}, "description": "Sides the tool comes from; by default the setup's axes: 3 is +z, 3+2 all six."},
        "tool": {"type": "number", "description": "The least end mill's diameter, mm (the setup's minTool)."},
        "reach": {"type": "number", "description": "How deep the tool reaches, mm."},
        "material": {"type": "string", "description": ", ".join(WALL)},
        "stock": {"type": "array", "items": {"type": "number"}, "description": "[x, y, z] mm, as the part stands."},
        "name": {"type": "string"},
    })
    check_schema = {**schema, "properties": {k: v for k, v in schema["properties"].items() if k != "name"}}
    return [
        Tool(
            "mill_check",
            "Can a body in a mill setup be milled: faces no tool direction reaches (undercuts, named), faces past the tool's reach, inside corners along a direction rounder than the least tool can make, holes and pockets past the reach or over 4 times as deep as wide, thin walls for the material, the stock. Directions from the setup's axes (3: +z only; 3+2: all six) unless given. Verdict fix, pass with warnings or pass. Trap: tool, reach, material and stock the setup leaves out are assumed and said so; a face on the stock's outside counts as the stock's.",
            check_schema,
            mill_check,
        ),
        Tool("mill_export", "STEP of a milled body (read back) and a setup sheet in Markdown beside it: stock, directions, tools, the check's findings and what was assumed. Takes mill_check's inputs.", schema, mill_export),
    ]
