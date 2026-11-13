# SPDX-License-Identifier: LGPL-2.1-or-later
# Heavy jobs on this Mac (unattended use, N): each heavy tool's time and peak
# memory are logged against a size measure (the faces of the parts, mesh
# facets, cloud points), so the next one can be estimated. Over 2 minutes,
# the first call is answered with the estimate and a rough pass offered —
# the same call again runs it. Over the memory ceiling (half this Mac's
# memory unless the person set Mod/AgentLink MemoryCeilingGB), it is refused
# before it starts. Holding back for heat, memory pressure or the person
# working in the window is the supervisor's (supervise.py), which sees every
# call whichever FreeCAD runs it.

import resource
import sys
import time

from . import keep
from .protocol import ToolError

OFFER_OVER = 120.0
OFFER_KEEP = 600.0


def _maxrss_mb():
    k = 1.0 if sys.platform == "darwin" else 1024.0  # bytes on macOS, KB elsewhere
    a = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * k
    b = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss * k
    return a / 1048576.0, b / 1048576.0


def size_of(session):
    """How big the work is: the parts' faces, mesh facets and cloud points."""
    n = 0
    try:
        for o in session.doc.Objects:
            sh = getattr(o, "Shape", None)
            if sh is not None:
                try:
                    if not sh.isNull():
                        n += len(sh.Faces)
                except Exception:
                    pass
            m = getattr(o, "Mesh", None)
            if m is not None and hasattr(m, "CountFacets"):
                n += int(m.CountFacets) // 20
            p = getattr(o, "Points", None)
            if p is not None and hasattr(p, "CountPoints"):
                n += int(p.CountPoints) // 50
    except Exception:
        pass
    return max(1, n)


def _fmt(sec):
    return f"{int(round(sec))} s" if sec < 90 else f"{sec / 60:.0f} min"


class Gate:
    def __init__(self, session):
        self.s = session
        self.offers = {}
        self.start = None

    @staticmethod
    def live():
        """Offers and the ceiling apply where the person's work runs: the
        window, or the hidden FreeCAD under the supervisor. A server used
        in-process (tests, scripts) only logs."""
        import os

        try:
            import FreeCAD

            gui = bool(getattr(FreeCAD, "GuiUp", False))
        except Exception:
            gui = False
        return gui or bool(os.environ.get("AGENTLINK_SUPERVISED")) or bool(os.environ.get("AGENTLINK_GATE"))

    def before(self, tool, args):
        if tool.name not in keep.HEAVY:
            return
        if not self.live():
            self.start = (time.time(), size_of(self.s), _maxrss_mb())
            return
        q = keep.queue_state()
        size = size_of(self.s)
        m = keep.preset(self._overrides())
        ceiling = float(m.get("memoryCeilingGB") or 0)
        mem = keep.estimate({"timings": q.get("memory") or {}}, tool.name, size)
        if ceiling and mem and mem / 1024.0 > ceiling:
            raise ToolError("invalid_state", f"{tool.name} at this size would need about {mem / 1024.0:.1f} GB (from its past runs here), over this Mac's {ceiling:g} GB ceiling for one job: a coarser pass (fewer elements or points) first. The person can raise the ceiling (Mod/AgentLink MemoryCeilingGB).")
        est = keep.estimate(q, tool.name, size)
        key = keep.call_key(tool.name, args)
        now = time.time()
        self.offers = {k: t for k, t in self.offers.items() if now - t < OFFER_KEEP}
        if est and est > OFFER_OVER and key not in self.offers:
            self.offers[key] = now
            rough = keep.estimate(q, tool.name, max(1, size / 4.0)) or est / 4.0
            raise ToolError("invalid_state", f"{tool.name} takes about {_fmt(est)} here at this size (from its past runs on this Mac); a rough pass (a coarser mesh, fewer points) takes about {_fmt(rough)} — run that first? The same call again runs it as asked. Nothing was run.")
        # A saved copy before a heavy change (A): a crash or a hang in it
        # loses nothing done before.
        if tool.changes:
            try:
                self.s.checkpoint(f"before {tool.name}", auto=True)
            except Exception:
                pass
        self.start = (now, size, _maxrss_mb())

    def after(self, tool, args, ok):
        if tool.name not in keep.HEAVY or self.start is None:
            return
        t0, size, (a0, b0) = self.start
        self.start = None
        if not ok:
            return
        secs = time.time() - t0
        try:
            q = keep.queue_state()
            keep.log_timing(q, tool.name, size, secs)
            a1, b1 = _maxrss_mb()
            peak = max(a1 if a1 > a0 else 0, b1 if b1 > b0 else 0)
            if peak:
                mem = {"timings": q.get("memory") or {}}
                keep.log_timing(mem, tool.name, size, peak)
                q["memory"] = mem["timings"]
            keep.save_queue(q)
        except Exception:
            pass

    @staticmethod
    def _overrides():
        try:
            import FreeCAD

            g = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Mod/AgentLink")
            return {"heavySlots": g.GetInt("HeavySlots", 0), "memoryCeilingGB": g.GetFloat("MemoryCeilingGB", 0.0)}
        except Exception:
            return {}
