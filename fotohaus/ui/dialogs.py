"""Export, collage, settings and welcome dialogs."""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
                               QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMessageBox, QPushButton, QSlider, QSpinBox,
                               QVBoxLayout)

from ..ai import clip, faces, vision_mac
from ..paths import DATA_DIR


def default_pictures() -> str:
    p = Path.home() / "Pictures"
    return str(p if p.exists() else Path.home())


class ExportDialog(QDialog):
    def __init__(self, lib, count, parent=None):
        super().__init__(parent)
        self.lib = lib
        self.setWindowTitle(f"Export {count} item{'s' if count != 1 else ''}")
        f = QFormLayout(self)
        row = QHBoxLayout()
        self.dest = QLineEdit(lib.settings.get("export_dir") or os.path.join(default_pictures(), "Fotohaus Exports"))
        b = QPushButton("Choose…")
        b.clicked.connect(self._choose)
        row.addWidget(self.dest)
        row.addWidget(b)
        f.addRow("Export to", row)
        self.size = QComboBox()
        self.size.addItems(["Original size", "4096 px", "2048 px", "1600 px", "1024 px", "800 px", "640 px"])
        f.addRow("Image size", self.size)
        self.fmt = QComboBox()
        self.fmt.addItems(["JPEG", "HEIC", "WebP", "PNG", "Same as original"])
        f.addRow("Format", self.fmt)
        self.q = QSlider(Qt.Horizontal)
        self.q.setRange(50, 100)
        self.q.setValue(88)
        self.ql = QLabel("88")
        self.q.valueChanged.connect(lambda v: self.ql.setText(str(v)))
        qr = QHBoxLayout()
        qr.addWidget(self.q)
        qr.addWidget(self.ql)
        f.addRow("Quality", qr)
        self.wm = QLineEdit()
        self.wm.setPlaceholderText("Optional text, e.g. © Your Name")
        f.addRow("Watermark", self.wm)
        self.strip = QCheckBox("Remove location (GPS) from exported files")
        f.addRow("", self.strip)
        self.edits = QCheckBox("Apply edits")
        self.edits.setChecked(True)
        f.addRow("", self.edits)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Export")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        f.addRow(bb)

    def _choose(self):
        d = QFileDialog.getExistingDirectory(self, "Export to", self.dest.text())
        if d:
            self.dest.setText(d)

    def options(self):
        from ..exporter import ExportOptions
        size_txt = self.size.currentText()
        max_size = None if size_txt.startswith("Original") else int(size_txt.split()[0])
        fmt = {"JPEG": "JPEG", "HEIC": "HEIF", "WebP": "WEBP", "PNG": "PNG",
               "Same as original": "ORIGINAL"}[self.fmt.currentText()]
        self.lib.settings.set("export_dir", self.dest.text())
        return ExportOptions(dest=self.dest.text(), max_size=max_size, fmt=fmt, quality=self.q.value(),
                             watermark=self.wm.text().strip(), strip_location=self.strip.isChecked(),
                             apply_edits=self.edits.isChecked())


class CollageDialog(QDialog):
    def __init__(self, count, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Make a Collage ({count} photos)")
        f = QFormLayout(self)
        self.layout_c = QComboBox()
        self.layout_c.addItems(["Mosaic", "Grid", "Picture pile"])
        f.addRow("Layout", self.layout_c)
        self.page = QComboBox()
        self.page.addItems(["Landscape 3:2 (3000×2000)", "Portrait 2:3 (2000×3000)", "Square (2400×2400)",
                            "Widescreen 16:9 (3840×2160)"])
        f.addRow("Size", self.page)
        self.spacing = QSpinBox()
        self.spacing.setRange(0, 80)
        self.spacing.setValue(12)
        f.addRow("Spacing (px)", self.spacing)
        self.bg = QColor(255, 255, 255)
        self.bg_btn = QPushButton("White")
        self.bg_btn.clicked.connect(self._color)
        f.addRow("Background", self.bg_btn)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Create")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        f.addRow(bb)

    def _color(self):
        c = QColorDialog.getColor(self.bg, self)
        if c.isValid():
            self.bg = c
            self.bg_btn.setText(c.name())

    def values(self):
        sizes = [(3000, 2000), (2000, 3000), (2400, 2400), (3840, 2160)]
        w, h = sizes[self.page.currentIndex()]
        layout = ["mosaic", "grid", "pile"][self.layout_c.currentIndex()]
        return dict(width=w, height=h, layout=layout, spacing=self.spacing.value(),
                    background=(self.bg.red(), self.bg.green(), self.bg.blue()))


class SettingsDialog(QDialog):
    rescan = Signal()
    reindex = Signal()
    download_clip = Signal()

    def __init__(self, lib, parent=None):
        super().__init__(parent)
        self.lib = lib
        self.setWindowTitle("Settings")
        self.resize(620, 560)
        v = QVBoxLayout(self)
        g = QGroupBox("Watched folders")
        gv = QVBoxLayout(g)
        self.roots = QListWidget()
        gv.addWidget(self.roots)
        row = QHBoxLayout()
        add = QPushButton("Add Folder…")
        add.clicked.connect(self._add)
        rem = QPushButton("Remove")
        rem.clicked.connect(self._remove)
        ro = QPushButton("Toggle Read-only")
        ro.setToolTip("Read-only folders are never written to: stars, captions and edits are kept "
                      "only in Fotohaus's database (use this for Apple Photos libraries or shared drives).")
        ro.clicked.connect(self._toggle_ro)
        sc = QPushButton("Rescan Now")
        sc.clicked.connect(self.rescan)
        for b in (add, rem, ro):
            row.addWidget(b)
        row.addStretch()
        row.addWidget(sc)
        gv.addLayout(row)
        v.addWidget(g)

        g2 = QGroupBox("Picasa compatibility")
        g2v = QVBoxLayout(g2)
        self.write_ini = QCheckBox("Keep .picasa.ini files in sync (stars, captions, albums, faces, edits)")
        self.write_ini.setChecked(bool(lib.settings.get("write_picasa_ini")))
        g2v.addWidget(self.write_ini)
        imp = QPushButton("Import Picasa contacts.xml…")
        imp.clicked.connect(self._contacts)
        g2v.addWidget(imp, 0, Qt.AlignLeft)
        v.addWidget(g2)

        g3 = QGroupBox("On-device intelligence (nothing leaves your Mac)")
        g3v = QVBoxLayout(g3)
        self.faces_cb = QCheckBox("Find and recognize faces")
        self.faces_cb.setChecked(bool(lib.settings.get("faces_enabled")))
        self.faces_cb.setEnabled(faces.available())
        g3v.addWidget(self.faces_cb)
        self.vision_cb = QCheckBox("Recognize objects, scenes and text (Apple Vision)")
        self.vision_cb.setChecked(bool(lib.settings.get("vision_enabled")))
        self.vision_cb.setEnabled(vision_mac.available())
        if not vision_mac.available():
            self.vision_cb.setToolTip("Requires macOS")
        g3v.addWidget(self.vision_cb)
        crow = QHBoxLayout()
        self.clip_cb = QCheckBox("Natural-language search (CLIP model, 155 MB download)")
        self.clip_cb.setChecked(bool(lib.settings.get("clip_enabled")))
        crow.addWidget(self.clip_cb)
        self.clip_btn = QPushButton("Download Model")
        self.clip_btn.clicked.connect(self.download_clip)
        crow.addWidget(self.clip_btn)
        g3v.addLayout(crow)
        self.clip_status = QLabel()
        self.clip_status.setObjectName("Muted")
        g3v.addWidget(self.clip_status)
        rb = QPushButton("Re-analyze All Photos")
        rb.clicked.connect(self.reindex)
        g3v.addWidget(rb, 0, Qt.AlignLeft)
        v.addWidget(g3)

        g4 = QGroupBox("Other")
        g4v = QFormLayout(g4)
        self.tiles_cb = QCheckBox("Load OpenStreetMap tiles in Places")
        self.tiles_cb.setChecked(bool(lib.settings.get("map_tiles")))
        g4v.addRow(self.tiles_cb)
        self.secs = QSpinBox()
        self.secs.setRange(1, 60)
        self.secs.setValue(int(lib.settings.get("slideshow_seconds") or 4))
        g4v.addRow("Slideshow seconds per photo", self.secs)
        lab = QLabel(f"Library database: {DATA_DIR}")
        lab.setObjectName("Muted")
        lab.setTextInteractionFlags(Qt.TextSelectableByMouse)
        g4v.addRow(lab)
        v.addWidget(g4)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        bb.accepted.connect(self.accept)
        v.addWidget(bb)
        self._fill()

    def _fill(self):
        self.roots.clear()
        for r in self.lib.roots():
            it = QListWidgetItem(r["path"] + ("   (read-only)" if r["readonly"] else ""))
            it.setData(Qt.UserRole, r["id"])
            self.roots.addItem(it)
        self.update_clip_status()

    def update_clip_status(self, msg=None):
        if msg:
            self.clip_status.setText(msg)
            return
        if clip.downloaded():
            self.clip_status.setText("Model installed.")
            self.clip_btn.setEnabled(False)
        else:
            self.clip_status.setText("Model not downloaded yet (from Hugging Face).")

    def _add(self):
        d = QFileDialog.getExistingDirectory(self, "Add folder", default_pictures())
        if d:
            self.lib.add_root(d)
            self._fill()
            self.rescan.emit()

    def _remove(self):
        it = self.roots.currentItem()
        if it and QMessageBox.question(self, "Remove folder",
                                       "Stop watching this folder? (Files are not deleted.)") == QMessageBox.Yes:
            self.lib.remove_root(it.data(Qt.UserRole))
            self._fill()

    def _toggle_ro(self):
        it = self.roots.currentItem()
        if it:
            r = next(x for x in self.lib.roots() if x["id"] == it.data(Qt.UserRole))
            self.lib.set_root_readonly(r["id"], not r["readonly"])
            self._fill()

    def _contacts(self):
        p, _ = QFileDialog.getOpenFileName(self, "Picasa contacts.xml", str(Path.home()), "contacts.xml (*.xml)")
        if p:
            n = self.lib.import_contacts_xml(p)
            QMessageBox.information(self, "Contacts", f"Updated {n} people from contacts.xml.")

    def done(self, r):
        s = self.lib.settings
        s.set("write_picasa_ini", self.write_ini.isChecked())
        s.set("faces_enabled", self.faces_cb.isChecked())
        s.set("vision_enabled", self.vision_cb.isChecked())
        s.set("clip_enabled", self.clip_cb.isChecked())
        s.set("map_tiles", self.tiles_cb.isChecked())
        s.set("slideshow_seconds", self.secs.value())
        super().done(r)


class WelcomeDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Welcome to Fotohaus")
        self.resize(560, 420)
        v = QVBoxLayout(self)
        t = QLabel("Welcome to Fotohaus")
        t.setObjectName("Title")
        v.addWidget(t)
        txt = QLabel(
            "Fotohaus organizes the photos already on your Mac — it never moves or uploads them.\n\n"
            "• Old Picasa folders open with their stars, captions, albums, name tags and edits intact.\n"
            "• Faces, objects, scenes and text are recognized on your Mac, so you can search for "
            "“beach”, a person's name, or words on a sign.\n"
            "• Edits are non-destructive until you choose Save to File.\n\n"
            "Which folders should Fotohaus watch?")
        txt.setWordWrap(True)
        v.addWidget(txt)
        self.list = QListWidget()
        home = Path.home()
        for p in (home / "Pictures", home / "Desktop", home / "Downloads", home / "Documents"):
            if p.exists():
                it = QListWidgetItem(str(p))
                it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
                it.setCheckState(Qt.Checked if p.name == "Pictures" else Qt.Unchecked)
                self.list.addItem(it)
        v.addWidget(self.list)
        add = QPushButton("Add Another Folder…")
        add.clicked.connect(self._add)
        v.addWidget(add, 0, Qt.AlignLeft)
        note = QLabel("Tip: macOS will ask for permission the first time Fotohaus reads Desktop, Documents or "
                      "Downloads.")
        note.setObjectName("Muted")
        note.setWordWrap(True)
        v.addWidget(note)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Start")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def _add(self):
        d = QFileDialog.getExistingDirectory(self, "Add folder", str(Path.home()))
        if d:
            it = QListWidgetItem(d)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked)
            self.list.addItem(it)

    def folders(self):
        return [self.list.item(i).text() for i in range(self.list.count())
                if self.list.item(i).checkState() == Qt.Checked]
