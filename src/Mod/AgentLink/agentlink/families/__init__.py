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
    "openscad",
    "robot",
]


# Families from other add-ons, found at run time: a module named here (or
# in AGENTLINK_EXTENSIONS, comma-separated) that imports gives FAMILIES, a
# list of modules keeping the same contract. Each keeps its own licence;
# nothing of theirs is part of this add-on.
EXTENSIONS = ["scanrulerfc.agentlink_families"]


def load():
    out = []
    for n in NAMES:
        try:
            out.append(importlib.import_module(f"{__name__}.{n}"))
        except ImportError as e:
            sys.stderr.write(f"Agent Link: the {n} tools are left out — {e}\n")
    extra = [x.strip() for x in os.environ.get("AGENTLINK_EXTENSIONS", "").split(",") if x.strip()]
    for n in EXTENSIONS + extra:
        try:
            mod = importlib.import_module(n)
        except ImportError:
            continue
        out.extend(getattr(mod, "FAMILIES", []))
    return out
