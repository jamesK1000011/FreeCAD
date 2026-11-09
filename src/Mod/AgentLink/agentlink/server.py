# SPDX-License-Identifier: LGPL-2.1-or-later
# The agent's server, over stdio — what an MCP client (Claude Desktop, Claude
# Code) starts. Two ways:
#
#   headless   the tools run here, on a document of their own: FreeCAD
#              imported into this Python (FreeCADCmd, or a Python that
#              FreeCAD's library is on — conda-forge's freecad, or
#              FREECAD_LIB pointing at it)
#   --connect  relay to the FreeCAD GUI's Agent Link (the workbench's Start
#              link, port 7318 by default): the person watches each body
#              arrive in their own window
#
# stdout is the protocol's: FreeCAD and OCCT print to it from C++, so the
# real stdout is kept for the protocol and file descriptor 1 pointed at
# stderr before anything is imported.

import os
import socket
import sys
import threading

VERSION = "0.1.0"


def _protocol_stdout():
    real = os.fdopen(os.dup(1), "w", buffering=1, encoding="utf-8")
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    return real


def _import_freecad():
    try:
        import FreeCAD  # noqa: F401
        return
    except ImportError:
        pass
    for p in filter(None, [os.environ.get("FREECAD_LIB"), "/usr/lib/freecad/lib", "/usr/lib/freecad-python3/lib", "/Applications/FreeCAD.app/Contents/Resources/lib"]):
        if os.path.isdir(p) and p not in sys.path:
            sys.path.append(p)
    import FreeCAD  # noqa: F401  — the error names what is missing


def relay(port, out):
    """Pass lines both ways between this process's stdio and the GUI's link."""
    try:
        s = socket.create_connection(("127.0.0.1", port), timeout=10)
    except OSError as e:
        sys.stderr.write(f"Agent Link: no FreeCAD window listening on port {port} ({e}). In FreeCAD, switch to the Agent Link workbench and press Start link.\n")
        sys.exit(2)
    s.settimeout(None)
    f = s.makefile("rw", encoding="utf-8", newline="\n")

    def down():
        for line in f:
            out.write(line)
            out.flush()
        os._exit(0)

    threading.Thread(target=down, daemon=True).start()
    for line in sys.stdin:
        f.write(line)
        f.flush()


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    # Env as well as flags: FreeCADCmd takes the arguments after a script
    # for files to open, so a script it runs is given these by env.
    out_dir = os.path.abspath(os.environ.get("AGENTLINK_OUT", os.getcwd()))
    port = int(os.environ["AGENTLINK_CONNECT"]) if os.environ.get("AGENTLINK_CONNECT", "").isdigit() else None
    while argv:
        a = argv.pop(0)
        if a == "--out":
            out_dir = os.path.abspath(argv.pop(0))
        elif a == "--connect":
            port = int(argv.pop(0)) if argv and argv[0].isdigit() else 7318
        elif a in ("-h", "--help"):
            sys.stderr.write("agentlink [--out DIR] [--connect [PORT]]\n")
            return 0
    out = _protocol_stdout()
    if port is not None:
        relay(port, out)
        return 0
    _import_freecad()
    from .protocol import Server, serve_lines
    from .tools import INSTRUCTIONS, Session, make_tools

    session = Session(out_dir=out_dir)
    server = Server("freecad-agentlink", VERSION, make_tools(session), INSTRUCTIONS)

    def write(text):
        out.write(text)
        out.flush()

    serve_lines(server, sys.stdin.readline, write)
    return 0


if __name__ == "__main__":
    sys.exit(main())
