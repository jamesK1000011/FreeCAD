# SPDX-License-Identifier: LGPL-2.1-or-later
# CalculiX's files, as much as a linear static solve needs: the mesh part
# of an Abaqus/CalculiX input deck (as FreeCAD's FemMesh.writeABAQUS writes
# it), the loads and supports added to it, and the nodal results read back
# from the .frd. Plain Python, no FreeCAD, so it is tested anywhere.
# Units: mm, N, MPa, tonne/mm³ (CalculiX takes any consistent set).

import math

# The corner and mid-side nodes of each face of a C3D10 tetrahedron, in
# CalculiX's numbering (0-based): face n is the n-th entry (faces 1 to 4).
TET10_FACES = [
    ((0, 1, 2), (4, 5, 6)),
    ((0, 3, 1), (7, 8, 4)),
    ((1, 3, 2), (8, 9, 5)),
    ((2, 3, 0), (9, 7, 6)),
]


def read_mesh(text):
    """Nodes {id: (x, y, z)} and C3D10 elements {id: (n1 … n10)} of a deck."""
    nodes, elems = {}, {}
    mode = None
    pending = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("**"):
            continue
        if line.startswith("*"):
            mode = None
            pending = []
            kw = line.upper().replace(" ", "")
            if kw.startswith("*NODE,") or kw == "*NODE":
                mode = "node"
            elif kw.startswith("*ELEMENT,") and "TYPE=C3D10" in kw:
                mode = "tet10"
            continue
        if mode == "node":
            p = [x for x in line.split(",") if x.strip()]
            nodes[int(p[0])] = (float(p[1]), float(p[2]), float(p[3]))
        elif mode == "tet10":
            pending += [int(x) for x in line.split(",") if x.strip()]
            if len(pending) >= 11:
                elems[pending[0]] = tuple(pending[1:11])
                pending = []
    return nodes, elems


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _norm(a):
    return math.sqrt(a[0] ** 2 + a[1] ** 2 + a[2] ** 2)


def face_nodes(elem, face_no):
    corners, mids = TET10_FACES[face_no - 1]
    return [elem[i] for i in corners], [elem[i] for i in mids]


def area_of(nodes, corners):
    a, b, c = (nodes[n] for n in corners)
    return 0.5 * _norm(_cross(_sub(b, a), _sub(c, a)))


def nodal_loads(nodes, elems, faces, force):
    """A total force [Fx, Fy, Fz] (N) spread evenly over the area of the
    element faces [(element, face 1-4)]: for a six-node triangle under an
    even traction the corners carry nothing and each mid-side node a third
    of the triangle's share (the consistent nodal loads). {node: [fx,fy,fz]}."""
    tris = []
    for eid, fno in faces:
        corners, mids = face_nodes(elems[eid], fno)
        tris.append((area_of(nodes, corners), mids))
    total = sum(a for a, _ in tris)
    if total <= 0:
        return {}, 0.0
    out = {}
    for a, mids in tris:
        share = a / total / 3.0
        for n in mids:
            f = out.setdefault(n, [0.0, 0.0, 0.0])
            for k in range(3):
                f[k] += force[k] * share
    return out, total


def _rows(ids, per=12):
    ids = list(ids)
    return "\n".join(", ".join(str(i) for i in ids[j:j + per]) for j in range(0, len(ids), per))


def deck(mesh_text, material, fixed, cloads, dloads, gravity):
    """The whole input deck: the mesh as written, one material on every
    volume element (Evolumes), the fixed nodes, nodal loads {node: [f]},
    face pressures [(element, face, MPa)], gravity [gx, gy, gz] mm/s² or
    None; nodal displacements and stresses to the .frd."""
    out = [mesh_text.rstrip(), "",
           "** Agent Link: material, supports, loads",
           "*MATERIAL, NAME=PART",
           "*ELASTIC",
           f"{material['E']:.6g}, {material['nu']:.6g}",
           "*DENSITY",
           f"{material['rho'] * 1e-9:.6g}",
           "*SOLID SECTION, ELSET=Evolumes, MATERIAL=PART",
           "*NSET, NSET=AGFIX",
           _rows(sorted(fixed)),
           "*STEP",
           "*STATIC",
           "*BOUNDARY",
           "AGFIX, 1, 3, 0.0"]
    cl = []
    for n in sorted(cloads):
        for k in range(3):
            v = cloads[n][k]
            if abs(v) > 1e-12:
                cl.append(f"{n}, {k + 1}, {v:.9g}")
    if cl:
        out += ["*CLOAD"] + cl
    dl = [f"{e}, P{f}, {p:.9g}" for e, f, p in dloads]
    if gravity is not None:
        g = _norm(gravity)
        if g > 0:
            dl.append(f"Evolumes, GRAV, {g:.9g}, {gravity[0] / g:.9g}, {gravity[1] / g:.9g}, {gravity[2] / g:.9g}")
    if dl:
        out += ["*DLOAD"] + dl
    out += ["*NODE FILE", "U", "*EL FILE", "S", "*END STEP", ""]
    return "\n".join(out)


def read_frd(text):
    """The last step's nodal DISP {node: (ux,uy,uz)} and STRESS
    {node: (sxx,syy,szz,sxy,syz,szx)} from an ASCII .frd."""
    disp, stress = {}, {}
    block = None
    for line in text.splitlines():
        if line.startswith(" -4"):
            name = line[5:13].strip()
            block = {"DISP": disp, "STRESS": stress}.get(name)
            if block is not None:
                block.clear()
            continue
        if line.startswith(" -3"):
            block = None
            continue
        if block is not None and line[1:3] == "-1":
            nid = int(line[3:13])
            s = line.rstrip("\r\n")
            block[nid] = tuple(float(s[p:p + 12]) for p in range(13, len(s), 12) if s[p:p + 12].strip())
    return disp, stress


def von_mises(s):
    sxx, syy, szz, sxy, syz, szx = s[:6]
    return math.sqrt(0.5 * ((sxx - syy) ** 2 + (syy - szz) ** 2 + (szz - sxx) ** 2) + 3.0 * (sxy ** 2 + syz ** 2 + szx ** 2))


def normal_stress(s, n):
    """The normal stress along the unit direction n."""
    sxx, syy, szz, sxy, syz, szx = s[:6]
    x, y, z = n
    return sxx * x * x + syy * y * y + szz * z * z + 2 * (sxy * x * y + syz * y * z + szx * z * x)
