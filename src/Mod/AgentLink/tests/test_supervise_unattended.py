# SPDX-License-Identifier: LGPL-2.1-or-later
# The supervisor's watchdog without FreeCAD (unattended use, D, E, F): a fake
# hidden FreeCAD and a fake window (a port, a pid, a heartbeat file) that
# hang, crash or sit behind a dialog on cue. Each case must be answered once,
# plainly, and the next call must go through.
import json
import os
import socket
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
AL = os.path.dirname(HERE)

FAKE_HIDDEN = textwrap.dedent('''
    import json, os, sys, time
    home = os.environ["AGENTLINK_HOME"]
    os.makedirs(home, exist_ok=True)
    json.dump({"pid": os.getpid(), "docs": [{"name": "Bracket", "label": "Bracket", "file": ""}]}, open(os.path.join(home, "hidden.json"), "w"))
    for line in sys.stdin:
        m = json.loads(line)
        if m.get("id") is None:
            continue
        name = (m.get("params") or {}).get("name")
        if name == "hang":
            time.sleep(3600)
        if name == "crash":
            os._exit(3)
        if name == "settings_restart":
            json.dump({"why": "settings"}, open(os.path.join(home, ".hidden-restart"), "w"))
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": m["id"], "result": {"content": [{"type": "text", "text": json.dumps({"rolledBackTo": "x"})}]}}) + "\\n")
            sys.stdout.flush()
            os._exit(0)
        body = {"recovered": [{"label": "Bracket", "source": "autosave", "time": "now", "lost": "about 1 min of changes lost"}]} if name == "app_recover" else {"ok": name}
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": m["id"], "result": {"content": [{"type": "text", "text": json.dumps(body)}]}}) + "\\n")
        sys.stdout.flush()
''')

FAKE_WINDOW = textwrap.dedent('''
    import json, os, socket, sys, threading, time
    home, port = os.environ["AGENTLINK_HOME"], int(os.environ["AGENTLINK_WINDOW_PORT"])
    state = {"beat": True, "dialog": None}
    def beat():
        while True:
            if state["beat"]:
                hb = {"pid": os.getpid(), "t": time.time(), "app": "Fake.app", "docs": [{"name": "Plate", "label": "Plate", "file": "/tmp/Plate.FCStd"}], "dialog": state["dialog"]}
                tmp = os.path.join(home, "heartbeat.json.tmp")
                json.dump(hb, open(tmp, "w")); os.replace(tmp, os.path.join(home, "heartbeat.json"))
            time.sleep(0.3)
    threading.Thread(target=beat, daemon=True).start()
    srv = socket.socket(); srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", port)); srv.listen(4)
    def serve(c):
        f = c.makefile("rw")
        for line in f:
            m = json.loads(line)
            name = (m.get("params") or {}).get("name")
            if name == "hang":
                state["beat"] = False
                time.sleep(3600)
            if name == "behind_dialog":
                state["dialog"] = {"class": "QMessageBox", "title": "Unsaved", "text": "Save changes?", "buttons": ["Save", "Discard"], "since": time.time()}
                time.sleep(3600)
            body = {"recovered": [{"label": "Plate", "source": "checkpoint", "time": "now", "lost": "nothing after it lost"}]} if name == "app_recover" else {"ok": name, "pid": os.getpid()}
            f.write(json.dumps({"jsonrpc": "2.0", "id": m["id"], "result": {"content": [{"type": "text", "text": json.dumps(body)}]}}) + "\\n"); f.flush()
    while True:
        c, _ = srv.accept()
        threading.Thread(target=serve, args=(c,), daemon=True).start()
''')


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Sup(unittest.TestCase):
    window = False

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="agentlink-sup-")
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.home)
        hidden = os.path.join(self.tmp, "fake_hidden.py")
        open(hidden, "w").write(FAKE_HIDDEN)
        self.port = free_port()
        self.env = {**os.environ, "AGENTLINK_HOME": self.home, "AGENTLINK_DEADLINE": "1", "AGENTLINK_GRACE": "1",
                    "AGENTLINK_WINDOW": "1" if self.window else "0", "AGENTLINK_WINDOW_PORT": str(self.port),
                    "AGENTLINK_RESTART_MARK": os.path.join(self.home, ".restarting")}
        self.procs = []
        if self.window:
            fw = os.path.join(self.tmp, "fake_window.py")
            open(fw, "w").write(FAKE_WINDOW)
            self.env["AGENTLINK_RELAUNCH"] = json.dumps([sys.executable, fw])
            self.procs.append(subprocess.Popen([sys.executable, fw], env=self.env))
            for _ in range(50):
                try:
                    socket.create_connection(("127.0.0.1", self.port), timeout=0.2).close()
                    break
                except OSError:
                    time.sleep(0.1)
        code = f"import sys; sys.path.insert(0, {AL!r}); from agentlink import supervise; supervise.run({hidden!r}, [])"
        self.sup = subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=self.env, text=True, bufsize=1)
        self.n = 0

    def tearDown(self):
        try:
            self.sup.stdin.close()
        except OSError:
            pass
        self.sup.kill()
        for p in self.procs:
            p.kill()
        subprocess.run(["pkill", "-f", os.path.join(self.tmp, "fake_window.py")], capture_output=True)

    def call(self, name, timeout=60):
        self.n += 1
        self.sup.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.n, "method": "tools/call", "params": {"name": name, "arguments": {}}}) + "\n")
        self.sup.stdin.flush()
        t0 = time.time()
        while time.time() - t0 < timeout:
            line = self.sup.stdout.readline()
            if not line:
                break
            m = json.loads(line)
            if m.get("id") == self.n:
                r = m["result"]
                return r.get("isError", False), json.loads(r["content"][0]["text"])
        self.fail(f"no answer to {name}")


class Hidden(Sup):
    def test_hang_is_stopped_recovered_and_said_once(self):
        self.assertEqual(self.call("ping")[1], {"ok": "ping"})
        err, out = self.call("hang")
        self.assertTrue(err)
        msg = out["error"]["message"]
        self.assertIn("stopped responding during hang", msg)
        self.assertIn("Bracket recovered from autosave", msg)
        self.assertEqual(self.call("ping")[1], {"ok": "ping"})
        log = open(os.path.join(self.home, "watchdog.log")).read()
        self.assertIn('"hang"', log)

    def test_twice_hung_is_refused(self):
        self.call("hang")
        self.call("hang")
        err, out = self.call("hang", timeout=5)
        self.assertTrue(err)
        self.assertIn("hung FreeCAD twice", out["error"]["message"])

    def test_settings_restart_is_quiet(self):
        self.assertEqual(self.call("settings_restart")[1], {"rolledBackTo": "x"})
        self.assertEqual(self.call("ping")[1], {"ok": "ping"})
        self.assertFalse(os.path.exists(os.path.join(self.home, ".hidden-restart")))

    def test_crash_is_recovered(self):
        err, out = self.call("crash")
        self.assertTrue(err)
        self.assertIn("exited with code 3", out["error"]["message"])
        self.assertIn("Bracket recovered", out["error"]["message"])
        self.assertEqual(self.call("ping")[1], {"ok": "ping"})


class WindowSide(Sup):
    window = True

    def test_dialog_answers_the_call_and_frees_the_next(self):
        self.assertEqual(self.call("ping")[1]["ok"], "ping")
        err, out = self.call("behind_dialog")
        self.assertTrue(err)
        self.assertIn("Blocked by a dialog", out["error"]["message"])
        self.assertIn("Save changes?", out["error"]["message"])
        self.assertEqual(self.call("ping")[1]["ok"], "ping")

    def test_hang_stops_relaunches_and_recovers(self):
        first = self.call("ping")[1]["pid"]
        err, out = self.call("hang", timeout=90)
        self.assertTrue(err)
        msg = out["error"]["message"]
        self.assertIn("stopped responding during hang", msg)
        self.assertIn("Plate recovered from checkpoint", msg)
        again = self.call("ping")[1]["pid"]
        self.assertNotEqual(first, again)
        launch = json.load(open(os.path.join(self.home, "launch.json")))
        self.assertEqual(launch["why"], "hang")


if __name__ == "__main__":
    unittest.main()
