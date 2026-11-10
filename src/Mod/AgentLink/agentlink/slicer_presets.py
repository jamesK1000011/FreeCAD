# SPDX-License-Identifier: LGPL-2.1-or-later
"""Slicer presets as data: the person's own printer, process and filament
presets read from where Bambu Studio and OrcaSlicer keep them, their
inheritance resolved (a user preset on top of a system one), and kept or
passed on as one JSON bundle that can be exported and imported. Nothing of
a printer is written into the code: the minimal profiles used when no
preset is chosen are a data file too (data/slicer-minimal.json).

A bundle is
    {"agentlinkPresets": 1, "slicer": "bambustudio", "name": "...",
     "machine": {...}, "process": {...}, "filament": {...}}
each part the slicer's own JSON, flattened (no "inherits")."""

import glob
import json
import os

KINDS = ("machine", "process", "filament")
HOME = os.path.expanduser("~")
_SUPPORT = os.path.join(HOME, "Library", "Application Support")

# Where each slicer keeps its presets, newest first: the person's own, the
# system ones it downloaded, then the ones inside the app.
ROOTS = {
    "bambustudio": (
        ("AGENTLINK_BAMBU_PRESETS",),
        [os.path.join(_SUPPORT, "BambuStudio", "user"), os.path.join(_SUPPORT, "BambuStudio", "system"),
         "/Applications/BambuStudio.app/Contents/Resources/profiles",
         os.path.join(HOME, ".config", "BambuStudio", "user"), os.path.join(HOME, ".config", "BambuStudio", "system")],
    ),
    "orcaslicer": (
        ("AGENTLINK_ORCA_PRESETS",),
        [os.path.join(_SUPPORT, "OrcaSlicer", "user"), os.path.join(_SUPPORT, "OrcaSlicer", "system"),
         "/Applications/OrcaSlicer.app/Contents/Resources/profiles",
         os.path.join(HOME, ".config", "OrcaSlicer", "user"), os.path.join(HOME, ".config", "OrcaSlicer", "system")],
    ),
}

MINIMAL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "slicer-minimal.json")


class PresetError(Exception):
    pass


def roots(slicer):
    env, places = ROOTS[slicer]
    given = [p for e in env for p in (os.environ.get(e) or "").split(os.pathsep) if p]
    return [p for p in (given or places) if os.path.isdir(p)]


def _kind_of(path, data):
    t = str(data.get("type") or "").lower()
    if t in KINDS:
        return t
    if t:
        return None  # machine_model and the like: not a preset to choose
    for k in KINDS:
        if f"{os.sep}{k}{os.sep}" in path:
            return k
    return None


def scan(slicer):
    """{kind: {name: [entry …]}}, an entry {path, root, user, listed}; the
    same name in several places is kept in the order of roots (the person's
    own first)."""
    index = {k: {} for k in KINDS}
    for root in roots(slicer):
        user = os.sep + "user" in root
        for path in sorted(glob.glob(os.path.join(root, "**", "*.json"), recursive=True)):
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                continue
            if not isinstance(data, dict) or "name" not in data:
                continue
            kind = _kind_of(path, data)
            if kind is None:
                continue
            listed = str(data.get("instantiation", "true")).lower() != "false"
            index[kind].setdefault(data["name"], []).append({"path": path, "root": root, "user": user, "listed": listed})
    return index


def listing(slicer, kind=None, query=None, limit=60):
    """The presets that can be chosen (not the base ones others inherit from),
    the person's own marked, filtered by words in query."""
    index = scan(slicer)
    words = [w.lower() for w in (query or "").split()]
    out = {}
    for k in ([kind] if kind else KINDS):
        names = []
        for name, entries in index[k].items():
            if not entries[0]["listed"]:
                continue
            if all(w in name.lower() for w in words):
                names.append({"name": name, **({"own": True} if entries[0]["user"] else {})})
        names.sort(key=lambda x: (not x.get("own"), x["name"]))
        out[k] = {"count": len(names), "presets": names[:limit]}
    return out


def resolve(index, kind, name, _seen=None):
    """A preset with what it inherits merged under it: the slicer's JSON,
    flat."""
    seen = _seen or []
    if name in seen:
        raise PresetError(f"{kind} preset {name!r} inherits from itself ({' > '.join(seen + [name])}).")
    entries = index[kind].get(name)
    if not entries:
        raise PresetError(f"No {kind} preset named {name!r}: slicer_presets list shows those there are.")
    with open(entries[0]["path"], encoding="utf-8") as f:
        data = json.load(f)
    parent = data.get("inherits")
    flat = {}
    if parent:
        flat = resolve(index, kind, parent, seen + [name])
    flat.update({k: v for k, v in data.items() if k != "inherits"})
    flat["name"] = name
    return flat


def bundle(slicer, machine, process, filament, name=None):
    index = scan(slicer)
    if not roots(slicer):
        raise PresetError(f"No {slicer} presets on this computer (looked in {', '.join(ROOTS[slicer][1][:3])}).")
    out = {"agentlinkPresets": 1, "slicer": slicer, "name": name or machine}
    for kind, n in zip(KINDS, (machine, process, filament)):
        out[kind] = resolve(index, kind, n)
    return out


def check_bundle(data):
    if not isinstance(data, dict) or data.get("agentlinkPresets") != 1:
        raise PresetError("Not a presets bundle: one exported by slicer_presets export ({ agentlinkPresets: 1, slicer, machine, process, filament }).")
    if data.get("slicer") not in ROOTS:
        raise PresetError(f"The bundle's slicer {data.get('slicer')!r} is not one of {', '.join(ROOTS)}.")
    for k in KINDS:
        if not isinstance(data.get(k), dict):
            raise PresetError(f"The bundle has no {k}.")
    return data


def folder(out_dir):
    return os.path.join(out_dir, "presets")


def save(out_dir, data, name=None):
    """A bundle kept with the person's files, to choose by name later."""
    check_bundle(data)
    name = (name or data.get("name") or "presets").strip()
    safe = "".join(c if c.isalnum() or c in " -_.@" else "_" for c in name).strip() or "presets"
    os.makedirs(folder(out_dir), exist_ok=True)
    path = os.path.join(folder(out_dir), safe + ".json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({**data, "name": name}, f, indent=1)
    return name, path


def load_saved(out_dir, name):
    for path in glob.glob(os.path.join(folder(out_dir), "*.json")):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        if data.get("name") == name or os.path.splitext(os.path.basename(path))[0] == name:
            return check_bundle(data)
    raise PresetError(f"No saved presets bundle named {name!r} in {folder(out_dir)}: slicer_presets import it first.")


def saved(out_dir):
    out = []
    for path in sorted(glob.glob(os.path.join(folder(out_dir), "*.json"))):
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
            out.append({"name": d.get("name"), "slicer": d.get("slicer"), "machine": d["machine"].get("name"), "process": d["process"].get("name"), "filament": d["filament"].get("name")})
        except Exception:
            continue
    return out


def write_profiles(data, folder_):
    """The bundle's three parts as the files the slicer's command line loads.
    Loaded together as new presets, Bambu Studio counts a process or a
    filament compatible only when it names the printer: the printer is added
    to their lists (the person chose them together)."""
    os.makedirs(folder_, exist_ok=True)
    printer = data["machine"].get("name")
    paths = {}
    for k in KINDS:
        part = dict(data[k])
        part["from"] = "User"
        part["instantiation"] = "true"
        if k != "machine" and printer:
            cp = list(part.get("compatible_printers") or [])
            if printer not in cp:
                cp.append(printer)
            part["compatible_printers"] = cp
            part["compatible_printers_condition"] = ""
        p = os.path.join(folder_, f"agentlink-{k}.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(part, f, indent=1)
        paths[k] = p
    return paths


def _first(v):
    return v[0] if isinstance(v, list) and v else v


def setup_from(data):
    """What a chosen bundle says of the print setup's own settings — so the
    mass estimate, the checks and the slicer agree: layer height, walls,
    infill, top and bottom layers, nozzle."""
    p, m = data["process"], data["machine"]
    out = {}

    def num(v, cast=float):
        try:
            return cast(str(_first(v)).rstrip("%"))
        except (TypeError, ValueError):
            return None

    for key, src, cast in (("layerHeight", p.get("layer_height"), float), ("firstLayer", p.get("initial_layer_print_height"), float),
                           ("walls", p.get("wall_loops"), int), ("infill", p.get("sparse_infill_density"), float),
                           ("topLayers", p.get("top_shell_layers"), int), ("bottomLayers", p.get("bottom_shell_layers"), int),
                           ("nozzle", m.get("nozzle_diameter"), float)):
        v = num(src, cast)
        if v is not None:
            out[key] = v
    return out


def minimal(settings):
    """The minimal profiles from data/slicer-minimal.json, the setup's
    values put in: {machine, process, filament} dicts."""
    with open(MINIMAL, encoding="utf-8") as f:
        tpl = json.load(f)
    vol = settings.get("volume") or (220, 220, 250)
    values = {
        "layerHeight": settings.get("layerHeight", 0.2), "firstLayer": settings.get("firstLayer", settings.get("layerHeight", 0.2)),
        "walls": int(settings.get("walls", 2)), "infill": int(settings.get("infill", 15)),
        "topLayers": int(settings.get("topLayers", 5)), "bottomLayers": int(settings.get("bottomLayers", 4)),
        "nozzle": settings.get("nozzle", 0.4), "flavor": settings.get("flavor", "marlin"),
        "volumeX": vol[0], "volumeY": vol[1], "volumeZ": vol[2],
        "filament": str(settings.get("filament", "PLA")).upper(), "density": settings.get("density", 1.24),
        "filamentDiameter": settings.get("filamentDiameter", 1.75),
    }

    def fill(v):
        if isinstance(v, str):
            try:
                return v.format(**values)
            except (KeyError, IndexError, ValueError):
                return v
        if isinstance(v, list):
            return [fill(x) for x in v]
        if isinstance(v, dict):
            return {k: fill(x) for k, x in v.items()}
        return v

    return {k: fill(tpl[k]) for k in KINDS}
