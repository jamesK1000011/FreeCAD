# SPDX-License-Identifier: LGPL-2.1-or-later
# FILES lists the add-on's files, so the Claude extension's launcher can
# fetch them as plain files instead of asking GitHub's API (60 calls an
# hour). A file added or removed without FILES following fails here:
# regenerate it with  git ls-files | grep -v -E '(^|/)__pycache__/|^FILES$' | sort > FILES
import os
import subprocess
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Files(unittest.TestCase):
    def test_files_list_is_current(self):
        try:
            out = subprocess.run(["git", "ls-files"], cwd=HERE, capture_output=True, text=True, check=True).stdout
        except (OSError, subprocess.CalledProcessError):
            self.skipTest("not a git checkout")
        tracked = sorted(p for p in out.splitlines() if p != "FILES" and "__pycache__/" not in p)
        with open(os.path.join(HERE, "FILES"), encoding="utf-8") as f:
            listed = [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]
        self.assertEqual(listed, tracked, "FILES is stale: regenerate it (see the comment at the top of this test)")


if __name__ == "__main__":
    unittest.main()
