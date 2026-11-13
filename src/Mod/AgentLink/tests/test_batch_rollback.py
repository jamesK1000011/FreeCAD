# SPDX-License-Identifier: LGPL-2.1-or-later
# A batch that rolls back takes back what it did and nothing else: with
# FreeCAD's undo stack full, and when a call moved to another document.
import unittest

from _fc import HAVE, caller, server


@unittest.skipUnless(HAVE, "FreeCAD is not importable here")
class BatchRollback(unittest.TestCase):
    def setUp(self):
        self.session, srv = server()
        self.call = caller(srv)
        self.call("doc_new", {"name": "Rollback"})

    def bodies(self):
        return len(self.call("session_state")[1]["bodies"])

    def box(self, name):
        return {"tool": "part_primitive", "arguments": {"kind": "box", "size": [5, 5, 5], "name": name}}

    def test_full_undo_stack(self):
        # 22 steps: FreeCAD's 20-step stack is full, so the undo count no
        # longer moves; the rollback used to undo nothing and say it had.
        for i in range(22):
            err, out = self.call("part_primitive", self.box(f"Pre{i}")["arguments"])
            self.assertFalse(err, out)
        n = self.bodies()
        err, b = self.call("batch", {"calls": [self.box("A"), self.box("B"), {"tool": "part_primitive", "arguments": {"kind": "nope"}}]})
        self.assertFalse(err, b)
        self.assertEqual(b["undone"], 2, b)
        self.assertEqual(self.bodies(), n)

    def test_too_long_to_roll_back_is_refused(self):
        n = self.bodies()
        err, e = self.call("batch", {"calls": [self.box(f"X{i}") for i in range(21)]})
        self.assertTrue(err)
        self.assertIn("undo steps", e["error"]["message"])
        self.assertEqual(self.bodies(), n)
        err, ok = self.call("batch", {"calls": [self.box(f"X{i}") for i in range(21)], "rollback": False})
        self.assertFalse(err, ok)

    def test_other_document_is_not_undone(self):
        self.call("part_primitive", self.box("Keep")["arguments"])
        home = self.session.doc
        n = home.UndoCount
        err, b = self.call("batch", {"calls": [{"tool": "doc_new", "arguments": {"name": "Elsewhere"}}, self.box("There"), {"tool": "part_primitive", "arguments": {"kind": "nope"}}]})
        self.assertFalse(err, b)
        self.assertIn("warning", b)
        self.assertEqual(home.UndoCount, n)
        self.assertTrue(any(o.Label == "Keep" for o in home.Objects))

    def test_history_inside_a_rolling_batch_is_refused(self):
        err, e = self.call("batch", {"calls": [self.box("A"), {"tool": "history_undo", "arguments": {}}]})
        self.assertTrue(err)
        self.assertIn("history_undo", e["error"]["message"])


if __name__ == "__main__":
    unittest.main()
