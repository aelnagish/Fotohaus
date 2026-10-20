"""Right-hand Info panel: a preview, the essentials, and tidy cards for description, details,
people and albums, recognized content and edits."""
from __future__ import annotations

import os

from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QLayout, QLineEdit, QPushButton,
                               QScrollArea, QSizePolicy, QVBoxLayout, QWidget)

from .. import media
from . import theme
from .grid import fmt_date, fmt_duration


# ------------------------------------------------------------------ small building blocks

class FlowLayout(QLayout):
    """Lays out chips left to right and wraps onto new lines."""

    def __init__(self, parent=None, spacing=6):
        super().__init__(parent)
        self._items = []
        self._sp = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientations(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        return self._do(QRect(0, 0, w, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        s = QSize()
        for it in self._items:
            s = s.expandedTo(it.minimumSize())
        return s

    def _do(self, rect, test):
        x, y, line = rect.x(), rect.y(), 0
        for it in self._items:
            hint = it.sizeHint()
            if x + hint.width() > rect.right() + 1 and line > 0:
                x, y, line = rect.x(), y + line + self._sp, 0
            if not test:
                it.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self._sp
            line = max(line, hint.height())
        return y + line - rect.y()


class Card(QFrame):
    """A rounded group with a small caps heading."""

    def __init__(self, title: str):
        super().__init__()
        self.setObjectName("InfoCard")
        self.v = QVBoxLayout(self)
        self.v.setContentsMargins(12, 10, 12, 12)
        self.v.setSpacing(6)
        h = QLabel(title.upper())
        h.setObjectName("Section")
        self.v.addWidget(h)


def _muted(text=""):
    lab = QLabel(text)
    lab.setObjectName("Muted")
    lab.setWordWrap(True)
    return lab


class Preview(QWidget):
    """Rounded photo preview; shows a small fanned stack when several photos are selected."""

    def __init__(self):
        super().__init__()
        self.pms: list[QPixmap] = []
        self.setFixedHeight(170)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_pixmaps(self, pms):
        self.pms = [p for p in pms if p is not None and not p.isNull()]
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        area = QRectF(self.rect()).adjusted(4, 4, -4, -4)
        dark = theme.is_dark()
        if not self.pms:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#2b2c30" if dark else "#e7e9ed"))
            p.drawRoundedRect(area, 10, 10)
            theme.icon("library", QColor("#6c6f76" if dark else "#a3a7ae")).paint(
                p, QRect(int(area.center().x()) - 18, int(area.center().y()) - 18, 36, 36))
            return
        stack = self.pms[:3]
        for i, pm in reversed(list(enumerate(stack))):
            sc = min(area.width() / pm.width(), area.height() / pm.height())
            if len(stack) > 1:
                sc *= 0.82
            w, h = pm.width() * sc, pm.height() * sc
            r = QRectF(area.center().x() - w / 2, area.center().y() - h / 2, w, h)
            p.save()
            if len(stack) > 1:
                p.translate(r.center())
                p.rotate((-6, 5, -2)[i] if i else 0)
                p.translate(-r.center())
            path = QPainterPath()
            path.addRoundedRect(r, 8, 8)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 45))
            p.drawRoundedRect(r.translated(0, 2), 8, 8)
            p.setClipPath(path)
            p.drawPixmap(r.toRect(), pm)
            p.setClipping(False)
            if len(stack) > 1:
                p.setPen(QColor(255, 255, 255, 220))
                p.setBrush(Qt.NoBrush)
                p.drawRoundedRect(r, 8, 8)
            p.restore()


# ------------------------------------------------------------------ the panel

class InfoPanel(QScrollArea):
    person_clicked = Signal(int)
    tag_clicked = Signal(str)
    show_on_map = Signal(float, float)

    def __init__(self, lib, loader=None, parent=None):
        super().__init__(parent)
        self.lib = lib
        self.loader = loader
        self.setObjectName("InfoPanel")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setMinimumWidth(250)
        self.setMaximumWidth(360)
        self.pid = None
        self._multi: list[int] = []
        if loader is not None:
            loader.loaded.connect(self._thumb_ready)

        body = QWidget()
        body.setObjectName("InfoPanel")
        self.v = QVBoxLayout(body)
        self.v.setContentsMargins(12, 12, 12, 16)
        self.v.setSpacing(10)

        # header: preview, name, date, star
        self.preview = Preview()
        self.v.addWidget(self.preview)
        self.title = QLabel()
        self.title.setObjectName("InfoTitle")
        self.title.setAlignment(Qt.AlignHCenter)
        self.title.setWordWrap(True)
        self.title.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.v.addWidget(self.title)
        self.sub = QLabel()
        self.sub.setObjectName("Muted")
        self.sub.setAlignment(Qt.AlignHCenter)
        self.sub.setWordWrap(True)
        self.v.addWidget(self.sub)
        self.star_btn = QPushButton("Star")
        self.star_btn.setObjectName("StarPill")
        self.star_btn.setCheckable(True)
        self.star_btn.setCursor(Qt.PointingHandCursor)
        self.star_btn.toggled.connect(self._star)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(self.star_btn)
        row.addStretch()
        self.v.addLayout(row)

        # description
        self.desc = Card("Description")
        self.caption = QLineEdit()
        self.caption.setObjectName("InfoField")
        self.caption.setPlaceholderText("Add a caption")
        self.caption.editingFinished.connect(self._caption)
        self.tags = QLineEdit()
        self.tags.setObjectName("InfoField")
        self.tags.setPlaceholderText("Add tags, separated by commas")
        self.tags.editingFinished.connect(self._tags)
        self.desc.v.addWidget(self.caption)
        self.desc.v.addWidget(self.tags)
        self.v.addWidget(self.desc)

        # details
        self.details = Card("Details")
        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(12)
        self.grid.setVerticalSpacing(5)
        self.grid.setColumnStretch(1, 1)
        self.details.v.addLayout(self.grid)
        self.loc_btn = QPushButton("Show on Map")
        self.loc_btn.setObjectName("LinkButton")
        self.loc_btn.setCursor(Qt.PointingHandCursor)
        self.loc_btn.clicked.connect(self._map)
        lr = QHBoxLayout()
        lr.addStretch()
        lr.addWidget(self.loc_btn)
        self.details.v.addLayout(lr)
        self.v.addWidget(self.details)

        # people & albums
        self.who = Card("People & Albums")
        self.people_flow = FlowLayout()
        self.who.v.addLayout(self.people_flow)
        self.albums_flow = FlowLayout()
        self.who.v.addLayout(self.albums_flow)
        self.who_note = _muted()
        self.who.v.addWidget(self.who_note)
        self.v.addWidget(self.who)

        # recognized
        self.recog = Card("Recognized")
        self.labels_flow = FlowLayout()
        self.recog.v.addLayout(self.labels_flow)
        self.ocr_lbl = _muted()
        self.ocr_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.recog.v.addWidget(self.ocr_lbl)
        self.v.addWidget(self.recog)

        # edits
        self.edits = Card("Edits")
        self.edits_lbl = QLabel()
        self.edits_lbl.setWordWrap(True)
        self.edits.v.addWidget(self.edits_lbl)
        self.v.addWidget(self.edits)

        self.v.addStretch()
        self.setWidget(body)
        self.show_photo(None)

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _clear(layout):
        while layout.count():
            it = layout.takeAt(0)
            w = it.widget() if it else None
            if w:
                w.deleteLater()

    def _detail(self, key, value, selectable=False):
        r = self.grid.rowCount()
        k = QLabel(key)
        k.setObjectName("Muted")
        k.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        val = QLabel(value)
        val.setAlignment(Qt.AlignRight | Qt.AlignTop)
        val.setWordWrap(True)
        if selectable:
            val.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.grid.addWidget(k, r, 0)
        self.grid.addWidget(val, r, 1)

    def _chip(self, flow, text, kind="", cb=None):
        b = QPushButton(text)
        b.setObjectName("Chip" + kind)
        b.setCursor(Qt.PointingHandCursor if cb else Qt.ArrowCursor)
        if cb:
            b.clicked.connect(cb)
        else:
            b.setFocusPolicy(Qt.NoFocus)
        flow.addWidget(b)

    def _set_star(self, on):
        self.star_btn.blockSignals(True)
        self.star_btn.setChecked(bool(on))
        self.star_btn.blockSignals(False)
        self.star_btn.setText("Starred" if on else "Star")
        self.star_btn.setIcon(theme.icon("star" if on else "star-outline", theme.STAR))

    def _thumb(self, row):
        return self.loader.get(row) if self.loader is not None else None

    def _thumb_ready(self, pid):
        if pid == self.pid or pid in self._multi:
            self._update_preview()

    def _update_preview(self):
        ids = self._multi[:3] if self._multi else ([self.pid] if self.pid else [])
        rows = self.lib.photos_by_ids(ids) if ids else []
        self.preview.set_pixmaps([self._thumb(r) for r in rows])

    def _reset_cards(self):
        while self.grid.count():
            w = self.grid.takeAt(0).widget()
            if w:
                w.deleteLater()
        for flow in (self.people_flow, self.albums_flow, self.labels_flow):
            self._clear(flow)

    # ------------------------------------------------------------------ public API
    def refresh(self):
        if self._multi:
            self.show_multi(self._multi)
        else:
            self.show_photo(self.pid)

    def show_multi(self, ids: list[int]):
        if len(ids) <= 1:
            self.show_photo(ids[0] if ids else None)
            return
        self._multi = list(ids)
        self.pid = None
        self._reset_cards()
        rows = self.lib.photos_by_ids(ids)
        self.title.setText(f"{len(ids)} items selected")
        size = sum(r["size"] or 0 for r in rows)
        dates = sorted(r["taken"] for r in rows if r["taken"])
        span = ""
        if dates:
            a, b = fmt_date(dates[0]), fmt_date(dates[-1])
            span = a if a == b else f"{a} – {b}"
        self.sub.setText("  ·  ".join(x for x in (media.format_bytes(size), span) if x))
        self.star_btn.setEnabled(True)
        self._set_star(all(r["star"] for r in rows))
        self._update_preview()
        photos = sum(1 for r in rows if r["kind"] != "video")
        self._detail("Photos", str(photos))
        if len(rows) - photos:
            self._detail("Videos", str(len(rows) - photos))
        self._detail("Total size", media.format_bytes(size))
        folders = {os.path.dirname(r["path"]) for r in rows}
        self._detail("Folders", str(len(folders)) if len(folders) > 1 else os.path.basename(next(iter(folders))))
        self.loc_btn.hide()
        for card in (self.desc, self.who, self.recog, self.edits):
            card.hide()
        self.details.show()

    def show_photo(self, pid):
        same = pid is not None and pid == self.pid and not self._multi
        self._multi = []
        self.pid = pid
        self._reset_cards()
        r = self.lib.photo(pid) if pid else None
        if r is None:
            self.title.setText("No Selection")
            self.sub.setText("Select a photo to see its details.")
            self.star_btn.setEnabled(False)
            self._set_star(False)
            self.preview.set_pixmaps([])
            for card in (self.desc, self.details, self.who, self.recog, self.edits):
                card.hide()
            return
        self.star_btn.setEnabled(True)
        self.title.setText(r["filename"])
        self.sub.setText(fmt_date(r["taken"]))
        self._set_star(r["star"])
        self._update_preview()

        self.desc.show()
        # a refresh while the user is typing must not replace what they typed
        if not (same and self.caption.hasFocus()):
            self.caption.setText(r["caption"] or "")
        if not (same and self.tags.hasFocus()):
            self.tags.setText(", ".join(k for k in (r["keywords"] or "").split(",") if k))
        for e in (self.caption, self.tags):     # show the start of long text, not the end
            if not e.hasFocus():
                e.setCursorPosition(0)

        self.details.show()
        if r["width"]:
            self._detail("Dimensions", f"{r['width']} × {r['height']}")
        self._detail("File size", media.format_bytes(r["size"]))
        if r["kind"] == "video" and r["duration"]:
            self._detail("Length", fmt_duration(r["duration"]))
        if r["camera"]:
            self._detail("Camera", r["camera"])
        if r["lens"]:
            self._detail("Lens", r["lens"])
        if r["exposure"]:
            self._detail("Exposure", r["exposure"])
        self._detail("Folder", os.path.basename(os.path.dirname(r["path"])) or os.path.dirname(r["path"]), True)
        keys = r.keys()
        if r["lat"] is not None:
            place = r["place_name"] if "place_name" in keys else None
            if place:
                self._detail("Place", place, True)
            self._detail("Location", f"{r['lat']:.4f}, {r['lon']:.4f}", True)
        if "stack_count" in keys and (r["stack_count"] or 0) > 1:
            kind = r["stack_kind"]
            self._detail("Stack", {"live": "Live Photo", "raw": "RAW + JPEG", "burst": f"Burst of {r['stack_count']}"}
                         .get(kind, f"{r['stack_count']} versions"))
        self.loc_btn.setVisible(r["lat"] is not None)

        faces = self.lib.faces_for_photo(pid)
        named = [f for f in faces if f["person_id"] and f["person_name"]]
        for f in named:
            self._chip(self.people_flow, ("" if f["confirmed"] else "? ") + f["person_name"], "Person",
                       lambda _=False, p=f["person_id"]: self.person_clicked.emit(p))
        albs = [a["name"] for a in self.lib.albums_for_photo(pid)]
        for name in albs:
            self._chip(self.albums_flow, name, "Album")
        unnamed = len(faces) - len(named)
        notes = []
        if unnamed:
            notes.append(f"{unnamed} unnamed face{'s' if unnamed != 1 else ''}")
        if not named and not albs and not unnamed:
            notes.append("No people or albums yet")
        self.who_note.setText(" · ".join(notes))
        self.who_note.setVisible(bool(notes))
        self.who.show()

        labels = [lab["label"] for lab in self.lib.labels_for_photo(pid)[:12]]
        for lab in labels:
            self._chip(self.labels_flow, lab)
        ocr = (r["ocr"] or "").strip()
        self.ocr_lbl.setText(("“" + (ocr[:240] + ("…" if len(ocr) > 240 else "")) + "”") if ocr else "")
        self.ocr_lbl.setVisible(bool(ocr))
        self.recog.setVisible(bool(labels or ocr))

        from ..edits import label_for
        parts = [label_for(f) for f in self.lib.filters(pid)]
        if r["rotate"]:
            parts.insert(0, f"Rotated {r['rotate'] * 90}°")
        txt = ", ".join(parts)
        if self.lib.has_backup(pid):
            txt += ("\n" if txt else "") + "Original kept in .picasaoriginals"
        self.edits_lbl.setText(txt)
        self.edits.setVisible(bool(txt))

    # ------------------------------------------------------------------ actions
    def _star(self, on):
        if self._multi:
            self.lib.set_star(self._multi, on)
        elif self.pid:
            self.lib.set_star([self.pid], on)
        self._set_star(on)

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
