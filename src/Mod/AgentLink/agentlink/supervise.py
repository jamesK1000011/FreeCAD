# SPDX-License-Identifier: LGPL-2.1-or-later
# The headless server kept running: it serves in a child process, and when
# FreeCAD dies under a call (a crash in Open CASCADE takes the whole
# process), every call still waiting is answered with what happened and a
# fresh FreeCAD takes the next. Here, in the add-on, so whatever starts the
# server gets it without being changed.

import json
import os
import subprocess
import sys
import threading
import time

FLAG = "AGENTLINK_SUPERVISED"


def run(script, argv):
    """Serve stdin/stdout through a child running script, restarted when it
    dies. Gives up after three deaths in a minute."""
    out = os.fdopen(os.dup(1), "w", buffering=1, encoding="utf-8")
    os.dup2(2, 1)
    lock = threading.Lock()
    pending = {}
    deaths = []
    state = {"child": None, "ending": False}
    env = {**os.environ, FLAG: "1"}

    def write(line):
        with lock:
            out.write(line + "\n")
            out.flush()

    def start():
        child = subprocess.Popen([sys.executable, script, *argv], stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=env, text=True, encoding="utf-8", bufsize=1)
        state["child"] = child

        def pump():
            for line in child.stdout:
                line = line.rstrip("\n")
                if not line.startswith("{"):
                    if line.strip():
                        sys.stderr.write(f"freecad: {line}\n")
                    continue
                try:
                    mid = json.loads(line).get("id")
                except ValueError:
                    mid = None
                if mid == "__restart":
                    continue
                if mid is not None:
                    pending.pop(mid, None)
                write(line)
            code = child.wait()
            if state["ending"]:
                os._exit(0)
            died(code)

        threading.Thread(target=pump, daemon=True).start()
        return child

    def died(code):
        why = f"stopped by signal {-code}" if code < 0 else f"exited with code {code}"
        sys.stderr.write(f"Agent Link: FreeCAD {why}; starting it again\n")
        for mid, method in list(pending.items()):
            text = json.dumps({"error": {"code": "failed", "message": f"FreeCAD {why} during this call and has been started again: the document and everything not saved in it is gone. session_state shows the fresh one; make the part again or doc_open what was saved. If the same call does this again, it is the call — try another way."}})
            write(json.dumps({"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": text}], "isError": True} if method == "tools/call" else {}}))
        pending.clear()
        now = time.time()
        deaths[:] = [t for t in deaths if now - t < 60] + [now]
        if len(deaths) > 3:
            sys.stderr.write("Agent Link: FreeCAD died more than three times in a minute; giving up\n")
            os._exit(1)
        child = start()
        try:
            child.stdin.write(json.dumps({"jsonrpc": "2.0", "id": "__restart", "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "agentlink-supervisor", "version": "1"}}}) + "\n")
            child.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
            child.stdin.flush()
        except OSError:
            pass

    start()
    for line in sys.stdin:
        try:
            msg = json.loads(line)
            if msg.get("id") is not None and msg.get("method"):
                pending[msg["id"]] = msg["method"]
        except ValueError:
            pass
        try:
            state["child"].stdin.write(line if line.endswith("\n") else line + "\n")
            state["child"].stdin.flush()
        except OSError:
            # Between a death and the restart: the call is answered by died().
            pass
    state["ending"] = True
    try:
        state["child"].stdin.close()
    except OSError:
        pass
    state["child"].wait()
    return 0
