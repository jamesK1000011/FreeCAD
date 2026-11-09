# SPDX-License-Identifier: LGPL-2.1-or-later
# The six tools, in ScanRuler's manner: few, each one call for one job,
# answers small and in millimetres, a refusal saying what to do instead.
#
#   session_state  what the document holds: bodies with their numbers, steps
#   gen_make       a standard part made to size (a Gridfinity bin), or measured
#   body_check     sound or not: valid, closed, one solid, no slivers
#   part_review    one verdict on a part: soundness, least wall, mass, a spec
#   export_file    a body as STEP or STL
#   history_diff   what a change did, per body, since a step
#
# The document is FreeCAD's own: in the GUI the person watches the bodies
# arrive; headless (FreeCADCmd or a Python with FreeCAD) the same calls run
# on a document of their own.

import os

import FreeCAD
import Part

from . import geometry as geo
from . import gridfinity
from .protocol import Tool, ToolError

DENSITY = {"pla": 1.24, "petg": 1.27, "abs": 1.04, "asa": 1.07, "pa6-cf": 1.12, "tpu-95a": 1.21}
NDFEB = 7.5  # g/cm³, the magnets
LINE = 0.42  # mm, one extrusion line of a 0.4 mm nozzle


def obj(props, required=()):
    return {"type": "object", "properties": props, "required": list(required), "additionalProperties": False}


class Session:
    def __init__(self, doc=None, out_dir=None):
        """doc: the document to work in — the GUI's open one — or None for a
        new one of its own (headless)."""
        self.doc = doc or FreeCAD.newDocument("AgentLink")
        self.out_dir = out_dir or os.getcwd()
        self.steps = []  # [{step, tool, bodies: {name: stats}}]
        self.meta = {}  # body name -> {encased: …, generator: …}

    # ---- the document ----

    def bodies(self):
        return [o for o in self.doc.Objects if hasattr(o, "Shape") and o.Shape and not o.Shape.isNull() and o.Shape.Solids]

    def body(self, ref):
        bs = self.bodies()
        if not bs:
            raise ToolError("not_found", "No body in the document: gen_make makes one.")
        if ref is None:
            if len(bs) == 1:
                return bs[0]
            raise ToolError("invalid_input", f"body: say which — {', '.join(o.Label for o in bs)}.")
        for o in bs:
            if o.Label == ref or o.Name == ref or o.Label.lower() == str(ref).lower():
                return o
        raise ToolError("not_found", f"body {ref!r}: not here — the bodies: {', '.join(o.Label for o in bs)}.")

    def unique(self, label):
        taken = {o.Label for o in self.doc.Objects}
        if label not in taken:
            return label
        n = 2
        while f"{label} {n}" in taken:
            n += 1
        return f"{label} {n}"

    def snapshot(self, tool):
        self.steps.append({"step": len(self.steps) + 1, "tool": tool, "bodies": {o.Label: geo.stats(o.Shape) for o in self.bodies()}})

    # ---- the tools ----

    def session_state(self, a):
        return {
            "document": self.doc.Name,
            "freecad": ".".join(FreeCAD.Version()[:3]),
            "bodies": [{"name": o.Label, **geo.stats(o.Shape), **({"encased": self.meta[o.Label]["encased"]["what"]} if self.meta.get(o.Label, {}).get("encased") else {})} for o in self.bodies()],
            "step": len(self.steps),
            "outDir": self.out_dir,
        }

    def gen_make(self, a):
        gen = a.get("generator", "gridfinity_bin")
        if gen != "gridfinity_bin":
            raise ToolError("invalid_input", "generator: gridfinity_bin is the one this spike has.")
        p = gridfinity.values(a.get("params") or {})
        shape = gridfinity.bin_shape(p)
        part = {"name": gridfinity.name_of(p), "volume": round(shape.Volume, 1), **geo.box_of(shape)}
        if a.get("measure"):
            return {"generator": gen, "values": p, "parts": [part], "made": False}
        label = self.unique(a.get("name") or gridfinity.name_of(p))
        o = self.doc.addObject("Part::Feature", "Body")
        o.Label = label
        o.Shape = shape
        self.doc.recompute()
        mags = gridfinity.magnets(p)
        self.meta[label] = {"generator": gen, "values": p, "encased": mags}
        self.snapshot("gen_make")
        out = {"generator": gen, "values": p, "bodies": [label], "parts": [part], "step": len(self.steps)}
        if mags:
            out["encased"] = {"what": f"{mags['count']} {mags['what']}", "pauseAt": mags["top"], "note": "The magnet holes open onto the bed: press the magnets in after printing, or drop them in at the pause and print over them."}
        return out

    def body_check(self, a):
        o = self.body(a.get("body"))
        s = geo.soundness(o.Shape)
        return {"body": o.Label, **s, **geo.stats(o.Shape)}

    def part_review(self, a):
        o = self.body(a.get("body"))
        spec = a.get("spec") or {}
        waived = a.get("waive") or []
        material = (a.get("material") or "pla").lower()
        if material not in DENSITY:
            raise ToolError("invalid_input", f"material: one of {', '.join(DENSITY)}.")
        shape = o.Shape
        findings, checks, unjudged = [], {}, []

        def find(source, severity, says, at=None):
            findings.append({"source": source, "severity": severity, "says": says, **({"at": at} if at else {})})

        s = geo.soundness(shape)
        checks["body_check"] = "ran"
        for p in s["problems"]:
            find("body_check", "error", p)

        min_wall = float(spec.get("minWall", 0.8))
        w = geo.least_wall(shape)
        checks["wall"] = "ran" if w.get("least") is not None else "failed: no ray read a wall"
        least = w.get("least")
        if least is not None:
            sev = "error" if "minWall" in spec else "warning"
            by = " (the spec)" if "minWall" in spec else ""
            runs = geo.thin_runs(w["readings"], min_wall, 2.5 * w["spacing"])

            def waiver(run):
                for x in waived:
                    at = x.get("at")
                    if isinstance(at, list) and len(at) == 3 and any(sum((at[i] - p[i]) ** 2 for i in range(3)) <= 1.5 ** 2 + (w["spacing"]) ** 2 for p in run["points"]):
                        return x
                return None

            open_runs = []
            for k, run in enumerate(runs, 1):
                x = waiver(run)
                if x is not None:
                    find("wall", "info", f"W{k} waived: {run['least']:.2f} mm over {run['count']} readings — {x.get('reason', 'no reason given')}", run["at"])
                else:
                    open_runs.append((k, run))
            for k, run in open_runs[:3]:
                find("wall", sev, f"W{k} wall {run['least']:.2f} mm, under {min_wall} mm{by}, over {run['count']} readings: it prints as a single line.", run["at"])
            least_open = open_runs[0][1]["least"] if open_runs else min((r[0] for r in w["readings"] if r[0] >= min_wall), default=least)
        elif "minWall" in spec:
            unjudged.append("minWall: no wall reading")

        vol = shape.Volume
        rho = DENSITY[material]
        shell = min(vol, shape.Area * 2 * LINE)
        printed = shell + 0.15 * (vol - shell)
        mags = (self.meta.get(o.Label) or {}).get("encased")
        mag_g = 0.0
        if mags:
            mag_g = mags["count"] * 3.14159265 * 3.0 ** 2 * MAGNET_T_MM * NDFEB / 1000.0
        mass = printed * rho / 1000.0 + mag_g
        checks["mass"] = "ran"
        if "massMax" in spec and mass > spec["massMax"]:
            find("mass", "error", f"Weighs {mass:.1f} g; the spec asks at most {spec['massMax']} g.")
        if "massMin" in spec and mass < spec["massMin"]:
            find("mass", "error", f"Weighs {mass:.1f} g; the spec asks at least {spec['massMin']} g.")
        order = {"error": 0, "warning": 1, "info": 2}
        findings.sort(key=lambda f: order[f["severity"]])
        verdict = "fix" if any(f["severity"] == "error" for f in findings) else ("unknown" if unjudged else "pass")
        out = {
            "body": o.Label,
            "verdict": verdict,
            "findings": findings[:8],
            "figures": {
                "volume": round(vol, 1),
                "mass": round(mass, 1),
                "material": material,
                "as": "printed: 2 walls of 0.42 mm, 15% infill (an estimate)",
                **({"encasedMass": round(mag_g, 1)} if mags else {}),
                **({"wall": least, "wallNotWaived": round(least_open, 3)} if least is not None else {}),
            },
            "checks": checks,
        }
        if unjudged:
            out["unjudged"] = unjudged
        if mags:
            out["printing"] = {"pauses": [{"z": mags["top"], "note": f"Insert {mags['count']} {mags['what']}"}]}
        return out

    def export_file(self, a):
        o = self.body(a.get("body"))
        fmt = (a.get("format") or "step").lower()
        if fmt not in ("step", "stl"):
            raise ToolError("invalid_input", "format: step or stl.")
        path = a.get("path") or os.path.join(self.out_dir, f"{o.Label}.{fmt}")
        if not os.path.isabs(path):
            path = os.path.join(self.out_dir, path)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        if fmt == "step":
            o.Shape.exportStep(path)
            back = Part.read(path)
            verify = {"volume": round(back.Volume, 1), "volumeDiff": round(abs(back.Volume - o.Shape.Volume), 3), "solids": len(back.Solids)}
        else:
            import Mesh
            import MeshPart

            tol = float(a.get("tolerance", 0.01))
            m = MeshPart.meshFromShape(Shape=o.Shape, LinearDeflection=tol, AngularDeflection=0.0873, Relative=False)
            m.write(path)
            back = Mesh.Mesh(path)
            verify = {"triangles": back.CountFacets, "volume": round(back.Volume, 1), "closed": back.isSolid()}
        return {"body": o.Label, "format": fmt, "path": path, "bytes": os.path.getsize(path), "verify": verify}

    def history_diff(self, a):
        if not self.steps:
            return {"changed": False, "note": "Nothing changed yet in this session."}
        last = len(self.steps)
        since = a.get("since", last - 1)
        if not isinstance(since, int) or not (0 <= since < last):
            raise ToolError("invalid_input", f"since: a step from 0 to {last - 1} (session_state gives the step now).")
        before = self.steps[since - 1]["bodies"] if since > 0 else {}
        now = self.steps[-1]["bodies"]
        changed = {}
        for name in sorted(set(before) | set(now)):
            b, n = before.get(name), now.get(name)
            if b == n:
                continue
            if b is None:
                changed[name] = {"added": n}
            elif n is None:
                changed[name] = {"removed": True}
            else:
                changed[name] = {k: {"from": b[k], "to": n[k]} for k in n if b.get(k) != n[k]}
        return {"since": since, "to": last, "changed": bool(changed), "bodies": changed, "steps": [s["tool"] for s in self.steps[since:]]}


MAGNET_T_MM = gridfinity.MAGNET_T

INSTRUCTIONS = (
    "FreeCAD through six tools, answers in millimetres. Make a part with gen_make (measure: true first to check "
    "its size without making it), then part_review for one verdict: soundness, least wall, mass, the spec you give. "
    "body_check reads soundness alone; history_diff says what a change did; export_file writes STEP or STL and "
    "reads it back. A refusal says what to change."
)


def make_tools(session):
    s = session
    body = {"type": "string", "description": "A body's name; may be left out when there is one."}
    return [
        Tool("session_state", "What the document holds: each body's volume, area, faces, solids and size (mm), the step count and where files go. Changes nothing.", obj({}), s.session_state),
        Tool(
            "gen_make",
            "Make a standard part to size as a body: gridfinity_bin (42 mm cells, 7 mm units, lip, magnets 6x2, screws, dividers, label ledge). measure: true builds it and gives size and volume without adding it. Magnets come back as encased, with the height to pause at.",
            obj({
                "generator": {"type": "string", "enum": ["gridfinity_bin"]},
                "params": {"type": "object", "description": "; ".join(f"{k}: {v['tip']}" for k, v in gridfinity.PARAMS.items())},
                "name": {"type": "string"},
                "measure": {"type": "boolean"},
            }),
            s.gen_make,
            changes=True,
        ),
        Tool("body_check", "Whether a body is sound: valid, closed, one solid, no sliver faces (under 0.01 mm wide) or very short edges, with where each problem is.", obj({"body": body}), s.body_check),
        Tool(
            "part_review",
            "One verdict on a part, pass, fix or unknown: soundness, the least wall by rays (warning under 0.8 mm, error under spec.minWall), the mass as printed with encased parts counted, against spec { minWall, massMin, massMax } in mm and g. waive: [{ at: [x,y,z], reason }] accepts a thin place on purpose (a stacking lip).",
            obj({
                "body": body,
                "spec": {"type": "object", "properties": {"minWall": {"type": "number"}, "massMin": {"type": "number"}, "massMax": {"type": "number"}}, "additionalProperties": False},
                "material": {"type": "string", "description": "pla (default), petg, abs, asa, pa6-cf, tpu-95a"},
                "waive": {"type": "array", "items": {"type": "object"}},
            }),
            s.part_review,
        ),
        Tool(
            "export_file",
            "Write a body as STEP or STL (tolerance: the chord error, mm, 0.01 by default) and read it back: verify gives the volume read and, for STEP, how far it is from the body's.",
            obj({"body": body, "format": {"type": "string", "enum": ["step", "stl"]}, "path": {"type": "string"}, "tolerance": {"type": "number"}}),
            s.export_file,
            changes=True,
        ),
        Tool("history_diff", "What changed since a step (the one before the last by default): per body its volume, area, faces, solids and size from and to, and the steps between.", obj({"since": {"type": "integer"}}), s.history_diff),
    ]
