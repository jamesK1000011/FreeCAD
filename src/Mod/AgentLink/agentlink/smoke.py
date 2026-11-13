# SPDX-License-Identifier: LGPL-2.1-or-later
# The smoke test an update must pass before the window moves onto it
# (unattended use, G): a FreeCAD and the add-ons as they would run together,
# started headless. Every tool family loads (or says which FreeCAD module it
# lacks), and a few core tools run on a fresh document: session_state, a
# primitive, body_check, an export read back. Run as a script with the new
# FreeCAD's Python (or FreeCADCmd) and the new add-ons on the path; the last
# line out is JSON: { ok, freecad, failures, families, ms }.

import json
import os
import sys
import tempfile
import time


def run():
    t0 = time.time()
    fails = []
    out = {"ok": False, "failures": fails}
    try:
        if os.environ.get("FREECAD_LIB") and os.environ["FREECAD_LIB"] not in sys.path:
            sys.path.append(os.environ["FREECAD_LIB"])
        import FreeCAD

        out["freecad"] = ".".join(FreeCAD.Version()[:3])
    except Exception as e:
        fails.append(f"FreeCAD does not import: {e}")
        return out
    try:
        from agentlink import families
        from agentlink.tools import Session, build_server
    except Exception as e:
        fails.append(f"Agent Link does not import: {e}")
        return out
    loaded, left = [], {}
    for name in families.NAMES:
        try:
            __import__(f"agentlink.families.{name}")
            loaded.append(name)
        except ImportError as e:
            # A FreeCAD module this build lacks (FEM, TechDraw …) is fine.
            left[name] = str(e)
        except Exception as e:
            fails.append(f"family {name} fails to load: {type(e).__name__}: {e}")
    out["families"] = {"loaded": len(loaded), "leftOut": left}
    d = tempfile.mkdtemp(prefix="agentlink-smoke-")
    try:
        s = Session(out_dir=d)
        srv = build_server(s, lean=False)

        def call(name, args):
            r = srv.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}})["result"]
            body = json.loads(r["content"][0]["text"])
            if r.get("isError"):
                fails.append(f"{name}: {body.get('error', {}).get('message', body)}"[:300])
                return None
            return body

        call("session_state", {})
        if call("part_primitive", {"kind": "box", "size": [20, 10, 5], "name": "Smoke"}):
            b = call("body_check", {"body": "Smoke"})
            if b is not None and (b.get("valid") is False or b.get("solids") != 1):
                fails.append(f"body_check: the test box reads unsound ({b.get('valid')}, {b.get('solids')} solids)")
            e = call("export_file", {"body": "Smoke", "path": os.path.join(d, "smoke.step")})
            if e is not None and abs((e.get("verify") or {}).get("volume", 0) - 1000.0) > 1.0:
                fails.append(f"export_file: read back {e.get('verify')}")
        out["tools"] = len(srv.tools)
        try:
            FreeCAD.closeDocument(s.doc.Name)
        except Exception:
            pass
    except Exception as e:
        fails.append(f"the server did not start: {type(e).__name__}: {e}")
    out["ok"] = not fails
    out["ms"] = int((time.time() - t0) * 1000)
    return out


def app_python(app):
    """A FreeCAD.app's own Python and library folder: (python, lib, cmd)."""
    b = os.path.join(app, "Contents", "Resources", "bin")
    lib = os.path.join(app, "Contents", "Resources", "lib")
    py = next((os.path.join(b, n) for n in ("python3", "python") if os.path.exists(os.path.join(b, n))), None)
    cmd = next((os.path.join(b, n) for n in ("freecadcmd", "FreeCADCmd") if os.path.exists(os.path.join(b, n))), None)
    return py, lib, cmd


def against(app=None, addon_dirs=(), python=None, lib=None, timeout=600):
    """Run this smoke test in another process: a FreeCAD.app (its own Python)
    or a Python with FreeCAD's lib, with addon_dirs (folders holding
    AgentLink, ScanRulerFC …) first on the path. The parsed result."""
    import subprocess

    cmd = None
    if app:
        python, lib, cmd = app_python(app)
    exe = python or cmd
    if not exe:
        return {"ok": False, "failures": [f"no Python or FreeCADCmd in {app}"]}
    here = os.path.dirname(os.path.abspath(__file__))
    dirs = [os.path.join(d, "AgentLink") if os.path.isdir(os.path.join(d, "AgentLink")) else d for d in addon_dirs] or [os.path.dirname(here)]
    extra = [os.path.join(d, "ScanRulerFC") for d in addon_dirs if os.path.isdir(os.path.join(d, "ScanRulerFC"))]
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(dirs + extra + ([lib] if lib else [])), **({"FREECAD_LIB": lib} if lib else {})}
    env.pop("AGENTLINK_SUPERVISED", None)
    script = os.path.join(dirs[0], "agentlink", "smoke.py")
    if not os.path.isfile(script):
        script = os.path.abspath(__file__)
    try:
        r = subprocess.run([exe, script], capture_output=True, timeout=timeout, env=env, text=True)
    except subprocess.TimeoutExpired:
        return {"ok": False, "failures": [f"the smoke test ran past {timeout} s"]}
    except OSError as e:
        return {"ok": False, "failures": [f"could not start {exe}: {e}"]}
    for line in reversed((r.stdout or "").splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except ValueError:
                break
    return {"ok": False, "failures": [f"no answer (exit {r.returncode}): " + ((r.stderr or "")[-400:] or "nothing on stderr")]}


if __name__ == "__main__":
    res = run()
    sys.stdout.write("\n" + json.dumps(res) + "\n")
    sys.stdout.flush()
    os._exit(0 if res["ok"] else 1)
