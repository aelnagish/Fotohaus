"""Image/video loading and metadata extraction."""
from __future__ import annotations

import datetime as _dt
import io
import os
import re
import threading

from PIL import Image, ImageOps

Image.MAX_IMAGE_PIXELS = 400_000_000

try:  # HEIC / HEIF / AVIF
    import pillow_heif  # type: ignore
    pillow_heif.register_heif_opener()
    HAVE_HEIF = True
except Exception:  # pragma: no cover
    HAVE_HEIF = False

try:
    import rawpy  # type: ignore
    HAVE_RAW = True
except Exception:  # pragma: no cover
    HAVE_RAW = False

try:
    import cv2  # type: ignore
    HAVE_CV2 = True
except Exception:  # pragma: no cover
    HAVE_CV2 = False

IMAGE_EXT = {".jpg", ".jpeg", ".jpe", ".png", ".gif", ".bmp", ".tif", ".tiff", ".webp",
             ".heic", ".heif", ".avif", ".psd", ".jfif"}
RAW_EXT = {".cr2", ".cr3", ".nef", ".nrw", ".arw", ".srf", ".sr2", ".dng", ".orf", ".rw2",
           ".raf", ".srw", ".pef", ".x3f", ".3fr", ".kdc", ".mrw", ".erf", ".iiq", ".rwl"}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".3gp", ".mts", ".m2ts", ".wmv", ".mpg",
             ".mpeg", ".webm", ".mod", ".flv"}
ALL_EXT = IMAGE_EXT | RAW_EXT | VIDEO_EXT
WRITABLE_EXT = {".jpg", ".jpeg", ".jpe", ".png", ".tif", ".tiff", ".webp", ".heic", ".heif", ".jfif"}


def kind_of(path: str | os.PathLike) -> str | None:
    ext = os.path.splitext(str(path))[1].lower()
    if ext in IMAGE_EXT:
        return "photo"
    if ext in RAW_EXT:
        return "raw"
    if ext in VIDEO_EXT:
        return "video"
    return None


# ------------------------------------------------------------------ loading

_raw_lock = threading.Lock()  # libraw is not fully thread-safe across handles on all builds


_SRGB = None


def _to_srgb(im: Image.Image, icc: bytes | None) -> Image.Image:
    """Convert from an embedded colour profile (e.g. Display P3, Adobe RGB) to sRGB."""
    global _SRGB
    if not icc or im.mode not in ("RGB", "RGBA"):
        return im
    try:
        from PIL import ImageCms
        src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
        desc = (ImageCms.getProfileDescription(src) or "").lower()
        if "srgb" in desc:
            return im
        if _SRGB is None:
            _SRGB = ImageCms.createProfile("sRGB")
        mode = im.mode
        return ImageCms.profileToProfile(im, src, _SRGB, outputMode=mode,
                                         renderingIntent=ImageCms.Intent.PERCEPTUAL)
    except Exception:
        return im


def _flatten(im: Image.Image) -> Image.Image:
    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        im = im.convert("RGBA")
        bg = Image.new("RGB", im.size, (236, 236, 236))
        bg.paste(im, mask=im.split()[-1])
        return bg
    if im.mode != "RGB":
        if im.mode in ("I;16", "I;16B", "I;16L", "I"):
            im = im.point(lambda v: v * (1 / 256)).convert("L")
        im = im.convert("RGB")
    return im


def _load_raw(path: str, max_dim: int | None) -> Image.Image:
    with _raw_lock:
        with rawpy.imread(path) as raw:
            if max_dim and max_dim <= 2048:
                try:
                    th = raw.extract_thumb()
                    if th.format == rawpy.ThumbFormat.JPEG:
                        im = Image.open(io.BytesIO(th.data))
                        im = ImageOps.exif_transpose(im)
                        return _flatten(im)
                    if th.format == rawpy.ThumbFormat.BITMAP:
                        return Image.fromarray(th.data).convert("RGB")
                except Exception:
                    pass
            rgb = raw.postprocess(use_camera_wb=True, half_size=bool(max_dim and max_dim <= 2500),
                                  no_auto_bright=False, output_bps=8)
    return Image.fromarray(rgb)


def video_frame(path: str, max_dim: int | None = None) -> Image.Image | None:
    if not HAVE_CV2:
        return None
    cap = cv2.VideoCapture(path)
    try:
        if not cap.isOpened():
            return None
        n = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
        if n > 10:
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(n * 0.1))
        ok, frame = cap.read()
        if not ok:
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ok, frame = cap.read()
        if not ok:
            return None
        im = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        if max_dim:
            im.thumbnail((max_dim, max_dim), Image.LANCZOS)
        return im
    finally:
        cap.release()


def video_info(path: str) -> tuple[int, int, float]:
    if not HAVE_CV2:
        return 0, 0, 0.0
    cap = cv2.VideoCapture(path)
    try:
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        fps = cap.get(cv2.CAP_PROP_FPS) or 0
        n = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
        dur = float(n / fps) if fps > 0 else 0.0
        return w, h, dur
    finally:
        cap.release()


def load_image(path: str | os.PathLike, max_dim: int | None = None, color_manage: bool = True) -> Image.Image:
    """Load any supported file as an upright RGB PIL image (optionally downscaled)."""
    path = str(path)
    k = kind_of(path)
    if k == "video":
        im = video_frame(path, max_dim)
        if im is None:
            im = Image.new("RGB", (640, 360), (40, 40, 40))
        return im
    if k == "raw" and HAVE_RAW:
        im = _load_raw(path, max_dim)
    else:
        im = Image.open(path)
        if max_dim and im.format == "JPEG":
            im.draft("RGB", (max_dim, max_dim))
        try:
            im.seek(0)
        except EOFError:
            pass
        icc = im.info.get("icc_profile")
        im = ImageOps.exif_transpose(im)
        im = _flatten(im)
        if color_manage:
            im = _to_srgb(im, icc)
    if max_dim and max(im.size) > max_dim:
        im.thumbnail((max_dim, max_dim), Image.LANCZOS)
    return im


# ------------------------------------------------------------------ metadata

EXIF_IFD = 0x8769
GPS_IFD = 0x8825


def _ratio(v) -> float | None:
    try:
        return float(v)
    except Exception:
        try:
            return v[0] / v[1]
        except Exception:
            return None


def _parse_exif_date(s) -> float | None:
    if not s:
        return None
    if isinstance(s, bytes):
        s = s.decode("ascii", "ignore")
    s = str(s).strip().replace("\x00", "")
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y:%m:%d %H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            return _dt.datetime.strptime(s[:19], fmt).timestamp()
        except ValueError:
            continue
    return None


def _gps_to_deg(vals, ref) -> float | None:
    try:
        d, m, s = (_ratio(x) for x in vals)
        deg = d + m / 60.0 + s / 3600.0
        if isinstance(ref, bytes):
            ref = ref.decode("ascii", "ignore")
        if str(ref).upper().startswith(("S", "W")):
            deg = -deg
        return deg
    except Exception:
        return None


_XMP_DESC = re.compile(rb"<dc:description>.*?<rdf:li[^>]*>(.*?)</rdf:li>", re.S)
_XMP_SUBJ = re.compile(rb"<dc:subject>\s*<rdf:Bag>(.*?)</rdf:Bag>", re.S)
_XMP_LI = re.compile(rb"<rdf:li[^>]*>(.*?)</rdf:li>", re.S)


def _xml_unescape(b: bytes) -> str:
    s = b.decode("utf-8", "replace")
    return (s.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
             .replace("&apos;", "'").replace("&amp;", "&")).strip()


def read_metadata(path: str | os.PathLike) -> dict:
    """Return a dict with width, height, taken, camera, lens, exposure, lat, lon,
    caption, keywords, duration (video)."""
    path = str(path)
    md: dict = {"width": 0, "height": 0, "taken": None, "camera": "", "lens": "", "exposure": "",
                "lat": None, "lon": None, "caption": "", "keywords": [], "duration": None}
    k = kind_of(path)
    try:
        st = os.stat(path)
        md["file_time"] = st.st_mtime
    except OSError:
        md["file_time"] = None
    if k == "video":
        w, h, dur = video_info(path)
        md.update(width=w, height=h, duration=dur)
        md["taken"] = _video_creation_time(path) or md["file_time"]
        return md
    try:
        if k == "raw" and HAVE_RAW:
            with _raw_lock:
                with rawpy.imread(path) as raw:
                    s = raw.sizes
                    md["width"], md["height"] = s.width, s.height
                    if s.flip in (5, 6):
                        md["width"], md["height"] = s.height, s.width
            # many RAWs are TIFF-based, Pillow can often still read the EXIF
            try:
                im = Image.open(path)
                _exif_into(md, im, set_size=False)
            except Exception:
                pass
        else:
            im = Image.open(path)
            md["width"], md["height"] = im.size
            _exif_into(md, im, set_size=True)
            _iptc_xmp_into(md, im, path)
    except Exception:
        pass
    if not md["taken"]:
        md["taken"] = md["file_time"]
    return md


def _exif_into(md: dict, im: Image.Image, set_size: bool) -> None:
    exif = im.getexif()
    if not exif:
        return
    orient = exif.get(0x0112, 1)
    md["orientation"] = orient
    if set_size and orient in (5, 6, 7, 8):
        md["width"], md["height"] = md["height"], md["width"]
    make = str(exif.get(0x010F, "") or "").strip("\x00 ").strip()
    model = str(exif.get(0x0110, "") or "").strip("\x00 ").strip()
    if model and make and model.lower().startswith(make.split()[0].lower()):
        md["camera"] = model
    else:
        md["camera"] = (make + " " + model).strip()
    desc = exif.get(0x010E)
    if desc and isinstance(desc, str) and desc.strip() and "OLYMPUS DIGITAL" not in desc:
        md["caption"] = desc.strip("\x00 ").strip()
    try:
        ex = exif.get_ifd(EXIF_IFD)
    except Exception:
        ex = {}
    taken = _parse_exif_date(ex.get(36867)) or _parse_exif_date(ex.get(36868)) or _parse_exif_date(exif.get(0x0132))
    if taken:
        md["taken"] = taken
    parts = []
    et = _ratio(ex.get(33434)) if ex.get(33434) is not None else None
    if et:
        parts.append(f"1/{round(1 / et)}s" if et < 1 else f"{et:g}s")
    fn = _ratio(ex.get(33437)) if ex.get(33437) is not None else None
    if fn:
        parts.append(f"f/{fn:.1f}".replace(".0", ""))
    iso = ex.get(34855)
    if iso:
        if isinstance(iso, (tuple, list)):
            iso = iso[0]
        parts.append(f"ISO {iso}")
    fl = _ratio(ex.get(37386)) if ex.get(37386) is not None else None
    if fl:
        parts.append(f"{fl:.0f}mm")
    md["exposure"] = "  ".join(parts)
    lens = ex.get(42036)
    if lens:
        md["lens"] = str(lens).strip("\x00 ")
    try:
        gps = exif.get_ifd(GPS_IFD)
        if gps and 2 in gps and 4 in gps:
            lat = _gps_to_deg(gps[2], gps.get(1, "N"))
            lon = _gps_to_deg(gps[4], gps.get(3, "E"))
            if lat is not None and lon is not None and not (lat == 0 and lon == 0):
                md["lat"], md["lon"] = lat, lon
    except Exception:
        pass


def _iptc_xmp_into(md: dict, im: Image.Image, path: str) -> None:
    try:
        from PIL import IptcImagePlugin
        iptc = IptcImagePlugin.getiptcinfo(im) or {}
        cap = iptc.get((2, 120))
        if cap:
            if isinstance(cap, list):
                cap = cap[0]
            md["caption"] = cap.decode("utf-8", "replace").strip() or md["caption"]
        kws = iptc.get((2, 25))
        if kws:
            if not isinstance(kws, list):
                kws = [kws]
            md["keywords"] = [k.decode("utf-8", "replace").strip() for k in kws if k]
    except Exception:
        pass
    xmp = im.info.get("xmp") or im.info.get("XML:com.adobe.xmp")
    if xmp is None and path.lower().endswith((".jpg", ".jpeg")):
        try:
            with open(path, "rb") as fh:
                head = fh.read(256 * 1024)
            i = head.find(b"<x:xmpmeta")
            if i >= 0:
                j = head.find(b"</x:xmpmeta>", i)
                xmp = head[i:j + 12] if j > 0 else None
        except OSError:
            pass
    if isinstance(xmp, str):
        xmp = xmp.encode("utf-8")
    if xmp:
        m = _XMP_DESC.search(xmp)
        if m and not md["caption"]:
            md["caption"] = _xml_unescape(m.group(1))
        m = _XMP_SUBJ.search(xmp)
        if m and not md["keywords"]:
            md["keywords"] = [_xml_unescape(x) for x in _XMP_LI.findall(m.group(1)) if x.strip()]


def _video_creation_time(path: str) -> float | None:
    """Read the QuickTime/MP4 'mvhd' creation time without external tools. Walks the top-level
    boxes, so it also works when the 'moov' box is at the end of the file (common for cameras)."""
    try:
        size_total = os.path.getsize(path)
        with open(path, "rb") as fh:
            pos = 0
            moov = None
            for _ in range(10000):
                if pos + 8 > size_total:
                    break
                fh.seek(pos)
                hdr = fh.read(16)
                if len(hdr) < 8:
                    break
                size = int.from_bytes(hdr[:4], "big")
                typ = hdr[4:8]
                head = 8
                if size == 1 and len(hdr) >= 16:
                    size, head = int.from_bytes(hdr[8:16], "big"), 16
                elif size == 0:
                    size = size_total - pos
                if size < head:
                    break
                if typ == b"moov":
                    fh.seek(pos + head)
                    moov = fh.read(min(size - head, 8 * 1024 * 1024))
                    break
                pos += size
        if moov is None:
            return None
        i = moov.find(b"mvhd")
        if i < 0:
            return None
        ver = moov[i + 4]
        if ver == 1:
            secs = int.from_bytes(moov[i + 8:i + 16], "big")
        else:
            secs = int.from_bytes(moov[i + 8:i + 12], "big")
        if secs <= 0:
            return None
        ts = secs - 2082844800  # 1904 epoch -> unix
        if 315532800 < ts < 4102444800:  # 1980..2100
            return float(ts)
    except Exception:
        pass
    return None


def format_bytes(n: int | None) -> str:
    if not n:
        return ""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return str(n)


def insert_exif(path: str, exif_bytes: bytes) -> None:
    """Replace a JPEG's EXIF block safely: the new file is written next to the original and swapped in
    only when complete, so a crash or full disk never leaves a half-written photo behind. The file's
    permissions are kept."""
    import piexif
    with open(path, "rb") as fh:
        data = fh.read()
    out = io.BytesIO()
    piexif.insert(exif_bytes, data, out)
    tmp = f"{path}.fotohaus-{os.getpid()}-{threading.get_ident()}.tmp"
    try:
        with open(tmp, "wb") as fh:
            fh.write(out.getvalue())
            fh.flush()
            os.fsync(fh.fileno())
        try:
            os.chmod(tmp, os.stat(path).st_mode & 0o7777)
        except OSError:
            pass
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def _to_dms(v: float):
    """Degrees → EXIF (deg, min, sec/100) rationals, carrying 59.995″ up to the next minute."""
    v = abs(v)
    total = round(v * 360000)            # hundredths of a second
    d, rem = divmod(total, 360000)
    m, s = divmod(rem, 6000)
    return ((int(d), 1), (int(m), 1), (int(s), 100))


def write_gps_exif(path: str, lat: float, lon: float) -> bool:
    """Write GPS coordinates into a JPEG's EXIF (in place, atomically). Returns success."""
    if not path.lower().endswith((".jpg", ".jpeg", ".jpe", ".jfif")):
        return False
    try:
        import piexif
        try:
            exif = piexif.load(path)
        except Exception:
            exif = {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}, "thumbnail": None}
        if exif.get("thumbnail") is None:
            exif.pop("thumbnail", None)
            exif["1st"] = {}
        exif["GPS"] = {
            piexif.GPSIFD.GPSVersionID: (2, 3, 0, 0),
            piexif.GPSIFD.GPSLatitudeRef: b"N" if lat >= 0 else b"S",
            piexif.GPSIFD.GPSLatitude: _to_dms(lat),
            piexif.GPSIFD.GPSLongitudeRef: b"E" if lon >= 0 else b"W",
            piexif.GPSIFD.GPSLongitude: _to_dms(lon),
        }
        insert_exif(path, piexif.dump(exif))
        return True
    except Exception:
        return False
