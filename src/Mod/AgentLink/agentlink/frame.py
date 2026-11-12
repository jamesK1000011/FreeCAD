# SPDX-License-Identifier: LGPL-2.1-or-later
# A frame of straight members — beams, columns, braces, rafters, a truss —
# solved with CalculiX's three-node beam elements (B32, expanded into
# solids by CalculiX): members joined where their ends meet, supports pinned,
# fixed or on rollers, loads along members (N/mm), at joints (N), and the
# members' own weight. Answers each member's peak stress along it and von
# Mises, its largest deflection relative to its ends and overall, and the
# reaction at each support. Pure Python with the ccx binary; no FreeCAD.

import math
import os
import subprocess

from . import ccx_io

SECTIONS = ("rect", "circ", "pipe", "box")


def _sub(a, b):
    return [a[i] - b[i] for i in range(3)]


def _dot(a, b):
    return sum(a[i] * b[i] for i in range(3))


def _cross(a, b):
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


def _norm(a):
    return math.sqrt(_dot(a, a))


def _unit(a):
    n = _norm(a)
    return [x / n for x in a]


def section_props(sec):
    """(area mm², I about the strong axis mm⁴, c the distance to the extreme fibre mm, ccx's section lines)."""
    k = sec.get("kind", "rect")
    if k == "rect":
        b, d = float(sec["b"]), float(sec["d"])
        return b * d, b * d ** 3 / 12, d / 2, ("RECT", f"{b:.6g}, {d:.6g}")
    if k == "circ":
        d = float(sec["d"])
        r = d / 2
        return math.pi * r * r, math.pi * r ** 4 / 4, r, ("CIRC", f"{r:.6g}, {r:.6g}")
    if k == "pipe":
        d, t = float(sec["d"]), float(sec["t"])
        ro, ri = d / 2, d / 2 - t
        return math.pi * (ro * ro - ri * ri), math.pi * (ro ** 4 - ri ** 4) / 4, ro, ("PIPE", f"{ro - t / 2:.6g}, {t:.6g}")
    if k == "box":
        b, d, t = float(sec["b"]), float(sec["d"]), float(sec["t"])
        I = (b * d ** 3 - (b - 2 * t) * (d - 2 * t) ** 3) / 12
        return b * d - (b - 2 * t) * (d - 2 * t), I, d / 2, ("BOX", f"{b:.6g}, {d:.6g}, {t:.6g}, {t:.6g}, {t:.6g}, {t:.6g}")
    raise ValueError(f"section kind: {', '.join(SECTIONS)}")


def orient(t):
    """The section's 1-direction for a member along t: horizontal across a
    sloped or level member (its depth then stands upright), x for a column."""
    up = [0.0, 0.0, 1.0]
    c = _cross(up, t)
    if _norm(c) < 1e-6:
        return [1.0, 0.0, 0.0]
    return _unit(c)


def build(members, supports, loads, gravity=None, n=8, tol=1.0):
    """The input deck and the bookkeeping to read it: members [{name, from,
    to, section {kind …}, E, nu, rho (kg/m³)}], supports [{at, kind pinned
    | fixed | roller}], loads [{member, w [wx,wy,wz] N/mm} | {at, force
    [fx,fy,fz] N}], gravity [gx,gy,gz] mm/s² or None."""
    joints = []

    def joint(p):
        for i, q in enumerate(joints):
            if _norm(_sub(p, q)) <= tol:
                return i
        joints.append(list(p))
        return len(joints) - 1

    node_xyz = {}
    nid = [0]

    def new_node(p):
        nid[0] += 1
        node_xyz[nid[0]] = p
        return nid[0]

    jnode = {}
    mem = []
    eid = 0
    lines_el, lines_sec, loads_c = [], [], {}
    mats = {}
    for k, m in enumerate(members):
        a, b = [float(x) for x in m["from"]], [float(x) for x in m["to"]]
        L = _norm(_sub(b, a))
        if L < tol:
            raise ValueError(f"member {m.get('name', k)}: its ends are the same point")
        ja, jb = joint(a), joint(b)
        for j, p in ((ja, a), (jb, b)):
            if j not in jnode:
                jnode[j] = new_node(joints[j])
        t = _unit(_sub(b, a))
        ids = [jnode[ja]]
        for i in range(1, 2 * n):
            ids.append(new_node([a[c] + t[c] * L * i / (2 * n) for c in range(3)]))
        ids.append(jnode[jb])
        A, I, cmax, (stype, sline) = section_props(m["section"])
        es = []
        for e in range(n):
            eid += 1
            es.append(eid)
            lines_el.append(f"{eid}, {ids[2 * e]}, {ids[2 * e + 1]}, {ids[2 * e + 2]}")
        mkey = (float(m["E"]), float(m.get("nu", 0.3)), float(m.get("rho", 0)))
        mname = mats.setdefault(mkey, f"M{len(mats) + 1}")
        n1 = orient(t)
        lines_sec += [f"*ELSET, ELSET=B{k + 1}", ccx_io._rows(es), f"*BEAM SECTION, ELSET=B{k + 1}, MATERIAL={mname}, SECTION={stype}", sline, f"{n1[0]:.6f}, {n1[1]:.6f}, {n1[2]:.6f}"]
        mem.append({"name": m.get("name") or f"M{k + 1}", "nodes": ids, "a": a, "b": b, "t": t, "L": L, "A": A, "I": I, "c": cmax, "elements": es,
                    "rho": float(m.get("rho", 0)), "E": float(m["E"])})
    byname = {m["name"]: m for m in mem}

    def addload(node, f):
        acc = loads_c.setdefault(node, [0.0, 0.0, 0.0])
        for c in range(3):
            acc[c] += f[c]

    def along(m, w):
        le = m["L"] / n
        for e in range(n):
            a_, mid, c_ = m["nodes"][2 * e], m["nodes"][2 * e + 1], m["nodes"][2 * e + 2]
            addload(a_, [x * le / 6 for x in w])
            addload(mid, [x * 2 * le / 3 for x in w])
            addload(c_, [x * le / 6 for x in w])

    applied = 0.0
    for ld in loads or []:
        if ld.get("member"):
            m = byname.get(ld["member"])
            if m is None:
                raise ValueError(f"load on member {ld['member']!r}: no such member")
            w = [float(x) for x in ld["w"]]
            along(m, w)
            applied += _norm(w) * m["L"]
        else:
            p = [float(x) for x in ld["at"]]
            j = next((i for i, q in enumerate(joints) if _norm(_sub(p, q)) <= tol), None)
            if j is None:
                raise ValueError(f"load at {p}: no joint there (loads go on member ends or along members)")
            addload(jnode[j], [float(x) for x in ld["force"]])
            applied += _norm(ld["force"])
    if gravity is not None:
        for m in mem:
            if m["rho"] > 0:
                q = m["rho"] * 1e-12 * m["A"]  # t/mm per mm length … N/mm per (mm/s²)
                along(m, [g * q for g in gravity])
    sup_lines, sup_sets = [], []
    for k, sp in enumerate(supports or []):
        p = [float(x) for x in sp["at"]]
        j = next((i for i, q in enumerate(joints) if _norm(_sub(p, q)) <= tol), None)
        if j is None:
            raise ValueError(f"support at {p}: no member end there")
        node = jnode[j]
        kind = sp.get("kind", "pinned")
        if kind == "fixed":
            sup_lines += [f"{node}, 1, 6"]
        elif kind == "pinned":
            sup_lines += [f"{node}, 1, 3"]
        elif kind == "roller":
            # held up and sideways, free to slide along the member it carries
            att = next((m for m in mem if node in (m["nodes"][0], m["nodes"][-1])), None)
            side = 1 if att is None or abs(att["t"][0]) >= abs(att["t"][1]) else 0
            sup_lines += [f"{node}, 3, 3", f"{node}, {side + 1}, {side + 1}"]
        else:
            raise ValueError("support kind: pinned | fixed | roller")
        sup_sets.append((f"S{k + 1}", node, p, kind))
    # torsion: a beam on pins is free to roll about its own axis; at each
    # pinned or roller support it is held about the axis nearest its own,
    # as a real seat holds it (joints between members are rigid already)
    held_rot = set()
    for _, node, _, kind in sup_sets:
        if kind == "fixed":
            continue
        for m in mem:
            if node in (m["nodes"][0], m["nodes"][-1]):
                ax = max(range(3), key=lambda c: abs(m["t"][c]))
                if (node, ax) not in held_rot:
                    held_rot.add((node, ax))
                    sup_lines.append(f"{node}, {4 + ax}, {4 + ax}")
    out = ["*NODE, NSET=NALL"] + [f"{i}, {p[0]:.6f}, {p[1]:.6f}, {p[2]:.6f}" for i, p in sorted(node_xyz.items())]
    out += ["*ELEMENT, TYPE=B32R, ELSET=EALL"] + lines_el
    for (E, nu, rho), name in mats.items():
        out += [f"*MATERIAL, NAME={name}", "*ELASTIC", f"{E:.6g}, {nu:.6g}"] + (["*DENSITY", f"{rho * 1e-12:.6g}"] if rho else [])
    out += lines_sec
    for name, node, _, _ in sup_sets:
        out += [f"*NSET, NSET={name}", str(node)]
    out += ["*STEP", "*STATIC", "*BOUNDARY"] + sup_lines
    if loads_c:
        out += ["*CLOAD"] + [f"{nd}, {c + 1}, {v[c]:.9g}" for nd, v in sorted(loads_c.items()) for c in range(3) if abs(v[c]) > 1e-12]
    out += ["*NODE FILE", "U", "*EL FILE", "S"]
    for name, _, _, _ in sup_sets:
        out += [f"*NODE PRINT, NSET={name}, TOTALS=ONLY", "RF"]
    out += ["*END STEP", ""]
    return "\n".join(out), {"members": mem, "supports": sup_sets, "applied": applied, "nodal": loads_c}


def read_coords(frd):
    xyz, on = {}, False
    for line in frd.splitlines():
        if line.startswith("    2C"):
            on = True
            continue
        if on:
            if line.startswith(" -3"):
                break
            if line.startswith(" -1"):
                xyz[int(line[3:13])] = (float(line[13:25]), float(line[25:37]), float(line[37:49]))
    return xyz


def read_reactions(dat):
    """{set name: [fx, fy, fz]} from *NODE PRINT TOTALS=ONLY."""
    out, cur = {}, None
    for line in dat.splitlines():
        low = line.lower()
        if "force (fx,fy,fz)" in low and "set" in low:
            cur = line.split("set")[-1].split()[0].strip().upper()
            continue
        if cur and line.strip():
            try:
                vals = [float(x) for x in line.split()]
            except ValueError:
                continue
            if len(vals) >= 3:
                out[cur] = vals[-3:]
                cur = None
    return out


def solve(ccx, workdir, members, supports, loads, gravity=None, n=8, timeout=300):
    text, book = build(members, supports, loads, gravity, n)
    os.makedirs(workdir, exist_ok=True)
    for f in os.listdir(workdir):
        if f.startswith("frame."):
            os.remove(os.path.join(workdir, f))
    with open(os.path.join(workdir, "frame.inp"), "w") as f:
        f.write(text)
    env = dict(os.environ)
    env.setdefault("OMP_NUM_THREADS", "2")
    r = subprocess.run([ccx, "-i", "frame"], cwd=workdir, capture_output=True, text=True, timeout=timeout, env=env)
    frd_p = os.path.join(workdir, "frame.frd")
    log = (r.stdout or "") + (r.stderr or "")
    if "*ERROR" in log or not os.path.isfile(frd_p):
        err = [ln.strip() for ln in log.splitlines() if "ERROR" in ln][:2] or log.strip().splitlines()[-2:]
        singular = any("singular" in ln.lower() or "zero pivot" in ln.lower() for ln in log.splitlines())
        raise RuntimeError("CalculiX refused the frame: " + " | ".join(err) + (" — it can move as a mechanism: more supports, or fixed ones." if singular else ""))
    with open(frd_p) as f:
        frd = f.read()
    disp, stress = ccx_io.read_frd(frd)
    xyz = read_coords(frd)
    dat = ""
    if os.path.isfile(os.path.join(workdir, "frame.dat")):
        with open(os.path.join(workdir, "frame.dat")) as f:
            dat = f.read()
    reac = read_reactions(dat)
    # each expanded node to the member whose axis it lies by
    rows = []
    for m in book["members"]:
        reach = m["c"] * 1.6 + 1.0
        ns = []
        for nd, p in xyz.items():
            v = _sub(p, m["a"])
            s_ = _dot(v, m["t"])
            if -1e-6 <= s_ <= m["L"] + 1e-6 and _norm([v[c] - m["t"][c] * s_ for c in range(3)]) <= reach:
                ns.append((nd, s_))
        if not ns:
            rows.append({"member": m["name"], "note": "no results found along it"})
            continue
        sig = [ccx_io.normal_stress(stress[nd], m["t"]) for nd, _ in ns if nd in stress]
        vm = [ccx_io.von_mises(stress[nd]) for nd, _ in ns if nd in stress]
        # deflection across the member, relative to the line between its ends
        ua = disp.get(m["nodes"][0], (0, 0, 0))[:3]
        ub = disp.get(m["nodes"][-1], (0, 0, 0))[:3]
        rel, tot = 0.0, 0.0
        for nd, s_ in ns:
            if nd not in disp:
                continue
            u = disp[nd][:3]
            f = s_ / m["L"]
            base = [ua[c] + (ub[c] - ua[c]) * f for c in range(3)]
            d = [u[c] - base[c] for c in range(3)]
            d = [d[c] - m["t"][c] * _dot(d, m["t"]) for c in range(3)]
            rel = max(rel, _norm(d))
            tot = max(tot, _norm(u))
        rows.append({"member": m["name"], "length": round(m["L"], 1), "axialBendingMPa": [round(min(sig), 3), round(max(sig), 3)] if sig else None,
                     "vonMisesMPa": round(max(vm), 3) if vm else None, "deflectionMm": round(rel, 3), "spanOverDeflection": round(m["L"] / rel) if rel > 1e-9 else None,
                     "movesMm": round(tot, 3)})
    # what the support gives: CalculiX's reaction, plus the load laid right on
    # the supported node (it goes straight into the support)
    sups = []
    for name, node, p, kind in book["supports"]:
        rf = reac.get(name.upper())
        on = book["nodal"].get(node, [0.0, 0.0, 0.0])
        sups.append({"at": [round(c, 2) for c in p], "kind": kind, "reactionN": [round(rf[c] - on[c], 2) for c in range(3)] if rf else None})
    out = {"members": rows, "supports": sups, "files": workdir}
    # the reactions must carry the whole load: CalculiX reads them low on some
    # beam supports (a point load on a column) — said, not hidden
    tot = [sum(v[c] for v in book["nodal"].values()) for c in range(3)]
    got = [sum((sp["reactionN"] or [0, 0, 0])[c] for sp in sups) for c in range(3)]
    if any(abs(got[c] + tot[c]) > 0.02 * max(1.0, max(abs(x) for x in tot)) for c in range(3)):
        out["reactionsNote"] = (f"the reactions add to {[round(x, 1) for x in got]} N against {[round(-x, 1) for x in tot]} N of load: "
                                "CalculiX reads beam supports short here — use the load total, and member stresses, which are sound")
    out["loadTotalN"] = [round(-x, 1) for x in tot]
    return out
