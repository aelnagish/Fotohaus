"""Create menu: Picture Collage editor, Movie Maker and Print (layouts + contact sheets)."""
from __future__ import annotations

import os
import random
from pathlib import Path

from PySide6.QtCore import QMarginsF, QObject, QRectF, QRunnable, QSize, QSizeF, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QImage, QPageLayout, QPageSize, QPainter, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox,
                               QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListView,
                               QListWidget, QListWidgetItem, QMessageBox, QPushButton, QSlider, QSpinBox, QSplitter,
                               QVBoxLayout, QWidget)

from .. import media, thumbs
from ..exporter import BORDERS, COLLAGE_TYPES, make_collage
from .dialogs import default_pictures


class _Sig(QObject):
    done = Signal(int, object)


class _Run(QRunnable):
    def __init__(self, sig, gen, fn):
        super().__init__()
        self.sig, self.gen, self.fn = sig, gen, fn

    def run(self):
        try:
            res = self.fn()
        except Exception as e:  # pragma: no cover
            res = e
        self.sig.done.emit(self.gen, res)


def _clips_list(rows, loader=None) -> QListWidget:
    lw = QListWidget()
    lw.setViewMode(QListView.IconMode)
    lw.setIconSize(QSize(72, 72))
    lw.setGridSize(QSize(84, 84))
    lw.setFlow(QListView.LeftToRight)
    lw.setWrapping(False)
    lw.setFixedHeight(104)
    lw.setDragDropMode(QAbstractItemView.InternalMove)
    lw.setMovement(QListView.Snap)
    for r in rows:
        it = QListWidgetItem()
        it.setData(Qt.UserRole, r["id"])
        it.setToolTip(r["filename"])
        try:
            it.setIcon(QIcon(str(thumbs.make_thumb(r))))
        except Exception:
            pass
        lw.addItem(it)
    return lw


def _ids_from(lw: QListWidget) -> list[int]:
    return [lw.item(i).data(Qt.UserRole) for i in range(lw.count())]


# ====================================================================== Collage

PAGE_FORMATS = {"Landscape 3:2": (3000, 2000), "Portrait 2:3": (2000, 3000), "Landscape 4:3": (3200, 2400),
                "Portrait 3:4": (2400, 3200), "Widescreen 16:9": (3840, 2160), "Square": (2400, 2400),
                "Letter landscape": (3300, 2550), "A4 landscape": (3508, 2480)}


class CollageDialog(QDialog):
    """Picasa's collage view: choose a type and options, preview live, then create the image."""

    def __init__(self, lib, rows, parent=None):
        super().__init__(parent)
        self.lib = lib
        self.rows = {r["id"]: r for r in rows}
        self.setWindowTitle("Picture Collage")
        self.resize(1100, 760)
        self.seed = 1
        self.bg = QColor(255, 255, 255)
        self.gen = 0
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.sig = _Sig()
        self.sig.done.connect(self._done)
        v = QVBoxLayout(self)
        split = QSplitter()
        left = QWidget()
        f = QFormLayout(left)
        self.kind = QComboBox()
        for k, label in COLLAGE_TYPES.items():
            self.kind.addItem(label, k)
        self.kind.setCurrentIndex(1)
        f.addRow("Collage type", self.kind)
        self.page = QComboBox()
        self.page.addItems(list(PAGE_FORMATS))
        f.addRow("Page format", self.page)
        self.spacing = QSlider(Qt.Horizontal)
        self.spacing.setRange(0, 80)
        self.spacing.setValue(12)
        f.addRow("Grid spacing", self.spacing)
        self.border = QComboBox()
        for k, label in BORDERS.items():
            self.border.addItem(label, k)
        f.addRow("Picture borders", self.border)
        self.shadows = QCheckBox("Draw shadows")
        f.addRow("", self.shadows)
        self.captions = QCheckBox("Include captions")
        f.addRow("", self.captions)
        bgrow = QHBoxLayout()
        self.bg_btn = QPushButton("Solid color…")
        self.bg_btn.clicked.connect(self._color)
        self.bg_img = QCheckBox("Use first photo as background")
        bgrow.addWidget(self.bg_btn)
        bgrow.addWidget(self.bg_img)
        f.addRow("Background", bgrow)
        shuffle = QPushButton("Shuffle Pictures")
        shuffle.clicked.connect(self._shuffle)
        f.addRow("", shuffle)
        split.addWidget(left)
        self.preview = QLabel("Rendering preview…")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumSize(560, 400)
        self.preview.setStyleSheet("background: #2a2a2c; color: #ddd;")
        split.addWidget(self.preview)
        split.setSizes([320, 780])
        v.addWidget(split, 1)
        v.addWidget(QLabel("Clips (drag to reorder, select and press Delete to remove):"))
        self.clips = _clips_list(rows)
        self.clips.model().rowsMoved.connect(lambda *_: self._schedule())
        v.addWidget(self.clips)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Create Collage")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)
        self.timer = QTimer(self, singleShot=True, interval=250, timeout=self._render)
        for w in (self.kind.currentIndexChanged, self.page.currentIndexChanged, self.spacing.valueChanged,
                  self.border.currentIndexChanged, self.shadows.toggled, self.captions.toggled, self.bg_img.toggled):
            w.connect(self._schedule)
        self._schedule()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Delete, Qt.Key_Backspace) and self.clips.hasFocus():
            for it in self.clips.selectedItems():
                self.clips.takeItem(self.clips.row(it))
            self._schedule()
            return
        super().keyPressEvent(e)

    def _color(self):
        c = QColorDialog.getColor(self.bg, self)
        if c.isValid():
            self.bg = c
            self.bg_img.setChecked(False)
            self._schedule()

    def _shuffle(self):
        self.seed = random.randint(1, 1_000_000)
        items = [self.clips.takeItem(0) for _ in range(self.clips.count())]
        random.shuffle(items)
        for it in items:
            self.clips.addItem(it)
        self._schedule()

    def _schedule(self, *_):
        self.timer.start()

    def params(self, scale=1.0):
        w, h = PAGE_FORMATS[self.page.currentText()]
        rows = [self.rows[i] for i in _ids_from(self.clips) if i in self.rows]
        return dict(rows=rows, width=int(w * scale), height=int(h * scale), layout=self.kind.currentData(),
                    spacing=max(0, int(self.spacing.value() * scale * 2)),
                    background=(self.bg.red(), self.bg.green(), self.bg.blue()), seed=self.seed,
                    border=self.border.currentData(), captions=self.captions.isChecked(),
                    background_row=rows[0] if (self.bg_img.isChecked() and rows) else None,
                    shadows=self.shadows.isChecked())

    def _render(self):
        self.gen += 1
        gen = self.gen
        p = self.params(scale=0.3)
        import tempfile
        tmp = os.path.join(tempfile.gettempdir(), f"fotohaus-collage-{os.getpid()}.jpg")

        def job():
            rows = p.pop("rows")
            make_collage(rows, tmp, **p)
            return QImage(tmp)
        self.pool.start(_Run(self.sig, gen, job))

    def _done(self, gen, img):
        if gen != self.gen or isinstance(img, Exception) or img.isNull():
            return
        pm = QPixmap.fromImage(img).scaled(self.preview.size() * 0.96, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.preview.setPixmap(pm)


# ====================================================================== Movie Maker

class MovieMakerDialog(QDialog):
    def __init__(self, lib, rows, parent=None):
        super().__init__(parent)
        from ..movie import SIZES
        self.lib = lib
        self.rows = {r["id"]: r for r in rows}
        self.setWindowTitle("Movie Maker")
        self.resize(760, 520)
        v = QVBoxLayout(self)
        f = QFormLayout()
        self.title = QLineEdit()
        self.title.setPlaceholderText("Title slide text (optional)")
        f.addRow("Title", self.title)
        self.size = QComboBox()
        self.size.addItems(list(SIZES))
        self.size.setCurrentIndex(1)
        f.addRow("Dimensions", self.size)
        self.trans = QComboBox()
        for k, label in (("dissolve", "Dissolve"), ("panzoom", "Pan and zoom"), ("wipe", "Wipe"), ("none", "None")):
            self.trans.addItem(label, k)
        f.addRow("Transition", self.trans)
        self.secs = QDoubleSpinBox()
        self.secs.setRange(0.5, 30)
        self.secs.setValue(3.0)
        self.secs.setSuffix(" s")
        f.addRow("Slide duration", self.secs)
        self.overlap = QDoubleSpinBox()
        self.overlap.setRange(0.1, 5)
        self.overlap.setValue(0.8)
        self.overlap.setSuffix(" s")
        f.addRow("Transition length", self.overlap)
        self.captions = QCheckBox("Show captions")
        self.captions.setChecked(True)
        self.full = QCheckBox("Full frame (crop photos to fill)")
        f.addRow("", self.captions)
        f.addRow("", self.full)
        arow = QHBoxLayout()
        self.audio = QLineEdit()
        self.audio.setPlaceholderText("Music (optional): MP3, M4A, WAV…")
        ab = QPushButton("Choose…")
        ab.clicked.connect(lambda: (lambda p: p and self.audio.setText(p))(
            QFileDialog.getOpenFileName(self, "Music", str(Path.home() / "Music"),
                                        "Audio (*.mp3 *.m4a *.aac *.wav *.aiff *.flac *.ogg)")[0]))
        arow.addWidget(self.audio)
        arow.addWidget(ab)
        f.addRow("Audio track", arow)
        v.addLayout(f)
        v.addWidget(QLabel("Clips (drag to reorder; select and press Delete to remove):"))
        self.clips = _clips_list(rows)
        v.addWidget(self.clips)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Create Movie")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Delete, Qt.Key_Backspace) and self.clips.hasFocus():
            for it in self.clips.selectedItems():
                self.clips.takeItem(self.clips.row(it))
            return
        super().keyPressEvent(e)

    def options(self, out_path):
        from ..movie import SIZES, MovieOptions
        return MovieOptions(out_path=out_path, size=SIZES[self.size.currentText()], slide_seconds=self.secs.value(),
                            transition=self.trans.currentData(), transition_seconds=self.overlap.value(),
                            title=self.title.text().strip(), captions=self.captions.isChecked(),
                            full_frame=self.full.isChecked(), audio_path=self.audio.text().strip())

    def ordered_rows(self):
        return [self.rows[i] for i in _ids_from(self.clips) if i in self.rows]


# ====================================================================== Print

PRINT_LAYOUTS = {"Wallet (2.5×3.5 in)": (2.5, 3.5), "3.5×5 in": (3.5, 5.0), "4×6 in": (4.0, 6.0),
                 "5×7 in": (5.0, 7.0), "8×10 in": (8.0, 10.0), "Full page": (0.0, 0.0), "Contact sheet": (-1, -1)}


class PrintDialog(QDialog):
    def __init__(self, lib, rows, contact=False, parent=None):
        super().__init__(parent)
        from PySide6.QtPrintSupport import QPrinter, QPrintPreviewWidget
        self.lib = lib
        self.rows = [r for r in rows if r["kind"] != "video"]
        self.cache: dict = {}
        self.setWindowTitle("Print")
        self.resize(1080, 760)
        self.printer = QPrinter(QPrinter.HighResolution)
        self.printer.setPageSize(QPageSize(QPageSize.Letter))
        v = QHBoxLayout(self)
        left = QWidget()
        left.setFixedWidth(300)
        f = QFormLayout(left)
        self.layout_c = QComboBox()
        self.layout_c.addItems(list(PRINT_LAYOUTS))
        self.layout_c.setCurrentIndex(len(PRINT_LAYOUTS) - 1 if contact else 2)
        f.addRow("Layout", self.layout_c)
        self.fit = QComboBox()
        self.fit.addItems(["Shrink to fit", "Crop to fit"])
        f.addRow("Fit", self.fit)
        self.border = QComboBox()
        self.border.addItems(["No border", "Thin white border", "Thick white border"])
        f.addRow("Border", self.border)
        self.caption = QCheckBox("Print captions (or file names)")
        f.addRow("", self.caption)
        self.copies = QSpinBox()
        self.copies.setRange(1, 50)
        f.addRow("Copies per picture", self.copies)
        self.paper = QComboBox()
        self.paper.addItem("Letter", QPageSize.Letter)
        self.paper.addItem("A4", QPageSize.A4)
        self.paper.addItem("4×6 in photo paper", "4x6")
        f.addRow("Paper", self.paper)
        self.orient = QComboBox()
        self.orient.addItems(["Portrait", "Landscape"])
        f.addRow("Orientation", self.orient)
        setup = QPushButton("Printer Setup…")
        setup.clicked.connect(self._setup)
        f.addRow(setup)
        pdf = QPushButton("Save as PDF…")
        pdf.clicked.connect(self._pdf)
        f.addRow(pdf)
        prn = QPushButton("Print…")
        prn.setDefault(True)
        prn.clicked.connect(self._print)
        f.addRow(prn)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        f.addRow(close)
        v.addWidget(left)
        self.preview = QPrintPreviewWidget(self.printer)
        self.preview.paintRequested.connect(self._paint)
        v.addWidget(self.preview, 1)
        for w in (self.layout_c.currentIndexChanged, self.fit.currentIndexChanged, self.border.currentIndexChanged,
                  self.caption.toggled, self.copies.valueChanged, self.paper.currentIndexChanged,
                  self.orient.currentIndexChanged):
            w.connect(self._update)
        self._update()

    def _update(self, *_):
        pid = self.paper.currentData()
        if pid == "4x6":
            ps = QPageSize(QSizeF(4, 6), QPageSize.Inch, "4x6")
        else:
            ps = QPageSize(pid)
        self.printer.setPageSize(ps)
        self.printer.setPageOrientation(QPageLayout.Landscape if self.orient.currentIndex() else QPageLayout.Portrait)
        self.printer.setPageMargins(QMarginsF(0.25, 0.25, 0.25, 0.25), QPageLayout.Inch)
        self.preview.updatePreview()

    def _img(self, r, px):
        key = (r["id"], px)
        if key not in self.cache:
            from ..edits import render
            from ..picasa.ini import parse_filters
            from .editor import pil_to_qimage
            im = render(media.load_image(r["path"], max_dim=px), parse_filters(r["filters"]), r["rotate"] or 0)
            self.cache[key] = pil_to_qimage(im)
        return self.cache[key]

    def _paint(self, printer):
        p = QPainter(printer)
        from PySide6.QtPrintSupport import QPrinter
        page = printer.pageRect(QPrinter.Unit.DevicePixel)
        page = QRectF(0, 0, page.width(), page.height())
        dpi = printer.resolution()
        lay = PRINT_LAYOUTS[self.layout_c.currentText()]
        items = [r for r in self.rows for _ in range(self.copies.value())]
        if lay == (-1, -1):
            self._contact(p, printer, page, dpi)
            p.end()
            return
        if lay == (0.0, 0.0):
            cw, ch = page.width(), page.height()
        else:
            cw, ch = lay[0] * dpi, lay[1] * dpi
            if page.width() > page.height():
                cw, ch = ch, cw
        gap = 0.15 * dpi if lay != (0.0, 0.0) else 0
        if lay == (0.0, 0.0):
            cw, ch = page.width(), page.height()
        cols = max(1, int((page.width() + gap) // (cw + gap)))
        rws = max(1, int((page.height() + gap) // (ch + gap)))
        if cols * cw + (cols - 1) * gap > page.width():
            cw = (page.width() - (cols - 1) * gap) / cols
        if rws * ch + (rws - 1) * gap > page.height():
            ch = (page.height() - (rws - 1) * gap) / rws
        per = cols * rws
        gx = (page.width() - cols * cw) / (cols + 1)
        gy = (page.height() - rws * ch) / (rws + 1)
        border = [0, 0.04, 0.1][self.border.currentIndex()] * dpi
        for i, r in enumerate(items):
            if i and i % per == 0:
                printer.newPage()
            k = i % per
            c, rr = k % cols, k // cols
            cell = QRectF(gx + c * (cw + gx), gy + rr * (ch + gy), cw, ch)
            cap_h = 0.3 * dpi if self.caption.isChecked() else 0
            img = self._img(r, int(max(cw, ch) / dpi * 220))
            target = cell.adjusted(border, border, -border, -border - cap_h)
            iw, ih = img.width(), img.height()
            # rotate to match the cell orientation
            if (iw > ih) != (target.width() > target.height()) and abs(iw - ih) > 2:
                from PySide6.QtGui import QTransform
                img = img.transformed(QTransform().rotate(90))
                iw, ih = ih, iw
            if self.fit.currentIndex() == 1:   # crop to fit
                s = max(target.width() / iw, target.height() / ih)
                sw, sh = target.width() / s, target.height() / s
                src = QRectF((iw - sw) / 2, (ih - sh) / 2, sw, sh)
                p.drawImage(target, img, src)
            else:
                s = min(target.width() / iw, target.height() / ih)
                dst = QRectF(0, 0, iw * s, ih * s)
                dst.moveCenter(target.center())
                p.drawImage(dst, img)
            if cap_h:
                f = QFont(self.font())
                f.setPointSizeF(9)
                p.setFont(f)
                p.drawText(QRectF(cell.left(), cell.bottom() - cap_h, cell.width(), cap_h), Qt.AlignCenter,
                           r["caption"] or r["filename"])
        p.end()

    def _contact(self, p, printer, page, dpi):
        cols = 5
        cw = page.width() / cols
        ch = cw * 1.05
        rws = max(1, int(page.height() // ch))
        f = QFont(self.font())
        f.setPointSizeF(7)
        p.setFont(f)
        for i, r in enumerate(self.rows):
            if i and i % (cols * rws) == 0:
                printer.newPage()
            k = i % (cols * rws)
            cell = QRectF((k % cols) * cw, (k // cols) * ch, cw, ch).adjusted(6, 6, -6, -6)
            img = self._img(r, 400)
            box = cell.adjusted(0, 0, 0, -0.18 * dpi)
            s = min(box.width() / img.width(), box.height() / img.height())
            dst = QRectF(0, 0, img.width() * s, img.height() * s)
            dst.moveCenter(box.center())
            p.drawImage(dst, img)
            p.drawText(QRectF(cell.left(), box.bottom(), cell.width(), 0.18 * dpi), Qt.AlignCenter,
                       r["caption"] or r["filename"])

    def _setup(self):
        from PySide6.QtPrintSupport import QPrintDialog as QPD
        d = QPD(self.printer, self)
        if d.exec():
            self.preview.updatePreview()

    def _print(self):
        from PySide6.QtPrintSupport import QPrintDialog as QPD
        d = QPD(self.printer, self)
        if d.exec():
            self._paint(self.printer)

    def _pdf(self):
        from PySide6.QtPrintSupport import QPrinter
        path, _ = QFileDialog.getSaveFileName(self, "Save PDF", os.path.join(default_pictures(), "Prints.pdf"),
                                              "PDF (*.pdf)")
        if not path:
            return
        pr = QPrinter(QPrinter.HighResolution)
        pr.setOutputFormat(QPrinter.PdfFormat)
        pr.setOutputFileName(path)
        pr.setPageLayout(self.printer.pageLayout())
        self._paint(pr)
        QMessageBox.information(self, "Saved", f"Saved {os.path.basename(path)}.")
