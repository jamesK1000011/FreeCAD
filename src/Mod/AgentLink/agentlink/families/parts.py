# SPDX-License-Identifier: LGPL-2.1-or-later
# More standard parts made to size, beside gen_make's bins: Gridfinity
# baseplates, project boxes with their lids, enclosures round a board.

from .. import boxes
from .. import geometry as geo
from .. import store
from ..protocol import Tool, ToolError

GROUP = "parts"
INFO = "Standard parts made to size: Gridfinity bins (gen_make), baseplates, project boxes and board enclosures with lids (gen_part)."

GENERATORS = {
    "gridfinity_baseplate": boxes.BASEPLATE,
    "project_box": boxes.PROJECT_BOX,
    "board_box": boxes.BOARD_BOX,
}


def calibration(doc):
    """The printer's fit calibration kept with the document (fit
    calibrate), else the typical printer's: holes print 0.2 mm small."""
    c = store.read(doc, "fit", None)
    if c:
        return {"hole": float(c.get("hole", 0.2)), "shaft": float(c.get("shaft", 0.0))}, "calibrated"
    return {"hole": 0.2, "shaft": 0.0}, "none — the typical printer assumed (holes print 0.2 mm small)"


def tight_box(shape):
    """The shape's extent, mm, tight: the plain bounding box runs a little
    loose round lofted faces."""
    try:
        b = shape.optimalBoundingBox(True, False)
    except Exception:  # an older FreeCAD without it: the plain box
        b = shape.BoundBox
    r = geo.r3
    return {"min": [r(b.XMin), r(b.YMin), r(b.ZMin)], "max": [r(b.XMax), r(b.YMax), r(b.ZMax)], "size": [r(b.XLength), r(b.YLength), r(b.ZLength)]}


def build(gen, given, cal):
    """[(name, shape)] and what was made, for a generator and its params."""
    p = boxes.check(GENERATORS[gen], given)
    if gen == "gridfinity_baseplate":
        shape, info = boxes.baseplate_shape(p, cal)
        return p, [(f"Gridfinity baseplate {p['x']}x{p['y']}", shape)], info
    if gen == "project_box":
        box, lid, info = boxes.project_box(p, cal)
        n = f"Project box {p['length']:g}x{p['width']:g}x{p['height']:g}"
        return p, [(n, box), (n + " lid", lid)], info
    box, lid, info = boxes.board_box(p, cal)
    return p, [("Board box", box), ("Board box lid", lid)], info


def add_bodies(session, made, base_name=None, placement=None):
    """Each shape as its own body; the labels given back."""
    labels = []
    for i, (name, shape) in enumerate(made):
        label = session.unique(base_name + ("" if i == 0 else " lid") if base_name else name)
        o = session.doc.addObject("Part::Feature", "Body")
        o.Label = label
        if placement is not None:
            shape = shape.copy()
            shape.translate(placement)
        o.Shape = shape
        labels.append(label)
    session.doc.recompute()
    return labels


def tools(session):
    s = session

    def gen_part(a):
        gen = a.get("generator")
        if gen not in GENERATORS:
            raise ToolError("invalid_input", f"generator: one of {', '.join(GENERATORS)} (gridfinity_bin is gen_make's).")
        cal, said = calibration(s.doc)
        p, made, info = build(gen, a.get("params") or {}, cal)
        parts = [{"name": n, "volume": round(sh.Volume, 1), **tight_box(sh)} for (n, sh) in made]
        out = {"generator": gen, "values": p, "parts": parts, **info, "printerFit": said}
        if a.get("measure"):
            return {**out, "made": False}
        labels = add_bodies(s, made, a.get("name"))
        for label, (_, sh) in zip(labels, made):
            s.meta[label] = {"generator": gen, "values": p}
        if info.get("magnets"):
            s.meta[labels[0]]["fitted"] = info["magnets"]
        s.snapshot("gen_part")
        sound = {lb: geo.soundness(sh)["ok"] for lb, (_, sh) in zip(labels, made)}
        return {**out, "bodies": labels, "sound": sound, "step": len(s.steps)}

    desc = (
        "Make a standard part to size, each piece its own body: gridfinity_baseplate (42 mm pockets, 4.65 mm profile; style open, "
        "or magnets: a floor with four 6x2 magnet holes per pocket, pressed in from above), project_box (box and its lid laid beside it, "
        "top down; the lid's lip at a clearance; bosses: corner screw bosses with pilot holes and clearance holes in the lid), board_box "
        "(an enclosure round a board: standoffs under its holes, openings for edge connectors, a lid; for a KiCad board, board_enclosure "
        "reads all that from the import). Holes take the printer's fit calibration (fit calibrate); without one, holes are made 0.2 mm big. "
        "measure: true builds it and gives the sizes without adding it. Params — "
        + " | ".join(f"{g}: " + ", ".join(f"{k} ({v.get('default')})" for k, v in t.items()) for g, t in GENERATORS.items())
        + " (mm and counts; each refused value is answered with its range)."
    )
    return [
        Tool(
            "gen_part",
            desc,
            {
                "type": "object",
                "properties": {
                    "generator": {"type": "string", "enum": list(GENERATORS)},
                    "params": {"type": "object"},
                    "name": {"type": "string", "description": "The body's name; a lid gets ' lid' after it."},
                    "measure": {"type": "boolean"},
                },
                "required": ["generator"],
                "additionalProperties": False,
            },
            gen_part,
            changes=True,
        )
    ]
