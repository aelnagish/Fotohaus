"""On-device face detection (YuNet) and recognition (SFace) with ONNX Runtime, plus clustering.

Models: OpenCV Zoo `face_detection_yunet_2023mar.onnx` (MIT; its input size was made flexible so
photos run at their own resolution) and `face_recognition_sface_2021dec.onnx` (Apache-2.0).
Pre- and post-processing follow OpenCV's FaceDetectorYN / FaceRecognizerSF exactly, so embeddings
stay compatible with libraries indexed by earlier versions.
"""
from __future__ import annotations

import threading

import numpy as np
from PIL import Image

from ..paths import BUNDLED_MODELS_DIR, USER_MODELS_DIR

try:
    import onnxruntime as ort  # type: ignore
except Exception:  # pragma: no cover
    ort = None

DET_MODEL = "face_detection_yunet_2023mar.onnx"
REC_MODEL = "face_recognition_sface_2021dec.onnx"
MATCH_THRESHOLD = 0.42     # cosine similarity to suggest a known person
CLUSTER_THRESHOLD = 0.45   # cosine similarity to group unknown faces
MIN_SCORE = 0.80
MIN_FACE_PX = 28
NMS_IOU = 0.3
TOP_K = 5000
STRIDES = (8, 16, 32)
# SFace reference landmarks (eyes, nose tip, mouth corners) in the 112x112 aligned crop
_REF = np.array([[38.2946, 51.6963], [73.5318, 51.5014], [56.0252, 71.7366],
                 [41.5493, 92.3655], [70.7299, 92.2041]], np.float64)


def _model(name: str):
    for d in (BUNDLED_MODELS_DIR, USER_MODELS_DIR):
        p = d / name
        if p.exists():
            return str(p)
    return None


def available() -> bool:
    return ort is not None and _model(DET_MODEL) is not None and _model(REC_MODEL) is not None


def _session(path: str):
    so = ort.SessionOptions()
    so.log_severity_level = 3
    so.intra_op_num_threads = 2
    so.inter_op_num_threads = 1
    return ort.InferenceSession(path, so, providers=["CPUExecutionProvider"])


def _nms(boxes: np.ndarray, scores: np.ndarray, iou: float, top_k: int) -> list[int]:
    """Greedy NMS on [x, y, w, h] boxes, highest score first (as cv2.dnn.NMSBoxes)."""
    order = np.argsort(-scores, kind="stable")[:top_k]
    x1, y1 = boxes[:, 0], boxes[:, 1]
    x2, y2 = x1 + boxes[:, 2], y1 + boxes[:, 3]
    area = boxes[:, 2] * boxes[:, 3]
    keep: list[int] = []
    while order.size:
        i = int(order[0])
        keep.append(i)
        rest = order[1:]
        w = np.maximum(0.0, np.minimum(x2[i], x2[rest]) - np.maximum(x1[i], x1[rest]))
        h = np.maximum(0.0, np.minimum(y2[i], y2[rest]) - np.maximum(y1[i], y1[rest]))
        inter = w * h
        ov = inter / np.maximum(area[i] + area[rest] - inter, 1e-9)
        order = rest[ov <= iou]
    return keep


def _similarity(src: np.ndarray) -> np.ndarray:
    """2x3 similarity transform mapping the 5 detected landmarks onto _REF (Umeyama)."""
    dst = _REF
    sm, dm = src.mean(0), dst.mean(0)
    s0, d0 = src - sm, dst - dm
    A = d0.T @ s0 / len(src)
    U, S, Vt = np.linalg.svd(A)
    D = np.ones(2)
    if np.linalg.det(A) < 0:
        D[1] = -1
    R = U @ np.diag(D) @ Vt
    var = (s0 ** 2).sum() / len(src)
    scale = (S * D).sum() / var if var > 0 else 1.0
    M = np.zeros((2, 3))
    M[:, :2] = R * scale
    M[:, 2] = dm - scale * (R @ sm)
    return M


def _warp112(img: np.ndarray, M: np.ndarray) -> np.ndarray:
    """cv2.warpAffine(img, M, (112, 112)) with bilinear sampling and black borders."""
    Minv = np.linalg.inv(np.vstack([M, [0, 0, 1]]))[:2]
    ys, xs = np.mgrid[0:112, 0:112].astype(np.float64)
    sx = Minv[0, 0] * xs + Minv[0, 1] * ys + Minv[0, 2]
    sy = Minv[1, 0] * xs + Minv[1, 1] * ys + Minv[1, 2]
    h, w = img.shape[:2]
    x0, y0 = np.floor(sx).astype(np.int64), np.floor(sy).astype(np.int64)
    fx, fy = (sx - x0)[..., None], (sy - y0)[..., None]
    src = img.astype(np.float64)
    out = np.zeros((112, 112, img.shape[2]))
    for dy, wy in ((0, 1 - fy), (1, fy)):
        for dx, wx in ((0, 1 - fx), (1, fx)):
            xx, yy = x0 + dx, y0 + dy
            ok = ((xx >= 0) & (xx < w) & (yy >= 0) & (yy < h))[..., None]
            v = src[np.clip(yy, 0, h - 1), np.clip(xx, 0, w - 1)]
            out += np.where(ok, v, 0.0) * wx * wy
    return np.clip(np.round(out), 0, 255).astype(np.uint8)


def _resize(rgb: np.ndarray, size) -> np.ndarray:
    return np.asarray(Image.fromarray(rgb).resize(size, Image.BILINEAR))


class FaceEngine:
    """Not thread-safe; use one engine per worker thread."""

    def __init__(self):
        if not available():
            raise RuntimeError("face models not available")
        self.det = _session(_model(DET_MODEL))
        self.rec = _session(_model(REC_MODEL))
        self._det_in = self.det.get_inputs()[0].name
        self._rec_in = self.rec.get_inputs()[0].name

    def _detect_raw(self, rgb: np.ndarray, min_score: float = MIN_SCORE) -> np.ndarray:
        """Faces as rows [x, y, w, h, 5 landmarks (x, y), score], like cv2.FaceDetectorYN.detect."""
        h, w = rgb.shape[:2]
        ph, pw = (h - 1) // 32 * 32 + 32, (w - 1) // 32 * 32 + 32
        blob = np.zeros((1, 3, ph, pw), np.float32)
        blob[0, :, :h, :w] = rgb[..., ::-1].transpose(2, 0, 1)    # BGR, unnormalized
        outs = self.det.run(None, {self._det_in: blob})
        rows = []
        n = len(STRIDES)
        for i, st in enumerate(STRIDES):
            cols, nrows = pw // st, ph // st
            cls = np.clip(outs[i].reshape(-1), 0, 1)
            obj = np.clip(outs[i + n].reshape(-1), 0, 1)
            score = np.sqrt(cls * obj)
            idx = np.nonzero(score >= min_score)[0]
            if not idx.size:
                continue
            bbox = outs[i + 2 * n].reshape(-1, 4)[idx]
            kps = outs[i + 3 * n].reshape(-1, 10)[idx]
            c = (idx % cols).astype(np.float32)
            r = (idx // cols).astype(np.float32)
            cx, cy = (c + bbox[:, 0]) * st, (r + bbox[:, 1]) * st
            bw, bh = np.exp(bbox[:, 2]) * st, np.exp(bbox[:, 3]) * st
            f = np.zeros((idx.size, 15), np.float32)
            f[:, 0], f[:, 1], f[:, 2], f[:, 3] = cx - bw / 2, cy - bh / 2, bw, bh
            f[:, 4:14:2] = (kps[:, 0::2] + c[:, None]) * st
            f[:, 5:14:2] = (kps[:, 1::2] + r[:, None]) * st
            f[:, 14] = score[idx]
            assert nrows * cols == outs[i].reshape(-1).size
            rows.append(f)
        if not rows:
            return np.zeros((0, 15), np.float32)
        allf = np.concatenate(rows)
        keep = _nms(allf[:, :4].astype(np.float64), allf[:, 14].astype(np.float64), NMS_IOU, TOP_K)
        return allf[keep]

    def detect(self, rgb: np.ndarray):
        """rgb uint8 HxWx3 -> list of dicts {rect(l,t,r,b) normalized, score, embedding}."""
        h, w = rgb.shape[:2]
        out = []
        for f in self._detect_raw(rgb):
            x, y, fw, fh, score = float(f[0]), float(f[1]), float(f[2]), float(f[3]), float(f[14])
            if fw < MIN_FACE_PX or fh < MIN_FACE_PX or score < MIN_SCORE:
                continue
            emb = self.embed_aligned(rgb, f)
            l, t = max(0.0, x / w), max(0.0, y / h)
            r, b = min(1.0, (x + fw) / w), min(1.0, (y + fh) / h)
            out.append({"rect": (l, t, r, b), "score": score, "embedding": emb})
        return out

    def _feature(self, rgb112: np.ndarray) -> np.ndarray:
        blob = rgb112.astype(np.float32).transpose(2, 0, 1)[None]     # RGB, unnormalized
        feat = self.rec.run(None, {self._rec_in: blob})[0].flatten().astype(np.float32)
        return feat / (np.linalg.norm(feat) + 1e-9)

    def embed_aligned(self, rgb: np.ndarray, face_row) -> np.ndarray:
        src = np.asarray(face_row[4:14], np.float64).reshape(5, 2)
        return self._feature(_warp112(rgb, _similarity(src)))

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
        if max(crop.shape[:2]) < 160:
            scale = 160 / max(crop.shape[:2])
            crop = _resize(crop, (max(1, round(crop.shape[1] * scale)), max(1, round(crop.shape[0] * scale))))
        faces = self._detect_raw(crop)
        if len(faces):
            best = max(faces, key=lambda f: f[14])
            return self.embed_aligned(crop, best)
        return self._feature(_resize(crop, (112, 112)))


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
    h, w = rgb.shape[:2]
    out = []
    for f in engine()._detect_raw(rgb, min_score=0.7):
        fw = float(f[2])
        r = max(4.0, fw * 0.11)
        for ex, ey in ((f[4], f[5]), (f[6], f[7])):
            out.append((max(0.0, (ex - r) / w), max(0.0, (ey - r) / h), min(1.0, (ex + r) / w), min(1.0, (ey + r) / h)))
    return out
