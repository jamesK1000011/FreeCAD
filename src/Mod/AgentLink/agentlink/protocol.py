# SPDX-License-Identifier: LGPL-2.1-or-later
# The Model Context Protocol, as much as a tool server needs: JSON-RPC 2.0,
# one message a line (the stdio framing), initialize, tools/list,
# tools/call and ping. No dependencies, so it runs in FreeCAD's own Python
# (FreeCADCmd, the GUI) as well as a plain interpreter — nothing to install
# into an application bundle.

import json
import sys
import time
import traceback

PROTOCOL = "2025-06-18"


class ToolError(Exception):
    """A refusal the agent can act on: a code (invalid_input, not_found,
    failed) and a message that says what to do next."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


class Tool:
    def __init__(self, name, description, schema, fn, changes=False, group="core"):
        self.name = name
        self.description = description
        self.schema = schema
        self.fn = fn
        # A change runs as one undo step (the session wraps it in a
        # transaction); a read changes nothing.
        self.changes = changes
        # Its group: listed when the group is on (tool_groups), callable
        # by name whether listed or not.
        self.group = group

    def listing(self):
        return {"name": self.name, "description": self.description, "inputSchema": self.schema}


def check_input(schema, args):
    """Unknown keys and missing required ones refused by name, as the
    answers name them: what the agent got wrong and what is allowed."""
    if not isinstance(args, dict):
        raise ToolError("invalid_input", "arguments: an object.")
    props = schema.get("properties", {})
    unknown = [k for k in args if k not in props]
    if unknown and schema.get("additionalProperties") is False:
        raise ToolError("invalid_input", f"input.{unknown[0]}: not a known field — the fields here: {', '.join(props) or 'none'}")
    for k in schema.get("required", []):
        if k not in args:
            raise ToolError("invalid_input", f"input.{k}: required.")
    for k, v in list(args.items()):
        spec = props.get(k)
        if isinstance(spec, dict) and v is not None:
            args[k] = _typed(f"input.{k}", spec, v)


_PY = {"string": (str,), "number": (int, float), "integer": (int,), "boolean": (bool,), "array": (list,), "object": (dict,)}


def _typed(where, spec, v):
    """A value checked against its field's type and enum — and coerced where
    a client sent the same thing in another dress: a number or a boolean as
    a string, a list or an object as a string of its JSON."""
    t = spec.get("type")
    if isinstance(t, str) and t in _PY:
        ok = isinstance(v, _PY[t]) and not (t in ("number", "integer") and isinstance(v, bool))
        if not ok and isinstance(v, str):
            text = v.strip()
            try:
                if t == "number":
                    v, ok = float(text), True
                elif t == "integer" and text.lstrip("-").isdigit():
                    v, ok = int(text), True
                elif t == "boolean" and text.lower() in ("true", "false"):
                    v, ok = text.lower() == "true", True
                elif t in ("array", "object"):
                    parsed = json.loads(text)
                    if isinstance(parsed, _PY[t]):
                        v, ok = parsed, True
            except ValueError:
                pass
        if not ok and t == "number" and isinstance(v, int) and not isinstance(v, bool):
            ok = True
        if not ok and t == "integer" and isinstance(v, float) and v.is_integer():
            v, ok = int(v), True
        if not ok:
            raise ToolError("invalid_input", f"{where}: {_said(t)}, not {_kind(v)}.")
    if "enum" in spec and v not in spec["enum"]:
        lowered = {str(e).lower(): e for e in spec["enum"]}
        if isinstance(v, str) and v.lower() in lowered:
            return lowered[v.lower()]
        raise ToolError("invalid_input", f"{where}: one of {', '.join(map(str, spec['enum']))} — not {v!r}.")
    return v


def _said(t):
    return {"string": "text", "number": "a number", "integer": "a whole number", "boolean": "true or false", "array": "a list", "object": "an object"}[t]


def _kind(v):
    if isinstance(v, bool):
        return "true/false"
    return {str: "text", int: "a number", float: "a number", list: "a list", dict: "an object"}.get(type(v), type(v).__name__)


def compact(obj):
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


class Server:
    def __init__(self, name, version, tools, instructions="", run=None, groups=None, resources=None):
        self.name = name
        self.version = version
        self.tools = {t.name: t for t in tools}
        self.instructions = instructions
        # How a call is run: run(tool, fn) — directly, or as one undo step
        # (the session's), or handed to the GUI's thread.
        self.run = run or (lambda tool, fn: fn())
        # The groups listed now; None lists every tool.
        self.groups = groups
        self.resources = resources
        # Notifications to send after the reply in hand (list_changed).
        self.outbox = []

    def add(self, tool):
        self.tools[tool.name] = tool

    def listed(self):
        return [t for t in self.tools.values() if self.groups is None or t.group in self.groups]

    def set_groups(self, groups):
        self.groups = groups
        self.outbox.append({"jsonrpc": "2.0", "method": "notifications/tools/list_changed"})

    def handle(self, msg):
        """One message in; the reply, or None for a notification."""
        mid = msg.get("id")
        method = msg.get("method")
        if method is None:
            return None
        try:
            if method == "initialize":
                result = {
                    "protocolVersion": PROTOCOL,
                    "capabilities": {"tools": {"listChanged": True}, **({"resources": {}} if self.resources else {})},
                    "serverInfo": {"name": self.name, "version": self.version},
                    "instructions": self.instructions,
                }
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": [t.listing() for t in self.listed()]}
            elif method == "resources/list" and self.resources:
                result = {"resources": self.resources.list()}
            elif method == "resources/read" and self.resources:
                result = {"contents": [self.resources.read((msg.get("params") or {}).get("uri", ""))]}
            elif method == "tools/call":
                result = self.call(msg.get("params") or {})
            elif mid is None:
                return None  # notifications/initialized and the like
            else:
                return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"{method}: not supported."}}
        except Exception as e:  # a protocol error, not a tool's
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": str(e)}}
        return None if mid is None else {"jsonrpc": "2.0", "id": mid, "result": result}

    def call(self, params):
        name = params.get("name")
        args = params.get("arguments") or {}
        tool = self.tools.get(name)
        if tool is None:
            return self.refusal("not_found", f"{name}: no such tool — the tools: {', '.join(self.tools)}.")
        t0 = time.time()
        try:
            check_input(tool.schema, args)
            out = self.run(tool, lambda: tool.fn(args))
        except ToolError as e:
            return self.refusal(e.code, e.message)
        except ValueError as e:
            return self.refusal("invalid_input", str(e))
        except Exception as e:
            traceback.print_exc(file=sys.stderr)
            return self.refusal("failed", f"{name} failed: {e}")
        image = None
        if isinstance(out, dict):
            # A picture for the agent to look at: image content beside the
            # text (the file is written as well; inline false leaves it out).
            image = out.pop("_png", None)
            out.setdefault("ms", int((time.time() - t0) * 1000))
        content = [{"type": "text", "text": compact(out)}]
        if image is not None:
            import base64

            content.append({"type": "image", "data": base64.b64encode(image).decode("ascii"), "mimeType": "image/png"})
        return {"content": content}

    @staticmethod
    def refusal(code, message):
        return {"content": [{"type": "text", "text": compact({"error": {"code": code, "message": message}})}], "isError": True}


def serve_lines(server, readline, write):
    """Read a message a line until the input ends; write each reply a line."""
    while True:
        line = readline()
        if not line:
            return
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            write(compact({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "not JSON"}}) + "\n")
            continue
        reply = server.handle(msg)
        if reply is not None:
            write(compact(reply) + "\n")
        while server.outbox:
            write(compact(server.outbox.pop(0)) + "\n")
