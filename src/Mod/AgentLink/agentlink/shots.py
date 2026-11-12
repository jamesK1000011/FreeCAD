# SPDX-License-Identifier: LGPL-2.1-or-later
# Screenshots for the agent, as in the ScanRuler fork's snapshot plugin: a
# camera button (⌥⇧S) takes the FreeCAD window as the person sees it, and a
# card opens on it with a note, mark-up — numbered pins with a comment each,
# arrows, boxes, circles, pen strokes, in a few colours, with undo — and
# Save. Each shot is written, marks drawn in, to the output folder's shots/
# (the plain one beside it), with an index the agent reads (snapshot_latest).
# Only the person takes a shot; the agent reads them.

import datetime
import json
import os

import FreeCAD

KEEP = 8
COLOURS = {"red": (226, 60, 60), "yellow": (240, 190, 40), "blue": (60, 130, 230), "green": (60, 170, 90), "white": (245, 245, 245)}


def folder():
    d = os.path.join(os.path.expanduser("~/AgentLink"), "shots")
    os.makedirs(d, exist_ok=True)
    return d


def index():
    try:
        with open(os.path.join(folder(), "index.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def save_index(rows):
    with open(os.path.join(folder(), "index.json"), "w", encoding="utf-8") as f:
        json.dump(rows[-KEEP * 4:], f, indent=1)


def record(shot):
    rows = [r for r in index() if r["id"] != shot["id"]]
    rows.append(shot)
    save_index(rows)


def draw_marks(painter, marks, QtCore, QtGui, scale=1.0):
    """Paint marks (in image pixels) with painter; scale maps them to the
    surface being painted."""
    for m in marks:
        col = QtGui.QColor(*COLOURS.get(m.get("colour", "red"), COLOURS["red"]))
        pen = QtGui.QPen(col)
        pen.setWidthF(max(2.0, 4.0 * scale))
        pen.setCapStyle(QtCore.Qt.RoundCap)
        painter.setPen(pen)
        painter.setBrush(QtCore.Qt.NoBrush)
        pts = [QtCore.QPointF(x * scale, y * scale) for x, y in m.get("points", [])]
        k = m["kind"]
        if k == "pin" and pts:
            r = 14 * max(scale, 0.6)
            painter.setBrush(col)
            painter.drawEllipse(pts[0], r, r)
            painter.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255)))
            f = painter.font()
            f.setBold(True)
            f.setPointSizeF(max(8.0, 12 * scale))
            painter.setFont(f)
            painter.drawText(QtCore.QRectF(pts[0].x() - r, pts[0].y() - r, 2 * r, 2 * r), QtCore.Qt.AlignCenter, str(m.get("n", "")))
        elif k in ("box", "circle") and len(pts) >= 2:
            rect = QtCore.QRectF(pts[0], pts[-1]).normalized()
            (painter.drawRect if k == "box" else painter.drawEllipse)(rect)
        elif k == "arrow" and len(pts) >= 2:
            a, b = pts[0], pts[-1]
            painter.drawLine(a, b)
            import math

            ang = math.atan2(b.y() - a.y(), b.x() - a.x())
            head = 18 * max(scale, 0.6)
            for d in (2.6, -2.6):
                painter.drawLine(b, QtCore.QPointF(b.x() + head * math.cos(ang + d), b.y() + head * math.sin(ang + d)))
        elif k == "pen" and len(pts) >= 2:
            for p, q in zip(pts, pts[1:]):
                painter.drawLine(p, q)


def take():
    """The camera button: grab the window and open the card on it."""
    import FreeCADGui

    from .grab import grab_window

    pix = grab_window()
    stamp = datetime.datetime.now()
    sid = stamp.strftime("%Y%m%d-%H%M%S")
    plain = os.path.join(folder(), f"shot-{sid}-plain.png")
    pix.save(plain, "PNG")
    wb = FreeCADGui.activeWorkbench().name() if FreeCADGui.activeWorkbench() else None
    shot = {"id": sid, "time": stamp.isoformat(timespec="seconds"), "workbench": wb,
            "document": FreeCAD.ActiveDocument.Label if FreeCAD.ActiveDocument else None,
            "original": plain, "path": plain, "note": "", "marks": [], "pins": [], "done": False}
    record(shot)
    card = Card(shot, pix)
    card.show()
    card.raise_()
    _open.append(card)
    return shot


_open = []


def _qt():
    from PySide import QtCore, QtGui

    QtW = getattr(__import__("PySide", fromlist=["QtWidgets"]), "QtWidgets", QtGui)
    return QtCore, QtGui, QtW


QtCore, QtGui, QtW = _qt()


class Canvas(QtW.QWidget):
    TOOLS = ("pin", "arrow", "box", "circle", "pen")

    def __init__(self, pix, card):
        super().__init__()
        self.pix = pix
        self.card = card
        self.marks = []
        self.tool = "pin"
        self.colour = "red"
        self.live = None
        self.setMinimumSize(320, 240)
        self.setMouseTracking(False)

    def scale(self):
        w, h = self.pix.width() / self.pix.devicePixelRatio(), self.pix.height() / self.pix.devicePixelRatio()
        return min(self.width() / w, self.height() / h) / self.pix.devicePixelRatio()

    def to_image(self, pos):
        s = self.scale()
        return [pos.x() / s, pos.y() / s]

    def paintEvent(self, _e):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        s = self.scale()
        p.drawPixmap(QtCore.QRectF(0, 0, self.pix.width() * s, self.pix.height() * s), self.pix, QtCore.QRectF(self.pix.rect()))
        draw_marks(p, self.marks + ([self.live] if self.live else []), QtCore, QtGui, s)
        p.end()

    def mousePressEvent(self, e):
        pt = self.to_image(e.pos())
        if self.tool == "pin":
            n = sum(1 for m in self.marks if m["kind"] == "pin") + 1
            text, ok = QtW.QInputDialog.getText(self, f"Pin {n}", "Comment:")
            if ok:
                self.marks.append({"kind": "pin", "n": n, "points": [pt], "comment": text.strip(), "colour": self.colour})
            self.update()
            return
        self.live = {"kind": self.tool, "points": [pt, pt], "colour": self.colour}

    def mouseMoveEvent(self, e):
        if self.live:
            pt = self.to_image(e.pos())
            if self.live["kind"] == "pen":
                self.live["points"].append(pt)
            else:
                self.live["points"][-1] = pt
            self.update()

    def mouseReleaseEvent(self, _e):
        if self.live:
            self.marks.append(self.live)
            self.live = None
            self.update()

    def undo(self):
        if self.marks:
            self.marks.pop()
            self.update()


class Card(QtW.QWidget):
    def __init__(self, shot, pix):
        super().__init__(None, QtCore.Qt.Tool | QtCore.Qt.WindowStaysOnTopHint)
        self.setWindowTitle("Screenshot for the agent")
        self.shot = shot
        self.pix = pix
        lay = QtW.QVBoxLayout(self)
        bar = QtW.QHBoxLayout()
        self.canvas = Canvas(pix, self)
        for key, name in zip("PABCD", Canvas.TOOLS):
            b = QtW.QPushButton(name.capitalize())
            b.setToolTip(f"{name.capitalize()} ({key})")
            b.setCheckable(True)
            b.setChecked(name == "pin")
            b.setShortcut(key)
            b.clicked.connect(lambda _c=False, n=name: self.pick(n))
            bar.addWidget(b)
            setattr(self, "b_" + name, b)
        self.colour = QtW.QComboBox()
        self.colour.addItems(list(COLOURS))
        self.colour.currentTextChanged.connect(lambda c: setattr(self.canvas, "colour", c))
        bar.addWidget(self.colour)
        undo = QtW.QPushButton("Undo")
        undo.setShortcut("Ctrl+Z")
        undo.clicked.connect(self.canvas.undo)
        bar.addWidget(undo)
        bar.addStretch(1)
        lay.addLayout(bar)
        lay.addWidget(self.canvas, 1)
        self.note = QtW.QLineEdit()
        self.note.setPlaceholderText("A note for the agent")
        lay.addWidget(self.note)
        keys = QtW.QHBoxLayout()
        keys.addStretch(1)
        for label, fn in (("Discard", self.discard), ("Done", self.done)):
            b = QtW.QPushButton(label)
            b.clicked.connect(fn)
            keys.addWidget(b)
        lay.addLayout(keys)
        w = pix.width() / pix.devicePixelRatio()
        h = pix.height() / pix.devicePixelRatio()
        k = min(1.0, 900 / max(w, 1), 700 / max(h, 1))
        self.resize(int(w * k) + 24, int(h * k) + 120)

    def pick(self, name):
        self.canvas.tool = name
        for n in Canvas.TOOLS:
            getattr(self, "b_" + n).setChecked(n == name)

    def done(self):
        out = QtGui.QPixmap(self.pix)
        out.setDevicePixelRatio(1.0)  # marks are in the shot's own pixels
        p = QtGui.QPainter(out)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        draw_marks(p, self.canvas.marks, QtCore, QtGui, 1.0)
        p.end()
        marked = self.shot["original"].replace("-plain.png", ".png")
        out.save(marked, "PNG")
        self.shot.update(path=marked, note=self.note.text().strip(), marks=self.canvas.marks, done=True,
                         pins=[{"n": m["n"], "at": [round(v) for v in m["points"][0]], "comment": m.get("comment", "")} for m in self.canvas.marks if m["kind"] == "pin"])
        record(self.shot)
        FreeCAD.Console.PrintMessage(f"Agent Link: screenshot saved for the agent ({marked})\n")
        self.close()

    def discard(self):
        rows = [r for r in index() if r["id"] != self.shot["id"]]
        save_index(rows)
        self.close()

    def closeEvent(self, e):
        if self in _open:
            _open.remove(self)
        super().closeEvent(e)
