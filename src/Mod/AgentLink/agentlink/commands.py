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


class _Shot:
    def GetResources(self):
        return {"MenuText": "Screenshot for the agent", "ToolTip": "Take the window as you see it, mark it up and leave it for the agent (Alt+Shift+S)", "Accel": "Alt+Shift+S"}

    def IsActive(self):
        return True

    def Activated(self):
        from agentlink import shots

        shots.take()


def autostart():
    """Turn the link on when the FreeCAD window opens, so an agent's calls
    land in it without a click (Preferences: Mod/AgentLink AutoStart, on by
    default). Quietly does nothing if the port is taken."""
    import FreeCAD

    if not FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Mod/AgentLink").GetBool("AutoStart", True):
        return
    import agentlink_state

    try:
        import FreeCADGui

        FreeCADGui.addCommand("AgentLink_Shot", _Shot())
    except Exception:
        pass
    if agentlink_state.link is not None:
        return
    try:
        from agentlink.gui_link import Link

        link = Link()
        link.start()
        agentlink_state.link = link
    except OSError as e:
        FreeCAD.Console.PrintLog(f"Agent Link: link not started ({e})\n")
