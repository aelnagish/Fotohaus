"""Background jobs (scan, analyze, export, download...) with progress reporting."""
from __future__ import annotations

import traceback
from collections import OrderedDict

from PySide6.QtCore import QObject, QRunnable, QThread, QThreadPool, Signal, Slot
from PySide6.QtGui import QImage, QPixmap

from .. import thumbs


class Job(QThread):
    progress = Signal(str, int, int)     # message, done, total (total=0 -> indeterminate)
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, name: str, fn, parent=None):
        super().__init__(parent)
        self.name = name
        self.fn = fn
        self._stop = False

    def stop(self):
        self._stop = True

    def should_stop(self) -> bool:
        return self._stop

    def report(self, msg: str, done: int = 0, total: int = 0):
        self.progress.emit(msg, int(done), int(total))

    def run(self):
        try:
            res = self.fn(self)
            self.done.emit(res)
        except Exception as e:  # pragma: no cover - surfaced in UI
            traceback.print_exc()
            self.failed.emit(f"{e}")


class JobManager(QObject):
    """Runs jobs one at a time; exposes status for the status bar."""
    status = Signal(str, int, int)
    idle = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.queue: list[Job] = []
        self.current: Job | None = None

    def submit(self, name: str, fn, on_done=None, on_fail=None, dedupe=True, front=False) -> Job:
        if dedupe:
            if self.current and self.current.name == name:
                return self.current
            for j in self.queue:
                if j.name == name:
                    return j
        job = Job(name, fn)
        job.progress.connect(self.status)
        if on_done:
            job.done.connect(on_done)
        if on_fail:
            job.failed.connect(on_fail)
        job.finished.connect(self._next)
        if front:
            self.queue.insert(0, job)
        else:
            self.queue.append(job)
        if self.current is None:
            self._next()
        return job

    def _next(self):
        self.current = None
        if self.queue:
            self.current = self.queue.pop(0)
            self.current.start(QThread.LowPriority)
        else:
            self.status.emit("", 0, 0)
            self.idle.emit()

    def stop_all(self):
        self.queue.clear()
        if self.current:
            self.current.stop()
            self.current.wait(8000)

    def busy(self) -> bool:
        return self.current is not None


# ------------------------------------------------------------------ thumbnails

class _ThumbTask(QRunnable):
    def __init__(self, loader, row, size, key):
        super().__init__()
        self.loader, self.row, self.size, self.key = loader, row, size, key

    def run(self):
        img = QImage()
        try:
            p = thumbs.make_thumb(self.row, self.size)
            img = QImage(str(p))
        except Exception:
            pass
        self.loader._loaded.emit(self.key, img)


class ThumbLoader(QObject):
    loaded = Signal(int)
    _loaded = Signal(object, QImage)

    def __init__(self, parent=None, capacity: int = 4000):
        super().__init__(parent)
        self.pool = QThreadPool()
        self.pool.setMaxThreadCount(max(2, QThreadPool.globalInstance().maxThreadCount() - 1))
        self.cache: OrderedDict = OrderedDict()
        self.pending: set = set()
        self.capacity = capacity
        self._loaded.connect(self._on_loaded)

    def _key(self, row, size):
        return (row["id"], row["mtime"], row["filters"], row["rotate"], size)

    def get(self, row, size: int = thumbs.THUMB_PX) -> QPixmap | None:
        k = self._key(row, size)
        pm = self.cache.get(k)
        if pm is not None:
            self.cache.move_to_end(k)
            return pm
        if k not in self.pending:
            # the key travels with the task, so a thumbnail requested again after an edit (new key)
            # is never filed under the old one
            self.pending.add(k)
            self.pool.start(_ThumbTask(self, row, size, k))
        return None

    @Slot(object, QImage)
    def _on_loaded(self, k, img):
        self.pending.discard(k)
        if img.isNull():
            img = QImage(8, 8, QImage.Format_RGB32)
            img.fill(0x555555)
        self.cache[k] = QPixmap.fromImage(img)
        while len(self.cache) > self.capacity:
            self.cache.popitem(last=False)
        self.loaded.emit(k[0])

    def invalidate(self, pid: int):
        for k in [k for k in self.cache if k[0] == pid]:
            del self.cache[k]
