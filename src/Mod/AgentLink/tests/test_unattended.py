# SPDX-License-Identifier: LGPL-2.1-or-later
# Unattended use on a real FreeCAD: checkpoints as saved copies (A), the
# records inside undo (B), auto-save copies (C), and the rest of item 2 that
# runs headless.
import json
import os
import tempfile
import unittest

_HOME = tempfile.mkdtemp(prefix="agentlink-home-")
os.environ["AGENTLINK_HOME"] = _HOME

from _pd_base import HAVE, Base  # noqa: E402


@unittest.skipUnless(HAVE, "FreeCAD with PartDesign and Sketcher is not importable here")
class Unattended(Base):
    def box(self, name, size):
        return self.ok("part_primitive", {"kind": "box", "size": size, "name": name})

    def vol(self, label):
        return self.session.doc.getObjectsByLabel(label)[0].Shape.Volume

    def test_1_checkpoint_by_undo(self):
        self.box("U a", [10, 10, 10])
        cp = self.ok("history_checkpoint", {"name": "start"})
        self.assertTrue(os.path.isfile(cp["file"]))
        self.assertTrue(cp["file"].startswith(_HOME))
        self.ok("object_set", {"object": "U a", "values": {"Length": 20}})
        self.ok("object_set", {"object": "U a", "values": {"Width": 20}})
        self.assertAlmostEqual(self.vol("U a"), 4000, places=3)
        d = self.ok("history_diff", {"since": "start"})
        self.assertTrue(d["changed"])
        r = self.ok("history_restore", {"checkpoint": "start"})
        self.assertEqual(r["how"], "undo")
        self.assertAlmostEqual(self.vol("U a"), 1000, places=3)
        self.assertIn("start", [c.get("name") for c in self.ok("history_checkpoint", {"list": True})["checkpoints"]])

    def test_2_checkpoint_past_the_undo_stack(self):
        self.box("U b", [10, 10, 10])
        cp = self.ok("history_checkpoint", {"name": "before many"})
        limit = self.session.undo_limit()
        for i in range(limit + 5):
            self.ok("object_set", {"object": "U b", "values": {"Height": 11 + i}})
        name, fname = self.session.doc.Name, self.session.doc.FileName
        r = self.ok("history_restore", {"checkpoint": cp["checkpoint"]})
        self.assertEqual(r["how"], "file")
        self.assertEqual(self.session.doc.Name, name)
        self.assertAlmostEqual(self.vol("U b"), 1000, places=3)
        # the restore itself can be undone the same way
        back = self.ok("history_restore", {"checkpoint": r["before"]})
        self.assertEqual(back["how"], "file")
        self.assertAlmostEqual(self.vol("U b"), 10 * 10 * (11 + limit + 4), places=3)
        # and history_diff reads an old checkpoint from its saved copy
        for e in self.session.checkpoints.values():
            e["session"] = 0
        d = self.ok("history_diff", {"since": cp["checkpoint"]})
        self.assertEqual(d["from"], "its saved copy")
        self.assertIn("U b", d["bodies"])
        del fname

    def test_3_records_undo_with_the_change(self):
        self.ok("part_spec", {"spec": {"minWall": 1.2}})
        self.ok("part_spec", {"spec": {"minWall": 2.0}})
        from agentlink import store

        self.assertIsNotNone(self.session.doc.getObject(store.HOLDER))
        self.assertEqual(store.spec(self.session.doc)["minWall"], 2.0)
        self.ok("history_undo")
        self.assertEqual(store.spec(self.session.doc)["minWall"], 1.2)
        self.ok("history_redo")
        self.assertEqual(store.spec(self.session.doc)["minWall"], 2.0)
        # the holder is no part
        self.assertNotIn(store.HOLDER, [o.Name for o in self.session.bodies()])

    def test_4_records_from_an_old_file(self):
        import FreeCAD

        from agentlink import store

        d = FreeCAD.newDocument("OldRecords")
        try:
            d.Meta = {store.KEY + "spec": json.dumps({"minWall": 0.8})}
            self.assertEqual(store.spec(d)["minWall"], 0.8)
            store.write(d, "waivers", [])
            self.assertEqual(store.spec(d)["minWall"], 0.8)  # copied over
            self.assertIsNotNone(d.getObject(store.HOLDER))
        finally:
            FreeCAD.closeDocument(d.Name)

    def test_5_autosave(self):
        from agentlink import keep

        s = self.session
        s._autosaved[s.doc.Name] = (0, s.tx)
        self.box("U c", [5, 5, 5])
        files = [f for f in os.listdir(keep.autosave_dir(s.doc.Name)) if f.endswith(".FCStd")]
        self.assertTrue(files)
        self.assertFalse(keep.saving())

    def test_6_calls_kept_for_macros(self):
        n = len(self.session.calls)
        self.box("U d", [3, 3, 3])
        self.assertEqual(self.session.calls[-1]["tool"], "part_primitive")
        self.assertEqual(len(self.session.calls), n + 1)
        self.ok("history_checkpoint")
        self.assertEqual(len(self.session.calls), n + 1)


if __name__ == "__main__":
    unittest.main()
