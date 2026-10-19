"""Right-hand side panels: Properties, Tags (with Quick Tags), People and Places — like Picasa's panels."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QCompleter, QGridLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QPushButton, QTabWidget, QToolButton, QVBoxLayout, QWidget)

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


class RightPanel(QTabWidget):
    """Properties / Tags / People / Places, switchable like Picasa's panel buttons."""

    def __init__(self, lib, loader, parent=None):
        super().__init__(parent)
        self.setObjectName("InfoPanel")
        self.setMinimumWidth(270)
        self.setMaximumWidth(360)
        self.info = InfoPanel(lib)
        self.tags = TagsPanel(lib)
        self.people = SelectionPeoplePanel(lib)
        self.places = PlacesPanel(lib, loader)
        self.addTab(self.info, "Info")
        self.addTab(self.tags, "Tags")
        self.addTab(self.people, "People")
        self.addTab(self.places, "Places")
        self.ids: list[int] = []
        self.currentChanged.connect(lambda _: self._push())

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
