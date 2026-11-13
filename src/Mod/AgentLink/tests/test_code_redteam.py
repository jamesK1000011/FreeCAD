# SPDX-License-Identifier: LGPL-2.1-or-later
# Red-team tests for agentlink/code (pure Python, no FreeCAD).
# Each test states the SAFE behaviour; each pinned a red-team finding
# (2026-11-13) against the engine before it was fixed.
import copy
import json
import os
import sys
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
from agentlink.code import engine, framing, ids, packs  # noqa: E402

SHIPPED = os.path.join(ROOT, "agentlink", "code", "packs_shipped")
with open(os.path.join(SHIPPED, "example.demo.json"), encoding="utf-8") as _f:
    DEMO = json.load(_f)
D = {"design.Fb": 6.0, "design.Fv": 1.0, "design.E": 9000}


def P(pid, layer, jur, rules=(), values=None, eff="2020-01-01", **h):
    return packs.validate({"pack": {"id": pid, "layer": layer, "jurisdiction": jur, "version": "1.0.0", "effective": eff, **h},
                           "rules": list(rules), "values": values or {}})


def demo():
    return packs.resolve([copy.deepcopy(DEMO)], "XX", "2026-11-12")[:2]


def member(**f):
    return {"id": "J", "kind": "member", "role": "joist", "facts": f}


def state(out, rule, sid="J"):
    return next(x["state"] for x in out if x["rule"] == rule and x["subject"] == sid)


class Units(unittest.TestCase):
    """F1: units on facts are stored and ignored."""

    def test_fact_unit_ft_is_converted_or_refused(self):
        rules, values = demo()
        out = engine.run(rules, values, [member(truss=False, use="floor", size="2x6", spacing=406, span={"value": 20, "unit": "ft"}, **D)])
        self.assertNotEqual(state(out, "example.framing.member"), "compliant")  # 20 ft of 2x6 cannot pass

    def test_imperial_riser_without_unit_is_not_compliant(self):
        rules, values = demo()
        out = engine.run(rules, values, [{"id": "S", "kind": "stair", "facts": {"riser_height": {"value": 8, "unit": "in"}}}])
        self.assertNotEqual(state(out, "example.stair.riser", "S"), "compliant")

    def test_implausible_span_is_questioned(self):
        rules, values = demo()  # 3.6 = metres typed where mm are meant
        out = engine.run(rules, values, [member(truss=False, use="floor", size="2x8", spacing=406, span=3.6, **D)])
        self.assertNotEqual(state(out, "example.framing.member"), "compliant")

    def test_design_values_in_psi_are_refused(self):
        rules, values = demo()
        out = engine.run(rules, values, [member(truss=False, use="floor", size="2x6", spacing=406, span=6000,
                                                **{"design.Fb": 875, "design.Fv": 135, "design.E": 1.4e6})])
        self.assertNotEqual(state(out, "example.framing.member"), "compliant")

    def test_imperial_tolerance_converted(self):
        # half a foot meant: the tolerance goes through framing.imperial with the rest (check_member itself is SI)
        m, ld, ds, t = framing.imperial({"size": "2x10", "span": 12, "spacing": 16}, {"live": 40, "dead": 10}, {"Fb": 900, "Fv": 180, "E": 1.6e6}, {"span": 0.5})
        self.assertAlmostEqual(t["span"], 152.4)
        r = framing.check_member(m, ld, ds, tol=t)
        self.assertGreater(r["range"][1] - r["range"][0], 0.05)


class Provenance(unittest.TestCase):
    """F4: assumed values that decide the gate or the loads still earn compliant."""

    def test_assumed_truss_false_not_compliant(self):
        rules, values = demo()
        out = engine.run(rules, values, [member(truss={"value": False, "provenance": "assumed"}, use="floor", size="2x8", spacing=406, span=2400, **D)])
        self.assertNotEqual(state(out, "example.framing.member"), "compliant")

    def test_assumed_use_not_compliant(self):
        rules, values = demo()
        out = engine.run(rules, values, [member(truss=False, use={"value": "attic_storage", "provenance": "assumed"}, size="2x8", spacing=406, span=3000, **D)])
        self.assertNotEqual(state(out, "example.framing.member"), "compliant")

    def test_negative_tolerance_rejected(self):
        rules, values = demo()
        sp = {"id": "R", "kind": "space", "use": "habitable", "facts": {"clear_height": {"value": 1000, "pm": -2000, "provenance": "user_supplied"}}}
        out = engine.run(rules, values, [sp])
        self.assertNotEqual(state(out, "example.space.ceiling", "R"), "compliant")

    def test_empty_values_do_not_satisfy_info_rules(self):
        rules, values = demo()
        w = {"id": "W", "kind": "wall", "facts": {"material": "", "Pset_WallCommon.FireRating": "", "Pset_WallCommon.IsExternal": {"value": None}}}
        self.assertNotEqual(state(engine.run(rules, values, [w]), "example.info.wall", "W"), "compliant")


class FailClosed(unittest.TestCase):
    """F3/F5: unknown scope and empty rule sets read as 'nothing wrong'."""

    def test_unknown_escape_flag_is_asked_not_skipped(self):
        rules, values = demo()
        op = {"id": "Bed window", "kind": "opening", "escape": None, "facts": {"clear_area": {"value": 100000}, "sill_height": {"value": 1500}}}
        out = engine.run(rules, values, [op])
        self.assertTrue(any(f["rule"].startswith("example.opening") and f["state"] == "insufficient_information" for f in out), out)

    def test_unknown_space_use_is_asked(self):
        rules, values = demo()
        out = engine.run(rules, values, [{"id": "R", "kind": "space", "use": None, "facts": {"clear_height": {"value": 1800}}}])
        self.assertTrue(out, "a room of unknown use produced no finding at all")

    def test_no_rules_in_force_is_an_error(self):
        rules, values, _, _ = packs.resolve([copy.deepcopy(DEMO)], "US-WA", "2026-11-12")
        out = engine.run(rules, values, [member(span=99999)])
        self.assertTrue(any(f["state"] == "error" for f in out), "zero rules in force must not return an empty (clean-looking) result")


class PackLayers(unittest.TestCase):
    """F6: the layering promises are not kept for values; editions collide."""

    BASE = None

    def setUp(self):
        self.base = P("irc2021", "model", "US", values={"load.live.floor": {"value": 1.92, "stricter": "higher"}})

    def test_contractor_cannot_loosen_plain_value(self):
        c = P("c1", "contractor", "US", values={"load.live.floor": 0.5})
        _, v, conf, _ = packs.resolve([self.base, c], "US-WA", "2026-11-12", include=["c1"])
        self.assertEqual(v["load.live.floor"]["value"], 1.92)
        self.assertTrue(conf)

    def test_contractor_cannot_flip_stricter(self):
        c = P("c2", "contractor", "US", values={"load.live.floor": {"value": 0.5, "stricter": "lower"}})
        _, v, _, _ = packs.resolve([self.base, c], "US-WA", "2026-11-12", include=["c2"])
        self.assertEqual(v["load.live.floor"]["value"], 1.92)

    def test_local_loosening_is_reported(self):
        loc = P("dupont", "local", "US-WA-Pierce-DuPont", values={"load.live.floor": {"value": 0.5, "stricter": "higher"}})
        _, v, conf, _ = packs.resolve([self.base, loc], "US-WA-Pierce-DuPont", "2026-11-12")
        self.assertTrue(conf or v["load.live.floor"].get("loosened_by"), "a looser local value replaced the model value with no record")

    def test_two_editions_in_force_refuse(self):
        R = lambda val: {"id": "r.h", "check": "fact.at_least", "cite": {"code": "IRC"}, "applies": {"kind": "space"}, "params": {"fact": "clear_height", "min_height_mm": val}}  # noqa: E731
        e18 = P("irc2018", "model", "US", [R(2134)], eff="2018-01-01")
        e21 = P("irc2021", "model", "US", [R(2032)], eff="2021-01-01")
        with self.assertRaises(packs.PackError):
            packs.resolve([e21, e18], "US-WA", "2026-11-12")

    def test_wildcard_local_pack_refused(self):
        with self.assertRaises(packs.PackError):
            P("anything", "local", "*", [{"op": "disable", "target": "r.h", "cite": {"source": "x"}}])

    def test_superseded_before_effective_refused(self):
        with self.assertRaises(packs.PackError):
            P("z", "model", "XX", eff="2020-01-01", superseded="2019-01-01")


class MalformedPacks(unittest.TestCase):
    """F8: a pack that validates can still crash the run or pass everything."""

    def test_applies_not_object_rejected(self):
        with self.assertRaises(packs.PackError):
            P("b", "model", "XX", [{"id": "x", "check": "fact.at_least", "cite": {"s": 1}, "applies": ["kind", "space"], "params": {"fact": "a", "min_a": 1}}])

    def test_infinite_limit_rejected(self):
        bad = copy.deepcopy(DEMO)
        next(r for r in bad["rules"] if r["id"] == "example.stair.riser")["params"]["max_height_mm"] = float("inf")
        with self.assertRaises(packs.PackError):
            packs.validate(bad)

    def test_negative_load_rejected(self):
        bad = copy.deepcopy(DEMO)
        bad["values"]["load.live.floor"] = {"value": -50, "unit": "kPa"}
        with self.assertRaises(packs.PackError):
            packs.validate(bad)

    def test_value_without_value_rejected(self):
        with self.assertRaises(packs.PackError):
            P("v", "model", "XX", values={"load.live.floor": {"unit": "kPa"}})

    def test_ids_attribute_escaping(self):
        import xml.dom.minidom as md

        r = {"x": {"check": "info.required", "applies": {"kind": "wall"}, "params": {"facts": ["Pset_A.B"]}, "title": 't"><x/>', "cite": {"a": '"'}}}
        xml, _ = ids.write(r, "T")
        md.parseString(xml.encode())  # raises ExpatError today: escape() leaves " in attributes


class Framing(unittest.TestCase):
    """F9: NDS factors defaulted the unconservative way; D-only combination missing."""

    def test_cr_not_applied_to_timbers(self):
        r = framing.check_member({"size": "6x12", "span": 4000, "spacing": 600}, {"live": 1.9, "dead": 0.5}, {"Fb": 6, "Fv": 1, "E": 9000})
        self.assertEqual(r["factors"].get("Cr", 1.0), 1.0)

    def test_dead_only_combination_checked(self):
        r = framing.check_member({"size": "2x8", "span": 3000, "spacing": 400}, {"live": 0.1, "dead": 4.0}, {"Fb": 6, "Fv": 1, "E": 9000})
        # D alone with CD 0.9 governs here: 1.247 * (4.0/4.1) / 0.9 = 1.352
        self.assertGreaterEqual(r["ratios"]["bending"], 1.35)

    def test_zero_depth_after_tolerance_is_value_error(self):
        with self.assertRaises(ValueError):  # ZeroDivisionError today, which code_span does not catch
            framing.check_member({"b": 38, "d": 10, "span": 5000, "spacing": 400}, {"live": 1.9, "dead": 0.5}, {"Fb": 6, "Fv": 1, "E": 9000}, tol={"d": 10})


class Regression(unittest.TestCase):
    """Things that are right today and should stay right."""

    def test_spf2_2x8_at_16_matches_irc_table(self):
        # IRC 2021 Table R502.3.1(2), 40/10 psf, L/360: SPF No.2 2x8 @ 16 in = 12 ft 3 in (deflection governs).
        # NDS 2018 Supplement Table 4A: Fb 875, Fv 135, E 1.4e6, Fc_perp 425 psi; CF 1.2 (2x8 Fb).
        m, ld, ds = framing.imperial({"size": "2x8", "span": 12.25, "spacing": 16}, {"live": 40, "dead": 10}, {"Fb": 875, "Fv": 135, "E": 1.4e6, "Fc_perp": 425})
        r = framing.check_member(m, ld, ds, {"CF": 1.2})
        self.assertEqual(r["governs"], "deflection_live")
        self.assertAlmostEqual(r["ratios"]["deflection_live"], 1.0, delta=0.02)

    def test_unknown_check_is_error_not_pass(self):
        out = engine.run({"x": {"id": "x", "check": "nope", "applies": {}}}, {}, [member()])
        self.assertEqual(out[0]["state"], "error")


if __name__ == "__main__":
    unittest.main()
