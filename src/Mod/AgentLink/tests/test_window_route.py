# SPDX-License-Identifier: LGPL-2.1-or-later
# The supervisor's window-first routing: a tool call goes to the FreeCAD
# window's link when one listens, and to the hidden FreeCAD when none does.
import json
import os
import socket
import sys
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agentlink import supervise  # noqa: E402


class WindowRoute(unittest.TestCase):
    def test_no_window_means_hidden(self):
        supervise.WINDOW_PORT = _free_port()
        w = supervise.Window(lambda line: self.fail("nothing should be written"))
        self.assertFalse(w.call(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call"})))

    def test_window_answers(self):
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        supervise.WINDOW_PORT = srv.getsockname()[1]

        def serve():
            conn, _ = srv.accept()
            f = conn.makefile("rw", encoding="utf-8", newline="\n")
            for line in f:
                mid = json.loads(line)["id"]
                f.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/tools/list_changed"}) + "\n")
                f.write(json.dumps({"jsonrpc": "2.0", "id": mid, "result": {"where": "window"}}) + "\n")
                f.flush()

        threading.Thread(target=serve, daemon=True).start()
        got = []
        w = supervise.Window(got.append)
        self.assertTrue(w.call(json.dumps({"jsonrpc": "2.0", "id": 7, "method": "tools/call"})))
        self.assertEqual(len(got), 2)
        self.assertEqual(json.loads(got[1])["result"]["where"], "window")
        self.assertTrue(w.call(json.dumps({"jsonrpc": "2.0", "id": 8, "method": "tools/call"})))
        srv.close()

    def test_window_gone_is_said_once_not_answered_by_the_hidden_one(self):
        """The window answered, then went (the person quit, a crash): the
        next call is answered with that, not run on the hidden FreeCAD's
        empty document; the one after goes there."""
        import tempfile

        os.environ["AGENTLINK_RESTART_MARK"] = os.path.join(tempfile.mkdtemp(), "mark")
        try:
            srv = socket.socket()
            srv.bind(("127.0.0.1", 0))
            srv.listen(1)
            supervise.WINDOW_PORT = srv.getsockname()[1]

            def once():
                conn, _ = srv.accept()
                f = conn.makefile("rw", encoding="utf-8", newline="\n")
                line = f.readline()
                f.write(json.dumps({"jsonrpc": "2.0", "id": json.loads(line)["id"], "result": {}}) + "\n")
                f.flush()
                conn.close()
                srv.close()

            threading.Thread(target=once, daemon=True).start()
            got = []
            w = supervise.Window(got.append)
            self.assertTrue(w.call(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call"})))
            # The connection went with the window, mid-session, no restart mark.
            self.assertTrue(w.call(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call"})))
            self.assertTrue(json.loads(got[-1])["result"]["isError"])
            self.assertIn("may or may not", got[-1])
            self.assertFalse(w.call(json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call"})))
        finally:
            os.environ.pop("AGENTLINK_RESTART_MARK", None)

    def test_restarting_window_is_waited_for(self):
        """A restart mark standing: the call waits for the window to listen
        again instead of going to the hidden FreeCAD."""
        import tempfile
        import time

        from agentlink import restart_mark

        mark = os.path.join(tempfile.mkdtemp(), "mark")
        os.environ["AGENTLINK_RESTART_MARK"] = mark
        try:
            restart_mark.write(["/nowhere/a.FCStd"])
            port = _free_port()
            supervise.WINDOW_PORT = port

            def late():
                time.sleep(1.2)
                srv = socket.socket()
                srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                srv.bind(("127.0.0.1", port))
                srv.listen(1)
                conn, _ = srv.accept()
                f = conn.makefile("rw", encoding="utf-8", newline="\n")
                for line in f:
                    f.write(json.dumps({"jsonrpc": "2.0", "id": json.loads(line)["id"], "result": {"where": "window"}}) + "\n")
                    f.flush()
                    restart_mark.clear()

            threading.Thread(target=late, daemon=True).start()
            got = []
            t0 = time.time()
            self.assertTrue(supervise.Window(got.append).call(json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call"})))
            self.assertGreater(time.time() - t0, 1.0)
            self.assertEqual(json.loads(got[0])["result"]["where"], "window")
        finally:
            restart_mark.clear()
            os.environ.pop("AGENTLINK_RESTART_MARK", None)

    def test_stale_mark_is_ignored(self):
        import tempfile

        from agentlink import restart_mark

        mark = os.path.join(tempfile.mkdtemp(), "mark")
        os.environ["AGENTLINK_RESTART_MARK"] = mark
        try:
            with open(mark, "w") as f:
                json.dump({"t": 0, "reopens": []}, f)
            supervise.WINDOW_PORT = _free_port()
            self.assertFalse(supervise.Window(lambda line: None).call(json.dumps({"jsonrpc": "2.0", "id": 4, "method": "tools/call"})))
            self.assertFalse(os.path.exists(mark))
        finally:
            os.environ.pop("AGENTLINK_RESTART_MARK", None)

    def test_dropped_connection_waits_for_the_restart(self):
        """The window quits under a kept connection (app_restart): the call
        is sent again to the window once it is back, not to the hidden one."""
        import tempfile
        import time

        from agentlink import restart_mark

        os.environ["AGENTLINK_RESTART_MARK"] = os.path.join(tempfile.mkdtemp(), "mark")
        try:
            srv = socket.socket()
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind(("127.0.0.1", 0))
            srv.listen(1)
            port = srv.getsockname()[1]
            supervise.WINDOW_PORT = port

            def first():
                conn, _ = srv.accept()
                f = conn.makefile("rw", encoding="utf-8", newline="\n")
                line = f.readline()
                f.write(json.dumps({"jsonrpc": "2.0", "id": json.loads(line)["id"], "result": {"restarting": True}}) + "\n")
                f.flush()
                restart_mark.write(["/nowhere/a.FCStd"])
                srv.close()
                time.sleep(0.3)
                f.close()
                conn.close()
                time.sleep(1.0)
                again = socket.socket()
                again.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                again.bind(("127.0.0.1", port))
                again.listen(1)
                c2, _ = again.accept()
                g = c2.makefile("rw", encoding="utf-8", newline="\n")
                for ln in g:
                    g.write(json.dumps({"jsonrpc": "2.0", "id": json.loads(ln)["id"], "result": {"where": "window"}}) + "\n")
                    g.flush()
                    restart_mark.clear()

            threading.Thread(target=first, daemon=True).start()
            got = []
            w = supervise.Window(got.append)
            self.assertTrue(w.call(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call"})))
            time.sleep(0.6)
            self.assertTrue(w.call(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call"})))
            self.assertEqual(json.loads(got[-1])["result"]["where"], "window")
        finally:
            restart_mark.clear()
            os.environ.pop("AGENTLINK_RESTART_MARK", None)


_USED = set()


def _free_port():
    """A port no test here has had yet: a server thread of an earlier test
    can still hold the one the system offers again."""
    while True:
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        p = s.getsockname()[1]
        s.close()
        if p not in _USED:
            _USED.add(p)
            return p


if __name__ == "__main__":
    unittest.main()
