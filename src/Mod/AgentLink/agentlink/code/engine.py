# SPDX-License-Identifier: LGPL-2.1-or-later
# The check engine: rules in force (packs.resolve) judged on facts. A fact is
# { value, unit, provenance: measured | user_supplied | assumed | derived,
# pm (plus-minus), source }, held per subject (a member, a space, an
# opening, the project). Each check is a function (rule, subject, facts,
# values) -> finding; a value it needs and lacks becomes a question, a value
# within its tolerance of the limit an engineer's call, and an assumed value
# never earns compliant on its own.

from . import framing
from .findings import finding

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


def matches(rule, subj):
    ap = rule.get("applies") or {}
    for k, want in ap.items():
        have = subj.get(k)
        if have is None:
            have = (get(subj.get("facts", {}), k) or {}).get("value")
        if isinstance(want, list):
            if have not in want:
                return False
        elif have != want:
            return False
    return True


def missing(rule, subj, names):
    return [n for n in names if get(subj.get("facts", {}), n) is None]


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
    lim = next((v for k, v in p.items() if k.startswith("min_" if which == "fact.at_least" else "max_")), None)
    if not name or lim is None:
        return finding(rule, subj["id"], "error", f"the rule's params need fact and a {'min_' if which == 'fact.at_least' else 'max_'} value")
    f = get(subj.get("facts", {}), name)
    if f is None:
        return finding(rule, subj["id"], "insufficient_information", f"{name} is not known", questions=[ask(subj["id"], name, rule.get("ask") or f"measure {name}")])
    v, pm = float(f["value"]), float(f.get("pm") or 0)
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
    gone = missing(rule, subj, names)
    if gone:
        return finding(rule, subj["id"], "insufficient_information", f"missing: {', '.join(gone)}", questions=[ask(subj["id"], n, f"give {n}") for n in gone])
    return finding(rule, subj["id"], "compliant", "all present")


@check("framing.spacing")
def _spacing(rule, subj, values):
    r = dict(rule)
    r["params"] = {"fact": "spacing", **(rule.get("params") or {})}
    return CHECKS["fact.at_most"](r, subj, values)


def _loads(use, values, params):
    """kPa by use from the values in force: load.live.<use>, load.dead.<use>,
    snow from load.snow.ground times the roof factor for roofs."""
    def val(name):
        v = values.get(name)
        return float(v["value"]) if isinstance(v, dict) else (float(v) if v is not None else None)
    live, dead = val(f"load.live.{use}"), val(f"load.dead.{use}")
    snow = None
    if use in ("roof", "rafter") and val("load.snow.ground") is not None:
        snow = val("load.snow.ground") * float(params.get("snow_factor", val("load.snow.roof_factor") or 0.7))
    return live, dead, snow


@check("framing.member")
def _member(rule, subj, values):
    facts = subj.get("facts", {})
    sid = subj["id"]
    p = rule.get("params") or {}
    if (get(facts, "truss") or {}).get("value") or subj.get("role") == "truss":
        return finding(rule, sid, "manual_review_required",
                       "part of an engineered truss: cutting, re-spanning or loading it beyond its design needs the truss maker's repair detail or an engineer's redesign",
                       gate="truss")
    need = ["span", "spacing", "use"]
    if get(facts, "size") is None and (get(facts, "b") is None or get(facts, "d") is None):
        need.append("size")
    gone = [n for n in need if get(facts, n) is None and not (n == "size" and get(facts, "size"))]
    design = {k: (get(facts, f"design.{k}") or {}).get("value") for k in ("Fb", "Fv", "E", "Fc_perp")}
    if any(design[k] is None for k in ("Fb", "Fv", "E")):
        gone.append("design values")
    qs = []
    for n in gone:
        if n == "design values":
            qs.append(ask(sid, "design.Fb/Fv/E", "the species and grade on the stamp, and its reference Fb, Fv, E (and Fc_perp) from the grade's table or the supplier — none are assumed"))
        else:
            qs.append(ask(sid, n, {"span": "the clear span between supports, mm", "spacing": "the on-centre spacing, mm", "use": "what the member carries: floor, attic_storage, habitable_attic, roof",
                                    "size": "the member's size (2x8) or its width and depth, mm"}.get(n, n)))
    if get(facts, "truss") is None:
        qs.append(ask(sid, "truss", "is this member part of an engineered (manufactured) truss? yes / no"))
    if gone:
        return finding(rule, sid, "insufficient_information", f"needs {', '.join(gone)}", questions=qs)
    use = get(facts, "use")["value"]
    live, dead, snow = _loads(use, values, p)
    if live is None and snow is None or dead is None:
        return finding(rule, sid, "insufficient_information", f"no design loads for use {use!r} in the packs in force",
                       questions=[ask("project", f"load.live.{use}", f"the live and dead loads for {use} (kPa) where this project is")])
    member = {"span": get(facts, "span")["value"], "spacing": get(facts, "spacing")["value"]}
    if get(facts, "size"):
        member["size"] = get(facts, "size")["value"]
    else:
        member["b"], member["d"] = get(facts, "b")["value"], get(facts, "d")["value"]
    if get(facts, "plies"):
        member["plies"] = get(facts, "plies")["value"]
    factors = {k: (get(facts, f"factor.{k}") or {}).get("value") for k in framing.FACTORS}
    factors = {k: v for k, v in factors.items() if v is not None}
    if "CD" not in factors:
        factors["CD"] = float(p.get(f"cd_{'snow' if snow else 'live'}", 1.15 if snow else 1.0))
    limits = {"live": p.get("min_span_ratio_live", 360), "total": p.get("min_span_ratio_total", 240)}
    tol = {"span": (get(facts, "span") or {}).get("pm") or 0, "d": (get(facts, "d") or {}).get("pm") or 0}
    try:
        res = framing.check_member(member, {"live": live or 0, "dead": dead, "snow": snow or 0}, design, factors, limits, tol)
    except (ValueError, KeyError) as e:
        return finding(rule, sid, "error", f"the calculation would not run: {e}")
    state = framing.verdict(res)
    used = ["span", "spacing", "size", "design.Fb", "design.Fv", "design.E"]
    extra = {"ratio": res["ratio"], "range": res["range"], "governs": res["governs"], "ratios": res["ratios"], "detail": res["detail"],
             "loads_kPa": {"live": live, "dead": dead, "snow": snow}, "factors": res["factors"], "limits": limits}
    state, extra = _bound(rule, subj, state, used, extra)
    if state == "compliant" and get(facts, "truss") is None:
        state = "manual_review_required"
        extra["questions"] = [ask(sid, "truss", "is this member part of an engineered (manufactured) truss? yes / no")]
        extra["note"] = "sized fine as a plain member; whether it is a truss member decides if this may be judged at all"
    why = {"compliant": f"{res['governs']} governs at {res['ratio']:.2f} of the allowed",
           "non_compliant": f"{res['governs']} at {res['ratio']:.2f} of the allowed: too small or too long for {use}",
           "manual_review_required": (f"rests on assumed {', '.join(extra['assumed'])}" if extra.get("assumed") else extra.get("note") or
                                      f"{res['governs']} between {res['range'][0]:.2f} and {res['range'][1]:.2f} of the allowed within the measurement tolerance")}[state]
    return finding(rule, sid, state, why, **extra)


def run(rules, values, subjects, only=None):
    """Every rule in force on every subject it applies to."""
    out = []
    for rid, rule in sorted(rules.items()):
        if only and not any(t in (rule.get("tags") or []) or t == rid for t in only):
            continue
        fn = CHECKS.get(rule.get("check"))
        hits = [s for s in subjects if matches(rule, s)]
        if fn is None:
            out.append(finding(rule, "-", "error", f"no check named {rule.get('check')!r} in this engine (update Agent Link?)"))
            continue
        for s in hits:
            try:
                out.append(fn(rule, s, values))
            except Exception as e:  # a check's own failure is reported, never taken as a pass
                out.append(finding(rule, s.get("id"), "error", f"{type(e).__name__}: {e}"))
    return out
