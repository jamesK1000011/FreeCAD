# SPDX-License-Identifier: LGPL-2.1-or-later
# The Agent Link panel in the FreeCAD window: Notes and Steps, inspired by
# the Reverse engineer panel of Stefan Hermann's ScanRuler (CNC Kitchen) —
# notes pinned to the part between the person and the agent, and the
# feature list played back with a caption a step. Agent Link's own code;
# extensions that port ScanRuler's tools credit him where they load. The records are the notes
# family's (families/notes.py), kept in the document; this panel only shows
# and edits them, so the agent and the person see the same notes.
#
# Nothing here changes the model: a pin and a played step are drawn in the
# 3D view's own scene, apart from the document, and taken away after.

import FreeCAD
import FreeCADGui

from .families import notes as nf
from .panel_tabs import FlowRow, Tabs, muted

try:
    from PySide import QtCore, QtGui

    QtWidgets = QtGui
    try:
        from PySide import QtWidgets  # noqa: F811  (Qt 6 builds)
    except ImportError:
        pass
except ImportError:  # pragma: no cover
    QtCore = QtGui = QtWidgets = None

# Agent Link's own panel, its own code: the idea credited, not the code.
CREDIT = "Inspired by ScanRuler by Stefan Hermann (CNC Kitchen)."
PIN_AGENT = (0.85, 0.45, 0.15)


def credit_text():
    """Agent Link's line, then each loaded extension's own credit and
    licence (an extension porting ScanRuler's tools names Stefan Hermann
    there, where his work loads)."""
    from . import families

    lines = [CREDIT]
    for e in getattr(families, "EXTENSIONS", []):
        if e.get("credit"):
            lines.append(e["credit"] + (f" ({e['license']})" if e.get("license") else ""))
    return "\n".join(lines)


from .usage import strip as usage_strip, text as usage_text  # noqa: E402
PIN_PERSON = (0.25, 0.55, 0.85)
PIN_DONE = (0.55, 0.55, 0.55)


def _doc():
    return FreeCAD.ActiveDocument


def _view():
    gd = FreeCADGui.ActiveDocument
    return gd.activeView() if gd else None


def _scene():
    v = _view()
    return v.getSceneGraph() if v and hasattr(v, "getSceneGraph") else None


class Pins:
    """The notes' pins in the 3D view: a marker each, coloured by who wrote
    it and greyed when done, with its number beside it."""

    def __init__(self):
        from pivy import coin

        self.coin = coin
        self.root = coin.SoSeparator()
        self.root.setName("AgentLinkPins")
        self.attached = None

    def show(self, notes, shown=True):
        coin = self.coin
        self.root.removeAllChildren()
        sg = _scene()
        if sg is not self.attached:
            if self.attached is not None:
                self.attached.removeChild(self.root)
            if sg is not None:
                sg.addChild(self.root)
            self.attached = sg
        if not shown:
            return
        for n in notes:
            sep = coin.SoSeparator()
            mat = coin.SoMaterial()
            col = PIN_DONE if n.get("done") else PIN_AGENT if n["by"] == "agent" else PIN_PERSON
            mat.diffuseColor = col
            mat.emissiveColor = col
            tr = coin.SoTranslation()
            tr.translation = tuple(n["at"])
            style = coin.SoMarkerSet()
            co = coin.SoCoordinate3()
            co.point.setValue(0, 0, 0)
            style.markerIndex = FreeCADGui.getMarkerIndex("CIRCLE_FILLED", 15)
            font = coin.SoFont()
            font.size = 20
            txt = coin.SoText2()
            txt.string = f"  {n['id']}"
            for c in (mat, tr, co, style, font, txt):
                sep.addChild(c)
            self.root.addChild(sep)

    def remove(self):
        if self.attached is not None:
            self.attached.removeChild(self.root)
            self.attached = None


class Stage:
    """One step of a body drawn in the view in place of the body: the
    feature's own shape (PartDesign keeps the solid as it stood after each
    feature), with the body hidden while it plays and shown again after."""

    def __init__(self):
        from pivy import coin

        self.coin = coin
        self.root = None
        self.body = None
        self.was = None

    def show(self, body, feature):
        coin = self.coin
        self.clear_node()
        if self.body is None:
            self.body, self.was = body, body.ViewObject.Visibility
            body.ViewObject.Visibility = False
        sh = feature.Shape
        if sh.isNull():
            return
        inp = coin.SoInput()
        inp.setBuffer(sh.writeInventor())
        node = coin.SoDB.readAll(inp)
        if node is None:
            return
        self.root = coin.SoSeparator()
        mat = coin.SoMaterial()
        mat.diffuseColor = (0.72, 0.45, 0.30)
        self.root.addChild(mat)
        self.root.addChild(node)
        sg = _scene()
        if sg is not None:
            sg.addChild(self.root)

    def clear_node(self):
        if self.root is not None:
            sg = _scene()
            if sg is not None:
                sg.removeChild(self.root)
            self.root = None

    def stop(self):
        self.clear_node()
        if self.body is not None:
            try:
                self.body.ViewObject.Visibility = self.was
            except Exception:
                pass
        self.body = self.was = None


class Panel(Tabs, QtWidgets.QDockWidget if QtWidgets else object):
    def __init__(self):
        super().__init__("Agent Link")
        self.setObjectName("AgentLinkPanel")
        self.pins = Pins()
        self.stage = Stage()
        self.placing = False
        self.callback = None
        self.seen = None
        self.steps, self.at = [], -1
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(1000)
        self.play_timer = QtCore.QTimer(self)
        self.play_timer.timeout.connect(self.next_step)

        tabs = QtWidgets.QTabWidget()
        # Narrow docks: the tab bar scrolls rather than squeezing the panel wide.
        tabs.setUsesScrollButtons(True)
        tabs.setElideMode(QtCore.Qt.ElideRight)
        tabs.addTab(self._notes_tab(), "Notes")
        tabs.addTab(self._steps_tab(), "Steps")
        tabs.addTab(self._review_tab(), "Review")
        tabs.addTab(self._setups_tab(), "Setups")
        tabs.addTab(self._materials_tab(), "Materials")
        tabs.addTab(self._fits_tab(), "Fits")
        tabs.addTab(self._manual_tab(), "Manual")
        tabs.addTab(self._activity_tab(), "Activity")
        tabs.addTab(self._session_tab(), "Session")
        tabs.setUsesScrollButtons(True)
        tabs.currentChanged.connect(lambda k: self.fill_tab(tabs.tabText(k)))
        self.tabs = tabs
        box = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(box)
        lay.setContentsMargins(6, 6, 6, 6)
        # Extensions' widgets above the tabs (agentlink/ext.py).
        from . import ext

        self.extras = []
        for make in ext.panel_widgets:
            try:
                wdg = make(self)
                lay.addWidget(wdg)
                self.extras.append(wdg)
            except Exception as e:
                FreeCAD.Console.PrintLog(f"Agent Link panel extension: {e}\n")
        lay.addWidget(tabs)
        # What the agent's work here has cost, apart from any credit.
        self.usage = QtWidgets.QPushButton("")
        self.usage.setFlat(True)
        self.usage.setStyleSheet(f"text-align: left; color: {muted(self.usage)}; font-size: 10px; padding: 0;")
        self.usage.setToolTip("Estimated tokens through Agent Link — click for where they went")
        self.usage.clicked.connect(self.usage_breakdown)
        lay.addWidget(self.usage)
        self._usage_seen = 0.0
        credit = QtWidgets.QLabel(credit_text())
        credit.setWordWrap(True)
        credit.setStyleSheet(f"color: {muted(credit)}; font-size: 10px;")
        lay.addWidget(credit)
        self.setWidget(box)
        self.refresh()

    # -- notes --

    def _notes_tab(self):
        w = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(w)
        row = QtWidgets.QHBoxLayout()
        self.pin_key = QtWidgets.QPushButton("Pin a note")
        self.pin_key.setCheckable(True)
        self.pin_key.setToolTip("Click the part to pin a note there")
        self.pin_key.toggled.connect(self.set_placing)
        self.show_pins = QtWidgets.QCheckBox("Pins")
        self.show_pins.setChecked(True)
        self.show_pins.setToolTip("Show the pins on the part (hide them for a picture)")
        self.show_pins.toggled.connect(lambda _: self.refresh(force=True))
        row.addWidget(self.pin_key)
        row.addStretch(1)
        row.addWidget(self.show_pins)
        lay.addLayout(row)
        self.note_list = QtWidgets.QTreeWidget()
        self.note_list.setHeaderLabels(["#", "Note", "Answer"])
        self.note_list.setRootIsDecorated(False)
        self.note_list.setColumnWidth(0, 52)
        self.note_list.setColumnWidth(1, 150)
        self.note_list.itemChanged.connect(self.tick)
        self.note_list.itemDoubleClicked.connect(self.edit_note)
        lay.addWidget(self.note_list)
        dele = QtWidgets.QPushButton("Delete")
        dele.setToolTip("Take the picked note out")
        dele.clicked.connect(self.delete_note)
        lay.addWidget(dele)
        return w

    def set_placing(self, on):
        self.placing = on
        v = _view()
        if v is None:
            return
        if on and self.callback is None:
            self.callback = v.addEventCallback("SoMouseButtonEvent", self.clicked)
        elif not on and self.callback is not None:
            try:
                v.removeEventCallback("SoMouseButtonEvent", self.callback)
            except Exception:
                pass
            self.callback = None

    def clicked(self, ev):
        if ev.get("State") != "DOWN" or ev.get("Button") != "BUTTON1":
            return
        v = _view()
        info = v.getObjectInfo(ev["Position"]) if v else None
        if not info:
            return
        at = [info["x"], info["y"], info["z"]]
        doc = _doc()
        obj = doc.getObject(info.get("Object", "")) if doc else None
        text, ok = QtWidgets.QInputDialog.getText(self, "Note", "What the note says:")
        self.pin_key.setChecked(False)
        if ok and text.strip():
            nf.add_note(doc, at, text.strip(), "person", obj.Label if obj else None, info.get("Component"))
            self.refresh(force=True)

    def tick(self, item, col):
        if col != 0 or self._filling:
            return
        doc = _doc()
        data = nf.notes_of(doc)
        n = nf.find_note(data, int(item.data(0, QtCore.Qt.UserRole)))
        n["done"] = item.checkState(0) == QtCore.Qt.Checked
        nf.save_notes(doc, data)
        self.refresh(force=True)

    def edit_note(self, item, col):
        doc = _doc()
        data = nf.notes_of(doc)
        n = nf.find_note(data, int(item.data(0, QtCore.Qt.UserRole)))
        if n["by"] == "agent":
            # The agent's question: the person's words go in as its answer.
            text, ok = QtWidgets.QInputDialog.getText(self, "Answer", n["text"], text=n.get("answer", ""))
            key = "answer"
        else:
            text, ok = QtWidgets.QInputDialog.getText(self, "Note", "What the note says:", text=n["text"])
            key = "text"
        if ok:
            n[key] = text.strip()
            nf.save_notes(doc, data)
            self.refresh(force=True)

    def delete_note(self):
        item = self.note_list.currentItem()
        if item is None:
            return
        doc = _doc()
        data = nf.notes_of(doc)
        data["notes"] = [n for n in data["notes"] if n["id"] != int(item.data(0, QtCore.Qt.UserRole))]
        nf.save_notes(doc, data)
        self.refresh(force=True)

    _filling = False

    def refresh_usage(self):
        import time

        if time.time() - self._usage_seen < 30:
            return
        self._usage_seen = time.time()
        try:
            from . import usage

            u = usage_strip(usage.report(7))
        except Exception as e:  # noqa: BLE001 — the strip is a convenience
            FreeCAD.Console.PrintLog(f"Agent Link usage strip: {e}\n")
            return
        self.usage.setText(u)

    def usage_breakdown(self):
        try:
            from . import usage

            r = usage.report(7)
        except Exception as e:  # noqa: BLE001
            r = {"error": str(e)}
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle("Agent Link — usage")
        box.setText(usage_text(r))
        box.exec_() if hasattr(box, "exec_") else box.exec()

    def refresh(self, force=False):
        if hasattr(self, "usage"):
            self.refresh_usage()
        for wdg in getattr(self, "extras", []):
            try:
                if hasattr(wdg, "refresh"):
                    wdg.refresh()
            except Exception:
                pass
        doc = _doc()
        notes = nf.notes_of(doc)["notes"] if doc else []
        key = (doc.Name if doc else None, repr(notes), self.show_pins.isChecked())
        if key == self.seen and not force:
            return
        self.seen = key
        self._filling = True
        self.note_list.clear()
        for n in notes:
            who = "Agent: " if n["by"] == "agent" else ""
            it = QtWidgets.QTreeWidgetItem([str(n["id"]), who + n["text"], n.get("answer", "")])
            it.setData(0, QtCore.Qt.UserRole, n["id"])
            it.setFlags(it.flags() | QtCore.Qt.ItemIsUserCheckable)
            it.setCheckState(0, QtCore.Qt.Checked if n.get("done") else QtCore.Qt.Unchecked)
            it.setToolTip(1, n["text"])
            self.note_list.addTopLevelItem(it)
        self._filling = False
        try:
            self.pins.show(notes, self.show_pins.isChecked())
        except Exception as e:  # a view without a scene (a TechDraw page in front)
            FreeCAD.Console.PrintLog(f"Agent Link pins: {e}\n")
        self.fill_bodies()

    # -- review: the part's verdict, as ScanRuler's review panel --

    def _review_tab(self):
        w = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(w)
        row = QtWidgets.QHBoxLayout()
        self.review_pick = QtWidgets.QComboBox()
        self.review_pick.setToolTip("The body to review")
        go = QtWidgets.QPushButton("Check")
        go.setToolTip("Soundness, printability (or the setup's process), mass and any strength results")
        go.clicked.connect(self.review)
        row.addWidget(self.review_pick, 1)
        row.addWidget(go)
        lay.addLayout(row)
        self.verdict = QtWidgets.QLabel("")
        self.verdict.setStyleSheet("font-weight: 600; font-size: 14px; padding: 4px;")
        lay.addWidget(self.verdict)
        self.findings = QtWidgets.QTreeWidget()
        self.findings.setHeaderLabels(["", "Finding", "Do"])
        self.findings.setRootIsDecorated(False)
        self.findings.setColumnWidth(0, 28)
        self.findings.itemClicked.connect(self._finding_picked)
        lay.addWidget(self.findings, 1)
        self.figures = QtWidgets.QLabel("")
        self.figures.setWordWrap(True)
        lay.addWidget(self.figures)
        return w

    def _call(self, name, args):
        """A tool, run as the agent's would be (so it is shown and logged)."""
        import json

        import agentlink_state

        link = agentlink_state.link
        if link is None or link.server is None:
            import os

            from .tools import Session, build_server

            # No link on: a server of the panel's own, kept, its files where
            # the link's go.
            out = os.path.expanduser("~/AgentLink")
            os.makedirs(out, exist_ok=True)
            own = getattr(self, "_own", None)
            if own is None or own[0].doc is not _doc():
                sess = Session(doc=_doc(), out_dir=out)
                own = self._own = (sess, build_server(sess))
            server = own[1]
        else:
            link._ready()
            server = link.server
        r = server.call({"name": name, "arguments": args})
        return not r.get("isError"), json.loads(r["content"][0]["text"])

    def review(self):
        body = self.review_pick.currentText()
        if not body:
            return
        ok, out = self._call("part_review", {"body": body})
        self.findings.clear()
        if not ok:
            self.verdict.setText("Could not review: " + (out.get("error") or {}).get("message", ""))
            return
        v = out.get("verdict", "?")
        colour = {"pass": "#3fae5a", "warn": "#e0a020", "fail": "#d94a4a"}.get(v, "palette(text)")
        self.verdict.setText(f"<span style='color:{colour}'>{v.upper()}</span> — {body}, made by {out.get('process', 'unknown')}")
        self._last_findings = out.get("findings") or []
        for f in self._last_findings:
            sev = {"error": "✗", "warning": "!", "info": "·"}.get(str(f.get("severity", "")).lower(), "·")
            it = QtWidgets.QTreeWidgetItem([sev, str(f.get("message") or f.get("what") or f.get("id", "")), str(f.get("fix") or f.get("do") or "")])
            it.setToolTip(1, it.text(1))
            it.setToolTip(2, it.text(2))
            self.findings.addTopLevelItem(it)
        if not self._last_findings:
            self.findings.addTopLevelItem(QtWidgets.QTreeWidgetItem(["", "Nothing found", ""]))
        fig = out.get("figures") or {}
        self.figures.setText("  ·  ".join(f"{k} {v}" for k, v in fig.items()) + (f"\n{out.get('processFrom')}" if out.get("processFrom") else ""))

    def _finding_picked(self, item, _col):
        """A finding with a place: point the view at it."""
        k = self.findings.indexOfTopLevelItem(item)
        f = (getattr(self, "_last_findings", None) or [None] * (k + 1))[k] if k >= 0 else None
        at = (f or {}).get("at")
        if isinstance(at, list) and len(at) == 3:
            v = FreeCADGui.ActiveDocument.activeView() if FreeCADGui.ActiveDocument else None
            if v is not None and hasattr(v, "viewPosition"):
                try:
                    cam = v.getCameraNode()
                    cam.focalDistance.getValue()
                    from pivy import coin

                    cam.pointAt(coin.SbVec3f(*at), coin.SbVec3f(0, 0, 1))
                except Exception:
                    pass

    # -- activity: what the agent did, call by call --

    def _activity_tab(self):
        from . import echo

        w = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(w)
        row = QtWidgets.QHBoxLayout()
        prefs = FreeCAD.ParamGet(echo.PREFS)
        self.echo_on = QtWidgets.QCheckBox("Show the agent's steps")
        self.echo_on.setToolTip("Workbench, button, selection and a caption for each thing the agent does")
        self.echo_on.setChecked(prefs.GetBool("Echo", True))
        self.echo_on.toggled.connect(lambda on: prefs.SetBool("Echo", on))
        self.pace = QtWidgets.QComboBox()
        self.pace.setToolTip("How long each step stays up before the agent goes on")
        paces = [("Fast", 0), ("Normal", 350), ("Slow", 900)]
        for label, _ms in paces:
            self.pace.addItem(label)
        cur = prefs.GetInt("EchoDelay", 350)
        self.pace.setCurrentIndex(min(range(3), key=lambda i: abs(paces[i][1] - cur)))
        self.pace.currentIndexChanged.connect(lambda i: prefs.SetInt("EchoDelay", paces[i][1]))
        row.addWidget(self.echo_on)
        self.follow = QtWidgets.QCheckBox("Follow")
        self.follow.setToolTip("Frame the part in the view after each change the agent makes")
        self.follow.setChecked(prefs.GetBool("Follow", True))
        self.follow.toggled.connect(lambda on: prefs.SetBool("Follow", on))
        row.addWidget(self.follow)
        row.addStretch(1)
        row.addWidget(self.pace)
        lay.addLayout(row)
        self.activity = QtWidgets.QTreeWidget()
        self.activity.setHeaderLabels(["Time", "Did", "Result"])
        self.activity.setRootIsDecorated(False)
        lay.addWidget(self.activity)
        for e in echo.log():
            self._add_activity(e)
        echo.listen(self._add_activity)
        return w

    def _add_activity(self, e):
        it = QtWidgets.QTreeWidgetItem([e["time"], ("" if e["ok"] else "✗ ") + e["what"], e.get("result", "")])
        it.setToolTip(1, e["what"])
        it.setToolTip(2, e.get("result", ""))
        self.activity.addTopLevelItem(it)
        self.activity.scrollToItem(it)

    # -- steps --

    def _steps_tab(self):
        w = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(w)
        self.body_pick = QtWidgets.QComboBox()
        self.body_pick.setToolTip("The PartDesign body to play")
        self.body_pick.currentIndexChanged.connect(lambda _: self.load_steps())
        lay.addWidget(self.body_pick)
        self.step_list = QtWidgets.QListWidget()
        self.step_list.currentRowChanged.connect(self.go_to)
        lay.addWidget(self.step_list)
        self.caption = QtWidgets.QLabel("")
        self.caption.setWordWrap(True)
        self.caption.setStyleSheet("font-weight: 600; padding: 4px;")
        lay.addWidget(self.caption)
        keys = FlowRow()
        for label, tip, fn in (("Play", "Play the build from the top", self.play), ("Pause", "Hold on this step", self.pause),
                               ("Step", "The next step", self.next_step), ("Stop", "End the playback and show the part again", self.stop)):
            b = QtWidgets.QPushButton(label)
            b.setToolTip(tip)
            b.clicked.connect(fn)
            keys.addWidget(b)
        lay.addWidget(keys)
        save = QtWidgets.QPushButton("Save as macro…")
        save.setToolTip("Keep this session's steps as a FreeCAD macro to run again (it runs once you approve it)")
        save.clicked.connect(self.save_macro)
        lay.addWidget(save)
        return w

    def save_macro(self):
        name, ok = QtWidgets.QInputDialog.getText(self, "Save as macro", "Macro name:")
        if not ok or not name.strip():
            return
        done, r = self._call("macro_save", {"name": name.strip()})
        QtWidgets.QMessageBox.information(self, "Agent Link", (f"Saved {r.get('path') or name}." + (" Approve it in the Session tab to run it." if done else ""))
                                          if done else (r.get("error") or {}).get("message", str(r)))

    def fill_bodies(self):
        doc = _doc()
        names = [o.Label for o in doc.Objects if o.TypeId == "PartDesign::Body"] if doc else []
        have = [self.body_pick.itemText(i) for i in range(self.body_pick.count())]
        solids = [o.Label for o in doc.Objects if hasattr(o, "Shape") and o.TypeId in ("PartDesign::Body", "Part::Feature", "Part::Box", "Part::Cylinder") and not any(p.TypeId == "PartDesign::Body" for p in o.InList)] if doc else []
        if [self.review_pick.itemText(i) for i in range(self.review_pick.count())] != solids:
            keep = self.review_pick.currentText()
            self.review_pick.clear()
            self.review_pick.addItems(solids)
            if keep in solids:
                self.review_pick.setCurrentIndex(solids.index(keep))
        if names != have:
            cur = self.body_pick.currentText()
            self.body_pick.blockSignals(True)
            self.body_pick.clear()
            self.body_pick.addItems(names)
            if cur in names:
                self.body_pick.setCurrentIndex(names.index(cur))
            self.body_pick.blockSignals(False)
            self.load_steps()

    def _body(self):
        doc = _doc()
        lab = self.body_pick.currentText()
        return next((o for o in doc.Objects if o.TypeId == "PartDesign::Body" and o.Label == lab), None) if doc else None

    def load_steps(self):
        b = self._body()
        self.steps = nf.steps_of(b) if b else []
        self.step_list.blockSignals(True)
        self.step_list.clear()
        for r in self.steps:
            self.step_list.addItem(f"{r['step']}. {r['label']}" + (f"  ({r['key']})" if r.get("key") else ""))
        self.step_list.blockSignals(False)

    def go_to(self, k):
        b = self._body()
        if b is None or not (0 <= k < len(self.steps)):
            return
        self.at = k
        r = self.steps[k]
        self.stage.show(b, _doc().getObject(r["name"]))
        did = r.get("change", 0)
        what = f"added {did:.0f} mm³" if did > 0.5 else f"took away {-did:.0f} mm³" if did < -0.5 else "no change to the volume"
        self.caption.setText(f"Step {r['step']} of {len(self.steps)} — {r['label']} ({r['type']}{', ' + r['key'] if r.get('key') else ''}): {what}.")
        FreeCADGui.updateGui()

    def play(self):
        self.load_steps()
        if not self.steps:
            return
        self.step_list.setCurrentRow(0)
        self.go_to(0)
        self.play_timer.start(1200)

    def pause(self):
        self.play_timer.stop()

    def next_step(self):
        if self.at + 1 >= len(self.steps):
            self.play_timer.stop()
            return
        self.step_list.setCurrentRow(self.at + 1)

    def stop(self):
        self.play_timer.stop()
        self.stage.stop()
        self.caption.setText("")
        self.at = -1

    def closeEvent(self, e):
        self.stop()
        self.set_placing(False)
        self.pins.remove()
        self.timer.stop()
        super().closeEvent(e)


_panel = None


def show():
    """Open the panel as a tab beside the tree; a second call brings it forward."""
    global _panel
    mw = FreeCADGui.getMainWindow()
    if _panel is None:
        _panel = Panel()
        # Beside the tree, as a tab of the left side panel (Model, Tasks):
        # the 3D view keeps its width, as in ScanRuler's side panels.
        docks = [d for d in mw.findChildren(QtWidgets.QDockWidget) if d is not _panel and d.isVisible()]
        left = next((d for d in docks if mw.dockWidgetArea(d) == QtCore.Qt.LeftDockWidgetArea), None)
        if left is not None:
            mw.addDockWidget(QtCore.Qt.LeftDockWidgetArea, _panel)
            mw.tabifyDockWidget(left, _panel)
        else:
            mw.addDockWidget(QtCore.Qt.LeftDockWidgetArea, _panel)
        # Frame the part in the view as it now stands.
        from .echo import fit

        QtCore.QTimer.singleShot(400, fit)
    _panel.show()
    _panel.raise_()
    return _panel
