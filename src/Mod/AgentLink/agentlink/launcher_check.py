# SPDX-License-Identifier: LGPL-2.1-or-later
"""The Claude extension's launcher as seen from the add-on: whether the copy
it fetched from GitHub is there and can load, and the log's last word on it.
The add-on is updated on every start even when the launcher is not, so the
add-on is where a launcher that cannot load is found and mended."""

import json
import os

HOME = os.path.expanduser("~")
FETCHED = os.path.join(HOME, ".config", "freecad-agentlink", "launcher", "index.js")
LOG = os.path.join(HOME, "Library", "Logs", "FreeCAD Agent Link", "launcher.log")


def _esm_scope(path):
    """The nearest package.json above path that makes .js an ES module, or None."""
    d = os.path.dirname(path)
    while True:
        p = os.path.join(d, "package.json")
        if os.path.isfile(p):
            try:
                with open(p, encoding="utf-8") as f:
                    return p if json.load(f).get("type") == "module" else None
            except Exception:
                return None
        up = os.path.dirname(d)
        if up == d:
            return None
        d = up


def mend():
    """A package.json saying CommonJS beside the fetched launcher, so a
    package.json higher up that says ES module cannot stop it loading.
    Returns what was done, or None."""
    if not os.path.isfile(FETCHED):
        return None
    own = os.path.join(os.path.dirname(FETCHED), "package.json")
    if os.path.isfile(own):
        return None
    try:
        with open(own, "w", encoding="utf-8") as f:
            f.write('{"type": "commonjs"}\n')
        return f"wrote {own}"
    except OSError:
        return None


def report(lines=12):
    out = {"fetched": os.path.isfile(FETCHED)}
    if out["fetched"]:
        st = os.stat(FETCHED)
        with open(FETCHED, encoding="utf-8", errors="replace") as f:
            src = f.read()
        out.update({"bytes": st.st_size, "hasUpdateTool": "agentlink_update" in src,
                    "ownPackageJson": os.path.isfile(os.path.join(os.path.dirname(FETCHED), "package.json"))})
        esm = _esm_scope(FETCHED)
        if esm:
            out["esModuleScope"] = esm
    if os.path.isfile(LOG):
        try:
            with open(LOG, encoding="utf-8", errors="replace") as f:
                tail = f.readlines()[-400:]
            keep = [l.rstrip() for l in tail if any(k in l for k in ("start:", "launcher", "fetched", "no update", "headless:", "relaying", "install"))]
            out["log"] = keep[-lines:]
        except OSError:
            pass
    return out
