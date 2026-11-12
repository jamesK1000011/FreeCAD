# SPDX-License-Identifier: LGPL-2.1-or-later
# The window restarting: a small file left by app_restart (the window going
# down) and taken away by the window's link once it is back with its
# documents reopened. While it is there, calls wait for the window instead of
# going to the hidden FreeCAD, whose empty document would answer for the one
# on screen. No FreeCAD here: the supervisor reads it too.

import json
import os
import time

# Past this the mark is stale (a launch that failed, a crash): ignored.
LIMIT = 90.0


def path():
    return os.environ.get("AGENTLINK_RESTART_MARK") or os.path.expanduser("~/AgentLink/.window-restarting")


def write(reopens, active=None):
    p = path()
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"t": time.time(), "reopens": list(reopens), "active": active}, f)
    except OSError:
        pass


def read():
    """The mark while it is fresh, else None (a stale one is removed)."""
    p = path()
    try:
        with open(p, encoding="utf-8") as f:
            m = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(m, dict) or time.time() - float(m.get("t") or 0) > LIMIT:
        clear()
        return None
    return m


def clear():
    try:
        os.remove(path())
    except OSError:
        pass


def wait_until(ready, step=0.5):
    """While a fresh mark stands, try ready() every step seconds; True once it
    answers, False when there is no mark (or it went stale) first."""
    while True:
        if ready():
            return True
        if read() is None:
            return False
        time.sleep(step)
