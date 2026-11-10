# SPDX-License-Identifier: LGPL-2.1-or-later
# Mesh formats FreeCAD's Mesh module does not read — glTF and GLB, Collada,
# VRML 2 and 3DS — read in numpy alone: every mesh node flattened into one
# mesh, its transforms applied, in millimetres and Z up. What each format
# says its units and up axis are:
#   glb, gltf  metres, Y up (the glTF specification)
#   dae        the file's <unit meter>, its <up_axis>
#   wrl        metres, Y up (the VRML specification)
#   3ds        no units in the format: taken as mm, Z up
# A caller may override either; FBX is not read (a closed format: export
# it as glTF from the program that made it).

import base64
import json
import math
import os
import re
import struct
import xml.etree.ElementTree as ET

import numpy as np

FORMATS = ("glb", "gltf", "dae", "wrl", "vrml", "3ds")
UNITS = {"mm": 1.0, "cm": 10.0, "m": 1000.0, "in": 25.4}


class FormatError(ValueError):
    pass


def y_up_to_z_up(V):
    return np.column_stack([V[:, 0], -V[:, 2], V[:, 1]])


def _apply(M, V):
    return V @ M[:3, :3].T + M[:3, 3]


# ---- glTF / GLB --------------------------------------------------------------

_COMP = {5120: np.int8, 5121: np.uint8, 5122: np.int16, 5123: np.uint16, 5125: np.uint32, 5126: np.float32}
_N = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}


def _quat(q):
    x, y, z, w = q
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


def _gltf_node_matrix(n):
    if "matrix" in n:
        return np.array(n["matrix"], float).reshape(4, 4).T  # column-major
    M = np.eye(4)
    S = np.diag(n.get("scale", [1, 1, 1]))
    M[:3, :3] = _quat(n.get("rotation", [0, 0, 0, 1])) @ S
    M[:3, 3] = n.get("translation", [0, 0, 0])
    return M


def _read_gltf(doc, bin_chunk, folder):
    buffers = []
    for b in doc.get("buffers", []):
        uri = b.get("uri")
        if uri is None:
            if bin_chunk is None:
                raise FormatError("A buffer with no uri and no binary chunk.")
            buffers.append(bin_chunk)
        elif uri.startswith("data:"):
            buffers.append(base64.b64decode(uri.split(",", 1)[1]))
        else:
            p = os.path.join(folder, uri)
            if not os.path.isfile(p):
                raise FormatError(f"The buffer {uri!r} is not beside the file.")
            with open(p, "rb") as f:
                buffers.append(f.read())

    def accessor(i):
        a = doc["accessors"][i]
        if "bufferView" not in a:
            return np.zeros((a["count"], _N[a["type"]]))
        bv = doc["bufferViews"][a["bufferView"]]
        dt = np.dtype(_COMP[a["componentType"]])
        n = _N[a["type"]]
        start = bv.get("byteOffset", 0) + a.get("byteOffset", 0)
        stride = bv.get("byteStride") or dt.itemsize * n
        raw = buffers[bv["buffer"]]
        if stride == dt.itemsize * n:
            out = np.frombuffer(raw, dtype=dt, count=a["count"] * n, offset=start).reshape(a["count"], n)
        else:
            out = np.stack([np.frombuffer(raw, dtype=dt, count=n, offset=start + k * stride) for k in range(a["count"])])
        if a.get("normalized"):
            out = out / float(np.iinfo(dt).max)
        return out.astype(float)

    Vs, Fs, nodes = [], [], 0
    base = 0

    def visit(i, parent):
        nonlocal base, nodes
        n = doc["nodes"][i]
        M = parent @ _gltf_node_matrix(n)
        if "mesh" in n:
            for prim in doc["meshes"][n["mesh"]]["primitives"]:
                if prim.get("mode", 4) != 4:
                    continue  # points and lines are no surface
                if "POSITION" not in prim["attributes"]:
                    continue
                if "extensions" in prim and "KHR_draco_mesh_compression" in prim["extensions"]:
                    raise FormatError("Draco-compressed glTF: export it again without mesh compression.")
                P = _apply(M, accessor(prim["attributes"]["POSITION"]))
                idx = accessor(prim["indices"]).astype(np.int64).ravel() if "indices" in prim else np.arange(len(P))
                F = idx[: len(idx) // 3 * 3].reshape(-1, 3)
                if np.linalg.det(M[:3, :3]) < 0:
                    F = F[:, [0, 2, 1]]
                Vs.append(P)
                Fs.append(F + base)
                base += len(P)
                nodes += 1
        for c in n.get("children", []):
            visit(c, M)

    scene = doc.get("scenes", [{}])[doc.get("scene", 0)] if doc.get("scenes") else {"nodes": list(range(len(doc.get("nodes", []))))}
    for i in scene.get("nodes", []):
        visit(i, np.eye(4))
    return Vs, Fs, nodes


def read_gltf(path):
    with open(path, "rb") as f:
        data = f.read()
    if data[:4] == b"glTF":
        _magic, _version, _length = struct.unpack("<4sII", data[:12])
        off, doc, binc = 12, None, None
        while off < len(data):
            n, kind = struct.unpack("<II", data[off:off + 8])
            chunk = data[off + 8:off + 8 + n]
            if kind == 0x4E4F534A:
                doc = json.loads(chunk.decode("utf-8"))
            elif kind == 0x004E4942:
                binc = chunk
            off += 8 + n
        if doc is None:
            raise FormatError("A GLB with no JSON chunk.")
    else:
        doc, binc = json.loads(data.decode("utf-8")), None
    return _read_gltf(doc, binc, os.path.dirname(path))


# ---- Collada -----------------------------------------------------------------


def _ns(tag):
    return tag.split("}", 1)[1] if "}" in tag else tag


def _children(e, name):
    return [c for c in e if _ns(c.tag) == name]


def _child(e, name):
    for c in e:
        if _ns(c.tag) == name:
            return c
    return None


def _floats(e):
    return np.array([float(x) for x in (e.text or "").split()], float)


def read_dae(path):
    root = ET.parse(path).getroot()
    asset = _child(root, "asset")
    unit, up = 1.0, "Y_UP"
    if asset is not None:
        u = _child(asset, "unit")
        if u is not None and u.get("meter"):
            unit = float(u.get("meter"))
        ua = _child(asset, "up_axis")
        if ua is not None and ua.text:
            up = ua.text.strip()
    ids = {e.get("id"): e for e in root.iter() if e.get("id")}

    def source_array(sid):
        src = ids[sid.lstrip("#")]
        if _ns(src.tag) == "vertices":
            inp = next(i for i in _children(src, "input") if i.get("semantic") == "POSITION")
            return source_array(inp.get("source"))
        fa = _child(src, "float_array")
        acc = _child(_child(src, "technique_common"), "accessor")
        stride = int(acc.get("stride", 3))
        return _floats(fa).reshape(-1, stride)[:, :3]

    def geometry(g):
        mesh = _child(g, "mesh")
        if mesh is None:
            return [], []
        Vs, Fs = [], []
        for kind in ("triangles", "polylist", "polygons"):
            for prim in _children(mesh, kind):
                inputs = _children(prim, "input")
                width = max(int(i.get("offset", 0)) for i in inputs) + 1
                vin = next((i for i in inputs if i.get("semantic") == "VERTEX"), None)
                if vin is None:
                    continue
                P = source_array(vin.get("source"))
                voff = int(vin.get("offset", 0))
                if kind == "polygons":
                    polys = [np.array([int(x) for x in p.text.split()]).reshape(-1, width)[:, voff] for p in _children(prim, "p")]
                else:
                    idx = np.array([int(x) for x in (_child(prim, "p").text or "").split()], np.int64).reshape(-1, width)[:, voff]
                    if kind == "triangles":
                        polys = list(idx.reshape(-1, 3))
                    else:
                        counts = [int(x) for x in _child(prim, "vcount").text.split()]
                        polys, k = [], 0
                        for c in counts:
                            polys.append(idx[k:k + c])
                            k += c
                F = [[p[0], p[j], p[j + 1]] for p in polys for j in range(1, len(p) - 1)]
                Vs.append(P)
                Fs.append(np.array(F, np.int64).reshape(-1, 3))
        return Vs, Fs

    def node_matrix(n):
        M = np.eye(4)
        for t in n:
            tag = _ns(t.tag)
            v = _floats(t) if t.text else None
            T = np.eye(4)
            if tag == "matrix":
                T = v.reshape(4, 4)
            elif tag == "translate":
                T[:3, 3] = v
            elif tag == "scale":
                T[:3, :3] = np.diag(v)
            elif tag == "rotate":
                ax, ang = v[:3] / (np.linalg.norm(v[:3]) or 1), math.radians(v[3])
                K = np.array([[0, -ax[2], ax[1]], [ax[2], 0, -ax[0]], [-ax[1], ax[0], 0]])
                T[:3, :3] = np.eye(3) + math.sin(ang) * K + (1 - math.cos(ang)) * K @ K
            else:
                continue
            M = M @ T
        return M

    Vs, Fs, nodes, base = [], [], 0, 0

    def visit(n, parent):
        nonlocal base, nodes
        M = parent @ node_matrix(n)
        for ig in _children(n, "instance_geometry"):
            g = ids.get(ig.get("url", "").lstrip("#"))
            if g is None:
                continue
            for P, F in zip(*geometry(g)):
                Vs.append(_apply(M, P))
                Fs.append((F[:, [0, 2, 1]] if np.linalg.det(M[:3, :3]) < 0 else F) + base)
                base += len(P)
                nodes += 1
        for c in _children(n, "node"):
            visit(c, M)
        for inode in _children(n, "instance_node"):
            ref = ids.get(inode.get("url", "").lstrip("#"))
            if ref is not None:
                visit(ref, M)

    scenes = [e for e in root.iter() if _ns(e.tag) == "visual_scene"]
    if scenes:
        for n in _children(scenes[0], "node"):
            visit(n, np.eye(4))
    else:
        for g in (e for e in root.iter() if _ns(e.tag) == "geometry"):
            for P, F in zip(*geometry(g)):
                Vs.append(P)
                Fs.append(F + base)
                base += len(P)
                nodes += 1
    return Vs, Fs, nodes, unit, up


# ---- VRML 2 ------------------------------------------------------------------

_TOKEN = re.compile(r"#[^\n]*|\"(?:[^\"\\]|\\.)*\"|[{}\[\],]|[^\s{}\[\],]+")


def read_wrl(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()
    if not text.startswith("#VRML V2") and "IndexedFaceSet" not in text:
        raise FormatError("Not a VRML 2 file (VRML 1 is not read).")
    toks = [t for t in _TOKEN.findall(text) if not t.startswith("#") and t != ","]
    pos = 0
    defs = {}

    def number_list():
        nonlocal pos
        out = []
        if toks[pos] == "[":
            pos += 1
            while toks[pos] != "]":
                out.append(float(toks[pos]))
                pos += 1
            pos += 1
        else:
            out.append(float(toks[pos]))
            pos += 1
        return out

    def skip_value():
        nonlocal pos
        t = toks[pos]
        if t in ("[", "{"):
            close = "]" if t == "[" else "}"
            depth = 0
            while True:
                if toks[pos] in ("[", "{"):
                    depth += 1
                elif toks[pos] in ("]", "}"):
                    depth -= 1
                pos += 1
                if depth == 0:
                    return
            _ = close
        pos += 1

    def node():
        """A node at pos: {type, fields}; fields hold nodes, lists or raw."""
        nonlocal pos
        name = None
        if toks[pos] == "DEF":
            name = toks[pos + 1]
            pos += 2
        if toks[pos] == "USE":
            n = defs.get(toks[pos + 1], {"type": "None", "f": {}})
            pos += 2
            return n
        kind = toks[pos]
        pos += 1
        if toks[pos] != "{":
            return {"type": kind, "f": {}}
        pos += 1
        f = {}
        while toks[pos] != "}":
            key = toks[pos]
            pos += 1
            if key in ("children", "geometry", "coord", "appearance", "material", "texture", "normal", "color", "texCoord", "proto", "choice", "level"):
                if toks[pos] == "[":
                    pos += 1
                    kids = []
                    while toks[pos] != "]":
                        kids.append(node())
                    pos += 1
                    f[key] = kids
                elif toks[pos] == "NULL":
                    pos += 1
                else:
                    f[key] = node()
            elif key in ("point", "coordIndex", "translation", "scale", "rotation", "center", "scaleOrientation"):
                f[key] = number_list() if key in ("point", "coordIndex") else [float(toks[pos + k]) for k in range(4 if key in ("rotation", "scaleOrientation") else 3)]
                if key not in ("point", "coordIndex"):
                    pos += 4 if key in ("rotation", "scaleOrientation") else 3
            elif key in ("ROUTE", "PROTO", "EXTERNPROTO"):
                raise FormatError("VRML with PROTO or ROUTE: export it plainer, or as glTF.")
            else:
                skip_value()
        pos += 1
        n = {"type": kind, "f": f}
        if name:
            defs[name] = n
        return n

    Vs, Fs, count, base = [], [], 0, 0

    def rot(r):
        ax = np.array(r[:3], float)
        ax /= np.linalg.norm(ax) or 1
        K = np.array([[0, -ax[2], ax[1]], [ax[2], 0, -ax[0]], [-ax[1], ax[0], 0]])
        return np.eye(3) + math.sin(r[3]) * K + (1 - math.cos(r[3])) * K @ K

    def visit(n, M):
        nonlocal base, count
        f = n["f"]
        if n["type"] == "Transform":
            T = np.eye(4)
            T[:3, :3] = rot(f.get("rotation", [0, 0, 1, 0])) @ np.diag(f.get("scale", [1, 1, 1]))
            T[:3, 3] = f.get("translation", [0, 0, 0])
            M = M @ T
        if n["type"] == "Shape" and isinstance(f.get("geometry"), dict) and f["geometry"]["type"] == "IndexedFaceSet":
            g = f["geometry"]["f"]
            P = np.array(g.get("coord", {"f": {}})["f"].get("point", []), float).reshape(-1, 3)
            idx = g.get("coordIndex", [])
            polys, cur = [], []
            for x in idx:
                if x < 0:
                    if len(cur) >= 3:
                        polys.append(cur)
                    cur = []
                else:
                    cur.append(int(x))
            if len(cur) >= 3:
                polys.append(cur)
            F = np.array([[p[0], p[j], p[j + 1]] for p in polys for j in range(1, len(p) - 1)], np.int64).reshape(-1, 3)
            if len(P) and len(F):
                Vs.append(_apply(M, P))
                Fs.append(F + base)
                base += len(P)
                count += 1
        kids = f.get("children") or []
        for c in kids if isinstance(kids, list) else [kids]:
            visit(c, M)

    while pos < len(toks):
        if toks[pos] in ("ROUTE",):
            pos += 4
            continue
        try:
            visit(node(), np.eye(4))
        except IndexError:
            break
    return Vs, Fs, count


# ---- 3DS ---------------------------------------------------------------------


def read_3ds(path):
    with open(path, "rb") as f:
        data = f.read()
    if struct.unpack("<H", data[:2])[0] != 0x4D4D:
        raise FormatError("Not a 3DS file.")
    Vs, Fs, count, base = [], [], 0, 0
    pending = {}

    def walk(start, end):
        nonlocal base, count
        off = start
        while off + 6 <= end:
            cid, n = struct.unpack("<HI", data[off:off + 6])
            if n < 6:
                break
            body, stop = off + 6, min(off + n, end)
            if cid in (0x4D4D, 0x3D3D):
                walk(body, stop)
            elif cid == 0x4000:  # an object: a name, then its chunks
                z = data.index(b"\x00", body)
                walk(z + 1, stop)
            elif cid == 0x4100:  # a triangle mesh
                pending.clear()
                walk(body, stop)
                if "P" in pending and "F" in pending:
                    P, F = pending["P"], pending["F"]
                    Vs.append(P)
                    Fs.append(F + base)
                    base += len(P)
                    count += 1
            elif cid == 0x4110:
                k = struct.unpack("<H", data[body:body + 2])[0]
                pending["P"] = np.frombuffer(data, "<f4", 3 * k, body + 2).reshape(-1, 3).astype(float)
            elif cid == 0x4120:
                k = struct.unpack("<H", data[body:body + 2])[0]
                pending["F"] = np.frombuffer(data, "<u2", 4 * k, body + 2).reshape(-1, 4)[:, :3].astype(np.int64)
            off += n

    walk(0, len(data))
    return Vs, Fs, count


# ---- the entry ---------------------------------------------------------------


def read(path, units=None, up=None):
    """The file as one mesh in mm, Z up: (V, F, info)."""
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    if ext not in FORMATS:
        raise FormatError(f"{ext}: one of {', '.join(FORMATS)}.")
    scale_m, file_up = 1000.0, "y"
    if ext in ("glb", "gltf"):
        Vs, Fs, nodes = read_gltf(path)
        taken = "m, Y up (glTF)"
    elif ext == "dae":
        Vs, Fs, nodes, unit, upax = read_dae(path)
        scale_m = unit * 1000.0
        file_up = {"X_UP": "x", "Y_UP": "y", "Z_UP": "z"}.get(upax, "y")
        taken = f"{unit:g} m per unit, {file_up.upper()} up (the file's own)"
    elif ext in ("wrl", "vrml"):
        Vs, Fs, nodes = read_wrl(path)
        taken = "m, Y up (VRML)"
    else:
        Vs, Fs, nodes = read_3ds(path)
        scale_m, file_up = 1.0, "z"
        taken = "mm, Z up (3DS has no units)"
    if not Vs:
        raise FormatError(f"{os.path.basename(path)}: no triangle mesh in it.")
    V = np.concatenate(Vs)
    F = np.concatenate(Fs)
    k = UNITS[units] if units else scale_m
    upax = up or file_up
    V = V * k
    if upax == "y":
        V = y_up_to_z_up(V)
    elif upax == "x":
        V = np.column_stack([V[:, 1], V[:, 2], V[:, 0]])
    if units or up:
        taken = f"{units or 'as the format says'}, {(up or file_up).upper()} up (as given)"
    return V, F, {"format": ext, "nodes": nodes, "takenAs": taken, "triangles": int(len(F))}
