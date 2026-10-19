"""Timeline view: folders laid out along time, newest on the right (Picasa's Timeline)."""
from __future__ import annotations

import datetime as _dt

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from . import theme


class _Strip(QWidget):
    folder_clicked = Signal(int)

    def __init__(self, lib, loader, parent=None):
        super().__init__(parent)
        self.lib, self.loader = lib, loader
        self.items = []
        self.card = 190
        self.setMouseTracking(True)
        loader.loaded.connect(lambda _: self.update())

    def reload(self):
        folders = [f for f in self.lib.folders() if f["date"]]
        folders.sort(key=lambda f: f["date"])
        self.items = []
        for f in folders:
            cover = self.lib.db.one(f"SELECT * FROM photos p WHERE folder_id=? AND hidden=0 AND {self.lib.vis()} ORDER BY star DESC, taken "
                                    "LIMIT 1", (f["id"],))
            if cover is not None:
                self.items.append((f, cover))
        self.setMinimumWidth(max(800, len(self.items) * (self.card + 24) + 80))
        self.setMinimumHeight(self.card + 140)
        self.update()

    def _rect(self, i):
        return QRectF(40 + i * (self.card + 24), 50, self.card, self.card)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        pal = self.palette()
        y_axis = 50 + self.card + 40
        p.setPen(QPen(pal.text().color(), 1))
        p.drawLine(20, y_axis, self.width() - 20, y_axis)
        last_year = None
        for i, (f, cover) in enumerate(self.items):
            r = self._rect(i)
            if not r.intersects(QRectF(e.rect())):
                continue
            pm = self.loader.get(cover)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 50))
            p.drawRoundedRect(r.translated(2, 3), 8, 8)
            p.setBrush(pal.base())
            p.drawRoundedRect(r, 8, 8)
            if pm is not None:
                src = pm.scaled(int(r.width() - 12), int(r.height() - 12), Qt.KeepAspectRatioByExpanding,
                                Qt.SmoothTransformation)
                tgt = r.adjusted(6, 6, -6, -6)
                p.drawPixmap(tgt.toRect(), src, QRectF((src.width() - tgt.width()) / 2,
                                                       (src.height() - tgt.height()) / 2, tgt.width(),
                                                       tgt.height()).toRect())
            d = _dt.datetime.fromtimestamp(f["date"])
            p.setPen(pal.text().color())
            p.setFont(theme.scaled_font(self.font(), 1.0, True))
            p.drawText(QRectF(r.left(), r.bottom() + 4, r.width(), 18), Qt.AlignHCenter,
                       p.fontMetrics().elidedText(f["name"], Qt.ElideRight, int(r.width())))
            p.setFont(theme.scaled_font(self.font(), 0.85, False))
            p.drawText(QRectF(r.left(), r.bottom() + 22, r.width(), 16), Qt.AlignHCenter,
                       f"{d:%b %d, %Y} · {f['count']}")
            x = r.center().x()
            p.drawLine(int(x), y_axis - 4, int(x), y_axis + 4)
            if d.year != last_year:
                p.setFont(theme.scaled_font(self.font(), 1.3, True))
                p.drawText(QRectF(x - 40, y_axis + 8, 80, 26), Qt.AlignHCenter, str(d.year))
                last_year = d.year

    def mouseDoubleClickEvent(self, e):
        for i, (f, _) in enumerate(self.items):
            if self._rect(i).contains(e.position()):
                self.folder_clicked.emit(f["id"])
                return

    mousePressEvent = mouseDoubleClickEvent


class TimelineView(QWidget):
    folder_clicked = Signal(int)

    def __init__(self, lib, loader, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        head = QHBoxLayout()
        t = QLabel("Timeline")
        t.setObjectName("Title")
        t.setContentsMargins(14, 10, 0, 4)
        head.addWidget(t)
        head.addStretch()
        v.addLayout(head)
        self.strip = _Strip(lib, loader)
        self.strip.folder_clicked.connect(self.folder_clicked)
        self.area = QScrollArea()
        self.area.setWidget(self.strip)
        self.area.setWidgetResizable(False)
        v.addWidget(self.area, 1)
        hint = QLabel("Click a folder to open it. Scroll sideways to travel through time.")
        hint.setObjectName("Muted")
        hint.setContentsMargins(14, 4, 14, 8)
        v.addWidget(hint)

    def refresh(self):
        self.strip.reload()
        self.strip.resize(self.strip.minimumWidth(), max(self.strip.minimumHeight(), self.area.viewport().height()))
        self.area.horizontalScrollBar().setValue(self.area.horizontalScrollBar().maximum())
