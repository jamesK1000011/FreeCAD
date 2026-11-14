# SPDX-License-Identifier: LGPL-2.1-or-later
# OrcaSlicer's calibration prints, headless: each one's test model from the
# person's own OrcaSlicer (its resources/calib folder, read where it is
# installed), set up as a project the command line slices. What the window
# changes as the print goes up (temperature, pressure advance, retraction,
# speed, input shaping) is written as per-layer G-code or per-height settings
# in the project, or into the sliced G-code where only that reaches it.

import json
import os
import re

KINDS = ("temperature", "pressure_advance", "retraction", "flow", "max_flow", "vfa", "input_shaping_freq", "input_shaping_damp", "cornering")
PLAN_FILE = "Metadata/agentlink_calibration.json"


def calib_dir(exe):
    """The calib folder of the OrcaSlicer at exe."""
    if not exe:
        return None
    exe = os.path.realpath(exe)
    d = os.path.dirname(exe)
    cands = [os.path.join(d, "resources", "calib"), os.path.join(d, "..", "Resources", "calib"), os.path.join(d, "..", "resources", "calib"),
             os.path.join(d, "..", "share", "OrcaSlicer", "calib"), os.path.join(d, "..", "share", "orca-slicer", "calib")]
    if ".app/" in exe:
        cands.insert(0, os.path.join(exe.split(".app/")[0] + ".app", "Contents", "Resources", "calib"))
    for c in cands:
        if os.path.isdir(c):
            return os.path.normpath(c)
    return None


def _steps(start, end, step):
    if step <= 0:
        raise ValueError("step: more than 0.")
    n = int(round(abs(end - start) / step)) + 1
    if n > 200:
        raise ValueError(f"{n} steps: at most 200.")
    sign = 1 if end >= start else -1
    return [round(start + sign * i * step, 6) for i in range(n)]


def _flavor(cfg):
    f = cfg.get("gcode_flavor") or "marlin"
    return f[0] if isinstance(f, list) else f


def pa_line(flavor, v):
    if flavor == "klipper":
        return f"SET_PRESSURE_ADVANCE ADVANCE={v:g}"
    if flavor in ("reprapfirmware", "reprap"):
        return f"M572 D0 S{v:g}"
    return f"M900 K{v:g}"


def shaper_line(flavor, freq=None, damp=None):
    if flavor == "klipper":
        parts = []
        if freq is not None:
            parts += [f"SHAPER_FREQ_X={freq:g}", f"SHAPER_FREQ_Y={freq:g}"]
        if damp is not None:
            parts += [f"DAMPING_RATIO_X={damp:g}", f"DAMPING_RATIO_Y={damp:g}"]
        return "SET_INPUT_SHAPER " + " ".join(parts)
    if flavor in ("reprapfirmware", "reprap"):
        return "M593 P\"zvd\"" + (f" F{freq:g}" if freq is not None else "") + (f" S{damp:g}" if damp is not None else "")
    return "M593" + (f" F{freq:g}" if freq is not None else "") + (f" D{damp:g}" if damp is not None else "")


def corner_line(flavor, v):
    if flavor == "klipper":
        return f"SET_VELOCITY_LIMIT SQUARE_CORNER_VELOCITY={v:g}"
    if flavor in ("reprapfirmware", "reprap"):
        return f"M566 X{v * 60:g} Y{v * 60:g}"
    return f"M205 X{v:g} Y{v:g}"


def plan(kind, params, cfg):
    """What the calibration needs: {model (under calib/), trim [low, high]
    mm of the model kept, object {settings}, project {settings}, slot
    {filament settings}, gcodes [[z, lines]], ranges [[zmin, zmax,
    {settings}]], post {retraction bands}, read (how to read the print)}."""
    p = dict(params or {})
    flavor = _flavor(cfg)
    out = {"kind": kind, "object": {}, "project": {}, "slot": {}, "gcodes": [], "ranges": [], "objects": {}}
    if kind == "temperature":
        start, end, step = float(p.get("start", 230)), float(p.get("end", 190)), float(p.get("step", 5))
        if step != 5:
            raise ValueError("temperature: the tower's blocks are 5 °C apart (step 5).")
        temps = _steps(start, end, step)
        top = (500 - min(start, end)) / 5 + 1
        bottom = (500 - max(start, end)) / 5
        out.update(model="temperature_tower/temperature_tower.drc", trim=[bottom * 10 + 1e-3, top * 10 - 1e-3])
        out["slot"] = {"nozzle_temperature": int(temps[0]), "nozzle_temperature_initial_layer": int(temps[0])}
        out["object"] = {"brim_type": "outer_only", "brim_width": 5, "brim_object_gap": 0, "seam_slope_type": "none"}
        out["gcodes"] = [[i * 10.0, f"M104 S{int(t)} ; temperature tower block {int(t)}"] for i, t in enumerate(temps) if i]
        out["read"] = f"Blocks of 10 mm from {int(temps[0])} °C at the bottom, {int(step)} °C cooler each block up: pick the block with the cleanest bridges and overhangs."
    elif kind == "pressure_advance":
        start, end, step = float(p.get("start", 0)), float(p.get("end", 0.1)), float(p.get("step", 0.002))
        vals = _steps(start, end, step)
        out.update(model="pressure_advance/tower_with_seam.drc", trim=[0, len(vals) + 1e-3])
        out["object"] = {"seam_position": "back", "wall_loops": 2, "top_shell_layers": 0, "bottom_shell_layers": 0, "sparse_infill_density": "0%",
                         "brim_type": "brim_ears", "brim_object_gap": 0, "brim_width": 6, "seam_slope_type": "none"}
        out["gcodes"] = [[0.0 + i, pa_line(flavor, v) + f" ; pressure advance {v:g}"] for i, v in enumerate(vals)]
        out["read"] = f"One mm per value from {vals[0]:g} at the bottom, +{step:g} a mm: measure the height of the sharpest corner, value = {vals[0]:g} + height_mm × {step:g}."
    elif kind == "retraction":
        start, end, step = float(p.get("start", 0)), float(p.get("end", 2)), float(p.get("step", 0.1))
        vals = _steps(start, end, step)
        out.update(model="retraction/retraction_tower.drc", trim=[0, len(vals) + 1e-3])
        out["object"] = {"wall_loops": 2, "top_shell_layers": 0, "bottom_shell_layers": 3, "sparse_infill_density": "0%"}
        out["post"] = {"retraction": [[float(i), float(i + 1), v] for i, v in enumerate(vals)]}
        out["read"] = f"One mm per length from {vals[0]:g} mm at the bottom, +{step:g} mm a mm: the lowest height without strings gives length = {vals[0]:g} + height_mm × {step:g}."
    elif kind == "flow":
        which = p.get("pass", 1)
        models = {1: "filament_flow/flowrate-test-pass1.3mf", 2: "filament_flow/flowrate-test-pass2.3mf", "linear": "filament_flow/Orca-LinearFlow.3mf",
                  "linear_fine": "filament_flow/Orca-LinearFlow_fine.3mf"}
        if which not in models:
            raise ValueError("flow: pass 1, 2, \"linear\" or \"linear_fine\".")
        out.update(model=models[which], trim=None)
        nz = cfg.get("nozzle_diameter") or ["0.4"]
        nz = float(nz[0] if isinstance(nz, list) else nz)
        w = round(nz * 1.2, 3)
        out["object"] = {"wall_loops": 1, "only_one_wall_top": True, "sparse_infill_density": "35%", "bottom_shell_layers": 2, "top_shell_layers": 5,
                         "top_shell_thickness": 0, "bottom_shell_thickness": 0, "detect_thin_wall": True, "sparse_infill_pattern": "rectilinear",
                         "top_surface_line_width": w, "internal_solid_infill_line_width": w, "infill_direction": 45, "solid_infill_direction": 135,
                         "ironing_type": "no ironing", "seam_slope_type": "none"}
        cur = cfg.get("filament_flow_ratio") or ["1"]
        cur = float(cur[0] if isinstance(cur, list) else cur) or 1.0
        out["flow_from_name"] = {"linear": str(which).startswith("linear"), "current": cur}
        out["read"] = "Each square's name is its flow change: the smoothest top gives the new flow ratio (the window's formula: pass — ratio × (100 + n)/100; linear — ratio + n)."
    elif kind in ("max_flow", "vfa"):
        if kind == "max_flow":
            start, end, step = float(p.get("start", 5)), float(p.get("end", 20)), float(p.get("step", 0.5))
            vals = _steps(start, end, step)
            nz = cfg.get("nozzle_diameter") or ["0.4"]
            nz = float(nz[0] if isinstance(nz, list) else nz)
            lh = float(cfg.get("layer_height") or 0.2)
            wdt = nz * 1.75
            area = (wdt - lh) * lh + 3.14159 * (lh / 2) ** 2
            out.update(model="volumetric_speed/SpeedTestStructure.drc", trim=[0, len(vals) * 1.0 + 1e-3])
            out["object"] = {"spiral_mode": True, "outer_wall_line_width": round(wdt, 3), "bottom_shell_layers": 1, "wall_loops": 1}
            out["slot"] = {"filament_max_volumetric_speed": 200}
            out["ranges"] = [[float(i), float(i + 1), {"outer_wall_speed": round(v / area, 1)}] for i, v in enumerate(vals)]
            out["read"] = f"One mm per flow from {vals[0]:g} mm³/s at the bottom, +{step:g} a mm: where the wall starts to fail, flow = {vals[0]:g} + height_mm × {step:g}."
        else:
            start, end, step = float(p.get("start", 40)), float(p.get("end", 200)), float(p.get("step", 10))
            vals = _steps(start, end, step)
            out.update(model="vfa/vfa.drc", trim=[0, len(vals) * 5.0 + 1e-3])
            out["object"] = {"spiral_mode": True, "bottom_shell_layers": 1, "wall_loops": 1}
            out["ranges"] = [[i * 5.0, i * 5.0 + 5, {"outer_wall_speed": v}] for i, v in enumerate(vals)]
            out["read"] = f"5 mm per speed from {vals[0]:g} mm/s: the bands with the faintest vertical lines are the speeds to use."
    elif kind in ("input_shaping_freq", "input_shaping_damp"):
        if kind.endswith("freq"):
            start, end, step = float(p.get("start", 15)), float(p.get("end", 110)), float(p.get("step", 1))
            vals = _steps(start, end, step)
            lines = [shaper_line(flavor, freq=v, damp=p.get("damping")) for v in vals]
        else:
            start, end, step = float(p.get("start", 0)), float(p.get("end", 0.2)), float(p.get("step", 0.01))
            vals = _steps(start, end, step)
            lines = [shaper_line(flavor, freq=p.get("freq"), damp=v) for v in vals]
        out.update(model="input_shaping/ringing_tower.drc", trim=[0, len(vals) + 1e-3])
        out["object"] = {"spiral_mode": True, "bottom_shell_layers": 1, "wall_loops": 1}
        out["gcodes"] = [[float(i), ln] for i, ln in enumerate(lines)]
        out["read"] = f"One mm per value from {vals[0]:g}: the height with the least ringing gives value = {vals[0]:g} + height_mm × {step:g}."
    elif kind == "cornering":
        start, end, step = float(p.get("start", 1)), float(p.get("end", 15)), float(p.get("step", 1))
        vals = _steps(start, end, step)
        out.update(model="cornering/SCV-V2.drc", trim=[0, len(vals) * 1.0 + 1e-3])
        out["gcodes"] = [[float(i), corner_line(flavor, v)] for i, v in enumerate(vals)]
        out["read"] = f"One mm per value from {vals[0]:g}: the highest clean corner gives value = {vals[0]:g} + height_mm × {step:g}."
    else:
        raise ValueError(f"kind: one of {', '.join(KINDS)}.")
    return out


def flow_for(name, info):
    """An object's flow ratio from its name (flowrate_m5 -> -5)."""
    m = re.match(r"^flowrate_(m?)([\d.]+)", name or "")
    if not m:
        return None
    n = float(m.group(2)) * (-1 if m.group(1) else 1)
    if info["linear"]:
        return round((info["current"] + n) / info["current"], 5)
    return round(1 + n / 100, 5)


def retract_bands(path, bands):
    """The sliced G-code's retractions changed by height: bands [[zmin,
    zmax, length mm]]. Each retract (an E-only negative move) and its
    matching unretract take the band's length. Returns how many changed."""
    with open(path, encoding="utf-8", errors="replace") as f:
        lines = f.read().split("\n")
    z, rel, changed = 0.0, True, 0
    pending = None
    move = re.compile(r"^(G1|G0)\s+(?:F[\d.]+\s+)?E(-?[\d.]+)(\s+F[\d.]+)?\s*(;.*)?$")
    for i, ln in enumerate(lines):
        s = ln.strip()
        if s.startswith("M82"):
            rel = False
        elif s.startswith("M83"):
            rel = True
        elif s.startswith(";Z:"):
            try:
                z = float(s[3:])
            except ValueError:
                pass
        if not rel:
            continue
        m = move.match(s)
        if not m:
            continue
        e = float(m.group(2))
        band = next((b for b in bands if b[0] <= z < b[1]), None)
        if band is None:
            continue
        if e < 0:
            lines[i] = f"{m.group(1)} E{-band[2]:.5f}{m.group(3) or ''} ; retraction tower {band[2]:g}"
            pending = band[2]
            changed += 1
        elif pending is not None:
            lines[i] = f"{m.group(1)} E{pending:.5f}{m.group(3) or ''} ; unretract {pending:g}"
            pending = None
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return changed


def dump(p):
    return json.dumps(p, indent=1).encode()
