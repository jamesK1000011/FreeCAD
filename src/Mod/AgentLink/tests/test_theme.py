# SPDX-License-Identifier: LGPL-2.1-or-later
# The Studio preference packs hold together: each named in package.xml has
# its folder, its .cfg naming its own theme, and a parameter file whose
# every reference resolves — and, where FreeCAD is here, every token its
# stylesheet asks for.
import os
import re
import unittest
import xml.etree.ElementTree as ET

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
NS = {"p": "https://wiki.freecad.org/Package_Metadata"}
# Set from the user's accent preference, not by a theme file.
FROM_PREFERENCES = {"ThemeAccentColor1", "ThemeAccentColor2", "ThemeAccentColor3"}


def packs():
    root = ET.parse(os.path.join(ROOT, "package.xml")).getroot()
    return [p.find("p:name", NS).text for p in root.find("p:content", NS).findall("p:preferencepack", NS)]


def tokens(path):
    out = {}
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        k, v = line.split(":", 1)
        out[k.strip()] = v.strip().strip('"')
    return out


def stock_stylesheet():
    try:
        import FreeCAD
    except ImportError:
        return None
    p = os.path.join(FreeCAD.getResourceDir(), "Gui", "Stylesheets", "FreeCAD.qss")
    return p if os.path.isfile(p) else None


class Theme(unittest.TestCase):
    def test_packs(self):
        names = packs()
        self.assertEqual(sorted(names), ["Studio Dark", "Studio Light"])
        qss = stock_stylesheet()
        wanted = set(re.findall(r"@([A-Za-z0-9]+)", open(qss, encoding="utf-8").read())) if qss else set()
        for name in names:
            folder = os.path.join(ROOT, name)
            cfg = open(os.path.join(folder, name + ".cfg"), encoding="utf-8").read()
            self.assertIn(f'<FCText Name="Theme">{name}</FCText>', cfg)
            self.assertIn('<FCText Name="StyleSheet">FreeCAD.qss</FCText>', cfg)
            ET.fromstring(cfg)
            t = tokens(os.path.join(folder, "parameters", name + ".yaml"))
            for k, v in t.items():
                for ref in re.findall(r"@([A-Za-z0-9]+)", v):
                    self.assertTrue(ref in t or ref in FROM_PREFERENCES, f"{name}: {k} refers to @{ref}, which no line sets")
                if v.startswith("#"):
                    self.assertRegex(v, r"^#[0-9a-fA-F]{6}$", f"{name}: {k}")
            missing = wanted - set(t) - FROM_PREFERENCES
            self.assertFalse(missing, f"{name} leaves these stylesheet tokens unset: {sorted(missing)}")


if __name__ == "__main__":
    unittest.main()
