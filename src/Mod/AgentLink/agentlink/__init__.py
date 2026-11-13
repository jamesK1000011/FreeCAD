# SPDX-License-Identifier: LGPL-2.1-or-later
"""Agent Link: FreeCAD driven by an agent over the Model Context Protocol."""


def run(tool, args=None):
    """Call an Agent Link tool from Python — what a saved macro does
    (macro_save writes agentlink.run(...) lines): the same refusals and undo
    steps as a live call. Answers the tool's result; a refusal raises
    RuntimeError with its message."""
    from . import macros

    return macros.run(tool, args or {})
