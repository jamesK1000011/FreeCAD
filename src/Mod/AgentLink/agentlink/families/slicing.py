# SPDX-License-Identifier: LGPL-2.1-or-later
# Slicing with the slicer the person uses: PrusaSlicer, OrcaSlicer or Bambu
# Studio by their command lines, with their own profiles by path or minimal
# ones written from the print setup; the pauses for parts encased mid-print
# (hw_embed, gen_make's magnets) written into the G-code.

import json
import os

from .. import embeds as E
from .. import printability, slicer_presets as SP, slicers, store
from . import embed as EM
from ..protocol import Tool, ToolError

GROUP = "slice"
INFO = "Slicing with PrusaSlicer, OrcaSlicer or Bambu Studio, profiles by path, the encased parts' pauses written in."

DENSITY = {"pla": 1.24, "petg": 1.27, "abs": 1.04, "asa": 1.07, "pa6-cf": 1.12, "tpu-95a": 1.21}


def obj(props, required=()):
    return {"type": "object", "properties": props, "required": list(required), "additionalProperties": False}


def pauses_for(session, o, setup):
    """The pauses a body's print needs: [{ z, note }] from the bed, z the
    top of the layer after which it stops — what insert_pauses takes."""
    first, lh = E.grid(setup)
    recs, _ = EM.live(session, o)
    meta = session.meta.get(o.Label) or {}
    items = [{"id": r["id"], "layer": r["layer"], "note": r["note"]} for r in recs]
    enc = meta.get("encased")
    if enc:
        k, _ = E.layer_at_or_above(enc["top"], first, lh)
        items.append({"id": "gen_make", "layer": k, "note": f"Insert {enc['count']} {enc['what']}"})
    return [{"z": p["z"], "note": p["note"], "afterLayer": p["afterLayer"]} for p in E.pauses(items, first, lh)]


def tools(session):
    s = session

    def slice_with(a):
        others = [s.body(b) for b in (a.get("bodies") or [])]
        o = others.pop(0) if others and not a.get("body") else s.body(a.get("body"))
        name = (a.get("slicer") or "prusaslicer").lower()
        if name not in slicers.NAMES:
            raise ToolError("invalid_input", f"slicer: one of {', '.join(slicers.NAMES)}.")
        setup = store.setup_of(s.doc, o.Label, "print")
        if not setup:
            raise ToolError("invalid_state", f"{o.Label} is in no print setup: setup add {{ type: \"print\", bodies: [\"{o.Label}\"] }} once the person says it is printed.")
        profiles = a.get("profiles") or {}
        chosen = setup.get("slicerPresets")
        preset_note = None
        if not profiles and chosen and name != "prusaslicer":
            try:
                data = SP.load_saved(s.out_dir, chosen["bundle"]) if chosen.get("bundle") else SP.bundle(chosen.get("slicer", name), chosen["machine"], chosen["process"], chosen["filament"])
            except (SP.PresetError, KeyError) as e:
                raise ToolError("invalid_state", f"The setup's presets cannot be read: {e}")
            if data["slicer"] != name:
                raise ToolError("invalid_input", f"The setup's presets are {data['slicer']}'s: slicer \"{data['slicer']}\", or slicer_presets use others.")
            profiles = SP.write_profiles(data, os.path.join(s.out_dir, f"{o.Label.replace(' ', '_')}-{name}-presets"))
            preset_note = f"{data['machine'].get('name')} / {data['process'].get('name')} / {data['filament'].get('name')}"
        for k, v in profiles.items():
            if k not in ("machine", "process", "filament", "config"):
                raise ToolError("invalid_input", f"profiles.{k}: machine, process, filament (JSON, OrcaSlicer and Bambu Studio) or config (an .ini, PrusaSlicer).")
            if v and not os.path.isfile(v):
                raise ToolError("not_found", f"profiles.{k}: {v} is not a file.")
        settings = {k: v for k, v in setup.items() if k not in ("id", "name", "type", "bodies")}
        mat = store.materials(s.doc).get(o.Label) or "pla"
        settings["filament"] = mat.split("-")[0]
        settings["density"] = DENSITY.get(mat, 1.24)
        settings["volume"] = printability.PRINTERS.get(setup.get("printer") or "generic", printability.PRINTERS["generic"])
        import MeshPart

        base = os.path.join(s.out_dir, a.get("name") or f"{o.Label.replace(' ', '_')}-{name}")
        stl, gcode = base + ".stl", base + ".gcode"
        MeshPart.meshFromShape(Shape=o.Shape, LinearDeflection=0.01, AngularDeflection=0.0873, Relative=False).write(stl)
        stls = [stl]
        for k, b in enumerate(x for x in others if x is not o):
            p = f"{base}-{k + 2}.stl"
            MeshPart.meshFromShape(Shape=b.Shape, LinearDeflection=0.01, AngularDeflection=0.0873, Relative=False).write(p)
            stls.append(p)
        options = {k: bool(a.get(k)) for k in ("orient", "arrange") if a.get(k)}
        if a.get("overrides"):
            if name == "prusaslicer":
                raise ToolError("invalid_input", "overrides: OrcaSlicer's and Bambu Studio's process settings (their JSON keys).")
            options["overrides"] = a["overrides"]
        if a.get("project"):
            options["project"] = base + ".3mf"
        if (options or len(stls) > 1) and name == "prusaslicer":
            raise ToolError("invalid_input", "orient, arrange, project and several bodies are OrcaSlicer's and Bambu Studio's: slicer orcaslicer.")
        try:
            exe, used = slicers.slice_with(name, stls if len(stls) > 1 else stl, gcode, settings, profiles, options=options)
        except FileNotFoundError as e:
            raise ToolError("unavailable", str(e))
        except RuntimeError as e:
            raise ToolError("failed", str(e))
        read = slicers.read_gcode(gcode)
        # Pauses are a body's own heights: with several on a plate (and moved by orient), none written.
        want = pauses_for(s, o, setup) if len(stls) == 1 and not a.get("orient") else []
        cmd = a.get("pauseCommand") or slicers.PAUSE[name]
        done = slicers.insert_pauses(gcode, want, cmd) if want else []
        out = {
            "body": o.Label, "slicer": name, "exe": os.path.basename(exe), "profiles": f"the person's presets: {preset_note}" if preset_note else used, "gcode": gcode, "bytes": os.path.getsize(gcode),
            **read, "setup": f"{setup['name']} ({setup['id']})",
        }
        if a.get("overrides"):
            out["overrides"] = a["overrides"]
        out.update(slicers.LAST)  # the slicer's warnings and plate statistics
        if len(stls) > 1:
            out["bodies"] = [o.Label] + [b.Label for b in others if b is not o]
        if options.get("project") and os.path.exists(options["project"]):
            out["project"] = options["project"]
        if want:
            out["pauses"] = done
            out["pauseCommand"] = cmd
            if len(done) < len(want):
                out["warning"] = f"{len(want) - len(done)} pause(s) not written: the G-code has no layer above them — check the part's height and the layer marks."
        out["note"] = ("Profiles written from the print setup, the slicer's defaults for the rest: the person's own presets (slicer_presets use) are the authority. " if used in ("written", "setup") and not preset_note else "") + "Starting the print is the person's call."
        return out


    def slicer_presets(a):
        action = a.get("action", "list")
        slicer = (a.get("slicer") or "bambustudio").lower()
        if slicer not in SP.ROOTS:
            raise ToolError("invalid_input", f"slicer: {', '.join(SP.ROOTS)} (PrusaSlicer takes an .ini: slice_with profiles.config).")
        try:
            if action == "list":
                if not SP.roots(slicer):
                    raise ToolError("unavailable", f"No {slicer} presets on this computer.")
                kind = a.get("kind")
                if kind and kind not in SP.KINDS:
                    raise ToolError("invalid_input", f"kind: {', '.join(SP.KINDS)}.")
                out = {"slicer": slicer, "from": SP.roots(slicer), **SP.listing(slicer, kind, a.get("query")), "saved": SP.saved(s.out_dir),
                       "next": "slicer_presets use { machine, process, filament } (names from here) or { bundle } (a saved one) for a body's print setup."}
                if slicer == "orcaslicer" and SP.packed_only(slicer) and not SP.orca_cached():
                    out["note"] = "This OrcaSlicer packs its system presets (.opc), which cannot be read here: slicer_presets fetch { vendors: [\"BBL\", …] } brings the same version's as JSON. The person's own presets are read either way."
                return out
            if action == "fetch":
                if slicer != "orcaslicer":
                    raise ToolError("invalid_input", "fetch: OrcaSlicer's system presets (slicer: orcaslicer).")
                vendors = a.get("vendors") or ([a["vendor"]] if a.get("vendor") else None)
                if not vendors:
                    raise ToolError("invalid_input", "vendors: the preset folders to fetch, e.g. [\"BBL\"] (Bambu Lab), [\"Prusa\"], [\"Creality\"].")
                return {**SP.fetch_orca(vendors, a.get("version")), "next": "slicer_presets list { slicer: orcaslicer, kind, query } now shows them."}
            if action == "use":
                o = s.body(a.get("body"))
                sets = store.setups(s.doc)
                setup = next((x for x in sets if o.Label in x["bodies"] and x["type"] == "print"), None)
                if not setup:
                    raise ToolError("invalid_state", f"{o.Label} is in no print setup: setup add {{ type: \"print\", bodies: [\"{o.Label}\"] }} first.")
                if a.get("bundle"):
                    data = SP.load_saved(s.out_dir, a["bundle"])
                    choice = {"bundle": a["bundle"]}
                else:
                    missing = [k for k in SP.KINDS if not a.get(k)]
                    if missing:
                        raise ToolError("invalid_input", f"{', '.join(missing)}: preset names (slicer_presets list), or bundle: a saved one.")
                    data = SP.bundle(slicer, a["machine"], a["process"], a["filament"])
                    choice = {"slicer": slicer, "machine": a["machine"], "process": a["process"], "filament": a["filament"]}
                took = SP.setup_from(data)
                setup.update(took)
                setup["slicerPresets"] = choice
                store.save_setups(s.doc, sets)
                return {"setup": f"{setup['name']} ({setup['id']})", "presets": {k: data[k].get("name") for k in SP.KINDS}, "slicer": data["slicer"],
                        "settingsFromPresets": took, "note": "The setup's layer height, walls, infill and shells now come from the presets, so the printability check and the mass estimate read the print as the slicer will lay it."}
            if action == "export":
                if a.get("bundle"):
                    data = SP.load_saved(s.out_dir, a["bundle"])
                elif all(a.get(k) for k in SP.KINDS):
                    data = SP.bundle(slicer, a["machine"], a["process"], a["filament"], a.get("name"))
                else:
                    o = s.body(a.get("body"))
                    setup = store.setup_of(s.doc, o.Label, "print")
                    ch = (setup or {}).get("slicerPresets")
                    if not ch:
                        raise ToolError("invalid_input", "machine, process and filament names, bundle, or a body whose print setup has presets chosen.")
                    data = SP.load_saved(s.out_dir, ch["bundle"]) if ch.get("bundle") else SP.bundle(ch.get("slicer", slicer), ch["machine"], ch["process"], ch["filament"], a.get("name"))
                name = a.get("name") or data.get("name")
                path = a.get("path") or os.path.join(SP.folder(s.out_dir), "export", ("".join(c if c.isalnum() or c in " -_.@" else "_" for c in name) or "presets") + ".json")
                os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
                with open(path, "w", encoding="utf-8") as f:
                    json.dump({**data, "name": name}, f, indent=1)
                kept = SP.save(s.out_dir, {**data, "name": name}, name)[0]
                return {"exported": path, "name": kept, "presets": {k: data[k].get("name") for k in SP.KINDS},
                        "note": "One file, the presets flattened: slicer_presets import it on another computer, or give it to someone without this slicer's presets."}
            if action == "import":
                path = a.get("path")
                if not path or not os.path.isfile(os.path.expanduser(path)):
                    raise ToolError("not_found", "path: a presets bundle file (slicer_presets export).")
                with open(os.path.expanduser(path), encoding="utf-8") as f:
                    data = SP.check_bundle(json.load(f))
                name, kept = SP.save(s.out_dir, data, a.get("name"))
                return {"imported": name, "kept": kept, "presets": {k: data[k].get("name") for k in SP.KINDS}, "next": f"slicer_presets use {{ body, bundle: \"{name}\" }}."}
        except SP.PresetError as e:
            raise ToolError("not_found", str(e))
        except (OSError, ValueError) as e:
            raise ToolError("failed", f"Presets could not be read or written: {e}")
        raise ToolError("invalid_input", "action: list, use, export, import or fetch.")

    def _resolver():
        idx = {}

        def resolve(slicer, kind, name):
            if slicer not in idx:
                idx[slicer] = SP.scan(slicer)
            return SP.resolve(idx[slicer], kind, name)

        return resolve

    def slice_read(a):
        from .. import threemf

        p = os.path.expanduser(a["path"])
        if not os.path.isabs(p):
            p = os.path.join(s.out_dir, p)
        if not os.path.isfile(p):
            raise ToolError("not_found", f"{p}: no such file.")
        sliced = None
        if a.get("slice"):
            try:
                app = threemf.read(p).get("slicer") or "orcaslicer"
            except Exception as e:
                raise ToolError("invalid_input", f"{os.path.basename(p)} is not a slicer project: {e}")
            outdir = os.path.splitext(p)[0] + "-sliced"
            try:
                exe, found = slicers.slice_project(app, p, outdir, export=os.path.splitext(p)[0] + "-sliced.3mf")
            except FileNotFoundError as e:
                raise ToolError("unavailable", str(e))
            except RuntimeError as e:
                raise ToolError("failed", str(e))
            sliced = {"gcode": found, **slicers.read_gcode(found[0]), **slicers.LAST}
            p = os.path.splitext(p)[0] + "-sliced.3mf" if os.path.isfile(os.path.splitext(p)[0] + "-sliced.3mf") else p
        try:
            out = threemf.read(p, _resolver())
        except Exception as e:
            raise ToolError("invalid_input", f"{os.path.basename(p)} could not be read as a slicer project: {e}")
        if sliced:
            out["slicedNow"] = sliced
        return out

    def slice_open(a):
        import subprocess
        import sys

        import FreeCAD

        if not FreeCAD.GuiUp:
            raise ToolError("invalid_state", "slice_open needs the FreeCAD window (it opens the slicer's window for the person).")
        p = os.path.expanduser(a["path"])
        if not os.path.isabs(p):
            p = os.path.join(s.out_dir, p)
        if not os.path.isfile(p):
            raise ToolError("not_found", f"{p}: no such file.")
        name = a.get("slicer") or "orcaslicer"
        exe = slicers.find(name)
        if not exe:
            raise ToolError("unavailable", f"{name} is not on this computer.")
        if sys.platform == "darwin" and ".app/" in exe:
            subprocess.Popen(["open", "-a", exe.split(".app/")[0] + ".app", p])
        else:
            subprocess.Popen([exe, p], start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {"opened": p, "in": name, "next": "When the person has saved it there, slice_read { path } brings back what they changed (slice: true slices it as it stands)."}

    return [
        Tool(
            "slicer_presets",
            "The person's own slicer presets, as data: action list { slicer (bambustudio, orcaslicer), kind, query }; fetch { vendors } (OrcaSlicer, whose own system presets are packed: the same version's, as JSON, by git) the printer, process and filament presets on this computer (their own first) and the saved bundles; use { body, machine, process, filament | bundle } puts them on the body's print setup, which then takes its layer height, walls, infill and shells from them and slices with them; export { machine, process, filament | bundle | body, path, name } writes one flattened JSON bundle (and keeps it); import { path, name } keeps a bundle to use. Nothing of a printer is in the code.",
            obj({"action": {"type": "string", "enum": ["list", "use", "export", "import", "fetch"]}, "vendors": {"type": "array", "items": {"type": "string"}}, "vendor": {"type": "string"}, "version": {"type": "string"}, "slicer": {"type": "string", "enum": list(SP.ROOTS)}, "kind": {"type": "string", "enum": list(SP.KINDS)},
                 "query": {"type": "string"}, "body": {"type": "string"}, "machine": {"type": "string"}, "process": {"type": "string"}, "filament": {"type": "string"},
                 "bundle": {"type": "string"}, "path": {"type": "string"}, "name": {"type": "string"}}),
            slicer_presets,
        ),
        Tool(
            "slice_with",
            "Slice a body in a print setup with slicer prusaslicer (default), orcaslicer or bambustudio, by its command line: profiles { machine, process, filament } JSON paths for OrcaSlicer and Bambu Studio (minimal ones written from the setup when left out), { config } an .ini for PrusaSlicer. Gives time, filament and layers, and writes in a pause after the layer over each encased part (hw_embed, gen_make magnets; M601, Bambu M400 U1, or pauseCommand). OrcaSlicer and Bambu Studio also take bodies (several on one plate, arranged), orient and arrange (the slicer's own), and project: true (the sliced plate as a .3mf its window opens). Trap: a slicer not on this computer is refused unavailable, naming those present (AGENTLINK_ORCASLICER, AGENTLINK_BAMBUSTUDIO set their paths).",
            obj({"body": {"type": "string"}, "bodies": {"type": "array", "items": {"type": "string"}}, "slicer": {"type": "string", "enum": list(slicers.NAMES)}, "profiles": {"type": "object"}, "pauseCommand": {"type": "string"}, "name": {"type": "string"},
                 "orient": {"type": "boolean"}, "arrange": {"type": "boolean"}, "project": {"type": "boolean"}, "overrides": {"type": "object"}}),
            slice_with,
        ),
        Tool(
            "slice_read",
            "Read an OrcaSlicer or Bambu Studio project (.3mf) back — one Agent Link wrote, or one the person changed and saved in the slicer's window: the presets it names and each setting changed from them (project-wide and per filament), every object's own settings (and its modifier parts), the plates, the slicer's warnings, and when sliced its time, weight, cost and features. slice: true slices it as it stands first (its own settings, headless), with the same answer.",
            obj({"path": {"type": "string"}, "slice": {"type": "boolean"}}, ["path"]),
            slice_read,
        ),
        Tool(
            "slice_open",
            "Open a project (.3mf) in the slicer's own window for the person (OrcaSlicer by default, or bambustudio): they change what they like there and save, then slice_read brings it back. Window only.",
            obj({"path": {"type": "string"}, "slicer": {"type": "string", "enum": ["orcaslicer", "bambustudio"]}}, ["path"]),
            slice_open,
        ),
    ]
