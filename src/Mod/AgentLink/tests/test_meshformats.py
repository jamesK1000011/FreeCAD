# SPDX-License-Identifier: LGPL-2.1-or-later
# The mesh formats FreeCAD's Mesh module does not read: a 40 x 20 x 10 mm
# box written in each with a node transform, read back in mm and Z up.
import base64
import importlib.util
import json
import os
import struct
import tempfile
import unittest

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("meshformats", os.path.join(HERE, "..", "agentlink", "meshformats.py"))
MF = importlib.util.module_from_spec(spec)
spec.loader.exec_module(MF)

# A box 40 (x) by 20 (y) by 10 (z) mm from the origin, in Z-up mm.
V = np.array([[x, y, z] for x in (0, 40) for y in (0, 20) for z in (0, 10)], float)
F = np.array([[0, 1, 3], [0, 3, 2], [4, 6, 7], [4, 7, 5], [0, 4, 5], [0, 5, 1], [2, 3, 7], [2, 7, 6], [0, 2, 6], [0, 6, 4], [1, 5, 7], [1, 7, 3]])
SHIFT = np.array([5.0, 0, 0])  # mm, applied by each file's node transform


def y_up_m(P):
    """Z-up mm to Y-up metres, as glTF and VRML keep them."""
    return np.column_stack([P[:, 0], P[:, 2], -P[:, 1]]) / 1000.0


class Formats(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def check(self, path, **kw):
        P, Fo, info = MF.read(path, **kw)
        self.assertEqual(len(Fo), 12, info)
        np.testing.assert_allclose(P.min(0), [5, 0, 0], atol=1e-4)
        np.testing.assert_allclose(P.max(0), [45, 20, 10], atol=1e-4)
        # Outward winding kept: the signed volume is the box's.
        T = P[Fo]
        vol = np.einsum("ij,ij->i", T[:, 0], np.cross(T[:, 1], T[:, 2])).sum() / 6
        self.assertAlmostEqual(vol, 8000, delta=1e-2)
        return info

    def gltf_doc(self, uri=None):
        pos = y_up_m(V).astype("<f4").tobytes()
        idx = F.astype("<u4").tobytes()
        doc = {"asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0]}],
               "nodes": [{"translation": list(y_up_m(SHIFT[None])[0]), "children": [1]}, {"mesh": 0}],
               "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1}]}],
               "buffers": [{"byteLength": len(pos) + len(idx), **({"uri": uri(pos + idx)} if uri else {})}],
               "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": len(pos)}, {"buffer": 0, "byteOffset": len(pos), "byteLength": len(idx)}],
               "accessors": [{"bufferView": 0, "componentType": 5126, "count": 8, "type": "VEC3"}, {"bufferView": 1, "componentType": 5125, "count": 36, "type": "SCALAR"}]}
        return doc, pos + idx

    def test_glb(self):
        doc, binary = self.gltf_doc()
        js = json.dumps(doc).encode()
        js += b" " * (-len(js) % 4)
        binary += b"\0" * (-len(binary) % 4)
        p = os.path.join(self.dir, "box.glb")
        with open(p, "wb") as f:
            f.write(struct.pack("<4sII", b"glTF", 2, 12 + 8 + len(js) + 8 + len(binary)))
            f.write(struct.pack("<II", len(js), 0x4E4F534A) + js + struct.pack("<II", len(binary), 0x004E4942) + binary)
        self.assertIn("glTF", self.check(p)["takenAs"])

    def test_gltf_data_uri(self):
        doc, _b = self.gltf_doc(lambda b: "data:application/octet-stream;base64," + base64.b64encode(b).decode())
        p = os.path.join(self.dir, "box.gltf")
        with open(p, "w") as f:
            json.dump(doc, f)
        self.check(p)

    def test_dae(self):
        # Z up in centimetres, the shift as a <matrix>.
        P = V / 10.0
        tris = " ".join(str(i) for i in F.ravel())
        dae = f"""<?xml version="1.0"?><COLLADA xmlns="http://www.collada.org/2005/11/COLLADASchema" version="1.4.1">
<asset><unit meter="0.01"/><up_axis>Z_UP</up_axis></asset>
<library_geometries><geometry id="g"><mesh>
<source id="p"><float_array id="pa" count="24">{' '.join(f'{x:g}' for x in P.ravel())}</float_array>
<technique_common><accessor source="#pa" count="8" stride="3"/></technique_common></source>
<vertices id="v"><input semantic="POSITION" source="#p"/></vertices>
<triangles count="12"><input semantic="VERTEX" source="#v" offset="0"/><p>{tris}</p></triangles>
</mesh></geometry></library_geometries>
<library_visual_scenes><visual_scene id="s"><node><matrix>1 0 0 0.5 0 1 0 0 0 0 1 0 0 0 0 1</matrix><instance_geometry url="#g"/></node></visual_scene></library_visual_scenes>
</COLLADA>"""
        p = os.path.join(self.dir, "box.dae")
        with open(p, "w") as f:
            f.write(dae)
        self.check(p)

    def test_wrl(self):
        P = y_up_m(V)
        idx = ", ".join(f"{a}, {b}, {c}, -1" for a, b, c in F)
        t = y_up_m(SHIFT[None])[0]
        wrl = f"""#VRML V2.0 utf8
# a box
DEF Box Transform {{ translation {t[0]:g} {t[1]:g} {t[2]:g} children [
  Shape {{ appearance Appearance {{ material Material {{ diffuseColor 0.8 0.8 0.8 }} }}
    geometry IndexedFaceSet {{ solid TRUE coord Coordinate {{ point [ {', '.join(' '.join(f'{x:g}' for x in r) for r in P)} ] }}
      coordIndex [ {idx} ] }} }} ] }}
"""
        p = os.path.join(self.dir, "box.wrl")
        with open(p, "w") as f:
            f.write(wrl)
        self.check(p)

    def test_3ds(self):
        P = (V + SHIFT).astype("<f4")

        def chunk(cid, body):
            return struct.pack("<HI", cid, 6 + len(body)) + body

        faces = b"".join(struct.pack("<4H", *t, 0) for t in F)
        mesh = chunk(0x4110, struct.pack("<H", 8) + P.tobytes()) + chunk(0x4120, struct.pack("<H", 12) + faces)
        p = os.path.join(self.dir, "box.3ds")
        with open(p, "wb") as f:
            f.write(chunk(0x4D4D, chunk(0x3D3D, chunk(0x4000, b"Box\0" + chunk(0x4100, mesh)))))
        self.check(p)

    def test_override(self):
        P = (V + SHIFT).astype("<f4") / 25.4

        def chunk(cid, body):
            return struct.pack("<HI", cid, 6 + len(body)) + body

        faces = b"".join(struct.pack("<4H", *t, 0) for t in F)
        mesh = chunk(0x4110, struct.pack("<H", 8) + P.tobytes()) + chunk(0x4120, struct.pack("<H", 12) + faces)
        p = os.path.join(self.dir, "inch.3ds")
        with open(p, "wb") as f:
            f.write(chunk(0x4D4D, chunk(0x3D3D, chunk(0x4000, b"Box\0" + chunk(0x4100, mesh)))))
        self.assertIn("as given", self.check(p, units="in", up="z")["takenAs"])


    def test_import_file(self):
        try:
            import FreeCAD  # noqa: F401
        except ImportError:
            self.skipTest("FreeCAD is not importable here")
        import sys

        sys.path.insert(0, os.path.join(HERE, ".."))
        from agentlink.tools import Session, build_server

        self.test_glb()
        server = build_server(Session(out_dir=self.dir), lean=False)
        r = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "import_file", "arguments": {"path": os.path.join(self.dir, "box.glb")}}})["result"]
        o = json.loads(r["content"][0]["text"])
        self.assertFalse(r.get("isError"), o)
        self.assertEqual(o["read"]["format"], "glb")
        self.assertTrue(o["objects"][0]["closed"], o)
        self.assertAlmostEqual(o["objects"][0]["volume"], 8000, delta=1)


if __name__ == "__main__":
    unittest.main()
