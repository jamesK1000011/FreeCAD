# SPDX-License-Identifier: LGPL-2.1-or-later
# Extensions: add-ons of their own that build on Agent Link, loaded when they
# are installed (a folder beside this one in FreeCAD's Mod folder, or on the
# Python path) and left out without a word when they are not. Each is a
# module with install(server, session); it may add tools, a check before
# each call (server.guard) and a wrapper round each call (server.call), and
# use the hooks below. Nothing here knows what an extension does.
#
# Hooks an extension may use:
#   panel_widgets   factories(panel) -> a widget shown above the panel's tabs
#                   (refresh() on it, if it has one, each second)
#   before_restart  fn(session), called before app_restart saves and quits
#   session.keep_doc       True: the window link does not move the session to
#                          the document the person brings to the front
#   session.window_quiet   True: the live echo leaves the window alone (the
#                          activity list still gets each call)
#   session.activity_tag   text put before each call in the activity list

import os
import sys

# Extensions named here or in AGENTLINK_EXTENSIONS, and every package
# named agentlink_… in a folder AgentLink… beside this add-on.
NAMES = []

panel_widgets = []
before_restart = []
loaded = {}


def _paths():
    """Mod folders beside this add-on's put on the path; the agentlink_…
    packages found in them."""
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    mod = os.path.dirname(here)
    found = []
    try:
        for name in sorted(os.listdir(mod)):
            p = os.path.join(mod, name)
            if not (name.startswith("AgentLink") and p != here and os.path.isdir(p)):
                continue
            if p not in sys.path:
                sys.path.append(p)
            for pkg in sorted(os.listdir(p)):
                if pkg.startswith("agentlink_") and os.path.isfile(os.path.join(p, pkg, "__init__.py")):
                    found.append(pkg)
    except OSError:
        pass
    return found


def install_all(server, session):
    names = []
    for n in NAMES + [n.strip() for n in os.environ.get("AGENTLINK_EXTENSIONS", "").split(",") if n.strip()] + _paths():
        if n not in names:
            names.append(n)
    for name in names:
        try:
            mod = __import__(name.strip(), fromlist=["install"])
        except ImportError:
            continue
        try:
            mod.install(server, session)
            loaded[name] = getattr(mod, "VERSION", "?")
        except Exception as e:
            sys.stderr.write(f"Agent Link: extension {name} did not load ({e})\n")
    return dict(loaded)


def restarting(session):
    for fn in list(before_restart):
        try:
            fn(session)
        except Exception as e:
            sys.stderr.write(f"Agent Link: before restart ({e})\n")


# ---- getting extensions: from a git repository, with this machine's own
# git sign-in (no token passes through here). Which repositories is a local
# setting (Mod/AgentLink ExtensionRepos: "owner/repo[@branch]", comma
# separated); each one's top-level folders named AgentLink… are copied into
# FreeCAD's Mod folder beside this add-on.

PREFS = "User parameter:BaseApp/Preferences/Mod/AgentLink"


def repos():
    import FreeCAD

    raw = FreeCAD.ParamGet(PREFS).GetString("ExtensionRepos", "")
    return [r.strip() for r in raw.split(",") if r.strip()]


def _git():
    import shutil

    for g in (shutil.which("git"), "/usr/bin/git", "/opt/homebrew/bin/git", "/usr/local/bin/git"):
        if g and os.path.exists(g):
            return g
    return None


def sync(cache=None):
    """Each listed repository fetched (cloned, or brought up to date) and
    its AgentLink… folders copied in beside this add-on. Returns what was
    installed and what could not be reached, and why."""
    import shutil
    import subprocess

    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    mod = os.path.dirname(here)
    cache = cache or os.path.expanduser("~/.config/freecad-agentlink/ext")
    os.makedirs(cache, exist_ok=True)
    git = _git()
    out = {"installed": [], "failed": []}
    if not repos():
        return {**out, "note": "No extension repositories set (ExtensionRepos)."}
    if git is None:
        return {**out, "failed": [{"repo": r, "why": "git is not installed on this computer"} for r in repos()]}
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}  # never wait on a password prompt
    for spec in repos():
        repo, _, branch = spec.partition("@")
        branch = branch or "main"
        url = repo if "://" in repo else f"https://github.com/{repo}.git"
        dest = os.path.join(cache, repo.replace("/", "__"))
        try:
            if os.path.isdir(os.path.join(dest, ".git")):
                cmds = [[git, "-C", dest, "fetch", "--depth", "1", "origin", branch], [git, "-C", dest, "reset", "--hard", "FETCH_HEAD"]]
            else:
                cmds = [[git, "clone", "--depth", "1", "-b", branch, url, dest]]
            for c in cmds:
                r = subprocess.run(c, capture_output=True, text=True, timeout=120, env=env)
                if r.returncode != 0:
                    raise RuntimeError((r.stderr or r.stdout).strip().splitlines()[-1] if (r.stderr or r.stdout).strip() else f"git exited {r.returncode}")
            sha = subprocess.run([git, "-C", dest, "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=30).stdout.strip()
            tops = [n for n in sorted(os.listdir(dest)) if n.startswith("AgentLink") and os.path.isdir(os.path.join(dest, n))]
            for n in tops:
                target = os.path.join(mod, n)
                if os.path.abspath(target) == os.path.abspath(here):
                    continue
                shutil.copytree(os.path.join(dest, n), target, dirs_exist_ok=True, ignore=shutil.ignore_patterns(".git", "__pycache__", "tests"))
            out["installed"].append({"repo": spec, "commit": sha, "folders": tops})
        except Exception as e:
            out["failed"].append({"repo": spec, "why": str(e)[:300]})
    return out


def tools(session):
    from .protocol import Tool, ToolError

    def app_extensions(a):
        import FreeCAD

        act = a.get("action", "list")
        if act == "list":
            return {"repos": repos(), "loaded": dict(loaded), "note": "Loaded ones run in this FreeCAD; a newly installed one loads after app_restart (window) or agentlink_update (hidden)."}
        if act in ("add", "remove"):
            r = str(a.get("repo") or "").strip()
            if not r:
                raise ToolError("invalid_input", "repo: owner/repo, with @branch if not main.")
            have = repos()
            have = (have + [r]) if act == "add" and r not in have else [x for x in have if x != r]
            FreeCAD.ParamGet(PREFS).SetString("ExtensionRepos", ",".join(have))
            return {"repos": have}
        if act == "sync":
            return sync()
        raise ToolError("invalid_input", "action: list, add, remove, sync.")

    return [Tool("app_extensions",
                 "Extensions to Agent Link kept in git repositories: list (set and loaded), add / remove { repo: owner/repo[@branch] }, sync (fetched with this computer's own git sign-in and installed beside Agent Link; then app_restart). The repositories are a local setting.",
                 {"type": "object", "properties": {"action": {"type": "string", "enum": ["list", "add", "remove", "sync"]}, "repo": {"type": "string"}}, "required": [], "additionalProperties": False},
                 app_extensions, group="core")]
