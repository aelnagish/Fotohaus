"""Export and welcome dialogs."""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
                               QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QPushButton, QSlider, QVBoxLayout)


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
