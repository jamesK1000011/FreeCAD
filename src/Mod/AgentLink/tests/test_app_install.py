# SPDX-License-Identifier: LGPL-2.1-or-later
# Installing another FreeCAD release: the right disk image picked, its
# published SHA-256 found, the old app kept under its version's name.
import os
import plistlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agentlink import install_job  # noqa: E402

ASSETS = [
    {"name": "FreeCAD_1.1.3-Linux-x86_64-py311.AppImage", "browser_download_url": "u0"},
    {"name": "FreeCAD_1.1.3-macOS-x86_64-py311.dmg", "browser_download_url": "u1", "digest": "sha256:" + "a" * 64},
    {"name": "FreeCAD_1.1.3-macOS-arm64-py311.dmg", "browser_download_url": "u2"},
    {"name": "FreeCAD_1.1.3-macOS-arm64-py311.dmg-SHA256.txt", "browser_download_url": "u3"},
]


def fake_app(root, name, version):
    c = os.path.join(root, name, "Contents")
    os.makedirs(c)
    with open(os.path.join(c, "Info.plist"), "wb") as f:
        plistlib.dump({"CFBundleShortVersionString": version}, f)
    return os.path.join(root, name)


class Install(unittest.TestCase):
    def test_pick(self):
        self.assertEqual(install_job.pick_asset(ASSETS, "arm64")["browser_download_url"], "u2")
        self.assertEqual(install_job.pick_asset(ASSETS, "x86_64")["browser_download_url"], "u1")
        self.assertIsNone(install_job.pick_asset(ASSETS[:1], "arm64"))

    def test_sha(self):
        self.assertEqual(install_job.published_sha(ASSETS[1], ASSETS), "a" * 64)
        got = install_job.published_sha(ASSETS[2], ASSETS, fetch=lambda u: (("b" * 64) + "  FreeCAD.dmg\n").encode())
        self.assertEqual(got, "b" * 64)
        self.assertIsNone(install_job.published_sha(ASSETS[2], ASSETS[:3]))

    def test_place_keeps_old(self):
        with tempfile.TemporaryDirectory() as d:
            apps = os.path.join(d, "Applications")
            os.makedirs(apps)
            fake_app(apps, "FreeCAD.app", "1.0.2")
            src = fake_app(d, "FreeCAD-new.app", "1.1.3")
            job = install_job.Job("1.1.3", os.path.join(d, "s.json"))

            def ditto(cmd, **kw):
                import shutil

                shutil.copytree(cmd[1], cmd[2])
                return mock.Mock(returncode=0)

            # /Applications is not ours to touch in a test: place under d.
            with mock.patch.object(install_job.subprocess, "run", side_effect=ditto):
                orig = install_job.Job._place

                def place(self, s, v):
                    with mock.patch("os.access", return_value=False), mock.patch.object(install_job, "HOME", d):
                        return orig(self, s, v)

                dest, kept = place(job, src, "1.1.3")
            self.assertEqual(dest, os.path.join(apps, "FreeCAD.app"))
            self.assertEqual(install_job.app_version(dest), "1.1.3")
            self.assertEqual(kept, os.path.join(apps, "FreeCAD 1.0.2.app"))
            self.assertEqual(install_job.app_version(kept), "1.0.2")


if __name__ == "__main__":
    unittest.main()
