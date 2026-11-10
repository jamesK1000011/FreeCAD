# SPDX-License-Identifier: LGPL-2.1-or-later
# ISO 286 limits of size for the common fits: the standard tolerance grades
# IT5 to IT11 and the fundamental deviations of the holes F, G, H, JS and
# the shafts f, g, h, js, k, n, p, for sizes up to 500 mm. "10 H7" gives
# its upper and lower deviations, mm.

import bisect

# Upper ends of the size ranges, mm (each range is over the one before, up to and including this).
RANGES = [3, 6, 10, 18, 30, 50, 80, 120, 180, 250, 315, 400, 500]

IT = {  # µm
    5: [4, 5, 6, 8, 9, 11, 13, 15, 18, 20, 23, 25, 27],
    6: [6, 8, 9, 11, 13, 16, 19, 22, 25, 29, 32, 36, 40],
    7: [10, 12, 15, 18, 21, 25, 30, 35, 40, 46, 52, 57, 63],
    8: [14, 18, 22, 27, 33, 39, 46, 54, 63, 72, 81, 89, 97],
    9: [25, 30, 36, 43, 52, 62, 74, 87, 100, 115, 130, 140, 155],
    10: [40, 48, 58, 70, 84, 100, 120, 140, 160, 185, 210, 230, 250],
    11: [60, 75, 90, 110, 130, 160, 190, 220, 250, 290, 320, 360, 400],
}

# Shafts: the deviation nearest zero, µm — upper (es) for f, g, h; lower (ei) for k, n, p.
SHAFT = {
    "f": ("es", [-6, -10, -13, -16, -20, -25, -30, -36, -43, -50, -56, -62, -68]),
    "g": ("es", [-2, -4, -5, -6, -7, -9, -10, -12, -14, -15, -17, -18, -20]),
    "h": ("es", [0] * 13),
    "k": ("ei", [0, 1, 1, 1, 2, 2, 2, 3, 3, 4, 4, 4, 5]),
    "n": ("ei", [4, 8, 10, 12, 15, 17, 20, 23, 27, 31, 34, 37, 40]),
    "p": ("ei", [6, 12, 15, 18, 22, 26, 32, 37, 43, 50, 56, 62, 68]),
}
# Holes: the lower deviation (EI), µm, for F, G, H (the shafts' mirrored).
HOLE = {
    "F": [6, 10, 13, 16, 20, 25, 30, 36, 43, 50, 56, 62, 68],
    "G": [2, 4, 5, 6, 7, 9, 10, 12, 14, 15, 17, 18, 20],
    "H": [0] * 13,
}


def _range(nominal):
    if not 0 < nominal <= RANGES[-1]:
        raise ValueError(f"{nominal} mm: ISO 286 is tabled here for sizes over 0 up to {RANGES[-1]} mm.")
    return bisect.bisect_left(RANGES, nominal)


def limits(nominal, code):
    """(upper, lower) deviation, mm, of a size nominal with tolerance code
    such as "H7", "g6", "js6", "JS7"."""
    code = str(code).strip()
    letters = code.rstrip("0123456789")
    grade = code[len(letters):]
    if not grade.isdigit() or int(grade) not in IT:
        raise ValueError(f"{code}: a letter and a grade {min(IT)} to {max(IT)} — H7, g6, js6 …")
    i = _range(float(nominal))
    it = IT[int(grade)][i]
    if letters in ("js", "JS"):
        return it / 2000.0, -it / 2000.0
    if letters in SHAFT:
        which, dev = SHAFT[letters]
        d = dev[i]
        if letters == "k" and int(grade) > 7:
            d = 0
        return ((d / 1000.0, (d - it) / 1000.0) if which == "es" else ((d + it) / 1000.0, d / 1000.0))
    if letters in HOLE:
        ei = HOLE[letters][i]
        return (ei + it) / 1000.0, ei / 1000.0
    raise ValueError(f"{code}: the holes F, G, H, JS and the shafts f, g, h, js, k, n, p are tabled here.")


def text(nominal, code=None, plus=None, minus=None, diameter=False):
    """The callout as a drawing writes it: "⌀10 H7 (+0.015/0)", "40 ±0.1",
    "25 +0.2/-0.1"."""
    head = ("⌀" if diameter else "") + _num(nominal)
    if code:
        up, lo = limits(nominal, code)
        return f"{head} {code} ({_dev(up)}/{_dev(lo)})"
    if plus is not None or minus is not None:
        p, m = float(plus or 0), float(minus if minus is not None else plus or 0)
        if abs(p - abs(m)) < 1e-9 and m >= 0:
            return f"{head} ±{_num(p)}"
        lo = -abs(m) if minus is not None and m > 0 else m
        return f"{head} {_dev(p)}/{_dev(lo)}"
    return head


def _num(v):
    s = f"{float(v):.3f}".rstrip("0").rstrip(".")
    return s or "0"


def _dev(v):
    return "0" if abs(v) < 5e-7 else (f"+{_num(v)}" if v > 0 else f"-{_num(-v)}")
