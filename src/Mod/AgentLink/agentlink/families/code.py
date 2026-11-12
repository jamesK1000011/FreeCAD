# SPDX-License-Identifier: LGPL-2.1-or-later
# Building-code checks for planning, headless: rule packs stacked by
# jurisdiction and date, facts read from the BIM model (members, rooms,
# openings) with where each came from, the person's answers kept with the
# document, the checks run in five states, the questions that would decide
# what is still open, a wood member's span check by hand with a beam FEM
# beside it, and the handoff packet for a licensed professional. Nothing
# here approves anything: every answer says it is a planning estimate.

import csv
import datetime
import json
import math
import os
import shutil

import FreeCAD

from .. import store
from ..code import DISCLAIMER, beamfem, engine, framing, packs
from ..code import ids as idsfile
from ..code.findings import STATES, questions, summary
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
        rules, values, conflicts, used = packs.resolve(ps, pr["jurisdiction"], pr.get("date") or datetime.date.today().isoformat(), pr.get("include"))
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

    def subjects(st=None):
        st = st or state()
        subs = model_subjects()
        byid = {x["id"]: x for x in subs}
        for sid, given in (st.get("answers") or {}).items():
            sub = byid.get(sid)
            if sub is None:
                sub = {"id": sid, "kind": (given.get("kind") or {}).get("value", "member"), "facts": {}}
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
        s.snapshot("code_project")
        out = {"project": pr, "packs": used, "rules": len(rules), "values": {k: v.get("value") for k, v in values.items()}, "step": len(s.steps)}
        if conflicts:
            out["conflicts"] = conflicts
        if errs:
            out["refusedPacks"] = errs
        if not used:
            out["note"] = "No pack applies here yet: import one (code_packs import), or try the example with jurisdiction XX."
        out["disclaimer"] = DISCLAIMER
        return out

    def code_facts(a):
        subs = subjects()
        want = a.get("subject")
        if want:
            subs = [x for x in subs if x["id"] == want]
            if not subs:
                raise ToolError("not_found", f"subject {want!r}: code_facts lists them.")
        kinds = {}
        for x in subs:
            kinds[x["kind"]] = kinds.get(x["kind"], 0) + 1
        return {"subjects": subs[: int(a.get("limit", 60))], "counts": kinds,
                "note": "provenance: measured (model geometry), derived (worked out from it), user_supplied (code_answer), assumed (a default — never enough for compliant)."}

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
            s.snapshot("code_answer")
            return {"cleared": sids}
        given = a.get("facts")
        if not isinstance(given, dict) or not given:
            raise bad("facts: { name: value } or { name: { value, pm, unit, note } } — span 3600, spacing 406, size \"2x8\", truss false, use \"habitable_attic\", design.Fb 5.9 …")
        for sid in sids:
            row = put(ans, sid, given)
        save(st)
        s.snapshot("code_answer")
        return {"subjects": sids, "facts": row, "step": len(s.steps)}

    def put(ans, sid, given):
        row = ans.setdefault(sid, {})
        for k, v in given.items():
            spec = v if isinstance(v, dict) and "value" in v else {"value": v}
            row[k] = {"value": spec["value"], "provenance": spec.get("provenance", "user_supplied"), "source": "the person",
                      **({"pm": float(spec["pm"])} if spec.get("pm") else {}), **({"unit": spec["unit"]} if spec.get("unit") else {}),
                      **({"note": spec["note"]} if spec.get("note") else {})}
            if row[k]["provenance"] not in ("user_supplied", "assumed", "measured"):
                raise bad(f"{k}.provenance: user_supplied (the default), measured or assumed.")
        return row

    def run_checks(a):
        rules, values, conflicts, used, errs = resolved()
        subs = subjects()
        if a.get("subjects"):
            subs = [x for x in subs if x["id"] in a["subjects"]]
        out = engine.run(rules, values, subs, only=a.get("tags"))
        s._agentlink_code = {"findings": out, "at": datetime.datetime.now().isoformat(timespec="seconds"), "packs": used, "conflicts": conflicts}
        return out, used, conflicts

    def code_check(a):
        out, used, conflicts = run_checks(a)
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
        q = questions(out)
        if q:
            res["questions"] = len(q)
            res["next"] = "code_missing groups what to ask the person; code_answer keeps their answers; then code_check again."
        return res

    def code_missing(a):
        cached = getattr(s, "_agentlink_code", None)
        out = cached["findings"] if cached and not a.get("fresh") else run_checks({})[0]
        q = questions(out)
        size = int(a.get("batch", 5))
        return {"questions": q[:size], "remaining": max(0, len(q) - size), "total": len(q),
                "note": "Ask these together, in plain words; store each answer with code_answer { subject, facts }. A measurement that straddles a limit wants a careful tape, not a guess."}

    def span_inputs(a):
        member = {k: a[k] for k in ("size", "b", "d", "plies", "span", "spacing", "bearing", "repetitive") if a.get(k) is not None}
        loads = dict(a.get("loads") or {})
        design = dict(a.get("design") or {})
        if a.get("units") == "imperial":
            member, loads, design = framing.imperial(member, loads, design)
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
        return member, loads, design

    def code_span(a):
        member, loads, design = span_inputs(a)
        try:
            res = framing.check_member(member, loads, design, a.get("factors"), a.get("limits"), a.get("tol"))
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
            g = lambda n: (engine.get(f, n) or {}).get("value")  # noqa: E731
            a = {**a, "span": a.get("span") or g("span"), "spacing": a.get("spacing") or g("spacing"), "size": a.get("size") or g("size"),
                 "b": a.get("b") or g("b"), "d": a.get("d") or g("d"), "use": a.get("use") or g("use") or sub.get("use"),
                 "design": a.get("design") or {k: g(f"design.{k}") for k in ("Fb", "Fv", "E", "Fc_perp") if g(f"design.{k}") is not None},
                 "name": a.get("name") or a["subject"]}
            if a.get("size"):
                a.pop("b", None)
                a.pop("d", None)
        member, loads, design = span_inputs(a)
        res = framing.check_member(member, loads, design, a.get("factors"), a.get("limits"))
        return {"member": a.get("name") or "member", "hand": {"ratio": res["ratio"], "governs": res["governs"], "deflection_total_mm": res["detail"]["deflection_total_mm"], "fb_MPa": res["detail"]["fb_MPa"]},
                "fem": fem_compare(res, design, a), "disclaimer": DISCLAIMER}

    def code_packet(a):
        cached = getattr(s, "_agentlink_code", None)
        out, used, conflicts = (cached["findings"], cached["packs"], cached["conflicts"]) if cached and not a.get("fresh") else run_checks({})
        st = state()
        name = (a.get("name") or st.get("project", {}).get("name") or "handoff").replace(" ", "_")
        folder = os.path.join(s.out_dir, f"{name}_packet")
        os.makedirs(folder, exist_ok=True)
        sm = summary(out)
        stamp = datetime.datetime.now().isoformat(timespec="minutes")
        payload = {"disclaimer": DISCLAIMER, "made": stamp, "project": st.get("project"), "packs": used, "conflicts": conflicts,
                   "counts": sm["counts"], "findings": sm["findings"]}
        with open(os.path.join(folder, "findings.json"), "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, default=str)
        lines = [f"# Findings: {name}", "", f"*{DISCLAIMER}*", "", f"Made {stamp}. Packs: {', '.join(used) or 'none'}.", "",
                 "| State | Count |", "| --- | --- |"] + [f"| {k} | {v} |" for k, v in sm["counts"].items()] + [""]
        for stt in STATES:
            fs = [x for x in sm["findings"] if x["state"] == stt]
            if not fs or stt == "not_applicable":
                continue
            lines += [f"## {stt.replace('_', ' ')}", ""]
            for x in fs:
                cite = x.get("cite") or {}
                c = " ".join(str(cite.get(k)) for k in ("code", "edition", "section", "source") if cite.get(k))
                lines.append(f"- **{x['subject']}**: {x.get('title') or x['rule']}. {x['why']}" + (f" (ratio {x['ratio']})" if x.get("ratio") is not None else "") + (f" [{c}]" if c else ""))
            lines.append("")
        with open(os.path.join(folder, "findings.md"), "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        # assumptions: every fact not measured from the model
        alines = [f"# Assumptions and inputs: {name}", "", f"*{DISCLAIMER}*", "", "| Subject | Value | Is | Provenance | ± | Source / note |", "| --- | --- | --- | --- | --- | --- |"]
        for sub in subjects(st):
            for k, f in sorted(sub["facts"].items()):
                if f.get("provenance") in ("user_supplied", "assumed", "derived"):
                    alines.append(f"| {sub['id']} | {k} | {f['value']} | {f['provenance']} | {f.get('pm', '')} | {f.get('source', '')} {f.get('note', '')} |")
        loads = [x for x in sm["findings"] if x.get("loads_kPa")]
        if loads:
            alines += ["", "## Loads used (kPa)", ""] + [f"- {x['subject']}: {x['loads_kPa']}" for x in loads]
        with open(os.path.join(folder, "assumptions.md"), "w", encoding="utf-8") as f:
            f.write("\n".join(alines) + "\n")
        slines = [f"# Structural: {name}", "", f"*{DISCLAIMER}*", "", "| Member | State | Governs | Ratio | Range | Bending | Shear | Defl. live | Defl. total | Bearing |", "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
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
            for sub in subjects(st):
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
        Tool("code_packs", "Rule packs: action list (the packs found, by layer, jurisdiction, version, effective date) | import (path: a pack .json into your pack folder, checked first) | export (id, path) | ids (the information rules as a buildingSMART IDS 1.0 file: id, or the project's in force) | schema (the pack format). Packs hold paraphrased, cited rules — never a code's text.",
             obj({"action": {"type": "string", "enum": ["list", "import", "export", "ids", "schema"]}, "path": s_, "id": s_}), code_packs),
        Tool("code_project", "Set the project the checks are for: jurisdiction (hierarchical, US-WA-Pierce-DuPont; XX tries the shipped example), date (permit or design date, YYYY-MM-DD: picks the pack versions in force), include [contractor or owner pack ids], name. Answers the packs used, rules in force, values (loads …) and any layer conflicts.",
             obj({"jurisdiction": s_, "date": s_, "include": {"type": "array", "items": s_}, "occupancy": s_, "name": s_}), code_project, changes=True),
        Tool("code_facts", "What the checks will read: each member, room and opening from the BIM model with its facts (b, d, span, spacing, size, clear heights, sill …) and where each came from — measured, derived, user_supplied, assumed. subject narrows to one. Changes nothing.",
             obj({"subject": s_, "limit": {"type": "integer"}}), code_facts),
        Tool("code_answer", "Keep the person's answers with the document: subject (a label, or a new one for something not modelled) or subjects [labels] for one answer to several, facts { span: 3600, spacing: 406, size: \"2x8\", truss: false, use: \"habitable_attic\", escape: true, \"design.Fb\": 5.9, \"design.Fv\": 1.2, \"design.E\": 11000 } — a value, or { value, pm, unit, note, provenance }. clear: true forgets a subject's answers. Answers win over what the model says.",
             obj({"subject": s_, "subjects": {"type": "array", "items": s_}, "facts": {"type": "object"}, "clear": {"type": "boolean"}}), code_answer, changes=True),
        Tool("code_check", "Run the rules in force on the model: each finding compliant | non_compliant | insufficient_information | manual_review_required | not_applicable | error, worst first, with its citation, values, ratio and why. tags [framing, geometry, information] or subjects narrow it; detail: true adds each member's working. A planning estimate, never approval.",
             obj({"tags": {"type": "array", "items": s_}, "subjects": {"type": "array", "items": s_}, "detail": {"type": "boolean"}, "all": {"type": "boolean"}, "limit": {"type": "integer"}}), code_check),
        Tool("code_missing", "The questions that would decide the open findings, most-needed first, a few at a time (batch, 5): what to measure or confirm, about which subject, and which rules wait on it. Ask the person, keep the answers with code_answer, check again.",
             obj({"batch": {"type": "integer"}, "fresh": {"type": "boolean"}}), code_missing),
        Tool("code_span", "A wood member checked by hand, as an engineer would first: size (\"2x8\", plies) or b, d; span and spacing (mm, or ft and in with units imperial); loads { live, dead, snow } kPa (psf) or use (from the project's packs); design { Fb, Fv, E, Fc_perp } MPa (psi) — the reference values for its species and grade, none assumed; factors { CD, CM, Ct, CF, Cr … }; limits { live: 360, total: 240 }; tol { span, d } plus-minus. Answers ratios for bending, shear, deflection, bearing, the governing one, its range and the state; fem: true adds a CalculiX beam model beside it.",
             obj({"size": s_, "plies": {"type": "integer"}, "b": num, "d": num, "span": num, "spacing": num, "bearing": num, "repetitive": {"type": "boolean"}, "units": {"type": "string", "enum": ["si", "imperial"]},
                  "loads": {"type": "object"}, "use": s_, "design": {"type": "object"}, "factors": {"type": "object"}, "limits": {"type": "object"}, "tol": {"type": "object"}, "fem": {"type": "boolean"}, "name": s_, "timeout": num}), code_span),
        Tool("code_fem", "The rough FEM look at a member: a CalculiX beam model (pinned, roller, the uniform load) beside the hand check — stress and mid-span deflection, how far apart they are, and a discrepancy note past 15 %. subject (a member, its facts and answers) or the inputs code_span takes. Needs ccx.",
             obj({"subject": s_, "size": s_, "b": num, "d": num, "span": num, "spacing": num, "units": {"type": "string", "enum": ["si", "imperial"]}, "loads": {"type": "object"}, "use": s_,
                  "design": {"type": "object"}, "factors": {"type": "object"}, "limits": {"type": "object"}, "name": s_, "timeout": num}), code_fem),
        Tool("code_packet", "Write the handoff packet for a licensed professional: a folder with findings (json, md), assumptions (every value not measured, its ± and source, the loads), structural (member by member), a cut list; ifc and drawings (paths from bim_export, td_export) copied in. Every file carries the planning-estimate notice.",
             obj({"name": s_, "ifc": s_, "drawings": s_, "fresh": {"type": "boolean"}}), code_packet),
    ]
