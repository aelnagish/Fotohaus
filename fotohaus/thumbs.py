"""On-disk thumbnail and preview cache."""
from __future__ import annotations

import hashlib
import os
import threading
from pathlib import Path

from PIL import Image

from . import media
from .edits import render
from .paths import PREVIEW_DIR, THUMB_DIR
from .picasa.ini import parse_filters

THUMB_PX = 360
PREVIEW_PX = 2048


def _key(row, size: int) -> str:
    s = f"{row['path']}|{row['mtime']}|{row['filters']}|{row['rotate']}|{size}"
    return hashlib.sha1(s.encode("utf-8", "replace")).hexdigest()[:20]


def thumb_path(row, size: int = THUMB_PX) -> Path:
    base = THUMB_DIR if size <= THUMB_PX else PREVIEW_DIR
    k = _key(row, size)
    return base / k[:2] / f"{k}.jpg"


def make_thumb(row, size: int = THUMB_PX) -> Path:
    """Return a cached JPEG for the photo (with edits applied), creating it if needed."""
    out = thumb_path(row, size)
    if out.exists():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    chain = parse_filters(row["filters"])
    # crops need more source pixels to keep the thumbnail sharp
    load_px = size * 2 if chain else int(size * 1.15)
    im = media.load_image(row["path"], max_dim=max(load_px, 256))
    if chain or row["rotate"]:
        im = render(im, chain, row["rotate"] or 0)
    im.thumbnail((size, size), Image.LANCZOS)
    # several threads can render the same thumbnail at once: each writes its own temp file
    tmp = out.with_name(f"{out.stem}.{os.getpid()}-{threading.get_ident()}.tmp")
    try:
        im.save(tmp, "JPEG", quality=86 if size <= THUMB_PX else 92)
        os.replace(tmp, out)
    finally:
        if tmp.exists():
            tmp.unlink()
    return out


def drop_cached(row) -> None:
    for size in (THUMB_PX, PREVIEW_PX):
        p = thumb_path(row, size)
        try:
            p.unlink()
        except OSError:
            pass
