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


FONTS = ["/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Helvetica.ttc", "/Library/Fonts/Arial.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/TTF/DejaVuSans.ttf", "/usr/share/fonts/dejavu/DejaVuSans.ttf",
         "C:/Windows/Fonts/arial.ttf"]

ACTIONS = ["new", "info", "set", "part_add", "part_remove", "replace", "place", "plate", "gcode", "object_add", "object_remove", "instance", "printable",
           "layer_heights", "brim_ears", "filament", "flush", "plate_add", "plate_remove", "mesh", "arrange", "orient", "cli", "lay_flat", "emboss", "copy", "instance_to_object", "plate_duplicate", "plate_move", "scale_to_fit",
           "fill_bed", "grid", "presets", "strength", "settings"]


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

    def to_body(proj, o, p, body):
        """A function taking a part's own mesh points to FreeCAD's coordinates.
        Orca keeps each loaded mesh centred and remembers the shift
        (source_offset), so a body exported as STL comes back exactly; a
        project from elsewhere is lined up by bounding boxes."""
        if p.get("sourceOffset"):
            so = p["sourceOffset"]
            return lambda v: (v[0] + so[0], v[1] + so[1], v[2] + so[2])
        b = O.bbox([O.apply(p["transform"], v) for v in proj.mesh(p)[0]])
        bb = body.Shape.BoundBox
        d = (bb.XMin - b[0], bb.YMin - b[1], bb.ZMin - b[2])
        return lambda v: tuple(O.apply(p["transform"], v)[i] + d[i] for i in range(3))

    def from_body(proj, o, p, body):
        """FreeCAD points to the part's own mesh coordinates (raw=True for
        replace_mesh), or None when they go in object coordinates."""
        if p.get("sourceOffset"):
            so = p["sourceOffset"]
            return lambda q: (q[0] - so[0], q[1] - so[1], q[2] - so[2])
        return None

    def offset(proj, o, p, body):
        """FreeCAD coordinates minus the object's own (object coordinates:
        the part's component transform applied)."""
        f = to_body(proj, o, p, body)
        v0 = proj.mesh(p)[0][0]
        a_ = f(v0)
        b_ = O.apply(p["transform"], v0)
        return (a_[0] - b_[0], a_[1] - b_[1], a_[2] - b_[2])

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
            elif a.get("file"):
                tris = O.read_stl(os.path.expanduser(a["file"]))
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
            painted = sorted(k[6:] for k, v in (proj.mesh(p)[2] or {}).items() if any(v))
            # Reload from disk / replace with an STL takes the file; else the body.
            body = None if a.get("file") else body_for(proj, o, a)
            free = O.read_stl(os.path.expanduser(a["file"])) if a.get("file") else _mesh_of(body.Shape)
            back = from_body(proj, o, p, body)
            if back:
                out = proj.replace_mesh(o["index"], a.get("part"), [[back(q) for q in t] for t in free], raw=True)
            else:
                d = offset(proj, o, p, body) if body else (0.0, 0.0, 0.0)
                out = proj.replace_mesh(o["index"], a.get("part"), [[(q[0] - d[0], q[1] - d[1], q[2] - d[2]) for q in t] for t in free])
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
        if action == "object_add":
            if a.get("body"):
                b = s.body(a["body"])
                models = [(a.get("name") or f"{b.Label}.stl", _mesh_of(b.Shape))]
            elif a.get("file") or a.get("handy"):
                models = load_models(a)
            else:
                models = [(a.get("name") or str(a.get("primitive", "cube")), primitive(a))]
            made = [proj.add_object(tris, name, int(a.get("plate", 1)), a.get("at"), int(a.get("extruder", 1))) for name, tris in models]
            return made[0] if len(made) == 1 else {"objects": made, "next": "orca_project arrange lays them out."}
        if action == "copy":
            return proj.copy_object(a["object"], a.get("plate"), int(a.get("instance", 0)), a.get("name"))
        if action == "instance_to_object":
            return proj.instance_to_object(a["object"], int(a.get("instance", 1)))
        if action == "plate_duplicate":
            return proj.duplicate_plate(int(a.get("plate", 1)))
        if action == "plate_move":
            return {"order": proj.move_plate(int(a["plate"]), int(a.get("to", 1)))}
        if action == "scale_to_fit":
            return proj.scale_to_fit(a["object"], float(a.get("margin", 5)))
        if action == "fill_bed":
            out = proj.fill_bed(a["object"], float(a.get("spacing", 5)))
            if a.get("arrange", True) and slicers.find("orcaslicer"):
                proj.save()
                slicers.relayout("orcaslicer", proj.path, arrange=True)
                proj.__init__(proj.path)
            return out
        if action == "grid":
            return proj.grid(a["object"], int(a.get("nx", 2)), int(a.get("ny", 2)), float(a.get("spacing", 5)))
        if action == "presets":
            from .. import slicer_presets as SP

            if a.get("bundle"):
                data = SP.load_saved(s.out_dir, a["bundle"])
            elif a.get("machine") or a.get("process") or a.get("filament"):
                cur = proj.config
                data = SP.bundle("orcaslicer", a.get("machine") or cur.get("printer_settings_id"), a.get("process") or cur.get("print_settings_id"),
                                 a.get("filament") or (cur.get("filament_settings_id") or [None])[0])
            else:
                raise ToolError("invalid_input", "presets: machine, process, filament (preset names) or bundle.")
            return {"presets": proj.apply_presets(data.get("machine"), data.get("process"), [data["filament"]] if data.get("filament") else None)}
        if action == "instance":
            if a.get("remove"):
                return proj.remove_instance(a["object"], int(a.get("instance", 0)))
            out = None
            for _ in range(max(1, int(a.get("count", 1)))):
                out = proj.add_instance(a["object"], a.get("move"))
            return out
        if action == "printable":
            return proj.set_printable(a["object"], a.get("value", True), a.get("instance"))
        if action == "layer_heights":
            if a.get("adaptive") is not None:
                return {"layerHeights": proj.set_layer_heights(a["object"], adaptive(proj, a))}
            return {"layerHeights": proj.set_layer_heights(a["object"], a.get("profile") or [])}
        if action == "brim_ears":
            return {"brimEars": proj.set_brim_ears(a["object"], a.get("points") or [], a.get("radius"))}
        if action == "filament":
            if a.get("remove"):
                return {"filaments": proj.remove_filament(int(a["slot"]))}
            slot = int(a["slot"]) if a.get("slot") else proj.add_filament(int(a.get("like", 1)))
            return proj.set_filament(slot, a.get("color"), a.get("type"), **(a.get("settings") or {}))
        if action == "flush":
            return proj.set_flush(a.get("matrix"), a.get("multiplier"))
        if action == "plate_add":
            return {"plate": proj.add_plate(a.get("name"))}
        if action == "plate_remove":
            return {"plates": proj.remove_plate(int(a["plate"]))}
        if action == "mesh":
            return mesh_op(proj, a)
        if action == "strength":
            from .. import printstrength as PS

            o = proj.object(a["object"])
            eff = effective(proj, o)
            eff.setdefault("nozzle_diameter", proj.config.get("nozzle_diameter"))
            body = body_for(proj, o, a)
            return {"object": o["name"], "body": body.Label, **PS.describe(eff, body.Shape),
                    "next": f"fea_setup {{ body: \"{body.Label}\", printed: {{ project, object }} }} judges the strength study by these."}
        if action in ("arrange", "orient", "cli"):
            return cli_layout(proj, action, a)
        if action == "lay_flat":
            o = proj.object(a["object"])
            if a.get("face"):
                body = body_for(proj, o, a)
                f = body.Shape.Faces[int(str(a["face"]).replace("Face", "")) - 1]
                u0, u1, v0, v1 = f.ParameterRange
                n = f.normalAt((u0 + u1) / 2, (v0 + v1) / 2)
                normal = [n.x, n.y, n.z]
            elif a.get("normal"):
                normal = a["normal"]
            else:
                raise ToolError("invalid_input", "lay_flat: face (a FreeCAD face of the body, e.g. \"Face3\") or normal [x, y, z].")
            return proj.lay_flat(o["index"], normal, int(a.get("instance", 0)))
        if action == "emboss":
            return emboss(proj, a)
        raise ToolError("invalid_input", "action: " + ", ".join(ACTIONS) + ".")

    def load_models(a):
        """[(name, triangles)] from a model file (STL, OBJ, or anything the
        slicer reads: 3MF, STEP, DRC …), a ZIP of them, or one of the
        slicer's handy models."""
        import tempfile
        import zipfile

        if a.get("handy"):
            from .. import calibrate as C

            cd = C.calib_dir(slicers.find("orcaslicer"))
            hd = os.path.join(os.path.dirname(cd), "handy_models") if cd else None
            if not hd or not os.path.isdir(hd):
                raise ToolError("unavailable", "This OrcaSlicer has no handy models folder.")
            names = sorted(os.listdir(hd))
            hit = [n for n in names if n.lower().startswith(str(a["handy"]).lower())] or [n for n in names if str(a["handy"]).lower() in n.lower()]
            if not hit:
                raise ToolError("not_found", f"handy: one of {', '.join(os.path.splitext(n)[0] for n in names)}.")
            files = [os.path.join(hd, hit[0])]
        else:
            f = os.path.expanduser(a["file"])
            if not os.path.isabs(f):
                f = os.path.join(s.out_dir, f)
            if not os.path.isfile(f):
                raise ToolError("not_found", f"{f}: no such file.")
            files = [f]
            if f.lower().endswith(".zip"):
                d = tempfile.mkdtemp()
                with zipfile.ZipFile(f) as z:
                    for n in z.namelist():
                        if os.path.splitext(n)[1].lower() in (".stl", ".obj", ".3mf", ".step", ".stp", ".drc", ".amf") and not n.startswith("__MACOSX"):
                            dest = os.path.join(d, os.path.basename(n))
                            with z.open(n) as src, open(dest, "wb") as out:
                                out.write(src.read())
                files = sorted(os.path.join(d, n) for n in os.listdir(d))
        out = []
        for f in files:
            ext = os.path.splitext(f)[1].lower()
            if ext == ".stl":
                out.append((os.path.basename(f), O.read_stl(f)))
            elif ext == ".obj":
                out.append((os.path.basename(f), O.read_obj(f)))
            else:
                d = tempfile.mkdtemp()
                try:
                    made = slicers.export_project("orcaslicer", f, d, "stls")
                except (FileNotFoundError, RuntimeError) as e:
                    raise ToolError("failed", f"{os.path.basename(f)} could not be read: {e}")
                for m_ in made:
                    for root, _, fs in os.walk(m_) if os.path.isdir(m_) else [(os.path.dirname(m_), [], [os.path.basename(m_)])]:
                        for n in sorted(fs):
                            if n.lower().endswith(".stl"):
                                out.append((n, O.read_stl(os.path.join(root, n))))
        if not out:
            raise ToolError("not_found", "No model in it.")
        return out

    def primitive(a):
        import Part

        kind = a.get("primitive", "cube")
        d = a.get("size") or [20, 20, 20]
        d = d if isinstance(d, list) else [d] * 3
        shapes = {"cube": lambda: Part.makeBox(*d), "cylinder": lambda: Part.makeCylinder(d[0] / 2, d[2]), "sphere": lambda: Part.makeSphere(d[0] / 2),
                  "cone": lambda: Part.makeCone(d[0] / 2, 0, d[2]), "disc": lambda: Part.makeCylinder(d[0] / 2, d[2] if len(d) > 2 and a.get("size") else 2),
                  "torus": lambda: Part.makeTorus(d[0] / 2 - d[2] / 2, d[2] / 2)}
        if kind not in shapes:
            raise ToolError("invalid_input", f"primitive: {', '.join(shapes)}.")
        return _mesh_of(shapes[kind]())

    def adaptive(proj, a):
        """Layer heights from the object's slope, as the window's Adaptive
        button: thin where the surface is near flat, thick on walls.
        adaptive is the quality 0 (thickest) .. 1 (thinnest)."""
        o = proj.object(a["object"])
        pts_tris = []
        for p in o["parts"]:
            if p["kind"] != "normal_part":
                continue
            v, t, _ = proj.mesh(p)
            w = [O.apply(p["transform"], x) for x in v]
            pts_tris += [(w[i], w[j], w[k]) for i, j, k in t]
        if not pts_tris:
            raise ToolError("invalid_state", "the object has no solid part.")
        import math

        lo, hi = 0.08, 0.28
        for k in ("min_layer_height", "max_layer_height"):
            v = proj.config.get(k)
            v = v[0] if isinstance(v, list) and v else v
            if v is not None:
                lo, hi = (float(v), hi) if k.startswith("min") else (lo, float(v))
        if hi <= lo:
            nz = proj.config.get("nozzle_diameter") or ["0.4"]
            hi = 0.75 * float(nz[0] if isinstance(nz, list) else nz)
        q = float(a.get("adaptive") or 0.5)
        top = max(p[2] for t in pts_tris for p in t)
        bins = max(4, int(top / 0.5))
        slope = [0.0] * (bins + 1)
        for t in pts_tris:
            (ax, ay, az), (bx, by, bz), (cx, cy, cz) = t
            n = ((by - ay) * (cz - az) - (bz - az) * (cy - ay), (bz - az) * (cx - ax) - (bx - ax) * (cz - az), (bx - ax) * (cy - ay) - (by - ay) * (cx - ax))
            ln = math.sqrt(sum(x * x for x in n)) or 1
            nz = abs(n[2]) / ln
            if nz > 0.999:
                continue  # flat tops and bottoms take the even height
            for z in (az, bz, cz):
                i = min(bins, int(z / top * bins))
                slope[i] = max(slope[i], nz)
        out = []
        for i in range(bins + 1):
            h = hi - (hi - lo) * min(1.0, slope[i] * (0.5 + q))
            out.append([round(i * top / bins, 3), round(max(lo, min(hi, h)), 3)])
        return out

    def mesh_op(proj, a):
        """The window's mesh tools, done by FreeCAD's Mesh module on a part:
        simplify, fix, smooth, mirror, boolean, split, merge, cut."""
        import Mesh

        op = a.get("op")
        o = proj.object(a["object"])
        p = proj.part(o, a.get("part"))

        def to_mesh(part):
            v, t, _ = proj.mesh(part)
            w = [O.apply(part["transform"], x) for x in v]
            return Mesh.Mesh([[w[i], w[j], w[k]] for i, j, k in t])

        def tris(m):
            pts = [(q.x, q.y, q.z) for q in m.Points]
            return [[pts[i], pts[j], pts[k]] for i, j, k in m.Topology[1]]

        m = to_mesh(p)
        before = m.CountFacets
        if op == "simplify":
            m.decimate(float(a.get("tolerance", 0.05)), float(a.get("ratio", 0.5)))
        elif op == "fix":
            m.harmonizeNormals()
            m.removeDuplicatedPoints()
            m.removeDuplicatedFacets()
            m.fixDegenerations(1e-6)
            m.fixSelfIntersections()
            m.fillupHoles(1000)
        elif op == "smooth":
            m.smooth(int(a.get("iterations", 3)))
        elif op == "mirror":
            axis = a.get("axis", "x")
            k = "xyz".index(axis)
            s_ = [1, 1, 1]
            s_[k] = -1
            b = m.BoundBox
            c = [(b.XMin + b.XMax) / 2, (b.YMin + b.YMax) / 2, (b.ZMin + b.ZMax) / 2]
            pts = [tuple(c[i] + s_[i] * (q[i] - c[i]) for i in range(3)) for q in ((pp.x, pp.y, pp.z) for pp in m.Points)]
            m = Mesh.Mesh([[pts[i], pts[k_], pts[j]] for i, j, k_ in m.Topology[1]])  # flipped winding keeps normals out
        elif op == "boolean":
            other = proj.part(o, a["with"])
            om = to_mesh(other)
            mode = a.get("mode", "difference")
            m = {"union": m.unite, "difference": m.difference, "intersection": m.intersect}[mode](om)
            if a.get("consume", True):
                proj.remove_part(o["index"], a["with"])
        elif op == "cut":
            import MeshPart
            import Part

            sh = Part.Shape()
            sh.makeShapeFromMesh(m.Topology, 0.05)
            solid = Part.makeSolid(Part.makeShell(sh.Faces)) if sh.Faces else None
            if solid is None:
                raise ToolError("failed", "the part's mesh does not close: orca_project mesh { op: fix } first.")
            import FreeCAD

            bb = m.BoundBox
            axis = a.get("axis", "z")
            k = "xyz".index(axis)
            lo_ = (bb.XMin, bb.YMin, bb.ZMin)[k]
            z = float(a.get(axis, a.get("at", (lo_ + (bb.XMax, bb.YMax, bb.ZMax)[k]) / 2)))
            big = 2 * bb.DiagonalLength + 10
            keep = a.get("keep", "both")
            dims = [big, big, big]
            dims[k] = z - lo_ + 1
            org = [bb.XMin - big / 4, bb.YMin - big / 4, bb.ZMin - big / 4]
            org[k] = lo_ - 1
            half = Part.makeBox(*dims, FreeCAD.Vector(*org))
            lower = solid.common(half)
            upper = solid.cut(half)
            def mesh_s(x):
                mm = MeshPart.meshFromShape(Shape=x, LinearDeflection=0.02, AngularDeflection=0.1, Relative=False)
                return tris(mm)
            made = []
            if keep in ("both", "upper") and upper.Volume > 1e-6:
                r = proj.add_object(mesh_s(upper), f"{o['name']} upper", 1)
                made.append(r["object"])
            if keep in ("both", "lower") and lower.Volume > 1e-6:
                r = proj.add_object(mesh_s(lower), f"{o['name']} lower", 1)
                made.append(r["object"])
            proj.remove_object(o["index"])
            return {"cut": o["name"], "atZ": z, "objects": made, "next": "orca_project arrange lays them out."}
        elif op == "subdivide":
            return {**proj.subdivide(o["index"], a.get("part")), "op": op}
        elif op == "decompose_color":
            return {**proj.decompose_color(o["index"], a.get("part")), "op": op}
        elif op == "cut_grid":
            # Pieces of at most size [x, y] mm, as the command line's cut grid.
            import FreeCAD
            import MeshPart
            import Part

            sh = Part.Shape()
            sh.makeShapeFromMesh(m.Topology, 0.05)
            solid = Part.makeSolid(Part.makeShell(sh.Faces))
            bb = solid.BoundBox
            gx, gy = (a.get("size") or [50, 50])[:2]
            made = []
            nx, ny = max(1, int(-(-bb.XLength // gx))), max(1, int(-(-bb.YLength // gy)))
            for i in range(nx):
                for j in range(ny):
                    cell = Part.makeBox(gx, gy, bb.ZLength + 2, FreeCAD.Vector(bb.XMin + i * gx, bb.YMin + j * gy, bb.ZMin - 1))
                    piece = solid.common(cell)
                    if piece.Volume > 1e-6:
                        mm = MeshPart.meshFromShape(Shape=piece, LinearDeflection=0.02, AngularDeflection=0.1, Relative=False)
                        made.append(proj.add_object(tris(mm), f"{o['name']} {i + 1}-{j + 1}", 1)["object"])
            proj.remove_object(o["index"])
            return {"cut": o["name"], "objects": made, "next": "orca_project arrange lays them out."}
        elif op in ("split", "split_objects"):
            comps = m.getSeparateComponents()
            if len(comps) < 2:
                return {"object": o["name"], "pieces": 1, "note": "One piece: nothing to split."}
            if op == "split":
                for i, c in enumerate(comps[1:], 2):
                    proj.add_part(o["index"], tris(c), "normal_part", f"{p['name']} {i}")
                m = comps[0]
            else:
                made = [proj.add_object(tris(c), f"{o['name']} {i}", 1)["object"] for i, c in enumerate(comps, 1)]
                proj.remove_object(o["index"])
                return {"object": o["name"], "objects": made}
        elif op == "merge":
            # Other objects' solid parts brought into this one, where they stand.
            inst = O.invert(o["instances"][0]["transform"])
            names = []
            for w in a.get("objects") or []:
                oo = proj.object(w)
                for pp in oo["parts"]:
                    t = O.mul(inst, O.mul(oo["instances"][0]["transform"], pp["transform"]))
                    v, tt, _ = proj.mesh(pp)
                    wv = [O.apply(t, x) for x in v]
                    proj.add_part(o["index"], [[wv[i], wv[j], wv[k]] for i, j, k in tt], pp["kind"], pp["name"], {k: v for k, v in pp["settings"].items()} or None)
                names.append(oo["name"])
                proj.remove_object(oo["index"])
            return {"object": o["name"], "merged": names}
        else:
            raise ToolError("invalid_input", "op: simplify, fix, smooth, subdivide, mirror, boolean, cut (axis, at), cut_grid (size), split, split_objects, merge, decompose_color.")
        out = proj.replace_mesh(o["index"], a.get("part"), tris(m))
        return {**out, "op": op, "trianglesBefore": before}

    def emboss(proj, a):
        """Text or an SVG drawing raised on (normal_part), cut into
        (negative_part) or marking (modifier_part) an object: the window's
        Emboss and SVG tools, made by FreeCAD."""
        import FreeCAD
        import Part

        o = proj.object(a["object"])
        depth = float(a.get("depth", 1.0))
        if a.get("text"):
            font = a.get("font") or next((f for f in FONTS if os.path.isfile(f)), None)
            if not font:
                raise ToolError("unavailable", "No font found: font (a .ttf or .otf path).")
            wires = Part.makeWireString(str(a["text"]), font, float(a.get("size", 8)), 0)
            faces = []
            for ch in wires:
                ws = [w for w in ch if w.isClosed()]
                if ws:
                    try:
                        faces.append(Part.Face(ws, "Part::FaceMakerBullseye"))
                    except Exception:
                        faces += [Part.Face(w) for w in ws]
            if not faces:
                raise ToolError("failed", "The text gave no closed outlines.")
            flat = Part.makeCompound(faces)
        elif a.get("svg"):
            import importSVG

            doc = FreeCAD.newDocument("agentlink_svg")
            try:
                importSVG.insert(os.path.expanduser(a["svg"]), doc.Name)
                ws = [w for ob in doc.Objects if hasattr(ob, "Shape") for w in ob.Shape.Wires if w.isClosed()]
                flat = Part.makeCompound([Part.Face(w) for w in ws])
                if a.get("size"):
                    bb = flat.BoundBox
                    k = float(a["size"]) / max(bb.XLength, bb.YLength, 1e-6)
                    flat = flat.scale(k)
            finally:
                FreeCAD.closeDocument(doc.Name)
        else:
            raise ToolError("invalid_input", "emboss: text or svg (a path).")
        solid = flat.extrude(FreeCAD.Vector(0, 0, depth))
        bb = solid.BoundBox
        solid.translate(FreeCAD.Vector(-(bb.XMin + bb.XMax) / 2, -(bb.YMin + bb.YMax) / 2, 0))
        if a.get("rotate"):
            for axis, deg in zip(((1, 0, 0), (0, 1, 0), (0, 0, 1)), a["rotate"]):
                if deg:
                    solid.rotate(FreeCAD.Vector(), FreeCAD.Vector(*axis), deg)
        pts = proj.world_points(o, instance=None)
        b = O.bbox(pts)
        at = a.get("at") or [(b[0] + b[3]) / 2, (b[1] + b[4]) / 2, b[5] - (depth if a.get("kind") == "negative_part" else 0)]
        solid.translate(FreeCAD.Vector(*at))
        kind = a.get("kind", "normal_part")
        return proj.add_part(o["index"], _mesh_of(solid, 0.02), kind, a.get("name") or (f"text {a['text']}" if a.get("text") else "svg"), a.get("settings"))

    def cli_layout(proj, action, a):
        proj.save()
        try:
            slicers.relayout("orcaslicer", proj.path, arrange=action == "arrange", orient=action == "orient", options=a.get("options") or {})
        except FileNotFoundError as e:
            raise ToolError("unavailable", str(e))
        except RuntimeError as e:
            raise ToolError("failed", str(e))
        fresh = O.Project(proj.path)
        return {"done": action, "objects": [{"name": x["name"], "placed": x.get("placed")} for x in fresh.info()["objects"]]}

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

    def presets_for(label, work):
        from .slicing import DENSITY
        from .. import store

        setup = (store.setup_of(s.doc, label, "print") if label else None) or {}
        settings = {k: v for k, v in setup.items() if k not in ("id", "name", "type", "bodies")}
        mat = (store.materials(s.doc).get(label) if label else None) or "pla"
        settings["filament"], settings["density"] = mat.split("-")[0], DENSITY.get(mat, 1.24)
        chosen = setup.get("slicerPresets")
        profiles = {}
        if chosen:
            from .. import slicer_presets as SP

            data = SP.load_saved(s.out_dir, chosen["bundle"]) if chosen.get("bundle") else SP.bundle(chosen.get("slicer", "orcaslicer"), chosen["machine"], chosen["process"], chosen["filament"])
            profiles = SP.write_profiles(data, os.path.join(work, "presets"))
        return settings, profiles

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
        settings, profiles = presets_for(bodies[0].Label, work)
        profiles = a.get("profiles") or profiles
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
        tb = to_body(proj, o, p, body)
        shape = body.Shape
        want = set()
        for f in faces:
            try:
                want.add(int(str(f).replace("Face", "")) - 1)
            except ValueError:
                raise ToolError("invalid_input", f"faces: Face names like Face3, not {f!r}.")
        if a.get("clear"):
            want, state = set(), None
        # Each triangle goes to the face nearest its centre: the faces whose
        # (slightly grown) boxes hold the centre are measured exactly.
        import FreeCAD
        import Part

        tol = max(shape.BoundBox.DiagonalLength * 1e-3, 0.05)
        boxes = []
        for f in shape.Faces:
            bb = f.BoundBox
            boxes.append((bb.XMin - tol, bb.YMin - tol, bb.ZMin - tol, bb.XMax + tol, bb.YMax + tol, bb.ZMax + tol))
        old = attrs.get(O.PAINT[kind][0]) or [""] * len(tris)
        states, hit = [], 0
        names = O.PAINT[kind][1]
        for n, (i, j, k) in enumerate(tris):
            pts3 = [tb(verts[x]) for x in (i, j, k)]
            c = [sum(q[m] for q in pts3) / 3 for m in range(3)]
            cand = [fi for fi, bx in enumerate(boxes) if bx[0] <= c[0] <= bx[3] and bx[1] <= c[1] <= bx[4] and bx[2] <= c[2] <= bx[5]]
            bf = None
            if len(cand) == 1:
                bf = cand[0]
            elif cand:
                v = Part.Vertex(FreeCAD.Vector(*c))
                bf = min(cand, key=lambda fi: shape.Faces[fi].distToShape(v)[0])
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

    # ---- orca_calibrate --------------------------------------------------------------------
    def orca_calibrate(a):
        from .. import calibrate as C

        kind = a.get("kind")
        exe = slicers.find("orcaslicer")
        if not exe:
            raise ToolError("unavailable", "OrcaSlicer is not on this computer (AGENTLINK_ORCASLICER sets its path): its calibration models are its own.")
        cdir = C.calib_dir(exe)
        if not cdir:
            raise ToolError("unavailable", f"No calib folder beside {exe}: the calibration models ship with OrcaSlicer.")
        dest = path_of({"path": a.get("path") or f"calibrate-{kind}.3mf"}, must=False)
        work = os.path.splitext(dest)[0] + "-work"
        os.makedirs(work, exist_ok=True)
        settings, profiles = presets_for(a.get("body"), work)
        profiles = a.get("profiles") or profiles
        cfg = {}
        if profiles.get("machine"):
            import json as _j

            for k in ("machine", "process", "filament"):
                try:
                    with open(profiles[k], encoding="utf-8") as f:
                        cfg.update(_j.load(f))
                except (OSError, ValueError, KeyError):
                    pass
        try:
            pl = C.plan(kind, a.get("params") or {}, cfg)
        except ValueError as e:
            raise ToolError("invalid_input", str(e))
        model = os.path.join(cdir, pl["model"])
        if not os.path.isfile(model):
            raise ToolError("unavailable", f"This OrcaSlicer has no {pl['model']} (a different version): the calibration cannot be built from it.")
        try:
            slicers.make_project("orcaslicer", [model], dest, settings, profiles, {"arrange": True})
        except RuntimeError as e:
            raise ToolError("failed", str(e))
        proj = O.Project(dest)
        skipped = []
        try:
            if pl.get("trim"):
                trim(proj, 1, *pl["trim"])
            first = float(proj.config.get("initial_layer_print_height") or 0.2)
            objs = proj.objects()
            for o in objs:
                for k, v in pl["object"].items():
                    try:
                        proj.set_object(o["index"], k, v)
                    except O.ProjectError:
                        skipped.append(k)
                if pl.get("flow_from_name"):
                    r = C.flow_for(o["name"], pl["flow_from_name"])
                    if r is not None:
                        proj.set_object(o["index"], "print_flow_ratio", r)
            for k, v in pl["slot"].items():
                try:
                    proj.set(k, v, 1)
                except O.ProjectError:
                    skipped.append(k)
            for k, v in pl["project"].items():
                proj.set(k, v)
            for z, lines in pl["gcodes"]:
                proj.set_gcode(max(float(z), first), "custom", gcode=lines)
            for zmin, zmax, st in pl["ranges"]:
                proj.set_range(1, zmin, zmax, st)
            if pl.get("post"):
                proj.files[C.PLAN_FILE] = C.dump(pl)
        except O.ProjectError as e:
            raise ToolError("failed", f"The calibration project could not be set up: {e}")
        proj.save()
        out = {"project": dest, "kind": kind, "objects": [o["name"] for o in proj.objects()], "howToRead": pl["read"],
               "next": "orca_slice { path } slices it; printing it is the person's call."}
        if skipped:
            out["notInThisOrca"] = sorted(set(skipped))
        return out

    def trim(proj, which, low, high):
        """Keep an object's mesh between heights low and high (its own
        coordinates), set back on the bed — the window's cut, keep one side."""
        import FreeCAD
        import MeshPart
        import Part

        o = proj.object(which)
        p = proj.part(o, 1)
        v, t, _ = proj.mesh(p)
        w = [O.apply(p["transform"], x) for x in v]
        import Mesh

        m = Mesh.Mesh([[w[i], w[j], w[k]] for i, j, k in t])
        b = m.BoundBox
        if low <= b.ZMin + 1e-6 and high >= b.ZMax - 1e-6:
            return
        sh = Part.Shape()
        sh.makeShapeFromMesh(m.Topology, 0.05)
        solid = Part.makeSolid(Part.makeShell(sh.Faces))
        big = b.DiagonalLength * 2 + 10
        lo, hi = max(low, b.ZMin - 1), min(high, b.ZMax + 1)
        kept = solid.common(Part.makeBox(big, big, hi - lo, FreeCAD.Vector(b.XMin - big / 4, b.YMin - big / 4, b.ZMin + lo - b.ZMin)))
        mm = MeshPart.meshFromShape(Shape=kept, LinearDeflection=0.02, AngularDeflection=0.1, Relative=False)
        pts = [(q.x, q.y, q.z - lo) for q in mm.Points]
        proj.replace_mesh(o["index"], 1, [[pts[i], pts[j], pts[k]] for i, j, k in mm.Topology[1]])
        proj.place(o["index"])

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
                exe, found = slicers.slice_project("orcaslicer", p, out_dir, export=os.path.splitext(p)[0] + "-sliced.3mf" if a.get("keep", True) else None, plate=plate,
                                                   options=a.get("options"))
                from .. import calibrate as C

                pr = O.Project(p)
                if C.PLAN_FILE in pr.files:
                    import json as _j

                    post = (_j.loads(pr.files[C.PLAN_FILE]).get("post") or {})
                    if post.get("retraction"):
                        for g in found:
                            C.retract_bands(g, post["retraction"])
                res = {"gcode": found, **slicers.read_gcode(found[0]), **slicers.LAST}
                if os.path.isfile(os.path.splitext(p)[0] + "-sliced.3mf"):
                    res["sliced"] = os.path.splitext(p)[0] + "-sliced.3mf"
                res["next"] = "gcode_read { path } shows it layer by layer; starting the print is the person's call."
                return res
            if what == "amf":
                os.makedirs(out_dir, exist_ok=True)
                return {"export": "amf", "files": [O.Project(p).export_amf(os.path.join(out_dir, a.get("name") or "objects.amf"))]}
            if what == "obj":
                os.makedirs(out_dir, exist_ok=True)
                return {"export": "obj", "files": [O.Project(p).export_obj(os.path.join(out_dir, a.get("name") or "objects.obj"), a.get("plate"))]}
            if what.startswith("inspect_") or what == "info":
                return {"export": what, "report": slicers.inspect("orcaslicer", p, what.replace("inspect_", ""))}
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
        want = a.get("layer") is not None or a.get("z") is not None or a.get("svg") or a.get("move") is not None
        view = a.get("view", "feature")
        if view not in G.VIEWS:
            raise ToolError("invalid_input", f"view: {', '.join(G.VIEWS)}.")
        if a.get("toolpaths"):
            data = G.read(p, keep_paths="all")
            out = G.toolpaths_obj(data, os.path.splitext(p)[0] + "-toolpaths.obj")
            return {"export": "toolpaths", **out}
        data = G.read(p)
        out = G.summary(data)
        out["legend"] = G.legend(data, view)
        if want:
            lay = G.layer(data, a.get("layer"), a.get("z"))
            if lay:
                data = G.read(p, keep_paths={lay["layer"]})
                lay = G.layer(data, lay["layer"])
            out["layer"] = lay
            if a.get("move") is not None and lay:
                out["move"] = G.move(data, lay["layer"], int(a["move"]))
            if a.get("svg") and lay:
                svg = os.path.splitext(p)[0] + f"-layer{lay['layer']}-{view}.svg"
                with open(svg, "w", encoding="utf-8") as f:
                    f.write(G.svg(data, lay["layer"], view, show=tuple(a.get("show") or ())))
                out["svg"] = svg
        return out

    return [
        Tool(
            "orca_project",
            "OrcaSlicer projects (.3mf), headless — everything the window keeps. action new { bodies, path } | info { path, object } | set { path, scope project|object|part|range|plate, key, value | settings, object, part, slot, zmin, zmax, plate } | part_add | part_remove | replace { object, body | file } (the body changed: its mesh follows, settings stay) | object_add | object_remove | copy | instance | instance_to_object | printable | place | lay_flat | emboss | scale_to_fit | fill_bed | grid | mesh { op } | layer_heights | gcode { z, kind } | brim_ears | filament | flush | presets | plate | plate_add | plate_remove | plate_duplicate | plate_move | arrange | orient | cli | strength { object } | settings { query } (every setting's key, type, limits, values). Values checked before saving; saveAs writes a copy. guide_read { topic: \"orca\" } has each action's arguments.",
            obj({"action": {"type": "string", "enum": ACTIONS},
                 "path": {"type": "string"}, "saveAs": {"type": "string"}, "body": {"type": "string"}, "bodies": {"type": "array", "items": {"type": "string"}}, "of": {"type": "string"},
                 "object": {"type": ["string", "integer"]}, "part": {"type": ["string", "integer"]}, "scope": {"type": "string", "enum": ["project", "object", "part", "range", "plate"]},
                 "key": {"type": "string"}, "value": {}, "settings": {"type": "object"}, "slot": {"type": "integer"}, "zmin": {"type": "number"}, "zmax": {"type": "number"},
                 "plate": {"type": "integer"}, "kind": {"type": "string"}, "box": VEC, "at": VEC, "name": {"type": "string"}, "instance": {"type": "integer"},
                 "move": VEC, "rotate": VEC, "scale": {}, "drop": {"type": "boolean"}, "z": {"type": "number"}, "gcode": {"type": "string"}, "extruder": {"type": "integer"},
                 "color": {"type": "string"}, "remove": {"type": "boolean"}, "orient": {"type": "boolean"}, "arrange": {"type": "boolean"}, "profiles": {"type": "object"},
                 "query": {"type": "string"}, "preset": {"type": "string", "enum": ["process", "machine", "filament"]}, "limit": {"type": "integer"},
                 "primitive": {"type": "string"}, "size": {}, "count": {"type": "integer"}, "profile": {"type": "array", "items": {"type": "array", "items": {"type": "number"}}}, "adaptive": {"type": "number"}, "points": {"type": "array", "items": {"type": "array", "items": {"type": "number"}}},
                 "radius": {"type": "number"}, "type": {"type": "string"}, "like": {"type": "integer"}, "matrix": {"type": "array", "items": {"type": "array", "items": {"type": "number"}}}, "multiplier": {"type": "number"},
                 "op": {"type": "string"}, "with": {"type": ["string", "integer"]}, "mode": {"type": "string"}, "consume": {"type": "boolean"}, "ratio": {"type": "number"},
                 "tolerance": {"type": "number"}, "iterations": {"type": "integer"}, "axis": {"type": "string"}, "keep": {"type": "string"}, "objects": {"type": "array", "items": {"type": ["string", "integer"]}}, "options": {"type": "object"},
                 "file": {"type": "string"}, "handy": {"type": "string"}, "to": {"type": "integer"}, "margin": {"type": "number"}, "spacing": {"type": "number"},
                 "nx": {"type": "integer"}, "ny": {"type": "integer"}, "bundle": {"type": "string"}, "machine": {"type": "string"}, "process": {"type": "string"},
                 "filament": {"type": "string"}, "face": {"type": "string"}, "normal": VEC, "text": {"type": "string"}, "svg": {"type": "string"}, "depth": {"type": "number"}, "font": {"type": "string"}}),
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
            "orca_calibrate",
            "OrcaSlicer's calibration prints, headless, from its own test models: kind temperature (params start, end; 5 °C blocks), pressure_advance (start, end, step), retraction (start, end, step mm), flow (pass 1|2|\"linear\"|\"linear_fine\"), max_flow (start, end, step mm³/s), vfa (start, end, step mm/s), input_shaping_freq / input_shaping_damp (start, end, step), cornering (start, end, step). { kind, params, body (its print setup's presets; minimal otherwise), path }. Gives the project and how to read the print; orca_slice slices it.",
            obj({"kind": {"type": "string", "enum": ["temperature", "pressure_advance", "retraction", "flow", "max_flow", "vfa", "input_shaping_freq", "input_shaping_damp", "cornering"]},
                 "params": {"type": "object"}, "body": {"type": "string"}, "path": {"type": "string"}, "profiles": {"type": "object"}}, ["kind"]),
            orca_calibrate,
        ),
        Tool(
            "orca_slice",
            "Slice an OrcaSlicer project as it stands, by its command line (headless): { path, plate (0 all), keep (the sliced .3mf, default true) } gives time, filament, warnings and statistics; export stl|stls|obj|amf|settings|slicedata|3mf writes those instead, inspect_mesh|inspect_paint|info gives Orca's own report; options (timelapse, skip_objects \"3,5\", allow_mix_temp, allow_multicolor_oneplate, no_check, mtcpp, mstpp) go to the slice.",
            obj({"path": {"type": "string"}, "plate": {"type": "integer"}, "keep": {"type": "boolean"}, "export": {"type": "string", "enum": ["gcode", "stl", "stls", "obj", "amf", "settings", "slicedata", "3mf", "inspect_mesh", "inspect_paint", "info"]}, "options": {"type": "object"}, "name": {"type": "string"}}, ["path"]),
            orca_slice,
        ),
        Tool(
            "gcode_read",
            "Read sliced G-code (a .gcode, or a sliced .3mf) as the slicer's preview shows it: totals, a legend in view feature|height|width|speed|actual_speed|fan|temperature|flow|actual_flow|layer_time|tool|acceleration, one layer { layer | z } (features, time, retracts, wipes, seams), move n (the move slider: that move and its G-code line), svg: true draws the layer (show travel|wipe|retract|seams), toolpaths: true writes the extrusions as an OBJ.",
            obj({"path": {"type": "string"}, "layer": {"type": "integer"}, "z": {"type": "number"}, "view": {"type": "string"}, "svg": {"type": "boolean"}, "plate": {"type": "integer"},
                 "move": {"type": "integer"}, "show": {"type": "array", "items": {"type": "string", "enum": ["travel", "wipe", "retract", "seams"]}}, "toolpaths": {"type": "boolean"}}, ["path"]),
            gcode_read,
        ),
    ]
