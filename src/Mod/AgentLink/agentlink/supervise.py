# SPDX-License-Identifier: LGPL-2.1-or-later
# The headless server kept running: it serves in a child process, and when
# FreeCAD dies under a call (a crash in Open CASCADE takes the whole
# process), every call still waiting is answered with what happened and a
# fresh FreeCAD takes the next. Here, in the add-on, so whatever starts the
# server gets it without being changed.
#
# Unattended use (D, E, F): a watchdog reads the window's heartbeat (written
# from FreeCAD's main thread every 2 s, with any modal dialog in front) and
# times the call in hand. A FreeCAD that stops answering past its call's
# deadline is stopped by its own pid, started again in the background and
# its documents recovered from the newest good copy; a call stuck behind a
# dialog is answered "blocked by a dialog". Each of these is said once, in
# the answer to the call it happened in. Nothing is retried quietly.

import json
import os
import signal
import subprocess
import sys
import threading
import time

try:
    from . import restart_mark
except ImportError:  # an update without it: no waiting for a restarting window
    restart_mark = None
try:
    from . import keep
except ImportError:
    keep = None

FLAG = "AGENTLINK_SUPERVISED"
WINDOW_PORT = int(os.environ.get("AGENTLINK_WINDOW_PORT", "7318"))
# Heavy tools: their deadline is their estimate times three (N); with no
# estimate yet, half an hour. The rest get a change's 120 s.
HEAVY = {
    "fea_native", "fea_buckle", "fea_shell", "fea_frame", "fem_mesh", "fea_refine", "asm_motion", "asm_sweep", "slice", "slice_with",
    "view_render", "dfm_check", "part_review", "cam_sim", "meshobj_boolean", "meshobj_offset", "meshobj_decimate", "points_mesh",
    "deviation_map", "deviation_align", "scan_check", "section_fit", "part_extract", "texture_prepare", "manual_render", "steps_render",
}
for _n in ("fea_solve", "fea_heat", "strength_check"):
    HEAVY.add(_n)
NO_ESTIMATE = 1800.0
# A call stuck behind a dialog this long is answered "blocked".
DIALOG_WAIT = 4.0


def _answer(mid, code, message):
    text = json.dumps({"error": {"code": code, "message": message}})
    return json.dumps({"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": text}], "isError": True}})


def _call_info(line):
    try:
        m = json.loads(line)
    except ValueError:
        return None, None, None
    p = m.get("params") or {}
    return m.get("id"), p.get("name"), p.get("arguments") or {}


def deadline_for(tool):
    if os.environ.get("AGENTLINK_DEADLINE"):
        return float(os.environ["AGENTLINK_DEADLINE"])
    if keep is None:
        return NO_ESTIMATE
    if tool in HEAVY:
        try:
            q = keep.queue_state()
            pts = (q.get("timings") or {}).get(tool) or []
            if pts:
                return keep.deadline("heavy", max(t for _, t in pts))
        except Exception:
            pass
        return NO_ESTIMATE
    return keep.deadline("change")


def stop_pid(pid, grace=10.0):
    """TERM, then KILL after grace seconds, on that one process."""
    if not pid or int(pid) in (os.getpid(), os.getppid(), 0, 1):
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return
    t0 = time.time()
    while time.time() - t0 < grace:
        try:
            os.kill(pid, 0)
        except OSError:
            return
        time.sleep(0.2)
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass


def heartbeat():
    if keep is None:
        return None
    hb = keep._load(keep.path("heartbeat.json"), None)
    return hb if isinstance(hb, dict) else None


def relaunch(app, why):
    """The window started again in the background (never in front). The
    command can be set (AGENTLINK_RELAUNCH, a JSON argv with {app}) for
    systems without macOS's open."""
    if keep is not None:
        keep.mark_launch(None, why)
    custom = os.environ.get("AGENTLINK_RELAUNCH")
    if custom:
        argv = [a.replace("{app}", app or "") for a in json.loads(custom)]
    elif sys.platform == "darwin" and app and app.endswith(".app"):
        argv = ["open", "-g", "-a", app]
    else:
        return False
    try:
        subprocess.Popen(argv, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except OSError:
        return False


class Window:
    """The FreeCAD window's link (the workbench's Start link), when it is on.
    Tried afresh for each call, so turning the link on or off takes effect
    at the next call; off (or AGENTLINK_WINDOW=0), calls stay hidden."""

    def __init__(self, write):
        self.write = write
        self.f = None
        self.sock = None
        self.off = os.environ.get("AGENTLINK_WINDOW", "1") == "0"
        self.seen = False  # the window has answered this session
        self.inflight = None  # {id, tool, args, start}
        self.answered = set()  # ids answered early (a dialog, a hang)
        self.hang = None  # set by the watchdog before it stops the window
        self.last_hb = None
        self.lock = threading.Lock()

    def _open(self):
        import socket

        if self.f is not None:
            return True
        try:
            s = socket.create_connection(("127.0.0.1", WINDOW_PORT), timeout=0.3)
        except OSError:
            return False
        s.settimeout(None)
        self.sock = s
        self.f = s.makefile("rw", encoding="utf-8", newline="\n")
        return True

    def _drop(self):
        try:
            if self.sock is not None:
                import socket

                self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.f = None
        self.sock = None

    def _ready(self):
        """Connected to the window, waiting for it while it restarts
        (app_restart, agentlink_update leave a mark) rather than let the
        hidden FreeCAD answer for the document on screen."""
        return self._open() or bool(restart_mark and restart_mark.wait_until(self._open))

    def _send(self, line, forward=True):
        """One go at the window: its answer (True, or the reply line when not
        forwarded), or False when it went away first."""
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
                try:
                    gid = json.loads(got).get("id")
                except ValueError:
                    gid = None
                if forward and gid not in self.answered:
                    self.write(got)
                if gid == mid:
                    self.seen = True
                    return True if forward else got
        except (OSError, AttributeError):
            self.f = None
            return False

    def ask(self, tool, args, timeout=180.0):
        """A call of the supervisor's own (recovery): its result, not passed on."""
        line = json.dumps({"jsonrpc": "2.0", "id": f"__sup{time.time()}", "method": "tools/call", "params": {"name": tool, "arguments": args}})
        t0 = time.time()
        while time.time() - t0 < timeout and not self._open():
            time.sleep(1)
        if self.f is None:
            return None
        got = self._send(line, forward=False)
        if not got:
            return None
        try:
            r = json.loads(got)["result"]
            return json.loads(r["content"][0]["text"])
        except (ValueError, KeyError, TypeError):
            return None

    def _refuse(self, line, why, code="invalid_state"):
        mid, _, _ = _call_info(line)
        self.write(_answer(mid, code, why))
        return True

    def abandon(self, why, code="invalid_state"):
        """Answer the call in hand now (a dialog holds it) and let go of the
        connection, so the next call can be taken; its late answer is dropped."""
        with self.lock:
            c = self.inflight
            if not c or c["id"] in self.answered:
                return False
            self.answered.add(c["id"])
            self.write(_answer(c["id"], code, why))
            c["abandoned"] = True
        self._drop()
        return True

    def recover(self, why, tool, t_crash):
        """After the window died (a crash or the watchdog): started again in
        the background and its documents recovered from the newest copy that
        opens. The words for the answer."""
        hb = self.last_hb or heartbeat() or {}
        app, docs = hb.get("app"), hb.get("docs") or []
        if keep is not None:
            keep.log_watchdog({"event": why, "tool": tool, "docs": [d.get("label") for d in docs], "app": app})
        if not relaunch(app, why):
            reports = keep.crash_reports() if keep else []
            return ("The window could not be started again from here" + (f" (crash report: {reports[0]})" if reports else "") +
                    ". The hidden FreeCAD takes the next calls: app_recover lists the saved copies of " + (", ".join(d.get("label", "?") for d in docs) or "the documents") + ".")
        self.f = None
        t0 = time.time()
        while time.time() - t0 < 120 and not self._open():
            time.sleep(1)
        if self.f is None:
            reports = keep.crash_reports() if keep else []
            return ("FreeCAD was started again in the background but its link did not come up within 2 minutes" +
                    (f" (newest crash report: {reports[0]})" if reports else "") + ". The hidden FreeCAD takes the next calls: app_recover lists the saved copies.")
        got = self.ask("app_recover", {"action": "auto", "documents": docs, "since": t_crash}) if docs else {"recovered": []}
        if not got:
            return "FreeCAD is back (started in the background); recovering its documents did not answer — app_recover lists the saved copies."
        parts = []
        for r in got.get("recovered") or []:
            parts.append(f"{r.get('label')} recovered from {r.get('source')} ({r.get('time')}, {r.get('lost')})")
        for r in got.get("failed") or []:
            parts.append(f"{r.get('label')}: no copy opened ({r.get('why')})")
        return "FreeCAD is back (started in the background). " + ("; ".join(parts) + ". " if parts else "No documents to recover. ") + \
            "Recovered copies open beside the files as \"(recovered …)\"; the person's own files are untouched — app_recover { accept } saves one over its file, with their OK."

    def call(self, line):
        """Send one call to the window and pass its answer on. False when the
        window isn't there: the hidden FreeCAD takes the call.

        A connection the window dropped while restarting on purpose
        (app_restart leaves a mark; the calls it had not started were never
        run) is made again and the call sent once more. Dropped with no mark
        — a crash, a hang the watchdog stopped, or the person quit — the call
        may have run or not, so it is answered, not sent again: with FreeCAD
        started again in the background and its documents recovered."""
        if self.off:
            return False
        if not self._ready():
            if self.seen:
                self.seen = False
                return self._refuse(line, "The FreeCAD window this session was working in has gone (closed or crashed): this call was not run. What was open there is not in the hidden FreeCAD, which has an empty document of its own. Ask the person to open FreeCAD again (its link starts with it), or carry on headless: the next call runs there.")
            return False
        mid, tool, args = _call_info(line)
        with self.lock:
            self.inflight = {"id": mid, "tool": tool, "args": args, "start": time.time()}
            self.hang = None
        try:
            if self._send(line):
                return True
            c = self.inflight or {}
            if c.get("abandoned"):
                return True
            restarting = restart_mark is not None and restart_mark.read() is not None
            if restarting:
                return self._ready() and self._send(line)
            self.seen = False
            hang = self.hang
            t_crash = time.time()
            if hang:
                head = f"FreeCAD stopped responding during {tool} after {int(hang['after'])} s and was stopped (its pid {hang['pid']})."
            else:
                head = f"The FreeCAD window went away during {tool} (a crash, or it was quit): it may or may not have run."
            tail = self.recover("hang" if hang else "crash", tool, t_crash)
            return self._refuse(line, head + " " + tail, code="failed")
        finally:
            with self.lock:
                self.inflight = None


def run(script, argv):
    """Serve stdin/stdout through a child running script, restarted when it
    dies. Gives up after three deaths in a minute."""
    out = os.fdopen(os.dup(1), "w", buffering=1, encoding="utf-8")
    os.dup2(2, 1)
    lock = threading.Lock()
    pending = {}
    lines = {}  # id -> the line, for a quiet restart to send again
    inflight = {}  # hidden: id -> {tool, args, start}
    hangs = {}  # call key -> times it hung
    deaths = []
    state = {"child": None, "ending": False, "hang": None, "recover": None}
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
                if mid == "__recover":
                    state["recover"] = line
                    continue
                if mid is not None:
                    pending.pop(mid, None)
                    lines.pop(mid, None)
                    if inflight.pop(mid, None) is not None:
                        state["last_reply"] = time.time()
                write(line)
            code = child.wait()
            if state["ending"]:
                os._exit(0)
            died(code)

        threading.Thread(target=pump, daemon=True).start()
        return child

    def send_child(line):
        state["child"].stdin.write(line if line.endswith("\n") else line + "\n")
        state["child"].stdin.flush()

    def died(code):
        quiet = keep is not None and os.path.exists(keep.path(".hidden-restart"))
        if quiet and code == 0:
            # Asked for (a settings rollback with the window closed): started
            # again without a word; nothing was in hand.
            try:
                os.remove(keep.path(".hidden-restart"))
            except OSError:
                pass
            start()
            try:
                send_child(json.dumps({"jsonrpc": "2.0", "id": "__restart", "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "agentlink-supervisor", "version": "1"}}}))
                send_child(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}))
                # It left right after its answer: calls queued behind it never
                # ran, so they go to the fresh one.
                for mid in list(pending):
                    if mid in lines:
                        if mid in inflight:
                            inflight[mid]["start"] = time.time()
                        send_child(lines[mid])
            except OSError:
                pass
            return
        hang = state.pop("hang", None)
        state["hang"] = None
        why = f"stopped by signal {-code}" if code < 0 else f"exited with code {code}"
        sys.stderr.write(f"Agent Link: FreeCAD {why}; starting it again\n")
        hidden = (keep._load(keep.path("hidden.json"), {}) if keep else {}) or {}
        docs = [d for d in hidden.get("docs") or [] if d.get("name")]
        t_crash = time.time()
        now = time.time()
        deaths[:] = [t for t in deaths if now - t < 60] + [now]
        if len(deaths) > 3:
            for mid, method in list(pending.items()):
                write(_answer(mid, "failed", f"FreeCAD {why} again: it died more than three times in a minute, so Agent Link stopped. Start it again once the cause is found."))
            sys.stderr.write("Agent Link: FreeCAD died more than three times in a minute; giving up\n")
            os._exit(1)
        child = start()
        recovered = None
        try:
            send_child(json.dumps({"jsonrpc": "2.0", "id": "__restart", "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "agentlink-supervisor", "version": "1"}}}))
            send_child(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}))
            if docs:
                state["recover"] = None
                send_child(json.dumps({"jsonrpc": "2.0", "id": "__recover", "method": "tools/call", "params": {"name": "app_recover", "arguments": {"action": "auto", "documents": docs, "since": t_crash}}}))
                t0 = time.time()
                while state["recover"] is None and time.time() - t0 < 120 and child.poll() is None:
                    time.sleep(0.2)
                if state["recover"]:
                    try:
                        recovered = json.loads(json.loads(state["recover"])["result"]["content"][0]["text"])
                    except (ValueError, KeyError, TypeError):
                        recovered = None
        except OSError:
            pass
        tail = ""
        if recovered:
            parts = [f"{r.get('label')} recovered from {r.get('source')} ({r.get('time')}, {r.get('lost')})" for r in recovered.get("recovered") or []]
            tail = (" " + "; ".join(parts) + ".") if parts else ""
        elif docs:
            tail = " app_recover lists the saved copies of " + ", ".join(d.get("label", "?") for d in docs) + "."
        for mid, method in list(pending.items()):
            c = inflight.get(mid) or {}
            if hang and mid == hang.get("id"):
                msg = f"FreeCAD stopped responding during {hang['tool']} after {int(hang['after'])} s; it was stopped and started again.{tail or ' Its documents are gone from memory: app_recover lists the saved copies.'}"
            else:
                msg = f"FreeCAD {why} during this call ({c.get('tool') or method}) and has been started again.{tail or ' The document and everything not saved in it is gone: session_state shows the fresh one; doc_open what was saved.'} If the same call does this again, it is the call — try another way."
            write(json.dumps({"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": json.dumps({"error": {"code": "failed", "message": msg}})}], "isError": True} if method == "tools/call" else {}}))
        pending.clear()
        inflight.clear()
        lines.clear()

    window = Window(write)

    def watchdog():
        """Every 2 s: a call stuck behind a dialog is answered; a FreeCAD
        past its call's deadline with no heartbeat for a minute is stopped
        (never while a save is in progress)."""
        while not state["ending"]:
            time.sleep(keep.PING_EVERY if keep else 2.0)
            try:
                now = time.time()
                c = window.inflight
                if c:
                    hb = heartbeat()
                    if hb:
                        window.last_hb = hb
                    dlg = (hb or {}).get("dialog")
                    if dlg and not dlg.get("pressed") and now - float(dlg.get("since") or now) > DIALOG_WAIT:
                        window.abandon(f"Blocked by a dialog in the FreeCAD window: \"{dlg.get('title')}\" — {str(dlg.get('text'))[:300]} (buttons: {', '.join(dlg.get('buttons') or []) or 'none'}). "
                                       f"{c['tool']} is waiting behind it. dialog_answer {{ button }} presses one if it is a harmless information box; anything else is the person's: ask them in chat.")
                        continue
                    last = float((hb or {}).get("t") or c["start"])
                    limit = deadline_for(c["tool"])
                    if keep.hung(now, c["start"], max(last, c["start"]), limit) and not keep.saving():
                        pid = (hb or {}).get("pid")
                        if pid:
                            window.hang = {"pid": pid, "after": now - c["start"], "tool": c["tool"]}
                            keep.log_watchdog({"event": "hang", "where": "window", "tool": c["tool"], "after": now - c["start"], "pid": pid})
                            key = keep.call_key(c["tool"], c.get("args"))
                            hangs[key] = hangs.get(key, 0) + 1
                            stop_pid(int(pid))
                # The hidden FreeCAD runs one call at a time: the oldest is the
                # one in hand, started when the one before it answered.
                first = min(inflight.items(), key=lambda kv: kv[1]["start"]) if inflight else None
                for mid, h in [first] if first else []:
                    began = max(h["start"], state.get("last_reply") or 0)
                    limit = deadline_for(h["tool"])
                    if keep.hung(now, began, began + limit, limit) and not keep.saving() and state["hang"] is None:
                        child = state["child"]
                        state["hang"] = {"id": mid, "tool": h["tool"], "after": now - began}
                        keep.log_watchdog({"event": "hang", "where": "hidden", "tool": h["tool"], "after": now - began, "pid": child.pid})
                        key = keep.call_key(h["tool"], h.get("args"))
                        hangs[key] = hangs.get(key, 0) + 1
                        threading.Thread(target=stop_pid, args=(child.pid,), daemon=True).start()
            except Exception as e:
                sys.stderr.write(f"Agent Link watchdog: {e}\n")

    start()
    if keep is not None:
        threading.Thread(target=watchdog, daemon=True).start()
    for line in sys.stdin:
        msg = None
        try:
            msg = json.loads(line)
            if msg.get("id") is not None and msg.get("method"):
                pending[msg["id"]] = msg["method"]
        except ValueError:
            pass
        if msg and msg.get("method") == "tools/call" and keep is not None:
            mid, tool, args = _call_info(line)
            if hangs.get(keep.call_key(tool, args), 0) >= 2:
                pending.pop(mid, None)
                write(_answer(mid, "invalid_state", f"{tool} with these arguments hung FreeCAD twice this session: refused for the rest of it. Try another way (other sizes, a rough pass, fewer objects)."))
                continue
        # Window first: a tool call goes to the FreeCAD window when its link
        # is on (Start link), so the person watches it land; else the hidden one.
        if msg and msg.get("method") == "tools/call" and window.call(line):
            pending.pop(msg.get("id"), None)
            continue
        if msg and msg.get("method") == "tools/call":
            mid, tool, args = _call_info(line)
            inflight[mid] = {"tool": tool, "args": args, "start": time.time()}
            lines[mid] = line
        try:
            send_child(line)
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
