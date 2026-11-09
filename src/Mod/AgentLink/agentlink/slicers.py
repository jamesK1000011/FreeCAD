# SPDX-License-Identifier: LGPL-2.1-or-later
# More slicers by their command lines: PrusaSlicer (as slicer.py drives it,
# or with a config bundle the person exported), OrcaSlicer and Bambu Studio
# (their --slice command line, which takes JSON machine, process and
# filament profiles: the person's own by path, or minimal ones written here
# from the print setup). The G-code each writes is read back and the pauses
# for parts encased mid-print written in, by each slicer's own layer marks.

import glob
import json
import os
import re
import shutil
import subprocess
import zipfile

from . import slicer as prusa

NAMES = ("prusaslicer", "orcaslicer", "bambustudio")
PAUSE = {"prusaslicer": "M601", "orcaslicer": "M601", "bambustudio": "M400 U1"}
_EXE = {
    "orcaslicer": ("AGENTLINK_ORCASLICER", ("orca-slicer", "OrcaSlicer", "orcaslicer"), ("/Applications/OrcaSlicer.app/Contents/MacOS/OrcaSlicer",)),
    "bambustudio": ("AGENTLINK_BAMBUSTUDIO", ("bambu-studio", "BambuStudio", "bambustudio"), ("/Applications/BambuStudio.app/Contents/MacOS/BambuStudio",)),
}


def find(name):
    if name == "prusaslicer":
        return prusa.find_slicer()
    env, names, places = _EXE[name]
    if os.environ.get(env):
        return os.environ[env]
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    return next((p for p in places if os.path.exists(p)), None)


def present():
    return {n: bool(find(n)) for n in NAMES}


def _run(args, timeout):
    run = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if run.returncode != 0 and not os.environ.get("DISPLAY") and shutil.which("xvfb-run"):
        run = subprocess.run(["xvfb-run", "-a"] + args, capture_output=True, text=True, timeout=timeout)
    return run


def minimal_profiles(settings, folder):
    """Machine, process and filament JSON from the print setup, written to
    folder: just the keys the setup says; the slicer's defaults do the rest."""
    lh = str(settings.get("layerHeight", 0.2))
    vol = settings.get("volume") or (220, 220, 250)
    mat = str(settings.get("filament", "PLA")).upper()
    machine = {
        "type": "machine", "name": "AgentLink machine", "from": "User", "instantiation": "true", "printer_technology": "FFF",
        "gcode_flavor": settings.get("flavor", "marlin"), "nozzle_diameter": [str(settings.get("nozzle", 0.4))],
        "printable_area": ["0x0", f"{vol[0]}x0", f"{vol[0]}x{vol[1]}", f"0x{vol[1]}"], "printable_height": str(vol[2]),
    }
    process = {
        "type": "process", "name": "AgentLink process", "from": "User", "instantiation": "true",
        "layer_height": lh, "initial_layer_print_height": str(settings.get("firstLayer", lh)),
        "wall_loops": str(int(settings.get("walls", 2))), "sparse_infill_density": f"{int(settings.get('infill', 15))}%",
    }
    filament = {
        "type": "filament", "name": f"AgentLink {mat}", "from": "User", "instantiation": "true",
        "filament_type": [mat], "filament_density": [str(settings.get("density", 1.24))], "filament_diameter": [str(settings.get("filamentDiameter", 1.75))],
    }
    out = {}
    for k, v in (("machine", machine), ("process", process), ("filament", filament)):
        p = os.path.join(folder, f"agentlink-{k}.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(v, f, indent=1)
        out[k] = p
    return out


def slice_with(name, stl, gcode, settings, profiles=None, timeout=900):
    """Slice stl into gcode with the named slicer. Returns (exe, profiles
    used: 'given' or 'written' or 'setup')."""
    exe = find(name)
    if not exe:
        how = "install PrusaSlicer, or set AGENTLINK_SLICER" if name == "prusaslicer" else f"install it, or set {_EXE[name][0]} to its command line"
        raise FileNotFoundError(f"{name} is not on this computer: {how}. present: {', '.join(k for k, v in present().items() if v) or 'none'}.")
    profiles = profiles or {}
    if name == "prusaslicer":
        if not profiles.get("config"):
            prusa.slice_stl(stl, gcode, settings, timeout)
            return exe, "setup"
        args = [exe, "--export-gcode", "--load", profiles["config"], "--output", gcode, stl]
        run = _run(args, timeout)
        if run.returncode != 0 or not os.path.exists(gcode):
            raise RuntimeError(_tail(run))
        return exe, "given"
    folder = os.path.dirname(gcode) or "."
    out_dir = os.path.join(folder, os.path.splitext(os.path.basename(gcode))[0] + f"-{name}")
    os.makedirs(out_dir, exist_ok=True)
    used = "given"
    if not all(profiles.get(k) for k in ("machine", "process", "filament")):
        written = minimal_profiles(settings, out_dir)
        profiles = {**written, **{k: v for k, v in profiles.items() if v}}
        used = "written" if not any(profiles.get(k) != written[k] for k in written) else "given and written"
    for k in ("machine", "process", "filament"):
        if not os.path.isfile(profiles[k]):
            raise RuntimeError(f"profiles.{k}: {profiles[k]} is not a file.")
    three_mf = os.path.join(out_dir, "out.3mf")
    args = [exe, "--slice", "0", "--load-settings", f"{profiles['machine']};{profiles['process']}", "--load-filaments", profiles["filament"], "--outputdir", out_dir]
    if name == "bambustudio":
        args += ["--export-3mf", three_mf]
    args.append(stl)
    run = _run(args, timeout)
    found = sorted(glob.glob(os.path.join(out_dir, "*.gcode")))
    if not found and os.path.isfile(three_mf):
        with zipfile.ZipFile(three_mf) as z:
            inner = sorted(n for n in z.namelist() if n.endswith(".gcode"))
            if inner:
                with z.open(inner[0]) as src, open(os.path.join(out_dir, "plate_1.gcode"), "wb") as dst:
                    shutil.copyfileobj(src, dst)
                found = [os.path.join(out_dir, "plate_1.gcode")]
    if not found:
        raise RuntimeError(_tail(run))
    shutil.copyfile(found[0], gcode)
    return exe, used


def _tail(run):
    tail = (run.stderr or run.stdout or "").strip().splitlines()[-3:]
    return f"The slicer stopped (code {run.returncode}): {' / '.join(tail) or 'no G-code written'}"


# ---- reading and pausing any of them ------------------------------------------------------

_LAYER = re.compile(r"^;\s*(LAYER_CHANGE|CHANGE_LAYER)\s*$")
_Z = re.compile(r"^;\s*(?:Z|Z_HEIGHT)\s*[:=]\s*([\d.]+)")


def read_gcode(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        text = f.read()

    def grab(*patterns):
        for pat in patterns:
            m = re.search(pat, text, re.M)
            if m:
                return m.group(1).strip()
        return None

    layers = sum(1 for line in text.splitlines() if _LAYER.match(line))
    g = grab(r";\s*(?:total )?filament used \[g\]\s*=\s*([\d.]+)", r";\s*total filament weight \[g\]\s*:\s*([\d.]+)")
    mm = grab(r";\s*(?:total )?filament used \[mm\]\s*=\s*([\d.]+)", r";\s*total filament length \[mm\]\s*:\s*([\d.]+)")
    t = grab(r";\s*estimated printing time \(normal mode\)\s*=\s*(.+)", r";\s*total estimated time\s*:\s*(.+)", r";\s*model printing time\s*:\s*([^;]+)")
    return {"layers": layers, "filamentG": float(g) if g else None, "filamentMm": float(mm) if mm else None, "time": t}


def insert_pauses(path, pauses, command):
    """Each pause before the first layer whose height is past its z, by the
    layer marks any of the three slicers writes. Returns where they went."""
    with open(path, encoding="utf-8", errors="replace") as f:
        lines = f.read().split("\n")
    todo = sorted(pauses, key=lambda p: p["z"])
    out, done = [], []
    for i, line in enumerate(lines):
        if todo and _LAYER.match(line):
            z = None
            for nxt in lines[i + 1:i + 4]:
                m = _Z.match(nxt)
                if m:
                    z = float(m.group(1))
                    break
            if z is not None:
                while todo and z > todo[0]["z"] + 1e-6:
                    p = todo.pop(0)
                    out += [f"; pause: {p['note']}", "M400", f"{command} ; {p['note']}"]
                    done.append({"z": z, "note": p["note"]})
        out.append(line)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    return done
