"""HEIC/HEIF support for Pillow, decoded and encoded by macOS ImageIO.

`register()` adds a Pillow plugin, so `Image.open("photo.heic")` and `im.save(path, "HEIF")` work
everywhere in Fotohaus. Pixels come out upright and in sRGB; the EXIF (read straight from the HEIF
container) has its orientation reset to 1 to match.
"""
from __future__ import annotations

import struct

from PIL import Image, ImageFile

from . import macmedia

_BRANDS = {b"heic", b"heix", b"heim", b"heis", b"hevc", b"hevx", b"hevm", b"hevs", b"mif1", b"msf1"}


def _accept(prefix: bytes) -> bool:
    return len(prefix) >= 12 and prefix[4:8] == b"ftyp" and prefix[8:12] in _BRANDS


# ------------------------------------------------------------------ HEIF container (ISO BMFF) parsing

def _boxes(data: bytes, start: int, end: int):
    i = start
    while i + 8 <= end:
        size, typ = struct.unpack(">I4s", data[i:i + 8])
        hdr = 8
        if size == 1:
            size = struct.unpack(">Q", data[i + 8:i + 16])[0]
            hdr = 16
        elif size == 0:
            size = end - i
        if size < hdr or i + size > end:
            return
        yield typ, i + hdr, i + size
        i += size


def _uint(data: bytes, pos: int, n: int) -> tuple[int, int]:
    if n == 0:
        return 0, pos
    return int.from_bytes(data[pos:pos + n], "big"), pos + n


def exif_from_heif(data: bytes) -> bytes | None:
    """The raw TIFF-format EXIF block stored in a HEIF file, or None."""
    try:
        meta = next(((s, e) for t, s, e in _boxes(data, 0, len(data)) if t == b"meta"), None)
        if not meta:
            return None
        ms, me = meta[0] + 4, meta[1]                      # FullBox: skip version/flags
        exif_id = None
        locs: dict[int, list[tuple[int, int]]] = {}
        for t, s, e in _boxes(data, ms, me):
            if t == b"iinf":
                ver = data[s]
                p = s + 4
                _, p = _uint(data, p, 2 if ver == 0 else 4)
                for bt, bs, be in _boxes(data, p, e):
                    if bt != b"infe":
                        continue
                    iv = data[bs]
                    q = bs + 4
                    if iv < 2:
                        continue
                    item_id, q = _uint(data, q, 2 if iv == 2 else 4)
                    q += 2                                   # protection index
                    if data[q:q + 4] == b"Exif":
                        exif_id = item_id
            elif t == b"iloc":
                ver = data[s]
                p = s + 4
                off_sz, len_sz = data[p] >> 4, data[p] & 15
                base_sz, idx_sz = data[p + 1] >> 4, (data[p + 1] & 15 if ver in (1, 2) else 0)
                p += 2
                count, p = _uint(data, p, 2 if ver < 2 else 4)
                for _ in range(count):
                    item_id, p = _uint(data, p, 2 if ver < 2 else 4)
                    method = 0
                    if ver in (1, 2):
                        method = int.from_bytes(data[p:p + 2], "big") & 15
                        p += 2
                    p += 2                                   # data reference index
                    base, p = _uint(data, p, base_sz)
                    n_ext, p = _uint(data, p, 2)
                    exts = []
                    for _ in range(n_ext):
                        _, p = _uint(data, p, idx_sz)
                        off, p = _uint(data, p, off_sz)
                        ln, p = _uint(data, p, len_sz)
                        exts.append((base + off, ln))
                    if method == 0:
                        locs[item_id] = exts
        if exif_id is None or exif_id not in locs:
            return None
        blob = b"".join(data[o:o + n] for o, n in locs[exif_id])
        if len(blob) < 8:
            return None
        skip = int.from_bytes(blob[:4], "big")
        tiff = blob[4 + skip:]
        if tiff.startswith(b"Exif\x00\x00"):
            tiff = tiff[6:]
        return tiff if tiff[:2] in (b"II", b"MM") else None
    except Exception:
        return None


def _upright_exif(tiff: bytes | None) -> bytes | None:
    if not tiff:
        return None
    try:
        ex = Image.Exif()
        ex.load(b"Exif\x00\x00" + tiff)
        ex[0x0112] = 1
        return ex.tobytes()
    except Exception:
        return None


# ------------------------------------------------------------------ Pillow plugin

class HeifImageFile(ImageFile.ImageFile):
    format = "HEIF"
    format_description = "HEIF / HEIC image (macOS ImageIO)"

    def _open(self):
        self.fp.seek(0)
        self._heif_data = self.fp.read()
        info = macmedia.image_info(self._heif_data)
        if not info:
            raise SyntaxError("not a readable HEIF image")
        w, h, orient = info
        if orient in (5, 6, 7, 8):
            w, h = h, w
        self._mode = "RGB"
        self._size = (w, h)
        self.tile = []
        exif = _upright_exif(exif_from_heif(self._heif_data))
        if exif:
            self.info["exif"] = exif
        self._target = None

    def draft(self, mode, size):
        """Decode at reduced size (much faster for thumbnails)."""
        if size and getattr(self, "im", None) is None:
            self._target = max(size)
            w, h = self.size
            s = min(1.0, self._target / max(w, h))
            self._size = (max(1, round(w * s)), max(1, round(h * s)))
        return None

    def load(self):
        if getattr(self, "im", None) is None and getattr(self, "_heif_data", None) is not None:
            img = macmedia.load_image(self._heif_data, self._target)
            if img.size != self.size:
                img = img.resize(self.size, Image.LANCZOS)
            self.im = img.im
            self._mode = img.mode
            self._heif_data = None
        return Image.Image.load(self)


def _save(im: Image.Image, fp, filename):
    enc = im.encoderinfo
    exif = enc.get("exif")
    if isinstance(exif, Image.Exif):
        exif = exif.tobytes()
    fp.write(macmedia.encode_heic(im, int(enc.get("quality", 90)), exif or None))


_registered = False


def register() -> bool:
    """Install the plugin (macOS only). Returns True when HEIC can be read."""
    global _registered
    if _registered:
        return True
    if not macmedia.available():
        return False
    Image.register_open(HeifImageFile.format, HeifImageFile, _accept)
    Image.register_save(HeifImageFile.format, _save)
    Image.register_extensions(HeifImageFile.format, [".heic", ".heif", ".hif"])
    Image.register_mime(HeifImageFile.format, "image/heif")
    _registered = True
    return True


__all__ = ["register", "exif_from_heif", "HeifImageFile"]
