# SPDX-License-Identifier: LGPL-2.1-or-later
# Sharing parts: a library pack of bodies as STEP and STL, with a README of
# their sizes and how to print them, a licence, and credits for the parts
# remixed — with the computer's paths and the user's name kept out of the
# files.

import os
import re
import tempfile

from .. import geometry as geo
from .. import packing, store
from ..protocol import Tool, ToolError

GROUP = "share"
INFO = "Sharing: library packs of bodies as STEP and STL with a README, a licence and credits, nothing personal in the files."

SETTING_WORDS = {"printer": "Printer", "nozzle": "Nozzle (mm)", "layerHeight": "Layer height (mm)", "firstLayer": "First layer (mm)", "walls": "Walls", "infill": "Infill (%)"}


def tools(session):
    s = session

    def library_pack(a):
        from ..tools import DENSITY

        lic = a.get("license")
        if lic not in packing.LICENSES:
            raise ToolError("invalid_input", f"license: one of {', '.join(packing.LICENSES)} — ask the person which; it decides what others may do with the parts.")
        author = (a.get("author") or "").strip()
        if lic in ("MIT", "CC-BY-4.0", "CC-BY-SA-4.0") and not author:
            raise ToolError("invalid_input", f"author: {lic} names who holds the copyright and is to be credited — the name the person wants shown (a handle will do).")
        credits = a.get("credits") or []
        if not isinstance(credits, list) or not all(isinstance(c, dict) for c in credits):
            raise ToolError("invalid_input", "credits: [{ title, author, url, license, changes }] for each remixed part.")
        errors, warnings = packing.check_credits(lic, credits)
        if errors:
            raise ToolError("invalid_input", "license: " + " ".join(errors))
        formats = a.get("formats") or ["step", "stl"]
        if not formats or any(f not in ("step", "stl") for f in formats):
            raise ToolError("invalid_input", "formats: step, stl or both.")
        objs = [s.body(b) for b in a["bodies"]] if a.get("bodies") else s.bodies()
        if not objs:
            raise ToolError("not_found", "No body in the document: make one first.")
        title = a.get("name") or (objs[0].Label if len(objs) == 1 else "Parts")
        slug = re.sub(r"[^A-Za-z0-9._-]+", "_", title).strip("_") or "parts"
        mats = store.materials(s.doc)
        import Mesh  # noqa: F401
        import MeshPart

        tmp = tempfile.mkdtemp()
        entries, parts, unsound = [], [], []
        settings, notes = {}, []
        for o in objs:
            fn = re.sub(r"[^A-Za-z0-9._-]+", "_", o.Label).strip("_") or "part"
            if not geo.soundness(o.Shape)["ok"]:
                unsound.append(o.Label)
            files = []
            if "step" in formats:
                p = os.path.join(tmp, fn + ".step")
                o.Shape.exportStep(p)
                with open(p, encoding="utf-8", errors="replace") as f:
                    text = packing.clean_step(f.read(), fn + ".step", author if lic != "CC0-1.0" else "")
                entries.append((f"{slug}/{fn}.step", text.encode("utf-8")))
                files.append(fn + ".step")
            if "stl" in formats:
                p = os.path.join(tmp, fn + ".stl")
                MeshPart.meshFromShape(Shape=o.Shape, LinearDeflection=float(a.get("tolerance", 0.01)), AngularDeflection=0.0873, Relative=False).write(p)
                with open(p, "rb") as f:
                    entries.append((f"{slug}/{fn}.stl", packing.clean_stl(f.read(), fn)))
                files.append(fn + ".stl")
            b = o.Shape.BoundBox
            m = mats.get(o.Label)
            parts.append({"files": files, "size": [round(b.XLength, 1), round(b.YLength, 1), round(b.ZLength, 1)], "volume": o.Shape.Volume, "material": m.upper() if m else None, "mass": round(o.Shape.Volume * DENSITY[m] / 1000, 1) if m in DENSITY else None})
            setup = store.setup_of(s.doc, o.Label, "print")
            if setup:
                for k, label in SETTING_WORDS.items():
                    if k in setup:
                        settings[label] = setup[k]
            enc = (s.meta.get(o.Label) or {}).get("encased")
            if enc:
                notes.append(f"{o.Label}: pause at {enc['top']} mm to drop in {enc['count']} {enc['what']}, or press them in after.")
        text = packing.readme(title, a.get("description"), author, lic, parts, settings, credits, notes)
        entries.append((f"{slug}/README.md", text.encode("utf-8")))
        entries.append((f"{slug}/LICENSE", packing.license_text(lic, author, title).encode("utf-8")))
        path = os.path.join(s.out_dir, f"{slug}.zip")
        packing.write_zip(path, entries)
        # What the pack holds may not carry the computer's paths or user name.
        leaks = []
        for name, data in entries:
            if name.endswith((".step", ".stl")):
                head = data[:4000].decode("utf-8", "replace")
                leaks += [name for x in packing.private_strings() if x in head]
        out = {"pack": path, "files": [n for n, _ in entries], "license": lic, "parts": [{"files": p["files"], "size": p["size"]} for p in parts], "bytes": os.path.getsize(path)}
        if warnings:
            out["warnings"] = warnings
        if unsound:
            out["unsound"] = {"bodies": unsound, "note": "body_check says these are not sound: fix them before sharing."}
        if leaks:
            out["personal"] = {"files": sorted(set(leaks)), "note": "A path or user name is still in these files' first lines: check them before sharing."}
        return out

    return [
        Tool(
            "library_pack",
            "Pack bodies to share: a zip of each as STEP and STL (formats), a README with their sizes, material and mass, and the print settings from their print "
            "setup (magnet pauses too), a LICENSE (license: CC-BY-4.0, CC-BY-SA-4.0, CC0-1.0 or MIT — ask the person; author is required for all but CC0), and "
            "credits [{ title, author, url, license, changes }] for remixed parts, refused when their licence does not allow the one chosen (no-derivatives, "
            "non-commercial, share-alike). The STEP header carries the file's name and the author given, no path, user name or organisation.",
            {
                "type": "object",
                "properties": {
                    "bodies": {"type": "array", "items": {"type": "string"}},
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "license": {"type": "string", "enum": list(packing.LICENSES)},
                    "author": {"type": "string"},
                    "credits": {"type": "array", "items": {"type": "object"}},
                    "formats": {"type": "array", "items": {"type": "string", "enum": ["step", "stl"]}},
                    "tolerance": {"type": "number"},
                },
                "required": ["license"],
                "additionalProperties": False,
            },
            library_pack,
        )
    ]
