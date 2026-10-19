"""Movie Maker: turn photos (and video clips) into an MP4 slideshow movie, like Picasa's Movie Maker.

Transitions: dissolve, pan & zoom (Ken Burns), wipe, none. Optional title slide, captions and
a music track (trimmed / faded to the movie length). Encoded as H.264 MP4 with macOS's own
AVFoundation (hardware encoder); off macOS, PyAV is used if it happens to be installed.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from PIL import Image, ImageDraw, ImageOps

import os

from . import macmedia, media
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


def _pick_codec(av):  # pragma: no cover - development fallback off macOS
    for name in ("h264_videotoolbox", "libx264", "h264", "mpeg4"):
        try:
            av.codec.Codec(name, "w")
            return name
        except Exception:
            continue
    return "mpeg4"


class _PyAVWriter:  # pragma: no cover - development fallback off macOS
    def __init__(self, path, size, fps):
        import av
        self.av, self.fps = av, fps
        self.container = av.open(path, mode="w")
        codec = _pick_codec(av)
        self.vs = self.container.add_stream(codec, rate=fps)
        self.vs.width, self.vs.height = size
        self.vs.pix_fmt = "yuv420p"
        self.vs.bit_rate = 6_000_000

    def add(self, img):
        frame = self.av.VideoFrame.from_ndarray(np.asarray(img.convert("RGB")), format="rgb24")
        for pkt in self.vs.encode(frame):
            self.container.mux(pkt)

    def close(self):
        for pkt in self.vs.encode():
            self.container.mux(pkt)
        self.container.close()


def _writer(path, size, fps):
    if macmedia.available():
        return macmedia.MovieWriter(path, size, fps)
    return _PyAVWriter(path, size, fps)


def make_movie(rows, opts: MovieOptions, progress=None, should_stop=None) -> str:
    size = (opts.size[0] // 2 * 2, opts.size[1] // 2 * 2)
    want_music = bool(opts.audio_path) and macmedia.available()
    video_path = opts.out_path + ".silent.mp4" if want_music else opts.out_path
    writer = _writer(video_path, size, opts.fps)

    def emit(img: Image.Image):
        writer.add(img)

    slide_n = max(1, int(opts.slide_seconds * opts.fps))
    trans_n = 0 if opts.transition in ("none", "panzoom") else max(1, int(opts.transition_seconds * opts.fps))
    if opts.transition == "panzoom":
        trans_n = max(1, int(opts.transition_seconds * opts.fps))
    total = len(rows)
    frames_written = 0
    prev_last = None
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
        if r["kind"] == "video":
            clip_frames = list(_video_frames(r["path"], size, opts, max_seconds=opts.extra.get("clip_max", 30)))
            if not clip_frames:
                continue
            frames = clip_frames
        else:
            try:
                im = media.load_image(r["path"], max_dim=max(size) * 2)
                im = render(im, parse_filters(r["filters"]), r["rotate"] or 0)
            except Exception:
                continue
            cap = (r["caption"] or "") if opts.captions else ""
            if opts.transition == "panzoom":
                frames = [_caption(f, cap) for f in _panzoom_frames(im, size, slide_n, k, opts.full_frame,
                                                                     opts.background)]
            else:
                still = _caption(_fit(im, size, opts.full_frame, opts.background), cap)
                frames = [still] * slide_n
        # transition from previous slide into this one
        if prev_last is not None and trans_n and opts.transition != "none":
            n = min(trans_n, len(frames))
            for i in range(n):
                t = (i + 1) / (n + 1)
                emit(_blend(prev_last, frames[i], t, "wipe" if opts.transition == "wipe" else "dissolve"))
                frames_written += 1
            frames = frames[n:]
        for f in frames:
            emit(f)
            frames_written += 1
        prev_last = frames[-1] if frames else prev_last
    writer.close()
    if want_music:
        try:
            ok = macmedia.add_music(video_path, opts.audio_path, opts.out_path)
        except Exception as e:  # keep the silent movie if the music can't be added
            print("music failed:", e)
            ok = False
        if ok:
            os.remove(video_path)
        else:
            os.replace(video_path, opts.out_path)
    return opts.out_path


def _video_frames(path, size, opts, max_seconds=30):
    try:
        if macmedia.available():
            for img in macmedia.video_frames(path, opts.fps, max_seconds, max_dim=max(size)):
                yield _fit(img, size, opts.full_frame, opts.background)
            return
        import av  # pragma: no cover - development fallback off macOS
        with av.open(path) as c:
            s = c.streams.video[0]
            step = max(1, round(float(s.average_rate or 30) / opts.fps))
            n = 0
            for i, fr in enumerate(c.decode(s)):
                if i % step:
                    continue
                yield _fit(fr.to_image(), size, opts.full_frame, opts.background)
                n += 1
                if n >= max_seconds * opts.fps:
                    break
    except Exception:
        return
