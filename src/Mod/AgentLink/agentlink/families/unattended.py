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

    def doc_switch(a):
        ref = a.get("document")
        if not ref:
            return {"document": s.doc.Name if s.doc else None, "label": s.doc.Label if s.doc else None,
                    "open": [{"name": d.Name, "label": d.Label, "file": d.FileName or None} for d in FreeCAD.listDocuments().values()]}
        want = os.path.abspath(os.path.expanduser(ref)) if (os.sep in ref or ref.lower().endswith(".fcstd")) else None
        nd = next((d for d in FreeCAD.listDocuments().values() if ref in (d.Name, d.Label) or (want and d.FileName and os.path.abspath(d.FileName) == want)), None)
        if nd is None:
            raise ToolError("not_found", f"{ref}: not open — " + (", ".join(d.Label for d in FreeCAD.listDocuments().values()) or "no documents are open") + ". doc_open opens a file.")
        if s.doc is not None and nd.Name == s.doc.Name:
            return {"document": nd.Name, "label": nd.Label, "note": "Already on it."}
        s.switch(nd)
        try:
            s.pinned_label = nd.Label
        except Exception:
            pass
        front = False
        if a.get("front") and getattr(FreeCAD, "GuiUp", False):
            import FreeCADGui

            FreeCAD.setActiveDocument(nd.Name)
            FreeCADGui.setActiveDocument(nd.Name)
            front = True
        return {"document": nd.Name, "label": nd.Label, "front": front, "bodies": [o.Label for o in s.bodies()],
                "note": "The agent works here from now on, whatever is in front of the window." + ("" if front else " front: true also brings it to the front.")}

    # ---- macros (J) ----------------------------------------------------------------------

    def _macro(name):
        from .. import macros

        if not keep.macro_name_ok(name):
            raise bad("name: letters, digits, spaces, - and _ (60 at most), no folders.")
        return macros.macro_path(name)

    def macro_save(a):
        from .. import macros
        from ..server import VERSION

        name = a.get("name")
        p = _macro(name)
        if os.path.exists(p) and not a.get("overwrite"):
            raise ToolError("invalid_state", f"A macro named {name} is there already ({p}): another name, or overwrite: true (it then needs approving again).")
        calls = list(s.calls)
        lo, hi = a.get("from"), a.get("to")

        def tx_of(ref, end):
            """A from / to bound as an undo-step count: a step number (the
            calls up to that step) or a checkpoint of this session."""
            if ref is None:
                return None
            if isinstance(ref, int) or (isinstance(ref, str) and ref.isdigit()):
                n = int(ref)
                return max([c["tx"] for c in calls if c["step"] <= n] or [0])
            cp = s.find_checkpoint(ref)
            if not cp:
                raise ToolError("not_found", f"{ref!r}: no such checkpoint or step.")
            if cp.get("session") != id(s):
                raise ToolError("invalid_state", f"{cp['id']} is from an earlier session: its steps are not here to save — from: a step of this one.")
            return int(cp.get("tx", 0))

        t0, t1 = tx_of(lo, False), tx_of(hi, True)
        picked = [c for c in calls if (t0 is None or c["tx"] > t0) and (t1 is None or c["tx"] <= t1)]
        # A step undone and not redone is not part of the work.
        picked = [c for c in picked if c["tx"] <= s.tx]
        if not picked:
            raise ToolError("invalid_state", "No changing calls in that range to save: make the part first, or widen from / to.")
        params = a.get("params") or {}
        if not isinstance(params, dict) or any(not isinstance(v, (int, float, str)) or isinstance(v, bool) for v in params.values()):
            raise bad("params: { name: number or text } — the key sizes, e.g. { \"wall\": 2.0, \"length\": 60 }; each place a value appears in the calls takes its name.")
        made = [o.Label for o in s.bodies()]
        header = [
            f"{name}: made by Agent Link {VERSION} from {len(picked)} calls of a session on {time.strftime('%Y-%m-%d %H:%M')}",
            f"Makes: {', '.join(made) or 'the changes below'} in {s.doc.Label if s.doc else 'the document'}",
            "Runs through agentlink.run(tool, arguments): the same refusals and undo steps as live calls.",
            "Run by Agent Link only once approved (macro_approve, the person's typed OK); a change to this file needs approving again.",
        ]
        text = keep.macro_text(name, picked, params, header)
        compile(text, p, "exec")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        state = keep.approval_state(name, p)
        consts, _ = keep.macro_calls(text)
        return {"macro": name, "path": p, "calls": len(picked), "constants": consts, "approved": state == "approved",
                "note": "Saved in FreeCAD's macro folder (Macro › Macros… runs it by hand too). Agent Link runs it only after the person approves it: macro_approve { name, confirm: \"approve " + name + "\" } typed by them."}

    def macro_list(a):
        from .. import macros

        d = macros.macro_dir()
        rows = []
        try:
            names = sorted(f for f in os.listdir(d) if f.lower().endswith(".fcmacro"))
        except OSError:
            names = []
        for f in names:
            name = f[: -len(".FCMacro")]
            p = os.path.join(d, f)
            first = ""
            try:
                with open(p, encoding="utf-8", errors="replace") as fh:
                    for line in fh:
                        if line.startswith("# ") and "coding" not in line:
                            first = line[2:].strip()
                            break
            except OSError:
                pass
            rows.append({"name": name, "state": keep.approval_state(name, p), **({"about": first[:160]} if first else {})})
        return {"folder": d, "macros": rows}

    def macro_show(a):
        p = _macro(a.get("name"))
        if not os.path.isfile(p):
            raise ToolError("not_found", f"No macro {a.get('name')} in {os.path.dirname(p)}: macro_list lists them.")
        with open(p, encoding="utf-8", errors="replace") as f:
            text = f.read()
        try:
            consts, calls = keep.macro_calls(text)
        except SyntaxError as e:
            consts, calls = {}, []
            text = f"(does not parse: {e})\n" + text
        return {"name": a.get("name"), "state": keep.approval_state(a.get("name"), p), "constants": consts, "calls": len(calls),
                "text": text if len(text) <= 6000 else text[:6000] + f"\n… ({len(text)} characters in all)"}

    def macro_approve(a):
        name = a.get("name")
        p = _macro(name)
        if not os.path.isfile(p):
            raise ToolError("not_found", f"No macro {name}: macro_list lists them.")
        want = f"approve {name}"
        if (a.get("confirm") or "").strip() != want:
            raise ToolError("not_allowed", f"confirm: \"{want}\", word for word, typed by the person — a macro is Python that runs with the Mac's full reach, so approving it is theirs. Show them macro_show first. Nothing was changed.")
        e = keep.approve(name, p)
        return {"approved": name, "sha256": e["sha256"], "note": "macro_run runs it from now on; a change to its file needs approving again."}

    def macro_unapprove(a):
        name = a.get("name")
        _macro(name)
        gone = keep.unapprove(name)
        return {"unapproved": name, "was": bool(gone)}

    def macro_run(a):
        from .. import macros

        name = a.get("name")
        p = _macro(name)
        if not os.path.isfile(p):
            raise ToolError("not_found", f"No macro {name}: macro_list lists them.")
        state = keep.approval_state(name, p)
        if state != "approved":
            why = "its file changed since it was approved" if state == "changed" else "it has not been approved"
            raise ToolError("not_allowed", f"{name} is not run: {why}. The person approves it: macro_approve {{ name: \"{name}\", confirm: \"approve {name}\" }} typed by them (macro_show shows it first).")
        with open(p, encoding="utf-8") as f:
            text = f.read()
        params = a.get("params") or {}
        if not isinstance(params, dict):
            raise bad("params: { CONSTANT: value } — the macro's named sizes (macro_show lists them).")
        tree, missing = macros.with_params(text, params)
        if missing:
            consts, _ = keep.macro_calls(text)
            raise bad(f"params: {', '.join(missing)} — not among the macro's named sizes ({', '.join(consts) or 'none'}).")
        server = getattr(s, "server", None)
        if server is None:
            raise ToolError("invalid_state", "No server to run the macro through.")
        cp, _ = s.checkpoint(f"before macro {name}", auto=True)
        n0 = len(s.calls)
        macros._current.append(server)
        try:
            exec(compile(tree, p, "exec"), {"__name__": "__agentlink_macro__", "__file__": p})
        except Exception as e:
            ran = len(s.calls) - n0
            try:
                back = s.restore(cp["id"])
                how = back.get("how")
            except Exception as e2:
                how = f"not rolled back ({e2})"
            raise ToolError("failed", f"{name} stopped after {ran} call{'s' if ran != 1 else ''}: {e}. Rolled back whole ({how}) to how it was before.")
        finally:
            macros._current.pop()
        return {"ran": name, "calls": len(s.calls) - n0, "checkpoint": cp["id"], **({"params": params} if params else {}),
                "bodies": [o.Label for o in s.bodies()], "note": f"history_restore {{ checkpoint: {cp['id']} }} goes back to before it."}

    st = {"type": "string"}
    return [
        Tool("macro_save", "Save this session's changing calls as a FreeCAD macro (.FCMacro in the person's macro folder): name, from / to (a checkpoint or step; the whole session by default), params { wall: 2.0, length: 60 } — the key sizes, named at the top of the macro and put in place of each value in the calls. It runs (macro_run) only once the person approves it.",
             obj({"name": st, "from": {"type": ["string", "integer"]}, "to": {"type": ["string", "integer"]}, "params": {"type": "object"}, "overwrite": {"type": "boolean"}}, ["name"]), macro_save),
        Tool("macro_list", "The macros in FreeCAD's macro folder, each approved, changed (its code moved since the OK) or not approved. Changes nothing.", obj({}), macro_list),
        Tool("macro_show", "One macro's text, its named sizes and how many calls it makes, and whether it is approved. Changes nothing.", obj({"name": st}, ["name"]), macro_show),
        Tool("macro_approve", "The person approves a macro to be run by Agent Link: confirm: \"approve <name>\" typed by them, never by the agent — a macro is Python with the Mac's full reach. Approval holds until its file changes.",
             obj({"name": st, "confirm": st}, ["name", "confirm"]), macro_approve),
        Tool("macro_unapprove", "Take a macro off the approved list.", obj({"name": st}, ["name"]), macro_unapprove),
        Tool("macro_run", "Run an approved macro: params { CONSTANT: value } changes its named sizes for this run. One checkpoint before; a call refused part way rolls the whole run back. An unapproved or changed macro is refused.",
             obj({"name": st, "params": {"type": "object"}}, ["name"]), macro_run),
        Tool("doc_switch", "Which open document the agent works in: document (name, label or file) switches to it — the session stays on it whatever the person brings to the front of the window; front: true also brings it to the front. No document: the agent's one and those open.",
             obj({"document": st, "front": {"type": "boolean"}}), doc_switch),
        Tool("app_recover", "Documents back after a crash or a hang: action list (each document's saved copies: auto-saves, checkpoints, FreeCAD's recovery files, newest first) | auto { documents [{ name, label, file }], since } (the newest copy that opens, beside the file as \"<label> (recovered HH:MM)\") | accept { document, confirm: \"save over <file name>\" typed by the person } (the recovered copy saved over their file, the old one kept).",
             obj({"action": {"type": "string", "enum": ["list", "auto", "accept"]}, "documents": {"type": "array", "items": {"type": "object"}}, "since": {"type": "number"}, "document": st, "confirm": st}), app_recover),
        Tool("dialog_answer", "The modal dialog in the FreeCAD window, if any (title, text, buttons); button presses one — only on a plain information box (OK). Any other dialog is the person's: quote it to them in chat.",
             obj({"button": st}), dialog_answer),
    ]
