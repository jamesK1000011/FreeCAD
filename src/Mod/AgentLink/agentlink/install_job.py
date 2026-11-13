# SPDX-License-Identifier: LGPL-2.1-or-later
# Another FreeCAD release beside the one running, on a Mac: the official
# disk image from FreeCAD's GitHub release, its SHA-256 checked against the
# one the release publishes, copied into Applications. The one already there
# is kept under its version's name ("FreeCAD 1.0.2.app"), so going back is a
# rename. Runs as its own process (a download takes minutes): app_install
# starts it and reads the state file it keeps.
#
#   python3 install_job.py VERSION STATE_FILE
#
# Plain Python, no FreeCAD: it runs from the bundle's python3.

import hashlib
import json
import os
import platform
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

API = "https://api.github.com/repos/FreeCAD/FreeCAD/releases/"
HOME = os.path.expanduser("~")
WORK = os.path.join(HOME, "AgentLink", "install")


def pick_asset(assets, arch):
    """The macOS disk image for this machine's processor."""
    want = "arm64" if arch in ("arm64", "aarch64") else "x86_64"
    for a in assets:
        n = a.get("name", "")
        if n.endswith(".dmg") and "macOS" in n and want in n:
            return a
    return None


def published_sha(asset, assets, fetch=None):
    """The SHA-256 the release gives for this file: GitHub's own digest of
    the upload, else the release's NAME-SHA256.txt beside it."""
    d = asset.get("digest") or ""
    if d.startswith("sha256:") and re.fullmatch(r"[0-9a-f]{64}", d[7:]):
        return d[7:]
    for a in assets:
        n = a.get("name", "")
        if n.lower() in (asset["name"].lower() + "-sha256.txt", asset["name"].lower() + ".sha256"):
            text = (fetch or _get)(a["browser_download_url"]).decode("utf-8", "replace")
            m = re.search(r"\b([0-9a-fA-F]{64})\b", text)
            if m:
                return m.group(1).lower()
    return None


def app_version(app):
    try:
        with open(os.path.join(app, "Contents", "Info.plist"), "rb") as f:
            p = plistlib.load(f)
        return p.get("CFBundleShortVersionString") or p.get("CFBundleVersion") or ""
    except Exception:
        return ""


def _get(url):
    r = urllib.request.Request(url, headers={"User-Agent": "FreeCAD-AgentLink", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(r, timeout=60) as f:
        return f.read()


class Job:
    def __init__(self, version, state_file):
        self.version = version
        self.state_file = state_file
        self.st = {"version": version, "state": "starting", "started": time.time(), "pid": os.getpid()}

    def put(self, **kw):
        self.st.update(kw)
        self.st["updated"] = time.time()
        tmp = self.state_file + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.st, f, indent=1)
        os.replace(tmp, self.state_file)

    def run(self):
        os.makedirs(WORK, exist_ok=True)
        tag = "latest" if self.version in ("", "latest") else "tags/" + self.version
        self.put(state="finding the release")
        rel = json.loads(_get(API + tag))
        assets = rel.get("assets", [])
        asset = pick_asset(assets, platform.machine())
        if not asset:
            raise RuntimeError("release %s has no macOS disk image for %s" % (rel.get("tag_name"), platform.machine()))
        sha = published_sha(asset, assets)
        if not sha:
            raise RuntimeError("release %s publishes no SHA-256 for %s: not installed" % (rel.get("tag_name"), asset["name"]))
        self.put(state="downloading", release=rel.get("tag_name"), file=asset["name"], bytes=asset.get("size"), sha256=sha, url=asset["browser_download_url"])

        dmg = os.path.join(WORK, asset["name"])
        if not (os.path.exists(dmg) and self._sha(dmg) == sha):
            self._download(asset["browser_download_url"], dmg, asset.get("size") or 0)
            got = self._sha(dmg)
            if got != sha:
                os.remove(dmg)
                raise RuntimeError("SHA-256 mismatch for %s (got %s, release says %s): deleted, not installed" % (asset["name"], got, sha))
        self.put(state="checked", verified=True)

        mnt = tempfile.mkdtemp(prefix="freecad-dmg-")
        r = subprocess.run(["hdiutil", "attach", "-nobrowse", "-readonly", "-noautoopen", "-mountpoint", mnt, dmg], input=b"Y\n", capture_output=True, timeout=300)
        if r.returncode:
            raise RuntimeError("hdiutil attach: " + r.stderr.decode(errors="replace")[:400])
        try:
            apps = [n for n in os.listdir(mnt) if n.endswith(".app") and n.lower().startswith("freecad")]
            if not apps:
                raise RuntimeError("no FreeCAD .app in the disk image")
            src = os.path.join(mnt, apps[0])
            new_ver = app_version(src)
            # Signed and accepted by Gatekeeper, or it never runs (M6): a
            # release that fails either is not put in place at all.
            sig = subprocess.run(["codesign", "--verify", "--deep", "--strict", src], capture_output=True, timeout=900)
            if sig.returncode:
                raise RuntimeError("codesign --verify --deep --strict refused %s: %s — not installed" % (os.path.basename(src), sig.stderr.decode(errors="replace")[:300]))
            gk = subprocess.run(["spctl", "--assess", "--type", "execute", src], capture_output=True, timeout=300)
            if gk.returncode:
                raise RuntimeError("Gatekeeper (spctl --assess) refused %s: %s — not installed" % (os.path.basename(src), gk.stderr.decode(errors="replace")[:300]))
            self.put(state="copying", new_version=new_ver, signed=True, gatekeeper=True)
            dest, kept = self._place(src, new_ver)
        finally:
            subprocess.run(["hdiutil", "detach", mnt, "-force"], capture_output=True, timeout=120)
            try:
                os.rmdir(mnt)
            except OSError:
                pass
        self.put(state="installed", app=dest, kept=kept, finished=time.time())

    def _place(self, src, new_ver):
        """Into /Applications as FreeCAD.app if it can be written, else
        ~/Applications; whatever held that name keeps its version's name."""
        roots = ["/Applications", os.path.join(HOME, "Applications")]
        root = next((r for r in roots if os.path.isdir(r) and os.access(r, os.W_OK)), None)
        if root is None:
            root = roots[1]
            os.makedirs(root, exist_ok=True)
        dest = os.path.join(root, "FreeCAD.app")
        kept = None
        if os.path.exists(dest):
            old = app_version(dest) or "old"
            if old == new_ver:
                return dest, None
            kept = os.path.join(root, "FreeCAD %s.app" % old)
            n = 2
            while os.path.exists(kept):
                kept = os.path.join(root, "FreeCAD %s (%d).app" % (old, n))
                n += 1
            os.rename(dest, kept)
        part = os.path.join(root, ".FreeCAD-installing.app")
        if os.path.exists(part):
            shutil.rmtree(part)
        r = subprocess.run(["ditto", src, part], capture_output=True, timeout=1800)
        if r.returncode:
            if kept:
                os.rename(kept, dest)
            raise RuntimeError("ditto: " + r.stderr.decode(errors="replace")[:400])
        os.rename(part, dest)
        return dest, kept

    def _download(self, url, path, size):
        part = path + ".part"
        req = urllib.request.Request(url, headers={"User-Agent": "FreeCAD-AgentLink"})
        got, last = 0, 0
        with urllib.request.urlopen(req, timeout=120) as r, open(part, "wb") as f:
            while True:
                b = r.read(1 << 20)
                if not b:
                    break
                f.write(b)
                got += len(b)
                if time.time() - last > 2:
                    self.put(downloaded=got, percent=round(100.0 * got / size, 1) if size else None)
                    last = time.time()
        os.replace(part, path)
        self.put(downloaded=got, percent=100.0)

    @staticmethod
    def _sha(path):
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for b in iter(lambda: f.read(1 << 20), b""):
                h.update(b)
        return h.hexdigest()


def main(argv):
    job = Job(argv[1], argv[2])
    try:
        job.run()
    except Exception as e:
        job.put(state="failed", error=str(e), finished=time.time())
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
