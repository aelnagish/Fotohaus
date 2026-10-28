"""Tools: Options, Folder Manager, Import, Back Up Pictures."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtCore import QDir, QIdentityProxyModel, QObject, QRectF, QRunnable, QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QFileDialog, QFileSystemModel, QFormLayout, QGridLayout, QHBoxLayout,
                               QLabel, QLineEdit, QListView, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
                               QFrame, QRadioButton, QSizePolicy, QSpinBox, QTabWidget, QToolButton, QTreeView, QVBoxLayout, QWidget)

from .. import macos, media
from ..ai import clip, faces, vision_mac
from ..paths import DATA_DIR
from . import theme
from .dialogs import default_pictures

BUTTONS = [("share", "Share"), ("email", "Email"), ("print", "Print"), ("export", "Export"),
           ("collage", "Collage"), ("movie", "Movie"), ("geotag", "Geo-Tag"), ("html", "Web Page"),
           ("desktop", "Set as Desktop")]


# ====================================================================== Options

LABEL_W = 200     # every Options tab shares one alignment line: labels end here …
CTRL_W = 280      # … and every control starts here, all the same width


class ThemeTile(QPushButton):
    """A small preview of Light / Dark / Match System with its name underneath."""

    def __init__(self, key, label):
        super().__init__()
        self.key, self.label = key, label
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(88, 70)
        self.setToolTip(label)
        self.setFocusPolicy(Qt.NoFocus)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        box = QRectF(4, 3, self.width() - 8, 44)
        light, dark = QColor("#f4f5f7"), QColor("#232427")
        path = QPainterPath()
        path.addRoundedRect(box, 7, 7)
        p.save()
        p.setClipPath(path)
        if self.key == "system":
            p.fillRect(QRectF(box.left(), box.top(), box.width() / 2, box.height()), light)
            p.fillRect(QRectF(box.center().x(), box.top(), box.width() / 2, box.height()), dark)
        else:
            p.fillRect(box, light if self.key == "light" else dark)
        # a tiny window: sidebar strip + three thumbnails
        for half, col in ((0, light), (1, dark)):
            if self.key != "system" and half == 1:
                break
            fg_ = QColor("#c9ccd2") if (self.key == "light" or (self.key == "system" and half == 0)) else QColor("#45474c")
            x0 = box.left() + (box.width() / 2 if half else 0) if self.key == "system" else box.left()
            w0 = box.width() / 2 if self.key == "system" else box.width()
            p.fillRect(QRectF(x0 + 4, box.top() + 6, w0 * 0.22, box.height() - 12), fg_)
            for i in range(2 if self.key == "system" else 3):
                p.fillRect(QRectF(x0 + 6 + w0 * 0.24 + i * (w0 * 0.24), box.top() + 10, w0 * 0.18, w0 * 0.14), fg_)
        p.restore()
        p.setPen(QPen(theme.ACCENT if self.isChecked() else QColor(128, 128, 128, 90), 2.5 if self.isChecked() else 1))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(box, 7, 7)
        f = self.font()
        f.setPointSizeF(max(8.0, f.pointSizeF() * 0.85))
        f.setBold(self.isChecked())
        p.setFont(f)
        p.setPen(theme.fg())
        p.drawText(QRectF(0, 50, self.width(), 18), Qt.AlignCenter, self.label)
        p.end()


class AccentDot(QPushButton):
    """A round colour swatch; the first one ('Match System') is a multicolour wheel."""

    def __init__(self, key, label, hexcol):
        super().__init__()
        self.key, self.hex = key, hexcol
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(24, 24)
        self.setToolTip(label)
        self.setFocusPolicy(Qt.NoFocus)

    def paintEvent(self, e):
        from PySide6.QtGui import QConicalGradient
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(4, 4, 16, 16)
        if self.hex:
            p.setBrush(QColor(self.hex))
        else:
            g = QConicalGradient(r.center(), 90)
            for i, c in enumerate(("#2f7de1", "#7f5bd6", "#d6458f", "#e0453f", "#f08a24", "#e0b21b", "#3fa34d", "#2f7de1")):
                g.setColorAt(i / 7, QColor(c))
            p.setBrush(g)
        p.setPen(QPen(QColor(0, 0, 0, 40), 0.8))
        p.drawEllipse(r)
        if self.isChecked():
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(theme.fg(), 2))
            p.drawEllipse(QRectF(1, 1, 22, 22))
            p.setBrush(QColor("white"))
            p.setPen(Qt.NoPen)
            p.drawEllipse(QRectF(9.5, 9.5, 5, 5))
        p.end()


class _Page(QWidget):
    """One Options tab: labels right-aligned to a shared centre line, controls left-aligned from it,
    sections separated by a hairline and a heading at the start of the label column."""

    def __init__(self):
        super().__init__()
        self.g = QGridLayout(self)
        self.g.setContentsMargins(20, 6, 20, 14)
        self.g.setHorizontalSpacing(10)
        self.g.setVerticalSpacing(9)
        self.g.setColumnStretch(0, 1)
        self.g.setColumnStretch(3, 1)
        self.g.setColumnMinimumWidth(1, LABEL_W)
        self.g.setColumnMinimumWidth(2, CTRL_W)
        self._r = 0
        self._sections = 0
        self.labels: list[QLabel] = []
        self.headings: list[QLabel] = []
        self.sized: list[QWidget] = []     # controls that take the shared control width

    def _next(self):
        r = self._r
        self._r += 1
        return r

    def heading(self, text):
        if self._sections:
            line = QFrame()
            line.setFrameShape(QFrame.HLine)
            line.setFrameShadow(QFrame.Plain)
            line.setObjectName("OptSep")
            self.g.addWidget(line, self._next(), 0, 1, 4)
        self._sections += 1
        h = QLabel(text)
        h.setObjectName("OptHeading")
        # section titles start where the label column starts, just left of the longest label
        self.g.addWidget(h, self._next(), 1, 1, 2, Qt.AlignLeft)
        self.headings.append(h)

    def row(self, label, *controls, fixed=True):
        r = self._next()
        lab = QLabel(label)
        lab.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.g.addWidget(lab, r, 1)
        self.labels.append(lab)
        if len(controls) == 1:
            c = controls[0]
            if fixed and isinstance(c, (QComboBox, QSpinBox, QLineEdit)):
                # put drop-downs in a plain box of the control width, exactly like the button rows, so the
                # Mac lines up their *visible* edges the same way (it insets controls differently otherwise)
                box = QWidget()
                bl = QHBoxLayout(box)
                bl.setContentsMargins(0, 0, 0, 0)
                c.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
                bl.addWidget(c)
                c = box
                c.setProperty("full_width", True)
            if fixed and c.property("full_width"):
                c.setFixedWidth(CTRL_W)
                self.sized.append(c)
            self.g.addWidget(c, r, 2, Qt.AlignLeft | Qt.AlignVCenter)
        else:
            self.g.addWidget(_hrow(*controls), r, 2, Qt.AlignLeft | Qt.AlignVCenter)

    def wide(self, widget, align=Qt.AlignHCenter):
        self.g.addWidget(widget, self._next(), 1, 1, 2, align)

    def note(self, text):
        n = QLabel(text)
        n.setObjectName("Muted")
        n.setAlignment(Qt.AlignHCenter)
        self.wide(n)

    def finish(self):
        self.g.setRowStretch(self._r, 1)

    def set_control_width(self, w: int) -> None:
        self.g.setColumnMinimumWidth(2, w)
        for c in self.sized:
            c.setFixedWidth(w)

    def indent_headings(self, longest: int) -> None:
        """Start the section titles a couple of letters left of the longest label (shared by all tabs)."""
        lead = 2 * self.fontMetrics().averageCharWidth()
        for h in self.headings:
            h.setContentsMargins(max(0, LABEL_W - longest - lead), 0, 0, 0)


def _hrow(*widgets):
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(8)
    for x in widgets:
        h.addWidget(x)
    h.addStretch()
    return w


def _halves(left, right):
    """A control-width row split into two equal halves (a button or label in each)."""
    w = QWidget()
    w.setProperty("full_width", True)
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(8)
    for x in (left, right):
        x.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        h.addWidget(x, 1)
    return w


def _grid(items, cols):
    w = QWidget()
    g = QGridLayout(w)
    g.setContentsMargins(0, 0, 0, 0)
    g.setHorizontalSpacing(22)
    g.setVerticalSpacing(7)
    for i, it in enumerate(items):
        g.addWidget(it, i // cols, i % cols)
    return w


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
        tabs.tabBar().hide()           # pages are switched by a segmented control in the accent colour
        self.tabs = tabs
        seg_row = QHBoxLayout()
        seg_row.addStretch()
        self.seg_holder = seg_row
        v.addLayout(seg_row)
        v.addWidget(tabs)

        # --- General
        g = _Page()
        g.heading("Opening Photos")
        self.open_in = QComboBox()
        self.open_in.addItem("Photo Viewer", "viewer")
        self.open_in.addItem("Edit view", "editor")
        self.open_in.setCurrentIndex(0 if s.get("open_files_in") != "editor" else 1)
        fm_name = {"darwin": "Finder", "win32": "File Explorer"}.get(sys.platform, "the file manager")
        g.row(f"Open files from {fm_name} in:", self.open_in)
        self.viewer_reuse = QComboBox()
        self.viewer_reuse.addItem("Show the new photo in the open viewer", True)
        self.viewer_reuse.addItem("Open a new viewer window", False)
        self.viewer_reuse.setCurrentIndex(0 if s.get("viewer_reuse", True) else 1)
        g.row("When a viewer is already open:", self.viewer_reuse)
        self.caption = QComboBox()
        for k, label in (("none", "None"), ("filename", "Filename"), ("caption", "Caption"),
                         ("resolution", "Resolution"), ("date", "Date taken"), ("tags", "Tags")):
            self.caption.addItem(label, k)
        self.caption.setCurrentIndex(max(0, self.caption.findData(s.get("thumb_caption"))))
        g.row("Thumbnail caption:", self.caption)
        g.heading("Library")
        fm = QPushButton("Folder Manager")
        fm.clicked.connect(self.folder_manager)
        sc = QPushButton("Rescan Now")
        sc.clicked.connect(self.rescan)
        mv = QPushButton("Move…")
        mv.clicked.connect(self._move_db)
        self._btn_need = 2 * max(b_.sizeHint().width() for b_ in (fm, sc, mv)) + 8
        # two equal halves that span exactly the width of the drop-downs above
        g.row("Watched folders:", _halves(fm, sc))
        self._db_path = QLabel()
        self._db_path.setObjectName("Muted")
        self._db_path.setToolTip(str(DATA_DIR))
        g.row("Database:", _halves(self._db_path, mv))
        g.heading("Places")
        self.tiles = QCheckBox("Load OpenStreetMap tiles")
        self.tiles.setChecked(bool(s.get("map_tiles")))
        g.row("Map:", self.tiles)
        g.finish()
        tabs.addTab(g, "General")

        # --- Appearance
        ap = _Page()
        ap.heading("Look")
        self._saved_look = (s.get("theme") or "system", s.get("accent") or "system", s.get("library_bg") or "neutral")
        self.theme_choice, self.accent_choice, _bg = self._saved_look
        self.theme_tiles = {}
        tiles = []
        for key, label in (("system", "Match System"), ("light", "Light"), ("dark", "Dark")):
            t = ThemeTile(key, label)
            t.setChecked(key == self.theme_choice)
            t.clicked.connect(lambda _=False, k=key: self._pick_theme(k))
            self.theme_tiles[key] = t
            tiles.append(t)
        ap.row("Theme:", *tiles)
        self.accent_dots = {}
        dots = []
        for key, label, hexcol in theme.ACCENTS:
            d = AccentDot(key, label, hexcol)
            d.setChecked(key == self.accent_choice)
            d.clicked.connect(lambda _=False, k=key: self._pick_accent(k))
            self.accent_dots[key] = d
            dots.append(d)
        dot_row = _hrow(*dots)
        dot_row.layout().setSpacing(max(2, (CTRL_W - 24 * len(dots)) // (len(dots) - 1)))
        ap.row("Accent colour:", dot_row)
        ap.heading("Library")
        self.thumb_size = QComboBox()
        cur_px = int(s.get("thumb_size") or 160)
        for label, px in (("Small", 110), ("Medium", 160), ("Large", 240)):
            self.thumb_size.addItem(label, px)
        self.thumb_size.setCurrentIndex(min(range(3), key=lambda i: abs(self.thumb_size.itemData(i) - cur_px)))
        self._thumb_start = self.thumb_size.currentIndex()
        ap.row("Thumbnail size:", self.thumb_size)
        self.library_bg = QComboBox()
        self.library_bg.addItem("Neutral", "neutral")
        self.library_bg.addItem("Gallery (darker backdrop)", "gallery")
        self.library_bg.setCurrentIndex(max(0, self.library_bg.findData(_bg)))
        self.library_bg.currentIndexChanged.connect(lambda _i: self._preview_look())
        ap.row("Background:", self.library_bg)
        ap.finish()
        tabs.addTab(ap, "Appearance")

        # --- File types
        ft = _Page()
        self.type_checks = {}
        skip = {e.lower() for e in (s.get("file_types") or [])}
        for i, (title, exts) in enumerate((("Photos", sorted(media.IMAGE_EXT)), ("Camera RAW", sorted(media.RAW_EXT)),
                                           ("Videos", sorted(media.VIDEO_EXT)))):
            ft.heading(title)
            boxes = []
            for ext in exts:
                cb = QCheckBox(ext.lstrip(".").upper())
                cb.setChecked(ext not in skip)
                self.type_checks[ext] = cb
                boxes.append(cb)
            ft.wide(_grid(boxes, 6))
        ft.note("Unchecked file types are skipped when Fotohaus scans your folders.")
        ft.finish()
        tabs.addTab(ft, "File Types")

        # --- Photo Viewer (file associations)
        pv = _Page()
        self.viewer_checks = {}
        if macos.IS_MAC:
            pv.heading("Open with Fotohaus")
            self.viewer_checks = {}
            boxes = []
            for label, uti in macos.VIEWER_TYPES:
                cb = QCheckBox(label)
                cur = macos.default_app_for_type(uti)
                cb.setChecked(bool(cur and cur.endswith("Fotohaus.app")))
                cb.setToolTip(f"Currently opens with: {os.path.basename(cur) if cur else 'unknown'}")
                self.viewer_checks[uti] = cb
                boxes.append(cb)
            pv.wide(_grid(boxes, 3))
            allb = QPushButton("Select All")
            allb.clicked.connect(lambda: [c.setChecked(True) for c in self.viewer_checks.values()])
            apply_ = QPushButton("Make Fotohaus the Default")
            apply_.clicked.connect(self._apply_viewer)
            for b_ in (allb, apply_):
                b_.setMinimumWidth(b_.sizeHint().width())
            pv.wide(_hrow(allb, apply_))
            pv.note("Use ← and → to browse the folder, Esc to close.")
            pv.note("Or right-click any photo in Finder and choose Open With ▸ Fotohaus.")
            pv.finish()
        elif macos.IS_WIN:
            pv.heading("Open with Fotohaus")
            pv.note("Fotohaus is listed under Open with for photos and videos. Windows lets only you "
                    "choose the default app: in Settings, pick Photos (or a file type such as .jpg) and "
                    "choose Fotohaus.")
            ws = QPushButton("Open Default Apps Settings")
            ws.clicked.connect(macos.open_windows_default_apps)
            ws.setMinimumWidth(ws.sizeHint().width())
            pv.wide(_hrow(ws))
            pv.note("Use ← and → to browse the folder, Esc to close.")
            pv.finish()
        else:
            pv.heading("Open with Fotohaus")
            pv.note("Adds Fotohaus to your applications menu and makes it the app that opens photos and "
                    "videos when you double-click them.")
            lx = QPushButton("Make Fotohaus the Default")
            lx.clicked.connect(self._apply_viewer_linux)
            lx.setMinimumWidth(lx.sizeHint().width())
            pv.wide(_hrow(lx))
            pv.note("Use ← and → to browse the folder, Esc to close.")
            pv.finish()
        tabs.addTab(pv, "Photo Viewer")

        # --- Name tags & metadata
        md = _Page()
        md.heading("Picasa Files")
        self.write_ini = QCheckBox("Keep .picasa.ini files in sync")
        self.write_ini.setToolTip("Stars, captions, albums, name tags and edits")
        self.write_ini.setChecked(bool(s.get("write_picasa_ini")))
        md.row("Sidecar files:", self.write_ini)
        imp = QPushButton("Import contacts.xml…")
        imp.clicked.connect(self._contacts)
        imp.setMinimumWidth(imp.sizeHint().width())
        md.row("Name tags:", imp)
        md.heading("Inside JPEG Files")
        self.write_meta = QCheckBox("Store captions and tags (IPTC/XMP)")
        self.write_meta.setChecked(bool(s.get("write_meta_in_files")))
        md.row("Captions and tags:", self.write_meta)
        self.write_faces = QCheckBox("Store name tags (XMP face regions)")
        self.write_faces.setChecked(bool(s.get("write_faces_in_files")))
        md.row("Name tags:", self.write_faces)
        md.note("The image itself is never re-compressed.")
        md.finish()
        tabs.addTab(md, "Name Tags && Metadata")

        # --- Intelligence
        ai = _Page()
        ai.heading("Recognition")
        self.faces_cb = QCheckBox("Find and recognize faces")
        self.faces_cb.setChecked(bool(s.get("faces_enabled")))
        self.faces_cb.setEnabled(faces.available())
        ai.row("Faces:", self.faces_cb)
        self.vision_cb = QCheckBox("Objects, scenes and text")
        self.vision_cb.setToolTip("Uses Apple Vision, on your Mac")
        self.vision_cb.setChecked(bool(s.get("vision_enabled")))
        self.vision_cb.setEnabled(vision_mac.available())
        ai.row("Apple Vision:", self.vision_cb)
        rb = QPushButton("Re-analyze All Photos")
        rb.clicked.connect(self.reindex)
        rb.setMinimumWidth(rb.sizeHint().width())
        ai.row("Library:", rb)
        ai.heading("Natural-Language Search")
        self.clip_cb = QCheckBox("Search by description")
        self.clip_cb.setToolTip("For example “kids playing in the snow”")
        self.clip_cb.setChecked(bool(s.get("clip_enabled")))
        ai.row("Search:", self.clip_cb)
        self.clip_btn = QPushButton("Download (155 MB)")
        self.clip_btn.clicked.connect(self.download_clip)
        self.clip_btn.setEnabled(not clip.downloaded())
        self.clip_btn.setMinimumWidth(self.clip_btn.sizeHint().width())
        self.clip_status = QLabel("Model installed." if clip.downloaded() else "Not downloaded yet.")
        self.clip_status.setObjectName("Muted")
        ai.row("Model:", self.clip_btn, self.clip_status)
        ai.note("Everything here runs on your Mac. No photo ever leaves your computer.")
        ai.finish()
        tabs.addTab(ai, "Intelligence")

        # --- Email & Slideshow
        es = _Page()
        es.heading("Slideshow")
        # a drop-down like the others (the Mac's spin box is drawn wider and breaks the line)
        self.secs = QComboBox()
        cur_secs = int(s.get("slideshow_seconds") or 4)
        choices = sorted({1, 2, 3, 4, 5, 7, 10, 15, 20, 30, 60, cur_secs})
        for n in choices:
            self.secs.addItem(f"{n} second{'s' if n != 1 else ''}", n)
        self.secs.setCurrentIndex(self.secs.findData(cur_secs))
        es.row("Show each photo for:", self.secs)
        self.trans = QComboBox()
        for k, label in (("dissolve", "Dissolve"), ("panzoom", "Pan and zoom"), ("wipe", "Wipe"),
                         ("shrink", "Shrink"), ("none", "None"), ("random", "Random")):
            self.trans.addItem(label, k)
        self.trans.setCurrentIndex(max(0, self.trans.findData(s.get("slideshow_transition"))))
        es.row("Transition:", self.trans)
        self.capt = QCheckBox("Show captions")
        self.capt.setChecked(bool(s.get("slideshow_captions")))
        es.row("Captions:", self.capt)
        es.heading("Email")
        self.email = QComboBox()
        for label, px in (("Small (640 px)", 640), ("Medium (800 px)", 800), ("Large (1024 px)", 1024),
                          ("Extra large (1600 px)", 1600), ("Original size", 0)):
            self.email.addItem(label, px)
        self.email.setCurrentIndex(max(0, self.email.findData(int(s.get("email_size") or 800))))
        es.row("Picture size:", self.email)
        es.finish()
        tabs.addTab(es, "Email && Slideshow")

        # --- Buttons
        bt = _Page()
        bt.heading("Buttons at the Bottom of the Window")
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
        self.btn_list.setFixedHeight(self.btn_list.sizeHintForRow(0) * len(order) + 6)
        self.btn_list.setFixedWidth(LABEL_W + CTRL_W + 10)
        self.btn_list.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        bt.wide(self.btn_list)
        bt.note("Check the buttons to show, and drag to change their order.")
        bt.finish()
        tabs.addTab(bt, "Buttons")

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)
        pages = [tabs.widget(i) for i in range(tabs.count()) if isinstance(tabs.widget(i), _Page)]
        longest = max((lab.sizeHint().width() for pg in pages for lab in pg.labels), default=0)
        ctrl_w = max(CTRL_W, self._btn_need)
        for pg in pages:
            pg.indent_headings(longest)
            pg.set_control_width(ctrl_w)
        half = (ctrl_w - 8) // 2
        self._db_path.setText(self._db_path.fontMetrics().elidedText(
            str(DATA_DIR).replace(str(Path.home()), "~"), Qt.ElideMiddle, half))
        # wide enough that every tab name fits on one row
        from .panels import Segmented
        self.seg = Segmented([tabs.tabText(i).replace("&&", "&") for i in range(tabs.count())], fit=True, accent=True)
        self.seg.changed.connect(tabs.setCurrentIndex)
        tabs.currentChanged.connect(lambda i: self.seg.set_index(i))
        self.seg_holder.addWidget(self.seg)
        self.seg_holder.addStretch()
        need = self.seg.sizeHint().width() + 48
        self.setMinimumWidth(need)
        self._width = max(need, 680)
        tabs.currentChanged.connect(self._fit_to_tab)
        self._fit_to_tab(tabs.currentIndex())

    def _fit_to_tab(self, index: int) -> None:
        """Size the window to the current tab, like macOS settings windows, so OK/Cancel sit
        right under the content instead of below a block of empty space."""
        from PySide6.QtWidgets import QSizePolicy
        for i in range(self.tabs.count()):
            pol = QSizePolicy.Preferred if i == index else QSizePolicy.Ignored
            self.tabs.widget(i).setSizePolicy(pol, pol)
        self.tabs.updateGeometry()
        self.layout().invalidate()
        self.layout().activate()          # recompute the minimum size before resizing
        page = self.tabs.widget(index)
        w = max(self.width(), self._width) - 30
        lay = page.layout()
        page_h = lay.totalHeightForWidth(w) if lay.hasHeightForWidth() else page.sizeHint().height()
        chrome = 12                                                # pane frame
        extra = self.sizeHint().height() - self.tabs.sizeHint().height()   # margins + OK/Cancel row
        self.resize(max(self.width(), self._width) if self.isVisible() else self._width,
                    page_h + chrome + extra)

    def set_page(self, i: int) -> None:
        if 0 <= i < self.tabs.count():
            self.tabs.setCurrentIndex(i)

    def _apply_viewer_linux(self):
        ok = macos.install_linux_desktop_entry(make_default=True)
        QMessageBox.information(self, "Photo Viewer", "Done. Double-clicking photos and videos now opens "
                                "Fotohaus's Photo Viewer." if ok else
                                "Fotohaus was added to your applications menu, but the defaults couldn't be "
                                "changed (xdg-mime is missing). Choose Fotohaus in Open With instead.")

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
        except Exception as e:          # OSError, or sqlite3.Error from the database copy
            QMessageBox.warning(self, "Move Database", str(e))

    # ---- appearance: previewed live, kept on OK, undone on Cancel
    def _pick_theme(self, key):
        self.theme_choice = key
        for k, t in self.theme_tiles.items():
            t.setChecked(k == key)
        self._preview_look()

    def _pick_accent(self, key):
        self.accent_choice = key
        for k, d in self.accent_dots.items():
            d.setChecked(k == key)
        self._preview_look()

    def _preview_look(self):
        from PySide6.QtWidgets import QApplication
        theme.apply_appearance(QApplication.instance(), self.theme_choice, self.accent_choice,
                               self.library_bg.currentData())

    def reject(self):
        from PySide6.QtWidgets import QApplication
        if (self.theme_choice, self.accent_choice, self.library_bg.currentData()) != self._saved_look:
            theme.apply_appearance(QApplication.instance(), *self._saved_look)
        super().reject()

    def accept(self):
        s = self.lib.settings
        s.set("theme", self.theme_choice)
        s.set("accent", self.accent_choice)
        s.set("library_bg", self.library_bg.currentData())
        if self.thumb_size.currentIndex() != self._thumb_start:
            s.set("thumb_size", int(self.thumb_size.currentData()))
        self._preview_look()
        s.set("open_files_in", self.open_in.currentData())
        s.set("viewer_reuse", bool(self.viewer_reuse.currentData()))
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
        s.set("slideshow_seconds", int(self.secs.currentData()))
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
    """Folder Manager: the folders Fotohaus watches, the subfolders it skips, and (folded away) a browser
    of every folder on the Mac for fine-grained control."""

    MODES = [("Keep up to date", "folder"), ("Scan once", "once")]

    def __init__(self, lib, parent=None):
        super().__init__(parent)
        self.lib = lib
        self.setWindowTitle("Folder Manager")
        self.setMinimumWidth(640)
        v = QVBoxLayout(self)
        v.setContentsMargins(18, 14, 18, 12)
        v.setSpacing(8)
        intro = QLabel("Fotohaus shows the photos in these folders. Your files are never moved or changed.")
        intro.setObjectName("Muted")
        v.addWidget(intro)

        v.addWidget(self._heading("Watched Folders"))
        self.watched = self._list_box()
        v.addWidget(self.watched["frame"])
        add = QPushButton("Add Folder…")
        add.setIcon(theme.icon("plus"))
        add.clicked.connect(self._add)
        v.addWidget(add, 0, Qt.AlignLeft)

        v.addSpacing(6)
        v.addWidget(self._heading("Skipped Folders"))
        self.skipped = self._list_box()
        v.addWidget(self.skipped["frame"])

        # ---- advanced: the full folder tree, folded away
        self.more = QToolButton()
        self.more.setText("Browse all folders on this Mac")
        self.more.setCheckable(True)
        self.more.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.more.setArrowType(Qt.RightArrow)
        self.more.setAutoRaise(True)
        self.more.toggled.connect(self._toggle_browser)
        v.addWidget(self.more, 0, Qt.AlignLeft)
        self.browser = self._build_browser()
        self.browser.hide()
        v.addWidget(self.browser, 1)

        foot = QHBoxLayout()
        self.summary = QLabel()
        self.summary.setObjectName("Muted")
        foot.addWidget(self.summary, 1)
        rescan = QPushButton("Rescan Now")
        rescan.clicked.connect(self.accept)          # the main window rescans when this closes
        done = QPushButton("Done")
        done.setDefault(True)
        done.clicked.connect(self.accept)
        foot.addWidget(rescan)
        foot.addWidget(done)
        v.addLayout(foot)
        self._fill()

    # ------------------------------------------------------------------ building blocks
    @staticmethod
    def _heading(text):
        h = QLabel(text)
        h.setObjectName("OptHeading")
        return h

    @staticmethod
    def _list_box():
        frame = QFrame()
        frame.setObjectName("FolderList")
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        return {"frame": frame, "lay": lay}

    def _photo_count(self, path):
        p = path.rstrip(os.sep)
        from ..db import under
        w, a = under("path", p, include_self=False)
        return self.lib.db.scalar(f"SELECT COUNT(*) FROM photos WHERE {w}", a, default=0)

    @staticmethod
    def _nice(path):
        home = str(Path.home())
        return "~" + path[len(home):] if path == home or path.startswith(home + os.sep) else path

    def _row(self, box, root, skipped=False):
        lay = box["lay"]
        if lay.count():
            sep = QFrame()
            sep.setObjectName("OptSep")
            sep.setFrameShape(QFrame.HLine)
            lay.addWidget(sep)
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(12, 8, 10, 8)
        h.setSpacing(10)
        ic = QLabel()
        ic.setPixmap(theme.icon("eye-off" if skipped else "folder",
                                None if skipped else theme.ACCENT).pixmap(20, 20))
        h.addWidget(ic)
        txt = QVBoxLayout()
        txt.setSpacing(1)
        path = root["path"]
        name = QLabel(os.path.basename(path.rstrip(os.sep)) or path)
        name.setObjectName("FolderName")
        sub = self._nice(path)
        if not skipped:
            n = self._photo_count(path)
            sub += f"  ·  {n:,} item{'s' if n != 1 else ''}"
        detail = QLabel(sub)
        detail.setObjectName("Muted")
        detail.setToolTip(path)
        txt.addWidget(name)
        txt.addWidget(detail)
        h.addLayout(txt, 1)
        if skipped:
            again = QPushButton("Show Again")
            again.clicked.connect(lambda _=False, p=path: self._set(p, None))
            h.addWidget(again)
        elif root["kind"] == "applephotos":
            lab = QLabel("Apple Photos (read-only)")
            lab.setObjectName("Muted")
            h.addWidget(lab)
        else:
            mode = QComboBox()
            for label, k in self.MODES:
                mode.addItem(label, k)
            mode.setCurrentIndex(max(0, mode.findData(root["kind"])))
            mode.setFixedWidth(160)
            mode.currentIndexChanged.connect(lambda _i, p=path, c=mode: self._set(p, c.currentData()))
            h.addWidget(mode)
        if not skipped:
            rm = QToolButton()
            rm.setIcon(theme.icon("close"))
            rm.setAutoRaise(True)
            rm.setToolTip("Stop watching this folder (the files stay on your Mac)")
            rm.clicked.connect(lambda _=False, p=path: self._remove(p))
            h.addWidget(rm)
        lay.addWidget(w)

    def _empty(self, box, text):
        lab = QLabel(text)
        lab.setObjectName("Muted")
        lab.setContentsMargins(12, 10, 12, 10)
        box["lay"].addWidget(lab)

    # ------------------------------------------------------------------ data
    def _fill(self):
        for box in (self.watched, self.skipped):
            lay = box["lay"]
            while lay.count():
                it = lay.takeAt(0)
                if it.widget():
                    it.widget().deleteLater()
        roots = list(self.lib.roots())
        watched = [r for r in roots if r["kind"] != "exclude"]
        skipped = [r for r in roots if r["kind"] == "exclude"]
        for r in watched:
            self._row(self.watched, r)
        if not watched:
            self._empty(self.watched, "No folders yet. Click Add Folder… to choose where your photos are.")
        for r in skipped:
            self._row(self.skipped, r, skipped=True)
        if not skipped:
            self._empty(self.skipped, "Nothing is skipped. To skip a folder, right-click it in the sidebar and choose Remove from Fotohaus.")
        self.summary.setText(f"{len(watched)} folder{'s' if len(watched) != 1 else ''} watched  ·  "
                             f"{len(skipped)} skipped")
        if hasattr(self, "proxy"):
            self.proxy.refresh()
            self._cur(self.tree.currentIndex())
        self.adjustSize()

    def _set(self, path, mode):
        self.lib.set_folder_mode(path, mode)
        self.changed = True
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, self._fill)       # rebuild after the clicked widget's signal returns

    def _remove(self, path):
        if QMessageBox.question(self, "Stop Watching", f"Stop showing the photos in “{os.path.basename(path)}”?\n\n"
                                "The files stay on your Mac, untouched.") == QMessageBox.Yes:
            self._set(path, None)

    def _add(self):
        d = QFileDialog.getExistingDirectory(self, "Add a folder to Fotohaus", default_pictures())
        if d:
            self._set(d, "folder")

    # ------------------------------------------------------------------ advanced browser
    def _build_browser(self):
        box = QWidget()
        h = QHBoxLayout(box)
        h.setContentsMargins(0, 0, 0, 0)
        self.fs = QFileSystemModel(self)
        self.fs.setFilter(QDir.AllDirs | QDir.NoDotAndDotDot)
        home_path = str(Path.home())
        self.fs.setRootPath(home_path)
        self.proxy = _ModeProxy(self.lib, self)
        self.proxy.setSourceModel(self.fs)
        self.tree = QTreeView()
        self.tree.setModel(self.proxy)
        for c in (1, 2, 3):
            self.tree.hideColumn(c)
        self.tree.setHeaderHidden(True)
        self.tree.setRootIndex(self.proxy.mapFromSource(self.fs.index(home_path)))
        self.tree.setMinimumHeight(240)
        self.tree.selectionModel().currentChanged.connect(self._cur)
        h.addWidget(self.tree, 3)
        side = QVBoxLayout()
        self.path_lbl = QLabel("Select a folder")
        self.path_lbl.setObjectName("FolderName")
        self.path_lbl.setWordWrap(True)
        side.addWidget(self.path_lbl)
        self.radios = {}
        group = QButtonGroup(self)
        for mode, label in (("folder", "Keep up to date"), ("once", "Scan once"), ("exclude", "Skip"),
                            (None, "Same as the folder above")):
            rb = QRadioButton(label)
            group.addButton(rb)
            self.radios[mode] = rb
            rb.toggled.connect(lambda on, m=mode: on and not self._suppress and self._set(self._path(), m))
            side.addWidget(rb)
        side.addStretch()
        h.addLayout(side, 2)
        self._suppress = False
        return box

    def _toggle_browser(self, on):
        self.more.setArrowType(Qt.DownArrow if on else Qt.RightArrow)
        self.browser.setVisible(on)
        self.adjustSize()

    def _path(self, idx=None) -> str:
        idx = idx or self.tree.currentIndex()
        return self.fs.filePath(self.proxy.mapToSource(idx))

    def _cur(self, idx, *_):
        if not idx.isValid():
            for rb in self.radios.values():
                rb.setEnabled(False)
            return
        for rb in self.radios.values():
            rb.setEnabled(True)
        p = self._path(idx)
        self.path_lbl.setText(self._nice(p))
        mode, inherited = self.lib.folder_mode(p)
        self._suppress = True
        self.radios[None if inherited or mode is None else mode].setChecked(True)
        self._suppress = False


# ====================================================================== Import

class _ThumbSig(QObject):
    done = Signal(int, int, QImage)      # generation, row, image


class _ImportThumb(QRunnable):
    def __init__(self, sig, gen, i, path):
        super().__init__()
        self.sig, self.gen, self.i, self.path = sig, gen, i, path

    def run(self):
        img = QImage()
        try:
            from .editor import pil_to_qimage
            img = pil_to_qimage(media.load_image(self.path, max_dim=160))
        except Exception:
            pass
        self.sig.done.emit(self.gen, self.i, img)


class ImportDialog(QDialog):
    def __init__(self, lib, jobs, parent=None):
        super().__init__(parent)
        from .. import importer
        self.lib, self.jobs = lib, jobs
        self.items = []
        from PySide6.QtCore import QThreadPool
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(3)
        self._thumb_sig = _ThumbSig()
        self._thumb_sig.done.connect(self._thumb_ready)
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
            self.grid.addItem(w)
        self.grid.blockSignals(False)
        self._count()
        # thumbnails load in the background: a card with thousands of pictures (or RAW files) would
        # otherwise freeze the window while every preview is decoded
        self._thumb_gen = getattr(self, "_thumb_gen", 0) + 1
        for i, it in enumerate(items):
            if media.kind_of(it.path) != "video":
                self._pool.start(_ImportThumb(self._thumb_sig, self._thumb_gen, i, it.path))

    def _thumb_ready(self, gen, i, img):
        if gen == getattr(self, "_thumb_gen", 0) and 0 <= i < self.grid.count() and not img.isNull():
            self.grid.blockSignals(True)
            self.grid.item(i).setIcon(QIcon(QPixmap.fromImage(img)))
            self.grid.blockSignals(False)

    def done(self, r):
        self._thumb_gen = getattr(self, "_thumb_gen", 0) + 1     # ignore thumbnails still loading
        self._pool.clear()
        super().done(r)

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
