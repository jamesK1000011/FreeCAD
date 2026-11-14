# SPDX-License-Identifier: LGPL-2.1-or-later
# The panel's Setups and Manual tabs, the parts that need no window: a
# setup's settings in a line, and the manual's rows as drawing_exploded steps.
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agentlink import panel_tabs  # noqa: E402


class PanelTabs(unittest.TestCase):
    def test_summary(self):
        self.assertEqual(panel_tabs.summary({"printer": "h2d", "nozzle": 0.4, "layerHeight": 0.2, "infill": 15}), "h2d, 0.4 nozzle, 0.2 layers, 15% infill")
        self.assertEqual(panel_tabs.summary({}), "")
        self.assertNotIn("fit", panel_tabs.summary({"fit": {"hole": 0.2}}))

    def test_manual_steps(self):
        rows = [("Base", ["Base"], ""), ("", [], "skipped"), ("Lid on", ["Lid", "Screw"], "Snug, not tight")]
        self.assertEqual(panel_tabs.manual_steps(rows), [{"title": "Base", "add": ["Base"]}, {"title": "Lid on", "add": ["Lid", "Screw"], "note": "Snug, not tight"}])

    def test_manual_plan_steps(self):
        rows = [("Base", ["Base"], ""), ("Lid on", ["Lid", "Screw"], "Snug, not tight"), ("Empty", [], "")]
        self.assertEqual(panel_tabs.manual_plan_steps(rows), [
            {"id": "s1", "place": [{"part": "Base"}], "text": "Base"},
            {"id": "s2", "place": [{"part": "Lid"}, {"part": "Screw"}], "text": "Snug, not tight", "notes": "Lid on"}])
        self.assertTrue(panel_tabs.missing_tool({"error": {"code": "not_found", "message": "manual_plan: no such tool — x."}}, "manual_plan"))
        self.assertFalse(panel_tabs.missing_tool({"error": {"code": "not_found", "message": "body Lid: none."}}, "manual_plan"))


if __name__ == "__main__":
    unittest.main()


try:
    from PySide import QtGui as _QtGui  # noqa: F401

    HAVE_QT = panel_tabs.QtWidgets is not None
except ImportError:
    HAVE_QT = False


@unittest.skipUnless(HAVE_QT, "no PySide here")
class NarrowPanel(unittest.TestCase):
    """Button rows wrap when the dock is narrow; the quiet text keeps its
    contrast in a dark theme."""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        QW = panel_tabs.QtWidgets
        cls.app = QW.QApplication.instance() or QW.QApplication([])

    def test_flow_row_wraps(self):
        QW = panel_tabs.QtWidgets
        row = panel_tabs.FlowRow([QW.QPushButton(t) for t in ("Use copy", "Roll back", "Approve", "Run")])
        one = row.heightForWidth(2000)
        self.assertGreater(row.heightForWidth(120), one * 2)
        self.assertLessEqual(row.minimumSizeHint().width(), 200)

    def test_muted_reads_on_dark(self):
        QW, QG = panel_tabs.QtWidgets, panel_tabs.QtGui
        lab = QW.QLabel("x")
        pal = lab.palette()
        pal.setColor(QG.QPalette.Window, QG.QColor(30, 30, 30))
        pal.setColor(QG.QPalette.WindowText, QG.QColor(230, 230, 230))
        lab.setPalette(pal)
        c = panel_tabs.muted(lab)
        lum = sum(int(c[i:i + 2], 16) for i in (1, 3, 5)) / 3
        self.assertGreater(lum, 120)  # well clear of the dark background
