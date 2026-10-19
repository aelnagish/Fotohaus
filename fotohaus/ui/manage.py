"""Tools: Options, Folder Manager, Import, Back Up Pictures."""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QDir, QIdentityProxyModel, QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QFileDialog, QFileSystemModel, QFormLayout, QGridLayout, QHBoxLayout,
                               QLabel, QLineEdit, QListView, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
                               QRadioButton, QSpinBox, QSplitter, QTabWidget, QTreeView, QVBoxLayout, QWidget)

from .. import macos, media
from ..ai import clip, faces, vision_mac
from ..paths import DATA_DIR
from .dialogs import default_pictures

BUTTONS = [("share", "Share"), ("email", "Email"), ("print", "Print"), ("export", "Export"),
           ("collage", "Collage"), ("movie", "Movie"), ("geotag", "Geo-Tag"), ("html", "Web Page"),
           ("desktop", "Set as Desktop")]


# ====================================================================== Options

class OptionsDialog(QDialog):
    rescan = Signal()
    reindex = Signal()
    download_clip = Signal()
    folder_manager = Signal()

    def __init__(self, lib, parent=None):
        super().__init__(parent)
        self.lib = lib
        s = lib.settings
        self.setWindowTitle("Options")
        v = QVBoxLayout(self)
        tabs = QTabWidget()
        bar = tabs.tabBar()
        bar.setElideMode(Qt.ElideNone)      # always show the full tab names
        bar.setUsesScrollButtons(False)
        bar.setExpanding(False)
        self.tabs = tabs
        v.addWidget(tabs)

        # --- General
        g = QWidget()
        f = QFormLayout(g)
        self.open_in = QComboBox()
        self.open_in.addItem("Photo Viewer", "viewer")
        self.open_in.addItem("Fotohaus's Edit view", "editor")
        self.open_in.setCurrentIndex(0 if s.get("open_files_in") != "editor" else 1)
        f.addRow("Files opened from Finder open in", self.open_in)
        self.caption = QComboBox()
        for k, label in (("none", "None"), ("filename", "Filename"), ("caption", "Caption"),
                         ("resolution", "Resolution"), ("date", "Date taken"), ("tags", "Tags")):
            self.caption.addItem(label, k)
        self.caption.setCurrentIndex(max(0, self.caption.findData(s.get("thumb_caption"))))
        f.addRow("Thumbnail caption", self.caption)
        self.tiles = QCheckBox("Load OpenStreetMap tiles in Places")
        self.tiles.setChecked(bool(s.get("map_tiles")))
        f.addRow("", self.tiles)
        fm = QPushButton("Folder Manager…")
        fm.clicked.connect(self.folder_manager)
        sc = QPushButton("Rescan Folders Now")
        sc.clicked.connect(self.rescan)
        row = QHBoxLayout()
        row.addWidget(fm)
        row.addWidget(sc)
        row.addStretch()
        f.addRow("Folders", row)
        lab = QLabel(f"Library database: {DATA_DIR}")
        lab.setObjectName("Muted")
        lab.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lab.setWordWrap(True)
        f.addRow(lab)
        mv = QPushButton("Move Database…")
        mv.clicked.connect(self._move_db)
        f.addRow("", mv)
        tabs.addTab(g, "General")

        # --- File types
        ft = QWidget()
        fv = QVBoxLayout(ft)
        fv.addWidget(QLabel("Fotohaus shows these file types (unchecked types are skipped when scanning):"))
        grid = QGridLayout()
        self.type_checks = {}
        skip = {e.lower() for e in (s.get("file_types") or [])}
        exts = sorted(media.IMAGE_EXT) + sorted(media.RAW_EXT) + sorted(media.VIDEO_EXT)
        for i, ext in enumerate(exts):
            cb = QCheckBox(ext)
            cb.setChecked(ext not in skip)
            self.type_checks[ext] = cb
            grid.addWidget(cb, i // 6, i % 6)
        fv.addLayout(grid)
        fv.addStretch()
        tabs.addTab(ft, "File Types")

        # --- Photo Viewer (file associations)
        pv = QWidget()
        pvl = QVBoxLayout(pv)
        pvl.addWidget(QLabel("Use Fotohaus's Photo Viewer when you double-click these files in Finder:"))
        self.viewer_checks = {}
        grid2 = QGridLayout()
        for i, (label, uti) in enumerate(macos.VIEWER_TYPES):
            cb = QCheckBox(label)
            cur = macos.default_app_for_type(uti)
            cb.setChecked(bool(cur and cur.endswith("Fotohaus.app")))
            cb.setToolTip(f"Currently opens with: {os.path.basename(cur) if cur else 'unknown'}")
            self.viewer_checks[uti] = cb
            grid2.addWidget(cb, i // 3, i % 3)
        pvl.addLayout(grid2)
        btns = QHBoxLayout()
        allb = QPushButton("Select All")
        allb.clicked.connect(lambda: [c.setChecked(True) for c in self.viewer_checks.values()])
        apply_ = QPushButton("Make Fotohaus the Default")
        apply_.clicked.connect(self._apply_viewer)
        btns.addWidget(allb)
        btns.addWidget(apply_)
        btns.addStretch()
        pvl.addLayout(btns)
        note = QLabel("You can also choose “Open With ▸ Fotohaus” on any photo in Finder, or use Finder ▸ Get Info "
                      "▸ Open with ▸ Change All…")
        note.setObjectName("Muted")
        note.setWordWrap(True)
        pvl.addWidget(note)
        pvl.addStretch()
        tabs.addTab(pv, "Photo Viewer")

        # --- Picasa / metadata
        md = QWidget()
        mf = QVBoxLayout(md)
        self.write_ini = QCheckBox("Keep .picasa.ini files in sync (stars, captions, albums, name tags, edits)")
        self.write_ini.setChecked(bool(s.get("write_picasa_ini")))
        self.write_meta = QCheckBox("Store captions and tags inside JPEG files (IPTC/XMP), like Picasa")
        self.write_meta.setChecked(bool(s.get("write_meta_in_files")))
        self.write_faces = QCheckBox("Store name tags inside JPEG files (XMP face regions)")
        self.write_faces.setChecked(bool(s.get("write_faces_in_files")))
        for w in (self.write_ini, self.write_meta, self.write_faces):
            mf.addWidget(w)
        imp = QPushButton("Import Picasa contacts.xml…")
        imp.clicked.connect(self._contacts)
        mf.addWidget(imp, 0, Qt.AlignLeft)
        mf.addStretch()
        tabs.addTab(md, "Name Tags && Metadata")

        # --- Intelligence
        ai = QWidget()
        af = QVBoxLayout(ai)
        self.faces_cb = QCheckBox("Find and recognize faces")
        self.faces_cb.setChecked(bool(s.get("faces_enabled")))
        self.faces_cb.setEnabled(faces.available())
        self.vision_cb = QCheckBox("Recognize objects, scenes and text (Apple Vision, on-device)")
        self.vision_cb.setChecked(bool(s.get("vision_enabled")))
        self.vision_cb.setEnabled(vision_mac.available())
        self.clip_cb = QCheckBox("Natural-language search (CLIP model, 155 MB download)")
        self.clip_cb.setChecked(bool(s.get("clip_enabled")))
        for w in (self.faces_cb, self.vision_cb, self.clip_cb):
            af.addWidget(w)
        row = QHBoxLayout()
        self.clip_btn = QPushButton("Download Model")
        self.clip_btn.clicked.connect(self.download_clip)
        self.clip_btn.setEnabled(not clip.downloaded())
        rb = QPushButton("Re-analyze All Photos")
        rb.clicked.connect(self.reindex)
        row.addWidget(self.clip_btn)
        row.addWidget(rb)
        row.addStretch()
        af.addLayout(row)
        self.clip_status = QLabel("Model installed." if clip.downloaded() else "Model not downloaded yet.")
        self.clip_status.setObjectName("Muted")
        af.addWidget(self.clip_status)
        af.addStretch()
        tabs.addTab(ai, "Intelligence")

        # --- Email & Slideshow
        es = QWidget()
        ef = QFormLayout(es)
        self.email = QComboBox()
        for label, px in (("640 px", 640), ("800 px", 800), ("1024 px", 1024), ("1600 px", 1600), ("Original", 0)):
            self.email.addItem(label, px)
        self.email.setCurrentIndex(max(0, self.email.findData(int(s.get("email_size") or 800))))
        ef.addRow("Email picture size", self.email)
        self.secs = QSpinBox()
        self.secs.setRange(1, 60)
        self.secs.setValue(int(s.get("slideshow_seconds") or 4))
        ef.addRow("Slideshow seconds per photo", self.secs)
        self.trans = QComboBox()
        for k, label in (("dissolve", "Dissolve"), ("panzoom", "Pan and zoom"), ("wipe", "Wipe"),
                         ("shrink", "Shrink"), ("none", "None"), ("random", "Random")):
            self.trans.addItem(label, k)
        self.trans.setCurrentIndex(max(0, self.trans.findData(s.get("slideshow_transition"))))
        ef.addRow("Slideshow transition", self.trans)
        self.capt = QCheckBox("Show captions in slideshows")
        self.capt.setChecked(bool(s.get("slideshow_captions")))
        ef.addRow("", self.capt)
        tabs.addTab(es, "Email && Slideshow")

        # --- Buttons
        bt = QWidget()
        bl = QVBoxLayout(bt)
        bl.addWidget(QLabel("Buttons shown at the bottom of the window:"))
        self.btn_list = QListWidget()
        cur = list(s.get("button_bar") or [])
        order = cur + [k for k, _ in BUTTONS if k not in cur]
        labels = dict(BUTTONS)
        for k in order:
            it = QListWidgetItem(labels.get(k, k))
            it.setData(Qt.UserRole, k)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsDragEnabled)
            it.setCheckState(Qt.Checked if k in cur else Qt.Unchecked)
            self.btn_list.addItem(it)
        self.btn_list.setDragDropMode(QAbstractItemView.InternalMove)
        bl.addWidget(self.btn_list)
        bl.addWidget(QLabel("Drag to reorder."))
        tabs.addTab(bt, "Buttons")

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)
        # wide enough that every tab name fits on one row
        need = bar.sizeHint().width() + 48
        self.setMinimumWidth(need)
        self.resize(max(need, 680), 560)

    def set_page(self, i: int) -> None:
        if 0 <= i < self.tabs.count():
            self.tabs.setCurrentIndex(i)

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
