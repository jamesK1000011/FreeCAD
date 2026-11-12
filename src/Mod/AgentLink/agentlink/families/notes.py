# SPDX-License-Identifier: LGPL-2.1-or-later
# Notes pinned to the parts, between the person and the agent, and the build
# played back step by step. Both after Stefan Hermann's ScanRuler (CNC
# Kitchen): its Reverse engineer panel pins notes to the part — the person
# writes and ticks them off, the agent answers them and pins questions of
# its own — and plays the feature list from the top with a caption a step,
# so a recording shows how the part was built. Here the same ideas over a
# FreeCAD document; the window's panel (agentlink/panel.py) shows them.
#
# A note is no step of the model and no step of the undo history: it lives
# in the document's Meta (store.py), saved with the .FCStd.

import datetime
import os

from .. import facepick, render, store
from ..protocol import Tool, ToolError
from ..tools import obj

GROUP = "notes"
INFO = "Notes pinned to the parts between the person and the agent; the build played back step by step, a picture a step (after Stefan Hermann's ScanRuler)."

KEY_PARAMS = ("Length", "Depth", "Angle", "Radius", "Size", "Occurrences", "Offset", "Value")


# ---- notes -------------------------------------------------------------------------------

def notes_of(doc):
    return store.read(doc, "notes", {"next": 1, "notes": []})


def save_notes(doc, data):
    store.write(doc, "notes", data)


def note_view(n):
    out = {"id": n["id"], "by": n["by"], "text": n["text"], "at": n["at"], "done": bool(n.get("done"))}
    for k in ("body", "face", "answer", "made"):
        if n.get(k):
            out[k] = n[k]
    return out


def add_note(doc, at, text, by, body=None, face=None):
    data = notes_of(doc)
    n = {"id": data["next"], "by": by, "text": str(text), "at": [round(float(x), 4) for x in at], "done": False,
         "made": datetime.datetime.now().isoformat(timespec="seconds")}
    if body:
        n["body"] = body
    if face:
        n["face"] = face
    data["notes"].append(n)
    data["next"] += 1
    save_notes(doc, data)
    return n


def find_note(data, nid):
    for n in data["notes"]:
        if n["id"] == int(nid):
            return n
    raise ToolError("not_found", f"No note {nid}: note_list shows them.")


# ---- steps -------------------------------------------------------------------------------

def steps_of(body, sketches=False):
    """The body's history in order: each feature (and, asked, each sketch),
    with the volume after it and what it changed."""
    out, prev = [], 0.0
    for o in body.Group:
        sketch = o.isDerivedFrom("Sketcher::SketchObject")
        if sketch and not sketches:
            continue
        if not sketch and not o.isDerivedFrom("PartDesign::Feature"):
            continue
        row = {"name": o.Name, "label": o.Label, "type": o.TypeId.split("::")[-1]}
        if not sketch:
            sh = o.Shape
            vol = sh.Volume if not sh.isNull() and sh.Solids else 0.0
            row["volume"] = round(vol, 2)
            row["change"] = round(vol - prev, 2)
            prev = vol
            if o.isDerivedFrom("PartDesign::Feature") and getattr(o, "isValid", lambda: True)() is False:
                row["failed"] = True
        for k in KEY_PARAMS:
            if hasattr(o, k) and k in o.PropertiesList:
                v = getattr(o, k)
                row["key"] = f"{k} {getattr(v, 'UserString', v)}"
                break
        if body.Tip == o:
            row["tip"] = True
        out.append(row)
    for i, r in enumerate(out):
        r["step"] = i + 1
    return out


def caption(step, total, row):
    """The caption a step carries: its number, what it is and what it did."""
    did = ""
    if "change" in row:
        c = row["change"]
        did = f"+{c:.0f} MM3" if c > 0.5 else f"{c:.0f} MM3" if c < -0.5 else "NO CHANGE"
    return f"{step}/{total}  {row['type']}  {did}".strip()


def tools(session):
    s = session

    def _body_pd(ref):
        if ref is None:
            pds = [o for o in s.doc.Objects if o.TypeId == "PartDesign::Body"]
            if len(pds) == 1:
                return pds[0]
            raise ToolError("invalid_input", "body: which PartDesign body — " + (", ".join(o.Label for o in pds) or "there is none") + ".")
        for o in s.doc.Objects:
            if o.TypeId == "PartDesign::Body" and (o.Label == ref or o.Name == ref):
                return o
        raise ToolError("not_found", f"No PartDesign body '{ref}'.")

    # -- notes --

    def note_add(a):
        if not str(a.get("text", "")).strip():
            raise ToolError("invalid_input", "text: what the note says (a question to the person, or a remark).")
        body, face = None, None
        if a.get("faces") is not None:
            o = s.body(a.get("body"))
            picked = facepick.pick(o.Shape, a["faces"], "faces")
            name, f = picked[0]
            c = f.CenterOfMass
            at, body, face = [c.x, c.y, c.z], o.Label, name
        elif a.get("at") is not None:
            at = a["at"]
            if not (isinstance(at, list) and len(at) == 3):
                raise ToolError("invalid_input", "at: [x, y, z] in mm.")
            if a.get("body"):
                body = s.body(a["body"]).Label
        else:
            raise ToolError("invalid_input", "at [x, y, z], or body and faces (the note pinned at the face's centre).")
        n = add_note(s.doc, at, a["text"], "agent", body, face)
        return {"note": note_view(n), "note_count": len(notes_of(s.doc)["notes"])}

    def note_list(a):
        data = notes_of(s.doc)
        rows = [note_view(n) for n in data["notes"] if not (a.get("open") and n.get("done"))]
        waiting = [n["id"] for n in data["notes"] if n["by"] == "person" and not n.get("done") and not n.get("answer")]
        out = {"notes": rows}
        if waiting:
            out["waiting"] = waiting
            out["next"] = "The person's notes waiting for an answer: note_answer { id, text }, and note_done when it is dealt with."
        return out

    def note_answer(a):
        data = notes_of(s.doc)
        n = find_note(data, a.get("id"))
        n["answer"] = str(a.get("text", "")).strip()
        if not n["answer"]:
            raise ToolError("invalid_input", "text: the answer.")
        save_notes(s.doc, data)
        return {"note": note_view(n)}

    def note_done(a):
        data = notes_of(s.doc)
        n = find_note(data, a.get("id"))
        n["done"] = bool(a.get("done", True))
        save_notes(s.doc, data)
        return {"note": note_view(n)}

    def note_delete(a):
        data = notes_of(s.doc)
        n = find_note(data, a.get("id"))
        data["notes"].remove(n)
        save_notes(s.doc, data)
        return {"deleted": n["id"], "left": len(data["notes"])}

    # -- steps --

    def steps_list(a):
        b = _body_pd(a.get("body"))
        rows = steps_of(b, bool(a.get("sketches")))
        return {"body": b.Label, "steps": rows, "count": len(rows)}

    def steps_render(a):
        b = _body_pd(a.get("body"))
        rows = steps_of(b, False)
        if not rows:
            raise ToolError("invalid_state", f"{b.Label} has no features yet.")
        view = str(a.get("view", "iso")).lower()
        if view not in render.VIEWS:
            raise ToolError("invalid_input", "view: " + ", ".join(render.VIEWS) + ".")
        w, h = int(a.get("width", 480)), int(a.get("height", 360))
        folder = os.path.expanduser(a.get("path") or f"steps_{''.join(ch if ch.isalnum() else '_' for ch in b.Label)}")
        if not os.path.isabs(folder):
            folder = os.path.join(s.out_dir, folder)
        os.makedirs(folder, exist_ok=True)
        # Framed on the finished part, so the camera holds still and the part grows in it.
        bb = b.Shape.BoundBox if not b.Shape.isNull() else None
        frame = {"at": [bb.Center.x, bb.Center.y, bb.Center.z], "size": bb.DiagonalLength * 1.05} if bb and bb.isValid() else None
        files, sheet = [], []
        total = len(rows)
        for r in rows:
            o = s.doc.getObject(r["name"])
            sh = o.Shape
            if sh.isNull():
                continue
            pts, tris = sh.tessellate(0.05)
            import numpy as np

            layer = {"name": o.Label, "V": np.array([[p.x, p.y, p.z] for p in pts], float),
                     "F": np.array(tris, np.int64).reshape(-1, 3), "color": render.PALETTE[0], "creases": True}
            img, _, _ = render.draw([layer], view, w, h, frame=frame, bottom=26)
            render.text(img, caption(r["step"], total, r), 10, h - 22, scale=2)
            p = os.path.join(folder, f"step_{r['step']:02d}.png")
            with open(p, "wb") as f:
                f.write(render.png(img))
            files.append(p)
            sheet.append(img)
        out = {"body": b.Label, "folder": folder, "files": files, "steps": rows}
        if a.get("sheet", True) and sheet:
            import math

            import numpy as np

            cols = min(4, len(sheet))
            nrows = math.ceil(len(sheet) / cols)
            big = np.tile(render.BG, (nrows * h, cols * w, 1))
            for k, im in enumerate(sheet):
                rr, cc = divmod(k, cols)
                big[rr * h:(rr + 1) * h, cc * w:(cc + 1) * w] = im
            p = os.path.join(folder, "steps.png")
            data = render.png(big)
            with open(p, "wb") as f:
                f.write(data)
            out["sheet"] = p
            if a.get("inline", True):
                out["_png"] = data
        return out

    return [
        Tool("note_add", "Pin a note to the part for the person: a question or a remark, at [x,y,z] or on body + faces (a query as fea_hold takes). It shows as a pin in the window; the person answers it there.",
             obj({"text": {"type": "string"}, "at": {"type": "array", "items": {"type": "number"}}, "body": {"type": "string"}, "faces": {}}, ["text"]), note_add, changes=True),
        Tool("note_list", "The notes pinned to the parts — the person's and the agent's — with answers and which are done; waiting lists the person's notes with no answer yet. open: true leaves out the done ones.",
             obj({"open": {"type": "boolean"}}), note_list),
        Tool("note_answer", "Answer a note (the person's question or request): id, text.", obj({"id": {"type": "integer"}, "text": {"type": "string"}}, ["id", "text"]), note_answer, changes=True),
        Tool("note_done", "Tick a note off (done: false opens it again).", obj({"id": {"type": "integer"}, "done": {"type": "boolean"}}, ["id"]), note_done, changes=True),
        Tool("note_delete", "Take a note out.", obj({"id": {"type": "integer"}}, ["id"]), note_delete, changes=True),
        Tool("steps_list", "A PartDesign body's history in order: each feature with its key value, the volume after it and what it added or took away (sketches: true lists the sketches too).",
             obj({"body": {"type": "string"}, "sketches": {"type": "boolean"}}), steps_list),
        Tool("steps_render", "The build played back as pictures: a picture a feature, framed on the finished part so it grows in place, each captioned with its step, type and what it did; a contact sheet of them (sheet: false leaves it out). view (iso), width, height, path (a folder).",
             obj({"body": {"type": "string"}, "view": {"type": "string"}, "width": {"type": "integer"}, "height": {"type": "integer"}, "path": {"type": "string"}, "sheet": {"type": "boolean"}, "inline": {"type": "boolean"}}), steps_render),
    ]
