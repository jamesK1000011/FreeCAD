# SPDX-License-Identifier: LGPL-2.1-or-later
# Printers on the network, by their own published HTTP APIs: OctoPrint
# (REST API, X-Api-Key), Moonraker (Klipper's API server) and PrusaLink
# (its v1 API, an API key or HTTP digest). What is known of each printer is
# kept in a JSON file beside the session's files, readable by its owner
# only; keys and passwords are never given back. Python's standard library
# only. Bambu Lab printers are not here: they take jobs over their own LAN
# mode protocol or from Bambu Studio.

import json
import os
import stat
import urllib.error
import urllib.parse
import urllib.request
import uuid

FILE = ".agentlink-printers.json"
KINDS = ("octoprint", "moonraker", "prusalink")
ALIASES = {"klipper": "moonraker", "mainsail": "moonraker", "fluidd": "moonraker", "prusa": "prusalink"}
BAMBU = ("bambu", "bambulab", "bambu-lab", "bambu lab")
GCODE = (".gcode", ".gco", ".g", ".bgcode")
SECRET = ("key", "password")


class PrinterError(Exception):
    """code: unavailable (cannot reach it), failed (it said no), invalid_state."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


# ---- the config file ----------------------------------------------------------------------

def path(out_dir):
    return os.path.join(out_dir, FILE)


def load(out_dir):
    p = path(out_dir)
    if not os.path.isfile(p):
        return {}
    with open(p, encoding="utf-8") as f:
        try:
            return json.load(f).get("printers", {})
        except ValueError:
            return {}


def save(out_dir, printers):
    os.makedirs(out_dir, exist_ok=True)
    p = path(out_dir)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, stat.S_IRUSR | stat.S_IWUSR)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"printers": printers}, f, indent=1)
    try:
        os.chmod(p, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def public(name, p):
    """A printer as it may be shown: no key, no password."""
    out = {"name": name, **{k: v for k, v in p.items() if k not in SECRET}}
    out["auth"] = "api key" if p.get("key") else ("digest" if p.get("password") else "none")
    return out


def kind_of(kind):
    k = str(kind or "").strip().lower()
    if k in BAMBU:
        raise PrinterError("unavailable", "Bambu Lab printers take jobs over their own LAN-mode protocol (the printer's access code, MQTT and FTPS) or from Bambu Studio, not by an open HTTP API: slice with slice_with { slicer: \"bambustudio\" } and send the file from Bambu Studio, or Bambu Handy.")
    k = ALIASES.get(k, k)
    if k not in KINDS:
        raise PrinterError("invalid_input", f"kind: one of {', '.join(KINDS)} (klipper is moonraker).")
    return k


def normal_url(url):
    u = str(url or "").strip().rstrip("/")
    if "://" not in u:
        u = "http://" + u
    p = urllib.parse.urlparse(u)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise PrinterError("invalid_input", "url: the printer's address, http://host[:port] — as the browser opens it.")
    return u


# ---- HTTP ---------------------------------------------------------------------------------

class Client:
    def __init__(self, p, timeout=10):
        self.p = p
        self.base = p["url"]
        self.timeout = timeout
        handlers = []
        if p.get("password"):
            mgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()
            mgr.add_password(None, self.base, p.get("user") or "maker", p["password"])
            handlers.append(urllib.request.HTTPDigestAuthHandler(mgr))
        self.opener = urllib.request.build_opener(*handlers)

    def request(self, method, route, body=None, headers=None, raw=False):
        h = dict(headers or {})
        if self.p.get("key"):
            h["X-Api-Key"] = self.p["key"]
        data = body
        if isinstance(body, (dict, list)):
            data = json.dumps(body).encode()
            h.setdefault("Content-Type", "application/json")
        req = urllib.request.Request(self.base + route, data=data, headers=h, method=method)
        try:
            with self.opener.open(req, timeout=self.timeout) as r:
                text = r.read()
                status = r.status
        except urllib.error.HTTPError as e:
            detail = e.read()[:200].decode("utf-8", "replace").strip()
            if e.code in (401, 403):
                raise PrinterError("failed", f"{self.base} refused the key or password ({e.code}): printer_add the printer again with the right one.")
            raise PrinterError("failed", f"{method} {route}: the printer said {e.code} {e.reason}{(' — ' + detail) if detail else ''}.")
        except (urllib.error.URLError, OSError) as e:
            reason = getattr(e, "reason", e)
            raise PrinterError("unavailable", f"{self.base}: can't reach it ({reason}) — is it on and on this network, and the address right?")
        if raw:
            return status, text
        if not text:
            return {}
        try:
            return json.loads(text)
        except ValueError:
            return {"text": text[:200].decode("utf-8", "replace")}


def _multipart(fields, file_field, filename, data):
    b = "----agentlink" + uuid.uuid4().hex
    parts = []
    for k, v in fields.items():
        parts.append(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    parts.append(f'--{b}\r\nContent-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode())
    parts.append(data)
    parts.append(f"\r\n--{b}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={b}"


def _t(v):
    return None if v is None else round(float(v), 1)


# ---- per kind -----------------------------------------------------------------------------

def status(p):
    c = Client(p)
    k = p["kind"]
    if k == "octoprint":
        job = c.request("GET", "/api/job")
        try:
            pr = c.request("GET", "/api/printer")
        except PrinterError as e:
            if "409" not in e.message:
                raise
            pr = {}
        temps = pr.get("temperature") or {}
        tool = temps.get("tool0") or {}
        bed = temps.get("bed") or {}
        prog = job.get("progress") or {}
        return {
            "state": job.get("state") or (pr.get("state") or {}).get("text"),
            "nozzle": {"now": _t(tool.get("actual")), "target": _t(tool.get("target"))},
            "bed": {"now": _t(bed.get("actual")), "target": _t(bed.get("target"))},
            "job": {"file": ((job.get("job") or {}).get("file") or {}).get("name"), "progress": None if prog.get("completion") is None else round(prog["completion"], 1), "left": prog.get("printTimeLeft")},
        }
    if k == "moonraker":
        q = c.request("GET", "/printer/objects/query?print_stats&extruder&heater_bed&virtual_sdcard")
        s = (q.get("result") or {}).get("status") or {}
        ps, ex, hb, sd = s.get("print_stats") or {}, s.get("extruder") or {}, s.get("heater_bed") or {}, s.get("virtual_sdcard") or {}
        prog = sd.get("progress")
        return {
            "state": ps.get("state"),
            "nozzle": {"now": _t(ex.get("temperature")), "target": _t(ex.get("target"))},
            "bed": {"now": _t(hb.get("temperature")), "target": _t(hb.get("target"))},
            "job": {"file": ps.get("filename") or None, "progress": None if prog is None else round(100 * prog, 1), "printed": ps.get("print_duration")},
        }
    s = c.request("GET", "/api/v1/status")
    pr, job = s.get("printer") or {}, s.get("job") or {}
    return {
        "state": (pr.get("state") or "").lower() or None,
        "nozzle": {"now": _t(pr.get("temp_nozzle")), "target": _t(pr.get("target_nozzle"))},
        "bed": {"now": _t(pr.get("temp_bed")), "target": _t(pr.get("target_bed"))},
        "job": {"id": job.get("id"), "progress": job.get("progress"), "left": job.get("time_remaining")},
    }


def send(p, file_path, start=False, storage=None):
    c = Client(p, timeout=120)
    name = os.path.basename(file_path)
    with open(file_path, "rb") as f:
        data = f.read()
    k = p["kind"]
    if k == "octoprint":
        fields = {"select": "true" if start else "false", "print": "true" if start else "false"}
        body, ctype = _multipart(fields, "file", name, data)
        r = c.request("POST", "/api/files/" + (storage or "local"), body, {"Content-Type": ctype})
        return {"uploaded": name, "where": (r.get("files") or {}).get("local", {}).get("path") or name, "started": bool(start and r.get("done", True))}
    if k == "moonraker":
        fields = {"root": storage or "gcodes"}
        if start:
            fields["print"] = "true"
        body, ctype = _multipart(fields, "file", name, data)
        r = c.request("POST", "/server/files/upload", body, {"Content-Type": ctype})
        return {"uploaded": name, "where": (r.get("item") or {}).get("path") or name, "started": bool(r.get("print_started", start))}
    route = f"/api/v1/files/{storage or 'usb'}/{urllib.parse.quote(name)}"
    headers = {"Content-Type": "text/x.gcode", "Overwrite": "?1", "Print-After-Upload": "?1" if start else "?0"}
    c.request("PUT", route, data, headers, raw=True)
    return {"uploaded": name, "where": f"{storage or 'usb'}/{name}", "started": bool(start)}


def control(p, action):
    """action: pause or stop (the job's own, as the printer's screen does)."""
    c = Client(p)
    k = p["kind"]
    if k == "octoprint":
        body = {"command": "pause", "action": "pause"} if action == "pause" else {"command": "cancel"}
        c.request("POST", "/api/job", body, raw=True)
        return {"done": action}
    if k == "moonraker":
        c.request("POST", "/printer/print/pause" if action == "pause" else "/printer/print/cancel", raw=True)
        return {"done": action}
    s = c.request("GET", "/api/v1/status")
    jid = (s.get("job") or {}).get("id")
    if jid is None:
        raise PrinterError("invalid_state", "The printer has no job running: nothing to pause or stop.")
    if action == "pause":
        c.request("PUT", f"/api/v1/job/{jid}/pause", raw=True)
    else:
        c.request("DELETE", f"/api/v1/job/{jid}", raw=True)
    return {"done": action, "job": jid}
