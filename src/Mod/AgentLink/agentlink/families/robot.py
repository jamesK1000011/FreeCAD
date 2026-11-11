# SPDX-License-Identifier: LGPL-2.1-or-later
# FreeCAD's Robot workbench headless: a six-axis robot (KUKA models where
# this FreeCAD ships them, a kinematics file of your own, or the built-in
# Puma 560), moved by its axes or its tool point, trajectories of waypoints
# or along a shape's edges, a run of a trajectory checked for reach and the
# axes' ranges, and KUKA KRL out.

import os

import FreeCAD
import Robot  # noqa: F401 — the family stands on the Robot module; left out without it

from ..protocol import Tool, ToolError
from ..tools import obj
from . import picks

GROUP = "robot"
INFO = "Robot: six-axis robots (KUKA models, your own kinematics), moves by axes or tool point, trajectories from waypoints or edges, reach checked along them, KUKA KRL out."

V = FreeCAD.Vector
MODELS = ("kr16", "kr125", "kr210", "kr500")


def bad(msg):
    return ToolError("invalid_input", msg)


def lib_file(model, ext):
    roots = []
    for base in (FreeCAD.getResourceDir(), FreeCAD.getHomePath(), os.path.join(FreeCAD.getHomePath(), "share")):
        roots += [os.path.join(base, "Mod", "Robot", "Lib", "Kuka"), os.path.join(base, "data", "Mod", "Robot", "Lib", "Kuka")]
    names = {"kr16": "kr16", "kr125": "kr125_3", "kr210": "kr210", "kr500": "kr500_1"}
    for r in roots:
        for stem in (names.get(model, model), model):
            f = os.path.join(r, f"{stem}.{ext}")
            if os.path.isfile(f):
                return f
    return None


def placement(a, what):
    at = picks.vec3(a.get("at", [0, 0, 0]), f"{what}.at")
    if a.get("ypr") is not None:
        y, p, r = (float(x) for x in a["ypr"])
        rot = FreeCAD.Rotation(y, p, r)
    elif a.get("axis") is not None:
        rot = FreeCAD.Rotation(picks.vec3(a["axis"], f"{what}.axis"), float(a.get("angle", 0)))
    else:
        rot = FreeCAD.Rotation()
    return FreeCAD.Placement(at, rot)


def pl_out(pl):
    y, p, r = pl.Rotation.toEuler()
    return {"at": [round(c, 3) for c in pl.Base], "ypr": [round(y, 3), round(p, 3), round(r, 3)]}


def axes_of(o):
    return [round(float(getattr(o, f"Axis{i}")), 3) for i in range(1, 7)]


def tools(session):
    s = session
    doc = lambda: s.doc  # noqa: E731

    def robots():
        return [o for o in doc().Objects if o.isDerivedFrom("Robot::RobotObject")]

    def robot(ref):
        if not ref:
            rs = robots()
            if len(rs) == 1:
                return rs[0]
            raise bad("robot: which — " + (", ".join(o.Label for o in rs) or "there are none (robot_new)") + ".")
        o = picks.find(doc(), ref, "robot")
        if not o.isDerivedFrom("Robot::RobotObject"):
            raise bad(f"{o.Label} is not a robot.")
        return o

    def trajectory(ref):
        o = picks.find(doc(), ref, "trajectory")
        if not hasattr(o, "Trajectory"):
            raise bad(f"{o.Label} is not a trajectory.")
        return o

    def state(o):
        return {"robot": o.Label, "axes": axes_of(o), "tcp": pl_out(o.Tcp), **({"error": o.Error} if getattr(o, "Error", "") else {})}

    def robot_new(a):
        model = a.get("model", "kr16")
        o = doc().addObject("Robot::RobotObject", "Robot")
        o.Label = s.unique(a.get("name") or f"Robot {model}", o)
        kin = a.get("kinematics")
        how = None
        if kin:
            if not os.path.isfile(kin):
                raise ToolError("not_found", f"kinematics: {kin} — no such file (a CSV of the six axes' a, alpha, d, theta, rotDir, maxAngle, minAngle, velocity).")
            o.RobotKinematicFile = kin
            how = f"kinematics from {os.path.basename(kin)}"
        elif model in MODELS:
            f = lib_file(model, "csv")
            if f:
                o.RobotKinematicFile = f
                w = lib_file(model, "wrl")
                if w:
                    o.RobotVrmlFile = w
                how = f"KUKA {model.upper()} (FreeCAD's library)"
        elif model != "puma":
            raise bad(f"model: {', '.join(MODELS)} or puma; or kinematics, a CSV of your own.")
        if how is None:
            if not a.get("name"):
                o.Label = s.unique("Robot Puma 560", o)
            how = "the built-in Puma 560" + ("" if model == "puma" else f" — this FreeCAD ships no {model.upper()} file; give kinematics for the real one")
        if a.get("at") is not None or a.get("ypr") is not None:
            o.Base = placement(a, "robot")
        doc().recompute()
        s.snapshot("robot_new")
        return {**state(o), "model": how, "step": len(s.steps)}

    def robot_move(a):
        o = robot(a.get("robot"))
        if a.get("axes") is not None:
            ax = a["axes"]
            if not (isinstance(ax, list) and len(ax) == 6):
                raise bad("axes: six angles in degrees, axis 1 … 6.")
            for i, v in enumerate(ax, start=1):
                setattr(o, f"Axis{i}", float(v))
        elif a.get("tcp") is not None:
            o.Error = ""
            o.Tcp = placement(a["tcp"], "tcp")
        elif a.get("home"):
            for i in range(1, 7):
                setattr(o, f"Axis{i}", 0.0)
        else:
            raise bad("axes [six degrees], tcp { at, ypr | axis + angle }, or home: true.")
        doc().recompute()
        out = state(o)
        if a.get("tcp") is not None:
            want = placement(a["tcp"], "tcp")
            miss = (o.Tcp.Base - want.Base).Length
            out["reached"] = miss < 0.1 and not getattr(o, "Error", "")
            if not out["reached"]:
                out["off_mm"] = round(miss, 3)
        s.snapshot("robot_move")
        return {**out, "step": len(s.steps)}

    def robot_path(a):
        name = a.get("name") or "Trajectory"
        if a.get("edges"):
            o = doc().addObject("Robot::Edge2TrajectoryObject", "Edge2Trajectory")
            refs = {}
            for r in a["edges"]:
                label, sub, _ = picks.resolve(s, r)
                x = picks.find(doc(), label)
                subs = [sub] if sub else [f"Edge{i + 1}" for i in range(len(x.Shape.Edges))]
                refs.setdefault(x, []).extend(subs)
            if len(refs) != 1:
                doc().removeObject(o.Name)
                raise bad("edges: of one object (FreeCAD's edge trajectory follows one shape).")
            o.Source = next(iter(refs.items()))
            o.SegValue = float(a.get("deviation", 0.5))
            o.UseRotation = bool(a.get("useRotation", False))
        else:
            wps = a.get("waypoints") or []
            if len(wps) < 2:
                raise bad("waypoints: two or more { at, ypr, type LIN | PTP | CIRC, speed mm/s, cont } — or edges, a shape's edges to follow.")
            pts = []
            for i, w in enumerate(wps):
                kind = str(w.get("type", "LIN")).upper()
                if kind not in ("LIN", "PTP", "CIRC", "WAIT"):
                    raise bad(f"waypoints.{i}.type: LIN, PTP, CIRC or WAIT.")
                wp = Robot.Waypoint(placement(w, f"waypoints.{i}"), kind, w.get("name") or "Pt")
                wp.Velocity = float(w.get("speed", 1000))
                wp.Cont = bool(w.get("cont", False))
                pts.append(wp)
            o = doc().addObject("Robot::TrajectoryObject", "Trajectory")
            o.Trajectory = Robot.Trajectory(pts)
        o.Label = s.unique(name, o)
        doc().recompute()
        tr = o.Trajectory
        s.snapshot("robot_path")
        return {"trajectory": o.Label, "waypoints": len(tr.Waypoints), "length": round(tr.Length, 2), "seconds": round(tr.Duration, 3), "step": len(s.steps)}

    def robot_run(a):
        o = robot(a.get("robot"))
        t = trajectory(a.get("trajectory"))
        tr = t.Trajectory
        if tr.Duration <= 0:
            raise ToolError("invalid_state", f"{t.Label} has no duration: give it waypoints with speeds.")
        n = int(a.get("samples", 100))
        if not 2 <= n <= 5000:
            raise bad("samples: 2 … 5000.")
        before = axes_of(o)
        lows, highs, misses, worst = [1e9] * 6, [-1e9] * 6, [], 0.0
        base = t.Base if hasattr(t, "Base") else FreeCAD.Placement()
        for k in range(n):
            time = tr.Duration * k / (n - 1)
            want = base.multiply(tr.position(time))
            o.Error = ""
            o.Tcp = want
            miss = (o.Tcp.Base - want.Base).Length
            worst = max(worst, miss)
            if miss > 0.1 or getattr(o, "Error", ""):
                misses.append({"t": round(time, 3), "at": [round(c, 2) for c in want.Base], "off_mm": round(miss, 3), **({"error": o.Error} if o.Error else {})})
            for i, v in enumerate(axes_of(o)):
                lows[i], highs[i] = min(lows[i], v), max(highs[i], v)
        for i, v in enumerate(before, start=1):
            setattr(o, f"Axis{i}", v)
        doc().recompute()
        out = {"robot": o.Label, "trajectory": t.Label, "seconds": round(tr.Duration, 3), "samples": n,
               "axes": [{"axis": i + 1, "from": round(lows[i], 2), "to": round(highs[i], 2)} for i in range(6)],
               "verdict": "reaches all" if not misses else f"misses {len(misses)} of {n} points"}
        if misses:
            out["misses"] = misses[:20]
            out["worst_mm"] = round(worst, 3)
            out["note"] = "Out of reach or past an axis limit there: move the work closer, turn the tool, or set the robot's base (robot_new at)."
        return out

    def robot_export(a):
        o = robot(a.get("robot"))
        t = trajectory(a.get("trajectory"))
        p = a.get("path") or f"{t.Label.replace(' ', '_')}.src"
        p = p if os.path.isabs(p) else os.path.join(s.out_dir, p)
        if not p.lower().endswith(".src"):
            p += ".src"
        os.makedirs(os.path.dirname(p), exist_ok=True)
        try:
            import KukaExporter

            (KukaExporter.ExportFullSub if a.get("full") else KukaExporter.ExportCompactSub)(o, t, p)
        except Exception as e:
            raise ToolError("failed", f"KRL export failed: {e}") from None
        with open(p, encoding="utf-8", errors="replace") as f:
            text = f.read()
        return {"path": p, "lines": text.count("\n"), "moves": text.count("LIN ") + text.count("PTP "),
                "note": "KUKA KRL. Check it in the controller's simulator before running; running it is the person's."}

    tcp = {"type": "object", "description": "{ at [x,y,z], ypr [yaw, pitch, roll] | axis + angle }"}
    return [
        Tool("robot_new", "A six-axis robot: model kr16 | kr125 | kr210 | kr500 (KUKA, where FreeCAD ships them) | puma; kinematics: a CSV of your own; at, ypr its base.",
             obj({"model": {"type": "string"}, "kinematics": {"type": "string"}, "at": {"type": "array"}, "ypr": {"type": "array"}, "name": {"type": "string"}}), robot_new, changes=True),
        Tool("robot_move", "Move a robot: axes [six degrees], tcp (its tool point: inverse kinematics; reached says if it got there), or home.",
             obj({"robot": {"type": "string"}, "axes": {"type": "array"}, "tcp": tcp, "home": {"type": "boolean"}}), robot_move, changes=True),
        Tool("robot_path", "A trajectory: waypoints [{ at, ypr, type LIN | PTP | CIRC | WAIT, speed mm/s, cont }] — or edges (\"Body.Edge3\", or shapes) to follow, deviation mm. Answers length and seconds.",
             obj({"waypoints": {"type": "array"}, "edges": {"type": "array", "items": {"type": "string"}}, "deviation": {"type": "number"}, "useRotation": {"type": "boolean"}, "name": {"type": "string"}}), robot_path, changes=True),
        Tool("robot_run", "Run a trajectory on a robot (samples along it): whether it reaches every point, where it does not, and each axis's range. The robot is left as it was.",
             obj({"robot": {"type": "string"}, "trajectory": {"type": "string"}, "samples": {"type": "integer"}}, ["trajectory"]), robot_run),
        Tool("robot_export", "Write a trajectory as KUKA KRL (.src): compact, or full: true.",
             obj({"robot": {"type": "string"}, "trajectory": {"type": "string"}, "path": {"type": "string"}, "full": {"type": "boolean"}}, ["trajectory"]), robot_export),
    ]
