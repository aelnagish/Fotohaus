"""Right-hand side panels: Properties, Tags (with Quick Tags), People and Places — like Picasa's panels."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QCompleter, QGridLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QPushButton, QSizePolicy, QStackedWidget, QToolButton, QVBoxLayout,
                               QWidget)

from . import theme
from .infopanel import InfoPanel


def _sec(text):
    l = QLabel(text.upper())
    l.setObjectName("Section")
    return l


class TagsPanel(QWidget):
    def __init__(self, lib, parent=None):
        super().__init__(parent)
        self.lib = lib
        self.ids: list[int] = []
        v = QVBoxLayout(self)
        v.setContentsMargins(12, 10, 12, 10)
        self.head = QLabel("No selection")
        self.head.setObjectName("Muted")
        v.addWidget(self.head)
        row = QHBoxLayout()
        self.entry = QLineEdit()
        self.entry.setPlaceholderText("Type a tag and press Return")
        self.entry.returnPressed.connect(self._add)
        row.addWidget(self.entry)
        add = QToolButton()
        add.setIcon(theme.icon("plus"))
        add.clicked.connect(self._add)
        row.addWidget(add)
        v.addLayout(row)
        v.addWidget(_sec("Tags on selected pictures"))
        self.list = QListWidget()
        self.list.setMaximumHeight(200)
        v.addWidget(self.list)
        rm = QPushButton("Remove Selected Tag")
        rm.clicked.connect(self._remove)
        v.addWidget(rm, 0, Qt.AlignLeft)
        qt_row = QHBoxLayout()
        qt_row.addWidget(_sec("Quick tags"))
        qt_row.addStretch()
        cfg = QToolButton()
        cfg.setText("Configure…")
        cfg.setAutoRaise(True)
        cfg.clicked.connect(self._configure)
        qt_row.addWidget(cfg)
        v.addLayout(qt_row)
        self.quick = QGridLayout()
        v.addLayout(self.quick)
        v.addStretch()
        self._build_quick()

    def _build_quick(self):
        while self.quick.count():
            w = self.quick.takeAt(0).widget()
            if w:
                w.deleteLater()
        for i, tag in enumerate((self.lib.settings.get("quick_tags") or [])[:10]):
            b = QPushButton(tag)
            b.setToolTip(f"Tag selected pictures “{tag}” (click again to remove)")
            b.clicked.connect(lambda _=False, t=tag: self._toggle(t))
            self.quick.addWidget(b, i // 2, i % 2)

    def _configure(self):
        cur = ", ".join(self.lib.settings.get("quick_tags") or [])
        text, ok = QInputDialog.getText(self, "Quick Tags", "Up to 10 tags, separated by commas:", text=cur)
        if ok:
            self.lib.settings.set("quick_tags", [t.strip() for t in text.split(",") if t.strip()][:10])
            self._build_quick()

    def set_ids(self, ids):
        self.ids = list(ids)
        self.refresh()

    def refresh(self):
        self.list.clear()
        rows = self.lib.photos_by_ids(self.ids)
        self.head.setText(f"{len(rows)} picture{'s' if len(rows) != 1 else ''} selected" if rows else "No selection")
        counts: dict[str, int] = {}
        for r in rows:
            for k in (r["keywords"] or "").split(","):
                if k:
                    counts[k] = counts.get(k, 0) + 1
        for k, n in sorted(counts.items(), key=lambda x: (-x[1], x[0].lower())):
            it = QListWidgetItem(f"{k}" + (f"   ({n} of {len(rows)})" if n != len(rows) else ""))
            it.setData(Qt.UserRole, k)
            self.list.addItem(it)
        names = [k for k, _ in self.lib.all_keywords()]
        comp = QCompleter(names, self)
        comp.setCaseSensitivity(Qt.CaseInsensitive)
        self.entry.setCompleter(comp)

    def _add(self):
        t = self.entry.text().strip()
        if t and self.ids:
            self.lib.add_keyword(self.ids, t)
            self.entry.clear()
            self.refresh()

    def _toggle(self, tag):
        if not self.ids:
            return
        rows = self.lib.photos_by_ids(self.ids)
        if all(tag.lower() in [k.lower() for k in (r["keywords"] or "").split(",")] for r in rows):
            self.lib.remove_keyword(self.ids, tag)
        else:
            self.lib.add_keyword(self.ids, tag)
        self.refresh()

    def _remove(self):
        it = self.list.currentItem()
        if it and self.ids:
            self.lib.remove_keyword(self.ids, it.data(Qt.UserRole))
            self.refresh()


class SelectionPeoplePanel(QWidget):
    person_clicked = Signal(int)
    add_person = Signal(int)

    def __init__(self, lib, parent=None):
        super().__init__(parent)
        from .people import FaceCrops
        self.lib = lib
        self.crops = FaceCrops(self)
        self.crops.ready.connect(lambda fid: self._ready(fid))
        self.ids: list[int] = []
        v = QVBoxLayout(self)
        v.setContentsMargins(12, 10, 12, 10)
        self.head = QLabel()
        self.head.setObjectName("Muted")
        v.addWidget(self.head)
        self.list = QListWidget()
        self.list.setIconSize(QSize(48, 48))
        self.list.itemDoubleClicked.connect(self._name)
        v.addWidget(self.list, 1)
        row = QHBoxLayout()
        nb = QPushButton("Name…")
        nb.clicked.connect(lambda: self.list.currentItem() and self._name(self.list.currentItem()))
        ig = QPushButton("Not a Face")
        ig.clicked.connect(self._ignore)
        row.addWidget(nb)
        row.addWidget(ig)
        v.addLayout(row)
        self.add_btn = QPushButton("Add a Person Manually…")
        self.add_btn.clicked.connect(lambda: self.ids and self.add_person.emit(self.ids[0]))
        v.addWidget(self.add_btn)
        self.items: dict[int, QListWidgetItem] = {}

    def set_ids(self, ids):
        self.ids = list(ids)[:200]
        self.refresh()

    def refresh(self):
        self.list.clear()
        self.items.clear()
        n = 0
        for pid in self.ids:
            r = self.lib.photo(pid)
            for f in self.lib.faces_for_photo(pid):
                d = dict(f)
                d["path"] = r["path"]
                label = (f["person_name"] or "Unnamed") + ("" if f["confirmed"] or not f["person_id"] else "  ?")
                it = QListWidgetItem(label)
                it.setData(Qt.UserRole, d)
                img = self.crops.get(d)
                if img is not None:
                    from .people import round_pixmap
                    it.setIcon(QIcon(round_pixmap(img, 48)))
                self.list.addItem(it)
                self.items[f["id"]] = it
                n += 1
        self.head.setText(f"{n} face{'s' if n != 1 else ''} in {len(self.ids)} picture{'s' if len(self.ids) != 1 else ''}"
                          if self.ids else "No selection")
        self.add_btn.setEnabled(len(self.ids) == 1)

    def _ready(self, fid):
        it = self.items.get(fid)
        img = self.crops.cache.get(fid)
        if it is not None and img is not None:
            from .people import round_pixmap
            it.setIcon(QIcon(round_pixmap(img, 48)))

    def _name(self, it):
        d = it.data(Qt.UserRole)
        names = [p["name"] for p in self.lib.people()]
        name, ok = QInputDialog.getItem(self, "Name this person", "Name:", names, -1, True)
        if ok and name.strip():
            self.lib.name_faces([d["id"]], name.strip())
            self.refresh()

    def _ignore(self):
        it = self.list.currentItem()
        if it:
            self.lib.ignore_faces([it.data(Qt.UserRole)["id"]])
            self.refresh()


class PlacesPanel(QWidget):
    def __init__(self, lib, loader, parent=None):
        super().__init__(parent)
        from .mapview import MapWidget
        self.lib = lib
        self.ids: list[int] = []
        v = QVBoxLayout(self)
        v.setContentsMargins(6, 6, 6, 6)
        self.head = QLabel()
        self.head.setObjectName("Muted")
        v.addWidget(self.head)
        self.map = MapWidget(loader)
        self.map.location_picked.connect(self._picked)
        v.addWidget(self.map, 1)
        row = QHBoxLayout()
        self.place = QPushButton("Place Here…")
        self.place.setToolTip("Click, then click the map to set the location of the selected pictures")
        self.place.clicked.connect(self._start)
        self.clear = QPushButton("Remove Location")
        self.clear.clicked.connect(lambda: (self.lib.set_location(self.ids, None, None, write_exif=False),
                                            self.refresh()))
        row.addWidget(self.place)
        row.addWidget(self.clear)
        v.addLayout(row)

    def set_ids(self, ids):
        self.ids = list(ids)
        self.refresh()

    def refresh(self):
        self.map.enabled_tiles = bool(self.lib.settings.get("map_tiles"))
        rows = [r for r in self.lib.photos_by_ids(self.ids) if r["lat"] is not None]
        self.map.set_points(rows if rows else self.lib.geotagged())
        if rows:
            self.map.fit_points()
        n = len(rows)
        self.head.setText(f"{n} of {len(self.ids)} selected pictures have a location" if self.ids else
                          "All geotagged pictures")
        self.place.setEnabled(bool(self.ids))
        self.clear.setEnabled(n > 0)

    def _start(self):
        self.map.pick_mode = True
        self.map.update()

    def _picked(self, lat, lon):
        self.lib.set_location(self.ids, lat, lon)
        self.refresh()


class Segmented(QWidget):
    """A macOS-style segmented control on a rounded track.
    fit=True sizes each segment to its text; accent=True fills the current segment with the accent colour."""
    changed = Signal(int)

    def __init__(self, labels, parent=None, fit=False, accent=False):
        super().__init__(parent)
        self.labels = list(labels)
        self.fit, self.accent = fit, accent
        self.index = 0
        self._hover = -1
        self.setMouseTracking(True)
        self.setFixedHeight(28)
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Fixed if fit else QSizePolicy.Expanding, QSizePolicy.Fixed)

    def _widths(self):
        fm = self.fontMetrics()
        return [fm.horizontalAdvance(lab) + 26 for lab in self.labels]

    def sizeHint(self):
        from PySide6.QtCore import QSize
        return QSize(sum(self._widths()) + 4, 28) if self.fit else QSize(240, 28)

    def _rects(self):
        from PySide6.QtCore import QRectF
        track = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        n = len(self.labels)
        if self.fit:
            ws = self._widths()
            scale = (track.width() - 4) / max(1, sum(ws))
            ws = [w * scale for w in ws]
        else:
            ws = [(track.width() - 4) / max(1, n)] * n
        out, x = [], track.left() + 2
        for w in ws:
            out.append(QRectF(x, track.top() + 2, w, track.height() - 4))
            x += w
        return track, out

    def set_index(self, i):
        if 0 <= i < len(self.labels) and i != self.index:
            self.index = i
            self.update()
            self.changed.emit(i)

    def _seg_at(self, x):
        _t, rects = self._rects()
        for i, r in enumerate(rects):
            if x < r.right():
                return i
        return len(rects) - 1

    def mousePressEvent(self, e):
        self.set_index(self._seg_at(e.position().x()))

    def mouseMoveEvent(self, e):
        h = self._seg_at(e.position().x())
        if h != self._hover:
            self._hover = h
            self.update()

    def leaveEvent(self, e):
        self._hover = -1
        self.update()

    def paintEvent(self, e):
        from PySide6.QtGui import QColor, QPainter
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        dark = theme.is_dark()
        track, rects = self._rects()
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#2f3034" if dark else "#e4e6ea"))
        p.drawRoundedRect(track, 8, 8)
        p.setPen(QColor("#45464b" if dark else "#cfd2d8"))
        for i in range(1, len(rects)):
            if i not in (self.index, self.index + 1):
                x = rects[i].left()
                p.drawLine(int(x), int(track.top() + 7), int(x), int(track.bottom() - 7))
        if 0 <= self._hover < len(rects) and self._hover != self.index:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, 18) if dark else QColor(0, 0, 0, 12))
            p.drawRoundedRect(rects[self._hover], 6, 6)
        sel = rects[self.index]
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 30))
        p.drawRoundedRect(sel.translated(0, 0.8), 6, 6)
        p.setBrush(theme.ACCENT if self.accent else QColor("#5a5b60" if dark else "#ffffff"))
        p.drawRoundedRect(sel, 6, 6)
        f = self.font()
        for i, (lab, r) in enumerate(zip(self.labels, rects)):
            f.setBold(i == self.index and not self.accent)
            p.setFont(f)
            p.setPen(QColor("white") if (self.accent and i == self.index) else theme.fg())
            p.drawText(r, Qt.AlignCenter, lab)
        p.end()


class RightPanel(QWidget):
    """Info / Tags / People / Places, switched with a segmented control like Picasa's panel buttons."""
    currentChanged = Signal(int)

    def __init__(self, lib, loader, parent=None):
        super().__init__(parent)
        self.setObjectName("RightPanel")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setMinimumWidth(270)
        self.setMaximumWidth(360)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        self.seg = Segmented(["Info", "Tags", "People", "Places"])
        holder = QWidget()
        hl = QHBoxLayout(holder)
        hl.setContentsMargins(12, 10, 12, 4)
        hl.addWidget(self.seg)
        v.addWidget(holder)
        self.stack = QStackedWidget()
        v.addWidget(self.stack, 1)
        self.info = InfoPanel(lib, loader)
        self.tags = TagsPanel(lib)
        self.people = SelectionPeoplePanel(lib)
        self.places = PlacesPanel(lib, loader)
        for w in (self.info, self.tags, self.people, self.places):
            self.stack.addWidget(w)
        self.seg.changed.connect(self.setCurrentIndex)
        self.ids: list[int] = []

    # QTabWidget-compatible bits used by the main window
    def setCurrentIndex(self, i):
        if self.stack.currentIndex() != i:
            self.stack.setCurrentIndex(i)
            self.seg.set_index(i)
            self.currentChanged.emit(i)
            self._push()

    def currentIndex(self):
        return self.stack.currentIndex()

    def currentWidget(self):
        return self.stack.currentWidget()

    def set_ids(self, ids):
        self.ids = list(ids)
        if len(self.ids) == 1:
            self.info.show_photo(self.ids[0])
        else:
            self.info.show_multi(self.ids)
        self._push()

    def _push(self):
        w = self.currentWidget()
        if w in (self.tags, self.people, self.places):
            w.set_ids(self.ids)

    def refresh(self):
        self.info.refresh()
        self._push()
