# SPDX-License-Identifier: LGPL-2.1-or-later
# The agent's work, shown as it happens in the FreeCAD window — as the
# ScanRuler fork shows it in the browser: for each call the workbench it
# belongs to comes up, the toolbar button that does the same job lights,
# what it works on is selected, a caption on the 3D view says what is being
# done, and afterwards what it made is selected and the body made active.
# Every call goes into an activity list (the Agent Link panel's Activity
# tab). Paced so the person can follow (Mod/AgentLink EchoDelay, ms;
# Echo off for none), and never in the way of the result: a failure here is
# logged, the call goes on.

import datetime
import time

import FreeCAD

PREFS = "User parameter:BaseApp/Preferences/Mod/AgentLink"

# Tool families → the workbench a person would be in.
WORKBENCH = [
    ("pd_", "PartDesignWorkbench"), ("sketch_", "SketcherWorkbench"), ("feature_", "PartDesignWorkbench"),
    ("part_", "PartWorkbench"), ("bim_", "BIMWorkbench"), ("ifc_", "BIMWorkbench"), ("fea_", "FemWorkbench"),
    ("td_", "TechDrawWorkbench"), ("drawing_", "TechDrawWorkbench"), ("draft_", "DraftWorkbench"),
    ("asm_", "AssemblyWorkbench"), ("cam_", "CAMWorkbench"), ("meshobj_", "MeshWorkbench"),
    ("sheet_", "SpreadsheetWorkbench"), ("insp_", "InspectionWorkbench"), ("points_", "PointsWorkbench"),
    ("surface_", "SurfaceWorkbench"), ("robot_", "RobotWorkbench"), ("scad_", "OpenSCADWorkbench"),
    ("material_", "MaterialWorkbench"),
]

# Tools → the FreeCAD command whose button does the same job.
COMMAND = {
    "pd_body_new": "PartDesign_Body", "sketch_new": "PartDesign_NewSketch", "pd_pad": "PartDesign_Pad",
    "pd_pocket": "PartDesign_Pocket", "pd_revolution": "PartDesign_Revolution", "pd_groove": "PartDesign_Groove",
    "pd_hole": "PartDesign_Hole", "pd_fillet": "PartDesign_Fillet", "pd_chamfer": "PartDesign_Chamfer",
    "pd_draft": "PartDesign_Draft", "pd_thickness": "PartDesign_Thickness", "pd_mirrored": "PartDesign_Mirrored",
    "pd_linear_pattern": "PartDesign_LinearPattern", "pd_polar_pattern": "PartDesign_PolarPattern",
    "pd_multitransform": "PartDesign_MultiTransform", "pd_loft": "PartDesign_AdditiveLoft", "pd_pipe": "PartDesign_AdditivePipe",
    "pd_helix": "PartDesign_AdditiveHelix", "pd_boolean": "PartDesign_Boolean", "pd_datum_plane": "PartDesign_Plane",
    "pd_binder": "PartDesign_ShapeBinder", "pd_primitive": "PartDesign_CompPrimitiveAdditive",
    "part_primitive": "Part_Primitives", "part_boolean": "Part_Boolean", "part_fillet": "Part_Fillet",
    "part_chamfer": "Part_Chamfer", "part_extrude": "Part_Extrude", "part_revolve": "Part_Revolve",
    "part_loft": "Part_Loft", "part_sweep": "Part_Sweep", "part_offset": "Part_Offset", "part_thickness": "Part_Thickness",
    "part_section": "Part_Section", "part_mirror": "Part_Mirror", "part_compound": "Part_Compound",
    "bim_wall": "BIM_Wall", "bim_structure": "BIM_Structure", "bim_roof": "BIM_Roof", "bim_stairs": "BIM_Stairs",
    "bim_opening": "BIM_Window", "bim_space": "BIM_Space", "bim_level": "BIM_Level",
    "fea_setup": "FEM_Analysis", "fea_hold": "FEM_ConstraintFixed", "fea_load": "FEM_ConstraintForce",
    "fea_solve": "FEM_SolverRun", "fea_native": "FEM_Analysis",
    "td_page": "TechDraw_PageDefault", "td_view": "TechDraw_View", "td_projection": "TechDraw_ProjectionGroup",
    "td_section": "TechDraw_SectionView", "td_detail": "TechDraw_DetailView", "td_dim": "TechDraw_LengthDimension",
    "draft_text": "Draft_Text", "draft_array": "Draft_OrthoArray", "draft_clone": "Draft_Clone",
    "draft_dimension": "Draft_Dimension", "draft_label": "Draft_Label",
    "asm_new": "Assembly_CreateAssembly", "asm_add": "Assembly_InsertLink", "asm_joint": "Assembly_CreateJointFixed",
    "asm_explode": "Assembly_CreateView", "asm_bom": "Assembly_CreateBom",
    "cam_job": "CAM_Job", "cam_post": "CAM_Post", "cam_sim": "CAM_SimulatorGL",
    "measure": "Std_Measure", "section": "Part_Section", "doc_save": "Std_Save", "export_file": "Std_Export",
    "history_undo": "Std_Undo", "history_redo": "Std_Redo", "recompute": "Std_Refresh",
}

# Read-only tools that still show something (a view, a picture).
SHOWN = ("view_", "measure", "clearance", "section", "body_check", "dfm_check", "part_review", "steps_render", "note_")

# What a tool's arguments and answer name: the objects to select.
ARG_KEYS = ("body", "sketch", "object", "base", "target", "feature", "page")
OUT_KEYS = ("feature", "sketch", "object", "body", "created", "page", "view")

_log = []
_listeners = []
LOG_MAX = 200


def enabled():
    return FreeCAD.GuiUp and FreeCAD.ParamGet(PREFS).GetBool("Echo", True)


def delay_ms():
    return FreeCAD.ParamGet(PREFS).GetInt("EchoDelay", 350)


def log():
    return list(_log)


def listen(fn):
    _listeners.append(fn)


def _note(entry):
    _log.append(entry)
    del _log[:-LOG_MAX]
    for fn in list(_listeners):
        try:
            fn(entry)
        except Exception:
            pass


def _qt():
    from PySide import QtCore, QtGui

    QtW = getattr(__import__("PySide", fromlist=["QtWidgets"]), "QtWidgets", QtGui)
    return QtCore, QtGui, QtW


def _wait(ms):
    """Let the window paint for ms without blocking it."""
    if ms <= 0:
        return
    QtCore, _QtGui, _QtW = _qt()
    loop = QtCore.QEventLoop()
    QtCore.QTimer.singleShot(int(ms), loop.quit)
    loop.exec_() if hasattr(loop, "exec_") else loop.exec()


PREFIX_NAME = {"pd": "Part Design", "part": "Part", "sketch": "Sketch", "bim": "BIM", "ifc": "IFC", "fea": "FEM",
               "td": "TechDraw", "draft": "Draft", "asm": "Assembly", "cam": "CAM", "meshobj": "Mesh", "sheet": "Spreadsheet",
               "insp": "Inspection", "points": "Points", "surface": "Surface", "note": "Note", "steps": "Steps", "view": "View",
               "doc": "Document", "object": "Object", "material": "Material", "code": "Code check", "slice": "Slice"}
SKIP_ARGS = ("calls", "body", "inline", "name", "rollback", "stopOnError")


def _value(v):
    if isinstance(v, dict):
        v = ", ".join(f"{k} {_value(x)}" for k, x in list(v.items())[:3])
    elif isinstance(v, list):
        v = "[" + ", ".join(_value(x) for x in v[:4]) + ("…" if len(v) > 4 else "") + "]"
    elif isinstance(v, float):
        v = f"{v:g}"
    s = str(v)
    return s if len(s) <= 40 else s[:37] + "…"


def _describe(name, args):
    """A tool call in words a person reads at a glance: 'Part Design ›
    primitive — kind cylinder, size radius 6, height 8 (Panel test)'."""
    head, _, rest = name.partition("_")
    area = PREFIX_NAME.get(head)
    what = (rest or head).replace("_", " ")
    title = f"{area} › {what}" if area else name.replace("_", " ")
    bits = [f"{k} {_value(v)}" for k, v in (args or {}).items() if k not in SKIP_ARGS and v not in (None, "", [], {})][:3]
    on = (args or {}).get("body")
    return title + (" — " + "; ".join(bits) if bits else "") + (f" ({on})" if isinstance(on, str) else "")


def _view_widget():
    import FreeCADGui

    QtCore, _QtGui, QtW = _qt()
    mw = FreeCADGui.getMainWindow()
    area = mw.findChild(QtW.QMdiArea)
    sub = area.activeSubWindow() if area else None
    return sub.widget() if sub else None


_caption = None


def caption(text, ms=2600):
    """A line of text over the 3D view, bottom left, fading after ms."""
    global _caption
    QtCore, _QtGui, QtW = _qt()
    host = _view_widget()
    if host is None:
        return
    if _caption is None or _caption.parent() is not host:
        _caption = QtW.QLabel(host)
        _caption.setStyleSheet("QLabel { background: rgba(20,22,26,200); color: #f2f2f2; border-radius: 6px; padding: 6px 10px; font-size: 13px; }")
        _caption.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents)
    _caption.setText("✦ Agent: " + text)
    _caption.setWordWrap(True)
    _caption.setMaximumWidth(max(200, host.width() - 28))
    _caption.adjustSize()
    _caption.move(14, max(14, host.height() - _caption.height() - 14))
    _caption.show()
    _caption.raise_()
    lbl = _caption
    QtCore.QTimer.singleShot(ms, lambda: lbl.isVisible() and lbl.text().endswith(text) and lbl.hide())


def flash_command(cmd, ms=1300):
    """Light the toolbar button (and menu item) that runs cmd."""
    import FreeCADGui

    QtCore, _QtGui, QtW = _qt()
    mw = FreeCADGui.getMainWindow()
    for act in mw.findChildren(QtW.QAction if hasattr(QtW, "QAction") else _QtGui.QAction):
        if act.objectName() != cmd:
            continue
        widgets = act.associatedWidgets() if hasattr(act, "associatedWidgets") else act.associatedObjects()
        for w in widgets:
            if isinstance(w, QtW.QToolButton):
                old = w.styleSheet()
                w.setStyleSheet("QToolButton { background: rgba(255,150,40,0.55); border: 1px solid #ff9628; border-radius: 4px; }")
                QtCore.QTimer.singleShot(ms, lambda w=w, old=old: w.setStyleSheet(old))


def _objects(doc, names):
    out = []
    for n in names:
        if not isinstance(n, str):
            continue
        o = doc.getObject(n) or next((x for x in doc.Objects if x.Label == n), None)
        if o is not None and o not in out:
            out.append(o)
    return out


def _select(objs):
    import FreeCADGui

    if not objs:
        return
    FreeCADGui.Selection.clearSelection()
    for o in objs:
        try:
            FreeCADGui.Selection.addSelection(o)
        except Exception:
            pass


def _workbench_for(name):
    for prefix, wb in WORKBENCH:
        if name.startswith(prefix):
            return wb
    return None


class Echo:
    def __init__(self, session):
        self.s = session
        self.t0 = None

    def before(self, tool, args):
        self.t0 = time.time()
        if tool is None or tool.name in ("batch", "session_state", "tool_groups", "guide_read"):
            return
        if not enabled():
            return
        shown = tool.changes or tool.name.startswith(SHOWN)
        if not shown:
            return
        try:
            import FreeCADGui

            wb = _workbench_for(tool.name)
            if wb and FreeCADGui.activeWorkbench().name() != wb and wb in FreeCADGui.listWorkbenches():
                FreeCADGui.activateWorkbench(wb)
            caption(_describe(tool.name, args))
            FreeCADGui.getMainWindow().statusBar().showMessage("Agent: " + _describe(tool.name, args), 4000)
            cmd = COMMAND.get(tool.name)
            if cmd:
                flash_command(cmd)
            doc = self.s.doc
            _select(_objects(doc, [args.get(k) for k in ARG_KEYS if args.get(k)]))
            FreeCADGui.updateGui()
            _wait(delay_ms())
        except Exception as e:
            FreeCAD.Console.PrintLog(f"Agent Link echo (before {tool.name}): {e}\n")

    def after(self, tool, args, reply):
        name = tool.name if tool is not None else "?"
        ok = not (reply or {}).get("isError")
        summary = ""
        try:
            import json

            body = json.loads(reply["content"][0]["text"]) if reply and reply.get("content") else {}
            if not ok:
                summary = (body.get("error") or {}).get("message", "")[:160]
            else:
                for k in ("feature", "body", "object", "sketch", "page", "note", "ran", "path"):
                    if k in body:
                        summary = f"{k}: {str(body[k])[:80]}"
                        break
        except Exception:
            body = {}
        if name != "batch":
            _note({"time": datetime.datetime.now().strftime("%H:%M:%S"), "tool": name, "ok": ok, "what": _describe(name, args), "result": summary,
                   "ms": int((time.time() - (self.t0 or time.time())) * 1000)})
        if not (enabled() and ok and tool is not None and tool.changes) or name == "batch":
            return
        try:
            import FreeCADGui

            doc = self.s.doc
            made = _objects(doc, [body.get(k) for k in OUT_KEYS if isinstance(body.get(k), str)])
            if made:
                _select(made)
                body_obj = next((o for o in made if o.TypeId == "PartDesign::Body"), None)
                if body_obj is None:
                    body_obj = next((p for o in made for p in o.InList if p.TypeId == "PartDesign::Body"), None)
                gv = FreeCADGui.ActiveDocument.ActiveView if FreeCADGui.ActiveDocument else None
                if body_obj is not None and gv is not None:
                    gv.setActiveObject("pdbody", body_obj)
            # Follow the work: the view framed on the part after each change
            # (Mod/AgentLink Follow; the Activity tab's check box).
            if FreeCAD.ParamGet(PREFS).GetBool("Follow", True) and FreeCADGui.ActiveDocument:
                FreeCADGui.SendMsgToActiveView("ViewFit")
            FreeCADGui.updateGui()
        except Exception as e:
            FreeCAD.Console.PrintLog(f"Agent Link echo (after {name}): {e}\n")


def wrap(server, session):
    """Show each call the server runs in the window — batches' calls one by one."""
    echo = Echo(session)
    plain = server.call

    def call(params):
        name = params.get("name")
        args = params.get("arguments") or {}
        tool = server.tools.get(name)
        echo.before(tool, args)
        reply = plain(params)
        echo.after(tool, args, reply)
        return reply

    server.call = call
    return echo
