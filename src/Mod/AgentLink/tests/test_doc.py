# SPDX-License-Identifier: LGPL-2.1-or-later
# The doc tools on a real FreeCAD: skipped where FreeCAD cannot be imported.
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
if os.environ.get("FREECAD_LIB"):
    sys.path.append(os.environ["FREECAD_LIB"])
try:
    import FreeCAD  # noqa: F401

    HAVE = True
except ImportError:
    HAVE = False


@unittest.skipUnless(HAVE, "FreeCAD is not importable here")
class Doc(unittest.TestCase):
    def setUp(self):
        from agentlink.tools import Session, build_server

        self.dir = tempfile.mkdtemp()
        self.session = Session(out_dir=self.dir)
        self.server = build_server(self.session, lean=False)

    def call(self, name, args=None, ok=True):
        r = self.server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args or {}}})["result"]
        err, out = r.get("isError", False), json.loads(r["content"][0]["text"])
        if ok is True:
            self.assertFalse(err, f"{name}: {out}")
        elif ok is False:
            self.assertTrue(err, f"{name} should be refused: {out}")
        return out

    def test_close(self):
        a = self.call("doc_new", {"name": "CloseA"})
        self.call("part_primitive", {"kind": "box", "size": [5, 5, 5], "name": "Box"})
        self.call("doc_save", {"path": "close_a.FCStd"})
        b = self.call("doc_new", {"name": "CloseB"})
        self.call("part_primitive", {"kind": "box", "size": [5, 5, 5], "name": "Box"})
        # B never saved and changed: refused, then closed with discard.
        self.call("doc_close", {}, ok=False)
        out = self.call("doc_close", {"discard": True})
        self.assertEqual(out["closed"], ["CloseB"])
        self.assertNotIn(b["document"], FreeCAD.listDocuments())
        self.assertNotEqual(self.session.doc.Name, b["document"])
        out = self.call("doc_close", {"documents": [os.path.join(self.dir, "close_a.FCStd")]})
        self.assertEqual(out["closed"], ["CloseA"])
        self.assertNotIn(a["document"], FreeCAD.listDocuments())
        self.call("doc_close", {"documents": ["Nope"]}, ok=False)

    def test_objects_and_properties(self):
        n = self.call("doc_new", {"name": "Parts"})
        self.assertEqual(self.session.doc.Name, n["document"])
        self.call("part_primitive", {"kind": "box", "size": [10, 10, 10], "name": "Box"})
        lst = self.call("object_list")
        self.assertEqual([o["label"] for o in lst["objects"]], ["Box"])
        self.assertAlmostEqual(lst["objects"][0]["shape"]["volume"], 1000, delta=1e-6)
        g = self.call("object_get", {"object": "Box", "filter": ["Length", "Placement"]})
        self.assertEqual(set(g["properties"]), {"Length", "Placement"})
        self.assertAlmostEqual(g["properties"]["Length"]["value"], 10, delta=1e-9)
        s = self.call("object_set", {"object": "Box", "values": {"Length": 30, "Placement": {"at": [1, 2, 3]}}})
        self.assertAlmostEqual(s["set"]["Length"]["to"], 30, delta=1e-9)
        self.assertAlmostEqual(self.call("body_check", {"body": "Box"})["volume"], 3000, delta=1e-6)
        e = self.call("object_set", {"object": "Box", "values": {"Lenght": 5}}, ok=False)
        self.assertIn("Length", e["error"]["message"])
        e = self.call("object_set", {"object": "Box", "values": {"Length": "wide"}}, ok=False)
        self.assertEqual(self.call("object_get", {"object": "Box", "filter": ["Length"]})["properties"]["Length"]["value"], 30)
        r = self.call("object_rename", {"object": "Box", "label": "Brick"})
        self.assertEqual(r["to"], "Brick")
        rc = self.call("recompute", {"force": True})
        self.assertTrue(rc["ok"], rc)
        self.call("part_primitive", {"kind": "sphere", "radius": 2, "name": "Ball"})
        self.call("object_delete", {"objects": ["Ball"]})
        self.assertEqual([o["label"] for o in self.call("object_list")["objects"]], ["Brick"])

    def test_files_round_trip(self):
        self.call("part_primitive", {"kind": "cylinder", "radius": 5, "height": 10, "name": "Can"})
        vol = self.call("body_check", {"body": "Can"})["volume"]
        for fmt in ("step", "iges", "brep", "stl", "obj", "3mf"):
            out = self.call("export", {"objects": "Can", "format": fmt})
            self.assertTrue(os.path.isfile(out["path"]), out)
            print(f"\nexport {fmt}:", json.dumps(out["verify"]))
            if fmt in ("step", "brep"):
                self.assertLess(out["verify"]["volumeDiff"], 0.01)
            elif fmt in ("stl", "obj", "3mf"):
                self.assertLess(out["verify"]["volumeDiff"], vol * 0.01)
                self.assertTrue(out["verify"]["closed"])
        imp = self.call("import_file", {"path": "Can.step", "name": "CanStep"})
        self.assertAlmostEqual(imp["objects"][0]["volume"], vol, delta=0.01)
        mesh = self.call("import_file", {"path": "Can.stl", "name": "CanMesh"})
        self.assertTrue(mesh["objects"][0]["closed"], mesh)
        self.assertAlmostEqual(mesh["objects"][0]["volume"], vol, delta=vol * 0.01)
        kept = self.call("import_file", {"path": "Can.3mf", "name": "CanMesh3mf", "solid": False})
        self.assertIn("mesh", kept["objects"][0])
        self.call("import_file", {"path": "nope.step"}, ok=False)
        bad = self.call("import_file", {"path": os.path.join(self.dir, "Can.step").replace(".step", ".svg")}, ok=False)
        self.assertIn(bad["error"]["code"], ("not_found", "unavailable"))
        saved = self.call("doc_save", {"path": "kept"})
        self.assertTrue(saved["path"].endswith("kept.FCStd"), saved)
        self.call("doc_new", {"name": "Other"})
        self.assertEqual(self.call("object_list")["count"], 0)
        back = self.call("doc_open", {"path": saved["path"]})
        self.assertIn("Can", back["bodies"])
        self.assertEqual(back["errors"], [])


if __name__ == "__main__":
    unittest.main()
