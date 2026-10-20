"""Movie Maker: turn photos (and video clips) into an MP4 slideshow movie, like Picasa's Movie Maker.

Transitions: dissolve, pan & zoom (Ken Burns), wipe, none. Optional title slide, captions and
a music track (trimmed / faded to the movie length). Encoded with PyAV (FFmpeg): uses the
hardware H.264 encoder on macOS when available.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from PIL import Image, ImageDraw, ImageOps

from . import media
from .edits import render
from .exporter import _font
from .picasa.ini import parse_filters

SIZES = {"1080p (1920×1080)": (1920, 1080), "720p (1280×720)": (1280, 720), "480p (854×480)": (854, 480),
         "Square (1080×1080)": (1080, 1080), "Vertical (1080×1920)": (1080, 1920)}


@dataclass
class MovieOptions:
    out_path: str
    size: tuple[int, int] = (1280, 720)
    fps: int = 30
    slide_seconds: float = 3.0
    transition: str = "dissolve"          # dissolve | panzoom | wipe | none
    transition_seconds: float = 0.8
    title: str = ""
    title_seconds: float = 3.0
    captions: bool = True
    full_frame: bool = False              # crop to fill instead of letterboxing
    audio_path: str = ""
    background: tuple = (0, 0, 0)
    extra: dict = field(default_factory=dict)


def _fit(im: Image.Image, size, full: bool, bg) -> Image.Image:
    if full:
        return ImageOps.fit(im, size, Image.LANCZOS)
    c = ImageOps.contain(im, size, Image.LANCZOS)
    out = Image.new("RGB", size, bg)
    out.paste(c, ((size[0] - c.width) // 2, (size[1] - c.height) // 2))
    return out


def _caption(frame: Image.Image, text: str) -> Image.Image:
    if not text:
        return frame
    d = ImageDraw.Draw(frame)
    f = _font(max(14, frame.height // 22))
    y = frame.height - frame.height // 12
    d.text((frame.width / 2 + 2, y + 2), text, font=f, fill=(0, 0, 0), anchor="mm")
    d.text((frame.width / 2, y), text, font=f, fill=(255, 255, 255), anchor="mm")
    return frame


def _title_frame(size, text, bg) -> Image.Image:
    im = Image.new("RGB", size, bg)
    d = ImageDraw.Draw(im)
    f = _font(max(18, size[1] // 12))
    d.multiline_text((size[0] / 2, size[1] / 2), text, font=f, fill=(255, 255, 255), anchor="mm", align="center")
    return im


def _panzoom_frames(im: Image.Image, size, n: int, k: int, full: bool, bg):
    """Ken Burns: slow zoom from 100% to 112% towards an alternating corner."""
    base = _fit(im, (int(size[0] * 1.15), int(size[1] * 1.15)), True if full else False, bg)
    bw, bh = base.size
    corners = [(0, 0), (1, 0), (0, 1), (1, 1)]
    tx, ty = corners[k % 4]
    for i in range(n):
        t = i / max(1, n - 1)
        z = 1.0 + 0.12 * t
        cw, ch = bw / z / 1.15 * 1.0, bh / z / 1.15
        cx = (bw - cw) * (0.5 + (tx - 0.5) * t * 0.8)
        cy = (bh - ch) * (0.5 + (ty - 0.5) * t * 0.8)
        yield base.resize(size, Image.BILINEAR, box=(cx, cy, cx + cw, cy + ch))


def _blend(a: Image.Image, b: Image.Image, t: float, kind: str) -> Image.Image:
    if kind == "wipe":
        w = int(a.width * t)
        out = a.copy()
        if w > 0:
            out.paste(b.crop((0, 0, w, b.height)), (0, 0))
        return out
    return Image.blend(a, b, t)


def _pick_codec(av):
    for name in ("h264_videotoolbox", "libx264", "h264", "mpeg4"):
        try:
            av.codec.Codec(name, "w")
            return name
        except Exception:
            continue
    return "mpeg4"


def make_movie(rows, opts: MovieOptions, progress=None, should_stop=None) -> str:
    import av
    size = (opts.size[0] // 2 * 2, opts.size[1] // 2 * 2)
    container = av.open(opts.out_path, mode="w")
    codec = _pick_codec(av)
    vs = container.add_stream(codec, rate=opts.fps)
    vs.width, vs.height = size
    vs.pix_fmt = "yuv420p"
    if codec in ("libx264", "h264"):
        vs.options = {"crf": "20", "preset": "medium"}
    elif codec == "h264_videotoolbox":
        vs.bit_rate = 8_000_000
    else:
        vs.bit_rate = 6_000_000
    astream = None
    if opts.audio_path:
        try:
            astream = container.add_stream("aac", rate=44100)
            astream.layout = "stereo"
        except Exception as e:
            print("no audio:", e)
            astream = None

    def emit(img: Image.Image):
        frame = av.VideoFrame.from_ndarray(np.asarray(img.convert("RGB")), format="rgb24")
        for pkt in vs.encode(frame):
            container.mux(pkt)

    slide_n = max(1, int(opts.slide_seconds * opts.fps))
    trans_n = 0 if opts.transition == "none" else max(1, int(opts.transition_seconds * opts.fps))
    total = len(rows)
    frames_written = 0
    prev_last = None
    try:
        if opts.title:
            tf = _title_frame(size, opts.title, opts.background)
            for _ in range(int(opts.title_seconds * opts.fps)):
                emit(tf)
                frames_written += 1
            prev_last = tf
        for k, r in enumerate(rows):
            if should_stop and should_stop():
                break
            if progress:
                progress(k, total)
            # frames are produced lazily: a 30-second 1080p clip would otherwise need gigabytes of memory
            if r["kind"] == "video":
                frames = _video_frames(r["path"], size, opts, max_seconds=opts.extra.get("clip_max", 30))
            else:
                try:
                    im = media.load_image(r["path"], max_dim=max(size) * 2)
                    im = render(im, parse_filters(r["filters"]), r["rotate"] or 0)
                except Exception:
                    continue
                cap = (r["caption"] or "") if opts.captions else ""
                if opts.transition == "panzoom":
                    frames = (_caption(f, cap) for f in _panzoom_frames(im, size, slide_n, k, opts.full_frame,
                                                                        opts.background))
                else:
                    still = _caption(_fit(im, size, opts.full_frame, opts.background), cap)
                    frames = (still for _ in range(slide_n))
            last = None
            i = 0
            for f in frames:
                # transition from the previous slide into this one over its first frames
                if prev_last is not None and i < trans_n:
                    t = (i + 1) / (trans_n + 1)
                    emit(_blend(prev_last, f, t, "wipe" if opts.transition == "wipe" else "dissolve"))
                else:
                    emit(f)
                frames_written += 1
                last = f
                i += 1
            if last is not None:
                prev_last = last
        for pkt in vs.encode():
            container.mux(pkt)
        duration = frames_written / opts.fps
        if astream is not None:
            try:
                _mux_audio(container, astream, opts.audio_path, duration)
            except Exception as e:  # keep the silent movie if audio fails
                print("audio failed:", e)
    finally:
        container.close()
    return opts.out_path


def _video_frames(path, size, opts, max_seconds=30):
    import av
    try:
        with av.open(path) as c:
            s = c.streams.video[0]
            fps_in = float(s.average_rate or 30)
            step = max(1, round(fps_in / opts.fps))
            n = 0
            for i, fr in enumerate(c.decode(s)):
                if i % step:
                    continue
                img = fr.to_image()
                yield _fit(img, size, opts.full_frame, opts.background)
                n += 1
                if n >= max_seconds * opts.fps:
                    break
    except Exception:
        return


def _mux_audio(container, astream, audio_path: str, duration: float):
    import av
    resampler = av.AudioResampler(format="fltp", layout="stereo", rate=44100)
    fade = 2.0
    with av.open(audio_path) as src:
        sstream = src.streams.audio[0]
        written = 0.0
        for frame in src.decode(sstream):
            for rf in resampler.resample(frame):
                t0 = written
                dur = rf.samples / 44100
                if t0 >= duration:
                    break
                arr = rf.to_ndarray()
                # fade out over the last seconds
                if t0 + dur > duration - fade:
                    tt = t0 + np.arange(arr.shape[-1]) / 44100
                    g = np.clip((duration - tt) / fade, 0, 1).astype(np.float32)
                    arr = (arr * g).astype(np.float32)
                    nf = av.AudioFrame.from_ndarray(arr, format="fltp", layout="stereo")
                    nf.sample_rate = 44100
                    rf = nf
                rf.pts = int(round(t0 * 44100))
                rf.time_base = __import__("fractions").Fraction(1, 44100)
                for pkt in astream.encode(rf):
                    container.mux(pkt)
                written += dur
            if written >= duration:
                break
    for pkt in astream.encode():
        container.mux(pkt)
