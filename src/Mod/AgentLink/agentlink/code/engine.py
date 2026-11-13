# SPDX-License-Identifier: LGPL-2.1-or-later
# The check engine: rules in force (packs.resolve) judged on facts. A fact is
# { value, unit, provenance: measured | user_supplied | assumed | derived,
# pm (plus-minus), source }, held per subject (a member, a space, an
# opening, the project). Each check is a function (rule, subject, facts,
# values) -> finding; a value it needs and lacks becomes a question, a value
# within its tolerance of the limit an engineer's call, and an assumed value
# on any fact a check reads never earns compliant. Facts are converted to
# one unit per kind on the way in (units.py): an unknown unit or a negative
# tolerance is an error, a value outside its plausible band a question. A
# rule whose applies key the subject holds as unknown (escape: null, use:
# "Undefined") is a question too, never silence. "compliant" means within
# the checked limits only.

import math

from . import framing, units
from .findings import finding

UNKNOWN = (None, "", "undefined", "unknown", "notdefined", "none", "null")
ASK_KEY = {"escape": "is this opening an emergency escape and rescue opening (one serving a sleeping room or basement)? yes / no",
           "use": "what is this room used for (habitable, sleeping, storage, garage …)?",
           "separates_garage": "does this separate the garage from the home? yes / no",
           "role": "what does this member do (joist, rafter, beam, column)?"}

CHECKS = {}


def check(name):
    def put(fn):
        CHECKS[name] = fn
        return fn
    return put


def get(facts, name):
    f = facts.get(name)
    if f is None:
        return None
    return f if isinstance(f, dict) and "value" in f else {"value": f, "provenance": "user_supplied"}


def ask(subject, fact, why):
    return {"subject": subject, "fact": fact, "ask": why}


def _unknown(v):
    return v is None or (isinstance(v, str) and v.strip().lower() in UNKNOWN)


def match(rule, subj):
    """("yes" | "no" | "absent" | "unknown", [keys held as unknown]). A key
    the subject holds with another value: no (not applicable). A key it does
    not carry at all: absent (silently skipped, as for a wall nobody said
    separates a garage). A key it holds as unknown (None, "Undefined"): a
    question once every known key matches."""
    ap = rule.get("applies") or {}
    if not isinstance(ap, dict):
        raise TypeError("the rule's applies is not an object")
    unknown = []
    for k, want in ap.items():
        held = k in subj or k in (subj.get("facts") or {})
        have = subj.get(k)
        if have is None:
            have = (get(subj.get("facts", {}), k) or {}).get("value")
        if _unknown(have):
            if held:
                unknown.append(k)
                continue
            return "absent", []
        if isinstance(want, list):
            if have not in want:
                return "no", []
        elif have != want:
            return "no", []
    return ("unknown", unknown) if unknown else ("yes", [])


def matches(rule, subj):
    return match(rule, subj)[0] == "yes"


def _empty(f):
    if f is None:
        return True
    v = f.get("value")
    return v is None or (isinstance(v, str) and not v.strip())


def missing(rule, subj, names):
    """Names with no value: absent, None, or an empty string."""
    return [n for n in names if _empty(get(subj.get("facts", {}), n))]


def assumed_used(subj, names):
    return [n for n in names if (get(subj.get("facts", {}), n) or {}).get("provenance") == "assumed"]


def _bound(rule, subj, state_if_ok, names, extra):
    """Downgrade a would-be compliant result resting on an assumed value."""
    if state_if_ok == "compliant":
        a = assumed_used(subj, names)
        if a:
            return "manual_review_required", {**extra, "assumed": a, "questions": [ask(subj["id"], n, f"confirm {n} (it was assumed)") for n in a]}
    return state_if_ok, extra


def _limit(rule, subj, values, which):
    p = rule.get("params") or {}
    name = p.get("fact")
    key = next((k for k in p if k.startswith("min_" if which == "fact.at_least" else "max_")), None)
    lim = p.get(key) if key else None
    if not name or lim is None or not isinstance(lim, (int, float)) or isinstance(lim, bool) or not math.isfinite(lim):
        return finding(rule, subj["id"], "error", f"the rule's params need fact and a {'min_' if which == 'fact.at_least' else 'max_'} value")
    f = get(subj.get("facts", {}), name)
    if f is None:
        return finding(rule, subj["id"], "insufficient_information", f"{name} is not known", questions=[ask(subj["id"], name, rule.get("ask") or f"measure {name}")])
    lim, _ = units.limit_in(key, float(lim))
    v, pm = float(f["value"]), abs(float(f.get("pm") or 0))
    lo, hi = v - pm, v + pm
    if which == "fact.at_least":
        state = "compliant" if lo >= lim else "non_compliant" if hi < lim else "manual_review_required"
        ratio = (lim / v) if v else None
    else:
        state = "compliant" if hi <= lim else "non_compliant" if lo > lim else "manual_review_required"
        ratio = (v / lim) if lim else None
    extra = {"values": {name: v, "pm": pm or None, "provenance": f.get("provenance")}, "limit": lim, "ratio": round(ratio, 3) if ratio is not None else None}
    if state == "manual_review_required":
        extra["questions"] = [ask(subj["id"], name, f"measure {name} more closely: {v:g} ± {pm:g} straddles the limit {lim:g}")]
    state, extra = _bound(rule, subj, state, [name], extra)
    why = {"compliant": f"{name} {v:g} meets {lim:g}", "non_compliant": f"{name} {v:g} misses {lim:g}",
           "manual_review_required": extra.get("questions", [{}])[0].get("ask", "within tolerance of the limit")}[state]
    return finding(rule, subj["id"], state, why, **extra)


CHECKS["fact.at_least"] = lambda r, s, v: _limit(r, s, v, "fact.at_least")
CHECKS["fact.at_most"] = lambda r, s, v: _limit(r, s, v, "fact.at_most")


@check("space.height_share")
def _height_share(rule, subj, values):
    """A share of a room's floor needs at least a clear height (a sloped
    attic: most of the floor at the full height, the rest lower): params
    min_height_mm, min_share (0..1); with min_any_mm, floor lower than it
    does not count toward the room at all."""
    p = rule.get("params") or {}
    f = get(subj.get("facts", {}), "height_profile")
    if f is None:
        return finding(rule, subj["id"], "insufficient_information", "the room's heights are not known",
                       questions=[ask(subj["id"], "height_profile", "model the room as a space (bim_space) under its roof, or give its lowest and highest clear heights")])
    prof = {float(k): float(v) for k, v in f["value"].items()}
    hs = sorted(prof)

    def share(h):
        if h <= hs[0]:
            return prof[hs[0]]
        if h >= hs[-1]:
            return prof[hs[-1]] if h == hs[-1] else 0.0
        for a, b in zip(hs, hs[1:]):
            if a <= h <= b:
                return prof[a] + (prof[b] - prof[a]) * (h - a) / (b - a)
        return 0.0
    need_h, need_s = float(p.get("min_height_mm", 0)), float(p.get("min_share", 0.5))
    base = share(float(p["min_any_mm"])) if p.get("min_any_mm") else 1.0
    got = share(need_h) / base if base else 0.0
    tol = float(rule.get("tolerance") or 25)
    lo, hi = share(need_h + tol) / base if base else 0, share(max(need_h - tol, 0)) / base if base else 0
    state = "compliant" if lo >= need_s else "non_compliant" if hi < need_s else "manual_review_required"
    extra = {"share": round(got, 3), "limit": need_s, "height_mm": need_h, "ratio": round(need_s / got, 3) if got else None}
    if state == "manual_review_required":
        extra["questions"] = [ask(subj["id"], "height_profile", f"measure the ceiling heights carefully: {got:.0%} of the floor has {need_h:g} mm, the limit is {need_s:.0%}")]
    state, extra = _bound(rule, subj, state, ["height_profile"], extra)
    return finding(rule, subj["id"], state, f"{got:.0%} of the counted floor has at least {need_h:g} mm (needs {need_s:.0%})", **extra)


@check("info.required")
def _required(rule, subj, values):
    names = (rule.get("params") or {}).get("facts") or rule.get("needs") or []
    if not isinstance(names, list) or not names:
        return finding(rule, subj["id"], "error", "the rule names no facts to require (params.facts)")
    gone = missing(rule, subj, names)
    if gone:
        def wording(n):
            if "." in n and n.split(".", 1)[0].lower().startswith(("pset", "qto")):
                return f"its {n.split('.', 1)[1]} ({n}): ask the person, then set it with bim_ifc properties"
            if n == "material":
                return "what it is made of: bim_material, or code_answer"
            return f"its {n.replace('_', ' ')}"
        return finding(rule, subj["id"], "insufficient_information", f"missing: {', '.join(gone)}", questions=[ask(subj["id"], n, wording(n)) for n in gone])
    return finding(rule, subj["id"], "compliant", f"all present: {', '.join(names)}")


@check("framing.spacing")
def _spacing(rule, subj, values):
    r = dict(rule)
    r["params"] = {"fact": "spacing", **(rule.get("params") or {})}
    return CHECKS["fact.at_most"](r, subj, values)


def _loads(use, values, params, defaults=None):
    """kPa by use from the values in force: load.live.<use>, load.dead.<use>,
    snow from load.snow.ground times the roof factor for roofs. A factor the
    engine fills in itself goes on defaults (a list), if given."""
    def val(name):
        return units.value_in(name, values.get(name), "kPa")
    live, dead = val(f"load.live.{use}"), val(f"load.dead.{use}")
    snow = None
    if use in ("roof", "rafter") and val("load.snow.ground") is not None:
        fac = params.get("snow_factor")
        if fac is None:
            fac = val("load.snow.roof_factor")
        if fac is None:
            fac = 0.7
            if defaults is not None:
                defaults.append({"fact": "load.snow.roof_factor", "value": 0.7, "provenance": "defaulted", "note": "roof snow over ground snow (engine default)"})
        snow = val("load.snow.ground") * float(fac)
    return live, dead, snow


MEMBER_ASK = {"span": "the clear span between supports, mm", "spacing": "the on-centre spacing, mm",
              "use": "what the member carries: floor, attic_storage, habitable_attic, roof",
              "size": "the member's size (2x8) or its width and depth, mm"}


@check("framing.member")
def _member(rule, subj, values):
    facts = subj.get("facts", {})
    sid = subj["id"]
    p = rule.get("params") or {}
    nc = list(framing.NOT_CHECKED)
    if (get(facts, "truss") or {}).get("value") is True or subj.get("role") == "truss":
        return finding(rule, sid, "manual_review_required",
                       "part of an engineered truss: cutting, re-spanning or loading it beyond its design needs the truss maker's repair detail or an engineer's redesign",
                       gate="truss", not_checked=nc)
    need = ["span", "spacing", "use"]
    if _empty(get(facts, "size")) and (_empty(get(facts, "b")) or _empty(get(facts, "d"))):
        need.append("size")
    gone = [n for n in need if _empty(get(facts, n))]
    design = {k: (get(facts, f"design.{k}") or {}).get("value") for k in ("Fb", "Fv", "E", "Fc_perp")}
    if any(design[k] is None for k in ("Fb", "Fv", "E")):
        gone.append("design values")
    qs = []
    for n in gone:
        if n == "design values":
            qs.append(ask(sid, "design.Fb/Fv/E", "the species and grade on the stamp, and its reference Fb, Fv, E (and Fc_perp) from the grade's table or the supplier — none are assumed"))
        else:
            qs.append(ask(sid, n, MEMBER_ASK.get(n, n)))
    if get(facts, "truss") is None:
        qs.append(ask(sid, "truss", "is this member part of an engineered (manufactured) truss? yes / no"))
    if gone:
        return finding(rule, sid, "insufficient_information", f"needs {', '.join(gone)}", questions=qs, not_checked=nc)
    use = get(facts, "use")["value"]
    defaults = []
    try:
        live, dead, snow = _loads(use, values, p, defaults)
    except units.UnitError as e:
        return finding(rule, sid, "error", f"a load in the packs in force: {e}", not_checked=nc)
    if live is None and snow is None or dead is None:
        return finding(rule, sid, "insufficient_information", f"no design loads for use {use!r} in the packs in force",
                       questions=[ask("project", f"load.live.{use}", f"the live and dead loads for {use} (kPa) where this project is")], not_checked=nc)
    member = {"span": get(facts, "span")["value"], "spacing": get(facts, "spacing")["value"]}
    if not _empty(get(facts, "size")):
        member["size"] = get(facts, "size")["value"]
    else:
        member["b"], member["d"] = get(facts, "b")["value"], get(facts, "d")["value"]
    if get(facts, "plies"):
        member["plies"] = get(facts, "plies")["value"]
    if get(facts, "bearing"):
        member["bearing"] = get(facts, "bearing")["value"]
    factors = {k: (get(facts, f"factor.{k}") or {}).get("value") for k in framing.FACTORS}
    factors = {k: v for k, v in factors.items() if v is not None}
    if "CD" not in factors:
        key = f"cd_{'snow' if snow else 'live'}"
        factors["CD"] = float(p.get(key, 1.15 if snow else 1.0))
        if key not in p:
            defaults.append({"fact": "factor.CD", "value": factors["CD"], "provenance": "defaulted",
                             "note": f"load duration {factors['CD']:g} for {'snow' if snow else 'live'} load (engine default)"})
    limits = {"live": p.get("min_span_ratio_live", 360), "total": p.get("min_span_ratio_total", 240)}
    for k, pk in (("live", "min_span_ratio_live"), ("total", "min_span_ratio_total")):
        if pk not in p:
            defaults.append({"fact": f"limit.{k}", "value": limits[k], "provenance": "defaulted", "note": f"deflection limit L/{limits[k]} (engine default)"})
    tol = {"span": (get(facts, "span") or {}).get("pm") or 0, "d": (get(facts, "d") or {}).get("pm") or 0}
    try:
        res = framing.check_member(member, {"live": live or 0, "dead": dead, "snow": snow or 0}, design, factors, limits, tol)
    except (ValueError, KeyError) as e:
        return finding(rule, sid, "error", f"the calculation would not run: {e}", not_checked=nc)
    seen = {d["fact"] for d in defaults}
    defaults += [d for d in res["defaults"] if d["fact"] not in seen and not d["fact"].startswith("limit.")]
    state = framing.verdict(res)
    used = ["span", "spacing", "size", "b", "d", "plies", "bearing", "use", "truss", "design.Fb", "design.Fv", "design.E", "design.Fc_perp"] + [f"factor.{k}" for k in framing.FACTORS]
    loose = [{"value": n, **values[n]["loosened_by"]} for n in (f"load.live.{use}", f"load.dead.{use}", "load.snow.ground", "load.snow.roof_factor")
             if isinstance(values.get(n), dict) and values[n].get("loosened_by")]
    extra = {"ratio": res["ratio"], "range": res["range"], "governs": res["governs"], "governing_case": res["governing_case"], "ratios": res["ratios"],
             "cases": res["cases"], "detail": res["detail"], "loads_kPa": {"live": live, "dead": dead, "snow": snow}, "factors": res["factors"],
             "limits": limits, "defaults": defaults, "not_checked": nc, **({"loosened_values": loose} if loose else {})}
    state, extra = _bound(rule, subj, state, used, extra)
    if state == "compliant" and get(facts, "truss") is None:
        state = "manual_review_required"
        extra["questions"] = [ask(sid, "truss", "is this member part of an engineered (manufactured) truss? yes / no")]
        extra["note"] = "sized fine as a plain member; whether it is a truss member decides if this may be judged at all"
    case = " (dead load alone, CD 0.9)" if res["governing_case"] == "D" else ""
    why = {"compliant": f"{res['governs']}{case} governs at {res['ratio']:.2f} of the allowed",
           "non_compliant": f"{res['governs']}{case} at {res['ratio']:.2f} of the allowed: too small or too long for {use}",
           "manual_review_required": (f"rests on assumed {', '.join(extra['assumed'])}" if extra.get("assumed") else extra.get("note") or
                                      f"{res['governs']} between {res['range'][0]:.2f} and {res['range'][1]:.2f} of the allowed within the measurement tolerance")}[state]
    return finding(rule, sid, state, why, **extra)


def reads(rule, subj):
    """The facts a rule's check reads for a subject (for units, plausibility
    and provenance), plus its applies keys."""
    p = rule.get("params") or {}
    c = rule.get("check")
    facts = subj.get("facts") or {}
    out = []
    if c in ("fact.at_least", "fact.at_most") or (c not in CHECKS and p.get("fact")):
        out.append(p.get("fact"))
    elif c == "framing.spacing":
        out.append(p.get("fact") or "spacing")
    elif c == "space.height_share":
        out.append("height_profile")
    elif c == "framing.member":
        out += ["span", "spacing", "use", "truss", "plies", "bearing"]
        out += ["size"] if not _empty(get(facts, "size")) else ["b", "d"]
        out += [n for n in facts if n.startswith(("design.", "factor."))]
    elif c == "info.required":
        out += list(p.get("facts") or rule.get("needs") or [])
    out += [k for k in (rule.get("applies") or {}) if k != "kind" and k in facts]
    return [n for n in dict.fromkeys(out) if n]


def _prepare(subj):
    """A copy of a subject with its facts in the held units, and the problems."""
    facts, probs = units.normalize_facts(subj.get("facts") or {})
    return {**subj, "facts": facts}, probs


def _one(fn, rule, subj, probs, values):
    f = _judge(fn, rule, subj, probs, values)
    if f is not None and rule.get("check") == "framing.member":
        f["not_checked"] = list(framing.NOT_CHECKED)
    return f


def _judge(fn, rule, subj, probs, values):
    sid = subj.get("id")
    state, unknown = match(rule, subj)
    if state == "absent":
        return None
    if state == "no":
        ap = rule.get("applies") or {}
        if "kind" in ap and ap["kind"] == subj.get("kind") and len(ap) > 1:
            return finding(rule, sid, "not_applicable", "its kind matches but " + ", ".join(f"{k} is {subj.get(k, (get(subj.get('facts', {}), k) or {}).get('value'))!r}" for k in ap if k != "kind"))
        return None
    if state == "unknown":
        return finding(rule, sid, "insufficient_information", f"whether this rule applies is not known: {', '.join(unknown)}",
                       questions=[ask(sid, k, ASK_KEY.get(k, f"{k.replace('_', ' ')}: which applies here? the rule wants {rule['applies'][k]!r}")) for k in unknown])
    names = reads(rule, subj)
    bad = {n: probs[n] for n in names if n in probs}
    if rule.get("check") == "info.required":
        bad = {n: x for n, x in bad.items() if x["kind"] == "error"}
    errs = [x["why"] for x in bad.values() if x["kind"] == "error"]
    if errs:
        return finding(rule, sid, "error", "refused: " + "; ".join(errs))
    if bad:
        return finding(rule, sid, "insufficient_information", "; ".join(x["why"] for x in bad.values()),
                       questions=[ask(sid, n, x["ask"]) for n, x in bad.items()])
    f = fn(rule, subj, values)
    if f["state"] == "compliant":
        facts = subj.get("facts") or {}
        a = [n for n in names if (get(facts, n) or {}).get("provenance") == "assumed"]
        if a:
            f = finding(rule, sid, "manual_review_required", f"rests on assumed {', '.join(a)}: confirm before reading it as within the checked limits",
                        **{k: v for k, v in f.items() if k not in ("rule", "subject", "state", "why", "cite", "title", "layerTrail", "scope")},
                        assumed=a, questions=[ask(sid, n, f"confirm {n} (it was assumed)") for n in a])
    return f


def run(rules, values, subjects, only=None):
    """Every rule in force on every subject it applies to. No rules in force
    is an error finding, never an empty (clean-looking) result."""
    if not rules:
        return [finding("-", "-", "error", "no rules are in force for this project's jurisdiction and date: nothing was checked. Set the project (code_project) to a place a pack covers, or import a pack.")]
    out = []
    prepared = [_prepare(s) for s in subjects]
    for rid, rule in sorted(rules.items()):
        if only and not any(t in (rule.get("tags") or []) or t == rid for t in only):
            continue
        fn = CHECKS.get(rule.get("check"))
        if fn is None:
            out.append(finding(rule, "-", "error", f"no check named {rule.get('check')!r} in this engine (update Agent Link?)"))
            continue
        for s, probs in prepared:
            try:
                f = _one(fn, rule, s, probs, values)
            except Exception as e:  # a check's own failure is reported, never taken as a pass
                f = finding(rule, s.get("id"), "error", f"{type(e).__name__}: {e}")
            if f is not None:
                out.append(f)
    return out
