# SPDX-License-Identifier: LGPL-2.1-or-later
# Slicing with the slicer the person uses: PrusaSlicer, OrcaSlicer or Bambu
# Studio by their command lines, with their own profiles by path or minimal
# ones written from the print setup; the pauses for parts encased mid-print
# (hw_embed, gen_make's magnets) written into the G-code.

import os

from .. import embeds as E
from .. import printability, slicers, store
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
        o = s.body(a.get("body"))
        name = (a.get("slicer") or "prusaslicer").lower()
        if name not in slicers.NAMES:
            raise ToolError("invalid_input", f"slicer: one of {', '.join(slicers.NAMES)}.")
        setup = store.setup_of(s.doc, o.Label, "print")
        if not setup:
            raise ToolError("invalid_state", f"{o.Label} is in no print setup: setup add {{ type: \"print\", bodies: [\"{o.Label}\"] }} once the person says it is printed.")
        profiles = a.get("profiles") or {}
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
        try:
            exe, used = slicers.slice_with(name, stl, gcode, settings, profiles)
        except FileNotFoundError as e:
            raise ToolError("unavailable", str(e))
        except RuntimeError as e:
            raise ToolError("failed", str(e))
        read = slicers.read_gcode(gcode)
        want = pauses_for(s, o, setup)
        cmd = a.get("pauseCommand") or slicers.PAUSE[name]
        done = slicers.insert_pauses(gcode, want, cmd) if want else []
        out = {
            "body": o.Label, "slicer": name, "exe": os.path.basename(exe), "profiles": used, "gcode": gcode, "bytes": os.path.getsize(gcode),
            **read, "setup": f"{setup['name']} ({setup['id']})",
        }
        if want:
            out["pauses"] = done
            out["pauseCommand"] = cmd
            if len(done) < len(want):
                out["warning"] = f"{len(want) - len(done)} pause(s) not written: the G-code has no layer above them — check the part's height and the layer marks."
        out["note"] = ("Profiles written from the print setup, the slicer's defaults for the rest: its own printer profile is the authority. " if used in ("written", "setup") else "") + "Starting the print is the person's call."
        return out

    return [
        Tool(
            "slice_with",
            "Slice a body in a print setup with slicer prusaslicer (default), orcaslicer or bambustudio, by its command line: profiles { machine, process, filament } JSON paths for OrcaSlicer and Bambu Studio (minimal ones written from the setup when left out), { config } an .ini for PrusaSlicer. Gives time, filament and layers, and writes in a pause after the layer over each encased part (hw_embed, gen_make magnets; M601, Bambu M400 U1, or pauseCommand). Trap: a slicer not on this computer is refused unavailable, naming those present (AGENTLINK_ORCASLICER, AGENTLINK_BAMBUSTUDIO set their paths).",
            obj({"body": {"type": "string"}, "slicer": {"type": "string", "enum": list(slicers.NAMES)}, "profiles": {"type": "object"}, "pauseCommand": {"type": "string"}, "name": {"type": "string"}}),
            slice_with,
        ),
    ]
