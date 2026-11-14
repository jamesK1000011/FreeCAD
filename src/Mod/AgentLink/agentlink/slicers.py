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


def _run(args, timeout, cwd=None):
    run = subprocess.run(args, capture_output=True, text=True, timeout=timeout, cwd=cwd)
    if run.returncode != 0 and not os.environ.get("DISPLAY") and shutil.which("xvfb-run"):
        run = subprocess.run(["xvfb-run", "-a"] + args, capture_output=True, text=True, timeout=timeout, cwd=cwd)
    return run


def minimal_profiles(settings, folder):
    """Machine, process and filament JSON from the print setup, written to
    folder: the minimal profiles of data/slicer-minimal.json, the setup's
    values put in; the slicer's defaults do the rest."""
    from . import slicer_presets

    out = {}
    for k, v in slicer_presets.minimal(settings).items():
        p = os.path.join(folder, f"agentlink-{k}.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(v, f, indent=1)
        out[k] = p
    return out


# What the last OrcaSlicer / Bambu Studio slice said beside its G-code
# (results()): warnings, plate statistics.
LAST = {}


def slice_with(name, stl, gcode, settings, profiles=None, timeout=900, options=None):
    """Slice stl (a path, or a list of them on one plate) into gcode with the
    named slicer. options (OrcaSlicer and Bambu Studio): orient, arrange
    (each bool), project (a .3mf path: the sliced plate as a project the
    slicer's window opens). Returns (exe, profiles used: 'given' or
    'written' or 'setup')."""
    options = options or {}
    stls = [stl] if isinstance(stl, str) else list(stl)
    if name == "prusaslicer" and len(stls) > 1:
        raise RuntimeError("Several bodies on one plate: OrcaSlicer or Bambu Studio.")
    stl = stls[0]
    exe = find(name)
    if not exe:
        how = "install PrusaSlicer, or set AGENTLINK_SLICER" if name == "prusaslicer" else f"install it, or set {_EXE[name][0]} to its command line"
        raise FileNotFoundError(f"{name} is not on this computer: {how}. present: {', '.join(k for k, v in present().items() if v) or 'none'}.")
    profiles = profiles or {}
    LAST.clear()
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
    if options.get("overrides"):
        # Settings changed for this slice only, over the process preset.
        with open(profiles["process"], encoding="utf-8") as f:
            proc = json.load(f)
        proc.update({k: (v if isinstance(v, (str, list)) else str(v)) for k, v in options["overrides"].items()})
        p = os.path.join(out_dir, "agentlink-process-overrides.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(proc, f, indent=1)
        profiles = {**profiles, "process": p}
    three_mf = options.get("project") or os.path.join(out_dir, "out.3mf")
    for old in glob.glob(os.path.join(out_dir, "*.gcode")) + [os.path.join(out_dir, "result.json"), os.path.join(out_dir, "out.3mf")]:
        if os.path.exists(old):
            os.remove(old)
    args = [exe, "--slice", "0", "--load-settings", f"{profiles['machine']};{profiles['process']}", "--load-filaments", ";".join([profiles["filament"]] * len(stls)), "--outputdir", out_dir]
    if options.get("orient"):
        args += ["--orient", "1"]
    if options.get("arrange") or len(stls) > 1:
        args += ["--arrange", "1"]
    if name == "orcaslicer" and supports(exe, "--export-statistics"):
        args += ["--export-statistics"]
    if name == "bambustudio" or options.get("project"):
        # A bare name: OrcaSlicer puts the output folder in front of any path.
        args += ["--export-3mf", "out.3mf"]
    if name == "bambustudio":
        kinds = nozzle_volume_types(profiles["machine"])
        if kinds:
            # Each filament put on a nozzle by hand: left to itself the
            # command line reads past the end of its extruder list.
            args += ["--nozzle-volume-type", ",".join(kinds), "--filament-map-mode", "Manual", "--filament-map", "1"]
    args += stls
    run = _run(args, timeout, cwd=out_dir)
    made = os.path.join(out_dir, "out.3mf")
    if options.get("project") and os.path.isfile(made) and os.path.abspath(made) != os.path.abspath(three_mf):
        shutil.move(made, three_mf)
    found = sorted(glob.glob(os.path.join(out_dir, "*.gcode")))
    if not found and os.path.isfile(three_mf):
        with zipfile.ZipFile(three_mf) as z:
            inner = sorted(n for n in z.namelist() if n.endswith(".gcode"))
            if inner:
                with z.open(inner[0]) as src, open(os.path.join(out_dir, "plate_1.gcode"), "wb") as dst:
                    shutil.copyfileobj(src, dst)
                found = [os.path.join(out_dir, "plate_1.gcode")]
    if not found:
        why = result_error(out_dir)
        raise RuntimeError((why + " — " if why else "") + _tail(run))
    shutil.copyfile(found[0], gcode)
    LAST.update(results(out_dir))
    return exe, used


def slice_project(name, threemf, out_dir, timeout=900, export=None, plate=0):
    """A project (.3mf) sliced as it stands — its own presets, settings,
    plates and objects, as the person left them in the slicer's window.
    export: a .3mf path for the sliced project. Returns (exe, [G-code
    paths]) and fills LAST."""
    exe = find(name)
    if not exe:
        raise FileNotFoundError(f"{name} is not on this computer.")
    os.makedirs(out_dir, exist_ok=True)
    for old in glob.glob(os.path.join(out_dir, "*.gcode")) + glob.glob(os.path.join(out_dir, "plate_*.statistics.json")) + [os.path.join(out_dir, "result.json")]:
        if os.path.exists(old):
            os.remove(old)
    args = [exe, "--slice", str(int(plate or 0)), "--outputdir", out_dir]
    if name == "orcaslicer" and supports(exe, "--export-statistics"):
        args += ["--export-statistics"]
    if export:
        args += ["--export-3mf", "sliced.3mf"]
    args += [os.path.abspath(threemf)]
    LAST.clear()
    run = _run(args, timeout, cwd=out_dir)
    found = sorted(glob.glob(os.path.join(out_dir, "*.gcode")))
    if export and os.path.isfile(os.path.join(out_dir, "sliced.3mf")):
        shutil.move(os.path.join(out_dir, "sliced.3mf"), export)
    if not found:
        why = result_error(out_dir)
        raise RuntimeError((why + " — " if why else "") + _tail(run))
    LAST.update(results(out_dir))
    return exe, found


def make_project(name, stls, dest, settings, profiles=None, options=None, timeout=600):
    """An unsliced project (.3mf) from STLs, one object each, with the
    profiles given (or minimal ones from settings): what the window makes
    when the files are dropped on its plate. options: orient, arrange."""
    exe = find(name)
    if not exe:
        raise FileNotFoundError(f"{name} is not on this computer.")
    options = options or {}
    out_dir = os.path.splitext(dest)[0] + "-cli"
    os.makedirs(out_dir, exist_ok=True)
    profiles = profiles or {}
    if not all(profiles.get(k) for k in ("machine", "process", "filament")):
        profiles = {**minimal_profiles(settings, out_dir), **{k: v for k, v in profiles.items() if v}}
    for old in (os.path.join(out_dir, "project.3mf"), os.path.join(out_dir, "result.json")):
        if os.path.exists(old):
            os.remove(old)
    args = [exe, "--load-settings", f"{profiles['machine']};{profiles['process']}", "--load-filaments", ";".join([profiles["filament"]] * len(stls)),
            "--arrange", "1" if options.get("arrange", True) else "0", "--outputdir", out_dir, "--export-3mf", "project.3mf"]
    if options.get("orient"):
        args += ["--orient", "1"]
    args += [os.path.abspath(x) for x in stls]
    run = _run(args, timeout, cwd=out_dir)
    made = os.path.join(out_dir, "project.3mf")
    if not os.path.isfile(made):
        # Some builds export only what they slice: slice, then drop the results.
        run = _run(args[:1] + ["--slice", "0"] + args[1:], timeout, cwd=out_dir)
        if os.path.isfile(made):
            from . import orca3mf

            pr = orca3mf.Project(made)
            pr.strip_results()
            pr.changed = False
            pr.save()
    if not os.path.isfile(made):
        why = result_error(out_dir)
        raise RuntimeError((why + " — " if why else "") + _tail(run))
    shutil.move(made, dest)
    return exe


EXPORTS = {"stl": ("--export-stl", None), "stls": ("--export-stls", "stls"), "settings": ("--export-settings", "settings.json"),
           "slicedata": ("--export-slicedata", "slicedata"), "3mf": ("--export-3mf", "export.3mf")}


def export_project(name, threemf, out_dir, what, filename=None, timeout=600):
    """A project's objects or settings written out by the command line:
    what stl (all objects in one), stls (one each, a folder), settings (the
    project's config as JSON), slicedata (the sliced layers, a folder) or
    3mf (re-saved by the slicer). Returns the files made."""
    exe = find(name)
    if not exe:
        raise FileNotFoundError(f"{name} is not on this computer.")
    if what not in EXPORTS:
        raise ValueError(f"export: {', '.join(EXPORTS)}.")
    os.makedirs(out_dir, exist_ok=True)
    flag, arg = EXPORTS[what]
    before = set(os.listdir(out_dir))
    args = [exe, "--outputdir", out_dir]
    if what == "slicedata":
        args += ["--slice", "0"]
    args += [flag] + ([filename or arg] if arg else []) + [os.path.abspath(threemf)]
    run = _run(args, timeout, cwd=out_dir)
    made = sorted(os.path.join(out_dir, n) for n in set(os.listdir(out_dir)) - before if n != "result.json")
    if not made:
        why = result_error(out_dir)
        raise RuntimeError((why + " — " if why else "") + _tail(run))
    return made


_HELP = {}


def supports(exe, option):
    """Whether this slicer's command line takes option (its --help, read once)."""
    if exe not in _HELP:
        try:
            r = subprocess.run([exe, "--help"], capture_output=True, text=True, timeout=120)
            _HELP[exe] = (r.stdout or "") + (r.stderr or "")
        except Exception:
            _HELP[exe] = ""
    return option in _HELP[exe]


def fmt_time(sec):
    sec = int(round(sec or 0))
    h, m, s = sec // 3600, sec % 3600 // 60, sec % 60
    return (f"{h}h " if h else "") + (f"{m}m " if h or m else "") + f"{s}s"


def statistics(path):
    """OrcaSlicer's plate_N.statistics.json (2.5 on, --export-statistics),
    in brief: time, filament and cost, what of it is model, support, prime
    tower and flush, the features by time, and each object's share."""
    with open(path, encoding="utf-8") as f:
        st = json.load(f)
    sm = st.get("summary") or {}
    out = {"time": fmt_time(sm.get("time")), "timeS": sm.get("time"), "weightG": sm.get("weight"), "cost": sm.get("filament_cost"),
           "layers": sm.get("layer_count"), "filamentChanges": sm.get("filament_changes"), "travelMm": sm.get("travel_distance")}
    split = {}
    for fil in sm.get("filaments") or []:
        for part in ("model", "support", "prime_tower", "flush"):
            w = (fil.get(part) or {}).get("weight")
            if w:
                split[part] = round(split.get(part, 0) + w, 3)
    if split:
        out["weightBy"] = split
    feats = sm.get("features") or {}
    total = sum((v or {}).get("time", 0) for v in feats.values()) or 1
    out["features"] = [{"feature": k, "time": fmt_time(v.get("time")), "share%": round(100 * v.get("time", 0) / total), "weightG": v.get("weight")}
                       for k, v in sorted(feats.items(), key=lambda kv: -(kv[1] or {}).get("time", 0))[:8]]
    objs = st.get("objects")
    if isinstance(objs, list) and objs:
        out["objects"] = [{k: o.get(k) for k in ("name", "id", "time", "weight", "volume") if k in o} for o in objs[:20]]
    elif isinstance(objs, dict) and objs:
        out["objects"] = [{"name": k, **{kk: vv for kk, vv in (v or {}).items() if kk in ("time", "weight", "volume")}} for k, v in list(objs.items())[:20]]
    return out


def results(out_dir):
    """What the slicer said beside the G-code: result.json's warnings, and
    the plate statistics when written."""
    out = {}
    try:
        with open(os.path.join(out_dir, "result.json"), encoding="utf-8") as f:
            r = json.load(f)
        warns = [w for w in (r.get("warnings") or []) if w]
        warns += [p["warning_message"] for p in (r.get("sliced_plates") or []) if p.get("warning_message")]
        if warns:
            out["warnings"] = warns
    except (OSError, ValueError):
        pass
    stats = sorted(glob.glob(os.path.join(out_dir, "plate_*.statistics.json")))
    if stats:
        try:
            out["statistics"] = statistics(stats[0])
        except (OSError, ValueError):
            pass
    return out


def result_error(out_dir):
    """The slicer's own word on a failed slice: OrcaSlicer and Bambu Studio
    write result.json beside the plates, with an error string."""
    try:
        with open(os.path.join(out_dir, "result.json"), encoding="utf-8") as f:
            r = json.load(f)
    except (OSError, ValueError):
        return None
    if r.get("return_code") in (0, None) and not r.get("error_string"):
        return None
    return f"{r.get('error_string') or 'failed'} (code {r.get('return_code')})"


def nozzle_volume_types(machine_path):
    """One nozzle volume type per nozzle for a printer with more than one
    (an H2D): Bambu Studio's command line does not seed them from the
    printer as its window does, and stops — or crashes — without them. None
    for a printer with one nozzle."""
    try:
        with open(machine_path, encoding="utf-8") as f:
            m = json.load(f)
    except (OSError, ValueError):
        return None
    nozzles = m.get("nozzle_diameter")
    n = len(nozzles) if isinstance(nozzles, list) else 1
    if n < 2:
        return None
    given = m.get("nozzle_volume_type") or m.get("default_nozzle_volume_type") or []
    if isinstance(given, str):
        given = [given]
    return [(given[i] if i < len(given) else (given[-1] if given else "Standard")) for i in range(n)]


def _tail(run):
    lines = ((run.stderr or "") + "\n" + (run.stdout or "")).strip().splitlines()
    tail = [l for l in lines if l.strip()][-3:]
    # Which build stopped matters: the slicers' command lines change a lot
    # between releases (Bambu Studio's for two-nozzle printers above all).
    version = next((l.split("]")[-1].strip() for l in lines if re.search(r"version", l, re.I) and re.search(r"\d+\.\d+\.\d+", l)), None)
    return f"The slicer stopped (code {run.returncode}): {' / '.join(tail) or 'no G-code written'}" + (f" [{version}]" if version else "")


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
