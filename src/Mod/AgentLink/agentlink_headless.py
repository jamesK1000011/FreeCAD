# SPDX-License-Identifier: LGPL-2.1-or-later
# The headless server, started by path — by a Python with FreeCAD on it:
#   python agentlink_headless.py --out ~/Documents/AgentLink
# or by FreeCAD's own command line, flags by env:
#   AGENTLINK_OUT=~/Documents/AgentLink FreeCADCmd agentlink_headless.py
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from agentlink.server import main  # noqa: E402

code = main()
if __name__ == "__main__":
    sys.exit(code)
