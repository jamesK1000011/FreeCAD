# SPDX-License-Identifier: LGPL-2.1-or-later
# More of the Agent Link panel, inspired by the ScanRuler fork's panels: Setups
# (how each part is made), Materials (what it is made of), Fits (the size to
# model a hole or peg at, and the printer's calibration) and Manual (an
# assembly manual, step by step). Each button runs the same tool the agent
# would (Panel._call), so what the person does here is shown, logged and
# undoable like the agent's work, and the agent sees it in the document.

import os

from . import store

try:
    from PySide import QtCore, QtGui

    QtWidgets = QtGui
    try:
        from PySide import QtWidgets  # noqa: F811  (Qt 6 builds)
    except ImportError:
        pass
except ImportError:  # pragma: no cover
    QtCore = QtGui = QtWidgets = None


def muted(widget):
    """A quiet colour that still reads in light and dark themes: the text
    colour blended two thirds of the way towards the background (palette's
    mid is near the background in some dark themes)."""
    pal = widget.palette()
    t, b = pal.color(QtGui.QPalette.WindowText), pal.color(QtGui.QPalette.Window)
    mix = [int(t.red() * 0.6 + b.red() * 0.4), int(t.green() * 0.6 + b.green() * 0.4), int(t.blue() * 0.6 + b.blue() * 0.4)]
    return "#%02x%02x%02x" % tuple(mix)


class FlowRow(QtWidgets.QWidget if QtWidgets else object):
    """Buttons in a row that wraps to the next line when the panel is narrow
    (the panel docks beside the model tree, often under 300 px)."""

    def __init__(self, widgets=(), spacing=4):
        super().__init__()
        self._items, self._sp = list(widgets), spacing
        for w in self._items:
            w.setParent(self)
        self.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Preferred)

    def addWidget(self, w):
        w.setParent(self)
        self._items.append(w)
        self.updateGeometry()

    def _place(self, width, move=True):
        x = y = line = 0
        for w in self._items:
            h = w.sizeHint()
            if x and x + h.width() > width:
                x, y = 0, y + line + self._sp
                line = 0
            if move:
                w.setGeometry(x, y, h.width(), h.height())
            x += h.width() + self._sp
            line = max(line, h.height())
        return y + line

    def resizeEvent(self, e):
        self._place(self.width())

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._place(width, move=False)

    def sizeHint(self):
        w = sum(x.sizeHint().width() + self._sp for x in self._items)
        return QtCore.QSize(min(w, 280), self._place(min(w, 280), move=False))

    def minimumSizeHint(self):
        return QtCore.QSize(max((x.sizeHint().width() for x in self._items), default=0), 0)


def _parts(doc):
    """The parts a person would name: the session's own idea of a body."""
    if doc is None:
        return []
    from .tools import Session

    class _D:
        pass

    d = _D()
    d.doc, d._solid = doc, Session._solid
    try:
        return [o.Label for o in Session.bodies(d)]
    except Exception:
        return [o.Label for o in doc.Objects if o.TypeId == "PartDesign::Body"]


def _sync(combo, items):
    """A combo box holding items, the choice kept where it can be."""
    if [combo.itemText(i) for i in range(combo.count())] == items:
        return
    keep = combo.currentText()
    combo.blockSignals(True)
    combo.clear()
    combo.addItems(items)
    if keep in items:
        combo.setCurrentIndex(items.index(keep))
    combo.blockSignals(False)


def _say(label, ok, out, good=""):
    if ok:
        label.setText(good)
        label.setStyleSheet("color: palette(text);")
    else:
        label.setText((out.get("error") or {}).get("message", "Refused."))
        label.setStyleSheet("color: #d94a4a;")


def summary(settings):
    """A setup's settings in a line: 'h2d, 0.4 nozzle, 0.2 layers, 15% infill'."""
    s = dict(settings or {})
    bits = []
    if s.get("printer"):
        bits.append(str(s["printer"]))
    if s.get("nozzle"):
        bits.append(f"{s['nozzle']:g} nozzle" if isinstance(s["nozzle"], (int, float)) else f"{s['nozzle']} nozzle")
    if s.get("layerHeight"):
        bits.append(f"{s['layerHeight']:g} layers" if isinstance(s["layerHeight"], (int, float)) else str(s["layerHeight"]))
    if s.get("infill") is not None:
        v = s["infill"]
        bits.append(f"{v:g}% infill" if isinstance(v, (int, float)) else f"{v} infill")
    rest = [k for k in s if k not in ("printer", "nozzle", "layerHeight", "infill", "fit")]
    bits += [f"{k} {s[k]}" for k in rest[:3]]
    return ", ".join(bits)


def manual_steps(rows):
    """The Manual tab's rows [(title, [bodies], note)] as drawing_exploded's
    steps, empty ones left out."""
    out = []
    for title, bodies, note in rows:
        if not bodies:
            continue
        st = {"title": title or "", "add": list(bodies)}
        if note:
            st["note"] = note
        out.append(st)
    return out


def manual_plan_steps(rows):
    """The same rows as ScanRulerFC's manual_plan steps: each step places its
    bodies (parts named as the bodies), the note its words, the title kept."""
    out = []
    for k, (title, bodies, note) in enumerate(rows):
        if not bodies:
            continue
        st = {"id": f"s{k + 1}", "place": [{"part": b} for b in bodies], "text": note or title or ""}
        if note and title:
            st["notes"] = title
        out.append(st)
    return out


def missing_tool(out, name):
    e = (out or {}).get("error") or {}
    return e.get("code") == "not_found" and str(e.get("message", "")).startswith(f"{name}:")


class Tabs:
    """Mixed into the Panel: needs self._call(name, args)."""

    # -- setups: how each part is made --

    def _doc_now(self):
        """The document in front: the tabs fill from it (Panel never had
        this, so each tab's refresh raised in the window)."""
        import FreeCAD

        return FreeCAD.ActiveDocument

    def _setups_tab(self):
        w = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(w)
        self.setup_tree = QtWidgets.QTreeWidget()
        self.setup_tree.setHeaderLabels(["Setup", "Parts", "Settings"])
        self.setup_tree.setRootIsDecorated(False)
        lay.addWidget(self.setup_tree, 1)
        row = QtWidgets.QHBoxLayout()
        self.setup_kind = QtWidgets.QComboBox()
        self.setup_kind.addItems(list(store.TYPES))
        self.setup_kind.setToolTip("How the part is made")
        self.setup_body = QtWidgets.QComboBox()
        self.setup_body.setToolTip("The part")
        new = QtWidgets.QPushButton("New")
        new.setToolTip("A new setup of this kind with the part in it")
        new.clicked.connect(self.setup_new)
        add = QtWidgets.QPushButton("Add to")
        add.setToolTip("Put the part in the setup picked above")
        add.clicked.connect(self.setup_add)
        rm = QtWidgets.QPushButton("Remove")
        rm.setToolTip("Remove the setup picked above (its parts then have no process)")
        rm.clicked.connect(self.setup_remove)
        for x in (self.setup_kind, self.setup_body):
            row.addWidget(x, 1)
        for x in (new, add, rm):
            row.addWidget(x)
        lay.addLayout(row)
        self.setup_msg = QtWidgets.QLabel("")
        self.setup_msg.setWordWrap(True)
        lay.addWidget(self.setup_msg)
        return w

    def fill_setups(self):
        ok, out = self._call("setup", {"action": "list"})
        self.setup_tree.clear()
        _sync(self.setup_body, _parts(self._doc_now()))
        if not ok:
            _say(self.setup_msg, ok, out)
            return
        for s in out.get("setups") or []:
            label = store.TYPES.get(s.get("type"), {}).get("label", s.get("type", ""))
            it = QtWidgets.QTreeWidgetItem([f"{s.get('name')} ({label})", ", ".join(s.get("bodies") or []) or "—", summary({k: v for k, v in s.items() if k not in ("id", "type", "name", "bodies")})])
            it.setData(0, QtCore.Qt.UserRole, s.get("id"))
            it.setToolTip(2, it.text(2))
            self.setup_tree.addTopLevelItem(it)
        none = [b for b in _parts(self._doc_now()) if not any(b in (s.get("bodies") or []) for s in out.get("setups") or [])]
        self.setup_msg.setText(("In no setup (no process yet): " + ", ".join(none)) if none else "")
        self.setup_msg.setStyleSheet(f"color: {muted(self.setup_msg)};")

    def _setup_picked(self):
        it = self.setup_tree.currentItem()
        return it.data(0, QtCore.Qt.UserRole) if it else None

    def setup_new(self):
        b = self.setup_body.currentText()
        ok, out = self._call("setup", {"action": "add", "type": self.setup_kind.currentText(), "bodies": [b] if b else []})
        _say(self.setup_msg, ok, out)
        self.fill_setups()

    def setup_add(self):
        sid, b = self._setup_picked(), self.setup_body.currentText()
        if not sid or not b:
            self.setup_msg.setText("Pick a setup in the list and a part.")
            return
        ok, out = self._call("setup", {"action": "set", "id": sid, "add": [b]})
        _say(self.setup_msg, ok, out)
        self.fill_setups()

    def setup_remove(self):
        sid = self._setup_picked()
        if not sid:
            self.setup_msg.setText("Pick a setup in the list.")
            return
        ok, out = self._call("setup", {"action": "remove", "id": sid})
        _say(self.setup_msg, ok, out)
        self.fill_setups()

    # -- materials: what each part is made of --

    def _materials_tab(self):
        w = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(w)
        self.mat_parts = QtWidgets.QTreeWidget()
        self.mat_parts.setHeaderLabels(["Part", "Material", "Density g/cm³"])
        self.mat_parts.setRootIsDecorated(False)
        lay.addWidget(self.mat_parts, 1)
        row = QtWidgets.QHBoxLayout()
        self.mat_find = QtWidgets.QLineEdit()
        self.mat_find.setPlaceholderText("Find a material (pla, steel, oak …)")
        self.mat_find.returnPressed.connect(self.mat_search)
        go = QtWidgets.QPushButton("Find")
        go.clicked.connect(self.mat_search)
        row.addWidget(self.mat_find, 1)
        row.addWidget(go)
        lay.addLayout(row)
        self.mat_hits = QtWidgets.QTreeWidget()
        self.mat_hits.setHeaderLabels(["Material", "Category", "Density", "E MPa", "Yield MPa"])
        self.mat_hits.setRootIsDecorated(False)
        lay.addWidget(self.mat_hits, 1)
        row = QtWidgets.QHBoxLayout()
        give = QtWidgets.QPushButton("Give to part")
        give.setToolTip("The material picked below, for the part picked above")
        give.clicked.connect(self.mat_assign)
        clear = QtWidgets.QPushButton("Forget")
        clear.setToolTip("The part picked above goes back to no material (PLA assumed, and said)")
        clear.clicked.connect(self.mat_clear)
        row.addStretch(1)
        row.addWidget(give)
        row.addWidget(clear)
        lay.addLayout(row)
        self.mat_msg = QtWidgets.QLabel("")
        self.mat_msg.setWordWrap(True)
        lay.addWidget(self.mat_msg)
        return w

    def fill_materials(self):
        from .families import material as mf

        doc = self._doc_now()
        self.mat_parts.clear()
        for b in _parts(doc):
            c, set_ = mf.of_body(doc, b)
            name = (c or {}).get("name", "—") if set_ else "— (PLA assumed)"
            d = (c or {}).get("density")
            it = QtWidgets.QTreeWidgetItem([b, name, f"{d:.3g}" if isinstance(d, (int, float)) else ""])
            self.mat_parts.addTopLevelItem(it)

    def mat_search(self):
        ok, out = self._call("material_list", {"words": self.mat_find.text().strip(), "limit": 40})
        self.mat_hits.clear()
        if not ok:
            _say(self.mat_msg, ok, out)
            return
        for m in out.get("materials") or out.get("results") or []:
            it = QtWidgets.QTreeWidgetItem([str(m.get("name", m.get("id"))), str(m.get("category", "")), str(m.get("density", "")), str(m.get("E", "")), str(m.get("yield", ""))])
            it.setData(0, QtCore.Qt.UserRole, m.get("id"))
            self.mat_hits.addTopLevelItem(it)
        self.mat_msg.setText("")

    def mat_assign(self):
        part, hit = self.mat_parts.currentItem(), self.mat_hits.currentItem()
        if part is None or hit is None:
            self.mat_msg.setText("Pick a part above and a material below.")
            return
        ok, out = self._call("material_set", {"bodies": [part.text(0)], "id": hit.data(0, QtCore.Qt.UserRole)})
        _say(self.mat_msg, ok, out, f"{part.text(0)}: {hit.text(0)}")
        self.fill_materials()

    def mat_clear(self):
        part = self.mat_parts.currentItem()
        if part is None:
            self.mat_msg.setText("Pick a part above.")
            return
        ok, out = self._call("material_clear", {"bodies": [part.text(0)]})
        _say(self.mat_msg, ok, out)
        self.fill_materials()

    # -- fits: the size to model at --

    def _fits_tab(self):
        w = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(w)
        form = QtWidgets.QFormLayout()
        self.fit_nominal = QtWidgets.QDoubleSpinBox()
        self.fit_nominal.setRange(0.1, 2000)
        self.fit_nominal.setDecimals(2)
        self.fit_nominal.setValue(5.0)
        self.fit_nominal.setSuffix(" mm")
        self.fit_of = QtWidgets.QComboBox()
        self.fit_of.addItems(["hole", "shaft"])
        self.fit_body = QtWidgets.QComboBox()
        self.fit_body.setToolTip("The part, so its print setup's calibration is used (blank: the document's)")
        form.addRow("Bought part / mate", self.fit_nominal)
        form.addRow("Modelling a", self.fit_of)
        form.addRow("In part", self.fit_body)
        lay.addLayout(form)
        go = QtWidgets.QPushButton("Size it")
        go.clicked.connect(self.fit_size)
        lay.addWidget(go)
        self.fit_table = QtWidgets.QTreeWidget()
        self.fit_table.setHeaderLabels(["Fit", "Model at mm"])
        self.fit_table.setRootIsDecorated(False)
        lay.addWidget(self.fit_table, 1)
        cal = QtWidgets.QHBoxLayout()
        self.cal_hole = QtWidgets.QDoubleSpinBox()
        self.cal_shaft = QtWidgets.QDoubleSpinBox()
        for sb, tip in ((self.cal_hole, "How much smaller a printed hole comes out, diametral mm"), (self.cal_shaft, "How much bigger a printed peg comes out, diametral mm")):
            sb.setRange(-1, 1)
            sb.setDecimals(2)
            sb.setSingleStep(0.05)
            sb.setToolTip(tip)
        self.cal_hole.setValue(0.2)
        cal.addWidget(QtWidgets.QLabel("Printer: holes small by"))
        cal.addWidget(self.cal_hole)
        cal.addWidget(QtWidgets.QLabel("pegs big by"))
        cal.addWidget(self.cal_shaft)
        keep = QtWidgets.QPushButton("Keep")
        keep.setToolTip("Every hole and fit sized from now on takes it")
        keep.clicked.connect(self.fit_calibrate)
        cal.addWidget(keep)
        lay.addLayout(cal)
        self.fit_msg = QtWidgets.QLabel("")
        self.fit_msg.setWordWrap(True)
        lay.addWidget(self.fit_msg)
        return w

    def fill_fits(self):
        _sync(self.fit_body, [""] + _parts(self._doc_now()))

    def fit_size(self):
        args = {"action": "size", "nominal": round(self.fit_nominal.value(), 3), "of": self.fit_of.currentText()}
        if self.fit_body.currentText():
            args["body"] = self.fit_body.currentText()
        ok, out = self._call("fit", args)
        self.fit_table.clear()
        if not ok:
            _say(self.fit_msg, ok, out)
            return
        for k, v in (out.get("model") or {}).items():
            self.fit_table.addTopLevelItem(QtWidgets.QTreeWidgetItem([k, f"{v:.3f}"]))
        self.fit_msg.setText("Calibration: " + str(out.get("printerFit", "")))
        self.fit_msg.setStyleSheet(f"color: {muted(self.fit_msg)};")

    def fit_calibrate(self):
        ok, out = self._call("fit", {"action": "calibrate", "hole": round(self.cal_hole.value(), 3), "shaft": round(self.cal_shaft.value(), 3)})
        _say(self.fit_msg, ok, out, "Kept: " + str(out.get("calibration", "")))

    # -- manual: an assembly manual, step by step --

    def _manual_tab(self):
        w = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(w)
        lay.addWidget(QtWidgets.QLabel("Tick the parts that go on next, name the step, Add step:"))
        self.man_parts = QtWidgets.QListWidget()
        lay.addWidget(self.man_parts, 1)
        row = QtWidgets.QHBoxLayout()
        self.man_title = QtWidgets.QLineEdit()
        self.man_title.setPlaceholderText("Step title (Base, Lid on …)")
        self.man_note = QtWidgets.QLineEdit()
        self.man_note.setPlaceholderText("Note (optional)")
        add = QtWidgets.QPushButton("Add step")
        add.clicked.connect(self.man_add)
        row.addWidget(self.man_title, 1)
        row.addWidget(self.man_note, 1)
        row.addWidget(add)
        lay.addLayout(row)
        self.man_steps = QtWidgets.QTreeWidget()
        self.man_steps.setHeaderLabels(["#", "Step", "Parts", "Note"])
        self.man_steps.setRootIsDecorated(False)
        lay.addWidget(self.man_steps, 1)
        row = QtWidgets.QHBoxLayout()
        self.man_name = QtWidgets.QLineEdit()
        self.man_name.setPlaceholderText("Manual name")
        drop = QtWidgets.QPushButton("Drop step")
        drop.clicked.connect(self.man_drop)
        make = QtWidgets.QPushButton("Make manual")
        make.setToolTip("Exploded views a step, balloons, a bill of materials and <name>_assembly.md")
        make.clicked.connect(self.man_make)
        self.man_open = QtWidgets.QPushButton("Open")
        self.man_open.setEnabled(False)
        self.man_open.clicked.connect(self.man_show)
        row.addWidget(self.man_name, 1)
        for b in (drop, make, self.man_open):
            row.addWidget(b)
        lay.addLayout(row)
        self.man_msg = QtWidgets.QLabel("")
        self.man_msg.setWordWrap(True)
        lay.addWidget(self.man_msg)
        self.man_rows = []
        self.man_file = None
        return w

    def fill_manual(self):
        used = {b for _t, bs, _n in self.man_rows for b in bs}
        want = [b for b in _parts(self._doc_now()) if b not in used]
        have = [self.man_parts.item(i).text() for i in range(self.man_parts.count())]
        if have == want:
            return
        self.man_parts.clear()
        for b in want:
            it = QtWidgets.QListWidgetItem(b)
            it.setFlags(it.flags() | QtCore.Qt.ItemIsUserCheckable)
            it.setCheckState(QtCore.Qt.Unchecked)
            self.man_parts.addItem(it)

    def _man_show_rows(self):
        self.man_steps.clear()
        for k, (t, bs, n) in enumerate(self.man_rows):
            self.man_steps.addTopLevelItem(QtWidgets.QTreeWidgetItem([str(k + 1), t, ", ".join(bs), n]))
        self.fill_manual()

    def man_add(self):
        picked = [self.man_parts.item(i).text() for i in range(self.man_parts.count()) if self.man_parts.item(i).checkState() == QtCore.Qt.Checked]
        if not picked:
            self.man_msg.setText("Tick the parts this step adds.")
            return
        self.man_rows.append((self.man_title.text().strip() or f"Step {len(self.man_rows) + 1}", picked, self.man_note.text().strip()))
        self.man_title.clear()
        self.man_note.clear()
        self.man_msg.setText("")
        self._man_show_rows()

    def man_drop(self):
        it = self.man_steps.currentItem()
        k = self.man_steps.indexOfTopLevelItem(it) if it else len(self.man_rows) - 1
        if 0 <= k < len(self.man_rows):
            del self.man_rows[k]
            self._man_show_rows()

    def man_make(self):
        steps = manual_steps(self.man_rows)
        if not steps:
            self.man_msg.setText("Add a step first.")
            return
        title = self.man_name.text().strip()
        # ScanRulerFC's manual leads where it is installed: its plan is kept
        # with the project, checked (ways in, words) and rendered as one page.
        ok, out = self._call("manual_plan", {"fromBodies": True, "steps": manual_plan_steps(self.man_rows), **({"settings": {"title": title}} if title else {})})
        if ok:
            ok, out = self._call("manual_render", {})
            _say(self.man_msg, ok, out, f"{out.get('steps', 0)} steps — {(out.get('file') or {}).get('path', '')}" + (" (see its word notes)" if out.get("lint") else ""))
            self.man_file = (out.get("file") or {}).get("path") if ok else None
            self.man_open.setEnabled(bool(self.man_file))
            return
        if not missing_tool(out, "manual_plan"):
            _say(self.man_msg, ok, out, "")
            return
        args = {"steps": steps}
        if title:
            args["name"] = title
        ok, out = self._call("drawing_exploded", args)
        _say(self.man_msg, ok, out, f"{len(out.get('steps') or [])} steps, {len(out.get('bom') or [])} items — {out.get('manual', '')}")
        self.man_file = out.get("manual") if ok else None
        self.man_open.setEnabled(bool(self.man_file))

    def man_show(self):
        if self.man_file and os.path.exists(self.man_file):
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(self.man_file))

    # -- all four, when their tab comes up --

    # -- session: what keeps the agent's work safe while nobody watches --

    def _session_tab(self):
        w = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(w)
        self.sess_pinned = QtWidgets.QLabel("")
        self.sess_pinned.setWordWrap(True)
        self.sess_pinned.setToolTip("The document the agent works in, whatever is in front")
        lay.addWidget(self.sess_pinned)
        held = QtWidgets.QHBoxLayout()
        self.sess_held = QtWidgets.QLabel("")
        self.sess_held.setWordWrap(True)
        held.addWidget(self.sess_held, 1)
        self.sess_go = QtWidgets.QPushButton("Go")
        self.sess_go.setToolTip("Run the heavy jobs held while you worked")
        self.sess_go.clicked.connect(lambda: self._sess_do("queue_go", {}))
        held.addWidget(self.sess_go)
        lay.addLayout(held)
        self.sess_tree = QtWidgets.QTreeWidget()
        self.sess_tree.setHeaderLabels(["", "When", "What"])
        self.sess_tree.setRootIsDecorated(True)
        lay.addWidget(self.sess_tree, 1)
        row = FlowRow()
        for label, tip, fn in (("Use copy", "Save the recovered copy picked above over its file (you type to confirm)", self.sess_accept),
                               ("Roll back", "Put FreeCAD's settings back as they were before the change picked above", self.sess_rollback),
                               ("Approve", "Let Agent Link run the macro picked above (you type to confirm)", self.sess_approve),
                               ("Run", "Run the approved macro picked above", self.sess_run)):
            b = QtWidgets.QPushButton(label)
            b.setToolTip(tip)
            b.clicked.connect(fn)
            row.addWidget(b)
        lay.addWidget(row)
        return w

    def _sess_do(self, tool, args):
        ok, r = self._call(tool, args)
        if not ok:
            QtWidgets.QMessageBox.information(self, "Agent Link", (r.get("error") or {}).get("message", str(r)))
        self.fill_session()
        return ok, r

    def fill_session(self):
        import json as _json

        from . import keep

        ok, d = self._call("doc_switch", {})
        name = (d.get("label") or d.get("document")) if ok else None
        self.sess_pinned.setText(f"Agent works in: {name}" if name else "Agent works in: the document in front")
        q = keep._load(keep.path("queue.json"), {}) or {}
        held = q.get("held") or []
        self.sess_held.setText(f"Held for your go: {q.get('notice') or len(held)}" if held else "Nothing held.")
        self.sess_go.setEnabled(bool(held))
        self.sess_tree.clear()

        def section(title, rows, kind):
            top = QtWidgets.QTreeWidgetItem([title, "", ""])
            for r in rows:
                it = QtWidgets.QTreeWidgetItem(["", str(r[0]), str(r[1])])
                it.setData(0, QtCore.Qt.UserRole, _json.dumps({"kind": kind, **r[2]}))
                top.addChild(it)
            self.sess_tree.addTopLevelItem(top)
            top.setExpanded(bool(rows))

        ok, rec = self._call("app_recover", {"action": "list"})
        rows = []
        for docrow in (rec.get("documents") or []) if ok else []:
            for c in (docrow.get("copies") or [])[:5]:
                rows.append((c.get("time", ""), f"{docrow.get('label')}: {c.get('source', '')}",
                             {"document": docrow.get("document"), "label": docrow.get("label")}))
        section("Recovered copies", rows, "recover")
        ok, ph = self._call("pref_history", {"limit": 10})
        section("Settings changes", [(c.get("time", ""), f"{c.get('path', '')}/{c.get('name', '')}: {c.get('from')} → {c.get('to')}", {"to": c.get("change")})
                                     for c in (ph.get("changes") or [])] if ok else [], "pref")
        ok, ml = self._call("macro_list", {})
        section("Macros", [(m.get("state", ""), m.get("name"), {"name": m.get("name")})
                           for m in (ml.get("macros") or [])] if ok else [], "macro")

    def _sess_pick(self, kind):
        import json as _json

        it = self.sess_tree.currentItem()
        raw = it.data(0, QtCore.Qt.UserRole) if it else None
        d = _json.loads(raw) if raw else None
        if not d or d.get("kind") != kind:
            QtWidgets.QMessageBox.information(self, "Agent Link", f"Pick a line under {dict(recover='Recovered copies', pref='Settings changes', macro='Macros')[kind]} first.")
            return None
        return d

    def _typed(self, title, want):
        text, ok = QtWidgets.QInputDialog.getText(self, title, f"Type  {want}  to confirm:")
        return text if ok and text.strip() == want else None

    def sess_accept(self):
        d = self._sess_pick("recover")
        if d:
            ok, r = self._sess_do("app_recover", {"action": "auto", "documents": [{"name": d["document"], "label": d.get("label")}]})
            rec = (r.get("recovered") or [None])[0] if ok else None
            if rec:
                if not rec.get("file"):
                    QtWidgets.QMessageBox.information(self, "Agent Link", f"{rec['as']} is open; it was never saved to a file — save it where you like.")
                    return
                typed = self._typed("Use the recovered copy", f"save over {os.path.basename(rec['file'])}")
                if typed:
                    self._sess_do("app_recover", {"action": "accept", "document": rec["document"], "confirm": typed})

    def sess_rollback(self):
        d = self._sess_pick("pref")
        if d and d.get("to") is not None:
            self._sess_do("pref_rollback", {"to": d["to"]})

    def sess_approve(self):
        d = self._sess_pick("macro")
        if d:
            typed = self._typed("Approve the macro", f"approve {d['name']}")
            if typed:
                self._sess_do("macro_approve", {"name": d["name"], "confirm": typed})

    def sess_run(self):
        d = self._sess_pick("macro")
        if d:
            self._sess_do("macro_run", {"name": d["name"]})

    def fill_tab(self, title):
        try:
            {"Setups": self.fill_setups, "Materials": self.fill_materials, "Fits": self.fill_fits, "Manual": self.fill_manual,
             "Session": self.fill_session}.get(title, lambda: None)()
        except Exception as e:
            import FreeCAD

            FreeCAD.Console.PrintLog(f"Agent Link panel ({title}): {e}\n")
