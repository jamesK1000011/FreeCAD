# SPDX-License-Identifier: LGPL-2.1-or-later
# Unattended use (the "James on a beach" test): what lets work go on, or stop
# safely and say why, with no one at the Mac. Recovering documents after a
# crash or a hang, answering harmless dialogs, the settings' change log and
# rollback, macros the person approved, the window's pinned document, the
# heavy-job queue and the Addon Manager. The rules and the files behind them
# are in agentlink/keep.py; the watchdog is in agentlink/supervise.py.
#
# The person's calls stay the person's: saving over their file, running a
# macro the first time, installing code — each needs their own words in
# chat (a typed confirm), never the agent's.

import json
import os
import shutil
import tempfile
import time

import FreeCAD

from .. import keep
from ..protocol import Tool, ToolError
from ..tools import obj

GROUP = "unattended"
INFO = "Unattended use: recover documents after a crash or hang (app_recover), answer harmless dialogs, settings history and rollback, approved macros, the pinned document, the heavy-job queue, the Addon Manager."

RECOVERED_FOR = "AgentLinkRecoveredFor"


def bad(msg):
    return ToolError("invalid_input", msg)


def _hm(t):
    return time.strftime("%H:%M", time.localtime(t))


def _cache_dirs():
    out = []
    for fn in ("getUserCachePath", "getUserCacheDir"):
        try:
            p = getattr(FreeCAD, fn)()
            if p and p not in out:
                out.append(p)
        except Exception:
            pass
    return out


def open_copy(path, name, label, t):
    """A saved copy opened beside the person's file, never over it: its own
    name and the label "<label> (recovered HH:MM)"."""
    tmpd = tempfile.mkdtemp(prefix="agentlink-recovered-")
    tmp = os.path.join(tmpd, f"{keep.safe(name)}_recovered.FCStd")
    shutil.copy2(path, tmp)
    nd = FreeCAD.openDocument(tmp)
    try:
        nd.recompute()
    except Exception:
        pass
    nd.Label = f"{label} (recovered {_hm(t)})"
    return nd


def recover_docs(documents, since=None):
    rec, failed = [], []
    for d in documents or []:
        name, label, fname = d.get("name"), d.get("label") or d.get("name"), d.get("file") or ""
        if not name:
            continue
        fc = [(p, t) for p, t, _ in keep.freecad_recovery_files(_cache_dirs(), label)]
        srcs = keep.recovery_sources(name, fc)
        if not srcs:
            failed.append({"label": label, "why": "no saved copy (no checkpoint, auto-save or FreeCAD recovery file)"})
            continue
        errors = []
        for src in srcs:
            try:
                nd = open_copy(src["path"], name, label, src["t"])
            except Exception as e:
                errors.append(f"{src['source']} {_hm(src['t'])}: {e}")
                continue
            m = dict(nd.Meta or {})
            m[RECOVERED_FOR] = json.dumps({"file": fname, "label": label, "source": src["source"], "t": src["t"]})
            nd.Meta = m
            lost = None
            if since:
                mins = max(0, int(round((float(since) - src["t"]) / 60.0)))
                lost = "nothing after it lost" if mins == 0 else f"about {mins} min of changes lost"
            rec.append({"label": label, "document": nd.Name, "as": nd.Label, "source": src["source"], "time": time.strftime("%Y-%m-%d %H:%M", time.localtime(src["t"])),
                        **({"lost": lost} if lost else {}), **({"skipped": errors} if errors else {}), "file": fname or None})
            break
        else:
            failed.append({"label": label, "why": "; ".join(errors)[:400]})
    return rec, failed


def tools(s):
    def app_recover(a):
        action = a.get("action", "list")
        if action == "list":
            names = {}
            for d in FreeCAD.listDocuments().values():
                names[d.Name] = {"label": d.Label, "file": d.FileName or ""}
            for sub in ("autosave", "checkpoints"):
                try:
                    for n in os.listdir(keep.path(sub)):
                        names.setdefault(n, {"label": n, "file": ""})
                except OSError:
                    pass
            out = []
            for n, info in sorted(names.items()):
                fc = [(p, t) for p, t, _ in keep.freecad_recovery_files(_cache_dirs(), info["label"])]
                srcs = keep.recovery_sources(n, fc)[:6]
                if srcs:
                    out.append({"document": n, "label": info["label"], "copies": [{"source": x["source"], "time": time.strftime("%Y-%m-%d %H:%M", time.localtime(x["t"])), "path": x["path"]} for x in srcs]})
            return {"documents": out, "note": "action auto { documents } opens the newest good copy of each beside its file; accept { document, confirm } saves one over its file."}
        if action == "auto":
            docs = a.get("documents")
            if not isinstance(docs, list) or not docs:
                raise bad("documents: [{ name, label, file }] — the documents to bring back (app_recover lists what has copies).")
            rec, failed = recover_docs(docs, a.get("since"))
            if rec:
                nd = FreeCAD.getDocument(rec[0]["document"])
                s.switch(nd)
            return {"recovered": rec, **({"failed": failed} if failed else {}),
                    "note": "Opened beside the person's files as \"(recovered …)\"; their files are untouched. accept { document, confirm: \"save over <file name>\" } typed by them saves one over its file."}
        if action == "accept":
            ref = a.get("document")
            nd = next((d for d in FreeCAD.listDocuments().values() if ref in (d.Name, d.Label)), None)
            if nd is None:
                raise ToolError("not_found", f"document {ref!r}: not open — app_recover (action list) shows the copies.")
            raw = (nd.Meta or {}).get(RECOVERED_FOR)
            if not raw:
                raise ToolError("invalid_state", f"{nd.Label} is not a recovered copy.")
            info = json.loads(raw)
            target = info.get("file")
            if not target:
                raise ToolError("invalid_state", f"{nd.Label} was never saved to a file: doc_save {{ path }} saves it where the person says.")
            want = f"save over {os.path.basename(target)}"
            if (a.get("confirm") or "").strip() != want:
                raise ToolError("not_allowed", f"confirm: \"{want}\", word for word, typed by the person — this replaces their file with the recovered copy. Nothing was changed.")
            other = next((d for d in FreeCAD.listDocuments().values() if d is not nd and d.FileName and os.path.abspath(d.FileName) == os.path.abspath(target)), None)
            if other is not None:
                raise ToolError("invalid_state", f"{target} is open as {other.Label}: close it first (doc_close — its unsaved changes are the person's call), then accept again.")
            if os.path.isfile(target):
                backup = keep.path("replaced", f"{keep.stamp()}-{os.path.basename(target)}")
                os.makedirs(os.path.dirname(backup), exist_ok=True)
                shutil.copy2(target, backup)
            else:
                backup = None
            m = dict(nd.Meta or {})
            m.pop(RECOVERED_FOR, None)
            nd.Meta = m
            nd.saveAs(target)
            nd.Label = info.get("label") or nd.Label
            s.switch(nd)
            return {"saved": target, "document": nd.Name, **({"previousFile": backup} if backup else {}), "note": "The recovered copy is now the file; what was there before is kept at previousFile."}
        raise bad("action: list | auto { documents, since } | accept { document, confirm }.")

    def dialog_answer(a):
        from .. import gui_link

        d = gui_link.modal_info() if FreeCAD.GuiUp else None
        if d is None:
            return {"dialog": None, "note": "No dialog is open in the FreeCAD window."}
        info = {k: v for k, v in d.items() if k != "widget"}
        btn = a.get("button")
        if not btn:
            return {"dialog": info}
        if not keep.harmless(d):
            raise ToolError("not_allowed", f"\"{d['title']}\" is not a plain information box ({', '.join(d['buttons'])}): which button is the person's call — ask them in chat, quoting it.")
        if btn.lower() not in [b.lower() for b in d["buttons"]]:
            raise bad(f"button: one of {', '.join(d['buttons'])}.")
        if not gui_link.press(d, btn):
            raise ToolError("failed", f"Could not press {btn}.")
        keep.log_watchdog({"event": "dialog", "title": d["title"], "pressed": btn, "by": "dialog_answer"})
        return {"pressed": btn, "dialog": info}

    st = {"type": "string"}
    return [
        Tool("app_recover", "Documents back after a crash or a hang: action list (each document's saved copies: auto-saves, checkpoints, FreeCAD's recovery files, newest first) | auto { documents [{ name, label, file }], since } (the newest copy that opens, beside the file as \"<label> (recovered HH:MM)\") | accept { document, confirm: \"save over <file name>\" typed by the person } (the recovered copy saved over their file, the old one kept).",
             obj({"action": {"type": "string", "enum": ["list", "auto", "accept"]}, "documents": {"type": "array", "items": {"type": "object"}}, "since": {"type": "number"}, "document": st, "confirm": st}), app_recover),
        Tool("dialog_answer", "The modal dialog in the FreeCAD window, if any (title, text, buttons); button presses one — only on a plain information box (OK). Any other dialog is the person's: quote it to them in chat.",
             obj({"button": st}), dialog_answer),
    ]
