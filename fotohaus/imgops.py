"""Image-processing building blocks in plain NumPy (no OpenCV).

Everything works on float32 arrays in 0..1 (H×W or H×W×C) unless noted.
"""
from __future__ import annotations

import numpy as np


# ------------------------------------------------------------------ resizing

def resize_f(a: np.ndarray, w: int, h: int) -> np.ndarray:
    """Resize a float array (H×W or H×W×C) with antialiased bilinear filtering."""
    from PIL import Image
    w, h = max(1, int(w)), max(1, int(h))
    if a.ndim == 2:
        return np.asarray(Image.fromarray(a.astype(np.float32), "F").resize((w, h), Image.BILINEAR), np.float32)
    return np.stack([resize_f(a[..., c], w, h) for c in range(a.shape[2])], -1)


# ------------------------------------------------------------------ box / gaussian filters

def _box1d(a: np.ndarray, r: int, axis: int) -> np.ndarray:
    """Mean over a (2r+1) window along one axis, edges replicated. O(N) via cumulative sums."""
    if r <= 0:
        return a
    a = np.moveaxis(a.astype(np.float32, copy=False), axis, 0)
    n = a.shape[0]
    first, last = a[:1], a[-1:]
    p = np.concatenate([np.repeat(first, r + 1, 0), a, np.repeat(last, r, 0)], 0)
    c = np.cumsum(p, axis=0, dtype=np.float32)
    out = (c[2 * r + 1:2 * r + 1 + n] - c[:n]) * (1.0 / (2 * r + 1))
    return np.moveaxis(out, 0, axis)


def box(a: np.ndarray, r: int) -> np.ndarray:
    return _box1d(_box1d(a, r, 0), r, 1)


def gaussian(a: np.ndarray, sigma: float) -> np.ndarray:
    """Gaussian blur, approximated by three box passes (accurate to a few percent)."""
    sigma = float(sigma)
    if sigma < 0.3:
        return a.astype(np.float32, copy=False)
    if sigma < 2.0:
        return _gauss_exact(a, sigma)
    if sigma > 8.0 and min(a.shape[:2]) > 64:
        k = sigma / 4.0
        H, W = a.shape[:2]
        small = resize_f(a, round(W / k), round(H / k))
        return resize_f(gaussian(small, sigma / k), W, H)
    # widths for 3 successive boxes matching the Gaussian variance (Kovesi)
    n = 3
    wl = int(np.floor(np.sqrt(12 * sigma * sigma / n + 1)))
    if wl % 2 == 0:
        wl -= 1
    wu = wl + 2
    m = round((12 * sigma * sigma - n * wl * wl - 4 * n * wl - 3 * n) / (-4 * wl - 4))
    out = a.astype(np.float32, copy=False)
    for i in range(n):
        out = box(out, ((wl if i < m else wu) - 1) // 2)
    return out


def _gauss_exact(a: np.ndarray, sigma: float) -> np.ndarray:
    r = max(1, int(np.ceil(3 * sigma)))
    x = np.arange(-r, r + 1, dtype=np.float32)
    k = np.exp(-x * x / (2 * sigma * sigma))
    k /= k.sum()
    out = a.astype(np.float32, copy=False)
    for axis in (0, 1):
        pad = [(0, 0)] * out.ndim
        pad[axis] = (r, r)
        p = np.pad(out, pad, mode="edge")
        n = out.shape[axis]
        acc = np.zeros_like(out)
        for i, kv in enumerate(k):
            acc += kv * np.take(p, np.arange(i, i + n), axis=axis)
        out = acc
    return out


# ------------------------------------------------------------------ edge-preserving smoothing

def guided(a: np.ndarray, r: int, eps: float, guide: np.ndarray | None = None) -> np.ndarray:
    """Guided filter (He et al.), computed at reduced resolution for large images (fast guided filter)."""
    if guide is None:
        guide = luma(a) if a.ndim == 3 else a
    g = guide.astype(np.float32)
    H, W = g.shape
    k = max(1, min(r // 2, int(max(H, W) / 700)))
    if k > 1:
        gs, as_ = resize_f(g, round(W / k), round(H / k)), resize_f(a, round(W / k), round(H / k))
        A, B = _guided_coeffs(as_, gs, max(1, round(r / k)), eps)
        A, B = resize_f(A, W, H), resize_f(B, W, H)
    else:
        A, B = _guided_coeffs(a.astype(np.float32), g, r, eps)
    return A * (g[..., None] if a.ndim == 3 else g) + B


def _guided_coeffs(a, g, r, eps):
    mg = box(g, r)
    vg = box(g * g, r) - mg * mg
    if a.ndim == 3:
        g3, mg3, vg3 = g[..., None], mg[..., None], vg[..., None]
    else:
        g3, mg3, vg3 = g, mg, vg
    mi = box(a, r)
    cov = box(a * g3, r) - mi * mg3
    A = cov / (vg3 + eps)
    B = mi - A * mg3
    return box(A, r), box(B, r)


def denoise(a: np.ndarray, strength: float) -> np.ndarray:
    """Noise reduction: edge-aware smoothing of luminance detail plus stronger chroma smoothing."""
    s = float(np.clip(strength, 0, 1))
    scale = max(1.0, max(a.shape[:2]) / 1500.0)
    r = max(1, int(round((1 + 2 * s) * scale)))
    y = luma(a)
    eps = (0.012 + 0.05 * s) ** 2
    y2 = guided(y, r, eps, y)
    chroma = a - y[..., None]
    chroma2 = guided(chroma, r * 2, (0.02 + 0.08 * s) ** 2, y2)
    return np.clip(y2[..., None] + chroma2, 0, 1).astype(np.float32)


# ------------------------------------------------------------------ contrast

def clahe(y: np.ndarray, tiles: int = 8, clip: float = 1.6, bins: int = 256) -> np.ndarray:
    """Contrast-limited adaptive histogram equalization on a 0..1 single channel (like cv2.createCLAHE)."""
    h, w = y.shape
    tiles = max(1, int(tiles))
    th, tw = int(np.ceil(h / tiles)), int(np.ceil(w / tiles))
    ph, pw = th * tiles, tw * tiles
    q = np.clip((y * (bins - 1) + 0.5).astype(np.int32), 0, bins - 1)
    qp = np.pad(q, ((0, ph - h), (0, pw - w)), mode="reflect" if (ph - h < h and pw - w < w) else "edge")
    blocks = qp.reshape(tiles, th, tiles, tw).transpose(0, 2, 1, 3).reshape(tiles * tiles, th * tw)
    area = th * tw
    limit = max(1.0, clip * area / bins)
    luts = np.empty((tiles * tiles, bins), np.float32)
    for i, blk in enumerate(blocks):
        hist = np.bincount(blk, minlength=bins).astype(np.float64)
        excess = np.maximum(hist - limit, 0).sum()
        hist = np.minimum(hist, limit) + excess / bins
        cdf = np.cumsum(hist)
        luts[i] = (cdf / cdf[-1]).astype(np.float32)
    luts = luts.reshape(tiles, tiles, bins)
    # bilinear interpolation between the four nearest tile centres
    fy = (np.arange(h) + 0.5) / th - 0.5
    fx = (np.arange(w) + 0.5) / tw - 0.5
    y0 = np.clip(np.floor(fy).astype(int), 0, tiles - 1)
    x0 = np.clip(np.floor(fx).astype(int), 0, tiles - 1)
    y1, x1 = np.minimum(y0 + 1, tiles - 1), np.minimum(x0 + 1, tiles - 1)
    wy = np.clip(fy - y0, 0, 1)[:, None]
    wx = np.clip(fx - x0, 0, 1)[None, :]
    v00 = luts[y0[:, None], x0[None, :], q]
    v01 = luts[y0[:, None], x1[None, :], q]
    v10 = luts[y1[:, None], x0[None, :], q]
    v11 = luts[y1[:, None], x1[None, :], q]
    top = v00 * (1 - wx) + v01 * wx
    bot = v10 * (1 - wx) + v11 * wx
    return (top * (1 - wy) + bot * wy).astype(np.float32)


# ------------------------------------------------------------------ colour

def luma(a: np.ndarray) -> np.ndarray:
    return (a[..., 0] * 0.299 + a[..., 1] * 0.587 + a[..., 2] * 0.114).astype(np.float32)


def rgb_to_hsv(a: np.ndarray) -> np.ndarray:
    """RGB 0..1 -> HSV with H in degrees 0..360, S and V 0..1 (cv2 float convention)."""
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    mx, mn = a.max(-1), a.min(-1)
    d = mx - mn
    s = np.where(mx > 0, d / np.maximum(mx, 1e-12), 0)
    dd = np.maximum(d, 1e-12)
    h = np.where(mx == r, (g - b) / dd, np.where(mx == g, 2 + (b - r) / dd, 4 + (r - g) / dd)) * 60.0
    h = np.where(d > 0, h % 360.0, 0)
    return np.stack([h, s, mx], -1).astype(np.float32)


def hsv_to_rgb(hsv: np.ndarray) -> np.ndarray:
    h, s, v = hsv[..., 0] % 360.0 / 60.0, hsv[..., 1], hsv[..., 2]
    i = np.floor(h).astype(np.int32) % 6
    f = h - np.floor(h)
    p, q, t = v * (1 - s), v * (1 - s * f), v * (1 - s * (1 - f))
    r = np.choose(i, [v, q, p, p, t, v])
    g = np.choose(i, [t, v, v, q, p, p])
    b = np.choose(i, [p, p, t, v, v, q])
    return np.stack([r, g, b], -1).astype(np.float32)


# ------------------------------------------------------------------ edges

def sobel_mag(y: np.ndarray) -> np.ndarray:
    p = np.pad(y, 1, mode="edge")
    gx = (p[:-2, 2:] + 2 * p[1:-1, 2:] + p[2:, 2:]) - (p[:-2, :-2] + 2 * p[1:-1, :-2] + p[2:, :-2])
    gy = (p[2:, :-2] + 2 * p[2:, 1:-1] + p[2:, 2:]) - (p[:-2, :-2] + 2 * p[:-2, 1:-1] + p[:-2, 2:])
    return np.hypot(gx, gy).astype(np.float32)


def edges(y: np.ndarray, low: float = 60 / 255, high: float = 140 / 255) -> np.ndarray:
    """Binary edge map 0/1 following Canny (Sobel L1 gradient, non-maximum suppression, hysteresis)."""
    p = np.pad(y.astype(np.float32), 1, mode="edge")
    gx = (p[:-2, 2:] + 2 * p[1:-1, 2:] + p[2:, 2:]) - (p[:-2, :-2] + 2 * p[1:-1, :-2] + p[2:, :-2])
    gy = (p[2:, :-2] + 2 * p[2:, 1:-1] + p[2:, 2:]) - (p[:-2, :-2] + 2 * p[:-2, 1:-1] + p[:-2, 2:])
    mag = np.abs(gx) + np.abs(gy)
    ang = (np.rad2deg(np.arctan2(gy, gx)) + 180.0) % 180.0
    m = np.pad(mag, 1)
    H, W = mag.shape

    def nb(dy, dx):
        return m[1 + dy:1 + dy + H, 1 + dx:1 + dx + W]
    keep = np.zeros_like(mag, bool)
    for lo_a, hi_a, (dy, dx) in ((0, 22.5, (0, 1)), (157.5, 180.1, (0, 1)), (22.5, 67.5, (1, 1)),
                                 (67.5, 112.5, (1, 0)), (112.5, 157.5, (1, -1))):
        sel = (ang >= lo_a) & (ang < hi_a)
        keep |= sel & (mag > nb(dy, dx)) & (mag >= nb(-dy, -dx))
    thin = np.where(keep, mag, 0)
    weak = thin > low
    cur = thin > high
    # hysteresis: grow strong edges through connected weak pixels until nothing changes
    for _ in range(max(H, W)):
        c = np.pad(cur, 1)
        grown = np.zeros_like(cur)
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                grown |= c[1 + dy:1 + dy + H, 1 + dx:1 + dx + W]
        nxt = grown & weak
        if np.array_equal(nxt, cur):
            break
        cur = nxt
    return cur.astype(np.float32)


def adaptive_threshold(y: np.ndarray, block: int = 9, c: float = 4 / 255) -> np.ndarray:
    """1 where the pixel is brighter than its local mean minus c, else 0 (cv2 ADAPTIVE_THRESH_MEAN_C)."""
    return (y > box(y, block // 2) - c).astype(np.float32)


# ------------------------------------------------------------------ blur along a line

def motion_blur(a: np.ndarray, length: int, angle_deg: float) -> np.ndarray:
    """Linear motion blur: rotate so the motion is horizontal, box-blur rows, rotate back."""
    from PIL import Image
    length = max(1, int(length))
    if length < 3:
        return a
    H, W = a.shape[:2]
    if length >= 16 and min(H, W) > 64:
        k = length / 8.0
        small = resize_f(a, round(W / k), round(H / k))
        return resize_f(motion_blur(small, max(3, round(length / k)), angle_deg), W, H)
    pad = length
    chans = a[..., None] if a.ndim == 2 else a
    out = np.empty(chans.shape, np.float32)
    for ch in range(chans.shape[2]):
        p = np.pad(chans[..., ch].astype(np.float32), pad, mode="edge")
        im = Image.fromarray(p, "F")
        rot = np.asarray(im.rotate(-angle_deg, Image.BILINEAR, expand=True), np.float32)
        # rotating a constant-1 image tells us which pixels are real (to renormalize edges)
        ones = np.asarray(Image.fromarray(np.ones_like(p), "F").rotate(-angle_deg, Image.BILINEAR, expand=True))
        num = _box1d(rot, length // 2, 1)
        den = _box1d(ones, length // 2, 1)
        blurred = num / np.maximum(den, 1e-4)
        back = np.asarray(Image.fromarray(blurred, "F").rotate(angle_deg, Image.BILINEAR, expand=True), np.float32)
        oy = (back.shape[0] - p.shape[0]) // 2 + pad
        ox = (back.shape[1] - p.shape[1]) // 2 + pad
        out[..., ch] = back[oy:oy + H, ox:ox + W]
    return out[..., 0] if a.ndim == 2 else out


# ------------------------------------------------------------------ inpainting

def inpaint(a: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Fill masked pixels smoothly from their surroundings (pyramid push-pull, then relaxation)."""
    m = mask.astype(bool)
    if not m.any():
        return a
    ys, xs = np.nonzero(m)
    H, W = m.shape
    pad = max(8, int(max(np.ptp(ys), np.ptp(xs)) * 0.6) + 4)
    y0, y1 = max(0, ys.min() - pad), min(H, ys.max() + pad + 1)
    x0, x1 = max(0, xs.min() - pad), min(W, xs.max() + pad + 1)
    sub = a[y0:y1, x0:x1].astype(np.float32)
    known = (~m[y0:y1, x0:x1]).astype(np.float32)
    filled = _push_pull(sub, known)
    out = np.where(known[..., None] > 0, sub, filled)
    # a few relaxation sweeps (Laplace) to smooth seams inside the hole
    hole = known < 0.5
    for _ in range(40):
        p = np.pad(out, ((1, 1), (1, 1), (0, 0)), mode="edge")
        avg = (p[:-2, 1:-1] + p[2:, 1:-1] + p[1:-1, :-2] + p[1:-1, 2:]) * 0.25
        out = np.where(hole[..., None], avg, out)
    res = a.copy()
    res[y0:y1, x0:x1] = out
    return res


def _push_pull(img: np.ndarray, w: np.ndarray) -> np.ndarray:
    h, wd = w.shape
    if min(h, wd) <= 2 or w.sum() == 0:
        mean = (img * w[..., None]).sum((0, 1)) / max(w.sum(), 1e-6)
        return np.broadcast_to(mean, img.shape).astype(np.float32)
    # downsample with weights
    hh, ww = (h + 1) // 2 * 2, (wd + 1) // 2 * 2
    ip = np.pad(img * w[..., None], ((0, hh - h), (0, ww - wd), (0, 0)))
    wp = np.pad(w, ((0, hh - h), (0, ww - wd)))
    si = ip.reshape(hh // 2, 2, ww // 2, 2, -1).sum((1, 3))
    sw = wp.reshape(hh // 2, 2, ww // 2, 2).sum((1, 3))
    small = si / np.maximum(sw, 1e-6)[..., None]
    coarse = _push_pull(small, np.minimum(sw, 1.0))
    coarse = np.where(sw[..., None] > 0, small, coarse)
    up = np.repeat(np.repeat(coarse, 2, 0), 2, 1)[:h, :wd]
    up = gaussian(up, 1.0)
    return np.where(w[..., None] > 0, img, up)
