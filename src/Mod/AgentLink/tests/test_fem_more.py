# SPDX-License-Identifier: LGPL-2.1-or-later
# The rest of FEM headless: every kind fem_add knows goes into an analysis
# (or says this FreeCAD lacks it), refinements on the Gmsh mesh, equations
# on Elmer, and stress linearization worked on a known field.
import json
import unittest

from _fc import HAVE, add_body, caller, server

try:
    import ObjectsFem  # noqa: F401

    FEM = HAVE
except Exception:
    FEM = False


@unittest.skipUnless(FEM, "FreeCAD with FEM is not importable here")
class FemMore(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import FreeCAD
        import Part

        cls.session, srv = server()
        cls.call = staticmethod(caller(srv))
        FreeCAD.newDocument("FemMore")
        cls.session.switch(FreeCAD.ActiveDocument)
        add_body(cls.session, "Bar", Part.makeBox(100, 20, 10))
        for name, args in (("fea_setup", {"body": "Bar", "material": "pla", "mesh": 5}), ("fea_hold", {"faces": {"normal": [-1, 0, 0]}}),
                           ("fea_load", {"faces": {"normal": [1, 0, 0]}, "force": [0, 0, -100]}), ("fea_native", {"solver": "elmer", "equations": ["elasticity"], "name": "Bar study"})):
            err, out = cls.call(name, args)
            assert not err, (name, out)

    def test_1_every_kind(self):
        from agentlink.families.fem_more import KINDS

        added, missing = [], []
        for kind, (fn, need) in KINDS.items():
            if kind.startswith("solver_"):
                continue
            args = {"kind": kind}
            if kind in ("spring", "rigid_body", "bearing", "gear", "pulley", "plane_rotation", "transform", "section_print", "displacement", "temperature", "heatflux", "fluid_boundary", "flow_velocity",
                        "electromagnetic", "electrostatic_potential", "magnetization", "current_density", "electric_charge_density", "contact", "tie"):
                args["refs"] = ["Bar.Face2"] if kind not in ("contact", "tie") else ["Bar.Face1", "Bar.Face2"]
            if kind.startswith("mesh_") and kind not in ("mesh_advanced", "mesh_manipulate", "mesh_distance", "mesh_shape"):
                args["refs"] = ["Bar.Face2"] if "volume" not in kind else ["Bar.Solid1"]
            err, out = self.call("fem_add", args)
            if err and out["error"]["code"] == "unavailable":
                missing.append(kind)
                continue
            self.assertFalse(err, f"{kind}: {out}")
            added.append(kind)
        print("\nfem_add:", json.dumps({"added": added, "notInThisFreeCAD": missing}))
        self.assertGreater(len(added), 30)

    def test_2_props_and_solvers(self):
        sp = self.call("fem_add", {"kind": "spring", "refs": ["Bar.Face2"], "props": {"NormalStiffness": 1000}})
        self.assertFalse(sp[0], sp[1])
        for kind in ("solver_z88", "solver_mystran"):
            err, out = self.call("fem_add", {"kind": kind})
            if err:
                self.assertEqual(out["error"]["code"], "unavailable", out)

    def test_3_linearize_a_known_field(self):
        # A fake CalculiX-style result: stress = 10 + 4·z through a 10 mm wall.
        import Fem
        import FreeCAD

        fm = Fem.FemMesh()
        nid = 1
        ids, vm = [], []
        for x in (0, 5):
            for y in (0, 5):
                for z in range(0, 11):
                    fm.addNode(x, y, z, nid)
                    ids.append(nid)
                    vm.append(10 + 4 * z)
                    nid += 1
        mo = self.session.doc.addObject("Fem::FemMeshObject", "FakeMesh")
        mo.FemMesh = fm
        res = ObjectsFem.makeResultMechanical(self.session.doc, "FakeResult")
        res.Mesh = mo
        res.NodeNumbers = ids
        res.vonMises = vm
        out = self.call("fem_linearize", {"result": "FakeResult", "from": [0, 0, 0], "to": [0, 0, 10], "points": 11})
        self.assertFalse(out[0], out[1])
        r = out[1]
        self.assertAlmostEqual(r["membrane"], 30.0, delta=0.01)
        self.assertAlmostEqual(r["bending"], 20.0, delta=0.01)
        del FreeCAD


if __name__ == "__main__":
    unittest.main()
