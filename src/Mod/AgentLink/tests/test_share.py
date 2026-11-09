# SPDX-License-Identifier: LGPL-2.1-or-later
# The share family. The cleaning, licences and credits are tested
# anywhere; the pack itself needs FreeCAD.
import json
import os
import struct
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
if os.environ.get("FREECAD_LIB"):
    sys.path.append(os.environ["FREECAD_LIB"])
try:
    import FreeCAD  # noqa: F401

    HAVE = True
except ImportError:
    HAVE = False

from agentlink import packing  # noqa: E402

HOME = os.path.expanduser("~")
STEP = f"""ISO-10303-21;
HEADER;
FILE_DESCRIPTION(('FreeCAD Model'),'2;1');
FILE_NAME('{HOME}/Documents/secret/Bin.step','2026-11-09T10:00:00',('Jane Q. Person'),(
    'Acme Skunkworks'),'Open CASCADE STEP processor 7.8','FreeCAD','Unknown');
FILE_SCHEMA(('AUTOMOTIVE_DESIGN {{ 1 0 10303 214 1 1 1 1 }}'));
ENDSEC;
DATA;
#1 = APPLICATION_PROTOCOL_DEFINITION('international standard','automotive_design',2000,#2);
#2 = PRODUCT('Bin','Bin','{HOME}/x',(#3));
ENDSEC;
END-ISO-10303-21;
"""


class Cleaning(unittest.TestCase):
    def test_step_header(self):
        out = packing.clean_step(STEP, "Bin.step", "jk")
        self.assertNotIn(HOME, out)
        self.assertNotIn("Jane", out)
        self.assertNotIn("Acme", out)
        self.assertIn("FILE_NAME('Bin.step','2026-11-09T10:00:00',('jk'),(''),'','FreeCAD','');", out)
        self.assertIn("FILE_SCHEMA", out)
        self.assertIn("#2 = PRODUCT('Bin','Bin','/x'", out)

    def test_stl(self):
        binary = b"solid FreeCAD by someone".ljust(80, b"\0") + struct.pack("<I", 0)
        out = packing.clean_stl(binary, "Bin")
        self.assertEqual(out[:7], b"STL Bin")
        self.assertEqual(len(out), 84)
        ascii_ = b"solid /home/me/Bin\nfacet normal 0 0 1\nouter loop\nvertex 0 0 0\nvertex 1 0 0\nvertex 0 1 0\nendloop\nendfacet\nendsolid /home/me/Bin\n"
        out = packing.clean_stl(ascii_, "Bin").decode()
        self.assertTrue(out.startswith("solid Bin\n"))
        self.assertIn("endsolid Bin", out)
        self.assertNotIn("/home/me", out)

    def test_licences_and_credits(self):
        mit = packing.license_text("MIT", "J. K.", "Bins")
        self.assertIn("Copyright (c)", mit)
        self.assertIn("J. K.", mit)
        self.assertIn("creativecommons.org/licenses/by-sa/4.0/", packing.license_text("CC-BY-SA-4.0", "J. K.", "Bins"))
        e, w = packing.check_credits("CC-BY-4.0", [{"title": "Lid", "license": "CC-BY-NC-4.0"}])
        self.assertTrue(e and "non-commercial" in e[0])
        e, w = packing.check_credits("CC-BY-4.0", [{"title": "Lid", "license": "CC-BY-SA-4.0"}])
        self.assertTrue(e and "CC-BY-SA-4.0" in e[0])
        e, w = packing.check_credits("CC-BY-SA-4.0", [{"title": "Lid", "license": "CC-BY-SA-4.0"}, {"title": "Hinge"}])
        self.assertEqual(e, [])
        self.assertEqual(len(w), 1)
        e, w = packing.check_credits("CC0-1.0", [{"title": "Lid", "license": "CC-BY-4.0"}])
        self.assertTrue(e)
        e, w = packing.check_credits("MIT", [{"title": "Lid", "license": "CC-BY-ND-4.0"}])
        self.assertTrue(e and "no derivatives" in e[0])

    def test_readme(self):
        r = packing.readme("Bins", "Two bins.", "jk", "MIT", [{"files": ["a.step", "a.stl"], "size": [41.5, 41.5, 21], "volume": 12000, "material": "PLA", "mass": 14.9}], {"Nozzle (mm)": "0.4"}, [{"title": "Base", "author": "Someone", "license": "CC-BY-4.0", "url": "https://example.org/base"}])
        self.assertIn("| a.step / a.stl | 41.5 x 41.5 x 21 | 12.0 | PLA | 14.9 |", r)
        self.assertIn("- Nozzle (mm): 0.4", r)
        self.assertIn("Base by Someone (https://example.org/base) — CC-BY-4.0", r)


@unittest.skipUnless(HAVE, "FreeCAD is not importable here")
class Share(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from agentlink.tools import Session, build_server

        cls.dir = tempfile.mkdtemp()
        cls.session = Session(out_dir=cls.dir)
        cls.server = build_server(cls.session, lean=False)
        cls.call(cls, "gen_make", {"params": {"x": 1, "y": 1, "units": 3, "magnets": True}, "name": "Bin"})
        cls.call(cls, "setup", {"action": "add", "type": "print", "bodies": ["Bin"], "settings": {"nozzle": 0.4, "layerHeight": 0.2, "walls": 3}})
        cls.call(cls, "part_spec", {"material": "petg", "bodies": ["Bin"]})

    def call(self, name, args=None):
        r = self.server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args or {}}})["result"]
        return r.get("isError", False), json.loads(r["content"][0]["text"])

    def test_pack(self):
        err, p = self.call("library_pack", {"license": "CC-BY-4.0", "author": "jk", "name": "Magnet bin", "credits": [{"title": "Gridfinity", "author": "Zack Freedman", "license": "MIT"}]})
        self.assertFalse(err, p)
        print("\npack:", json.dumps(p))
        self.assertNotIn("personal", p)
        with zipfile.ZipFile(p["pack"]) as z:
            names = z.namelist()
            self.assertEqual(sorted(names), ["Magnet_bin/Bin.step", "Magnet_bin/Bin.stl", "Magnet_bin/LICENSE", "Magnet_bin/README.md"])
            step = z.read("Magnet_bin/Bin.step").decode()
            readme = z.read("Magnet_bin/README.md").decode()
            stl = z.read("Magnet_bin/Bin.stl")
        self.assertIn("FILE_NAME('Bin.step'", step)
        self.assertNotIn(HOME, step)
        self.assertIn("PETG", readme)
        self.assertIn("Layer height (mm): 0.2", readme)
        self.assertIn("pause at 2.4 mm", readme)
        self.assertIn("Zack Freedman", readme)
        self.assertNotIn(HOME.encode(), stl[:80])

    def test_refusals(self):
        err, e = self.call("library_pack", {"license": "MIT"})
        self.assertTrue(err)
        self.assertIn("author", e["error"]["message"])
        err, e = self.call("library_pack", {"license": "CC-BY-4.0", "author": "jk", "credits": [{"title": "Hinge", "license": "CC-BY-NC-SA-4.0"}]})
        self.assertTrue(err)
        self.assertIn("non-commercial", e["error"]["message"])
        err, e = self.call("library_pack", {"license": "GPL-3.0"})
        self.assertTrue(err)


if __name__ == "__main__":
    unittest.main()
