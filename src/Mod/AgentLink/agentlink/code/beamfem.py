# SPDX-License-Identifier: LGPL-2.1-or-later
# The rough FEM look at a member: CalculiX's three-node beam elements (B32,
# which it expands into solids) along the span, pinned at one end and on a
# roller at the other, the uniform load as consistent nodal loads. Answers
# the peak bending stress and the mid-span deflection beside the hand
# calculation's, so a reviewer sees how far they differ. Isotropic wood with
# a shear modulus of E/16 is a simplification and says so.

import os
import shutil
import subprocess
import tempfile

from .. import ccx_io


def deck(b, d, span, w, E, n=20, nu=0.3):
    """The input deck: n elements along x, the section b (y) by d (z), load w
    N/mm down -z."""
    le = span / n
    nodes = [(i + 1, i * le / 2.0) for i in range(2 * n + 1)]
    lines = ["*NODE, NSET=NALL"] + [f"{i}, {x:.6f}, 0, 0" for i, x in nodes]
    lines += ["*ELEMENT, TYPE=B32, ELSET=EB"] + [f"{e + 1}, {2 * e + 1}, {2 * e + 2}, {2 * e + 3}" for e in range(n)]
    lines += ["*MATERIAL, NAME=WOOD", "*ELASTIC", f"{E:.6g}, {nu}",
              "*BEAM SECTION, ELSET=EB, MATERIAL=WOOD, SECTION=RECT", f"{b:.6g}, {d:.6g}", "0, 1, 0",
              "*BOUNDARY", "1, 1, 3", "1, 4, 4", f"{2 * n + 1}, 2, 3",
              "*STEP", "*STATIC", "*CLOAD"]
    # consistent loads of a quadratic element: wl/6, 2wl/3, wl/6
    load = {}
    for e in range(n):
        a, m, c = 2 * e + 1, 2 * e + 2, 2 * e + 3
        load[a] = load.get(a, 0) + w * le / 6.0
        load[m] = load.get(m, 0) + 2 * w * le / 3.0
        load[c] = load.get(c, 0) + w * le / 6.0
    lines += [f"{k}, 3, {-v:.9g}" for k, v in sorted(load.items())]
    lines += ["*NODE FILE", "U", "*EL FILE", "S", "*END STEP", ""]
    return "\n".join(lines), n + 1  # the mid-span node


def ccx_path():
    for p in (os.environ.get("AGENTLINK_CCX"), shutil.which("ccx"), shutil.which("ccx_2.21"), shutil.which("ccx_2.22")):
        if p and shutil.which(p):
            return shutil.which(p)
    try:
        import FreeCAD

        q = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Mod/Fem/Ccx").GetString("ccxBinaryPath", "")
        if q and shutil.which(q):
            return shutil.which(q)
    except Exception:
        pass
    return None


def run(b, d, span, w, E, workdir=None, timeout=120, n=20):
    """{deflection_mm, stress_MPa, nodes, folder} of a simply supported member."""
    exe = ccx_path()
    if exe is None:
        raise FileNotFoundError("CalculiX (ccx) is not installed or not found")
    wd = workdir or tempfile.mkdtemp(prefix="agentlink_beam_")
    os.makedirs(wd, exist_ok=True)
    text, mid = deck(b, d, span, w, E, n)
    with open(os.path.join(wd, "beam.inp"), "w") as f:
        f.write(text)
    env = dict(os.environ)
    env.setdefault("OMP_NUM_THREADS", "1")
    r = subprocess.run([exe, "-i", "beam"], cwd=wd, capture_output=True, text=True, timeout=timeout, env=env)
    frd_p = os.path.join(wd, "beam.frd")
    if not os.path.isfile(frd_p):
        log = (r.stdout or "") + (r.stderr or "")
        raise RuntimeError("CalculiX wrote no results: " + " | ".join([l.strip() for l in log.splitlines() if "ERROR" in l][:2] or log.strip().splitlines()[-2:]))
    with open(frd_p) as f:
        disp, stress = ccx_io.read_frd(f.read())
    # the beam is expanded into solids: its own nodes carry the axis's displacement
    dz = max((abs(u[2]) for u in disp.values()), default=0.0)
    sx = max((abs(s[0]) for s in stress.values()), default=0.0)
    return {"deflection_mm": round(dz, 3), "stress_MPa": round(sx, 3), "nodes": len(disp), "folder": wd}
