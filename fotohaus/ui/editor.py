"""Photo editor view (non-destructive, Picasa-compatible edit chain)."""
from __future__ import annotations

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QImage, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (QColorDialog, QComboBox, QGridLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
                               QListWidget, QMessageBox, QPushButton, QScrollArea, QSizePolicy, QSlider,
                               QStackedWidget, QTabWidget, QToolButton, QVBoxLayout, QWidget)

from .. import media
from ..edits import FILTERS, apply_filter, label_for, render
from ..picasa.ini import Filter
from . import theme
from .canvas import ASPECTS, ImageCanvas

PREVIEW_MAX = 1800

EFFECT_PAGES = {
    "Effects": ["unsharp2", "sepia", "bw", "warm", "grain2", "tint", "sat", "radblur", "glow2", "ansel", "radsat",
                "dir_tint"],
    "More": ["IR", "Lomo", "Holga", "HDR", "Cinemascope", "Orton", "Sixties", "Invert", "HeatMap", "CrossProcess",
             "QuantizePalette", "TwoTone"],
    "Even more": ["Boost", "Soften", "Vignette", "Pixelate", "PicnikFocalPixelate", "PencilSketch", "Neon", "Comicize",
                  "Border", "DropShadow", "Polaroid", "MuseumMatte", "RoundedEdges", "Matte", "NightVision",
                  "FocalZoom", "LocalContrast", "linblur"],
}
EFFECTS = [f for page in EFFECT_PAGES.values() for f in page]
CLASSIC_TUNING = ["colortemp", "shadow", "contrast", "gamma", "backlight", "dir_sat", "dir_brite", "dir_sharp",
                  "radtint", "blur"]
SMART = ["fh_smart", "fh_denoise", "fh_clarity", "fh_vibrance", "fh_exposure", "fh_contrast", "fh_hsl"]
POINT_EFFECTS = {"radblur", "radsat", "dir_tint", "FocalZoom", "PicnikFocalPixelate", "radtint"}
COLOR_EFFECTS = {"tint": QColor(160, 120, 80), "ansel": QColor(255, 150, 50), "dir_tint": QColor(70, 110, 190),
                 "PicnikTint": QColor(160, 120, 80), "radtint": QColor(60, 90, 170), "Border": QColor(255, 255, 255),
                 "TwoTone": QColor(30, 30, 90)}


def font_files() -> dict[str, str]:
    """Readable name -> font file, from the standard macOS / Linux font folders."""
    import glob
    import os
    out = {}
    for d in ("/System/Library/Fonts", "/System/Library/Fonts/Supplemental", "/Library/Fonts",
              os.path.expanduser("~/Library/Fonts"), "/usr/share/fonts/truetype"):
        for f in glob.glob(os.path.join(d, "**", "*.tt[fc]"), recursive=True) + glob.glob(os.path.join(d, "*.otf")):
            name = os.path.splitext(os.path.basename(f))[0]
            if not name.startswith("."):
                out.setdefault(name, f)
    return dict(sorted(out.items(), key=lambda x: x[0].lower()))


def pil_to_qimage(im) -> QImage:
    im = im.convert("RGB")
    data = im.tobytes("raw", "RGB")
    q = QImage(data, im.width, im.height, im.width * 3, QImage.Format_RGB888)
    return q.copy()


def color_hex(c: QColor) -> str:
    return f"ff{c.red():02x}{c.green():02x}{c.blue():02x}"


class _Signals(QObject):
    done = Signal(int, object, object)   # generation, payload, kind


class _Task(QRunnable):
    def __init__(self, sig, gen, kind, fn):
        super().__init__()
        self.sig, self.gen, self.kind, self.fn = sig, gen, kind, fn

    def run(self):
        try:
            res = self.fn()
        except Exception as e:  # pragma: no cover
            res = e
        self.sig.done.emit(self.gen, res, self.kind)


class ParamSlider(QWidget):
    changed = Signal()

    def __init__(self, label, lo, hi, val, parent=None):
        super().__init__(parent)
        self.lo, self.hi = lo, hi
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 2, 0, 2)
        v.setSpacing(1)
        top = QHBoxLayout()
        self.lab = QLabel(label)
        self.val = QLabel()
        self.val.setObjectName("Muted")
        top.addWidget(self.lab)
        top.addStretch()
        top.addWidget(self.val)
        v.addLayout(top)
        self.s = QSlider(Qt.Horizontal)
        self.s.setRange(0, 1000)
        self.s.valueChanged.connect(self._ch)
        v.addWidget(self.s)
        self.set_value(val)

    def value(self) -> float:
        return self.lo + (self.hi - self.lo) * self.s.value() / 1000.0

    def set_value(self, v: float):
        self.s.blockSignals(True)
        self.s.setValue(int(round((v - self.lo) / max(1e-9, self.hi - self.lo) * 1000)))
        self.s.blockSignals(False)
        self._label()

    def _label(self):
        self.val.setText(f"{self.value():+.2f}" if self.lo < 0 else f"{self.value():.2f}")

    def _ch(self, _):
        self._label()
        self.changed.emit()


class EditorView(QWidget):
    back = Signal()
    changed = Signal(int)
    export_requested = Signal(list)
    navigate = Signal(int)

    def __init__(self, lib, loader, jobs, parent=None):
        super().__init__(parent)
        self.lib, self.loader, self.jobs = lib, loader, jobs
        self.pid = None
        self.ids: list[int] = []
        self.base = None            # PIL, oriented, preview size
        self.committed = None       # PIL render of rotate+chain
        self.chain: list[Filter] = []
        self.rotate = 0
        self.undo: list = []
        self.redo: list = []
        self.pending: Filter | None = None
        self.session = None         # dict describing the active adjustment
        self.gen = 0
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.fx_pool = QThreadPool(self)
        self.fx_pool.setMaxThreadCount(2)
        self.sig = _Signals()
        self.sig.done.connect(self._task_done)
        self._live = QTimer(self, singleShot=True, interval=70, timeout=self._render_pending)
        self._build()

    # ------------------------------------------------------------------ layout
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        top = QWidget()
        top.setObjectName("EditorBar")
        th = QHBoxLayout(top)
        th.setContentsMargins(8, 6, 8, 6)
        b = QPushButton(theme.icon("back"), " Back to Library")
        b.clicked.connect(self._back)
        th.addWidget(b)
        th.addSpacing(12)
        self.prev_btn = QToolButton()
        self.prev_btn.setIcon(theme.icon("back"))
        self.prev_btn.clicked.connect(lambda: self._step(-1))
        self.next_btn = QToolButton()
        self.next_btn.setIcon(theme.icon("fwd"))
        self.next_btn.clicked.connect(lambda: self._step(1))
        th.addWidget(self.prev_btn)
        th.addWidget(self.next_btn)
        self.name_lbl = QLabel()
        self.name_lbl.setObjectName("Muted")
        th.addWidget(self.name_lbl)
        th.addStretch()
        self.faces_btn = QToolButton()
        self.faces_btn.setText("People")
        self.faces_btn.setIcon(theme.icon("people"))
        self.faces_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.faces_btn.setCheckable(True)
        self.faces_btn.toggled.connect(self._toggle_faces)
        th.addWidget(self.faces_btn)
        self.overflow_btn = QToolButton()
        self.overflow_btn.setText("Overflow")
        self.overflow_btn.setToolTip("Show overflow pixels: clipped highlights in red, clipped shadows in blue (O)")
        self.overflow_btn.setCheckable(True)
        self.overflow_btn.toggled.connect(lambda _: self._redisplay())
        th.addWidget(self.overflow_btn)
        zb = QToolButton()
        zb.setText("Fit / 100%")
        zb.clicked.connect(lambda: self.canvas.toggle_zoom())
        th.addWidget(zb)
        root.addWidget(top)
        from .grid import PhotoGrid
        from PySide6.QtWidgets import QListView
        self.strip = PhotoGrid(self.loader)
        self.strip.setFlow(QListView.LeftToRight)
        self.strip.setWrapping(False)
        self.strip.setFixedHeight(92)
        self.strip.set_thumb_size(64)
        self.strip.setSelectionMode(QListView.SingleSelection)
        self.strip.setHorizontalScrollMode(QListView.ScrollPerPixel)
        self.strip.setDragEnabled(False)
        self.strip.clicked.connect(self._strip_clicked)
        root.addWidget(self.strip)

        mid = QHBoxLayout()
        mid.setContentsMargins(0, 0, 0, 0)
        mid.setSpacing(0)
        left = QWidget()
        left.setFixedWidth(272)
        lv = QVBoxLayout(left)
        lv.setContentsMargins(8, 8, 8, 8)
        self.left_stack = QStackedWidget()
        self.tabs = QTabWidget()
        self.tabs.setObjectName("EditTabs")
        self.tabs.addTab(self._tab_fix(), "Fix")
        self.tabs.addTab(self._tab_tune(), "Tune")
        self.tabs.addTab(self._tab_effects(), "Effects")
        self.tabs.addTab(self._tab_smart(), "Smart")
        self.tabs.currentChanged.connect(self._tab_changed)
        self.left_stack.addWidget(self.tabs)
        self.panel = QWidget()
        self.panel_v = QVBoxLayout(self.panel)
        self.panel_v.setContentsMargins(4, 4, 4, 4)
        self.left_stack.addWidget(self.panel)
        lv.addWidget(self.left_stack, 3)
        hist_lbl = QLabel("EDIT HISTORY")
        hist_lbl.setObjectName("Section")
        lv.addWidget(hist_lbl)
        self.history = QListWidget()
        self.history.setMaximumHeight(130)
        lv.addWidget(self.history)
        ur = QHBoxLayout()
        self.undo_btn = QPushButton("Undo")
        self.undo_btn.clicked.connect(self.do_undo)
        self.redo_btn = QPushButton("Redo")
        self.redo_btn.clicked.connect(self.do_redo)
        ur.addWidget(self.undo_btn)
        ur.addWidget(self.redo_btn)
        lv.addLayout(ur)
        sr = QHBoxLayout()
        self.save_btn = QPushButton("Save to File")
        self.save_btn.setToolTip("Write the edits into the file. The original is kept in a "
                                 "'.picasaoriginals' folder so you can undo this later.")
        self.save_btn.clicked.connect(self._save)
        self.revert_btn = QPushButton("Revert")
        self.revert_btn.clicked.connect(self._revert)
        sr.addWidget(self.save_btn)
        sr.addWidget(self.revert_btn)
        lv.addLayout(sr)
        exp = QPushButton(theme.icon("export"), " Export…")
        exp.clicked.connect(lambda: self.pid and self.export_requested.emit([self.pid]))
        lv.addWidget(exp)
        mid.addWidget(left)

        center = QVBoxLayout()
        center.setContentsMargins(0, 0, 0, 0)
        center.setSpacing(0)
        self.canvas = ImageCanvas()
        self.canvas.clicked_rel.connect(self._canvas_click)
        self.canvas.rect_rel.connect(self._canvas_rect)
        self.canvas.face_clicked.connect(self._face_clicked)
        self.canvas.brushed.connect(self._brushed)
        self.center_stack = QStackedWidget()
        self.center_stack.addWidget(self.canvas)
        from .video import VideoPlayer
        self.video = VideoPlayer(trim=True, dark=False)
        self.video.trim_changed.connect(self._trim_changed)
        self.video.snapshot_taken.connect(self._snapshot)
        self.center_stack.addWidget(self.video)
        center.addWidget(self.center_stack, 1)
        bar = QWidget()
        bar.setObjectName("EditorBar")
        bh = QHBoxLayout(bar)
        bh.setContentsMargins(10, 6, 10, 6)
        self.star_btn = QToolButton()
        self.star_btn.setCheckable(True)
        self.star_btn.toggled.connect(self._star)
        bh.addWidget(self.star_btn)
        for name, d in (("rotate-left", -1), ("rotate-right", 1)):
            rb = QToolButton()
            rb.setIcon(theme.icon(name))
            rb.setToolTip("Rotate " + ("left" if d < 0 else "right"))
            rb.clicked.connect(lambda _=False, d=d: self._rotate(d))
            bh.addWidget(rb)
        self.caption = QLineEdit()
        self.caption.setPlaceholderText("Make a caption!")
        self.caption.setAlignment(Qt.AlignCenter)
        self.caption.editingFinished.connect(self._caption)
        bh.addWidget(self.caption, 1)
        center.addWidget(bar)
        cw = QWidget()
        cw.setLayout(center)
        mid.addWidget(cw, 1)
        root.addLayout(mid, 1)

        QShortcut(QKeySequence.Undo, self, self.do_undo)
        QShortcut(QKeySequence.Redo, self, self.do_redo)
        QShortcut(QKeySequence(Qt.Key_Escape), self, self._escape)
        QShortcut(QKeySequence(Qt.Key_Left), self, lambda: self._step(-1))
        QShortcut(QKeySequence(Qt.Key_Right), self, lambda: self._step(1))
        QShortcut(QKeySequence(Qt.Key_O), self, lambda: self.overflow_btn.toggle())

    def _btn(self, text, icon_name=None, cb=None, tip=None):
        b = QPushButton(text)
        b.setObjectName("Effect")
        if icon_name:
            b.setIcon(theme.icon(icon_name))
        if cb:
            b.clicked.connect(cb)
        if tip:
            b.setToolTip(tip)
        return b

    def _scroll(self, w):
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setFrameShape(QScrollArea.NoFrame)
        sa.setWidget(w)
        return sa

    def _tab_fix(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setSpacing(4)
        v.addWidget(self._btn("Crop", None, self._start_crop))
        v.addWidget(self._btn("Straighten", None, self._start_straighten))
        v.addWidget(self._btn("Red-Eye", None, self._start_redeye,
                              "Drag boxes around red eyes, or click Auto, then Apply"))
        v.addWidget(self._btn("Retouch", None, self._start_retouch, "Paint over blemishes, dust or small objects"))
        v.addWidget(self._btn("Text", None, self._start_text, "Add text on top of the photo"))
        v.addSpacing(6)
        v.addWidget(self._btn("Auto Enhance", "sparkle", lambda: self.commit(Filter.make("enhance")),
                              "One-click contrast, color and brightness fix"))
        v.addWidget(self._btn("Smart Enhance (local contrast)", "sparkle",
                              lambda: self.commit(Filter.make("fh_smart", 0.8))))
        v.addWidget(self._btn("Auto Contrast", None, lambda: self.commit(Filter.make("autolight"))))
        v.addWidget(self._btn("Auto Color", None, lambda: self.commit(Filter.make("autocolor"))))
        v.addSpacing(6)
        v.addWidget(self._btn("Fill Light…", None, lambda: self.start_adjust("fill")))
        v.addWidget(self._btn("Reduce Noise…", None, lambda: self.start_adjust("fh_denoise")))
        v.addStretch()
        return self._scroll(w)

    def _tab_tune(self):
        w = QWidget()
        v = QVBoxLayout(w)
        self.tune = {
            "fill": ParamSlider("Fill Light", 0.0, 1.0, 0.0),
            "hi": ParamSlider("Highlights", 0.0, 0.48, 0.0),
            "sh": ParamSlider("Shadows", 0.0, 0.48, 0.0),
            "temp": ParamSlider("Color Temperature", -1.0, 1.0, 0.0),
        }
        for s in self.tune.values():
            s.changed.connect(self._tune_changed)
            v.addWidget(s)
        self.neutral_color = None
        nb = QPushButton("Neutral Color Picker")
        nb.setToolTip("Click something in the photo that should be white or gray")
        nb.clicked.connect(self._pick_neutral)
        v.addWidget(nb)
        self.neutral_lbl = QLabel("")
        self.neutral_lbl.setObjectName("Muted")
        v.addWidget(self.neutral_lbl)
        row = QHBoxLayout()
        ap = QPushButton("Apply")
        ap.setDefault(True)
        ap.clicked.connect(self._tune_apply)
        rs = QPushButton("Reset")
        rs.clicked.connect(self._tune_reset)
        row.addWidget(ap)
        row.addWidget(rs)
        v.addLayout(row)
        more = QLabel("MORE ADJUSTMENTS")
        more.setObjectName("Section")
        v.addWidget(more)
        for fid in CLASSIC_TUNING:
            v.addWidget(self._btn(FILTERS[fid].label + "…", None, lambda _=False, f=fid: self.start_adjust(f)))
        v.addStretch()
        return self._scroll(w)

    def _tab_effects(self):
        pages = QTabWidget()
        pages.setDocumentMode(True)
        self.fx_buttons = {}
        for title, fids in EFFECT_PAGES.items():
            w = QWidget()
            g = QGridLayout(w)
            g.setSpacing(6)
            for i, fid in enumerate(fids):
                b = QToolButton()
                b.setText(FILTERS[fid].label.replace("&", "&&"))
                b.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
                b.setIconSize(QPixmap(88, 60).size())
                b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
                b.setAutoRaise(True)
                b.clicked.connect(lambda _=False, f=fid: self._effect(f))
                g.addWidget(b, i // 2, i % 2)
                self.fx_buttons[fid] = b
            sa = self._scroll(w)
            sa.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            pages.addTab(sa, title)
        pages.currentChanged.connect(lambda _: self._refresh_effect_icons())
        self.fx_pages = pages
        return pages

    def _tab_smart(self):
        w = QWidget()
        v = QVBoxLayout(w)
        info = QLabel("Modern tools. These are saved as Fotohaus-only edits; Picasa ignores them.")
        info.setWordWrap(True)
        info.setObjectName("Muted")
        v.addWidget(info)
        for fid in SMART:
            v.addWidget(self._btn(FILTERS[fid].label + "…", "sparkle" if fid == "fh_smart" else None,
                                  lambda _=False, f=fid: self.start_adjust(f)))
        v.addStretch()
        return self._scroll(w)

    # ------------------------------------------------------------------ open/close
    def open(self, pid: int, ids: list[int] | None = None):
        if ids is not None:
            self.ids = ids
        self.cancel_session()
        self.pid = pid
        r = self.lib.photo(pid)
        if r is None:
            return
        self.name_lbl.setText(f"  {r['filename']}")
        self.chain = self.lib.filters(pid)
        self.rotate = r["rotate"] or 0
        self.undo, self.redo = [], []
        self.base = None
        self.committed = None
        self.canvas.set_image(None, keep_view=False)
        self.star_btn.blockSignals(True)
        self.star_btn.setChecked(bool(r["star"]))
        self.star_btn.blockSignals(False)
        self._star_icon(bool(r["star"]))
        self.caption.setText(r["caption"] or "")
        self._update_history()
        self.gen += 1
        gen = self.gen
        path = r["path"]
        if self.strip.model_.rowCount() != len(self.ids) or getattr(self, "_strip_ids", None) != self.ids:
            self.strip.model_.set_photos(self.lib.photos_by_ids(self.ids), None)
            self._strip_ids = list(self.ids)
        self.strip.select_ids([pid])
        if r["kind"] == "video":
            self.center_stack.setCurrentWidget(self.video)
            start = end = 0.0
            for f in self.chain:
                if f.id == "moviestart":
                    start = f.floats(1)[0]
                elif f.id == "movieend":
                    end = f.floats(1)[0]
            self.video.load(path, start, end, autoplay=False)
            self.canvas.busy = False
        else:
            self.video.stop()
            self.center_stack.setCurrentWidget(self.canvas)
            self.canvas.busy = True
            self.pool.start(_Task(self.sig, gen, "base", lambda: media.load_image(path, max_dim=PREVIEW_MAX)))
        i = self.ids.index(pid) if pid in self.ids else -1
        self.prev_btn.setEnabled(i > 0)
        self.next_btn.setEnabled(0 <= i < len(self.ids) - 1)
        is_video = r["kind"] == "video"
        self.tabs.setEnabled(not is_video)
        self.save_btn.setEnabled(not is_video and r["kind"] != "raw")

    def _step(self, d):
        if self.pid not in self.ids:
            return
        i = self.ids.index(self.pid) + d
        if 0 <= i < len(self.ids):
            self.open(self.ids[i])
            self.navigate.emit(self.ids[i])

    def _back(self):
        self.cancel_session()
        self.video.stop()
        self.back.emit()

    # -- video trim / snapshot
    def _trim_changed(self, start, end):
        chain = [f for f in self.chain if f.id not in ("moviestart", "movieend")]
        if start:
            chain.append(Filter.make("moviestart", float(start)))
        if end:
            chain.append(Filter.make("movieend", float(end)))
        self.chain = chain
        self._persist()

    def _snapshot(self, path):
        import os
        self.lib.ensure_in_library(path)
        QMessageBox.information(self, "Snapshot saved", f"Saved {os.path.basename(path)} next to the video.")

    def _escape(self):
        if self.session:
            self.cancel_session()
        else:
            self._back()

    # ------------------------------------------------------------------ rendering
    def _task_done(self, gen, res, kind):
        if gen != self.gen and kind != "fx":
            return
        if isinstance(res, Exception):
            self.canvas.busy = False
            self.canvas.update()
            if kind == "base":
                QMessageBox.warning(self, "Can't open photo", str(res))
            return
        if kind == "base":
            self.base = res
            self._recompute_committed()
        elif kind == "committed":
            self.committed = res
            self.canvas.busy = False
            self.canvas.set_image(self._display(res))
            self._refresh_faces()
            self._refresh_effect_icons()
            if self.pending is not None:
                self._render_pending()
        elif kind == "pending":
            self.canvas.busy = False
            self._pending_img = res
            self.canvas.set_image(self._display(res))
        elif kind == "fx":
            fid, qimg, token = res
            if token == self.gen and fid in self.fx_buttons:
                self.fx_buttons[fid].setIcon(QIcon(QPixmap.fromImage(qimg)))

    def _display(self, im) -> QImage:
        if self.overflow_btn.isChecked():
            from ..edits import overflow_mask, to_f, to_im
            im = to_im(overflow_mask(to_f(im)))
        return pil_to_qimage(im)

    def _redisplay(self):
        if self.pending is not None and getattr(self, "_pending_img", None) is not None:
            self.canvas.set_image(self._display(self._pending_img))
        elif self.committed is not None:
            self.canvas.set_image(self._display(self.committed))

    def _strip_clicked(self, index):
        from .grid import ROLE_ENTRY
        kind, obj = index.data(ROLE_ENTRY)
        if kind == "photo" and obj["id"] != self.pid:
            self.open(obj["id"])
            self.navigate.emit(obj["id"])

    def _recompute_committed(self):
        if self.base is None:
            return
        self.gen += 1
        gen = self.gen
        base, chain, rot = self.base, list(self.chain), self.rotate
        self.canvas.busy = True
        self.canvas.update()
        self.pool.start(_Task(self.sig, gen, "committed", lambda: render(base, chain, rot)))

    def _render_pending(self):
        if self.committed is None:
            return
        if self.pending is None:
            self.canvas.set_image(pil_to_qimage(self.committed))
            return
        gen = self.gen
        img, f = self.committed, self.pending
        replace = self.session and self.session.get("replace_last")
        if replace:
            base, chain, rot = self.base, list(self.chain[:-1]) + [f], self.rotate
            fn = lambda: render(base, chain, rot)  # noqa: E731
        else:
            fn = lambda: apply_filter(img, f)  # noqa: E731
        self.canvas.busy = True
        self.canvas.update()
        self.pool.start(_Task(self.sig, gen, "pending", fn))

    def _refresh_effect_icons(self):
        if self.committed is None or self.tabs.currentIndex() != 2:
            return
        small = self.committed.copy()
        small.thumbnail((112, 76))
        token = self.gen
        page = list(EFFECT_PAGES.values())[self.fx_pages.currentIndex()] if hasattr(self, "fx_pages") else EFFECTS
        for fid in page:
            def job(fid=fid):
                out = apply_filter(small, Filter.make(fid, *self._default_args(fid)))
                return fid, pil_to_qimage(out), token
            self.fx_pool.start(_Task(self.sig, token, "fx", job))

    def _tab_changed(self, i):
        if i == 2:
            self._refresh_effect_icons()
        if i == 1:
            self._tune_load()
        elif self.session and self.session.get("fid") == "_tune":
            self.cancel_session()

    # ------------------------------------------------------------------ chain ops
    def _persist(self):
        self.lib.set_filters(self.pid, self.chain)
        r = self.lib.photo(self.pid)
        if r and (r["rotate"] or 0) != self.rotate:
            self.lib.rotate([self.pid], self.rotate - (r["rotate"] or 0))
        self.loader.invalidate(self.pid)
        self._update_history()
        self.changed.emit(self.pid)

    def commit(self, f: Filter, replace_last: bool = False):
        if self.pid is None:
            return
        self.undo.append((list(self.chain), self.rotate))
        self.redo.clear()
        if replace_last and self.chain:
            self.chain[-1] = f
        else:
            self.chain.append(f)
        self.pending = None
        self._persist()
        self._recompute_committed()

    def do_undo(self):
        if self.session:
            self.cancel_session()
            return
        if not self.undo:
            return
        self.redo.append((list(self.chain), self.rotate))
        self.chain, self.rotate = self.undo.pop()
        self._persist()
        self._recompute_committed()

    def do_redo(self):
        if not self.redo:
            return
        self.undo.append((list(self.chain), self.rotate))
        self.chain, self.rotate = self.redo.pop()
        self._persist()
        self._recompute_committed()

    def _update_history(self):
        self.history.clear()
        if self.rotate:
            self.history.addItem(f"Rotate {self.rotate * 90}°")
        for f in self.chain:
            self.history.addItem(label_for(f))
        has_backup = self.pid is not None and self.lib.has_backup(self.pid)
        self.undo_btn.setEnabled(bool(self.undo))
        self.redo_btn.setEnabled(bool(self.redo))
        self.revert_btn.setEnabled(bool(self.chain or self.rotate or has_backup))
        self.revert_btn.setText("Undo Saved Changes" if not (self.chain or self.rotate) and has_backup else "Revert")

    def _rotate(self, d):
        self.undo.append((list(self.chain), self.rotate))
        self.redo.clear()
        self.rotate = (self.rotate + d) % 4
        self._persist()
        self._recompute_committed()

    def _revert(self):
        if self.chain or self.rotate:
            self.undo.append((list(self.chain), self.rotate))
            self.chain, self.rotate = [], 0
            self._persist()
            self._recompute_committed()
            return
        if self.lib.has_backup(self.pid):
            if QMessageBox.question(self, "Undo saved changes",
                                    "Restore the original file from the .picasaoriginals backup?") == QMessageBox.Yes:
                self.lib.revert_saved(self.pid)
                self.loader.invalidate(self.pid)
                self.changed.emit(self.pid)
                self.open(self.pid)

    def _save(self):
        if not self.chain and not self.rotate:
            QMessageBox.information(self, "Nothing to save", "This photo has no edits to write to the file.")
            return
        pid = self.pid

        def work(job):
            job.report("Saving edits to file…")
            self.lib.save_edits(pid)
            return pid

        def done(_):
            self.loader.invalidate(pid)
            self.changed.emit(pid)
            if self.pid == pid:
                self.open(pid)

        self.jobs.submit(f"save-{pid}", work, on_done=done,
                         on_fail=lambda m: QMessageBox.warning(self, "Couldn't save", m))

    def _star(self, on):
        if self.pid:
            self.lib.set_star([self.pid], on)
            self._star_icon(on)
            self.changed.emit(self.pid)

    def _star_icon(self, on):
        self.star_btn.setIcon(theme.icon("star" if on else "star-outline", theme.STAR))

    def _caption(self):
        if self.pid:
            r = self.lib.photo(self.pid)
            if r and (r["caption"] or "") != self.caption.text().strip():
                self.lib.set_caption(self.pid, self.caption.text())
                self.changed.emit(self.pid)

    # ------------------------------------------------------------------ adjustment sessions
    def _default_args(self, fid, values=None, point=None, color=None):
        info = FILTERS[fid]
        vals = values if values is not None else [p[3] for p in info.params]
        x, y = point or (0.5, 0.4 if fid == "dir_tint" else 0.5)
        if fid in ("radblur", "radsat", "FocalZoom", "PicnikFocalPixelate"):
            return [x, y] + vals
        if fid == "radtint":
            return [x, y] + vals + [color_hex(color or COLOR_EFFECTS["radtint"])]
        if fid in ("PicnikTint",):
            return vals + [color_hex(color or COLOR_EFFECTS[fid])]
        if fid == "Border":
            return vals + [color_hex(color or COLOR_EFFECTS["Border"])]
        if fid == "TwoTone":
            return [color_hex(color or COLOR_EFFECTS["TwoTone"]), "ffffd98c"]
        if fid == "dir_tint":
            return [x, y] + vals + [color_hex(color or COLOR_EFFECTS["dir_tint"])]
        if fid == "tint":
            return vals + [color_hex(color or COLOR_EFFECTS["tint"])]
        if fid == "ansel":
            return [color_hex(color or COLOR_EFFECTS["ansel"])]
        if fid == "tilt":
            return vals + [0.0]
        return vals

    def _effect(self, fid):
        info = FILTERS[fid]
        if info.params or fid in COLOR_EFFECTS or fid in POINT_EFFECTS:
            self.start_adjust(fid)
        else:
            self.commit(Filter.make(fid))

    def start_adjust(self, fid, replace_last=False, initial=None):
        if self.committed is None:
            return
        self.cancel_session(rerender=False)
        info = FILTERS[fid]
        self.session = {"fid": fid, "sliders": [], "point": None, "color": COLOR_EFFECTS.get(fid),
                        "replace_last": replace_last}
        self._clear_panel()
        t = QLabel(info.label)
        t.setObjectName("Title")
        self.panel_v.addWidget(t)
        for i, (lab, lo, hi, dv) in enumerate(info.params):
            v = initial[i] if initial and i < len(initial) else dv
            s = ParamSlider(lab, lo, hi, v)
            s.changed.connect(self._session_changed)
            self.panel_v.addWidget(s)
            self.session["sliders"].append(s)
        if fid in COLOR_EFFECTS:
            cb = QPushButton("Pick Color…")
            cb.clicked.connect(self._session_color)
            self.panel_v.addWidget(cb)
        if fid in POINT_EFFECTS:
            hint = QLabel("Click the photo to set the focal point.")
            hint.setWordWrap(True)
            hint.setObjectName("Muted")
            self.panel_v.addWidget(hint)
            self.canvas.set_mode("point")
        self._panel_buttons()
        self.left_stack.setCurrentIndex(1)
        self._session_changed()

    def _panel_buttons(self, apply_cb=None):
        row = QHBoxLayout()
        ap = QPushButton("Apply")
        ap.setDefault(True)
        ap.clicked.connect(apply_cb or self.apply_session)
        cc = QPushButton("Cancel")
        cc.clicked.connect(self.cancel_session)
        row.addWidget(ap)
        row.addWidget(cc)
        self.panel_v.addLayout(row)
        self.panel_v.addStretch()

    def _clear_panel(self):
        def clear(layout):
            while layout.count():
                item = layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
                elif item.layout():
                    clear(item.layout())
        clear(self.panel_v)

    def _session_values(self):
        return [s.value() for s in self.session["sliders"]]

    def _session_changed(self):
        if not self.session:
            return
        fid = self.session["fid"]
        args = self._default_args(fid, self._session_values(), self.session["point"], self.session["color"])
        self.pending = Filter.make(fid, *args)
        self._live.start()

    def _session_color(self):
        c = QColorDialog.getColor(self.session.get("color") or QColor(200, 150, 100), self, "Choose color")
        if c.isValid():
            self.session["color"] = c
            self._session_changed()

    def apply_session(self):
        if not self.session or self.pending is None:
            self.cancel_session()
            return
        f, replace = self.pending, self.session.get("replace_last")
        self._end_session()
        self.commit(f, replace_last=bool(replace))

    def cancel_session(self, rerender=True):
        had = self.session is not None or self.pending is not None
        self._end_session()
        if had and rerender and self.committed is not None:
            self.canvas.set_image(pil_to_qimage(self.committed))

    def _end_session(self):
        if self.session and self.session.get("fid") == "_retouch":
            try:
                self.canvas.brush_done.disconnect(self._retouch_render)
            except (RuntimeError, TypeError):
                pass
        self.session = None
        self.pending = None
        self.canvas.set_mode("view")
        self.canvas.grid = False
        self.left_stack.setCurrentIndex(0)

    def _canvas_click(self, x, y):
        if self.session and self.session.get("fid") == "_text":
            self.session["point"] = (x, y)
            self._text_changed()
            return
        if self.session and self.session.get("fid") in POINT_EFFECTS:
            self.session["point"] = (x, y)
            self._session_changed()
        elif self.session and self.session.get("fid") == "_pick":
            if self.committed is not None:
                im = self.committed
                px = im.getpixel((min(im.width - 1, int(x * im.width)), min(im.height - 1, int(y * im.height))))
                self.neutral_color = QColor(*px[:3])
                self.neutral_lbl.setText(f"Neutral point: rgb{tuple(px[:3])}")
            self._end_session()
            self.tabs.setCurrentIndex(1)
            self._tune_changed()

    # -- crop
    def _start_crop(self):
        if self.committed is None:
            return
        self.cancel_session(rerender=False)
        self.session = {"fid": "_crop"}
        self._clear_panel()
        t = QLabel("Crop")
        t.setObjectName("Title")
        self.panel_v.addWidget(t)
        combo = QComboBox()
        combo.addItems(list(ASPECTS))
        combo.currentTextChanged.connect(self.canvas.set_aspect)
        self.panel_v.addWidget(QLabel("Aspect ratio"))
        self.panel_v.addWidget(combo)
        hint = QLabel("Drag on the photo to choose the area to keep.")
        hint.setWordWrap(True)
        hint.setObjectName("Muted")
        self.panel_v.addWidget(hint)
        self.canvas.set_image(pil_to_qimage(self.committed))
        self.canvas.set_mode("crop")
        self.canvas.set_aspect("Free")
        self._panel_buttons(self._apply_crop)
        self.left_stack.setCurrentIndex(1)

    def _apply_crop(self):
        cr = self.canvas.crop
        self._end_session()
        if cr is None or cr.width() < 0.01 or cr.height() < 0.01:
            return
        from ..picasa.ini import encode_rect64
        self.commit(Filter("crop64", [encode_rect64(cr.left(), cr.top(), cr.right(), cr.bottom())]))

    # -- straighten
    def _start_straighten(self):
        self.start_adjust("tilt")
        self.canvas.grid = True
        self.canvas.update()

    # -- red-eye
    def _start_redeye(self):
        if self.committed is None:
            return
        self.cancel_session(rerender=False)
        self.session = {"fid": "_redeye", "rects": []}
        self._clear_panel()
        t = QLabel("Red-Eye")
        t.setObjectName("Title")
        self.panel_v.addWidget(t)
        hint = QLabel("Drag a box around each red eye, or let Fotohaus find the eyes.")
        hint.setWordWrap(True)
        hint.setObjectName("Muted")
        self.panel_v.addWidget(hint)
        auto = QPushButton("Auto")
        auto.clicked.connect(self._auto_redeye)
        self.panel_v.addWidget(auto)
        self.canvas.set_mode("rect")
        self._panel_buttons()
        self.left_stack.setCurrentIndex(1)

    def _auto_redeye(self):
        if self.committed is None:
            return
        import numpy as np
        from ..ai.faces import eye_rects
        from ..picasa.ini import encode_rect64
        rects = eye_rects(np.ascontiguousarray(np.asarray(self.committed.convert("RGB"))))
        if not rects:
            QMessageBox.information(self, "Red-Eye", "No eyes were found. Drag boxes around the eyes instead.")
            return
        self.session["rects"] += [encode_rect64(*map(float, r)) for r in rects]
        self.pending = Filter("redeye", list(self.session["rects"]))
        self._live.start()

    # -- retouch
    def _start_retouch(self):
        if self.committed is None:
            return
        self.cancel_session(rerender=False)
        self.session = {"fid": "_retouch", "strokes": []}
        self._clear_panel()
        t = QLabel("Retouch")
        t.setObjectName("Title")
        self.panel_v.addWidget(t)
        hint = QLabel("Paint over a blemish, dust spot or small object. Fotohaus fills it in from its surroundings.")
        hint.setWordWrap(True)
        hint.setObjectName("Muted")
        self.panel_v.addWidget(hint)
        size = ParamSlider("Brush size", 4, 80, 24)
        size.changed.connect(lambda: setattr(self.canvas, "brush_px", int(size.value())))
        self.panel_v.addWidget(size)
        undo = QPushButton("Undo Last Stroke")
        undo.clicked.connect(self._retouch_undo)
        self.panel_v.addWidget(undo)
        self.canvas.brush_px = 24
        self.canvas.set_mode("brush")
        self.canvas.brush_done.connect(self._retouch_render)
        self._panel_buttons()
        self.left_stack.setCurrentIndex(1)

    def _brushed(self, x, y, r):
        if self.session and self.session.get("fid") == "_retouch":
            self.session["strokes"].append((x, y, r))

    def _retouch_render(self):
        if self.session and self.session.get("fid") == "_retouch":
            from ..edits import encode_strokes
            self.pending = Filter("fh_retouch", [encode_strokes(self.session["strokes"])])
            self._live.start()

    def _retouch_undo(self):
        if self.session and self.session.get("strokes"):
            # remove the last stroke (consecutive points)
            st = self.session["strokes"]
            st[-12:] = []
            self._retouch_render()

    # -- text
    def _start_text(self):
        if self.committed is None:
            return
        self.cancel_session(rerender=False)
        self.session = {"fid": "_text", "point": (0.5, 0.85), "color": QColor(255, 255, 255)}
        self._clear_panel()
        t = QLabel("Text")
        t.setObjectName("Title")
        self.panel_v.addWidget(t)
        from PySide6.QtWidgets import QPlainTextEdit
        self.text_edit = QPlainTextEdit()
        self.text_edit.setPlaceholderText("Type your text")
        self.text_edit.setMaximumHeight(70)
        self.text_edit.textChanged.connect(self._text_changed)
        self.panel_v.addWidget(self.text_edit)
        self.font_combo = QComboBox()
        self._fonts = font_files()
        self.font_combo.addItem("Default")
        self.font_combo.addItems(list(self._fonts))
        self.font_combo.currentIndexChanged.connect(self._text_changed)
        self.panel_v.addWidget(self.font_combo)
        self.text_sliders = {
            "size": ParamSlider("Size", 0.02, 0.3, 0.08), "outline": ParamSlider("Outline", 0.0, 1.0, 0.3),
            "opacity": ParamSlider("Opacity", 0.1, 1.0, 1.0), "angle": ParamSlider("Rotation", -90.0, 90.0, 0.0)}
        for sl in self.text_sliders.values():
            sl.changed.connect(self._text_changed)
            self.panel_v.addWidget(sl)
        cb = QPushButton("Text Color…")
        cb.clicked.connect(self._text_color)
        self.panel_v.addWidget(cb)
        hint = QLabel("Click the photo to place the text.")
        hint.setObjectName("Muted")
        self.panel_v.addWidget(hint)
        self.canvas.set_mode("point")
        self._panel_buttons()
        self.left_stack.setCurrentIndex(1)
        self.text_edit.setFocus()

    def _text_color(self):
        c = QColorDialog.getColor(self.session.get("color") or QColor(255, 255, 255), self, "Text color")
        if c.isValid():
            self.session["color"] = c
            self._text_changed()

    def _text_changed(self, *_):
        if not self.session or self.session.get("fid") != "_text":
            return
        from urllib.parse import quote
        txt = self.text_edit.toPlainText()
        if not txt.strip():
            self.pending = None
            self._redisplay()
            return
        x, y = self.session["point"]
        sl = self.text_sliders
        font = self._fonts.get(self.font_combo.currentText(), "")
        self.pending = Filter.make("fh_text", quote(txt), float(x), float(y), sl["size"].value(),
                                   color_hex(self.session["color"]), sl["outline"].value(), sl["opacity"].value(),
                                   sl["angle"].value(), quote(font))
        self._live.start()

    def _canvas_rect(self, l, t, r, b):
        if self.session and self.session.get("fid") == "_redeye":
            from ..picasa.ini import encode_rect64
            self.session["rects"].append(encode_rect64(l, t, r, b))
            self.pending = Filter("redeye", list(self.session["rects"]))
            self._live.start()
        elif self.session and self.session.get("fid") == "_addface":
            self._end_session()
            name, ok = QInputDialog.getText(self, "Add person", "Who is this?")
            if ok:
                self.lib.add_manual_face(self.pid, (l, t, r, b), name.strip() or None)
                self._refresh_faces()
                self.changed.emit(self.pid)

    # -- tuning
    def _tune_load(self):
        last = self.chain[-1] if self.chain else None
        if last is not None and last.id == "finetune2":
            vals = last.args + [""] * 5
            for k, v in zip(("fill", "hi", "sh"), vals[:3]):
                try:
                    self.tune[k].set_value(float(v))
                except ValueError:
                    self.tune[k].set_value(0.0)
            try:
                self.tune["temp"].set_value(float(vals[4]))
            except ValueError:
                self.tune["temp"].set_value(0.0)
        else:
            for s in self.tune.values():
                s.set_value(0.0)
            self.neutral_color = None
            self.neutral_lbl.setText("")

    def _tune_filter(self) -> Filter:
        col = color_hex(self.neutral_color) if self.neutral_color else "00000000"
        return Filter.make("finetune2", self.tune["fill"].value(), self.tune["hi"].value(),
                           self.tune["sh"].value(), col, self.tune["temp"].value())

    def _tune_changed(self):
        if self.committed is None:
            return
        last = self.chain[-1] if self.chain else None
        self.session = {"fid": "_tune", "replace_last": bool(last is not None and last.id == "finetune2")}
        self.pending = self._tune_filter()
        self._live.start()

    def _tune_apply(self):
        if self.session and self.session.get("fid") == "_tune" and self.pending is not None:
            f, replace = self.pending, self.session.get("replace_last")
            self.session = None
            self.pending = None
            self.commit(f, replace_last=bool(replace))

    def _tune_reset(self):
        for s in self.tune.values():
            s.set_value(0.0)
        self.neutral_color = None
        self.neutral_lbl.setText("")
        self.session = None
        self.pending = None
        if self.committed is not None:
            self.canvas.set_image(pil_to_qimage(self.committed))

    def _pick_neutral(self):
        self.session = {"fid": "_pick"}
        self.canvas.set_mode("pick")

    # ------------------------------------------------------------------ faces overlay
    def _toggle_faces(self, on):
        self.canvas.show_faces = on
        self._refresh_faces()

    def _refresh_faces(self):
        from PySide6.QtCore import QRectF
        self.canvas.faces = []
        if not self.canvas.show_faces or self.pid is None:
            self.canvas.update()
            return
        geometric = any(f.id in ("crop64", "crop", "tilt") for f in self.chain) or self.rotate
        if geometric:
            self.canvas.faces = []
            self.name_lbl.setText(self.name_lbl.text().split("  —")[0] + "  — face boxes hidden on cropped/rotated view")
        else:
            for f in self.lib.faces_for_photo(self.pid):
                self.canvas.faces.append((f["id"], QRectF(f["x"], f["y"], f["w"], f["h"]),
                                          f["person_name"] if f["person_id"] else ""))
        self.canvas.update()

    def _face_clicked(self, face_id):
        name, ok = QInputDialog.getText(self, "Name this person", "Name (leave empty to remove the tag):")
        if not ok:
            return
        if name.strip():
            self.lib.name_faces([face_id], name.strip())
        else:
            self.lib.ignore_faces([face_id])
        self._refresh_faces()
        self.changed.emit(self.pid)

    def add_face_mode(self):
        self.session = {"fid": "_addface"}
        self.canvas.set_mode("rect")
