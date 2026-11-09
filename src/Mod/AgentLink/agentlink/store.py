# SPDX-License-Identifier: LGPL-2.1-or-later
# What the agent records about the parts, kept in the document itself so it
# is saved with the .FCStd: how each part is made (setups), what it is made
# of, the limits it must meet (the spec), and the findings accepted on
# purpose (waivers). A document's Meta holds strings; each record is JSON
# under its own key.

import json

KEY = "AgentLink."


def read(doc, name, default):
    raw = (doc.Meta or {}).get(KEY + name)
    if not raw:
        return default
    try:
        return json.loads(raw)
    except ValueError:
        return default


def write(doc, name, value):
    m = dict(doc.Meta or {})
    m[KEY + name] = json.dumps(value, separators=(",", ":"))
    doc.Meta = m


# ---- setups: how each part is made --------------------------------------------------------
# One setup is one process and the bodies made by it, with that process's
# settings — as a setup is in Fusion's Manufacture workspace. A body is in
# one setup of a kind at most. Nothing chooses a process for a part: the
# person decides, and a setup records it.

TYPES = {
    "print": {
        "label": "Print",
        "settings": {
            "printer": "The printer profile: generic, bed-slinger, corexy, h2d.",
            "nozzle": "The nozzle, mm: 0.2, 0.4, 0.6 or 0.8.",
            "layerHeight": "The layer height, mm.",
            "firstLayer": "The first layer's height, mm.",
            "up": "Which way of the part stands up on the bed: +z as modelled (only +z in this build).",
            "walls": "Walls (perimeters).",
            "infill": "Infill, percent.",
        },
    },
    "cut": {"label": "Cut", "settings": {"machine": "laser, waterjet, plasma or router.", "thickness": "The sheet, mm.", "kerf": "The cut's width, mm.", "material": "The sheet's material, for the least feature.", "sheet": "The sheet's size, [width, height] mm."}},
    "mill": {"label": "Mill", "settings": {"axes": "\"3\" or \"3+2\".", "minTool": "The least end mill, mm.", "grade": "The ISO 286 IT grade, 5 to 14.", "directions": "Tool directions from the part to the spindle: \"+z\", \"-z\" or [x, y, z], one or more.", "material": "The stock's material.", "stock": "The stock's size [x, y, z], mm.", "reach": "How far the longest tool reaches below the top, mm."}},
}


def setups(doc):
    return read(doc, "setups", [])


def setup_of(doc, body, kind=None):
    for s in setups(doc):
        if body in s["bodies"] and (kind is None or s["type"] == kind):
            return s
    return None


def save_setups(doc, value):
    write(doc, "setups", value)


# ---- per-part records ---------------------------------------------------------------------

def spec(doc):
    """{ minWall, minClearance, massMin, massMax, minSafety } for every part."""
    return read(doc, "spec", {})


def materials(doc):
    return read(doc, "materials", {})


def waivers(doc):
    """[{ body, rule, at, reason }]: a finding of that rule at that place
    (to the millimetre and a half) comes back waived."""
    return read(doc, "waivers", [])
