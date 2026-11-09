# SPDX-License-Identifier: LGPL-2.1-or-later
# The link in the FreeCAD window: the same six tools, served on a local port
# (127.0.0.1 only) to the stdio relay an MCP client starts
# (agentlink_headless.py --connect). A thread reads the socket; every call
# runs on the GUI's own thread, from a timer, because FreeCAD's documents
# and the 3D view must not be touched from another — so the person sees
# each body arrive in the document they have open.

import json
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
        doc = FreeCAD.ActiveDocument or FreeCAD.newDocument("AgentLink")
        self.session = Session(doc=doc, out_dir=FreeCAD.getUserAppDataDir())
        from .server import VERSION

        self.server = build_server(self.session, version=VERSION)

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
                self.inbox.put((line, f))

    def _drain(self):
        while True:
            try:
                line, f = self.inbox.get_nowait()
            except queue.Empty:
                return
            try:
                reply = self.server.handle(json.loads(line))
            except ValueError:
                reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "not JSON"}}
            try:
                if reply is not None:
                    f.write(compact(reply) + "\n")
                while self.server.outbox:
                    f.write(compact(self.server.outbox.pop(0)) + "\n")
                f.flush()
            except OSError:
                pass
            if FreeCAD.GuiUp:
                import FreeCADGui

                FreeCADGui.updateGui()
