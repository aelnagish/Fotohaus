"""Photo Viewer: the window that opens when a photo is double-clicked in Finder.

Behaves like Picasa Photo Viewer: the photo floats over a dimmed desktop, arrow keys move
through the other photos in the same folder, the wheel zooms, clicking the backdrop or Esc
closes, and a control bar offers star / rotate / delete / slideshow / edit.
"""
from __future__ import annotations

import os
import re

from PySide6.QtCore import (QEvent, QObject, QPointF, QRectF, QRunnable, QSize, Qt, QThreadPool, QTimer, Signal)
from PySide6.QtGui import QColor, QGuiApplication, QImage, QKeySequence, QMovie, QPainter, QShortcut
from PySide6.QtWidgets import QHBoxLayout, QMessageBox, QToolButton, QWidget

from .. import macos, media, thumbs
from . import pointer, theme

WHITE = QColor("#f2f2f2")


def natural_key(s: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def folder_items(path: str) -> list[str]:
    folder = os.path.dirname(os.path.abspath(path))
    try:
        names = [f for f in os.listdir(folder) if not f.startswith(".") and media.kind_of(f)]
    except OSError:
        names = [os.path.basename(path)]
    # the short .mov half of a Live Photo is shown with its still, not as a separate item
    stills = {os.path.splitext(n)[0].lower() for n in names if media.kind_of(n) in ("photo", "raw")}
    names = [n for n in names if not (n.lower().endswith(".mov") and os.path.splitext(n)[0].lower() in stills)
             or n == os.path.basename(path)]
    names.sort(key=natural_key)
    return [os.path.join(folder, n) for n in names]


def _items_around(path: str) -> tuple[list[str], int]:
    """The pictures in path's folder and path's position among them. Matching ignores case where
    the file system does (Windows), so "IMG_1.JPG" given by Explorer finds "img_1.jpg"."""
    p = os.path.abspath(path)
    items = folder_items(p)
    key = os.path.normcase(p)
    for k, q in enumerate(items):
        if os.path.normcase(q) == key:
            return items, k
    return [p] + items, 0


def live_partner(path: str) -> str | None:
    """The .mov of an iPhone Live Photo saved next to the still (IMG_1234.HEIC + IMG_1234.MOV)."""
    if media.kind_of(path) != "photo":
        return None
    stem = os.path.splitext(path)[0]
    for ext in (".MOV", ".mov"):
        if os.path.exists(stem + ext):
            return stem + ext
    return None


class _Sig(QObject):
    done = Signal(str, int, QImage)     # path, size, image


class _Load(QRunnable):
    def __init__(self, sig, row, size):
        super().__init__()
        self.sig, self.row, self.size = sig, row, size

    def run(self):
        img = QImage()
        try:
            if self.size <= 0:   # full resolution, edits applied
                from ..edits import render
                from ..picasa.ini import parse_filters
                im = media.load_image(self.row["path"])
                im = render(im, parse_filters(self.row["filters"]), self.row["rotate"] or 0)
                from .editor import pil_to_qimage
                img = pil_to_qimage(im)
            else:
                img = QImage(str(thumbs.make_thumb(self.row, self.size)))
        except Exception:
            img = QImage()
        self.sig.done.emit(self.row["path"], self.size, img)


class _StripSig(QObject):
    done = Signal(str, QImage)


class _StripLoad(QRunnable):
    def __init__(self, sig, row):
        super().__init__()
        self.sig, self.row = sig, row

    def run(self):
        try:
            img = QImage(str(thumbs.make_thumb(self.row, thumbs.THUMB_PX)))
        except Exception:
            img = QImage()
        self.sig.done.emit(self.row["path"], img)


class _Button(QToolButton):
    def __init__(self, icon_name, tip, cb, parent=None, color=WHITE):
        super().__init__(parent)
        self.setIcon(theme.icon(icon_name, color))
        self.setIconSize(QSize(22, 22))
        self.setToolTip(tip)
        self.setAutoRaise(True)
        self.setCursor(Qt.PointingHandCursor)
        self.clicked.connect(cb)
        self.setStyleSheet("QToolButton { border: none; padding: 6px; border-radius: 6px; }"
                           "QToolButton:hover { background: rgba(255,255,255,40); }")


class PhotoViewer(QWidget):
    edit_requested = Signal(str)          # path
    closed = Signal()

    def __init__(self, lib, path: str, parent=None):
        super().__init__(parent, Qt.Window | Qt.FramelessWindowHint)
        self.lib = lib
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowTitle("Fotohaus Photo Viewer")
        self.setMouseTracking(True)
        self.items, self.i = _items_around(path)
        self.row = None
        self.img = QImage()
        self.full_loaded = False
        self.zoom_fit = True
        self.zoom = 1.0
        self.offset = QPointF(0, 0)
        self._drag = None
        self.show_info = False
        self.cache: dict = {}
        self.movie: QMovie | None = None
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(3)
        self.sig = _Sig()
        self.sig.done.connect(self._loaded)
        self.video = None
        self.live = None
        self.orig_size = None
        self.strip_icons: dict = {}
        self.strip_sig = _StripSig()
        self.strip_sig.done.connect(self._strip_loaded)
        self._build_bar()
        self._build_strip()
        self._shortcuts()
        scr = QGuiApplication.screenAt(self.cursor().pos()) or QGuiApplication.primaryScreen()
        self.setGeometry(scr.availableGeometry())
        self.dpr = scr.devicePixelRatio()
        self.view_px = int(min(3200, max(scr.geometry().width(), scr.geometry().height()) * self.dpr))
        self.hide_timer = QTimer(self, singleShot=True, interval=2600, timeout=self._autohide)
        self.show_item(self.i)

    # ------------------------------------------------------------------ UI
    def _build_bar(self):
        self.bar = QWidget(self)
        self.bar.setObjectName("ViewerBar")
        self.bar.setStyleSheet("#ViewerBar { background: rgba(20,20,22,215); border-radius: 14px; }"
                               "QLabel { color: #e6e6e6; }")
        h = QHBoxLayout(self.bar)
        h.setContentsMargins(10, 4, 10, 4)
        h.setSpacing(2)
        self.b_zoom_out = _Button("zoom-out", "Zoom out (−)", lambda: self.zoom_by(1 / 1.25))
        self.b_zoom_in = _Button("zoom-in", "Zoom in (+)", lambda: self.zoom_by(1.25))
        self.b_fit = _Button("library", "Fit to screen / actual size (double-click)", self.toggle_zoom)
        for b in (self.b_zoom_out, self.b_zoom_in, self.b_fit):
            h.addWidget(b)
        h.addSpacing(10)
        h.addWidget(_Button("back", "Previous (←)", lambda: self.step(-1)))
        self.b_play = _Button("play", "Slideshow", self.slideshow)
        h.addWidget(self.b_play)
        h.addWidget(_Button("fwd", "Next (→)", lambda: self.step(1)))
        h.addSpacing(10)
        h.addWidget(_Button("rotate-left", "Rotate counterclockwise ([)", lambda: self.rotate(-1)))
        h.addWidget(_Button("rotate-right", "Rotate clockwise (])", lambda: self.rotate(1)))
        self.b_star = _Button("star-outline", macos.keys("Star (⌘8)"), self.toggle_star, color=theme.STAR)
        h.addWidget(self.b_star)
        self.b_live = _Button("live", "Play Live Photo (L)", self.play_live)
        h.addWidget(self.b_live)
        h.addWidget(_Button("trash", "Delete (⌫)", self.delete))
        h.addSpacing(10)
        h.addWidget(_Button("info", "Info (I)", self.toggle_info))
        h.addWidget(_Button("share", "Share…", self.share))
        edit = QToolButton()
        edit.setText("Edit in Fotohaus")
        edit.setCursor(Qt.PointingHandCursor)
        edit.setStyleSheet(f"QToolButton {{ color: white; background: {theme.ACCENT.name()}; border: none; "
                           f"border-radius: 6px; padding: 5px 10px; margin-left: 6px; }} "
                           f"QToolButton:hover {{ background: {theme.ACCENT.lighter(112).name()}; }}")
        edit.clicked.connect(lambda: self.edit_requested.emit(self.items[self.i]))
        h.addWidget(edit)
        h.addWidget(_Button("close", "Close (Esc)", self.close))
        self.bar.adjustSize()

    def _build_strip(self):
        from PySide6.QtWidgets import QListView, QListWidget
        self.strip = QListWidget(self)
        self.strip.setViewMode(QListView.IconMode)
        self.strip.setFlow(QListView.LeftToRight)
        self.strip.setWrapping(False)
        self.strip.setIconSize(QSize(56, 56))
        self.strip.setGridSize(QSize(64, 64))
        self.strip.setFixedHeight(76)
        self.strip.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.strip.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.strip.setStyleSheet("QListWidget { background: rgba(20,20,22,170); border-radius: 10px; }"
                                 "QListWidget::item:selected { background: rgba(47,125,225,160); border-radius: 6px; }")
        self._fill_strip()
        self.strip.itemClicked.connect(lambda it: self.show_item(self.items.index(it.data(Qt.UserRole)))
                                       if it.data(Qt.UserRole) in self.items else None)

    def _fill_strip(self):
        from PySide6.QtWidgets import QListWidgetItem
        self.strip.clear()
        for p in self.items:
            it = QListWidgetItem()
            it.setToolTip(os.path.basename(p))
            it.setData(Qt.UserRole, p)
            icon = self.strip_icons.get(p)
            if icon is not None:
                it.setIcon(icon)
            self.strip.addItem(it)

    def open_path(self, path: str) -> None:
        """Show another photo in this viewer: used when a file is opened from Finder / Explorer while
        the viewer is already up and "Show the new photo in the open viewer" is chosen in Options.
        The filmstrip and the arrow keys switch to the new photo's folder."""
        items, i = _items_around(path)
        if items != self.items:
            self.items = items
            self._fill_strip()
        self.show_item(i)
        if self.isMinimized():
            self.showNormal()
        self.raise_()
        self.activateWindow()

    def _strip_window(self):
        """Load thumbnails for the strip around the current picture."""
        lo, hi = max(0, self.i - 12), min(len(self.items), self.i + 13)
        for k in range(lo, hi):
            p = self.items[k]
            if p in self.strip_icons:
                continue
            self.strip_icons[p] = None
            try:
                row = self.lib.describe_file(p)
            except OSError:
                continue
            self.pool.start(_StripLoad(self.strip_sig, row))

    def _strip_loaded(self, path, img):
        from PySide6.QtGui import QIcon, QPixmap
        if img.isNull():
            return
        icon = QIcon(QPixmap.fromImage(img))
        self.strip_icons[path] = icon
        for k in range(self.strip.count()):
            it = self.strip.item(k)
            if it.data(Qt.UserRole) == path:
                it.setIcon(icon)
                break

    def _shortcuts(self):
        for key, fn in ((Qt.Key_Right, lambda: self.step(1)), (Qt.Key_Down, lambda: self.step(1)),
                        (Qt.Key_Left, lambda: self.step(-1)), (Qt.Key_Up, lambda: self.step(-1)),
                        (Qt.Key_Space, lambda: self.step(1)), (Qt.Key_Home, lambda: self.show_item(0)),
                        (Qt.Key_End, lambda: self.show_item(len(self.items) - 1)),
                        (Qt.Key_Escape, self.close), (Qt.Key_Plus, lambda: self.zoom_by(1.25)),
                        (Qt.Key_Equal, lambda: self.zoom_by(1.25)), (Qt.Key_Minus, lambda: self.zoom_by(0.8)),
                        (Qt.Key_0, self.fit), (Qt.Key_I, self.toggle_info),
                        (Qt.Key_BracketLeft, lambda: self.rotate(-1)), (Qt.Key_BracketRight, lambda: self.rotate(1)),
                        (Qt.Key_Return, lambda: self.edit_requested.emit(self.items[self.i])),
                        (Qt.Key_Backspace, self.delete), (Qt.Key_Delete, self.delete)):
            QShortcut(QKeySequence(key), self, fn)
        QShortcut(QKeySequence("Ctrl+8"), self, self.toggle_star)
        QShortcut(QKeySequence(Qt.Key_L), self, self.play_live)
        QShortcut(QKeySequence("Ctrl+W"), self, self.close)

    def resizeEvent(self, e):
        self.bar.adjustSize()
        self.bar.move((self.width() - self.bar.width()) // 2, self.height() - self.bar.height() - 26)
        sw = min(self.width() - 80, max(300, 64 * self.strip.count() + 16))
        self.strip.setGeometry((self.width() - sw) // 2, self.bar.y() - 84, sw, 76)
        if self.video is not None:
            self._place_video()
        super().resizeEvent(e)

    def _autohide(self):
        if not self.bar.underMouse() and not self.strip.underMouse():
            self.bar.hide()
            self.strip.hide()
            self.setCursor(Qt.BlankCursor if not self.zoom_fit or self.video is None else Qt.ArrowCursor)

    def _wake(self):
        self.bar.show()
        if len(self.items) > 1:
            self.strip.show()
        self.unsetCursor()
        self.hide_timer.start()

    # ------------------------------------------------------------------ items
    def show_item(self, i):
        if not self.items:
            self.close()
            return
        self.i = max(0, min(i, len(self.items) - 1))
        path = self.items[self.i]
        self._stop_media()
        try:
            self.row = self.lib.describe_file(path)
        except OSError:
            self.items.pop(self.i)
            self.show_item(self.i)
            return
        self.zoom_fit, self.offset, self.full_loaded = True, QPointF(0, 0), False
        self.orig_size = None
        if not self.row["filters"] and self.row["kind"] in ("photo", "raw"):
            w_, h_ = self.row.get("width") or 0, self.row.get("height") or 0
            if not w_:
                try:
                    md = media.read_metadata(path)
                    w_, h_ = md["width"], md["height"]
                except Exception:
                    w_ = h_ = 0
            if w_ and h_:
                self.orig_size = (h_, w_) if (self.row["rotate"] or 0) % 2 else (w_, h_)
        if self.strip.count() == len(self.items):
            self.strip.setCurrentRow(self.i)
            self.strip.scrollToItem(self.strip.item(self.i))
        self._strip_window()
        self.b_star.setIcon(theme.icon("star" if self.row["star"] else "star-outline", theme.STAR))
        self.live = live_partner(path)
        self.b_live.setVisible(bool(self.live))
        self.bar.adjustSize()
        kind = self.row["kind"]
        if kind == "video":
            self.img = QImage()
            self._start_video(path)
        elif path.lower().endswith(".gif") and not self.row["filters"]:
            self.movie = QMovie(path)
            if self.movie.isValid() and self.movie.frameCount() != 1:
                self.movie.frameChanged.connect(lambda _: self._movie_frame())
                self.movie.start()
            else:
                self.movie = None
                self._request(self.row)
        else:
            self._request(self.row)
        # prefetch neighbours
        for j in (self.i + 1, self.i - 1):
            if 0 <= j < len(self.items) and media.kind_of(self.items[j]) != "video":
                try:
                    self._request(self.lib.describe_file(self.items[j]), prefetch=True)
                except OSError:
                    pass
        self.update()
        self._wake()

    def _request(self, row, prefetch=False):
        key = (row["path"], row["mtime"], row["filters"], row["rotate"])
        if key in self.cache:
            if not prefetch:
                img = self.cache[key]
                if img.isNull():          # still being prefetched: show the thumbnail meanwhile
                    p = thumbs.thumb_path(row, thumbs.THUMB_PX)
                    img = QImage(str(p)) if p.exists() else img
                self.img = img
                self.update()
            return
        if not prefetch:
            p = thumbs.thumb_path(row, thumbs.THUMB_PX)
            if p.exists():
                self.img = QImage(str(p))   # instant low-res while the sharp version loads
            else:
                self.img = QImage()
        self.cache[key] = QImage() if prefetch else self.img
        self.pool.start(_Load(self.sig, row, self.view_px))

    def _loaded(self, path, size, img):
        r = None
        try:
            r = self.lib.describe_file(path)
        except OSError:
            return
        key = (r["path"], r["mtime"], r["filters"], r["rotate"])
        if size <= 0:
            if path == self.items[self.i]:
                self.img = img
                self.full_loaded = True
                self.update()
            return
        self.cache[key] = img
        if len(self.cache) > 12:
            for k in list(self.cache)[:4]:
                if k[0] != self.items[self.i]:
                    del self.cache[k]
        if path == self.items[self.i] and not img.isNull():
            self.img = img
            self.update()

    def _movie_frame(self):
        if self.movie is not None:
            self.img = self.movie.currentImage()
            self.update()

    def _start_video(self, path):
        from .video import VideoPlayer
        self.video = VideoPlayer(trim=False, dark=True, parent=self)
        self.video.load(path)
        self.video.show()
        self._place_video()

    def _place_video(self):
        W, H = self.width(), self.height()
        w, h = int(W * 0.8), int(H * 0.78)
        self.video.setGeometry((W - w) // 2, int(H * 0.06), w, h)

    def _stop_media(self):
        if self.movie is not None:
            self.movie.stop()
            self.movie = None
        if self.video is not None:
            self.video.stop()
            self.video.deleteLater()
            self.video = None

    def play_live(self):
        """Play the motion of a Live Photo; press again (or L) to go back to the still."""
        if not getattr(self, "live", None):
            return
        if self.video is not None:
            self._stop_media()
            self.update()
            return
        self._start_video(self.live)

    def step(self, d):
        if len(self.items) > 1:
            self.show_item((self.i + d) % len(self.items))

    # ------------------------------------------------------------------ actions
    def rotate(self, d):
        if self.row is None or self.row["kind"] == "video":
            return
        self.lib.rotate_path(self.items[self.i], d)
        self.show_item(self.i)

    def toggle_star(self):
        if self.row is None:
            return
        on = not self.row["star"]
        self.lib.set_star_path(self.items[self.i], on)
        self.row["star"] = 1 if on else 0
        self.b_star.setIcon(theme.icon("star" if on else "star-outline", theme.STAR))
        self.update()

    def delete(self):
        path = self.items[self.i]
        r = self.lib.photo_by_path(path)
        if r is not None:      # library photos go to Recently Deleted (restorable for 30 days)
            self._stop_media()
            ok = self.lib.soft_delete([r["id"]])
        else:
            if QMessageBox.question(self, f"Move to {macos.TRASH}", f"Move “{os.path.basename(path)}” to the {macos.TRASH}?") \
                    != QMessageBox.Yes:
                return
            self._stop_media()
            from ..library import _trash
            ok = _trash(path)
        if ok:
            self.items.pop(self.i)
            self.show_item(min(self.i, len(self.items) - 1))

    def slideshow(self):
        from .slideshow import Slideshow
        rows = []
        for k, p in enumerate(self.items):
            if media.kind_of(p) == "video":
                continue
            try:
                d = self.lib.describe_file(p)
            except OSError:
                continue
            d["id"] = d["id"] if d["id"] is not None else -(k + 1)
            rows.append(d)
        start = next((k for k, d in enumerate(rows) if d["path"] == self.items[self.i]), 0)
        self._ss = Slideshow(rows, start, int(self.lib.settings.get("slideshow_seconds") or 4))
        self._ss.closed_path = None
        self._ss.start()

    def share(self):
        from .. import macos
        macos.share_files([self.items[self.i]], self)

    def toggle_info(self):
        self.show_info = not self.show_info
        if self.show_info and self.row is not None and not self.row.get("_md"):
            md = media.read_metadata(self.items[self.i])
            self.row["_md"] = md
        self.update()

    # ------------------------------------------------------------------ zoom
    def image_rect(self) -> QRectF:
        if self.img.isNull():
            return QRectF()
        iw, ih = self.img.width(), self.img.height()
        s = self._fit_scale() if self.zoom_fit else self.zoom
        r = QRectF(0, 0, iw * s, ih * s)
        r.moveCenter(QPointF(self.width() / 2, (self.height() - 70) / 2) + self.offset)
        return r

    def _fit_scale(self) -> float:
        iw, ih = self.img.width(), self.img.height()
        W, H = self.width() * 0.92, (self.height() - 90) * 0.96
        cap = (self.orig_size[0] / iw) / self.dpr if self.orig_size else 1e9   # never beyond 100%
        return min(W / iw, H / ih, cap)

    def _ensure_full(self):
        if not self.full_loaded and self.row is not None and self.row["kind"] != "video":
            self.full_loaded = True
            self.pool.start(_Load(self.sig, self.row, 0))

    def zoom_by(self, f, anchor: QPointF | None = None):
        if self.img.isNull():
            return
        r = self.image_rect()
        cur = r.width() / max(1, self.img.width())
        new = max(0.02, min(16.0, cur * f))
        if f < 1 and new <= self._fit_scale() * 1.001:
            # zooming out past "fit" settles back on the whole photo, centred, like Picasa
            self.fit()
            return
        k = new / cur
        if anchor is not None:
            # keep the image point under the cursor fixed
            win_c = QPointF(self.width() / 2, (self.height() - 70) / 2)
            c = r.center()
            v = anchor - c
            self.offset = (anchor - v * k) - win_c
        else:
            self.offset = self.offset * k
        self.zoom = new
        self.zoom_fit = False
        self._ensure_full()
        self.update()

    def toggle_zoom(self):
        if self.zoom_fit:
            self.zoom_fit = False
            self.zoom = 1.0 / self.dpr
            self._ensure_full()
        else:
            self.fit()
        self.update()

    def fit(self):
        self.zoom_fit = True
        self.offset = QPointF(0, 0)
        self.update()

    # ------------------------------------------------------------------ painting & mouse
    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(0, 0, 0, 205))
        if self.video is None:
            r = self.image_rect()
            if not self.img.isNull():
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(0, 0, 0, 120))
                p.drawRect(r.adjusted(-1, 3, 3, 6))
                p.drawImage(r, self.img)
            else:
                p.setPen(WHITE)
                p.drawText(self.rect(), Qt.AlignCenter, "Loading…")
        # title / counter
        p.setPen(WHITE)
        f = theme.scaled_font(self.font(), 1.05, True)
        p.setFont(f)
        name = os.path.basename(self.items[self.i]) if self.items else ""
        p.drawText(QRectF(24, 16, self.width() - 48, 24), Qt.AlignLeft | Qt.AlignVCenter, name)
        p.setFont(theme.scaled_font(self.font(), 0.95, False))
        p.setPen(QColor(220, 220, 220, 190))
        extra = f"{self.i + 1} of {len(self.items)}"
        if not self.img.isNull() and self.orig_size:
            pct = int(round(self.image_rect().width() * self.dpr / max(1, self.orig_size[0]) * 100))
            extra += f"   ·   {pct}%"
        p.drawText(QRectF(24, 40, self.width() - 48, 20), Qt.AlignLeft | Qt.AlignVCenter, extra)
        if self.row and self.row["star"]:
            p.setPen(Qt.NoPen)
            p.setBrush(theme.STAR)
            p.drawPath(theme.star_path(QRectF(self.width() - 46, 18, 22, 22)))
        if self.row and self.row.get("caption"):
            p.setFont(theme.scaled_font(self.font(), 1.25, False))
            p.setPen(WHITE)
            p.drawText(QRectF(0, self.height() - 120, self.width(), 30), Qt.AlignCenter, self.row["caption"])
        if self.show_info and self.row:
            self._paint_info(p)

    def _paint_info(self, p):
        md = self.row.get("_md") or {}
        lines = [self.items[self.i]]
        if md.get("width"):
            lines.append(f"{md['width']} × {md['height']}   {media.format_bytes(self.row.get('size'))}")
        if md.get("taken"):
            import datetime as _dt
            lines.append(_dt.datetime.fromtimestamp(md["taken"]).strftime("%A, %B %d, %Y  %H:%M"))
        for k in ("camera", "lens", "exposure"):
            if md.get(k):
                lines.append(md[k])
        if md.get("lat") is not None:
            lines.append(f"{md['lat']:.5f}, {md['lon']:.5f}")
        box = QRectF(self.width() - 380, 70, 356, 22 * len(lines) + 20)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(20, 20, 22, 220))
        p.drawRoundedRect(box, 10, 10)
        p.setPen(WHITE)
        p.setFont(theme.scaled_font(self.font(), 0.95, False))
        for k, line in enumerate(lines):
            p.drawText(QRectF(box.left() + 12, box.top() + 10 + 22 * k, box.width() - 24, 22),
                       Qt.AlignLeft | Qt.AlignVCenter | Qt.TextSingleLine,
                       p.fontMetrics().elidedText(line, Qt.ElideMiddle, int(box.width() - 24)))

    def mouseMoveEvent(self, e):
        self._wake()
        if self._drag:
            start, off = self._drag
            self.offset = off + (e.position() - start)
            self.update()

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        r = self.image_rect()
        if self.video is None and not r.contains(e.position()) and not self.bar.geometry().contains(e.position().toPoint()) \
                and not (self.strip.isVisible() and self.strip.geometry().contains(e.position().toPoint())):
            self.close()   # click on the backdrop closes, like Picasa
            return
        if not self.zoom_fit:
            self._drag = (e.position(), QPointF(self.offset))

    def mouseReleaseEvent(self, e):
        self._drag = None

    def mouseDoubleClickEvent(self, e):
        if self.video is None:
            self.toggle_zoom()

    def wheelEvent(self, e):
        if self.video is not None:
            return
        if pointer.is_trackpad_scroll(e) and not pointer.zoom_key(e):
            # trackpad: two-finger scroll pans a zoomed photo (pinch zooms, see event())
            if not self.zoom_fit:
                self.offset += QPointF(e.pixelDelta())
                self.update()
            return
        # mouse wheel (or Cmd/Ctrl + scroll): zoom around the pointer
        dy = pointer.wheel_steps(e)
        if dy:
            self.zoom_by(1.0015 ** dy, e.position())

    def event(self, e):
        if e.type() == QEvent.NativeGesture and e.gestureType() == Qt.NativeGestureType.ZoomNativeGesture:
            self.zoom_by(1.0 + e.value(), e.position())
            return True
        return super().event(e)

    def closeEvent(self, e):
        self._stop_media()
        self.closed.emit()
        super().closeEvent(e)
