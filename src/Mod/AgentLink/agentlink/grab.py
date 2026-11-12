# SPDX-License-Identifier: LGPL-2.1-or-later
# A picture of the FreeCAD window (or a part of it) as the person sees it:
# Qt draws the widgets, and the 3D view, which OpenGL draws and a widget
# grab leaves blank, is rendered by FreeCAD and painted into its place.
# No screen-recording permission: FreeCAD draws it all itself.

import os

import FreeCAD
import FreeCADGui


def grab_window(target=None):
    gui = FreeCADGui
    target = target or gui.getMainWindow()
    gui.updateGui()
    pix = target.grab()
    # The 3D view is drawn by OpenGL, which a widget grab leaves blank:
    # render it the way view_snapshot does and paint it in its place.
    try:
        import tempfile

        from PySide import QtCore, QtGui

        QtW = getattr(__import__("PySide", fromlist=["QtWidgets"]), "QtWidgets", QtGui)
        gd = gui.ActiveDocument
        view = gd.activeView() if gd else None
        gl = next((w for w in target.findChildren(QtW.QWidget) if w.isVisible() and type(w).__name__ in ("QuarterWidget", "QOpenGLWidget", "QGLWidget")), None)
        if view is not None and gl is not None and hasattr(view, "saveImage"):
            tmp = os.path.join(tempfile.gettempdir(), "agentlink_view.png")
            ratio = getattr(pix, "devicePixelRatio", lambda: 1.0)()
            view.saveImage(tmp, int(gl.width() * ratio), int(gl.height() * ratio), "Current")
            img = QtGui.QImage(tmp)
            painter = QtGui.QPainter(pix)
            painter.drawImage(QtCore.QRect(gl.mapTo(target, QtCore.QPoint(0, 0)), gl.size()), img)
            # What floats over the view (the agent's caption) goes back on top.
            for lbl in target.findChildren(QtW.QLabel):
                if lbl.isVisible() and lbl.text().startswith("✦"):
                    painter.drawPixmap(lbl.mapTo(target, QtCore.QPoint(0, 0)), lbl.grab())
            painter.end()
    except Exception as e:
        FreeCAD.Console.PrintLog(f"Agent Link window_snapshot: 3D view not painted in ({e})\n")
    return pix
