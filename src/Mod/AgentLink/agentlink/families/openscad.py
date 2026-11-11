# SPDX-License-Identifier: LGPL-2.1-or-later
# FreeCAD's OpenSCAD workbench headless: CSG files in (and .scad through the
# OpenSCAD program), OpenSCAD code run straight into the document, objects
# written out as CSG for OpenSCAD, and hull / Minkowski sums through OpenSCAD
# — the convex hull computed here when OpenSCAD is not installed.

import contextlib
import os

import FreeCAD
import Part

from ..protocol import Tool, ToolError
from ..tools import obj
from . import picks

GROUP = "openscad"
INFO = "OpenSCAD: CSG and SCAD files in, OpenSCAD code run into the document, CSG out, hull and Minkowski sums."


def bad(msg):
    return ToolError("invalid_input", msg)


@contextlib.contextmanager
def console_mode():
    """The workbench's importers reach for view objects when FreeCAD says a
    window is up (Agent Link's headless renderer loads FreeCADGui)."""
    up = FreeCAD.GuiUp
    FreeCAD.GuiUp = False
    # A CSG file can call OpenSCAD (hull, minkowski, import): FreeCAD then
    # searches for it through Finder on a Mac — kept to the plain search here.
    try:
        import OpenSCADUtils as U

        search, U.searchforopenscadexe = U.searchforopenscadexe, (lambda: exe())
    except ImportError:
        U = None
    try:
        yield
    finally:
        FreeCAD.GuiUp = up
        if U is not None:
            U.searchforopenscadexe = search


PREF = "User parameter:BaseApp/Preferences/Mod/OpenSCAD"


def exe():
    """The OpenSCAD program: FreeCAD's preference, the PATH, or the usual app
    folders. Never FreeCAD's own search, which on a Mac asks Finder through
    AppleScript and waits on a permission prompt."""
    import shutil

    cands = [FreeCAD.ParamGet(PREF).GetString("openscadexecutable"), shutil.which("openscad"),
             "/Applications/OpenSCAD.app/Contents/MacOS/OpenSCAD", os.path.expanduser("~/Applications/OpenSCAD.app/Contents/MacOS/OpenSCAD"),
             "/opt/homebrew/bin/openscad", "/usr/local/bin/openscad", "/usr/bin/openscad"]
    for p in cands:
        if p and os.path.isfile(p):
            # FreeCAD's own calls then find it without searching.
            FreeCAD.ParamGet(PREF).SetString("openscadexecutable", p)
            return p
    return None


def need_exe(what):
    p = exe()
    if not p:
        raise ToolError("unavailable", f"{what} needs the OpenSCAD program, not found on this computer: the person installs it (openscad.org) "
                        "or sets its path in FreeCAD's OpenSCAD preferences.")
    return p


def hull_here(shapes, tol=0.05):
    """The convex hull of shapes' points (tessellated) as a solid."""
    import numpy as np

    try:
        from scipy.spatial import ConvexHull
    except ImportError:
        raise ToolError("unavailable", "A hull without OpenSCAD needs scipy in FreeCAD's Python.") from None
    pts = []
    for sh in shapes:
        v, _f = sh.tessellate(tol)
        pts += [(p.x, p.y, p.z) for p in v]
        pts += [(p.Point.x, p.Point.y, p.Point.z) for p in sh.Vertexes]
    P = np.unique(np.round(np.array(pts, float), 9), axis=0)
    if len(P) < 4:
        raise bad("objects: not enough points for a solid hull.")
    h = ConvexHull(P)
    faces = []
    for simplex, eq in zip(h.simplices, h.equations):
        a, b, c = (FreeCAD.Vector(*P[i]) for i in simplex)
        if (b - a).cross(c - a).dot(FreeCAD.Vector(*eq[:3])) < 0:
            b, c = c, b
        faces.append(Part.Face(Part.makePolygon([a, b, c, a])))
    sol = Part.makeSolid(Part.makeShell(faces))
    try:
        sol = sol.removeSplitter()
    except Exception:
        pass
    return sol


def tools(session):
    s = session
    doc = lambda: s.doc  # noqa: E731

    def where(p):
        if not p:
            raise bad("path: a file.")
        return p if os.path.isabs(p) else os.path.join(s.out_dir, p)

    def made_since(before):
        out = []
        for o in doc().Objects:
            if o.Name in before:
                continue
            e = {"object": o.Label, "type": o.TypeId}
            sh = getattr(o, "Shape", None)
            if isinstance(sh, Part.Shape) and not sh.isNull():
                e["solids"] = len(sh.Solids)
                if sh.Solids:
                    e["volume"] = round(sh.Volume, 3)
            out.append(e)
        # the top of what came in: objects nothing else made uses
        tops = [o.Label for o in doc().Objects if o.Name not in before and not any(p.Name not in before for p in o.InList)]
        return out, tops

    def scad_import(a):
        p = where(a.get("path"))
        if not os.path.isfile(p):
            raise ToolError("not_found", f"{p}: no such file.")
        ext = os.path.splitext(p)[1].lower()
        if ext not in (".csg", ".scad"):
            raise bad("path: a .csg or .scad file.")
        if ext == ".scad":
            need_exe("A .scad file")
        before = {o.Name for o in doc().Objects}
        try:
            import importCSG

            with console_mode():
                importCSG.insert(p, doc().Name)
        except ImportError as e:
            raise ToolError("unavailable", f"FreeCAD's CSG importer is not loadable here ({e}).") from None
        except Exception as e:
            raise ToolError("failed", f"{os.path.basename(p)} could not be read: {e}") from None
        doc().recompute()
        objs, tops = made_since(before)
        if not objs:
            raise ToolError("failed", f"{os.path.basename(p)}: nothing came in.")
        s.snapshot("scad_import")
        return {"path": p, "objects": len(objs), "top": tops, "made": objs[:40], "step": len(s.steps)}

    def scad_code(a):
        code = a.get("code")
        if not isinstance(code, str) or not code.strip():
            raise bad("code: OpenSCAD source, e.g. \"difference() { cube(20, center=true); sphere(13); }\".")
        need_exe("OpenSCAD code")
        import OpenSCADUtils as U

        try:
            with console_mode():
                out = U.callopenscadstring(code, "csg")
        except Exception as e:
            raise ToolError("failed", f"OpenSCAD refused the code: {e}") from None
        if not out or not os.path.isfile(out):
            raise ToolError("failed", "OpenSCAD made nothing from the code (an error in it, or no geometry at the top level).")
        before = {o.Name for o in doc().Objects}
        try:
            import importCSG

            with console_mode():
                importCSG.insert(out, doc().Name)
        finally:
            try:
                os.unlink(out)
            except OSError:
                pass
        doc().recompute()
        objs, tops = made_since(before)
        if a.get("name") and len(tops) == 1:
            top = next(o for o in doc().Objects if o.Label == tops[0])
            top.Label = s.unique(a["name"], top)
            tops = [top.Label]
        s.snapshot("scad_code")
        return {"top": tops, "objects": len(objs), "made": objs[:40], "step": len(s.steps)}

    def scad_export(a):
        names = a.get("objects") or []
        objs = [picks.find(doc(), n) for n in names] if names else [s.body(None)]
        p = where(a.get("path") or (objs[0].Label.replace(" ", "_") + ".csg"))
        if not p.lower().endswith((".csg", ".scad")):
            p += ".csg"
        os.makedirs(os.path.dirname(p), exist_ok=True)
        try:
            import exportCSG

            with console_mode():
                exportCSG.export(objs, p)
        except Exception as e:
            raise ToolError("failed", f"CSG export failed: {e}") from None
        with open(p, encoding="utf-8", errors="replace") as f:
            text = f.read()
        poly = text.count("polyhedron(")
        out = {"objects": [o.Label for o in objs], "path": p, "bytes": os.path.getsize(p), "lines": text.count("\n")}
        if poly:
            out["note"] = f"{poly} object(s) went out as polyhedra (triangles): OpenSCAD has no equivalent for those features."
        return out

    def scad_op(a):
        op = a.get("op", "hull")
        if op not in ("hull", "minkowski"):
            raise bad("op: hull | minkowski.")
        objs = [picks.find(doc(), n) for n in (a.get("objects") or [])]
        if len(objs) < (1 if op == "hull" else 2):
            raise bad("objects: " + ("one or more bodies" if op == "hull" else "two bodies (the shape, then what sweeps round it)") + ".")
        how = "OpenSCAD"
        if exe():
            import OpenSCADUtils as U

            try:
                with console_mode():
                    res = U.process_ObjectsViaOpenSCAD(doc(), objs, op)
            except Exception as e:
                raise ToolError("failed", f"OpenSCAD's {op} failed: {e}") from None
            if res is None:
                raise ToolError("failed", f"OpenSCAD's {op} made nothing: mix of 2D and 3D, or OpenSCAD's own error.")
        elif op == "hull":
            res = doc().addObject("Part::Feature", "Hull")
            res.Shape = hull_here([o.Shape for o in objs])
            how = "here (OpenSCAD not installed): the hull of the bodies' points"
        else:
            need_exe("A Minkowski sum")
        res.Label = s.unique(a.get("name") or op.capitalize(), res)
        doc().recompute()
        s.snapshot("scad_op")
        sh = res.Shape
        return {"object": res.Label, "op": op, "of": [o.Label for o in objs], "made": how, "volume": round(sh.Volume, 3) if sh.Solids else None,
                "faces": len(sh.Faces), "step": len(s.steps)}

    def scad_status(a):
        p = exe()
        out = {"openscad": p}
        if p:
            try:
                import OpenSCADUtils as U

                out["version"] = U.getopenscadversion(p)  # given the path: no search
            except Exception as e:
                out["version"] = f"unknown ({e})"
        else:
            out["note"] = "OpenSCAD is not installed: .csg import, CSG export and hull work; .scad files, scad_code and Minkowski need it."
        try:
            import ply  # noqa: F401

            out["csgParser"] = True
        except ImportError:
            out["csgParser"] = False
            out["warning"] = "The CSG parser (ply) is missing from FreeCAD's Python: CSG import will not run."
        return out

    return [
        Tool("scad_status", "Whether the OpenSCAD program is installed (and which), and what works without it. Changes nothing.", obj({}), scad_status),
        Tool("scad_import", "Read an OpenSCAD file: .csg (headless) or .scad (through OpenSCAD) as FreeCAD's parametric primitives and booleans where it can, meshes where not.",
             obj({"path": {"type": "string"}}, ["path"]), scad_import, changes=True),
        Tool("scad_code", "Run OpenSCAD code into the document (code, e.g. \"difference() { cube(20, center=true); sphere(13); }\"): it comes in as FreeCAD objects. Needs OpenSCAD.",
             obj({"code": {"type": "string"}, "name": {"type": "string"}}, ["code"]), scad_code, changes=True),
        Tool("scad_export", "Write objects (the one body when left out) as OpenSCAD CSG (.csg or .scad): primitives and booleans as OpenSCAD's own, the rest as polyhedra.",
             obj({"objects": {"type": "array", "items": {"type": "string"}}, "path": {"type": "string"}}), scad_export),
        Tool("scad_op", "OpenSCAD's hull (the shape stretched round bodies, like shrink-wrap) or minkowski (a shape swept round another: rounded edges all over) of objects. Hull works without OpenSCAD.",
             obj({"op": {"type": "string", "enum": ["hull", "minkowski"]}, "objects": {"type": "array", "items": {"type": "string"}}, "name": {"type": "string"}}, ["objects"]), scad_op, changes=True),
    ]
