"""Left sidebar: library collections, albums, people shortcuts and folders grouped by year."""
from __future__ import annotations

import datetime as _dt
import json

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QAbstractItemView, QTreeWidget, QTreeWidgetItem

from . import theme
from .grid import MIME_IDS

ROLE = Qt.UserRole + 7


def _sidebar_icon(name: str):
    """Follows the theme, and turns white on the blue (accent) selection."""
    from PySide6.QtGui import QColor
    return theme.icon(name, selected=QColor("white"))


class Sidebar(QTreeWidget):
    selected = Signal(str, object)          # kind, id
    dropped_on_album = Signal(int, list)    # album id, photo ids
    dropped_on_new_album = Signal(list)
    dropped_on_folder = Signal(int, list, bool)   # folder id, photo ids, copy?
    context = Signal(str, object, object)   # kind, id, global pos

    def __init__(self, lib, parent=None):
        super().__init__(parent)
        self.lib = lib
        self.setObjectName("Sidebar")
        self.setHeaderHidden(True)
        self.setIndentation(12)
        self.setRootIsDecorated(False)
        self.setUniformRowHeights(True)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DropOnly)
        self.setDropIndicatorShown(True)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._ctx)
        self.itemSelectionChanged.connect(self._sel)
        self.setMinimumWidth(200)
        self._suppress = False
        self._expanded_years: set[str] = set()
        self.itemExpanded.connect(lambda it: self._expanded_years.add(it.text(0)))
        self.itemCollapsed.connect(lambda it: self._expanded_years.discard(it.text(0)))

    def _section(self, title: str) -> QTreeWidgetItem:
        it = QTreeWidgetItem([title.upper()])
        f = theme.scaled_font(self.font(), 0.8)
        f.setBold(True)
        it.setFont(0, f)
        it.setFlags(Qt.ItemIsEnabled)
        it.setForeground(0, QColor("#8c8f96"))     # readable in light and dark
        self.addTopLevelItem(it)
        it.setExpanded(True)
        return it

    def _item(self, parent, text, kind, ident, icon_name=None, count=None):
        label = text if count is None else f"{text}"
        it = QTreeWidgetItem([label])
        it.setData(0, ROLE, (kind, ident))
        if count is not None:
            it.setToolTip(0, f"{text} — {count} item{'s' if count != 1 else ''}")
            it.setText(0, f"{text}  ")
            it.setData(0, Qt.UserRole + 8, count)
        if icon_name:
            it.setIcon(0, _sidebar_icon(icon_name))
        parent.addChild(it)
        return it

    def rebuild(self):
        cur = self.current()
        self._suppress = True
        self.clear()
        c = self.lib.counts()
        sec = self._section("Library")
        self._item(sec, "All Photos", "all", None, "library")
        if c["memories"]:
            self._item(sec, "Memories", "memories", None, "memories", c["memories"])
        self._item(sec, "Starred", "starred", None, "star-outline", c["starred"])
        if c["videos"]:
            self._item(sec, "Videos", "videos", None, "video", c["videos"])
        self._item(sec, "Recently Added", "recent", None, "clock")
        self._item(sec, "Places", "places", None, "pin", c["geotagged"])
        self._item(sec, "Timeline", "timeline", None, "clock")
        self._item(sec, "Duplicates", "dupes", None, "dupes")
        if c["hidden"]:
            self._item(sec, "Hidden", "hidden", None, "eye-off", c["hidden"])
        if c["archived"]:
            self._item(sec, "Archive", "archived", None, "archive", c["archived"])
        if c["private"]:
            self._item(sec, "Private", "private", None, "lock")
        if c["deleted"]:
            self._item(sec, "Recently Deleted", "deleted", None, "trash", c["deleted"])
        sec = self._section("Albums")
        for a in self.lib.albums():
            it = self._item(sec, a["name"], "album", a["id"], "album", a["n"])
            it.setFlags(it.flags() | Qt.ItemIsDropEnabled)
        newit = self._item(sec, "New Album…", "new_album", None, "plus")
        newit.setFlags(newit.flags() | Qt.ItemIsDropEnabled)
        sec = self._section("People")
        self._item(sec, "All People", "people", None, "people", c["people"])
        order = self.lib.settings.get("people_sort") or "name"
        people = [p for p in self.lib.people() if not p["hidden"] and p["n"]]
        if order == "amount":
            people.sort(key=lambda p: -p["n"])
        elif order == "top10":
            people = sorted(people, key=lambda p: -p["n"])[:10]
        for p in people:
            self._item(sec, p["name"], "person", p["id"], None, p["n"])
        sec = self._section("Folders")
        if (self.lib.settings.get("folder_view") or "flat") == "tree":
            self._build_tree(sec)
        else:
            by_year: dict[str, list] = {}
            for f in self.lib.folders():
                y = _dt.datetime.fromtimestamp(f["date"]).strftime("%Y") if f["date"] else "Undated"
                by_year.setdefault(y, []).append(f)
            keys = list(by_year)
            for i, y in enumerate(keys):
                yi = QTreeWidgetItem([y])
                yi.setFlags(Qt.ItemIsEnabled)
                yi.setData(0, ROLE, ("year", y))
                sec.addChild(yi)
                for f in by_year[y]:
                    self._folder_item(yi, f)
                yi.setExpanded(y in self._expanded_years or i < 2)
        self._suppress = False
        if cur:
            self.select(*cur)

    def _folder_item(self, parent, f, label=None):
        n = f["shown"] if "shown" in f.keys() else f["count"]
        it = self._item(parent, label or f["name"], "folder", f["id"], "folder", n)
        it.setFlags(it.flags() | Qt.ItemIsDropEnabled)
        if f["hidden"]:
            it.setForeground(0, QColor("#8c8f96"))     # readable in light and dark
            it.setToolTip(0, "Hidden folder")
        return it

    def _build_tree(self, sec):
        import os
        folders = {f["path"]: f for f in self.lib.folders()}
        roots = [r for r in self.lib.roots() if r["kind"] != "exclude"]
        nodes: dict[str, QTreeWidgetItem] = {}

        def node_for(path, root_path, root_item):
            if path == root_path:
                return root_item
            if path in nodes:
                return nodes[path]
            parent = node_for(os.path.dirname(path), root_path, root_item)
            f = folders.get(path)
            if f is not None:
                it = self._folder_item(parent, f, os.path.basename(path))
            else:
                it = QTreeWidgetItem([os.path.basename(path)])
                it.setFlags(Qt.ItemIsEnabled)
                it.setIcon(0, _sidebar_icon("folder"))
                parent.addChild(it)
            nodes[path] = it
            return it
        for r in sorted(roots, key=lambda r: r["path"].lower()):
            rp = r["path"].rstrip(os.sep)
            f = folders.get(rp)
            if f is not None:
                ri = self._folder_item(sec, f, os.path.basename(rp) or rp)
            else:
                ri = QTreeWidgetItem([os.path.basename(rp) or rp])
                ri.setFlags(Qt.ItemIsEnabled)
                ri.setIcon(0, _sidebar_icon("folder"))
                sec.addChild(ri)
            ri.setToolTip(0, rp)
            for path in sorted(p for p in folders if p.startswith(rp + os.sep)):
                node_for(path, rp, ri)
            ri.setExpanded(True)

    def current(self):
        it = self.currentItem()
        return it.data(0, ROLE) if it else None

    def select(self, kind, ident=None):
        it = self._find(kind, ident)
        if it:
            self._suppress = True
            self.setCurrentItem(it)
            self._suppress = False

    def _find(self, kind, ident):
        stack = [self.topLevelItem(i) for i in range(self.topLevelItemCount())]
        while stack:
            it = stack.pop()
            d = it.data(0, ROLE)
            if d and d[0] == kind and d[1] == ident:
                return it
            stack.extend(it.child(i) for i in range(it.childCount()))
        return None

    def _sel(self):
        if self._suppress:
            return
        d = self.current()
        if d and d[0] != "year":
            self.selected.emit(d[0], d[1])

    def _ctx(self, pos):
        it = self.itemAt(pos)
        if it and it.data(0, ROLE):
            k, i = it.data(0, ROLE)
            self.context.emit(k, i, self.viewport().mapToGlobal(pos))

    def drawRow(self, painter, option, index):
        # One clean rounded highlight across the whole row (like Finder's sidebar) instead of the
        # style's default, which leaves the indent area square and clips the rounded corner.
        if self.selectionModel().isSelected(index):
            from PySide6.QtCore import QRectF
            painter.save()
            painter.setRenderHint(painter.RenderHint.Antialiasing)
            painter.setPen(Qt.NoPen)
            col = theme.ACCENT if self.hasFocus() or self.window().isActiveWindow() else theme.ACCENT.lighter(135)
            painter.setBrush(col)
            r = option.rect
            painter.drawRoundedRect(QRectF(6, r.top() + 1, self.viewport().width() - 12, r.height() - 2), 6, 6)
            painter.restore()
        super().drawRow(painter, option, index)

    def paintEvent(self, e):
        super().paintEvent(e)
        # draw counts right-aligned (muted)
        from PySide6.QtGui import QPainter, QColor
        p = QPainter(self.viewport())
        col = QColor(self.palette().text().color())
        col.setAlpha(110)
        p.setPen(col)
        f = theme.scaled_font(self.font(), 0.85)
        p.setFont(f)
        it = self.itemAt(0, 0)
        while it is not None:
            n = it.data(0, Qt.UserRole + 8)
            if n is not None:
                r = self.visualItemRect(it)
                if not it.isSelected():
                    p.drawText(r.adjusted(0, 0, -10, 0), Qt.AlignRight | Qt.AlignVCenter, str(n))
            it = self.itemBelow(it)
            if it is not None and self.visualItemRect(it).top() > self.viewport().height():
                break
        p.end()

    # -- drag & drop onto albums
    def dragEnterEvent(self, e):
        if e.mimeData().hasFormat(MIME_IDS):
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragMoveEvent(self, e):
        it = self.itemAt(e.position().toPoint())
        d = it.data(0, ROLE) if it else None
        if d and d[0] in ("album", "new_album", "folder") and e.mimeData().hasFormat(MIME_IDS):
            e.acceptProposedAction()
        else:
            e.ignore()

    def dropEvent(self, e):
        it = self.itemAt(e.position().toPoint())
        d = it.data(0, ROLE) if it else None
        if not d:
            return
        ids = json.loads(bytes(e.mimeData().data(MIME_IDS)).decode())
        if d[0] == "album":
            self.dropped_on_album.emit(d[1], ids)
        elif d[0] == "new_album":
            self.dropped_on_new_album.emit(ids)
        elif d[0] == "folder":
            copy = bool(e.modifiers() & Qt.AltModifier)
            self.dropped_on_folder.emit(d[1], ids, copy)
        e.acceptProposedAction()
