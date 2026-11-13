# SPDX-License-Identifier: LGPL-2.1-or-later
# A finding: one rule judged on one subject. Five states and an error state;
# nothing here ever says "passes code". The state "compliant" is kept for the
# API, but it means only "within the checked limits": every finding carries
# its scope (what this rule looked at) and not_checked (what it did not).

STATES = ("non_compliant", "manual_review_required", "insufficient_information", "error", "compliant", "not_applicable")
ORDER = {s: i for i, s in enumerate(STATES)}  # worst first
WITHIN = "within the checked limits"
LABEL = {"compliant": f"{WITHIN} (compliant)", "non_compliant": "outside the checked limits (non compliant)",
         "manual_review_required": "for an engineer's review", "insufficient_information": "needs information",
         "error": "check failed (error — never a pass)", "not_applicable": "not applicable"}
NOT_CHECKED = ["any rule not in the packs in force", "how it is built on site (an inspector's job)", "anything a licensed professional would add"]


def finding(rule, subject, state, why, **kw):
    if state not in STATES:
        raise ValueError(f"state {state!r}")
    f = {"rule": rule.get("id") if isinstance(rule, dict) else rule, "subject": subject, "state": state, "why": why}
    if isinstance(rule, dict):
        if rule.get("cite"):
            f["cite"] = rule["cite"]
        if rule.get("title"):
            f["title"] = rule["title"]
        if rule.get("_trail"):
            f["layerTrail"] = rule["_trail"]
        if rule.get("_loosened"):
            f["loosened"] = rule["_loosened"]
    for k, v in kw.items():
        if v is not None:
            f[k] = v
    if state == "compliant" and WITHIN not in f["why"]:
        f["why"] = f"{f['why']}: {WITHIN} — not an approval"
    title = (rule.get("title") if isinstance(rule, dict) else None) or f["rule"]
    f.setdefault("scope", f"only what this rule checks ({title}); a result here is {WITHIN}, never an approval")
    f.setdefault("not_checked", list(NOT_CHECKED))
    return f


def summary(findings):
    counts = {s: 0 for s in STATES}
    for f in findings:
        counts[f["state"]] += 1
    worst = sorted(findings, key=lambda f: (ORDER[f["state"]], -float(f.get("ratio") or 0)))
    return {"counts": {k: v for k, v in counts.items() if v}, "findings": worst}


def questions(findings):
    """The values that blocked checks, as plain questions: the same question
    about several subjects asked once (one answer may do for all of them,
    or each may differ), with the rules that wait on it."""
    out = {}
    for f in findings:
        for q in f.get("questions") or []:
            key = (q.get("fact"), q.get("ask"))
            row = out.setdefault(key, {"fact": q.get("fact"), "ask": q.get("ask"), "subjects": [], "rules": []})
            if q.get("subject") not in row["subjects"]:
                row["subjects"].append(q.get("subject"))
            if f["rule"] not in row["rules"]:
                row["rules"].append(f["rule"])
    return sorted(out.values(), key=lambda r: (-len(r["rules"]) * len(r["subjects"]), r["fact"] or ""))
