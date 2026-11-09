# SPDX-License-Identifier: LGPL-2.1-or-later
# Slicing a body with the slicer on this computer, by its command line, and
# reading back what the G-code says: time, filament, layers. Parts dropped
# in mid-print (the magnets) get their pause written into the G-code at the
# first layer printed over them. PrusaSlicer's command line in this build;
# Bambu Studio and OrcaSlicer take profiles as JSON and come next.

import os
import re
import shutil
import subprocess

PAUSE = {"prusaslicer": "M601", "marlin": "M0", "klipper": "PAUSE"}


def find_slicer():
    """The slicer's command: AGENTLINK_SLICER, or PrusaSlicer on the PATH
    or where macOS puts it."""
    given = os.environ.get("AGENTLINK_SLICER")
    if given:
        return given
    for name in ("prusa-slicer", "PrusaSlicer", "prusaslicer"):
        p = shutil.which(name)
        if p:
            return p
    mac = "/Applications/PrusaSlicer.app/Contents/MacOS/PrusaSlicer"
    return mac if os.path.exists(mac) else None


def slice_stl(stl, gcode, settings, timeout=900):
    exe = find_slicer()
    if not exe:
        raise FileNotFoundError("No slicer found: install PrusaSlicer, or set AGENTLINK_SLICER to its command line.")
    s = settings
    args = [
        exe, "--export-gcode",
        "--layer-height", str(s.get("layerHeight", 0.2)),
        "--first-layer-height", str(s.get("firstLayer", s.get("layerHeight", 0.2))),
        "--nozzle-diameter", str(s.get("nozzle", 0.4)),
        "--perimeters", str(int(s.get("walls", 2))),
        "--fill-density", f"{int(s.get('infill', 15))}%",
        "--filament-type", str(s.get("filament", "PLA")).upper(),
        # The slicer's own default density is 0, which reads as 0 g.
        "--filament-density", str(s.get("density", 1.24)),
        "--filament-diameter", str(s.get("filamentDiameter", 1.75)),
        "--output", gcode, stl,
    ]
    env = dict(os.environ)
    run = subprocess.run(args, capture_output=True, text=True, timeout=timeout, env=env)
    if run.returncode != 0 and not os.environ.get("DISPLAY") and shutil.which("xvfb-run"):
        # Some builds want a display even for the command line.
        run = subprocess.run(["xvfb-run", "-a"] + args, capture_output=True, text=True, timeout=timeout, env=env)
    if run.returncode != 0 or not os.path.exists(gcode):
        tail = (run.stderr or run.stdout or "").strip().splitlines()[-3:]
        raise RuntimeError(f"The slicer stopped (code {run.returncode}): {' / '.join(tail)}")
    return exe


def read_gcode(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        text = f.read()

    def grab(pattern):
        m = re.search(pattern, text)
        return m.group(1).strip() if m else None

    layers = len(re.findall(r"^;LAYER_CHANGE", text, re.M))
    g = grab(r";\s*(?:total )?filament used \[g\]\s*=\s*([\d.]+)")
    mm = grab(r";\s*filament used \[mm\]\s*=\s*([\d.]+)")
    t = grab(r";\s*estimated printing time \(normal mode\)\s*=\s*(.+)")
    return {"layers": layers, "filamentG": float(g) if g else None, "filamentMm": float(mm) if mm else None, "time": t}


def insert_pauses(path, pauses, command="M601"):
    """Write each pause in before the first layer whose height is past the
    top of the parts dropped in, so they are printed over. Returns where."""
    with open(path, encoding="utf-8", errors="replace") as f:
        lines = f.read().split("\n")
    done = []
    todo = sorted(pauses, key=lambda p: p["z"])
    out = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if todo and line.startswith(";LAYER_CHANGE") and i + 1 < len(lines) and lines[i + 1].startswith(";Z:"):
            z = float(lines[i + 1][3:])
            while todo and z > todo[0]["z"] + 1e-6:
                p = todo.pop(0)
                out += [f"; pause: {p['note']}", "M400", f"{command} ; {p['note']}"]
                done.append({"z": z, "note": p["note"]})
        out.append(line)
        i += 1
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    return done
