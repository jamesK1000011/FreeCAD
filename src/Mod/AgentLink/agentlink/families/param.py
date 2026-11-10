# SPDX-License-Identifier: LGPL-2.1-or-later
# Parameters: named values in a spreadsheet "Params" (name in column A, the
# value in B under the name as its alias, a note in C), and properties or
# named sketch constraints bound to expressions over them — FreeCAD's own
# expression engine, so the part stays parametric when opened by hand.

import re

from ..protocol import Tool, ToolError
from . import pd_common as pc

GROUP = "param"
INFO = "Named parameters in a Params spreadsheet, and properties or named sketch constraints bound to expressions over them."

SHEET = "Params"
NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
CELL = re.compile(r"^[A-Za-z]{1,2}[0-9]+$")


def obj(props, required=()):
    return {"type": "object", "properties": props, "required": list(required), "additionalProperties": False}


def sheet(doc, make=False):
    for o in doc.Objects:
        if o.TypeId == "Spreadsheet::Sheet" and o.Label == SHEET:
            return o
    if not make:
        return None
    ss = doc.addObject("Spreadsheet::Sheet", SHEET)
    ss.Label = SHEET
    ss.set("A1", "name")
    ss.set("B1", "value")
    ss.set("C1", "note")
    return ss


def rows(ss):
    out = []
    r = 2
    while True:
        n = ss.getContents(f"A{r}")
        if not n:
            return out
        out.append((r, n.lstrip("'")))
        r += 1


def value_of(ss, name):
    try:
        v = ss.get(name)
    except Exception as e:
        return None, str(e)
    if hasattr(v, "Value"):
        u = str(v).split(" ", 1)
        return round(float(v.Value), 6) + 0.0, (u[1] if len(u) > 1 else "")
    return v, ""


def users(doc, ss, name):
    pat = re.compile(rf"(\b{re.escape(ss.Name)}|<<{re.escape(ss.Label)}>>)\.{re.escape(name)}\b")
    out = []
    for o in doc.Objects:
        for path, expr in getattr(o, "ExpressionEngine", []) or []:
            if pat.search(expr):
                out.append(f"{o.Label}.{path.lstrip('.')}")
    return out


def tools(session):
    s = session
    doc = lambda: s.doc  # noqa: E731

    repaired = {}

    def check(doc_, what, refs=None):
        doc_.recompute()
        if refs and pc.broken(doc_):
            found = pc.repair_refs(doc_, refs)
            if found:
                repaired.update(found)
        bad = pc.broken(doc_)
        if bad:
            raise ToolError("failed", f"{what} breaks {bad[0]['object']}: {bad[0]['error']}" + (f" (and {', '.join(b['object'] for b in bad[1:])})" if len(bad) > 1 else "") + " — nothing was kept. Choose a value the features can take.")

    def bodies_now():
        return [pc.body_reading(b) for b in pc.pd_bodies(doc())]

    def param_set(a):
        name = a.get("name")
        if not isinstance(name, str) or not NAME.match(name) or CELL.match(name):
            raise ToolError("invalid_input", "name: letters, digits and _, starting with a letter — not a cell address like A1.")
        v = a.get("value")
        unit = a.get("unit", "mm" if isinstance(v, (int, float)) else "")
        if isinstance(v, bool) or not isinstance(v, (int, float, str)):
            raise ToolError("invalid_input", "value: a number (with unit mm, deg or \"\"), or an expression string over other parameters, e.g. \"width / 2\".")
        content = f"={v} {unit}".rstrip() if isinstance(v, (int, float)) else "=" + v.lstrip("=")
        ss = sheet(doc(), make=True)
        refs = [e for b in pc.pd_bodies(doc()) for e in pc.ref_snapshot(b)]
        repaired.clear()
        found = next((r for r, n in rows(ss) if n == name), None)
        new = found is None
        if new:
            found = len(rows(ss)) + 2
            ss.set(f"A{found}", name)
        ss.set(f"B{found}", content)
        if new:
            try:
                ss.setAlias(f"B{found}", name)
            except Exception as e:
                raise ToolError("invalid_input", f"name {name!r}: FreeCAD refuses it as an alias ({e}) — choose another.")
        if a.get("note") is not None:
            ss.set(f"C{found}", str(a["note"]))
        check(doc(), f"{name} = {v}", refs)
        val, u = value_of(ss, name)
        if val is None:
            raise ToolError("invalid_input", f"{name} = {v!r} does not evaluate: {u} — param_list gives the names to use.")
        s.snapshot("param_set")
        out = {"name": name, "value": val, "unit": u, "ref": f"{ss.Name}.{name}", "new": new, "usedBy": users(doc(), ss, name), "bodies": bodies_now(), "step": len(s.steps)}
        if repaired:
            out["refsFound"] = dict(repaired)
            out["note"] = "The change renamed edges or faces later features use: each was found again by what it is. Check them with view_render."
        return out

    def param_list(a):
        ss = sheet(doc())
        if ss is None:
            return {"params": [], "note": "No Params sheet yet: param_set makes it."}
        out = []
        for r, n in rows(ss):
            val, u = value_of(ss, n)
            e = {"name": n, "value": val, "unit": u, "content": ss.getContents(f"B{r}").lstrip("=")}
            note = ss.getContents(f"C{r}")
            if note:
                e["note"] = note.lstrip("'")
            us = users(doc(), ss, n)
            if us:
                e["usedBy"] = us
            out.append(e)
        return {"sheet": ss.Name, "params": out, "refAs": f"{ss.Name}.<name>"}

    def param_remove(a):
        ss = sheet(doc())
        name = a.get("name")
        r = next((r for r, n in rows(ss) if n == name), None) if ss else None
        if r is None:
            raise ToolError("not_found", f"parameter {name!r}: not here — param_list gives them.")
        us = users(doc(), ss, name)
        if us:
            raise ToolError("invalid_state", f"{name} is used by {', '.join(us)}: param_bind each to null (or another expression) first.")
        ss.setAlias(f"B{r}", "")
        ss.removeRows(str(r), 1)
        check(doc(), f"Removing {name}")
        s.snapshot("param_remove")
        return {"removed": name, "left": [n for _, n in rows(ss)], "step": len(s.steps)}

    def param_bind(a):
        target = a.get("target")
        if not isinstance(target, str) or "." not in target:
            raise ToolError("invalid_input", "target: \"Object.Property\" (Pad.Length) or \"Sketch.<constraint name>\".")
        oname, path = target.split(".", 1)
        o = next((x for x in doc().Objects if oname in (x.Label, x.Name)), None)
        if o is None:
            raise ToolError("not_found", f"{oname!r}: no such object — feature_list names the features.")
        if o.TypeId == "Sketcher::SketchObject" and path.split(".")[0] not in o.PropertiesList:
            names = [c.Name for c in o.Constraints if c.Name]
            if path.startswith("Constraints."):
                path = path.split(".", 1)[1]
            if path not in names:
                raise ToolError("not_found", f"constraint {path!r}: not a named constraint of {o.Label} — they are: {', '.join(names) or 'none (name one with sketch_constrain or sketch_edit rename)'}.")
            path = f"Constraints.{path}"
        elif path.split(".")[0] not in o.PropertiesList:
            raise ToolError("not_found", f"{path}: not a property of {o.Label}.")
        expr = a.get("expression")
        if expr in (None, ""):
            o.setExpression(path, None)
            check(doc(), f"Unbinding {target}")
            s.snapshot("param_bind")
            return {"target": target, "expression": None, "bodies": bodies_now(), "step": len(s.steps)}
        try:
            o.evalExpression(str(expr))
        except Exception as e:
            raise ToolError("invalid_input", f"{expr!r} does not evaluate: {e} — refer to a parameter as {SHEET}.<name> (param_list).")
        try:
            o.setExpression(path, str(expr))
        except Exception as e:
            raise ToolError("invalid_input", f"{target} = {expr!r}: {e}")
        check(doc(), f"Binding {target} to {expr}")
        try:
            now = pc.num(o.getDatum(path.split(".", 1)[1])) if path.startswith("Constraints.") else pc.num(getattr(o, path))
        except Exception:
            now = None
        s.snapshot("param_bind")
        return {"target": target, "expression": expr, "value": now, "bodies": bodies_now(), "step": len(s.steps)}

    return [
        Tool("param_set", "Set a named parameter in the Params spreadsheet (made when first needed): name, value (a number with unit mm | deg | \"\", or an expression over others, \"width / 2\"), note. Everything bound to it recomputes; a value that breaks a feature is refused with FreeCAD's error and nothing kept. Answers its reference (Params.<name>) and who uses it.",
             obj({"name": {"type": "string"}, "value": {"type": ["number", "string"]}, "unit": {"type": "string"}, "note": {"type": "string"}}, ["name", "value"]), param_set, changes=True),
        Tool("param_list", "The parameters: name, value, unit, what they are set to, and which properties use them. Changes nothing.", obj({}), param_list),
        Tool("param_remove", "Remove a parameter no property uses (unbind its users first).", obj({"name": {"type": "string"}}, ["name"]), param_remove, changes=True),
        Tool("param_bind", "Bind a property or a named sketch constraint to an expression: target \"Pad.Length\" | \"Sketch.width\" (a constraint's name), expression \"Params.width\" or \"Params.width / 2 + 1\"; null unbinds (the value stays). A bound value is then changed by param_set, not edited directly.",
             obj({"target": {"type": "string"}, "expression": {"type": ["string", "null"]}}, ["target", "expression"]), param_bind, changes=True),
    ]
