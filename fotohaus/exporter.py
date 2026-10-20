"""Export (resize / convert / watermark) and collage creation."""
from __future__ import annotations

import math
import os
import random
import shutil
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps

from . import media
from .edits import render
from .picasa.ini import parse_filters


@dataclass
class ExportOptions:
    dest: str
    max_size: int | None = None      # long edge in px; None = original
    fmt: str = "JPEG"                # JPEG | PNG | WEBP | HEIF | ORIGINAL
    quality: int = 88
    watermark: str = ""
    strip_location: bool = False
    keep_metadata: bool = True
    apply_edits: bool = True


def _font(size: int):
    for name in ("/System/Library/Fonts/SFNS.ttf", "/System/Library/Fonts/Helvetica.ttc",
                 "/Library/Fonts/Arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        if os.path.exists(name):
            try:
                return ImageFont.truetype(name, size)
            except OSError:
                continue
    return ImageFont.load_default()


def add_watermark(im: Image.Image, text: str) -> Image.Image:
    im = im.convert("RGBA")
    layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    size = max(12, int(min(im.size) * 0.035))
    font = _font(size)
    bbox = d.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x, y = im.width - tw - size, im.height - th - size
    d.text((x + 1, y + 1), text, font=font, fill=(0, 0, 0, 110))
    d.text((x, y), text, font=font, fill=(255, 255, 255, 190))
    return Image.alpha_composite(im, layer).convert("RGB")


def _unique(path: Path) -> Path:
    if not path.exists():
        return path
    stem, ext = path.stem, path.suffix
    i = 2
    while True:
        p = path.with_name(f"{stem} ({i}){ext}")
        if not p.exists():
            return p
        i += 1


def export_photo(row, opts: ExportOptions) -> Path:
    src = row["path"]
    dest_dir = Path(opts.dest)
    dest_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(src).stem
    chain = parse_filters(row["filters"]) if opts.apply_edits else []
    rot = (row["rotate"] or 0) if opts.apply_edits else 0
    plain_copy = (opts.fmt == "ORIGINAL" and not chain and not rot and not opts.max_size
                  and not opts.watermark and not opts.strip_location)
    if row["kind"] == "video" or plain_copy:
        out = _unique(dest_dir / Path(src).name)
        shutil.copy2(src, out)
        return out
    im = media.load_image(src)
    im = render(im, chain, rot)
    if opts.max_size and max(im.size) > opts.max_size:
        im.thumbnail((opts.max_size, opts.max_size), Image.LANCZOS)
    if opts.watermark:
        im = add_watermark(im, opts.watermark)
    fmt = opts.fmt
    if fmt == "ORIGINAL":
        ext = Path(src).suffix.lower()
        fmt = {".png": "PNG", ".webp": "WEBP", ".heic": "HEIF", ".heif": "HEIF", ".tif": "TIFF",
               ".tiff": "TIFF"}.get(ext, "JPEG")
    ext = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp", "HEIF": ".heic", "TIFF": ".tif"}[fmt]
    out = _unique(dest_dir / f"{stem}{ext}")
    kw = {}
    if opts.keep_metadata:
        try:
            exif = Image.open(src).getexif()
            if exif:
                exif[0x0112] = 1
                if opts.strip_location and 0x8825 in exif:
                    del exif[0x8825]
                kw["exif"] = exif.tobytes()
        except Exception:
            pass
    if fmt == "JPEG":
        im.save(out, "JPEG", quality=opts.quality, optimize=True, **kw)
    elif fmt == "WEBP":
        im.save(out, "WEBP", quality=opts.quality, **kw)
    elif fmt == "HEIF":
        im.save(out, "HEIF", quality=opts.quality, **kw)
    elif fmt == "PNG":
        im.save(out, "PNG")
    else:
        im.save(out, fmt)
    return out


# ------------------------------------------------------------------ collage

COLLAGE_TYPES = {"pile": "Picture Pile", "mosaic": "Mosaic", "frame": "Frame Mosaic", "grid": "Grid",
                 "contact": "Contact Sheet", "exposure": "Multiple Exposure"}
BORDERS = {"none": "None", "white": "White border", "shadow": "White border with shadow", "polaroid": "Polaroid"}


def _load_rendered(r, max_dim=1600):
    im = media.load_image(r["path"], max_dim=max_dim)
    return render(im, parse_filters(r["filters"]), r["rotate"] or 0)


def _framed(im: Image.Image, border: str, caption: str = "") -> Image.Image:
    if border == "none":
        return im.convert("RGBA")
    if border == "polaroid":
        m = max(6, im.width // 18)
        out = Image.new("RGBA", (im.width + 2 * m, im.height + m + int(im.height * 0.2)), (250, 248, 242, 255))
        out.paste(im, (m, m))
        if caption:
            d = ImageDraw.Draw(out)
            f = _font(max(10, int(im.height * 0.07)))
            d.text((out.width / 2, im.height + m + (out.height - im.height - m) / 2), caption[:40], font=f,
                   fill=(60, 60, 60, 255), anchor="mm")
        return out
    m = max(3, im.width // 40)
    out = ImageOps.expand(im, border=m, fill=(255, 255, 255)).convert("RGBA")
    return out


def make_collage(rows, out_path: str, width: int = 3000, height: int = 2000, layout: str = "mosaic",
                 spacing: int = 12, background=(255, 255, 255), seed: int = 1, border: str = "none",
                 captions: bool = False, background_row=None, shadows: bool = False) -> Path:
    """Picasa-style collages: pile, mosaic, frame (mosaic around a large centre photo), grid,
    contact (sheet with file names), exposure (multiple exposure blend)."""
    rows = list(rows)[:300]
    canvas = Image.new("RGBA", (width, height), tuple(background) + (255,))
    if background_row is not None:
        try:
            bg = ImageOps.fit(_load_rendered(background_row, max(width, height)), (width, height))
            canvas.paste(bg, (0, 0))
        except Exception:
            pass
    imgs, caps = [], []
    for r in rows:
        try:
            imgs.append(_load_rendered(r, 1400 if layout != "contact" else 600))
            caps.append(r["caption"] or r["filename"] if layout == "contact" else (r["caption"] or ""))
        except Exception:
            continue
    n = len(imgs)
    if n == 0:
        canvas.convert("RGB").save(out_path, quality=92)
        return Path(out_path)
    shadows = shadows or border == "shadow"
    if layout == "exposure":
        acc = np.zeros((height, width, 3), np.float32)
        for im in imgs:
            acc += np.asarray(ImageOps.fit(im, (width, height)), np.float32)
        out = Image.fromarray(np.clip(acc / n * 1.15, 0, 255).astype(np.uint8))
        out.save(out_path, quality=92)
        return Path(out_path)
    if layout == "pile":
        rnd = random.Random(seed)
        tile = int(min(width, height) * (0.5 if n < 6 else 0.38))
        for im, cap in zip(imgs, caps):
            t = _framed(ImageOps.contain(im, (tile, tile)), border if border != "none" else "white",
                        cap if captions else "")
            t = t.rotate(rnd.uniform(-20, 20), expand=True, resample=Image.BICUBIC)
            x = rnd.randint(-tile // 8, max(1, width - t.width + tile // 8))
            y = rnd.randint(-tile // 8, max(1, height - t.height + tile // 8))
            _paste_layer(canvas, t, x, y, True)
    elif layout in ("grid", "contact"):
        cols = math.ceil(math.sqrt(n * width / height))
        rws = math.ceil(n / cols)
        label_h = int(height / rws * 0.14) if (layout == "contact" or captions) else 0
        cw = (width - spacing * (cols + 1)) / cols
        ch = (height - spacing * (rws + 1)) / rws - label_h
        f = _font(max(10, int(label_h * 0.55))) if label_h else None
        d = ImageDraw.Draw(canvas)
        for i, (im, cap) in enumerate(zip(imgs, caps)):
            c, r = i % cols, i // cols
            x0 = int(spacing + c * (cw + spacing))
            y0 = int(spacing + r * (ch + label_h + spacing))
            if layout == "contact":
                cell = ImageOps.contain(im, (max(1, int(cw)), max(1, int(ch))))
                ox, oy = x0 + (int(cw) - cell.width) // 2, y0 + (int(ch) - cell.height) // 2
            else:
                cell = ImageOps.fit(im, (max(1, int(cw)), max(1, int(ch))), Image.LANCZOS)
                ox, oy = x0, y0
            t = _framed(cell, border if border != "polaroid" else "white")
            _paste_layer(canvas, t, ox, oy, shadows)
            if label_h and cap:
                d.text((x0 + cw / 2, y0 + ch + label_h / 2), cap[:48], font=f, fill=(40, 40, 40, 255), anchor="mm")
    elif layout == "frame" and n >= 5:
        cols = max(3, math.ceil(math.sqrt((n + 3) * width / height)))
        rws = max(3, math.ceil((n + 3) / cols))
        cw = (width - spacing * (cols + 1)) / cols
        ch = (height - spacing * (rws + 1)) / rws
        c0, r0 = (cols - 2) // 2, (rws - 2) // 2
        big = ImageOps.fit(imgs[0], (int(2 * cw + spacing), int(2 * ch + spacing)), Image.LANCZOS)
        _paste_layer(canvas, _framed(big, border), int(spacing + c0 * (cw + spacing)),
                     int(spacing + r0 * (ch + spacing)), shadows)
        k = 1
        for r in range(rws):
            for c in range(cols):
                if r0 <= r <= r0 + 1 and c0 <= c <= c0 + 1:
                    continue
                im = imgs[k % n] if k < n or n > 1 else imgs[0]
                cell = ImageOps.fit(im, (max(1, int(cw)), max(1, int(ch))), Image.LANCZOS)
                _paste_layer(canvas, _framed(cell, border), int(spacing + c * (cw + spacing)),
                             int(spacing + r * (ch + spacing)), shadows)
                k += 1
    else:  # mosaic (and frame with too few photos)
        _mosaic(canvas, imgs, spacing, border, shadows)
    canvas.convert("RGB").save(out_path, quality=92)
    return Path(out_path)


def _paste_layer(canvas, tile, x, y, shadow):
    tile = tile.convert("RGBA")
    if shadow:
        from PIL import ImageFilter
        off = max(3, tile.width // 60)
        sh = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        mask = tile.split()[-1].point(lambda v: 110 if v else 0)
        sh.paste((0, 0, 0, 255), (x + off, y + off), mask)
        canvas.alpha_composite(sh.filter(ImageFilter.GaussianBlur(off)))
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    layer.paste(tile, (x, y), tile)
    canvas.alpha_composite(layer)


def _mosaic(canvas: Image.Image, imgs: list[Image.Image], spacing: int, border: str = "none",
            shadows: bool = False) -> None:
    """Justified rows that fill the canvas."""
    W, H = canvas.size
    n = len(imgs)
    rows_n = max(1, round(math.sqrt(n * H / W * 1.3)))
    per = math.ceil(n / rows_n)
    groups = [imgs[i:i + per] for i in range(0, n, per)]
    row_h = (H - spacing * (len(groups) + 1)) / len(groups)
    y = spacing
    for g in groups:
        aspects = [im.width / im.height for im in g]
        total = sum(aspects)
        avail = W - spacing * (len(g) + 1)
        x = spacing
        for im, a in zip(g, aspects):
            w = avail * a / total
            cell = ImageOps.fit(im, (max(1, int(w)), max(1, int(row_h))), Image.LANCZOS)
            _paste_layer(canvas, _framed(cell, border if border != "polaroid" else "white"), int(x), int(y), shadows)
            x += w + spacing
        y += row_h + spacing


# ------------------------------------------------------------------ HTML page export

HTML_TEMPLATES = {
    "grid": ("Light grid", "#ffffff", "#222", "#666"),
    "dark": ("Dark grid", "#141416", "#eee", "#999"),
    "captions": ("Captioned list", "#faf8f3", "#222", "#555"),
}


def _esc(s: str) -> str:
    import html
    return html.escape(s or "")


def export_html(rows, dest: str, title: str = "Photos", template: str = "grid", image_size: int = 1280,
                thumb_size: int = 240, progress=None) -> Path:
    """Static web album: index.html with thumbnails and one page per photo (prev/next)."""
    dest_p = Path(dest)
    (dest_p / "images").mkdir(parents=True, exist_ok=True)
    (dest_p / "thumbnails").mkdir(exist_ok=True)
    name, bg, fg, muted = HTML_TEMPLATES.get(template, HTML_TEMPLATES["grid"])
    rows = [r for r in rows if r["kind"] != "video"]
    items = []
    for i, r in enumerate(rows):
        if progress:
            progress(i, len(rows))
        im = _load_rendered(r, max(image_size, 2000))
        im.thumbnail((image_size, image_size), Image.LANCZOS)
        base = f"{i + 1:04d}"
        im.save(dest_p / "images" / f"{base}.jpg", quality=88)
        th = im.copy()
        th.thumbnail((thumb_size, thumb_size), Image.LANCZOS)
        th.save(dest_p / "thumbnails" / f"{base}.jpg", quality=82)
        items.append((base, r["caption"] or "", r["filename"], im.size))
    css = (f"body{{margin:0;font-family:-apple-system,Helvetica,Arial,sans-serif;background:{bg};color:{fg}}}"
           f"header{{padding:28px 32px 8px}}h1{{margin:0;font-weight:600}}.sub{{color:{muted}}}"
           f".grid{{display:flex;flex-wrap:wrap;gap:12px;padding:16px 32px 40px}}"
           f".grid a{{display:block;text-decoration:none;color:{muted};font-size:13px;width:{thumb_size}px}}"
           f".grid img{{width:{thumb_size}px;height:{thumb_size}px;object-fit:cover;border-radius:4px;display:block}}"
           f".list{{max-width:900px;margin:auto;padding:16px}}.list figure{{margin:0 0 36px}}"
           f".list img{{width:100%;border-radius:4px}}figcaption{{color:{muted};margin-top:6px}}"
           f".photo{{text-align:center;padding:20px}}.photo img{{max-width:96vw;max-height:80vh;box-shadow:0 4px 24px #0004}}"
           f"nav{{padding:14px;text-align:center}}nav a{{color:{fg};margin:0 14px;text-decoration:none}}"
           f"footer{{color:{muted};font-size:12px;text-align:center;padding:24px}}")
    (dest_p / "style.css").write_text(css, encoding="utf-8")
    head = lambda t: (f"<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' "  # noqa: E731
                      f"content='width=device-width,initial-scale=1'><title>{_esc(t)}</title>"
                      f"<link rel='stylesheet' href='style.css'></head><body>")
    if template == "captions":
        body = "".join(f"<figure><a href='{b}.html'><img src='images/{b}.jpg' alt=''></a>"
                       f"<figcaption>{_esc(c or fn)}</figcaption></figure>" for b, c, fn, _ in items)
        idx = f"{head(title)}<header><h1>{_esc(title)}</h1><div class='sub'>{len(items)} photos</div></header>" \
              f"<div class='list'>{body}</div>"
    else:
        body = "".join(f"<a href='{b}.html'><img src='thumbnails/{b}.jpg' alt=''>{_esc(c)}</a>"
                       for b, c, fn, _ in items)
        idx = f"{head(title)}<header><h1>{_esc(title)}</h1><div class='sub'>{len(items)} photos</div></header>" \
              f"<div class='grid'>{body}</div>"
    idx += "<footer>Made with Fotohaus</footer></body></html>"
    (dest_p / "index.html").write_text(idx, encoding="utf-8")
    for k, (b, c, fn, size) in enumerate(items):
        prev_ = items[k - 1][0] + ".html" if k > 0 else "index.html"
        next_ = items[k + 1][0] + ".html" if k + 1 < len(items) else "index.html"
        page = (f"{head(c or fn)}<nav><a href='{prev_}'>&larr; Previous</a><a href='index.html'>Index</a>"
                f"<a href='{next_}'>Next &rarr;</a></nav><div class='photo'><img src='images/{b}.jpg' "
                f"width='{size[0]}' height='{size[1]}' alt=''><p>{_esc(c)}</p><p class='sub'>{k + 1} of {len(items)}"
                f"</p></div></body></html>")
        (dest_p / f"{b}.html").write_text(page, encoding="utf-8")
    return dest_p / "index.html"


# ------------------------------------------------------------------ Google Earth KML

def export_kml(rows, out_path: str, title: str = "Fotohaus photos") -> int:
    pts = [r for r in rows if r["lat"] is not None]
    parts = [f"<?xml version='1.0' encoding='UTF-8'?>\n<kml xmlns='http://www.opengis.net/kml/2.2'><Document>"
             f"<name>{_esc(title)}</name>"]
    for r in pts:
        parts.append(f"<Placemark><name>{_esc(r['caption'] or r['filename'])}</name>"
                     f"<description><![CDATA[<img src='file://{r['path']}' width='320'>]]></description>"
                     f"<Point><coordinates>{r['lon']:.7f},{r['lat']:.7f},0</coordinates></Point></Placemark>")
    parts.append("</Document></kml>")
    Path(out_path).write_text("".join(parts), encoding="utf-8")
    return len(pts)


# ------------------------------------------------------------------ poster

PAPER = {"Letter (8.5×11 in)": (8.5, 11.0), "A4 (210×297 mm)": (8.27, 11.69), "Legal (8.5×14 in)": (8.5, 14.0),
         "A3 (297×420 mm)": (11.69, 16.54)}


def make_poster(row, dest_dir: str, enlarge: int = 3, paper: str = "Letter (8.5×11 in)", overlap: bool = True,
                dpi: int = 200) -> Path:
    """Split one photo into enlarge×enlarge printable pages (+ a combined PDF)."""
    pw, ph = PAPER.get(paper, (8.5, 11.0))
    im = _load_rendered(row, 8000)
    if im.width > im.height:
        pw, ph = ph, pw
    page_px = (int(pw * dpi), int(ph * dpi))
    margin = int(0.25 * dpi)
    tile_w, tile_h = page_px[0] - 2 * margin, page_px[1] - 2 * margin
    ov = int(0.25 * dpi) if overlap else 0
    big_w = tile_w * enlarge - ov * (enlarge - 1)
    big = ImageOps.fit(im, (big_w, int(big_w * im.height / im.width)), Image.LANCZOS)
    rows_n = math.ceil((big.height + ov * 0) / (tile_h - ov)) if tile_h > ov else enlarge
    out_dir = Path(dest_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(row["path"]).stem
    pages = []
    for r in range(rows_n):
        for c in range(enlarge):
            x, y = c * (tile_w - ov), r * (tile_h - ov)
            if y >= big.height:
                continue
            crop = big.crop((x, y, x + tile_w, y + tile_h))
            page = Image.new("RGB", page_px, (255, 255, 255))
            page.paste(crop, (margin, margin))
            d = ImageDraw.Draw(page)
            d.text((margin, page_px[1] - margin * 0.7), f"{stem} — row {r + 1}, column {c + 1}",
                   fill=(150, 150, 150), font=_font(int(dpi * 0.12)))
            p = out_dir / f"{stem}-poster-r{r + 1}c{c + 1}.jpg"
            page.save(p, quality=92, dpi=(dpi, dpi))
            pages.append(page)
    pdf = out_dir / f"{stem}-poster.pdf"
    if pages:
        pages[0].save(pdf, save_all=True, append_images=pages[1:], resolution=dpi)
    return pdf


# ------------------------------------------------------------------ passport photos

PASSPORT_SPECS = {
    "US passport (2×2 in)": (50.8, 50.8, 0.60),
    "EU / UK / Schengen (35×45 mm)": (35.0, 45.0, 0.75),
    "Canada (50×70 mm)": (50.0, 70.0, 0.47),
    "India (2×2 in)": (50.8, 50.8, 0.62),
    "China (33×48 mm)": (33.0, 48.0, 0.68),
    "Japan (35×45 mm)": (35.0, 45.0, 0.75),
}


def make_passport_sheet(row, out_path: str, spec: str = "EU / UK / Schengen (35×45 mm)", dpi: int = 300,
                        sheet_in=(6.0, 4.0)) -> Path:
    """Crop around the face to the official size and tile copies on a 6×4 in print."""
    w_mm, h_mm, head_frac = PASSPORT_SPECS[spec]
    im = _load_rendered(row, 4000)
    rgb = np.ascontiguousarray(np.asarray(im))
    face = None
    try:
        from .ai import faces as F
        if F.available():
            det = F.engine().detect(rgb)
            if det:
                face = max(det, key=lambda d: (d["rect"][2] - d["rect"][0]) * (d["rect"][3] - d["rect"][1]))["rect"]
    except Exception:
        face = None
    W, H = im.size
    if face:
        l, t, r, b = face[0] * W, face[1] * H, face[2] * W, face[3] * H
        crown, chin = t - (b - t) * 0.5, b + (b - t) * 0.03   # detector box ≈ brow to chin
    else:
        l, r, crown, chin = W * 0.3, W * 0.7, H * 0.15, H * 0.7
    head_h = chin - crown
    crop_h = head_h / head_frac
    crop_w = crop_h * w_mm / h_mm
    cx = (l + r) / 2
    top = crown - (crop_h - head_h) * 0.42
    box = (int(cx - crop_w / 2), int(top), int(cx + crop_w / 2), int(top + crop_h))
    photo = Image.new("RGB", (box[2] - box[0], box[3] - box[1]), (255, 255, 255))
    photo.paste(im.crop((max(0, box[0]), max(0, box[1]), min(W, box[2]), min(H, box[3]))),
                (max(0, -box[0]), max(0, -box[1])))
    pw, ph = int(w_mm / 25.4 * dpi), int(h_mm / 25.4 * dpi)
    photo = photo.resize((pw, ph), Image.LANCZOS)
    sw, sh = int(sheet_in[0] * dpi), int(sheet_in[1] * dpi)
    sheet = Image.new("RGB", (sw, sh), (255, 255, 255))
    gap = int(0.08 * dpi)
    cols = max(1, (sw - gap) // (pw + gap))
    rws = max(1, (sh - gap) // (ph + gap))
    x0 = (sw - cols * pw - (cols - 1) * gap) // 2
    y0 = (sh - rws * ph - (rws - 1) * gap) // 2
    d = ImageDraw.Draw(sheet)
    for rr in range(rws):
        for cc in range(cols):
            x, y = x0 + cc * (pw + gap), y0 + rr * (ph + gap)
            sheet.paste(photo, (x, y))
            d.rectangle([x - 1, y - 1, x + pw, y + ph], outline=(200, 200, 200))
    sheet.save(out_path, quality=95, dpi=(dpi, dpi))
    return Path(out_path)
