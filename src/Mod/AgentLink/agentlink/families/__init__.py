# SPDX-License-Identifier: LGPL-2.1-or-later
# The tool families beyond the first ones: each a module here with GROUP (its
# group's name), INFO (one line on what the group holds) and tools(session)
# giving its Tool list. A family whose FreeCAD module is missing in this
# build (FEM, TechDraw …) is left out, and says why in the server's log.

import importlib
import sys

# One line each, in the order they are listed.
NAMES = ["parts", "drawing", "electronics", "share"]


def load():
    out = []
    for n in NAMES:
        try:
            out.append(importlib.import_module(f"{__name__}.{n}"))
        except ImportError as e:
            sys.stderr.write(f"Agent Link: the {n} tools are left out — {e}\n")
    return out
