# SPDX-License-Identifier: LGPL-2.1-or-later
# Rule packs: plain JSON files that stack in layers. A pack holds
# paraphrased, parameterised rules with citations — never a code's own text —
# and values (design loads, frost depth …). The resolver picks the packs in
# force for a project's jurisdiction and date and folds them base to top:
# a stricter value wins, a looser one is refused with a conflict, a
# jurisdiction may replace or switch off a rule with a citation, and a
# contractor's layer may only add or tighten.

import datetime
import glob
import hashlib
import json
import os
import re

LAYERS = ("model", "state", "local", "contractor")
OPS = ("add", "tighten", "replace", "disable")
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
    seen = set()
    for i, r in enumerate(data.get("rules") or []):
        w = f"rules[{i}]"
        if not isinstance(r, dict):
            probs.append(f"{w}: an object")
            continue
        op = r.get("op", "add")
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
        if op in ("replace", "disable") and h.get("layer") == "contractor":
            probs.append(f"{w}.op: a contractor's layer may add or tighten, not {op}")
        if op in ("replace", "disable") and not r.get("cite"):
            probs.append(f"{w}.cite: a {op} names the amendment that does it")
        if "text" in r and len(str(r["text"])) > 400:
            probs.append(f"{w}.text: a pack carries a paraphrase and a citation, not a code's text")
    if not isinstance(data.get("values", {}), dict):
        probs.append("values: { name: value }")
    if probs:
        raise PackError(f"{where}: " + "; ".join(probs))
    return data


def digest(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()[:16]


def load(path):
    with open(path, encoding="utf-8") as f:
        try:
            data = json.load(f)
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
    j = str(h["jurisdiction"])
    if not (jurisdiction == j or jurisdiction.startswith(j + "-") or j in ("*", "any")):
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


def resolve(packs, jurisdiction, on=None, include=None):
    """The rules and values in force: (rules {id: rule}, values {name: value},
    conflicts [], used packs []). include: pack ids a project opted into
    (a contractor's or owner's own); jurisdiction packs apply by place."""
    on = on or datetime.date.today()
    if isinstance(on, str):
        on = _date(on, "date")
    chosen = [p for p in packs if applies(p, jurisdiction, on) and (p["pack"]["layer"] != "contractor" or p["pack"]["id"] in (include or []))]
    # base to top; a longer jurisdiction is more local within a layer
    chosen.sort(key=lambda p: (LAYERS.index(p["pack"]["layer"]), len(str(p["pack"]["jurisdiction"])), p["pack"]["id"]))
    rules, values, conflicts, trail_of = {}, {}, [], {}
    for p in chosen:
        h = p["pack"]
        tag = f"{h['id']}@{h['version']}"
        for name, v in (p.get("values") or {}).items():
            spec = v if isinstance(v, dict) else {"value": v}
            old = values.get(name)
            way = spec.get("stricter")
            if old is not None and way and isinstance(spec.get("value"), (int, float)) and isinstance(old["value"], (int, float)):
                looser = spec["value"] < old["value"] if way == "higher" else spec["value"] > old["value"]
                if looser and h["layer"] == "contractor":
                    conflicts.append({"value": name, "pack": tag, "kept": old["value"], "refused": spec["value"], "why": "a contractor's value may not be looser than the jurisdiction's"})
                    continue
            values[name] = {**spec, "from": tag}
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
                rules[rid] = new
            else:  # tighten
                params = dict(base.get("params") or {})
                for k, v in (r.get("params") or {}).items():
                    way = stricter(k, base)
                    old = params.get(k)
                    if way is None or old is None or not isinstance(v, (int, float)):
                        conflicts.append({"rule": rid, "param": k, "pack": tag, "why": "tighten names a parameter with no known stricter way"})
                        continue
                    if (way == "higher" and v < old) or (way == "lower" and v > old):
                        conflicts.append({"rule": rid, "param": k, "pack": tag, "kept": old, "refused": v, "why": "looser than the layer below: a tighten cannot loosen"})
                        continue
                    params[k] = v
                base["params"] = params
                base["_trail"].append(f"{tag} tightened {', '.join(sorted((r.get('params') or {})))}")
    used = [f"{p['pack']['id']}@{p['pack']['version']} ({p['pack']['layer']}, {p['pack']['jurisdiction']})" for p in chosen]
    return {k: v for k, v in rules.items() if not v.get("_disabled")}, values, conflicts, used


SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "Agent Link rule pack",
    "type": "object",
    "required": ["pack"],
    "properties": {
        "pack": {"type": "object", "required": ["id", "layer", "jurisdiction", "version", "effective"],
                 "properties": {"id": {"type": "string"}, "title": {"type": "string"}, "layer": {"enum": list(LAYERS)},
                                "jurisdiction": {"type": "string", "description": "Hierarchical: US, US-WA, US-WA-Pierce, US-WA-Pierce-DuPont"},
                                "version": {"type": "string"}, "effective": {"type": "string", "format": "date"}, "superseded": {"type": "string", "format": "date"},
                                "license": {"type": "string"}, "note": {"type": "string"}}},
        "values": {"type": "object", "additionalProperties": {"oneOf": [{"type": ["number", "string", "boolean"]},
                                                                        {"type": "object", "required": ["value"], "properties": {"value": {}, "unit": {"type": "string"}, "stricter": {"enum": ["higher", "lower"]}, "cite": {}}}]}},
        "rules": {"type": "array", "items": {"type": "object", "properties": {
            "op": {"enum": list(OPS)}, "id": {"type": "string"}, "target": {"type": "string"}, "title": {"type": "string"}, "cite": {},
            "applies": {"type": "object"}, "needs": {"type": "array", "items": {"type": "string"}}, "params": {"type": "object"},
            "stricter": {"type": "object"}, "check": {"type": "string"}, "tolerance": {"type": "number"}, "severity": {"enum": ["error", "warning", "info"]}}}},
    },
}
