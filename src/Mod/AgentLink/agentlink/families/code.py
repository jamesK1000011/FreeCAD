# SPDX-License-Identifier: LGPL-2.1-or-later
# Building-code checks for planning, headless: rule packs stacked by
# jurisdiction and date, facts read from the BIM model (members, rooms,
# openings) with where each came from, the person's answers kept with the
# document, the checks run in five states, the questions that would decide
# what is still open, a wood member's span check by hand with a beam FEM
# beside it, and the handoff packet for a licensed professional. Nothing
# here approves anything: every answer says it is a planning estimate, and
# "compliant" means only within the checked limits.

import csv
import datetime
import json
import math
import os
import shutil

import FreeCAD

from .. import store
from ..code import DISCLAIMER, beamfem, engine, framing, packs, units
from ..code import ids as idsfile
from ..code.findings import LABEL, STATES, WITHIN, questions, summary
from ..protocol import Tool, ToolError
from ..tools import obj

GROUP = "code"
INFO = "Building-code checks for planning (never approval): rule packs by jurisdiction, facts from the BIM model, member spans with a beam FEM beside them, the questions still open, the handoff packet for an engineer."

KEY = "code"
SHIPPED = os.path.join(os.path.dirname(os.path.dirname(__file__)), "code", "packs_shipped")
V = FreeCAD.Vector


def bad(msg):
    return ToolError("invalid_input", msg)


def ifc_type(o):
    return str(getattr(o, "IfcType", "") or getattr(o, "IfcRole", "") or "")


def proxy(o):
    return type(getattr(o, "Proxy", None)).__name__


def fact(value, provenance, source, pm=None, unit=None, note=None):
    f = {"value": value, "provenance": provenance, "source": source}
    if pm:
        f["pm"] = pm
    if unit:
        f["unit"] = unit
    if note:
        f["note"] = note
    return f


def axes_dims(o):
    """A box-like component's run, depth and width (mm) and its run's
    direction in the world: its own frame's extents, the longest the run,
    the most upright of the other two its depth."""
    sh = o.Shape
    rot = o.Placement.Rotation
    loc = sh.copy()
    loc.transformShape(o.Placement.inverse().toMatrix(), True)
    bb = loc.BoundBox
    dims = [(bb.XLength, rot.multVec(V(1, 0, 0))), (bb.YLength, rot.multVec(V(0, 1, 0))), (bb.ZLength, rot.multVec(V(0, 0, 1)))]
    dims.sort(key=lambda t: -t[0])
    run, rest = dims[0], dims[1:]
    rest.sort(key=lambda t: -abs(t[1].z))
    return run[0], rest[0][0], rest[1][0], run[1]


def tools(session):
    s = session

    def doc():
        if s.doc is None:
            raise ToolError("invalid_state", "No document: doc_new first.")
        return s.doc

    def state():
        return store.read(doc(), KEY, None) or {"project": {}, "answers": {}}

    def save(st):
        store.write(doc(), KEY, st)

    def pack_dirs():
        return [SHIPPED, os.path.join(s.out_dir, "codepacks")]

    def all_packs():
        return packs.load_dir(*pack_dirs())

    def resolved(st=None):
        st = st or state()
        pr = st.get("project") or {}
        if not pr.get("jurisdiction"):
            raise ToolError("invalid_state", "No project yet: code_project { jurisdiction (US-WA-Pierce-DuPont, or XX for the example pack), date } first.")
        ps, errs = all_packs()
        try:
            rules, values, conflicts, used = packs.resolve(ps, pr["jurisdiction"], pr.get("date") or datetime.date.today().isoformat(), pr.get("include"))
        except packs.PackError as e:
            raise ToolError("invalid_state", f"The packs in force cannot be stacked: {e}") from None
        values = dict(values)
        for name, f in ((st.get("answers") or {}).get("project") or {}).items():
            # the person's project-wide answers (a ground snow load …) as values; a pack's value is never loosened by them
            spec = {"value": f.get("value"), "provenance": f.get("provenance", "user_supplied"), "from": "the person (code_answer project)",
                    **({"unit": f["unit"]} if f.get("unit") else {})}
            old = values.get(name)
            way = (old or {}).get("stricter")
            if old is not None and way and isinstance(spec["value"], (int, float)) and isinstance(old.get("value"), (int, float)):
                if (way == "higher" and spec["value"] < old["value"]) or (way == "lower" and spec["value"] > old["value"]):
                    conflicts = conflicts + [{"value": name, "pack": "code_answer project", "kept": old["value"], "refused": spec["value"],
                                              "why": "the person's answer is looser than the packs in force: the pack's value holds"}]
                    continue
                spec["stricter"] = way
            values[name] = spec
        return rules, values, conflicts, used, errs

    # ---- facts from the model --------------------------------------------------------

    def model_subjects():
        subs = []
        members = []
        d = doc()
        for o in d.Objects:
            t = ifc_type(o)
            sh = getattr(o, "Shape", None)
            if sh is None or sh.isNull() or not hasattr(o, "IfcType"):
                continue
            pn = proxy(o)
            if t in ("Beam", "Member", "Column", "Rafter", "Joist") or pn in ("_Structure", "_Frame") and t not in ("Slab", "Footing", "Wall"):
                if not sh.Solids:
                    continue
                run, depth, width, axis = axes_dims(o)
                f = {"b": fact(round(width, 1), "measured", "model"), "d": fact(round(depth, 1), "measured", "model"),
                     "length": fact(round(run, 1), "measured", "model"),
                     "span": fact(round(run, 1), "measured", "model", note="the member's whole length: give the clear span between supports with code_answer")}
                nom = next((k for k, (bb, dd) in framing.DRESSED.items() if abs(bb - width) < 1.5 and abs(dd - depth) < 1.5), None)
                if nom:
                    f["size"] = fact(nom, "derived", "model", note="its dressed size")
                if t == "Truss" or pn == "_Truss" or "truss" in o.Label.lower():
                    f["truss"] = fact(True, "derived", "model")
                mat = getattr(o, "Material", None)
                if mat is not None:
                    f["material"] = fact(mat.Label, "derived", "model")
                role = {"Beam": "beam", "Member": "member", "Column": "column", "Rafter": "rafter", "Joist": "joist"}.get(t, "member")
                if abs(axis.z) > 0.95:
                    role = "column"
                sub = {"id": o.Label, "kind": "member", "role": role, "ifc": t, "facts": f}
                members.append((sub, sh.BoundBox.Center, axis, depth))
                subs.append(sub)
            elif t == "Space" or pn == "_Space":
                bb = sh.BoundBox
                use = str(getattr(o, "SpaceType", "") or "").lower().replace(" ", "_") or None
                if use in ("undefined", "notdefined", "unknown"):
                    use = None  # asked, never guessed
                f = {"clear_height": fact(round(bb.ZLength, 1), "measured", "model", note="the space's tallest clear height")}
                prof = height_profile(sh)
                if prof:
                    f["height_profile"] = fact(prof, "measured", "model", note="share of the floor area with at least each clear height (mm)")
                    f["min_clear_height"] = fact(min(int(h) for h, share in prof.items() if share > 0.999) if any(v > 0.999 for v in prof.values()) else 0, "measured", "model",
                                                 note="the height every part of the floor has above it")
                ar = getattr(o, "Area", None)
                if ar is not None:
                    f["area"] = fact(round(float(getattr(ar, "Value", ar)) / 1e6, 3), "measured", "model", unit="m2")
                subs.append({"id": o.Label, "kind": "space", "use": use, "facts": f})
            elif t in ("Wall", "Slab", "Roof", "Covering") or pn in ("_Wall", "_Roof"):
                kind = {"Wall": "wall", "Slab": "slab", "Roof": "roof", "Covering": "covering"}.get(t, "wall")
                f = {}
                mat = getattr(o, "Material", None)
                if mat is not None:
                    f["material"] = fact(mat.Label, "derived", "model")
                if hasattr(o, "Width") and kind == "wall":
                    f["thickness"] = fact(round(float(getattr(o.Width, "Value", o.Width)), 1), "measured", "model", unit="mm")
                subs.append({"id": o.Label, "kind": kind, "ifc": t, "facts": f})
            elif t in ("Stair", "Stairs") or pn == "_Stairs":
                # A flight's rise and going from its own sizes; headroom and
                # handrails are the person's to say (code_answer).
                q = lambda n: float(getattr(getattr(o, n, 0), "Value", getattr(o, n, 0)) or 0)
                steps = int(getattr(o, "NumberOfSteps", 0) or 0)
                f = {}
                if steps >= 2 and q("Height"):
                    f["riser_height"] = fact(round(q("Height") / steps, 1), "derived", "model", unit="mm", note="its height over its steps")
                if steps >= 2 and q("Length"):
                    f["tread_depth"] = fact(round(q("Length") / (steps - 1), 1), "derived", "model", unit="mm", note="its run over its treads (the going)")
                if q("Width"):
                    f["width"] = fact(round(q("Width"), 1), "measured", "model", unit="mm")
                if q("Height"):
                    f["rise"] = fact(round(q("Height"), 1), "measured", "model", unit="mm")
                subs.append({"id": o.Label, "kind": "stair", "ifc": "Stair", "facts": f})
            elif t in ("Window", "Door") or pn == "_Window":
                w, h = float(getattr(o, "Width", 0) or 0), float(getattr(o, "Height", 0) or 0)
                f = {}
                if w and h:
                    clear = (w - 100) * (h - 100)
                    f["clear_area"] = fact(round(w * h), "derived", "model", pm=round(w * h - clear), unit="mm2", note="the rough opening: its frame takes some")
                    f["clear_width"] = fact(round(w), "derived", "model", pm=50, unit="mm")
                    f["clear_height"] = fact(round(h), "derived", "model", pm=50, unit="mm")
                f["sill_height"] = fact(round(sh.BoundBox.ZMin - level_z(o), 1), "derived", "model", unit="mm", note="from its storey's floor")
                subs.append({"id": o.Label, "kind": "opening", "ifc": t or "Window", "escape": None, "facts": f})
        # IFC property sets as facts named "Pset.Property"
        byid = {x["id"]: x for x in subs}
        for o in d.Objects:
            sub = byid.get(o.Label)
            if sub is None:
                continue
            for name, val in (getattr(o, "IfcProperties", {}) or {}).items():
                parts = str(val).split(";;")
                if len(parts) >= 3:
                    sub["facts"][f"{parts[0]}.{name}"] = fact(parts[2], "derived", "model", note="its IFC property")
            for k in ("StandardCode", "Classification"):
                if getattr(o, k, ""):
                    sub["facts"]["classification"] = fact(getattr(o, k), "derived", "model")
        # spacing: the nearest parallel member at about the same height
        for sub, c, ax, dep in members:
            if sub["role"] == "column":
                continue
            best = None
            for other, c2, ax2, dep2 in members:
                if other is sub or abs(ax.dot(ax2)) < 0.99 or abs(c.z - c2.z) > max(dep, dep2) / 2:
                    continue
                dv = c2 - c
                perp = dv - ax * dv.dot(ax)
                dist = math.hypot(perp.x, perp.y)
                if dist > 1 and (best is None or dist < best):
                    best = dist
            if best:
                sub["facts"]["spacing"] = fact(round(best, 1), "derived", "model", note="to the nearest parallel member")
        return subs

    def height_profile(sh):
        """Share of the floor's area with at least each clear height, every
        50 mm: slices of the room's solid, as a sloped ceiling cuts it."""
        import Part

        if not sh.Solids:
            return None
        bb = sh.BoundBox
        floor = None
        try:
            floor = sh.common(Part.makeBox(bb.XLength + 2, bb.YLength + 2, 1.0, V(bb.XMin - 1, bb.YMin - 1, bb.ZMin))).Volume / 1.0
        except Exception:
            return None
        if not floor:
            return None
        out = {}
        h = 1000
        while h <= min(bb.ZLength, 4000) + 1e-6:
            try:
                sl = sh.common(Part.makeBox(bb.XLength + 2, bb.YLength + 2, 1.0, V(bb.XMin - 1, bb.YMin - 1, bb.ZMin + h - 1.0))).Volume
            except Exception:
                sl = 0.0
            out[str(h)] = round(min(sl / floor, 1.0), 4)
            h += 50
        return out

    def level_z(o):
        for p in o.InList:
            if ifc_type(p) in ("Building Storey",) or proxy(p) in ("_Floor", "BuildingPart"):
                return float(p.Placement.Base.z)
            for q in p.InList:
                if ifc_type(q) in ("Building Storey",) or proxy(q) in ("_Floor", "BuildingPart"):
                    return float(q.Placement.Base.z)
        return 0.0

    def subjects(st=None, warnings=None):
        """The model's subjects with the person's answers on them. Answers for
        "project" are values, not a subject; answers for a label the model no
        longer has (and no kind given) make no phantom subject: they go on
        warnings (a list), if given."""
        st = st or state()
        subs = model_subjects()
        byid = {x["id"]: x for x in subs}
        for sid, given in (st.get("answers") or {}).items():
            if sid == "project":
                continue
            sub = byid.get(sid)
            if sub is None:
                if not (given.get("kind") or {}).get("value"):
                    if warnings is not None:
                        warnings.append({"subject": sid, "facts": sorted(given),
                                         "why": "answers kept for a subject the model no longer has (renamed or deleted?): code_answer clear: true forgets them, or give kind to check it as something not modelled"})
                    continue
                sub = {"id": sid, "kind": given["kind"]["value"], "facts": {}}
                subs.append(sub)
                byid[sid] = sub
            for name, f in given.items():
                if name in ("kind", "role", "use", "escape"):
                    sub[name] = f["value"]
                sub["facts"][name] = f
        return subs

    # ---- tools -------------------------------------------------------------------------

    def code_packs(a):
        action = a.get("action", "list")
        if action == "schema":
            return {"schema": packs.SCHEMA}
        if action == "import":
            p = a.get("path")
            if not p:
                raise bad("path: a rule pack (.json) to add to your pack folder.")
            p = p if os.path.isabs(os.path.expanduser(p)) else os.path.join(s.out_dir, p)
            try:
                data = packs.load(os.path.expanduser(p))
            except (OSError, packs.PackError) as e:
                raise bad(str(e))
            dst_dir = os.path.join(s.out_dir, "codepacks")
            os.makedirs(dst_dir, exist_ok=True)
            dst = os.path.join(dst_dir, f"{data['pack']['id']}.json")
            shutil.copyfile(os.path.expanduser(p), dst)
            return {"imported": data["pack"]["id"], "version": data["pack"]["version"], "layer": data["pack"]["layer"], "rules": len(data.get("rules") or []), "into": dst}
        if action == "export":
            pid = a.get("id")
            ps, _ = all_packs()
            hit = next((p for p in ps if p["pack"]["id"] == pid), None)
            if hit is None:
                raise ToolError("not_found", f"pack {pid!r}: code_packs lists them.")
            dst = a.get("path") or f"{pid}.json"
            dst = dst if os.path.isabs(os.path.expanduser(dst)) else os.path.join(s.out_dir, dst)
            with open(os.path.expanduser(dst), "w", encoding="utf-8") as f:
                json.dump({k: v for k, v in hit.items() if not k.startswith("_")}, f, indent=2)
            return {"exported": pid, "path": dst}
        if action == "ids":
            rules = resolved()[0] if not a.get("id") else {r["id"]: r for p in all_packs()[0] if p["pack"]["id"] == a["id"] for r in p.get("rules") or [] if r.get("op", "add") == "add"}
            xml, skipped = idsfile.write(rules, a.get("id") or "Agent Link project information rules")
            dst = a.get("path") or f"{a.get('id') or 'project'}.ids"
            dst = dst if os.path.isabs(os.path.expanduser(dst)) else os.path.join(s.out_dir, dst)
            with open(os.path.expanduser(dst), "w", encoding="utf-8") as f:
                f.write(xml)
            out = {"ids": dst, "specifications": xml.count("<specification "), "skipped": [{"rule": r, "fact": f, "why": w} for r, f, w in skipped][:30]}
            try:
                import ifctester  # noqa: F401

                out["validator"] = "ifctester is installed: ifctester.ids.open(path) checks an IFC against it"
            except ImportError:
                out["validator"] = "ifctester is not installed here; any IDS 1.0 checker reads the file"
            return out
        if action != "list":
            raise bad("action: list | import (path) | export (id, path) | ids (the information rules as IDS 1.0: id or the project's) | schema.")
        ps, errs = all_packs()
        return {"packs": [{"id": p["pack"]["id"], "title": p["pack"].get("title"), "layer": p["pack"]["layer"], "jurisdiction": p["pack"]["jurisdiction"],
                           "version": p["pack"]["version"], "effective": p["pack"]["effective"], "rules": len(p.get("rules") or []),
                           "values": len(p.get("values") or {}), "file": os.path.basename(p["_file"]), "hash": p["_hash"]} for p in ps],
                "folders": pack_dirs(), **({"refused": errs} if errs else {}),
                "note": "Packs carry paraphrased rules with citations; drop more into the second folder or code_packs import. The shipped example (jurisdiction XX) is made-up numbers to try the engine."}

    def code_project(a):
        st = state()
        pr = dict(st.get("project") or {})
        for k in ("jurisdiction", "date", "include", "occupancy", "name"):
            if a.get(k) is not None:
                pr[k] = a[k]
        if pr.get("date"):
            try:
                datetime.date.fromisoformat(pr["date"])
            except ValueError:
                raise bad("date: the permit or design date, YYYY-MM-DD.") from None
        st["project"] = pr
        rules, values, conflicts, used, errs = resolved(st)
        save(st)
        s._agentlink_code = None
        s.snapshot("code_project")
        out = {"project": pr, "packs": used, "rules": len(rules), "values": {k: v.get("value") for k, v in values.items()}, "step": len(s.steps)}
        if conflicts:
            out["conflicts"] = conflicts
        if errs:
            out["refusedPacks"] = errs
        lo = packs.loosened(rules, values)
        if lo:
            out["loosened"] = lo
        if not rules:
            out["note"] = "No rules are in force here yet, so code_check will refuse to run: import a pack (code_packs import), or try the example with jurisdiction XX."
        out["disclaimer"] = DISCLAIMER
        return out

    def code_facts(a):
        warn = []
        subs = subjects(warnings=warn)
        proj = (state().get("answers") or {}).get("project")
        if proj:
            # project-wide answers: values beside the packs', not a subject the rules judge
            subs = subs + [{"id": "project", "kind": "project", "facts": proj, "note": "project-wide values: they join the packs' values and may not loosen them"}]
        want = a.get("subject")
        if want:
            subs = [x for x in subs if x["id"] == want]
            if not subs:
                raise ToolError("not_found", f"subject {want!r}: code_facts lists them.")
        kinds = {}
        for x in subs:
            kinds[x["kind"]] = kinds.get(x["kind"], 0) + 1
        return {"subjects": subs[: int(a.get("limit", 60))], "counts": kinds, **({"warnings": warn} if warn else {}),
                "note": "provenance: measured (model geometry), derived (worked out from it), user_supplied (code_answer), assumed (never enough to be within the checked limits). Lengths are held in mm, areas mm2, loads kPa, design values MPa."}

    def code_answer(a):
        sids = a.get("subjects") or ([a["subject"]] if a.get("subject") else [])
        if not sids:
            raise bad("subject (or subjects [ … ] for the same answer to several): a member's or room's label, or \"project\".")
        st = state()
        ans = st.setdefault("answers", {})
        if a.get("clear"):
            for sid in sids:
                ans.pop(sid, None)
            save(st)
            s._agentlink_code = None
            s.snapshot("code_answer")
            return {"cleared": sids}
        given = a.get("facts")
        if not isinstance(given, dict) or not given:
            raise bad("facts: { name: value } or { name: { value, pm, unit, note } } — span 3600, spacing 406, size \"2x8\", truss false, use \"habitable_attic\", design.Fb 5.9 …")
        checked, doubts = {}, []
        for k, v in given.items():
            checked[k], doubt = clean(k, v)
            if doubt:
                doubts.append(doubt)
        for sid in sids:
            row = ans.setdefault(sid, {})
            row.update(checked)
        save(st)
        s._agentlink_code = None
        s.snapshot("code_answer")
        out = {"subjects": sids, "facts": row, "step": len(s.steps)}
        if doubts:
            out["check"] = doubts
        if any(sid not in ("project",) and sid not in {x["id"] for x in model_subjects()} and not (ans[sid].get("kind") or {}).get("value") for sid in sids):
            out["warning"] = "a subject here is not in the model: give kind (member, space, opening …) to check it as something not modelled, or it is not checked"
        return out

    def clean(k, v):
        """One answer, checked and held in the engine's units: (fact, doubt)."""
        spec = dict(v) if isinstance(v, dict) and "value" in v else {"value": v}
        if spec.get("provenance", "user_supplied") not in ("user_supplied", "assumed", "measured"):
            raise bad(f"{k}.provenance: user_supplied (the default), measured or assumed.")
        if spec.get("pm") is not None:
            try:
                spec["pm"] = float(spec["pm"])
            except (TypeError, ValueError):
                raise bad(f"{k}.pm: a number (plus-minus, 0 or more).") from None
        if k.startswith("factor."):
            try:
                framing.check_factors({k.split(".", 1)[1]: spec["value"]})
            except ValueError as e:
                raise bad(str(e)) from None
        f, prob = units.normalize(k, spec)
        if prob and prob["kind"] == "error":
            raise bad(prob["why"])
        row = {"value": f["value"], "provenance": spec.get("provenance", "user_supplied"), "source": "the person",
               **({"pm": f["pm"]} if f.get("pm") else {}), **({"unit": f["unit"]} if f.get("unit") else {}),
               **({"given": f["given"]} if f.get("given") else {}), **({"confirmed": True} if spec.get("confirmed") else {}),
               **({"note": spec["note"]} if spec.get("note") else {})}
        return row, ({"fact": k, "ask": prob["ask"]} if prob else None)

    def run_checks(a):
        """(findings, packs used, conflicts, extra { refusedPacks, loosened,
        warnings }). Refuses when no rule is in force."""
        rules, values, conflicts, used, errs = resolved()
        if not rules:
            raise ToolError("invalid_state", "No rules are in force for this project's jurisdiction and date, so nothing can be checked (this is not a clean result). "
                            + ("Packs refused as malformed: " + "; ".join(errs) + ". " if errs else "")
                            + "code_packs lists the packs; code_project sets the place and date (XX tries the shipped example).")
        warn = []
        subs = subjects(warnings=warn)
        if a.get("subjects"):
            subs = [x for x in subs if x["id"] in a["subjects"]]
        out = engine.run(rules, values, subs, only=a.get("tags"))
        extra = {k: v for k, v in (("refusedPacks", errs), ("loosened", packs.loosened(rules, values)), ("warnings", warn)) if v}
        filtered = bool(a.get("subjects") or a.get("tags"))
        s._agentlink_code = {"findings": out, "at": datetime.datetime.now().isoformat(timespec="seconds"), "packs": used, "conflicts": conflicts,
                             "filtered": filtered, **extra}
        return out, used, conflicts, extra

    def code_check(a):
        out, used, conflicts, more = run_checks(a)
        sm = summary(out)
        show = sm["findings"]
        if not a.get("all"):
            show = [f for f in show if f["state"] != "not_applicable"]
        for f in show:
            if not a.get("detail"):
                f.pop("detail", None)
        res = {"counts": sm["counts"], "findings": show[: int(a.get("limit", 40))], "packs": used, "disclaimer": DISCLAIMER}
        if len(show) > int(a.get("limit", 40)):
            res["more"] = len(show) - int(a.get("limit", 40))
        if conflicts:
            res["conflicts"] = conflicts
        res.update(more)
        res["meaning"] = f"compliant means {WITHIN} of the rules in force, for the facts given — not an approval; each finding lists its scope and what it did not check."
        q = questions(out)
        if q:
            res["questions"] = len(q)
            res["next"] = "code_missing groups what to ask the person; code_answer keeps their answers; then code_check again."
        return res

    def code_missing(a):
        cached = getattr(s, "_agentlink_code", None)
        out = cached["findings"] if cached and not cached.get("filtered") and not a.get("fresh") else run_checks({})[0]
        q = questions(out)
        size = int(a.get("batch", 5))
        return {"questions": q[:size], "remaining": max(0, len(q) - size), "total": len(q),
                "note": "Ask these together, in plain words; store each answer with code_answer { subject, facts }. A measurement that straddles a limit wants a careful tape, not a guess."}

    def span_inputs(a):
        member = {k: a[k] for k in ("size", "b", "d", "plies", "span", "spacing", "bearing", "repetitive") if a.get(k) is not None}
        loads = dict(a.get("loads") or {})
        design = dict(a.get("design") or {})
        tol = dict(a.get("tol") or {})
        if a.get("units") == "imperial":
            member, loads, design, tol = framing.imperial(member, loads, design, tol)
        if not loads and a.get("use"):
            try:
                _, values, _, _, _ = resolved()
            except ToolError:
                values = {}
            live, dead, snow = engine._loads(a["use"], values, {})
            if dead is None:
                raise bad(f"loads: {{ live, dead, snow }} kPa — the packs in force give none for use {a['use']!r}.")
            loads = {"live": live or 0, "dead": dead, "snow": snow or 0}
        if not loads:
            raise bad("loads { live, dead, snow } (kPa, or psf with units imperial), or use with a project's packs.")
        if any(design.get(k) is None for k in ("Fb", "Fv", "E")):
            raise bad("design: { Fb, Fv, E (MPa, or psi with units imperial), Fc_perp } — the reference values for the species and grade on the stamp, from its table or the supplier. None are assumed here.")
        return member, loads, design, tol

    def code_span(a):
        member, loads, design, tol = span_inputs(a)
        try:
            res = framing.check_member(member, loads, design, a.get("factors"), a.get("limits"), tol)
        except (ValueError, KeyError) as e:
            raise bad(str(e))
        res["state"] = framing.verdict(res)
        res["loads_kPa"] = loads
        if a.get("fem"):
            res["fem"] = fem_compare(res, design, a)
        res["disclaimer"] = DISCLAIMER
        return res

    def fem_compare(res, design, a):
        b, d, L = res["b"], res["d"], res["span"]
        w = res["detail"]["w_N_per_mm"]
        E = float(design["E"]) * float((a.get("factors") or {}).get("CM", 1)) * float((a.get("factors") or {}).get("Ct", 1))
        wd = os.path.join(s.out_dir, "code_fem", f"{a.get('name') or 'member'}".replace(" ", "_"))
        try:
            r = beamfem.run(b, d, L, w, E, workdir=wd, timeout=float(a.get("timeout", 120)))
        except FileNotFoundError as e:
            return {"skipped": str(e)}
        except Exception as e:
            return {"failed": str(e)[:300]}
        hand_d, hand_s = res["detail"]["deflection_total_mm"], res["detail"]["fb_MPa"]
        diff_d = (r["deflection_mm"] - hand_d) / hand_d if hand_d else 0
        diff_s = (r["stress_MPa"] - hand_s) / hand_s if hand_s else 0
        out = {**r, "hand": {"deflection_mm": hand_d, "stress_MPa": hand_s}, "difference": {"deflection": round(diff_d, 3), "stress": round(diff_s, 3)},
               "note": "A beam model with isotropic wood: a rough look beside the hand check, never the deciding answer."}
        if abs(diff_d) > 0.15 or abs(diff_s) > 0.15:
            out["discrepancy"] = "over 15 % apart: check the span, supports and section the two were given before trusting either."
        return out

    def code_fem(a):
        if a.get("subject"):
            subs = {x["id"]: x for x in subjects()}
            sub = subs.get(a["subject"])
            if sub is None:
                raise ToolError("not_found", f"subject {a['subject']!r}: code_facts lists them.")
            f = sub["facts"]
            g = lambda n: units.normalize(n, f[n])[0].get("value") if n in f else None  # noqa: E731  (held units: mm, MPa)
            a = {**a, "span": a.get("span") or g("span"), "spacing": a.get("spacing") or g("spacing"), "size": a.get("size") or g("size"),
                 "b": a.get("b") or g("b"), "d": a.get("d") or g("d"), "use": a.get("use") or g("use") or sub.get("use"),
                 "design": a.get("design") or {k: g(f"design.{k}") for k in ("Fb", "Fv", "E", "Fc_perp") if g(f"design.{k}") is not None},
                 "name": a.get("name") or a["subject"]}
            if a.get("size"):
                a.pop("b", None)
                a.pop("d", None)
        member, loads, design, tol = span_inputs(a)
        try:
            res = framing.check_member(member, loads, design, a.get("factors"), a.get("limits"), tol)
        except (ValueError, KeyError) as e:
            raise bad(str(e))
        return {"member": a.get("name") or "member", "hand": {"ratio": res["ratio"], "governs": res["governs"], "deflection_total_mm": res["detail"]["deflection_total_mm"], "fb_MPa": res["detail"]["fb_MPa"]},
                "fem": fem_compare(res, design, a), "disclaimer": DISCLAIMER}

    def code_packet(a):
        # always fresh and unfiltered: never the last (possibly narrowed) code_check
        out, used, conflicts, more = run_checks({})
        st = state()
        name = (a.get("name") or st.get("project", {}).get("name") or "handoff").replace(" ", "_")
        folder = os.path.join(s.out_dir, f"{name}_packet")
        os.makedirs(folder, exist_ok=True)
        sm = summary(out)
        stamp = datetime.datetime.now().isoformat(timespec="minutes")
        payload = {"disclaimer": DISCLAIMER, "meaning": f"compliant means {WITHIN} only — not an approval", "made": stamp, "project": st.get("project"),
                   "packs": used, "conflicts": conflicts, **more, "counts": sm["counts"], "findings": sm["findings"]}
        with open(os.path.join(folder, "findings.json"), "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, default=str)
        lines = [f"# Findings: {name}", "", "## Planning estimate — not an approval", "", DISCLAIMER,
                 f"A result headed {WITHIN} means only that: the rules in force, for the facts given, found nothing outside their limits. Each finding lists what it did not check. A licensed professional reviews, approves and stamps.", "",
                 f"Made {stamp}. Packs: {', '.join(used) or 'none'}.", "",
                 "| State | Count |", "| --- | --- |"] + [f"| {LABEL.get(k, k)} | {v} |" for k, v in sm["counts"].items()] + [""]
        if more.get("refusedPacks"):
            lines += ["## Packs refused as malformed", ""] + [f"- {e}" for e in more["refusedPacks"]] + [""]
        if conflicts:
            lines += ["## Layer conflicts", ""] + [f"- {c.get('value') or c.get('rule')} ({c.get('pack')}): {c.get('why')}" for c in conflicts] + [""]
        if more.get("loosened"):
            lines += ["## Loosened by an amendment", ""] + [f"- {x.get('value') or x.get('rule')} {x.get('param', '')}: {x.get('from')} to {x.get('to')} by {x.get('pack')} {x.get('cite') or ''}" for x in more["loosened"]] + [""]
        if more.get("warnings"):
            lines += ["## Warnings", ""] + [f"- {w['subject']}: {w['why']}" for w in more["warnings"]] + [""]
        for stt in STATES:
            fs = [x for x in sm["findings"] if x["state"] == stt]
            if not fs or stt == "not_applicable":
                continue
            lines += [f"## {LABEL.get(stt, stt).capitalize()}", ""]
            for x in fs:
                cite = x.get("cite") or {}
                c = " ".join(str(cite.get(k)) for k in ("code", "edition", "section", "source") if cite.get(k))
                lines.append(f"- **{x['subject']}**: {x.get('title') or x['rule']}. {x['why']}" + (f" (ratio {x['ratio']})" if x.get("ratio") is not None else "") + (f" [{c}]" if c else ""))
                if x.get("not_checked"):
                    lines.append(f"  - not checked: {', '.join(x['not_checked'])}")
                if x.get("defaults"):
                    lines.append("  - engine defaults: " + "; ".join(f"{d['fact']} {d['value']}" for d in x["defaults"]))
            lines.append("")
        with open(os.path.join(folder, "findings.md"), "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        # assumptions: every fact not measured from the model
        alines = [f"# Assumptions and inputs: {name}", "", "## Planning estimate — not an approval", "", DISCLAIMER, "",
                  "| Subject | Value | Is | Provenance | ± | Source / note |", "| --- | --- | --- | --- | --- | --- |"]
        for sub in subjects(st, warnings=[]):
            for k, f in sorted(sub["facts"].items()):
                if f.get("provenance") in ("user_supplied", "assumed", "derived"):
                    alines.append(f"| {sub['id']} | {k} | {f['value']} | {f['provenance']} | {f.get('pm', '')} | {f.get('source', '')} {f.get('note', '')} |")
        for k, f in sorted(((st.get("answers") or {}).get("project") or {}).items()):
            alines.append(f"| project | {k} | {f.get('value')} | {f.get('provenance')} | {f.get('pm', '')} | the person |")
        loads = [x for x in sm["findings"] if x.get("loads_kPa")]
        if loads:
            alines += ["", "## Loads used (kPa)", ""] + [f"- {x['subject']}: {x['loads_kPa']}" for x in loads]
        dfl = [x for x in sm["findings"] if x.get("defaults")]
        if dfl:
            alines += ["", "## Engine defaults (filled in, not given)", ""] + [f"- {x['subject']}: " + "; ".join(f"{d['fact']} {d['value']} ({d.get('note', '')})" for d in x["defaults"]) for x in dfl]
        with open(os.path.join(folder, "assumptions.md"), "w", encoding="utf-8") as f:
            f.write("\n".join(alines) + "\n")
        slines = [f"# Structural: {name}", "", "## Planning estimate — not an approval", "", DISCLAIMER, "",
                  f"Hand checks of simple spans: {WITHIN} only. Not checked: {', '.join(framing.NOT_CHECKED)}.", "", "| Member | State | Governs | Ratio | Range | Bending | Shear | Defl. live | Defl. total | Bearing |", "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
        for x in sm["findings"]:
            if x.get("ratios"):
                r = x["ratios"]
                slines.append(f"| {x['subject']} | {x['state']} | {x['governs']} | {x['ratio']} | {x['range']} | {r.get('bending')} | {r.get('shear')} | {r.get('deflection_live')} | {r.get('deflection_total')} | {r.get('bearing', '')} |")
            elif x.get("gate") == "truss":
                slines.append(f"| {x['subject']} | {x['state']} | truss gate | | | | | | | |")
        with open(os.path.join(folder, "structural.md"), "w", encoding="utf-8") as f:
            f.write("\n".join(slines) + "\n")
        with open(os.path.join(folder, "cutlist.csv"), "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["member", "size", "b_mm", "d_mm", "length_mm", "material"])
            for sub in subjects(st, warnings=[]):
                if sub["kind"] == "member":
                    g = lambda n: (engine.get(sub["facts"], n) or {}).get("value", "")  # noqa: E731
                    w.writerow([sub["id"], g("size"), g("b"), g("d"), g("length") or g("span"), g("material")])
        copied = []
        for k in ("ifc", "drawings"):
            p = a.get(k)
            if p:
                src = p if os.path.isabs(os.path.expanduser(p)) else os.path.join(s.out_dir, p)
                src = os.path.expanduser(src)
                if not os.path.isfile(src):
                    raise bad(f"{k}: {p} is not a file (bim_export / td_export write them first).")
                shutil.copyfile(src, os.path.join(folder, os.path.basename(src)))
                copied.append(os.path.basename(src))
        files = sorted(os.listdir(folder))
        return {"packet": folder, "files": files, "counts": sm["counts"], **({"copied": copied} if copied else {}),
                "note": "Hand it to the engineer as is: findings, assumptions, structural, cut list" + (", the model and drawings." if copied else "; add the model and drawings with ifc / drawings (bim_export, td_export first)."),
                "disclaimer": DISCLAIMER}

    s_ = {"type": "string"}
    num = {"type": "number"}
    return [
        Tool("code_packs", "Rule packs: action list (the packs found, by layer, jurisdiction, version, effective date) | import (path: a pack .json into your pack folder, checked first) | export (id, path) | ids (the information rules as a buildingSMART IDS 1.0 file: id, or the project's in force) | schema (the pack format). Packs hold paraphrased, cited rules — never a code's text. A pack is refused (and listed under refused) when it breaks the format: applies not an object, a non-finite number (Infinity, NaN), a negative load, a value object without value, a model pack's number without stricter (higher | lower), jurisdiction \"*\" on anything but a model pack, superseded not after effective. Optional code and edition (IRC, 2021) let the resolver refuse two editions in force at once.",
             obj({"action": {"type": "string", "enum": ["list", "import", "export", "ids", "schema"]}, "path": s_, "id": s_}), code_packs),
        Tool("code_project", "Set the project the checks are for: jurisdiction (hierarchical, US-WA-Pierce-DuPont, matched without regard to case; XX tries the shipped example), date (permit or design date, YYYY-MM-DD: picks the pack versions in force), include [contractor or owner pack ids], name. Answers the packs used, rules in force, values (loads …), any layer conflicts (a value or parameter looser than the layer below is refused; the base value's stricter direction holds), anything a state or local amendment loosened explicitly (op loosen with a cite: listed under loosened and in the findings), and packs refused as malformed. Refuses when two packs of the same code and layer (two editions) are both in force: set superseded on the older. With no rules in force it says so, and code_check will refuse.",
             obj({"jurisdiction": s_, "date": s_, "include": {"type": "array", "items": s_}, "occupancy": s_, "name": s_}), code_project, changes=True),
        Tool("code_facts", "What the checks will read: each member, room and opening from the BIM model with its facts (b, d, span, spacing, size, clear heights, sill …) and where each came from — measured, derived, user_supplied, assumed. subject narrows to one. Changes nothing.",
             obj({"subject": s_, "limit": {"type": "integer"}}), code_facts),
        Tool("code_answer", "Keep the person's answers with the document: subject (a label in the model; something not modelled needs kind too, or it is only warned about and not checked; \"project\" for project-wide values such as load.snow.ground, which join the pack values but may not loosen them) or subjects [labels] for one answer to several, facts { span: 3600, spacing: 406, size: \"2x8\", truss: false, use: \"habitable_attic\", escape: true, \"design.Fb\": 5.9, \"design.Fv\": 1.2, \"design.E\": 11000 } — a value, or { value, pm, unit, note, provenance, confirmed }. Units are converted on the way in and held as mm (lengths: mm, cm, m, in, ft), mm2 (areas: mm2, m2, in2, ft2), kPa (load.*: kPa, psf) and MPa (design.*: MPa, psi); a unit not in that list is refused, and with no unit the held unit is meant. A value outside its plausible band (span 300–15000 mm, spacing 200–2500, riser 100–250, sill 0–2500, Fb 2–40 MPa, Fv 0.3–5, E 3000–25000 …) is kept but asked about (is this in mm?) until given with its unit or confirmed: true. pm must be 0 or more; factor.* must be within its NDS range (CD 0.9–2.0, Cr 1.0–1.15 …) or it is refused. An assumed provenance on any fact a check reads keeps it from being within the checked limits. clear: true forgets a subject's answers. Answers win over what the model says, and each answer clears the last code_check's results.",
             obj({"subject": s_, "subjects": {"type": "array", "items": s_}, "facts": {"type": "object"}, "clear": {"type": "boolean"}}), code_answer, changes=True),
        Tool("code_check", "Run the rules in force on the model: each finding compliant (within the checked limits only, never a sign-off) | non_compliant | insufficient_information | manual_review_required | not_applicable | error, worst first, with its citation, values, ratio, why, scope and not_checked (what it did not look at). A subject whose applies key is unknown (an opening not yet said to be an escape, a room of undefined use) gets a question, not silence; a value in a unit the engine does not know or with a negative ± is an error; a value outside its plausible band is a question (is this in mm?); an assumed value on any fact a check reads is never within the checked limits. Refuses (an error, not an empty result) when no rule is in force. Also lists packs refused as malformed, layer conflicts, loosened values, and answers kept for subjects the model no longer has. tags [framing, geometry, information] or subjects narrow it; detail: true adds each member's working (both load cases). A planning estimate, never approval.",
             obj({"tags": {"type": "array", "items": s_}, "subjects": {"type": "array", "items": s_}, "detail": {"type": "boolean"}, "all": {"type": "boolean"}, "limit": {"type": "integer"}}), code_check),
        Tool("code_missing", "The questions that would decide the open findings, most-needed first, a few at a time (batch, 5): what to measure or confirm, about which subject, and which rules wait on it. Uses the last code_check only if it was unfiltered and nothing was answered since; otherwise (or with fresh: true) runs every check again. Ask the person, keep the answers with code_answer, check again.",
             obj({"batch": {"type": "integer"}, "fresh": {"type": "boolean"}}), code_missing),
        Tool("code_span", "A first hand check of one wood member on a simple span (a planning estimate, not an engineer's design): size (\"2x8\", plies) or b, d; span and spacing (mm, or ft and in with units imperial); loads { live, dead, snow } kPa (psf) or use (from the project's packs); design { Fb, Fv, E, Fc_perp } MPa (psi) — the reference values for its species and grade, none assumed; factors { CD, CM, Ct, CL, CF, Cfu, Ci, Cr, Cb }, each within its NDS range or refused (CD 0.9–2.0); limits { live: 360, total: 240 }; tol { span, d } plus-minus, 0 or more (ft and in with units imperial; a depth tolerance that leaves no depth is refused). Two load cases: dead plus live (or snow) at CD, and dead alone at CD 0.9; the worse governs. Cr 1.15 only for dimension lumber (89 mm or thinner a ply) at 610 mm or closer, never timbers. Answers ratios for bending, shear, deflection, bearing, the governing one and its case, its range, the state (compliant = within these checked limits only), defaults (every value the engine filled in: bearing length, Cr, CD, deflection limits) and not_checked (connections, ledgers, notches, lateral stability, uplift …); fem: true adds a CalculiX beam model beside it.",
             obj({"size": s_, "plies": {"type": "integer"}, "b": num, "d": num, "span": num, "spacing": num, "bearing": num, "repetitive": {"type": "boolean"}, "units": {"type": "string", "enum": ["si", "imperial"]},
                  "loads": {"type": "object"}, "use": s_, "design": {"type": "object"}, "factors": {"type": "object"}, "limits": {"type": "object"}, "tol": {"type": "object"}, "fem": {"type": "boolean"}, "name": s_, "timeout": num}), code_span),
        Tool("code_fem", "The rough FEM look at a member: a CalculiX beam model (pinned, roller, the uniform load) beside the hand check — stress and mid-span deflection, how far apart they are, and a discrepancy note past 15 %. subject (a member, its facts and answers) or the inputs code_span takes. Needs ccx.",
             obj({"subject": s_, "size": s_, "b": num, "d": num, "span": num, "spacing": num, "units": {"type": "string", "enum": ["si", "imperial"]}, "loads": {"type": "object"}, "use": s_,
                  "design": {"type": "object"}, "factors": {"type": "object"}, "limits": {"type": "object"}, "name": s_, "timeout": num}), code_fem),
        Tool("code_packet", "Write the handoff packet for a licensed professional: a folder with findings (json, md), assumptions (every value not measured, its ± and source, the loads, the engine's defaults), structural (member by member, with what was not checked), a cut list; ifc and drawings (paths from bim_export, td_export) copied in. Always runs every check fresh and unfiltered (never the last, possibly narrowed, code_check; fresh is accepted and ignored), and refuses like code_check when no rule is in force. Lists refused packs, layer conflicts and loosened values. Every file opens with the planning-estimate notice as a heading; compliant results are headed \"within the checked limits\".",
             obj({"name": s_, "ifc": s_, "drawings": s_, "fresh": {"type": "boolean"}}), code_packet),
    ]
