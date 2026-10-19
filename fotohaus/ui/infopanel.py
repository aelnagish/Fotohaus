"""Right-hand panel: properties, caption, tags, people, location, detected objects/text."""
from __future__ import annotations

import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea,
                               QToolButton, QVBoxLayout, QWidget)

from .. import media
from . import theme
from .grid import fmt_date, fmt_duration


def _sec(text):
    l = QLabel(text.upper())
    l.setObjectName("Section")
    return l


class InfoPanel(QScrollArea):
    person_clicked = Signal(int)
    tag_clicked = Signal(str)
    show_on_map = Signal(float, float)

    def __init__(self, lib, parent=None):
        super().__init__(parent)
        self.lib = lib
        self.setObjectName("InfoPanel")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setMinimumWidth(250)
        self.setMaximumWidth(340)
        self.pid = None
        body = QWidget()
        body.setObjectName("InfoPanel")
        self.v = QVBoxLayout(body)
        self.v.setContentsMargins(14, 12, 14, 12)
        self.v.setSpacing(6)
        self.title = QLabel()
        self.title.setObjectName("Title")
        self.title.setWordWrap(True)
        self.v.addWidget(self.title)
        self.sub = QLabel()
        self.sub.setObjectName("Muted")
        self.sub.setWordWrap(True)
        self.v.addWidget(self.sub)

        star_row = QHBoxLayout()
        self.star_btn = QToolButton()
        self.star_btn.setCheckable(True)
        self.star_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.star_btn.setText("Star")
        self.star_btn.toggled.connect(self._star)
        star_row.addWidget(self.star_btn)
        star_row.addStretch()
        self.v.addLayout(star_row)

        self.v.addWidget(_sec("Caption"))
        self.caption = QLineEdit()
        self.caption.setPlaceholderText("Add a caption…")
        self.caption.editingFinished.connect(self._caption)
        self.v.addWidget(self.caption)

        self.v.addWidget(_sec("Tags"))
        self.tags = QLineEdit()
        self.tags.setPlaceholderText("Comma-separated tags")
        self.tags.editingFinished.connect(self._tags)
        self.v.addWidget(self.tags)

        self.v.addWidget(_sec("People"))
        self.people_box = QVBoxLayout()
        self.v.addLayout(self.people_box)

        self.v.addWidget(_sec("Albums"))
        self.albums_lbl = QLabel()
        self.albums_lbl.setWordWrap(True)
        self.v.addWidget(self.albums_lbl)

        self.v.addWidget(_sec("Details"))
        self.form = QFormLayout()
        self.form.setLabelAlignment(Qt.AlignLeft)
        self.form.setHorizontalSpacing(10)
        self.v.addLayout(self.form)

        self.loc_btn = QPushButton("Show on map")
        self.loc_btn.clicked.connect(self._map)
        self.v.addWidget(self.loc_btn)

        self.v.addWidget(_sec("Recognized"))
        self.labels_lbl = QLabel()
        self.labels_lbl.setWordWrap(True)
        self.labels_lbl.setObjectName("Muted")
        self.v.addWidget(self.labels_lbl)
        self.ocr_lbl = QLabel()
        self.ocr_lbl.setWordWrap(True)
        self.ocr_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.v.addWidget(self.ocr_lbl)

        self.v.addWidget(_sec("Edits"))
        self.edits_lbl = QLabel()
        self.edits_lbl.setWordWrap(True)
        self.v.addWidget(self.edits_lbl)
        self.v.addStretch()
        self.setWidget(body)
        self._multi: list[int] = []
        self.show_photo(None)

    def show_multi(self, ids: list[int]):
        if len(ids) <= 1:
            self.show_photo(ids[0] if ids else None)
            return
        self._multi = ids
        self.pid = None
        rows = self.lib.photos_by_ids(ids)
        self.title.setText(f"{len(ids)} items selected")
        size = sum(r["size"] or 0 for r in rows)
        dates = [r["taken"] for r in rows if r["taken"]]
        span = f"{fmt_date(min(dates))} – {fmt_date(max(dates))}" if dates else ""
        self.sub.setText(f"{media.format_bytes(size)}\n{span}")
        for w in (self.caption, self.tags, self.loc_btn):
            w.setEnabled(False)
        self.star_btn.blockSignals(True)
        self.star_btn.setChecked(all(r["star"] for r in rows))
        self.star_btn.blockSignals(False)
        self.star_btn.setIcon(theme.icon("star" if self.star_btn.isChecked() else "star-outline", theme.STAR))
        self._clear_form()
        self._clear_people()
        self.albums_lbl.setText("")
        self.labels_lbl.setText("")
        self.ocr_lbl.setText("")
        self.edits_lbl.setText("")

    def _clear_form(self):
        while self.form.rowCount():
            self.form.removeRow(0)

    def _clear_people(self):
        while self.people_box.count():
            w = self.people_box.takeAt(0).widget()
            if w:
                w.deleteLater()

    def refresh(self):
        if self._multi:
            self.show_multi(self._multi)
        else:
            self.show_photo(self.pid)

    def show_photo(self, pid):
        self._multi = []
        self.pid = pid
        r = self.lib.photo(pid) if pid else None
        self._clear_form()
        self._clear_people()
        enabled = r is not None
        for w in (self.caption, self.tags, self.star_btn):
            w.setEnabled(enabled)
        if r is None:
            self.title.setText("No selection")
            self.sub.setText("Select a photo to see its details.")
            self.caption.setText("")
            self.tags.setText("")
            self.loc_btn.setEnabled(False)
            self.albums_lbl.setText("")
            self.labels_lbl.setText("")
            self.ocr_lbl.setText("")
            self.edits_lbl.setText("")
            return
        self.title.setText(r["filename"])
        self.sub.setText(fmt_date(r["taken"]))
        self.star_btn.blockSignals(True)
        self.star_btn.setChecked(bool(r["star"]))
        self.star_btn.blockSignals(False)
        self.star_btn.setIcon(theme.icon("star" if r["star"] else "star-outline", theme.STAR))
        self.caption.setText(r["caption"] or "")
        self.tags.setText(", ".join(k for k in (r["keywords"] or "").split(",") if k))
        add = self.form.addRow
        if r["width"]:
            add("Size", QLabel(f"{r['width']} × {r['height']}  ·  {media.format_bytes(r['size'])}"))
        if r["kind"] == "video" and r["duration"]:
            add("Length", QLabel(fmt_duration(r["duration"])))
        if r["camera"]:
            add("Camera", QLabel(r["camera"]))
        if r["lens"]:
            add("Lens", QLabel(r["lens"]))
        if r["exposure"]:
            add("Exposure", QLabel(r["exposure"]))
        folder = QLabel(os.path.dirname(r["path"]))
        folder.setWordWrap(True)
        folder.setTextInteractionFlags(Qt.TextSelectableByMouse)
        add("Folder", folder)
        if r["lat"] is not None:
            add("Location", QLabel(f"{r['lat']:.5f}, {r['lon']:.5f}"))
        self.loc_btn.setEnabled(r["lat"] is not None)
        for f in self.lib.faces_for_photo(pid):
            if f["person_id"] and f["person_name"]:
                b = QPushButton(("✓ " if f["confirmed"] else "? ") + f["person_name"])
                b.setFlat(True)
                b.setStyleSheet("text-align: left;")
                b.clicked.connect(lambda _=False, p=f["person_id"]: self.person_clicked.emit(p))
                self.people_box.addWidget(b)
        if self.people_box.count() == 0:
            n = len(self.lib.faces_for_photo(pid))
            lab = QLabel(f"{n} unnamed face{'s' if n != 1 else ''}" if n else "No people")
            lab.setObjectName("Muted")
            self.people_box.addWidget(lab)
        albs = [a["name"] for a in self.lib.albums_for_photo(pid)]
        self.albums_lbl.setText(", ".join(albs) if albs else "—")
        labels = [l["label"] for l in self.lib.labels_for_photo(pid)[:12]]
        self.labels_lbl.setText(", ".join(labels) if labels else "—")
        ocr = (r["ocr"] or "").strip()
        self.ocr_lbl.setText(("Text: " + (ocr[:300] + ("…" if len(ocr) > 300 else ""))) if ocr else "")
        from ..edits import label_for
        chain = self.lib.filters(pid)
        parts = [label_for(f) for f in chain]
        if r["rotate"]:
            parts.insert(0, f"Rotated {r['rotate'] * 90}°")
        if self.lib.has_backup(pid):
            parts.append("(original saved in .picasaoriginals)")
        self.edits_lbl.setText(", ".join(parts) if parts else "None")

    def _star(self, on):
        if self._multi:
            self.lib.set_star(self._multi, on)
        elif self.pid:
            self.lib.set_star([self.pid], on)
        self.star_btn.setIcon(theme.icon("star" if on else "star-outline", theme.STAR))

    def _caption(self):
        if self.pid:
            r = self.lib.photo(self.pid)
            if r and (r["caption"] or "") != self.caption.text().strip():
                self.lib.set_caption(self.pid, self.caption.text())

    def _tags(self):
        if self.pid:
            kws = [t.strip() for t in self.tags.text().split(",")]
            r = self.lib.photo(self.pid)
            if r and ",".join(k for k in kws if k) != (r["keywords"] or ""):
                self.lib.set_keywords(self.pid, kws)

    def _map(self):
        r = self.lib.photo(self.pid) if self.pid else None
        if r and r["lat"] is not None:
            self.show_on_map.emit(r["lat"], r["lon"])
