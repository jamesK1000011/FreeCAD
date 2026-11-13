# SPDX-License-Identifier: LGPL-2.1-or-later
# The link in the FreeCAD window: the same six tools, served on a local port
# (127.0.0.1 only) to the stdio relay an MCP client starts
# (agentlink_headless.py --connect). A thread reads the socket; every call
# runs on the GUI's own thread, from a timer, because FreeCAD's documents
# and the 3D view must not be touched from another — so the person sees
# each body arrive in the document they have open.

import json
import os
import queue
import socket
import sys
import threading
import time

import FreeCAD

from .protocol import compact
from .tools import Session, build_server

PORT = 7318


def _tool_name(line):
    try:
        m = json.loads(line)
        return (m.get("params") or {}).get("name") if m.get("method") == "tools/call" else None
    except (ValueError, AttributeError):
        return None


# ---- what the window shows: a modal dialog, and the heartbeat (unattended use, E and F) ----

def _qt():
    from PySide import QtGui

    try:
        from PySide import QtWidgets
    except ImportError:
        QtWidgets = QtGui
    return QtGui, QtWidgets


def modal_info():
    """The modal dialog in front, if any: { class, title, text, buttons,
    widget }. Qt keeps timers running inside a dialog's own loop, so this is
    read even while the dialog blocks the window."""
    try:
        QtGui, QtWidgets = _qt()
        app = QtWidgets.QApplication.instance()
        w = app.activeModalWidget() if app is not None else None
    except Exception:
        return None
    if w is None:
        return None
    parts = []
    for getter in ("text", "informativeText"):
        try:
            v = getattr(w, getter)()
            if v:
                parts.append(v)
        except Exception:
            pass
    if not parts:
        try:
            for lab in w.findChildren(QtWidgets.QLabel):
                if lab.isVisible() and lab.text():
                    parts.append(lab.text())
        except Exception:
            pass
    buttons = []
    try:
        for b in w.findChildren(QtWidgets.QAbstractButton):
            if b.isVisible() and b.isEnabled() and b.text() and not b.isCheckable():
                buttons.append(b.text().replace("&", ""))
    except Exception:
        pass
    import re as _re

    text = _re.sub(r"<[^>]+>", " ", " ".join(parts))
    return {"class": w.metaObject().className(), "title": w.windowTitle(), "text": " ".join(text.split())[:600], "buttons": buttons, "widget": w}


def press(d, label):
    """Press a dialog's button by its text (never by where it is)."""
    try:
        QtGui, QtWidgets = _qt()
        want = label.lower().replace("&", "")
        for b in d["widget"].findChildren(QtWidgets.QAbstractButton):
            if b.isVisible() and b.isEnabled() and b.text().lower().replace("&", "") == want:
                b.click()
                return True
    except Exception:
        pass
    return False


def _bundle():
    exe = sys.executable
    return exe.split(".app/")[0] + ".app" if ".app/" in exe else exe


def _autorecovery_on():
    """FreeCAD's own auto-recovery stays on (unattended use, C): turned on
    once if it is off, and the change logged."""
    try:
        from . import keep

        al = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Mod/AgentLink")
        if al.GetBool("AutoRecoveryChecked", False):
            return
        g = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Document")
        if not g.GetBool("AutoSaveEnabled", True):
            g.SetBool("AutoSaveEnabled", True)
            keep.settings_log({"tool": "agentlink", "path": "BaseApp/Preferences/Document", "name": "AutoSaveEnabled", "from": False, "to": True, "restart": False,
                               "why": "FreeCAD's own recovery copies kept on for unattended use"})
        al.SetBool("AutoRecoveryChecked", True)
    except Exception:
        pass


def looks_like_call(line):
    """A JSON-RPC 2.0 message: an object with jsonrpc "2.0" and a method."""
    if len(line) > 8_000_000 or not line.lstrip().startswith("{"):
        return False
    try:
        m = json.loads(line)
    except ValueError:
        return False
    return isinstance(m, dict) and m.get("jsonrpc") == "2.0" and isinstance(m.get("method"), str)


class Link:
    def __init__(self, port=PORT):
        self.port = port
        self.inbox = queue.Queue()
        self.sock = None
        self.timer = None
        self.session = None
        self.server = None
        self.going = False

    def _ready(self):
        """The session, made at the first call (so a link turned on as the
        window opens leaves the start page alone), and kept on the document
        the person has in front: switching documents moves the agent too."""
        active = FreeCAD.ActiveDocument
        if self.session is None:
            out = os.path.expanduser("~/AgentLink")
            os.makedirs(out, exist_ok=True)
            self.session = Session(doc=active or FreeCAD.newDocument("AgentLink"), out_dir=out)
            # session_state says which FreeCAD answered: this window, not the hidden one.
            self.session.where = "window"
            from .server import VERSION

            self.server = build_server(self.session, version=VERSION)
            # Each call shown in the window as it runs (agentlink/echo.py).
            from . import echo

            echo.wrap(self.server, self.session)
        elif getattr(self.session, "keep_doc", False):
            # An extension holds the session on a document of its own
            # (agentlink/ext.py): kept whatever the person brings to the front.
            if self.session.doc.Name not in FreeCAD.listDocuments():
                self.session.keep_doc = False
                self.session.doc = active or FreeCAD.newDocument("AgentLink")
        elif active is not None and active is not self.session.doc:
            s = self.session
            s.doc = active
            s.doc.UndoMode = 1
            s.checkpoints, s.steps, s.meta = {}, [], {}
        elif self.session.doc not in FreeCAD.listDocuments().values():
            self.session.doc = active or FreeCAD.newDocument("AgentLink")

    def start(self):
        from PySide import QtCore

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", self.port))
        self.sock.listen(2)
        threading.Thread(target=self._accept, daemon=True).start()
        self.timer = QtCore.QTimer()
        self.timer.timeout.connect(self._drain)
        self.timer.start(30)
        # The heartbeat: written from the GUI's own thread every 2 s, so a
        # stuck event loop shows as a stale file to the supervisor's
        # watchdog; it also reads (and, where the rule says, answers) a modal
        # dialog in front.
        self.busy = None
        self.in_call = False
        self._dialog = None
        self.beat = QtCore.QTimer()
        self.beat.timeout.connect(self._beat)
        self.beat.start(2000)
        _autorecovery_on()
        self._beat()
        FreeCAD.Console.PrintMessage(f"Agent Link: listening on 127.0.0.1:{self.port}\n")

    def _beat(self):
        try:
            from . import keep, restart_mark

            d = modal_info()
            if d is not None:
                key = (d["class"], d["title"], d["text"])
                if self._dialog is None or self._dialog[0] != key:
                    self._dialog = (key, time.time())
                d["since"] = self._dialog[1]
                s = self.session
                act = keep.dialog_answer_for(d, ours=keep.launched_by_us(), restarting=restart_mark.read() is not None,
                                             save_rule=getattr(s, "restart_save", "agent") if s else "agent", agent_docs=sorted(getattr(s, "agent_docs", ()) or ()) if s else [])
                if act[0] == "press" and press(d, act[1]):
                    d["pressed"] = act[1]
                    keep.log_watchdog({"event": "dialog", "title": d["title"], "pressed": act[1]})
            else:
                self._dialog = None
            docs = [{"name": x.Name, "label": x.Label, "file": x.FileName or ""} for x in FreeCAD.listDocuments().values()]
            keep._save(keep.path("heartbeat.json"), {
                "pid": os.getpid(), "t": time.time(), "app": _bundle(), "freecad": ".".join(FreeCAD.Version()[:3]), "port": self.port,
                "docs": docs, "busy": self.busy, "pinned": getattr(self.session, "doc", None).Name if self.session and getattr(self.session, "doc", None) else None,
                "dialog": {k: v for k, v in d.items() if k != "widget"} if d else None,
            })
        except Exception:
            pass

    def stop(self):
        if getattr(self, "beat", None):
            self.beat.stop()
        if self.timer:
            self.timer.stop()
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
        FreeCAD.Console.PrintMessage("Agent Link: stopped\n")

    def _accept(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._read, args=(conn,), daemon=True).start()

    def _read(self, conn):
        """Lines of JSON-RPC, one object each. Anything else ends the
        connection at once: a web page can reach 127.0.0.1 too, and a
        browser's request starts with an HTTP line ("POST / HTTP/1.1") and
        headers, so its body — a tool call — was run once the lines that
        were not JSON had been skipped. Only a client speaking the protocol
        from its first line is served."""
        f = conn.makefile("rw", encoding="utf-8", newline="\n")
        try:
            for line in f:
                if not line.strip():
                    continue
                if not looks_like_call(line):
                    break
                self.inbox.put((line, f, conn))
                continue
            else:
                return
        except (OSError, UnicodeDecodeError):
            pass
        try:
            conn.shutdown(socket.SHUT_RDWR)
            conn.close()
        except OSError:
            pass

    def _back(self):
        """After app_restart: False while the documents it is reopening are
        not all open yet (the calls wait in the inbox); then the one that was
        in front is put in front again and the mark taken away."""
        from . import restart_mark

        m = restart_mark.read()
        if m is None:
            return True
        if restart_mark.going(m):
            self.going = True
            # This window is quitting to start again (app_restart): no more
            # calls here. Each connection dropped, so the caller waits for
            # the new window instead of this one answering from no document.
            if self.sock:
                try:
                    self.sock.close()
                except OSError:
                    pass
                self.sock = None
            while True:
                try:
                    _line, _f, conn = self.inbox.get_nowait()
                except queue.Empty:
                    break
                try:
                    import socket as _s

                    conn.shutdown(_s.SHUT_RDWR)
                    conn.close()
                except OSError:
                    pass
            return False
        open_ = {os.path.abspath(d.FileName) for d in FreeCAD.listDocuments().values() if d.FileName}
        if any(os.path.abspath(p) not in open_ for p in m.get("reopens") or [] if os.path.isfile(p)):
            return False
        want = m.get("active")
        d = next((d for d in FreeCAD.listDocuments().values() if want and d.FileName and os.path.abspath(d.FileName) == os.path.abspath(want)), None)
        if d is not None:
            try:
                FreeCAD.setActiveDocument(d.Name)
                if FreeCAD.GuiUp:
                    import FreeCADGui

                    FreeCADGui.setActiveDocument(d.Name)
            except Exception:
                pass
        # The documents app_restart did not save: reopened from their copies
        # beside their files (unattended use, L).
        for c in m.get("recover") or []:
            try:
                from .families import unattended

                if os.path.isfile(c.get("copy", "")):
                    unattended.open_copy(c["copy"], c.get("name") or "doc", c.get("label") or c.get("name") or "doc", float(c.get("t") or time.time()))
            except Exception as e:
                FreeCAD.Console.PrintWarning(f"Agent Link: could not reopen {c.get('label')} from its copy: {e}\n")
        restart_mark.clear()
        return True

    def _blocked(self, line, f, d):
        """A call that came while a modal dialog holds the window: answered
        at once, not run inside the dialog's loop."""
        try:
            mid = json.loads(line).get("id")
        except ValueError:
            mid = None
        msg = (f"Blocked by a dialog in the FreeCAD window: \"{d['title']}\" — {d['text'][:300]} (buttons: {', '.join(d['buttons']) or 'none'}). "
               "dialog_answer { button } presses one if it is a harmless information box; anything else is the person's: ask them in chat. This call was not run.") if d else \
            "The window is busy with a call that is waiting on a dialog: this call was not run."
        text = compact({"error": {"code": "invalid_state", "message": msg}})
        try:
            f.write(compact({"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": text}], "isError": True}}) + "\n")
            f.flush()
        except OSError:
            pass

    def _drain(self):
        if not self.inbox.empty() and not self._back():
            return
        if self.inbox.empty():
            return
        d = modal_info()
        if d is not None or self.in_call:
            # Inside a dialog's own loop (Qt runs timers there): no tool call
            # runs nested in it but dialog_answer; the rest are answered
            # "blocked" at once. With no dialog (a call still in hand), they
            # wait their turn.
            items = []
            while True:
                try:
                    items.append(self.inbox.get_nowait())
                except queue.Empty:
                    break
            wait = []
            for it in items:
                name = _tool_name(it[0])
                if d is not None and name == "dialog_answer":
                    self._run_one(*it)
                elif d is not None and name is not None:
                    self._blocked(it[0], it[1], d)
                elif d is not None and not self.in_call:
                    self._run_one(*it)
                else:
                    wait.append(it)
            for it in wait:
                self.inbox.put(it)
            return
        while True:
            try:
                line, f, conn = self.inbox.get_nowait()
            except queue.Empty:
                return
            self._run_one(line, f, conn)
            if self.going:
                continue

    def _run_one(self, line, f, conn):
        if self.going:
            # Quitting (app_restart answered just before): dropped, so the
            # caller sends it to the window that comes up next.
            try:
                import socket as _s

                conn.shutdown(_s.SHUT_RDWR)
                conn.close()
            except OSError:
                pass
            return
        prev = (self.busy, self.in_call)
        try:
            self._ready()
            name = _tool_name(line)
            if name is not None and name != "dialog_answer":
                self.busy, self.in_call = name, True
            try:
                reply = self.server.handle(json.loads(line))
            finally:
                self.busy, self.in_call = prev
        except ValueError:
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "not JSON"}}
        if not self.going and _tool_name(line) == "app_restart":
            from . import restart_mark

            self.going = restart_mark.going(restart_mark.read())
            if self.going and self.sock:
                try:
                    self.sock.close()
                except OSError:
                    pass
                self.sock = None
        try:
            if reply is not None:
                f.write(compact(reply) + "\n")
            while self.server is not None and self.server.outbox:
                f.write(compact(self.server.outbox.pop(0)) + "\n")
            f.flush()
        except OSError:
            pass
        if FreeCAD.GuiUp and not self.in_call:
            import FreeCADGui

            FreeCADGui.updateGui()
