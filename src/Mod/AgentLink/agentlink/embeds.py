# SPDX-License-Identifier: LGPL-2.1-or-later
# Parts encased mid-print: the layer grid a print setup lays down, where a
# pocket's top lands on it, the pauses a body needs (two on one layer made
# one), and the records kept for them. The pause comes after the layer whose
# top is the pocket's top: the next layer is the first printed over the part.
# Heights here are from the bed (the body's lowest point), mm. No FreeCAD.

import math

# Bought parts, the sizes a pocket is made from (mm). Typical figures; the
# part's own sheet is the authority.
BEARINGS = {
    # name: (outer diameter, width)
    "623": (10.0, 4.0), "624": (13.0, 5.0), "625": (16.0, 5.0), "626": (19.0, 6.0), "608": (22.0, 7.0),
    "688": (16.0, 5.0), "6000": (26.0, 8.0), "6001": (28.0, 8.0), "MR105": (10.0, 4.0), "MR85": (8.0, 2.5),
}
HEADROOM = 0.2  # over the part's top, so the nozzle clears it on the next layer
EPS = 1e-6


def grid(setup):
    """(first layer, layer height) from a print setup, mm."""
    lh = float((setup or {}).get("layerHeight", 0.2))
    first = float((setup or {}).get("firstLayer", lh))
    if lh <= 0 or first <= 0:
        raise ValueError("layerHeight and firstLayer: over 0 mm.")
    return first, lh


def layer_top(k, first, lh):
    """The top of layer k (1 the first), from the bed."""
    return round(first + (k - 1) * lh, 6)


def layer_at_or_above(z, first, lh):
    """The first layer whose top is at or over z: (k, its top)."""
    if z <= first + EPS:
        return 1, layer_top(1, first, lh)
    k = 1 + int(math.ceil((z - first) / lh - 1e-6))
    return k, layer_top(k, first, lh)


def pauses(records, first, lh):
    """One pause per layer: records on the same layer joined. Each record
    has layer, pauseZ and note. Sorted from the bed up."""
    by = {}
    for r in records:
        k = int(r["layer"])
        p = by.setdefault(k, {"layer": k, "z": layer_top(k, first, lh), "ids": [], "notes": []})
        p["ids"].append(r.get("id"))
        p["notes"].append(r["note"])
    out = []
    for k in sorted(by):
        p = by[k]
        seen = []
        for n in p["notes"]:
            if n not in seen:
                seen.append(n)
        note = "; ".join(n + (f" (x{p['notes'].count(n)})" if p["notes"].count(n) > 1 else "") for n in seen)
        out.append({"afterLayer": k, "z": round(p["z"], 3), "ids": [i for i in p["ids"] if i], "note": note, "merged": len(p["notes"]) > 1})
    return out


def next_id(records):
    n = 1 + max([int(str(r.get("id", "E0"))[1:] or 0) for r in records] or [0])
    return f"E{n}"
