# SPDX-License-Identifier: LGPL-2.1-or-later
# Where a tool may read and write on the person's computer. Every file path
# an agent passes in goes through here before the tool runs: an agent can be
# talked into things by what it reads (a web page, a document), so the add-on
# keeps it away from credentials, hidden settings, scripts and the system,
# whatever it is asked. Reading is open otherwise (the person's own models,
# scans, fonts); writing stays in the person's home, the session's folder or
# the temporary folder, as model, drawing, data or G-code files.

import os

from .protocol import ToolError

# Fields that hold a file path (or a list of them).
PATH_KEYS = {"path", "file", "files", "kinematics", "template", "font", "export", "import", "pattern", "image"}
# Fields that are files only for some tools
PATH_KEYS_FOR = {"ifc_diff": {"a", "b"}}
# Tools where "path" is a curve in the model, not a file.
GEOMETRIC = {"part_sweep", "pd_pipe", "draft_link_array", "draft_array", "bim_frame", "bim_fence", "printed_channel", "hw_hole", "bim_truss"}
# Tools that write their path (the rest read theirs).
WRITERS = {"export", "export_file", "bim_export", "doc_save", "robot_export", "meshobj_unwrap", "meshobj_export", "cam_sim", "cam_post",
           "sheet_export", "sheet_chart", "scad_export", "texture_prepare", "view_render", "view_snapshot", "points_export", "drawing_make",
           "td_export", "fea_export", "cam_template_export", "fea_chart", "insp_export", "mill_export", "code_packet", "ifc_save", "asm_export", "asm_motion", "steps_render", "window_snapshot"}
SECRET_PARTS = {".ssh", ".aws", ".gnupg", ".kube", ".docker", ".config", ".netrc", ".git", ".password-store", "keychains", "Keychains",
                ".zshrc", ".bashrc", ".profile", ".bash_profile", ".zprofile", ".env"}
NEVER_WRITE_EXT = {".sh", ".bash", ".zsh", ".command", ".tool", ".py", ".pyc", ".pyw", ".js", ".mjs", ".cjs", ".ts", ".rb", ".pl", ".php",
                   ".app", ".plist", ".dylib", ".so", ".dll", ".exe", ".bat", ".cmd", ".ps1", ".vbs", ".scpt", ".applescript", ".workflow",
                   ".service", ".desktop", ".kext", ".pkg", ".dmg", ".jar", ".FCMacro", ".fcmacro"}


def _real(p):
    return os.path.realpath(os.path.expanduser(p))


def _parts(p):
    return [x for x in p.replace("\\", "/").split("/") if x]


def check(tool, key, value, out_dir, write):
    """Refuse a path the agent should not touch; answers the path as given."""
    if not isinstance(value, str) or not value.strip():
        return value
    raw = value.strip()
    p = raw if os.path.isabs(os.path.expanduser(raw)) else os.path.join(out_dir or os.getcwd(), raw)
    real = _real(p)
    parts = _parts(real)
    secret = [x for x in parts if x in SECRET_PARTS]
    if secret:
        raise ToolError("invalid_input", f"{key}: {raw} is in {secret[0]} — credentials and shell settings are off limits to the agent.")
    if write:
        home = _real("~")
        # A session folder at the disk's root (a server started from / with
        # no --out) is not taken as leave to write anywhere.
        roots = [r for r in (_real(out_dir) if out_dir else None, home, _real("/tmp"), _real(os.environ.get("TMPDIR", "/tmp"))) if r and r != "/"]
        inside = any(real == r or real.startswith(r.rstrip("/") + "/") for r in roots)
        if not inside:
            raise ToolError("invalid_input", f"{key}: {raw} — files are written in the session's folder or under your home folder, not there.")
        rel = os.path.relpath(real, home) if real.startswith(home) else ""
        hidden = [x for x in _parts(rel) if x.startswith(".")]
        out_real = _real(out_dir) if out_dir and _real(out_dir) != "/" else None
        if hidden and not (out_real and real.startswith(out_real.rstrip("/") + "/")):
            raise ToolError("invalid_input", f"{key}: {raw} is in a hidden folder ({hidden[0]}) — write it somewhere visible.")
        in_out = bool(out_real and (real == out_real or real.startswith(out_real.rstrip("/") + "/")))
        top = _parts(rel)[:1]
        if top == ["Library"] and not in_out:
            raise ToolError("invalid_input", f"{key}: {raw} is in ~/Library, where apps keep their settings — write it somewhere you'd look for it.")
        ext = os.path.splitext(real)[1]
        if ext.lower() == ".fcstd" and os.path.exists(real) and tool != "doc_save":
            raise ToolError("invalid_input", f"{key}: {raw} is a FreeCAD document already there — {tool} would write over it. Another name, or doc_save for the document itself.")
        if ext.lower() in {e.lower() for e in NEVER_WRITE_EXT}:
            raise ToolError("invalid_input", f"{key}: {raw} — the add-on writes models, drawings, data and G-code, never {ext} files (scripts, programs, settings).")
    return value


def guard(tool, args, out_dir):
    """Every path-like field of a call checked; raises on the first refusal."""
    name = tool.name
    # Some tools make their files' names from name (slice, td_export, cut
    # and mill exports): a name with a path in it wrote anywhere.
    nm = (args or {}).get("name")
    if isinstance(nm, str) and ("/" in nm or "\\" in nm or nm.startswith("~") or nm.strip(". ") == "" and nm):
        raise ToolError("invalid_input", f"name: {nm!r} — a name, not a path (files go to the session's folder; path where a tool takes one).")
    writes = name in WRITERS or (args or {}).get("action") == "export"
    for k, v in (args or {}).items():
        if (k not in PATH_KEYS and k not in PATH_KEYS_FOR.get(name, ())) or (k == "path" and name in GEOMETRIC):
            continue
        write = (writes and k in ("path", "file")) or k == "export"
        if isinstance(v, list):
            for x in v:
                check(name, k, x, out_dir, write)
        else:
            check(name, k, v, out_dir, write)
