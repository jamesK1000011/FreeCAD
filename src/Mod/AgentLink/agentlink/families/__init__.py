# SPDX-License-Identifier: LGPL-2.1-or-later
# The tool families beyond the first ones: each a module here with GROUP (its
# group's name), INFO (one line on what the group holds) and tools(session)
# giving its Tool list. A family whose FreeCAD module is missing in this
# build (FEM, TechDraw …) is left out, and says why in the server's log.

import importlib
import os
import sys

# One line each, in the order they are listed.
NAMES = [
    "doc",
    "model",
    "model_more",
    "part_more",
    "measure",
    "sketch",
    "sketch_more",
    "feature",
    "feature_more",
    "param",
    "sheet",
    "material",
    "mass",
    "strength",
    "thread",
    "cut",
    "mill",
    "embed",
    "printer",
    "slicing",
    "parts",
    "drawing",
    "techdraw",
    "inspection",
    "electronics",
    "share",
    "view",
    "notes",
    "app",
    "meshobj",
    "assembly",
    "assembly_more",
    "cam",
    "draft",
    "draft_more",
    "surface",
    "points",
    "bim",
    "bim_more",
    "ifc",
    "code",
    "openscad",
    "robot",
    "std_more",
    "cam_more",
    "bim_gaps",
    "fem_more",
    "unattended",
]


# Families from other add-ons, found at run time and never named here: any
# package on the path holding an agentlink_families module (FreeCAD puts
# each Mod folder on the path), and modules named in AGENTLINK_EXTENSIONS
# (comma-separated). Its FAMILIES keep the same contract. Each keeps its
# own licence; nothing of theirs is part of this add-on, and this add-on
# imports none of them by name.


def _discovered():
    found = []
    for d in list(sys.path):
        if not d or not os.path.isdir(d):
            continue
        try:
            names = os.listdir(d)
        except OSError:
            continue
        for pkg in names:
            if pkg.startswith((".", "_")) or not pkg.isidentifier():
                continue
            if os.path.isfile(os.path.join(d, pkg, "agentlink_families.py")) and os.path.isfile(os.path.join(d, pkg, "__init__.py")):
                n = f"{pkg}.agentlink_families"
                if n not in found:
                    found.append(n)
    return found


def load():
    out = []
    for n in NAMES:
        try:
            out.append(importlib.import_module(f"{__name__}.{n}"))
        except ImportError as e:
            sys.stderr.write(f"Agent Link: the {n} tools are left out — {e}\n")
    extra = [x.strip() for x in os.environ.get("AGENTLINK_EXTENSIONS", "").split(",") if x.strip()]
    seen = set()
    for n in _discovered() + extra:
        if n in seen:
            continue
        seen.add(n)
        # An add-on that fails to load (an older or newer Agent Link than it
        # expects) is left out and said, not allowed to stop this one.
        try:
            mod = importlib.import_module(n)
            fams = list(getattr(mod, "FAMILIES", []))
        except Exception as e:
            sys.stderr.write(f"Agent Link: extension {n} left out — {type(e).__name__}: {e}\n")
            continue
        for f in fams:
            f.EXTERNAL = True
        out.extend(fams)
    return out
