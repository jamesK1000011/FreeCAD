# SPDX-License-Identifier: LGPL-2.1-or-later
# The Agent Link workbench: Start link and Stop link. While the link is on,
# an agent's calls arrive in the document open in this window.

import FreeCADGui


class AgentLinkWorkbench(FreeCADGui.Workbench):
    MenuText = "Agent Link"
    ToolTip = "Drive FreeCAD from an agent"

    def Initialize(self):
        from agentlink.commands import _Panel, _StartLink, _StopLink

        FreeCADGui.addCommand("AgentLink_Start", _StartLink())
        FreeCADGui.addCommand("AgentLink_Stop", _StopLink())
        FreeCADGui.addCommand("AgentLink_Panel", _Panel())
        self.appendToolbar("Agent Link", ["AgentLink_Start", "AgentLink_Stop", "AgentLink_Panel"])
        self.appendMenu("Agent Link", ["AgentLink_Start", "AgentLink_Stop", "AgentLink_Panel"])

    def GetClassName(self):
        return "Gui::PythonWorkbench"


FreeCADGui.addWorkbench(AgentLinkWorkbench())


def _agentlink_autostart():
    from agentlink.commands import autostart

    autostart()


try:
    from PySide import QtCore

    # After the window is up: the link needs a document and the GUI's timer.
    QtCore.QTimer.singleShot(1500, _agentlink_autostart)
except Exception:
    pass
