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
import threading

import FreeCAD

from .protocol import compact
from .tools import Session, build_server

PORT = 7318


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
        FreeCAD.Console.PrintMessage(f"Agent Link: listening on 127.0.0.1:{self.port}\n")

    def stop(self):
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
        f = conn.makefile("rw", encoding="utf-8", newline="\n")
        for line in f:
            if line.strip():
                self.inbox.put((line, f, conn))

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
        restart_mark.clear()
        return True

    def _drain(self):
        if not self.inbox.empty() and not self._back():
            return
        while True:
            try:
                line, f, conn = self.inbox.get_nowait()
            except queue.Empty:
                return
            if self.going:
                # Quitting (app_restart answered just before): dropped, so
                # the caller sends it to the window that comes up next.
                try:
                    import socket as _s

                    conn.shutdown(_s.SHUT_RDWR)
                    conn.close()
                except OSError:
                    pass
                continue
            try:
                self._ready()
                reply = self.server.handle(json.loads(line))
            except ValueError:
                reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "not JSON"}}
            if not self.going and "app_restart" in line:
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
            if FreeCAD.GuiUp:
                import FreeCADGui

                FreeCADGui.updateGui()
