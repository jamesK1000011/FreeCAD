# SPDX-License-Identifier: LGPL-2.1-or-later
# Screw threads: the standards' sizes, what size a cylindrical face is, and
# a thread on a hole through FreeCAD's own PartDesign Hole (a cosmetic
# thread — the hole at its tapping size, the thread recorded — or a
# modelled one, cut into the part).

import math

import FreeCAD
import Part

from .. import facepick
from .. import geometry as geo
from .. import store
from .. import threads_table as tt
from ..protocol import Tool, ToolError
from ..tools import obj

GROUP = "thread"
INFO = "Screw threads: metric and Unified sizes from the standards, what size a hole or shaft is, and threads cut on holes."

V = FreeCAD.Vector
HOLE_TYPE = {"metric": "ISOMetricProfile", "metric-fine": "ISOMetricFineProfile", "unc": "UNC", "unf": "UNF"}


def _r(x, n=3):
    return round(float(x), n) + 0.0


def cylinder_of(shape, face):
    """Diameter, inside (a hole) or outside (a shaft), axis, and the two
    ends along the axis of a cylindrical face."""
    s = face.Surface
    if not isinstance(s, Part.Cylinder):
        raise ToolError("invalid_input", "faces: that face is not cylindrical — pick the hole's or shaft's wall ({ near, cylinder: true }).")
    A = V(s.Axis)
    A.normalize()
    C = V(s.Center)
    u0, u1, v0, v1 = face.ParameterRange
    um, vm = (u0 + u1) / 2, (v0 + v1) / 2
    p = face.valueAt(um, vm)
    n = face.normalAt(um, vm)
    radial = (p - C) - A * ((p - C).dot(A))
    inside = n.dot(radial) < 0
    ts = sorted((face.valueAt(um, v) - C).dot(A) for v in (v0, v1))
    return {"diameter": 2 * s.Radius, "inside": inside, "axis": A, "centre": C, "t": ts, "length": ts[1] - ts[0]}


def _entry(shape, cyl, prefer=None):
    """The open end of a hole and the direction into it."""
    A, C = cyl["axis"], cyl["centre"]
    ends = []
    for i, t in enumerate(cyl["t"]):
        other = cyl["t"][1 - i]
        out_dir = A * (1 if t > other else -1)
        e = C + A * t
        beyond = e + out_dir * 0.3
        ends.append({"at": e, "out": out_dir, "open": not shape.isInside(beyond, 0.001, True)})
    opens = [e for e in ends if e["open"]] or ends
    if prefer is not None:
        p = V(*prefer)
        return min(opens, key=lambda e: (e["at"] - p).Length)
    return max(opens, key=lambda e: (e["at"].z, e["at"].x, e["at"].y))


def _row(size):
    r = tt.lookup(size)
    if r is None:
        raise ToolError("not_found", f"size {size!r}: not a size here — M1.6 … M24 (coarse), M10x1.25 (fine), #4-40 … 1/2-13 (UNC), #4-48 … 1/2-20 (UNF); thread_lookup lists them.")
    return r


def tools(session):
    s = session

    def thread_lookup(a):
        if a.get("size"):
            r = _row(a["size"])
            return {"thread": r, "units": "mm", "note": "Basic sizes (ISO 68-1 / ASME B1.1 profile); tap drills are the usual ones. A printed hole is better tapped or fitted with an insert than printed threaded under M5."}
        series = (a.get("series") or "all").lower()
        if series not in tt.SERIES + ("all",):
            raise ToolError("invalid_input", f"series: {', '.join(tt.SERIES)} or all.")
        rows = tt.table(series)
        if a.get("near") is not None:
            d = float(a["near"])
            rows = sorted(rows, key=lambda r: abs(r["major"] - d))[:6]
        return {"threads": [{k: r[k] for k in ("size", "major", "pitch", "minorInt", "tapDrill")} for r in rows], "units": "mm", "columns": "size, major, pitch, internal minor, tap drill"}

    def thread_identify(a):
        o = s.body(a.get("body"))
        q = dict(a.get("faces") or {})
        q.setdefault("cylinder", True)
        picked = facepick.pick(o.Shape, q, "faces")
        name, face = picked[0]
        cyl = cylinder_of(o.Shape, face)
        d = cyl["diameter"]
        near = tt.nearest(d, cyl["inside"], 4)
        out = {
            "body": o.Label, "face": name, "diameter": _r(d), "kind": "hole" if cyl["inside"] else "shaft",
            "length": _r(cyl["length"], 2), "axis": [_r(x) for x in cyl["axis"]],
            "nearest": near,
        }
        if len(picked) > 1:
            out["alsoPicked"] = [n for n, _ in picked[1:]]
        t = tt.tie(near)
        if t and not a.get("pitch"):
            out["needs"] = "pitch"
            out["ask"] = f"Same diameter for pitches {', '.join(str(x['pitch']) for x in t)} mm ({', '.join(x['size'] for x in t)}): ask which, or measure it — thread_identify {{ pitch }} picks."
        elif a.get("pitch"):
            m = [x for x in near if abs(x["pitch"] - float(a["pitch"])) < 0.01]
            out["best"] = m[0] if m else None
            if not m:
                out["note"] = "No near size has that pitch."
        else:
            out["best"] = near[0]
        if out.get("best") and abs(out["best"]["off"]) > 0.3:
            out["note"] = f"{out['best']['off']:+.2f} mm off the nearest: maybe not a thread size — a clearance hole, a press fit or a print's shrinkage."
        return out

    def thread_cut(a):
        o = s.body(a.get("body"))
        mode = a.get("mode", "cosmetic")
        if mode not in ("cosmetic", "modelled"):
            raise ToolError("invalid_input", "mode: cosmetic (the hole at its tapping size, the thread recorded) or modelled (the thread cut into the part).")
        q = dict(a.get("faces") or {})
        q.setdefault("cylinder", True)
        name, face = facepick.pick(o.Shape, q, "faces")[0]
        cyl = cylinder_of(o.Shape, face)
        if a.get("size"):
            row = _row(a["size"])
        else:
            near = tt.nearest(cyl["diameter"], cyl["inside"], 4)
            if tt.tie(near):
                raise ToolError("invalid_input", f"size: say which — {', '.join(x['size'] for x in tt.tie(near))} fit this diameter alike.")
            if abs(near[0]["off"]) > 0.3:
                raise ToolError("invalid_input", f"size: say which — the face ({cyl['diameter']:.2f} mm) is near no thread size.")
            row = _row(near[0]["size"])
        rec = {"body": o.Label, "size": row["size"], "series": row["series"], "mode": mode, "kind": "hole" if cyl["inside"] else "shaft", "diameter": _r(cyl["diameter"])}
        threads = store.read(s.doc, "threads", [])
        if not cyl["inside"]:
            if mode == "modelled":
                raise ToolError("unavailable", "An outside thread cannot be modelled here: FreeCAD's PartDesign threads holes only. mode: cosmetic records it; a printed bolt is better bought.")
            if cyl["diameter"] > row["major"] + 0.05:
                raise ToolError("invalid_state", f"The shaft ({cyl['diameter']:.2f} mm) is over {row['size']}'s major diameter ({row['major']} mm): turn it to the major first.")
            rec["note"] = "Recorded only: the shaft is left at its diameter."
            threads.append(rec)
            store.write(s.doc, "threads", threads)
            s.snapshot("thread_cut")
            return {"thread": rec, "thread_row": {k: row[k] for k in ("major", "pitch", "minorExt")}, "changed": False}
        if cyl["diameter"] > row["major"] + 0.01:
            raise ToolError("invalid_state", f"The hole ({cyl['diameter']:.2f} mm) is over {row['size']}'s major diameter ({row['major']} mm): nothing left to thread — a larger size, or an insert.")
        try:
            import PartDesign  # noqa: F401
            import Sketcher  # noqa: F401
        except ImportError:
            raise ToolError("unavailable", "PartDesign is not in this build: threads are cut with its Hole feature.")
        end = _entry(o.Shape, cyl, a.get("from"))
        depth = float(a.get("depth", cyl["length"]))
        if depth <= 0:
            raise ToolError("invalid_input", "depth: mm, over 0 (the hole's length by default).")
        doc = s.doc
        had = {x.Name for x in doc.Objects}
        made = []
        try:
            base = doc.addObject("Part::Feature", "AgentLinkThreadBase")
            made.append(base)
            base.Shape = o.Shape.copy()
            body = doc.addObject("PartDesign::Body", "AgentLinkThreadBody")
            made.append(body)
            body.BaseFeature = base
            sk = doc.addObject("Sketcher::SketchObject", "AgentLinkThreadSketch")
            made.append(sk)
            body.addObject(sk)
            sk.MapMode = "Deactivated"
            sk.Placement = FreeCAD.Placement(end["at"], FreeCAD.Rotation(V(0, 0, 1), end["out"]))
            sk.addGeometry(Part.Circle(V(0, 0, 0), V(0, 0, 1), cyl["diameter"] / 2), False)
            doc.recompute()
            h = doc.addObject("PartDesign::Hole", "AgentLinkThreadHole")
            made.append(h)
            h.Profile = sk
            body.addObject(h)
            h.ThreadType = HOLE_TYPE[row["series"]]
            h.ThreadSize = row["freecad"]
            h.Threaded = True
            h.ModelThread = mode == "modelled"
            h.HoleCutType = "None"
            h.DepthType = "Dimension"
            h.Depth = depth
            h.DrillPoint = "Flat"
            try:
                h.ThreadDepthType = "Hole Depth"
            except Exception:
                pass
            doc.recompute()
            shape = h.Shape
            bad = h.isValid() is False or shape.isNull() or not shape.Solids
            if bad:
                raise ToolError("failed", f"FreeCAD's Hole could not make {row['size']} here ({mode}): {getattr(h, 'getStatusString', lambda: '')()} — try mode cosmetic, or check the hole with body_check.")
            shape = shape.copy()
            hole_d = float(h.Diameter)
        finally:
            # Everything the Hole needed goes again, the Body's own helpers too.
            for x in reversed(made):
                try:
                    if x.isDerivedFrom("PartDesign::Body"):
                        x.Group = []
                except Exception:
                    pass
            for _ in range(3):
                extra = [x.Name for x in doc.Objects if x.Name not in had]
                if not extra:
                    break
                for n in extra:
                    try:
                        doc.removeObject(n)
                    except Exception:
                        pass
            doc.recompute()
        if len(shape.Solids) == 1 and shape.ShapeType != "Solid":
            shape = shape.Solids[0]
        shape.Placement = FreeCAD.Placement()
        before = o.Shape.Volume
        o.Shape = shape
        doc.recompute()
        rec.update({"depth": _r(depth, 2), "at": [_r(x) for x in end["at"]], "into": [_r(x) for x in end["out"] * -1], "holeDiameter": _r(hole_d)})
        threads.append(rec)
        store.write(s.doc, "threads", threads)
        s.snapshot("thread_cut")
        sound = geo.soundness(o.Shape)
        out = {"thread": rec, "removed": _r(before - o.Shape.Volume, 2), "sound": sound["ok"], "step": len(s.steps)}
        if not sound["ok"]:
            out["problems"] = sound["problems"][:3]
        if mode == "cosmetic":
            out["note"] = "The hole is at the thread's tapping size and the thread is recorded with the document; tap it after making."
        elif store.setup_of(doc, o.Label, "print") and row["major"] < 5:
            out["warning"] = "A modelled thread under M5 rarely prints cleanly: print the tapping hole and tap it, or use a heat-set insert."
        if cyl["diameter"] > hole_d + 0.01:
            out["warning2"] = f"The hole was {cyl['diameter']:.2f} mm, over the {hole_d:.2f} mm the thread drills: the thread is shallow there."
        return out

    q = {"type": "object", "description": "The cylindrical face by query: { near: [x,y,z] } (cylindrical faces only) or { faces: [\"Face7\"] }"}
    return [
        Tool(
            "thread_lookup",
            "Thread sizes from the standards, mm: size (M6, M10x1.25, 1/4-20, #10-32) gives major, pitch, pitch diameter, internal and external minor, tap drill; or a table by series (metric, metric-fine, unc, unf), near: a diameter for the six nearest. Metric M1.6–M24, Unified #4–1/2\".",
            obj({"size": {"type": "string"}, "series": {"type": "string"}, "near": {"type": "number"}}),
            thread_lookup,
        ),
        Tool(
            "thread_identify",
            "What thread a hole or shaft is: a cylindrical face by query gives its diameter, hole or shaft, length, and the nearest standard sizes — a hole read as tap drill, internal minor or major, a shaft as major — each with how far off. When sizes tie but for pitch it says needs: pitch: ask the person or measure, then give pitch.",
            obj({"body": {"type": "string"}, "faces": q, "pitch": {"type": "number"}}, ["faces"]),
            thread_identify,
        ),
        Tool(
            "thread_cut",
            "Thread a hole with FreeCAD's PartDesign Hole: size (or the nearest when it is plain), mode cosmetic (the hole drilled to the tapping size, the thread recorded — the default) or modelled (the thread cut into the part; slow on fine threads), depth mm (the hole's length), from [x,y,z] the end to start at (the open end by default). A shaft can be recorded cosmetic only. Refused when the hole is bigger than the thread.",
            obj({"body": {"type": "string"}, "faces": q, "size": {"type": "string"}, "mode": {"type": "string", "enum": ["cosmetic", "modelled"]}, "depth": {"type": "number"}, "from": {"type": "array", "items": {"type": "number"}}}, ["faces"]),
            thread_cut,
            changes=True,
        ),
    ]
