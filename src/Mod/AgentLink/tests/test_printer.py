# SPDX-License-Identifier: LGPL-2.1-or-later
# The printer tools against a mock printer: an HTTP server in a thread
# answering as OctoPrint, Moonraker and PrusaLink do. Never a real printer.
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
if os.environ.get("FREECAD_LIB"):
    sys.path.append(os.environ["FREECAD_LIB"])
try:
    import FreeCAD  # noqa: F401

    HAVE = True
except ImportError:
    HAVE = False

from agentlink import printers as P  # noqa: E402


class Mock(BaseHTTPRequestHandler):
    log = []
    job = {"state": "Operational"}

    def log_message(self, *a):
        pass

    def _send(self, code, obj=None):
        body = json.dumps(obj).encode() if obj is not None else b""
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def _auth(self):
        if self.path.startswith("/digest/"):
            a = self.headers.get("Authorization") or ""
            if not a.startswith("Digest ") or 'username="maker"' not in a:
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Digest realm="Printer API", nonce="abc123", qop="auth", algorithm=MD5')
                self.send_header("Content-Length", "0")
                self.end_headers()
                return False
            self.path = self.path[len("/digest"):]
            return True
        if self.path.startswith("/api/") and not self.path.startswith("/api/v1/") and self.headers.get("X-Api-Key") != "OCTOKEY":
            self._send(403, {"error": "bad key"})
            return False
        return True

    def handle_any(self, method):
        if not self._auth():
            return
        body = self._body()
        Mock.log.append({"method": method, "path": self.path, "headers": dict(self.headers), "body": body})
        p = self.path
        if p == "/api/job" and method == "GET":
            return self._send(200, {"state": Mock.job["state"], "job": {"file": {"name": "box.gcode"}}, "progress": {"completion": 42.123, "printTimeLeft": 600}})
        if p == "/api/printer":
            return self._send(200, {"state": {"text": "Operational"}, "temperature": {"tool0": {"actual": 210.04, "target": 210}, "bed": {"actual": 59.9, "target": 60}}})
        if p.startswith("/api/files/") and method == "POST":
            return self._send(201, {"done": True, "files": {"local": {"path": "box.gcode"}}})
        if p == "/api/job" and method == "POST":
            return self._send(204)
        if p.startswith("/printer/objects/query"):
            return self._send(200, {"result": {"status": {"print_stats": {"state": "standby", "filename": ""}, "extruder": {"temperature": 25.0, "target": 0}, "heater_bed": {"temperature": 24.5, "target": 0}, "virtual_sdcard": {"progress": 0.0}}}})
        if p == "/server/files/upload":
            return self._send(201, {"item": {"path": "box.gcode", "root": "gcodes"}, "print_started": b'name="print"' in body})
        if p in ("/printer/print/pause", "/printer/print/cancel"):
            return self._send(200, {"result": "ok"})
        if p == "/api/v1/status":
            return self._send(200, {"printer": {"state": "PRINTING", "temp_nozzle": 215.0, "target_nozzle": 215, "temp_bed": 60.1, "target_bed": 60}, "job": {"id": 7, "progress": 12, "time_remaining": 3000}})
        if p.startswith("/api/v1/files/") and method == "PUT":
            return self._send(201)
        if p.startswith("/api/v1/job/7"):
            return self._send(204)
        return self._send(404, {"error": p})

    def do_GET(self):
        self.handle_any("GET")

    def do_POST(self):
        self.handle_any("POST")

    def do_PUT(self):
        self.handle_any("PUT")

    def do_DELETE(self):
        self.handle_any("DELETE")


def start_mock():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Mock)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


class Printers(unittest.TestCase):
    """The HTTP side alone: no FreeCAD needed."""

    @classmethod
    def setUpClass(cls):
        cls.srv, cls.url = start_mock()
        cls.dir = tempfile.mkdtemp()
        cls.gcode = os.path.join(cls.dir, "box.gcode")
        with open(cls.gcode, "w") as f:
            f.write(";LAYER_CHANGE\n;Z:0.2\nG1 X1\n")

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        Mock.log.clear()

    def test_octoprint(self):
        p = {"kind": "octoprint", "url": self.url, "key": "OCTOKEY"}
        st = P.status(p)
        self.assertEqual(st["nozzle"], {"now": 210.0, "target": 210.0})
        self.assertEqual(st["job"]["progress"], 42.1)
        r = P.send(p, self.gcode, start=False)
        self.assertFalse(r["started"])
        up = [x for x in Mock.log if x["path"] == "/api/files/local"][0]
        self.assertIn(b'name="print"\r\n\r\nfalse', up["body"])
        self.assertIn(b"G1 X1", up["body"])
        P.control(p, "pause")
        self.assertEqual(json.loads(Mock.log[-1]["body"]), {"command": "pause", "action": "pause"})
        with self.assertRaises(P.PrinterError) as e:
            P.status({**p, "key": "WRONG"})
        self.assertIn("refused the key", e.exception.message)

    def test_moonraker(self):
        p = {"kind": "moonraker", "url": self.url}
        st = P.status(p)
        self.assertEqual(st["state"], "standby")
        self.assertEqual(st["job"]["progress"], 0.0)
        r = P.send(p, self.gcode, start=True)
        self.assertTrue(r["started"])
        P.control(p, "stop")
        self.assertEqual(Mock.log[-1]["path"], "/printer/print/cancel")

    def test_prusalink_key_and_digest(self):
        p = {"kind": "prusalink", "url": self.url, "key": "PL"}
        st = P.status(p)
        self.assertEqual(st["state"], "printing")
        P.send(p, self.gcode, start=False)
        put = [x for x in Mock.log if x["method"] == "PUT"][0]
        self.assertEqual(put["path"], "/api/v1/files/usb/box.gcode")
        self.assertEqual(put["headers"].get("Print-After-Upload"), "?0")
        self.assertEqual(put["headers"].get("X-Api-Key"), "PL")
        r = P.control(p, "stop")
        self.assertEqual(r["job"], 7)
        self.assertEqual(Mock.log[-1]["method"], "DELETE")
        d = {"kind": "prusalink", "url": self.url + "/digest", "user": "maker", "password": "secret"}
        self.assertEqual(P.status(d)["state"], "printing")

    def test_unreachable_and_bambu(self):
        with self.assertRaises(P.PrinterError) as e:
            P.status({"kind": "moonraker", "url": "http://127.0.0.1:9"})
        self.assertEqual(e.exception.code, "unavailable")
        with self.assertRaises(P.PrinterError) as e:
            P.kind_of("Bambu")
        self.assertEqual(e.exception.code, "unavailable")
        self.assertIn("LAN", e.exception.message)
        self.assertEqual(P.kind_of("klipper"), "moonraker")

    def test_file_keeps_secrets(self):
        d = tempfile.mkdtemp()
        P.save(d, {"a": {"kind": "octoprint", "url": "http://x", "key": "SECRET"}})
        self.assertEqual(os.stat(P.path(d)).st_mode & 0o777, 0o600)
        pub = P.public("a", P.load(d)["a"])
        self.assertNotIn("SECRET", json.dumps(pub))
        self.assertEqual(pub["auth"], "api key")


class PrinterTools(unittest.TestCase):
    """The tools through the server: build_server on FreeCAD; where FreeCAD
    is missing, the printer family alone (it needs no document)."""

    @classmethod
    def setUpClass(cls):
        cls.srv, cls.url = start_mock()
        cls.dir = tempfile.mkdtemp()
        if HAVE:
            from agentlink.tools import Session, build_server

            cls.server = build_server(Session(out_dir=cls.dir), lean=False)
        else:
            import types

            from agentlink.families import printer
            from agentlink.protocol import Server

            cls.server = Server("t", "0", printer.tools(types.SimpleNamespace(out_dir=cls.dir)))
        with open(os.path.join(cls.dir, "box.gcode"), "w") as f:
            f.write(";LAYER_CHANGE\n;Z:0.2\n")

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def call(self, name, args=None):
        r = self.server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args or {}}})["result"]
        return r.get("isError", False), json.loads(r["content"][0]["text"])

    def test_the_tools(self):
        err, a = self.call("printer_add", {"name": "Octo", "kind": "octoprint", "url": self.url, "key": "OCTOKEY"})
        self.assertFalse(err, a)
        self.assertTrue(a["reached"])
        self.assertNotIn("OCTOKEY", json.dumps(a))
        err, lst = self.call("printer_list")
        self.assertNotIn("OCTOKEY", json.dumps(lst))
        with open(os.path.join(self.dir, ".agentlink-printers.json")) as f:
            self.assertIn("OCTOKEY", f.read())
        err, st = self.call("printer_status", {"printer": "Octo"})
        self.assertFalse(err, st)
        self.assertEqual(st["bed"]["target"], 60.0)
        Mock.log.clear()
        err, r = self.call("printer_send", {"printer": "Octo", "file": "box.gcode", "start": True})
        self.assertTrue(err)
        self.assertIn("start Octo", r["error"]["message"])
        self.assertFalse(any(x["method"] == "POST" for x in Mock.log), "nothing uploaded without the confirm")
        err, r = self.call("printer_send", {"printer": "Octo", "file": "box.gcode"})
        self.assertFalse(err, r)
        self.assertFalse(r["started"])
        err, r = self.call("printer_send", {"printer": "Octo", "file": "box.gcode", "start": True, "confirm": "start Octo"})
        self.assertFalse(err, r)
        self.assertTrue(r["started"])
        err, r = self.call("printer_stop", {"printer": "Octo", "confirm": "stop it"})
        self.assertTrue(err)
        err, r = self.call("printer_stop", {"printer": "Octo", "confirm": "stop Octo"})
        self.assertFalse(err, r)
        err, b = self.call("printer_add", {"name": "X1", "kind": "bambu", "url": "192.168.1.9"})
        self.assertTrue(err)
        self.assertEqual(b["error"]["code"], "unavailable")


if __name__ == "__main__":
    unittest.main()
