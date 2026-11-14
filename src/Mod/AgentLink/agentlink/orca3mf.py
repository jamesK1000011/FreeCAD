# SPDX-License-Identifier: LGPL-2.1-or-later
# OrcaSlicer's project file (.3mf), read and changed headless: everything the
# slicer's window keeps in a project — the settings at every level (project,
# per filament, per object, per part, per layer range, per plate), the parts
# of each object (modifiers, negative parts, support blockers and enforcers),
# the instances and plates, the per-layer G-code (pauses, colour changes,
# custom), and the painted faces (supports, seam, colour, fuzzy skin) — so the
# command line slices exactly what the window would.
#
# Written from the file format (the 3MF core spec and the Metadata files
# OrcaSlicer writes); the settings are checked against data/orca-options.json,
# which holds each setting's key, type, limits and allowed values only.

import copy
import io
import json
import math
import os
import re
import xml.etree.ElementTree as ET
import zipfile

CORE = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
PROD = "http://schemas.microsoft.com/3dmanufacturing/production/2015/06"
BBS = "http://schemas.bambulab.com/package/2021"
for _p, _u in (("", CORE), ("p", PROD), ("BambuStudio", BBS)):
    ET.register_namespace(_p, _u)

MODEL = "3D/3dmodel.model"
SETTINGS = "Metadata/model_settings.config"
PROJECT = "Metadata/project_settings.config"
RANGES = "Metadata/layer_config_ranges.xml"
GCODES = "Metadata/custom_gcode_per_layer.xml"
SLICE_INFO = "Metadata/slice_info.config"

SUBTYPES = ("normal_part", "modifier_part", "negative_part", "support_blocker", "support_enforcer")
GCODE_TYPES = {"color_change": 0, "pause": 1, "tool_change": 2, "template": 3, "custom": 4}
GCODE_NAMES = {v: k for k, v in GCODE_TYPES.items()}
# Painting, per triangle: the attribute and what each state means.
PAINT = {"supports": ("paint_supports", {"none": 0, "enforce": 1, "block": 2}),
         "seam": ("paint_seam", {"none": 0, "enforce": 1, "block": 2}),
         "fuzzy_skin": ("paint_fuzzy_skin", {"none": 0, "fuzzy": 1}),
         "color": ("paint_color", None)}
# Metadata a part or object carries that is bookkeeping, not a setting.
BOOK = {"name", "matrix", "source_file", "source_object_id", "source_volume_id", "source_offset_x", "source_offset_y", "source_offset_z",
        "mesh_stat", "extruder", "mesh_shared", "volume_type", "source_in_inches", "source_in_meters"}
PLATE_KEYS = {"name": "plater_name", "locked": "locked", "bed_type": "curr_bed_type", "print_sequence": "print_sequence",
              "first_layer_print_sequence": "first_layer_print_sequence", "other_layers_print_sequence": "other_layers_print_sequence",
              "spiral_mode": "spiral_mode", "filament_map_mode": "filament_map_mode", "filament_maps": "filament_maps"}

_OPTIONS = None


class ProjectError(ValueError):
    pass


def options():
    """The settings table: {key: {type, preset, values, min, max, default,
    category, sidetext}} and the Orca version it came from."""
    global _OPTIONS
    if _OPTIONS is None:
        p = os.path.join(os.path.dirname(__file__), "data", "orca-options.json")
        with open(p, encoding="utf-8") as f:
            _OPTIONS = json.load(f)
    return _OPTIONS


def _fmt(x):
    if isinstance(x, bool):
        return "1" if x else "0"
    if isinstance(x, float):
        s = f"{x:.6f}".rstrip("0").rstrip(".")
        return s if s not in ("", "-0") else "0"
    return str(x)


def check(key, value, scope="project"):
    """value made what Orca keeps for key (strings, lists of strings for the
    plural types), or ProjectError naming what is wrong. scope: project,
    object, part, range — the last three take process settings only."""
    table = options()["options"]
    o = table.get(key)
    if not o:
        near = [k for k in table if key.lower() in k][:8]
        raise ProjectError(f"{key}: not an OrcaSlicer setting." + (f" Near: {', '.join(near)}." if near else ""))
    if o.get("readonly"):
        raise ProjectError(f"{key}: read-only (Orca sets it).")
    if scope in ("object", "part", "range") and o.get("preset") != "process":
        raise ProjectError(f"{key} is {('a ' + o['preset']) if o.get('preset') else 'a project'} setting: per object, part or layer range only process settings apply. Set it on the project.")
    t = o["type"]
    plural = t in ("floats", "ints", "strings", "percents", "floats_or_percents", "points", "bools", "enums")

    def one(v):
        base = t[:-1] if plural else t
        if base in ("float", "int", "percent", "float_or_percent"):
            s = str(v).strip()
            pct = s.endswith("%")
            if pct and base in ("float", "int"):
                raise ProjectError(f"{key}: a number, not a percentage.")
            if base == "percent" and not pct:
                s += "%"
                pct = True
            try:
                n = float(s.rstrip("%"))
            except ValueError:
                raise ProjectError(f"{key}: a number{' or percentage' if 'percent' in base else ''}, not {v!r}.")
            if base == "int":
                if n != int(n):
                    raise ProjectError(f"{key}: a whole number, not {v!r}.")
                n = int(n)
            if not pct or base == "percent":
                if "min" in o and n < o["min"]:
                    raise ProjectError(f"{key}: at least {_fmt(o['min'])}, not {_fmt(n)}.")
                if "max" in o and n > o["max"]:
                    raise ProjectError(f"{key}: at most {_fmt(o['max'])}, not {_fmt(n)}.")
            return _fmt(n) + ("%" if pct else "")
        if base == "bool":
            if isinstance(v, bool):
                return "1" if v else "0"
            s = str(v).strip().lower()
            if s in ("1", "true", "yes", "on"):
                return "1"
            if s in ("0", "false", "no", "off"):
                return "0"
            raise ProjectError(f"{key}: true or false, not {v!r}.")
        if base == "enum":
            vals = o.get("values")
            s = str(v)
            if vals and s not in vals:
                raise ProjectError(f"{key}: one of {', '.join(vals)}; not {v!r}.")
            return s
        if base == "point":
            if isinstance(v, (list, tuple)) and len(v) == 2:
                return f"{_fmt(float(v[0]))}x{_fmt(float(v[1]))}"
            if re.match(r"^-?[\d.]+x-?[\d.]+$", str(v)):
                return str(v)
            raise ProjectError(f"{key}: a point, [x, y] or \"XxY\".")
        return str(v)

    if plural:
        if isinstance(value, (list, tuple)) and not (t == "points" and len(value) == 2 and all(isinstance(x, (int, float)) for x in value)):
            return [one(v) for v in value]
        return [one(value)]
    if isinstance(value, (list, tuple)) and t != "point":
        raise ProjectError(f"{key}: one value, not a list.")
    return one(value)


def is_per_filament(key):
    o = options()["options"].get(key) or {}
    return o.get("preset") == "filament" or (o.get("type", "").endswith("s") and key.startswith("filament_"))


# ---- meshes ------------------------------------------------------------------------------

def mesh_xml(vertices, triangles, oid, paint=None):
    """An object's mesh as 3MF XML text (the sub-model files Orca writes)."""
    out = [f'  <object id="{oid}" type="model">\n   <mesh>\n    <vertices>\n']
    out += [f'     <vertex x="{_fmt(float(x))}" y="{_fmt(float(y))}" z="{_fmt(float(z))}"/>\n' for x, y, z in vertices]
    out.append("    </vertices>\n    <triangles>\n")
    for i, (a, b, c) in enumerate(triangles):
        extra = ""
        if paint:
            for attr, states in paint.items():
                if states[i]:
                    extra += f' {attr}="{states[i]}"'
        out.append(f'     <triangle v1="{a}" v2="{b}" v3="{c}"{extra}/>\n')
    out.append("    </triangles>\n   </mesh>\n  </object>\n")
    return "".join(out)


def weld(tris):
    """[[p, p, p], ...] -> (vertices, triangles by index)."""
    idx, verts, faces = {}, [], []
    for t in tris:
        f = []
        for p in t:
            k = (round(p[0], 6), round(p[1], 6), round(p[2], 6))
            if k not in idx:
                idx[k] = len(verts)
                verts.append(k)
            f.append(idx[k])
        if len(set(f)) == 3:
            faces.append(tuple(f))
    return verts, faces


def read_stl(path):
    import struct

    with open(path, "rb") as f:
        data = f.read()
    if data[:5].lower() == b"solid" and b"facet" in data[:400]:
        pts = [tuple(float(x) for x in m.groups()) for m in re.finditer(rb"vertex\s+(\S+)\s+(\S+)\s+(\S+)", data)]
        return [pts[i:i + 3] for i in range(0, len(pts) - 2, 3)]
    n = struct.unpack_from("<I", data, 80)[0]
    tris = []
    for i in range(n):
        v = struct.unpack_from("<12f", data, 84 + 50 * i)
        tris.append([v[3:6], v[6:9], v[9:12]])
    return tris


def read_obj(path):
    """Triangles of an OBJ file (polygons fanned)."""
    vs, tris = [], []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if line.startswith("v "):
                vs.append(tuple(float(x) for x in line.split()[1:4]))
            elif line.startswith("f "):
                idx = [int(x.split("/")[0]) for x in line.split()[1:]]
                idx = [i - 1 if i > 0 else len(vs) + i for i in idx]
                for k in range(1, len(idx) - 1):
                    tris.append([vs[idx[0]], vs[idx[k]], vs[idx[k + 1]]])
    return tris


def write_stl(path, tris):
    import struct

    with open(path, "wb") as f:
        f.write(b"\0" * 80 + struct.pack("<I", len(tris)))
        for t in tris:
            f.write(struct.pack("<12fH", 0, 0, 0, *t[0], *t[1], *t[2], 0))


_VERT = re.compile(rb'<vertex\s+x="([^"]+)"\s+y="([^"]+)"\s+z="([^"]+)"')
_TRI = re.compile(rb"<triangle\s([^>]*?)/?>")
_ATTR = re.compile(rb'(\w+)="([^"]*)"')


def parse_mesh(text, oid):
    """(vertices, triangles, per-triangle attributes {attr: [str]}) of object
    oid in a model file's text."""
    m = re.search(rb'<object[^>]*\sid="' + str(oid).encode() + rb'"[^>]*>(.*?)</object>', text, re.S)
    if not m:
        return None
    body = m.group(1)
    if b"<mesh" not in body:
        return None
    verts = [(float(a), float(b), float(c)) for a, b, c in _VERT.findall(body)]
    tris, attrs = [], {}
    for i, t in enumerate(_TRI.findall(body)):
        d = dict(_ATTR.findall(t))
        tris.append((int(d.pop(b"v1")), int(d.pop(b"v2")), int(d.pop(b"v3"))))
        for k, v in d.items():
            if k.startswith(b"paint_"):
                attrs.setdefault(k.decode(), {})[i] = v.decode()
    return verts, tris, {k: [d.get(i, "") for i in range(len(tris))] for k, d in attrs.items()}


def matrix(text):
    """A 3MF transform "m00 m01 m02 m10 ... m32" -> 4x4 row-major (3MF
    keeps the transpose of the usual column form)."""
    v = [float(x) for x in text.split()] if text else [1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0]
    if len(v) == 16:
        return [v[0:4], v[4:8], v[8:12], v[12:16]]
    return [[v[0], v[3], v[6], v[9]], [v[1], v[4], v[7], v[10]], [v[2], v[5], v[8], v[11]], [0, 0, 0, 1]]


def matrix_text(m):
    return " ".join(_fmt(float(x)) for x in (m[0][0], m[1][0], m[2][0], m[0][1], m[1][1], m[2][1], m[0][2], m[1][2], m[2][2], m[0][3], m[1][3], m[2][3]))


def mul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def apply(m, p):
    return tuple(m[i][0] * p[0] + m[i][1] * p[1] + m[i][2] * p[2] + m[i][3] for i in range(3))


def compose(move=None, rotate=None, scale=None):
    """A transform: scale (number or [sx, sy, sz]), then rotate ([rx, ry, rz]
    degrees about X, then Y, then Z), then move ([x, y, z] mm)."""
    s = scale if isinstance(scale, (list, tuple)) else [scale or 1] * 3
    m = [[s[0], 0, 0, 0], [0, s[1], 0, 0], [0, 0, s[2], 0], [0, 0, 0, 1]]
    for axis, deg in zip("xyz", rotate or (0, 0, 0)):
        if not deg:
            continue
        c, n = math.cos(math.radians(deg)), math.sin(math.radians(deg))
        r = {"x": [[1, 0, 0, 0], [0, c, -n, 0], [0, n, c, 0], [0, 0, 0, 1]],
             "y": [[c, 0, n, 0], [0, 1, 0, 0], [-n, 0, c, 0], [0, 0, 0, 1]],
             "z": [[c, -n, 0, 0], [n, c, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]}[axis]
        m = mul(r, m)
    if move:
        m = mul([[1, 0, 0, move[0]], [0, 1, 0, move[1]], [0, 0, 1, move[2]], [0, 0, 0, 1]], m)
    return m


def bbox(points):
    if not points:
        return None
    xs, ys, zs = zip(*points)
    return [min(xs), min(ys), min(zs), max(xs), max(ys), max(zs)]


# ---- paint states --------------------------------------------------------------------------

def paint_state(n):
    """The whole-triangle state string Orca writes for state n (1..): its
    TriangleSelector serialisation, read backwards as hex nibbles."""
    if n <= 0:
        return ""
    if n < 3:
        return "4" if n == 1 else "8"
    # 0b1100 marks "state >= 3"; the next nibbles carry n - 3.
    rest = n - 3
    if rest < 15:
        return f"{rest:X}C"
    return f"{rest - 15:X}FC"


def paint_value(s):
    """The state of a whole (unsplit) triangle's paint string; None when it
    is split (painted in pieces)."""
    s = s.upper()
    if s in ("", "0"):
        return 0
    if s == "4":
        return 1
    if s == "8":
        return 2
    m = re.match(r"^([0-9A-E])C$", s)
    if m:
        return int(m.group(1), 16) + 3
    m = re.match(r"^([0-9A-F])FC$", s)
    if m:
        return int(m.group(1), 16) + 18
    return None


# ---- the project ---------------------------------------------------------------------------

def _meta(el):
    return {m.get("key"): m.get("value") for m in el.findall("metadata")}


def _set_meta(el, key, value):
    for m in el.findall("metadata"):
        if m.get("key") == key:
            if value is None:
                el.remove(m)
            else:
                m.set("value", str(value))
            return
    if value is not None:
        new = ET.Element("metadata", {"key": key, "value": str(value)})
        # Settings go after the existing metadata, before parts and mesh_stat.
        kids = list(el)
        at = len(kids)
        for i, k in enumerate(kids):
            if k.tag in ("part", "mesh_stat", "model_instance"):
                at = i
                break
        el.insert(at, new)


def _q(tag, ns=CORE):
    return f"{{{ns}}}{tag}"


class Project:
    """An OrcaSlicer project in memory: Project(path) reads it, save(path)
    writes it. Members it does not understand are kept as they were."""

    def __init__(self, path):
        self.path = path
        with zipfile.ZipFile(path) as z:
            self.files = {n: z.read(n) for n in z.namelist()}
        if MODEL not in self.files:
            raise ProjectError(f"{os.path.basename(path)}: no {MODEL} — not a 3MF project.")
        self.model = ET.fromstring(self.files[MODEL])
        self.settings_xml = ET.fromstring(self.files[SETTINGS]) if SETTINGS in self.files else ET.Element("config")
        try:
            self.config = json.loads(self.files.get(PROJECT) or b"{}")
        except ValueError:
            self.config = {}
        self.changed = False
        self._meshes = {}

    # -- reading -----------------------------------------------------------------------------
    def _model_objects(self):
        res = self.model.find(_q("resources"))
        return {o.get("id"): o for o in (res.findall(_q("object")) if res is not None else [])}

    def _build_items(self):
        b = self.model.find(_q("build"))
        return b.findall(_q("item")) if b is not None else []

    def _settings_objects(self):
        return {o.get("id"): o for o in self.settings_xml.findall("object")}

    def objects(self):
        """[{index, id, name, settings, extruder, parts: [...], instances}]
        — index counts from 1 in the order the slicer lists them."""
        mo, so = self._model_objects(), self._settings_objects()
        out = []
        build = self._build_items()
        ids = []
        for it in build:
            if it.get("objectid") not in ids:
                ids.append(it.get("objectid"))
        ids += [i for i in so if i not in ids]
        for n, oid in enumerate(ids, 1):
            meta = _meta(so[oid]) if oid in so else {}
            parts = []
            o = mo.get(oid)
            comps = o.find(_q("components")) if o is not None else None
            sparts = {p.get("id"): p for p in so[oid].findall("part")} if oid in so else {}
            if comps is not None:
                for c in comps.findall(_q("component")):
                    pid = c.get("objectid")
                    sp = sparts.get(pid)
                    pm = _meta(sp) if sp is not None else {}
                    so_ = [pm.get(f"source_offset_{c}") for c in "xyz"]
                    parts.append({"id": pid, "name": pm.get("name"), "kind": (sp.get("subtype") if sp is not None else None) or "normal_part",
                                  **({"sourceOffset": [float(x) for x in so_]} if all(x is not None for x in so_) else {}),
                                  "file": c.get(_q("path", PROD)) or MODEL, "transform": matrix(c.get("transform")),
                                  **({"extruder": pm["extruder"]} if pm.get("extruder") else {}),
                                  "settings": {k: v for k, v in pm.items() if k not in BOOK}})
            elif o is not None:
                parts.append({"id": oid, "name": meta.get("name"), "kind": "normal_part", "file": MODEL, "transform": matrix(None), "settings": {}})
            inst = [{"transform": matrix(it.get("transform")), "printable": it.get("printable", "1") != "0"} for it in build if it.get("objectid") == oid]
            src = next((x for x in (so[oid].findall("part") if oid in so else []) if _meta(x).get("source_file")), None)
            out.append({"index": n, "id": oid, "name": meta.get("name"), **({"source": _meta(src)["source_file"]} if src is not None else {}), **({"extruder": meta["extruder"]} if meta.get("extruder") else {}),
                        "settings": {k: v for k, v in meta.items() if k not in BOOK}, "parts": parts, "instances": inst})
        return out

    def object(self, which):
        """An object by name, index (1..) or id."""
        objs = self.objects()
        for o in objs:
            if str(which) in (o["name"], str(o["index"])) or which == o["index"]:
                return o
        for o in objs:
            if o["name"] and str(which).lower() in o["name"].lower():
                return o
        raise ProjectError(f"object {which!r}: not in the project. Objects: " + ", ".join(f"{o['index']} {o['name']}" for o in objs) + ".")

    def part(self, obj, which=None):
        parts = obj["parts"]
        if which is None:
            if len(parts) == 1:
                return parts[0]
            which = 1
        for i, p in enumerate(parts, 1):
            if str(which) in (p["name"], str(i)) or which == i:
                return p
        raise ProjectError(f"part {which!r} of {obj['name']}: parts are " + ", ".join(f"{i} {p['name']} ({p['kind']})" for i, p in enumerate(parts, 1)) + ".")

    def mesh(self, part):
        """(vertices, triangles, paint attributes) of a part, in its own
        coordinates."""
        key = (part["file"], part["id"])
        if key not in self._meshes:
            text = self.files.get(part["file"].lstrip("/"), b"")
            self._meshes[key] = parse_mesh(text, part["id"])
        return self._meshes[key]

    def world_points(self, obj, part=None, instance=0):
        """The points of an object's parts (or one) placed on the plate."""
        inst = obj["instances"][instance]["transform"] if obj["instances"] and instance is not None else matrix(None)
        pts = []
        for p in ([part] if part else obj["parts"]):
            if p["kind"] != "normal_part" and part is None:
                continue
            m = self.mesh(p)
            if m:
                t = mul(inst, p["transform"])
                pts += [apply(t, v) for v in m[0]]
        return pts

    def plates(self):
        out = []
        for pl in self.settings_xml.findall("plate"):
            pm = _meta(pl)
            insts = [_meta(i) for i in pl.findall("model_instance")]
            out.append({"plate": int(pm.get("plater_id") or len(out) + 1), "settings": {k: v for k, v in pm.items() if k not in ("plater_id", "thumbnail_file", "thumbnail_no_light_file", "top_file", "pick_file", "pattern_bbox_file", "pattern_file")},
                        "objects": [i.get("object_id") for i in insts], "instances": insts})
        return out

    def ranges(self):
        """{object index: [{min, max, settings}]} — the per-layer-range
        settings (Orca's height range modifiers)."""
        if RANGES not in self.files:
            return {}
        root = ET.fromstring(self.files[RANGES])
        out = {}
        for o in root.findall("object"):
            out[int(o.get("id"))] = [{"min": float(r.get("min_z")), "max": float(r.get("max_z")),
                                      "settings": {op.get("opt_key"): op.text or "" for op in r.findall("option")}} for r in o.findall("range")]
        return out

    def gcodes(self):
        """{plate: {mode, items: [{z, type, extruder, color, gcode}]}}."""
        if GCODES not in self.files:
            return {}
        root = ET.fromstring(self.files[GCODES])
        out = {}
        for pl in root.findall("plate"):
            info = pl.find("plate_info")
            n = int(info.get("id")) if info is not None else 1
            mode = pl.find("mode")
            out[n] = {"mode": mode.get("value") if mode is not None else "SingleExtruder",
                      "items": [{"z": float(l.get("top_z")), "type": GCODE_NAMES.get(int(l.get("type", 4)), l.get("type")),
                                 "extruder": int(l.get("extruder", 1)), **({"color": l.get("color")} if l.get("color") else {}),
                                 "gcode": l.get("extra") if int(l.get("type", 4)) == 4 else l.get("gcode", ""),
                                 **({"message": l.get("extra")} if int(l.get("type", 4)) == 1 and l.get("extra") else {})} for l in pl.findall("layer")]}
        return out

    def info(self):
        objs = self.objects()
        for o in objs:
            pts = self.world_points(o)
            o["size"] = None
            if pts:
                b = bbox(pts)
                o["size"] = [round(b[3] - b[0], 3), round(b[4] - b[1], 3), round(b[5] - b[2], 3)]
                o["placed"] = [round(x, 3) for x in b]
            for p in o["parts"]:
                m = self.mesh(p)
                p["triangles"] = len(m[1]) if m else 0
                painted = sorted(k[6:] for k in (m[2] if m else {}) if any(m[2][k]))
                if painted:
                    p["painted"] = painted
                p.pop("transform", None)
                p.pop("file", None)
                if not p["settings"]:
                    p.pop("settings")
            for i in o["instances"]:
                i["at"] = [round(i["transform"][k][3], 3) for k in range(3)]
                i.pop("transform")
            if not o["settings"]:
                o.pop("settings")
        cfg = self.config
        filaments = cfg.get("filament_settings_id") or []
        out = {"objects": objs, "plates": self.plates(), "filaments": [{"slot": i + 1, "preset": f, "type": (cfg.get("filament_type") or [None] * (i + 1))[i] if i < len(cfg.get("filament_type") or []) else None,
                                                                         "colour": (cfg.get("filament_colour") or [None] * (i + 1))[i] if i < len(cfg.get("filament_colour") or []) else None} for i, f in enumerate(filaments)],
               "presets": {"machine": cfg.get("printer_settings_id"), "process": cfg.get("print_settings_id")}}
        r = self.ranges()
        if r:
            out["ranges"] = r
        g = self.gcodes()
        if g:
            out["gcodes"] = g
        out["sliced"] = any(re.match(r"Metadata/plate_\d+\.gcode$", n) for n in self.files)
        return out

    # -- changing ----------------------------------------------------------------------------
    def _dirty(self):
        self.changed = True

    def set(self, key, value, slot=None):
        """A project setting; slot (1..) for one filament's or extruder's
        value of a per-filament setting."""
        v = check(key, value, "project")
        if isinstance(v, list) and slot:
            cur = self.config.get(key)
            cur = list(cur) if isinstance(cur, list) else ([cur] if cur is not None else [])
            n = max(len(cur), slot, len(self.config.get("filament_settings_id") or []) if is_per_filament(key) else 0)
            cur += [cur[-1] if cur else v[0]] * (n - len(cur))
            cur[slot - 1] = v[0]
            v = cur
        elif isinstance(v, list) and is_per_filament(key) and len(v) == 1:
            # One value for every filament.
            v = v * max(1, len(self.config.get("filament_settings_id") or []))
        old = self.config.get(key)
        self.config[key] = v
        # The window marks a setting changed from its preset in
        # different_settings_to_system; keep it honest so the window shows it.
        self._mark_different(key)
        self._dirty()
        return old, v

    def _mark_different(self, key):
        d = self.config.get("different_settings_to_system")
        if not isinstance(d, list) or len(d) < 2:
            return
        o = options()["options"].get(key) or {}
        i = {"process": 0, "machine": len(d) - 1}.get(o.get("preset"))
        idxs = [i] if i is not None else list(range(1, len(d) - 1))
        for i in idxs:
            have = [x for x in d[i].split(";") if x]
            if key not in have:
                d[i] = ";".join(have + [key])

    def _so(self, obj):
        so = self._settings_objects()
        if obj["id"] not in so:
            raise ProjectError(f"{obj['name']}: no settings entry in the project (not written by OrcaSlicer).")
        return so[obj["id"]]

    def set_object(self, which, key, value):
        o = self.object(which)
        el = self._so(o)
        if key in ("extruder", "filament"):
            _set_meta(el, "extruder", str(int(value)))
        elif key == "name":
            _set_meta(el, "name", str(value))
        else:
            _set_meta(el, key, None if value is None else check(key, value, "object"))
        self._dirty()
        return o["name"]

    def set_part(self, which, part, key, value):
        o = self.object(which)
        p = self.part(o, part)
        el = next(x for x in self._so(o).findall("part") if x.get("id") == p["id"])
        if key in ("extruder", "filament"):
            _set_meta(el, "extruder", str(int(value)))
        elif key == "name":
            _set_meta(el, "name", str(value))
        elif key == "kind":
            if value not in SUBTYPES:
                raise ProjectError(f"kind: one of {', '.join(SUBTYPES)}.")
            el.set("subtype", value)
        else:
            _set_meta(el, key, None if value is None else check(key, value, "part"))
        self._dirty()
        return p["name"]

    def _next_id(self):
        ids = [int(i) for i in self._model_objects()]
        for f, data in self.files.items():
            if f.startswith("3D/Objects/"):
                ids += [int(x) for x in re.findall(rb'<object[^>]*\sid="(\d+)"', data)]
        return max(ids + [0]) + 1

    def add_part(self, which, tris, kind="modifier_part", name=None, settings=None, transform=None):
        """A part added to an object from triangles in the object's own
        coordinates (a modifier, negative part, support blocker or enforcer,
        or another normal part); settings apply to it (modifiers)."""
        if kind not in SUBTYPES:
            raise ProjectError(f"kind: one of {', '.join(SUBTYPES)}.")
        o = self.object(which)
        mo = self._model_objects()[o["id"]]
        comps = mo.find(_q("components"))
        if comps is None:
            raise ProjectError(f"{o['name']}: a single mesh, not parts — save it from OrcaSlicer (or orca_project new) first.")
        verts, faces = weld(tris)
        if not faces:
            raise ProjectError("the part's mesh has no triangles.")
        pid = self._next_id()
        fname = f"3D/Objects/agentlink_part_{pid}.model"
        self.files[fname] = (f'<?xml version="1.0" encoding="UTF-8"?>\n<model unit="millimeter" xml:lang="en-US" xmlns="{CORE}" xmlns:p="{PROD}" requiredextensions="p">\n <resources>\n'
                             + mesh_xml(verts, faces, pid) + " </resources>\n <build/>\n</model>\n").encode()
        tr = transform or matrix(None)
        ET.SubElement(comps, _q("component"), {_q("path", PROD): "/" + fname, "objectid": str(pid), "transform": matrix_text(tr)})
        el = ET.SubElement(self._so(o), "part", {"id": str(pid), "subtype": kind})
        _set_meta(el, "name", name or f"{kind.split('_')[0]} {pid}")
        _set_meta(el, "matrix", " ".join(_fmt(float(x)) for row in tr for x in row))
        for k, v in (settings or {}).items():
            _set_meta(el, k, check(k, v, "part"))
        # mesh_stat after the metadata, as Orca writes it.
        ET.SubElement(el, "mesh_stat", {"edges_fixed": "0", "degenerate_facets": "0", "facets_removed": "0", "facets_reversed": "0", "backwards_edges": "0"})
        self._rels_add(fname)
        self._dirty()
        return {"object": o["name"], "part": el.find("metadata").get("value"), "id": str(pid), "triangles": len(faces)}

    def _rels_add(self, fname):
        rel = "3D/_rels/3dmodel.model.rels"
        if rel not in self.files:
            return
        text = self.files[rel].decode()
        if f'Target="/{fname}"' in text:
            return
        n = len(re.findall(r"<Relationship ", text)) + 1
        text = text.replace("</Relationships>", f' <Relationship Target="/{fname}" Id="rel-agentlink-{n}" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>\n</Relationships>')
        self.files[rel] = text.encode()

    def remove_part(self, which, part):
        o = self.object(which)
        p = self.part(o, part)
        if p["kind"] == "normal_part" and sum(1 for x in o["parts"] if x["kind"] == "normal_part") == 1:
            raise ProjectError(f"{p['name']} is {o['name']}'s only solid part: remove the object instead.")
        comps = self._model_objects()[o["id"]].find(_q("components"))
        for c in comps.findall(_q("component")):
            if c.get("objectid") == p["id"]:
                comps.remove(c)
        so = self._so(o)
        for x in so.findall("part"):
            if x.get("id") == p["id"]:
                so.remove(x)
        self._dirty()
        return p["name"]

    def replace_mesh(self, which, part, tris, keep_paint=False, raw=False):
        """A part's mesh replaced (the FreeCAD body changed): triangles in the
        object's coordinates; the part's settings, kind and place stay. Paint
        is dropped unless keep_paint (then repainted by the caller)."""
        o = self.object(which)
        p = self.part(o, part)
        verts, faces = weld(tris)
        if not faces:
            raise ProjectError("the new mesh has no triangles.")
        if not raw:
            inv = invert(p["transform"])
            verts = [apply(inv, v) for v in verts]
        f = p["file"].lstrip("/")
        text = self.files[f]
        new = mesh_xml(verts, faces, p["id"]).encode()
        pat = re.compile(rb'\s*<object[^>]*\sid="' + p["id"].encode() + rb'"[^>]*>.*?</object>\s*\n?', re.S)
        if not pat.search(text):
            raise ProjectError(f"{p['name']}: its mesh is not in {f}.")
        self.files[f] = pat.sub(lambda m: b"\n" + new, text, count=1)
        self._meshes.pop((p["file"], p["id"]), None)
        # Orca keeps the source mesh's stats; new mesh, nothing repaired.
        for x in self._so(o).findall("part"):
            if x.get("id") == p["id"]:
                ms = x.find("mesh_stat")
                if ms is not None:
                    for k in list(ms.attrib):
                        ms.set(k, "0")
        self._dirty()
        return {"object": o["name"], "part": p["name"], "triangles": len(faces)}

    def paint(self, which, part, kind, states):
        """Paint a part's triangles: states a list (one per triangle) of the
        PAINT kind's state names (supports/seam: none, enforce, block;
        fuzzy_skin: none, fuzzy; color: a filament slot 1.. or 0)."""
        if kind not in PAINT:
            raise ProjectError(f"paint: one of {', '.join(PAINT)}.")
        attr, names = PAINT[kind]
        o = self.object(which)
        p = self.part(o, part)
        verts, tris, attrs = self.mesh(p)
        if len(states) != len(tris):
            raise ProjectError(f"{len(states)} states for {len(tris)} triangles.")
        codes = []
        for s in states:
            n = int(s) if names is None else names.get(str(s))
            if n is None:
                raise ProjectError(f"{kind}: {', '.join(names)}, not {s!r}.")
            codes.append(paint_state(n))
        attrs = dict(attrs)
        attrs[attr] = codes
        f = p["file"].lstrip("/")
        new = mesh_xml(verts, tris, p["id"], {k: v for k, v in attrs.items() if any(v)}).encode()
        pat = re.compile(rb'\s*<object[^>]*\sid="' + p["id"].encode() + rb'"[^>]*>.*?</object>\s*\n?', re.S)
        self.files[f] = pat.sub(lambda m: b"\n" + new, self.files[f], count=1)
        self._meshes.pop((p["file"], p["id"]), None)
        self._dirty()
        return sum(1 for c in codes if c)

    def place(self, which, instance=0, move=None, rotate=None, scale=None, at=None, drop=True):
        """An instance moved, rotated or scaled (about its own origin), or put
        at [x, y] on the plate; drop sets it on the bed (Orca does that too
        when it slices)."""
        o = self.object(which)
        items = [it for it in self._build_items() if it.get("objectid") == o["id"]]
        if instance >= len(items):
            raise ProjectError(f"{o['name']} has {len(items)} instance(s).")
        it = items[instance]
        m = matrix(it.get("transform"))
        if scale or rotate:
            t = [m[0][3], m[1][3], m[2][3]]
            m = mul(compose(rotate=rotate, scale=scale), [[m[i][j] if j < 3 else 0 for j in range(4)] for i in range(3)] + [[0, 0, 0, 1]])
            for i in range(3):
                m[i][3] = t[i]
        if move:
            for i in range(3):
                m[i][3] += move[i] if i < len(move) else 0
        it.set("transform", matrix_text(m))
        pts = self.world_points(self.object(o["index"]), instance=instance)
        b = bbox(pts)
        if b and at:
            cx, cy = (b[0] + b[3]) / 2, (b[1] + b[4]) / 2
            m[0][3] += at[0] - cx
            m[1][3] += at[1] - cy
        if b and drop:
            m[2][3] -= b[2]
        it.set("transform", matrix_text(m))
        self._dirty()
        b = bbox(self.world_points(self.object(o["index"]), instance=instance))
        return {"object": o["name"], "placed": [round(x, 3) for x in b] if b else None}

    def lay_flat(self, which, normal, instance=0):
        """Turn an instance so the face with outward normal (object
        coordinates) lies on the bed, as the window's Lay on face does."""
        o = self.object(which)
        n = [float(x) for x in normal]
        ln = math.sqrt(sum(x * x for x in n))
        if ln < 1e-9:
            raise ProjectError("normal: a direction, not zero.")
        n = [x / ln for x in n]
        items = [it for it in self._build_items() if it.get("objectid") == o["id"]]
        it = items[instance]
        m = matrix(it.get("transform"))
        # The normal as the instance turns it now.
        cur = [sum(m[i][j] * n[j] for j in range(3)) for i in range(3)]
        tgt = [0.0, 0.0, -1.0]
        axis = [cur[1] * tgt[2] - cur[2] * tgt[1], cur[2] * tgt[0] - cur[0] * tgt[2], cur[0] * tgt[1] - cur[1] * tgt[0]]
        s_ = math.sqrt(sum(x * x for x in axis))
        c = sum(cur[i] * tgt[i] for i in range(3))
        if s_ < 1e-9:
            r = [[1, 0, 0], [0, 1, 0], [0, 0, 1]] if c > 0 else [[1, 0, 0], [0, -1, 0], [0, 0, -1]]
        else:
            k = [x / s_ for x in axis]
            K = [[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]]
            K2 = [[sum(K[i][l] * K[l][j] for l in range(3)) for j in range(3)] for i in range(3)]
            r = [[(1 if i == j else 0) + s_ * K[i][j] + (1 - c) * K2[i][j] for j in range(3)] for i in range(3)]
        rot = [r[0] + [0], r[1] + [0], r[2] + [0], [0, 0, 0, 1]]
        t = [m[0][3], m[1][3], m[2][3]]
        lin = [[m[i][j] if j < 3 else 0 for j in range(4)] for i in range(3)] + [[0, 0, 0, 1]]
        m = mul(rot, lin)
        for i in range(3):
            m[i][3] = t[i]
        it.set("transform", matrix_text(m))
        self._dirty()
        return self.place(o["index"], instance)

    def set_range(self, which, zmin, zmax, settings):
        """Settings for an object's layers from zmin to zmax (mm); settings
        {} removes that range."""
        o = self.object(which)
        root = ET.fromstring(self.files[RANGES]) if RANGES in self.files else ET.Element("objects")
        el = next((x for x in root.findall("object") if x.get("id") == str(o["index"])), None)
        if el is None:
            el = ET.SubElement(root, "object", {"id": str(o["index"])})
        r = next((x for x in el.findall("range") if abs(float(x.get("min_z")) - zmin) < 1e-6 and abs(float(x.get("max_z")) - zmax) < 1e-6), None)
        if not settings:
            if r is not None:
                el.remove(r)
            if not el.findall("range"):
                root.remove(el)
        else:
            if r is None:
                for x in el.findall("range"):
                    a, b = float(x.get("min_z")), float(x.get("max_z"))
                    if a < zmax - 1e-6 and zmin < b - 1e-6:
                        raise ProjectError(f"{_fmt(zmin)}–{_fmt(zmax)} mm overlaps the range {x.get('min_z')}–{x.get('max_z')}.")
                r = ET.SubElement(el, "range", {"min_z": _fmt(zmin), "max_z": _fmt(zmax)})
                ET.SubElement(r, "option", {"opt_key": "extruder"}).text = "0"
                ET.SubElement(r, "option", {"opt_key": "layer_height"}).text = _fmt(float((self.config.get("layer_height") or 0.2)))
            for k, v in settings.items():
                val = check(k, v, "range")
                op = next((x for x in r.findall("option") if x.get("opt_key") == k), None)
                if op is None:
                    op = ET.SubElement(r, "option", {"opt_key": k})
                op.text = val if not isinstance(val, list) else ",".join(val)
        if len(root):
            self.files[RANGES] = _xml(root)
        else:
            self.files.pop(RANGES, None)
        self._dirty()
        return self.ranges().get(o["index"], [])

    def set_gcode(self, z, kind, plate=1, gcode=None, extruder=1, color=None, remove=False):
        """G-code at the layer whose top is z (mm) on a plate: kind pause
        (gcode: the message shown), color_change, tool_change, template,
        custom (gcode: the lines); remove takes the one at z away."""
        if kind not in GCODE_TYPES and not remove:
            raise ProjectError(f"kind: one of {', '.join(GCODE_TYPES)}.")
        if kind == "custom" and not gcode and not remove:
            raise ProjectError("custom: gcode (the lines to run there).")
        root = ET.fromstring(self.files[GCODES]) if GCODES in self.files else ET.Element("custom_gcodes_per_layer")
        pl = next((x for x in root.findall("plate") if (x.find("plate_info") is not None and x.find("plate_info").get("id") == str(plate))), None)
        if pl is None:
            pl = ET.SubElement(root, "plate")
            ET.SubElement(pl, "plate_info", {"id": str(plate)})
            ET.SubElement(pl, "mode", {"value": "SingleExtruder" if len(self.config.get("filament_settings_id") or [1]) < 2 else "MultiAsSingle"})
        for l in pl.findall("layer"):
            if abs(float(l.get("top_z")) - z) < 1e-4 and (remove or GCODE_NAMES.get(int(l.get("type"))) == kind):
                pl.remove(l)
        if not remove:
            colours = self.config.get("filament_colour") or ["#FFFFFF"]
            attrs = {"top_z": _fmt(float(z)), "type": str(GCODE_TYPES[kind]), "extruder": str(int(extruder)),
                     "color": color or (colours[int(extruder) - 1] if kind in ("color_change", "tool_change") and int(extruder) <= len(colours) else ""),
                     # Orca reads the custom lines (and a pause's message) from extra.
                     "extra": gcode or "", "gcode": gcode if kind == "custom" else {"pause": str(self.config.get("machine_pause_gcode") or "M601"), "color_change": "M600", "tool_change": "tool_change",
                                                                                     "template": str(self.config.get("template_custom_gcode") or "")}.get(kind, "")}
            layer = ET.Element("layer", attrs)
            mode = pl.find("mode")
            pl.insert(list(pl).index(mode) if mode is not None else len(pl), layer)
            ls = sorted(pl.findall("layer"), key=lambda e: float(e.get("top_z")))
            for l in pl.findall("layer"):
                pl.remove(l)
            at = list(pl).index(pl.find("mode")) if pl.find("mode") is not None else len(pl)
            for i, l in enumerate(ls):
                pl.insert(at + i, l)
        self.files[GCODES] = _xml(root)
        self._dirty()
        return self.gcodes().get(plate, {"items": []})["items"]

    def set_plate(self, plate, key, value):
        el = next((p for p in self.settings_xml.findall("plate") if _meta(p).get("plater_id") == str(plate)), None)
        if el is None:
            raise ProjectError(f"plate {plate}: plates are {', '.join(str(p['plate']) for p in self.plates())}.")
        if key not in PLATE_KEYS:
            raise ProjectError(f"plate settings: {', '.join(PLATE_KEYS)}.")
        k = PLATE_KEYS[key]
        if key == "bed_type" and value is not None:
            ok = options()["options"].get("curr_bed_type", {}).get("values") or []
            if ok and value not in ok:
                raise ProjectError(f"bed_type: one of {', '.join(ok)}.")
        if key == "locked":
            value = "true" if value in (True, "1", "true") else "false"
        if isinstance(value, list):
            value = ",".join(str(x) for x in value)
        _set_meta(el, k, value)
        self._dirty()
        return self.plates()

    def move_to_plate(self, which, plate, instance=0):
        """An object's instance put on another plate (the plate made when it
        is the next one); place it after with place(at=)."""
        o = self.object(which)
        plates = self.settings_xml.findall("plate")
        target = next((p for p in plates if _meta(p).get("plater_id") == str(plate)), None)
        if target is None:
            if plate != len(plates) + 1:
                raise ProjectError(f"plate {plate}: there are {len(plates)}; the next new one is {len(plates) + 1}.")
            target = copy.deepcopy(plates[-1]) if plates else ET.Element("plate")
            for x in list(target):
                if x.tag == "model_instance" or (x.tag == "metadata" and x.get("key") in ("thumbnail_file", "thumbnail_no_light_file", "top_file", "pick_file", "pattern_bbox_file", "plater_name")):
                    target.remove(x)
            _set_meta(target, "plater_id", str(plate))
            at = list(self.settings_xml).index(plates[-1]) + 1 if plates else len(self.settings_xml)
            self.settings_xml.insert(at, target)
        n_before = len(plates)
        n_after = len(self.settings_xml.findall("plate"))
        if n_after != n_before:
            self._relayout(n_before, n_after, {i: i for i in range(1, n_before + 1)})
        moved, came_from = None, None
        for p in plates:
            for mi in p.findall("model_instance"):
                m = _meta(mi)
                if m.get("object_id") == o["id"] and m.get("instance_id", "0") == str(instance):
                    p.remove(mi)
                    moved, came_from = mi, int(_meta(p).get("plater_id"))
        if moved is None:
            moved = ET.Element("model_instance")
            _set_meta(moved, "object_id", o["id"])
            _set_meta(moved, "instance_id", str(instance))
            _set_meta(moved, "identify_id", str(self._next_id() + 1000))
        target.append(moved)
        if came_from and came_from != plate:
            a0, b0 = self.plate_origin(came_from), self.plate_origin(plate)
            it = [x for x in self._build_items() if x.get("objectid") == o["id"]][instance]
            m = matrix(it.get("transform"))
            m[0][3] += b0[0] - a0[0]
            m[1][3] += b0[1] - a0[1]
            it.set("transform", matrix_text(m))
        self._dirty()
        return self.plates()

    def _relayout(self, n_old, n_new, slot):
        """Plates' objects moved when the plate count changes the columns,
        or plates change places: slot maps each plate's old number to its
        new one (numbers as they will stand)."""
        moves = {}
        for old, new in slot.items():
            a0, b0 = self.plate_origin(old, n_old), self.plate_origin(new, n_new)
            moves[new] = (b0[0] - a0[0], b0[1] - a0[1])
        self._shift_plates(moves)

    def remove_object(self, which):
        o = self.object(which)
        res = self.model.find(_q("resources"))
        res.remove(self._model_objects()[o["id"]])
        b = self.model.find(_q("build"))
        for it in list(b):
            if it.get("objectid") == o["id"]:
                b.remove(it)
        self.settings_xml.remove(self._so(o))
        for p in self.settings_xml.findall("plate"):
            for mi in p.findall("model_instance"):
                if _meta(mi).get("object_id") == o["id"]:
                    p.remove(mi)
        # Layer ranges count objects by place: shift those after it.
        if RANGES in self.files:
            root = ET.fromstring(self.files[RANGES])
            for x in list(root.findall("object")):
                i = int(x.get("id"))
                if i == o["index"]:
                    root.remove(x)
                elif i > o["index"]:
                    x.set("id", str(i - 1))
            self.files[RANGES] = _xml(root)
        self._dirty()
        return o["name"]

    # -- objects, instances, layer heights, brim ears ------------------------------------------
    def add_object(self, tris, name, plate=1, at=None, extruder=1):
        """A new object of one part from triangles (its source coordinates,
        as an STL of the body would have them), put on a plate."""
        verts, faces = weld(tris)
        if not faces:
            raise ProjectError("the object's mesh has no triangles.")
        b = bbox(verts)
        c = [(b[0] + b[3]) / 2, (b[1] + b[4]) / 2, (b[2] + b[5]) / 2]
        pid = self._next_id()
        oid = pid + 1
        safe = re.sub(r"[^\w.-]", "_", name)
        fname = f"3D/Objects/{safe}_{pid}.model"
        self.files[fname] = (f'<?xml version="1.0" encoding="UTF-8"?>\n<model unit="millimeter" xml:lang="en-US" xmlns="{CORE}" xmlns:p="{PROD}" requiredextensions="p">\n <resources>\n'
                             + mesh_xml([(x - c[0], y - c[1], z - c[2]) for x, y, z in verts], faces, pid) + " </resources>\n <build/>\n</model>\n").encode()
        self._rels_add(fname)
        comp = [[1, 0, 0, c[0]], [0, 1, 0, c[1]], [0, 0, 1, c[2]], [0, 0, 0, 1]]
        res = self.model.find(_q("resources"))
        o = ET.SubElement(res, _q("object"), {"id": str(oid), "type": "model"})
        comps = ET.SubElement(o, _q("components"))
        ET.SubElement(comps, _q("component"), {_q("path", PROD): "/" + fname, "objectid": str(pid), "transform": matrix_text(comp)})
        build = self.model.find(_q("build"))
        if build is None:
            build = ET.SubElement(self.model, _q("build"))
        ET.SubElement(build, _q("item"), {"objectid": str(oid), "transform": matrix_text([[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]), "printable": "1"})
        so = ET.Element("object", {"id": str(oid)})
        _set_meta(so, "name", name)
        _set_meta(so, "extruder", str(int(extruder)))
        part = ET.SubElement(so, "part", {"id": str(pid), "subtype": "normal_part"})
        for k, v in (("name", name), ("matrix", " ".join(_fmt(float(x)) for row in comp for x in row)), ("source_file", name),
                     ("source_object_id", "0"), ("source_volume_id", "0"), ("source_offset_x", _fmt(c[0])), ("source_offset_y", _fmt(c[1])), ("source_offset_z", _fmt(c[2]))):
            _set_meta(part, k, v)
        ET.SubElement(part, "mesh_stat", {"edges_fixed": "0", "degenerate_facets": "0", "facets_removed": "0", "facets_reversed": "0", "backwards_edges": "0"})
        objs = self.settings_xml.findall("object")
        at_ = list(self.settings_xml).index(objs[-1]) + 1 if objs else 0
        self.settings_xml.insert(at_, so)
        self._dirty()
        self.move_to_plate(self.objects()[-1]["index"], plate)
        o_ = self.objects()[-1]
        bed = self.bed(plate)
        where = [a + b for a, b in zip(at, self.plate_origin(plate))] if at else ([(bed[0] + bed[2]) / 2, (bed[1] + bed[3]) / 2] if bed else [0, 0])
        r = self.place(o_["index"], at=where)
        return {"object": name, "index": o_["index"], "placed": r["placed"]}

    def bed(self, plate=1):
        """[xmin, ymin, xmax, ymax] of a plate's bed (the printable area, at
        the place the plate has in the project's world, as Orca lays plates
        out side by side)."""
        b = self._bed0()
        if not b:
            return None
        ox, oy = self.plate_origin(plate)
        return [b[0] + ox, b[1] + oy, b[2] + ox, b[3] + oy]

    def plate_origin(self, plate, count=None):
        """Where plate (1..) sits: Orca's plates in rows of ceil-ish
        sqrt(count) columns, each a bed and a fifth apart."""
        b = self._bed0()
        if not b:
            return (0.0, 0.0)
        n = count or max(1, len(self.settings_xml.findall("plate")))
        r = math.sqrt(n)
        cols = int(round(r)) + (1 if r > round(r) else 0)
        i = plate - 1
        w, d = b[2] - b[0], b[3] - b[1]
        return ((i % cols) * w * 1.2, -(i // cols) * d * 1.2)

    def plate_of(self, which, instance=0):
        o = self.object(which)
        for p in self.plates():
            for mi in p["instances"]:
                if mi.get("object_id") == o["id"] and mi.get("instance_id", "0") == str(instance):
                    return p["plate"]
        return 1

    def _shift_plates(self, moves):
        """Move every instance on plate k by moves[k] = (dx, dy)."""
        on = {}
        for p in self.plates():
            for mi in p["instances"]:
                on[(mi.get("object_id"), int(mi.get("instance_id", "0")))] = p["plate"]
        counters = {}
        for it in self._build_items():
            oid = it.get("objectid")
            k = counters.get(oid, 0)
            counters[oid] = k + 1
            d = moves.get(on.get((oid, k)))
            if d and (d[0] or d[1]):
                m = matrix(it.get("transform"))
                m[0][3] += d[0]
                m[1][3] += d[1]
                it.set("transform", matrix_text(m))

    def _bed0(self):
        area = self.config.get("printable_area") or []
        pts = []
        for p in area:
            try:
                x, y = p.split("x")
                pts.append((float(x), float(y)))
            except ValueError:
                pass
        if not pts:
            return None
        xs, ys = zip(*pts)
        return [min(xs), min(ys), max(xs), max(ys)]

    def add_instance(self, which, offset=None):
        """Another copy of an object, beside the last (offset [dx, dy] mm,
        default its width plus 5 mm along X)."""
        o = self.object(which)
        items = [it for it in self._build_items() if it.get("objectid") == o["id"]]
        last = items[-1]
        m = matrix(last.get("transform"))
        b = bbox(self.world_points(o, instance=len(items) - 1)) or [0, 0, 0, 0, 0, 0]
        d = offset or [b[3] - b[0] + 5, 0]
        m[0][3] += d[0]
        m[1][3] += d[1] if len(d) > 1 else 0
        build = self.model.find(_q("build"))
        new = ET.Element(_q("item"), {"objectid": o["id"], "transform": matrix_text(m), "printable": last.get("printable", "1")})
        build.insert(list(build).index(last) + 1, new)
        # The plate the first copy is on gets this one too.
        for p in self.settings_xml.findall("plate"):
            if any(_meta(mi).get("object_id") == o["id"] for mi in p.findall("model_instance")):
                mi = ET.SubElement(p, "model_instance")
                _set_meta(mi, "object_id", o["id"])
                _set_meta(mi, "instance_id", str(len(items)))
                _set_meta(mi, "identify_id", str(self._next_id() + 2000 + len(items)))
                break
        self._dirty()
        return {"object": o["name"], "instances": len(items) + 1}

    def remove_instance(self, which, instance):
        o = self.object(which)
        items = [it for it in self._build_items() if it.get("objectid") == o["id"]]
        if len(items) < 2:
            raise ProjectError(f"{o['name']} has one copy: remove the object instead.")
        if not 0 <= instance < len(items):
            raise ProjectError(f"instance: 0..{len(items) - 1}.")
        self.model.find(_q("build")).remove(items[instance])
        for p in self.settings_xml.findall("plate"):
            for mi in p.findall("model_instance"):
                m = _meta(mi)
                if m.get("object_id") == o["id"]:
                    i = int(m.get("instance_id", "0"))
                    if i == instance:
                        p.remove(mi)
                    elif i > instance:
                        _set_meta(mi, "instance_id", str(i - 1))
        self._dirty()
        return {"object": o["name"], "instances": len(items) - 1}

    def set_printable(self, which, printable, instance=None):
        o = self.object(which)
        for i, it in enumerate(x for x in self._build_items() if x.get("objectid") == o["id"]):
            if instance is None or i == instance:
                it.set("printable", "1" if printable else "0")
        self._dirty()
        return {"object": o["name"], "printable": bool(printable)}

    def layer_heights(self):
        """{object index: [[z, layer height], ...]} — variable layer height."""
        t = self.files.get("Metadata/layer_heights_profile.txt", b"").decode()
        out = {}
        for line in t.splitlines():
            if "|" in line and line.startswith("object_id="):
                head, vals = line.split("|", 1)
                v = [float(x) for x in vals.split(";") if x]
                out[int(head.split("=")[1])] = [[v[i], v[i + 1]] for i in range(0, len(v) - 1, 2)]
        return out

    def set_layer_heights(self, which, profile):
        """An object's layer heights along Z: [[z, height], ...] (at least
        three points, z rising from 0); [] goes back to the even height."""
        o = self.object(which)
        cur = self.layer_heights()
        if profile:
            pts = sorted((float(z), float(h)) for z, h in profile)
            if len(pts) < 3:
                raise ProjectError("layer heights: at least three [z, height] points.")
            lo, hi = 0.04, 0.6
            for k in ("min_layer_height", "max_layer_height"):
                v = self.config.get(k)
                if isinstance(v, list) and v:
                    v = v[0]
                if v is not None:
                    if k.startswith("min"):
                        lo = float(v)
                    else:
                        hi = float(v)
            if hi <= lo:  # 0: not set — the nozzle decides
                nz = self.config.get("nozzle_diameter") or ["0.4"]
                hi = 0.75 * float(nz[0] if isinstance(nz, list) else nz)
            for z, h in pts:
                if not lo - 1e-9 <= h <= hi + 1e-9:
                    raise ProjectError(f"layer height {_fmt(h)} at z {_fmt(z)}: the printer takes {_fmt(lo)} to {_fmt(hi)} mm.")
            cur[o["index"]] = [list(p) for p in pts]
        else:
            cur.pop(o["index"], None)
        text = "".join(f"object_id={i}|" + ";".join(f"{v:f}" for p in prof for v in p) + "\n" for i, prof in sorted(cur.items()))
        if text:
            self.files["Metadata/layer_heights_profile.txt"] = text.encode()
        else:
            self.files.pop("Metadata/layer_heights_profile.txt", None)
        self._dirty()
        return cur.get(o["index"], [])

    def brim_ears(self):
        t = self.files.get("Metadata/brim_ear_points.txt", b"").decode()
        out = {}
        for line in t.splitlines():
            if line.startswith("object_id=") and "|" in line:
                head, vals = line.split("|", 1)
                v = [float(x) for x in vals.split()]
                out[int(head.split("=")[1])] = [v[i:i + 4] for i in range(0, len(v) - 3, 4)]
        return out

    def set_brim_ears(self, which, points, radius=None):
        """Brim ears at points [[x, y] or [x, y, z, r]] on the object's
        bottom (object coordinates); brim_type brim_ears turns them on for
        it. [] removes them."""
        o = self.object(which)
        cur = self.brim_ears()
        if points:
            b = bbox(self.world_points(o, instance=None))
            r = float(radius or 5)
            cur[o["index"]] = [[float(q[0]), float(q[1]), float(q[2]) if len(q) > 2 else (b[2] if b else 0.0), float(q[3]) if len(q) > 3 else r] for q in points]
            self.set_object(o["index"], "brim_type", "brim_ears")
        else:
            cur.pop(o["index"], None)
        text = "".join(f"object_id={i}|" + " ".join(f"{v:f}" for q in pts for v in q) + "\n" for i, pts in sorted(cur.items()))
        if text:
            self.files["Metadata/brim_ear_points.txt"] = ("brim_points_format_version=0\n" + text).encode()
        else:
            self.files.pop("Metadata/brim_ear_points.txt", None)
        self._dirty()
        return cur.get(o["index"], [])

    # -- filaments -------------------------------------------------------------------------
    def filament_keys(self):
        """The project's per-filament lists (each as long as the slots)."""
        n = len(self.config.get("filament_settings_id") or [])
        t = options()["options"]
        return [k for k, v in self.config.items() if isinstance(v, list) and len(v) == n and (t.get(k, {}).get("preset") == "filament" or k.startswith("filament_") or k in ("nozzle_temperature", "nozzle_temperature_initial_layer"))
                and k not in ("filament_map", "different_settings_to_system", "inherits_group", "flush_volumes_matrix", "flush_volumes_vector")]

    def add_filament(self, like=1, colour=None, type_=None):
        """Another filament slot, a copy of slot like's settings; colour
        "#RRGGBB" and type change it."""
        n = len(self.config.get("filament_settings_id") or [])
        if not 1 <= like <= n:
            raise ProjectError(f"like: a slot 1..{n}.")
        for k in self.filament_keys():
            self.config[k] = list(self.config[k]) + [self.config[k][like - 1]]
        for k in ("filament_map",):
            if isinstance(self.config.get(k), list):
                self.config[k] = list(self.config[k]) + [self.config[k][like - 1] if like <= len(self.config[k]) else "1"]
        d = self.config.get("different_settings_to_system")
        if isinstance(d, list) and len(d) >= 2:
            d.insert(len(d) - 1, d[like] if like < len(d) - 1 else "")
        ig = self.config.get("inherits_group")
        if isinstance(ig, list) and len(ig) >= 2:
            ig.insert(len(ig) - 1, ig[like] if like < len(ig) - 1 else "")
        self._resize_flush(n + 1)
        slot = n + 1
        if colour:
            self.set_filament(slot, colour=colour)
        if type_:
            self.set_filament(slot, type_=type_)
        self._dirty()
        return slot

    def remove_filament(self, slot):
        n = len(self.config.get("filament_settings_id") or [])
        if n < 2:
            raise ProjectError("the project's only filament stays.")
        if not 1 <= slot <= n:
            raise ProjectError(f"slot: 1..{n}.")
        for k in self.filament_keys() + [k for k in ("filament_map",) if isinstance(self.config.get(k), list) and len(self.config[k]) == n]:
            v = list(self.config[k])
            del v[slot - 1]
            self.config[k] = v
        for k in ("different_settings_to_system", "inherits_group"):
            d = self.config.get(k)
            if isinstance(d, list) and len(d) == n + 2:
                del d[slot]
        self._resize_flush(n - 1, drop=slot)
        # Objects and parts on the slot go to slot 1; later slots move down.
        for el in self.settings_xml.iter():
            if el.tag in ("object", "part"):
                e = _meta(el).get("extruder")
                if e and e.isdigit():
                    e = int(e)
                    _set_meta(el, "extruder", "1" if e == slot else str(e - 1 if e > slot else e))
        self._dirty()
        return n - 1

    def set_filament(self, slot, colour=None, type_=None, **settings):
        n = len(self.config.get("filament_settings_id") or [])
        if not 1 <= slot <= n:
            raise ProjectError(f"slot: 1..{n}.")
        if colour:
            if not re.match(r"^#[0-9A-Fa-f]{6}$", colour):
                raise ProjectError("colour: \"#RRGGBB\".")
            self.set("filament_colour", colour.upper(), slot)
        if type_:
            self.set("filament_type", type_, slot)
        for k, v in settings.items():
            self.set(k, v, slot)
        return {"slot": slot, "colour": (self.config.get("filament_colour") or [None] * slot)[slot - 1], "type": (self.config.get("filament_type") or [None] * slot)[slot - 1]}

    def _resize_flush(self, n, drop=None):
        m = self.config.get("flush_volumes_matrix")
        if not isinstance(m, list):
            return
        old = int(round(len(m) ** 0.5)) if m else 0
        if old * old != len(m):
            return
        grid = [m[i * old:(i + 1) * old] for i in range(old)]
        if drop:
            grid = [row[:drop - 1] + row[drop:] for i, row in enumerate(grid) if i != drop - 1]
        while len(grid) < n:
            fill = grid[0][1] if old > 1 else "280"
            for row in grid:
                row.append(fill)
            grid.append([fill] * (len(grid)) + ["0"])
        self.config["flush_volumes_matrix"] = [v for row in grid for v in row]

    def set_flush(self, matrix_=None, multiplier=None):
        """Flushing volumes (mm³) between filaments: an n x n matrix, row
        from, column to; multiplier scales them all."""
        n = len(self.config.get("filament_settings_id") or [])
        if matrix_ is not None:
            if len(matrix_) != n or any(len(r) != n for r in matrix_):
                raise ProjectError(f"flush: a {n} x {n} matrix.")
            self.config["flush_volumes_matrix"] = [_fmt(float(v)) for r in matrix_ for v in r]
        if multiplier is not None:
            self.config["flush_multiplier"] = [_fmt(float(multiplier))] if isinstance(self.config.get("flush_multiplier"), list) else _fmt(float(multiplier))
        self._dirty()
        return {"matrix": self.config.get("flush_volumes_matrix"), "multiplier": self.config.get("flush_multiplier")}

    # -- plates ----------------------------------------------------------------------------
    def add_plate(self, name=None):
        plates = self.settings_xml.findall("plate")
        n = len(plates) + 1
        el = ET.Element("plate")
        for k, v in (("plater_id", str(n)), ("plater_name", name or ""), ("locked", "false")):
            _set_meta(el, k, v)
        at = list(self.settings_xml).index(plates[-1]) + 1 if plates else len(self.settings_xml)
        self.settings_xml.insert(at, el)
        self._relayout(n - 1, n, {i: i for i in range(1, n)})
        self._dirty()
        return n

    def remove_plate(self, plate):
        """A plate and the objects on it removed; later plates move up."""
        plates = self.settings_xml.findall("plate")
        if len(plates) < 2:
            raise ProjectError("the project's only plate stays.")
        el = next((p for p in plates if _meta(p).get("plater_id") == str(plate)), None)
        if el is None:
            raise ProjectError(f"plate {plate}: 1..{len(plates)}.")
        ids = {_meta(mi).get("object_id") for mi in el.findall("model_instance")}
        for oid in ids:
            o = next((x for x in self.objects() if x["id"] == oid), None)
            if o:
                self.remove_object(o["index"])
        self.settings_xml.remove(el)
        for p in self.settings_xml.findall("plate"):
            i = int(_meta(p).get("plater_id"))
            if i > plate:
                _set_meta(p, "plater_id", str(i - 1))
        n = len(plates)
        self._relayout(n, n - 1, {i: (i - 1 if i > plate else i) for i in range(1, n + 1) if i != plate})
        if GCODES in self.files:
            root = ET.fromstring(self.files[GCODES])
            for pl in list(root.findall("plate")):
                info = pl.find("plate_info")
                i = int(info.get("id")) if info is not None else 1
                if i == plate:
                    root.remove(pl)
                elif i > plate:
                    info.set("id", str(i - 1))
            self.files[GCODES] = _xml(root)
        self._dirty()
        return len(plates) - 1

    def copy_object(self, which, plate=None, instance=0, name=None):
        """A new object copying one (every part, its settings, at the same
        place): Set as an individual object, Duplicate plate."""
        o = self.object(which)
        src_plate = self.plate_of(o["index"], instance)
        plate = plate or src_plate
        parts = o["parts"]
        first = parts[0]
        v, t, _ = self.mesh(first)
        w = [apply(first["transform"], x) for x in v]
        r = self.add_object([[w[a], w[b], w[c]] for a, b, c in t], name or o["name"], plate, None, int(o.get("extruder") or 1))
        new = self.objects()[-1]
        for k, val in o["settings"].items():
            _set_meta(self._so(new), k, val)
        if first["settings"]:
            el = next(x for x in self._so(new).findall("part"))
            for k, val in first["settings"].items():
                _set_meta(el, k, val)
        for p in parts[1:]:
            v, t, _ = self.mesh(p)
            w = [apply(p["transform"], x) for x in v]
            self.add_part(new["index"], [[w[a], w[b], w[c]] for a, b, c in t], p["kind"], p["name"], None)
            el = self._so(new).findall("part")[-1]
            for k, val in p["settings"].items():
                _set_meta(el, k, val)
        # Same place as the copied instance.
        src = [it for it in self._build_items() if it.get("objectid") == o["id"]][instance]
        dst = [it for it in self._build_items() if it.get("objectid") == new["id"]][0]
        m = matrix(src.get("transform"))
        a0, b0 = self.plate_origin(src_plate), self.plate_origin(plate)
        m[0][3] += b0[0] - a0[0]
        m[1][3] += b0[1] - a0[1]
        dst.set("transform", matrix_text(m))
        self._dirty()
        return {"object": r["object"], "index": new["index"]}

    def instance_to_object(self, which, instance):
        r = self.copy_object(which, None, instance)
        plate = next((p["plate"] for p in self.plates() if self.object(which)["id"] in p["objects"]), 1)
        self.move_to_plate(r["index"], plate)
        self.remove_instance(which, instance)
        return r

    def duplicate_plate(self, plate):
        """A new plate holding a copy of every object on plate."""
        pl = next((p for p in self.plates() if p["plate"] == plate), None)
        if pl is None:
            raise ProjectError(f"plate {plate}: 1..{len(self.plates())}.")
        n = self.add_plate()
        made = []
        for oid in dict.fromkeys(pl["objects"]):
            o = next(x for x in self.objects() if x["id"] == oid)
            for k in range(len(o["instances"])):
                r = self.copy_object(o["index"], n, k)
                self.move_to_plate(r["index"], n)
                made.append(r["object"])
        return {"plate": n, "objects": made}

    def move_plate(self, plate, to):
        """Plates reordered: plate moves to place to (1 = the front)."""
        plates = self.settings_xml.findall("plate")
        n = len(plates)
        if not (1 <= plate <= n and 1 <= to <= n):
            raise ProjectError(f"plates are 1..{n}.")
        order = list(range(1, n + 1))
        order.remove(plate)
        order.insert(to - 1, plate)
        new_id = {old: i + 1 for i, old in enumerate(order)}
        for p in plates:
            _set_meta(p, "plater_id", str(new_id[int(_meta(p).get("plater_id"))]))
        self._relayout(n, n, new_id)
        pos = list(self.settings_xml).index(plates[0])
        for p in plates:
            self.settings_xml.remove(p)
        for i, p in enumerate(sorted(plates, key=lambda e: int(_meta(e).get("plater_id")))):
            self.settings_xml.insert(pos + i, p)
        if GCODES in self.files:
            root = ET.fromstring(self.files[GCODES])
            for pl in root.findall("plate"):
                info = pl.find("plate_info")
                if info is not None:
                    info.set("id", str(new_id.get(int(info.get("id")), int(info.get("id")))))
            self.files[GCODES] = _xml(root)
        self._dirty()
        return [new_id[i] for i in range(1, n + 1)]

    def scale_to_fit(self, which, margin=0.0):
        """Scale an object up or down to fill the printer's volume (the
        window's Scale to build volume)."""
        o = self.object(which)
        bed = self.bed(self.plate_of(o["index"]))
        hz = float(self.config.get("printable_height") or 0)
        if not bed or hz <= 0:
            raise ProjectError("the project names no printable area and height.")
        b = bbox(self.world_points(o))
        size = [b[3] - b[0], b[4] - b[1], b[5] - b[2]]
        room = [bed[2] - bed[0] - 2 * margin, bed[3] - bed[1] - 2 * margin, hz - margin]
        k = min(room[i] / size[i] for i in range(3) if size[i] > 0)
        return {**self.place(o["index"], scale=k, at=[(bed[0] + bed[2]) / 2, (bed[1] + bed[3]) / 2]), "scale": round(k, 4)}

    def fill_bed(self, which, spacing=5.0, limit=200):
        """As many copies as the bed holds in a grid (Fill bed with copies);
        orca_project arrange packs them tighter."""
        o = self.object(which)
        bed = self.bed(self.plate_of(o["index"]))
        if not bed:
            raise ProjectError("the project names no printable area.")
        b = bbox(self.world_points(o))
        w, d = b[3] - b[0] + spacing, b[4] - b[1] + spacing
        nx, ny = max(1, int((bed[2] - bed[0] + spacing) // w)), max(1, int((bed[3] - bed[1] + spacing) // d))
        total = min(limit, nx * ny)
        return self.grid(o["index"], nx, ny, spacing, total)

    def grid(self, which, nx, ny, spacing=5.0, total=None):
        """Copies in a grid of nx by ny from the first one's place."""
        o = self.object(which)
        while len(self.object(o["index"])["instances"]) > 1:
            self.remove_instance(o["index"], len(self.object(o["index"])["instances"]) - 1)
        b = bbox(self.world_points(o))
        w, d = b[3] - b[0] + spacing, b[4] - b[1] + spacing
        bed = self.bed(self.plate_of(o["index"])) or [0, 0, nx * w, ny * d]
        x0, y0 = bed[0] + spacing / 2 - b[0], bed[1] + spacing / 2 - b[1]
        self.place(o["index"], move=[x0, y0, 0])
        count = 1
        for j in range(ny):
            for i in range(nx):
                if i == 0 and j == 0:
                    continue
                if total and count >= total:
                    break
                self.add_instance(o["index"], [i * w, j * d])
                count += 1
        # add_instance offsets from the last copy: set each from the first.
        items = [it for it in self._build_items() if it.get("objectid") == o["id"]]
        base = matrix(items[0].get("transform"))
        k = 0
        for j in range(ny):
            for i in range(nx):
                if k >= len(items):
                    break
                m = [row[:] for row in base]
                m[0][3] += i * w
                m[1][3] += j * d
                items[k].set("transform", matrix_text(m))
                k += 1
        self._dirty()
        return {"object": o["name"], "copies": len(items), "grid": [nx, ny]}

    def subdivide(self, which, part=None):
        """Each triangle split in four (Subdivision mesh)."""
        o = self.object(which)
        p = self.part(o, part)
        v, t, _ = self.mesh(p)
        w = [apply(p["transform"], x) for x in v]
        tris = []
        for a, b, c in t:
            A, B, C = w[a], w[b], w[c]
            ab = tuple((A[i] + B[i]) / 2 for i in range(3))
            bc = tuple((B[i] + C[i]) / 2 for i in range(3))
            ca = tuple((C[i] + A[i]) / 2 for i in range(3))
            tris += [[A, ab, ca], [ab, B, bc], [ca, bc, C], [ab, bc, ca]]
        return self.replace_mesh(o["index"], part, tris)

    def decompose_color(self, which, part=None):
        """A part painted in colours split into one part per colour, each on
        its filament (Decompose Color). Triangles painted in pieces stay
        with the part."""
        o = self.object(which)
        p = self.part(o, part)
        v, t, attrs = self.mesh(p)
        states = attrs.get("paint_color")
        if not states or not any(states):
            raise ProjectError(f"{p['name']} has no colour painting.")
        w = [apply(p["transform"], x) for x in v]
        groups = {}
        rest = []
        for i, (a, b, c) in enumerate(t):
            s_ = paint_value(states[i]) if states[i] else 0
            tri = [w[a], w[b], w[c]]
            if s_:
                groups.setdefault(s_, []).append(tri)
            else:
                rest.append(tri)
        if not rest:
            # Every triangle painted: the first colour keeps the part.
            first = min(groups)
            rest = groups.pop(first)
            self.set_part(o["index"], part, "extruder", first)
        made = []
        for slot, tris in sorted(groups.items()):
            r = self.add_part(o["index"], tris, "normal_part", f"{p['name']} colour {slot}")
            el = self._so(self.object(o["index"])).findall("part")[-1]
            _set_meta(el, "extruder", str(slot))
            made.append(r["part"])
        self.replace_mesh(o["index"], part, rest)
        return {"object": o["name"], "parts": made}

    def apply_presets(self, machine=None, process=None, filaments=None):
        """The project's printer, process and filament presets swapped for
        others (flattened JSON, as slicer_presets writes them): their values
        go in, the project's own changes to the old ones go."""
        t = options()["options"]
        done = {}
        for kind, data in (("machine", machine), ("process", process)):
            if not data:
                continue
            for k, val in data.items():
                if k in t and k not in ("inherits", "name", "from", "type"):
                    self.config[k] = val
            key = {"machine": "printer_settings_id", "process": "print_settings_id"}[kind]
            if data.get("name"):
                self.config[key] = data["name"]
            done[kind] = data.get("name")
        if filaments:
            n = len(self.config.get("filament_settings_id") or [])
            while n < len(filaments):
                n = self.add_filament(1)
            for i, data in enumerate(filaments):
                for k, val in data.items():
                    if k in t and isinstance(self.config.get(k), list) and len(self.config[k]) >= n:
                        v = val[0] if isinstance(val, list) and val else val
                        self.config[k][i] = v
                ids = self.config.get("filament_settings_id")
                if data.get("name") and isinstance(ids, list):
                    ids[i] = data["name"]
            done["filaments"] = [d.get("name") for d in filaments]
        d = self.config.get("different_settings_to_system")
        if isinstance(d, list):
            self.config["different_settings_to_system"] = [""] * len(d)
        self._dirty()
        return done

    def export_amf(self, path):
        """The printable objects as placed, one AMF file."""
        out = ['<?xml version="1.0" encoding="UTF-8"?>', '<amf unit="millimeter" version="1.1">']
        for n, o in enumerate(self.objects(), 1):
            for k, inst in enumerate(o["instances"]):
                out.append(f' <object id="{n * 1000 + k}"><metadata type="name">{(o["name"] or "object").replace("&", "&amp;").replace("<", "&lt;")}</metadata><mesh><vertices>')
                vols, base = [], 0
                for p in o["parts"]:
                    if p["kind"] != "normal_part":
                        continue
                    v, t, _ = self.mesh(p)
                    tr = mul(inst["transform"], p["transform"])
                    out += ["  <vertex><coordinates><x>%s</x><y>%s</y><z>%s</z></coordinates></vertex>" % tuple(_fmt(x) for x in apply(tr, q)) for q in v]
                    vols.append((base, t))
                    base += len(v)
                out.append(" </vertices>")
                for b0, t in vols:
                    out.append("  <volume>")
                    out += [f"   <triangle><v1>{a + b0}</v1><v2>{b + b0}</v2><v3>{c + b0}</v3></triangle>" for a, b, c in t]
                    out.append("  </volume>")
                out.append(" </mesh></object>")
        out.append("</amf>")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(out) + "\n")
        return path

    def export_obj(self, path, plate=None):
        """The printable objects as placed (solid parts) in one OBJ file."""
        lines, base = ["# OrcaSlicer project objects, as placed"], 1
        plates = {p["plate"]: set(p["objects"]) for p in self.plates()}
        for o in self.objects():
            if plate and o["id"] not in plates.get(plate, set()):
                continue
            for k, inst in enumerate(o["instances"]):
                for p in o["parts"]:
                    if p["kind"] != "normal_part":
                        continue
                    v, t, _ = self.mesh(p)
                    tr = mul(inst["transform"], p["transform"])
                    lines.append(f"o {re.sub(r'[^A-Za-z0-9_.-]', '_', o['name'] or 'object')}_{k + 1}")
                    lines += ["v %s %s %s" % tuple(_fmt(x) for x in apply(tr, q)) for q in v]
                    lines += [f"f {a + base} {b + base} {c + base}" for a, b, c in t]
                    base += len(v)
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        return path

    def strip_results(self):
        """The sliced results dropped (they no longer match): plate G-code,
        its checksums and statistics, and the slicer's per-plate notes."""
        for n in list(self.files):
            if re.match(r"Metadata/plate_\d+\.(gcode|gcode\.md5|statistics\.json)$", n) or re.match(r"Metadata/plate_\d+\.json$", n):
                del self.files[n]
        if SLICE_INFO in self.files:
            try:
                root = ET.fromstring(self.files[SLICE_INFO])
                for p in root.findall("plate"):
                    root.remove(p)
                self.files[SLICE_INFO] = _xml(root)
            except ET.ParseError:
                pass

    def save(self, path=None):
        path = path or self.path
        if self.changed:
            self.strip_results()
        self.files[MODEL] = _xml(self.model)
        self.files[SETTINGS] = _xml(self.settings_xml)
        self.files[PROJECT] = json.dumps(self.config, indent=4, ensure_ascii=False).encode()
        tmp = path + ".tmp"
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            order = ["[Content_Types].xml", "_rels/.rels"]
            for n in order + sorted(x for x in self.files if x not in order):
                if n in self.files:
                    z.writestr(n, self.files[n])
        os.replace(tmp, path)
        self.path = path
        self.changed = False
        return path


def invert(m):
    """Inverse of an affine 4x4."""
    a = [row[:3] for row in m[:3]]
    det = (a[0][0] * (a[1][1] * a[2][2] - a[1][2] * a[2][1]) - a[0][1] * (a[1][0] * a[2][2] - a[1][2] * a[2][0]) + a[0][2] * (a[1][0] * a[2][1] - a[1][1] * a[2][0]))
    if abs(det) < 1e-12:
        raise ProjectError("a transform that cannot be undone (zero scale).")
    inv = [[(a[(j + 1) % 3][(i + 1) % 3] * a[(j + 2) % 3][(i + 2) % 3] - a[(j + 1) % 3][(i + 2) % 3] * a[(j + 2) % 3][(i + 1) % 3]) / det for j in range(3)] for i in range(3)]
    t = [-sum(inv[i][k] * m[k][3] for k in range(3)) for i in range(3)]
    return [inv[0] + [t[0]], inv[1] + [t[1]], inv[2] + [t[2]], [0, 0, 0, 1]]


def _xml(root):
    # Other modules register namespaces too (one taking the default prefix
    # drops ours): ours again, each time, so Orca reads p:path and no ns0:.
    for _p, _u in (("", CORE), ("p", PROD), ("BambuStudio", BBS)):
        ET.register_namespace(_p, _u)
    buf = io.BytesIO()
    ET.ElementTree(root).write(buf, encoding="UTF-8", xml_declaration=True)
    return buf.getvalue()
