"""Dialogs for Picasa's Picture / Folder / Create commands."""
from __future__ import annotations

import datetime as _dt
import os

from PySide6.QtCore import QDateTime
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDateTimeEdit, QDialog, QDialogButtonBox, QFileDialog,
                               QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPlainTextEdit, QPushButton,
                               QRadioButton, QTableWidget, QTableWidgetItem, QVBoxLayout)

from .dialogs import default_pictures


def _buttons(dlg, ok_text="OK"):
    bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    bb.button(QDialogButtonBox.Ok).setText(ok_text)
    bb.accepted.connect(dlg.accept)
    bb.rejected.connect(dlg.reject)
    return bb


class BatchRenameDialog(QDialog):
    def __init__(self, count, sample_row, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Rename {count} file{'s' if count != 1 else ''}")
        self.count, self.sample = count, sample_row
        f = QFormLayout(self)
        self.base = QLineEdit(os.path.splitext(sample_row["filename"])[0] if count == 1 else "")
        self.base.setPlaceholderText("New name")
        f.addRow("Name", self.base)
        self.date = QCheckBox("Include date/time")
        self.res = QCheckBox("Include image resolution")
        f.addRow("", self.date)
        f.addRow("", self.res)
        self.preview = QLabel()
        self.preview.setObjectName("Muted")
        f.addRow("Example", self.preview)
        for w in (self.base.textChanged, self.date.toggled, self.res.toggled):
            w.connect(self._upd)
        self._upd()
        f.addRow(_buttons(self, "Rename"))

    def _upd(self, *_):
        r = self.sample
        parts = [self.base.text().strip() or "Photo"]
        if self.date.isChecked() and r["taken"]:
            parts.append(_dt.datetime.fromtimestamp(r["taken"]).strftime("%Y-%m-%d %H.%M.%S"))
        if self.res.isChecked() and r["width"]:
            parts.append(f"{r['width']}x{r['height']}")
        if self.count > 1:
            parts.append("1")
        self.preview.setText("-".join(parts) + os.path.splitext(r["filename"])[1].lower())


class AdjustDateDialog(QDialog):
    """Picasa's 'Adjust Date and Time': shift all by the change of one photo, or set them all."""

    def __init__(self, rows, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Adjust Date and Time")
        self.rows = rows
        first = rows[0]
        cur = QDateTime.fromSecsSinceEpoch(int(first["taken"] or first["mtime"] or 0))
        f = QFormLayout(self)
        f.addRow("Photo", QLabel(first["filename"] + (f"  (+{len(rows) - 1} more)" if len(rows) > 1 else "")))
        f.addRow("Current date", QLabel(cur.toString("dddd, MMMM d, yyyy  hh:mm:ss")))
        self.new = QDateTimeEdit(cur)
        self.new.setDisplayFormat("yyyy-MM-dd  HH:mm:ss")
        self.new.setCalendarPopup(True)
        f.addRow("New date", self.new)
        self.rel = QRadioButton("Adjust all photo dates by the same amount")
        self.abs = QRadioButton("Set all photos to the same date and time")
        self.rel.setChecked(True)
        f.addRow("", self.rel)
        f.addRow("", self.abs)
        self.write = QCheckBox("Change the date in the files too (JPEG EXIF)")
        self.write.setChecked(True)
        f.addRow("", self.write)
        self.cur = cur
        f.addRow(_buttons(self, "Adjust"))

    def result_values(self):
        new = self.new.dateTime().toSecsSinceEpoch()
        if self.abs.isChecked():
            return None, float(new), self.write.isChecked()
        return float(new - self.cur.toSecsSinceEpoch()), None, self.write.isChecked()


class FolderPropertiesDialog(QDialog):
    """Name, date, place taken and description — for folders, albums and 'Move to New Folder'."""

    def __init__(self, title, name="", date=None, place="", description="", parent=None, ok_text="OK",
                 show_date=True):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(460, 280)
        f = QFormLayout(self)
        self.name = QLineEdit(name)
        f.addRow("Name", self.name)
        self.date = QDateTimeEdit(QDateTime.fromSecsSinceEpoch(int(date)) if date else QDateTime.currentDateTime())
        self.date.setDisplayFormat("yyyy-MM-dd")
        self.date.setCalendarPopup(True)
        if show_date:
            f.addRow("Date", self.date)
        self.place = QLineEdit(place)
        self.place.setPlaceholderText("Place taken (optional)")
        f.addRow("Place taken", self.place)
        self.desc = QPlainTextEdit(description)
        self.desc.setPlaceholderText("Description (optional)")
        self.desc.setMaximumHeight(90)
        f.addRow("Description", self.desc)
        f.addRow(_buttons(self, ok_text))

    def values(self):
        return (self.name.text().strip(), float(self.date.dateTime().toSecsSinceEpoch()), self.place.text().strip(),
                self.desc.toPlainText().strip())


class EmailDialog(QDialog):
    SIZES = [("Small (640 px)", 640), ("Medium (800 px)", 800), ("Large (1024 px)", 1024),
             ("Extra large (1600 px)", 1600), ("Original size", 0)]

    def __init__(self, count, default_size=800, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Email {count} item{'s' if count != 1 else ''}")
        f = QFormLayout(self)
        self.size = QComboBox()
        for label, px in self.SIZES:
            self.size.addItem(label, px)
        idx = next((i for i, (_, px) in enumerate(self.SIZES) if px == default_size), 1)
        self.size.setCurrentIndex(idx)
        f.addRow("Picture size", self.size)
        self.subject = QLineEdit()
        self.subject.setPlaceholderText("Subject (optional)")
        f.addRow("Subject", self.subject)
        f.addRow(QLabel("Photos are resized and attached to a new message in your mail app.\nVideos are attached "
                        "unchanged."))
        f.addRow(_buttons(self, "Create Message"))


class HtmlExportDialog(QDialog):
    def __init__(self, count, parent=None):
        super().__init__(parent)
        from ..exporter import HTML_TEMPLATES
        self.setWindowTitle(f"Export {count} photos as an HTML page")
        f = QFormLayout(self)
        self.title = QLineEdit("My Photos")
        f.addRow("Page title", self.title)
        self.template = QComboBox()
        for k, (label, *_x) in HTML_TEMPLATES.items():
            self.template.addItem(label, k)
        f.addRow("Template", self.template)
        self.size = QComboBox()
        for px in (800, 1024, 1280, 1600, 2048):
            self.size.addItem(f"{px} px", px)
        self.size.setCurrentIndex(2)
        f.addRow("Image size", self.size)
        row = QHBoxLayout()
        self.dest = QLineEdit(os.path.join(default_pictures(), "Fotohaus Web Pages"))
        b = QPushButton("Choose…")
        b.clicked.connect(lambda: (lambda d: d and self.dest.setText(d))(
            QFileDialog.getExistingDirectory(self, "Export to", self.dest.text())))
        row.addWidget(self.dest)
        row.addWidget(b)
        f.addRow("Location", row)
        f.addRow(_buttons(self, "Export"))


class PosterDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        from ..exporter import PAPER
        self.setWindowTitle("Make a Poster")
        f = QFormLayout(self)
        self.enlarge = QComboBox()
        for n in (2, 3, 4, 5, 6):
            self.enlarge.addItem(f"{n * 100}%  ({n}×{n} pages wide)", n)
        self.enlarge.setCurrentIndex(1)
        f.addRow("Size", self.enlarge)
        self.paper = QComboBox()
        self.paper.addItems(list(PAPER))
        f.addRow("Paper", self.paper)
        self.overlap = QCheckBox("Overlap pages (easier to assemble)")
        self.overlap.setChecked(True)
        f.addRow("", self.overlap)
        f.addRow(_buttons(self, "Make Poster"))


class PassportDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        from ..exporter import PASSPORT_SPECS
        self.setWindowTitle("Passport Photo")
        f = QFormLayout(self)
        self.spec = QComboBox()
        self.spec.addItems(list(PASSPORT_SPECS))
        f.addRow("Size", self.spec)
        f.addRow(QLabel("Fotohaus finds the face, crops to the official size and lays out copies on a 6×4 in "
                        "(10×15 cm) print.\nAlways check the result against your country's rules."))
        f.addRow(_buttons(self, "Create"))


class GiftDiskDialog(QDialog):
    def __init__(self, count, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Create a Gift Disk Image ({count} items)")
        f = QFormLayout(self)
        self.name = QLineEdit("Photos")
        f.addRow("Disk name", self.name)
        self.size = QComboBox()
        for label, px in (("Original size", 0), ("2048 px", 2048), ("1600 px", 1600), ("1024 px", 1024)):
            self.size.addItem(label, px)
        f.addRow("Picture size", self.size)
        self.html = QCheckBox("Include a web page slideshow")
        self.html.setChecked(True)
        f.addRow("", self.html)
        f.addRow(QLabel("Macs no longer burn CDs, so Fotohaus makes a disk image (.dmg) you can copy to a USB "
                        "stick or share."))
        f.addRow(_buttons(self, "Create"))


SHORTCUTS = [
    ("Library", [("⌘O", "Add folder"), ("⌘F", "Search"), ("⌘A", "Select all"), ("⌥⌘A", "Clear selection"),
                 ("⇧⌘A", "Archive / unarchive"), ("⌘8", "Star / unstar"), ("⌘[  /  ⌘]", "Rotate counterclockwise / clockwise"),
                 ("⌘T", "Add tag"), ("⌘K", "Hold selection in photo tray"), ("⌘⇧C / ⌘⇧V", "Copy / paste all effects"),
                 ("⌘X / ⌘C / ⌘V", "Cut / copy / paste pictures"), ("⌘⌫", "Delete (kept 30 days in Recently Deleted)"),
                 ("Return", "Edit picture"), ("⌘Return", "Slideshow"), ("⌘= / ⌘-", "Larger / smaller thumbnails"),
                 ("⌘1 – ⌘5", "Library, People, Places, Duplicates, Timeline"), ("⌘I", "Info panel")]),
    ("Edit view", [("← / →", "Previous / next picture"), ("⌘Z / ⌘⇧Z", "Undo / redo"), ("Esc", "Back to Library"),
                   ("Double-click", "Fit / 100%"), ("O", "Show overflow pixels")]),
    ("Photo Viewer", [("← / →, Space", "Previous / next"), ("Wheel, pinch, + / −", "Zoom"), ("0", "Fit"),
                      ("[ / ]", "Rotate"), ("⌘8", "Star"), ("I", "Info"), ("Return", "Edit in Fotohaus"),
                      ("⌫", "Delete"), ("Esc / click outside", "Close")]),
    ("Slideshow", [("Space", "Pause / play"), ("← / →", "Previous / next"), ("↑ / ↓", "Slower / faster"),
                   ("⌘8", "Star"), ("Esc", "Exit")]),
]


class ShortcutsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Keyboard Shortcuts")
        self.resize(560, 620)
        v = QVBoxLayout(self)
        t = QTableWidget(0, 2)
        t.setHorizontalHeaderLabels(["Keys", "Action"])
        t.verticalHeader().hide()
        t.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        t.setEditTriggers(QTableWidget.NoEditTriggers)
        for section, rows in SHORTCUTS:
            r = t.rowCount()
            t.insertRow(r)
            it = QTableWidgetItem(section)
            f = it.font()
            f.setBold(True)
            it.setFont(f)
            t.setItem(r, 0, it)
            for keys, action in rows:
                r = t.rowCount()
                t.insertRow(r)
                t.setItem(r, 0, QTableWidgetItem(keys))
                t.setItem(r, 1, QTableWidgetItem(action))
        t.resizeColumnToContents(0)
        v.addWidget(t)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)


class ExifDialog(QDialog):
    """Picasa's Properties: every EXIF / GPS / IPTC field in the file."""

    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Properties — {os.path.basename(path)}")
        self.resize(560, 640)
        v = QVBoxLayout(self)
        t = QTableWidget(0, 2)
        t.setHorizontalHeaderLabels(["Field", "Value"])
        t.verticalHeader().hide()
        t.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        t.setEditTriggers(QTableWidget.NoEditTriggers)
        for k, val in all_exif(path):
            r = t.rowCount()
            t.insertRow(r)
            t.setItem(r, 0, QTableWidgetItem(k))
            t.setItem(r, 1, QTableWidgetItem(val))
        t.resizeColumnToContents(0)
        v.addWidget(t)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)


def all_exif(path) -> list[tuple[str, str]]:
    from PIL import ExifTags, Image
    out = [("File", path)]
    try:
        st = os.stat(path)
        out.append(("File size", f"{st.st_size:,} bytes"))
        out.append(("File modified", _dt.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S")))
        im = Image.open(path)
        out.append(("Format", f"{im.format}  {im.mode}"))
        out.append(("Dimensions", f"{im.width} × {im.height}"))
        exif = im.getexif()
        groups = [("", exif)]
        for ifd_id, label in ((0x8769, ""), (0x8825, "GPS "), (0xA005, "Interop ")):
            try:
                groups.append((label, exif.get_ifd(ifd_id)))
            except Exception:
                pass
        for label, d in groups:
            names = ExifTags.GPSTAGS if label == "GPS " else ExifTags.TAGS
            for k, v in d.items():
                if k in (0x8769, 0x8825, 0xA005, 0x927C, 0x02BC):
                    continue
                if isinstance(v, bytes):
                    v = v[:60].decode("latin-1", "replace") + ("…" if len(v) > 60 else "")
                out.append((label + str(names.get(k, hex(k))), str(v)[:200]))
        from PIL import IptcImagePlugin
        iptc = IptcImagePlugin.getiptcinfo(im) or {}
        for (rec, ds), v in iptc.items():
            vals = v if isinstance(v, list) else [v]
            out.append((f"IPTC {rec}:{ds}", ", ".join(x.decode("utf-8", "replace") for x in vals)[:200]))
    except Exception as e:
        out.append(("Error", str(e)))
    return out
