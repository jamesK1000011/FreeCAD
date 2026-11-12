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


if __name__ == "__main__":
    unittest.main()
