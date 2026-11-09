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

import math
import os

import FreeCAD
import Part

from . import geometry as geo
from . import fits_hw, gridfinity, printability, store
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

    # ---- how it is made, what it must meet ----

    def setup(self, a):
        act = a.get("action", "list")
        all_ = store.setups(self.doc)
        if act == "list":
            return {"setups": all_, "types": {k: v["settings"] for k, v in store.TYPES.items()}}
        if act == "of":
            o = self.body(a.get("body"))
            s = store.setup_of(self.doc, o.Label)
            return {"body": o.Label, "setup": s} if s else {"body": o.Label, "setup": None, "none": "in no setup: how it is made is not recorded — ask, then setup add"}
        if act == "add":
            kind = a.get("type")
            if kind not in store.TYPES:
                raise ToolError("invalid_input", f"type: one of {', '.join(store.TYPES)}.")
            bodies = [self.body(b).Label for b in (a.get("bodies") or [])]
            for b in bodies:
                other = store.setup_of(self.doc, b, kind)
                if other:
                    raise ToolError("invalid_input", f"{b} is in {other['name']} ({other['id']}) already: setup set {{ id: \"{other['id']}\", remove: [\"{b}\"] }} first.")
            settings = self._settings(kind, a.get("settings") or {})
            n = 1 + max([int(x["id"][1:]) for x in all_] or [0])
            same = sum(1 for x in all_ if x["type"] == kind)
            s = {"id": f"S{n}", "type": kind, "name": a.get("name") or store.TYPES[kind]["label"] + (f" {same + 1}" if same else ""), "bodies": bodies, **settings}
            store.save_setups(self.doc, all_ + [s])
            return {"added": s, "setups": len(all_) + 1}
        ref = a.get("id")
        s = next((x for x in all_ if x["id"] == ref or x["name"] == ref), None)
        if s is None:
            raise ToolError("not_found", f"setup {ref!r}: not here — setup list gives them.")
        if act == "remove":
            store.save_setups(self.doc, [x for x in all_ if x is not s])
            return {"removed": s["id"]}
        if act == "set":
            if "bodies" in a:
                s["bodies"] = [self.body(b).Label for b in a["bodies"]]
            for b in a.get("add") or []:
                lb = self.body(b).Label
                if lb not in s["bodies"]:
                    s["bodies"].append(lb)
            for b in a.get("remove") or []:
                s["bodies"] = [x for x in s["bodies"] if x != b]
            for k, v in (a.get("settings") or {}).items():
                if v is None:
                    s.pop(k, None)
                else:
                    s.update(self._settings(s["type"], {k: v}))
            if a.get("name"):
                s["name"] = a["name"]
            store.save_setups(self.doc, all_)
            return {"setup": s}
        raise ToolError("invalid_input", "action: list, of, add, set or remove.")

    @staticmethod
    def _settings(kind, given):
        known = store.TYPES[kind]["settings"]
        bad = [k for k in given if k not in known]
        if bad:
            raise ToolError("invalid_input", f"settings.{bad[0]}: not a {kind} setting — they are: {', '.join(known)}.")
        out = dict(given)
        if "nozzle" in out:
            out["nozzle"] = str(float(out["nozzle"]))
        if "printer" in out and out["printer"] not in printability.PRINTERS:
            raise ToolError("invalid_input", f"settings.printer: one of {', '.join(printability.PRINTERS)}.")
        if out.get("up", "+z") != "+z":
            raise ToolError("invalid_input", "settings.up: +z only in this build — turn the part to stand it another way.")
        return out

    def part_spec(self, a):
        spec = dict(store.spec(self.doc))
        if a.get("clear"):
            spec = {}
        for k, v in (a.get("spec") or {}).items():
            if v is None:
                spec.pop(k, None)
            elif k in ("minWall", "massMin", "massMax", "minSafety", "minClearance") and isinstance(v, (int, float)):
                spec[k] = v
            else:
                raise ToolError("invalid_input", f"spec.{k}: minWall, massMin or massMax (numbers, mm and g).")
        store.write(self.doc, "spec", spec)
        mats = store.materials(self.doc)
        if a.get("material"):
            m = a["material"].lower()
            if m not in DENSITY:
                raise ToolError("invalid_input", f"material: one of {', '.join(DENSITY)}.")
            for b in a.get("bodies") or [self.body(None).Label]:
                mats[self.body(b).Label] = m
            store.write(self.doc, "materials", mats)
        return {"spec": spec, "materials": mats}

    def _rules(self, body):
        s = store.setup_of(self.doc, body, "print") or {}
        return printability.rules_for(s.get("nozzle", "0.4"), s.get("printer")), s

    def dfm_check(self, a):
        o = self.body(a.get("body"))
        waivers = store.waivers(self.doc)
        if a.get("waive"):
            w = a["waive"]
            last = getattr(self, "_last_dfm", {}).get(o.Label, [])
            f = next((x for x in last if x["id"] == w.get("id")), None)
            if f is None:
                raise ToolError("not_found", f"waive.id {w.get('id')!r}: not a finding of the last check on {o.Label} — dfm_check it first.")
            waivers.append({"body": o.Label, "rule": f["rule"], "at": f["at"], "reason": w.get("reason") or "waived"})
            store.write(self.doc, "waivers", waivers)
        rules, setup = self._rules(o.Label)
        out = printability.check(o.Shape, rules, [x for x in waivers if x.get("body") == o.Label])
        if not hasattr(self, "_last_dfm"):
            self._last_dfm = {}
        self._last_dfm[o.Label] = out["findings"]
        out["body"] = o.Label
        if setup:
            out["setup"] = f"{setup['name']} ({setup['id']})"
        return out

    def part_review(self, a):
        o = self.body(a.get("body"))
        spec = {**store.spec(self.doc), **(a.get("spec") or {})}
        material = (a.get("material") or store.materials(self.doc).get(o.Label) or "pla").lower()
        if material not in DENSITY:
            raise ToolError("invalid_input", f"material: one of {', '.join(DENSITY)}.")
        shape = o.Shape
        findings, checks, unjudged, informed = [], {}, [], []

        def find(into, source, severity, says, at=None):
            into.append({"source": source, "severity": severity, "says": says, **({"at": at} if at else {})})

        s = geo.soundness(shape)
        checks["body_check"] = "ran"
        for p in s["problems"]:
            find(findings, "body_check", "error", p)

        setup = store.setup_of(self.doc, o.Label)
        printed = bool(setup and setup["type"] == "print")
        # Waivers given here are kept, as dfm_check's are.
        if a.get("waive"):
            ws = store.waivers(self.doc)
            for x in a["waive"]:
                ws.append({"body": o.Label, "rule": x.get("rule", "thin-wall"), "at": x.get("at"), "reason": x.get("reason", "waived")})
            store.write(self.doc, "waivers", ws)
        rules, _ = self._rules(o.Label)
        dfm = printability.check(shape, rules, [x for x in store.waivers(self.doc) if x.get("body") == o.Label])
        checks["dfm_check"] = "ran" if printed or not setup else "skipped: not printed"
        into = findings if printed else informed
        if printed or not setup:
            for f in dfm["findings"]:
                if not f.get("waived"):
                    find(into, "dfm_check", f["severity"], f"{f['id']} {f['rule']}: {f['says']}", f["at"])
        least = dfm["wall"]["least"]
        if "minWall" in spec:
            open_thin = [f["value"] for f in dfm["findings"] if f["rule"] == "thin-wall" and not f.get("waived")]
            judged = min(open_thin) if open_thin else (least if least is not None and least >= spec["minWall"] else None)
            if least is None:
                unjudged.append("minWall: no wall reading")
            elif judged is not None and judged < spec["minWall"]:
                find(findings, "dfm_check", "error", f"Least wall not waived {judged:.2f} mm, under the spec's {spec['minWall']} mm.")
            elif judged is None and spec["minWall"] > rules["minWall"]:
                unjudged.append(f"minWall: the least wall is waived, and the check reads walls only under {rules['minWall']} mm")

        vol = shape.Volume
        rho = DENSITY[material]
        walls = int((setup or {}).get("walls", 2))
        infill = float((setup or {}).get("infill", 15)) / 100
        if printed:
            shell = min(vol, shape.Area / 2 * walls * LINE * 2)
            body_g = (shell + infill * (vol - shell)) * rho / 1000.0
            as_ = f"printed: {walls} walls of {LINE} mm, {infill * 100:.0f}% infill (an estimate)"
        else:
            body_g = vol * rho / 1000.0
            as_ = "solid"
        mags = (self.meta.get(o.Label) or {}).get("encased")
        mag_g = mags["count"] * math.pi * 3.0 ** 2 * MAGNET_T_MM * NDFEB / 1000.0 if mags else 0.0
        mass = body_g + mag_g
        checks["mass"] = "ran"
        if "massMax" in spec and mass > spec["massMax"]:
            find(findings, "mass", "error", f"Weighs {mass:.1f} g; the spec asks at most {spec['massMax']} g.")
        if "massMin" in spec and mass < spec["massMin"]:
            find(findings, "mass", "error", f"Weighs {mass:.1f} g; the spec asks at least {spec['massMin']} g.")
        if "minSafety" in spec:
            unjudged.append("minSafety: no strength solve in this build")
        order = {"error": 0, "warning": 1, "info": 2}
        findings.sort(key=lambda f: order[f["severity"]])
        verdict = "fix" if any(f["severity"] == "error" for f in findings) else ("unknown" if unjudged else "pass")
        out = {
            "body": o.Label,
            "process": "printed" if printed else (setup["type"] if setup else "unknown"),
            "processFrom": f"setup {setup['name']} ({setup['id']})" if setup else "not said — setup add records it",
            "verdict": verdict,
            "findings": findings[:8],
            "figures": {"volume": round(vol, 1), "mass": round(mass, 1), "material": material, "as": as_, **({"encasedMass": round(mag_g, 1)} if mags else {}), **({"wall": least} if least is not None else {})},
            "checks": checks,
            "spec": spec,
        }
        if informed:
            out["ifPrinted"] = informed[:5]
        if unjudged:
            out["unjudged"] = unjudged
        if mags and printed:
            lh = float(setup.get("layerHeight", 0.2))
            layer = int(math.ceil(mags["top"] / lh))
            out["printing"] = {"pauses": [{"z": round(layer * lh, 3), "afterLayer": layer, "note": f"Insert {mags['count']} {mags['what']}"}]}
        return out

    # ---- fits and hardware ----

    def _cal(self, body=None):
        """The printer calibration the sizes take: the body's print setup's,
        else the document's (fit calibrate), else the typical printer's."""
        s = store.setup_of(self.doc, body, "print") if body else None
        c = (s or {}).get("fit") or store.read(self.doc, "fit", None)
        return (c, "calibrated") if c else (dict(fits_hw.DEFAULT_CAL), "none — the typical printer assumed (holes print 0.2 mm small)")

    def fit(self, a):
        act = a.get("action", "size")
        if act == "calibrate":
            c = {"hole": float(a.get("hole", 0.2)), "shaft": float(a.get("shaft", 0.0))}
            if not (-1 <= c["hole"] <= 1 and -1 <= c["shaft"] <= 1):
                raise ToolError("invalid_input", "hole and shaft: how much a printed hole comes out small and a peg big, diametral mm, -1 to 1.")
            store.write(self.doc, "fit", c)
            return {"calibration": c, "note": "Every hole and fit sized from now on takes it; a print setup's own fit wins for its parts."}
        if act == "stack":
            items = a.get("items") or []
            if not items or not all(isinstance(i.get("nominal"), (int, float)) for i in items):
                raise ToolError("invalid_input", "items: [{ name, nominal (mm, signed), tol (± mm) }], one or more.")
            out = fits_hw.stack(items)
            if "target" in a:
                lo, hi = a["target"]
                out["worstCaseOk"] = lo <= out["worstCase"][0] and out["worstCase"][1] <= hi
                out["rssOk"] = lo <= out["rss"][0] and out["rss"][1] <= hi
            return out
        if act == "size":
            cal, said = self._cal(a.get("body"))
            nominal = a.get("nominal")
            if not isinstance(nominal, (int, float)):
                raise ToolError("invalid_input", "nominal: the size to fit, mm — the bought part's, or the mating part's.")
            out = {k: round(fits_hw.size_fit(k, nominal, a.get("of", "hole"), cal), 3) for k in ([a["kind"]] if a.get("kind") else fits_hw.FITS)}
            return {"of": a.get("of", "hole"), "nominal": nominal, "model": out, "printerFit": said}
        raise ToolError("invalid_input", "action: size, calibrate or stack.")

    def hw_hole(self, a):
        o = self.body(a.get("body"))
        kind = a.get("kind")
        if kind not in fits_hw.KINDS:
            raise ToolError("invalid_input", f"kind: one of {', '.join(fits_hw.KINDS)}.")
        at = a.get("at")
        if not (isinstance(at, list) and len(at) == 3):
            raise ToolError("invalid_input", "at: [x, y, z], mm — where the hole enters the part, on its face.")
        cal, said = self._cal(o.Label)
        bb = o.Shape.BoundBox
        depth = a.get("depth", "through")
        depth = bb.DiagonalLength if depth == "through" else float(depth)
        tool, sizes = fits_hw.hole_tool(kind, a.get("size", "M3"), at, a.get("dir", "-z"), depth, a.get("fit", "normal"), cal)
        before = o.Shape.Volume
        cut = geo.clean(o.Shape.cut(tool))
        removed = before - cut.Volume
        if removed < 1e-3:
            raise ToolError("failed", f"The hole cut nothing at {at} going {a.get('dir', '-z')}: put at on the part's face, dir into the material.")
        o.Shape = cut
        self.doc.recompute()
        self.snapshot("hw_hole")
        s = geo.soundness(cut)
        return {"body": o.Label, "kind": kind, "size": a.get("size", "M3"), "sizes": sizes, "printerFit": said, "removed": round(removed, 2), "sound": s["ok"], **({"problems": s["problems"]} if not s["ok"] else {}), "step": len(self.steps)}

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
            "setup",
            "How each part is made, recorded: action list (the setups and the kinds), of { body }, add { type: print|cut|mill, bodies, settings, name }, set { id, bodies | add | remove, settings (null drops one), name }, remove { id }. Print settings: printer (generic, bed-slinger, corexy, h2d), nozzle, layerHeight, firstLayer, walls, infill. A body in no setup has no process: ask, then add. Kept with the document.",
            obj({"action": {"type": "string", "enum": ["list", "of", "add", "set", "remove"]}, "type": {"type": "string"}, "id": {"type": "string"}, "name": {"type": "string"}, "body": body, "bodies": {"type": "array", "items": {"type": "string"}}, "add": {"type": "array", "items": {"type": "string"}}, "remove": {"type": "array", "items": {"type": "string"}}, "settings": {"type": "object"}}),
            s.setup,
            changes=True,
        ),
        Tool(
            "part_spec",
            "The limits every part must meet, kept with the document: spec { minWall mm, massMin g, massMax g } (null drops one; clear: true starts again), and what parts are made of: material (pla, petg, abs, asa, pa6-cf, tpu-95a) for bodies (the one body when left out).",
            obj({"spec": {"type": "object"}, "clear": {"type": "boolean"}, "material": {"type": "string"}, "bodies": {"type": "array", "items": {"type": "string"}}}),
            s.part_spec,
            changes=True,
        ),
        Tool(
            "dfm_check",
            "Is a body printable as it stands (+z up), with its print setup's nozzle and printer: thin walls (under one line an error, under two lines a warning), overhangs past 50°, flat ceilings over 10 mm, upright holes too small to print open, bed contact, tall and thin, build volume. Findings F1 … with value, limit, where and what to do. waive { id, reason } accepts one of the last check's findings on purpose — kept, and it comes back waived.",
            obj({"body": body, "waive": {"type": "object", "properties": {"id": {"type": "string"}, "reason": {"type": "string"}}, "required": ["id"], "additionalProperties": False}}),
            s.dfm_check,
        ),
        Tool(
            "part_review",
            "One verdict on a part, pass, fix or unknown, after every change: soundness, and how it is made decides the rest — in a print setup the printability check judges it; in no setup its findings come under ifPrinted, for information; the least wall and the mass (as printed in a print setup, encased magnets counted) against the spec kept with part_spec, or spec given here for this review alone. waive: [{ rule, at, reason }] accepts a place on purpose, kept.",
            obj({
                "body": body,
                "spec": {"type": "object", "properties": {"minWall": {"type": "number"}, "massMin": {"type": "number"}, "massMax": {"type": "number"}}, "additionalProperties": False},
                "material": {"type": "string", "description": "pla (default), petg, abs, asa, pa6-cf, tpu-95a"},
                "waive": {"type": "array", "items": {"type": "object"}},
            }),
            s.part_review,
        ),
        Tool(
            "fit",
            "Fits for made parts: action size { nominal, of: hole|shaft, kind: press|snug|slide|free (all when left out), body } gives the size to model at, the printer calibration added; calibrate { hole, shaft } keeps how much this printer prints a hole small and a peg big (diametral mm); stack { items: [{ name, nominal, tol }], target: [lo, hi] } adds a tolerance stack worst case and RSS.",
            obj({"action": {"type": "string", "enum": ["size", "calibrate", "stack"]}, "nominal": {"type": "number"}, "of": {"type": "string", "enum": ["hole", "shaft"]}, "kind": {"type": "string"}, "body": body, "hole": {"type": "number"}, "shaft": {"type": "number"}, "items": {"type": "array", "items": {"type": "object"}}, "target": {"type": "array", "items": {"type": "number"}}}),
            s.fit,
        ),
        Tool(
            "hw_hole",
            "Cut a hole for hardware into a body: kind clearance, counterbore, countersink, nut_trap (screws M2 to M8, fit close|normal|loose), insert (heat-set, typical sizes), magnet (size \"6x2\"); at [x,y,z] where it enters, on the face; dir into the material (-z by default; +x … or [x,y,z]); depth mm or \"through\". Sizes from the standards, the printer calibration added; answers the sizes used and whether the body is still sound.",
            obj({"body": body, "kind": {"type": "string", "enum": list(fits_hw.KINDS)}, "size": {"type": "string"}, "at": {"type": "array", "items": {"type": "number"}}, "dir": {}, "depth": {}, "fit": {"type": "string", "enum": ["close", "normal", "loose"]}}, ["kind", "at"]),
            s.hw_hole,
            changes=True,
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
