"""Thumbnail grid with folder/date section headers (like Picasa's library view)."""
from __future__ import annotations

import datetime as _dt
import json

from PySide6.QtCore import (QAbstractListModel, QMimeData, QModelIndex, QPoint, QRect, QRectF, QSize, Qt,
                            QTimer, QUrl, Signal)
from PySide6.QtGui import QColor, QPainter, QPalette, QPen, QDrag
from PySide6.QtWidgets import QAbstractItemView, QListView, QStyle, QStyledItemDelegate

from . import theme

MIME_IDS = "application/x-fotohaus-photo-ids"
ROLE_ENTRY = Qt.UserRole + 1


def fmt_date(ts) -> str:
    if not ts:
        return ""
    try:
        return _dt.datetime.fromtimestamp(ts).strftime("%b %-d, %Y")
    except (ValueError, OSError):
        return _dt.datetime.fromtimestamp(ts).strftime("%b %d, %Y")


def fmt_duration(sec) -> str:
    if not sec:
        return ""
    sec = int(round(sec))
    return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}" if sec >= 3600 else f"{sec // 60}:{sec % 60:02d}"


class PhotoGridModel(QAbstractListModel):
    """Entries are ('header', dict) or ('photo', sqlite Row)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.entries: list[tuple[str, object]] = []
        self.row_of: dict[int, int] = {}

    def set_entries(self, entries):
        self.beginResetModel()
        self.entries = entries
        self.row_of = {e[1]["id"]: i for i, e in enumerate(entries) if e[0] == "photo"}
        self.endResetModel()

    def set_photos(self, rows, sections: str | None = None, folder_titles: dict | None = None):
        """sections: None (flat) | 'folder' | 'month'."""
        entries = []
        if sections is None:
            entries = [("photo", r) for r in rows]
        else:
            cur = None
            group: list = []

            def flush():
                if group:
                    entries.append(("header", cur | {"count": len(group)}))
                    entries.extend(("photo", r) for r in group)
            for r in rows:
                if sections == "folder":
                    key = r["folder_id"]
                    meta = (folder_titles or {}).get(key, {})
                    hdr = {"key": key, "title": meta.get("title") or "", "date": meta.get("date"),
                           "subtitle": meta.get("subtitle", ""), "folder_id": key}
                elif sections == "year":          # Memories: "3 years ago · October 2023"
                    d = _dt.datetime.fromtimestamp(r["taken"] or 0)
                    ago = _dt.date.today().year - d.year
                    key = d.year
                    hdr = {"key": key, "title": f"{ago} year{'s' if ago != 1 else ''} ago", "date": None,
                           "subtitle": d.strftime("%B %-d, %Y") if hasattr(d, "strftime") else ""}
                else:
                    d = _dt.datetime.fromtimestamp(r["taken"] or 0)
                    key = (d.year, d.month)
                    hdr = {"key": key, "title": d.strftime("%B %Y"), "date": None, "subtitle": ""}
                if cur is None or hdr["key"] != cur["key"]:
                    flush()
                    cur, group = hdr, []
                group.append(r)
            flush()
        self.set_entries(entries)

    def photo_rows(self):
        return [e[1] for e in self.entries if e[0] == "photo"]

    def update_row(self, row):
        i = self.row_of.get(row["id"])
        if i is not None:
            self.entries[i] = ("photo", row)
            idx = self.index(i)
            self.dataChanged.emit(idx, idx)

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.entries)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        kind, obj = self.entries[index.row()]
        if role == ROLE_ENTRY:
            return self.entries[index.row()]
        if role == Qt.ToolTipRole and kind == "photo":
            parts = [obj["filename"]]
            if obj["caption"]:
                parts.append(obj["caption"])
            parts.append(fmt_date(obj["taken"]))
            if obj["width"]:
                parts.append(f"{obj['width']}×{obj['height']}")
            return "\n".join(parts)
        return None

    def flags(self, index):
        if not index.isValid():
            return Qt.NoItemFlags
        if self.entries[index.row()][0] == "header":
            return Qt.ItemIsEnabled
        return Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsDragEnabled

    def mimeTypes(self):
        return [MIME_IDS, "text/uri-list"]

    def mimeData(self, indexes):
        md = QMimeData()
        rows = [self.entries[i.row()][1] for i in indexes if self.entries[i.row()][0] == "photo"]
        md.setData(MIME_IDS, json.dumps([r["id"] for r in rows]).encode())
        md.setUrls([QUrl.fromLocalFile(r["path"]) for r in rows])
        return md


class GridDelegate(QStyledItemDelegate):
    def __init__(self, view, loader):
        super().__init__(view)
        self.view = view
        self.loader = loader
        self.size = 160
        self.caption_mode = "none"
        self.held: set[int] = set()

    def caption_text(self, row) -> str:
        m = self.caption_mode
        if m == "filename":
            return row["filename"]
        if m == "caption":
            return row["caption"] or ""
        if m == "resolution":
            return f"{row['width']}×{row['height']}" if row["width"] else ""
        if m == "date":
            return fmt_date(row["taken"])
        if m == "tags":
            return ", ".join(k for k in (row["keywords"] or "").split(",") if k)
        return ""

    def sizeHint(self, option, index):
        kind, obj = index.data(ROLE_ENTRY)
        if kind == "header":
            w = max(200, self.view.viewport().width() - 2 * self.view.spacing() - 2)
            return QSize(w, 44 if obj.get("subtitle") else 36)
        extra = 18 if self.caption_mode != "none" else 0
        return QSize(self.size + 12, self.size + 12 + extra)

    def paint(self, p: QPainter, option, index):
        kind, obj = index.data(ROLE_ENTRY)
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        pal = option.palette
        if kind == "header":
            self._paint_header(p, option.rect, obj, pal)
        else:
            self._paint_photo(p, option, obj, pal)
        p.restore()

    def _paint_header(self, p, rect, h, pal):
        f = theme.scaled_font(p.font(), 1.15)
        f.setBold(True)
        p.setFont(f)
        p.setPen(pal.color(QPalette.Text))
        r = QRect(rect.left() + 6, rect.top() + 8, rect.width() - 12, 22)
        title = h.get("title") or ""
        p.drawText(r, Qt.AlignLeft | Qt.AlignVCenter, title)
        fm = p.fontMetrics()
        tw = fm.horizontalAdvance(title)
        f2 = theme.scaled_font(f, 0.78, False)
        p.setFont(f2)
        muted = QColor(pal.color(QPalette.Text))
        muted.setAlpha(140)
        p.setPen(muted)
        bits = []
        if h.get("date"):
            bits.append(fmt_date(h["date"]))
        bits.append(f"{h.get('count', 0)} item{'s' if h.get('count', 0) != 1 else ''}")
        p.drawText(QRect(r.left() + tw + 12, r.top(), r.width() - tw - 12, 22), Qt.AlignLeft | Qt.AlignVCenter,
                   "  ·  ".join(bits))
        if h.get("subtitle"):
            p.drawText(QRect(r.left(), r.bottom() + 1, r.width(), 16), Qt.AlignLeft | Qt.AlignVCenter, h["subtitle"])
        line = QColor(pal.color(QPalette.Text))
        line.setAlpha(30)
        p.setPen(QPen(line, 1))
        p.drawLine(rect.left() + 6, rect.bottom() - 1, rect.right() - 6, rect.bottom() - 1)

    def _paint_photo(self, p, option, row, pal):
        cap_h = 18 if self.caption_mode != "none" else 0
        rect = option.rect.adjusted(6, 6, -6, -6 - cap_h)
        if cap_h:
            txt = self.caption_text(row)
            if txt:
                f = theme.scaled_font(p.font(), 0.85)
                p.setFont(f)
                muted = QColor(pal.color(QPalette.Text))
                muted.setAlpha(170)
                p.setPen(muted)
                cr = QRect(option.rect.left() + 4, option.rect.bottom() - cap_h - 2, option.rect.width() - 8, cap_h)
                p.drawText(cr, Qt.AlignCenter, p.fontMetrics().elidedText(txt, Qt.ElideMiddle, cr.width()))
        selected = bool(option.state & QStyle.State_Selected)
        if selected:
            sel = QColor(theme.ACCENT)
            sel.setAlpha(60)
            p.setPen(QPen(theme.ACCENT, 2))
            p.setBrush(sel)
            p.drawRoundedRect(QRectF(option.rect.adjusted(2, 2, -2, -2)), 8, 8)
        pm = self.loader.get(row)
        if pm is None or pm.isNull():
            ph = QColor(pal.color(QPalette.Text))
            ph.setAlpha(18)
            p.setPen(Qt.NoPen)
            p.setBrush(ph)
            p.drawRoundedRect(QRectF(rect).adjusted(rect.width() * 0.08, rect.height() * 0.18,
                                                    -rect.width() * 0.08, -rect.height() * 0.18), 4, 4)
            img_rect = rect
        else:
            s = pm.size()
            s.scale(rect.size(), Qt.KeepAspectRatio)
            img_rect = QRect(0, 0, s.width(), s.height())
            img_rect.moveCenter(rect.center())
            # subtle drop shadow
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 40))
            p.drawRect(img_rect.adjusted(1, 2, 1, 2))
            p.drawPixmap(img_rect, pm)
        # badges
        if row["star"]:
            sr = QRectF(img_rect.left() + 4, img_rect.bottom() - 22, 18, 18)
            p.setPen(QPen(QColor(0, 0, 0, 120), 1))
            p.setBrush(theme.STAR)
            p.drawPath(theme.star_path(sr))
        if row["kind"] == "video":
            txt = fmt_duration(row["duration"]) or "video"
            fm = p.fontMetrics()
            w = fm.horizontalAdvance(txt) + 22
            br = QRectF(img_rect.right() - w - 4, img_rect.bottom() - 20, w, 16)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 150))
            p.drawRoundedRect(br, 4, 4)
            p.setPen(Qt.white)
            p.drawText(br.adjusted(16, 0, -2, 0), Qt.AlignVCenter | Qt.AlignLeft, txt)
            tri = [br.topLeft() + QPoint(5, 4), br.topLeft() + QPoint(5, 12), br.topLeft() + QPoint(12, 8)]
            from PySide6.QtGui import QPolygonF
            p.setBrush(Qt.white)
            p.drawPolygon(QPolygonF([QPoint(int(t.x()), int(t.y())) for t in tri]))
        elif row["kind"] == "raw":
            br = QRectF(img_rect.right() - 34, img_rect.top() + 4, 30, 14)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 140))
            p.drawRoundedRect(br, 3, 3)
            f = theme.scaled_font(p.font(), 0.7)
            f.setBold(True)
            p.setFont(f)
            p.setPen(Qt.white)
            p.drawText(br, Qt.AlignCenter, "RAW")
        keys = row.keys() if hasattr(row, "keys") else ()
        if "stack_count" in keys and (row["stack_count"] or 0) > 1 and row["stack_kind"] != "live":
            txt = str(row["stack_count"])
            f = theme.scaled_font(p.font(), 0.75)
            f.setBold(True)
            p.setFont(f)
            w = p.fontMetrics().horizontalAdvance(txt) + 22
            br = QRectF(img_rect.right() - w - 4, img_rect.top() + 4, w, 16)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 150))
            p.drawRoundedRect(br, 8, 8)
            theme.icon("stack", QColor("#ffffff")).paint(p, QRect(int(br.left()) + 4, int(br.top()) + 2, 12, 12))
            p.setPen(Qt.white)
            p.drawText(br.adjusted(16, 0, -4, 0), Qt.AlignCenter, txt)
        if "live_video" in keys and row["live_video"]:
            f = theme.scaled_font(p.font(), 0.7)
            f.setBold(True)
            p.setFont(f)
            br = QRectF(img_rect.left() + 4, img_rect.top() + 4, 44, 16)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 140))
            p.drawRoundedRect(br, 8, 8)
            theme.icon("live", QColor("#ffffff")).paint(p, QRect(int(br.left()) + 4, int(br.top()) + 2, 12, 12))
            p.setPen(Qt.white)
            p.drawText(br.adjusted(16, 0, -3, 0), Qt.AlignVCenter | Qt.AlignLeft, "LIVE")
        if row["id"] in self.held:
            pin = QRectF(img_rect.left() + 4, img_rect.top() + 4, 16, 16)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 130))
            p.drawEllipse(pin)
            p.drawPixmap(pin.adjusted(2, 2, -2, -2).toRect(), theme.icon("hold", QColor("#ffffff")).pixmap(12, 12))
        if row["filters"]:
            dot = QRectF(img_rect.right() - 12, img_rect.bottom() - 12, 7, 7)
            if row["kind"] != "video":
                p.setPen(QPen(QColor(255, 255, 255, 200), 1))
                p.setBrush(theme.ACCENT)
                p.drawEllipse(dot)


class PhotoGrid(QListView):
    activated_photo = Signal(int)            # double click / enter
    context_requested = Signal(QPoint)
    selection_changed_ids = Signal(list)
    reordered = Signal(list)                 # new id order (albums)
    header_clicked = Signal(object)          # header dict

    def __init__(self, loader, parent=None):
        super().__init__(parent)
        self.setObjectName("Grid")
        self.loader = loader
        self.model_ = PhotoGridModel(self)
        self.setModel(self.model_)
        self.delegate = GridDelegate(self, loader)
        self.setItemDelegate(self.delegate)
        self.setViewMode(QListView.IconMode)
        self.setResizeMode(QListView.Adjust)
        self.setMovement(QListView.Static)
        self.setUniformItemSizes(False)
        self.setSpacing(2)
        self.setLayoutMode(QListView.Batched)
        self.setBatchSize(400)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragOnly)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.verticalScrollBar().setSingleStep(24)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self.context_requested)
        self.doubleClicked.connect(self._dbl)
        self.selectionModel().selectionChanged.connect(self._sel)
        loader.loaded.connect(self._thumb_ready)
        self._relayout = QTimer(self, singleShot=True, interval=60, timeout=self.doItemsLayout)
        self.reorderable = False
        self.setAcceptDrops(True)
        self.clicked.connect(self._clicked)

    def _clicked(self, index):
        kind, obj = index.data(ROLE_ENTRY)
        if kind == "header":
            # Picasa: clicking a folder header selects every picture in the folder
            sm = self.selectionModel()
            sm.clearSelection()
            i = index.row() + 1
            while i < len(self.model_.entries) and self.model_.entries[i][0] == "photo":
                sm.select(self.model_.index(i), sm.SelectionFlag.Select)
                i += 1
            self.header_clicked.emit(obj)

    def set_reorderable(self, on: bool):
        self.reorderable = on

    def dragEnterEvent(self, e):
        if self.reorderable and e.source() is self and e.mimeData().hasFormat(MIME_IDS):
            e.acceptProposedAction()
        else:
            super().dragEnterEvent(e)

    def dragMoveEvent(self, e):
        if self.reorderable and e.source() is self:
            e.acceptProposedAction()
        else:
            super().dragMoveEvent(e)

    def dropEvent(self, e):
        if not (self.reorderable and e.source() is self):
            return super().dropEvent(e)
        ids = json.loads(bytes(e.mimeData().data(MIME_IDS)).decode())
        target = self.indexAt(e.position().toPoint())
        order = [r["id"] for r in self.model_.photo_rows() if r["id"] not in ids]
        if target.isValid():
            kind, obj = self.model_.entries[target.row()]
            pos = order.index(obj["id"]) if kind == "photo" and obj["id"] in order else len(order)
        else:
            pos = len(order)
        order[pos:pos] = ids
        e.acceptProposedAction()
        self.reordered.emit(order)

    def set_thumb_size(self, s: int):
        self.delegate.size = s
        self.doItemsLayout()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._relayout.start()

    def _thumb_ready(self, pid):
        i = self.model_.row_of.get(pid)
        if i is not None:
            self.update(self.model_.index(i))

    def _dbl(self, index):
        kind, obj = index.data(ROLE_ENTRY)
        if kind == "photo":
            self.activated_photo.emit(obj["id"])

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter):
            ids = self.selected_ids()
            if ids:
                self.activated_photo.emit(ids[0])
                return
        super().keyPressEvent(e)

    def _sel(self, *_):
        self.selection_changed_ids.emit(self.selected_ids())

    def selected_ids(self) -> list[int]:
        idx = sorted(self.selectionModel().selectedIndexes(), key=lambda i: i.row())
        out = []
        for i in idx:
            kind, obj = self.model_.entries[i.row()]
            if kind == "photo":
                out.append(obj["id"])
        return out

    def selected_rows(self):
        idx = sorted(self.selectionModel().selectedIndexes(), key=lambda i: i.row())
        return [self.model_.entries[i.row()][1] for i in idx if self.model_.entries[i.row()][0] == "photo"]

    def select_ids(self, ids: list[int]):
        sm = self.selectionModel()
        sm.clearSelection()
        first = None
        for pid in ids:
            i = self.model_.row_of.get(pid)
            if i is not None:
                idx = self.model_.index(i)
                sm.select(idx, sm.SelectionFlag.Select)
                if first is None:
                    first = idx
        if first is not None:
            sm.setCurrentIndex(first, sm.SelectionFlag.NoUpdate)
            self.scrollTo(first)

    def startDrag(self, actions):
        idx = self.selectedIndexes()
        if not idx:
            return
        md = self.model_.mimeData(idx)
        drag = QDrag(self)
        drag.setMimeData(md)
        kind, obj = self.model_.entries[idx[0].row()]
        pm = self.loader.get(obj) if kind == "photo" else None
        if pm is not None:
            drag.setPixmap(pm.scaled(96, 96, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        drag.exec(Qt.CopyAction | Qt.MoveAction, Qt.CopyAction)
