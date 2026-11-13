# SPDX-License-Identifier: LGPL-2.1-or-later
# Unattended use on a real FreeCAD: checkpoints as saved copies (A), the
# records inside undo (B), auto-save copies (C), and the rest of item 2 that
# runs headless.
import json
import os
import tempfile
import unittest

_HOME = tempfile.mkdtemp(prefix="agentlink-home-")

from _pd_base import HAVE, Base  # noqa: E402


@unittest.skipUnless(HAVE, "FreeCAD with PartDesign and Sketcher is not importable here")
class Unattended(Base):
    @classmethod
    def setUpClass(cls):
        # Its own home for this class, whatever other modules did to the env.
        cls._was_home = os.environ.get("AGENTLINK_HOME")
        os.environ["AGENTLINK_HOME"] = _HOME
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        if cls._was_home is None:
            os.environ.pop("AGENTLINK_HOME", None)
        else:
            os.environ["AGENTLINK_HOME"] = cls._was_home
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

    def test_7_recover_beside_the_file(self):
        import time

        import FreeCAD

        s = self.session
        self.box("U r", [8, 8, 8])
        path = os.path.join(self.dir, "recover_me.FCStd")
        self.ok("doc_save", {"path": path})
        s.autosave_now()
        name, label = s.doc.Name, s.doc.Label
        listed = self.ok("app_recover")
        self.assertIn(name, [d["document"] for d in listed["documents"]])
        r = self.ok("app_recover", {"action": "auto", "documents": [{"name": name, "label": label, "file": path}], "since": time.time() + 120})
        rec = r["recovered"][0]
        self.assertIn("(recovered", rec["as"])
        self.assertEqual(rec["file"], path)
        self.assertIn("lost", rec)
        nd = FreeCAD.getDocument(rec["document"])
        self.assertNotEqual(os.path.abspath(nd.FileName), os.path.abspath(path))
        self.assertEqual(self.refused("app_recover", {"action": "accept", "document": rec["document"]})["code"], "not_allowed")
        self.assertIn("close it first", self.refused("app_recover", {"action": "accept", "document": rec["document"], "confirm": "save over recover_me.FCStd"})["message"])
        FreeCAD.closeDocument(name)
        ok = self.ok("app_recover", {"action": "accept", "document": rec["document"], "confirm": "save over recover_me.FCStd"})
        self.assertEqual(ok["saved"], path)
        self.assertTrue(os.path.isfile(ok["previousFile"]))
        self.assertEqual(self.ok("dialog_answer")["dialog"], None)

    def test_8_settings_logged_and_snapshotted(self):
        from agentlink import keep

        r = self.ok("pref_set", {"path": "BaseApp/Preferences/Mod/AgentLinkTest", "name": "Probe", "value": 3})
        self.assertIn("Probe", r["set"])
        h = self.ok("pref_history", {"limit": 5})["changes"]
        self.assertEqual((h[0]["tool"], h[0]["name"], h[0]["to"]), ("pref_set", "Probe", 3))
        import FreeCAD

        if os.path.isfile(os.path.join(FreeCAD.getUserConfigDir(), "user.cfg")):
            self.assertTrue(os.listdir(keep.path("settings")))
            if not os.environ.get("AGENTLINK_SUPERVISED"):
                self.assertEqual(self.refused("pref_rollback", {"to": h[0]["change"]})["code"], "unavailable")
        self.assertEqual(self.refused("pref_rollback", {"to": 99999})["code"], "not_found")

    def test_9_smoke_test_passes_here(self):
        import sys

        from agentlink import smoke

        r = smoke.run()
        self.assertTrue(r["ok"], r["failures"])
        self.assertGreater(r["families"]["loaded"], 30)
        lib = os.environ.get("FREECAD_LIB")
        out = smoke.against(python=sys.executable, lib=lib, addon_dirs=[os.path.dirname(os.path.dirname(os.path.abspath(__file__)))], timeout=300)
        self.assertTrue(out["ok"], out["failures"])

    def test_10_doc_switch_and_own_errors(self):
        import FreeCAD

        s = self.session
        home = s.doc
        other = FreeCAD.newDocument("Elsewhere")
        try:
            r = self.ok("doc_switch", {"document": other.Name})
            self.assertEqual(s.doc.Name, other.Name)
            self.assertIn(other.Label, [d["label"] for d in self.ok("doc_switch")["open"]])
            self.assertEqual(self.refused("doc_switch", {"document": "Nowhere"})["code"], "not_found")
            # M4: an unrelated object failing in the recompute is a note, not a refusal
            self.box("K box", [10, 10, 10])
            bad = s.doc.addObject("Part::Extrusion", "BadExtrusion")
            s.doc.recompute()
            broke = "Invalid" in bad.State or "Error" in bad.State or not bad.isValid()
            out = self.ok("object_set", {"object": "K box", "values": {"Length": 12}})
            if broke:
                self.assertIn("BadExtrusion", out.get("note", ""))
            self.assertAlmostEqual(s.doc.getObjectsByLabel("K box")[0].Shape.Volume, 1200, places=3)
            del bad, r
        finally:
            self.ok("doc_switch", {"document": home.Name})
            FreeCAD.closeDocument(other.Name)
        self.assertEqual(self.ok("session_state")["document"], home.Name)

    def test_11_macros(self):
        import tempfile

        import FreeCAD

        mdir = tempfile.mkdtemp(prefix="agentlink-macros-")
        FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Macro").SetString("MacroPath", mdir)
        s = self.session
        self.ok("history_checkpoint", {"name": "macro start"})
        self.ok("part_primitive", {"kind": "box", "size": [60, 20, 2], "name": "M plate"})
        saved = self.ok("macro_save", {"name": "Plate test", "from": "macro start", "params": {"length": 60, "wall": 2}})
        self.assertEqual(saved["calls"], 1)
        self.assertEqual(saved["constants"], {"LENGTH": 60, "WALL": 2})
        text = open(saved["path"]).read()
        self.assertIn("[LENGTH, 20, WALL]", text)
        self.assertEqual(self.refused("macro_run", {"name": "Plate test"})["code"], "not_allowed")
        self.assertEqual(self.refused("macro_approve", {"name": "Plate test", "confirm": "yes"})["code"], "not_allowed")
        self.ok("macro_approve", {"name": "Plate test", "confirm": "approve Plate test"})
        self.assertEqual([m["state"] for m in self.ok("macro_list")["macros"] if m["name"] == "Plate test"], ["approved"])
        n = len(s.bodies())
        r = self.ok("macro_run", {"name": "Plate test", "params": {"LENGTH": 30}})
        self.assertEqual(r["calls"], 1)
        vols = sorted(round(o.Shape.Volume) for o in s.bodies())
        self.assertIn(1200, vols)
        self.assertEqual(len(s.bodies()), n + 1)
        # a refused call part way rolls the run back whole
        n = len(s.bodies())
        e = self.refused("macro_run", {"name": "Plate test", "params": {"LENGTH": -5}})
        self.assertIn("Rolled back", e["message"])
        self.assertEqual(len(s.bodies()), n)
        self.assertIn("params", self.refused("macro_run", {"name": "Plate test", "params": {"NOPE": 1}})["message"])
        # its code changed: approval no longer holds
        with open(saved["path"], "a") as f:
            f.write("# edited\n")
        self.assertIn("changed", self.refused("macro_run", {"name": "Plate test"})["message"])
        self.assertEqual(self.ok("macro_show", {"name": "Plate test"})["state"], "changed")
        self.ok("macro_unapprove", {"name": "Plate test"})

    def test_12_addon_manager(self):
        import tempfile

        import FreeCAD

        if tuple(int(x) for x in FreeCAD.Version()[:2]) < (1, 1):
            e = self.refused("addon")
            self.assertEqual(e["code"], "unavailable")
            self.assertIn("1.1", e["message"])
            return
        src = os.path.join(tempfile.mkdtemp(prefix="agentlink-addon-"), "AgentLinkTestAddon")
        os.makedirs(src)
        open(os.path.join(src, "Init.py"), "w").write("# a test add-on: does nothing\n")
        open(os.path.join(src, "package.xml"), "w").write("<?xml version='1.0'?><package format='1'><name>AgentLinkTestAddon</name><version>0.1</version><description>Agent Link test</description></package>\n")
        r = self.call("addon", {"action": "list"})
        if r[0] and r[1]["error"]["code"] == "unavailable":
            self.skipTest(r[1]["error"]["message"])
        self.assertEqual(self.refused("addon", {"action": "install", "name": "AgentLinkTestAddon", "url": src})["code"], "not_allowed")
        got = self.ok("addon", {"action": "install", "name": "AgentLinkTestAddon", "url": src, "confirm": "install AgentLinkTestAddon"})
        self.assertEqual(got["smoke"], "passed")
        names = [x["name"] for x in self.ok("addon", {"action": "list"})["installed"]]
        self.assertIn("AgentLinkTestAddon", names)
        up = self.ok("addon", {"action": "update", "name": "AgentLinkTestAddon", "url": src})
        self.assertTrue(os.path.isdir(up["previous"]))
        self.assertIn("agentlink_update", self.refused("addon", {"action": "update", "name": "AgentLink"})["message"])
        self.ok("addon", {"action": "remove", "name": "AgentLinkTestAddon", "confirm": "remove AgentLinkTestAddon"})
        self.assertNotIn("AgentLinkTestAddon", [x["name"] for x in self.ok("addon", {"action": "list"})["installed"]])

    def test_13_machine_queue_and_estimates(self):
        from agentlink import keep

        st = self.ok("session_state")
        self.assertIn("heavySlots", st["machine"])
        self.assertTrue(self.ok("queue_go")["go"])
        # Past runs say view_render is slow at this size: the first call is
        # answered with the estimate, the same call again runs it.
        q = keep.queue_state()
        q["timings"] = {"view_render": [[1, 400.0], [2, 800.0]]}
        keep.save_queue(q)
        self.box("N box", [10, 10, 10])
        os.environ["AGENTLINK_GATE"] = "1"
        try:
            e = self.refused("view_render", {"view": "iso", "inline": False})
            self.assertIn("takes about", e["message"])
            self.ok("view_render", {"view": "iso", "inline": False})
            # a heavy change gets a checkpoint first
            import types

            from agentlink.heavy import Gate

            n = len(keep.cp_index(self.session.doc.Name))
            Gate(self.session).before(types.SimpleNamespace(name="part_extract", changes=True), {})
            idx = keep.cp_index(self.session.doc.Name)
            self.assertEqual(len(idx), n + 1)
            self.assertEqual(idx[-1]["name"], "before part_extract")
        finally:
            os.environ.pop("AGENTLINK_GATE", None)
            q = keep.queue_state()
            q["timings"] = {}
            keep.save_queue(q)


if __name__ == "__main__":
    unittest.main()
