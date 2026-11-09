# SPDX-License-Identifier: LGPL-2.1-or-later
# The Agent Link workbench: Start link and Stop link. While the link is on,
# an agent's calls arrive in the document open in this window.

import FreeCADGui


class _StartLink:
    def GetResources(self):
        return {"MenuText": "Start link", "ToolTip": "Let an agent drive this window (local only)"}

    def IsActive(self):
        import agentlink_state

        return agentlink_state.link is None

    def Activated(self):
        import agentlink_state
        from agentlink.gui_link import Link

        agentlink_state.link = Link()
        agentlink_state.link.start()


class _StopLink:
    def GetResources(self):
        return {"MenuText": "Stop link", "ToolTip": "Close the agent's link"}

    def IsActive(self):
        import agentlink_state

        return agentlink_state.link is not None

    def Activated(self):
        import agentlink_state

        agentlink_state.link.stop()
        agentlink_state.link = None


class AgentLinkWorkbench(FreeCADGui.Workbench):
    MenuText = "Agent Link"
    ToolTip = "Drive FreeCAD from an agent"

    def Initialize(self):
        FreeCADGui.addCommand("AgentLink_Start", _StartLink())
        FreeCADGui.addCommand("AgentLink_Stop", _StopLink())
        self.appendToolbar("Agent Link", ["AgentLink_Start", "AgentLink_Stop"])
        self.appendMenu("Agent Link", ["AgentLink_Start", "AgentLink_Stop"])

    def GetClassName(self):
        return "Gui::PythonWorkbench"


FreeCADGui.addWorkbench(AgentLinkWorkbench())
