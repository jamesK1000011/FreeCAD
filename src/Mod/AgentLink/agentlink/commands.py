# SPDX-License-Identifier: LGPL-2.1-or-later
# The Agent Link workbench's commands. They live in a module of their own:
# FreeCAD runs InitGui.py so that its top-level names are gone by the time
# the workbench's Initialize runs.


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


class _Panel:
    def GetResources(self):
        return {"MenuText": "Notes and steps", "ToolTip": "Notes pinned to the parts, and the build played step by step"}

    def IsActive(self):
        return True

    def Activated(self):
        from agentlink import panel

        panel.show()
