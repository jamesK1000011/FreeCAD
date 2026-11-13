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

COMMAND.update({
    "asm_ground": "Assembly_ToggleGrounded", "asm_sub": "Assembly_InsertLink", "asm_motion": "Assembly_CreateJointRevolute",
    "bim_building": "BIM_Building", "bim_section": "BIM_SectionPlane", "bim_grid": "BIM_AxisSystem", "bim_member": "BIM_Beam",
    "bim_frame": "BIM_Frame", "bim_equipment": "BIM_Equipment", "bim_panel": "BIM_Panel", "bim_rebar": "BIM_Rebar",
    "bim_truss": "BIM_Truss", "bim_curtain_wall": "BIM_CurtainWall", "bim_fence": "BIM_Fence", "bim_pipe": "BIM_Pipe",
    "bim_covering": "BIM_Covering", "bim_schedule": "BIM_Schedule", "bim_material": "BIM_Material",
    "cam_tool": "CAM_ToolController", "cam_tool_add": "CAM_ToolBitDock", "cam_setup": "CAM_Job",
})

# Tools whose button depends on what kind was asked for.
CAM_OP = {"profile": "CAM_Profile", "pocket": "CAM_Pocket_Shape", "adaptive": "CAM_Adaptive", "face": "CAM_MillFace",
          "drill": "CAM_Drilling", "engrave": "CAM_Engrave", "helix": "CAM_Helix", "slot": "CAM_Slot", "deburr": "CAM_Deburr",
          "vcarve": "CAM_Vcarve", "thread": "CAM_ThreadMilling", "surface": "CAM_Surface", "waterline": "CAM_Waterline"}
CAM_DRESSUP = {"dogbone": "CAM_DressupDogbone", "tag": "CAM_DressupTag", "tags": "CAM_DressupTag", "ramp": "CAM_DressupRampEntry",
               "boundary": "CAM_DressupPathBoundary", "leadinout": "CAM_DressupLeadInOut", "axismap": "CAM_DressupAxisMap", "zcorrect": "CAM_DressupZCorrect"}


def command_for(name, args):
    """The command whose button does what this call does."""
    kind = str((args or {}).get("kind") or "").lower().replace("-", "").replace("_", "")
    if name == "asm_joint" and kind:
        return "Assembly_CreateJoint" + {"rackpinion": "RackPinion", "slider": "Slider", "ball": "Ball"}.get(kind, kind.capitalize())
    if name == "cam_op" and kind in CAM_OP:
        return CAM_OP[kind]
    if name == "cam_dressup" and kind in CAM_DRESSUP:
        return CAM_DRESSUP[kind]
    return COMMAND.get(name)


# Read-only tools that still show something (a view, a picture).
SHOWN = ("view_", "measure", "clearance", "section", "body_check", "dfm_check", "part_review", "steps_render", "note_")

# What a tool's arguments and answer name: the objects to select.
ARG_KEYS = ("body", "sketch", "object", "base", "target", "feature", "page", "op", "joint")
OUT_KEYS = ("feature", "sketch", "object", "body", "created", "page", "view", "joint", "op", "sub")

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


def _paint_flags(QtCore):
    f = getattr(QtCore.QEventLoop, "ExcludeUserInputEvents", None)
    if f is None:
        f = QtCore.QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents
    return f


def _paint(ms=0):
    """Let the window paint (and timers run) for ms, without taking the
    person's clicks and keys while a call is in hand (M7): no nested event
    loop that would let input change the document mid-call. The link's own
    timer will not start another call meanwhile (gui_link's in_call)."""
    import time as _t

    QtCore, _QtGui, _QtW = _qt()
    flags = _paint_flags(QtCore)
    end = _t.time() + max(0, ms) / 1000.0
    while True:
        try:
            QtCore.QCoreApplication.processEvents(flags, 30)
        except TypeError:
            QtCore.QCoreApplication.processEvents(flags)
        if _t.time() >= end:
            return
        _t.sleep(0.01)


def _wait(ms):
    """Let the window paint for ms without blocking it."""
    if ms <= 0:
        return
    _paint(ms)


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
        return v if len(v) <= 60 else v[:57] + "…]"
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


def fit():
    """Frame the part in the 3D view in front — only a live 3D view of an
    open document, and not while a restart is reopening documents (a view
    being made or torn down then crashes FreeCAD in its fit animation)."""
    try:
        import FreeCADGui

        from . import restart_mark

        if restart_mark.read() is not None:
            return False
        gd = FreeCADGui.ActiveDocument
        if gd is None or FreeCAD.ActiveDocument is None or gd.Document.Name not in FreeCAD.listDocuments():
            return False
        v = gd.ActiveView
        if v is None or not hasattr(v, "getSceneGraph") or not hasattr(v, "fitAll"):
            return False
        v.fitAll()
        return True
    except Exception as e:
        FreeCAD.Console.PrintLog(f"Agent Link fit: {e}\n")
        return False


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

    def live(self):
        """Shown in the window, unless an extension asks for the window to
        be left alone (session.window_quiet, agentlink/ext.py)."""
        return not getattr(self.s, "window_quiet", False)

    def before(self, tool, args):
        self.t0 = time.time()
        if tool is None or tool.name in ("batch", "session_state", "tool_groups", "guide_read"):
            return
        if not enabled() or not self.live():
            return
        shown = tool.changes or tool.name.startswith(SHOWN)
        if not shown:
            return
        try:
            import FreeCADGui

            wb = _workbench_for(tool.name)
            if wb and FreeCADGui.activeWorkbench().name() != wb and wb in FreeCADGui.listWorkbenches():
                FreeCADGui.activateWorkbench(wb)
            self._mode(tool.name, args)
            caption(_describe(tool.name, args))
            # Which document the agent is on (K), when it is not the one in front.
            where = ""
            try:
                if FreeCAD.ActiveDocument is not None and self.s.doc is not None and FreeCAD.ActiveDocument.Name != self.s.doc.Name:
                    where = f" (in {self.s.doc.Label})"
            except Exception:
                pass
            FreeCADGui.getMainWindow().statusBar().showMessage("Agent" + where + ": " + _describe(tool.name, args), 4000)
            cmd = command_for(tool.name, args)
            if cmd:
                flash_command(cmd)
            doc = self.s.doc
            _select(_objects(doc, [args.get(k) for k in ARG_KEYS if args.get(k)]))
            _paint()
            _wait(delay_ms())
        except Exception as e:
            FreeCAD.Console.PrintLog(f"Agent Link echo (before {tool.name}): {e}\n")

    def _views(self, name, args, body):
        """The window a person would be looking at: a TechDraw tool's page
        open in its tab, everything else in the 3D view; a FEM analysis
        made active."""
        import FreeCADGui

        QtCore, _QtGui, QtW = _qt()
        doc = self.s.doc
        page_ref = (body or {}).get("page") or (args or {}).get("page")
        if name.startswith(("td_", "drawing_")) and isinstance(page_ref, str):
            page = _objects(doc, [page_ref])
            if page and page[0].ViewObject:
                try:
                    page[0].ViewObject.doubleClicked()
                except Exception:
                    pass
            return
        an = (body or {}).get("analysis")
        if an:
            obj = _objects(doc, [an])
            if obj:
                try:
                    import FemGui

                    FemGui.setActiveAnalysis(obj[0])
                except Exception:
                    pass
        # Back to the 3D view when a drawing page is in front.
        area = FreeCADGui.getMainWindow().findChild(QtW.QMdiArea)
        if area is None:
            return
        cur = area.activeSubWindow()
        if cur is not None and type(cur.widget()).__name__ != "View3DInventor":
            for sub in area.subWindowList():
                if type(sub.widget()).__name__ == "View3DInventor":
                    area.setActiveSubWindow(sub)
                    break
        self._container(name, args or {}, body or {})

    def _container(self, name, args, body):
        """What a person would have made active for the work: an assembly
        (as Assembly's "Activate assembly" does, its joints then going into
        it), a BIM level or building (the active container new BIM objects
        go into), a CAM job's operation shown with its stock; a joint's two
        faces lit."""
        import FreeCADGui

        doc = self.s.doc
        gd = FreeCADGui.ActiveDocument
        view = getattr(gd, "ActiveView", None) if gd else None
        if view is None:
            return
        if name.startswith("asm_"):
            asm = _objects(doc, [body.get("assembly") or args.get("assembly")])
            if asm:
                try:
                    if view.getActiveObject("part") is not asm[0]:
                        view.setActiveObject("part", asm[0])
                except Exception:
                    pass
            if name == "asm_joint":
                ends = [body.get("a"), body.get("b")]
                lit = []
                for end in ends:
                    if not isinstance(end, str) or "." not in end:
                        continue
                    label, el = end.rsplit(".", 1)
                    o = _objects(doc, [label])
                    if o:
                        lit.append((o[0], el))
                if lit:
                    FreeCADGui.Selection.clearSelection()
                    for o, el in lit:
                        try:
                            FreeCADGui.Selection.addSelection(o, el)
                        except Exception:
                            pass
                    _wait(delay_ms())
        elif name.startswith("bim_") and name in ("bim_level", "bim_building"):
            made = _objects(doc, [body.get("object")])
            if made:
                try:
                    view.setActiveObject("Arch", made[0])
                except Exception:
                    pass
        elif name.startswith("bim_") and isinstance(args.get("level"), str):
            lv = _objects(doc, [args["level"]])
            if lv:
                try:
                    view.setActiveObject("Arch", lv[0])
                except Exception:
                    pass
        elif name.startswith("cam_"):
            job = _objects(doc, [body.get("job") or args.get("job")])
            op = _objects(doc, [body.get("op")])
            if job:
                j = job[0]
                stock = getattr(j, "Stock", None)
                for o in [stock] + op:
                    vo = getattr(o, "ViewObject", None) if o is not None else None
                    if vo is not None:
                        try:
                            vo.Visibility = True
                        except Exception:
                            pass
                if stock is not None and getattr(stock, "ViewObject", None) is not None:
                    try:
                        stock.ViewObject.Transparency = max(int(stock.ViewObject.Transparency or 0), 70)
                    except Exception:
                        pass

    def _mode(self, name, args):
        """The mode a person would be in: a sketch's tools work with the
        sketch open in the Sketcher; anything else closes it first."""
        import FreeCADGui

        gd = FreeCADGui.ActiveDocument
        if gd is None:
            return
        editing = gd.getInEdit()
        sk = args.get("sketch") if name.startswith("sketch_") and name not in ("sketch_new", "sketch_state") else None
        target = _objects(self.s.doc, [sk])[0] if sk and _objects(self.s.doc, [sk]) else None
        if target is not None:
            if editing is None or editing.Object is not target:
                if editing is not None:
                    gd.resetEdit()
                gd.setEdit(target.Name)
        elif editing is not None and not name.startswith(("sketch_", "view_", "note_", "measure")):
            gd.resetEdit()

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
            _note({"time": datetime.datetime.now().strftime("%H:%M:%S"), "tool": name, "ok": ok, "what": (getattr(self.s, "activity_tag", "") or "") + _describe(name, args), "result": summary,
                   "ms": int((time.time() - (self.t0 or time.time())) * 1000)})
        if not (enabled() and ok and tool is not None and tool.changes and self.live()) or name == "batch":
            return
        try:
            import FreeCADGui

            doc = self.s.doc
            made = _objects(doc, [body.get(k) for k in OUT_KEYS if isinstance(body.get(k), str)])
            # The window's view effects only where the agent's document is
            # the one in front (K): the person's own document is left alone.
            if FreeCAD.ActiveDocument is None or doc is None or FreeCAD.ActiveDocument.Name != doc.Name:
                _paint()
                return
            self._views(name, args, body)
            if name == "sketch_new" and made and FreeCADGui.ActiveDocument:
                FreeCADGui.ActiveDocument.setEdit(made[0].Name)
            if made:
                _select(made)
                # Lit for a moment, then the part shows in its own colours again.
                QtCore, _QtGui, _QtW = _qt()
                QtCore.QTimer.singleShot(1800, FreeCADGui.Selection.clearSelection)
                body_obj = next((o for o in made if o.TypeId == "PartDesign::Body"), None)
                if body_obj is None:
                    body_obj = next((p for o in made for p in o.InList if p.TypeId == "PartDesign::Body"), None)
                gv = FreeCADGui.ActiveDocument.ActiveView if FreeCADGui.ActiveDocument else None
                if body_obj is not None and gv is not None:
                    gv.setActiveObject("pdbody", body_obj)
            # Follow the work: the view framed on the part after each change
            # (Mod/AgentLink Follow; the Activity tab's check box).
            if FreeCAD.ParamGet(PREFS).GetBool("Follow", True) and not name.startswith(("td_", "drawing_")):
                fit()
            _paint()
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
