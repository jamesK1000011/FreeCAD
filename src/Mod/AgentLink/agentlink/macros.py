# SPDX-License-Identifier: LGPL-2.1-or-later
# Macros of Agent Link calls (unattended use, J): a session's steps saved as
# a .FCMacro in FreeCAD's own macro folder, its key sizes named at the top,
# replayed through agentlink.run(tool, args) — the same refusals and undo
# steps as live calls. A macro is Python with the run of the Mac, so one
# runs only once the person approved it (its SHA-256 recorded; changed
# code needs approving again), and the agent never approves its own.

import ast
import os

import FreeCAD

# The server a running macro calls into: macro_run sets it for the run; a
# macro run by hand in the window gets a session on the document in front.
_current = []


def macro_dir():
    try:
        d = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Macro").GetString("MacroPath", "")
    except Exception:
        d = ""
    if not d:
        try:
            d = FreeCAD.getUserMacroDir(True)
        except Exception:
            d = os.path.join(FreeCAD.getUserAppDataDir(), "Macro")
    return d


def macro_path(name):
    return os.path.join(macro_dir(), f"{name}.FCMacro")


def run(tool, args):
    import json

    if _current:
        server = _current[-1]
    else:
        from .tools import Session, build_server

        doc = FreeCAD.ActiveDocument or FreeCAD.newDocument("AgentLink")
        server = build_server(Session(doc=doc, out_dir=os.path.expanduser("~/AgentLink")), lean=False)
        _current.append(server)
    r = server.call({"name": tool, "arguments": args})
    body = json.loads(r["content"][0]["text"])
    if r.get("isError"):
        raise RuntimeError(f"{tool}: {(body.get('error') or {}).get('message', body)}")
    return body


def with_params(text, params):
    """The macro's source with its named constants set to params (only
    top-level NAME = literal lines; the rest is the approved code as it is)."""
    tree = ast.parse(text)
    known = set()
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            n = node.targets[0].id
            if n in params:
                node.value = ast.Constant(params[n]) if not isinstance(params[n], (list, dict)) else ast.parse(repr(params[n]), mode="eval").body
                known.add(n)
    missing = [k for k in params if k not in known]
    return ast.fix_missing_locations(tree), missing
