# SPDX-License-Identifier: LGPL-2.1-or-later
# Pictures of the parts without a screen: an orthographic software
# rasteriser in numpy — shaded, with silhouettes and creases drawn, cut open
# at a plane, painted with a map (wall thickness, stress, a scan's
# deviation) and a legend — written as PNG. So an agent can look at what it
# made, headless, and see where a number says something is wrong.
#
# What is drawn comes in layers. The parts are a layer of their own; other
# families add theirs (register_layer: a scan, a reference) and their maps
# (register_map: stress, deviation …), each a function of the session.

import math
import struct
import zlib

import numpy as np

# Camera: the direction from the part towards the eye, and which way is up.
VIEWS = {
    "iso": ((1, -1, 1), (0, 0, 1)),
    "iso-front-left": ((-1, -1, 1), (0, 0, 1)),
    "iso-rear-left": ((-1, 1, 1), (0, 0, 1)),
    "iso-rear-right": ((1, 1, 1), (0, 0, 1)),
    "top": ((0, 0, 1), (0, 1, 0)),
    "bottom": ((0, 0, -1), (0, -1, 0)),
    "front": ((0, -1, 0), (0, 0, 1)),
    "rear": ((0, 1, 0), (0, 0, 1)),
    "left": ((-1, 0, 0), (0, 0, 1)),
    "right": ((1, 0, 0), (0, 0, 1)),
}
PALETTE = [(0.72, 0.45, 0.30), (0.40, 0.58, 0.78), (0.55, 0.70, 0.45), (0.80, 0.70, 0.40), (0.65, 0.50, 0.75), (0.45, 0.70, 0.70)]
BG = np.array([0.17, 0.19, 0.22])
LINE = np.array([0.06, 0.06, 0.07])
UNMEASURED = np.array([0.55, 0.55, 0.55])

LAYERS = {}
MAPS = {}


def register_layer(name, fn, about=""):
    """fn(session, args) -> [layer]; a layer is { name, V (n,3), F (m,3),
    color (r,g,b) or colors (n,3), creases: bool }."""
    LAYERS[name] = (fn, about)


def register_map(name, fn, about=""):
    """fn(session, args) -> { layers: [layer with values (n,)], unit, range?
    [lo, hi], symmetric?: bool, cmap?: "jet"|"jet_r", note? }."""
    MAPS[name] = (fn, about)


# ---- colour --------------------------------------------------------------------------------

def jet(t):
    t = np.clip(t, 0.0, 1.0)
    r = np.clip(1.5 - np.abs(4 * t - 3), 0, 1)
    g = np.clip(1.5 - np.abs(4 * t - 2), 0, 1)
    b = np.clip(1.5 - np.abs(4 * t - 1), 0, 1)
    return np.stack([r, g, b], axis=-1)


def colours_for(values, lo, hi, cmap="jet"):
    v = np.asarray(values, dtype=float)
    t = (v - lo) / (hi - lo) if hi > lo else np.zeros_like(v)
    if cmap == "jet_r":
        t = 1 - t
    c = jet(t)
    bad = ~np.isfinite(v)
    c[bad] = UNMEASURED
    return c


# ---- a tiny font for labels ---------------------------------------------------------------

_GLYPHS = {
    "0": "0e1113151911 0e", "1": "040c040404040e", "2": "0e11010204081f", "3": "1f020402011 10e",
    "4": "02060a121f0202", "5": "1f101e0101110e", "6": "0608101e11110e", "7": "1f010204080808",
    "8": "0e11110e11110e", "9": "0e11110f01020c", ".": "00000000000c0c", "-": "000000 1f000000",
    "+": "0004041f040400", ":": "000c0c000c0c00", "/": "00010204081000", "%": "18190204081303",
    " ": "00000000000000", "_": "0000000000001f", "(": "02040808080402", ")": "08040202020408",
    "A": "0e11111f111111", "B": "1e11111e11111e", "C": "0e11101010110e", "D": "1c12111111121c",
    "E": "1f10101e10101f", "F": "1f10101e101010", "G": "0e11101713110f", "H": "1111111f111111",
    "I": "0e04040404040e", "J": "07020202021 20c", "K": "11121418141211", "L": "1010101010101f",
    "M": "111b1515111111", "N": "11111915131111", "O": "0e11111111110e", "P": "1e11111e101010",
    "Q": "0e11111115120d", "R": "1e11111e141211", "S": "0f10100e01011e", "T": "1f040404040404",
    "U": "1111111111110e", "V": "11111111110a04", "W": "11111115151b11", "X": "11110a040a1111",
    "Y": "11110a04040404", "Z": "1f01020408101f",
}


def _glyph(ch):
    g = _GLYPHS.get(ch.upper(), _GLYPHS[" "]).replace(" ", "")
    return [int(g[i:i + 2], 16) for i in range(0, 14, 2)]


def text(img, s, x, y, colour=(0.92, 0.92, 0.92), scale=2):
    """Write s at (x, y), top-left, in 5 x 7 cells scaled up."""
    h, w = img.shape[:2]
    cx = x
    for ch in s:
        for row, bits in enumerate(_glyph(ch)):
            for col in range(5):
                if bits & (1 << (4 - col)):
                    y0, x0 = y + row * scale, cx + col * scale
                    if 0 <= y0 < h - scale and 0 <= x0 < w - scale:
                        img[y0:y0 + scale, x0:x0 + scale] = colour
        cx += 6 * scale
    return cx


# ---- the rasteriser -----------------------------------------------------------------------

def _basis(view):
    d, up = VIEWS[view]
    back = np.array(d, dtype=float)
    back /= np.linalg.norm(back)
    right = np.cross(np.array(up, dtype=float), back)
    right /= np.linalg.norm(right)
    upv = np.cross(back, right)
    return right, upv, back


def _vertex_colours(layer):
    n = len(layer["V"])
    if layer.get("colors") is not None:
        return np.asarray(layer["colors"], dtype=float)
    return np.tile(np.asarray(layer.get("color", PALETTE[0]), dtype=float), (n, 1))


def draw(layers, view, width, height, frame=None, section=None, margin=14, bottom=0):
    """One view of the layers, shaded: (rgb image h x w x 3 in 0..1,
    millimetres across, whether anything was drawn)."""
    right, upv, back = _basis(view)
    light = back + 0.5 * upv + 0.2 * right
    light /= np.linalg.norm(light)
    img = np.tile(BG, (height, width, 1))
    zbuf = np.full((height, width), -np.inf)
    fid = np.full((height, width), -1, dtype=np.int64)
    bmask = np.zeros((height, width), bool)

    sec_n, sec_d = None, None
    if section:
        ax = section.get("axis", "x")
        sec_n = np.array({"x": (1, 0, 0), "y": (0, 1, 0), "z": (0, 0, 1)}[ax] if isinstance(ax, str) else ax, dtype=float)
        sec_n /= np.linalg.norm(sec_n)
        # Keep the half away from the eye, so the cut faces it.
        sec_sign = 1.0
        if (sec_n @ back < 0) != bool(section.get("flip")):
            sec_n, sec_sign = -sec_n, -1.0

    allv = [l["V"] for l in layers if len(l["F"])]
    if not allv:
        return img, 0.0, False
    P = np.concatenate(allv)
    if sec_n is not None:
        sec_d = sec_sign * float(section["offset"]) if "offset" in section else float(np.median(P @ sec_n))
        keep = P @ sec_n <= sec_d + 1e-9
        # Framed on the whole part, so a cut does not move the camera.
    if frame:
        at = np.asarray(frame["at"], dtype=float)
        half = float(frame["size"]) / 2
        cx, cy = at @ right, at @ upv
        sx = sy = 2 * half
    else:
        xs, ys = P @ right, P @ upv
        cx, cy = (xs.min() + xs.max()) / 2, (ys.min() + ys.max()) / 2
        sx, sy = max(np.ptp(xs), 1e-6), max(np.ptp(ys), 1e-6)
    avail_w, avail_h = width - 2 * margin, height - 2 * margin - bottom
    scale = min(avail_w / sx, avail_h / sy)
    ox, oy = width / 2 - cx * scale, margin + avail_h / 2 + cy * scale

    face_normals, face_crease, face_tint = [], [], []
    base = 0
    for layer in layers:
        V, F = np.asarray(layer["V"], dtype=float), np.asarray(layer["F"], dtype=np.int64)
        if not len(F):
            continue
        C = _vertex_colours(layer)
        T = V[F]
        n = np.cross(T[:, 1] - T[:, 0], T[:, 2] - T[:, 0])
        ln = np.linalg.norm(n, axis=1)
        ok = ln > 1e-12
        n[ok] /= ln[ok, None]
        # Seen from behind (the inside, through a cut): tinted, so a cut reads.
        behind = (n @ back) < 0
        shade = 0.22 + 0.78 * np.abs(n @ light)
        face_normals.append(n)
        face_crease.append(np.full(len(F), bool(layer.get("creases", True))))
        face_tint.append(behind)
        px = V @ right * scale + ox
        py = oy - V @ upv * scale
        pz = V @ back
        tx, ty, tz = px[F], py[F], pz[F]
        tc = C[F]
        x0 = np.clip(np.floor(tx.min(1)).astype(int), 0, width - 1)
        x1 = np.clip(np.ceil(tx.max(1)).astype(int), 0, width - 1)
        y0 = np.clip(np.floor(ty.min(1)).astype(int), 0, height - 1)
        y1 = np.clip(np.ceil(ty.max(1)).astype(int), 0, height - 1)
        span = (x1 - x0 + 1) * (y1 - y0 + 1)
        small = span <= 4
        # Large triangles one by one, by their pixels.
        for i in np.nonzero(~small & ok)[0]:
            xs_ = np.arange(x0[i], x1[i] + 1) + 0.5
            ys_ = np.arange(y0[i], y1[i] + 1) + 0.5
            gx, gy = np.meshgrid(xs_, ys_)
            (a0, a1, a2), (b0, b1, b2) = tx[i], ty[i]
            den = (b1 - b2) * (a0 - a2) + (a2 - a1) * (b0 - b2)
            if abs(den) < 1e-12:
                continue
            w0 = ((b1 - b2) * (gx - a2) + (a2 - a1) * (gy - b2)) / den
            w1 = ((b2 - b0) * (gx - a2) + (a0 - a2) * (gy - b2)) / den
            w2 = 1 - w0 - w1
            m = (w0 >= -1e-6) & (w1 >= -1e-6) & (w2 >= -1e-6)
            if not m.any():
                continue
            z = w0 * tz[i, 0] + w1 * tz[i, 1] + w2 * tz[i, 2]
            if sec_n is not None:
                W = T[i]
                s_ = (w0[..., None] * W[0] + w1[..., None] * W[1] + w2[..., None] * W[2]) @ sec_n
                m &= s_ <= sec_d
            sub = zbuf[y0[i]:y1[i] + 1, x0[i]:x1[i] + 1]
            m &= z > sub
            if not m.any():
                continue
            sub[m] = z[m]
            col = (w0[..., None] * tc[i, 0] + w1[..., None] * tc[i, 1] + w2[..., None] * tc[i, 2]) * shade[i]
            bmask[y0[i]:y1[i] + 1, x0[i]:x1[i] + 1][m] = behind[i]
            img[y0[i]:y1[i] + 1, x0[i]:x1[i] + 1][m] = col[m]
            fid[y0[i]:y1[i] + 1, x0[i]:x1[i] + 1][m] = base + i
        # Small ones as points, nearest last.
        idx = np.nonzero(small & ok)[0]
        if len(idx):
            cxp = tx[idx].mean(1)
            cyp = ty[idx].mean(1)
            czp = tz[idx].mean(1)
            if sec_n is not None:
                keep = T[idx].mean(1) @ sec_n <= sec_d
                idx, cxp, cyp, czp = idx[keep], cxp[keep], cyp[keep], czp[keep]
            ix = np.clip(cxp.astype(int), 0, width - 1)
            iy = np.clip(cyp.astype(int), 0, height - 1)
            order = np.argsort(czp)
            flat = iy[order] * width + ix[order]
            zz = np.full(height * width, -np.inf)
            zz[flat] = czp[order]
            owner = np.full(height * width, -1, dtype=np.int64)
            owner[flat] = idx[order]
            win = zz > zbuf.ravel()
            pix = np.nonzero(win)[0]
            if len(pix):
                f = owner[pix]
                col = tc[f].mean(1) * shade[f, None]
                bmask.ravel()[pix] = behind[f]
                zbuf.ravel()[pix] = zz[pix]
                img.reshape(-1, 3)[pix] = col
                fid.ravel()[pix] = base + f
        base += len(F)

    # Lines: silhouettes (a part against the background, or a jump in
    # depth) and creases (neighbouring faces of a CAD part meeting at over
    # 35°; a scan's own facets are not drawn).
    fn = np.concatenate(face_normals) if face_normals else np.zeros((0, 3))
    fc = np.concatenate(face_crease) if face_crease else np.zeros(0, bool)
    diag = math.hypot(sx, sy)
    edge = np.zeros((height, width), bool)
    for dy, dx in ((0, 1), (1, 0)):
        a = fid[: height - dy, : width - dx]
        b = fid[dy:, dx:]
        za = zbuf[: height - dy, : width - dx]
        zb = zbuf[dy:, dx:]
        diff = a != b
        sil = diff & ((a < 0) ^ (b < 0))
        both = diff & (a >= 0) & (b >= 0)
        with np.errstate(invalid="ignore"):
            dz = np.where(np.isfinite(za) & np.isfinite(zb), za - zb, 0.0)
        jump = both & (np.abs(dz) > 0.015 * diag)
        crease = np.zeros_like(both)
        if both.any():
            ia, ib = a[both], b[both]
            cosang = np.einsum("ij,ij->i", fn[ia], fn[ib])
            crease[both] = (cosang < math.cos(math.radians(35))) & fc[ia] & fc[ib]
        e = sil | jump | crease
        edge[: height - dy, : width - dx] |= e
    # Inside a solid, seen through a cut: hatched, so the cut reads as one.
    if sec_n is not None and bmask.any():
        yy, xx = np.mgrid[0:height, 0:width]
        hatch = ((xx + yy) % 7) < 2
        img[bmask] = img[bmask] * 0.45 + np.array([0.62, 0.16, 0.14]) * 0.55
        img[bmask & hatch] *= 0.6
    img[edge] = img[edge] * 0.25 + LINE * 0.75
    drawn = bool((fid >= 0).any())
    return img, (width / scale), drawn


def legend(img, lo, hi, unit, cmap, label, y):
    h, w = img.shape[:2]
    x0, x1 = 20, w - 20
    bar = np.linspace(0, 1, max(2, x1 - x0))
    cols = jet(1 - bar if cmap == "jet_r" else bar)
    img[y:y + 12, x0:x1] = cols[None, :, :]
    fmt = (lambda v: f"{v:.3f}") if max(abs(lo), abs(hi)) < 1 else (lambda v: f"{v:.2f}") if max(abs(lo), abs(hi)) < 100 else (lambda v: f"{v:.0f}")
    text(img, fmt(lo), x0, y + 16, scale=2)
    mid = fmt((lo + hi) / 2)
    text(img, mid, (x0 + x1) // 2 - len(mid) * 6, y + 16, scale=2)
    s = f"{fmt(hi)} {unit}"
    text(img, s, x1 - len(s) * 12, y + 16, scale=2)
    if label:
        text(img, label, x0, y - 18, scale=2)


def png(img):
    """An RGB image (h x w x 3, 0..1) as PNG bytes."""
    a = (np.clip(img, 0, 1) * 255 + 0.5).astype(np.uint8)
    h, w = a.shape[:2]
    raw = b"".join(b"\x00" + a[r].tobytes() for r in range(h))

    def chunk(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b"")


def compose(layers, views, width, height, frame=None, section=None, mapinfo=None):
    """One picture: a view, or a grid of views each fitted; the map's legend
    under them. (image, per-view mm across, drawn)."""
    n = len(views)
    cols = 1 if n == 1 else 2 if n <= 4 else 3
    rows = math.ceil(n / cols)
    leg = 54 if mapinfo else 0
    cw, ch = width // cols, (height - leg) // rows
    img = np.tile(BG, (height, width, 1))
    across, any_drawn = {}, False
    for k, v in enumerate(views):
        r, c = divmod(k, cols)
        sub, mm, drawn = draw(layers, v, cw, ch, frame=frame, section=section)
        any_drawn |= drawn
        img[r * ch:(r + 1) * ch, c * cw:(c + 1) * cw] = sub
        if n > 1:
            img[r * ch:(r + 1) * ch, c * cw] = BG * 0.6
            img[r * ch, c * cw:(c + 1) * cw] = BG * 0.6
        text(img, v.upper(), c * cw + 8, r * ch + 8, scale=2)
        across[v] = round(mm, 2)
    if mapinfo:
        legend(img, mapinfo["range"][0], mapinfo["range"][1], mapinfo.get("unit", ""), mapinfo.get("cmap", "jet"), mapinfo.get("label", ""), height - leg + 22)
    return img, across, any_drawn


# ---- the parts as a layer -------------------------------------------------------------------

def body_layer(o, i, tol=0.05):
    pts, tris = o.Shape.tessellate(tol)
    V = np.array([[p.x, p.y, p.z] for p in pts], dtype=float)
    F = np.array(tris, dtype=np.int64).reshape(-1, 3)
    return {"name": o.Label, "V": V, "F": F, "color": PALETTE[i % len(PALETTE)], "creases": True}


def vertex_normals(V, F):
    T = V[F]
    n = np.cross(T[:, 1] - T[:, 0], T[:, 2] - T[:, 0])
    vn = np.zeros_like(V)
    for k in range(3):
        np.add.at(vn, F[:, k], n)
    ln = np.linalg.norm(vn, axis=1)
    ok = ln > 0
    vn[ok] /= ln[ok, None]
    return vn


def nearest_values(points, src_points, src_values, chunk=2048):
    """Each point takes the value of the nearest source point (brute force
    in chunks: no scipy)."""
    P = np.asarray(points, dtype=float)
    S = np.asarray(src_points, dtype=float)
    out = np.empty(len(P))
    for i in range(0, len(P), chunk):
        d = ((P[i:i + chunk, None, :] - S[None, :, :]) ** 2).sum(-1)
        out[i:i + chunk] = np.asarray(src_values)[d.argmin(1)]
    return out


def even_length(V, F, cap, floor=None):
    """The longest edge that lets the whole surface be split evenly within
    cap triangles: so no face is left whole when the budget runs out on
    the others."""
    T = np.asarray(V, dtype=float)[np.asarray(F, dtype=np.int64)]
    area = float(np.linalg.norm(np.cross(T[:, 1] - T[:, 0], T[:, 2] - T[:, 0]), axis=1).sum() / 2)
    L = math.sqrt(area / (0.433 * 0.5 * cap)) if area > 0 else 1.0
    return max(L, floor or 0.0, 0.2)


def subdivide(V, F, maxlen, cap=60000):
    """Split triangles until no edge is over maxlen (or the count reaches
    cap): so a map has points to show on a big flat face. Unshared
    vertices out — each triangle its own three."""
    T = np.asarray(V, dtype=float)[np.asarray(F, dtype=np.int64)]
    while len(T) < cap:
        e = np.stack([np.linalg.norm(T[:, 1] - T[:, 0], axis=1), np.linalg.norm(T[:, 2] - T[:, 1], axis=1), np.linalg.norm(T[:, 0] - T[:, 2], axis=1)], axis=1)
        long_ = e.max(1) > maxlen
        if not long_.any():
            break
        if len(T) + 3 * long_.sum() > cap:
            # Split the longest first, as many as fit.
            room = max(0, (cap - len(T)) // 3)
            if room == 0:
                break
            order = np.argsort(-e.max(1))[:room]
            long_ = np.zeros(len(T), bool)
            long_[order] = True
        A = T[long_]
        a, b, c = A[:, 0], A[:, 1], A[:, 2]
        ab, bc, ca = (a + b) / 2, (b + c) / 2, (c + a) / 2
        new = np.concatenate([np.stack([a, ab, ca], 1), np.stack([ab, b, bc], 1), np.stack([ca, bc, c], 1), np.stack([ab, bc, ca], 1)])
        T = np.concatenate([T[~long_], new])
    n = len(T)
    return T.reshape(-1, 3), np.arange(3 * n, dtype=np.int64).reshape(n, 3)


def cast(origins, dirs, V, F, reach, group=48, chunk_cells=4_000_000):
    """For each ray, the distance to the nearest triangle it meets past
    2 µm and within reach, else nan (Möller–Trumbore). Rays go in groups of
    neighbours, each against only the triangles within reach of the group:
    a wall map's rays look a few millimetres in, not across the part."""
    V = np.asarray(V, dtype=float)
    F = np.asarray(F, dtype=np.int64)
    T = V[F]
    tlo, thi = T.min(1), T.max(1)
    O = np.asarray(origins, dtype=float)
    D = np.asarray(dirs, dtype=float)
    out = np.full(len(O), np.nan)
    if not len(O) or not len(F):
        return out
    finite = np.isfinite(reach) and reach < 1e8
    # Neighbours together: sorted along a coarse grid.
    cell = max((float(reach) / 3) if finite else np.ptp(O, axis=0).max(), 1e-6)
    key = np.floor((O - O.min(0)) / cell).astype(np.int64)
    order = np.lexsort((key[:, 2], key[:, 1], key[:, 0]))
    for i in range(0, len(O), group):
        idx = order[i:i + group]
        if finite:
            # The box the group's ray segments sweep, not a ball round them.
            ends = O[idx] + D[idx] * reach
            lo = np.minimum(O[idx].min(0), ends.min(0)) - 1e-6
            hi = np.maximum(O[idx].max(0), ends.max(0)) + 1e-6
            near = np.nonzero(np.all((thi >= lo) & (tlo <= hi), axis=1))[0]
        else:
            near = np.arange(len(F))
        if not len(near):
            continue
        v0, e1, e2 = T[near, 0], T[near, 1] - T[near, 0], T[near, 2] - T[near, 0]
        step = max(1, chunk_cells // len(near))
        for j in range(0, len(idx), step):
            r = idx[j:j + step]
            o, d = O[r, None, :], D[r, None, :]
            h = np.cross(d, e2[None])
            a = (e1[None] * h).sum(-1)
            ok = np.abs(a) > 1e-12
            f = np.where(ok, 1.0 / np.where(ok, a, 1.0), 0.0)
            s_ = o - v0[None]
            u = f * (s_ * h).sum(-1)
            q = np.cross(s_, e1[None])
            v = f * (q * d).sum(-1)
            t = f * (e2[None] * q).sum(-1)
            m = ok & (u >= -1e-9) & (v >= -1e-9) & (u + v <= 1 + 1e-9) & (t > 2e-3) & (t <= reach)
            best = np.where(m, t, np.inf).min(1)
            out[r] = np.where(np.isfinite(best), best, np.nan)
    return out


def wall_map(V, F, reach=12.0, maxlen=None, cap=16000, closed=True):
    """The wall at each spot of a solid's surface: straight in from each
    small triangle's middle to where the ray leaves the material. A
    triangle the ray leaves past reach reads reach (thick). A solid's
    tessellation is closed; for a mesh that may not be (closed=False) a ray
    that finds no way out at all reads nan. (V, F, values per vertex)."""
    V = np.asarray(V, dtype=float)
    F = np.asarray(F, dtype=np.int64)
    SV, SF = subdivide(V, F, even_length(V, F, cap, maxlen), cap=cap)
    T = SV[SF]
    c = T.mean(1)
    n = np.cross(T[:, 1] - T[:, 0], T[:, 2] - T[:, 0])
    ln = np.linalg.norm(n, axis=1)
    ok = ln > 1e-12
    n[ok] /= ln[ok, None]
    d = cast(c - n * 1e-3, -n, V, F, reach * 1.0001)
    # No hit within reach: thick if the ray had room to go (inside the
    # bounding box for reach), else the mesh is open there.
    far = np.isnan(d)
    if closed:
        d[far] = reach
    elif far.any():
        d2 = cast(c[far] - n[far] * 1e-3, -n[far], V, F, 1e9)
        d[np.nonzero(far)[0][np.isfinite(d2)]] = reach
    vals = np.repeat(d, 3)
    return SV, SF, vals
