# SPDX-License-Identifier: LGPL-2.1-or-later
# OrcaSlicer headless: its projects made from FreeCAD bodies, read and
# changed at every level the slicer's window reaches (settings per project,
# filament, object, part, layer range and plate; modifiers, negative parts,
# support blockers and enforcers; instances, plates, per-layer G-code;
# painted supports, seam, colour and fuzzy skin by FreeCAD faces), sliced
# and exported by its command line, and the G-code read back layer by layer —
# one loop between the two: design, slice, read the print back, revise the
# body, re-slice.

import os

from .. import orca3mf as O
from .. import slicers
from ..protocol import Tool, ToolError

GROUP = "orca"
INFO = "OrcaSlicer headless: projects from bodies, every setting and part, paint by faces, slice, export, G-code read back."


def obj(props, required=()):
    return {"type": "object", "properties": props, "required": list(required), "additionalProperties": False}


VEC = {"type": "array", "items": {"type": "number"}}


def _mesh_of(shape, tol=0.01):
    import MeshPart

    m = MeshPart.meshFromShape(Shape=shape, LinearDeflection=tol, AngularDeflection=0.0873, Relative=False)
    pts = [(p.x, p.y, p.z) for p in m.Points]
    return [[pts[a], pts[b], pts[c]] for a, b, c in m.Topology[1]]


def tools(session):
    s = session

    def path_of(a, must=True):
        p = a.get("path")
        if not p:
            raise ToolError("invalid_input", "path: the project (.3mf).")
        p = os.path.expanduser(p)
        if not os.path.isabs(p):
            p = os.path.join(s.out_dir, p)
        if must and not os.path.isfile(p):
            raise ToolError("not_found", f"{p}: no such file.")
        return p

    def load(a):
        try:
            return O.Project(path_of(a))
        except O.ProjectError as e:
            raise ToolError("invalid_input", str(e))
        except Exception as e:
            raise ToolError("invalid_input", f"{os.path.basename(a.get('path', ''))} is not an OrcaSlicer project: {e}")

    def offset(proj, o, p, body):
        """FreeCAD coordinates minus the object's own. Orca keeps an object
        in its source file's coordinates (each part's centring undone by its
        component transform), so a body exported as STL lines up as it is;
        a project from elsewhere is lined up by bounding boxes."""
        if o.get("source"):
            return (0.0, 0.0, 0.0)
        b = O.bbox([O.apply(p["transform"], v) for v in proj.mesh(p)[0]])
        bb = body.Shape.BoundBox
        return (bb.XMin - b[0], bb.YMin - b[1], bb.ZMin - b[2])

    def body_for(proj, o, a):
        if a.get("body"):
            return s.body(a["body"])
        name = (o.get("name") or "").rsplit(".", 1)[0]
        for cand in (name, name.replace("_", " ")):
            try:
                return s.body(cand)
            except Exception:
                pass
        raise ToolError("invalid_input", f"body: the FreeCAD body {o['name']} was made from.")

    # ---- orca_project ----------------------------------------------------------------------
    def orca_project(a):
        action = a.get("action", "info")
        if action == "settings":
            return settings_list(a)
        if action == "new":
            return new(a)
        proj = load(a)
        try:
            out = act(proj, action, a)
        except O.ProjectError as e:
            raise ToolError("invalid_input", str(e))
        if proj.changed:
            dest = path_of({"path": a["saveAs"]}, must=False) if a.get("saveAs") else proj.path
            proj.save(dest)
            out = {**out, "saved": dest, "next": "orca_slice { path } slices it as it now stands."}
        return out

    def act(proj, action, a):
        if action == "info":
            out = proj.info()
            if a.get("object"):
                o = proj.object(a["object"])
                out = {**next(x for x in out["objects"] if x["id"] == o["id"]), "effective": effective(proj, o)}
            return out
        if action == "set":
            scope = a.get("scope") or ("part" if a.get("part") else "object" if a.get("object") else "project")
            pairs = dict(a.get("settings") or {})
            if a.get("key"):
                pairs[a["key"]] = a.get("value")
            if not pairs and scope != "range":
                raise ToolError("invalid_input", "key and value, or settings { key: value }.")
            done = {}
            for k, v in pairs.items():
                if scope == "project":
                    old, new_ = proj.set(k, v, a.get("slot"))
                    done[k] = {"was": old, "now": new_}
                elif scope == "object":
                    proj.set_object(a["object"], k, v)
                    done[k] = v
                elif scope == "part":
                    proj.set_part(a["object"], a.get("part"), k, v)
                    done[k] = v
                elif scope == "plate":
                    proj.set_plate(int(a.get("plate", 1)), k, v)
                    done[k] = v
            if scope == "range":
                if a.get("zmin") is None or a.get("zmax") is None:
                    raise ToolError("invalid_input", "range: object, zmin and zmax (mm from the bed) and settings ({} removes it).")
                return {"object": a["object"], "ranges": proj.set_range(a["object"], float(a["zmin"]), float(a["zmax"]), pairs)}
            if scope not in ("project", "object", "part", "plate"):
                raise ToolError("invalid_input", "scope: project, object, part, range or plate.")
            return {"scope": scope, **({"object": a["object"]} if a.get("object") else {}), "set": done}
        if action == "part_add":
            kind = a.get("kind", "modifier_part")
            o = proj.object(a["object"])
            if a.get("body"):
                src = s.body(a["body"])
                host = body_for(proj, o, {"body": a["of"]} if a.get("of") else {})
                d = offset(proj, o, proj.part(o, 1), host)
                tris = [[(p[0] - d[0], p[1] - d[1], p[2] - d[2]) for p in t] for t in _mesh_of(src.Shape)]
            elif a.get("box"):
                size, at = a["box"], a.get("at") or [0, 0, 0]
                lo = [at[i] - size[i] / 2 for i in range(3)]
                hi = [at[i] + size[i] / 2 for i in range(3)]
                c = [(x, y, z) for z in (lo[2], hi[2]) for y in (lo[1], hi[1]) for x in (lo[0], hi[0])]
                f = [(0, 2, 1), (1, 2, 3), (4, 5, 6), (5, 7, 6), (0, 1, 4), (1, 5, 4), (2, 6, 3), (3, 6, 7), (0, 4, 2), (2, 4, 6), (1, 3, 5), (3, 7, 5)]
                tris = [[c[i], c[j], c[k]] for i, j, k in f]
            else:
                raise ToolError("invalid_input", "part_add: body (a FreeCAD body, in place) or box [x, y, z] mm with at [x, y, z] (centre, object coordinates).")
            return proj.add_part(a["object"], tris, kind, a.get("name"), a.get("settings"))
        if action == "part_remove":
            return {"removed": proj.remove_part(a["object"], a.get("part"))}
        if action == "replace":
            # The loop's return leg: the body changed in FreeCAD, its part
            # in the project follows; settings, modifiers and place stay.
            o = proj.object(a["object"])
            p = proj.part(o, a.get("part"))
            body = body_for(proj, o, a)
            painted = sorted(k[6:] for k, v in (proj.mesh(p)[2] or {}).items() if any(v))
            d = offset(proj, o, p, body) if a.get("keepPlace", True) else (0, 0, 0)
            tris = [[(q[0] - d[0], q[1] - d[1], q[2] - d[2]) for q in t] for t in _mesh_of(body.Shape)]
            out = proj.replace_mesh(o["index"], a.get("part"), tris)
            if painted:
                out["paintDropped"] = painted
                out["note"] = "Painted faces went with the old mesh: orca_paint them again by face."
            return out
        if action == "place":
            return proj.place(a["object"], int(a.get("instance", 0)), a.get("move"), a.get("rotate"), a.get("scale"), a.get("at"), a.get("drop", True))
        if action == "plate":
            return {"plates": proj.move_to_plate(a["object"], int(a["plate"]), int(a.get("instance", 0)))}
        if action == "gcode":
            if a.get("z") is None:
                raise ToolError("invalid_input", "gcode: z (top of the layer, mm), kind (pause, color_change, tool_change, template, custom), gcode for custom.")
            return {"plate": a.get("plate", 1), "gcodes": proj.set_gcode(float(a["z"]), a.get("kind", "pause"), int(a.get("plate", 1)), a.get("gcode"), int(a.get("extruder", 1)), a.get("color"), bool(a.get("remove")))}
        if action == "object_remove":
            return {"removed": proj.remove_object(a["object"])}
        raise ToolError("invalid_input", "action: new, info, set, part_add, part_remove, replace, place, plate, gcode, object_remove, settings.")

    def effective(proj, o):
        """What prints for an object: project, then its own, then its first
        part's — the settings a strength check reads."""
        keys = ("layer_height", "wall_loops", "top_shell_layers", "bottom_shell_layers", "sparse_infill_density", "sparse_infill_pattern",
                "outer_wall_line_width", "inner_wall_line_width", "sparse_infill_line_width", "line_width", "infill_direction", "enable_support", "support_type", "brim_type")
        cfg = proj.config
        out = {k: cfg.get(k) for k in keys if cfg.get(k) is not None}
        out.update(o.get("settings") or {})
        ex = int(o.get("extruder") or 1)
        ft = cfg.get("filament_type") or []
        if ft:
            out["filament"] = ft[min(ex, len(ft)) - 1]
        return out

    def settings_list(a):
        t = O.options()
        q = (a.get("query") or "").lower()
        rows = []
        for k, v in sorted(t["options"].items()):
            if q and q not in k and q not in (v.get("category") or "").lower():
                continue
            if a.get("preset") and v.get("preset") != a["preset"]:
                continue
            rows.append({"key": k, **{x: v[x] for x in ("type", "preset", "category", "values", "min", "max", "default", "sidetext") if x in v}})
        lim = int(a.get("limit", 40))
        return {"orca": t["orca"], "count": len(rows), "settings": rows[:lim], **({"more": len(rows) - lim} if len(rows) > lim else {}),
                "note": "Keys and limits only; the person's OrcaSlicer shows each one's label and help."}

    def new(a):
        bodies = [s.body(b) for b in (a.get("bodies") or ([a["body"]] if a.get("body") else []))]
        if not bodies:
            raise ToolError("invalid_input", "new: bodies (FreeCAD bodies, one object each).")
        dest = path_of({"path": a.get("path") or f"{bodies[0].Label.replace(' ', '_')}-orca.3mf"}, must=False)
        work = os.path.splitext(dest)[0] + "-work"
        os.makedirs(work, exist_ok=True)
        stls = []
        for b in bodies:
            p = os.path.join(work, f"{b.Label.replace(' ', '_')}.stl")
            O.write_stl(p, _mesh_of(b.Shape))
            stls.append(p)
        from .slicing import DENSITY
        from .. import store

        setup = store.setup_of(s.doc, bodies[0].Label, "print") or {}
        settings = {k: v for k, v in setup.items() if k not in ("id", "name", "type", "bodies")}
        mat = store.materials(s.doc).get(bodies[0].Label) or "pla"
        settings["filament"], settings["density"] = mat.split("-")[0], DENSITY.get(mat, 1.24)
        profiles = a.get("profiles") or {}
        chosen = setup.get("slicerPresets")
        if not profiles and chosen:
            from .. import slicer_presets as SP

            data = SP.load_saved(s.out_dir, chosen["bundle"]) if chosen.get("bundle") else SP.bundle(chosen.get("slicer", "orcaslicer"), chosen["machine"], chosen["process"], chosen["filament"])
            profiles = SP.write_profiles(data, os.path.join(work, "presets"))
        try:
            exe = slicers.make_project("orcaslicer", stls, dest, settings, profiles, {k: True for k in ("orient", "arrange") if a.get(k)})
        except FileNotFoundError as e:
            raise ToolError("unavailable", str(e))
        except RuntimeError as e:
            raise ToolError("failed", str(e))
        proj = O.Project(dest)
        return {"project": dest, "objects": [{"index": o["index"], "name": o["name"], "body": b.Label} for o, b in zip(proj.objects(), bodies)],
                "next": "orca_project set/part_add/gcode change it; orca_paint paints by face; orca_slice slices it."}

    # ---- orca_paint -------------------------------------------------------------------------
    def orca_paint(a):
        proj = load(a)
        try:
            o = proj.object(a["object"])
            p = proj.part(o, a.get("part"))
        except O.ProjectError as e:
            raise ToolError("invalid_input", str(e))
        kind = a.get("kind", "supports")
        body = body_for(proj, o, a)
        faces = a.get("faces") or []
        state = a.get("state", {"supports": "enforce", "seam": "enforce", "fuzzy_skin": "fuzzy", "color": 2}.get(kind))
        verts, tris, attrs = proj.mesh(p)
        d = offset(proj, o, p, body)
        shape = body.Shape
        want = set()
        for f in faces:
            try:
                want.add(int(str(f).replace("Face", "")) - 1)
            except ValueError:
                raise ToolError("invalid_input", f"faces: Face names like Face3, not {f!r}.")
        if a.get("clear"):
            want, state = set(), None
        # Sample each face on its own tessellation; each triangle goes to the
        # face whose samples lie nearest its centre.
        import FreeCAD

        cell = max(shape.BoundBox.DiagonalLength / 60, 0.5)
        grid = {}
        for i, f in enumerate(shape.Faces):
            pts, ft = f.tessellate(cell / 4)
            samples = [FreeCAD.Vector(*(sum(pts[t[j]][k] for j in range(3)) / 3 for k in range(3))) for t in ft] + list(pts)
            for q in samples:
                grid.setdefault((int(q.x // cell), int(q.y // cell), int(q.z // cell)), []).append((q, i))
        old = attrs.get(O.PAINT[kind][0]) or [""] * len(tris)
        states, hit = [], 0
        names = O.PAINT[kind][1]
        for n, (i, j, k) in enumerate(tris):
            c = [sum(O.apply(p["transform"], verts[x])[m] for x in (i, j, k)) / 3 + d[m] for m in range(3)]
            key = (int(c[0] // cell), int(c[1] // cell), int(c[2] // cell))
            best, bf = None, None
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for dz in (-1, 0, 1):
                        for q, fi in grid.get((key[0] + dx, key[1] + dy, key[2] + dz), ()):
                            dd = (q.x - c[0]) ** 2 + (q.y - c[1]) ** 2 + (q.z - c[2]) ** 2
                            if best is None or dd < best:
                                best, bf = dd, fi
            if bf in want:
                states.append(state)
                hit += 1
            elif a.get("clear") or a.get("replace"):
                states.append("none" if names else 0)
            else:
                v = O.paint_value(old[n]) if old[n] else 0
                inv = {x: y for y, x in names.items()} if names else None
                states.append(inv.get(v, "none") if inv else (v or 0))
        try:
            proj.paint(o["index"], a.get("part"), kind, states)
        except O.ProjectError as e:
            raise ToolError("invalid_input", str(e))
        proj.save()
        out = {"project": proj.path, "object": o["name"], "kind": kind, "state": state, "faces": faces, "triangles": hit}
        if faces and not hit:
            out["warning"] = "No triangle matched those faces: check the body is the one the object was made from."
        if kind == "supports" and state == "enforce" and str(proj.config.get("enable_support")) not in ("1", "true"):
            out["note"] = "Enforced supports print only with supports on: orca_project set { key: \"enable_support\", value: true } (support_type normal(manual) or tree(manual) keeps to the painted ones)."
        return out

    # ---- orca_slice -------------------------------------------------------------------------
    def orca_slice(a):
        p = path_of(a)
        exe = slicers.find("orcaslicer")
        if not exe:
            raise ToolError("unavailable", "OrcaSlicer is not on this computer (AGENTLINK_ORCASLICER sets its path).")
        what = a.get("export", "gcode")
        out_dir = os.path.splitext(p)[0] + "-out"
        try:
            if what == "gcode":
                plate = int(a.get("plate", 0))
                exe, found = slicers.slice_project("orcaslicer", p, out_dir, export=os.path.splitext(p)[0] + "-sliced.3mf" if a.get("keep", True) else None, plate=plate)
                res = {"gcode": found, **slicers.read_gcode(found[0]), **slicers.LAST}
                if os.path.isfile(os.path.splitext(p)[0] + "-sliced.3mf"):
                    res["sliced"] = os.path.splitext(p)[0] + "-sliced.3mf"
                res["next"] = "gcode_read { path } shows it layer by layer; starting the print is the person's call."
                return res
            made = slicers.export_project("orcaslicer", p, out_dir, what, a.get("name"))
            return {"export": what, "files": made}
        except FileNotFoundError as e:
            raise ToolError("unavailable", str(e))
        except (RuntimeError, ValueError) as e:
            raise ToolError("failed", str(e))

    # ---- gcode_read -------------------------------------------------------------------------
    def gcode_read(a):
        from .. import gcode_view as G

        p = a.get("path")
        if not p:
            raise ToolError("invalid_input", "path: a .gcode file (or a sliced .3mf).")
        p = os.path.expanduser(p if os.path.isabs(os.path.expanduser(p)) else os.path.join(s.out_dir, p))
        if p.endswith(".3mf"):
            import zipfile

            with zipfile.ZipFile(p) as z:
                inner = sorted(n for n in z.namelist() if n.endswith(".gcode"))
                if not inner:
                    raise ToolError("invalid_state", "The project is not sliced: orca_slice it first.")
                n = inner[int(a.get("plate", 1)) - 1 if int(a.get("plate", 1)) <= len(inner) else 0]
                dest = os.path.splitext(p)[0] + "-" + os.path.basename(n)
                with z.open(n) as src, open(dest, "wb") as f:
                    f.write(src.read())
                p = dest
        if not os.path.isfile(p):
            raise ToolError("not_found", f"{p}: no such file.")
        want = a.get("layer") is not None or a.get("z") is not None or a.get("svg")
        view = a.get("view", "feature")
        if view not in G.VIEWS:
            raise ToolError("invalid_input", f"view: {', '.join(G.VIEWS)}.")
        data = G.read(p)
        out = G.summary(data)
        out["legend"] = G.legend(data, view)
        if want:
            lay = G.layer(data, a.get("layer"), a.get("z"))
            out["layer"] = lay
            if a.get("svg") and lay:
                data = G.read(p, keep_paths={lay["layer"]})
                svg = os.path.splitext(p)[0] + f"-layer{lay['layer']}-{view}.svg"
                with open(svg, "w", encoding="utf-8") as f:
                    f.write(G.svg(data, lay["layer"], view))
                out["svg"] = svg
        return out

    return [
        Tool(
            "orca_project",
            "OrcaSlicer projects (.3mf), headless, all the window keeps: action new { bodies, path, orient, arrange } (from FreeCAD bodies, the print setup's presets); info { path, object } (objects, parts, plates, filaments, ranges, per-layer G-code; object: its effective print settings); set { path, scope project|object|part|range|plate, key, value | settings {}, object, part, slot (filament), zmin, zmax, plate }; part_add { path, object, kind modifier_part|negative_part|support_blocker|support_enforcer|normal_part, body | box [x,y,z] + at, name, settings }; part_remove; replace { path, object, part, body } (the body changed: its mesh follows, settings stay); place { object, instance, move, rotate, scale, at [x,y], drop }; plate { object, plate } (moves it; next number makes a plate); gcode { z, kind pause|color_change|tool_change|template|custom, gcode, extruder, plate, remove }; object_remove; settings { query, preset } (every setting's key, type, limits, values). Values checked before saving; saveAs writes a copy.",
            obj({"action": {"type": "string", "enum": ["new", "info", "set", "part_add", "part_remove", "replace", "place", "plate", "gcode", "object_remove", "settings"]},
                 "path": {"type": "string"}, "saveAs": {"type": "string"}, "body": {"type": "string"}, "bodies": {"type": "array", "items": {"type": "string"}}, "of": {"type": "string"},
                 "object": {"type": ["string", "integer"]}, "part": {"type": ["string", "integer"]}, "scope": {"type": "string", "enum": ["project", "object", "part", "range", "plate"]},
                 "key": {"type": "string"}, "value": {}, "settings": {"type": "object"}, "slot": {"type": "integer"}, "zmin": {"type": "number"}, "zmax": {"type": "number"},
                 "plate": {"type": "integer"}, "kind": {"type": "string"}, "box": VEC, "at": VEC, "name": {"type": "string"}, "instance": {"type": "integer"},
                 "move": VEC, "rotate": VEC, "scale": {}, "drop": {"type": "boolean"}, "z": {"type": "number"}, "gcode": {"type": "string"}, "extruder": {"type": "integer"},
                 "color": {"type": "string"}, "remove": {"type": "boolean"}, "orient": {"type": "boolean"}, "arrange": {"type": "boolean"}, "profiles": {"type": "object"},
                 "query": {"type": "string"}, "preset": {"type": "string", "enum": ["process", "machine", "filament"]}, "limit": {"type": "integer"}}),
            orca_project,
            changes=False,
        ),
        Tool(
            "orca_paint",
            "Paint an OrcaSlicer project's object by FreeCAD faces, as the window's paint tools do: kind supports (state enforce|block), seam (enforce|block), fuzzy_skin (fuzzy), color (a filament slot 1..). { path, object, part, body (the body it was made from; found by name otherwise), faces [\"Face3\", …], state, replace (unpainted faces cleared), clear }.",
            obj({"path": {"type": "string"}, "object": {"type": ["string", "integer"]}, "part": {"type": ["string", "integer"]}, "body": {"type": "string"},
                 "kind": {"type": "string", "enum": list(O.PAINT)}, "faces": {"type": "array", "items": {"type": "string"}}, "state": {}, "replace": {"type": "boolean"}, "clear": {"type": "boolean"}},
                ["path", "object"]),
            orca_paint,
        ),
        Tool(
            "orca_slice",
            "Slice an OrcaSlicer project as it stands, by its command line (headless): { path, plate (0 all), keep (the sliced .3mf, default true) } gives time, filament, warnings and statistics; export stl|stls|settings|slicedata|3mf writes those instead.",
            obj({"path": {"type": "string"}, "plate": {"type": "integer"}, "keep": {"type": "boolean"}, "export": {"type": "string", "enum": ["gcode", "stl", "stls", "settings", "slicedata", "3mf"]}, "name": {"type": "string"}}, ["path"]),
            orca_slice,
        ),
        Tool(
            "gcode_read",
            "Read sliced G-code (a .gcode, or a sliced .3mf) as the slicer's preview shows it: totals, a legend in view feature|height|width|speed|fan|temperature|flow|layer_time|tool|acceleration, one layer { layer | z } (its moves, features, time), svg: true draws that layer.",
            obj({"path": {"type": "string"}, "layer": {"type": "integer"}, "z": {"type": "number"}, "view": {"type": "string"}, "svg": {"type": "boolean"}, "plate": {"type": "integer"}}, ["path"]),
            gcode_read,
        ),
    ]
