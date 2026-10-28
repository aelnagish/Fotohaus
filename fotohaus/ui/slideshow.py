"""Full-screen slideshow with Picasa-style transitions and on-screen controls."""
from __future__ import annotations

import random

from PySide6.QtCore import (QObject, QPointF, QRectF, QRunnable, QSize, Qt, QThreadPool, QTimer,
                            QVariantAnimation, Signal)
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QToolButton, QWidget

from .. import macos, thumbs
from . import theme

WHITE = QColor("#f0f0f0")
TRANSITIONS = [("dissolve", "Dissolve"), ("panzoom", "Pan and zoom"), ("wipe", "Wipe"), ("shrink", "Shrink"),
               ("none", "None"), ("random", "Random")]


class _Sig(QObject):
    ready = Signal(int, QImage)


class _Load(QRunnable):
    def __init__(self, sig, idx, row):
        super().__init__()
        self.sig, self.idx, self.row = sig, idx, row

    def run(self):
        try:
            img = QImage(str(thumbs.make_thumb(self.row, thumbs.PREVIEW_PX)))
        except Exception:
            img = QImage()
        self.sig.ready.emit(self.idx, img)


class Slideshow(QWidget):
    closed = Signal(int)

    def __init__(self, rows, start=0, seconds=4, parent=None, lib=None):
        super().__init__(parent, Qt.Window)
        self.setWindowTitle("Slideshow")
        self.lib = lib
        if lib is None:
            try:
                from ..library import get_library
                self.lib = get_library()
            except Exception:
                self.lib = None
        self.rows = [r if isinstance(r, dict) else dict(r) for r in rows if (r["kind"] if "kind" in r.keys() else "photo") != "video"]
        self.i = max(0, min(start, len(self.rows) - 1))
        self.seconds = float(seconds)
        s = self.lib.settings if self.lib else None
        self.transition = (s.get("slideshow_transition") if s else None) or "dissolve"
        self.captions = bool(s.get("slideshow_captions")) if s else True
        self.cur = QImage()
        self.prev = QImage()
        self.t = 1.0            # transition progress
        self.kb = 0.0           # Ken Burns progress for the current slide
        self.kind = self.transition
        self.cache: dict[int, QImage] = {}
        self.playing = True
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(2)
        self.sig = _Sig()
        self.sig.ready.connect(self._ready)
        self.timer = QTimer(self, interval=int(self.seconds * 1000), timeout=self.next)
        self.fade = QVariantAnimation(self, startValue=0.0, endValue=1.0, duration=800)
        self.fade.valueChanged.connect(self._fade)
        self.kb_anim = QVariantAnimation(self, startValue=0.0, endValue=1.0)
        self.kb_anim.valueChanged.connect(lambda v: (setattr(self, "kb", float(v)), self.update()))
        self.hud_timer = QTimer(self, singleShot=True, interval=2500, timeout=self._hide_hud)
        self.setMouseTracking(True)
        self._build_bar()
        self._load(self.i)
        self._load(self.i + 1)

    # ------------------------------------------------------------------ controls
    def _build_bar(self):
        self.bar = QWidget(self)
        self.bar.setObjectName("SSBar")
        self.bar.setStyleSheet("#SSBar { background: rgba(20,20,22,215); border-radius: 14px; }"
                               "QLabel { color: #ddd; } QToolButton { border: none; padding: 6px; border-radius: 6px; }"
                               "QToolButton:hover { background: rgba(255,255,255,40); }"
                               "QComboBox { color: #eee; background: rgba(255,255,255,30); border: 1px solid "
                               "rgba(255,255,255,60); border-radius: 6px; padding: 3px 8px; }"
                               "QComboBox QAbstractItemView { color: #111; background: #f4f4f4; }")
        h = QHBoxLayout(self.bar)
        h.setContentsMargins(10, 4, 10, 4)

        def btn(icon, tip, cb, color=WHITE):
            b = QToolButton()
            b.setIcon(theme.icon(icon, color))
            b.setIconSize(QSize(22, 22))
            b.setToolTip(tip)
            b.clicked.connect(cb)
            h.addWidget(b)
            return b
        btn("back", "Previous (←)", self.back)
        self.play_btn = btn("pause", "Pause / play (Space)", self.toggle_play)
        btn("fwd", "Next (→)", self.next)
        h.addSpacing(10)
        self.trans = QComboBox()
        for k, label in TRANSITIONS:
            self.trans.addItem(label, k)
        self.trans.setCurrentIndex(max(0, self.trans.findData(self.transition)))
        self.trans.currentIndexChanged.connect(self._set_transition)
        h.addWidget(self.trans)
        btn("zoom-out", "Shorter display time (↓)", lambda: self.set_seconds(self.seconds - 1))
        self.secs_lbl = QLabel()
        h.addWidget(self.secs_lbl)
        btn("zoom-in", "Longer display time (↑)", lambda: self.set_seconds(self.seconds + 1))
        h.addSpacing(10)
        self.cap_btn = QToolButton()
        self.cap_btn.setText("Captions")
        self.cap_btn.setCheckable(True)
        self.cap_btn.setChecked(self.captions)
        self.cap_btn.setStyleSheet("color: #ddd;")
        self.cap_btn.toggled.connect(lambda on: (setattr(self, "captions", on), self.update()))
        h.addWidget(self.cap_btn)
        self.star_btn = btn("star-outline", macos.keys("Star (⌘8)"), self.toggle_star, theme.STAR)
        btn("rotate-left", "Rotate counterclockwise", lambda: self.rotate(-1))
        btn("rotate-right", "Rotate clockwise", lambda: self.rotate(1))
        btn("close", "Exit (Esc)", self.close)
        self.set_seconds(self.seconds)

    def resizeEvent(self, e):
        self.bar.adjustSize()
        self.bar.move((self.width() - self.bar.width()) // 2, self.height() - self.bar.height() - 24)
        super().resizeEvent(e)

    def set_seconds(self, s):
        self.seconds = max(1.0, min(60.0, s))
        self.secs_lbl.setText(f"{self.seconds:.0f} s")
        self.timer.setInterval(int(self.seconds * 1000))
        self.kb_anim.setDuration(int(self.seconds * 1000 + 800))

    def _set_transition(self, i):
        self.transition = self.trans.itemData(i)
        if self.lib:
            self.lib.settings.set("slideshow_transition", self.transition)

    def toggle_play(self):
        self.playing = not self.playing
        (self.timer.start() if self.playing else self.timer.stop())
        self.play_btn.setIcon(theme.icon("pause" if self.playing else "play", WHITE))
        self._wake()

    def _update_star(self):
        if self.rows:
            on = bool(self.rows[self.i].get("star"))
            self.star_btn.setIcon(theme.icon("star" if on else "star-outline", theme.STAR))

    def toggle_star(self):
        if not self.rows or self.lib is None:
            return
        r = self.rows[self.i]
        on = not r.get("star")
        self.lib.set_star_path(r["path"], on)
        r["star"] = 1 if on else 0
        self._update_star()

    def rotate(self, d):
        if not self.rows or self.lib is None:
            return
        r = self.rows[self.i]
        self.lib.rotate_path(r["path"], d)
        nr = self.lib.describe_file(r["path"])
        nr["id"] = r.get("id")
        self.rows[self.i] = nr
        self.cache.pop(self.i, None)
        self.cur = QImage()
        self._load(self.i)

    # ------------------------------------------------------------------ playback
    def start(self):
        self.showFullScreen()
        self.timer.start()
        self._update_star()
        self._start_kb()
        self._wake()

    def _load(self, idx):
        if 0 <= idx < len(self.rows) and idx not in self.cache:
            self.cache[idx] = QImage()
            self.pool.start(_Load(self.sig, idx, self.rows[idx]))

    def _ready(self, idx, img):
        self.cache[idx] = img
        if idx == self.i and self.cur.isNull():
            self.cur = img
            self.update()

    def _fade(self, v):
        self.t = float(v)
        self.update()

    def _start_kb(self):
        self.kb = 0.0
        self.kb_anim.stop()
        if self.kind == "panzoom":
            self.kb_anim.start()

    def show_index(self, idx):
        if not self.rows:
            return
        idx %= len(self.rows)
        self.prev = self.cur
        self.prev_kb = self.kb
        self.i = idx
        self.cur = self.cache.get(idx, QImage())
        self._load(idx)
        self._load(idx + 1)
        for k in [k for k in self.cache if abs(k - idx) > 3]:
            del self.cache[k]
        self.kind = random.choice([k for k, _ in TRANSITIONS[:4]]) if self.transition == "random" else self.transition
        self._update_star()
        self._start_kb()
        if self.kind == "none":
            self.t = 1.0
            self.update()
        else:
            self.t = 0.0
            self.fade.start()

    def next(self):
        self.show_index(self.i + 1)

    def back(self):
        self.show_index(self.i - 1)

    # ------------------------------------------------------------------ painting
    def _fit_rect(self, img, kb=0.0, idx=0):
        s = img.size()
        s.scale(self.size(), Qt.KeepAspectRatio)
        r = QRectF(0, 0, s.width(), s.height())
        r.moveCenter(QRectF(self.rect()).center())
        if self.kind == "panzoom" or (kb and self.transition == "panzoom"):
            z = 1.0 + 0.12 * kb
            dx = (1 if idx % 2 else -1) * 0.04 * kb * r.width()
            dy = (1 if idx % 4 < 2 else -1) * 0.03 * kb * r.height()
            c = r.center() + QPointF(dx, dy)
            r = QRectF(0, 0, r.width() * z, r.height() * z)
            r.moveCenter(c)
        return r

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.fillRect(self.rect(), Qt.black)
        t = self.t
        if t < 1.0 and not self.prev.isNull():
            pr = self._fit_rect(self.prev, getattr(self, "prev_kb", 0.0), self.i - 1)
            if self.kind == "dissolve" or self.kind == "panzoom":
                p.setOpacity(1 - t)
                p.drawImage(pr, self.prev)
            elif self.kind == "shrink":
                sr = QRectF(pr)
                sr.setSize(pr.size() * (1 - t))
                sr.moveCenter(pr.center())
                p.drawImage(sr, self.prev)
            elif self.kind == "wipe":
                p.drawImage(pr, self.prev)
        if not self.cur.isNull():
            cr = self._fit_rect(self.cur, self.kb, self.i)
            if t < 1.0 and self.kind == "wipe":
                p.save()
                p.setClipRect(QRectF(0, 0, self.width() * t, self.height()))
                p.drawImage(cr, self.cur)
                p.restore()
            else:
                p.setOpacity(t if self.kind in ("dissolve", "panzoom", "shrink") else 1.0)
                p.drawImage(cr, self.cur)
        p.setOpacity(1.0)
        if self.rows and self.captions:
            cap = self.rows[self.i].get("caption") or ""
            if cap:
                p.setFont(theme.scaled_font(self.font(), 1.6))
                br = QRectF(0, self.height() - 140, self.width(), 50)
                p.setPen(QColor(0, 0, 0, 200))
                p.drawText(br.translated(1, 1), Qt.AlignCenter, cap)
                p.setPen(Qt.white)
                p.drawText(br, Qt.AlignCenter, cap)
        if self.bar.isVisible() and self.rows:
            p.setPen(QColor(230, 230, 230))
            p.setFont(theme.scaled_font(self.font(), 1.0))
            p.drawText(QRectF(20, 16, 400, 24), Qt.AlignLeft, f"{self.i + 1} / {len(self.rows)}   "
                       f"{self.rows[self.i].get('filename', '')}")

    # ------------------------------------------------------------------ input
    def _hide_hud(self):
        if not self.bar.underMouse():
            self.bar.hide()
            self.setCursor(Qt.BlankCursor)
            self.update()

    def _wake(self):
        self.bar.show()
        self.unsetCursor()
        self.hud_timer.start()
        self.update()

    def mouseMoveEvent(self, e):
        self._wake()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.next()
            self.timer.start()
        elif e.button() == Qt.RightButton:
            self.back()

    def keyPressEvent(self, e):
        k = e.key()
        if k in (Qt.Key_Escape, Qt.Key_Q):
            self.close()
        elif k in (Qt.Key_Right, Qt.Key_PageDown):
            self.next()
            self.timer.start()
        elif k in (Qt.Key_Left, Qt.Key_PageUp):
            self.back()
            self.timer.start()
        elif k == Qt.Key_Up:
            self.set_seconds(self.seconds + 1)
        elif k == Qt.Key_Down:
            self.set_seconds(self.seconds - 1)
        elif k == Qt.Key_Space:
            self.toggle_play()
        elif k == Qt.Key_8 and e.modifiers() & (Qt.ControlModifier | Qt.MetaModifier):
            self.toggle_star()

    def closeEvent(self, e):
        self.timer.stop()
        rid = self.rows[self.i].get("id") if self.rows else None
        self.closed.emit(rid if isinstance(rid, int) and rid > 0 else -1)
        super().closeEvent(e)
