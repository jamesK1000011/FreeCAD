# SPDX-License-Identifier: LGPL-2.1-or-later
# What the agent records about the parts, kept in the document itself so it
# is saved with the .FCStd: how each part is made (setups), what it is made
# of, the limits it must meet (the spec), and the findings accepted on
# purpose (waivers). Each record is JSON under its own name.
#
# The records live in one hidden object, AgentLinkRecords (a string property
# a record), so writing one happens inside the tool's undo step and undo
# takes it back with the rest (unattended use, B). Files from before kept
# them in the document's Meta, which is outside undo: those are read from
# there until the first write copies them over. Meta is left as it was — the
# state before the records object, which is what undoing its creation goes
# back to.

import json
import re

KEY = "AgentLink."
HOLDER = "AgentLinkRecords"


def _prop(name):
    return "R_" + re.sub(r"\W", "_", name)


def _objects(doc):
    # A document without objects of its own (a test double): Meta only.
    return hasattr(doc, "getObject") and hasattr(doc, "addObject")


def holder(doc, make=False):
    if not _objects(doc):
        return None
    o = doc.getObject(HOLDER)
    if o is None and make:
        o = doc.addObject("App::FeaturePython", HOLDER)
        o.Label = "Agent Link records"
        try:
            o.setPropertyStatus("Label", "ReadOnly")
        except Exception:
            pass
        try:
            o.Visibility = False
        except Exception:
            pass
        vo = getattr(o, "ViewObject", None)
        if vo is not None and hasattr(vo, "ShowInTree"):
            vo.ShowInTree = False
        for k, v in (doc.Meta or {}).items():
            if k.startswith(KEY):
                _set(o, k[len(KEY):], v)
    return o


def _set(o, name, raw):
    p = _prop(name)
    if p not in o.PropertiesList:
        o.addProperty("App::PropertyString", p, "AgentLink", f"Agent Link record: {name} (JSON)")
        try:
            o.setEditorMode(p, 2)
        except Exception:
            pass
    setattr(o, p, raw)


def _raw(doc, name):
    o = doc.getObject(HOLDER) if _objects(doc) else None
    if o is not None:
        p = _prop(name)
        if p in o.PropertiesList:
            return getattr(o, p)
        return None
    return (doc.Meta or {}).get(KEY + name)


def read(doc, name, default):
    raw = _raw(doc, name)
    if not raw:
        return default
    try:
        return json.loads(raw)
    except ValueError:
        return default


def write(doc, name, value):
    raw = json.dumps(value, separators=(",", ":"))
    o = holder(doc, make=True)
    if o is None:
        m = dict(doc.Meta or {})
        m[KEY + name] = raw
        doc.Meta = m
        return
    _set(o, name, raw)


def remove(doc, name):
    if not _objects(doc):
        m = dict(doc.Meta or {})
        m.pop(KEY + name, None)
        doc.Meta = m
        return
    o = holder(doc, make=(KEY + name) in (doc.Meta or {}))
    if o is not None and _prop(name) in o.PropertiesList:
        setattr(o, _prop(name), "")


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
            "topLayers": "Solid layers on top (5 to start).",
            "bottomLayers": "Solid layers at the bottom (4 to start).",
            "slicerPresets": "The person's own slicer presets, set by slicer_presets use: { slicer, machine, process, filament } or { bundle }.",
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
