# SPDX-License-Identifier: LGPL-2.1-or-later
# The unattended-use store (agentlink/keep.py) without FreeCAD: what is kept,
# what is due, what the link answers.
import json
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agentlink import keep  # noqa: E402


class Home(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["AGENTLINK_HOME"] = self.tmp.name

    def tearDown(self):
        os.environ.pop("AGENTLINK_HOME", None)
        self.tmp.cleanup()


class Checkpoints(Home):
    def test_cap(self):
        self.assertEqual(keep.cap_bytes(10 * 1024 ** 3), 500 * keep.MB)
        self.assertEqual(keep.cap_bytes(100 * 1024 ** 3), int(0.02 * 100 * 1024 ** 3))
        self.assertEqual(keep.cap_bytes(100 * 1024 ** 3, 50), 50 * keep.MB)

    def test_prune_keeps_named_and_last_ten(self):
        es = [{"n": i, "name": "datum" if i == 1 else None, "bytes": 1} for i in range(1, 16)]
        kept, dropped = keep.cp_prune(es, cap=10 ** 9)
        self.assertEqual([e["n"] for e in kept], [1] + list(range(6, 16)))
        self.assertEqual(len(dropped), 4)
        # over the cap: the oldest unnamed go, the named one and the newest stay
        kept, _ = keep.cp_prune(es, cap=3)
        self.assertIn(1, [e["n"] for e in kept])
        self.assertIn(15, [e["n"] for e in kept])
        self.assertLessEqual(sum(e["bytes"] for e in kept), 3)

    def test_add_find_and_files(self):
        e = keep.cp_new("My Doc/1", name="before fillet", step=4, undo=3, tx=7)
        os.makedirs(os.path.dirname(e["file"]), exist_ok=True)
        open(e["file"], "wb").write(b"x" * 10)
        keep.cp_add("My Doc/1", e, cap=10 ** 9)
        self.assertEqual(keep.cp_find("My Doc/1", "before fillet")["id"], "cp1")
        self.assertEqual(keep.cp_find("My Doc/1", "cp1")["bytes"], 10)
        self.assertIn("My_Doc_1", e["file"])
        for i in range(12):
            x = keep.cp_new("My Doc/1", step=i)
            open(x["file"], "wb").write(b"y")
            keep.cp_add("My Doc/1", x, cap=10 ** 9)
        names = [x["id"] for x in keep.cp_index("My Doc/1")]
        self.assertEqual(len(names), 11)
        self.assertIn("cp1", names)
        self.assertEqual(len([f for f in os.listdir(keep.cp_dir("My Doc/1")) if f.endswith(".FCStd")]), 11)

    def test_undo_reach(self):
        e = {"tx": 5, "undo": 5}
        self.assertTrue(keep.can_undo_back(e, 8, 8))
        self.assertFalse(keep.can_undo_back(e, 30, 20))  # fell off the 20-step stack
        self.assertFalse(keep.can_undo_back(e, 8, 6))  # something else moved the stack
        self.assertTrue(keep.can_undo_back(e, 5, 0))


class AutoSave(Home):
    def test_due(self):
        now = time.time()
        self.assertFalse(keep.autosave_due(now - 10, 0, now, 3))
        self.assertTrue(keep.autosave_due(now - 400, 0, now, 1))
        self.assertTrue(keep.autosave_due(now - 10, 0, now, 10))
        self.assertFalse(keep.autosave_due(now - 400, 5, now, 5))  # nothing changed

    def test_prune_and_recovery_order(self):
        d = keep.autosave_dir("Doc")
        os.makedirs(d)
        for i in range(7):
            p = os.path.join(d, f"2026111{i}-000000.FCStd")
            open(p, "w").write("x")
            os.utime(p, (1000 + i, 1000 + i))
        self.assertEqual(len(keep.autosave_prune("Doc")), 2)
        fc = os.path.join(self.tmp.name, "fc.fcstd")
        open(fc, "w").write("x")
        src = keep.recovery_sources("Doc", [(fc, 5000)])
        self.assertEqual(src[0]["source"], "freecad")
        self.assertEqual(len(src), 6)

    def test_freecad_recovery_scan(self):
        root = os.path.join(self.tmp.name, "cache")
        d = os.path.join(root, "FreeCAD_Doc_x_1")
        os.makedirs(d)
        open(os.path.join(d, "fc_recovery_file.fcstd"), "w").write("x")
        open(os.path.join(d, "fc_recovery_file.xml"), "w").write("<AutoRecovery><Label>Bracket</Label></AutoRecovery>")
        self.assertEqual(keep.freecad_recovery_files([root], "Bracket")[0][2], "Bracket")
        self.assertEqual(keep.freecad_recovery_files([root], "Other"), [])

    def test_saving_flag(self):
        self.assertFalse(keep.saving())
        keep.set_saving(True)
        self.assertTrue(keep.saving())
        keep.set_saving(False)
        self.assertFalse(keep.saving())


class Watchdog(unittest.TestCase):
    def test_deadlines(self):
        self.assertEqual(keep.deadline("read"), 30)
        self.assertEqual(keep.deadline("change"), 120)
        self.assertEqual(keep.deadline("heavy", 300), 900)
        self.assertEqual(keep.deadline("heavy", 10), 120)

    def test_hung_needs_missing_pings(self):
        self.assertFalse(keep.hung(1000, 0, 999, 120))  # long but alive
        self.assertTrue(keep.hung(1000, 0, 900, 120))
        self.assertFalse(keep.hung(100, 0, 0, 120))  # within its deadline

    def test_key(self):
        self.assertEqual(keep.call_key("a", {"x": 1, "y": 2}), keep.call_key("a", {"y": 2, "x": 1}))


class Dialogs(Home):
    REC = {"class": "QDialog", "title": "Document Recovery", "text": "Press Start Recovery", "buttons": ["Start Recovery", "Skip"]}

    def test_recovery_only_when_ours(self):
        self.assertEqual(keep.dialog_answer_for(self.REC, ours=True), ("press", "skip"))
        self.assertEqual(keep.dialog_answer_for(self.REC, ours=False)[0], "report")

    def test_save_on_restart_follows_the_rule(self):
        d = {"class": "QMessageBox", "title": "Unsaved document", "text": "Do you want to save your changes to document 'Bracket' before closing?", "buttons": ["Save", "Discard", "Cancel"]}
        self.assertEqual(keep.dialog_answer_for(d, False, restarting=True, save_rule="agent", agent_docs=["Bracket"]), ("press", "save"))
        self.assertEqual(keep.dialog_answer_for(d, False, restarting=True, save_rule="agent", agent_docs=[]), ("press", "discard"))
        self.assertEqual(keep.dialog_answer_for(d, False, restarting=False)[0], "report")

    def test_harmless(self):
        self.assertTrue(keep.harmless({"class": "QMessageBox", "buttons": ["OK"]}))
        self.assertFalse(keep.harmless({"class": "QMessageBox", "buttons": ["Yes", "No"]}))
        self.assertFalse(keep.harmless({"class": "QFileDialog", "buttons": ["OK"]}))

    def test_launch_mark(self):
        self.assertFalse(keep.launched_by_us(42))
        keep.mark_launch(42, "crash")
        self.assertTrue(keep.launched_by_us(42))
        self.assertFalse(keep.launched_by_us(43))


class Settings(Home):
    def test_snapshot_log_rollback(self):
        cfg = os.path.join(self.tmp.name, "user.cfg")
        open(cfg, "w").write("<cfg>1</cfg>")
        snap = keep.settings_snapshot(cfg)
        keep.settings_log({"tool": "pref_set", "path": "View", "name": "Gradient", "from": True, "to": False, "snapshot": snap})
        h = keep.settings_history()
        self.assertEqual(h[0]["change"], 1)
        self.assertEqual(keep.settings_snapshot_for(1), snap)
        self.assertEqual(open(keep.settings_snapshot_for(os.path.basename(snap)[:8])).read(), "<cfg>1</cfg>")
        for _ in range(25):
            keep.settings_snapshot(cfg)
        self.assertEqual(len(os.listdir(os.path.join(self.tmp.name, "settings"))), keep.SETTINGS_KEEP)


class Macros(Home):
    def test_text_names_the_sizes(self):
        calls = [{"tool": "pd_pad", "args": {"sketch": "S", "length": 60}}, {"tool": "pd_thickness", "args": {"thickness": 2.0, "faces": ["top"]}}]
        t = keep.macro_text("Box", calls, {"wall": 2.0, "length": 60}, ["makes a box"])
        self.assertIn("WALL = 2.0", t)
        self.assertIn("'length': LENGTH", t)
        self.assertIn("'thickness': WALL", t)
        consts, cs = keep.macro_calls(t)
        self.assertEqual(consts, {"WALL": 2.0, "LENGTH": 60})
        self.assertEqual(len(cs), 2)
        compile(t, "m", "exec")

    def test_approval_follows_the_code(self):
        p = os.path.join(self.tmp.name, "Box.FCMacro")
        open(p, "w").write("x = 1\n")
        self.assertEqual(keep.approval_state("Box", p), "no")
        keep.approve("Box", p)
        self.assertEqual(keep.approval_state("Box", p), "approved")
        open(p, "w").write("x = 2\n")
        self.assertEqual(keep.approval_state("Box", p), "changed")
        keep.unapprove("Box")
        self.assertEqual(keep.approval_state("Box", p), "no")

    def test_names(self):
        self.assertTrue(keep.macro_name_ok("Bracket v2"))
        self.assertFalse(keep.macro_name_ok("../evil"))


class Machine(Home):
    AIR = {"machdep.cpu.brand_string": "Apple M4", "hw.perflevel0.physicalcpu": "4", "hw.ncpu": "10", "hw.memsize": str(16 * 1024 ** 3), "hw.model": "MacBookAir10,1"}

    def test_preset(self):
        m = keep.machine_from(self.AIR)
        self.assertEqual((m["fan"], m["heavySlots"], m["memoryGB"], m["memoryCeilingGB"]), (False, 1, 16.0, 8.0))
        pro = dict(self.AIR, **{"hw.model": "Mac16,5", "hw.perflevel0.physicalcpu": "12"})
        self.assertEqual(keep.machine_from(pro)["heavySlots"], 2)

    def test_readings(self):
        self.assertTrue(keep.thermal_limited("CPU_Scheduler_Limit = 100\nCPU_Speed_Limit = 80"))
        self.assertFalse(keep.thermal_limited("Note: No thermal warning level has been recorded"))
        self.assertEqual(keep.memory_pressure_level("kern.memorystatus_vm_pressure_level: 2"), "warn")
        self.assertEqual(keep.memory_pressure_level("System-wide memory free percentage: 45%"), "normal")
        self.assertAlmostEqual(keep.hid_idle_seconds('"HIDIdleTime" = 12500000000'), 12.5)

    def test_queue(self):
        q = keep.queue_state()
        self.assertIsNone(keep.estimate(q, "fea_solve", 1000))
        keep.log_timing(q, "fea_solve", 1000, 10)
        keep.log_timing(q, "fea_solve", 3000, 30)
        self.assertAlmostEqual(keep.estimate(q, "fea_solve", 24000), 240, places=3)
        m = keep.machine_from(self.AIR)
        self.assertEqual(keep.admit(q, m, False, False, "normal"), (True, None))
        self.assertFalse(keep.admit(q, m, True, False, "normal")[0])
        self.assertFalse(keep.admit(q, m, False, True, "normal")[0])
        self.assertFalse(keep.admit(q, m, False, False, "warn")[0])
        q["running"] = [{"tool": "fea_solve"}]
        self.assertIn("at once", keep.admit(q, m, False, False, "normal")[1])
        keep.save_queue(q)
        self.assertEqual(keep.queue_state()["running"][0]["tool"], "fea_solve")


if __name__ == "__main__":
    unittest.main()
