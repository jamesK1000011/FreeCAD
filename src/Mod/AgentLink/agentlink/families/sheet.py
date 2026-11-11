# SPDX-License-Identifier: LGPL-2.1-or-later
# Spreadsheets: any cell of any sheet read and written (values, expressions,
# aliases, units), formatted, rows and columns inserted and removed, CSV in
# and out — and design tables, built the way FreeCAD's own "Configuration
# table" dialog builds them (a dynamic enumeration bound to a column of
# configuration names, the active row bound to the chosen one), so a part
# made here switches by hand in FreeCAD too.

import os
import re

from .. import cells as C
from ..protocol import Tool, ToolError
from . import pd_common as pc

GROUP = "sheet"
INFO = "Spreadsheets: any cell read and written (values, expressions, aliases, units), formatting, rows and columns, CSV import and export, and design tables (configurations) that drive a part."

READ_CAP = 400  # cells one sheet_read answers
STYLES = ("bold", "italic", "underline")
H_ALIGN = ("left", "center", "right")
V_ALIGN = ("top", "vcenter", "bottom")
CONF_PROP = "Configuration"
CONF_GROUP = "ConfigurationTable"
HIDDEN = re.compile(r"^=\s*hiddenref\(\s*(.+?)\.(\w+)\.String\s*\)\s*$")
BIND = re.compile(r"^\.?cells\.Bind\.([A-Z]+[0-9]+)\.([A-Z]+[0-9]+)$")


def obj(props, required=()):
    return {"type": "object", "properties": props, "required": list(required), "additionalProperties": False}


def bad(msg):
    return ToolError("invalid_input", msg)


def addr_or_refuse(a, what="cell"):
    try:
        C.parse(a)
    except C.AddressError as e:
        raise bad(f"{what}: {e}.")
    return str(a).upper().replace("$", "")


def range_or_refuse(r, what="range"):
    try:
        a, b = C.parse_range(r)
    except C.AddressError as e:
        raise bad(f"{what}: {e}.")
    return C.range_text(a, b)


# ---- reading a sheet ----


def sheets(doc):
    return [o for o in doc.Objects if o.TypeId == "Spreadsheet::Sheet"]


def find(doc, ref):
    all_ = sheets(doc)
    if ref is None:
        if len(all_) == 1:
            return all_[0]
        if not all_:
            raise ToolError("not_found", "No spreadsheet in the document: sheet_new makes one.")
        raise bad(f"sheet: say which — {', '.join(o.Label for o in all_)}.")
    for o in all_:
        if ref in (o.Label, o.Name):
            return o
    for o in all_:
        if o.Label.lower() == str(ref).lower():
            return o
    raise ToolError("not_found", f"sheet {ref!r}: not here — the sheets: {', '.join(o.Label for o in all_) or 'none (sheet_new)'}.")


def filled(ss):
    """The addresses that hold something, row by row."""
    for m in ("getNonEmptyCells", "getUsedCells"):
        if hasattr(ss, m):
            try:
                return sorted({a for a in getattr(ss, m)() if ss.getContents(a)}, key=C.sort_key)
            except Exception:
                pass
    # Older builds: walk outward until a band of empty rows and columns.
    out = []
    for r in range(1, 401):
        for c in range(1, 53):
            a = C.name(r, c)
            if ss.getContents(a):
                out.append(a)
    return out


def value_of(ss, a):
    """(value, unit, error): a cell's computed value as JSON."""
    try:
        v = ss.get(a)
    except Exception as e:
        msg = str(e)
        return None, "", (msg or "does not evaluate")
    if hasattr(v, "Value") and hasattr(v, "Unit"):
        parts = str(v).split(" ", 1)
        return round(float(v.Value), 6) + 0.0, (parts[1] if len(parts) > 1 else ""), None
    if isinstance(v, bool):
        return bool(v), "", None
    if isinstance(v, float):
        return round(v, 9) + 0.0, "", None
    if isinstance(v, (int, str)):
        return v, "", None
    return str(v), "", None


def cell_reading(ss, a, full=False):
    raw = ss.getContents(a)
    val, unit, err = value_of(ss, a)
    e = {"cell": a, "content": raw}
    if err:
        e["error"] = err
    else:
        e["value"] = val
        if unit:
            e["unit"] = unit
    try:
        al = ss.getAlias(a)
    except Exception:
        al = None
    if al:
        e["alias"] = al
    if full:
        for key, getter in (("style", "getStyle"), ("align", "getAlignment")):
            try:
                g = getattr(ss, getter)(a)
            except Exception:
                g = None
            if g:
                e[key] = sorted(str(x) for x in g)
        for key, getter in (("color", "getForeground"), ("background", "getBackground")):
            try:
                g = C.rgba_hex(getattr(ss, getter)(a))
            except Exception:
                g = None
            if g:
                e[key] = g
        try:
            du = ss.getDisplayUnit(a)
        except Exception:
            du = None
        if du:
            e["displayUnit"] = du if isinstance(du, str) else str(du[0] if isinstance(du, (tuple, list)) else du)
    return e


def aliases(ss, addrs):
    out = {}
    for a in addrs:
        try:
            al = ss.getAlias(a)
        except Exception:
            al = None
        if al:
            out[al] = a
    return out


def bindings(ss):
    """[(from, to, expression)] — ranges bound to other cells."""
    out = []
    for path, expr in getattr(ss, "ExpressionEngine", []) or []:
        m = BIND.match(path.lstrip("."))
        if m:
            out.append((m.group(1), m.group(2), expr))
    return out


def users_of(doc, ss, alias):
    pat = re.compile(rf"(\b{re.escape(ss.Name)}|<<{re.escape(ss.Label)}>>)\.{re.escape(alias)}\b")
    out = []
    for o in doc.Objects:
        if o is ss:
            continue
        for path, expr in getattr(o, "ExpressionEngine", []) or []:
            if pat.search(expr):
                out.append(f"{o.Label}.{path.lstrip('.')}")
    return out


# ---- design tables ----


def tables(doc, ss):
    """The design tables on a sheet: where each sits, its parameters, its
    configurations and which one is on."""
    out = []
    for a in filled(ss):
        m = HIDDEN.match(ss.getContents(a))
        if not m:
            continue
        owner_ref, prop = m.group(1), m.group(2)
        owner = next((o for o in doc.Objects if owner_ref.strip("<>") in (o.Name, o.Label)), None)
        r, c = C.parse(a)
        params = []
        cc = c + 1
        while r > 1 and cc <= C.MAX_COL and ss.getContents(C.name(r - 1, cc)):
            params.append((C.name(r, cc), ss.getContents(C.name(r - 1, cc)).lstrip("'")))
            cc += 1
        confs = []
        rr = r + 1
        while rr <= C.MAX_ROW and ss.getContents(C.name(rr, c)):
            confs.append((rr, ss.getContents(C.name(rr, c)).lstrip("'")))
            rr += 1
        active = None
        if owner is not None and hasattr(owner, prop):
            try:
                active = getattr(owner, prop)
            except Exception:
                active = None
        items = []
        if owner is not None and hasattr(owner, prop):
            try:
                items = list(owner.getEnumerationsOfProperty(prop) or [])
            except Exception:
                items = []
        out.append({"items": items, "at": a, "owner": owner, "ownerLabel": owner.Label if owner is not None else owner_ref,
                    "property": prop, "params": params, "configs": confs, "active": active})
    return out


def table_reading(ss, t):
    rows = []
    for rr, n in t["configs"]:
        vals = {}
        for j, (_, p) in enumerate(t["params"]):
            v, u, err = value_of(ss, C.name(rr, C.parse(t["at"])[1] + 1 + j))
            if err:
                vals[p] = f"error: {err}"
            elif isinstance(v, float) and u:
                vals[p] = f"{v:g} {u}"
            else:
                vals[p] = v
        rows.append({"config": n, "values": vals})
    act = {}
    for a, p in t["params"]:
        v, u, err = value_of(ss, a)
        act[p] = {"value": v, **({"unit": u} if u else {}), **({"error": err} if err else {})}
    names = [n for _, n in t["configs"]]
    out_of_step = t["items"] and t["items"] != names
    return {**({"switchOffers": t["items"]} if out_of_step else {}), "at": t["at"], "switch": f"{t['ownerLabel']}.{t['property']}", "active": t["active"], "params": [p for _, p in t["params"]],
            "configs": rows, "current": act, "refAs": f"{ss.Name}.<param>"}


def tools(session):
    s = session
    doc = lambda: s.doc  # noqa: E731

    def check(what):
        doc().recompute()
        b = pc.broken(doc())
        if not b:
            return
        # A sheet says only that some cell failed: name the cells.
        why = []
        for x in b:
            o = doc().getObject(next((o.Name for o in doc().Objects if o.Label == x["object"]), ""))
            if o is not None and o.TypeId == "Spreadsheet::Sheet":
                bad_cells = [c for c in (cell_reading(o, a) for a in filled(o)) if "error" in c]
                why.append(f"{x['object']} " + "; ".join(f"{c['cell']} ({c['content']}): {c['error']}" for c in bad_cells[:3]) if bad_cells else f"{x['object']}: {x['error']}")
            else:
                why.append(f"{x['object']}: {x['error']}")
        # Undo here and recompute, so the sheet's values stand as they were
        # (rolling back alone leaves the cells uncomputed).
        doc().abortTransaction()
        doc().recompute()
        raise ToolError("failed", f"{what} breaks " + "; and ".join(why) + " — nothing was kept.")

    def bodies_now():
        return [pc.body_reading(b) for b in pc.pd_bodies(doc())]

    def done(tool, out, ss=None):
        s.snapshot(tool)
        out["step"] = len(s.steps)
        if ss is not None:
            out.setdefault("sheet", ss.Label)
        return out

    # ---- sheets ----

    def sheet_new(a):
        label = s.unique(a.get("name") or "Spreadsheet")
        if not C.NAME.match(label.replace(" ", "_")):
            raise bad("name: letters, digits, spaces and _.")
        ss = doc().addObject("Spreadsheet::Sheet", "Spreadsheet")
        ss.Label = label
        doc().recompute()
        return done("sheet_new", {"made": ss.Label, "name": ss.Name, "refAs": f"{ss.Name}.<alias> or {ss.Name}.A1, or <<{ss.Label}>>.<alias>"}, ss)

    def sheet_list(a):
        out = []
        for ss in sheets(doc()):
            fl = filled(ss)
            als = aliases(ss, fl)
            e = {"sheet": ss.Label, "name": ss.Name, "cells": len(fl), "range": C.bounds(fl)}
            if als:
                e["aliases"] = sorted(als)
                used = sorted({u for al in als for u in users_of(doc(), ss, al)})
                if used:
                    e["usedBy"] = used
            ts = tables(doc(), ss)
            if ts:
                e["designTables"] = [{"at": t["at"], "switch": f"{t['ownerLabel']}.{t['property']}", "active": t["active"], "configs": [n for _, n in t["configs"]]} for t in ts]
            out.append(e)
        return {"sheets": out} if out else {"sheets": [], "note": "No spreadsheet yet: sheet_new makes one (param_set keeps a Params sheet of its own)."}

    def sheet_read(a):
        ss = find(doc(), a.get("sheet"))
        fl = filled(ss)
        if a.get("range"):
            want = set(C.cells_in(range_or_refuse(a["range"])))
            fl = [x for x in fl if x in want]
        if a.get("cells"):
            fl = sorted({addr_or_refuse(x) for x in a["cells"]}, key=C.sort_key)
        more = max(0, len(fl) - READ_CAP)
        rows = [cell_reading(ss, x, full=bool(a.get("format"))) for x in fl[:READ_CAP]]
        out = {"sheet": ss.Label, "name": ss.Name, "range": C.bounds(fl), "cells": rows}
        errs = [r["cell"] for r in rows if "error" in r]
        if errs:
            out["errors"] = errs
        if more:
            out["more"] = f"{more} cells more: read a range."
        bs = bindings(ss)
        if bs:
            out["bound"] = [{"range": f"{f}:{t}", "to": e} for f, t, e in bs]
        ts = tables(doc(), ss)
        if ts:
            out["designTables"] = [table_reading(ss, t) for t in ts]
        return out

    def write_cells(ss, cells, text):
        for addr, v in cells.items():
            try:
                body = C.content(v, text=text)
            except C.AddressError as e:
                raise bad(f"{addr}: {e}.")
            if body == "":
                ss.clear(addr)
            else:
                ss.set(addr, body)

    def sheet_set(a):
        ss = find(doc(), a.get("sheet"))
        cells = {}
        for k, v in (a.get("cells") or {}).items():
            cells[addr_or_refuse(k)] = v
        if a.get("rows") is not None:
            try:
                cells.update(C.grid(addr_or_refuse(a.get("at", "A1"), "at"), a["rows"]))
            except C.AddressError as e:
                raise bad(f"rows: {e}.")
        als = {addr_or_refuse(k): v for k, v in (a.get("aliases") or {}).items()}
        if not cells and not als:
            raise bad("Nothing to set: cells { A1: value }, rows [[…]] from at, or aliases { B2: name }.")
        bound = [(f, t) for f, t, _ in bindings(ss)]
        for addr in cells:
            for f, t in bound:
                if addr in C.cells_in(f"{f}:{t}"):
                    raise ToolError("invalid_state", f"{addr} is bound to another range (a design table's active row): change the table's rows, or switch it with sheet_table use.")
        write_cells(ss, cells, bool(a.get("text")))
        for addr, al in als.items():
            if al not in (None, "") and (not C.NAME.match(str(al)) or C.is_cell(al)):
                raise bad(f"alias {al!r}: letters, digits and _, starting with a letter, and not a cell address.")
            try:
                ss.setAlias(addr, al or "")
            except Exception as e:
                raise bad(f"alias {al!r} for {addr}: FreeCAD refuses it ({e}) — it may be taken; sheet_read shows the aliases.")
        check("That change")
        touched = sorted(set(cells) | set(als), key=C.sort_key)
        rows = [cell_reading(ss, x) for x in touched[:READ_CAP]]
        errs = [r for r in rows if "error" in r]
        if errs:
            doc().abortTransaction()
            doc().recompute()
            raise ToolError("failed", f"{errs[0]['cell']} = {errs[0]['content']!r} does not evaluate: {errs[0]['error']} — nothing was kept.")
        used = sorted({u for al in aliases(ss, touched) for u in users_of(doc(), ss, al)})
        out = {"cells": rows}
        if used:
            out["drives"] = used
            out["bodies"] = bodies_now()
        return done("sheet_set", out, ss)

    def sheet_clear(a):
        ss = find(doc(), a.get("sheet"))
        rng = range_or_refuse(a.get("range"))
        hit = [x for x in filled(ss) if x in set(C.cells_in(rng))]
        used = sorted({u for al in aliases(ss, hit) for u in users_of(doc(), ss, al)})
        if used and not a.get("force"):
            raise ToolError("invalid_state", f"{rng} holds aliases in use by {', '.join(used)}: unbind them (param_bind expression: null), or force: true to break them.")
        for x in C.cells_in(rng):
            ss.clear(x)
        check(f"Clearing {rng}")
        return done("sheet_clear", {"cleared": rng, "cells": len(hit)}, ss)

    def sheet_format(a):
        ss = find(doc(), a.get("sheet"))
        rng = range_or_refuse(a.get("range"))
        addrs = C.cells_in(rng)
        changed = []
        if "style" in a:
            st = a["style"] or []
            if any(x not in STYLES for x in st):
                raise bad(f"style: any of {', '.join(STYLES)}; [] clears.")
            for x in addrs:
                ss.setStyle(x, "|".join(st) if st else "", "replace")
            changed.append("style")
        if "align" in a:
            al = a["align"] or []
            al = [al] if isinstance(al, str) else al
            h = [x for x in al if x in H_ALIGN]
            v = [x for x in al if x in V_ALIGN]
            if len(h) + len(v) != len(al) or len(h) > 1 or len(v) > 1:
                raise bad(f"align: one of {', '.join(H_ALIGN)} and/or one of {', '.join(V_ALIGN)}.")
            for x in addrs:
                ss.setAlignment(x, "|".join(h + v) if (h or v) else "left|vcenter", "replace")
            changed.append("align")
        for key, setter, clearer in (("color", "setForeground", "clearForeground"), ("background", "setBackground", "clearBackground")):
            if key in a:
                if a[key] is None:
                    for x in addrs:
                        if hasattr(ss, clearer):
                            getattr(ss, clearer)(x)
                        else:
                            getattr(ss, setter)(x, (0.0, 0.0, 0.0, 1.0) if key == "color" else (1.0, 1.0, 1.0, 1.0))
                else:
                    try:
                        rgba = C.hex_rgba(a[key])
                    except C.AddressError as e:
                        raise bad(f"{key}: {e}.")
                    for x in addrs:
                        getattr(ss, setter)(x, rgba)
                changed.append(key)
        if "displayUnit" in a:
            u = a["displayUnit"] or ""
            for x in addrs:
                try:
                    ss.setDisplayUnit(x, u)
                except Exception as e:
                    raise bad(f"displayUnit {u!r}: FreeCAD refuses it ({e}) — a unit such as mm, cm, in, deg.")
            changed.append("displayUnit")
        (r0, c0), (r1, c1) = C.parse_range(rng)
        if a.get("width") is not None:
            w = int(a["width"])
            if not 10 <= w <= 2000:
                raise bad("width: pixels, 10 … 2000.")
            for c in range(c0, c1 + 1):
                ss.setColumnWidth(C.col_name(c), w)
            changed.append("width")
        if a.get("height") is not None:
            h = int(a["height"])
            if not 10 <= h <= 1000:
                raise bad("height: pixels, 10 … 1000.")
            for r in range(r0, r1 + 1):
                ss.setRowHeight(str(r), h)
            changed.append("height")
        if "merge" in a:
            if a["merge"]:
                if ":" not in rng:
                    raise bad("merge: a range of more than one cell.")
                ss.mergeCells(rng)
            else:
                ss.splitCell(rng.split(":")[0])
            changed.append("merge")
        if not changed:
            raise bad("Nothing to format: style, align, color, background, displayUnit, width, height or merge.")
        check(f"Formatting {rng}")
        out = {"range": rng, "changed": changed, "cells": [cell_reading(ss, x, full=True) for x in addrs[:12] if ss.getContents(x)]}
        return done("sheet_format", out, ss)

    def sheet_rows(a):
        ss = find(doc(), a.get("sheet"))
        act = a.get("action")
        if act not in ("insert", "remove"):
            raise bad("action: insert or remove.")
        n = int(a.get("count", 1))
        if not 1 <= n <= 1000:
            raise bad("count: 1 … 1000.")
        if a.get("rows") is not None:
            r = int(a["rows"])
            if not 1 <= r <= C.MAX_ROW:
                raise bad(f"rows: the first row, 1 … {C.MAX_ROW}.")
            (ss.insertRows if act == "insert" else ss.removeRows)(str(r), n)
            what = f"{n} row{'s' if n > 1 else ''} at {r}"
        elif a.get("columns") is not None:
            col = str(a["columns"]).upper()
            if not C.COL.match(col):
                raise bad("columns: the first column's letters, A … ZZ.")
            (ss.insertColumns if act == "insert" else ss.removeColumns)(col, n)
            what = f"{n} column{'s' if n > 1 else ''} at {col}"
        else:
            raise bad("rows: the first row number, or columns: the first column's letters.")
        check(f"{'Inserting' if act == 'insert' else 'Removing'} {what}")
        return done("sheet_rows", {act + "ed" if act == "insert" else "removed": what, "note": "References and aliases moved with the cells, as FreeCAD does by hand."}, ss)

    def path_of(p, ext):
        p = os.path.expanduser(str(p))
        if not os.path.isabs(p):
            p = os.path.join(s.out_dir, p)
        if not os.path.splitext(p)[1]:
            p += ext
        return p

    DELIM = {"comma": ",", "tab": "\t", "semicolon": ";", ",": ",", "\t": "\t", ";": ";"}

    def sheet_import(a):
        p = path_of(a.get("path") or "", ".csv")
        if not os.path.isfile(p):
            raise ToolError("not_found", f"{p}: no such file.")
        d = DELIM.get(a.get("delimiter") or ("tab" if p.lower().endswith((".tsv", ".tab")) else "comma"))
        if d is None:
            raise bad("delimiter: comma, tab or semicolon.")
        ss = find(doc(), a["sheet"]) if a.get("sheet") else None
        if ss is None:
            ss = doc().addObject("Spreadsheet::Sheet", "Spreadsheet")
            ss.Label = s.unique(os.path.splitext(os.path.basename(p))[0])
        elif filled(ss) and not a.get("replace"):
            raise ToolError("invalid_state", f"{ss.Label} already holds cells: replace: true clears it first, or leave sheet out for a new one.")
        else:
            ss.clearAll()
        ss.importFile(p, d, '"', "\\")
        check(f"Importing {os.path.basename(p)}")
        fl = filled(ss)
        return done("sheet_import", {"from": p, "cells": len(fl), "range": C.bounds(fl), "first": [cell_reading(ss, x) for x in fl[:6]]}, ss)

    def sheet_export(a):
        ss = find(doc(), a.get("sheet"))
        p = path_of(a.get("path") or ss.Label.replace(" ", "-"), ".csv")
        d = DELIM.get(a.get("delimiter") or ("tab" if p.lower().endswith((".tsv", ".tab")) else "comma"))
        if d is None:
            raise bad("delimiter: comma, tab or semicolon.")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        ss.exportFile(p, d, '"', "\\")
        if not os.path.isfile(p):
            raise ToolError("failed", f"FreeCAD wrote nothing at {p}.")
        with open(p, encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
        return {"sheet": ss.Label, "file": p, "rows": len(lines), "bytes": os.path.getsize(p), "note": "Values as computed, not the expressions: the FreeCAD file keeps those."}

    # ---- design tables ----

    def table_make(a):
        ss = find(doc(), a["sheet"]) if a.get("sheet") else None
        if ss is None:
            ss = doc().addObject("Spreadsheet::Sheet", "Spreadsheet")
            ss.Label = s.unique(a.get("name") or "DesignTable")
        params = a.get("params") or []
        if not params or not all(isinstance(p, str) and C.NAME.match(p) and not C.is_cell(p) for p in params):
            raise bad("params: their names, letters, digits and _, not cell addresses: [\"length\", \"width\"].")
        if len(set(params)) != len(params):
            raise bad("params: each name once.")
        confs = a.get("configs") or []
        if not confs:
            raise bad("configs: [{ name, values: [one per param] }], at least one.")
        names = []
        for c in confs:
            n, v = c.get("name"), c.get("values")
            if not isinstance(n, str) or not n.strip():
                raise bad("configs: each needs a name.")
            if isinstance(v, dict):
                missing = [p for p in params if p not in v]
                if missing:
                    raise bad(f"config {n!r}: no value for {', '.join(missing)}.")
                c["values"] = [v[p] for p in params]
            elif not isinstance(v, list) or len(v) != len(params):
                raise bad(f"config {n!r}: values, one per param ({len(params)}), as a list or {{ param: value }}.")
            names.append(n)
        if len(set(names)) != len(names):
            raise bad("configs: each name once.")
        units = a.get("units") or {}
        at = addr_or_refuse(a.get("at", "A1"), "at")
        r0, c0 = C.parse(at)
        # header, the active row (r0 + 1), then one row per configuration
        span = C.range_text((r0, c0), (r0 + 1 + len(confs), c0 + len(params)))
        busy = [x for x in filled(ss) if x in set(C.cells_in(span))]
        if busy:
            raise ToolError("invalid_state", f"{span} is not empty ({', '.join(busy[:4])}…): choose another at, or another sheet.")
        for p in params:
            if ss.getCellFromAlias(p) if hasattr(ss, "getCellFromAlias") else None:
                raise ToolError("invalid_state", f"{p} is already an alias in {ss.Label}: choose another name or another sheet.")
        owner_ref = a.get("owner")
        owner = ss if owner_ref is None else next((o for o in doc().Objects if owner_ref in (o.Label, o.Name)), None)
        if owner is None:
            raise ToolError("not_found", f"owner {owner_ref!r}: no such object.")
        prop = a.get("property") or CONF_PROP
        if not C.NAME.match(prop):
            raise bad("property: letters, digits and _.")
        if prop in owner.PropertiesList:
            raise ToolError("invalid_state", f"{owner.Label} already has a {prop} property: name another (property).")
        ss.set(C.name(r0, c0), "'configuration")
        for j, p in enumerate(params):
            ss.set(C.name(r0, c0 + 1 + j), "'" + p)
        for i, c in enumerate(confs):
            r = r0 + 2 + i
            ss.set(C.name(r, c0), "'" + c["name"])
            for j, (p, v) in enumerate(zip(params, c["values"])):
                u = units.get(p, "mm" if isinstance(v, (int, float)) else "")
                try:
                    body = C.content(v)
                except C.AddressError as e:
                    raise bad(f"config {c['name']!r}, {p}: {e}.")
                if isinstance(v, (int, float)) and u:
                    body = f"={body} {u}"
                ss.set(C.name(r, c0 + 1 + j), body)
        first_conf, last_conf = r0 + 2, r0 + 1 + len(confs)
        owner.addProperty("App::PropertyEnumeration", prop, CONF_GROUP, "The configuration the design table in " + ss.Label + " drives")
        conf_rng = C.range_text((first_conf, c0), (last_conf, c0))
        # The items now, and the binding that keeps them in step with the
        # names column from the next recompute on (FreeCAD fills an
        # enumeration from its expression only then).
        setattr(owner, prop, list(names))
        owner.setExpression(f"{prop}.Enum", f"{ss.Name}.cells[<<{conf_rng}>>]")
        doc().recompute()
        ss.set(C.name(r0 + 1, c0), f"=hiddenref({owner.Name}.{prop}.String)")
        a_from, a_to = C.name(r0 + 1, c0 + 1), C.name(r0 + 1, c0 + len(params))
        cf, ct = C.col_name(c0 + 1), C.col_name(c0 + len(params))
        ss.setExpression(f".cells.Bind.{a_from}.{a_to}",
                         f"tuple(.cells; <<{cf}>> + str(hiddenref({owner.Name}.{prop}) + {first_conf}); <<{ct}>> + str(hiddenref({owner.Name}.{prop}) + {first_conf}))")
        for j, p in enumerate(params):
            try:
                ss.setAlias(C.name(r0 + 1, c0 + 1 + j), p)
            except Exception as e:
                raise bad(f"param {p!r}: FreeCAD refuses it as an alias ({e}) — choose another name.")
        start = a.get("active") or names[0]
        if start not in names:
            raise bad(f"active: one of {', '.join(names)}.")
        setattr(owner, prop, start)
        check("The design table")
        t = next(t for t in tables(doc(), ss) if t["at"] == C.name(r0 + 1, c0))
        out = table_reading(ss, t)
        out["next"] = f"param_bind {{ target: \"Pad.Length\", expression: \"{ss.Name}.{params[0]}\" }} binds a property to it; sheet_table {{ action: \"use\", config }} switches. In FreeCAD by hand: {owner.Label} → {prop}."
        return done("sheet_table", out, ss)

    def table_of(a):
        ss = find(doc(), a.get("sheet")) if a.get("sheet") or len(sheets(doc())) <= 1 else None
        cands = [(x, t) for x in ([ss] if ss is not None else sheets(doc())) for t in tables(doc(), x)]
        if a.get("at"):
            at = addr_or_refuse(a["at"], "at")
            # the active row's first cell, or the header cell above it
            cands = [(x, t) for x, t in cands if at == t["at"] or (C.parse(t["at"])[0] > 1 and at == C.offset(t["at"], -1, 0))]
        if not cands:
            raise ToolError("not_found", "No design table here: sheet_table { action: \"make\" } makes one.")
        if len(cands) > 1:
            raise bad("Several design tables: say which with sheet and at — " + ", ".join(f"{x.Label} {t['at']}" for x, t in cands) + ".")
        return cands[0]

    def table_use(a):
        ss, t = table_of(a)
        names = [n for _, n in t["configs"]]
        want = a.get("config")
        if want not in names:
            raise bad(f"config: one of {', '.join(names)}.")
        before = t["active"]
        setattr(t["owner"], t["property"], want)
        check(f"Configuration {want}")
        t = next(x for x in tables(doc(), ss) if x["at"] == t["at"])
        out = table_reading(ss, t)
        out["was"] = before
        out["bodies"] = bodies_now()
        out["drives"] = sorted({u for _, p in t["params"] for u in users_of(doc(), ss, p)})
        return done("sheet_table", out, ss)

    def table_add(a):
        ss, t = table_of(a)
        n, v = a.get("config"), a.get("values")
        names = [x for _, x in t["configs"]]
        params = [p for _, p in t["params"]]
        if not isinstance(n, str) or not n.strip() or n in names:
            raise bad(f"config: a new name (there are {', '.join(names)}).")
        if isinstance(v, dict):
            missing = [p for p in params if p not in v]
            if missing:
                raise bad(f"values: none for {', '.join(missing)}.")
            v = [v[p] for p in params]
        if not isinstance(v, list) or len(v) != len(params):
            raise bad(f"values: one per param ({', '.join(params)}), as a list or {{ param: value }}.")
        r, c = C.parse(t["at"])
        last = t["configs"][-1][0]
        new = last + 1
        if any(ss.getContents(C.name(new, c + j)) for j in range(len(params) + 1)):
            ss.insertRows(str(new), 1)
        units = a.get("units") or {}
        ss.set(C.name(new, c), "'" + n)
        for j, (p, x) in enumerate(zip(params, v)):
            # the unit the column already uses, unless told
            ref_v, ref_u, _ = value_of(ss, C.name(t["configs"][0][0], c + 1 + j))
            u = units.get(p, ref_u if isinstance(x, (int, float)) else "")
            body = C.content(x)
            ss.set(C.name(new, c + 1 + j), f"={body} {u}" if isinstance(x, (int, float)) and u else body)
        first = t["configs"][0][0]
        cur = getattr(t["owner"], t["property"])
        setattr(t["owner"], t["property"], names + [n])
        setattr(t["owner"], t["property"], cur)
        t["owner"].setExpression(f"{t['property']}.Enum", f"{ss.Name}.cells[<<{C.range_text((first, c), (new, c))}>>]")
        check(f"Configuration {n}")
        t = next(x for x in tables(doc(), ss) if x["at"] == t["at"])
        return done("sheet_table", table_reading(ss, t), ss)

    def sheet_table(a):
        act = a.get("action", "list")
        if act == "make":
            return table_make(a)
        if act == "use":
            return table_use(a)
        if act == "add":
            return table_add(a)
        if act == "list":
            out = [table_reading(x, t) for x in ([find(doc(), a["sheet"])] if a.get("sheet") else sheets(doc())) for t in tables(doc(), x)]
            return {"designTables": out} if out else {"designTables": [], "note": "None yet: sheet_table { action: \"make\", params, configs }."}
        raise bad("action: make, use, add or list.")

    cell_val = {"type": ["number", "string", "null"]}
    return [
        Tool("sheet_new", "Make an empty spreadsheet: name. Answers how expressions refer to its cells (Sheet.A1, Sheet.<alias>).", obj({"name": {"type": "string"}}), sheet_new, changes=True),
        Tool("sheet_list", "The spreadsheets: cells used, their range, aliases and what uses them, design tables. Changes nothing.", obj({}), sheet_list),
        Tool("sheet_read", "Read a sheet's cells: what each holds (content), its computed value and unit, alias and any error; range \"A1:D20\" or cells [..] to narrow; format: true adds style, alignment, colours and display unit. Shows bound ranges and design tables. Changes nothing.",
             obj({"sheet": {"type": "string"}, "range": {"type": "string"}, "cells": {"type": "array", "items": {"type": "string"}}, "format": {"type": "boolean"}}), sheet_read),
        Tool("sheet_set", "Write cells, as if typed in FreeCAD: cells { A1: 12, B1: \"=A1 * 2\", C1: \"10 mm\", D1: \"text\", E1: null (clears) }, or rows [[…], …] laid out from at (default A1); text: true keeps strings as text; aliases { B2: \"width\" } (\"\" removes) names cells for expressions elsewhere. Everything bound to them recomputes; an error anywhere is refused and nothing kept.",
             obj({"sheet": {"type": "string"}, "cells": {"type": "object", "additionalProperties": cell_val}, "at": {"type": "string"}, "rows": {"type": "array", "items": {"type": "array", "items": cell_val}}, "text": {"type": "boolean"}, "aliases": {"type": "object", "additionalProperties": {"type": "string"}}}), sheet_set, changes=True),
        Tool("sheet_clear", "Clear a range of cells (contents, alias, format). Refused if an alias there is in use, unless force.", obj({"sheet": {"type": "string"}, "range": {"type": "string"}, "force": {"type": "boolean"}}, ["range"]), sheet_clear, changes=True),
        Tool("sheet_format", "Format a range: style [bold, italic, underline] ([] clears), align [left|center|right, top|vcenter|bottom], color / background \"#rrggbb\" (null clears), displayUnit \"mm\" | \"in\" | \"deg\" …, width (column px), height (row px), merge true | false.",
             obj({"sheet": {"type": "string"}, "range": {"type": "string"}, "style": {"type": "array", "items": {"type": "string"}}, "align": {"type": ["array", "string"], "items": {"type": "string"}}, "color": {"type": ["string", "null"]}, "background": {"type": ["string", "null"]}, "displayUnit": {"type": ["string", "null"]}, "width": {"type": "integer"}, "height": {"type": "integer"}, "merge": {"type": "boolean"}}, ["range"]), sheet_format, changes=True),
        Tool("sheet_rows", "Insert or remove rows or columns: action insert | remove, rows (the first row number) or columns (the first column's letters), count. References move with the cells.",
             obj({"sheet": {"type": "string"}, "action": {"type": "string", "enum": ["insert", "remove"]}, "rows": {"type": "integer"}, "columns": {"type": "string"}, "count": {"type": "integer"}}, ["action"]), sheet_rows, changes=True),
        Tool("sheet_import", "Read a CSV / TSV file into a sheet: path (in the out folder unless absolute), delimiter comma | tab | semicolon; sheet to fill one (replace: true if it holds cells), else a new sheet named after the file.",
             obj({"path": {"type": "string"}, "sheet": {"type": "string"}, "delimiter": {"type": "string"}, "replace": {"type": "boolean"}}, ["path"]), sheet_import, changes=True),
        Tool("sheet_export", "Write a sheet's computed values to a CSV / TSV file: path (default <sheet>.csv in the out folder), delimiter. Changes nothing in the document.",
             obj({"sheet": {"type": "string"}, "path": {"type": "string"}, "delimiter": {"type": "string"}}), sheet_export),
        Tool("sheet_table", "Design tables (configurations), FreeCAD's own kind: action make { params [names], configs [{ name, values [..] | { param: value } }], units { param: \"mm\" | \"deg\" | \"\" } (numbers default mm), sheet (else a new one), at, owner (the object carrying the switch; default the sheet), property (default Configuration), active } — then bind properties to <Sheet>.<param> with param_bind; use { config } switches and answers the bodies; add { config, values } adds a row; list.",
             obj({"action": {"type": "string", "enum": ["make", "use", "add", "list"]}, "sheet": {"type": "string"}, "name": {"type": "string"}, "at": {"type": "string"}, "params": {"type": "array", "items": {"type": "string"}},
                  "configs": {"type": "array", "items": {"type": "object"}}, "units": {"type": "object", "additionalProperties": {"type": "string"}}, "owner": {"type": "string"}, "property": {"type": "string"}, "active": {"type": "string"},
                  "config": {"type": "string"}, "values": {"type": ["array", "object"]}}), sheet_table, changes=True),
    ]
