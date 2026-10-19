"""Non-destructive edit pipeline.

Edits are stored as an ordered chain of :class:`~fotohaus.picasa.ini.Filter`
entries using Picasa's filter identifiers where one exists (so edits made in
Picasa render here, and vice versa), plus ``fh_*`` filters for Fotohaus-only
tools. All algorithms here are original implementations.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageFilter

from .picasa.ini import Filter, decode_rect64

try:
    import cv2  # type: ignore
except Exception:  # pragma: no cover
    cv2 = None

# Picasa's straighten slider spans -1..1; we map the end stops to this angle.
TILT_MAX_DEGREES = 10.0


# ------------------------------------------------------------------ helpers

def to_f(im: Image.Image) -> np.ndarray:
    return np.asarray(im.convert("RGB"), dtype=np.float32) / 255.0


def to_im(a: np.ndarray) -> Image.Image:
    a = np.nan_to_num(a, nan=0.0, posinf=1.0, neginf=0.0)
    return Image.fromarray((np.clip(a, 0, 1) * 255.0 + 0.5).astype(np.uint8), "RGB")


def luma(a: np.ndarray) -> np.ndarray:
    return a[..., 0] * 0.299 + a[..., 1] * 0.587 + a[..., 2] * 0.114


def _blur(a: np.ndarray, sigma: float) -> np.ndarray:
    sigma = max(0.3, float(sigma))
    if cv2 is not None:
        return cv2.GaussianBlur(a, (0, 0), sigma)
    im = to_im(a)
    return to_f(im.filter(ImageFilter.GaussianBlur(sigma)))


def _scale(a: np.ndarray) -> float:
    """Size-relative unit so previews and full-res renders match."""
    return max(a.shape[0], a.shape[1]) / 1000.0


def _hex_color(s: str) -> tuple[float, float, float] | None:
    try:
        v = int(s, 16)
    except (ValueError, TypeError):
        return None
    if v == 0:
        return None
    r, g, b = (v >> 16) & 255, (v >> 8) & 255, v & 255
    return r / 255.0, g / 255.0, b / 255.0


def _apply_lum_ratio(a: np.ndarray, new_l: np.ndarray, old_l: np.ndarray) -> np.ndarray:
    ratio = new_l / np.maximum(old_l, 1e-4)
    return a * ratio[..., None]


def _saturate(a: np.ndarray, amount: float) -> np.ndarray:
    y = luma(a)[..., None]
    return y + (a - y) * (1.0 + amount)


def _radial_mask(h: int, w: int, cx: float, cy: float, radius: float, feather: float = 0.5) -> np.ndarray:
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    d = np.sqrt(((xx / w - cx) * (w / max(w, h))) ** 2 + ((yy / h - cy) * (h / max(w, h))) ** 2)
    r0 = radius * (1 - feather)
    m = np.clip((d - r0) / max(1e-4, radius - r0), 0, 1)
    return m  # 0 inside focus, 1 outside


# ------------------------------------------------------------------ auto tools

def auto_contrast(a: np.ndarray, clip: float = 0.5) -> np.ndarray:
    y = luma(a)
    lo, hi = np.percentile(y, [clip, 100 - clip])
    if hi - lo < 1e-3:
        return a
    return (a - lo) / (hi - lo)


def auto_color(a: np.ndarray) -> np.ndarray:
    out = np.empty_like(a)
    for c in range(3):
        lo, hi = np.percentile(a[..., c], [0.3, 99.7])
        out[..., c] = (a[..., c] - lo) / max(1e-3, hi - lo)
    # keep the overall brightness of the original
    m0, m1 = luma(a).mean(), luma(np.clip(out, 0, 1)).mean()
    if m1 > 1e-3:
        out *= min(1.4, max(0.7, m0 / m1)) ** 0.5
    # gray-world balance (gentle)
    means = np.clip(out, 0, 1).reshape(-1, 3).mean(0)
    g = means.mean()
    gains = np.clip(g / np.maximum(means, 1e-3), 0.8, 1.25)
    return out * (0.5 + 0.5 * gains)


def fill_light(a: np.ndarray, amount: float) -> np.ndarray:
    if amount <= 0:
        return a
    a = np.clip(a, 0, 1)
    y = luma(a)
    lifted = 1.0 - (1.0 - y) ** (1.0 + 2.2 * amount)
    new = y + (lifted - y) * (1.0 - y) ** 0.5 * 1.0
    return _apply_lum_ratio(a, np.clip(new, 0, 1), y)


def highlights_shadows(a: np.ndarray, highlights: float, shadows: float) -> np.ndarray:
    """Picasa-style: 'highlights' moves the white point down, 'shadows' the black point up."""
    if highlights > 0:
        a = a / max(0.05, 1.0 - highlights)
    if shadows > 0:
        a = (a - shadows) / max(0.05, 1.0 - shadows)
    return a


def color_temperature(a: np.ndarray, t: float) -> np.ndarray:
    if abs(t) < 1e-4:
        return a
    gains = np.array([1 + 0.18 * t, 1 + 0.03 * t, 1 - 0.18 * t], dtype=np.float32)
    return a * gains


def neutral_point(a: np.ndarray, rgb: tuple[float, float, float]) -> np.ndarray:
    r, g, b = (max(1e-3, c) for c in rgb)
    m = (r + g + b) / 3.0
    return a * np.array([m / r, m / g, m / b], dtype=np.float32)


def smart_enhance(a: np.ndarray, strength: float = 1.0) -> np.ndarray:
    """Local-contrast (CLAHE on L*), gentle auto white balance and vibrance."""
    base = a
    if cv2 is not None:
        u8 = (np.clip(a, 0, 1) * 255).astype(np.uint8)
        lab = cv2.cvtColor(u8, cv2.COLOR_RGB2LAB)
        tiles = max(2, int(round(max(a.shape[:2]) / 400)))
        clahe = cv2.createCLAHE(clipLimit=1.6, tileGridSize=(tiles, tiles))
        lab[..., 0] = clahe.apply(lab[..., 0])
        a = cv2.cvtColor(lab, cv2.COLOR_LAB2RGB).astype(np.float32) / 255.0
    else:
        a = auto_contrast(a, 0.3)
    means = np.clip(a, 0, 1).reshape(-1, 3).mean(0)
    gains = np.clip(means.mean() / np.maximum(means, 1e-3), 0.9, 1.1)
    a = a * gains
    a = vibrance(a, 0.25)
    return base + (a - base) * strength


def vibrance(a: np.ndarray, amount: float) -> np.ndarray:
    mx, mn = a.max(-1), a.min(-1)
    sat = (mx - mn)
    w = (1.0 - np.clip(sat, 0, 1)) * amount
    y = luma(a)[..., None]
    return y + (a - y) * (1.0 + w[..., None])


def clarity(a: np.ndarray, amount: float) -> np.ndarray:
    if abs(amount) < 1e-3:
        return a
    y = luma(a)
    blur = _blur(y, 20 * _scale(a))
    detail = y - blur
    mid = 1.0 - np.abs(y - 0.5) * 2  # protect deep shadows/highlights
    new = y + detail * amount * 1.5 * (0.4 + 0.6 * mid)
    return _apply_lum_ratio(a, np.clip(new, 0, 1), y)


def denoise(a: np.ndarray, strength: float) -> np.ndarray:
    if strength <= 0:
        return a
    if cv2 is not None:
        u8 = (np.clip(a, 0, 1) * 255).astype(np.uint8)
        h = 3 + 12 * strength
        out = cv2.fastNlMeansDenoisingColored(u8, None, h, h, 7, 21)
        return out.astype(np.float32) / 255.0
    return to_f(to_im(a).filter(ImageFilter.MedianFilter(3)))


def unsharp(a: np.ndarray, amount: float, radius: float = 1.2) -> np.ndarray:
    if amount <= 0:
        return a
    blur = _blur(a, radius * max(0.6, _scale(a)))
    return a + (a - blur) * amount


# ------------------------------------------------------------------ geometry

def crop_rel(im: Image.Image, l: float, t: float, r: float, b: float) -> Image.Image:
    w, h = im.size
    box = (int(round(l * w)), int(round(t * h)), int(round(r * w)), int(round(b * h)))
    if box[2] - box[0] < 2 or box[3] - box[1] < 2:
        return im
    return im.crop(box)


def straighten(im: Image.Image, degrees: float) -> Image.Image:
    if abs(degrees) < 0.01:
        return im
    w, h = im.size
    rad = math.radians(abs(degrees))
    # largest axis-aligned rectangle with the original aspect inside the rotated image
    scale = 1.0 / (math.cos(rad) + max(w / h, h / w) * math.sin(rad))
    rot = im.rotate(-degrees, resample=Image.BICUBIC, expand=False)
    nw, nh = w * scale, h * scale
    l, t = (w - nw) / 2, (h - nh) / 2
    out = rot.crop((int(l), int(t), int(l + nw), int(t + nh)))
    return out.resize((w, h), Image.LANCZOS)


# ------------------------------------------------------------------ effects

def sepia(a):
    y = luma(a)[..., None]
    tone = np.array([1.07, 0.95, 0.74], dtype=np.float32)
    return y * tone


def black_white(a):
    y = luma(a)[..., None]
    return np.repeat(y, 3, axis=-1)


def warmify(a):
    return color_temperature(_saturate(a, 0.08), 0.35)


def film_grain(a, amount=0.06, seed=7):
    rng = np.random.default_rng(seed)
    h, w = a.shape[:2]
    noise = rng.normal(0, 1, (h, w)).astype(np.float32)
    noise = _blur(noise, 0.6 * max(1.0, _scale(a)))
    noise /= max(1e-3, noise.std())
    y = luma(a)
    weight = 4 * y * (1 - y)  # strongest in midtones
    return a + (noise * amount * (0.3 + weight))[..., None]


def tint(a, color, preserve: float):
    y = luma(a)[..., None]
    tinted = y * np.array(color, dtype=np.float32) * 1.6
    return tinted * (1 - preserve) + a * preserve


def glow(a, intensity=0.65, radius=3.0):
    blur = _blur(a, radius * 3 * max(0.5, _scale(a)))
    screen = 1 - (1 - a) * (1 - blur)
    return a + (screen - a) * intensity


def soft_focus(a, cx=0.5, cy=0.5, size=0.5, amount=0.5):
    blur = _blur(a, 6 * max(0.5, _scale(a)))
    m = _radial_mask(a.shape[0], a.shape[1], cx, cy, 0.15 + 0.6 * size, 0.6)[..., None]
    return a + (blur - a) * m * min(1.0, amount * 1.5)


def focal_bw(a, cx=0.5, cy=0.5, size=0.5, sharpness=0.5):
    bw = black_white(a)
    m = _radial_mask(a.shape[0], a.shape[1], cx, cy, 0.15 + 0.6 * size, 1 - 0.9 * sharpness)[..., None]
    return a + (bw - a) * m


def filtered_bw(a, color):
    w = np.array(color, dtype=np.float32) + 0.2
    w = w / w.sum()
    y = (a * w).sum(-1, keepdims=True)
    return np.repeat(y, 3, -1)


def graduated_tint(a, cy=0.3, color=(0.2, 0.35, 0.7), feather=0.25, shade=0.25):
    h, w = a.shape[:2]
    yy = (np.arange(h, dtype=np.float32) / h)[:, None, None]
    m = np.clip((cy - yy) / max(0.02, feather) + 0.5, 0, 1)
    tinted = a * (1 - shade) + np.array(color, dtype=np.float32) * a * shade * 2
    return a + (tinted - a) * m


def vignette(a, amount=0.5):
    h, w = a.shape[:2]
    m = _radial_mask(h, w, 0.5, 0.5, 0.75, 0.9)
    return a * (1 - amount * 0.85 * m ** 1.5)[..., None]


def lomo(a):
    a = auto_contrast(a, 1.0)
    a = 0.5 + (a - 0.5) * 1.25
    a = _saturate(a, 0.35)
    a = a * np.array([1.03, 1.0, 0.92], dtype=np.float32)
    return vignette(a, 0.8)


def holga(a):
    a = soft_focus(a, 0.5, 0.5, 0.45, 0.6)
    a = _saturate(a, -0.25)
    return vignette(a, 0.9)


def orton(a):
    bright = np.clip(a * 1.25, 0, 1)
    blur = _blur(bright, 8 * max(0.5, _scale(a)))
    return 1 - (1 - a) * (1 - blur * 0.6)


def hdr_ish(a):
    a = clarity(a, 1.0)
    a = fill_light(a, 0.25)
    return vibrance(a, 0.3)


def cross_process(a):
    r = 0.5 + (a[..., 0] - 0.5) * 1.3
    g = 0.5 + (a[..., 1] - 0.5) * 1.15
    b = a[..., 2] * 0.7 + 0.15
    return np.stack([r, g, b], -1)


def infrared(a):
    y = a[..., 1] * 0.6 + a[..., 0] * 0.4
    y = np.clip(auto_contrast(np.repeat(y[..., None], 3, -1), 1.0), 0, 1)
    return glow(y, 0.45, 3)


def pencil_sketch(a):
    y = luma(a)
    inv = 1 - y
    blur = _blur(inv, 4 * max(0.5, _scale(a)))
    sk = np.clip(y / np.maximum(1 - blur, 1e-3), 0, 1)
    return np.repeat(sk[..., None], 3, -1)


def pixelate(a, size=0.02):
    im = to_im(a)
    w, h = im.size
    px = max(2, int(max(w, h) * size))
    small = im.resize((max(1, w // px), max(1, h // px)), Image.BILINEAR)
    return to_f(small.resize((w, h), Image.NEAREST))


def invert(a):
    return 1 - a


def posterize(a, levels=5):
    return np.round(a * (levels - 1)) / (levels - 1)


def cinemascope(a):
    h, w = a.shape[:2]
    target_h = int(w / 2.39)
    if target_h < h:
        bar = (h - target_h) // 2
        a = a.copy()
        a[:bar] = 0
        a[h - bar:] = 0
    return a


def redeye_regions(a, rects):
    a = a.copy()
    h, w = a.shape[:2]
    for (l, t, r, b) in rects:
        x0, y0, x1, y1 = int(l * w), int(t * h), int(r * w), int(b * h)
        if x1 <= x0 or y1 <= y0:
            continue
        reg = a[y0:y1, x0:x1]
        R, G, B = reg[..., 0], reg[..., 1], reg[..., 2]
        mask = (R > 0.25) & (R > 1.6 * np.maximum(G, B))
        reg[..., 0] = np.where(mask, (G + B) / 2, R)
    return a


# ------------------------------------------------------------------ Creative Kit style effects

def sixties(a, color=(0.95, 0.75, 0.45)):
    a = _saturate(a, -0.35)
    a = 0.08 + a * 0.88
    a = a * np.array(color, dtype=np.float32) * 1.1
    return vignette(film_grain(a, 0.04), 0.5)


def heat_map(a):
    y = luma(a)
    r = np.clip(1.5 - np.abs(4 * y - 3), 0, 1)
    g = np.clip(1.5 - np.abs(4 * y - 2), 0, 1)
    b = np.clip(1.5 - np.abs(4 * y - 1), 0, 1)
    return np.stack([r, g, b], -1)


def duotone(a, dark=(0.10, 0.10, 0.35), light=(1.0, 0.85, 0.55)):
    y = luma(a)[..., None]
    return np.array(dark, np.float32) * (1 - y) + np.array(light, np.float32) * y


def boost(a, amount=0.5):
    a = 0.5 + (a - 0.5) * (1 + 0.4 * amount)
    return vibrance(a, 0.6 * amount)


def neon(a):
    if cv2 is None:
        return invert(a)
    g = (luma(a) * 255).astype(np.uint8)
    e = cv2.Canny(g, 60, 140).astype(np.float32) / 255.0
    e = _blur(e, 1.2 * max(1.0, _scale(a)))
    e = np.clip(e * 3, 0, 1)
    hue = _saturate(a, 1.5)
    return np.clip(hue * e[..., None] * 1.8, 0, 1)


def comicize(a):
    if cv2 is None:
        return posterize(a, 6)
    u8 = (np.clip(a, 0, 1) * 255).astype(np.uint8)
    smooth = cv2.bilateralFilter(u8, 9, 60, 60)
    q = (np.round(smooth.astype(np.float32) / 255 * 5) / 5)
    g = cv2.cvtColor(u8, cv2.COLOR_RGB2GRAY)
    edges = cv2.adaptiveThreshold(cv2.medianBlur(g, 5), 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY, 9, 4)
    return q * (edges.astype(np.float32) / 255.0)[..., None]


def night_vision(a):
    y = np.clip(auto_contrast(np.repeat(luma(a)[..., None], 3, -1), 1.0), 0, 1)[..., 0]
    y = 0.15 + y * 0.85
    out = np.stack([y * 0.25, y, y * 0.3], -1)
    return vignette(film_grain(out, 0.08), 0.8)


def local_contrast(a, amount=0.6):
    return clarity(a, amount)


def focal_zoom(a, cx=0.5, cy=0.5, size=0.35, amount=0.6):
    im = to_im(a)
    w, h = im.size
    acc = a.copy()
    n = 6
    for k in range(1, n + 1):
        s = 1 + 0.025 * k * amount * 2
        big = im.resize((int(w * s), int(h * s)), Image.BILINEAR)
        l, t = int(cx * w * s - cx * w), int(cy * h * s - cy * h)
        acc += to_f(big.crop((l, t, l + w, t + h)))
    blurred = acc / (n + 1)
    m = _radial_mask(h, w, cx, cy, 0.1 + 0.6 * size, 0.7)[..., None]
    return a + (blurred - a) * m


def focal_pixelate(a, cx=0.5, cy=0.5, size=0.4, pixel=0.025):
    px = pixelate(a, pixel)
    m = _radial_mask(a.shape[0], a.shape[1], cx, cy, 0.1 + 0.6 * size, 0.25)[..., None]
    return a + (px - a) * m


def linear_blur(a, amount=2.0, angle=0.0):
    if cv2 is None:
        return a
    k = max(3, int(amount * 6 * max(0.5, _scale(a))) | 1)
    kern = np.zeros((k, k), np.float32)
    kern[k // 2, :] = 1.0
    M = cv2.getRotationMatrix2D((k / 2 - 0.5, k / 2 - 0.5), angle, 1.0)
    kern = cv2.warpAffine(kern, M, (k, k))
    kern /= max(1e-6, kern.sum())
    return cv2.filter2D(a, -1, kern)


def directional(a, kind, lr=0.0, tb=0.0):
    h, w = a.shape[:2]
    gx = np.linspace(-1, 1, w, dtype=np.float32)[None, :]
    gy = np.linspace(-1, 1, h, dtype=np.float32)[:, None]
    g = (gx * lr + gy * tb)[..., None]
    if kind == "sat":
        y = luma(a)[..., None]
        return y + (a - y) * (1 + g)
    if kind == "brite":
        return a * (1 + 0.6 * g)
    blur = _blur(a, 1.5 * max(0.6, _scale(a)))
    return a + (a - blur) * np.clip(g * 2, -1, 3)


def add_border(im: Image.Image, frac=0.04, color=(255, 255, 255)) -> Image.Image:
    from PIL import ImageOps
    b = max(2, int(max(im.size) * frac))
    return ImageOps.expand(im, border=b, fill=color)


def museum_matte(im: Image.Image) -> Image.Image:
    from PIL import ImageOps
    b = int(max(im.size) * 0.12)
    inner = ImageOps.expand(im, border=max(2, b // 25), fill=(30, 30, 30))
    return ImageOps.expand(inner, border=b, fill=(245, 242, 236))


def polaroid(im: Image.Image) -> Image.Image:
    from PIL import ImageOps
    s = min(im.size)
    im = ImageOps.fit(im, (s, s))
    m = int(s * 0.06)
    out = Image.new("RGB", (s + 2 * m, s + m + int(s * 0.22)), (250, 248, 242))
    a = to_f(im)
    a = 0.04 + a * 0.92
    a = a * np.array([1.03, 1.0, 0.93], np.float32)
    out.paste(to_im(a), (m, m))
    return out


def drop_shadow(im: Image.Image, bg=(255, 255, 255)) -> Image.Image:
    from PIL import ImageFilter
    pad = int(max(im.size) * 0.06)
    canvas = Image.new("RGB", (im.width + 2 * pad, im.height + 2 * pad), bg)
    shadow = Image.new("L", canvas.size, 0)
    sh = Image.new("L", im.size, 150)
    shadow.paste(sh, (pad + pad // 3, pad + pad // 3))
    shadow = shadow.filter(ImageFilter.GaussianBlur(pad / 3))
    canvas.paste((0, 0, 0), (0, 0), shadow)
    canvas.paste(im, (pad, pad))
    return canvas


def rounded_edges(im: Image.Image, bg=(255, 255, 255)) -> Image.Image:
    from PIL import ImageDraw
    r = int(min(im.size) * 0.08)
    mask = Image.new("L", im.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, im.width - 1, im.height - 1], radius=r, fill=255)
    out = Image.new("RGB", im.size, bg)
    out.paste(im, (0, 0), mask)
    return out


def matte(a, amount=0.35):
    return a * (1 - amount * 0.35) + amount * 0.12


def text_overlay(im: Image.Image, text: str, x: float, y: float, size: float, color, outline: float,
                 opacity: float, angle: float, font_name: str = "") -> Image.Image:
    """Picasa's Text tool: text placed at (x, y) (centre, relative), size relative to image height."""
    from PIL import ImageDraw, ImageFont
    px = max(8, int(size * im.height))
    font = None
    cands = ([font_name] if font_name else []) + [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf", "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/SFNS.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]
    for c in cands:
        try:
            font = ImageFont.truetype(c, px)
            break
        except (OSError, ValueError):
            continue
    if font is None:
        font = ImageFont.load_default()
    layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    bbox = d.multiline_textbbox((0, 0), text, font=font, stroke_width=int(outline * px * 0.08))
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    tile = Image.new("RGBA", (tw + px, th + px), (0, 0, 0, 0))
    td = ImageDraw.Draw(tile)
    sw = int(outline * px * 0.08)
    td.multiline_text((px // 2 - bbox[0], px // 2 - bbox[1]), text, font=font,
                      fill=tuple(int(c * 255) for c in color) + (int(opacity * 255),),
                      stroke_width=sw, stroke_fill=(0, 0, 0, int(opacity * 255)) if sw else None, align="center")
    if angle:
        tile = tile.rotate(-angle, expand=True, resample=Image.BICUBIC)
    layer.paste(tile, (int(x * im.width - tile.width / 2), int(y * im.height - tile.height / 2)), tile)
    return Image.alpha_composite(im.convert("RGBA"), layer).convert("RGB")


def retouch(a, strokes):
    """Heal brush strokes [(x, y, r)] (relative coords, r relative to image width) by inpainting."""
    if cv2 is None or not strokes:
        return a
    h, w = a.shape[:2]
    mask = np.zeros((h, w), np.uint8)
    for x, y, r in strokes:
        cv2.circle(mask, (int(x * w), int(y * h)), max(1, int(r * w)), 255, -1)
    u8 = (np.clip(a, 0, 1) * 255).astype(np.uint8)
    rad = max(3, int(max(h, w) / 300))
    out = cv2.inpaint(u8, mask, rad, cv2.INPAINT_TELEA)
    return out.astype(np.float32) / 255.0


def encode_strokes(strokes) -> str:
    return ":".join(f"{x:.4f}/{y:.4f}/{r:.4f}" for x, y, r in strokes)


def decode_strokes(s: str):
    out = []
    for part in (s or "").split(":"):
        try:
            x, y, r = (float(v) for v in part.split("/"))
            out.append((x, y, r))
        except ValueError:
            continue
    return out


def overflow_mask(a: np.ndarray) -> np.ndarray:
    """Picasa's 'Show overflow pixels': mark clipped highlights (red) and shadows (blue)."""
    out = a.copy()
    hi = (a >= 0.995).any(-1)
    lo = (a <= 0.005).all(-1)
    out[hi] = (1.0, 0.0, 0.0)
    out[lo] = (0.0, 0.3, 1.0)
    return out


# ------------------------------------------------------------------ chain

@dataclass
class FilterInfo:
    id: str
    label: str
    group: str            # basic | tuning | effects | smart | geometry | history
    params: list = None   # [(label, min, max, default)]
    picasa: bool = True


FILTERS: dict[str, FilterInfo] = {}


def _reg(fid, label, group, params=None, picasa=True):
    FILTERS[fid] = FilterInfo(fid, label, group, params or [], picasa)


# Picasa identifiers (labels are our own wording)
_reg("crop64", "Crop", "geometry")
_reg("tilt", "Straighten", "geometry", [("Angle", -1.0, 1.0, 0.0)])
_reg("redeye", "Red-eye", "basic")
_reg("enhance", "Auto Enhance (one click)", "basic")
_reg("autolight", "Auto Contrast", "basic")
_reg("autocontrast", "Auto Contrast", "basic")
_reg("autocolor", "Auto Color", "basic")
_reg("autobacklight", "Auto Fill Light", "basic")
_reg("fill", "Fill Light", "basic", [("Fill light", 0.0, 1.0, 0.0)])
_reg("finetune2", "Tuning", "tuning")
_reg("finetune", "Tuning", "tuning")
_reg("triple3", "Lighting", "tuning")
_reg("unsharp2", "Sharpen", "effects", [("Amount", 0.0, 3.0, 0.6)])
_reg("unsharp", "Sharpen", "effects", [("Amount", 0.0, 1.0, 0.6)])
_reg("sepia", "Sepia", "effects")
_reg("bw", "Black & White", "effects")
_reg("warm", "Warm Up", "effects")
_reg("grain2", "Film Grain", "effects")
_reg("grain", "Film Grain", "effects")
_reg("tint", "Tint", "effects", [("Color preservation", 0.0, 1.0, 0.0)])
_reg("sat", "Saturation", "effects", [("Amount", -1.0, 1.0, 0.16)])
_reg("radblur", "Soft Focus", "effects", [("Size", 0.0, 1.0, 0.5), ("Amount", 0.0, 1.0, 0.5)])
_reg("glow2", "Glow", "effects", [("Intensity", 0.0, 1.0, 0.65), ("Radius", 0.5, 10.0, 3.0)])
_reg("glow", "Glow", "effects", [("Intensity", 0.0, 1.0, 0.65), ("Radius", 0.5, 10.0, 3.0)])
_reg("ansel", "Filtered B&W", "effects")
_reg("radsat", "Focal B&W", "effects", [("Size", 0.0, 1.0, 0.5), ("Sharpness", 0.0, 1.0, 0.5)])
_reg("dir_tint", "Graduated Tint", "effects", [("Feather", 0.0, 1.0, 0.25), ("Shade", 0.0, 1.0, 0.25)])
_reg("Vignette", "Vignette", "effects", [("Amount", 0.0, 1.0, 0.5)])
_reg("Lomo", "Lomo-ish", "effects")
_reg("Holga", "Holga-ish", "effects")
_reg("Orton", "Orton-ish", "effects")
_reg("HDR", "HDR-ish", "effects")
_reg("CrossProcess", "Cross Process", "effects")
_reg("IR", "Infrared Film", "effects")
_reg("PencilSketch", "Pencil Sketch", "effects")
_reg("Pixelate", "Pixelate", "effects")
_reg("Invert", "Invert Colors", "effects")
_reg("QuantizePalette", "Posterize", "effects")
_reg("Cinemascope", "Cinemascope", "effects")
_reg("Soften", "Soften", "effects")
_reg("Sixties", "1960's", "effects")
_reg("HeatMap", "Heat Map", "effects")
_reg("TwoTone", "Duo-Tone", "effects")
_reg("Boost", "Boost", "effects", [("Amount", 0.0, 1.0, 0.5)])
_reg("Neon", "Neon", "effects")
_reg("Comicize", "Comic Book", "effects")
_reg("NightVision", "Night Vision", "effects")
_reg("LocalContrast", "Local Contrast", "effects", [("Amount", 0.0, 1.0, 0.6)])
_reg("FocalZoom", "Focal Zoom", "effects", [("Size", 0.0, 1.0, 0.35), ("Amount", 0.0, 1.0, 0.6)])
_reg("PicnikFocalPixelate", "Focal Pixelate", "effects", [("Size", 0.0, 1.0, 0.4), ("Pixel size", 0.005, 0.08, 0.025)])
_reg("focalpixelate", "Focal Pixelate", "effects")
_reg("linblur", "Linear Blur", "effects", [("Amount", 0.0, 10.0, 2.0), ("Angle", -90.0, 90.0, 0.0)])
_reg("dir_sat", "Directional Saturation", "effects", [("Left to right", -1.0, 1.0, 0.5), ("Top to bottom", -1.0, 1.0, 0.0)])
_reg("dir_brite", "Directional Brightness", "effects", [("Left to right", -1.0, 1.0, 0.0), ("Top to bottom", -1.0, 1.0, -0.4)])
_reg("dir_sharp", "Directional Sharpen", "effects", [("Left to right", -1.0, 1.0, 0.5), ("Top to bottom", -1.0, 1.0, 0.0)])
_reg("radtint", "Radial Tint", "effects", [("Feather", 0.0, 1.0, 0.25)])
_reg("Matte", "Matte", "effects", [("Amount", 0.0, 1.0, 0.35)])
_reg("PicnikGrain", "Film Grain", "effects")
_reg("PicnikTint", "Tint", "effects", [("Color preservation", 0.0, 1.0, 0.0)])
_reg("Border", "Border", "effects", [("Width", 0.005, 0.15, 0.04)])
_reg("DropShadow", "Drop Shadow", "effects")
_reg("Polaroid", "Polaroid", "effects")
_reg("RoundedEdges", "Rounded Edges", "effects")
_reg("MuseumMatte", "Museum Matte", "effects")
_reg("colortemp", "Color Temperature", "tuning", [("Cool to warm", -0.5, 0.5, 0.125), ("White shift", 0.0, 1.0, 0.0)])
_reg("shadow", "Shadow & Highlight", "tuning", [("Radius", 0.0, 1.0, 0.3), ("Shadow %", 0.0, 1.0, 0.3), ("Highlight %", 0.0, 1.0, 0.2)])
_reg("contrast", "Contrast", "tuning", [("Contrast", -0.5, 0.5, 0.1)])
_reg("gamma", "Gamma Correct", "tuning", [("Level", -1.0, 1.0, 0.16)])
_reg("backlight", "Backlight Fix", "tuning", [("Amount", 0.0, 1.0, 0.25)])
_reg("blur", "Blur", "effects", [("Amount", 0.0, 1.0, 0.2)])
_reg("triple", "Lighting Fixes", "tuning")
_reg("triple2", "Lighting Fixes", "tuning")
_reg("colorfix", "Color Fixes", "tuning")
_reg("whitept", "White Point", "tuning")
_reg("retouch", "Retouch", "basic")
_reg("moviestart", "Movie Start", "video")
_reg("movieend", "Movie End", "video")
# Fotohaus-only filters
_reg("fh_text", "Text", "basic", picasa=False)
_reg("fh_retouch", "Retouch", "basic", picasa=False)
_reg("fh_smart", "Smart Enhance", "smart", [("Strength", 0.0, 1.0, 0.8)], picasa=False)
_reg("fh_denoise", "Reduce Noise", "smart", [("Strength", 0.0, 1.0, 0.4)], picasa=False)
_reg("fh_clarity", "Clarity", "smart", [("Amount", -1.0, 1.0, 0.4)], picasa=False)
_reg("fh_vibrance", "Vibrance", "smart", [("Amount", -1.0, 1.0, 0.3)], picasa=False)
_reg("fh_exposure", "Exposure", "smart", [("EV", -2.0, 2.0, 0.0)], picasa=False)
_reg("fh_contrast", "Contrast", "smart", [("Amount", -1.0, 1.0, 0.0)], picasa=False)
_reg("fh_hsl", "Hue / Saturation", "smart", [("Hue shift", -0.5, 0.5, 0.0), ("Saturation", -1.0, 1.0, 0.0)], picasa=False)


def label_for(f: Filter) -> str:
    info = FILTERS.get(f.id)
    return info.label if info else f.id


def _rects_in_args(args: list[str]) -> list[tuple[float, float, float, float]]:
    out = []
    for a in args:
        s = a.strip().lower()
        if s.startswith("rect64("):
            s = s[7:-1]
        if 8 <= len(s) <= 16 and all(c in "0123456789abcdef" for c in s):
            out.append(decode_rect64(s))
    return out


def apply_filter(im: Image.Image, f: Filter) -> Image.Image:
    """Apply one filter to a PIL image, returning a new image."""
    fid = f.id
    if f.enabled not in ("1", "yes", "true"):
        return im
    # ---- geometry (PIL)
    if fid in ("crop64", "crop"):
        rects = _rects_in_args(f.args)
        if rects:
            return crop_rel(im, *rects[0])
        return im
    if fid == "tilt":
        v = f.floats(1)[0]
        return straighten(im, v * TILT_MAX_DEGREES)
    if fid == "Border":
        col = _hex_color(f.args[1]) if len(f.args) > 1 else None
        return add_border(im, f.floats(1, [0.04])[0], tuple(int(c * 255) for c in (col or (1, 1, 1))))
    if fid == "MuseumMatte":
        return museum_matte(im)
    if fid == "Polaroid":
        return polaroid(im)
    if fid == "DropShadow":
        return drop_shadow(im)
    if fid == "RoundedEdges":
        return rounded_edges(im)
    if fid in ("moviestart", "movieend", "retouch", "picnik", "save", "rot", "debug"):
        return im   # history markers / data Picasa kept elsewhere
    if fid == "fh_text":
        from urllib.parse import unquote
        vals = f.args + [""] * 10
        text = unquote(vals[0])
        x, y, size, outline, opacity, angle = (_num(v) for v in vals[1:4] + vals[5:8])
        col = _hex_color(vals[4]) or (1.0, 1.0, 1.0)
        return text_overlay(im, text, x or 0.5, y or 0.85, size or 0.08, col, outline, opacity or 1.0, angle,
                            unquote(vals[8]))
    # ---- pixel ops (numpy)
    a = to_f(im)
    if fid in ("autolight", "autocontrast"):
        a = auto_contrast(a)
    elif fid == "autocolor":
        a = auto_color(a)
    elif fid == "enhance":
        a = auto_color(auto_contrast(a, 0.3))
        a = _saturate(np.clip(a, 0, 1), 0.08)
    elif fid == "autobacklight":
        a = fill_light(a, 0.35)
    elif fid == "fill":
        a = fill_light(a, f.floats(1)[0])
    elif fid in ("finetune2", "finetune", "triple3"):
        vals = f.args + [""] * 5
        fl = _num(vals[0])
        hi = _num(vals[1])
        sh = _num(vals[2])
        a = fill_light(a, fl)
        a = highlights_shadows(a, hi, sh)
        if fid in ("finetune2", "finetune"):
            col = _hex_color(vals[3])
            if col:
                a = neutral_point(a, col)
            t = _num(vals[4])
            if fid == "finetune":
                t = (t - 0.5) * 2 if t else 0.0
            a = color_temperature(a, t)
    elif fid == "redeye":
        a = redeye_regions(a, _rects_in_args(f.args))
    elif fid in ("unsharp2", "unsharp"):
        a = unsharp(a, f.floats(1, [0.6])[0])
    elif fid == "sepia":
        a = sepia(a)
    elif fid == "bw":
        a = black_white(a)
    elif fid == "warm":
        a = warmify(a)
    elif fid in ("grain2", "grain"):
        a = film_grain(a)
    elif fid == "tint":
        preserve = _num(f.args[0]) if f.args else 0.0
        col = _hex_color(f.args[1]) if len(f.args) > 1 else None
        a = tint(a, col or (0.55, 0.45, 0.3), min(1.0, preserve if preserve <= 1 else preserve / 256))
    elif fid == "sat":
        a = _saturate(a, f.floats(1, [0.16])[0])
    elif fid == "radblur":
        x, y, size, amt = f.floats(4, [0.5, 0.5, 0.5, 0.5])
        a = soft_focus(a, x, y, size, amt)
    elif fid in ("glow2", "glow"):
        inten, rad = f.floats(2, [0.65, 3.0])
        a = glow(a, inten, rad)
    elif fid == "ansel":
        col = _hex_color(f.args[0]) if f.args else None
        a = filtered_bw(a, col or (1.0, 0.6, 0.2))
    elif fid == "radsat":
        x, y, size, sharp = f.floats(4, [0.5, 0.5, 0.5, 0.5])
        a = focal_bw(a, x, y, size, sharp)
    elif fid == "dir_tint":
        x, y, feather, shade = f.floats(4, [0.5, 0.3, 0.25, 0.25])
        col = _hex_color(f.args[4]) if len(f.args) > 4 else None
        a = graduated_tint(a, y, col or (0.25, 0.4, 0.75), feather, shade)
    elif fid == "Vignette":
        a = vignette(a, f.floats(1, [0.5])[0])
    elif fid == "Lomo":
        a = lomo(a)
    elif fid == "Holga":
        a = holga(a)
    elif fid == "Orton":
        a = orton(a)
    elif fid == "HDR":
        a = hdr_ish(a)
    elif fid == "CrossProcess":
        a = cross_process(a)
    elif fid == "IR":
        a = infrared(a)
    elif fid == "PencilSketch":
        a = pencil_sketch(a)
    elif fid == "Pixelate":
        a = pixelate(a)
    elif fid == "Invert":
        a = invert(a)
    elif fid == "QuantizePalette":
        a = posterize(a)
    elif fid == "Cinemascope":
        a = cinemascope(a)
    elif fid == "Soften":
        a = a + (_blur(a, 2.5 * max(0.5, _scale(a))) - a) * 0.6
    elif fid == "Sixties":
        a = sixties(a)
    elif fid == "HeatMap":
        a = heat_map(a)
    elif fid == "TwoTone":
        dark = _hex_color(f.args[0]) if f.args else None
        light = _hex_color(f.args[1]) if len(f.args) > 1 else None
        a = duotone(a, dark or (0.10, 0.10, 0.35), light or (1.0, 0.85, 0.55))
    elif fid == "Boost":
        a = boost(a, f.floats(1, [0.5])[0])
    elif fid == "Neon":
        a = neon(a)
    elif fid == "Comicize":
        a = comicize(a)
    elif fid == "NightVision":
        a = night_vision(a)
    elif fid == "LocalContrast":
        a = local_contrast(a, f.floats(1, [0.6])[0])
    elif fid == "FocalZoom":
        x, y, size, amt = f.floats(4, [0.5, 0.5, 0.35, 0.6])
        a = focal_zoom(a, x, y, size, amt)
    elif fid in ("PicnikFocalPixelate", "focalpixelate"):
        x, y, size, px = f.floats(4, [0.5, 0.5, 0.4, 0.025])
        a = focal_pixelate(a, x, y, size, px if px < 0.5 else px / 1000)
    elif fid == "linblur":
        amt, ang = f.floats(2, [2.0, 0.0])
        a = linear_blur(a, amt, ang)
    elif fid in ("dir_sat", "dir_brite", "dir_sharp"):
        lr, tb = f.floats(2)
        a = directional(a, fid[4:], lr, tb)
    elif fid == "radtint":
        x, y, feather = f.floats(3, [0.5, 0.5, 0.25])
        col = _hex_color(f.args[3]) if len(f.args) > 3 else None
        m = _radial_mask(a.shape[0], a.shape[1], x, y, 0.5, max(0.05, feather))[..., None]
        c = np.array(col or (0.2, 0.3, 0.6), np.float32)
        a = a * (1 - m * 0.5) + c * a * m
    elif fid == "Matte":
        a = matte(a, f.floats(1, [0.35])[0])
    elif fid == "PicnikGrain":
        a = film_grain(a, 0.09)
    elif fid == "PicnikTint":
        preserve = _num(f.args[0]) if f.args else 0.0
        col = _hex_color(f.args[1]) if len(f.args) > 1 else None
        a = tint(a, col or (0.55, 0.45, 0.3), preserve)
    elif fid == "colortemp":
        t, shift = f.floats(2, [0.125, 0.0])
        a = color_temperature(a, t * 2) + shift * 0.05
    elif fid == "shadow":
        rad, sh, hi = f.floats(3, [0.3, 0.3, 0.2])
        y = luma(a)
        base = _blur(y, (2 + 20 * rad) * max(0.5, _scale(a)))
        lift = sh * (1 - base) ** 2 * 0.6
        drop = hi * base ** 2 * 0.5
        a = _apply_lum_ratio(a, np.clip(y + lift - drop, 0, 1), y)
    elif fid == "contrast":
        c = f.floats(1, [0.1])[0]
        a = 0.5 + (a - 0.5) * (1 + c * 2)
    elif fid == "gamma":
        g = f.floats(1, [0.16])[0]
        a = np.clip(a, 0, 1) ** (1.0 / max(0.2, 1 + g))
    elif fid == "backlight":
        a = fill_light(a, f.floats(1, [0.25])[0])
    elif fid == "blur":
        a = _blur(a, (0.5 + 6 * f.floats(1, [0.2])[0]) * max(0.5, _scale(a)))
    elif fid == "triple":
        br, ct, fl = f.floats(3)
        a = fill_light(a, fl)
        a = (0.5 + (a - 0.5) * (1 + ct)) * (1 + br * 0.5)
    elif fid == "triple2":
        fl, bp, wp = f.floats(3, [0.0, 0.0, 1.0])
        a = fill_light(a, fl)
        a = (a - bp * 0.3) / max(0.2, wp - bp * 0.3)
    elif fid in ("colorfix", "whitept"):
        col = next((c for c in (_hex_color(x) for x in f.args) if c), None)
        if col:
            a = neutral_point(a, col)
    elif fid == "fh_retouch":
        a = retouch(a, decode_strokes(f.args[0] if f.args else ""))
    elif fid == "fh_smart":
        a = smart_enhance(a, f.floats(1, [0.8])[0])
    elif fid == "fh_denoise":
        a = denoise(a, f.floats(1, [0.4])[0])
    elif fid == "fh_clarity":
        a = clarity(a, f.floats(1, [0.4])[0])
    elif fid == "fh_vibrance":
        a = vibrance(a, f.floats(1, [0.3])[0])
    elif fid == "fh_exposure":
        a = a * (2.0 ** f.floats(1)[0])
    elif fid == "fh_contrast":
        c = f.floats(1)[0]
        a = 0.5 + (a - 0.5) * (1 + c)
    elif fid == "fh_hsl":
        hue, s = f.floats(2)
        a = _hue_sat(a, hue, s)
    else:
        return im  # unknown filter: preserved in metadata, not rendered
    return to_im(a)


def _hue_sat(a, hue, sat):
    if cv2 is None:
        return _saturate(a, sat)
    hsv = cv2.cvtColor(np.clip(a, 0, 1).astype(np.float32), cv2.COLOR_RGB2HSV)
    hsv[..., 0] = (hsv[..., 0] + hue * 360.0) % 360.0
    hsv[..., 1] = np.clip(hsv[..., 1] * (1 + sat), 0, 1)
    return cv2.cvtColor(hsv, cv2.COLOR_HSV2RGB)


def _num(s) -> float:
    try:
        return float(s)
    except (TypeError, ValueError):
        return 0.0


def apply_rotation(im: Image.Image, quarter_turns: int) -> Image.Image:
    q = quarter_turns % 4
    if q == 1:
        return im.transpose(Image.ROTATE_270)
    if q == 2:
        return im.transpose(Image.ROTATE_180)
    if q == 3:
        return im.transpose(Image.ROTATE_90)
    return im


def render(im: Image.Image, chain: list[Filter], rotate: int = 0, upto: int | None = None) -> Image.Image:
    """Render rotation + filter chain (optionally only the first `upto` filters)."""
    im = apply_rotation(im, rotate)
    for f in chain[:upto] if upto is not None else chain:
        try:
            im = apply_filter(im, f)
        except Exception:
            continue
    return im


def chain_key(chain: list[Filter], rotate: int) -> str:
    import hashlib
    s = f"{rotate}|" + ";".join(f.to_str() for f in chain)
    return hashlib.sha1(s.encode()).hexdigest()[:12]
