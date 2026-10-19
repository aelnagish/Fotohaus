"""Apple Vision framework (macOS): scene/object labels and text recognition, fully on-device."""
from __future__ import annotations

import sys

_ok = None

# Vision's taxonomy is broad; skip labels that are too generic to be useful in search.
_SKIP = {"structure", "material", "textile", "document", "people", "adult", "consumer_electronics"}


def available() -> bool:
    global _ok
    if _ok is None:
        if sys.platform != "darwin":
            _ok = False
        else:
            try:
                import Vision  # noqa: F401
                import Foundation  # noqa: F401
                _ok = True
            except Exception:
                _ok = False
    return _ok


def _text_request(Vision, accurate: bool):
    req = Vision.VNRecognizeTextRequest.alloc().init()
    req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate if accurate
                             else Vision.VNRequestTextRecognitionLevelFast)
    req.setUsesLanguageCorrection_(accurate)
    if accurate:
        try:
            req.setAutomaticallyDetectsLanguage_(True)
        except Exception:
            pass
    return req


def _text_from(req) -> str:
    lines = []
    for obs in req.results() or []:
        cands = obs.topCandidates_(1)
        if cands and len(cands):
            s = str(cands[0].string()).strip()
            if s and float(cands[0].confidence()) > 0.3:
                lines.append(s)
    return " ".join(lines)[:4000]


def analyze(path: str, want_labels: bool = True, want_text: bool = True,
            min_conf: float = 0.35) -> tuple[list[tuple[str, float]], str]:
    """Return ([(label, confidence)], recognized_text).

    Text is first found with the fast recognizer; only photos that contain text get the
    slower, more accurate pass - this keeps first-time analysis of large libraries quick.
    """
    import objc
    import Foundation
    import Vision

    labels: list[tuple[str, float]] = []
    text = ""
    with objc.autorelease_pool():
        url = Foundation.NSURL.fileURLWithPath_(path)
        handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(url, {})
        reqs = []
        cls_req = txt_req = None
        if want_labels:
            cls_req = Vision.VNClassifyImageRequest.alloc().init()
            reqs.append(cls_req)
        if want_text:
            txt_req = _text_request(Vision, accurate=False)
            reqs.append(txt_req)
        ok, err = handler.performRequests_error_(reqs, None)
        if not ok:
            return labels, text
        if cls_req is not None:
            for obs in cls_req.results() or []:
                conf = float(obs.confidence())
                if conf < min_conf:
                    continue
                ident = str(obs.identifier())
                if ident in _SKIP:
                    continue
                labels.append((ident.replace("_", " "), conf))
        if txt_req is not None:
            text = _text_from(txt_req)
            if len(text) >= 3:
                acc = _text_request(Vision, accurate=True)
                ok2, _ = handler.performRequests_error_([acc], None)
                if ok2:
                    text = _text_from(acc) or text
    labels.sort(key=lambda x: -x[1])
    return labels[:25], text
