# SPDX-License-Identifier: LGPL-2.1-or-later
# Parts encased mid-print: a pocket for a nut, magnet, bearing, insert,
# weight or any box or cylinder, sized with the printer's fit calibration,
# its top on the print setup's layer grid and a roof over it; the print
# pauses after the layer at the pocket's top, the part goes in, and the
# next layers close over it. The records are kept in the session's
# meta[label]["embeds"] (what slicing reads for its pauses) and in the
# document, so they are saved with it.

import math

import FreeCAD
import Part

from .. import embeds as E
from .. import fits_hw, printability, store
from .. import geometry as geo
from ..protocol import Tool, ToolError

GROUP = "embed"
INFO = "Parts encased mid-print: nut, magnet, bearing, insert and weight pockets with a roof, and the pauses to drop them in."

V = FreeCAD.Vector
KINDS = ("nut", "magnet", "bearing", "insert", "weight", "box", "cylinder")


def obj(props, required=()):
    return {"type": "object", "properties": props, "required": list(required), "additionalProperties": False}


# ---- the records --------------------------------------------------------------------------

def records(session, label):
    m = session.meta.setdefault(label, {})
    if "embeds" not in m:
        m["embeds"] = list(store.read(session.doc, "embeds", {}).get(label, []))
    return m["embeds"]


def keep(session, label, recs):
    session.meta.setdefault(label, {})["embeds"] = recs
    allr = store.read(session.doc, "embeds", {})
    if recs:
        allr[label] = recs
    else:
        allr.pop(label, None)
    store.write(session.doc, "embeds", allr)


def live(session, o):
    """The records whose pocket is still open in the body: one an undo
    filled is dropped. (records, ids dropped)"""
    recs = records(session, o.Label)
    solid = o.Shape
    ok, gone = [], []
    for r in recs:
        c = V(*r["centre"])
        if solid.isInside(c, 1e-6, True):
            gone.append(r["id"])
        else:
            ok.append(r)
    if gone:
        keep(session, o.Label, ok)
    return ok, gone


# ---- what goes in, and its pocket ---------------------------------------------------------

def _dims(text, n, what):
    try:
        v = [float(x) for x in str(text).lower().replace("×", "x").split("x")]
    except ValueError:
        v = []
    if len(v) != n or min(v) <= 0:
        raise ToolError("invalid_input", f"size: {what}.")
    return v


def item(a):
    """(shape: cyl|hex|box, nominal sizes, height, what) for the thing put in."""
    kind, size, dims = a.get("kind"), a.get("size"), a.get("dims")
    if kind not in KINDS:
        raise ToolError("invalid_input", f"kind: one of {', '.join(KINDS)}.")
    if kind == "nut":
        s = size or "M3"
        if s not in fits_hw.M:
            raise ToolError("invalid_input", f"size: a nut {', '.join(fits_hw.M)}.")
        af, m = fits_hw.M[s]["nut"]
        return "hex", [af], m, f"{s} hex nut"
    if kind == "magnet":
        d, t = _dims(size or "6x2", 2, "a magnet as diameter x thickness, mm — \"6x2\"")
        return "cyl", [d], t, f"{size or '6x2'} magnet"
    if kind == "bearing":
        s = str(size or "608").upper()
        if s in E.BEARINGS:
            d, w = E.BEARINGS[s]
        else:
            d, w = _dims(s, 2, f"a bearing {', '.join(E.BEARINGS)}, or outer diameter x width — \"22x7\"")
        return "cyl", [d], w, f"{s} bearing"
    if kind == "insert":
        s = size or "M3"
        if s not in fits_hw.M:
            raise ToolError("invalid_input", f"size: an insert {', '.join(fits_hw.M)}.")
        d, h = fits_hw.M[s]["insert"]
        return "cyl", [d], h, f"{s} insert"
    if kind == "weight":
        if dims:
            v = [float(x) for x in dims]
        else:
            v = _dims(size or "", len(str(size or "").lower().split("x")), "a weight as diameter x height (\"20x8\") or w x d x h (\"30x20x6\"), mm")
        if len(v) == 2:
            return "cyl", [v[0]], v[1], f"{v[0]:g}x{v[1]:g} weight"
        if len(v) == 3:
            return "box", v[:2], v[2], f"{v[0]:g}x{v[1]:g}x{v[2]:g} weight"
        raise ToolError("invalid_input", "size: a weight as diameter x height or w x d x h, mm.")
    v = [float(x) for x in (dims or [])]
    if kind == "box":
        if len(v) != 3 or min(v) <= 0:
            raise ToolError("invalid_input", "dims: [w, d, h] mm for a box.")
        return "box", v[:2], v[2], f"{v[0]:g}x{v[1]:g}x{v[2]:g} part"
    if len(v) != 2 or min(v) <= 0:
        raise ToolError("invalid_input", "dims: [diameter, h] mm for a cylinder.")
    return "cyl", [v[0]], v[1], f"Ø{v[0]:g}x{v[1]:g} part"


def pocket_solid(shape, sizes, x, y, z0, z1, turn):
    h = z1 - z0
    rot = FreeCAD.Rotation(V(0, 0, 1), turn)
    if shape == "cyl":
        return Part.makeCylinder(sizes[0] / 2, h, V(x, y, z0))
    if shape == "hex":
        r = sizes[0] / math.sqrt(3)
        pts = [V(r * math.cos(math.radians(30 + 60 * i)), r * math.sin(math.radians(30 + 60 * i)), 0) for i in range(6)]
        s = Part.Face(Part.makePolygon(pts + [pts[0]])).extrude(V(0, 0, h))
    else:
        w, d = sizes
        s = Part.makeBox(w, d, h, V(-w / 2, -d / 2, 0))
    s.Placement = FreeCAD.Placement(V(x, y, z0), rot)
    return s


def footprint(shape, sizes, x, y, turn):
    """Points on the pocket's top, a hair inside its edge, and its centre."""
    if shape == "cyl":
        r = sizes[0] / 2 * 0.97
        pts = [(r * math.cos(math.radians(45 * i)), r * math.sin(math.radians(45 * i))) for i in range(8)]
    elif shape == "hex":
        r = sizes[0] / math.sqrt(3) * 0.97
        pts = [(r * math.cos(math.radians(30 + 60 * i)), r * math.sin(math.radians(30 + 60 * i))) for i in range(6)]
    else:
        w, d = sizes[0] / 2 * 0.97, sizes[1] / 2 * 0.97
        pts = [(sx * w, sy * d) for sx in (-1, 1) for sy in (-1, 1)] + [(0, d), (0, -d), (w, 0), (-w, 0)]
    c, s = math.cos(math.radians(turn)), math.sin(math.radians(turn))
    return [(x, y)] + [(x + px * c - py * s, y + px * s + py * c) for px, py in pts]


def roof_over(solid, pts, z):
    """The least material straight above each point from height z, or None
    at a point with no material just over z (the pocket opens there)."""
    starts = []
    for (px, py) in pts:
        if not solid.isInside(V(px, py, z + 0.01), 1e-6, True):
            return None, (px, py)
        starts.append((V(px, py, z + 0.005), V(0, 0, -1)))
    hits = geo._ray_hits(solid, starts, solid.BoundBox.ZLength + 1)
    if any(h is None for h in hits):
        return None, None
    return round(min(hits) + 0.005, 3), None


# ---- the tools ----------------------------------------------------------------------------

def tools(session):
    s = session

    def print_setup(o):
        setup = store.setup_of(s.doc, o.Label, "print")
        if not setup:
            raise ToolError("invalid_state", f"{o.Label} is in no print setup: a part is encased between layers — setup add {{ type: \"print\", bodies: [\"{o.Label}\"], settings: {{ layerHeight }} }} once the person says it is printed.")
        return setup

    def cal(o):
        if hasattr(s, "_cal"):
            return s._cal(o.Label)
        return dict(fits_hw.DEFAULT_CAL), "none — the typical printer assumed"

    def hw_embed(a):
        o = s.body(a.get("body"))
        setup = print_setup(o)
        try:
            first, lh = E.grid(setup)
        except ValueError as e:
            raise ToolError("invalid_input", f"the print setup's {e}")
        shape, nominal, h, what = item(a)
        at = a.get("at")
        if not (isinstance(at, list) and len(at) == 3):
            raise ToolError("invalid_input", "at: [x, y, z] mm — the centre of the pocket's floor, inside the part.")
        fit = a.get("fit", "slide")
        if fit not in fits_hw.FITS:
            raise ToolError("invalid_input", f"fit: one of {', '.join(fits_hw.FITS)} (slide drops in by hand).")
        c, said = cal(o)
        sizes = [round(fits_hw.size_fit(fit, n, "hole", c), 3) for n in nominal]
        turn = float(a.get("turn", 0))
        head = float(a.get("headroom", E.HEADROOM))
        solid = o.Shape
        bed = solid.BoundBox.ZMin
        x, y, z0 = (float(v) for v in at)
        layer, top_rel = E.layer_at_or_above(z0 - bed + h + head, first, lh)
        z1 = bed + top_rel
        pocket = pocket_solid(shape, sizes, x, y, z0, z1, turn)
        rules = printability.rules_for(setup.get("nozzle", "0.4"), setup.get("printer"))
        least_roof = max(float(a.get("minRoof", 0)), 2 * lh)
        inside = pocket.common(solid).Volume
        if inside < pocket.Volume - 1e-3:
            out_by = pocket.Volume - inside
            raise ToolError("invalid_input", f"The pocket for the {what} ({' x '.join(f'{v:g}' for v in sizes)} mm, z {z0 - bed:.2f} to {top_rel:.2f} from the bed) comes out of {o.Label} ({out_by:.1f} mm³ outside): it would open the part — move at into thicker material, or make the part bigger there.")
        roof, open_at = roof_over(solid, footprint(shape, sizes, x, y, turn), z1)
        if roof is None:
            where = f" at [{open_at[0]:.1f}, {open_at[1]:.1f}]" if open_at else ""
            raise ToolError("invalid_input", f"No material over the pocket's top at z {top_rel:.2f}{where}: the {what} would be left open, not encased — lower at's z, or use hw_hole for a pocket pressed in after.")
        if roof < least_roof - 1e-6:
            raise ToolError("invalid_input", f"The roof over the {what} is {roof:.2f} mm, under the least {least_roof:.2f} mm ({int(round(least_roof / lh))} layers of {lh} mm): lower at's z by {least_roof - roof:.2f} mm or more, or thicken the part above it.")
        shell = Part.Compound(solid.Faces)
        # The walls beside and under it: the pocket cut short of its top by
        # the least wall, so the roof is not read as a wall.
        low = pocket_solid(shape, sizes, x, y, z0, max(z0 + 0.01, z1 - rules["minWall"]), turn)
        sides = low.distToShape(shell)[0]
        cut = geo.clean(solid.cut(pocket))
        recs = records(s, o.Label)
        rid = E.next_id(recs)
        note = a.get("note") or f"Insert the {what}"
        rec = {
            "id": rid, "kind": a["kind"], "what": what, "shape": shape, "nominal": nominal, "pocket": sizes, "height": round(h, 3),
            "at": [x, y, z0], "turn": turn, "fit": fit, "floor": round(z0 - bed, 3), "top": round(top_rel, 3),
            "pauseZ": round(top_rel, 3), "layer": layer, "roof": roof, "note": note,
            "centre": [x, y, round((z0 + z1) / 2, 4)],
        }
        o.Shape = cut
        s.doc.recompute()
        keep(s, o.Label, recs + [rec])
        s.snapshot("hw_embed")
        out = {
            "body": o.Label, "id": rid, "what": what, "pocket": {"shape": {"cyl": "round", "hex": "hex", "box": "box"}[shape], "size": sizes, "depth": round(z1 - z0, 3), "floor": rec["floor"], "top": rec["top"]},
            "pause": {"afterLayer": layer, "z": rec["pauseZ"], "note": note}, "roof": roof, "least": {"roof": round(least_roof, 3), "wall": rules["minWall"]},
            "sides": round(sides, 3), "printerFit": said, "step": len(s.steps),
        }
        if sides < rules["minWall"]:
            out["warning"] = f"The wall beside or under the pocket is {sides:.2f} mm, under {rules['minWall']} mm: it may break through — move the pocket in."
        return out

    def hw_embed_list(a):
        o = s.body(a.get("body"))
        recs, gone = live(s, o)
        out = {"body": o.Label, "embeds": [{k: r[k] for k in ("id", "what", "pocket", "floor", "top", "layer", "roof", "note")} for r in recs]}
        if gone:
            out["dropped"] = {"ids": gone, "why": "their pockets are filled now (undone)"}
        return out

    def hw_unembed(a):
        o = s.body(a.get("body"))
        recs, _ = live(s, o)
        rid = a.get("id")
        r = next((x for x in recs if x["id"] == rid), None)
        if r is None:
            raise ToolError("not_found", f"id {rid!r}: not an embed in {o.Label} — they are: {', '.join(x['id'] for x in recs) or 'none'} (hw_embed_list).")
        rest = [x for x in recs if x is not r]
        if a.get("keepPocket"):
            keep(s, o.Label, rest)
            return {"body": o.Label, "removed": rid, "pocket": "left open: it is a hole now, the part pressed in after printing"}
        x, y, z0 = r["at"]
        bed = o.Shape.BoundBox.ZMin
        fill = pocket_solid(r["shape"], r["pocket"], x, y, z0, bed + r["top"], r["turn"])
        o.Shape = geo.clean(o.Shape.fuse(fill))
        s.doc.recompute()
        keep(s, o.Label, rest)
        s.snapshot("hw_unembed")
        return {"body": o.Label, "removed": rid, "pocket": "filled", "solids": len(o.Shape.Solids), "step": len(s.steps)}

    def hw_pauses(a):
        o = s.body(a.get("body"))
        setup = print_setup(o)
        first, lh = E.grid(setup)
        recs, gone = live(s, o)
        all_ = [{"id": r["id"], "layer": r["layer"], "note": r["note"]} for r in recs]
        enc = (s.meta.get(o.Label) or {}).get("encased")
        if enc:
            k, _ = E.layer_at_or_above(enc["top"], first, lh)
            all_.append({"id": "gen_make", "layer": k, "note": f"Insert {enc['count']} {enc['what']}"})
        ps = E.pauses(all_, first, lh)
        out = {"body": o.Label, "layerHeight": lh, "firstLayer": first, "pauses": ps, "count": len(ps)}
        if gone:
            out["dropped"] = gone
        if not ps:
            out["note"] = "No part is encased in this body: no pause."
        else:
            out["note"] = "Each pause comes after its layer: the print stops with the pocket open, the part goes in, the next layer closes over it. slice_with writes them in."
        return out

    body = {"type": "string", "description": "A body's name; may be left out when there is one."}
    return [
        Tool(
            "hw_embed",
            "Encase a bought part mid-print: cut a pocket for kind nut (size M2…M8), magnet (\"6x2\"), bearing (608, 625 … or \"22x7\"), insert (M2…M8), weight (\"20x8\" or \"30x20x6\"), box (dims [w,d,h]) or cylinder (dims [d,h]), standing upright, its floor's centre at [x,y,z], turn ° about z. Sized with the printer's fit calibration (fit slide by default) and a headroom of 0.2 mm; its top is rounded up to the body's print setup layer grid and the pause comes after that layer. Refused when the pocket would open the part or the roof over it is under 2 layers (minRoof raises it). Trap: needs a print setup; the part goes in from above, so nothing may overhang the pocket.",
            obj({"body": body, "kind": {"type": "string", "enum": list(KINDS)}, "size": {"type": "string"}, "dims": {"type": "array", "items": {"type": "number"}}, "at": {"type": "array", "items": {"type": "number"}}, "turn": {"type": "number"}, "fit": {"type": "string", "enum": list(fits_hw.FITS)}, "headroom": {"type": "number"}, "minRoof": {"type": "number"}, "note": {"type": "string"}}, ["kind", "at"]),
            hw_embed,
            changes=True,
        ),
        Tool("hw_embed_list", "The parts encased in a body: each pocket's size, floor and top from the bed, the layer it pauses after, its roof. Pockets an undo filled are dropped.", obj({"body": body}), hw_embed_list),
        Tool("hw_unembed", "Take an encased part out by id: its pocket filled again, or keepPocket: true leaves it open as a hole (pressed in after printing) and drops only the pause.", obj({"body": body, "id": {"type": "string"}, "keepPocket": {"type": "boolean"}}, ["id"]), hw_unembed, changes=True),
        Tool("hw_pauses", "Every pause a body's print needs: layer numbers and heights from the bed, what goes in at each, two on one layer made one; gen_make's magnets included. The layer grid is the body's print setup's.", obj({"body": body}), hw_pauses),
    ]
