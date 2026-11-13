# SPDX-License-Identifier: LGPL-2.1-or-later
# Agent Link (LGPL) never imports another add-on by name — ScanRulerFC
# (AGPL) least of all: extensions are found on the path by convention
# (a package holding agentlink_families) and register themselves.
import os
import re
import unittest

HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "agentlink")
PAT = re.compile(r"""(import\s+scanrulerfc|from\s+scanrulerfc|import_module\(\s*["']scanrulerfc|["']scanrulerfc\.)""")


class LicenceBoundary(unittest.TestCase):
    def test_no_import_of_scanrulerfc(self):
        hits = []
        for root, _dirs, files in os.walk(HERE):
            for f in files:
                if f.endswith(".py"):
                    p = os.path.join(root, f)
                    with open(p, encoding="utf-8") as fh:
                        for i, line in enumerate(fh, 1):
                            if PAT.search(line.split("#", 1)[0]):
                                hits.append(f"{os.path.relpath(p, HERE)}:{i}")
        self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()
