# SPDX-License-Identifier: LGPL-2.1-or-later
# Where the agent may read and write: credentials, hidden settings, scripts
# and places outside the home and session folders are refused.
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from agentlink import paths  # noqa: E402
from agentlink.protocol import ToolError  # noqa: E402


class T:
    def __init__(self, name):
        self.name = name


class Paths(unittest.TestCase):
    def setUp(self):
        self.out = tempfile.mkdtemp()

    def refused(self, tool, args):
        with self.assertRaises(ToolError):
            paths.guard(T(tool), args, self.out)

    def fine(self, tool, args):
        paths.guard(T(tool), args, self.out)

    def test_writes(self):
        self.fine("export", {"path": "part.step"})
        self.fine("export", {"path": os.path.join(self.out, "sub", "part.stl")})
        self.fine("cam_post", {"path": "~/Desktop/part.nc"})
        self.refused("export", {"path": "~/.zshrc"})
        self.refused("export", {"path": "~/.ssh/authorized_keys"})
        self.refused("export", {"path": "run.sh"})
        self.refused("doc_save", {"path": "/etc/hosts"})
        self.refused("export", {"path": "~/Library/LaunchAgents/x.plist"})
        self.refused("view_render", {"path": "~/.hidden/x.png"})
        self.refused("td_title", {"export": "~/.config/x.json"})
        self.refused("cam_library", {"action": "export", "path": "tools.py"})

    def test_reads(self):
        self.fine("import_file", {"path": "/Volumes/scans/part.stl"})
        self.fine("draft_text", {"font": "/System/Library/Fonts/Helvetica.ttc"})
        self.refused("sheet_import", {"path": "~/.ssh/id_rsa"})
        self.refused("import_file", {"path": "~/.aws/credentials"})

    def test_geometric_paths_pass(self):
        self.fine("part_sweep", {"path": "Helix"})
        self.fine("bim_frame", {"path": ".weird label"})


if __name__ == "__main__":
    unittest.main()
