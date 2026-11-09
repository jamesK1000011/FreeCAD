# SPDX-License-Identifier: LGPL-2.1-or-later
# Packing files to hand on: a PCB order (Gerbers, drill, BOM and placement
# files sorted, the board's size read from its outline Gerber, an order
# sheet) and a library pack (STEP and STL with nothing personal left in
# their headers, a README, a licence, the credits checked against it). No
# FreeCAD needed here.

import datetime
import getpass
import os
import re
import zipfile

# ---- PCB orders ---------------------------------------------------------------------------

GERBER_EXT = {".gbr", ".gtl", ".gbl", ".gto", ".gbo", ".gts", ".gbs", ".gtp", ".gbp", ".gko", ".gm1", ".gml", ".gbrjob", ".gm", ".gg1"}
GERBER_INNER = re.compile(r"\.g\d+$|\.gl\d+$", re.I)
DRILL_EXT = {".drl", ".xln", ".exc", ".drd"}


def classify(paths):
    """{gerbers, drill, bom, cpl, other}: each a list of the paths given."""
    out = {"gerbers": [], "drill": [], "bom": [], "cpl": [], "other": []}
    for p in paths:
        base = os.path.basename(p).lower()
        ext = os.path.splitext(base)[1]
        if ext in GERBER_EXT or GERBER_INNER.search(base):
            out["gerbers"].append(p)
        elif ext in DRILL_EXT or (ext == ".txt" and "drill" in base):
            out["drill"].append(p)
        elif ext in (".csv", ".xlsx", ".xls", ".txt") and re.search(r"cpl|pos|position|centroid|placement|pnp|pick", base):
            out["cpl"].append(p)
        elif ext in (".csv", ".xlsx", ".xls", ".txt") and "bom" in base:
            out["bom"].append(p)
        else:
            out["other"].append(p)
    return out


def is_outline(path):
    b = os.path.basename(path).lower()
    return "edge_cuts" in b or "edge.cuts" in b or b.endswith((".gko", ".gm1", ".gml", ".gm"))


def is_copper(path):
    b = os.path.basename(path).lower()
    return bool(re.search(r"[fb]_cu|in\d+_cu|\.gtl$|\.gbl$|\.g\d+$|\.gl\d+$", b))


def gerber_box(text):
    """The extent of a Gerber file's coordinates, mm: [xmin, ymin, xmax,
    ymax], or None. Reads %FS…% and %MO…%; draws and moves alike."""
    fs = re.search(r"%FS[LT]?A?X(\d)(\d)Y(\d)(\d)\*%", text)
    if not fs:
        return None
    xd, yd = int(fs.group(2)), int(fs.group(4))
    inch = "%MOIN*%" in text
    k = 25.4 if inch else 1.0
    xs, ys = [], []
    x = y = 0
    for m in re.finditer(r"(?:X(-?\d+))?(?:Y(-?\d+))?(?:I-?\d+)?(?:J-?\d+)?D0?[123]\*", text):
        if m.group(1) is None and m.group(2) is None:
            continue
        if m.group(1) is not None:
            x = int(m.group(1)) / 10 ** xd * k
        if m.group(2) is not None:
            y = int(m.group(2)) / 10 ** yd * k
        xs.append(x)
        ys.append(y)
    if not xs:
        return None
    return [min(xs), min(ys), max(xs), max(ys)]


VENDORS = {
    "jlcpcb": {
        "name": "JLCPCB",
        "upload": "Upload the Gerber zip (Gerbers and the drill file together) on the PCB quote page.",
        "assembly": "For assembly, upload the BOM (Comment, Designator, Footprint, LCSC part number) and the CPL (Designator, Mid X, Mid Y, Layer, Rotation) when asked; check every part's rotation in the preview.",
        "quantity": 5,
    },
    "pcbway": {
        "name": "PCBWay",
        "upload": "Upload the Gerber zip on the PCB instant quote.",
        "assembly": "For assembly, the BOM and the centroid (placement) file go with the assembly quote.",
        "quantity": 5,
    },
    "oshpark": {
        "name": "OSH Park",
        "upload": "Upload the Gerber zip (or the .kicad_pcb itself). Boards come in sets of three.",
        "assembly": "OSH Park makes bare boards only: the BOM and placement files are for your own assembly.",
        "quantity": 3,
    },
}


def order_sheet(vendor, board, files, quantity=None, drc=None, finish="HASL (lead-free)", colour="green"):
    """The order sheet, Markdown: the board's figures, the files, what to
    upload where."""
    v = VENDORS[vendor]
    q = quantity or v["quantity"]
    if vendor == "oshpark" and q % 3:
        q += 3 - q % 3
    w, d = board.get("size") or (None, None)
    rows = [
        f"# PCB order — {board.get('name') or 'board'}",
        "",
        f"For {v['name']}. Written {datetime.date.today().isoformat()}. Check every figure against the board before paying.",
        "",
        "| | |",
        "|---|---|",
        f"| Size | {f'{w:.2f} x {d:.2f} mm' if w else 'not known — read it from the outline'} |",
        f"| Copper layers | {board.get('layers') or 'not known'} |",
        f"| Thickness | {board.get('thickness', 1.6)} mm |",
        f"| Quantity | {q} |",
        f"| Surface finish | {finish} |",
        f"| Solder mask | {colour} |",
        "",
        "## Files",
        "",
    ]
    for kind in ("gerbers", "drill", "bom", "cpl", "other"):
        if files.get(kind):
            rows.append(f"- {kind}: " + ", ".join(os.path.basename(p) for p in files[kind]))
    rows += ["", "## Design rule check", ""]
    if drc is None:
        rows.append("Not run: kicad-cli was not found on this computer. Run Inspect › Design Rules Checker in KiCad before ordering.")
    else:
        rows.append(drc)
    rows += ["", "## Ordering", "", f"- {v['upload']}", f"- {v['assembly']}", "- Placing the order and paying are yours to do."]
    return "\n".join(rows) + "\n"


def write_zip(path, entries):
    """A zip of [(name in the zip, source path or bytes)], deflated, with no
    folder of the computer's in it."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, src in entries:
            info = zipfile.ZipInfo(name.replace("\\", "/").lstrip("/"), date_time=datetime.datetime.now().timetuple()[:6])
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            if isinstance(src, (bytes, bytearray)):
                z.writestr(info, bytes(src))
            elif isinstance(src, str) and os.path.isfile(src):
                with open(src, "rb") as f:
                    z.writestr(info, f.read())
            else:
                z.writestr(info, str(src).encode("utf-8"))
    return path


# ---- library packs ------------------------------------------------------------------------

LICENSES = {
    "CC-BY-4.0": ("Creative Commons Attribution 4.0 International", "https://creativecommons.org/licenses/by/4.0/"),
    "CC-BY-SA-4.0": ("Creative Commons Attribution-ShareAlike 4.0 International", "https://creativecommons.org/licenses/by-sa/4.0/"),
    "CC0-1.0": ("CC0 1.0 Universal", "https://creativecommons.org/publicdomain/zero/1.0/"),
    "MIT": ("MIT License", "https://opensource.org/license/mit"),
}

MIT_TEXT = """MIT License

Copyright (c) {year} {holder}

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""


def license_text(spdx, holder, title):
    """The LICENSE file. MIT whole; the Creative Commons ones as their
    notice and link, as Creative Commons asks works to be marked."""
    year = datetime.date.today().year
    if spdx == "MIT":
        return MIT_TEXT.format(year=year, holder=holder)
    name, url = LICENSES[spdx]
    if spdx == "CC0-1.0":
        who = holder or "The person who associated a work with this deed"
        return (
            f"{title}\n\n{who} has dedicated this work to the public domain by waiving all of their rights to the work "
            f"worldwide under copyright law, including all related and neighboring rights, to the extent allowed by law.\n\n"
            f"{name}: {url}\nThe full legal code: {url}legalcode\n"
        )
    return f"{title} © {year} {holder}\n\nThis work is licensed under {name} ({spdx}).\nTo view a copy of this license, visit {url}\nThe full legal code: {url}legalcode\n"


def check_credits(spdx, credits):
    """(refusals, warnings) for remixed parts' licences against the pack's."""
    errors, warnings = [], []
    for i, c in enumerate(credits):
        lic = str(c.get("license") or "").upper().replace(" ", "-")
        what = c.get("title") or c.get("url") or f"credits.{i}"
        if not lic:
            warnings.append(f"{what}: no licence given — check its page says you may remix and share it.")
            continue
        if "ND" in lic.split("-") or "NODERIVS" in lic or "NODERIVATIVES" in lic:
            errors.append(f"{what} is {c['license']}: no derivatives, so it cannot be remixed into a pack at all — leave it out, or ask its author.")
        elif "NC" in lic.split("-"):
            errors.append(f"{what} is {c['license']}: non-commercial, which none of CC-BY-4.0, CC-BY-SA-4.0, CC0-1.0 or MIT keeps — leave it out, or ask its author.")
        elif "SA" in lic.split("-") and spdx != "CC-BY-SA-4.0":
            errors.append(f"{what} is {c['license']}: share-alike, so the remix must be CC-BY-SA-4.0.")
        elif "GPL" in lic and spdx not in ("CC-BY-SA-4.0",):
            errors.append(f"{what} is {c['license']}: the remix must stay under the GPL, which this pack does not offer — leave it out.")
        elif lic.startswith("CC-BY") and spdx == "CC0-1.0":
            errors.append(f"{what} is {c['license']}: its author must be credited, which CC0 does not keep — choose CC-BY-4.0 or CC-BY-SA-4.0.")
        elif lic.startswith("CC-BY") and spdx == "MIT":
            warnings.append(f"{what} is {c['license']}: its part stays CC BY; the credit in the README keeps that.")
    return errors, warnings


def private_strings():
    """What must not leave the computer in a file's header: the home folder
    and the user's name."""
    out = []
    home = os.path.expanduser("~")
    if home and home not in ("/", "~"):
        out.append(home)
    try:
        u = getpass.getuser()
        if u and len(u) >= 3:
            out.append(u)
    except Exception:  # no user name to be had: nothing to strip
        pass
    return out


def clean_step(text, name, author=""):
    """A STEP file's text with its header cleaned: FILE_NAME gives the file's
    own name, the author given (or none), no organisation; the home folder
    and user name gone from the header; the home folder gone from the
    data."""
    m = re.search(r"HEADER;(.*?)ENDSEC;", text, re.S)
    if not m:
        return text
    hdr = m.group(1)

    def q(s):
        return "'" + str(s).replace("'", "''") + "'"

    stamp = re.search(r"FILE_NAME\s*\(\s*'(?:[^']|'')*'\s*,\s*'([^']*)'", hdr)
    when = stamp.group(1) if stamp else datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    new_fn = f"FILE_NAME({q(name)},{q(when)},({q(author)}),({q('')}),{q('')},{q('FreeCAD')},{q('')});"
    hdr = re.sub(r"FILE_NAME\s*\(.*?\)\s*;", lambda _: new_fn, hdr, count=1, flags=re.S)
    for s in private_strings():
        hdr = hdr.replace(s, "")
    body = text[m.end():]
    home = os.path.expanduser("~")
    if home and home not in ("/", "~"):
        body = body.replace(home, "")
    return text[: m.start()] + "HEADER;" + hdr + "ENDSEC;" + body


def clean_stl(data, name):
    """An STL's header (binary) or solid name (ASCII) set to the part's name."""
    if data[:5].lower() == b"solid" and b"facet" in data[:400]:
        text = data.decode("ascii", "replace").splitlines()
        if text:
            text[0] = f"solid {name}"
            for i in range(len(text) - 1, -1, -1):
                if text[i].strip().startswith("endsolid"):
                    text[i] = f"endsolid {name}"
                    break
        return ("\n".join(text) + "\n").encode("ascii", "replace")
    head = f"STL {name}".encode("ascii", "replace")[:80].ljust(80, b" ")
    return head + data[80:]


def readme(title, description, author, spdx, parts, settings, credits, notes=()):
    lines = [f"# {title}", ""]
    if description:
        lines += [description, ""]
    lines += [f"By {author}." if author else "", f"Licence: {spdx} — see LICENSE.", "", "## Parts", "", "| File | Size (mm) | Volume (cm³) | Material | Mass (g) |", "|---|---|---|---|---|"]
    for p in parts:
        lines.append(f"| {' / '.join(p['files'])} | {' x '.join(f'{v:g}' for v in p['size'])} | {p['volume'] / 1000:.1f} | {p.get('material') or '—'} | {p.get('mass') if p.get('mass') is not None else '—'} |")
    lines += ["", "## Printing", ""]
    if settings:
        for k, v in settings.items():
            lines.append(f"- {k}: {v}")
    else:
        lines.append("- No print setup recorded: PLA, 0.2 mm layers, 3 walls and 15% infill suit most parts like these.")
    for n in notes:
        lines.append(f"- {n}")
    if credits:
        lines += ["", "## Credits", ""]
        for c in credits:
            bits = [c.get("title") or "A part"]
            if c.get("author"):
                bits.append(f"by {c['author']}")
            if c.get("url"):
                bits.append(f"({c['url']})")
            if c.get("license"):
                bits.append(f"— {c['license']}")
            if c.get("changes"):
                bits.append(f"; changed: {c['changes']}")
            lines.append("- " + " ".join(bits))
    lines += ["", "Made with FreeCAD."]
    return "\n".join(x for x in lines if x is not None) + "\n"
