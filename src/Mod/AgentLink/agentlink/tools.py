# SPDX-License-Identifier: LGPL-2.1-or-later
# The tools, in ScanRuler's manner: few per job, each one call, answers
# small and in millimetres, a refusal saying what to do instead. They come
# in groups (tool_groups): core is always listed; the lean list (the
# default) adds the groups a part's make-check-verdict loop needs, the rest
# are listed when asked for — and every tool is callable by name, listed or
# not, alone or in a batch.
#
# The document is FreeCAD's own: in the GUI the person watches the bodies
# arrive; headless (FreeCADCmd or a Python with FreeCAD) the same calls run
# on a document of their own. Every change is one undo step.

import math
import os

import FreeCAD
import Part

from . import geometry as geo
from . import fits_hw, gridfinity, printability, slicer, store
from . import printmass
from .printmass import printed_fraction
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
        self.doc.UndoMode = 1
        self.checkpoints = {}
        # Each family's own state for the session, under its group's name.
        self.family_state = {}
        self.out_dir = out_dir or os.getcwd()
        self.steps = []  # [{step, tool, bodies: {name: stats}}]
        self.meta = {}  # body name -> {encased: …, generator: …}

    # ---- every change one undo step ----

    def execute(self, tool, fn):
        if not tool.changes:
            return fn()
        self.doc.openTransaction(tool.name)
        try:
            out = fn()
        except BaseException:
            self.doc.abortTransaction()
            raise
        self.doc.commitTransaction()
        return out

    # ---- the document ----

    @staticmethod
    def _solid(o):
        try:
            return hasattr(o, "Shape") and not o.Shape.isNull() and bool(o.Shape.Solids)
        except Exception:
            return False

    def bodies(self):
        """The parts: solid objects that nothing else is built from (a
        PartDesign Body, not its features; a boolean's result, not its
        inputs) and that are shown. A folder or a spreadsheet using one
        does not count."""
        passive = ("App::DocumentObjectGroup", "Spreadsheet::Sheet", "App::Part", "Assembly::AssemblyObject", "Assembly::JointGroup")
        out = []
        for o in self.doc.Objects:
            if not self._solid(o):
                continue
            # Built into another solid (a boolean, the next feature): not a
            # part of its own. A drawing, a folder or a spreadsheet using it
            # leaves it one.
            if any(p.TypeId not in passive and self._solid(p) for p in o.InList):
                continue
            try:
                parent = o.getParentGeoFeatureGroup()
            except Exception:
                parent = None
            if parent is not None and parent.TypeId == "PartDesign::Body":
                continue  # a feature: its Body is the part
            if getattr(o, "Visibility", True) is False:
                continue
            out.append(o)
        return out

    def switch(self, doc):
        """Work in another document from now on: its own undo, checkpoints
        and steps."""
        self.doc = doc
        self.doc.UndoMode = 1
        self.checkpoints = {}
        # Each family's own state for the session, under its group's name.
        self.family_state = {}
        self.steps = []
        self.meta = {}

    def body(self, ref):
        # Named, any solid object answers (a hidden one, a feature); left
        # out, the one part shown.
        if ref is not None:
            for o in self.doc.Objects:
                if self._solid(o) and (o.Label == ref or o.Name == ref or o.Label.lower() == str(ref).lower()):
                    return o
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

    def _card(self, label, given=None):
        """The body's material: (id, density g/cm³, filament word or None,
        name) — from the material library and custom materials when the
        material family is in the build, else the filaments here."""
        mid = given or store.materials(self.doc).get(label) or "pla"
        c = None
        try:
            from .families import material as fm

            c = fm.card(self.doc, mid)
        except ImportError:
            pass
        key = str(mid).lower()
        if c is None and key in DENSITY:
            c = {"density": DENSITY[key], "name": key.upper()}
        if not c or not c.get("density"):
            raise ToolError("invalid_input", f"material {mid!r}: not known, or without a density — material_list finds one, material_set gives a body its own.")
        filament = key.split("-")[0] if key in DENSITY else None
        return mid, float(c["density"]), filament, c.get("name", str(mid))

    def _encased(self, label):
        """Everything dropped into the part mid-print: each with its mass
        (g) and the height to pause at — the magnets gen_make records, the
        ballast mass_ballast cuts, and the parts hw_embed seals in."""
        # Heights from the part's lowest point, as it stands on the bed.
        m = self.meta.get(label) or {}
        try:
            z0 = self.body(label).Shape.BoundBox.ZMin
        except ToolError:
            z0 = 0.0
        out = []
        mags = m.get("encased")
        if mags:
            out.append({"what": f"{mags['count']} {mags['what']}", "mass": mags["count"] * math.pi * 3.0 ** 2 * MAGNET_T_MM * NDFEB / 1000.0, "pauseAt": mags["top"] - z0})
        b = m.get("ballast")
        if b and b.get("pockets"):
            top = max(p["at"][2] + p["depth"] for p in b["pockets"])
            out.append({"what": f"{b['what']} ballast in {len(b['pockets'])} pockets", "mass": sum(p.get("fill", 0) for p in b["pockets"]), "pauseAt": top - z0})
        for e in m.get("embeds") or []:
            if isinstance(e, dict) and ("pauseZ" in e or "pauseAt" in e):
                out.append({"what": e.get("what", "a part"), "mass": float(e.get("mass", 0) or 0), "pauseAt": e.get("pauseZ", e.get("pauseAt"))})
        return out

    def part_review(self, a):
        o = self.body(a.get("body"))
        spec = {**store.spec(self.doc), **(a.get("spec") or {})}
        material, rho, _, _ = self._card(o.Label, a.get("material"))
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
        walls = int((setup or {}).get("walls", 2))
        infill = float((setup or {}).get("infill", 15)) / 100
        if printed:
            # Through the part along each wall ray: solid where the walls
            # from both faces fill the thickness, infill in what they leave.
            # Each ray stands for its patch of surface, so summing over the
            # rays weighs by volume; each column is read from both of its
            # faces, which the ratio cancels.
            # A ray in from a side wall crosses the perimeters at each end; one
            # in from a top or bottom face crosses the solid layers — the
            # bottom skin at one end, the top skin at the other. A sloped face
            # is between the two, by how far it faces up or down.
            lh = float((setup or {}).get("layerHeight", 0.2))
            tops = int((setup or {}).get("topLayers", 5))
            bottoms = int((setup or {}).get("bottomLayers", 4))
            side_shell, skin_shell = printmass.shells(setup, walls)
            reads, nz = printmass.readings(geo.least_wall(shape, reach=shape.BoundBox.DiagonalLength + 1.0))
            if reads:
                f = printed_fraction(reads, nz, side_shell, skin_shell, infill)
            else:
                f = 1.0
            body_g = vol * f * rho / 1000.0
            as_ = f"printed: {walls} walls, {tops} top and {bottoms} bottom layers of {lh} mm, {infill * 100:.0f}% infill — {f * 100:.0f}% of the solid, from the walls read (an estimate; the slicer's figure is the authority)"
        else:
            body_g = vol * rho / 1000.0
            as_ = "solid"
        encased = self._encased(o.Label)
        mag_g = sum(e["mass"] for e in encased)
        mags = encased
        mass = body_g + mag_g
        checks["mass"] = "ran"
        if "massMax" in spec and mass > spec["massMax"]:
            find(findings, "mass", "error", f"Weighs {mass:.1f} g; the spec asks at most {spec['massMax']} g.")
        if "massMin" in spec and mass < spec["massMin"]:
            find(findings, "mass", "error", f"Weighs {mass:.1f} g; the spec asks at least {spec['massMin']} g.")
        sfl = None
        fea = getattr(self, "_agentlink_fea", None)
        fea_ok = bool(fea and fea.get("body") == o.Label and fea.get("stats") == geo.stats(shape) and fea.get("result"))
        if fea_ok:
            r = fea["result"]
            sf = r.get("safety")
            across = (r.get("acrossLayers") or {}).get("safety")
            sfl = min(x for x in (sf, across) if x is not None) if (sf is not None or across is not None) else None
            checks["fea_results"] = "ran"
            if sfl is not None and "minSafety" in spec and sfl < spec["minSafety"]:
                find(findings, "fea_results", "error", f"Safety factor {sfl:.2f}, under the spec's {spec['minSafety']}.")
        elif fea and fea.get("body") == o.Label:
            checks["fea_results"] = "skipped: stale — the body changed since the solve; fea_solve again"
        else:
            checks["fea_results"] = "skipped: nothing solved"
        if "minSafety" in spec and not fea_ok:
            unjudged.append("minSafety: " + checks["fea_results"].replace("skipped: ", ""))
        order = {"error": 0, "warning": 1, "info": 2}
        findings.sort(key=lambda f: order[f["severity"]])
        verdict = "fix" if any(f["severity"] == "error" for f in findings) else ("unknown" if unjudged else "pass")
        out = {
            "body": o.Label,
            "process": "printed" if printed else (setup["type"] if setup else "unknown"),
            "processFrom": f"setup {setup['name']} ({setup['id']})" if setup else "not said — setup add records it",
            "verdict": verdict,
            "findings": findings[:8],
            "figures": {"volume": round(vol, 1), "mass": round(mass, 1), "material": material, "as": as_, **({"encasedMass": round(mag_g, 1)} if mags else {}),
                **({"safety": sfl} if fea_ok and sfl is not None else {}), **({"wall": least} if least is not None else {})},
            "checks": checks,
            "spec": spec,
        }
        if informed:
            out["ifPrinted"] = informed[:5]
        if unjudged:
            out["unjudged"] = unjudged
        if mags and printed:
            lh = float(setup.get("layerHeight", 0.2))
            pauses = {}
            for e in mags:
                layer = int(math.ceil(e["pauseAt"] / lh - 1e-9))
                p = pauses.setdefault(layer, {"z": round(layer * lh, 3), "afterLayer": layer, "note": []})
                p["note"].append(e["what"])
            out["printing"] = {"pauses": [{**p, "note": "Insert " + "; ".join(p["note"])} for _, p in sorted(pauses.items())]}
        return out

    # ---- fits and hardware ----

    def calibration(self, body=None):
        """The printer calibration sizes take: (hole, shaft diametral mm
        dict, where it came from)."""
        return self._cal(body)

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

    def slice(self, a):
        o = self.body(a.get("body"))
        setup = store.setup_of(self.doc, o.Label, "print")
        if not setup:
            raise ToolError("invalid_state", f"{o.Label} is in no print setup: slicing prints it — setup add {{ type: \"print\", bodies: [\"{o.Label}\"] }} once the person says it is printed.")
        settings = {**setup, **(a.get("settings") or {})}
        mat, rho, filament, name = self._card(o.Label)
        if filament is None:
            raise ToolError("invalid_state", f"{o.Label} is in {name}, not a printing filament: material_set a filament (pla, petg, abs, asa, pa6-cf, tpu-95a) before slicing.")
        settings["filament"] = filament
        settings["density"] = rho
        import Mesh  # noqa: F401
        import MeshPart

        base = os.path.join(self.out_dir, a.get("name") or o.Label.replace(" ", "_"))
        stl, gcode = base + ".stl", base + ".gcode"
        MeshPart.meshFromShape(Shape=o.Shape, LinearDeflection=0.01, AngularDeflection=0.0873, Relative=False).write(stl)
        try:
            exe = slicer.slice_stl(stl, gcode, settings)
        except FileNotFoundError as e:
            # No PrusaSlicer: the slicer the person has, by slice_with.
            from . import slicers
            from .families import slicing

            other = next((n for n, here in slicers.present().items() if here and n != "prusaslicer"), None)
            if not other:
                raise ToolError("unavailable", f"{e} OrcaSlicer and Bambu Studio are not here either.")
            fn = {t.name: t.fn for t in slicing.tools(self)}["slice_with"]
            out = fn({k: v for k, v in {**a, "slicer": other}.items() if k in ("body", "slicer", "pauseCommand", "name", "profiles")})
            out["note"] = f"PrusaSlicer is not on this computer: sliced with {other} (slice_with). " + out.get("note", "")
            return out
        except RuntimeError as e:
            raise ToolError("failed", str(e))
        read = slicer.read_gcode(gcode)
        mags = self._encased(o.Label)
        pauses = []
        if mags:
            pauses = slicer.insert_pauses(gcode, [{"z": e["pauseAt"], "note": f"Insert {e['what']}"} for e in mags], a.get("pauseCommand", "M601"))
        return {
            "body": o.Label,
            "slicer": os.path.basename(exe),
            "gcode": gcode,
            "bytes": os.path.getsize(gcode),
            **read,
            "setup": f"{setup['name']} ({setup['id']})",
            **({"pauses": pauses} if mags else {}),
            "note": "Sliced with the slicer's own defaults for anything the setup leaves unset; the printer's profile in the slicer is the authority. Starting the print is the person's.",
        }

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

# What the server says it is, at initialize: how a session goes, in the
# order an agent needs it. Built per server: headless or the FreeCAD window,
# lean or every tool listed.
ABOUT_WINDOW = (
    "These tools drive FreeCAD in the window the person has open (Agent Link's Start link): they watch every step "
    "in its 3D view and can undo it there."
)
ABOUT_HEADLESS = (
    "These tools drive FreeCAD headless: FreeCAD runs inside this server with no window — nobody watches, so look "
    "at the work yourself with view_render. To watch, the person opens FreeCAD, picks the Agent Link workbench and "
    "presses Start link; a new chat then works in that window."
)
WORKING = (
    "Start with session_state: the document, its bodies with their sizes, the step count and the folder files go to. "
    "Before a task's first step read its guide: guide_read with no topic lists them (start, parts, model, sketch, "
    "measure, strength, drawing, assembly, cam, slice, view, and the scan guides when ScanRuler for FreeCAD is here); a guide's "
    "## sections can be read one at a time. "
    "Lengths are millimetres, angles degrees, masses grams. A body is named by its label; a face or edge by what it "
    "is (shape_query: normal, radius, near a point) rather than by FreeCAD's Face12, which changes with the model. "
    "Every tool that changes the document is one undo step: history_undo, history_redo, history_checkpoint and "
    "history_restore; history_diff says what changed. batch runs calls in order and rolls back whole when one is "
    "refused. A refusal is { error: { code, message } }: the code (invalid_input, invalid_state, not_found, "
    "unavailable, failed) and a message saying what to change — correct the call and go on. "
    "Look at the work after each change that matters: view_render draws the parts from any view, cut open, zoomed "
    "to a spot, or with a map (wall, stress, a scan's deviation), and gives the worst place; part_review gives one "
    "verdict, pass, fix or unknown. Ask the person what only they know — how a part is made, what it is made of, "
    "what it must carry — and never start, pause or stop a print without their typed say-so."
)
LEAN_NOTE = (
    "Only the tools every task uses are listed at the start: the session, guides, undo, the make-check-verdict loop "
    "and the pictures. The rest come in groups (doc, model, sketch, feature, measure, strength, drawing, assembly, cam, scan, align "
    "…): tool_groups lists them and enable: [group] lists a group's tools from then on — enable what the task's "
    "guide names, not all. A tool not listed still runs: call it by name, or through batch. AGENTLINK_TOOLS=full "
    "lists every tool from the start."
)


def instructions(lean, window):
    return " ".join([ABOUT_WINDOW if window else ABOUT_HEADLESS, LEAN_NOTE if lean else "Every tool is listed.", WORKING])


INSTRUCTIONS = instructions(True, False)


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
            "How each part is made, recorded: action list (the setups and the kinds), of { body }, add { type: print|cut|mill, bodies, settings, name }, set { id, bodies | add | remove, settings (null drops one), name }, remove { id }. Print settings: printer (generic, bed-slinger, corexy, h2d), nozzle, layerHeight, firstLayer, walls, infill, topLayers, bottomLayers. A body in no setup has no process: ask, then add. Kept with the document.",
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
            "slice",
            "Slice a body in a print setup with the slicer on this computer (PrusaSlicer's command line; AGENTLINK_SLICER names another binary): the setup's layers, nozzle, walls and infill, the part's material as the filament. Answers the G-code's path, print time, filament (g, mm) and layers; parts encased at a pause (magnets) get it written into the G-code before the first layer over them. Refused for a part in no print setup.",
            obj({"body": body, "settings": {"type": "object"}, "name": {"type": "string"}, "pauseCommand": {"type": "string"}}),
            s.slice,
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


# ---- groups, guides, batches, undo: the server as a whole ---------------------------------

GROUP_OF = {
    "session_state": "core", "body_check": "core", "export_file": "core", "history_diff": "core",
    "gen_make": "parts",
    "setup": "make", "part_spec": "make", "dfm_check": "make", "part_review": "make", "slice": "make",
    "fit": "fits", "hw_hole": "fits",
}
GROUP_INFO = {
    "core": "The session, groups, guides, batches, undo, soundness, export. Always listed.",
    "parts": "Standard parts made to size: Gridfinity bins and baseplates, project boxes, board enclosures.",
    "make": "How each part is made (setups), its limits and material, the printability check, the verdict, slicing.",
    "fits": "Fits, printer calibration, tolerance stacks and holes for hardware.",
}
# Listed by default (lean): core, the groups a part's make-check-verdict loop
# needs, and the pictures to check it by.
LEAN = {"core", "parts", "make", "fits", "view"}
GUIDES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "guides")
# Guides from other add-ons (register_guides): read alongside these, the
# add-on's own taking no precedence over a guide of the same name here.
GUIDE_DIRS = [GUIDES]


def register_guides(folder):
    if os.path.isdir(folder) and folder not in GUIDE_DIRS:
        GUIDE_DIRS.append(folder)


def _guide_files():
    seen = {}
    for d in GUIDE_DIRS:
        if os.path.isdir(d):
            for n in sorted(os.listdir(d)):
                if n.endswith(".md") and n[:-3] not in seen:
                    seen[n[:-3]] = os.path.join(d, n)
    return dict(sorted(seen.items()))


class Resources:
    """The guides and the files the session wrote, as MCP resources."""

    def __init__(self, session):
        self.s = session

    def list(self):
        out = []
        for topic in _guide_files():
            out.append({"uri": f"agentlink://guide/{topic}", "name": topic, "mimeType": "text/markdown"})
        if os.path.isdir(self.s.out_dir):
            for n in sorted(os.listdir(self.s.out_dir))[:200]:
                p = os.path.join(self.s.out_dir, n)
                if os.path.isfile(p):
                    out.append({"uri": "file://" + p, "name": n, "size": os.path.getsize(p)})
        return out

    def read(self, uri):
        import base64

        if uri.startswith("agentlink://guide/"):
            p = _guide_files().get(uri.rsplit("/", 1)[1])
            if not p:
                raise ValueError(f"{uri}: no such guide.")
        elif uri.startswith("file://"):
            p = uri[7:]
            if not os.path.abspath(p).startswith(os.path.abspath(self.s.out_dir)):
                raise ValueError("Only the session's own files are served.")
        else:
            raise ValueError(f"{uri}: not a resource here.")
        if p.endswith((".md", ".json", ".txt", ".gcode", ".csv", ".svg", ".step", ".stp")):
            with open(p, encoding="utf-8", errors="replace") as f:
                return {"uri": uri, "mimeType": "text/plain", "text": f.read()}
        with open(p, "rb") as f:
            data = f.read()
        if len(data) > 8 * 1024 * 1024:
            raise ValueError("Over 8 MB: read it from the file itself.")
        return {"uri": uri, "mimeType": "application/octet-stream", "blob": base64.b64encode(data).decode()}


def _guide_index():
    out = []
    for topic, p in _guide_files().items():
        with open(p, encoding="utf-8") as f:
            text = f.read()
        first = next((l.lstrip("# ").strip() for l in text.splitlines() if l.strip() and not l.lstrip().startswith("<!--")), topic)
        sections = [l[3:].strip() for l in text.splitlines() if l.startswith("## ")]
        out.append({"topic": topic, "title": first, "sections": sections, "chars": len(text)})
    return out


def build_server(session, name="freecad-agentlink", version="0.2.0", lean=None):
    """The server: every family's tools, grouped, with the core ones that
    act on the server itself (groups, guides, batch, undo)."""
    from . import families
    from .protocol import Server

    if lean is None:
        lean = os.environ.get("AGENTLINK_TOOLS", "lean") != "full"
    tools = make_tools(session)
    for t in tools:
        t.group = GROUP_OF.get(t.name, t.group)
    info = dict(GROUP_INFO)
    for fam in families.load():
        info[fam.GROUP] = fam.INFO
        for t in fam.tools(session):
            t.group = fam.GROUP
            tools.append(t)
    # Two tools of one name: the second would hide the first without a word.
    seen = {}
    for t in tools:
        if t.name in seen:
            raise RuntimeError(f"Agent Link: two tools are named {t.name} ({seen[t.name]} and {t.group}) — rename one.")
        seen[t.name] = t.group
    try:
        window = bool(FreeCAD.GuiUp)
    except Exception:
        window = False
    server = Server(name, version, tools, instructions(lean, window), run=session.execute, groups=set(LEAN) if lean else None, resources=Resources(session))
    s = session

    def tool_groups(a):
        groups = sorted({t.group for t in server.tools.values()})
        on = set(groups) if server.groups is None else set(server.groups)
        if a.get("enable") or a.get("disable"):
            bad = [g for g in (a.get("enable") or []) + (a.get("disable") or []) if g not in groups]
            if bad:
                raise ToolError("invalid_input", f"{bad[0]}: not a group — they are: {', '.join(groups)}.")
            on |= set(a.get("enable") or [])
            on -= set(a.get("disable") or []) - {"core"}
            server.set_groups(on)
        return {"groups": [{"group": g, "on": g in on, "tools": sorted(t.name for t in server.tools.values() if t.group == g), "about": info.get(g, "")} for g in groups], "note": "Every tool is callable by name, listed or not."}

    def guide_read(a):
        topic = a.get("topic")
        if not topic:
            return {"guides": _guide_index()}
        p = _guide_files().get(topic)
        if not p:
            raise ToolError("not_found", f"{topic}: no such guide — they are: {', '.join(g['topic'] for g in _guide_index())}.")
        with open(p, encoding="utf-8") as f:
            text = f.read()
        sec = a.get("section")
        if sec:
            parts = text.split("\n## ")
            hit = [x for x in parts[1:] if x.lower().startswith(sec.lower())]
            if not hit:
                raise ToolError("not_found", f"section {sec!r}: the sections are: {', '.join(x.splitlines()[0] for x in parts[1:])}.")
            text = "## " + hit[0]
        return {"topic": topic, "text": text}

    def batch(a):
        import json as _json

        calls = a.get("calls") or []
        # Some clients send a large value as a string of its JSON: the list,
        # a call, or a call's arguments.
        if isinstance(calls, str):
            try:
                calls = _json.loads(calls)
            except ValueError as e:
                raise ToolError("invalid_input", f"calls came as a string that is not JSON ({e}) — give a list: [{{ tool, arguments }}].")
            calls = calls.get("calls", calls) if isinstance(calls, dict) else calls
        if not isinstance(calls, list):
            raise ToolError("invalid_input", "calls: a list of { tool, arguments }.")
        fixed = []
        for i, c in enumerate(calls):
            if isinstance(c, str):
                try:
                    c = _json.loads(c)
                except ValueError:
                    raise ToolError("invalid_input", f"calls.{i} came as a string that is not a call's JSON — each call is {{ tool, arguments }}. Nothing was run.")
            if not isinstance(c, dict) or not isinstance(c.get("tool"), str):
                raise ToolError("invalid_input", f"calls.{i}: {{ tool, arguments }} with tool a tool's name. Nothing was run.")
            args = c.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = _json.loads(args) if args.strip() else {}
                except ValueError:
                    raise ToolError("invalid_input", f"calls.{i}.arguments ({c['tool']}) came as a string that is not JSON — give an object, {{}} for none. Nothing was run.")
            if not isinstance(args, dict):
                raise ToolError("invalid_input", f"calls.{i}.arguments ({c['tool']}): an object. Nothing was run.")
            fixed.append({"tool": c["tool"], "arguments": args})
        calls = fixed
        unknown = [c["tool"] for c in calls if c["tool"] not in server.tools]
        if unknown:
            raise ToolError("not_found", f"No tool named {', '.join(sorted(set(unknown)))} — nothing was run. tool_groups lists them all.")
        rollback = a.get("rollback", True)
        stop = a.get("stopOnError", True)
        mark = s.doc.UndoCount
        results = []
        for i, c in enumerate(calls):
            if c.get("tool") == "batch":
                raise ToolError("invalid_input", f"calls.{i}: a batch does not run another batch.")
            r = server.call({"name": c["tool"], "arguments": c["arguments"]})

            body = _json.loads(r["content"][0]["text"])
            ok = not r.get("isError")
            results.append({"tool": c.get("tool"), "ok": ok, **({"result": body} if ok else {"error": body.get("error")})})
            if not ok and stop:
                out = {"results": results, "stopped": i}
                if rollback:
                    undone = 0
                    while s.doc.UndoCount > mark:
                        s.doc.undo()
                        undone += 1
                    s.doc.recompute()
                    s.snapshot("batch rolled back")
                    out["undone"] = undone
                return out
        return {"results": results, "ran": len(results)}

    def history(action):
        def run(a):
            d = s.doc
            if action == "undo":
                n = int(a.get("steps", 1))
                names = list(d.UndoNames)[:n]
                for _ in range(n):
                    if d.UndoCount == 0:
                        break
                    d.undo()
            elif action == "redo":
                n = int(a.get("steps", 1))
                names = list(d.RedoNames)[:n]
                for _ in range(n):
                    if d.RedoCount == 0:
                        break
                    d.redo()
            elif action == "checkpoint":
                cid = f"cp{len(s.checkpoints) + 1}"
                s.checkpoints[cid] = {"undo": d.UndoCount, "step": len(s.steps)}
                return {"checkpoint": cid, "undoCount": d.UndoCount, "step": len(s.steps)}
            elif action == "restore":
                cp = s.checkpoints.get(a.get("checkpoint"))
                if not cp:
                    raise ToolError("not_found", f"checkpoint {a.get('checkpoint')!r}: the checkpoints are {', '.join(s.checkpoints) or 'none'} — history_checkpoint takes one.")
                names = []
                while d.UndoCount > cp["undo"]:
                    names.append(d.UndoNames[0])
                    d.undo()
            d.recompute()
            s.snapshot(f"history_{action}")
            return {"done": action, "steps": names, "undoCount": d.UndoCount, "redoCount": d.RedoCount, "bodies": [o.Label for o in s.bodies()]}

        return run

    core = [
        Tool("tool_groups", "The tool groups, which are listed and what each holds; enable / disable: [group …] lists or hides them (core stays). Every tool is callable by name either way.", obj({"enable": {"type": "array", "items": {"type": "string"}}, "disable": {"type": "array", "items": {"type": "string"}}}), tool_groups),
        Tool("guide_read", "How to do a task with these tools: the workflow and the traps the tools' own descriptions do not carry. No topic: the index. topic: a guide whole; section: one of its ## sections.", obj({"topic": {"type": "string"}, "section": {"type": "string"}}), guide_read),
        Tool("batch", "Run several tools in one call, in order: calls [{ tool, arguments }]. The first refused stops the rest (stopOnError: false runs them all), and with rollback (the default) undoes what the batch changed before it, so it stands or falls whole.", obj({"calls": {"type": "array", "items": {"type": "object"}}, "rollback": {"type": "boolean"}, "stopOnError": {"type": "boolean"}}, ["calls"]), batch),
        Tool("history_undo", "Undo the last change (steps: how many), as FreeCAD's Edit › Undo does.", obj({"steps": {"type": "integer"}}), history("undo")),
        Tool("history_redo", "Redo what was undone (steps: how many).", obj({"steps": {"type": "integer"}}), history("redo")),
        Tool("history_checkpoint", "Mark the session now, to come back to with history_restore or measure from with history_diff { since: its step }.", obj({}), history("checkpoint")),
        Tool("history_restore", "Undo every change since a checkpoint.", obj({"checkpoint": {"type": "string"}}, ["checkpoint"]), history("restore")),
    ]
    for t in core:
        t.group = "core"
        server.add(t)
    return server
