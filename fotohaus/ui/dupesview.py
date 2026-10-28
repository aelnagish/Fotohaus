"""Duplicates / near-duplicates review."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel, QMessageBox, QPushButton,
                               QScrollArea, QToolButton, QVBoxLayout, QWidget)

from .. import macos, media
from ..ai.dupes import find_duplicates


class GroupRow(QFrame):
    def __init__(self, loader, rows, parent=None):
        super().__init__(parent)
        self.rows = rows
        self.loader = loader
        h = QHBoxLayout(self)
        h.setContentsMargins(4, 8, 4, 8)
        self.checks = []
        self.buttons = {}
        for i, r in enumerate(rows[:10]):
            col = QVBoxLayout()
            b = QToolButton()
            b.setIconSize(QSize(150, 120))
            b.setAutoRaise(True)
            b.setToolTip(r["path"])
            self.buttons[r["id"]] = (b, r)
            self._icon(r)
            col.addWidget(b)
            info = QLabel(f"{r['width']}×{r['height']} · {media.format_bytes(r['size'])}\n{r['filename']}")
            info.setObjectName("Muted")
            info.setAlignment(Qt.AlignHCenter)
            col.addWidget(info)
            cb = QCheckBox("Keep" if i == 0 else "Remove")
            cb.setChecked(i != 0)
            cb.toggled.connect(lambda on, cb=cb: cb.setText("Remove" if on else "Keep"))
            if i == 0:
                cb.setChecked(False)
            col.addWidget(cb, 0, Qt.AlignHCenter)
            self.checks.append((cb, r))
            h.addLayout(col)
        h.addStretch()
        loader.loaded.connect(self._loaded)

    def _icon(self, r):
        pm = self.loader.get(r)
        if pm is not None:
            self.buttons[r["id"]][0].setIcon(QIcon(pm))

    def _loaded(self, pid):
        if pid in self.buttons:
            self._icon(self.buttons[pid][1])

    def to_remove(self):
        return [r["id"] for cb, r in self.checks if cb.isChecked()]


class DupesView(QWidget):
    open_photo = Signal(int)

    def __init__(self, lib, loader, parent=None):
        super().__init__(parent)
        self.lib, self.loader = lib, loader
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 12, 16, 12)
        top = QHBoxLayout()
        t = QLabel("Duplicates")
        t.setObjectName("Title")
        top.addWidget(t)
        self.info = QLabel()
        self.info.setObjectName("Muted")
        top.addWidget(self.info)
        top.addStretch()
        top.addWidget(QLabel("Match:"))
        self.level = QComboBox()
        self.level.addItems(["Exact copies", "Very similar", "Similar (bursts, edits)"])
        self.level.setCurrentIndex(1)
        self.level.currentIndexChanged.connect(self.refresh)
        top.addWidget(self.level)
        trash = QPushButton(f"Move Marked to {macos.TRASH}")
        trash.clicked.connect(self._trash)
        top.addWidget(trash)
        v.addLayout(top)
        hint = QLabel(f"The largest, highest-resolution copy is kept by default. Files are moved to the {macos.TRASH}, "
                      "so you can restore them.")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        v.addWidget(hint)
        self.area = QScrollArea()
        self.area.setWidgetResizable(True)
        self.area.setFrameShape(QScrollArea.NoFrame)
        v.addWidget(self.area, 1)
        self.groups: list[GroupRow] = []

    def refresh(self):
        dist = [0, 4, 9][self.level.currentIndex()]
        groups = find_duplicates(self.lib, dist)
        pending = self.lib.db.scalar("SELECT COUNT(*) FROM photos WHERE phash IS NULL", default=0)
        w = QWidget()
        cv = QVBoxLayout(w)
        cv.setContentsMargins(0, 0, 0, 0)
        self.groups = []
        for g in groups[:150]:
            row = GroupRow(self.loader, g)
            row.setFrameShape(QFrame.StyledPanel)
            cv.addWidget(row)
            self.groups.append(row)
        if not groups:
            lab = QLabel("No duplicates found." + (f" ({pending} photos still being analyzed.)" if pending else ""))
            lab.setObjectName("Muted")
            cv.addWidget(lab)
        cv.addStretch()
        self.area.setWidget(w)
        extra = sum(len(g) - 1 for g in groups)
        self.info.setText(f"   {len(groups)} groups · {extra} extra copies"
                          + (f" · analyzing {pending} more" if pending else ""))

    def _trash(self):
        ids = [i for g in self.groups for i in g.to_remove()]
        if not ids:
            return
        if QMessageBox.question(self, f"Move to {macos.TRASH}", f"Move {len(ids)} files to the {macos.TRASH}?") != QMessageBox.Yes:
            return
        n = self.lib.move_to_trash(ids)
        QMessageBox.information(self, "Done", f"Moved {n} files to the {macos.TRASH}.")
        self.refresh()
