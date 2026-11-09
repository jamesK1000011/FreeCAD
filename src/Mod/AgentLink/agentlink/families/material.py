# SPDX-License-Identifier: LGPL-2.1-or-later
# What parts are made of: FreeCAD's material library (the Materials
# module's MaterialManager) searched by words and category, the add-on's
# own printing filaments, and custom materials given by their figures.
# A body's material is kept in the document (store "materials",
# {body: id}) and, where this FreeCAD has it, on the object's ShapeMaterial.
#
# Ids: a filament key (pla, petg …), a library card's UUID, or
# custom:<name> for one given here (its figures kept under materialDefs).

import re

from .. import store
from ..protocol import Tool, ToolError
from ..tools import DENSITY, obj

GROUP = "material"
INFO = "Materials: search FreeCAD's library and the printing filaments, read a card's figures, set what each body is made of."

# Typical figures for FDM-printed filament, along the layers (MPa, g/cm³).
# Data sheets vary a good deal between brands; these are middling ones.
FILAMENT = {
    "pla": {"name": "PLA (printed)", "E": 3500, "nu": 0.36, "yield": 50, "uts": 55},
    "petg": {"name": "PETG (printed)", "E": 2100, "nu": 0.38, "yield": 45, "uts": 50},
    "abs": {"name": "ABS (printed)", "E": 2200, "nu": 0.35, "yield": 40, "uts": 42},
    "asa": {"name": "ASA (printed)", "E": 2000, "nu": 0.35, "yield": 42, "uts": 45},
    "pa6-cf": {"name": "PA6-CF (printed, dry)", "E": 6000, "nu": 0.35, "yield": 80, "uts": 90},
    "tpu-95a": {"name": "TPU 95A (printed)", "E": 26, "nu": 0.48, "yield": None, "uts": 35},
}
FILAMENT_SOURCE = "Agent Link's typical FDM filament figures, along the layers"
TYPICAL = "Library and filament figures are typical: for a part that must not fail, use the supplier's data sheet (material_set custom)."
# Library folders that hold no solid's mechanics.
SKIP_DIRS = ("Appearance", "Patterns", "Test", "Fluid")
# The library's cards for filament keys, by name, for ShapeMaterial.
LIB_NAME = {"pla": "PLA", "abs": "ABS"}

_cache = {}


def _manager():
    if "mm" not in _cache:
        try:
            import Materials

            _cache["mm"] = Materials.MaterialManager()
        except Exception:  # an old FreeCAD: the filament table alone
            _cache["mm"] = None
    return _cache["mm"]


def _q(m, name, unit):
    try:
        if not m.hasPhysicalProperty(name):
            return None
        v = m.getPhysicalValue(name)
    except Exception:
        return None
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(v.getValueAs(unit))
    except Exception:
        return None


def _library():
    """{uuid: card} of the solids in FreeCAD's library, read once."""
    if "lib" in _cache:
        return _cache["lib"]
    out = {}
    mm = _manager()
    if mm is not None:
        try:
            mats = mm.Materials
        except Exception:
            mats = {}
        for uid, m in mats.items():
            try:
                d = str(getattr(m, "Directory", "") or "")
                if any(d.startswith(s) or f"/{s}" in d for s in SKIP_DIRS):
                    continue
                rho = _q(m, "Density", "g/cm^3")
                E = _q(m, "YoungsModulus", "MPa")
                if rho is None and E is None:
                    continue
                out[uid] = {
                    "id": uid,
                    "name": m.Name,
                    "category": d.replace("Standard/", "").strip("/") or "Library",
                    "library": getattr(m, "LibraryName", ""),
                    "density": rho,
                    "E": E,
                    "nu": _q(m, "PoissonRatio", ""),
                    "yield": _q(m, "YieldStrength", "MPa"),
                    "uts": _q(m, "UltimateTensileStrength", "MPa"),
                    "tags": list(getattr(m, "Tags", []) or []),
                    "about": (getattr(m, "Description", "") or "")[:160],
                    "source": "FreeCAD material library" + (f" ({getattr(m, 'URL', '')})" if getattr(m, "URL", "") else ""),
                }
            except Exception:
                continue
    _cache["lib"] = out
    return out


def filament_card(key):
    f = FILAMENT.get(key, {"name": key.upper(), "E": None, "nu": None, "yield": None, "uts": None})
    return {"id": key, "name": f["name"], "category": "Printing filament", "density": DENSITY[key], "E": f["E"], "nu": f["nu"], "yield": f["yield"], "uts": f["uts"], "source": FILAMENT_SOURCE}


def defs(doc):
    return store.read(doc, "materialDefs", {})


def card(doc, mid):
    """A material's figures by id, or None."""
    if mid is None:
        return None
    key = str(mid)
    if key.lower() in DENSITY:
        return filament_card(key.lower())
    if key.startswith("custom:"):
        d = defs(doc).get(key)
        return dict(d) if d else None
    return _library().get(key)


def of_body(doc, label, default="pla"):
    """The body's material card, and whether it was set or assumed."""
    mid = store.materials(doc).get(label)
    c = card(doc, mid)
    if c:
        return c, True
    return (filament_card(default), False) if default else (None, False)


def _brief(c):
    return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in c.items() if k in ("id", "name", "category", "density", "E", "yield") and v is not None}


def tools(session):
    s = session

    def material_list(a):
        words = [w for w in re.split(r"\s+", (a.get("words") or "").lower()) if w]
        cat = (a.get("category") or "").lower()
        cards = [filament_card(k) for k in DENSITY] + sorted(_library().values(), key=lambda c: (c["category"], c["name"]))
        cards += [dict(v) for v in defs(s.doc).values()]
        hits = []
        for c in cards:
            hay = " ".join(str(x) for x in (c["id"], c["name"], c["category"], " ".join(c.get("tags", [])), c.get("about", ""))).lower()
            if cat and cat not in c["category"].lower():
                continue
            if all(w in hay for w in words):
                hits.append(c)
        # Those named by the words first, then those that only mention them.
        hits.sort(key=lambda c: not all(w in (c["id"] + " " + c["name"]).lower() for w in words))
        limit = int(a.get("limit", 20))
        cats = sorted({c["category"] for c in cards})
        out = {"found": len(hits), "materials": [_brief(c) for c in hits[:limit]], "categories": cats}
        if _manager() is None:
            out["note"] = "FreeCAD's material library is not in this build: the printing filaments only."
        if not hits:
            out["note"] = "Nothing matches: fewer words, or a category from categories."
        return out

    def material_get(a):
        c = card(s.doc, a.get("id"))
        if c is None:
            raise ToolError("not_found", f"material {a.get('id')!r}: no such id — material_list finds one.")
        out = {k: (round(v, 4) if isinstance(v, float) else v) for k, v in c.items() if v not in (None, "", [])}
        out["units"] = "density g/cm³; E, yield, uts MPa"
        missing = [k for k in ("density", "E", "nu", "yield") if c.get(k) is None]
        if missing:
            out["missing"] = missing
        out["note"] = TYPICAL
        return out

    def material_set(a):
        bodies = [s.body(b).Label for b in (a.get("bodies") or [None])]
        custom = a.get("custom")
        if custom:
            name = str(custom.get("name") or "").strip()
            if not name:
                raise ToolError("invalid_input", "custom.name: required — what the material is called.")
            rho = custom.get("density")
            if not isinstance(rho, (int, float)) or not 0.01 < rho < 25:
                raise ToolError("invalid_input", "custom.density: g/cm³, 0.01 to 25.")
            for k in ("youngsMPa", "yieldMPa", "poisson"):
                if k in custom and not isinstance(custom[k], (int, float)):
                    raise ToolError("invalid_input", f"custom.{k}: a number.")
            mid = "custom:" + re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
            c = {"id": mid, "name": name, "category": "Custom", "density": float(rho), "E": custom.get("youngsMPa"), "nu": custom.get("poisson", 0.35 if custom.get("youngsMPa") else None), "yield": custom.get("yieldMPa"), "uts": custom.get("utsMPa"), "source": custom.get("source") or "given by the person"}
            d = defs(s.doc)
            d[mid] = c
            store.write(s.doc, "materialDefs", d)
        else:
            mid = a.get("id")
            if not mid:
                raise ToolError("invalid_input", "id (from material_list) or custom { name, density, youngsMPa, yieldMPa }.")
            if str(mid).lower() in DENSITY:
                mid = str(mid).lower()
            c = card(s.doc, mid)
            if c is None:
                raise ToolError("not_found", f"material {mid!r}: no such id — material_list finds one.")
        mats = store.materials(s.doc)
        shape_mat = {}
        lib_uuid = c["id"] if c["id"] in _library() else next((u for u, x in _library().items() if x["name"] == LIB_NAME.get(c["id"])), None)
        for b in bodies:
            mats[b] = c["id"]
            o = s.body(b)
            if lib_uuid and hasattr(o, "ShapeMaterial") and _manager() is not None:
                try:
                    o.ShapeMaterial = _manager().getMaterial(lib_uuid)
                    shape_mat[b] = True
                except Exception:
                    shape_mat[b] = False
            else:
                shape_mat[b] = False
        store.write(s.doc, "materials", mats)
        s.snapshot("material_set")
        out = {"bodies": bodies, "material": _brief(c), "shapeMaterial": shape_mat}
        if not all(shape_mat.values()):
            out["shapeMaterialNote"] = "Not every body's ShapeMaterial was set: only FreeCAD library cards go there; the material is kept with the document either way."
        if c["id"] not in DENSITY:
            out["note"] = "part_review and slice know the filament keys only (pla, petg, abs, asa, pa6-cf, tpu-95a); mass_properties and fea_* read this material."
        missing = [k for k in ("E", "yield") if c.get(k) is None]
        if missing:
            out["missing"] = missing
        return out

    def material_clear(a):
        mats = store.materials(s.doc)
        bodies = [s.body(b).Label for b in a["bodies"]] if a.get("bodies") else list(mats)
        cleared = [b for b in bodies if mats.pop(b, None) is not None]
        store.write(s.doc, "materials", mats)
        s.snapshot("material_clear")
        return {"cleared": cleared, "materials": mats, "note": "The objects' ShapeMaterial is left as it was."}

    return [
        Tool(
            "material_list",
            "Find materials: words (all must match name, category, tags or description) and category (Printing filament, Metal/Steel, Thermoplast …). The printing filaments (pla, petg, abs, asa, pa6-cf, tpu-95a) come first, then FreeCAD's library, then custom ones. Answers id, name, category, density g/cm³, E and yield MPa where the card has them.",
            obj({"words": {"type": "string"}, "category": {"type": "string"}, "limit": {"type": "integer"}}),
            material_list,
        ),
        Tool(
            "material_get",
            "One material's figures by id: density g/cm³, Young's modulus, Poisson's ratio, yield and ultimate MPa where the card has them, and where they come from. Library and filament figures are typical — a part that must not fail needs the supplier's.",
            obj({"id": {"type": "string"}}, ["id"]),
            material_get,
        ),
        Tool(
            "material_set",
            "Say what bodies are made of: id from material_list, or custom { name, density g/cm³, youngsMPa, yieldMPa, poisson, source } for the supplier's figures. Kept with the document; a library card also goes on the object's ShapeMaterial. bodies: the one body when left out.",
            obj({"bodies": {"type": "array", "items": {"type": "string"}}, "id": {"type": "string"}, "custom": {"type": "object"}}),
            material_set,
            changes=True,
        ),
        Tool(
            "material_clear",
            "Forget what bodies are made of (every body when bodies is left out); mass and strength then assume PLA and say so.",
            obj({"bodies": {"type": "array", "items": {"type": "string"}}}),
            material_clear,
            changes=True,
        ),
    ]
