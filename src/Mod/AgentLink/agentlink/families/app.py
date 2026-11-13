# SPDX-License-Identifier: LGPL-2.1-or-later
# The rest of the window, for an agent: FreeCAD's settings (any parameter
# group, and the preference packs applied by their values), its commands
# (listed with what they do, run by name), its menus (walked as the person
# sees them, an item pressed by its path) and its workbenches. Settings work
# headless too; commands and menus need the window (the link on).
#
# A command or menu item that would open a modal dialog would hold the
# window until someone closes it, and the agent with it: those are refused,
# with the tool that does the same job named.

import json
import os
import re
import xml.etree.ElementTree as ET

import FreeCAD

from ..protocol import Tool, ToolError
from ..tools import obj

GROUP = "app"
INFO = "FreeCAD itself: settings (any parameter, preference packs), commands and menus run by name in the window, workbenches."

_INSTALL_DIR = os.path.join(os.path.expanduser("~"), "AgentLink", "install")

# FreeCAD 1.1 on keeps its settings and add-ons in a folder per release
# (…/FreeCAD/v1-1); the first time a release opens it asks, in a dialog,
# whether to copy the older ones. Done here beforehand, the same way
# (ApplicationDirectories.migrateAllPaths), so it opens with them.
_MIGRATE = (
    "import FreeCAD as F,os,sys\n"
    "AD=getattr(F,'ApplicationDirectories',None)\n"
    "p=[F.getUserAppDataDir(),F.getUserConfigDir()]\n"
    "if AD is None: sys.stdout.write('@@unversioned@@')\n"
    "elif all(AD.usingCurrentVersionConfig(x) for x in p):\n"
    "  done=[]\n"
    "  for x in p:\n"
    "    x=x.rstrip('/'); old=os.path.dirname(x)\n"
    "    if not os.path.exists(os.path.join(x,'.agentlink-migrated')) and os.path.isdir(old):\n"
    "      AD.migrateConfig(old,x); open(os.path.join(x,'.agentlink-migrated'),'w').close(); done.append(x)\n"
    "  sys.stdout.write(('@@migrated@@' if done else '@@already@@')+repr(done or p))\n"
    "else:\n"
    "  new=AD.migrateAllPaths(p)\n"
    "  sys.stdout.write('@@migrated@@'+repr(new))\n"
)
# Run in the new release once its own folders exist. FreeCAD 1.1.3 on
# macOS 26+ hangs for good the first time a pop-up notification opens: Qt
# logs a warning while making its window, the log goes back to the
# notification area, which waits on itself. The pop-ups off (the list
# behind the bell stays).
_QUIET = (
    "import FreeCAD as F,sys\n"
    "g=F.ParamGet('User parameter:BaseApp/Preferences/NotificationArea')\n"
    "g.SetBool('NonIntrusiveNotificationsEnabled',False)\n"
    "F.saveParameter()\n"
    "sys.stdout.write('@@quiet@@'+F.getUserConfigDir())\n"
)


def _alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except Exception:
        return False


def _bundle_python(app=None):
    import sys

    if app is None:
        exe = sys.executable
        if ".app/" not in exe:
            return None
        app = exe.split(".app/")[0] + ".app"
    for n in ("python3", "python"):
        p = os.path.join(app, "Contents", "Resources", "bin", n)
        if os.path.exists(p):
            return p
    return None


def _clean_env():
    """This FreeCAD's environment without what its bundle set for its own
    Python and Qt: another release started from here must find its own."""
    env = dict(os.environ)
    for k in list(env):
        if k.startswith(("PYTHON", "DYLD_", "QT_", "FREECAD", "CONDA", "__PYVENV")) or k in ("LD_LIBRARY_PATH", "GMSH_PATH"):
            env.pop(k)
    return env


def _carry_settings(app):
    """Run the other release headless to copy this one's settings and
    add-ons into its versioned folders (and, on a Mac, turn off the pop-up
    notifications that hang 1.1.3 there); says what it did."""
    import platform
    import subprocess

    bin_ = os.path.join(app, "Contents", "Resources", "bin")
    cmd = next((os.path.join(bin_, n) for n in ("freecadcmd", "FreeCADCmd") if os.path.exists(os.path.join(bin_, n))), None)
    if cmd is None:
        return "no FreeCADCmd in " + app + ": its first start asks about settings itself"

    def run(code):
        try:
            r = subprocess.run([cmd, "-c", code], capture_output=True, text=True, timeout=300, env=_clean_env())
        except Exception as e:
            return None, str(e)
        out = (r.stdout or "") + (r.stderr or "")
        m = re.search(r"@@(\w+)@@(.*)", out)
        return (m.group(1), m.group(2)) if m else (None, out.strip()[-300:] or "no answer")

    kind, what = run(_MIGRATE)
    said = {"migrated": "settings and add-ons copied: " + what, "already": "it has its own settings already: " + what, "unversioned": "it shares this one's settings folder"}.get(kind, "not carried over: " + what)
    if platform.system() == "Darwin":
        q, w = run(_QUIET)
        said += "; pop-up notifications off (they hang 1.1.3 on this macOS)" if q == "quiet" else "; pop-up notifications NOT turned off: " + w
    return said


ROOT = "User parameter:"
# Settings this FreeCAD set for its own sake, kept over an imported file's.
_KEEP = [("BaseApp/Preferences/NotificationArea", "NonIntrusiveNotificationsEnabled")]
# Commands that would hold the window (modal dialogs) or end the session.
REFUSED = {
    "Std_DlgPreferences": "pref_get / pref_set read and write the same settings",
    "Std_DlgParameter": "pref_get / pref_set",
    "Std_DlgCustomize": "pref_set (toolbars and shortcuts are parameters too)",
    "Std_Quit": "the person closes FreeCAD",
    "Std_CloseAllWindows": "doc_close per document",
    "Std_DlgMacroExecute": "running a macro is the person's call",
    "Std_DlgMacroRecord": "the person records macros",
    "Std_AddonMgr": "the person installs add-ons",
    "Std_Open": "doc_open { path }",
    "Std_SaveAs": "doc_save { path }",
    "Std_Import": "import_file { path }",
    "Std_Export": "export_file",
    "Std_Print": "the person prints",
    "Std_PrintPdf": "export_file to PDF",
    "Std_About": "session_state",
}


def _gui():
    if not FreeCAD.GuiUp:
        raise ToolError("unavailable", "This FreeCAD has no window: commands and menus run only in the window (the link on). Settings (pref_*) work here too.")
    import FreeCADGui

    return FreeCADGui


def _group(path):
    path = (path or "BaseApp/Preferences").strip("/")
    if path.startswith(ROOT):
        path = path[len(ROOT):]
    return path, FreeCAD.ParamGet(ROOT + path)


def _contents(g):
    out = {}
    for row in g.GetContents() or []:
        kind, name, value = row[0], row[1], row[2]
        out[name] = {"type": kind, "value": value}
    return out


def _set(g, name, value, kind=None):
    kind = (kind or "").lower()
    if not kind:
        old = _contents(g).get(name)
        kind = (old or {}).get("type", "").lower()
    if not kind:
        kind = "boolean" if isinstance(value, bool) else "integer" if isinstance(value, int) else "float" if isinstance(value, float) else "string"
    if kind in ("boolean", "bool"):
        g.SetBool(name, bool(value))
    elif kind in ("integer", "int"):
        g.SetInt(name, int(value))
    elif kind in ("unsigned", "uint"):
        g.SetUnsigned(name, int(value))
    elif kind in ("float", "double"):
        g.SetFloat(name, float(value))
    else:
        g.SetString(name, str(value))
    return kind


PACK_TYPES = {"FCBool": "bool", "FCInt": "int", "FCUInt": "uint", "FCFloat": "float", "FCText": "string"}


def apply_cfg(path):
    """Set every value a preference pack's .cfg holds, group by group (what
    FreeCAD's own pack manager does, without its dialog)."""
    tree = ET.parse(path)
    n = 0

    def walk(node, prefix):
        nonlocal n
        for child in node:
            if child.tag == "FCParamGroup":
                walk(child, prefix + [child.get("Name")])
            elif child.tag in PACK_TYPES and prefix:
                g = FreeCAD.ParamGet(ROOT + "/".join(prefix))
                v = child.text if child.tag == "FCText" else child.get("Value")
                if child.tag == "FCBool":
                    v = v in ("1", "true", "True")
                _set(g, child.get("Name"), v, PACK_TYPES[child.tag])
                n += 1

    root = tree.getroot()
    # The file's top groups are Root > BaseApp > …; parameters address from BaseApp.
    for top in root:
        if top.tag == "FCParamGroup" and top.get("Name") == "Root":
            walk(top, [])
        else:
            walk(root, [])
            break
    return n


def packs():
    """The preference packs this FreeCAD has: its own and every add-on's."""
    found = {}
    roots = [os.path.join(FreeCAD.getResourceDir(), "Gui", "PreferencePacks"), os.path.join(FreeCAD.getUserAppDataDir(), "SavedPreferencePacks")]
    mod = os.path.join(FreeCAD.getUserAppDataDir(), "Mod")
    if os.path.isdir(mod):
        roots += [os.path.join(mod, m) for m in os.listdir(mod)]
    roots.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    for r in roots:
        if not os.path.isdir(r):
            continue
        for d in os.listdir(r):
            cfg = os.path.join(r, d, d + ".cfg")
            if os.path.isfile(cfg):
                found.setdefault(d, cfg)
    return found


def _actions(menu, path, out, depth=0):
    for act in menu.actions():
        if act.isSeparator():
            continue
        title = re.sub(r"&(?!&)", "", act.text()).replace("&&", "&").strip()
        if not title:
            continue
        sub = act.menu()
        row = {"path": path + [title]}
        if act.objectName() and not sub:
            row["command"] = act.objectName()
        if not act.isEnabled():
            row["enabled"] = False
        if act.isCheckable():
            row["checked"] = act.isChecked()
        if sub is not None and depth < 4:
            _actions(sub, path + [title], out, depth + 1)
        else:
            out.append(row)


def _menubar():
    gui = _gui()
    return gui.getMainWindow().menuBar()


def _find_action(path):
    bar = _menubar()
    want = [re.sub(r"\.\.\.$|…$", "", p).strip().lower() for p in path]
    acts = bar.actions()
    for i, w in enumerate(want):
        hit = None
        for act in acts:
            t = re.sub(r"&(?!&)", "", act.text()).replace("&&", "&").strip()
            t = re.sub(r"\.\.\.$|…$", "", t).strip().lower()
            if t == w:
                hit = act
                break
        if hit is None:
            have = [re.sub(r"&(?!&)", "", a.text()).strip() for a in acts if not a.isSeparator() and a.text()]
            raise ToolError("not_found", f"No menu item '{path[i]}' there: " + ", ".join(have[:40]))
        if i == len(want) - 1:
            return hit
        if hit.menu() is None:
            raise ToolError("invalid_input", f"'{path[i]}' is an item, not a menu.")
        hit.menu().aboutToShow.emit()  # menus FreeCAD fills when opened (Recent files, Workbench)
        acts = hit.menu().actions()


def tools(session):
    s = session

    def pref_get(a):
        path, g = _group(a.get("path"))
        out = {"path": path, "values": _contents(g)}
        try:
            out["groups"] = sorted(g.GetGroups())
        except Exception:
            pass
        if a.get("name"):
            v = out["values"].get(a["name"])
            if v is None:
                raise ToolError("not_found", f"No setting '{a['name']}' in {path} (FreeCAD's default applies until one is set): " + ", ".join(sorted(out["values"])[:40]))
            return {"path": path, "name": a["name"], **v}
        return out

    def pref_set(a):
        path, g = _group(a.get("path"))
        values = dict(a.get("values") or {})
        if a.get("name"):
            values[a["name"]] = a.get("value")
        if not values:
            raise ToolError("invalid_input", "name and value, or values { name: value }.")
        done = {}
        for k, v in values.items():
            before = _contents(g).get(k, {}).get("value")
            kind = _set(g, k, v, a.get("type"))
            done[k] = {"was": before, "now": _contents(g).get(k, {}).get("value"), "type": kind}
        return {"path": path, "set": done, "note": "Most settings take effect at once; a few (the theme, the stylesheet, toolbars) only after FreeCAD restarts."}

    def pref_reset(a):
        path, g = _group(a.get("path"))
        names = a.get("names") or []
        if not names:
            raise ToolError("invalid_input", "names: the settings to take back to FreeCAD's defaults.")
        for k in names:
            kind = _contents(g).get(k, {}).get("type", "")
            {"Boolean": g.RemBool, "Integer": g.RemInt, "Unsigned": g.RemUnsigned, "Float": g.RemFloat, "String": g.RemString}.get(kind, lambda _n: None)(k)
        return {"path": path, "reset": names}

    def pref_pack(a):
        have = packs()
        name = a.get("apply")
        if not name:
            return {"packs": sorted(have), "next": "pref_pack { apply: name } sets its values; restart FreeCAD for the theme and stylesheet."}
        if name not in have:
            raise ToolError("not_found", f"No preference pack '{name}': " + ", ".join(sorted(have)))
        n = apply_cfg(have[name])
        return {"applied": name, "values": n, "note": "Restart FreeCAD for the stylesheet and theme to take hold; navigation and colours change now."}

    def cmd_list(a):
        gui = _gui()
        q = str(a.get("query", "")).lower()
        rows = []
        for name in sorted(gui.listCommands()):
            if q and q not in name.lower():
                info = None
                try:
                    info = gui.Command.get(name).getInfo()
                except Exception:
                    pass
                if not info or q not in (str(info.get("menuText", "")) + str(info.get("toolTip", ""))).lower():
                    continue
            row = {"name": name}
            try:
                info = gui.Command.get(name).getInfo()
                row["menu"] = re.sub(r"&(?!&)", "", str(info.get("menuText", "")))
                tip = str(info.get("toolTip", "")).strip()
                if tip:
                    row["tip"] = re.sub(r"<[^>]+>", "", tip)[:160]
                if info.get("shortcutText"):
                    row["shortcut"] = info["shortcutText"]
            except Exception:
                pass
            if name in REFUSED:
                row["refused"] = REFUSED[name]
            rows.append(row)
            if len(rows) >= int(a.get("limit", 80)):
                break
        return {"commands": rows, "count": len(rows), "workbench": gui.activeWorkbench().name()}

    def cmd_run(a):
        gui = _gui()
        name = a.get("name", "")
        if name in REFUSED or re.match(r"^Std_Dlg", name):
            raise ToolError("refused", f"{name} opens a dialog that would hold the window: {REFUSED.get(name, 'its settings are pref_set; its job, the matching tool')}.")
        if name not in gui.listCommands():
            raise ToolError("not_found", f"No command '{name}': cmd_list {{ query }} finds them.")
        gui.runCommand(name, int(a.get("index", 0)))
        FreeCAD.ActiveDocument and FreeCAD.ActiveDocument.recompute()
        gui.updateGui()
        return {"ran": name, "workbench": gui.activeWorkbench().name(), "document": FreeCAD.ActiveDocument.Name if FreeCAD.ActiveDocument else None}

    def menu_list(a):
        bar = _menubar()
        out = []
        top = a.get("menu")
        for act in bar.actions():
            title = re.sub(r"&(?!&)", "", act.text()).strip()
            if not title or act.menu() is None:
                continue
            if top and title.lower() != str(top).lower():
                continue
            act.menu().aboutToShow.emit()
            _actions(act.menu(), [title], out)
        if top and not out:
            raise ToolError("not_found", f"No menu '{top}'.")
        return {"items": out if top else out[: int(a.get("limit", 400))], "count": len(out)}

    def menu_run(a):
        path = a.get("path") or []
        if not path:
            raise ToolError("invalid_input", "path: the menu's titles, e.g. [\"View\", \"Standard views\", \"Fit all\"].")
        act = _find_action(path)
        name = act.objectName()
        if name in REFUSED or re.match(r"^Std_Dlg", name or ""):
            raise ToolError("refused", f"'{' > '.join(path)}' ({name}) opens a dialog that would hold the window: {REFUSED.get(name, 'pref_set or the matching tool')}.")
        if act.menu() is not None:
            raise ToolError("invalid_input", "That is a menu: menu_list { menu } shows its items.")
        if not act.isEnabled():
            raise ToolError("invalid_state", f"'{' > '.join(path)}' is greyed out just now (it needs a selection or another workbench).")
        act.trigger()
        _gui().updateGui()
        return {"pressed": path, "command": name or None, "checked": act.isChecked() if act.isCheckable() else None}

    def workbench(a):
        gui = _gui()
        if a.get("activate"):
            want = str(a["activate"]).lower().replace(" ", "")
            names = gui.listWorkbenches()
            hit = next((k for k, w in names.items() if k.lower() == want or k.lower() == want + "workbench" or getattr(w, "MenuText", "").lower().replace(" ", "") == want), None)
            if hit is None:
                raise ToolError("not_found", "Workbenches: " + ", ".join(sorted(getattr(w, "MenuText", k) for k, w in names.items())))
            gui.activateWorkbench(hit)
        wbs = gui.listWorkbenches()
        return {"active": gui.activeWorkbench().name(), "workbenches": sorted(getattr(w, "MenuText", k) for k, w in wbs.items())}

    def show(a):
        """Show or hide objects in the window, as the eye in the tree does.
        A PartDesign body shows its tip (its finished part), not its history."""
        _gui()
        doc = s.doc
        names = a.get("objects")
        if names in (None, "all"):
            objs = [o for o in doc.Objects if o.TypeId in ("PartDesign::Body",) or (hasattr(o, "Shape") and not o.InList and o.TypeId not in ("App::Origin", "App::Line", "App::Plane"))]
        else:
            objs = []
            for n in names if isinstance(names, list) else [names]:
                o = doc.getObject(n) or next((x for x in doc.Objects if x.Label == n), None)
                if o is None:
                    raise ToolError("not_found", f"No object '{n}'.")
                objs.append(o)
        on = bool(a.get("visible", True))
        done = []
        for o in objs:
            if not getattr(o, "ViewObject", None):
                continue
            o.ViewObject.Visibility = on
            if on and o.TypeId == "PartDesign::Body" and o.Tip is not None:
                o.Tip.ViewObject.Visibility = True
            done.append(o.Label)
        import FreeCADGui

        FreeCADGui.updateGui()
        return {"visible" if on else "hidden": done}

    def selection(a):
        import FreeCADGui

        _gui()
        if a.get("clear", True) and not a.get("objects"):
            FreeCADGui.Selection.clearSelection()
            return {"selected": []}
        FreeCADGui.Selection.clearSelection()
        for n in a.get("objects") or []:
            o = s.doc.getObject(n) or next((x for x in s.doc.Objects if x.Label == n), None)
            if o is None:
                raise ToolError("not_found", f"No object '{n}'.")
            FreeCADGui.Selection.addSelection(o)
        return {"selected": [o.Object.Label for o in FreeCADGui.Selection.getSelectionEx()]}

    def window_snapshot(a):
        """The whole FreeCAD window as the person sees it — toolbars, tree,
        panels, the 3D view — drawn by Qt itself, so it works in the
        background with no screen-recording permission. part: one dock
        panel by its title (e.g. "Report view", "Agent Link")."""
        gui = _gui()
        mw = gui.getMainWindow()
        target = mw
        if a.get("part"):
            want = str(a["part"]).lower()
            from PySide import QtGui

            QtW = getattr(__import__("PySide", fromlist=["QtWidgets"]), "QtWidgets", QtGui)
            docks = mw.findChildren(QtW.QDockWidget)
            target = next((d for d in docks if d.windowTitle().lower() == want or d.objectName().lower() == want), None)
            if target is None:
                raise ToolError("not_found", "Panels: " + ", ".join(sorted(d.windowTitle() for d in docks if d.windowTitle())))
        from ..grab import grab_window

        pix = grab_window(target)
        out = os.path.expanduser(a.get("path") or os.path.join(s.out_dir, "window.png"))
        if not os.path.isabs(out):
            out = os.path.join(s.out_dir, out)
        if not pix.save(out, "PNG"):
            raise ToolError("failed", f"Could not write {out}")
        res = {"path": out, "width": pix.width(), "height": pix.height()}
        if a.get("inline", True):
            with open(out, "rb") as f:
                res["_png"] = f.read()
        return res

    def _shots():
        try:
            with open(os.path.join(os.path.expanduser("~/AgentLink"), "shots", "index.json"), encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return []

    def snapshot_latest(a):
        rows = _shots()
        if not rows:
            raise ToolError("not_found", "No screenshots yet: the person takes one with the camera button (Agent Link toolbar) or Alt+Shift+S.")
        k = int(a.get("index", 1))
        if not 1 <= k <= len(rows):
            raise ToolError("invalid_input", f"index: 1 to {len(rows)}.")
        r = dict(rows[-k])
        path = r["original"] if a.get("original") else r["path"]
        r.pop("marks", None)
        if os.path.isfile(path):
            with open(path, "rb") as f:
                r["_png"] = f.read()
        if not r.get("done"):
            r["note"] = (r.get("note") or "") + " (the person is still marking it up)"
        return r

    def snapshot_list(a):
        return {"shots": [{k: v for k, v in r.items() if k != "marks"} for r in reversed(_shots())]}

    def app_restart(a):
        """Quit this FreeCAD and start it again (new add-on code, a theme):
        the settings written, each changed document saved first — one never
        saved is refused unless discard: true."""
        gui = _gui()
        import subprocess
        import sys

        target = a.get("app")
        if target:
            target = os.path.expanduser(target).rstrip("/")
            if not (target.endswith(".app") and os.path.isdir(os.path.join(target, "Contents"))):
                raise ToolError("not_found", "No FreeCAD app at " + target + " (app_install says where it put one).")

        # A step playing hides the body: show it again before anything is saved.
        try:
            from .. import panel

            if panel._panel is not None:
                panel._panel.stop()
        except Exception:
            pass
        # Extensions tidy up first (a document of their own not kept).
        from .. import ext

        ext.restarting(s)

        def changed(d):
            # "Modified" is the window's own flag (the * on the tab); isTouched
            # clears at each recompute, so it misses most changes.
            gd = gui.getDocument(d.Name)
            return bool(getattr(gd, "Modified", False)) or d.isTouched()

        unsaved = [d.Label for d in FreeCAD.listDocuments().values() if changed(d) and not d.FileName]
        if unsaved and not a.get("discard"):
            raise ToolError("invalid_state", "Never saved: " + ", ".join(unsaved) + " — doc_save { path } first, or discard: true to lose them.")
        saved = []
        for d in list(FreeCAD.listDocuments().values()):
            if changed(d) and d.FileName:
                d.save()
                saved.append(d.FileName)
        reopen = [d.FileName for d in FreeCAD.listDocuments().values() if d.FileName]
        # Calls wait for the window (and these documents) instead of going to
        # the hidden FreeCAD meanwhile; the one in front is in front again.
        from .. import restart_mark

        restart_mark.write(reopen, active=getattr(s.doc, "FileName", None) or None)
        FreeCAD.saveParameter()
        exe = sys.executable
        bundle = exe.split(".app/")[0] + ".app" if ".app/" in exe else None
        migrated = None
        if target:
            # The settings and add-ons, this one's freshly written, carried
            # into the other release's own folders before it first opens.
            migrated = _carry_settings(target)
            bundle = target
        files = " ".join("'" + f.replace("'", "'\\''") + "'" for f in reopen)
        if bundle:
            cmd = f"sleep 3; open -a '{bundle}' {files}"
        else:
            cmd = f"sleep 3; '{exe}' {files} &"
        subprocess.Popen(["/bin/sh", "-c", cmd], start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=_clean_env() if target else None)
        for d in list(FreeCAD.listDocuments().values()):
            FreeCAD.closeDocument(d.Name)
        from PySide import QtCore, QtGui

        app = QtGui.QApplication.instance() if hasattr(QtGui, "QApplication") else None
        if app is None:
            from PySide import QtWidgets

            app = QtWidgets.QApplication.instance()
        QtCore.QTimer.singleShot(300, app.quit)
        return {"restarting": True, "saved": saved, "reopens": reopen, **({"app": bundle, "settings": migrated} if target else {}), "note": "FreeCAD quits and opens again in a few seconds with these documents, its link on by itself; calls meanwhile wait for it (up to 90 s)."}

    def app_install(a):
        """Install another FreeCAD release beside this one (a Mac): started
        in the background, then read back with no arguments."""
        import platform
        import subprocess
        import sys

        os.makedirs(_INSTALL_DIR, exist_ok=True)
        state = os.path.join(_INSTALL_DIR, "state.json")
        cur = None
        if os.path.exists(state):
            with open(state) as f:
                cur = json.load(f)
        running = bool(cur and cur.get("state") not in ("installed", "failed") and _alive(cur.get("pid")))
        if a.get("open"):
            app = os.path.expanduser(a["open"]).rstrip("/")
            if not (app.endswith(".app") and os.path.isdir(os.path.join(app, "Contents"))):
                raise ToolError("not_found", "No FreeCAD app at " + app)
            if FreeCAD.GuiUp:
                raise ToolError("invalid_state", "This FreeCAD's window is open: app_restart { app } switches it over.")
            said = _carry_settings(app)
            subprocess.Popen(["/usr/bin/open", "-a", app], start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=_clean_env())
            return {"opening": app, "settings": said, "note": "Its window opens in a few seconds; its link comes on by itself once Agent Link is among its add-ons."}
        version = a.get("version")
        if not version:
            if cur is None:
                return {"state": "none", "running_version": ".".join(FreeCAD.Version()[:3]), "next": "app_install { version: \"1.1.3\" } (or \"latest\")"}
            out = dict(cur)
            if cur.get("state") == "installed":
                out["next"] = "app_restart { app: \"%s\" } opens it, settings and add-ons carried over" % cur.get("app")
            elif cur.get("state") not in ("failed",) and not running:
                out["state"] = "stopped"
            return out
        if running:
            raise ToolError("invalid_state", "An install is already running (" + str(cur.get("state")) + "); app_install with no arguments follows it.")
        if platform.system() != "Darwin":
            raise ToolError("unsupported", "app_install installs the macOS release; elsewhere use the system's package manager.")
        py = _bundle_python()
        if py is None:
            raise ToolError("not_found", "No python3 in this FreeCAD's bundle to run the install with.")
        job = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "install_job.py")
        if os.path.exists(state):
            os.remove(state)
        log = open(os.path.join(_INSTALL_DIR, "install.log"), "w")
        p = subprocess.Popen([py, "-I", job, version, state], start_new_session=True, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
        return {"started": True, "pid": p.pid, "version": version, "note": "Downloading FreeCAD's official disk image from its GitHub release, SHA-256 checked against the release's; the FreeCAD.app there now is kept under its version's name. app_install with no arguments follows it (a few minutes)."}

    def pref_import(a):
        """Merge another settings file (the release before this one's, by
        default) into this FreeCAD's settings; this one's kept first."""
        import shutil
        import time

        src = a.get("path")
        if not src:
            cfg = FreeCAD.getUserConfigDir().rstrip("/")
            tries = [os.path.join(os.path.dirname(cfg), "user.cfg")]
            AD = getattr(FreeCAD, "ApplicationDirectories", None)
            if AD is not None:
                try:
                    if not AD.usingCurrentVersionConfig(cfg):
                        tries = []
                except Exception:
                    pass
            src = next((t for t in tries if os.path.exists(t)), None)
            if not src:
                raise ToolError("not_found", "No earlier release's settings beside this one's (" + cfg + "): give path: a user.cfg.")
        src = os.path.expanduser(src)
        if not os.path.isfile(src):
            raise ToolError("not_found", "No settings file at " + src)
        backup = os.path.join(os.path.expanduser("~"), "AgentLink", "prefs-backup-%d.cfg" % int(time.time()))
        os.makedirs(os.path.dirname(backup), exist_ok=True)
        root = FreeCAD.ParamGet(ROOT)
        root.Export(backup)
        keep = {}
        for path, name in _KEEP:
            g = FreeCAD.ParamGet(ROOT + path)
            if name in g.GetBools():
                keep[(path, name)] = g.GetBool(name)
        root.Insert(src)
        for (path, name), v in keep.items():
            FreeCAD.ParamGet(ROOT + path).SetBool(name, v)
        FreeCAD.saveParameter()
        copied = []
        if a.get("macros", True):
            new_mac = os.path.join(FreeCAD.getUserAppDataDir(), "Macro")
            old_mac = os.path.join(os.path.dirname(FreeCAD.getUserAppDataDir().rstrip("/")), "Macro")
            if os.path.isdir(old_mac) and os.path.realpath(old_mac) != os.path.realpath(new_mac):
                os.makedirs(new_mac, exist_ok=True)
                for n in os.listdir(old_mac):
                    if os.path.isfile(os.path.join(old_mac, n)) and not os.path.exists(os.path.join(new_mac, n)):
                        shutil.copy2(os.path.join(old_mac, n), os.path.join(new_mac, n))
                        copied.append(n)
        return {"from": src, "backup": backup, "kept": {"/".join(k): v for k, v in keep.items()}, "macros": copied, "note": "Merged: the file's values win, this one's other settings stay. Some (theme, toolbars) show after app_restart. pref_import { path: backup } puts this back."}

    return [
        Tool("app_install", "Install another FreeCAD release beside this one on a Mac: version (\"1.1.3\", \"latest\") from FreeCAD's GitHub release, SHA-256 checked; the current FreeCAD.app kept as \"FreeCAD <version>.app\". Runs in the background: no arguments reads its progress. Then app_restart { app } switches the window over; with the window closed, open: an .app carries settings over and opens it.",
             obj({"version": {"type": "string"}, "open": {"type": "string"}}), app_install, changes=False),
        Tool("pref_get", "Read FreeCAD's settings: a parameter group (path, e.g. \"BaseApp/Preferences/View\") with its values and subgroups, or one setting (name). Anything Edit > Preferences shows lives here.",
             obj({"path": {"type": "string"}, "name": {"type": "string"}}), pref_get),
        Tool("pref_set", "Change FreeCAD's settings: path, then name + value or values { name: value }; the type follows the setting's own (or type: bool, int, uint, float, string). Says what each was.",
             obj({"path": {"type": "string"}, "name": {"type": "string"}, "value": {}, "values": {"type": "object"}, "type": {"type": "string"}}), pref_set, changes=True),
        Tool("pref_import", "Bring settings over from another file: by default the release before this one's user.cfg (after a FreeCAD upgrade), or path. Merged in, the file's values winning; this FreeCAD's settings exported to a backup first; macros the new one lacks copied too (macros: false not).",
             obj({"path": {"type": "string"}, "macros": {"type": "boolean"}}), pref_import),
        Tool("pref_reset", "Take settings back to FreeCAD's defaults: path, names.", obj({"path": {"type": "string"}, "names": {"type": "array", "items": {"type": "string"}}}, ["names"]), pref_reset),
        Tool("pref_pack", "Preference packs (themes and their settings, the Studio packs among them): list them, or apply: name to set its values.", obj({"apply": {"type": "string"}}), pref_pack, changes=True),
        Tool("cmd_list", "FreeCAD's commands (every toolbar button and menu item is one), with menu text, tip and shortcut; query filters by name or words. Window only.",
             obj({"query": {"type": "string"}, "limit": {"type": "integer"}}), cmd_list),
        Tool("cmd_run", "Run a FreeCAD command by name (Std_ViewFitAll, PartDesign_Pad …), as its button would, on the current selection. Commands that open a modal dialog are refused, naming the tool for the job. Window only.",
             obj({"name": {"type": "string"}, "index": {"type": "integer"}}, ["name"]), cmd_run, changes=True),
        Tool("menu_list", "The window's menus as the person sees them for the active workbench: each item's path, its command, greyed or ticked; menu: one menu only. Window only.",
             obj({"menu": {"type": "string"}, "limit": {"type": "integer"}}), menu_list),
        Tool("menu_run", "Press a menu item by its path of titles ([\"View\", \"Standard views\", \"Fit all\"]); modal dialogs refused as cmd_run. Window only.",
             obj({"path": {"type": "array", "items": {"type": "string"}}}, ["path"]), menu_run, changes=True),
        Tool("show", "Show or hide objects in the window as the tree's eye does (objects: names or labels, or all; visible: false hides); a PartDesign body shows its finished part. Window only.",
             obj({"objects": {}, "visible": {"type": "boolean"}}), show, changes=True),
        Tool("selection", "Select objects in the window (objects: names or labels) for a command to act on, or clear the selection (no objects). Window only.",
             obj({"objects": {"type": "array", "items": {"type": "string"}}, "clear": {"type": "boolean"}}), selection),
        Tool("window_snapshot", "A picture of the whole FreeCAD window as the person sees it (toolbars, tree, panels, 3D view), or part: one panel by its title; drawn by FreeCAD itself, in the background. path (a .png). Window only.",
             obj({"part": {"type": "string"}, "path": {"type": "string"}, "inline": {"type": "boolean"}}), window_snapshot),
        Tool("snapshot_latest", "The person's newest screenshot (index 2, 3 … older ones) as a picture with their marks (original: true without), with its note, numbered pins and their comments, workbench and time. Only the person takes them (camera button, Alt+Shift+S).",
             obj({"index": {"type": "integer"}, "original": {"type": "boolean"}}), snapshot_latest),
        Tool("snapshot_list", "The person's screenshots, newest first, without pictures: time, note, pins, saved path.", obj({}), snapshot_list),
        Tool("app_restart", "Quit this FreeCAD window and open it again (new add-ons, a theme, settings that need a restart): settings written, changed documents saved and reopened; one never saved is refused unless discard: true; app: another FreeCAD .app (app_install's) opens that one instead, this one's settings and add-ons carried into its folders first. Window only.",
             obj({"discard": {"type": "boolean"}, "app": {"type": "string"}}), app_restart),
        Tool("workbench", "The workbenches, the active one, and activate: a name (Part Design, BIM, FEM …). Window only.", obj({"activate": {"type": "string"}}), workbench),
    ]
