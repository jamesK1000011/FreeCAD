# SPDX-License-Identifier: LGPL-2.1-or-later
# The person's slicer presets as data: read, inheritance resolved, chosen,
# exported and imported. A fake Bambu Studio preset tree; no FreeCAD.
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agentlink import slicer_presets as SP  # noqa: E402


def put(root, rel, data):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        json.dump(data, f)


class Presets(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        sysroot = os.path.join(self.tmp, "system")
        user = os.path.join(self.tmp, "user")
        put(sysroot, "BBL/machine/fdm_machine_common.json", {"type": "machine", "name": "fdm_machine_common", "instantiation": "false", "gcode_flavor": "marlin", "nozzle_diameter": ["0.4"]})
        put(sysroot, "BBL/machine/Bambu Lab H2D.json", {"type": "machine_model", "name": "Bambu Lab H2D", "nozzle_diameter": "0.4;0.2;0.6;0.8"})
        put(sysroot, "BBL/machine/Bambu Lab H2D 0.4 nozzle.json", {"type": "machine", "name": "Bambu Lab H2D 0.4 nozzle", "inherits": "fdm_machine_common", "printable_height": "325"})
        put(sysroot, "BBL/process/fdm_process_common.json", {"type": "process", "name": "fdm_process_common", "instantiation": "false", "wall_loops": "2", "sparse_infill_density": "15%", "top_shell_layers": "5", "bottom_shell_layers": "3"})
        put(sysroot, "BBL/process/0.20mm Standard @BBL H2D.json", {"type": "process", "name": "0.20mm Standard @BBL H2D", "inherits": "fdm_process_common", "layer_height": "0.2", "compatible_printers": ["Bambu Lab H2D 0.4 nozzle"]})
        put(sysroot, "BBL/filament/Bambu PLA Basic @BBL H2D.json", {"type": "filament", "name": "Bambu PLA Basic @BBL H2D", "filament_type": ["PLA"], "filament_density": ["1.26"]})
        # The person's own: finer layers, three walls, on top of the system one.
        put(user, "1234/process/My fine PLA.json", {"name": "My fine PLA", "inherits": "0.20mm Standard @BBL H2D", "layer_height": "0.12", "wall_loops": "3", "from": "User"})
        # A filament of the person's own that says nothing of its kind, as Bambu
        # Studio's user folder has them: the command line refuses it written so.
        put(user, "1234/filament/My PLA.json", {"name": "My PLA", "inherits": "Bambu PLA Basic @BBL H2D", "from": "User", "filament_density": ["1.24"]})
        os.environ["AGENTLINK_BAMBU_PRESETS"] = os.pathsep.join([user, sysroot])
        self.out = os.path.join(self.tmp, "out")

    def tearDown(self):
        os.environ.pop("AGENTLINK_BAMBU_PRESETS", None)

    def test_listing(self):
        got = SP.listing("bambustudio")
        procs = got["process"]["presets"]
        # The base presets others inherit from are not offered; the person's own first.
        self.assertEqual(procs[0], {"name": "My fine PLA", "own": True})
        self.assertNotIn("fdm_process_common", [p["name"] for p in procs])
        self.assertEqual(SP.listing("bambustudio", "machine", "h2d")["machine"]["count"], 1)

    def test_bundle_resolves_inheritance(self):
        b = SP.bundle("bambustudio", "Bambu Lab H2D 0.4 nozzle", "My fine PLA", "Bambu PLA Basic @BBL H2D")
        p = b["process"]
        self.assertEqual(p["layer_height"], "0.12")       # the person's
        self.assertEqual(p["wall_loops"], "3")            # the person's
        self.assertEqual(p["sparse_infill_density"], "15%")  # from the base, two levels up
        self.assertNotIn("inherits", p)
        self.assertEqual(b["machine"]["gcode_flavor"], "marlin")
        self.assertEqual(SP.setup_from(b), {"layerHeight": 0.12, "walls": 3, "infill": 15.0, "topLayers": 5, "bottomLayers": 3, "nozzle": 0.4})

    def test_profiles_name_the_printer(self):
        b = SP.bundle("bambustudio", "Bambu Lab H2D 0.4 nozzle", "My fine PLA", "My PLA")
        paths = SP.write_profiles(b, os.path.join(self.tmp, "w"))
        with open(paths["filament"]) as f:
            fil = json.load(f)
        self.assertEqual(fil["type"], "filament")
        self.assertIn("Bambu Lab H2D 0.4 nozzle", fil["compatible_printers"])
        with open(paths["process"]) as f:
            proc = json.load(f)
        self.assertEqual(proc["compatible_printers"].count("Bambu Lab H2D 0.4 nozzle"), 1)
        # The person's process stands on its system one; the system printer is one.
        self.assertEqual((proc["from"], proc["inherits"]), ("User", "0.20mm Standard @BBL H2D"))
        with open(paths["machine"]) as f:
            mach = json.load(f)
        self.assertEqual(mach["from"], "system")
        self.assertNotIn("_system", mach)

    def test_export_import_round_trip(self):
        b = SP.bundle("bambustudio", "Bambu Lab H2D 0.4 nozzle", "My fine PLA", "Bambu PLA Basic @BBL H2D", "H2D fine")
        name, path = SP.save(self.out, b)
        self.assertEqual(SP.load_saved(self.out, "H2D fine")["process"]["layer_height"], "0.12")
        self.assertEqual(SP.saved(self.out)[0]["machine"], "Bambu Lab H2D 0.4 nozzle")
        # Elsewhere, no Bambu presets: the bundle alone still slices.
        os.environ["AGENTLINK_BAMBU_PRESETS"] = os.path.join(self.tmp, "nowhere")
        with open(path) as f:
            again = SP.check_bundle(json.load(f))
        self.assertEqual(again["filament"]["filament_density"], ["1.26"])
        with self.assertRaises(SP.PresetError):
            SP.check_bundle({"slicer": "bambustudio"})

    def test_unknown_and_looping(self):
        with self.assertRaises(SP.PresetError):
            SP.bundle("bambustudio", "Bambu Lab H2D 0.4 nozzle", "Nope", "Bambu PLA Basic @BBL H2D")
        put(os.path.join(self.tmp, "user"), "1234/process/Loop.json", {"type": "process", "name": "Loop", "inherits": "Loop"})
        with self.assertRaises(SP.PresetError):
            SP.resolve(SP.scan("bambustudio"), "process", "Loop")

    def test_minimal_is_data(self):
        m = SP.minimal({"layerHeight": 0.16, "walls": 4, "volume": (350, 320, 325), "filament": "petg", "density": 1.27})
        self.assertEqual(m["process"]["layer_height"], "0.16")
        self.assertEqual(m["process"]["wall_loops"], "4")
        self.assertEqual(m["machine"]["printable_height"], "325")
        self.assertEqual(m["filament"]["filament_type"], ["PETG"])


if __name__ == "__main__":
    unittest.main()
