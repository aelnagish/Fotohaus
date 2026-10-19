"""Image/video loading and metadata extraction."""
from __future__ import annotations

import datetime as _dt
import io
import os
import re
import threading

from PIL import Image, ImageOps

Image.MAX_IMAGE_PIXELS = 400_000_000

from . import heif, macmedia

# HEIC / HEIF: macOS ImageIO through our own Pillow plugin (AVIF is built into Pillow).
HAVE_HEIF = heif.register()
if not HAVE_HEIF:  # pragma: no cover - only when running from source on another system
    try:
        import pillow_heif  # type: ignore
        pillow_heif.register_heif_opener()
        HAVE_HEIF = True
    except Exception:
        pass

try:
    import rawpy  # type: ignore
    HAVE_RAW = True
except Exception:  # pragma: no cover
    HAVE_RAW = False

HAVE_VIDEO = macmedia.available()
cv2 = None
if not HAVE_VIDEO:  # pragma: no cover - development fallback off macOS
    try:
        import cv2  # type: ignore
        HAVE_VIDEO = True
    except Exception:
        cv2 = None

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


def video_frame(path: str, max_dim: int | None = None, at: float | None = None) -> Image.Image | None:
    """An upright frame of a video (about 10% in, or at `at` seconds); None if it can't be read."""
    im = None
    if macmedia.available():
        try:
            im = macmedia.video_frame(path, max_dim, at)
        except Exception:
            im = None
    elif cv2 is not None:  # pragma: no cover
        im = _cv2_frame(path, at)
    if im is None:          # formats AVFoundation can't read (MKV, AVI, WebM, WMV …)
        im = _qt_frame(path, at)
    if im is not None and max_dim and max(im.size) > max_dim:
        im.thumbnail((max_dim, max_dim), Image.LANCZOS)
    return im


_qt_lock = threading.Lock()


def _qt_frame(path: str, at: float | None) -> Image.Image | None:
    """Grab a frame with Qt Multimedia (its bundled LGPL FFmpeg). Needs a running QApplication."""
    try:
        from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer, QUrl
        from PySide6.QtMultimedia import QMediaPlayer, QVideoSink
        import shiboken6
    except Exception:
        return None
    if QCoreApplication.instance() is None:
        return None
    with _qt_lock:
        loop = QEventLoop()
        player, sink = QMediaPlayer(), QVideoSink()
        player.setVideoSink(sink)
        got: dict = {}

        def on_frame(f):
            if "img" not in got and f.isValid():
                img = f.toImage()
                if not img.isNull():
                    got["img"] = img
                    loop.quit()

        def on_status(st):
            if st == QMediaPlayer.MediaStatus.LoadedMedia:
                pos = at * 1000 if at is not None else player.duration() * 0.1
                player.setPosition(int(max(0, pos)))
                player.play()
            elif st == QMediaPlayer.MediaStatus.InvalidMedia:
                loop.quit()

        sink.videoFrameChanged.connect(on_frame)
        player.mediaStatusChanged.connect(on_status)
        timer = QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(loop.quit)
        timer.start(8000)
        player.setSource(QUrl.fromLocalFile(os.path.abspath(path)))
        loop.exec()
        timer.stop()
        player.stop()
        player.setVideoSink(None)
        for obj in (timer, player, sink):
            shiboken6.delete(obj)
        img = got.get("img")
    if img is None:
        return None
    from PySide6.QtCore import QBuffer, QIODevice
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return Image.open(io.BytesIO(bytes(buf.data()))).convert("RGB")


def _cv2_frame(path, at):  # pragma: no cover - development fallback off macOS
    cap = cv2.VideoCapture(path)
    try:
        if not cap.isOpened():
            return None
        if at is not None:
            cap.set(cv2.CAP_PROP_POS_MSEC, at * 1000)
        else:
            n = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
            if n > 10:
                cap.set(cv2.CAP_PROP_POS_FRAMES, int(n * 0.1))
        ok, frame = cap.read()
        if not ok and at is None:
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ok, frame = cap.read()
        return Image.fromarray(frame[..., ::-1].copy()) if ok else None
    finally:
        cap.release()


def video_info(path: str) -> tuple[int, int, float]:
    """(width, height, seconds); zeros if unknown."""
    if macmedia.available():
        try:
            return macmedia.video_info(path)
        except Exception:
            return 0, 0, 0.0
    if cv2 is None:
        return 0, 0, 0.0
    cap = cv2.VideoCapture(path)  # pragma: no cover
    try:
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        fps = cap.get(cv2.CAP_PROP_FPS) or 0
        n = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
        return w, h, (float(n / fps) if fps > 0 else 0.0)
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
        if max_dim and im.format in ("JPEG", "HEIF"):
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
    """Read the QuickTime/MP4 'mvhd' creation time without external tools."""
    try:
        with open(path, "rb") as fh:
            data = fh.read(4 * 1024 * 1024)
        i = data.find(b"mvhd")
        if i < 0:
            return None
        ver = data[i + 4]
        if ver == 1:
            secs = int.from_bytes(data[i + 8:i + 16], "big")
        else:
            secs = int.from_bytes(data[i + 8:i + 12], "big")
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


def write_gps_exif(path: str, lat: float, lon: float) -> bool:
    """Write GPS coordinates into a JPEG's EXIF (in place). Returns success."""
    if not path.lower().endswith((".jpg", ".jpeg", ".jpe", ".jfif")):
        return False
    try:
        import piexif

        def to_dms(v: float):
            v = abs(v)
            d = int(v)
            m = int((v - d) * 60)
            s = round(((v - d) * 60 - m) * 60 * 100)
            return ((d, 1), (m, 1), (s, 100))
        try:
            exif = piexif.load(path)
        except Exception:
            exif = {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}, "thumbnail": None}
        exif["GPS"] = {
            piexif.GPSIFD.GPSVersionID: (2, 3, 0, 0),
            piexif.GPSIFD.GPSLatitudeRef: b"N" if lat >= 0 else b"S",
            piexif.GPSIFD.GPSLatitude: to_dms(lat),
            piexif.GPSIFD.GPSLongitudeRef: b"E" if lon >= 0 else b"W",
            piexif.GPSIFD.GPSLongitude: to_dms(lon),
        }
        piexif.insert(piexif.dump(exif), path)
        return True
    except Exception:
        return False
