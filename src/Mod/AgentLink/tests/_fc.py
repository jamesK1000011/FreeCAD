# SPDX-License-Identifier: LGPL-2.1-or-later
# Shared by the analysis families' tests: FreeCAD found or not, a server on
# a session of its own, and a call that answers (refused, answer).
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
if os.environ.get("FREECAD_LIB"):
    sys.path.append(os.environ["FREECAD_LIB"])
try:
    import FreeCAD  # noqa: F401

    HAVE = True
except ImportError:
    HAVE = False


def server():
    from agentlink.tools import Session, build_server

    session = Session(out_dir=tempfile.mkdtemp())
    return session, build_server(session, lean=False)


def caller(srv):
    def call(name, args=None):
        r = srv.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args or {}}})["result"]
        return r.get("isError", False), json.loads(r["content"][0]["text"])

    return call


def add_body(session, label, shape):
    o = session.doc.addObject("Part::Feature", "Body")
    o.Label = label
    o.Shape = shape
    session.doc.recompute()
    return o
