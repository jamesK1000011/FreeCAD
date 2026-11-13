# SPDX-License-Identifier: LGPL-2.1-or-later
# The Model Context Protocol, as much as a tool server needs: JSON-RPC 2.0,
# one message a line (the stdio framing), initialize, tools/list,
# tools/call and ping. No dependencies, so it runs in FreeCAD's own Python
# (FreeCADCmd, the GUI) as well as a plain interpreter — nothing to install
# into an application bundle.

import json
import math
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


SYNONYMS = {
    "centre": ("center",), "center": ("centre",), "colour": ("color",), "color": ("colour",),
    "body": ("object", "part"), "object": ("body",), "part": ("body", "object"),
    "bodies": ("objects", "parts"), "objects": ("bodies",), "parts": ("bodies", "objects"),
    "file": ("path",), "filename": ("path", "file"), "path": ("file",),
}


def check_input(schema, args):
    """Unknown keys and missing required ones refused by name, as the
    answers name them: what the agent got wrong and what is allowed."""
    if not isinstance(args, dict):
        raise ToolError("invalid_input", "arguments: an object.")
    props = schema.get("properties", {})
    # The same thing by another common name (centre for center, body for
    # object …): taken as the field the tool has, when that one is not given.
    for k in [k for k in args if k not in props]:
        alt = next((s for s in SYNONYMS.get(k, ()) if s in props and s not in args), None)
        if alt is not None:
            args[alt] = args.pop(k)
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
        if ok and isinstance(v, float) and not math.isfinite(v):
            ok = False
        if not ok and isinstance(v, str):
            text = v.strip()
            try:
                if t == "number":
                    v, ok = float(text), True
                    if not math.isfinite(v):
                        ok = False
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


MAX_REPLY = 60_000  # characters: a reply past this costs the agent's context more than it tells


def trim(out, limit):
    """A reply cut to fit: its longest lists shortened (the count kept in
    truncated), longest first, until the whole fits."""
    cut = {}

    def lists(o, path):
        if isinstance(o, dict):
            for k, v in o.items():
                yield from lists(v, path + [k])
        elif isinstance(o, list):
            yield path, o
            for i, v in enumerate(o[:50]):
                yield from lists(v, path + [i])

    for _ in range(40):
        if len(compact(out)) <= limit:
            break
        # a batch's results are each a call's answer: shortened inside, never dropped
        found = [(len(compact(l)), p, l) for p, l in lists(out, []) if len(l) > 4 and p[-1:] != ["results"]]
        if not found:
            break
        size, path, l = max(found, key=lambda x: x[0])
        keep = max(4, len(l) // 2)
        parent = out
        for k in path[:-1]:
            parent = parent[k]
        name = ".".join(map(str, path))
        cut.setdefault(name, len(l))
        parent[path[-1]] = l[:keep]
    if cut:
        out["truncated"] = {k: f"{v} items, the first shown — ask for fewer (a filter, a range) to see the rest" for k, v in cut.items()}
    return out


def _finite(o):
    """NaN and infinities as null: JSON has no such numbers, and a client's
    parser (Node's JSON.parse) refuses the whole reply when Python writes
    them."""
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {k: _finite(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_finite(v) for v in o]
    return o


def compact(obj):
    try:
        return json.dumps(obj, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except ValueError:
        return json.dumps(_finite(obj), separators=(",", ":"), ensure_ascii=False, allow_nan=False)


class Server:
    def __init__(self, name, version, tools, instructions="", run=None, groups=None, resources=None, guard=None):
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
        # A check on a call's arguments before it runs (where files may go).
        self.guard = guard
        # Notifications to send after the reply in hand (list_changed).
        self.outbox = []
        # What the calls cost the agent (usage.Meter), when one is kept.
        self.meter = None
        # Told of each call that went through: on_call(tool, args).
        self.on_call = None
        # Heavy jobs' estimates and memory ceiling (agentlink/heavy.py).
        self.gate = None

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
                if self.meter is not None:
                    self.meter.list(result["tools"], self.instructions)
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
        t0 = time.time()
        result = self._call(params)
        if self.meter is not None:
            try:
                content = result.get("content") or []
                text = "".join(c.get("text", "") for c in content if c.get("type") == "text")
                img = next((c for c in content if c.get("type") == "image"), None)
                import base64

                data = base64.b64decode(img["data"][:64]) if img else None
                err = None
                if result.get("isError"):
                    err = (json.loads(text).get("error") or {}).get("code", "error")
                self.meter.call(str(params.get("name")), compact(params.get("arguments") or {}), text, data, err, (time.time() - t0) * 1000)
            except Exception:
                pass
        return result

    def _call(self, params):
        name = params.get("name")
        args = params.get("arguments") or {}
        tool = self.tools.get(name)
        if tool is None:
            import difflib

            near = difflib.get_close_matches(str(name), list(self.tools), n=5, cutoff=0.5)
            prefix = str(name).split("_")[0] + "_"
            same = [n for n in self.tools if n.startswith(prefix)][:12] if not near else []
            if len(self.tools) <= 30:
                hint = f"the tools: {', '.join(self.tools)}"
            else:
                hint = f"near: {', '.join(near)}" if near else (f"its family: {', '.join(same)}" if same else "tool_groups lists them all")
            return self.refusal("not_found", f"{name}: no such tool — {hint}.")
        t0 = time.time()
        try:
            check_input(tool.schema, args)
            if self.guard is not None:
                self.guard(tool, args)
            gate = self.gate
            if gate is not None:
                gate.before(tool, args)
            try:
                out = self.run(tool, lambda: tool.fn(args))
            except BaseException:
                if gate is not None:
                    gate.after(tool, args, False)
                raise
            if gate is not None:
                gate.after(tool, args, True)
            if self.on_call is not None:
                try:
                    self.on_call(tool, args)
                except Exception:
                    pass
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
        text = compact(out)
        if len(text) > MAX_REPLY and isinstance(out, dict):
            out = trim(out, MAX_REPLY)
            text = compact(out)
        content = [{"type": "text", "text": text}]
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
