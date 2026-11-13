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

try:
    from . import restart_mark
except ImportError:  # an update without it: no waiting for a restarting window
    restart_mark = None

FLAG = "AGENTLINK_SUPERVISED"
WINDOW_PORT = int(os.environ.get("AGENTLINK_WINDOW_PORT", "7318"))


class Window:
    """The FreeCAD window's link (the workbench's Start link), when it is on.
    Tried afresh for each call, so turning the link on or off takes effect
    at the next call; off (or AGENTLINK_WINDOW=0), calls stay hidden."""

    def __init__(self, write):
        self.write = write
        self.f = None
        self.off = os.environ.get("AGENTLINK_WINDOW", "1") == "0"
        self.seen = False  # the window has answered this session

    def _open(self):
        import socket

        if self.f is not None:
            return True
        try:
            s = socket.create_connection(("127.0.0.1", WINDOW_PORT), timeout=0.3)
        except OSError:
            return False
        s.settimeout(None)
        self.f = s.makefile("rw", encoding="utf-8", newline="\n")
        return True

    def _ready(self):
        """Connected to the window, waiting for it while it restarts
        (app_restart, agentlink_update leave a mark) rather than let the
        hidden FreeCAD answer for the document on screen."""
        return self._open() or bool(restart_mark and restart_mark.wait_until(self._open))

    def _send(self, line):
        """One go at the window: True answered, False went away first."""
        try:
            mid = json.loads(line).get("id")
            self.f.write(line if line.endswith("\n") else line + "\n")
            self.f.flush()
            while True:
                got = self.f.readline()
                if not got:
                    raise OSError("window closed")
                got = got.rstrip("\n")
                if not got:
                    continue
                self.write(got)
                try:
                    if json.loads(got).get("id") == mid:
                        self.seen = True
                        return True
                except ValueError:
                    pass
        except OSError:
            self.f = None
            return False

    def _refuse(self, line, why):
        try:
            mid = json.loads(line).get("id")
        except ValueError:
            mid = None
        text = json.dumps({"error": {"code": "invalid_state", "message": why}})
        self.write(json.dumps({"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": text}], "isError": True}}))
        return True

    def call(self, line):
        """Send one call to the window and pass its answer on. False when the
        window isn't there: the hidden FreeCAD takes the call.

        A connection the window dropped while restarting on purpose
        (app_restart leaves a mark; the calls it had not started were never
        run) is made again and the call sent once more. Dropped with no mark
        — a crash, or the person quit — the call may have run or not, so it
        is answered, not sent again. And once the window has answered this
        session, its going away is said once rather than the hidden FreeCAD
        quietly answering from its own empty document (a doc_save there
        would write that over the person's file)."""
        if self.off:
            return False
        if not self._ready():
            if self.seen:
                self.seen = False
                return self._refuse(line, "The FreeCAD window this session was working in has gone (closed or crashed): this call was not run. What was open there is not in the hidden FreeCAD, which has an empty document of its own. Ask the person to open FreeCAD again (its link starts with it), or carry on headless: the next call runs there.")
            return False
        if self._send(line):
            return True
        restarting = restart_mark is not None and restart_mark.read() is not None
        if not restarting:
            self.seen = False
            return self._refuse(line, "The FreeCAD window went away during this call: it may or may not have run. session_state shows where things stand; then call again if it is needed.")
        return self._ready() and self._send(line)


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
    window = Window(write)
    for line in sys.stdin:
        msg = None
        try:
            msg = json.loads(line)
            if msg.get("id") is not None and msg.get("method"):
                pending[msg["id"]] = msg["method"]
        except ValueError:
            pass
        # Window first: a tool call goes to the FreeCAD window when its link
        # is on (Start link), so the person watches it land; else the hidden one.
        if msg and msg.get("method") == "tools/call" and window.call(line):
            pending.pop(msg.get("id"), None)
            continue
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
