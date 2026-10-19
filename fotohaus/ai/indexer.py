"""Background analysis: thumbnails, perceptual hashes, faces, labels/text, CLIP embeddings."""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

import numpy as np
from PIL import Image

from .. import media, thumbs
from . import clip, dupes, faces, vision_mac


class Indexer:
    def __init__(self, lib):
        self.lib = lib

    def _flags(self):
        s = self.lib.settings
        return {
            "faces": bool(s.get("faces_enabled")) and faces.available(),
            "vision": bool(s.get("vision_enabled")) and vision_mac.available(),
            "clip": bool(s.get("clip_enabled")) and clip.available(),
        }

    def pending(self) -> int:
        f = self._flags()
        conds = ["phash IS NULL"]
        if f["faces"]:
            conds.append("(faces_done=0 AND kind!='video')")
        if f["vision"]:
            conds.append("(labels_done=0 AND kind!='video')")
        if f["clip"]:
            conds.append("clip_done=0")
        return self.lib.db.scalar(f"SELECT COUNT(*) FROM photos WHERE {' OR '.join(conds)}", default=0)

    def run(self, progress: Callable[[int, int, str], None] | None = None,
            should_stop: Callable[[], bool] | None = None, workers: int | None = None) -> dict:
        lib = self.lib
        flags = self._flags()
        total = self.pending()
        done = 0
        stats = {"processed": 0, "faces": 0, "labels": 0}
        if total == 0:
            self._embed_imported_faces(flags, should_stop)
            return stats
        workers = workers or max(2, min(4, (os.cpu_count() or 4) // 2))
        conds = ["phash IS NULL"]
        if flags["faces"]:
            conds.append("(faces_done=0 AND kind!='video')")
        if flags["vision"]:
            conds.append("(labels_done=0 AND kind!='video')")
        if flags["clip"]:
            conds.append("clip_done=0")
        where = " OR ".join(conds)
        last_id = 0
        with ThreadPoolExecutor(max_workers=workers) as pool:
            while True:
                if should_stop and should_stop():
                    break
                rows = lib.db.q(f"SELECT * FROM photos WHERE id > ? AND ({where}) ORDER BY id LIMIT 64", (last_id,))
                if not rows:
                    break
                last_id = rows[-1]["id"]
                results = list(pool.map(lambda r: self._process(r, flags), rows))
                self._store(results, stats)
                done += len(rows)
                stats["processed"] = done
                if progress:
                    progress(done, total, "Analyzing photos")
                if flags["faces"] and done % 512 < 64:
                    faces.update_suggestions_and_clusters(lib)
                    lib.emit("people")
        self._embed_imported_faces(flags, should_stop)
        if flags["faces"]:
            faces.update_suggestions_and_clusters(lib)
            lib.emit("people")
        lib.emit("index_done", stats)
        return stats

    # -- per photo (runs in worker threads; no DB writes here)
    def _process(self, r, flags) -> dict:
        out = {"id": r["id"], "kind": r["kind"]}
        try:
            if not r["phash"]:
                t = thumbs.make_thumb(r)
                with Image.open(t) as im:
                    out["phash"] = dupes.phash(im)
                    if flags["clip"] and not r["clip_done"]:
                        out["clip"] = clip.embed_images([im.convert("RGB")])[0]
            if r["kind"] != "video" and flags["faces"] and not r["faces_done"]:
                im = media.load_image(r["path"], max_dim=1600)
                rgb = np.ascontiguousarray(np.asarray(im.convert("RGB")))
                out["faces"] = faces.engine().detect(rgb)
            if r["kind"] != "video" and flags["vision"] and not r["labels_done"]:
                labels, text = vision_mac.analyze(r["path"])
                out["labels"], out["ocr"] = labels, text
            if flags["clip"] and not r["clip_done"] and "clip" not in out:
                t = thumbs.make_thumb(r)
                with Image.open(t) as im:
                    out["clip"] = clip.embed_images([im.convert("RGB")])[0]
        except Exception as e:  # unreadable file etc.
            out["error"] = str(e)
        return out

    def _store(self, results: list[dict], stats: dict) -> None:
        db = self.lib.db
        with db.write_lock:
            c = db.conn
            for res in results:
                pid = res["id"]
                failed = "error" in res
                if "phash" in res or failed:
                    c.execute("UPDATE photos SET phash=? WHERE id=?", (res.get("phash", ""), pid))
                if "faces" in res or (failed and res["kind"] != "video"):
                    existing = c.execute("SELECT id, x, y, w, h, embedding FROM faces WHERE photo_id=?",
                                         (pid,)).fetchall()
                    for fdat in res.get("faces", []):
                        l, t, r, b = fdat["rect"]
                        dup = None
                        for e in existing:
                            from ..library import _iou
                            if _iou((l, t, r, b), (e["x"], e["y"], e["x"] + e["w"], e["y"] + e["h"])) > 0.4:
                                dup = e
                                break
                        blob = faces.to_blob(fdat["embedding"])
                        if dup is not None:
                            if dup["embedding"] is None:
                                c.execute("UPDATE faces SET embedding=?, score=? WHERE id=?",
                                          (blob, fdat["score"], dup["id"]))
                            continue
                        c.execute("INSERT INTO faces(photo_id, x, y, w, h, source, score, embedding) "
                                  "VALUES(?,?,?,?,?,?,?,?)", (pid, l, t, r - l, b - t, "auto", fdat["score"], blob))
                        stats["faces"] += 1
                    c.execute("UPDATE photos SET faces_done=1 WHERE id=?", (pid,))
                if "labels" in res or (failed and res["kind"] != "video"):
                    c.execute("DELETE FROM labels WHERE photo_id=? AND source='vision'", (pid,))
                    c.executemany("INSERT OR REPLACE INTO labels(photo_id, label, conf, source) VALUES(?,?,?,?)",
                                  [(pid, lab, conf, "vision") for lab, conf in res.get("labels", [])])
                    c.execute("UPDATE photos SET labels_done=1, ocr=? WHERE id=?", (res.get("ocr", ""), pid))
                    stats["labels"] += len(res.get("labels", []))
                if "clip" in res:
                    c.execute("INSERT OR REPLACE INTO clip(photo_id, vec) VALUES(?,?)",
                              (pid, np.asarray(res["clip"], dtype=np.float32).tobytes()))
                    c.execute("UPDATE photos SET clip_done=1 WHERE id=?", (pid,))
                elif failed:
                    c.execute("UPDATE photos SET clip_done=1 WHERE id=?", (pid,))
            c.commit()

    def _embed_imported_faces(self, flags, should_stop) -> None:
        """Compute embeddings for face rectangles imported from Picasa, so they train recognition."""
        if not flags["faces"]:
            return
        lib = self.lib
        rows = lib.db.q("SELECT f.id, f.x, f.y, f.w, f.h, p.path FROM faces f JOIN photos p ON p.id=f.photo_id "
                        "WHERE f.embedding IS NULL AND f.ignored=0 AND p.kind!='video' ORDER BY p.id LIMIT 5000")
        cache_path, cache_rgb = None, None
        eng = faces.engine()
        for r in rows:
            if should_stop and should_stop():
                return
            try:
                if r["path"] != cache_path:
                    im = media.load_image(r["path"], max_dim=1600)
                    cache_rgb = np.ascontiguousarray(np.asarray(im.convert("RGB")))
                    cache_path = r["path"]
                emb = eng.embed_rect(cache_rgb, (r["x"], r["y"], r["x"] + r["w"], r["y"] + r["h"]))
                lib.db.x("UPDATE faces SET embedding=? WHERE id=?",
                         (faces.to_blob(emb) if emb is not None else b"", r["id"]))
            except Exception:
                lib.db.x("UPDATE faces SET embedding=? WHERE id=?", (b"", r["id"]))
