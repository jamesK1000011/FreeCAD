# SPDX-License-Identifier: LGPL-2.1-or-later
# The headless server, started by path — by a Python with FreeCAD on it:
#   python agentlink_headless.py --out ~/Documents/AgentLink
# or by FreeCAD's own command line, flags by env:
#   AGENTLINK_OUT=~/Documents/AgentLink FreeCADCmd agentlink_headless.py
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# The Claude extension's own launcher, mended where it cannot load (see
# agentlink/launcher_check.py): never in the way of serving.
try:
    from agentlink import launcher_check  # noqa: E402

    launcher_check.mend()
except Exception:
    pass

# Served through a supervisor that starts FreeCAD again if it dies under a
# call — when run by a plain Python (FreeCADCmd takes its arguments its own
# way) and not asked otherwise (AGENTLINK_SUPERVISE=0).
_plain = os.path.basename(sys.executable).lower().startswith("python")
if _plain and not os.environ.get("AGENTLINK_SUPERVISED") and os.environ.get("AGENTLINK_SUPERVISE", "1") != "0" and "--connect" not in sys.argv:
    from agentlink.supervise import run  # noqa: E402

    code = run(os.path.abspath(__file__), sys.argv[1:])
else:
    from agentlink.server import main  # noqa: E402

    code = main()
if __name__ == "__main__":
    sys.exit(code)
