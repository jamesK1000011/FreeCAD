# SPDX-License-Identifier: LGPL-2.1-or-later
"""An OrcaSlicer or Bambu Studio project (.3mf) read back: which presets it
names and which of their settings were changed (in the project, and object
by object), the plates and what is on them, the slicer's own warnings, and
the plate statistics when it was sliced. What the person did in the
slicer's window comes back to FreeCAD this way. Plain zip and XML: no
FreeCAD needed."""

import json
import re
import xml.etree.ElementTree as ET
import zipfile

KINDS = {"machine": "printer_settings_id", "process": "print_settings_id", "filament": "filament_settings_id"}
# Keys that say which preset, or where it came from: not settings.
NOT_SETTINGS = {"name", "inherits", "from", "setting_id", "instantiation", "version", "type", "compatible_printers",
                "compatible_printers_condition", "compatible_prints", "compatible_prints_condition", "filament_settings_id",
                "print_settings_id", "printer_settings_id", "filament_vendor", "_system", "is_custom_defined", "print_compatible_printers",
                "different_settings_to_system", "printer_model", "printer_variant", "default_print_profile", "default_filament_profile",
                "upward_compatible_machine", "filament_ids", "renamed_from", "description", "filament_notes", "process_notes", "printer_notes"}


def _norm(v):
    if isinstance(v, list):
        return [str(x) for x in v]
    return str(v)


def _meta(el):
    return {m.get("key"): m.get("value") for m in el.findall("metadata") if m.get("key") is not None}


def application(z):
    try:
        root = ET.fromstring(z.read("3D/3dmodel.model"))
    except (KeyError, ET.ParseError):
        return None
    for m in root.iter():
        if m.tag.endswith("metadata") and m.get("name") == "Application":
            return (m.text or "").strip()
    return None


def slicer_of(app):
    a = (app or "").lower()
    return "bambustudio" if "bambu" in a else ("orcaslicer" if "orca" in a else None)


def changed(project, preset_flat, index=None):
    """{key: [preset value, project value]} where the project differs.
    index: the filament's place, for a filament preset (the project keeps
    one value per filament where the preset has one)."""
    out = {}
    for k, pv in preset_flat.items():
        if k in NOT_SETTINGS or k.startswith("_") or k not in project:
            continue
        a, b = _norm(pv), _norm(project[k])
        i = index or 0
        if isinstance(b, list) and not isinstance(a, list):
            b = b[i] if i < len(b) else b
        if index is not None and isinstance(a, list) and isinstance(b, list) and len(a) == 1 and len(b) > 1:
            b = b[i:i + 1]
        if a != b:
            out[k] = [a, b]
    return out


def read(path, resolve=None):
    """The project in brief. resolve(slicer, kind, name) -> the preset
    flattened (slicer_presets), to say what was changed from it; None
    leaves that out."""
    out = {"path": path}
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        app = application(z)
        out["application"] = app
        slicer = slicer_of(app)
        out["slicer"] = slicer
        project = {}
        if "Metadata/project_settings.config" in names:
            try:
                project = json.loads(z.read("Metadata/project_settings.config"))
            except ValueError:
                project = {}
        presets = {}
        for kind, key in KINDS.items():
            v = project.get(key)
            if isinstance(v, list):
                presets[kind] = v if kind == "filament" else (v[0] if v else None)
            elif v:
                presets[kind] = [v] if kind == "filament" else v
        out["presets"] = presets
        if resolve and slicer and project:
            diffs, unknown = {}, []
            for kind in ("machine", "process"):
                if presets.get(kind):
                    try:
                        d = changed(project, resolve(slicer, kind, presets[kind]))
                        if d:
                            diffs[kind] = d
                    except Exception:
                        unknown.append(presets[kind])
            for i, fname in enumerate(presets.get("filament") or []):
                try:
                    d = changed(project, resolve(slicer, "filament", fname), i)
                    if d:
                        diffs[f"filament {i + 1}"] = d
                except Exception:
                    unknown.append(fname)
            out["changedFromPresets"] = diffs
            if unknown:
                out["presetsNotHere"] = sorted(set(unknown))
        objects = []
        if "Metadata/model_settings.config" in names:
            try:
                root = ET.fromstring(z.read("Metadata/model_settings.config"))
            except ET.ParseError:
                root = None
            if root is not None:
                for o in root.findall("object"):
                    m = _meta(o)
                    own = {k: v for k, v in m.items() if k not in ("name", "extruder", "source_file", "source_object_id", "source_volume_id", "source_offset_x", "source_offset_y", "source_offset_z", "matrix")}
                    parts = []
                    for part in o.findall("part"):
                        pm = _meta(part)
                        pown = {k: v for k, v in pm.items() if k not in ("name", "matrix", "source_file", "source_object_id", "source_volume_id", "source_offset_x", "source_offset_y", "source_offset_z", "extruder", "mesh_stat")}
                        if pown or part.get("subtype") not in (None, "normal_part"):
                            parts.append({"name": pm.get("name"), "kind": part.get("subtype"), **({"settings": pown} if pown else {})})
                    objects.append({"id": o.get("id"), "name": m.get("name"), **({"extruder": m["extruder"]} if m.get("extruder") else {}),
                                    **({"settings": own} if own else {}), **({"parts": parts} if parts else {})})
                plates = []
                for pl in root.findall("plate"):
                    pm = _meta(pl)
                    plates.append({"plate": pm.get("plater_id") or pm.get("index"), "name": pm.get("plater_name") or None,
                                   "objects": [_meta(i).get("object_id") for i in pl.findall("model_instance")]})
                if plates:
                    out["plates"] = plates
        out["objects"] = objects
        warnings, sliced = [], []
        if "Metadata/slice_info.config" in names:
            try:
                root = ET.fromstring(z.read("Metadata/slice_info.config"))
                for pl in root.findall("plate"):
                    pm = _meta(pl)
                    sliced.append({k: pm.get(k) for k in ("index", "prediction", "weight", "nozzle_diameters", "printer_model_id", "outside", "support_used") if pm.get(k) is not None})
                    for w in pl.findall("warning"):
                        warnings.append({k: w.get(k) for k in ("msg", "level", "error_code") if w.get(k)})
            except ET.ParseError:
                pass
        if sliced:
            out["sliced"] = sliced
        if warnings:
            out["warnings"] = warnings
        stats = sorted(n for n in names if re.match(r"Metadata/plate_\d+\.statistics\.json$", n))
        if stats:
            from .slicers import statistics
            import os
            import tempfile

            with tempfile.TemporaryDirectory() as d:
                p = os.path.join(d, "s.json")
                with open(p, "wb") as f:
                    f.write(z.read(stats[0]))
                try:
                    out["statistics"] = statistics(p)
                except (OSError, ValueError):
                    pass
        out["hasGcode"] = any(re.match(r"Metadata/plate_\d+\.gcode$", n) for n in names)
    return out
