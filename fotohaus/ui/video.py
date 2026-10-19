"""Video playback widget (Qt Multimedia) with Picasa-style trim (start/end) and snapshot."""
from __future__ import annotations

import os

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSlider, QToolButton, QVBoxLayout, QWidget

from . import theme

try:
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
    from PySide6.QtMultimediaWidgets import QVideoWidget
    HAVE_MM = True
except Exception:  # pragma: no cover
    HAVE_MM = False


def fmt_ms(ms: int) -> str:
    s = max(0, int(ms // 1000))
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


class VideoPlayer(QWidget):
    """Player with transport controls. `trim` adds Set Start / Set End / Snapshot buttons."""
    snapshot_taken = Signal(str)
    trim_changed = Signal(float, float)   # start, end seconds (end 0 = to the end)

    def __init__(self, trim: bool = False, dark: bool = True, parent=None):
        super().__init__(parent)
        self.path = None
        self.start_s = 0.0
        self.end_s = 0.0
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        if not HAVE_MM:
            lab = QLabel("Video playback isn't available in this build.")
            lab.setAlignment(Qt.AlignCenter)
            v.addWidget(lab)
            return
        self.video = QVideoWidget()
        self.video.setStyleSheet("background: black;")
        v.addWidget(self.video, 1)
        self.player = None      # created on first load (keeps startup fast)
        self.audio = None
        bar = QWidget()
        h = QHBoxLayout(bar)
        h.setContentsMargins(8, 2, 8, 6)
        col = theme.fg() if not dark else None
        from PySide6.QtGui import QColor
        icol = col or QColor("#f0f0f0")
        self.play_btn = QToolButton()
        self.play_btn.setIcon(theme.icon("play", icol))
        self.play_btn.setAutoRaise(True)
        self.play_btn.clicked.connect(self.toggle)
        h.addWidget(self.play_btn)
        self.pos = QSlider(Qt.Horizontal)
        self.pos.sliderMoved.connect(lambda v_: self.player is not None and self.player.setPosition(v_))
        h.addWidget(self.pos, 1)
        self.time = QLabel("0:00 / 0:00")
        if dark:
            self.time.setStyleSheet("color: #ddd;")
        h.addWidget(self.time)
        self.vol = QSlider(Qt.Horizontal)
        self.vol.setRange(0, 100)
        self.vol.setValue(80)
        self.vol.setFixedWidth(70)
        self.vol.valueChanged.connect(lambda x: self.audio is not None and self.audio.setVolume(x / 100))
        h.addWidget(self.vol)
        if trim:
            for text, cb in (("Set Start", self._set_start), ("Set End", self._set_end),
                             ("Clear Trim", self._clear_trim), ("Save Snapshot", self.snapshot)):
                b = QToolButton()
                b.setText(text)
                b.clicked.connect(cb)
                h.addWidget(b)
            self.trim_lbl = QLabel("")
            h.addWidget(self.trim_lbl)
        v.addWidget(bar)

    def _ensure_player(self):
        if self.player is not None:
            return
        import os
        self.player = QMediaPlayer(self)
        if not os.environ.get("FOTOHAUS_NO_AUDIO"):
            self.audio = QAudioOutput(self)
            self.audio.setVolume(self.vol.value() / 100)
            self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.video)
        self.player.positionChanged.connect(self._pos)
        self.player.durationChanged.connect(lambda d: self.pos.setRange(0, d))
        self.player.playbackStateChanged.connect(self._state)

    def load(self, path: str, start: float = 0.0, end: float = 0.0, autoplay: bool = True):
        if not HAVE_MM:
            return
        self._ensure_player()
        self.path = path
        self.start_s, self.end_s = start, end
        self.player.setSource(QUrl.fromLocalFile(path))
        if start:
            self.player.setPosition(int(start * 1000))
        self._trim_label()
        if autoplay:
            self.player.play()

    def stop(self):
        if HAVE_MM and self.player is not None:
            self.player.stop()
            self.player.setSource(QUrl())

    def toggle(self):
        if self.player is None:
            return
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            if self.end_s and self.player.position() >= self.end_s * 1000 - 50:
                self.player.setPosition(int(self.start_s * 1000))
            self.player.play()

    def _state(self, st):
        from PySide6.QtGui import QColor
        self.play_btn.setIcon(theme.icon("pause" if st == QMediaPlayer.PlayingState else "play", QColor("#f0f0f0")))

    def _pos(self, p):
        if not self.pos.isSliderDown():
            self.pos.setValue(p)
        self.time.setText(f"{fmt_ms(p)} / {fmt_ms(self.player.duration())}")
        if self.end_s and p >= self.end_s * 1000:
            self.player.pause()

    # -- trim
    def _set_start(self):
        if self.player is None:
            return
        self.start_s = self.player.position() / 1000
        self._trim_label()
        self.trim_changed.emit(self.start_s, self.end_s)

    def _set_end(self):
        if self.player is None:
            return
        self.end_s = self.player.position() / 1000
        self._trim_label()
        self.trim_changed.emit(self.start_s, self.end_s)

    def _clear_trim(self):
        self.start_s = self.end_s = 0.0
        self._trim_label()
        self.trim_changed.emit(0.0, 0.0)

    def _trim_label(self):
        if hasattr(self, "trim_lbl"):
            if self.start_s or self.end_s:
                self.trim_lbl.setText(f"Trim {fmt_ms(self.start_s * 1000)}–"
                                      f"{fmt_ms(self.end_s * 1000) if self.end_s else 'end'}")
            else:
                self.trim_lbl.setText("")

    def snapshot(self):
        """Save the current frame as a JPEG next to the video (Picasa's 'Save Snapshot')."""
        if not self.path or self.player is None:
            return
        img = None
        try:
            frame = self.video.videoSink().videoFrame()
            if frame.isValid():
                img = frame.toImage()
        except Exception:
            img = None
        t = self.player.position() / 1000
        if img is None or img.isNull():
            from .. import media
            pil = media.video_frame(self.path, None, at=t)
            if pil is None:
                return
            out = self._snap_name(t)
            pil.save(out, quality=92)
        else:
            out = self._snap_name(t)
            img.save(out, "JPG", 92)
        self.snapshot_taken.emit(out)

    def _snap_name(self, t):
        base, _ = os.path.splitext(self.path)
        i = 0
        while True:
            p = f"{base}-snapshot-{int(t)}s{'-' + str(i) if i else ''}.jpg"
            if not os.path.exists(p):
                return p
            i += 1
