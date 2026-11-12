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


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


if __name__ == "__main__":
    unittest.main()
