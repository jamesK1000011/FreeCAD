# SPDX-License-Identifier: LGPL-2.1-or-later
# What working through Agent Link costs the agent, measured on this side: the
# size of every call's arguments and answer, the pictures sent, and the tool
# list each session loads up front. Agent Link cannot see what Claude counts,
# so these are estimates — text at about 3.5 characters a token (JSON with
# numbers runs denser than prose), a picture at width × height / 750 tokens,
# as Anthropic's vision guide gives it — good for seeing where the cost goes
# and whether a change made it smaller, not for billing.
#
# Kept a line per call in ~/AgentLink/usage/<day>.jsonl, by whichever FreeCAD
# ran the call (the window, or the hidden one when the window is not there),
# so nothing is counted twice.

import json
import os
import struct
import threading
import time

CHARS_PER_TOKEN = 3.5
DAYS_KEPT = 60


def est_tokens(chars):
    return int(round(chars / CHARS_PER_TOKEN))


def png_tokens(data):
    """Tokens a PNG costs as an image block: width × height / 750, after the
    long side is held to 1568 px as Claude resizes it."""
    try:
        if data[:8] != b"\x89PNG\r\n\x1a\n":
            return 0
        w, h = struct.unpack(">II", data[16:24])
    except Exception:
        return 0
    long_side = max(w, h)
    if long_side > 1568:
        f = 1568 / long_side
        w, h = int(w * f), int(h * f)
    return int(round(w * h / 750))


def folder():
    return os.path.join(os.path.expanduser("~"), "AgentLink", "usage")


class Meter:
    """Counts a server's calls and lists, this session and on disk."""

    def __init__(self, where=None, session_id=None, write=True):
        self.where = where or folder()
        self.session_id = session_id or time.strftime("%Y%m%d-%H%M%S") + f"-{os.getpid()}"
        self.write = write
        self.lock = threading.Lock()
        self.calls = []
        self.listed = None

    def _append(self, row):
        if not self.write:
            return
        try:
            os.makedirs(self.where, exist_ok=True)
            p = os.path.join(self.where, time.strftime("%Y-%m-%d") + ".jsonl")
            with open(p, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, separators=(",", ":")) + "\n")
        except OSError:
            pass

    def list(self, tools, instructions=""):
        """tools/list answered: the up-front cost of every listed definition."""
        chars = len(json.dumps(tools, separators=(",", ":"), ensure_ascii=False)) + len(instructions or "")
        self.listed = {"tools": len(tools), "chars": chars, "tokens": est_tokens(chars)}
        self._append({"t": round(time.time(), 1), "s": self.session_id, "kind": "list", **self.listed})

    def call(self, tool, args_text, reply_text, image=None, error=None, ms=0):
        img = png_tokens(image) if image else 0
        row = {"t": round(time.time(), 1), "s": self.session_id, "kind": "call", "tool": tool, "in": len(args_text), "out": len(reply_text),
               "img": img, "ms": int(ms), **({"err": error} if error else {})}
        with self.lock:
            self.calls.append(row)
            del self.calls[:-5000]
        self._append(row)

    def session_totals(self):
        with self.lock:
            rows = list(self.calls)
        return totals(rows, self.listed)


def tokens_of(row):
    return est_tokens(row.get("in", 0)) + est_tokens(row.get("out", 0)) + row.get("img", 0)


def totals(rows, listed=None):
    calls = [r for r in rows if r.get("kind", "call") == "call"]
    by = {}
    for r in calls:
        b = by.setdefault(r["tool"], {"tool": r["tool"], "calls": 0, "tokens": 0, "imageTokens": 0, "errors": 0, "ms": 0})
        b["calls"] += 1
        b["tokens"] += tokens_of(r)
        b["imageTokens"] += r.get("img", 0)
        b["errors"] += 1 if r.get("err") else 0
        b["ms"] += r.get("ms", 0)
    out = {"calls": len(calls), "tokens": sum(b["tokens"] for b in by.values()), "imageTokens": sum(b["imageTokens"] for b in by.values()),
           "errors": sum(b["errors"] for b in by.values()), "byTool": sorted(by.values(), key=lambda b: -b["tokens"])}
    if listed:
        out["toolList"] = listed
    return out


def read_days(days=7, where=None):
    where = where or folder()
    rows = []
    now = time.time()
    if not os.path.isdir(where):
        return rows
    for n in sorted(os.listdir(where)):
        if not n.endswith(".jsonl"):
            continue
        p = os.path.join(where, n)
        try:
            day = time.mktime(time.strptime(n[:-6], "%Y-%m-%d"))
        except ValueError:
            continue
        if now - day > DAYS_KEPT * 86400:
            try:
                os.remove(p)
            except OSError:
                pass
            continue
        if now - day > days * 86400 + 86400:
            continue
        with open(p, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if now - r.get("t", 0) <= days * 86400:
                    rows.append(r)
    return rows


def report(days=7, where=None, top=12):
    """Calls and estimated tokens over the last days: per day, per session
    (with its tool list's cost), and the tools that cost the most."""
    rows = read_days(days, where)
    calls = [r for r in rows if r.get("kind") == "call"]
    lists = [r for r in rows if r.get("kind") == "list"]
    sessions = {}
    for r in lists:
        sessions.setdefault(r["s"], {"session": r["s"], "listed": r["tools"], "listTokens": r["tokens"], "calls": 0, "tokens": 0})
    for r in calls:
        s = sessions.setdefault(r["s"], {"session": r["s"], "listed": None, "listTokens": 0, "calls": 0, "tokens": 0})
        s["calls"] += 1
        s["tokens"] += tokens_of(r)
    per_day = {}
    for r in calls:
        d = time.strftime("%Y-%m-%d", time.localtime(r["t"]))
        x = per_day.setdefault(d, {"day": d, "calls": 0, "tokens": 0})
        x["calls"] += 1
        x["tokens"] += tokens_of(r)
    t = totals(calls)
    list_tok = sum(s["listTokens"] for s in sessions.values())
    out = {"days": days, "calls": t["calls"], "tokens": t["tokens"] + list_tok, "callTokens": t["tokens"], "toolListTokens": list_tok,
           "imageTokens": t["imageTokens"], "errors": t["errors"], "perDay": sorted(per_day.values(), key=lambda x: x["day"]),
           "sessions": len(sessions), "topTools": t["byTool"][:top],
           "note": "Estimates from what passed through Agent Link (text at ~3.5 characters a token, pictures at width × height / 750): where the cost goes, not Claude's own count. The tool list is paid at each session's start; your plan's usage page has the real totals."}
    tips = []
    if out["tokens"] and list_tok > 0.3 * out["tokens"]:
        tips.append("The tool list is over a third of it: fewer groups listed (tool_groups disable) cuts every session's start.")
    if t["tokens"] and t["imageTokens"] > 0.4 * t["tokens"]:
        tips.append("Pictures are over 40 %: smaller ones (view_snapshot width) or inline: false where a file will do.")
    big = [b for b in t["byTool"] if b["calls"] and b["tokens"] / b["calls"] > 4000]
    if big:
        tips.append("Large answers: " + ", ".join(f"{b['tool']} (~{b['tokens'] // b['calls']} a call)" for b in big[:4]) + " — ask for less (detail: false, a filter, a range).")
    if tips:
        out["tips"] = tips
    return out
