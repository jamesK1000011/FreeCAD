# SPDX-License-Identifier: LGPL-2.1-or-later
# The panel's usage strip and its breakdown (agentlink/usage.py), apart from
# the credit line beside it.

import os
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from agentlink import usage  # noqa: E402


class Strip(unittest.TestCase):
    def test_strip_and_breakdown(self):
        today = time.strftime("%Y-%m-%d")
        r = {"days": 7, "calls": 12, "tokens": 310500, "toolListTokens": 40000, "imageTokens": 9000, "perDay": [{"day": "2000-01-01", "calls": 2, "tokens": 1000}, {"day": today, "calls": 10, "tokens": 41200}],
             "topTools": [{"tool": "view_render", "calls": 3, "tokens": 9000}], "tips": ["Pictures are over 40 %"], "note": "Estimates."}
        self.assertEqual(usage.strip(r), "Today 41.2k · week 310.5k tokens (est.)")
        t = usage.text(r)
        self.assertIn("view_render: 3 calls", t)
        self.assertNotIn("Stefan", t)  # the strip is apart from any credit

    def test_empty(self):
        self.assertEqual(usage.strip({"perDay": [], "tokens": 0}), "Today 0.0k · week 0.0k tokens (est.)")


if __name__ == "__main__":
    unittest.main()
