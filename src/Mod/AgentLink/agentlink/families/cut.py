# SPDX-License-Identifier: LGPL-2.1-or-later
# Parts cut from sheet (laser, waterjet, plasma, router): is the body a
# sheet part — two parallel flat faces a constant thickness apart and every
# other face square to them — and can the setup's machine cut its profile:
# webs, slots and holes against the least feature for the machine, the
# material and the thickness; a router's inside corners against its
# cutter; plasma's holes against the thickness; the part against the
# sheet. Then the flat profiles, laid out in rows on the sheet, as DXF and
# SVG. The least features are rules of thumb, said as assumed: the cutting
# shop's own figures win.

import math
import os

import FreeCAD
import Part

from .. import sheetfile as sf
from .. import store
from ..protocol import Tool, ToolError

GROUP = "cut"
INFO = "Sheet parts for laser, waterjet, plasma and router: the cut check, and DXF/SVG profiles laid out on the sheet."

V = FreeCAD.Vector
MACHINES = ("laser", "waterjet", "plasma", "router")
METALS = ("steel", "stainless", "aluminium", "aluminum", "brass", "copper", "titanium")
OTHERS = ("acrylic", "plywood", "wood", "mdf", "hdpe", "polycarbonate", "cardboard", "rubber", "foam", "carbon")
KERF = {"laser": 0.2, "waterjet": 0.9, "plasma": 1.5, "router": 3.175}
MAX_EDGES = 160


def obj(props, required=()):
    return {"type": "object", "properties": props, "required": list(required), "additionalProperties": False}


def rules(machine, material, t, kerf):
    """The least hole, web (material between cuts) and slot (gap cut out),
    and the hard floor under which it cannot be cut at all. Rules of thumb."""
    metal = material in METALS
    if machine == "laser":
        r = {"hole": max(0.5, t if metal else 0.5 * t), "web": max(0.5, t if metal else 0.5 * t), "slot": max(2 * kerf, 0.5 * t if metal else 0.0)}
        floor = {"hole": 2 * kerf, "slot": kerf}
    elif machine == "waterjet":
        r = {"hole": max(2 * kerf, 0.5 * t), "web": max(2 * kerf, 0.5 * t), "slot": max(1.5 * kerf, 0.0)}
        floor = {"hole": 1.5 * kerf, "slot": kerf}
    elif machine == "plasma":
        r = {"hole": max(1.5 * t, 3.0), "web": max(2 * kerf, t), "slot": max(2 * kerf, t)}
        floor = {"hole": t, "slot": kerf}
    else:  # router: the kerf is the cutter's diameter
        r = {"hole": kerf, "web": max(1.0 if metal else 1.5, 0.5 * t), "slot": kerf, "corner": kerf / 2}
        floor = {"hole": kerf, "slot": kerf}
    r = {k: round(v, 3) for k, v in r.items()}
    return r, {k: round(v, 3) for k, v in floor.items()}


# ---- reading a body as a sheet ------------------------------------------------------------

def _plane_normal(f):
    u0, u1, v0, v1 = f.ParameterRange
    n = f.normalAt((u0 + u1) / 2, (v0 + v1) / 2)
    n.normalize()
    return n


def _normals(f, k=3):
    u0, u1, v0, v1 = f.ParameterRange
    out = []
    for i in range(k):
        for j in range(k):
            u = u0 + (u1 - u0) * (i + 0.5) / k
            v = v0 + (v1 - v0) * (j + 0.5) / k
            try:
                n = f.normalAt(u, v)
            except Exception:
                continue
            if n.Length > 1e-9:
                n.normalize()
                out.append(n)
    return out


def sheet_of(shape):
    """{ sheet, thickness, normal, notSquare: [Face…], steps: [Face…] }."""
    planar = [(i, f) for i, f in enumerate(shape.Faces) if type(f.Surface).__name__ == "Plane"]
    if not planar:
        return {"sheet": False, "why": "no flat face to lie it on"}
    i0, big = max(planar, key=lambda x: x[1].Area)
    n = _plane_normal(big)
    offsets = {}
    not_square = []
    for i, f in enumerate(shape.Faces):
        ns = _normals(f)
        if type(f.Surface).__name__ == "Plane" and ns and abs(abs(ns[0].dot(n)) - 1) < 1e-6:
            d = round(f.CenterOfMass.dot(n), 4)
            key = next((k for k in offsets if abs(k - d) < 1e-3), d)
            offsets.setdefault(key, []).append(f"Face{i + 1}")
            continue
        if any(abs(m.dot(n)) > 1e-3 for m in ns):
            not_square.append(f"Face{i + 1}")
    ds = sorted(offsets)
    steps = []
    if len(ds) > 2:
        for d in ds[1:-1]:
            steps += offsets[d]
    t = ds[-1] - ds[0] if len(ds) >= 2 else 0.0
    ok = len(ds) == 2 and not not_square
    return {"sheet": ok, "thickness": round(t, 4), "normal": [round(n.x, 6), round(n.y, 6), round(n.z, 6)], "low": ds[0], "notSquare": not_square, "steps": steps}


def flatten(shape, info):
    """The body turned to lie flat, its low face on z = 0, its lower left at
    the origin; and its profile at mid-thickness: (flat solid, outer wire,
    hole wires)."""
    n = V(*info["normal"])
    rot = FreeCAD.Rotation(n, V(0, 0, 1))
    flat = shape.copy()
    flat.transformShape(FreeCAD.Placement(V(0, 0, 0), rot).toMatrix())
    bb = flat.BoundBox
    flat.translate(V(-bb.XMin, -bb.YMin, -bb.ZMin))
    t = info["thickness"]
    wires = flat.slice(V(0, 0, 1), t / 2)
    if not wires:
        raise ToolError("failed", "The profile could not be read at mid-thickness.")
    wires = sorted(wires, key=lambda w: -w.BoundBox.DiagonalLength)
    return flat, wires[0], wires[1:]


def _ang(c, p):
    return math.degrees(math.atan2(p.y - c.y, p.x - c.x))


def primitives(wire, layer):
    out = []
    for e in wire.Edges:
        cname = type(e.Curve).__name__
        if cname in ("Line", "LineSegment"):
            a, b = e.Vertexes[0].Point, e.Vertexes[-1].Point
            out.append((layer, ("line", a.x, a.y, b.x, b.y)))
        elif cname == "Circle":
            c, r = e.Curve.Center, e.Curve.Radius
            if e.isClosed():
                out.append((layer, ("circle", c.x, c.y, r)))
            else:
                pa, pb = e.valueAt(e.FirstParameter), e.valueAt(e.LastParameter)
                if e.Curve.Axis.z > 0:
                    out.append((layer, ("arc", c.x, c.y, r, _ang(c, pa), _ang(c, pb))))
                else:
                    out.append((layer, ("arc", c.x, c.y, r, _ang(c, pb), _ang(c, pa))))
        else:
            pts = e.discretize(Deflection=0.01)
            out.append((layer, ("poly", [(p.x, p.y) for p in pts], e.isClosed())))
    return out


# ---- the checks on the profile ------------------------------------------------------------

def _tangent(e, p):
    try:
        t = e.tangentAt(e.Curve.parameter(p))
        t.normalize()
        return t
    except Exception:
        return None


def _in(flat, t, x, y):
    return flat.isInside(V(x, y, t / 2), 1e-6, True)


def narrows(flat, t, wires):
    """The narrowest places between the profile's edges: webs (material
    between two cuts) and gaps (cut-out slots), each { width, at }."""
    edges = [(wi, e) for wi, w in enumerate(wires) for e in w.Edges]
    if len(edges) > MAX_EDGES:
        return None, None
    webs, gaps = [], []
    for i in range(len(edges)):
        wi, ei = edges[i]
        for j in range(i + 1, len(edges)):
            wj, ej = edges[j]
            if wi == wj and any(a.Point.distanceToPoint(b.Point) < 1e-6 for a in ei.Vertexes for b in ej.Vertexes):
                continue
            d, pts, _ = ei.distToShape(ej)
            if d < 1e-6:
                continue
            p, q = pts[0]
            if wi == wj:
                seg = q - p
                seg.normalize()
                ti, tj = _tangent(ei, p), _tangent(ej, q)
                if ti is None or tj is None or abs(seg.dot(ti)) > 0.2 or abs(seg.dot(tj)) > 0.2:
                    continue  # reaching round a corner, not across
            mx, my = (p.x + q.x) / 2, (p.y + q.y) / 2
            (webs if _in(flat, t, mx, my) else gaps).append({"width": round(d, 3), "at": [round(mx, 2), round(my, 2)]})
    return sorted(webs, key=lambda x: x["width"]), sorted(gaps, key=lambda x: x["width"])


def inside_corners(flat, t, wires, radius):
    """Corners a round cutter cannot reach: sharp corners with material
    round more than half of them, and concave arcs tighter than its radius."""
    out = []
    for w in wires:
        es = w.OrderedEdges if hasattr(w, "OrderedEdges") else w.Edges
        for k, e in enumerate(es):
            nx = es[(k + 1) % len(es)]
            p = next((a.Point for a in e.Vertexes for b in nx.Vertexes if a.Point.distanceToPoint(b.Point) < 1e-6), None)
            if p is not None and len(es) > 1:
                ta, tb = _tangent(e, p), _tangent(nx, p)
                if ta is not None and tb is not None and abs(ta.dot(tb)) < math.cos(math.radians(5)):
                    eps = 0.05
                    share = sum(1 for i in range(24) if _in(flat, t, p.x + eps * math.cos(i * math.pi / 12), p.y + eps * math.sin(i * math.pi / 12))) / 24
                    if share > 0.55:
                        out.append({"radius": 0.0, "at": [round(p.x, 2), round(p.y, 2)]})
            if type(e.Curve).__name__ == "Circle" and not e.isClosed() and e.Curve.Radius < radius - 1e-6:
                c = e.Curve.Center
                if not _in(flat, t, c.x, c.y):
                    m = e.valueAt((e.FirstParameter + e.LastParameter) / 2)
                    out.append({"radius": round(e.Curve.Radius, 3), "at": [round(m.x, 2), round(m.y, 2)]})
    return out


def hole_sizes(holes):
    out = []
    for h in holes:
        if len(h.Edges) == 1 and type(h.Edges[0].Curve).__name__ == "Circle":
            c = h.Edges[0].Curve
            out.append({"d": round(2 * c.Radius, 3), "at": [round(c.Location.x, 2), round(c.Location.y, 2)], "round": True})
        else:
            bb = h.BoundBox
            out.append({"d": round(min(bb.XLength, bb.YLength), 3), "at": [round(bb.Center.x, 2), round(bb.Center.y, 2)], "round": False})
    return out


# ---- the tools ----------------------------------------------------------------------------

def tools(session):
    s = session

    def cut_setup(o):
        setup = store.setup_of(s.doc, o.Label, "cut")
        if not setup:
            raise ToolError("invalid_state", f"{o.Label} is in no cut setup: setup add {{ type: \"cut\", bodies: [\"{o.Label}\"], settings: {{ machine, thickness, kerf }} }} once the person says it is cut from sheet.")
        return setup

    def machine_of(setup, a, assumed):
        m = (a.get("machine") or setup.get("machine") or "").lower()
        if not m:
            m = "laser"
            assumed["machine"] = "laser (the setup does not say)"
        if m not in MACHINES:
            raise ToolError("invalid_input", f"machine: one of {', '.join(MACHINES)}.")
        mat = (a.get("material") or setup.get("material") or "").lower()
        if not mat:
            mat = "plywood" if m == "router" else "steel"
            assumed["material"] = f"{mat} (not said)"
        if mat not in METALS + OTHERS:
            raise ToolError("invalid_input", f"material: one of {', '.join(METALS + OTHERS)}.")
        if setup.get("kerf") is not None:
            kerf = float(setup["kerf"])
        else:
            kerf = KERF[m]
            assumed["kerf"] = f"{kerf} mm, a typical {m}" + (" cutter's diameter" if m == "router" else "'s")
        return m, mat, kerf

    def sheet_part(o):
        info = sheet_of(o.Shape)
        if info["sheet"]:
            return info
        if info.get("why"):
            raise ToolError("invalid_input", f"{o.Label} is not a sheet part: {info['why']}.")
        bits = []
        if info["notSquare"]:
            bits.append(f"faces not square to the sheet: {', '.join(info['notSquare'][:12])}")
        if info["steps"]:
            bits.append(f"flat faces between the two sides (steps or pockets): {', '.join(info['steps'][:12])}")
        return {**info, "problem": "; ".join(bits) or "fewer than two parallel flat faces"}

    def cut_check(a):
        o = s.body(a.get("body"))
        setup = cut_setup(o)
        assumed = {}
        m, mat, kerf = machine_of(setup, a, assumed)
        info = sheet_part(o)
        findings = []

        def find(rule, severity, says, value=None, limit=None, at=None):
            findings.append({"rule": rule, "severity": severity, **({"value": value} if value is not None else {}), **({"limit": limit} if limit is not None else {}), **({"at": at} if at else {}), "says": says})

        if not info["sheet"]:
            find("not-sheet", "error", f"Not a sheet part — {info['problem']}. A sheet is cut straight through: make every side square to the faces, or mill it.")
            return {"body": o.Label, "verdict": "fix", "machine": m, "findings": findings, "sheet": {k: info[k] for k in ("thickness", "normal", "notSquare", "steps")}}
        t = info["thickness"]
        if setup.get("thickness") is not None and abs(float(setup["thickness"]) - t) > 0.02:
            find("thickness", "error", f"{t:.2f} mm thick, the setup's sheet is {float(setup['thickness']):g} mm: make the part as thick as the sheet, or set the setup's thickness.", t, float(setup["thickness"]))
        elif setup.get("thickness") is None:
            assumed["thickness"] = f"{t:g} mm, read from the part (the setup does not say)"
        r, floor = rules(m, mat, t, kerf)
        flat, outer, holes = flatten(o.Shape, info)
        for h in hole_sizes(holes):
            if h["d"] < floor["hole"] - 1e-6:
                why = {"plasma": "under the sheet's thickness, plasma can't cut it clean", "router": "under the cutter's diameter"}.get(m, "too small for the beam to pierce clean")
                find("small-hole", "error", f"A hole {h['d']:.2f} mm across, {why}: drill it after, or make it at least {floor['hole']:.2f} mm.", h["d"], floor["hole"], h["at"])
            elif h["d"] < r["hole"] - 1e-6:
                find("small-hole", "warning", f"A hole {h['d']:.2f} mm across, under {r['hole']:.2f} mm for {m} in {t:g} mm {mat}: it may come out tapered or not round — make it bigger or drill it.", h["d"], r["hole"], h["at"])
        webs, gaps = narrows(flat, t, [outer] + holes)
        if webs is None:
            assumed["webs"] = f"not read: over {MAX_EDGES} edges in the profile"
        else:
            for w in webs[:3]:
                if w["width"] < r["web"] - 1e-6:
                    find("thin-web", "error" if w["width"] < kerf else "warning", f"Material {w['width']:.2f} mm wide between two cuts, under {r['web']:.2f} mm: it can burn through, warp or snap — widen it.", w["width"], r["web"], w["at"])
            for g in gaps[:3]:
                if g["width"] < floor["slot"] - 1e-6:
                    find("narrow-slot", "error", f"A gap {g['width']:.2f} mm wide, under the cut's own width {floor['slot']:.2f} mm: it cannot be cut — widen it.", g["width"], floor["slot"], g["at"])
                elif g["width"] < r["slot"] - 1e-6:
                    find("narrow-slot", "warning", f"A gap {g['width']:.2f} mm wide, under {r['slot']:.2f} mm: the slug may not drop out clean — widen it.", g["width"], r["slot"], g["at"])
        if m == "router":
            for c in inside_corners(flat, t, [outer] + holes, kerf / 2)[:6]:
                find("inside-corner", "warning", f"An inside corner of radius {c['radius']:.2f} mm, tighter than the cutter's {kerf / 2:.2f} mm: it comes out rounded — round it to {kerf / 2:.2f} mm or more, or add a dogbone.", c["radius"], round(kerf / 2, 3), c["at"])
        bb = flat.BoundBox
        size = [round(bb.XLength, 2), round(bb.YLength, 2)]
        sheet = a.get("sheet")
        if sheet:
            sw, sh = float(sheet[0]), float(sheet[1])
            if not ((size[0] <= sw and size[1] <= sh) or (size[0] <= sh and size[1] <= sw)):
                find("sheet-size", "error", f"{size[0]} x {size[1]} mm does not fit the {sw:g} x {sh:g} mm sheet: split it.", max(size), max(sw, sh))
        order = {"error": 0, "warning": 1}
        findings.sort(key=lambda f: order[f["severity"]])
        verdict = "fix" if any(f["severity"] == "error" for f in findings) else ("pass" if not findings else "pass with warnings")
        out = {
            "body": o.Label, "verdict": verdict, "machine": m, "material": mat, "kerf": kerf,
            "sheet": {"thickness": t, "normal": info["normal"]},
            "profile": {"size": size, "holes": len(holes), "edges": sum(len(w.Edges) for w in [outer] + holes), **({"leastWeb": webs[0]["width"]} if webs else {}), **({"leastGap": gaps[0]["width"]} if gaps else {})},
            "least": r, "findings": findings[:10],
            "setup": f"{setup['name']} ({setup['id']})",
        }
        if not sheet:
            out["unjudged"] = ["sheet size: give sheet [w, h] mm to check it fits"]
        if assumed:
            out["assumed"] = assumed
        out["note"] = "Least features are rules of thumb for the machine, material and thickness; the shop's own figures win."
        return out

    def cut_export(a):
        names = a.get("bodies") or ([a["body"]] if a.get("body") else None)
        objs = [s.body(n) for n in names] if names else [s.body(None)]
        copies = a.get("copies", 1)
        setups = {o.Label: cut_setup(o) for o in objs}
        first = setups[objs[0].Label]
        kerf = float(first.get("kerf") if first.get("kerf") is not None else KERF.get((first.get("machine") or "laser").lower(), 0.2))
        comp = bool(a.get("compensate", False))
        gap = float(a.get("gap", max(3.0, 2 * kerf)))
        sheet = a.get("sheet")
        parts = []
        thick = None
        for o in objs:
            info = sheet_part(o)
            if not info["sheet"]:
                raise ToolError("invalid_input", f"{o.Label} is not a sheet part — {info['problem']}: cut_check says more.")
            if thick is not None and abs(info["thickness"] - thick) > 0.02:
                raise ToolError("invalid_input", f"{o.Label} is {info['thickness']:g} mm, the others {thick:g} mm: one sheet per file — export them apart.")
            thick = info["thickness"]
            flat, outer, holes = flatten(o.Shape, info)
            loops = [[p for _, p in primitives(w, None)] for w in [outer] + holes]
            if comp:
                # Each loop moved off the material by half the kerf: the
                # outside out, the holes in.
                try:
                    loops = [sf.offset_loop(loops[0], kerf / 2)] + [sf.offset_loop(h, -kerf / 2) for h in loops[1:]]
                except ValueError as e:
                    raise ToolError("failed", f"The kerf offset on {o.Label}: {e} — export with compensate: false and let the cutter's software take off the kerf.")
            prims = [(sf.OUTER, p) for p in loops[0]] + [(sf.HOLES, p) for h in loops[1:] for p in h]
            bb = sf.bbox(loops[0])
            n = int(copies.get(o.Label, 1)) if isinstance(copies, dict) else int(copies)
            if n < 1 or n > 500:
                raise ToolError("invalid_input", "copies: 1 to 500 (a number, or { body: n }).")
            for _ in range(n):
                parts.append((o.Label, prims, bb))
        sw, sh = (float(sheet[0]), float(sheet[1])) if sheet else (None, None)
        at = sf.shelf([(bb[2] - bb[0], bb[3] - bb[1]) for _, _, bb in parts], sw, sh, gap)
        items, placed, left = [], {}, {}
        for (label, prims, bb), pos in zip(parts, at):
            if pos is None:
                left[label] = left.get(label, 0) + 1
                continue
            dx, dy = pos[0] - bb[0], pos[1] - bb[1]
            items += [(layer, sf.shift(p, dx, dy)) for layer, p in prims]
            placed[label] = placed.get(label, 0) + 1
        if not items:
            raise ToolError("invalid_input", f"Nothing fits the {sw:g} x {sh:g} mm sheet with a {gap:g} mm gap: a bigger sheet, or split the part.")
        if sheet:
            W, H = sw, sh
        else:
            W = max(pos[0] + bb[2] - bb[0] for (_, _, bb), pos in zip(parts, at) if pos) + gap
            H = max(pos[1] + bb[3] - bb[1] for (_, _, bb), pos in zip(parts, at) if pos) + gap
        base = os.path.join(s.out_dir, a.get("name") or (objs[0].Label.replace(" ", "_") + "-cut"))
        os.makedirs(os.path.dirname(base) or ".", exist_ok=True)
        wrote = {}
        want = {}
        for _, p in items:
            k = {"line": "LINE", "arc": "ARC", "circle": "CIRCLE", "poly": "POLYLINE"}[p[0]]
            want[k] = want.get(k, 0) + 1
        fmts = [f.lower() for f in (a.get("formats") or ["dxf", "svg"])]
        for f in fmts:
            if f not in ("dxf", "svg"):
                raise ToolError("invalid_input", "formats: dxf and/or svg.")
        if "dxf" in fmts:
            p = base + ".dxf"
            with open(p, "w", encoding="ascii", newline="\r\n") as fh:
                fh.write(sf.dxf(items, (0, 0, W, H)))
            with open(p, encoding="ascii") as fh:
                back = sf.read_dxf(fh.read())
            wrote["dxf"] = {"path": p, "bytes": os.path.getsize(p), "readBack": back["entities"], "layers": back["layers"], "matches": back["entities"] == want}
        if "svg" in fmts:
            p = base + ".svg"
            with open(p, "w", encoding="utf-8") as fh:
                fh.write(sf.svg(items, W, H))
            with open(p, encoding="utf-8") as fh:
                back = sf.read_svg(fh.read())
            wrote["svg"] = {"path": p, "bytes": os.path.getsize(p), "readBack": {k: v for k, v in back.items() if v}}
        out = {"files": wrote, "placed": placed, "sheet": [round(W, 2), round(H, 2)], "thickness": thick, "kerf": kerf, "compensated": comp, "gap": gap, "entities": want}
        if left:
            out["left"] = {"bodies": left, "why": "no room on the sheet: a bigger sheet, fewer copies, or another sheet"}
        out["note"] = ("Profiles offset out by half the kerf (holes in): cut on the line." if comp else "Profiles as modelled: let the cutter's software take off the kerf.") + " Layers OUTER and HOLES; cut holes first."
        return out

    body = {"type": "string", "description": "A body's name; may be left out when there is one."}
    sheet = {"type": "array", "items": {"type": "number"}, "description": "The sheet [w, h], mm."}
    return [
        Tool(
            "cut_check",
            "Can a body in a cut setup be cut from sheet: two parallel flat faces a constant thickness apart and every other face square to them (faces that are not named); its thickness against the setup's sheet; holes, webs (material between cuts) and slots under the least feature for the machine (laser, waterjet, plasma, router), material and thickness; a router's inside corners tighter than its cutter; plasma holes under the thickness; sheet [w,h] fit. Verdict fix, pass with warnings or pass. Trap: the least features are rules of thumb, said under assumed with the kerf and material when the setup leaves them out.",
            obj({"body": body, "machine": {"type": "string", "enum": list(MACHINES)}, "material": {"type": "string"}, "sheet": sheet}),
            cut_check,
        ),
        Tool(
            "cut_export",
            "The flat profiles of one or more sheet bodies (bodies, copies n or { body: n }) laid out in rows on the sheet [w,h] (a gap between, 3 mm or two kerfs by default), as DXF (R12: LINE, ARC, CIRCLE, POLYLINE; layers OUTER and HOLES) and SVG in mm, read back and counted. compensate: true offsets each profile by half the kerf (outside out, holes in) — leave it off when the cutter's software does that. One thickness per file.",
            obj({"body": body, "bodies": {"type": "array", "items": {"type": "string"}}, "copies": {"type": ["integer", "object"]}, "sheet": sheet, "gap": {"type": "number"}, "compensate": {"type": "boolean"}, "formats": {"type": "array", "items": {"type": "string", "enum": ["dxf", "svg"]}}, "name": {"type": "string"}}),
            cut_export,
        ),
    ]
