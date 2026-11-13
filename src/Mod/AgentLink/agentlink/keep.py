# SPDX-License-Identifier: LGPL-2.1-or-later
# What Agent Link keeps on disk so the work survives with no one at the Mac
# (unattended use): checkpoints and auto-saves as saved copies of a document,
# settings snapshots and their change log, the versions before an update,
# approved macros, the machine preset, the heavy-job queue, the launch mark
# and the watchdog's log. All of it under ~/AgentLink, all of it local.
#
# No FreeCAD here: the supervisor, the window's link and the hidden FreeCAD
# all read it, and the rules (what to keep, what is due, what to answer) are
# tested without FreeCAD.

import hashlib
import json
import os
import re
import shutil
import time

MB = 1024 * 1024


def home():
    return os.environ.get("AGENTLINK_HOME") or os.path.expanduser("~/AgentLink")


def path(*parts):
    return os.path.join(home(), *parts)


def safe(name):
    """A document's name as a folder: letters, digits, - _ . only."""
    s = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(name or "doc")).strip("._") or "doc"
    return s[:80]


def stamp(t=None):
    return time.strftime("%Y%m%d-%H%M%S", time.localtime(t if t is not None else time.time()))


def _load(p, default):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _save(p, value):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(value, f, indent=1)
    os.replace(tmp, p)


def _size(p):
    try:
        return os.path.getsize(p)
    except OSError:
        return 0


def free_bytes(where=None):
    p = where or home()
    while p and not os.path.isdir(p) and os.path.dirname(p) != p:
        p = os.path.dirname(p)
    try:
        return shutil.disk_usage(p or "/").free
    except OSError:
        return 0


# ---- A. checkpoints as saved copies ------------------------------------------------------

KEEP_UNNAMED = 10


def cap_bytes(free, setting_mb=0):
    """A document's checkpoint cap: the person's setting (MB) when set, else
    the larger of 500 MB and 2% of the free disk."""
    if setting_mb and setting_mb > 0:
        return int(setting_mb * MB)
    return max(500 * MB, int(0.02 * max(0, free)))


def cp_dir(doc):
    return path("checkpoints", safe(doc))


def cp_index(doc):
    return _load(os.path.join(cp_dir(doc), "index.json"), [])


def cp_save_index(doc, entries):
    _save(os.path.join(cp_dir(doc), "index.json"), entries)


def cp_new(doc, name=None, step=0, undo=0, tx=0, auto=False):
    """A new checkpoint's entry and the file it is to be written to (the
    caller saves the copy there, then cp_add)."""
    entries = cp_index(doc)
    n = max([e.get("n", 0) for e in entries] or [0]) + 1
    tag = safe(name) if name else ("auto" if auto else "cp")
    f = os.path.join(cp_dir(doc), f"{n}-{tag}.FCStd")
    return {"id": f"cp{n}", "n": n, "name": name, "auto": bool(auto), "file": f, "step": step, "undo": undo, "tx": tx, "t": time.time()}


def cp_add(doc, entry, cap):
    """Record a written checkpoint and keep within the cap: the last 10
    unnamed and every named one, the oldest unnamed going first. Answers
    the entries dropped."""
    entry["bytes"] = _size(entry["file"])
    entries = cp_index(doc) + [entry]
    kept, dropped = cp_prune(entries, cap)
    for e in dropped:
        try:
            os.remove(e["file"])
        except OSError:
            pass
    cp_save_index(doc, kept)
    return dropped


def cp_prune(entries, cap, keep=KEEP_UNNAMED):
    """(kept, dropped): unnamed ones past the newest `keep`, then the oldest
    unnamed while the total is over cap. Named ones stay — the person's or
    the agent's own; the automatic ones ("before restore …") go like
    unnamed ones."""
    entries = sorted(entries, key=lambda e: e.get("n", 0))
    unnamed = [e for e in entries if not e.get("name") or e.get("auto")]
    drop = {id(e) for e in unnamed[:-keep]} if len(unnamed) > keep else set()
    total = sum(e.get("bytes", 0) for e in entries if id(e) not in drop)
    for e in unnamed:
        if total <= cap:
            break
        if id(e) in drop or e is unnamed[-1]:
            continue
        drop.add(id(e))
        total -= e.get("bytes", 0)
    return [e for e in entries if id(e) not in drop], [e for e in entries if id(e) in drop]


def cp_find(doc, ref):
    """A checkpoint by id (cp3), number, or name; the newest of a name."""
    entries = cp_index(doc)
    for e in reversed(entries):
        if ref in (e.get("id"), e.get("name"), str(e.get("n"))) or (isinstance(ref, int) and e.get("n") == ref):
            return e
    return None


def can_undo_back(entry, tx_now, undo_now, undo_limit=20):
    """Whether the undo stack still reaches a checkpoint: as many steps on it
    as transactions since, none of them fallen off the bottom."""
    since = tx_now - int(entry.get("tx", 0))
    if since < 0:
        return False
    if since == 0:
        return True
    expect = int(entry.get("undo", 0)) + since
    return expect <= undo_limit and undo_now == expect


# ---- C. auto-save -------------------------------------------------------------------------

AUTOSAVE_SECONDS = 300
AUTOSAVE_STEPS = 10
AUTOSAVE_KEEP = 5


def autosave_due(last_t, last_tx, now, tx):
    """A copy is due 5 minutes or 10 changes after the last one."""
    if tx <= last_tx:
        return False
    return (now - last_t) >= AUTOSAVE_SECONDS or (tx - last_tx) >= AUTOSAVE_STEPS


def autosave_dir(doc):
    return path("autosave", safe(doc))


def autosave_file(doc, t=None):
    return os.path.join(autosave_dir(doc), f"{stamp(t)}.FCStd")


def autosave_prune(doc, keep=AUTOSAVE_KEEP):
    d = autosave_dir(doc)
    try:
        files = sorted(f for f in os.listdir(d) if f.endswith(".FCStd"))
    except OSError:
        return []
    gone = files[:-keep] if len(files) > keep else []
    for f in gone:
        try:
            os.remove(os.path.join(d, f))
        except OSError:
            pass
    return gone


def saving_flag():
    return path(".saving")


def set_saving(on):
    p = saving_flag()
    if on:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write(str(time.time()))
    else:
        try:
            os.remove(p)
        except OSError:
            pass


def saving(limit=120.0):
    """A save in progress (the watchdog waits for it); a stale flag is not."""
    try:
        return time.time() - os.path.getmtime(saving_flag()) < limit
    except OSError:
        return False


# ---- D. recovery --------------------------------------------------------------------------

def recovery_sources(doc, freecad_recovery=()):
    """What a document could come back from, newest first: our auto-saves,
    checkpoints, and FreeCAD's own recovery files ([(path, t)] passed in).
    Each { source, path, t }."""
    out = []
    d = autosave_dir(doc)
    try:
        for f in os.listdir(d):
            if f.endswith(".FCStd"):
                p = os.path.join(d, f)
                out.append({"source": "autosave", "path": p, "t": os.path.getmtime(p)})
    except OSError:
        pass
    for e in cp_index(doc):
        if os.path.isfile(e.get("file", "")):
            out.append({"source": "checkpoint", "path": e["file"], "t": os.path.getmtime(e["file"]), "checkpoint": e.get("id")})
    for p, t in freecad_recovery:
        out.append({"source": "freecad", "path": p, "t": t})
    return sorted(out, key=lambda x: -x["t"])


def freecad_recovery_files(cache_dirs, doc_label=None):
    """FreeCAD's own recovery copies under its cache folders: each holds
    fc_recovery_file.fcstd and an .xml naming the document. [(path, t, label)]"""
    found = []
    for root in cache_dirs:
        try:
            names = os.listdir(root)
        except OSError:
            continue
        for n in names:
            d = os.path.join(root, n)
            f = os.path.join(d, "fc_recovery_file.fcstd")
            if not os.path.isfile(f):
                continue
            label = None
            try:
                with open(os.path.join(d, "fc_recovery_file.xml"), encoding="utf-8", errors="replace") as fh:
                    m = re.search(r"<Label>([^<]*)</Label>", fh.read())
                    label = m.group(1) if m else None
            except OSError:
                pass
            if doc_label is None or label in (None, doc_label):
                found.append((f, os.path.getmtime(f), label))
    return sorted(found, key=lambda x: -x[1])


def crash_reports(limit=1, folder=None):
    d = folder or os.path.expanduser("~/Library/Logs/DiagnosticReports")
    try:
        fs = [os.path.join(d, f) for f in os.listdir(d) if f.lower().startswith("freecad")]
    except OSError:
        return []
    return sorted(fs, key=os.path.getmtime, reverse=True)[:limit]


# ---- E. the watchdog ----------------------------------------------------------------------

PING_EVERY = 2.0
GRACE = float(os.environ.get("AGENTLINK_GRACE", "60"))
DEADLINES = {"read": 30.0, "change": float(os.environ.get("AGENTLINK_DEADLINE", "120"))}


def deadline(kind, estimate=None):
    """Seconds a call may take: a read 30, a change 120, a heavy job its
    estimate times three (never under a change's)."""
    if kind == "heavy":
        return max(DEADLINES["change"], 3.0 * float(estimate or 0))
    return DEADLINES.get(kind, DEADLINES["change"])


def hung(now, started, last_ping, limit):
    """Stuck: past its deadline and no ping for a minute. A long solve that
    keeps the event loop alive keeps pinging and is never stuck."""
    return (now - started) > limit and (now - last_ping) > GRACE


def call_key(tool, args):
    return tool + ":" + hashlib.sha256(json.dumps(args or {}, sort_keys=True, default=str).encode()).hexdigest()[:16]


def log_watchdog(event):
    p = path("watchdog.log")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps({"t": time.time(), **event}, default=str) + "\n")


# ---- F. dialogs ---------------------------------------------------------------------------

def launch_mark():
    return path("launch.json")


def mark_launch(pid, why):
    _save(launch_mark(), {"pid": pid, "t": time.time(), "why": why})


def launched_by_us(pid=None, limit=600.0):
    m = _load(launch_mark(), None)
    if not isinstance(m, dict):
        return False
    if pid is not None and m.get("pid") not in (pid, None):
        return False
    return time.time() - float(m.get("t") or 0) < limit


# Dialogs anyone may close: information boxes with a plain OK.
HARMLESS = ("ok", "close")


def dialog_answer_for(d, ours, restarting=False, save_rule="agent", agent_docs=()):
    """What the link does about a modal dialog: ("press", button) or
    ("report", why). d = { class, title, text, buttons }."""
    title = (d.get("title") or "").lower()
    text = (d.get("text") or "").lower()
    buttons = [b.lower().replace("&", "") for b in d.get("buttons") or []]
    if "recover" in title or "document recovery" in text or "recover" in " ".join(buttons):
        if ours:
            for b in ("skip", "cancel", "no"):
                if b in buttons:
                    return ("press", b)
        return ("report", "FreeCAD's recovery dialog, in a FreeCAD the person started: it is theirs.")
    if restarting and ("save" in title or "save changes" in text or "unsaved" in text):
        name = re.search(r"['\"“]([^'\"”]+)['\"”]", d.get("text") or "")
        doc = name.group(1) if name else None
        save = save_rule == "all" or (save_rule == "agent" and doc in agent_docs)
        for b in (("save", "yes") if save else ("discard", "don't save", "no")):
            if b in buttons:
                return ("press", b)
    return ("report", "Not one with a set answer: the person decides, in chat.")


def harmless(d):
    buttons = [b.lower().replace("&", "") for b in d.get("buttons") or []]
    return len(buttons) <= 1 and all(b in HARMLESS for b in buttons) and (d.get("class") or "").endswith("MessageBox")


# ---- H. settings --------------------------------------------------------------------------

SETTINGS_KEEP = 20


def settings_snapshot(cfg):
    """Copy user.cfg before a change; the last 20 kept. The copy's path."""
    d = path("settings")
    os.makedirs(d, exist_ok=True)
    t = time.time()
    base = f"{stamp(t)}-{int((t % 1) * 1000):03d}"
    dest, k = os.path.join(d, base + ".cfg"), 1
    while os.path.exists(dest):
        dest, k = os.path.join(d, f"{base}-{k:02d}.cfg"), k + 1
    shutil.copy2(cfg, dest)
    fs = sorted(f for f in os.listdir(d) if f.endswith(".cfg"))
    for f in fs[:-SETTINGS_KEEP] if len(fs) > SETTINGS_KEEP else []:
        try:
            os.remove(os.path.join(d, f))
        except OSError:
            pass
    return dest


def settings_log(entry):
    p = path("settings-log.jsonl")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps({"t": time.time(), **entry}, default=str) + "\n")


def settings_history(limit=20):
    out = []
    try:
        with open(path("settings-log.jsonl"), encoding="utf-8") as f:
            for i, line in enumerate(f):
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                e["change"] = i + 1
                out.append(e)
    except OSError:
        pass
    return out[-limit:][::-1] if limit else out[::-1]


def settings_snapshot_for(ref):
    """The snapshot taken before a change (its number) or at a time stamp:
    the newest whose name is at or before it."""
    d = path("settings")
    try:
        fs = sorted(f for f in os.listdir(d) if f.endswith(".cfg"))
    except OSError:
        return None
    if isinstance(ref, int) or (isinstance(ref, str) and ref.isdigit() and len(ref) < 8):
        e = next((x for x in settings_history(0) if x["change"] == int(ref)), None)
        if not e or not e.get("snapshot"):
            return None
        return e["snapshot"] if os.path.isfile(e["snapshot"]) else None
    ref = str(ref)
    pick = [f for f in fs if f[: len(ref)] <= ref or f.startswith(ref)]
    return os.path.join(d, pick[-1]) if pick else None


# ---- G. versions --------------------------------------------------------------------------

def versions():
    return _load(path("versions.json"), {"history": []})


def record_versions(before, after=None, outcome=None, keep=2):
    v = versions()
    v.setdefault("history", []).append({"t": time.time(), "before": before, "after": after, "outcome": outcome})
    v["history"] = v["history"][-(keep * 3):]
    _save(path("versions.json"), v)
    return v


# ---- J. macros ----------------------------------------------------------------------------

def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(65536), b""):
            h.update(b)
    return h.hexdigest()


def approvals():
    return _load(path("macros-approved.json"), {})


def approve(name, p):
    a = approvals()
    a[name] = {"sha256": sha256_file(p), "path": p, "t": time.time()}
    _save(path("macros-approved.json"), a)
    return a[name]


def unapprove(name):
    a = approvals()
    gone = a.pop(name, None)
    _save(path("macros-approved.json"), a)
    return gone


def approval_state(name, p):
    """approved | changed (its code moved since the OK) | no."""
    e = approvals().get(name)
    if not e:
        return "no"
    try:
        return "approved" if sha256_file(p) == e["sha256"] else "changed"
    except OSError:
        return "no"


def macro_name_ok(name):
    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _-]{0,60}", str(name or "")))


def _ident(k):
    s = re.sub(r"\W+", "_", str(k)).strip("_").upper() or "P"
    return s if not s[0].isdigit() else "P_" + s


def macro_text(name, calls, params, header):
    """A .FCMacro replaying Agent Link calls in order, with the listed sizes
    as named constants at the top: each place a listed value appears in the
    calls' arguments is replaced by its name."""
    consts = {_ident(k): v for k, v in (params or {}).items()}
    by_value = {}
    for k, v in consts.items():
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            by_value.setdefault(float(v), k)
        elif isinstance(v, str):
            by_value.setdefault(v, k)

    def lit(x):
        if isinstance(x, bool) or x is None:
            return repr(x)
        if isinstance(x, (int, float)):
            k = by_value.get(float(x))
            return k if k else repr(x)
        if isinstance(x, str):
            k = by_value.get(x)
            return k if k else repr(x)
        if isinstance(x, list):
            return "[" + ", ".join(lit(y) for y in x) + "]"
        if isinstance(x, dict):
            return "{" + ", ".join(f"{k!r}: {lit(v)}" for k, v in x.items()) + "}"
        return repr(x)

    lines = ["# -*- coding: utf-8 -*-"] + [f"# {h}" for h in header] + ["", "# Key sizes: change them here."]
    for k, v in consts.items():
        lines.append(f"{k} = {v!r}")
    lines += ["", "import agentlink", ""]
    for c in calls:
        lines.append(f"agentlink.run({c['tool']!r}, {lit(c.get('args') or {})})")
    return "\n".join(lines) + "\n"


def macro_calls(text):
    """The calls and constants a macro written by macro_text holds, read
    without running it (it is Python: only literals are taken)."""
    import ast

    tree = ast.parse(text)
    consts, calls = {}, []
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            try:
                consts[node.targets[0].id] = ast.literal_eval(node.value)
            except ValueError:
                pass
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            f = node.value.func
            if isinstance(f, ast.Attribute) and f.attr == "run" and len(node.value.args) == 2:
                calls.append(node.value)
    return consts, calls


# ---- N. the machine and heavy jobs --------------------------------------------------------

# Heavy tools: solves (FEM, assembly), meshing, alignment and deviation,
# slicing, renders and big booleans. They go through the queue, are timed
# for estimates, and get their estimate times three as a deadline.
HEAVY = {
    "fea_native", "fea_buckle", "fea_shell", "fea_frame", "fem_mesh", "fea_refine", "fea_solve", "fea_heat", "strength_check",
    "asm_motion", "asm_sweep", "slice", "slice_with", "view_render", "dfm_check", "part_review", "cam_sim",
    "meshobj_boolean", "meshobj_offset", "meshobj_decimate", "points_mesh", "part_extract", "texture_prepare",
    "deviation_map", "deviation_align", "scan_check", "section_fit", "manual_render", "steps_render",
}
SYSCTL_KEYS = ("machdep.cpu.brand_string", "hw.perflevel0.physicalcpu", "hw.physicalcpu", "hw.ncpu", "hw.memsize", "hw.model")


def _run(argv, timeout=5):
    import subprocess

    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return r.stdout if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def read_machine():
    """The preset, read from the machine once (macOS sysctl; elsewhere what
    Python can see) and kept in machine.json."""
    import sys

    got = {}
    if sys.platform == "darwin":
        for k in SYSCTL_KEYS:
            v = _run(["sysctl", "-n", k]).strip()
            if v:
                got[k] = v
    else:
        got["hw.ncpu"] = str(os.cpu_count() or 1)
        got["hw.physicalcpu"] = got["hw.ncpu"]
        try:
            with open("/proc/meminfo") as f:
                kb = int(re.search(r"MemTotal:\s+(\d+)", f.read()).group(1))
            got["hw.memsize"] = str(kb * 1024)
        except (OSError, AttributeError, ValueError):
            pass
        got["hw.model"] = "linux"
    m = machine_from(got)
    m["read"] = time.time()
    save_machine(m)
    return m


def preset(overrides=None):
    """The kept preset (read once), with the person's own settings on top:
    { heavySlots, memoryCeilingGB } from Mod/AgentLink."""
    m = machine() or read_machine()
    for k, v in (overrides or {}).items():
        if v:
            m[k] = v
    return m


_cache = {}


def _cached(key, fn, ttl=10.0):
    v = _cache.get(key)
    if v and time.time() - v[0] < ttl:
        return v[1]
    out = fn()
    _cache[key] = (time.time(), out)
    return out


def person_idle_seconds():
    import sys

    if sys.platform != "darwin":
        return None
    return _cached("idle", lambda: hid_idle_seconds(_run(["ioreg", "-c", "IOHIDSystem", "-r", "-k", "HIDIdleTime"])))


def mac_thermal():
    import sys

    if os.environ.get("AGENTLINK_FAKE_THERMAL"):  # tests
        return True
    return sys.platform == "darwin" and _cached("therm", lambda: thermal_limited(_run(["pmset", "-g", "therm"])))


def mac_pressure():
    import sys

    if sys.platform != "darwin":
        return "normal"
    return _cached("press", lambda: memory_pressure_level(_run(["sysctl", "kern.memorystatus_vm_pressure_level"])))

def machine_from(sysctl):
    """The preset from sysctl's answers: { chip, cores, perfCores, memoryGB,
    model, fan, heavySlots, memoryCeilingGB }."""
    def num(k, d=0):
        try:
            return int(str(sysctl.get(k, d)).strip())
        except ValueError:
            return d

    model = str(sysctl.get("hw.model", "")).strip()
    mem = num("hw.memsize")
    perf = num("hw.perflevel0.physicalcpu") or num("hw.physicalcpu")
    fan = not model.startswith("MacBookAir") if model else True
    slots = 1 if not fan else (2 if perf >= 8 else 1)
    return {
        "chip": str(sysctl.get("machdep.cpu.brand_string", "")).strip(),
        "cores": num("hw.ncpu") or num("hw.physicalcpu"),
        "perfCores": perf,
        "memoryGB": round(mem / 1024 ** 3, 1) if mem else None,
        "model": model,
        "fan": fan,
        "heavySlots": slots,
        "memoryCeilingGB": round(0.5 * mem / 1024 ** 3, 1) if mem else None,
    }


def machine():
    return _load(path("machine.json"), None)


def save_machine(m):
    _save(path("machine.json"), m)


def thermal_limited(pmset_therm):
    """`pmset -g therm`: a CPU speed limit under 100 means throttled."""
    m = re.search(r"CPU_Speed_Limit\s*=\s*(\d+)", pmset_therm or "")
    return bool(m) and int(m.group(1)) < 100


def memory_pressure_level(text):
    """`memory_pressure` / sysctl kern.memorystatus_vm_pressure_level: normal,
    warn or critical."""
    t = (text or "").lower()
    m = re.search(r"memorystatus_vm_pressure_level:\s*(\d)", t) or re.search(r"pressure level:\s*(\d)", t)
    if m:
        return {1: "normal", 2: "warn", 4: "critical"}.get(int(m.group(1)), "normal")
    m = re.search(r"system-wide memory free percentage:\s*(\d+)%", t)
    if m:
        free = int(m.group(1))
        return "critical" if free < 10 else ("warn" if free < 20 else "normal")
    return "normal"


def hid_idle_seconds(ioreg):
    """`ioreg -c IOHIDSystem`: HIDIdleTime is in nanoseconds."""
    m = re.search(r'"HIDIdleTime"\s*=\s*(\d+)', ioreg or "")
    return int(m.group(1)) / 1e9 if m else None


def queue_state():
    return _load(path("queue.json"), {"running": [], "waiting": [], "held": False, "timings": {}})


def save_queue(q):
    _save(path("queue.json"), q)


def log_timing(q, tool, size, seconds, keep=40):
    t = q.setdefault("timings", {}).setdefault(tool, [])
    t.append([float(size), float(seconds)])
    del t[:-keep]
    return q


def estimate(q, tool, size):
    """Seconds for a heavy job of this size, from its past runs: a line
    through them (time per unit of size), else None."""
    pts = [(s, t) for s, t in (q.get("timings") or {}).get(tool, []) if s > 0]
    if not pts:
        return None
    if len(pts) == 1:
        s, t = pts[0]
        return t * float(size) / s
    n = len(pts)
    mx, my = sum(s for s, _ in pts) / n, sum(t for _, t in pts) / n
    den = sum((s - mx) ** 2 for s, _ in pts)
    if den <= 0:
        return my
    b = sum((s - mx) * (t - my) for s, t in pts) / den
    a = my - b * mx
    return max(0.0, a + b * float(size))


def admit(q, preset, busy_person, thermal, pressure):
    """Whether a new heavy job may start now: (True, None) or (False, why)."""
    if q.get("held"):
        return False, "held until the person says go (queue_go): they were working in the window when a job ran"
    if busy_person:
        return False, "the person is working in the window: new heavy jobs wait for their go (queue_go)"
    if thermal:
        return False, "macOS reports the Mac is throttling for heat"
    if pressure in ("warn", "critical"):
        return False, f"macOS reports memory pressure ({pressure})"
    slots = int((preset or {}).get("heavySlots") or 1)
    if len(q.get("running") or []) >= slots:
        return False, f"{slots} heavy job{'s' if slots > 1 else ''} at once on this Mac, and that many are running"
    return True, None
