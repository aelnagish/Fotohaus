"""Picasa's bottom bar: the Photo Tray (selection + held pictures) and the action buttons."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QListView, QListWidget, QListWidgetItem, QProgressBar, QSlider,
                               QToolButton, QVBoxLayout, QWidget)

from . import theme

ACTIONS = {"share": ("share", "Share"), "email": ("mail", "Email"), "print": ("print", "Print"),
           "export": ("export", "Export"), "collage": ("collage", "Collage"), "movie": ("movie", "Movie"),
           "geotag": ("pin", "Geo-Tag"), "html": ("library", "Web Page"), "desktop": ("library", "Desktop")}


class PhotoTray(QWidget):
    action = Signal(str)
    hold = Signal()
    clear = Signal()
    add_to_album = Signal()
    star = Signal()
    rotate = Signal(int)
    zoom_changed = Signal(int)
    stop_job = Signal()

    def __init__(self, loader, zoom: int, buttons: list[str], parent=None):
        super().__init__(parent)
        self.setObjectName("BottomBar")
        self.loader = loader
        h = QHBoxLayout(self)
        h.setContentsMargins(8, 4, 8, 4)
        h.setSpacing(4)
        # --- tray
        tray = QVBoxLayout()
        tray.setSpacing(2)
        self.label = QLabel("Photo Tray")
        self.label.setObjectName("Muted")
        tray.addWidget(self.label)
        self.thumbs = QListWidget()
        self.thumbs.setViewMode(QListView.IconMode)
        self.thumbs.setFlow(QListView.LeftToRight)
        self.thumbs.setWrapping(False)
        self.thumbs.setIconSize(QSize(34, 34))
        self.thumbs.setFixedSize(190, 44)
        self.thumbs.setSpacing(1)
        self.thumbs.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.thumbs.setStyleSheet("QListWidget { background: transparent; border: none; }")
        tray.addWidget(self.thumbs)
        h.addLayout(tray)
        col = QVBoxLayout()
        col.setSpacing(0)

        def small(icon, tip, cb, color=None):
            b = QToolButton()
            b.setIcon(theme.icon(icon, color))
            b.setIconSize(QSize(16, 16))
            b.setToolTip(tip)
            b.setAutoRaise(True)
            b.clicked.connect(cb)
            return b
        col.addWidget(small("hold", "Hold selection in the Photo Tray (⌘K)", self.hold))
        col.addWidget(small("close", "Clear the Photo Tray", self.clear))
        col.addWidget(small("plus", "Add tray items to an album", self.add_to_album))
        h.addLayout(col)
        h.addSpacing(6)
        for icon, tip, cb, color in (("star", "Star / unstar (⌘8)", lambda: self.star.emit(), theme.STAR),
                                     ("rotate-left", "Rotate counterclockwise (⌘[)", lambda: self.rotate.emit(-1), None),
                                     ("rotate-right", "Rotate clockwise (⌘])", lambda: self.rotate.emit(1), None)):
            b = small(icon, tip, cb, color)
            b.setIconSize(QSize(20, 20))
            h.addWidget(b)
        h.addSpacing(10)
        # --- action buttons
        self.btn_box = QHBoxLayout()
        self.btn_box.setSpacing(2)
        h.addLayout(self.btn_box)
        self.set_buttons(buttons)
        h.addStretch()
        # --- status
        self.status = QLabel("")
        self.status.setObjectName("Muted")
        h.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setFixedWidth(140)
        self.progress.setTextVisible(False)
        self.progress.setMaximumHeight(8)
        self.progress.hide()
        h.addWidget(self.progress)
        self.stop_btn = QToolButton()
        self.stop_btn.setText("Stop")
        self.stop_btn.setAutoRaise(True)
        self.stop_btn.clicked.connect(self.stop_job)
        self.stop_btn.hide()
        h.addWidget(self.stop_btn)
        h.addSpacing(8)
        self.zoom = QSlider(Qt.Horizontal)
        self.zoom.setRange(64, 340)
        self.zoom.setValue(zoom)
        self.zoom.setFixedWidth(120)
        self.zoom.setToolTip("Thumbnail size")
        self.zoom.valueChanged.connect(self.zoom_changed)
        h.addWidget(self.zoom)

    def set_buttons(self, keys):
        while self.btn_box.count():
            w = self.btn_box.takeAt(0).widget()
            if w:
                w.deleteLater()
        for k in keys:
            if k not in ACTIONS:
                continue
            icon, label = ACTIONS[k]
            b = QToolButton()
            b.setText(label)
            b.setIcon(theme.icon(icon))
            b.setIconSize(QSize(20, 20))
            b.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            b.setAutoRaise(True)
            b.clicked.connect(lambda _=False, k=k: self.action.emit(k))
            self.btn_box.addWidget(b)

    def set_items(self, rows, held_count: int):
        self.thumbs.clear()
        for r in rows[:40]:
            it = QListWidgetItem()
            pm = self.loader.get(r)
            if pm is not None:
                it.setIcon(QIcon(pm))
            it.setToolTip(r["filename"])
            self.thumbs.addItem(it)
        n = len(rows)
        txt = f"{n} item{'s' if n != 1 else ''} in tray" if n else "Photo Tray"
        if held_count:
            txt += f" · {held_count} held"
        self.label.setText(txt)

    def set_status(self, msg, done, total):
        busy = bool(msg)
        self.progress.setVisible(busy)
        self.stop_btn.setVisible(busy)
        if total > 0:
            self.progress.setRange(0, total)
            self.progress.setValue(done)
        else:
            self.progress.setRange(0, 0)
        self.status.setText(msg)
