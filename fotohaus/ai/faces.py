"""On-device face detection (YuNet) and recognition (SFace) via OpenCV, plus clustering.

Models: OpenCV Zoo `face_detection_yunet_2023mar.onnx` and `face_recognition_sface_2021dec.onnx`
(Apache-2.0 / MIT, see LICENSES in the app bundle).
"""
from __future__ import annotations

import threading

import numpy as np

from ..paths import BUNDLED_MODELS_DIR, USER_MODELS_DIR

try:
    import cv2  # type: ignore
except Exception:  # pragma: no cover
    cv2 = None

DET_MODEL = "face_detection_yunet_2023mar.onnx"
REC_MODEL = "face_recognition_sface_2021dec.onnx"
MATCH_THRESHOLD = 0.42     # cosine similarity to suggest a known person
CLUSTER_THRESHOLD = 0.45   # cosine similarity to group unknown faces
MIN_SCORE = 0.80
MIN_FACE_PX = 28


def _model(name: str):
    for d in (BUNDLED_MODELS_DIR, USER_MODELS_DIR):
        p = d / name
        if p.exists():
            return str(p)
    return None


def available() -> bool:
    return cv2 is not None and _model(DET_MODEL) is not None and _model(REC_MODEL) is not None


class FaceEngine:
    """Not thread-safe; use one engine per worker thread."""

    def __init__(self):
        if not available():
            raise RuntimeError("face models not available")
        self.det = cv2.FaceDetectorYN.create(_model(DET_MODEL), "", (320, 320), MIN_SCORE, 0.3, 5000)
        self.rec = cv2.FaceRecognizerSF.create(_model(REC_MODEL), "")

    def detect(self, rgb: np.ndarray):
        """rgb uint8 HxWx3 -> list of dicts {rect(l,t,r,b) normalized, score, embedding}."""
        h, w = rgb.shape[:2]
        bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        self.det.setInputSize((w, h))
        _, faces = self.det.detect(bgr)
        out = []
        if faces is None:
            return out
        for f in faces:
            x, y, fw, fh, score = float(f[0]), float(f[1]), float(f[2]), float(f[3]), float(f[14])
            if fw < MIN_FACE_PX or fh < MIN_FACE_PX or score < MIN_SCORE:
                continue
            emb = self.embed_aligned(bgr, f)
            l, t = max(0.0, x / w), max(0.0, y / h)
            r, b = min(1.0, (x + fw) / w), min(1.0, (y + fh) / h)
            out.append({"rect": (l, t, r, b), "score": score, "embedding": emb})
        return out

    def embed_aligned(self, bgr: np.ndarray, face_row) -> np.ndarray:
        crop = self.rec.alignCrop(bgr, face_row)
        feat = self.rec.feature(crop).flatten().astype(np.float32)
        return feat / (np.linalg.norm(feat) + 1e-9)

    def embed_rect(self, rgb: np.ndarray, rect) -> np.ndarray | None:
        """Embedding for a known face rectangle (e.g. imported from Picasa)."""
        h, w = rgb.shape[:2]
        l, t, r, b = rect
        cx, cy = (l + r) / 2 * w, (t + b) / 2 * h
        size = max((r - l) * w, (b - t) * h) * 1.8
        x0, y0 = int(max(0, cx - size / 2)), int(max(0, cy - size / 2))
        x1, y1 = int(min(w, cx + size / 2)), int(min(h, cy + size / 2))
        if x1 - x0 < 16 or y1 - y0 < 16:
            return None
        crop = np.ascontiguousarray(rgb[y0:y1, x0:x1])
        scale = 1.0
        if max(crop.shape[:2]) < 160:
            scale = 160 / max(crop.shape[:2])
            crop = cv2.resize(crop, None, fx=scale, fy=scale)
        bgr = cv2.cvtColor(crop, cv2.COLOR_RGB2BGR)
        self.det.setInputSize((bgr.shape[1], bgr.shape[0]))
        _, faces = self.det.detect(bgr)
        if faces is not None and len(faces):
            best = max(faces, key=lambda f: f[14])
            return self.embed_aligned(bgr, best)
        face = cv2.resize(bgr, (112, 112))
        feat = self.rec.feature(face).flatten().astype(np.float32)
        return feat / (np.linalg.norm(feat) + 1e-9)


_engines = threading.local()


def engine() -> FaceEngine:
    e = getattr(_engines, "e", None)
    if e is None:
        e = FaceEngine()
        _engines.e = e
    return e


def to_blob(v: np.ndarray) -> bytes:
    return np.asarray(v, dtype=np.float32).tobytes()


def from_blob(b: bytes) -> np.ndarray:
    return np.frombuffer(b, dtype=np.float32)


# ------------------------------------------------------------------ recognition / clustering

def update_suggestions_and_clusters(lib) -> dict:
    """Suggest names for unconfirmed faces and cluster the rest. Returns stats."""
    db = lib.db
    rows = db.q("SELECT id, person_id, confirmed, embedding FROM faces WHERE embedding IS NOT NULL AND ignored=0")
    if not rows:
        return {"suggested": 0, "clusters": 0}
    known: dict[int, list[np.ndarray]] = {}
    unknown_ids, unknown_vecs = [], []
    for r in rows:
        v = from_blob(r["embedding"])
        if r["person_id"] and r["confirmed"]:
            known.setdefault(r["person_id"], []).append(v)
        elif not r["confirmed"]:
            unknown_ids.append(r["id"])
            unknown_vecs.append(v)
    stats = {"suggested": 0, "clusters": 0}
    if not unknown_ids:
        return stats
    U = np.stack(unknown_vecs)
    best_person = np.full(len(unknown_ids), -1, dtype=np.int64)
    best_sim = np.zeros(len(unknown_ids), dtype=np.float32)
    if known:
        pids = list(known)
        # compare against every confirmed face (max) - robust for people photographed across years
        for pid in pids:
            K = np.stack(known[pid])
            sims = (U @ K.T).max(1)
            better = sims > best_sim
            best_sim[better] = sims[better]
            best_person[better] = pid
    updates = []
    remaining = []
    for i, fid in enumerate(unknown_ids):
        if best_person[i] >= 0 and best_sim[i] >= MATCH_THRESHOLD:
            updates.append((int(best_person[i]), float(best_sim[i]), fid))
        else:
            remaining.append(i)
    with db.write_lock:
        c = db.conn
        # previously rejected suggestions keep person NULL; only update faces still unconfirmed
        c.executemany("UPDATE faces SET person_id=?, suggestion=?, cluster=NULL WHERE id=? AND confirmed=0", updates)
        c.commit()
    stats["suggested"] = len(updates)
    # greedy clustering of the remaining unknown faces
    centroids: list[np.ndarray] = []
    counts: list[int] = []
    assign = []
    for i in remaining:
        v = U[i]
        if centroids:
            C = np.stack(centroids)
            sims = C @ v
            j = int(np.argmax(sims))
            if sims[j] >= CLUSTER_THRESHOLD:
                n = counts[j]
                c_new = centroids[j] * n + v
                centroids[j] = c_new / (np.linalg.norm(c_new) + 1e-9)
                counts[j] += 1
                assign.append((j, unknown_ids[i]))
                continue
        centroids.append(v.copy())
        counts.append(1)
        assign.append((len(centroids) - 1, unknown_ids[i]))
    with db.write_lock:
        c = db.conn
        c.executemany("UPDATE faces SET cluster=?, person_id=NULL, suggestion=NULL WHERE id=?",
                      [(cl, fid) for cl, fid in assign])
        c.commit()
    stats["clusters"] = sum(1 for n in counts if n > 1)
    return stats


def eye_rects(rgb: np.ndarray) -> list[tuple[float, float, float, float]]:
    """Rectangles (relative) around each detected eye — used for automatic red-eye removal."""
    if not available():
        return []
    eng = engine()
    h, w = rgb.shape[:2]
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    eng.det.setInputSize((w, h))
    _, faces = eng.det.detect(bgr)
    out = []
    if faces is None:
        return out
    for f in faces:
        if float(f[14]) < 0.7:
            continue
        fw = float(f[2])
        r = max(4.0, fw * 0.11)
        for ex, ey in ((f[4], f[5]), (f[6], f[7])):
            out.append((max(0.0, (ex - r) / w), max(0.0, (ey - r) / h), min(1.0, (ex + r) / w), min(1.0, (ey + r) / h)))
    return out
