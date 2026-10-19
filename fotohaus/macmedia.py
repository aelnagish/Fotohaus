"""Photo and video I/O through macOS's own frameworks (ImageIO and AVFoundation).

Used for HEIC/HEIF photos, video thumbnails and frames, video size and length, and for writing
Movie Maker films with H.264 video and AAC music. Nothing here is available off macOS;
`available()` tells callers whether to use it.
"""
from __future__ import annotations

import io
import os
import sys
import threading
import time

from PIL import Image

_ok = None


def available() -> bool:
    global _ok
    if _ok is None:
        _ok = False
        if sys.platform == "darwin":
            try:
                import Quartz  # noqa: F401
                _ok = True
            except Exception:
                _ok = False
    return _ok


def _av():
    import AVFoundation  # type: ignore
    import CoreMedia  # type: ignore
    return AVFoundation, CoreMedia


def _url(path: str):
    from Foundation import NSURL  # type: ignore
    return NSURL.fileURLWithPath_(os.path.abspath(path))


def _pool():
    import objc  # type: ignore
    return objc.autorelease_pool()


# ------------------------------------------------------------------ CGImage <-> PIL

def cgimage_to_pil(cg) -> Image.Image:
    """Render a CGImage into an sRGB RGB PIL image (colour-managed by Quartz)."""
    import Quartz  # type: ignore
    w, h = int(Quartz.CGImageGetWidth(cg)), int(Quartz.CGImageGetHeight(cg))
    cs = Quartz.CGColorSpaceCreateWithName(Quartz.kCGColorSpaceSRGB)
    ctx = Quartz.CGBitmapContextCreate(None, w, h, 8, w * 4, cs, Quartz.kCGImageAlphaNoneSkipLast)
    Quartz.CGContextDrawImage(ctx, Quartz.CGRectMake(0, 0, w, h), cg)
    out = Quartz.CGBitmapContextCreateImage(ctx)
    data = bytes(Quartz.CGDataProviderCopyData(Quartz.CGImageGetDataProvider(out)))
    bpr = int(Quartz.CGImageGetBytesPerRow(out))
    return Image.frombytes("RGB", (w, h), data, "raw", "RGBX", bpr, 1)


def pil_to_cgimage(im: Image.Image):
    import Quartz  # type: ignore
    from Foundation import NSData  # type: ignore
    im = im.convert("RGB")
    w, h = im.size
    raw = im.tobytes("raw", "RGBX")
    prov = Quartz.CGDataProviderCreateWithCFData(NSData.dataWithBytes_length_(raw, len(raw)))
    cs = Quartz.CGColorSpaceCreateWithName(Quartz.kCGColorSpaceSRGB)
    return Quartz.CGImageCreate(w, h, 8, 32, w * 4, cs, Quartz.kCGImageAlphaNoneSkipLast, prov, None, False,
                                Quartz.kCGRenderingIntentDefault)


# ------------------------------------------------------------------ still images (HEIC / HEIF)

def _source(data_or_path):
    import Quartz  # type: ignore
    from Foundation import NSData  # type: ignore
    if isinstance(data_or_path, (bytes, bytearray)):
        return Quartz.CGImageSourceCreateWithData(NSData.dataWithBytes_length_(bytes(data_or_path),
                                                                               len(data_or_path)), None)
    return Quartz.CGImageSourceCreateWithURL(_url(data_or_path), None)


def image_info(data_or_path) -> tuple[int, int, int] | None:
    """(width, height, orientation) as stored, before rotation; None if unreadable."""
    import Quartz  # type: ignore
    with _pool():
        src = _source(data_or_path)
        if src is None or Quartz.CGImageSourceGetCount(src) < 1:
            return None
        p = Quartz.CGImageSourceCopyPropertiesAtIndex(src, 0, None) or {}
        w = int(p.get(Quartz.kCGImagePropertyPixelWidth) or 0)
        h = int(p.get(Quartz.kCGImagePropertyPixelHeight) or 0)
        o = int(p.get(Quartz.kCGImagePropertyOrientation) or 1)
        return (w, h, o) if w and h else None


def load_image(data_or_path, max_dim: int | None = None) -> Image.Image:
    """Decode any ImageIO-readable photo, upright and in sRGB, optionally downscaled."""
    import Quartz  # type: ignore
    with _pool():
        src = _source(data_or_path)
        if src is None:
            raise OSError("ImageIO could not open the image")
        p = Quartz.CGImageSourceCopyPropertiesAtIndex(src, 0, None) or {}
        full = max(int(p.get(Quartz.kCGImagePropertyPixelWidth) or 0),
                   int(p.get(Quartz.kCGImagePropertyPixelHeight) or 0)) or 100000
        size = min(full, int(max_dim)) if max_dim else full
        opts = {Quartz.kCGImageSourceCreateThumbnailFromImageAlways: True,
                Quartz.kCGImageSourceCreateThumbnailWithTransform: True,
                Quartz.kCGImageSourceThumbnailMaxPixelSize: size}
        cg = Quartz.CGImageSourceCreateThumbnailAtIndex(src, 0, opts)
        if cg is None:
            raise OSError("ImageIO could not decode the image")
        return cgimage_to_pil(cg)


def _metadata_from_exif(exif_bytes: bytes | None, strip_location: bool = False) -> dict:
    """Turn raw EXIF bytes into ImageIO metadata dictionaries (by letting ImageIO parse a tiny JPEG)."""
    import Quartz  # type: ignore
    if not exif_bytes:
        return {}
    buf = io.BytesIO()
    Image.new("RGB", (8, 8)).save(buf, "JPEG", exif=exif_bytes)
    src = _source(buf.getvalue())
    p = Quartz.CGImageSourceCopyPropertiesAtIndex(src, 0, None) if src is not None else None
    if not p:
        return {}
    out = {}
    for key in (Quartz.kCGImagePropertyExifDictionary, Quartz.kCGImagePropertyTIFFDictionary,
                Quartz.kCGImagePropertyGPSDictionary):
        d = p.get(key)
        if d:
            out[key] = dict(d)
    ex = out.get(Quartz.kCGImagePropertyExifDictionary)
    if ex:
        ex.pop(Quartz.kCGImagePropertyExifPixelXDimension, None)
        ex.pop(Quartz.kCGImagePropertyExifPixelYDimension, None)
    tiff = out.get(Quartz.kCGImagePropertyTIFFDictionary)
    if tiff is not None:
        tiff[Quartz.kCGImagePropertyTIFFOrientation] = 1
    if strip_location:
        out.pop(Quartz.kCGImagePropertyGPSDictionary, None)
    out[Quartz.kCGImagePropertyOrientation] = 1
    return out


def encode_heic(im: Image.Image, quality: int = 90, exif: bytes | None = None) -> bytes:
    import Quartz  # type: ignore
    from Foundation import NSMutableData  # type: ignore
    with _pool():
        data = NSMutableData.data()
        dest = Quartz.CGImageDestinationCreateWithData(data, "public.heic", 1, None)
        if dest is None:
            raise OSError("this Mac cannot write HEIC")
        props = _metadata_from_exif(exif)
        props[Quartz.kCGImageDestinationLossyCompressionQuality] = max(0.0, min(1.0, quality / 100.0))
        Quartz.CGImageDestinationAddImage(dest, pil_to_cgimage(im), props)
        if not Quartz.CGImageDestinationFinalize(dest):
            raise OSError("HEIC encoding failed")
        return bytes(data)


# ------------------------------------------------------------------ video

def _asset(path: str):
    AVF, _ = _av()
    return AVF.AVURLAsset.URLAssetWithURL_options_(_url(path), None)


def video_info(path: str) -> tuple[int, int, float]:
    """(width, height, seconds) as displayed (rotation applied)."""
    AVF, CM = _av()
    with _pool():
        a = _asset(path)
        dur = float(CM.CMTimeGetSeconds(a.duration()) or 0.0)
        if dur != dur:          # NaN for unreadable files
            dur = 0.0
        tracks = a.tracksWithMediaType_(AVF.AVMediaTypeVideo)
        if not tracks:
            return 0, 0, dur
        t = tracks[0]
        size = t.naturalSize()
        w, h = int(round(abs(size.width))), int(round(abs(size.height)))
        tr = t.preferredTransform()
        if abs(tr.b) > 0.5 and abs(tr.c) > 0.5:     # rotated by ±90°
            w, h = h, w
        return w, h, dur


def video_frame(path: str, max_dim: int | None = None, at: float | None = None) -> Image.Image | None:
    """One upright frame: at `at` seconds, or about 10% into the clip."""
    AVF, CM = _av()
    with _pool():
        a = _asset(path)
        dur = float(CM.CMTimeGetSeconds(a.duration()) or 0.0)
        if dur != dur:
            dur = 0.0
        gen = AVF.AVAssetImageGenerator.assetImageGeneratorWithAsset_(a)
        gen.setAppliesPreferredTrackTransform_(True)
        if max_dim:
            gen.setMaximumSize_((max_dim, max_dim))
        if at is not None:
            gen.setRequestedTimeToleranceBefore_(CM.kCMTimeZero)
            gen.setRequestedTimeToleranceAfter_(CM.kCMTimeZero)
        times = [at] if at is not None else ([dur * 0.1] if dur > 1 else []) + [0.0]
        for t in times:
            cg, _actual, _err = gen.copyCGImageAtTime_actualTime_error_(CM.CMTimeMakeWithSeconds(t, 600), None, None)
            if cg is not None:
                return cgimage_to_pil(cg)
        return None


def video_frames(path: str, fps: float, max_seconds: float, max_dim: int | None = None):
    """Yield upright PIL frames sampled at `fps` for up to `max_seconds`."""
    AVF, CM = _av()
    a = _asset(path)
    dur = float(CM.CMTimeGetSeconds(a.duration()) or 0.0)
    if not dur or dur != dur:
        return
    gen = AVF.AVAssetImageGenerator.assetImageGeneratorWithAsset_(a)
    gen.setAppliesPreferredTrackTransform_(True)
    if max_dim:
        gen.setMaximumSize_((max_dim, max_dim))
    tol = CM.CMTimeMakeWithSeconds(0.5 / max(1.0, fps), 600)
    gen.setRequestedTimeToleranceBefore_(tol)
    gen.setRequestedTimeToleranceAfter_(tol)
    n = int(min(dur, max_seconds) * fps)
    for i in range(n):
        with _pool():
            cg, _actual, _err = gen.copyCGImageAtTime_actualTime_error_(CM.CMTimeMakeWithSeconds(i / fps, 600),
                                                                         None, None)
            if cg is None:
                continue
            frame = cgimage_to_pil(cg)
        yield frame


class MovieWriter:
    """H.264 MP4 writer (AVAssetWriter). Feed RGB PIL frames with `add`, then `close`."""

    def __init__(self, path: str, size: tuple[int, int], fps: int, bitrate: int = 8_000_000):
        import Quartz  # type: ignore
        AVF, CM = _av()
        self.AVF, self.CM, self.Q = AVF, CM, Quartz
        self.path, self.size, self.fps, self.n = path, size, fps, 0
        if os.path.exists(path):
            os.remove(path)
        w, h = size
        self.writer, err = AVF.AVAssetWriter.assetWriterWithURL_fileType_error_(_url(path), AVF.AVFileTypeMPEG4, None)
        if self.writer is None:
            raise OSError(f"cannot create movie: {err}")
        settings = {AVF.AVVideoCodecKey: AVF.AVVideoCodecTypeH264, AVF.AVVideoWidthKey: w, AVF.AVVideoHeightKey: h,
                    AVF.AVVideoCompressionPropertiesKey: {AVF.AVVideoAverageBitRateKey: bitrate}}
        self.input = AVF.AVAssetWriterInput.assetWriterInputWithMediaType_outputSettings_(AVF.AVMediaTypeVideo,
                                                                                         settings)
        self.input.setExpectsMediaDataInRealTime_(False)
        attrs = {Quartz.kCVPixelBufferPixelFormatTypeKey: Quartz.kCVPixelFormatType_32BGRA,
                 Quartz.kCVPixelBufferWidthKey: w, Quartz.kCVPixelBufferHeightKey: h}
        self.adaptor = AVF.AVAssetWriterInputPixelBufferAdaptor.\
            assetWriterInputPixelBufferAdaptorWithAssetWriterInput_sourcePixelBufferAttributes_(self.input, attrs)
        self.writer.addInput_(self.input)
        if not self.writer.startWriting():
            raise OSError(f"cannot start movie: {self.writer.error()}")
        self.writer.startSessionAtSourceTime_(CM.kCMTimeZero)

    def _buffer(self):
        Q = self.Q
        pool = self.adaptor.pixelBufferPool()
        pb = None
        if pool is not None:
            _rv, pb = Q.CVPixelBufferPoolCreatePixelBuffer(None, pool, None)
        if pb is None:
            _rv, pb = Q.CVPixelBufferCreate(None, self.size[0], self.size[1], Q.kCVPixelFormatType_32BGRA,
                                            None, None)
        return pb

    def add(self, im: Image.Image):
        import numpy as np
        Q = self.Q
        w, h = self.size
        with _pool():
            t0 = time.time()
            while not self.input.isReadyForMoreMediaData():
                if self.writer.status() != self.AVF.AVAssetWriterStatusWriting:
                    raise OSError(f"movie writer stopped: {self.writer.error()}")
                if time.time() - t0 > 30:
                    raise OSError("movie writer is not accepting frames")
                time.sleep(0.003)
            pb = self._buffer()
            Q.CVPixelBufferLockBaseAddress(pb, 0)
            try:
                bpr = int(Q.CVPixelBufferGetBytesPerRow(pb))
                mv = Q.CVPixelBufferGetBaseAddress(pb).as_buffer(bpr * h)
                rgb = im.convert("RGB")
                if rgb.size != (w, h):
                    rgb = rgb.resize((w, h))
                bgra = np.frombuffer(rgb.convert("RGBA").tobytes("raw", "BGRA"), np.uint8).reshape(h, w * 4)
                dst = np.frombuffer(mv, np.uint8).reshape(h, bpr)
                dst[:, :w * 4] = bgra
            finally:
                Q.CVPixelBufferUnlockBaseAddress(pb, 0)
            if not self.adaptor.appendPixelBuffer_withPresentationTime_(pb, self.CM.CMTimeMake(self.n, self.fps)):
                raise OSError(f"could not add frame: {self.writer.error()}")
            self.n += 1

    def close(self):
        self.input.markAsFinished()
        done = threading.Event()
        self.writer.finishWritingWithCompletionHandler_(lambda: done.set())
        done.wait(300)
        if self.writer.status() != self.AVF.AVAssetWriterStatusCompleted:
            raise OSError(f"movie could not be finished: {self.writer.error()}")


def add_music(video_path: str, audio_path: str, out_path: str, fade_seconds: float = 2.0) -> bool:
    """Mux a music track under a silent movie (trimmed to the movie, faded out at the end)."""
    AVF, CM = _av()
    with _pool():
        v = _asset(video_path)
        au = _asset(audio_path)
        vt = v.tracksWithMediaType_(AVF.AVMediaTypeVideo)
        at = au.tracksWithMediaType_(AVF.AVMediaTypeAudio)
        if not vt or not at:
            return False
        comp = AVF.AVMutableComposition.composition()
        vdur = v.duration()
        cv = comp.addMutableTrackWithMediaType_preferredTrackID_(AVF.AVMediaTypeVideo, 0)
        ok, _ = cv.insertTimeRange_ofTrack_atTime_error_(CM.CMTimeRangeMake(CM.kCMTimeZero, vdur), vt[0],
                                                         CM.kCMTimeZero, None)
        if not ok:
            return False
        adur = CM.CMTimeMinimum(vdur, au.duration())
        ca = comp.addMutableTrackWithMediaType_preferredTrackID_(AVF.AVMediaTypeAudio, 0)
        ok, _ = ca.insertTimeRange_ofTrack_atTime_error_(CM.CMTimeRangeMake(CM.kCMTimeZero, adur), at[0],
                                                         CM.kCMTimeZero, None)
        if not ok:
            return False
        secs = float(CM.CMTimeGetSeconds(adur))
        params = AVF.AVMutableAudioMixInputParameters.audioMixInputParametersWithTrack_(ca)
        fs = max(0.0, secs - fade_seconds)
        params.setVolumeRampFromStartVolume_toEndVolume_timeRange_(
            1.0, 0.0, CM.CMTimeRangeMake(CM.CMTimeMakeWithSeconds(fs, 600), CM.CMTimeMakeWithSeconds(secs - fs, 600)))
        mix = AVF.AVMutableAudioMix.audioMix()
        mix.setInputParameters_([params])
        if os.path.exists(out_path):
            os.remove(out_path)
        exp = AVF.AVAssetExportSession.exportSessionWithAsset_presetName_(comp, AVF.AVAssetExportPresetHighestQuality)
        if exp is None:
            return False
        exp.setOutputURL_(_url(out_path))
        exp.setOutputFileType_(AVF.AVFileTypeMPEG4)
        exp.setAudioMix_(mix)
        done = threading.Event()
        exp.exportAsynchronouslyWithCompletionHandler_(lambda: done.set())
        done.wait(1800)
        return exp.status() == AVF.AVAssetExportSessionStatusCompleted
