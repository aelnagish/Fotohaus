"""Tools: Settings, Folder Manager, Import, Back Up Pictures."""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QDir, QIdentityProxyModel, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QFileDialog, QFileSystemModel, QFormLayout, QGridLayout, QHBoxLayout,
                               QLabel, QLineEdit, QListView, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
                               QFrame, QRadioButton, QScrollArea, QSpinBox, QSplitter, QStackedWidget, QTreeView, QVBoxLayout, QWidget)

from .. import macos, media
from ..ai import clip, faces, vision_mac
from ..paths import DATA_DIR
from . import theme
from .dialogs import default_pictures

BUTTONS = [("share", "Share"), ("email", "Email"), ("print", "Print"), ("export", "Export"),
           ("collage", "Collage"), ("movie", "Movie"), ("geotag", "Geo-Tag"), ("html", "Web Page"),
           ("desktop", "Set as Desktop")]


# ====================================================================== Options

class Switch(QCheckBox):
    """A macOS-style on/off switch with the QCheckBox API (isChecked / setChecked / toggled)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(40, 22)

    def sizeHint(self):
        return QSize(40, 22)

    def hitButton(self, pos):
        return self.rect().contains(pos)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        on, en = self.isChecked(), self.isEnabled()
        track = theme.ACCENT if on else QColor("#4a4b50" if theme.is_dark() else "#d5d7db")
        if not en:
            track = QColor(track)
            track.setAlpha(110)
        p.setPen(Qt.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(QRectF(0, 1, 40, 20), 10, 10)
        knob = QColor("#ffffff")
        if not en:
            knob.setAlpha(190)
        p.setBrush(QColor(0, 0, 0, 40))
        x = 20.5 if on else 2.5
        p.drawEllipse(QRectF(x, 3.5, 17, 17))
        p.setBrush(knob)
        p.drawEllipse(QRectF(x, 2.5, 17, 17))
        p.end()


def _badge(name: str, color: str) -> QIcon:
    """Settings-style icon: a white glyph on a coloured rounded square."""
    ic = QIcon()
    for size in (28, 56):
        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(color))
        p.drawRoundedRect(QRectF(0, 0, size, size), size * 0.24, size * 0.24)
        g = theme.icon(name, QColor("white")).pixmap(int(size * 0.7), int(size * 0.7))
        g.setDevicePixelRatio(1.0)
        off = (size - g.width()) // 2
        p.drawPixmap(off, off, g)
        p.end()
        ic.addPixmap(pm)
    return ic


def _check_images() -> tuple[str, str]:
    """Checkbox indicator images for list rows (stylesheets can only use image files)."""
    import tempfile
    d = Path(tempfile.gettempdir()) / "fotohaus-ui"
    d.mkdir(exist_ok=True)
    dark = theme.is_dark()
    paths = []
    for checked in (False, True):
        pm = QPixmap(36, 36)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(3, 3, 30, 30)
        if checked:
            p.setPen(Qt.NoPen)
            p.setBrush(theme.ACCENT)
            p.drawRoundedRect(r, 7, 7)
            pen = QPen(QColor("white"), 4, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
            p.setPen(pen)
            p.drawPolyline([QPointF(10, 18.5), QPointF(15.5, 24), QPointF(26, 12)])
        else:
            p.setPen(QPen(QColor("#6a6c72" if dark else "#b9bcc3"), 2))
            p.setBrush(QColor("#2a2b2f" if dark else "#ffffff"))
            p.drawRoundedRect(r, 7, 7)
        p.end()
        f = d / f"check_{int(checked)}_{int(dark)}.png"
        pm.save(str(f))
        paths.append(f.as_posix())
    return paths[0], paths[1]


def _prefs_style() -> str:
    off, on = _check_images()
    dark = theme.is_dark()
    nav_bg = "#26272b" if dark else "#f1f2f5"
    page_bg = "#1e1f22" if dark else "#f7f8fa"
    card = "#2a2b2f" if dark else "#ffffff"
    border = "#3a3b40" if dark else "#e3e5ea"
    sel = "#3a3c42" if dark else "#e0e3e9"
    sec = "#8e9199" if dark else "#7a7e87"
    return f"""
    QDialog#Prefs {{ background: {page_bg}; }}
    QListWidget#PrefsNav {{ background: {nav_bg}; border: none; border-right: 1px solid {border};
                            padding: 14px 10px; outline: 0; font-size: 13px; }}
    QListWidget#PrefsNav::item {{ padding: 6px 8px; margin: 1px 0; border-radius: 7px; }}
    QListWidget#PrefsNav::item:selected {{ background: {sel}; color: palette(text); }}
    QListWidget#PrefsNav::item:hover:!selected {{ background: {sel}; }}
    QScrollArea#PrefsScroll, QWidget#PrefsPage {{ background: {page_bg}; border: none; }}
    QLabel#PrefsTitle {{ font-size: 22px; font-weight: 700; }}
    QLabel#PrefsSubtitle {{ color: {sec}; font-size: 12px; }}
    QLabel#PrefsGroup {{ color: {sec}; font-size: 11px; font-weight: 600; letter-spacing: 0.4px;
                         padding: 0 4px; }}
    QFrame#PrefsCard {{ background: {card}; border: 1px solid {border}; border-radius: 10px; }}
    QFrame#PrefsSep {{ background: {border}; max-height: 1px; min-height: 1px; border: none; }}
    QLabel#PrefsRowTitle {{ font-size: 13px; }}
    QLabel#PrefsRowDesc {{ color: {sec}; font-size: 11px; }}
    QWidget#PrefsFooter {{ background: {page_bg}; border-top: 1px solid {border}; }}
    QListWidget#PrefsList {{ background: transparent; border: none; outline: 0; }}
    QListWidget#PrefsList::indicator {{ width: 18px; height: 18px; }}
    QListWidget#PrefsList::indicator:unchecked {{ image: url({off}); }}
    QListWidget#PrefsList::indicator:checked {{ image: url({on}); }}
    """


class _Page(QScrollArea):
    """One settings page: a large title, a subtitle and stacked cards of rows."""

    def __init__(self, title: str, subtitle: str):
        super().__init__()
        self.setObjectName("PrefsScroll")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        body = QWidget()
        body.setObjectName("PrefsPage")
        self.v = QVBoxLayout(body)
        self.v.setContentsMargins(28, 24, 28, 24)
        self.v.setSpacing(6)
        t = QLabel(title)
        t.setObjectName("PrefsTitle")
        self.v.addWidget(t)
        st = QLabel(subtitle)
        st.setObjectName("PrefsSubtitle")
        st.setWordWrap(True)
        self.v.addWidget(st)
        self.v.addSpacing(10)
        self.setWidget(body)
        self._card = None

    def group(self, heading: str = "") -> None:
        if heading:
            self.v.addSpacing(8)
            h = QLabel(heading.upper())
            h.setObjectName("PrefsGroup")
            self.v.addWidget(h)
        card = QFrame()
        card.setObjectName("PrefsCard")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 4, 16, 4)
        lay.setSpacing(0)
        self.v.addWidget(card)
        self._card = lay

    def row(self, title: str, desc: str = "", control=None) -> None:
        if self._card is None:
            self.group()
        if self._card.count():
            sep = QFrame()
            sep.setObjectName("PrefsSep")
            self._card.addWidget(sep)
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 10, 0, 10)
        h.setSpacing(16)
        txt = QVBoxLayout()
        txt.setSpacing(2)
        t = QLabel(title)
        t.setObjectName("PrefsRowTitle")
        txt.addWidget(t)
        if desc:
            d = QLabel(desc)
            d.setObjectName("PrefsRowDesc")
            d.setWordWrap(True)
            d.setTextInteractionFlags(Qt.TextSelectableByMouse)
            txt.addWidget(d)
        h.addLayout(txt, 1)
        if control is not None:
            if isinstance(control, (list, tuple)):
                box = QHBoxLayout()
                box.setSpacing(8)
                for c in control:
                    box.addWidget(c)
                h.addLayout(box)
            else:
                h.addWidget(control, 0, Qt.AlignVCenter)
        self._card.addWidget(w)

    def block(self, widget) -> None:
        """A free-form widget inside the current card (grids, lists)."""
        if self._card is None:
            self.group()
        if self._card.count():
            sep = QFrame()
            sep.setObjectName("PrefsSep")
            self._card.addWidget(sep)
        wrap = QWidget()
        lay = QVBoxLayout(wrap)
        lay.setContentsMargins(0, 10, 0, 10)
        lay.addWidget(widget)
        self._card.addWidget(wrap)

    def note(self, text: str) -> None:
        n = QLabel(text)
        n.setObjectName("PrefsRowDesc")
        n.setWordWrap(True)
        n.setContentsMargins(4, 6, 4, 0)
        self.v.addWidget(n)

    def finish(self) -> None:
        self.v.addStretch(1)


def _combo(items, current) -> QComboBox:
    c = QComboBox()
    for label, data in items:
        c.addItem(label, data)
    c.setCurrentIndex(max(0, c.findData(current)))
    c.setMinimumWidth(170)
    return c


def _switch(on) -> Switch:
    s = Switch()
    s.setChecked(bool(on))
    return s


class OptionsDialog(QDialog):
    rescan = Signal()
    reindex = Signal()
    download_clip = Signal()
    folder_manager = Signal()

    PAGES = ["General", "File Types", "Photo Viewer", "Name Tags & Metadata", "Intelligence",
             "Slideshow & Email", "Toolbar"]

    def __init__(self, lib, parent=None):
        super().__init__(parent)
        self.lib = lib
        s = lib.settings
        self.setObjectName("Prefs")
        self.setWindowTitle("Settings")
        self.setStyleSheet(_prefs_style())
        self.resize(860, 600)
        self.setMinimumSize(760, 500)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        body = QHBoxLayout()
        body.setSpacing(0)
        outer.addLayout(body, 1)

        self.nav = QListWidget()
        self.nav.setObjectName("PrefsNav")
        self.nav.setFixedWidth(220)
        self.nav.setIconSize(QSize(22, 22))
        self.nav.setFocusPolicy(Qt.NoFocus)
        self.stack = QStackedWidget()
        body.addWidget(self.nav)
        body.addWidget(self.stack, 1)
        badges = [("settings", "#8e8e93"), ("folder", "#3b82f6"), ("library", "#0ea5e9"), ("tag", "#f59e0b"),
                  ("sparkle", "#8b5cf6"), ("play", "#22c55e"), ("collage", "#14b8a6")]
        for name, (glyph, col) in zip(self.PAGES, badges):
            it = QListWidgetItem(_badge(glyph, col), name)
            it.setSizeHint(QSize(190, 36))
            self.nav.addItem(it)
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)

        # ---------------------------------------------------------- General
        g = _Page("General", "How Fotohaus opens photos and where it keeps its library.")
        g.group("Opening photos")
        self.open_in = _combo([("Photo Viewer", "viewer"), ("Edit view", "editor")],
                              "editor" if s.get("open_files_in") == "editor" else "viewer")
        g.row("Open files from Finder in", "The Photo Viewer opens instantly over your desktop; "
              "arrow keys move through the folder.", self.open_in)
        self.caption = _combo([("None", "none"), ("Filename", "filename"), ("Caption", "caption"),
                               ("Resolution", "resolution"), ("Date taken", "date"), ("Tags", "tags")],
                              s.get("thumb_caption") or "none")
        g.row("Thumbnail captions", "Text shown under each thumbnail in the library.", self.caption)
        g.group("Library")
        fm = QPushButton("Folder Manager…")
        fm.clicked.connect(self.folder_manager)
        sc = QPushButton("Rescan Now")
        sc.clicked.connect(self.rescan)
        g.row("Watched folders", "Choose which folders Fotohaus scans and keeps up to date.", [fm, sc])
        mv = QPushButton("Move…")
        mv.clicked.connect(self._move_db)
        g.row("Library database", str(DATA_DIR), mv)
        g.group("Maps")
        self.tiles = _switch(s.get("map_tiles"))
        g.row("Show map tiles in Places", "Downloads map images from OpenStreetMap. Turn off to keep "
              "Fotohaus completely offline.", self.tiles)
        g.finish()
        self.stack.addWidget(g)

        # ---------------------------------------------------------- File types
        ft = _Page("File Types", "Fotohaus skips file types you switch off here when it scans your folders.")
        self.type_checks = {}
        skip = {e.lower() for e in (s.get("file_types") or [])}
        for heading, exts in (("Photos", sorted(media.IMAGE_EXT)), ("Camera RAW", sorted(media.RAW_EXT)),
                              ("Videos", sorted(media.VIDEO_EXT))):
            ft.group(heading)
            box = QWidget()
            grid = QGridLayout(box)
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setHorizontalSpacing(18)
            grid.setVerticalSpacing(8)
            for i, ext in enumerate(exts):
                cb = QCheckBox(ext.lstrip(".").upper())
                cb.setChecked(ext not in skip)
                self.type_checks[ext] = cb
                grid.addWidget(cb, i // 6, i % 6)
            ft.block(box)
        ft.finish()
        self.stack.addWidget(ft)

        # ---------------------------------------------------------- Photo Viewer
        pv = _Page("Photo Viewer", "Double-click a photo in Finder and it opens over your desktop. "
                   "Use ← and → to browse the rest of the folder, Esc to close.")
        pv.group("Open these files with Fotohaus")
        self.viewer_checks = {}
        box = QWidget()
        grid2 = QGridLayout(box)
        grid2.setContentsMargins(0, 0, 0, 0)
        grid2.setHorizontalSpacing(18)
        grid2.setVerticalSpacing(8)
        for i, (label, uti) in enumerate(macos.VIEWER_TYPES):
            cb = QCheckBox(label)
            cur = macos.default_app_for_type(uti)
            cb.setChecked(bool(cur and cur.endswith("Fotohaus.app")))
            cb.setToolTip(f"Currently opens with: {os.path.basename(cur) if cur else 'unknown'}")
            self.viewer_checks[uti] = cb
            grid2.addWidget(cb, i // 3, i % 3)
        pv.block(box)
        allb = QPushButton("Select All")
        allb.clicked.connect(lambda: [c.setChecked(True) for c in self.viewer_checks.values()])
        apply_ = QPushButton("Make Default")
        apply_.setDefault(False)
        apply_.clicked.connect(self._apply_viewer)
        pv.row("Default viewer", "Makes Fotohaus the app that opens the checked types.", [allb, apply_])
        pv.note("You can also right-click any photo in Finder and choose Open With ▸ Fotohaus, or use "
                "Get Info ▸ Open with ▸ Change All….")
        pv.finish()
        self.stack.addWidget(pv)

        # ---------------------------------------------------------- Metadata
        md = _Page("Name Tags & Metadata", "Keep your organizing work portable, so other apps see it too.")
        md.group("Sidecar files")
        self.write_ini = _switch(s.get("write_picasa_ini"))
        md.row("Keep .picasa.ini files in sync", "Stars, captions, albums, name tags and edits are saved "
               "next to your photos, readable by Picasa.", self.write_ini)
        md.group("Inside your photos")
        self.write_meta = _switch(s.get("write_meta_in_files"))
        md.row("Write captions and tags into JPEGs", "Stored as standard IPTC/XMP metadata. "
               "The image itself is never re-compressed.", self.write_meta)
        self.write_faces = _switch(s.get("write_faces_in_files"))
        md.row("Write name tags into JPEGs", "Stored as XMP face regions that other photo apps understand.",
               self.write_faces)
        md.group("Import")
        imp = QPushButton("Import…")
        imp.clicked.connect(self._contacts)
        md.row("Picasa contacts", "Bring in names from a Picasa contacts.xml file.", imp)
        md.finish()
        self.stack.addWidget(md)

        # ---------------------------------------------------------- Intelligence
        ai = _Page("Intelligence", "Everything below runs on your Mac. No photo ever leaves your computer.")
        ai.group("Recognition")
        self.faces_cb = _switch(s.get("faces_enabled"))
        self.faces_cb.setEnabled(faces.available())
        ai.row("Faces", "Find faces and suggest names for the people you've tagged.", self.faces_cb)
        self.vision_cb = _switch(s.get("vision_enabled"))
        self.vision_cb.setEnabled(vision_mac.available())
        ai.row("Objects, scenes and text", "Search for “dog”, “beach” or words inside photos, "
               "using Apple's on-device Vision.", self.vision_cb)
        ai.group("Natural-language search")
        self.clip_cb = _switch(s.get("clip_enabled"))
        ai.row("Search by description", "Find photos with phrases like “kids playing in the snow”.",
               self.clip_cb)
        self.clip_btn = QPushButton("Download")
        self.clip_btn.clicked.connect(self.download_clip)
        self.clip_btn.setEnabled(not clip.downloaded())
        self.clip_status = QLabel("Installed" if clip.downloaded() else "Not downloaded (155 MB)")
        self.clip_status.setObjectName("PrefsRowDesc")
        ai.row("Language model", "Needed once for search by description.", [self.clip_status, self.clip_btn])
        ai.group("Maintenance")
        rb = QPushButton("Re-analyze")
        rb.clicked.connect(self.reindex)
        ai.row("Re-analyze all photos", "Runs face and scene analysis again across the whole library.", rb)
        ai.finish()
        self.stack.addWidget(ai)

        # ---------------------------------------------------------- Slideshow & Email
        es = _Page("Slideshow & Email", "Defaults for showing and sending photos.")
        es.group("Slideshow")
        self.secs = QSpinBox()
        self.secs.setRange(1, 60)
        self.secs.setSuffix(" s")
        self.secs.setValue(int(s.get("slideshow_seconds") or 4))
        self.secs.setMinimumWidth(80)
        es.row("Time per photo", "", self.secs)
        self.trans = _combo([("Dissolve", "dissolve"), ("Pan and zoom", "panzoom"), ("Wipe", "wipe"),
                             ("Shrink", "shrink"), ("None", "none"), ("Random", "random")],
                            s.get("slideshow_transition") or "dissolve")
        es.row("Transition", "", self.trans)
        self.capt = _switch(s.get("slideshow_captions"))
        es.row("Show captions", "", self.capt)
        es.group("Email")
        self.email = _combo([("Small (640 px)", 640), ("Medium (800 px)", 800), ("Large (1024 px)", 1024),
                             ("Extra large (1600 px)", 1600), ("Original size", 0)],
                            int(s.get("email_size") or 800))
        es.row("Picture size", "Photos are resized before they're attached.", self.email)
        es.finish()
        self.stack.addWidget(es)

        # ---------------------------------------------------------- Toolbar
        bt = _Page("Toolbar", "Choose the buttons at the bottom of the library window. Drag to reorder.")
        bt.group("Buttons")
        self.btn_list = QListWidget()
        self.btn_list.setObjectName("PrefsList")
        cur = list(s.get("button_bar") or [])
        order = cur + [k for k, _ in BUTTONS if k not in cur]
        labels = dict(BUTTONS)
        for k in order:
            it = QListWidgetItem(theme.icon(k if k not in ("email", "geotag", "html", "desktop") else
                                            {"email": "mail", "geotag": "pin", "html": "export",
                                             "desktop": "library"}[k]), labels.get(k, k))
            it.setData(Qt.UserRole, k)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsDragEnabled)
            it.setCheckState(Qt.Checked if k in cur else Qt.Unchecked)
            it.setSizeHint(QSize(200, 32))
            self.btn_list.addItem(it)
        self.btn_list.setDragDropMode(QAbstractItemView.InternalMove)
        self.btn_list.setIconSize(QSize(18, 18))
        self.btn_list.setFixedHeight(len(order) * 32 + 6)
        self.btn_list.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        bt.block(self.btn_list)
        bt.finish()
        self.stack.addWidget(bt)

        # ---------------------------------------------------------- footer
        foot = QWidget()
        foot.setObjectName("PrefsFooter")
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(16, 10, 16, 10)
        fl.addStretch()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        done = QPushButton("Done")
        done.setDefault(True)
        done.clicked.connect(self.accept)
        for b in (cancel, done):
            b.setMinimumWidth(90)
            fl.addWidget(b)
        outer.addWidget(foot)
        self.nav.setCurrentRow(0)

    def set_page(self, i: int) -> None:
        if 0 <= i < self.nav.count():
            self.nav.setCurrentRow(i)

    def _apply_viewer(self):
        chosen = [u for u, cb in self.viewer_checks.items() if cb.isChecked()]
        if not macos.bundle_path():
            QMessageBox.information(self, "Photo Viewer", "This works when Fotohaus runs as an installed app "
                                    "(Fotohaus.app in Applications).")
            return
        failed = macos.set_default_viewer(chosen)
        QMessageBox.information(self, "Photo Viewer", "Done. Double-clicking these files in Finder now opens "
                                "Fotohaus's Photo Viewer." if not failed else
                                f"Some types couldn't be changed: {', '.join(failed)}")

    def _contacts(self):
        p, _ = QFileDialog.getOpenFileName(self, "Picasa contacts.xml", str(Path.home()), "contacts.xml (*.xml)")
        if p:
            n = self.lib.import_contacts_xml(p)
            QMessageBox.information(self, "Contacts", f"Updated {n} people from contacts.xml.")

    def _move_db(self):
        d = QFileDialog.getExistingDirectory(self, "Move the library database to", str(Path.home()))
        if not d:
            return
        from ..paths import set_data_dir
        try:
            set_data_dir(d)
            QMessageBox.information(self, "Move Database", "The database was copied. Quit and reopen Fotohaus to "
                                    "use the new location.")
        except OSError as e:
            QMessageBox.warning(self, "Move Database", str(e))

    def accept(self):
        s = self.lib.settings
        s.set("open_files_in", self.open_in.currentData())
        s.set("thumb_caption", self.caption.currentData())
        s.set("map_tiles", self.tiles.isChecked())
        s.set("file_types", [e for e, cb in self.type_checks.items() if not cb.isChecked()])
        s.set("write_picasa_ini", self.write_ini.isChecked())
        s.set("write_meta_in_files", self.write_meta.isChecked())
        s.set("write_faces_in_files", self.write_faces.isChecked())
        s.set("faces_enabled", self.faces_cb.isChecked())
        s.set("vision_enabled", self.vision_cb.isChecked())
        s.set("clip_enabled", self.clip_cb.isChecked())
        s.set("email_size", self.email.currentData())
        s.set("slideshow_seconds", self.secs.value())
        s.set("slideshow_transition", self.trans.currentData())
        s.set("slideshow_captions", self.capt.isChecked())
        s.set("button_bar", [self.btn_list.item(i).data(Qt.UserRole) for i in range(self.btn_list.count())
                             if self.btn_list.item(i).checkState() == Qt.Checked])
        super().accept()


# ====================================================================== Folder Manager

class _ModeProxy(QIdentityProxyModel):
    def __init__(self, lib, parent=None):
        super().__init__(parent)
        self.lib = lib
        self.cache: dict = {}

    def refresh(self):
        self.cache.clear()
        self.layoutChanged.emit()

    def data(self, index, role=Qt.DisplayRole):
        if role in (Qt.DecorationRole, Qt.ToolTipRole, Qt.ForegroundRole):
            path = self.sourceModel().filePath(self.mapToSource(index))
            if path not in self.cache:
                self.cache[path] = self.lib.folder_mode(path)
            mode, inherited = self.cache[path]
            if role == Qt.DecorationRole:
                col = {"folder": "#2e9e4f", "once": "#d79b00", "exclude": "#c0392b", "applephotos": "#2e9e4f"}.get(mode)
                pm = QPixmap(12, 12)
                pm.fill(Qt.transparent)
                if col:
                    from PySide6.QtGui import QPainter
                    p = QPainter(pm)
                    p.setRenderHint(QPainter.Antialiasing)
                    c = QColor(col)
                    if inherited:
                        c.setAlpha(110)
                    p.setBrush(c)
                    p.setPen(Qt.NoPen)
                    p.drawEllipse(1, 1, 10, 10)
                    p.end()
                return QIcon(pm)
            if role == Qt.ToolTipRole:
                return {None: "Not watched", "folder": "Scan always", "once": "Scan once",
                        "exclude": "Removed from Fotohaus", "applephotos": "Apple Photos (read-only)"}.get(mode, "") + \
                       (" (inherited)" if inherited else "")
        return super().data(index, role)


class FolderManagerDialog(QDialog):
    """Picasa's Folder Manager: choose which folders are scanned always, once, or never."""

    def __init__(self, lib, parent=None):
        super().__init__(parent)
        self.lib = lib
        self.setWindowTitle("Folder Manager")
        self.resize(820, 560)
        v = QVBoxLayout(self)
        intro = QLabel("Choose how Fotohaus treats each folder. “Scan always” watches the folder and its subfolders; "
                       "“Remove from Fotohaus” hides it. Files are never moved or changed.")
        intro.setWordWrap(True)
        v.addWidget(intro)
        split = QSplitter()
        self.fs = QFileSystemModel(self)
        self.fs.setFilter(QDir.AllDirs | QDir.NoDotAndDotDot)
        self.fs.setRootPath("/")
        self.proxy = _ModeProxy(lib, self)
        self.proxy.setSourceModel(self.fs)
        self.tree = QTreeView()
        self.tree.setModel(self.proxy)
        for c in (1, 2, 3):
            self.tree.hideColumn(c)
        self.tree.setHeaderHidden(True)
        home = self.proxy.mapFromSource(self.fs.index(str(Path.home())))
        self.tree.setCurrentIndex(home)
        self.tree.expand(home)
        self.tree.scrollTo(home)
        self.tree.selectionModel().currentChanged.connect(self._cur)
        split.addWidget(self.tree)
        right = QWidget()
        rv = QVBoxLayout(right)
        self.path_lbl = QLabel()
        self.path_lbl.setWordWrap(True)
        self.path_lbl.setObjectName("Title")
        rv.addWidget(self.path_lbl)
        self.group = QButtonGroup(self)
        self.radios = {}
        for mode, label in (("folder", "Scan always"), ("once", "Scan once"), ("exclude", "Remove from Fotohaus"),
                            (None, "Not watched (inherit)")):
            rb = QRadioButton(label)
            self.group.addButton(rb)
            self.radios[mode] = rb
            rb.toggled.connect(lambda on, m=mode: on and self._set(m))
            rv.addWidget(rb)
        rv.addSpacing(10)
        rv.addWidget(QLabel("Watched and removed folders:"))
        self.list = QListWidget()
        self.list.itemClicked.connect(self._jump)
        rv.addWidget(self.list, 1)
        legend = QLabel("<span style='color:#2e9e4f'>●</span> scan always   <span style='color:#d79b00'>●</span> "
                        "scan once   <span style='color:#c0392b'>●</span> removed")
        rv.addWidget(legend)
        split.addWidget(right)
        split.setSizes([480, 320])
        v.addWidget(split, 1)
        bb = QDialogButtonBox(QDialogButtonBox.Ok)
        bb.accepted.connect(self.accept)
        v.addWidget(bb)
        self._suppress = False
        self._fill()
        self._cur(self.tree.currentIndex())

    def _path(self, idx=None) -> str:
        idx = idx or self.tree.currentIndex()
        return self.fs.filePath(self.proxy.mapToSource(idx))

    def _fill(self):
        self.list.clear()
        for r in self.lib.roots():
            it = QListWidgetItem(f"{'●'} {r['path']}   — " + {"folder": "scan always", "once": "scan once",
                                                              "exclude": "removed", "applephotos": "Apple Photos"}
                                 .get(r["kind"], r["kind"]))
            it.setData(Qt.UserRole, r["path"])
            it.setForeground(QColor({"folder": "#2e9e4f", "once": "#d79b00", "exclude": "#c0392b"}.get(r["kind"], "#2e9e4f")))
            self.list.addItem(it)

    def _cur(self, idx, *_):
        if not idx.isValid():
            return
        p = self._path(idx)
        self.path_lbl.setText(p)
        mode, inherited = self.lib.folder_mode(p)
        self._suppress = True
        self.radios[None if inherited else mode].setChecked(True)
        self._suppress = False

    def _set(self, mode):
        if self._suppress:
            return
        p = self._path()
        self.lib.set_folder_mode(p, mode)
        self.proxy.refresh()
        self._fill()
        self.changed = True

    def _jump(self, it):
        idx = self.proxy.mapFromSource(self.fs.index(it.data(Qt.UserRole)))
        self.tree.setCurrentIndex(idx)
        self.tree.scrollTo(idx)


# ====================================================================== Import

class ImportDialog(QDialog):
    def __init__(self, lib, jobs, parent=None):
        super().__init__(parent)
        from .. import importer
        self.lib, self.jobs = lib, jobs
        self.items = []
        self.setWindowTitle("Import")
        self.resize(940, 640)
        v = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel("Import from:"))
        self.src = QComboBox()
        for label, p in importer.sources():
            self.src.addItem(label, p)
        self.src.addItem("Folder…", "")
        self.src.activated.connect(self._pick)
        top.addWidget(self.src, 1)
        self.dupes = QCheckBox("Exclude duplicates")
        self.dupes.setChecked(True)
        self.dupes.toggled.connect(self._apply_dupes)
        top.addWidget(self.dupes)
        v.addLayout(top)
        self.status = QLabel("Choose a camera card or folder.")
        self.status.setObjectName("Muted")
        v.addWidget(self.status)
        self.grid = QListWidget()
        self.grid.setViewMode(QListView.IconMode)
        self.grid.setIconSize(QSize(120, 90))
        self.grid.setGridSize(QSize(140, 130))
        self.grid.setResizeMode(QListView.Adjust)
        self.grid.setMovement(QListView.Static)
        self.grid.itemChanged.connect(lambda *_: self._count())
        v.addWidget(self.grid, 1)
        sel = QHBoxLayout()
        a = QPushButton("Select All")
        a.clicked.connect(lambda: self._check_all(True))
        n = QPushButton("Select None")
        n.clicked.connect(lambda: self._check_all(False))
        sel.addWidget(a)
        sel.addWidget(n)
        sel.addStretch()
        v.addLayout(sel)
        form = QFormLayout()
        row = QHBoxLayout()
        self.dest = QLineEdit(default_pictures())
        b = QPushButton("Choose…")
        b.clicked.connect(lambda: (lambda d: d and self.dest.setText(d))(
            QFileDialog.getExistingDirectory(self, "Import to", self.dest.text())))
        row.addWidget(self.dest)
        row.addWidget(b)
        form.addRow("Import to", row)
        import datetime as _dt
        self.title = QLineEdit(_dt.date.today().isoformat())
        form.addRow("Folder title", self.title)
        self.by_date = QCheckBox("One folder per day (named by date taken)")
        form.addRow("", self.by_date)
        self.after = QComboBox()
        self.after.addItems(["Leave pictures on the card / source", "Delete pictures from the source after copying"])
        form.addRow("After copying", self.after)
        v.addLayout(form)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Import")
        bb.accepted.connect(self._import)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)
        if self.src.count() > 1:
            self._load(self.src.currentData())

    def _pick(self, i):
        p = self.src.itemData(i)
        if not p:
            p = QFileDialog.getExistingDirectory(self, "Import from folder", str(Path.home()))
            if not p:
                return
            self.src.insertItem(self.src.count() - 1, os.path.basename(p) or p, p)
            self.src.setCurrentIndex(self.src.count() - 2)
        self._load(p)

    def _load(self, path):
        from .. import importer
        self.status.setText("Looking for pictures…")

        def work(job):
            return importer.scan_source(self.lib, path, progress=lambda n: job.report(f"Found {n} items…"),
                                        should_stop=job.should_stop)
        self.jobs.submit("import-scan", work, on_done=self._loaded)

    def _loaded(self, items):
        self.items = items
        self.grid.blockSignals(True)
        self.grid.clear()
        for it in items:
            w = QListWidgetItem(os.path.basename(it.path))
            w.setFlags(w.flags() | Qt.ItemIsUserCheckable)
            w.setCheckState(Qt.Checked if it.selected else Qt.Unchecked)
            if it.duplicate:
                w.setToolTip("Already in your library")
                w.setForeground(QColor("#999"))
            try:
                if media.kind_of(it.path) != "video":
                    im = media.load_image(it.path, max_dim=160)
                    from .editor import pil_to_qimage
                    w.setIcon(QIcon(QPixmap.fromImage(pil_to_qimage(im))))
            except Exception:
                pass
            self.grid.addItem(w)
        self.grid.blockSignals(False)
        self._count()

    def _apply_dupes(self, on):
        for i, it in enumerate(self.items):
            if it.duplicate:
                self.grid.item(i).setCheckState(Qt.Unchecked if on else Qt.Checked)

    def _check_all(self, on):
        for i in range(self.grid.count()):
            self.grid.item(i).setCheckState(Qt.Checked if on else Qt.Unchecked)

    def _count(self):
        n = sum(1 for i in range(self.grid.count()) if self.grid.item(i).checkState() == Qt.Checked)
        d = sum(1 for it in self.items if it.duplicate)
        self.status.setText(f"{len(self.items)} items found · {d} already in your library · {n} selected")

    def _import(self):
        from .. import importer
        for i, it in enumerate(self.items):
            it.selected = self.grid.item(i).checkState() == Qt.Checked
        if not any(it.selected for it in self.items):
            return
        delete = self.after.currentIndex() == 1
        if delete and QMessageBox.question(self, "Delete after import", "Delete the imported pictures from the "
                                           "source after copying?") != QMessageBox.Yes:
            return
        items, dest, title, by_date = self.items, self.dest.text(), self.title.text(), self.by_date.isChecked()

        def work(job):
            return importer.do_import(self.lib, items, dest, title, by_date, delete,
                                      progress=lambda d, t, m: job.report(f"Importing {m}", d, t),
                                      should_stop=job.should_stop)
        self.jobs.submit("import", work, on_done=lambda st: QMessageBox.information(
            self.parent(), "Import", f"Imported {st['copied']} items into {len(st['folders'])} folder(s)."))
        self.accept()


# ====================================================================== Backup

class BackupDialog(QDialog):
    def __init__(self, lib, jobs, parent=None):
        super().__init__(parent)
        from .. import backup
        self.lib, self.jobs, self.B = lib, jobs, backup
        self.setWindowTitle("Back Up Pictures")
        self.resize(760, 560)
        v = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel("Backup set:"))
        self.sets = QComboBox()
        top.addWidget(self.sets, 1)
        nb = QPushButton("New Set…")
        nb.clicked.connect(self._new)
        db = QPushButton("Delete Set")
        db.clicked.connect(self._delete)
        top.addWidget(nb)
        top.addWidget(db)
        v.addLayout(top)
        self.info = QLabel()
        self.info.setObjectName("Muted")
        v.addWidget(self.info)
        v.addWidget(QLabel("Folders and albums to include:"))
        self.list = QListWidget()
        v.addWidget(self.list, 1)
        row = QHBoxLayout()
        rs = QPushButton("Restore from a Backup…")
        rs.clicked.connect(self._restore)
        row.addWidget(rs)
        row.addStretch()
        self.run_btn = QPushButton("Burn / Copy Backup")
        self.run_btn.setText("Back Up Now")
        self.run_btn.clicked.connect(self._run)
        row.addWidget(self.run_btn)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        v.addLayout(row)
        self.sets.currentIndexChanged.connect(self._show)
        self._reload()

    def _reload(self):
        self.sets.blockSignals(True)
        self.sets.clear()
        for s in self.B.backup_sets(self.lib):
            self.sets.addItem(s["name"], s)
        self.sets.blockSignals(False)
        self._show()

    def _show(self, *_):
        s = self.sets.currentData()
        self.list.clear()
        self.run_btn.setEnabled(s is not None)
        if not s:
            self.info.setText("Create a backup set to choose what to back up and where.")
            return
        import datetime as _dt
        last = _dt.datetime.fromtimestamp(s["last_run"]).strftime("%Y-%m-%d %H:%M") if s.get("last_run") else "never"
        self.info.setText(f"Destination: {s['dest']}   ·   {'Photos only' if s.get('types') == 'photos' else 'Photos and videos'}"
                          f"   ·   Last backup: {last}")
        for f in self.lib.folders(include_hidden=True):
            it = QListWidgetItem(f"📁 {f['name']}  ({f['count']})")
            it.setData(Qt.UserRole, ("folder", f["id"]))
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if f["id"] in s.get("folders", []) else Qt.Unchecked)
            self.list.addItem(it)
        for a in self.lib.albums():
            it = QListWidgetItem(f"🗂 {a['name']}  ({a['n']})")
            it.setData(Qt.UserRole, ("album", a["id"]))
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if a["id"] in s.get("albums", []) else Qt.Unchecked)
            self.list.addItem(it)

    def _collect(self):
        s = dict(self.sets.currentData())
        s["folders"], s["albums"] = [], []
        for i in range(self.list.count()):
            it = self.list.item(i)
            if it.checkState() == Qt.Checked:
                kind, ident = it.data(Qt.UserRole)
                s["folders" if kind == "folder" else "albums"].append(ident)
        self.B.save_backup_set(self.lib, s)
        return s

    def _new(self):
        d = QDialog(self)
        d.setWindowTitle("New Backup Set")
        f = QFormLayout(d)
        name = QLineEdit("My Backup")
        f.addRow("Name", name)
        row = QHBoxLayout()
        dest = QLineEdit()
        b = QPushButton("Choose…")
        b.clicked.connect(lambda: (lambda p: p and dest.setText(p))(
            QFileDialog.getExistingDirectory(d, "Back up to (e.g. an external drive)", "/Volumes")))
        row.addWidget(dest)
        row.addWidget(b)
        f.addRow("Back up to", row)
        types = QComboBox()
        types.addItem("Photos and videos", "all")
        types.addItem("Photos only", "photos")
        f.addRow("File types", types)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(d.accept)
        bb.rejected.connect(d.reject)
        f.addRow(bb)
        if d.exec() and name.text().strip() and dest.text():
            self.B.save_backup_set(self.lib, {"name": name.text().strip(), "dest": dest.text(),
                                              "types": types.currentData(), "folders": [], "albums": []})
            self._reload()
            self.sets.setCurrentIndex(self.sets.findText(name.text().strip()))

    def _delete(self):
        s = self.sets.currentData()
        if s and QMessageBox.question(self, "Delete set", f"Delete backup set “{s['name']}”? "
                                      "(Backed-up files are kept.)") == QMessageBox.Yes:
            self.B.delete_backup_set(self.lib, s["name"])
            self._reload()

    def _run(self):
        s = self._collect()

        def work(job):
            return self.B.run_backup(self.lib, s, progress=lambda d, t, m: job.report(f"Backing up {m}", d, t),
                                     should_stop=job.should_stop)
        self.jobs.submit("backup", work, on_done=lambda st: QMessageBox.information(
            self.parent(), "Backup", f"Backup finished: {st['copied']} copied, {st['skipped']} unchanged, "
                                     f"{st['errors']} errors."))
        self.accept()

    def _restore(self):
        d = QFileDialog.getExistingDirectory(self, "Choose a backup set folder (contains fotohaus-backup.json)",
                                             "/Volumes")
        if not d:
            return
        if not os.path.exists(os.path.join(d, self.B.MANIFEST)):
            QMessageBox.warning(self, "Restore", "That folder isn't a Fotohaus backup set.")
            return

        def work(job):
            return self.B.restore(d, True, progress=lambda i, t, m: job.report(f"Restoring {m}", i, t))
        self.jobs.submit("restore", work, on_done=lambda st: QMessageBox.information(
            self.parent(), "Restore", f"Restored {st['restored']} missing files ({st['skipped']} already present)."))
        self.accept()
