# SPDX-License-Identifier: LGPL-2.1-or-later
# The building-code engine without FreeCAD: packs, layers, a wood member by
# hand, and the five states.
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from agentlink.code import engine, framing, packs  # noqa: E402
from agentlink.code.findings import questions, summary  # noqa: E402

HERE = os.path.join(os.path.dirname(__file__), "..", "agentlink", "code", "packs_shipped")


def pack(pid, layer, jur, rules=(), values=None, eff="2020-01-01", **h):
    return packs.validate({"pack": {"id": pid, "layer": layer, "jurisdiction": jur, "version": "1.0.0", "effective": eff, **h},
                           "rules": list(rules), "values": values or {}})


BASE = {"id": "r.height", "check": "fact.at_least", "cite": {"source": "t"}, "applies": {"kind": "space"}, "params": {"fact": "clear_height", "min_height_mm": 2100}}


class Packs(unittest.TestCase):
    def test_shipped_example_loads(self):
        ps, errs = packs.load_dir(HERE)
        self.assertEqual(errs, [])
        self.assertEqual([p["pack"]["id"] for p in ps], ["example.demo"])

    def test_bad_packs_say_why(self):
        with self.assertRaises(packs.PackError) as e:
            packs.validate({"pack": {"id": "x", "layer": "planet"}, "rules": [{"id": "a"}]})
        for word in ("pack.layer", "jurisdiction", "check", "cite"):
            self.assertIn(word, str(e.exception))
        with self.assertRaises(packs.PackError):
            pack("c", "contractor", "US", [{"op": "disable", "target": "r.height", "cite": {"source": "me"}}])

    def test_layers_tighten_not_loosen(self):
        ps = [pack("m", "model", "US", [BASE]),
              pack("s", "state", "US-WA", [{"op": "tighten", "target": "r.height", "params": {"min_height_mm": 2200}}]),
              pack("c", "contractor", "US", [{"op": "tighten", "target": "r.height", "params": {"min_height_mm": 2000}}])]
        rules, values, conflicts, used = packs.resolve(ps, "US-WA-Pierce", "2026-11-12", include=["c"])
        self.assertEqual(rules["r.height"]["params"]["min_height_mm"], 2200)
        self.assertEqual(len(conflicts), 1)
        self.assertIn("looser", conflicts[0]["why"])
        # elsewhere the state pack does not apply, and the contractor's only when opted in
        rules, *_ = packs.resolve(ps, "US-OR", "2026-11-12")
        self.assertEqual(rules["r.height"]["params"]["min_height_mm"], 2100)

    def test_dates_and_disable(self):
        ps = [pack("m", "model", "US", [BASE]), pack("l", "local", "US-WA-Pierce", [{"op": "disable", "target": "r.height", "cite": {"source": "ord. 1"}}], eff="2027-01-01")]
        self.assertIn("r.height", packs.resolve(ps, "US-WA-Pierce", "2026-06-01")[0])
        self.assertNotIn("r.height", packs.resolve(ps, "US-WA-Pierce", "2027-06-01")[0])


class Member(unittest.TestCase):
    def test_bending_by_hand(self):
        # 38 x 184 at 406 mm over 3658 mm, 2.4 kPa: w 0.9744 N/mm, M 1.6298 kNm, S 214421 mm3, fb 7.60 MPa; Fb' 6.0 x Cr 1.15
        r = framing.check_member({"size": "2x8", "span": 3658, "spacing": 406}, {"live": 1.92, "dead": 0.48}, {"Fb": 6.0, "Fv": 1.0, "E": 9000})
        self.assertAlmostEqual(r["ratios"]["bending"], 7.6008 / 6.9, delta=0.002)
        self.assertEqual(r["governs"], max(r["ratios"], key=r["ratios"].get))
        # live deflection: 5 w L^4 / 384 E I against L/360
        I = 38 * 184 ** 3 / 12
        dl = 5 * 1.92e-3 * 406 * 3658 ** 4 / (384 * 9000 * I)
        self.assertAlmostEqual(r["ratios"]["deflection_live"], dl / (3658 / 360), delta=0.002)

    def test_imperial_and_tolerance(self):
        m, ld, ds = framing.imperial({"size": "2x10", "span": 12, "spacing": 16}, {"live": 40, "dead": 10}, {"Fb": 900, "Fv": 180, "E": 1.6e6})
        self.assertAlmostEqual(m["span"], 3657.6)
        r = framing.check_member(m, ld, ds, tol={"span": 100, "d": 3})
        self.assertLess(r["range"][0], r["ratio"])
        self.assertGreater(r["range"][1], r["ratio"])


class Engine(unittest.TestCase):
    def setUp(self):
        ps, _ = packs.load_dir(HERE)
        self.rules, self.values, _, _ = packs.resolve(ps, "XX", "2026-11-12")

    def subj(self, **facts):
        return {"id": "Joist 1", "kind": "member", "role": "joist", "facts": facts}

    def test_missing_values_become_questions(self):
        out = engine.run(self.rules, self.values, [self.subj(span={"value": 3658, "provenance": "user_supplied"})])
        f = next(x for x in out if x["rule"] == "example.framing.member")
        self.assertEqual(f["state"], "insufficient_information")
        asks = {q["fact"] for q in questions(out)}
        self.assertTrue({"spacing", "use", "size", "design.Fb/Fv/E", "truss"} <= asks)

    def test_truss_gate(self):
        out = engine.run(self.rules, self.values, [self.subj(truss=True, span=3000, spacing=600, use="attic_storage", size="2x4")])
        f = next(x for x in out if x["rule"] == "example.framing.member")
        self.assertEqual(f["state"], "manual_review_required")
        self.assertEqual(f["gate"], "truss")

    def test_states(self):
        d = {"design.Fb": 6.0, "design.Fv": 1.0, "design.E": 9000}
        ok = self.subj(truss=False, span=2400, spacing=406, use="floor", size="2x8", **d)
        bad = dict(self.subj(truss=False, span=6000, spacing=610, use="floor", size="2x6", **d), id="Joist 2")
        assumed = dict(self.subj(truss=False, span=2400, spacing=406, use="floor", size="2x8", **{**d, "design.Fb": {"value": 6.0, "provenance": "assumed"}}), id="Joist 3")
        out = engine.run(self.rules, self.values, [ok, bad, assumed])
        st = {(f["subject"], f["rule"]): f["state"] for f in out}
        self.assertEqual(st[("Joist 1", "example.framing.member")], "compliant")
        self.assertEqual(st[("Joist 2", "example.framing.member")], "non_compliant")
        self.assertEqual(st[("Joist 3", "example.framing.member")], "manual_review_required")
        self.assertEqual(st[("Joist 2", "example.framing.spacing")], "compliant")
        s = summary(out)
        self.assertEqual(s["findings"][0]["state"], "non_compliant")

    def test_tolerance_straddle(self):
        sp = {"id": "Loft", "kind": "space", "use": "habitable_attic", "facts": {"clear_height": {"value": 2110, "pm": 25, "provenance": "user_supplied"}}}
        f = next(x for x in engine.run(self.rules, self.values, [sp]) if x["rule"] == "example.space.ceiling")
        self.assertEqual(f["state"], "manual_review_required")
        self.assertTrue(f["questions"])


if __name__ == "__main__":
    unittest.main()


class BeamFem(unittest.TestCase):
    def test_against_hand(self):
        from agentlink.code import beamfem

        if beamfem.ccx_path() is None:
            self.skipTest("ccx not installed")
        b, d, L, w, E = 38.0, 184.0, 3658.0, 0.9744, 9000.0
        r = beamfem.run(b, d, L, w, E)
        I = b * d ** 3 / 12
        hand_d = 5 * w * L ** 4 / (384 * E * I)
        hand_s = (w * L * L / 8) / (b * d * d / 6)
        print(f"\nbeam fem: {r} hand: {hand_d:.3f} mm {hand_s:.3f} MPa", file=sys.stderr)
        self.assertAlmostEqual(r["deflection_mm"], hand_d, delta=hand_d * 0.1)
        self.assertAlmostEqual(r["stress_MPa"], hand_s, delta=hand_s * 0.15)
