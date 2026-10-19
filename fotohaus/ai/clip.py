"""Optional semantic search with a CLIP model (ONNX), downloaded on demand.

Model: OpenAI CLIP ViT-B/32 (MIT), ONNX conversion published as
`Xenova/clip-vit-base-patch32` on Hugging Face (quantized vision + text encoders).
"""
from __future__ import annotations

import os
import threading
import urllib.request

import numpy as np
from PIL import Image

from ..paths import USER_MODELS_DIR

MODEL_DIR = USER_MODELS_DIR / "clip-vit-b32"
BASE = "https://huggingface.co/Xenova/clip-vit-base-patch32/resolve/main/"
FILES = {
    "vision.onnx": BASE + "onnx/vision_model_quantized.onnx",
    "text.onnx": BASE + "onnx/text_model_quantized.onnx",
    "tokenizer.json": BASE + "tokenizer.json",
}
MEAN = np.array([0.48145466, 0.4578275, 0.40821073], dtype=np.float32)
STD = np.array([0.26862954, 0.26130258, 0.27577711], dtype=np.float32)

_lock = threading.Lock()
_state: dict = {}


def downloaded() -> bool:
    return all((MODEL_DIR / n).exists() for n in FILES)


def available() -> bool:
    if not downloaded():
        return False
    try:
        import onnxruntime  # noqa: F401
        import tokenizers  # noqa: F401
        return True
    except Exception:
        return False


def download(progress=None, should_stop=None) -> None:
    """Download model files (~155 MB). progress(fraction, message)."""
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    names = list(FILES)
    for i, name in enumerate(names):
        dest = MODEL_DIR / name
        if dest.exists():
            continue
        tmp = dest.with_suffix(dest.suffix + ".part")
        req = urllib.request.Request(FILES[name], headers={"User-Agent": "Fotohaus/1.0"})
        with urllib.request.urlopen(req, timeout=60) as resp, open(tmp, "wb") as fh:
            total = int(resp.headers.get("Content-Length") or 0)
            got = 0
            while True:
                if should_stop and should_stop():
                    raise RuntimeError("cancelled")
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                fh.write(chunk)
                got += len(chunk)
                if progress:
                    frac = (i + (got / total if total else 0)) / len(names)
                    progress(frac, f"Downloading {name} ({got // (1 << 20)} MB)")
        os.replace(tmp, dest)
    if progress:
        progress(1.0, "Model ready")


def _sessions():
    with _lock:
        if "vision" not in _state:
            import onnxruntime as ort
            from tokenizers import Tokenizer
            so = ort.SessionOptions()
            so.intra_op_num_threads = max(1, (os.cpu_count() or 4) // 2)
            _state["vision"] = ort.InferenceSession(str(MODEL_DIR / "vision.onnx"), so,
                                                    providers=["CPUExecutionProvider"])
            _state["text"] = ort.InferenceSession(str(MODEL_DIR / "text.onnx"), so,
                                                  providers=["CPUExecutionProvider"])
            _state["tok"] = Tokenizer.from_file(str(MODEL_DIR / "tokenizer.json"))
        return _state["vision"], _state["text"], _state["tok"]


def _pick_embedding(outputs, names) -> np.ndarray:
    for name, val in zip(names, outputs):
        if "embed" in name and val.ndim == 2:
            return val
    for val in outputs:
        if val.ndim == 2:
            return val
    return outputs[0].reshape(outputs[0].shape[0], -1)


def _preprocess(im: Image.Image) -> np.ndarray:
    im = im.convert("RGB")
    w, h = im.size
    s = 224 / min(w, h)
    im = im.resize((max(224, round(w * s)), max(224, round(h * s))), Image.BICUBIC)
    w, h = im.size
    l, t = (w - 224) // 2, (h - 224) // 2
    im = im.crop((l, t, l + 224, t + 224))
    a = (np.asarray(im, dtype=np.float32) / 255.0 - MEAN) / STD
    return a.transpose(2, 0, 1)


def embed_images(images: list[Image.Image]) -> np.ndarray:
    vis, _, _ = _sessions()
    batch = np.stack([_preprocess(im) for im in images]).astype(np.float32)
    inp = {i.name: batch for i in vis.get_inputs() if "pixel" in i.name or len(vis.get_inputs()) == 1}
    outs = vis.run(None, inp)
    e = _pick_embedding(outs, [o.name for o in vis.get_outputs()]).astype(np.float32)
    return e / (np.linalg.norm(e, axis=1, keepdims=True) + 1e-9)


def embed_text(text: str) -> np.ndarray:
    _, txt, tok = _sessions()
    enc = tok.encode(text.lower())
    ids = np.array([enc.ids[:77]], dtype=np.int64)
    feed = {}
    for i in txt.get_inputs():
        if "mask" in i.name:
            feed[i.name] = np.ones_like(ids)
        else:
            feed[i.name] = ids
    outs = txt.run(None, feed)
    e = _pick_embedding(outs, [o.name for o in txt.get_outputs()]).astype(np.float32)[0]
    return e / (np.linalg.norm(e) + 1e-9)


_matrix_cache: dict = {}


def _matrix(lib):
    n = lib.db.scalar("SELECT COUNT(*) FROM clip", default=0)
    if _matrix_cache.get("n") != n:
        rows = lib.db.q("SELECT photo_id, vec FROM clip")
        if not rows:
            return [], None
        ids = [r["photo_id"] for r in rows]
        M = np.stack([np.frombuffer(r["vec"], dtype=np.float32) for r in rows])
        _matrix_cache.update(n=n, ids=ids, M=M)
    return _matrix_cache.get("ids", []), _matrix_cache.get("M")


def search_text(lib, query: str, top_k: int = 200, min_score: float = 0.2) -> list[tuple[int, float]]:
    ids, M = _matrix(lib)
    if M is None:
        return []
    q = embed_text(f"a photo of {query}")
    sims = M @ q
    order = np.argsort(-sims)[:top_k]
    best = float(sims[order[0]]) if len(order) else 0
    cutoff = max(min_score, best - 0.08)
    return [(ids[i], float(sims[i])) for i in order if sims[i] >= cutoff]


def similar_to(lib, photo_id: int, top_k: int = 60) -> list[tuple[int, float]]:
    ids, M = _matrix(lib)
    if M is None or photo_id not in ids:
        return []
    v = M[ids.index(photo_id)]
    sims = M @ v
    order = np.argsort(-sims)[:top_k + 1]
    return [(ids[i], float(sims[i])) for i in order if ids[i] != photo_id]
