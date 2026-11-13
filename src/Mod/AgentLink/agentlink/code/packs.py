# SPDX-License-Identifier: LGPL-2.1-or-later
# Rule packs: plain JSON files that stack in layers. A pack holds
# paraphrased, parameterised rules with citations — never a code's own text —
# and values (design loads, frost depth …). The resolver picks the packs in
# force for a project's jurisdiction and date and folds them base to top.
# The base value says which way is stricter (stricter: higher | lower); a
# layer above may tighten, and a looser value is refused with a conflict
# unless a state or local layer loosens it explicitly (op: "loosen" with a
# citation), which every finding that uses it then shows. A jurisdiction
# may replace or switch off a rule with a citation; a contractor's layer may
# only add or tighten. Two packs of the same code and layer (two editions)
# both in force are refused, never settled by file order.

import datetime
import glob
import hashlib
import json
import math
import os
import re

LAYERS = ("model", "state", "local", "contractor")
OPS = ("add", "tighten", "loosen", "replace", "disable")
WILD = ("*", "any")
ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,120}$")


class PackError(ValueError):
    pass


def _date(s, what):
    try:
        return datetime.date.fromisoformat(str(s))
    except ValueError:
        raise PackError(f"{what}: a date, YYYY-MM-DD — not {s!r}.") from None


def validate(data, where="pack"):
    """A pack checked against its format; answers the pack, its problems
    raised as one PackError naming each."""
    probs = []
    if not isinstance(data, dict) or not isinstance(data.get("pack"), dict):
        raise PackError(f"{where}: {{ pack: {{ id, layer, jurisdiction, version, effective }}, rules: [], values: {{}} }}.")
    h = data["pack"]
    for k in ("id", "layer", "jurisdiction", "version", "effective"):
        if not h.get(k):
            probs.append(f"pack.{k}: required")
    if h.get("id") and not ID.match(str(h["id"])):
        probs.append("pack.id: lower case letters, digits, dots, dashes")
    if h.get("layer") and h["layer"] not in LAYERS:
        probs.append(f"pack.layer: one of {', '.join(LAYERS)}")
    for k in ("effective", "superseded"):
        if h.get(k):
            try:
                _date(h[k], f"pack.{k}")
            except PackError as e:
                probs.append(str(e))
    if h.get("version") and not re.match(r"^\d+\.\d+\.\d+", str(h["version"])):
        probs.append("pack.version: semver, 1.0.0")
    if h.get("jurisdiction") and str(h["jurisdiction"]).strip().lower() in WILD and h.get("layer") != "model":
        probs.append("pack.jurisdiction: \"*\" (everywhere) only on a model or example pack — a state, local or contractor pack names its place")
    if h.get("effective") and h.get("superseded"):
        try:
            if _date(h["superseded"], "s") <= _date(h["effective"], "e"):
                probs.append("pack.superseded: must be after pack.effective")
        except PackError:
            pass
    for k in ("code", "edition"):
        if k in h and not isinstance(h[k], (str, int)):
            probs.append(f"pack.{k}: a name or year (code \"IRC\", edition \"2021\")")
    _finite(data, "", probs)
    seen = set()
    for i, r in enumerate(data.get("rules") or []):
        w = f"rules[{i}]"
        if not isinstance(r, dict):
            probs.append(f"{w}: an object")
            continue
        op = r.get("op", "add")
        if "applies" in r and not isinstance(r["applies"], dict):
            probs.append(f"{w}.applies: an object {{ kind: …, key: value }}, not {type(r['applies']).__name__}")
        if "params" in r and not isinstance(r["params"], dict):
            probs.append(f"{w}.params: an object")
        if op not in OPS:
            probs.append(f"{w}.op: one of {', '.join(OPS)}")
        rid = r.get("id") if op == "add" else r.get("target")
        if not rid or not ID.match(str(rid)):
            probs.append(f"{w}.{'id' if op == 'add' else 'target'}: a rule id")
        if op == "add":
            if rid in seen:
                probs.append(f"{w}.id: {rid} twice")
            seen.add(rid)
            if not r.get("check"):
                probs.append(f"{w}.check: the check function's name")
            if not r.get("cite"):
                probs.append(f"{w}.cite: where the rule comes from ({{ code, edition, section }} or {{ source }})")
        if op in ("replace", "disable", "loosen") and h.get("layer") == "contractor":
            probs.append(f"{w}.op: a contractor's layer may add or tighten, not {op}")
        if op == "loosen" and h.get("layer") == "model":
            probs.append(f"{w}.op: loosen is for a state or local amendment, not a model pack")
        if op in ("replace", "disable", "loosen") and not r.get("cite"):
            probs.append(f"{w}.cite: a {op} names the amendment that does it")
        if "text" in r and len(str(r["text"])) > 400:
            probs.append(f"{w}.text: a pack carries a paraphrase and a citation, not a code's text")
    vals = data.get("values", {})
    if not isinstance(vals, dict):
        probs.append("values: { name: value }")
        vals = {}
    for name, v in vals.items():
        w = f"values.{name}"
        if isinstance(v, dict):
            if "value" not in v:
                probs.append(f"{w}: an object needs value ({{ value, unit, stricter }})")
                continue
            if v.get("stricter") not in (None, "higher", "lower"):
                probs.append(f"{w}.stricter: higher or lower")
            if v.get("op") not in (None, "loosen"):
                probs.append(f"{w}.op: only loosen (with cite) is an op on a value")
            if v.get("op") == "loosen":
                if h.get("layer") in ("model", "contractor"):
                    probs.append(f"{w}.op: loosen is for a state or local amendment")
                if not v.get("cite"):
                    probs.append(f"{w}.cite: a loosen names the amendment that does it")
        val = v.get("value") if isinstance(v, dict) else v
        num = isinstance(val, (int, float)) and not isinstance(val, bool)
        if num and name.startswith("load.") and val < 0:
            probs.append(f"{w}: a load is 0 or more, not {val:g}")
        if num and h.get("layer") == "model" and not (isinstance(v, dict) and v.get("stricter")):
            probs.append(f"{w}.stricter: a model pack's number says which way is stricter (higher | lower), so a layer above can be judged")
    if probs:
        raise PackError(f"{where}: " + "; ".join(probs))
    return data


def _finite(x, where, probs):
    """No Infinity or NaN anywhere in a pack."""
    if isinstance(x, float) and not math.isfinite(x):
        probs.append(f"{where or 'pack'}: {x!r} is not a finite number")
    elif isinstance(x, dict):
        for k, v in x.items():
            _finite(v, f"{where}.{k}" if where else str(k), probs)
    elif isinstance(x, list):
        for i, v in enumerate(x):
            _finite(v, f"{where}[{i}]", probs)


def _no_constant(c):
    raise PackError(f"{c} is not a number a pack may hold")


def digest(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()[:16]


def load(path):
    with open(path, encoding="utf-8") as f:
        try:
            data = json.load(f, parse_constant=_no_constant)
        except PackError as e:
            raise PackError(f"{os.path.basename(path)}: {e}") from None
        except ValueError as e:
            raise PackError(f"{os.path.basename(path)}: not JSON ({e})") from None
    validate(data, os.path.basename(path))
    data["_file"] = path
    data["_hash"] = digest({k: v for k, v in data.items() if not k.startswith("_")})
    return data


def load_dir(*dirs):
    packs, errors = [], []
    for d in dirs:
        if not d or not os.path.isdir(d):
            continue
        for p in sorted(glob.glob(os.path.join(d, "*.json"))):
            try:
                packs.append(load(p))
            except PackError as e:
                errors.append(str(e))
    return packs, errors


def applies(pack, jurisdiction, on):
    h = pack["pack"]
    j = str(h["jurisdiction"]).strip().upper()
    jurisdiction = str(jurisdiction).strip().upper()
    if not (jurisdiction == j or jurisdiction.startswith(j + "-") or j.lower() in WILD):
        return False
    if _date(h["effective"], "effective") > on:
        return False
    if h.get("superseded") and _date(h["superseded"], "superseded") <= on:
        return False
    return True


def stricter(name, rule):
    """Which way a parameter gets stricter: higher for min_*, lower for max_*,
    else as the rule says in stricter { name: higher | lower }."""
    way = (rule.get("stricter") or {}).get(name)
    if way:
        return way
    if name.startswith("min_"):
        return "higher"
    if name.startswith("max_"):
        return "lower"
    return None


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _same_place(p):
    h = p["pack"]
    return (h["layer"], str(h["jurisdiction"]).strip().upper())


def _editions(chosen):
    """Refuse two packs of one code and layer both in force (two editions),
    and two packs of one layer and place adding the same rule or setting the
    same value: which wins must never be file order."""
    probs = []
    by_code = {}
    for p in chosen:
        h = p["pack"]
        if h.get("code"):
            by_code.setdefault((str(h["code"]).strip().upper(), h["layer"], str(h["jurisdiction"]).strip().upper()), []).append(p)
    for (code, layer, jur), ps in by_code.items():
        if len(ps) > 1:
            probs.append(f"{code} at the {layer} layer for {jur}: {', '.join(_tag(p) for p in ps)} are all in force — set superseded on the older edition, or pick the edition with the project's date")
    seen_r, seen_v = {}, {}
    for p in chosen:
        place = _same_place(p)
        for r in p.get("rules") or []:
            if r.get("op", "add") == "add":
                other = seen_r.setdefault((place, r.get("id")), p)
                if other is not p:
                    probs.append(f"rule {r.get('id')} is added by both {_tag(other)} and {_tag(p)} at the {place[0]} layer for {place[1]} (two editions in force?) — set superseded on the older")
        for name in p.get("values") or {}:
            other = seen_v.setdefault((place, name), p)
            if other is not p:
                probs.append(f"value {name} is set by both {_tag(other)} and {_tag(p)} at the {place[0]} layer for {place[1]} — set superseded on the older")
    if probs:
        raise PackError("; ".join(dict.fromkeys(probs)))


def _tag(p):
    return f"{p['pack']['id']}@{p['pack']['version']}"


def resolve(packs, jurisdiction, on=None, include=None):
    """The rules and values in force: (rules {id: rule}, values {name: value},
    conflicts [], used packs []). include: pack ids a project opted into
    (a contractor's or owner's own); jurisdiction packs apply by place.
    Raises PackError when two editions of one code and layer are in force.
    A value loosened by an explicit loosen op carries loosened_by; a rule
    loosened carries _loosened."""
    on = on or datetime.date.today()
    if isinstance(on, str):
        on = _date(on, "date")
    inc = [str(i) for i in (include or [])]
    chosen = [p for p in packs if applies(p, jurisdiction, on) and (p["pack"]["layer"] != "contractor" or p["pack"]["id"] in inc)]
    # base to top; a longer jurisdiction is more local within a layer
    chosen.sort(key=lambda p: (LAYERS.index(p["pack"]["layer"]), len(str(p["pack"]["jurisdiction"])), p["pack"]["id"]))
    _editions(chosen)
    rules, values, conflicts = {}, {}, []
    for p in chosen:
        h = p["pack"]
        tag = _tag(p)
        for name, v in (p.get("values") or {}).items():
            spec = dict(v) if isinstance(v, dict) else {"value": v}
            op = spec.pop("op", None)
            old = values.get(name)
            if old is None or not (_num(spec.get("value")) and _num(old.get("value"))):
                if old is not None and h["layer"] == "contractor":
                    conflicts.append({"value": name, "pack": tag, "kept": old.get("value"), "refused": spec.get("value"), "why": "a contractor's layer may not change a jurisdiction's non-numeric value"})
                    continue
                values[name] = {**spec, "from": tag}
                continue
            way = old.get("stricter")
            if spec.get("stricter") and way and spec["stricter"] != way:
                conflicts.append({"value": name, "pack": tag, "why": f"tried to turn which way is stricter from {way} to {spec['stricter']}: the base value's direction holds"})
            if not way:
                conflicts.append({"value": name, "pack": tag, "kept": old["value"], "refused": spec["value"],
                                  "why": "the value below says no stricter direction, so an override cannot be judged: refused"})
                continue
            nv, ov = spec["value"], old["value"]
            looser = nv < ov if way == "higher" else nv > ov
            if looser:
                if op == "loosen" and spec.get("cite") and h["layer"] in ("state", "local"):
                    values[name] = {**spec, "stricter": way, "from": tag,
                                    "loosened_by": {"pack": tag, "from": ov, "to": nv, "cite": spec.get("cite"), "layer": h["layer"]}}
                else:
                    conflicts.append({"value": name, "pack": tag, "kept": ov, "refused": nv,
                                      "why": f"looser than the layer below ({way} is stricter): refused — a state or local amendment loosens only with op loosen and a cite; a contractor never"})
                continue
            values[name] = {**spec, "stricter": way, "from": tag}
        for r in p.get("rules") or []:
            op = r.get("op", "add")
            rid = r.get("id") if op == "add" else r.get("target")
            if op == "add":
                if rid in rules:
                    conflicts.append({"rule": rid, "pack": tag, "why": "added again: use tighten or replace"})
                    continue
                rules[rid] = {**r, "_trail": [f"{tag} added"]}
                continue
            base = rules.get(rid)
            if base is None:
                conflicts.append({"rule": rid, "pack": tag, "why": f"{op} of a rule not in force here"})
                continue
            if op == "disable":
                base["_disabled"] = {"by": tag, "cite": r.get("cite")}
                base["_trail"].append(f"{tag} disabled")
            elif op == "replace":
                new = {**base, **{k: v for k, v in r.items() if k not in ("op", "target")}, "id": rid}
                new["_trail"] = base["_trail"] + [f"{tag} replaced"]
                new.pop("_disabled", None)
                looser = []
                for k, v in (new.get("params") or {}).items():
                    old, way = (base.get("params") or {}).get(k), stricter(k, base)
                    if _num(v) and _num(old) and way and ((way == "higher" and v < old) or (way == "lower" and v > old)):
                        looser.append({"param": k, "from": old, "to": v, "pack": tag, "cite": r.get("cite")})
                if looser:
                    new["_loosened"] = list(base.get("_loosened") or []) + looser
                rules[rid] = new
            else:  # tighten or loosen
                params = dict(base.get("params") or {})
                for k, v in (r.get("params") or {}).items():
                    way = stricter(k, base)
                    old = params.get(k)
                    if way is None or old is None or not _num(v):
                        conflicts.append({"rule": rid, "param": k, "pack": tag, "why": f"{op} names a parameter with no known stricter way"})
                        continue
                    is_looser = (way == "higher" and v < old) or (way == "lower" and v > old)
                    if op == "tighten" and is_looser:
                        conflicts.append({"rule": rid, "param": k, "pack": tag, "kept": old, "refused": v, "why": "looser than the layer below: a tighten cannot loosen"})
                        continue
                    if op == "loosen" and is_looser:
                        base["_loosened"] = list(base.get("_loosened") or []) + [{"param": k, "from": old, "to": v, "pack": tag, "cite": r.get("cite")}]
                    params[k] = v
                base["params"] = params
                base["_trail"].append(f"{tag} {op}ed {', '.join(sorted((r.get('params') or {})))}")
    used = [f"{p['pack']['id']}@{p['pack']['version']} ({p['pack']['layer']}, {p['pack']['jurisdiction']})" for p in chosen]
    return {k: v for k, v in rules.items() if not v.get("_disabled")}, values, conflicts, used


def loosened(rules, values):
    """Every loosening in force: values loosened by an amendment, rule
    parameters loosened by a replace or loosen."""
    out = [{"value": k, **v["loosened_by"]} for k, v in sorted(values.items()) if isinstance(v, dict) and v.get("loosened_by")]
    for rid, r in sorted(rules.items()):
        for x in r.get("_loosened") or []:
            out.append({"rule": rid, **x})
    return out


SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "Agent Link rule pack",
    "type": "object",
    "required": ["pack"],
    "properties": {
        "pack": {"type": "object", "required": ["id", "layer", "jurisdiction", "version", "effective"],
                 "properties": {"id": {"type": "string"}, "title": {"type": "string"}, "layer": {"enum": list(LAYERS)},
                                "jurisdiction": {"type": "string", "description": "Hierarchical: US, US-WA, US-WA-Pierce, US-WA-Pierce-DuPont"},
                                "version": {"type": "string"}, "effective": {"type": "string", "format": "date"},
                                "superseded": {"type": "string", "format": "date", "description": "after effective"},
                                "code": {"type": "string", "description": "the code this pack paraphrases (IRC, IBC …): two packs of one code and layer may not both be in force"},
                                "edition": {"type": ["string", "integer"], "description": "the code's edition (2021)"},
                                "license": {"type": "string"}, "note": {"type": "string"}}},
        "values": {"type": "object", "additionalProperties": {"oneOf": [{"type": ["number", "string", "boolean"]},
                                                                        {"type": "object", "required": ["value"], "properties": {"value": {}, "unit": {"type": "string"},
                                                                                                                           "stricter": {"enum": ["higher", "lower"], "description": "required on a model pack's numbers; the base value's direction is the one that holds"},
                                                                                                                           "op": {"enum": ["loosen"], "description": "state or local only, with cite: a value looser than the layer below"},
                                                                                                                           "cite": {}}}]},
                   "description": "Finite numbers only (no Infinity/NaN); load.* values 0 or more."},
        "rules": {"type": "array", "items": {"type": "object", "properties": {
            "op": {"enum": list(OPS)}, "id": {"type": "string"}, "target": {"type": "string"}, "title": {"type": "string"}, "cite": {},
            "applies": {"type": "object", "description": "keys the subject must match; a key the subject holds as unknown (null) gets a question"}, "needs": {"type": "array", "items": {"type": "string"}}, "params": {"type": "object"},
            "stricter": {"type": "object"}, "check": {"type": "string"}, "tolerance": {"type": "number"},
            "ask": {"type": "string"}, "severity": {"enum": ["error", "warning", "info"]}}}},
    },
}
