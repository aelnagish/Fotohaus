"""Main window — Picasa's Library: folder list, lightbox, panels, Photo Tray and full menu set."""
from __future__ import annotations

import datetime as _dt
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QByteArray, QMimeData, QSettings, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QActionGroup, QDesktopServices, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (QApplication, QFileDialog, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMainWindow,
                               QMenu, QMessageBox, QSizePolicy, QSlider, QSplitter, QStackedWidget, QToolBar,
                               QToolButton, QVBoxLayout, QWidget)

from .. import APP_NAME, SUPPORT_URL, __version__, fileops, macos
from ..ai import clip
from ..ai.indexer import Indexer
from ..picasa.ini import Filter
from ..search import search
from . import theme
from .dialogs import WelcomeDialog, default_pictures, ExportDialog
from .dupesview import DupesView
from .editor import EditorView
from .grid import PhotoGrid
from .jobs import JobManager, ThumbLoader
from .mapview import PlacesView
from .panels import RightPanel
from .people import PeopleView
from .sidebar import Sidebar
from .slideshow import Slideshow
from .timeline import TimelineView
from .tray import PhotoTray

PAGE_LIBRARY, PAGE_EDITOR, PAGE_PEOPLE, PAGE_PLACES, PAGE_DUPES, PAGE_TIMELINE = range(6)
BATCH = [("I'm Feeling Lucky", "enhance"), ("Auto Contrast", "autolight"), ("Auto Color", "autocolor"),
         ("Smart Enhance", "fh_smart"), ("Sharpen", "unsharp2"), ("Sepia", "sepia"), ("Black && White", "bw"),
         ("Warmify", "warm"), ("Film Grain", "grain2")]


def reveal_in_finder(path: str):
    macos.reveal(path)


class MainWindow(QMainWindow):
    lib_event = Signal(str, object)
    open_in_viewer = Signal(str)

    def __init__(self, lib):
        super().__init__()
        self.lib = lib
        self.setWindowTitle(APP_NAME)
        self.resize(1400, 880)
        self.setUnifiedTitleAndToolBarOnMac(True)
        self.loader = ThumbLoader(self)
        self.jobs = JobManager(self)
        self.jobs.status.connect(self._status)
        self.collection = ("all", None)
        self.prev_collection = ("all", None)
        self.held: list[int] = []
        self.clip_ids: list[int] = []
        self.clip_cut = False
        self.copied_effects = None
        self.slideshow = None
        self._build()
        self._menus()
        lib.listeners.append(lambda what, payload: self.lib_event.emit(what, payload))
        self.lib_event.connect(self._on_lib_event)
        self._refresh_timer = QTimer(self, singleShot=True, interval=500, timeout=self._refresh_all)
        self._people_timer = QTimer(self, singleShot=True, interval=1500, timeout=self._refresh_people)
        self._search_timer = QTimer(self, singleShot=True, interval=280, timeout=self._do_search)
        self._rescan_timer = QTimer(self, interval=10 * 60 * 1000, timeout=self.start_scan)
        self._rescan_timer.start()
        self._restore()
        self.sidebar.rebuild()
        self.sidebar.select("all", None)
        self.show_collection("all", None)
        QTimer.singleShot(400, self._startup)

    # ================================================================== build
    def _build(self):
        tb = QToolBar("Main")
        tb.setMovable(False)
        tb.setIconSize(QSize(20, 20))
        tb.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        self.addToolBar(tb)
        imp = QToolButton()
        imp.setText("Import")
        imp.setIcon(theme.icon("plus"))
        imp.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        imp.setPopupMode(QToolButton.InstantPopup)
        m = QMenu(imp)
        m.addAction("From Camera or Card…", self.import_from)
        m.addAction("Add Folder…", self.add_folder)
        m.addAction("Add File…", self.add_file)
        m.addAction("Google Takeout…", self.import_takeout)
        m.addAction("Apple Photos Library (read-only)…", self.add_apple_photos)
        m.addAction("Picasa Contacts…", self.import_contacts)
        imp.setMenu(m)
        tb.addWidget(imp)
        tb.addSeparator()
        self.act_slideshow = tb.addAction(theme.icon("play"), "Slideshow", self.start_slideshow)
        tb.addAction(theme.icon("edit"), "Edit", lambda: self._sel() and self.open_editor(self._sel()[0]))
        tb.addAction(theme.icon("collage"), "Collage", self.make_collage)
        tb.addAction(theme.icon("movie"), "Movie", self.make_movie)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        tb.addWidget(spacer)
        # filter buttons (Picasa: show only starred / video / faces / geotagged) + date slider
        self.filters = {}
        for key, icon, tip in (("star", "star", "Show only starred"), ("video", "video", "Show only videos"),
                               ("faces", "people", "Show only pictures with faces"),
                               ("geo", "pin", "Show only geotagged pictures")):
            b = QToolButton()
            b.setCheckable(True)
            b.setIcon(theme.icon(icon, theme.STAR if key == "star" else None))
            b.setToolTip(tip)
            b.toggled.connect(lambda _: self.reload_collection())
            tb.addWidget(b)
            self.filters[key] = b
        self.age = QSlider(Qt.Horizontal)
        self.age.setRange(0, 100)
        self.age.setValue(0)
        self.age.setFixedWidth(90)
        self.age.setToolTip("Slide right to show only newer pictures")
        self.age.valueChanged.connect(self._age_changed)
        tb.addWidget(self.age)
        self.search_box = QLineEdit()
        self.search_box.setObjectName("Search")
        self.search_box.setClearButtonEnabled(True)
        self.search_box.setPlaceholderText("Search people, places, things, text…")
        self.search_box.addAction(theme.icon("search"), QLineEdit.LeadingPosition)
        self.search_box.textChanged.connect(lambda _: self._search_timer.start())
        tb.addWidget(self.search_box)
        tb.addSeparator()
        self.act_info = tb.addAction(theme.icon("info"), "Panels")
        self.act_info.setCheckable(True)
        self.act_info.setChecked(True)
        self.act_info.toggled.connect(lambda on: self.panel.setVisible(on))

        self.sidebar = Sidebar(self.lib)
        self.sidebar.selected.connect(self._sidebar_selected)
        self.sidebar.dropped_on_album.connect(lambda aid, ids: self.lib.add_to_album(aid, ids))
        self.sidebar.dropped_on_new_album.connect(self.new_album)
        self.sidebar.dropped_on_folder.connect(self._drop_on_folder)
        self.sidebar.context.connect(self._sidebar_ctx)

        self.stack = QStackedWidget()
        lib_page = QWidget()
        lv = QVBoxLayout(lib_page)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(0)
        head = QHBoxLayout()
        head.setContentsMargins(14, 8, 14, 2)
        self.header = QLabel()
        self.header.setObjectName("Title")
        head.addWidget(self.header, 1)
        self.header_actions = QHBoxLayout()
        head.addLayout(self.header_actions)
        lv.addLayout(head)
        self.subheader = QLabel()
        self.subheader.setObjectName("Muted")
        self.subheader.setContentsMargins(14, 0, 14, 4)
        self.subheader.setWordWrap(True)
        lv.addWidget(self.subheader)
        self.empty = QLabel()
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setObjectName("Muted")
        self.empty.setWordWrap(True)
        self.grid = PhotoGrid(self.loader)
        self.grid.delegate.caption_mode = self.lib.settings.get("thumb_caption") or "none"
        self.grid.activated_photo.connect(self.open_editor)
        self.grid.context_requested.connect(self._grid_ctx)
        self.grid.selection_changed_ids.connect(self._selection_changed)
        self.grid.reordered.connect(self._album_reordered)
        lv.addWidget(self.empty)
        lv.addWidget(self.grid, 1)
        self.stack.addWidget(lib_page)
        self.editor = EditorView(self.lib, self.loader, self.jobs)
        self.editor.back.connect(self.close_editor)
        self.editor.changed.connect(self._photo_changed)
        self.editor.export_requested.connect(self.export_ids)
        self.editor.navigate.connect(lambda pid: self.panel.set_ids([pid]))
        self.stack.addWidget(self.editor)
        self.people = PeopleView(self.lib)
        self.people.person_selected.connect(lambda p: self._go("person", p))
        self.people.request_index.connect(self.start_index)
        self.stack.addWidget(self.people)
        self.places = PlacesView(self.lib, self.loader)
        self.places.show_photos.connect(lambda ids: self._go("ids", ids))
        self.stack.addWidget(self.places)
        self.dupes = DupesView(self.lib, self.loader)
        self.stack.addWidget(self.dupes)
        self.timeline = TimelineView(self.lib, self.loader)
        self.timeline.folder_clicked.connect(lambda fid: self._go("folder", fid))
        self.stack.addWidget(self.timeline)

        self.panel = RightPanel(self.lib, self.loader)
        self.panel.info.person_clicked.connect(lambda p: self._go("person", p))
        self.panel.info.show_on_map.connect(self._show_on_map)
        self.panel.people.add_person.connect(self._add_person_manually)

        split = QSplitter()
        split.addWidget(self.sidebar)
        split.addWidget(self.stack)
        split.addWidget(self.panel)
        split.setStretchFactor(1, 1)
        split.setSizes([230, 900, 290])
        split.setCollapsible(1, False)
        self.splitter = split
        central = QWidget()
        cv = QVBoxLayout(central)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(0)
        cv.addWidget(split, 1)
        self.tray = PhotoTray(self.loader, int(self.lib.settings.get("thumb_size") or 160),
                              list(self.lib.settings.get("button_bar") or []))
        self.tray.action.connect(self._tray_action)
        self.tray.hold.connect(self.hold_selection)
        self.tray.clear.connect(self.clear_tray)
        self.tray.add_to_album.connect(self._album_menu)
        self.tray.star.connect(self.toggle_star)
        self.tray.rotate.connect(self.rotate_selected)
        self.tray.zoom_changed.connect(self._zoom)
        self.tray.stop_job.connect(lambda: self.jobs.current and self.jobs.current.stop())
        self.grid.set_thumb_size(self.tray.zoom.value())
        self.loader.loaded.connect(lambda _: self._tray_refresh_soon())
        self._tray_timer = QTimer(self, singleShot=True, interval=200, timeout=self._tray_refresh)
        cv.addWidget(self.tray)
        self.setCentralWidget(central)

    # ================================================================== menus
    def _menus(self):
        mb = self.menuBar()
        S = QKeySequence
        # ---- File
        f = mb.addMenu("&File")
        f.addAction("Add Folder to Fotohaus…", self.add_folder, S.Open)
        f.addAction("Add File to Fotohaus…", self.add_file)
        f.addAction("Import From Camera or Card…", self.import_from, S("Ctrl+Shift+I"))
        f.addAction("Import from Google Takeout…", self.import_takeout)
        f.addAction("Add Apple Photos Library (read-only)…", self.add_apple_photos)
        f.addSeparator()
        f.addAction("New Album…", lambda: self.new_album(self._sel()), S("Ctrl+N"))
        f.addAction("New Folder…", self.new_folder, S("Ctrl+Shift+N"))
        f.addSeparator()
        f.addAction("Open in Edit View", lambda: self._sel() and self.open_editor(self._sel()[0]))
        f.addAction("View in Photo Viewer", self.view_in_viewer, S("Ctrl+Y"))
        self.open_with_menu = f.addMenu("Open With")
        self.open_with_menu.aboutToShow.connect(self._fill_open_with)
        f.addAction("Show in Finder", self.reveal_selected, S("Ctrl+Shift+R"))
        f.addSeparator()
        f.addAction("Save (Write Edits to File)", self.save_selected, S.Save)
        f.addAction("Save As…", self.save_as)
        f.addAction("Save a Copy", self.save_copy)
        f.addAction("Revert to Original", self.revert_selected)
        f.addAction("Undo All Edits", self.undo_all_edits)
        f.addSeparator()
        f.addAction("Export Picture to Folder…", self.export_selected, S("Ctrl+Shift+E"))
        f.addAction("Export as HTML Page…", self.export_html)
        f.addAction("Export Places to Google Earth (KML)…", self.export_kml)
        f.addSeparator()
        f.addAction("Print…", self.print_selected, S.Print)
        f.addAction("Print Contact Sheet…", lambda: self.print_selected(contact=True))
        f.addAction("Email…", self.email_selected, S("Ctrl+E"))
        f.addAction("Share…", lambda: self._tray_action("share"))
        f.addSeparator()
        f.addAction("Rename…", self.rename_selected, S("Ctrl+Shift+M"))
        f.addAction("Move to Trash", self.trash_selected, S("Ctrl+Backspace"))
        f.addSeparator()
        a = f.addAction("Options…", self.open_settings, S.Preferences)
        a.setMenuRole(QAction.PreferencesRole)
        q = f.addAction("Quit", self.close, S.Quit)
        q.setMenuRole(QAction.QuitRole)
        # ---- Edit
        e = mb.addMenu("&Edit")
        e.addAction("Undo", self._undo, S.Undo)
        e.addAction("Redo", self._redo, S.Redo)
        e.addSeparator()
        e.addAction("Cut", lambda: self.copy_pictures(cut=True), S.Cut)
        e.addAction("Copy", lambda: self.copy_pictures(cut=False), S.Copy)
        e.addAction("Paste", self.paste_pictures, S.Paste)
        e.addAction("Copy Picture to Clipboard", self.copy_image)
        e.addAction("Copy Full Path", self.copy_path)
        e.addSeparator()
        e.addAction("Copy All Effects", self.copy_effects, S("Ctrl+Shift+C"))
        e.addAction("Paste All Effects", self.paste_effects, S("Ctrl+Shift+V"))
        e.addSeparator()
        e.addAction("Select All", self.grid.selectAll, S.SelectAll)
        e.addAction("Select Starred", self.select_starred)
        e.addAction("Invert Selection", self.invert_selection)
        e.addAction("Clear Selection", self.grid.clearSelection, S("Ctrl+Shift+A"))
        e.addSeparator()
        e.addAction("Find", lambda: (self.search_box.setFocus(), self.search_box.selectAll()), S.Find)
        e.addAction("Save Search Results as Album…", self.save_search)
        # ---- View
        v = mb.addMenu("&View")
        v.addAction("Library", lambda: self._go("all", None), S("Ctrl+1"))
        v.addAction("People", lambda: self._go("people", None), S("Ctrl+2"))
        v.addAction("Places", lambda: self._go("places", None), S("Ctrl+3"))
        v.addAction("Duplicates", lambda: self._go("dupes", None), S("Ctrl+4"))
        v.addAction("Timeline", lambda: self._go("timeline", None), S("Ctrl+5"))
        v.addAction("Edit View", lambda: self._sel() and self.open_editor(self._sel()[0]))
        v.addAction("Slideshow", self.start_slideshow, S("Ctrl+Return"))
        v.addSeparator()
        self._radio_menu(v, "Folder View", "folder_view", [("Flat (by year)", "flat"), ("Tree", "tree")],
                         self._refresh_all)
        self._radio_menu(v, "Sort Folders By", "folder_sort", [("Creation Date", "date"), ("Recent Changes", "recent"),
                                                               ("Size", "size"), ("Name", "name")], self._refresh_all,
                         reverse_key="folder_sort_reverse")
        self._radio_menu(v, "Sort Pictures By", "sort", [("Date Taken", "date"), ("Name", "name"), ("Size", "size")],
                         self.reload_collection, reverse_key="sort_reverse")
        self._radio_menu(v, "Thumbnail Caption", "thumb_caption",
                         [("None", "none"), ("Filename", "filename"), ("Caption", "caption"),
                          ("Resolution", "resolution"), ("Date", "date"), ("Tags", "tags")], self._caption_mode)
        self._radio_menu(v, "Sort People By", "people_sort", [("Name", "name"), ("Amount", "amount"),
                                                             ("Top 10", "top10")], self._refresh_all)
        v.addAction("Larger Thumbnails", lambda: self.tray.zoom.setValue(self.tray.zoom.value() + 30), S.ZoomIn)
        v.addAction("Smaller Thumbnails", lambda: self.tray.zoom.setValue(self.tray.zoom.value() - 30), S.ZoomOut)
        v.addSeparator()
        hid = v.addAction("Show Hidden Pictures and Folders")
        hid.setCheckable(True)
        hid.setChecked(bool(self.lib.settings.get("show_hidden")))
        hid.toggled.connect(lambda on: (self.lib.settings.set("show_hidden", on), self._refresh_all()))
        v.addAction(self.act_info)
        self.act_info.setShortcut(S("Ctrl+I"))
        pm = v.addMenu("Panels")
        for i, name in enumerate(("Info", "Tags", "People", "Places")):
            pm.addAction(name, lambda i=i: (self.act_info.setChecked(True), self.panel.setCurrentIndex(i)),
                         S(f"Ctrl+Alt+{i + 1}"))
        # ---- Folder
        fo = mb.addMenu("F&older")
        fo.addAction("Folder Properties…", lambda: self._cur_folder() and self._folder_props(self._cur_folder()))
        fo.addAction("Rename Folder…", lambda: self._cur_folder() and self._rename_folder(self._cur_folder()))
        fo.addAction("Move Folder…", lambda: self._cur_folder() and self._move_folder(self._cur_folder()))
        fo.addAction("New Subfolder…", lambda: self.new_folder(inside=True))
        fo.addAction("Delete Folder…", lambda: self._cur_folder() and self._delete_folder(self._cur_folder()))
        fo.addSeparator()
        fo.addAction("Hide / Unhide Folder", lambda: self._cur_folder() and self._toggle_folder_hidden(self._cur_folder()))
        fo.addAction("Remove from Fotohaus…", lambda: self._cur_folder() and self._remove_folder(self._cur_folder()))
        fo.addAction("Refresh Thumbnails", self.refresh_thumbnails)
        fo.addSeparator()
        fo.addAction("Show in Finder", lambda: self._cur_folder() and reveal_in_finder(self.lib.folder(self._cur_folder())["path"]))
        fo.addAction("View Slideshow", lambda: self.start_slideshow(from_start=True))
        fo.addAction("Select All Pictures", self.grid.selectAll)
        fo.addAction("Export Folder…", lambda: self.export_ids([r["id"] for r in self.grid.model_.photo_rows()]))
        # ---- Picture
        pi = mb.addMenu("&Picture")
        pi.addAction("View and Edit", lambda: self._sel() and self.open_editor(self._sel()[0]))
        pi.addAction("Hold in Photo Tray", self.hold_selection, S("Ctrl+K"))
        pi.addAction("Clear Photo Tray", self.clear_tray)
        pi.addSeparator()
        pi.addAction("Star / Unstar", self.toggle_star, S("Ctrl+8"))
        pi.addAction("Rotate Counterclockwise", lambda: self.rotate_selected(-1), S("Ctrl+["))
        pi.addAction("Rotate Clockwise", lambda: self.rotate_selected(1), S("Ctrl+]"))
        be = pi.addMenu("Batch Edit")
        for label, fid in BATCH:
            be.addAction(label, lambda fid=fid: self.batch_effect(fid))
        be.addSeparator()
        be.addAction("Rename…", self.rename_selected)
        be.addAction("Adjust Date and Time…", self.adjust_dates)
        be.addAction("Undo All Edits", self.undo_all_edits)
        pi.addSeparator()
        self.album_menu = pi.addMenu("Add to Album")
        self.album_menu.aboutToShow.connect(lambda: self._fill_album_menu(self.album_menu))
        pi.addAction("Remove from Album", self._remove_from_album)
        pi.addAction("Add Tag…", self.add_tag_selected, S("Ctrl+T"))
        pi.addAction("Caption…", self.caption_selected)
        pi.addAction("Properties…", self.show_properties, S("Ctrl+Alt+I"))
        pi.addSeparator()
        geo = pi.addMenu("Geo-Tag")
        geo.addAction("Place on Map…", self.place_selected)
        geo.addAction("Remove Location", lambda: self.lib.set_location(self._sel(), None, None, write_exif=False))
        nt = pi.addMenu("Name Tags")
        nt.addAction("Add a Person Manually…", lambda: self._sel() and self._add_person_manually(self._sel()[0]))
        nt.addAction("Reset Faces (find again)", self.reset_faces)
        pi.addAction("Find Similar Pictures", self.find_similar)
        pi.addSeparator()
        pi.addAction("Move to Folder…", self.move_to_folder)
        pi.addAction("Move to New Folder…", self.move_to_new_folder)
        pi.addAction("Split Folder Here…", self.split_folder)
        pi.addSeparator()
        pi.addAction("Hide / Unhide", self.hide_selected, S("Ctrl+Shift+H"))
        pi.addAction("Set as Desktop", self.set_desktop)
        pi.addAction("Move to Trash", self.trash_selected)
        # ---- Create
        cr = mb.addMenu("&Create")
        cr.addAction("Picture Collage…", self.make_collage)
        cr.addAction("Movie…", self.make_movie)
        cr.addAction("Make a Poster…", self.make_poster)
        cr.addAction("Passport Photo…", self.make_passport)
        cr.addAction("Export as HTML Page…", self.export_html)
        cr.addAction("Create a Gift Disk Image…", self.make_gift)
        cr.addSeparator()
        cr.addAction("Set as Desktop", self.set_desktop)
        cr.addAction("Add to Screensaver", self.add_to_screensaver)
        # ---- Tools
        t = mb.addMenu("&Tools")
        t.addAction("Folder Manager…", self.folder_manager)
        t.addAction("Import…", self.import_from)
        t.addAction("Back Up Pictures…", self.backup)
        t.addAction("Adjust Date and Time…", self.adjust_dates)
        t.addSeparator()
        t.addAction("Rescan Folders Now", self.start_scan, S("Ctrl+R"))
        t.addAction("Refresh Thumbnails", self.refresh_thumbnails)
        t.addAction("Analyze Photos Now (faces, objects, text)", self.start_index)
        t.addAction("Show Duplicate Files", lambda: self._go("dupes", None))
        t.addSeparator()
        t.addAction("Configure Photo Viewer…", lambda: self.open_settings(tab=3))
        t.addAction("Download Natural-Language Search Model…", self.download_clip)
        t.addAction("Command Line && Claude (MCP) Integration…", self.show_cli_help)
        t.addSeparator()
        t.addAction("Options…", self.open_settings)
        # ---- Help
        h = mb.addMenu("&Help")
        h.addAction("Keyboard Shortcuts", self.show_shortcuts, S("Ctrl+/"))
        h.addAction("Search Tips", self.search_help)
        h.addAction("Picasa Compatibility", self.compat_help)
        h.addSeparator()
        h.addAction("☕ Buy Me a Coffee…", lambda: QDesktopServices.openUrl(QUrl(SUPPORT_URL)))
        ab = h.addAction(f"About {APP_NAME}", self.about)
        ab.setMenuRole(QAction.AboutRole)

    def _radio_menu(self, parent, title, key, options, after, reverse_key=None):
        m = parent.addMenu(title)
        grp = QActionGroup(self)
        cur = self.lib.settings.get(key)
        for label, val in options:
            a = m.addAction(label)
            a.setCheckable(True)
            a.setChecked(cur == val or (cur is None and val == options[0][1]))
            grp.addAction(a)
            a.triggered.connect(lambda _=False, val=val: (self.lib.settings.set(key, val), after()))
        if reverse_key:
            m.addSeparator()
            r = m.addAction("Reverse Order")
            r.setCheckable(True)
            r.setChecked(bool(self.lib.settings.get(reverse_key)))
            r.toggled.connect(lambda on: (self.lib.settings.set(reverse_key, on), after()))

    def _caption_mode(self):
        self.grid.delegate.caption_mode = self.lib.settings.get("thumb_caption") or "none"
        self.grid.doItemsLayout()

    # ================================================================== startup / jobs
    def _startup(self):
        if not self.lib.settings.get("first_run_done"):
            self.lib.settings.set("first_run_done", True)
            if not list(self.lib.roots()):
                dlg = WelcomeDialog(self)
                if dlg.exec():
                    for folder in dlg.folders():
                        self.lib.add_root(folder)
        if list(self.lib.roots()):
            self.start_scan()
        self._update_empty()

    def start_scan(self):
        def work(job):
            def prog(path, n):
                job.report(f"Scanning {os.path.basename(path) or path}…  {n} new", 0, 0)
            return self.lib.scan(progress=prog, should_stop=job.should_stop)
        self.jobs.submit("scan", work, on_done=lambda stats: self.start_index())

    def start_index(self):
        def work(job):
            ix = Indexer(self.lib)
            return ix.run(progress=lambda d, t, m: job.report(f"{m}… {d}/{t}", d, t), should_stop=job.should_stop)
        self.jobs.submit("index", work, on_done=lambda s: self._people_timer.start())

    def _status(self, msg, done, total):
        if not msg:
            c = self.lib.counts()
            msg2 = f"{c['photos']} pictures · {c['videos']} videos · {c['folders']} folders"
            self.tray.set_status("", 0, 0)
            self.tray.status.setText(msg2)
        else:
            self.tray.set_status(msg, done, total)

    # ================================================================== library events
    def _on_lib_event(self, what, payload):
        if what in ("library", "folder", "roots", "albums"):
            self._refresh_timer.start()
        elif what == "photos":
            ids = payload or []
            for r in self.lib.photos_by_ids(list(ids)):
                self.loader.invalidate(r["id"])
                self.grid.model_.update_row(r)
            self.panel.refresh()
            if self.collection[0] == "starred" or any(b.isChecked() for b in self.filters.values()):
                self._refresh_timer.start()
        elif what in ("people", "index_done"):
            self._people_timer.start()

    def _refresh_all(self):
        self.sidebar.rebuild()
        page = self.stack.currentIndex()
        if page == PAGE_LIBRARY:
            self.reload_collection(keep_selection=True)
        elif page == PAGE_PLACES:
            self.places.refresh()
        elif page == PAGE_TIMELINE:
            self.timeline.refresh()
        self._update_empty()
        if not self.jobs.busy():
            self._status("", 0, 0)

    def _refresh_people(self):
        self.sidebar.rebuild()
        if self.stack.currentIndex() == PAGE_PEOPLE:
            self.people.refresh()

    def _photo_changed(self, pid):
        r = self.lib.photo(pid)
        if r:
            self.loader.invalidate(pid)
            self.grid.model_.update_row(r)
            self.panel.refresh()

    # ================================================================== navigation
    def _sidebar_selected(self, kind, ident):
        if kind == "new_album":
            self.new_album(self._sel())
            return
        self.search_box.blockSignals(True)
        self.search_box.clear()
        self.search_box.blockSignals(False)
        self._go(kind, ident, from_sidebar=True)

    def _go(self, kind, ident, from_sidebar=False):
        if not from_sidebar:
            self.sidebar.select(kind, ident)
        if kind == "people":
            self.stack.setCurrentIndex(PAGE_PEOPLE)
            self.people.refresh()
            return
        if kind == "places":
            self.stack.setCurrentIndex(PAGE_PLACES)
            self.places.set_selection(self._tray_ids())
            self.places.refresh()
            return
        if kind == "dupes":
            self.stack.setCurrentIndex(PAGE_DUPES)
            self.dupes.refresh()
            return
        if kind == "timeline":
            self.stack.setCurrentIndex(PAGE_TIMELINE)
            self.timeline.refresh()
            return
        self.show_collection(kind, ident)

    def show_collection(self, kind, ident):
        if kind != "search":
            self.prev_collection = (kind, ident)
        self.collection = (kind, ident)
        self.stack.setCurrentIndex(PAGE_LIBRARY)
        self.reload_collection()

    def locate(self, pid):
        """Picasa's 'Locate in Picasa': show the photo's folder and select it."""
        r = self.lib.photo(pid)
        if r is None:
            return
        self._go("folder", r["folder_id"])
        self.grid.select_ids([pid])

    def _clear_header_actions(self):
        while self.header_actions.count():
            w = self.header_actions.takeAt(0).widget()
            if w:
                w.deleteLater()

    def _header_btn(self, text, cb):
        b = QToolButton()
        b.setText(text)
        b.setAutoRaise(True)
        b.clicked.connect(cb)
        self.header_actions.addWidget(b)

    def reload_collection(self, keep_selection=False):
        kind, ident = self.collection
        sel = self.grid.selected_ids() if keep_selection else []
        scroll = self.grid.verticalScrollBar().value() if keep_selection else 0
        lib = self.lib
        sections = "month"
        title, sub = "", ""
        folder_titles = None
        self._clear_header_actions()
        self.grid.set_reorderable(False)
        if kind == "all":
            rows = lib.all_photos()
            sections = "folder"
            title = "All Photos"
            folder_titles = {f["id"]: {"title": f["name"], "date": f["date"],
                                       "subtitle": " · ".join(x for x in (f["place"], f["description"]) if x)}
                             for f in lib.folders(include_hidden=True)}
        elif kind == "folder":
            f = lib.folder(ident)
            rows = lib.photos_in_folder(ident, include_hidden=bool(lib.settings.get("show_hidden"))) if f else []
            sections = None
            title = f["name"] if f else ""
            if f:
                sub = " · ".join(x for x in (_dt.datetime.fromtimestamp(f["date"]).strftime("%B %d, %Y")
                                             if f["date"] else "", f["place"], f["description"]) if x)
                self._header_btn("Play Slideshow", lambda: self.start_slideshow(from_start=True))
                self._header_btn("Edit Description…", lambda fid=ident: self._folder_props(fid))
                self._header_btn("Show in Finder", lambda p=f["path"]: reveal_in_finder(p))
        elif kind == "album":
            a = lib.album(ident)
            rows = lib.photos_in_album(ident)
            sections = None
            title = a["name"] if a else ""
            if a:
                sub = " · ".join(x for x in (a["place"] if "place" in a.keys() else "", a["description"]) if x)
                self.grid.set_reorderable(True)
                self._header_btn("Play Slideshow", lambda: self.start_slideshow(from_start=True))
                self._header_btn("Album Properties…", lambda aid=ident: self._album_props(aid))
        elif kind == "starred":
            rows, title = lib.starred(), "Starred"
        elif kind == "videos":
            rows, title = lib.videos(), "Videos"
        elif kind == "recent":
            rows, title, sections = lib.recent(), "Recently Added", None
        elif kind == "hidden":
            rows, title = lib.hidden(), "Hidden"
        elif kind == "person":
            p = lib.person(ident)
            rows, title = lib.photos_for_person(ident), (p["name"] if p else "")
            self._header_btn("Play Slideshow", lambda: self.start_slideshow(from_start=True))
        elif kind == "search":
            rows = search(lib, ident)
            title = f"Search: “{ident}”"
            sections = None
            self._header_btn("Save as Album…", self.save_search)
        elif kind == "ids":
            rows = lib.photos_by_ids(list(ident))
            title = f"{len(rows)} pictures"
            sections = None
        else:
            rows = []
        rows = self._apply_filters(rows)
        self.header.setText(f"{title}   <span style='font-size:12px;font-weight:normal;color:gray'>"
                            f"{len(rows)} item{'s' if len(rows) != 1 else ''}</span>")
        self.subheader.setText(sub)
        self.subheader.setVisible(bool(sub))
        self.grid.model_.set_photos(rows, sections, folder_titles)
        self._update_empty(len(rows))
        if sel:
            self.grid.select_ids(sel)
            self.grid.verticalScrollBar().setValue(scroll)
        else:
            self._selection_changed([])

    def _apply_filters(self, rows):
        f = self.filters
        if f["star"].isChecked():
            rows = [r for r in rows if r["star"]]
        if f["video"].isChecked():
            rows = [r for r in rows if r["kind"] == "video"]
        if f["geo"].isChecked():
            rows = [r for r in rows if r["lat"] is not None]
        if f["faces"].isChecked():
            have = {r[0] for r in self.lib.db.q("SELECT DISTINCT photo_id FROM faces WHERE ignored=0")}
            rows = [r for r in rows if r["id"] in have]
        if self.age.value() > 0:
            lo, hi = self._date_range()
            cut = lo + (hi - lo) * self.age.value() / 100.0
            rows = [r for r in rows if (r["taken"] or 0) >= cut]
        return rows

    def _date_range(self):
        r = self.lib.db.one("SELECT MIN(taken), MAX(taken) FROM photos WHERE taken > 0")
        return (r[0] or 0, r[1] or 0) if r else (0, 0)

    def _age_changed(self, v):
        lo, hi = self._date_range()
        if v and hi:
            cut = lo + (hi - lo) * v / 100.0
            self.age.setToolTip(f"Showing pictures from {_dt.datetime.fromtimestamp(cut):%B %Y} onwards")
        else:
            self.age.setToolTip("Slide right to show only newer pictures")
        self.reload_collection()

    def _update_empty(self, n=None):
        if n is None:
            n = len(self.grid.model_.entries)
        has_roots = bool([r for r in self.lib.roots() if r["kind"] != "exclude"])
        self.empty.setVisible(n == 0)
        if n == 0:
            if not has_roots:
                self.empty.setText("Your library is empty.\n\nChoose Import ▸ Add Folder… or Tools ▸ Folder Manager… "
                                   "to start.")
            elif self.collection[0] == "search":
                self.empty.setText("No matches.\n\nTry fewer words, a person's name, a place, or see Help ▸ "
                                   "Search Tips.")
            elif self.jobs.busy():
                self.empty.setText("Looking for pictures…")
            else:
                self.empty.setText("Nothing here.")

    def _do_search(self):
        q = self.search_box.text().strip()
        if q:
            if self.stack.currentIndex() != PAGE_LIBRARY:
                self.stack.setCurrentIndex(PAGE_LIBRARY)
            self.collection = ("search", q)
            self.reload_collection()
        else:
            self.show_collection(*self.prev_collection)

    def _selection_changed(self, ids):
        self.panel.set_ids(ids if ids else self.held)
        self.places.set_selection(self._tray_ids())
        self._tray_refresh_soon()

    def _zoom(self, v):
        self.grid.set_thumb_size(v)
        self.lib.settings.set("thumb_size", v)

    # ================================================================== tray / selection helpers
    def _sel(self):
        if self.stack.currentIndex() == PAGE_EDITOR and self.editor.pid:
            return [self.editor.pid]
        ids = self.grid.selected_ids()
        return ids if ids else list(self.held)

    def _tray_ids(self):
        ids = list(self.held)
        for i in self._sel():
            if i not in ids:
                ids.append(i)
        return ids

    def _tray_refresh_soon(self):
        self._tray_timer.start()

    def _tray_refresh(self):
        rows = self.lib.photos_by_ids(self._tray_ids())
        self.tray.set_items(rows, len(self.held))
        self.grid.delegate.held = set(self.held)
        self.grid.viewport().update()

    def hold_selection(self):
        for i in self.grid.selected_ids():
            if i not in self.held:
                self.held.append(i)
        self._tray_refresh()

    def clear_tray(self):
        self.held = []
        self.grid.clearSelection()
        self._tray_refresh()

    def _tray_action(self, key):
        ids = self._tray_ids()
        if key == "share":
            rows = self.lib.photos_by_ids(ids)
            if rows:
                macos.share_files([r["path"] for r in rows], self)
        elif key == "email":
            self.email_selected()
        elif key == "print":
            self.print_selected()
        elif key == "export":
            self.export_selected()
        elif key == "collage":
            self.make_collage()
        elif key == "movie":
            self.make_movie()
        elif key == "geotag":
            self.place_selected()
        elif key == "html":
            self.export_html()
        elif key == "desktop":
            self.set_desktop()

    # ================================================================== editor
    def open_editor(self, pid):
        rows = self.grid.model_.photo_rows()
        ids = [r["id"] for r in rows]
        if pid not in ids:
            ids = [pid]
        self.editor.open(pid, ids)
        self.stack.setCurrentIndex(PAGE_EDITOR)
        self.panel.set_ids([pid])
        self.editor.canvas.setFocus()

    def close_editor(self):
        pid = self.editor.pid
        self.stack.setCurrentIndex(PAGE_LIBRARY)
        if pid:
            self.grid.select_ids([pid])

    def view_in_viewer(self):
        rows = self.lib.photos_by_ids(self._sel()[:1])
        if rows:
            self.open_in_viewer.emit(rows[0]["path"])

    def _undo(self):
        if self.stack.currentIndex() == PAGE_EDITOR:
            self.editor.do_undo()

    def _redo(self):
        if self.stack.currentIndex() == PAGE_EDITOR:
            self.editor.do_redo()

    # ================================================================== picture actions
    def toggle_star(self):
        ids = self._sel()
        if not ids:
            return
        rows = self.lib.photos_by_ids(ids)
        on = not all(r["star"] for r in rows)
        self.lib.set_star(ids, on)
        self.sidebar.rebuild()

    def rotate_selected(self, d):
        if self.stack.currentIndex() == PAGE_EDITOR:
            self.editor._rotate(d)
            return
        ids = self._sel()
        if ids:
            self.lib.rotate(ids, d)

    def add_tag_selected(self):
        ids = self._sel()
        if not ids:
            return
        tag, ok = QInputDialog.getText(self, "Add tag", f"Tag for {len(ids)} picture(s):")
        if ok and tag.strip():
            self.lib.add_keyword(ids, tag.strip())

    def caption_selected(self):
        ids = self._sel()
        if not ids:
            return
        r = self.lib.photo(ids[0])
        text, ok = QInputDialog.getText(self, "Caption", "Caption:", text=r["caption"] or "")
        if ok:
            for pid in ids:
                self.lib.set_caption(pid, text)

    def show_properties(self):
        rows = self.lib.photos_by_ids(self._sel()[:1])
        if rows:
            from .tools import ExifDialog
            ExifDialog(rows[0]["path"], self).exec()

    def hide_selected(self):
        ids = self._sel()
        if ids:
            rows = self.lib.photos_by_ids(ids)
            self.lib.set_hidden(ids, not all(r["hidden"] for r in rows))

    def reveal_selected(self):
        rows = self.lib.photos_by_ids(self._sel())
        if rows:
            reveal_in_finder(rows[0]["path"])

    def _fill_open_with(self):
        m = self.open_with_menu
        m.clear()
        rows = self.lib.photos_by_ids(self._sel()[:20])
        if not rows:
            m.addAction("(nothing selected)").setEnabled(False)
            return
        paths = [r["path"] for r in rows]
        m.addAction("Default Application", lambda: macos.open_with(paths, None))
        apps = macos.apps_for(paths[0])
        if apps:
            m.addSeparator()
        for name, app in apps:
            m.addAction(name, lambda app=app: macos.open_with(paths, app))
        m.addSeparator()

        def choose():
            start = "/Applications" if os.path.isdir("/Applications") else str(Path.home())
            app, _ = QFileDialog.getOpenFileName(self, "Choose an application", start, "Applications (*.app)")
            if not app:
                app = QFileDialog.getExistingDirectory(self, "Choose an application", start)
            if app:
                macos.open_with(paths, app)
        m.addAction("Other…", choose)

    def open_external(self):
        rows = self.lib.photos_by_ids(self._sel()[:10])
        macos.open_with([r["path"] for r in rows], None)

    def trash_selected(self):
        ids = self._sel()
        if not ids:
            return
        if QMessageBox.question(self, "Move to Trash",
                                f"Move {len(ids)} file{'s' if len(ids) != 1 else ''} to the Trash?") != QMessageBox.Yes:
            return
        if self.stack.currentIndex() == PAGE_EDITOR:
            self.close_editor()
        self.held = [i for i in self.held if i not in ids]
        self.lib.move_to_trash(ids)

    def rename_selected(self):
        ids = self._sel()
        rows = self.lib.photos_by_ids(ids)
        if not rows:
            return
        from .tools import BatchRenameDialog
        d = BatchRenameDialog(len(rows), rows[0], self)
        if not d.exec():
            return
        try:
            if len(rows) == 1 and not d.date.isChecked() and not d.res.isChecked():
                fileops.rename_photo(self.lib, rows[0]["id"], d.base.text().strip() or rows[0]["filename"])
            else:
                fileops.batch_rename(self.lib, ids, d.base.text(), d.date.isChecked(), d.res.isChecked())
        except OSError as e:
            QMessageBox.warning(self, "Rename", str(e))

    def adjust_dates(self):
        rows = self.lib.photos_by_ids(self._sel())
        if not rows:
            QMessageBox.information(self, "Adjust Date and Time", "Select the pictures to adjust first.")
            return
        from .tools import AdjustDateDialog
        d = AdjustDateDialog(rows, self)
        if d.exec():
            delta, absolute, write = d.result_values()
            fileops.adjust_dates(self.lib, [r["id"] for r in rows], delta, absolute, write)

    def batch_effect(self, fid):
        ids = self._sel()
        if not ids:
            return
        for pid in ids:
            chain = self.lib.filters(pid)
            args = [0.8] if fid == "fh_smart" else ([0.6] if fid == "unsharp2" else [])
            chain.append(Filter.make(fid, *args))
            self.lib.set_filters(pid, chain)

    def undo_all_edits(self):
        ids = self._sel()
        if not ids:
            return
        for pid in ids:
            self.lib.set_filters(pid, [])
            r = self.lib.photo(pid)
            if r["rotate"]:
                self.lib.rotate([pid], -r["rotate"])
        if self.stack.currentIndex() == PAGE_EDITOR:
            self.editor.open(self.editor.pid)

    def revert_selected(self):
        n = 0
        for pid in self._sel():
            if self.lib.revert_saved(pid):
                n += 1
        if not n:
            self.undo_all_edits()
        elif self.stack.currentIndex() == PAGE_EDITOR:
            self.editor.open(self.editor.pid)

    def save_selected(self):
        ids = [i for i in self._sel() if self.lib.photo(i) and (self.lib.photo(i)["filters"] or self.lib.photo(i)["rotate"])]
        if not ids:
            QMessageBox.information(self, "Save", "The selected pictures have no edits to save.")
            return

        def work(job):
            for k, pid in enumerate(ids):
                job.report("Saving edits to files…", k, len(ids))
                self.lib.save_edits(pid)
            return ids
        self.jobs.submit("save", work, on_done=lambda _: self.stack.currentIndex() == PAGE_EDITOR
                         and self.editor.open(self.editor.pid),
                         on_fail=lambda m: QMessageBox.warning(self, "Couldn't save", m))

    def save_copy(self, dest=None):
        from ..exporter import ExportOptions, export_photo
        out = []
        for r in self.lib.photos_by_ids(self._sel()):
            if r["kind"] == "video":
                continue
            folder = dest or os.path.dirname(r["path"])
            p = export_photo(r, ExportOptions(dest=folder, fmt="ORIGINAL", quality=95))
            stem, ext = os.path.splitext(r["filename"])
            target = fileops.unique_path(os.path.join(folder, f"{stem}-edited{ext if ext.lower() in ('.jpg', '.jpeg', '.png', '.tif', '.tiff', '.webp', '.heic') else '.jpg'}"))
            os.replace(p, target)
            out.append(target)
            self.lib.ensure_in_library(target)
        return out

    def save_as(self):
        rows = self.lib.photos_by_ids(self._sel()[:1])
        if not rows:
            return
        r = rows[0]
        path, _ = QFileDialog.getSaveFileName(self, "Save As", os.path.join(os.path.dirname(r["path"]),
                                              os.path.splitext(r["filename"])[0] + "-edited.jpg"),
                                              "JPEG (*.jpg);;PNG (*.png);;HEIC (*.heic);;TIFF (*.tif)")
        if not path:
            return
        from ..exporter import ExportOptions, export_photo
        ext = os.path.splitext(path)[1].lower()
        fmt = {".png": "PNG", ".heic": "HEIF", ".tif": "TIFF", ".tiff": "TIFF"}.get(ext, "JPEG")
        tmpd = tempfile.mkdtemp()
        p = export_photo(r, ExportOptions(dest=tmpd, fmt=fmt, quality=95))
        shutil.move(str(p), path)
        self.lib.ensure_in_library(path)

    def copy_pictures(self, cut=False):
        if self.stack.currentIndex() == PAGE_EDITOR or QApplication.focusWidget() is self.search_box:
            if QApplication.focusWidget() is self.search_box:
                (self.search_box.cut() if cut else self.search_box.copy())
            return
        ids = self._sel()
        if not ids:
            return
        self.clip_ids, self.clip_cut = list(ids), cut
        md = QMimeData()
        md.setUrls([QUrl.fromLocalFile(r["path"]) for r in self.lib.photos_by_ids(ids)])
        QGuiApplication.clipboard().setMimeData(md)
        self.tray.status.setText(f"{'Cut' if cut else 'Copied'} {len(ids)} picture(s) — choose a folder and Paste")

    def paste_pictures(self):
        if QApplication.focusWidget() is self.search_box:
            self.search_box.paste()
            return
        dest = None
        if self.collection[0] == "folder":
            f = self.lib.folder(self.collection[1])
            dest = f["path"] if f else None
        if not dest:
            QMessageBox.information(self, "Paste", "Open a folder first, then Paste to move or copy pictures into it.")
            return
        if self.clip_ids:
            fileops.move_photos(self.lib, self.clip_ids, dest, copy=not self.clip_cut)
            if self.clip_cut:
                self.clip_ids = []
            return
        md = QGuiApplication.clipboard().mimeData()
        if md and md.hasUrls():
            from .. import media
            for u in md.urls():
                p = u.toLocalFile()
                if p and os.path.isfile(p) and media.kind_of(p):
                    shutil.copy2(p, fileops.unique_path(os.path.join(dest, os.path.basename(p))))
            self.start_scan()

    def copy_image(self):
        rows = self.lib.photos_by_ids(self._sel()[:1])
        if rows:
            fileops.copy_to_clipboard_image(rows[0])

    def copy_path(self):
        rows = self.lib.photos_by_ids(self._sel())
        QGuiApplication.clipboard().setText("\n".join(r["path"] for r in rows))

    def copy_effects(self):
        ids = self._sel()
        if ids:
            self.copied_effects = [f for f in self.lib.filters(ids[0])]
            self.tray.status.setText(f"Copied {len(self.copied_effects)} effect(s)")

    def paste_effects(self):
        if not self.copied_effects:
            return
        for pid in self._sel():
            self.lib.set_filters(pid, list(self.copied_effects))
        if self.stack.currentIndex() == PAGE_EDITOR:
            self.editor.open(self.editor.pid)

    def select_starred(self):
        self.grid.select_ids([r["id"] for r in self.grid.model_.photo_rows() if r["star"]])

    def invert_selection(self):
        cur = set(self.grid.selected_ids())
        self.grid.select_ids([r["id"] for r in self.grid.model_.photo_rows() if r["id"] not in cur])

    def save_search(self):
        if self.collection[0] not in ("search", "ids"):
            return
        ids = [r["id"] for r in self.grid.model_.photo_rows()]
        name, ok = QInputDialog.getText(self, "Save search results", "Album name:",
                                        text=str(self.collection[1]) if self.collection[0] == "search" else "")
        if ok and name.strip():
            aid = self.lib.create_album(name.strip())
            self.lib.add_to_album(aid, ids)

    def reset_faces(self):
        ids = self._sel()
        for pid in ids:
            self.lib.db.x("DELETE FROM faces WHERE photo_id=? AND source='auto' AND confirmed=0", (pid,))
            self.lib.db.x("UPDATE photos SET faces_done=0 WHERE id=?", (pid,))
        self.start_index()

    def _add_person_manually(self, pid):
        self.open_editor(pid)
        self.editor.faces_btn.setChecked(True)
        self.editor.add_face_mode()
        self.tray.status.setText("Drag a box around the face, then type the name")

    # ---- albums
    def new_album(self, ids=None):
        from .tools import FolderPropertiesDialog
        d = FolderPropertiesDialog("New Album", parent=self, ok_text="Create")
        if d.exec():
            name, date, place, desc = d.values()
            if name:
                aid = self.lib.create_album(name, desc)
                self.lib.db.x("UPDATE albums SET place=?, date=? WHERE id=?", (place, date, aid))
                if ids:
                    self.lib.add_to_album(aid, ids)
                self.sidebar.rebuild()

    def _album_props(self, aid):
        from .tools import FolderPropertiesDialog
        a = self.lib.album(aid)
        d = FolderPropertiesDialog("Album Properties", a["name"], a["date"] if "date" in a.keys() else a["created"],
                                   a["place"] if "place" in a.keys() else "", a["description"] or "", self)
        if d.exec():
            name, date, place, desc = d.values()
            if name and name != a["name"]:
                self.lib.rename_album(aid, name)
            self.lib.db.x("UPDATE albums SET description=?, place=?, date=? WHERE id=?", (desc, place, date, aid))
            self.lib.emit("albums")

    def _fill_album_menu(self, m, ids=None):
        m.clear()
        ids = ids if ids is not None else self._tray_ids()
        for a in self.lib.albums():
            m.addAction(a["name"], lambda aid=a["id"]: self.lib.add_to_album(aid, ids))
        m.addSeparator()
        m.addAction("New Album…", lambda: self.new_album(ids))

    def _album_menu(self):
        m = QMenu(self)
        self._fill_album_menu(m)
        if self.collection[0] == "album":
            m.addAction("Remove from This Album", self._remove_from_album)
        m.exec(self.cursor().pos())

    def _remove_from_album(self):
        if self.collection[0] == "album":
            self.lib.remove_from_album(self.collection[1], self._sel())

    def _album_reordered(self, order):
        if self.collection[0] == "album":
            self.lib.reorder_album(self.collection[1], order)
            self.reload_collection(keep_selection=True)

    # ---- folders
    def _cur_folder(self):
        if self.collection[0] == "folder":
            return self.collection[1]
        rows = self.lib.photos_by_ids(self._sel()[:1])
        return rows[0]["folder_id"] if rows else None

    def _folder_props(self, fid):
        from .tools import FolderPropertiesDialog
        f = self.lib.folder(fid)
        d = FolderPropertiesDialog("Folder Properties", f["name"], f["date"], f["place"] or "", f["description"] or "",
                                   self)
        if d.exec():
            name, date, place, desc = d.values()
            fileops.set_folder_properties(self.lib, fid, name or f["name"], desc, place, date)

    def _rename_folder(self, fid):
        f = self.lib.folder(fid)
        name, ok = QInputDialog.getText(self, "Rename Folder", "New folder name (on disk):",
                                        text=os.path.basename(f["path"]))
        if ok and name.strip():
            try:
                fileops.rename_folder(self.lib, fid, name.strip())
            except OSError as e:
                QMessageBox.warning(self, "Rename Folder", str(e))

    def _move_folder(self, fid):
        d = QFileDialog.getExistingDirectory(self, "Move folder into…", str(Path.home()))
        if d:
            try:
                fileops.move_folder(self.lib, fid, d)
            except OSError as e:
                QMessageBox.warning(self, "Move Folder", str(e))

    def _delete_folder(self, fid):
        f = self.lib.folder(fid)
        if QMessageBox.question(self, "Delete Folder", f"Move the folder “{f['name']}” and everything in it to the "
                                "Trash?") == QMessageBox.Yes:
            fileops.delete_folder(self.lib, fid)
            self._go("all", None)

    def _toggle_folder_hidden(self, fid):
        f = self.lib.folder(fid)
        self.lib.set_folder_hidden(fid, not f["hidden"])

    def _remove_folder(self, fid):
        f = self.lib.folder(fid)
        if QMessageBox.question(self, "Remove from Fotohaus", f"Stop showing “{f['name']}” in Fotohaus? The files stay "
                                "on disk; you can add it back in the Folder Manager.") == QMessageBox.Yes:
            fileops.remove_folder_from_library(self.lib, fid)
            self._go("all", None)

    def new_folder(self, inside=False):
        fid = self._cur_folder()
        base = self.lib.folder(fid)["path"] if fid else default_pictures()
        parent = base if inside else os.path.dirname(base) if fid else base
        name, ok = QInputDialog.getText(self, "New Folder", f"New folder in {parent}:")
        if ok and name.strip():
            fileops.new_folder(self.lib, parent, name.strip())

    def move_to_folder(self):
        ids = self._sel()
        if not ids:
            return
        d = QFileDialog.getExistingDirectory(self, "Move pictures to folder", default_pictures())
        if d:
            fileops.move_photos(self.lib, ids, d)

    def move_to_new_folder(self):
        ids = self._sel()
        if not ids:
            return
        from .tools import FolderPropertiesDialog
        d = FolderPropertiesDialog("Move to New Folder", parent=self, ok_text="Move")
        if d.exec():
            name, date, place, desc = d.values()
            if name:
                path = fileops.move_to_new_folder(self.lib, ids, name, description=desc, place=place, date=date)
                fid = self.lib.db.scalar("SELECT id FROM folders WHERE path=?", (path,))
                if fid:
                    self._go("folder", fid)

    def split_folder(self):
        ids = self._sel()
        if not ids or self.collection[0] not in ("folder", "all"):
            QMessageBox.information(self, "Split Folder", "Select the first picture of the new folder.")
            return
        r = self.lib.photo(ids[0])
        name, ok = QInputDialog.getText(self, "Split Folder Here",
                                        "Pictures from the selected one onwards move to a new folder named:",
                                        text=f"{self.lib.folder(r['folder_id'])['name']} (2)")
        if ok and name.strip():
            fileops.split_folder_here(self.lib, r["folder_id"], r["id"], name.strip())

    def _drop_on_folder(self, fid, ids, copy):
        f = self.lib.folder(fid)
        if not f:
            return
        verb = "Copy" if copy else "Move"
        if QMessageBox.question(self, f"{verb} pictures", f"{verb} {len(ids)} picture(s) to “{f['name']}”?\n\n"
                                "This moves the actual files on disk, with their stars, captions and edits.") \
                == QMessageBox.Yes:
            fileops.move_photos(self.lib, ids, f["path"], copy=copy)

    def refresh_thumbnails(self):
        from .. import thumbs
        for r in self.lib.photos_by_ids(self._sel()) or self.grid.model_.photo_rows():
            thumbs.drop_cached(r)
            self.loader.invalidate(r["id"])
        self.grid.viewport().update()

    # ---- context menus
    def _grid_ctx(self, pos):
        ids = self.grid.selected_ids()
        if not ids:
            return
        m = QMenu(self)
        m.addAction("View and Edit", lambda: self.open_editor(ids[0]))
        m.addAction("View in Photo Viewer", self.view_in_viewer)
        m.addAction("Slideshow from Here", self.start_slideshow)
        m.addSeparator()
        m.addAction("Star / Unstar", self.toggle_star)
        m.addAction("Rotate Counterclockwise", lambda: self.rotate_selected(-1))
        m.addAction("Rotate Clockwise", lambda: self.rotate_selected(1))
        m.addAction("Hold in Photo Tray", self.hold_selection)
        be = m.addMenu("Batch Edit")
        for label, fid in BATCH:
            be.addAction(label, lambda fid=fid: self.batch_effect(fid))
        m.addAction("Copy All Effects", self.copy_effects)
        if self.copied_effects:
            m.addAction("Paste All Effects", self.paste_effects)
        m.addSeparator()
        am = m.addMenu("Add to Album")
        self._fill_album_menu(am, ids)
        if self.collection[0] == "album":
            m.addAction("Remove from This Album", self._remove_from_album)
        m.addAction("Add Tag…", self.add_tag_selected)
        m.addAction("Caption…", self.caption_selected)
        m.addAction("Find Similar Pictures", self.find_similar)
        m.addAction("Set Location on Map…", self.place_selected)
        m.addSeparator()
        m.addAction("Rename…", self.rename_selected)
        m.addAction("Adjust Date and Time…", self.adjust_dates)
        m.addAction("Move to New Folder…", self.move_to_new_folder)
        m.addAction("Locate in Folder", lambda: self.locate(ids[0]))
        m.addAction("Show in Finder", self.reveal_selected)
        ow = m.addMenu("Open With")
        self.open_with_menu_ctx = ow
        ow.aboutToShow.connect(lambda: self._fill_open_with_into(ow))
        m.addAction("Properties…", self.show_properties)
        m.addSeparator()
        m.addAction("Export…", self.export_selected)
        m.addAction("Email…", self.email_selected)
        m.addAction("Print…", self.print_selected)
        m.addAction("Share…", lambda: self._tray_action("share"))
        m.addAction("Set as Desktop", self.set_desktop)
        m.addAction("Hide / Unhide", self.hide_selected)
        m.addSeparator()
        m.addAction("Move to Trash", self.trash_selected)
        m.exec(self.grid.viewport().mapToGlobal(pos))

    def _fill_open_with_into(self, menu):
        saved = self.open_with_menu
        self.open_with_menu = menu
        self._fill_open_with()
        self.open_with_menu = saved

    def _sidebar_ctx(self, kind, ident, gpos):
        m = QMenu(self)
        if kind == "album":
            m.addAction("Play Slideshow", lambda: (self._go("album", ident), self.start_slideshow(from_start=True)))
            m.addAction("Album Properties…", lambda: self._album_props(ident))
            m.addAction("Export Album…", lambda: self.export_ids([r["id"] for r in self.lib.photos_in_album(ident)]))
            m.addAction("Export as HTML Page…", lambda: self.export_html([r["id"] for r in self.lib.photos_in_album(ident)]))
            m.addSeparator()
            m.addAction("Delete Album", lambda: self._delete_album(ident))
        elif kind == "folder":
            f = self.lib.folder(ident)
            if not f:
                return
            m.addAction("Play Slideshow", lambda: (self._go("folder", ident), self.start_slideshow(from_start=True)))
            m.addAction("Folder Properties…", lambda: self._folder_props(ident))
            m.addAction("Show in Finder", lambda: reveal_in_finder(f["path"]))
            m.addSeparator()
            m.addAction("Rename Folder…", lambda: self._rename_folder(ident))
            m.addAction("Move Folder…", lambda: self._move_folder(ident))
            m.addAction("Unhide Folder" if f["hidden"] else "Hide Folder", lambda: self._toggle_folder_hidden(ident))
            m.addAction("Export Folder…", lambda: self.export_ids([r["id"] for r in self.lib.photos_in_folder(ident)]))
            m.addAction("Remove from Fotohaus…", lambda: self._remove_folder(ident))
            m.addSeparator()
            m.addAction("Delete Folder…", lambda: self._delete_folder(ident))
        elif kind == "person":
            m.addAction("Play Slideshow", lambda: (self._go("person", ident), self.start_slideshow(from_start=True)))
            m.addAction("Rename…", lambda: self._rename_person(ident))
            m.addAction("Review Suggestions…", lambda: self._review_person(ident))
            m.addSeparator()
            m.addAction("Remove Person", lambda: (self.lib.remove_person(ident), self.sidebar.rebuild()))
        else:
            return
        m.exec(gpos)

    def _rename_person(self, pid):
        p = self.lib.person(pid)
        name, ok = QInputDialog.getText(self, "Rename", "Name:", text=p["name"])
        if ok and name.strip():
            self.lib.rename_person(pid, name.strip())

    def _review_person(self, pid):
        from .people import SuggestionsDialog
        SuggestionsDialog(self.lib, self.people.crops, pid, self).exec()

    def _delete_album(self, aid):
        a = self.lib.album(aid)
        if QMessageBox.question(self, "Delete album", f"Delete the album “{a['name']}”? "
                                "The pictures themselves are not deleted.") == QMessageBox.Yes:
            self.lib.delete_album(aid)
            self._go("all", None)

    # ================================================================== smart features
    def find_similar(self):
        ids = self._sel()
        if not ids:
            return
        if not (self.lib.settings.get("clip_enabled") and clip.available()):
            r = self.lib.photo(ids[0])
            if not r or not r["phash"]:
                QMessageBox.information(self, "Find similar", "This picture hasn't been analyzed yet.")
                return
            from ..ai.dupes import _popcount
            import numpy as np
            rows = self.lib.db.q("SELECT id, phash FROM photos WHERE phash IS NOT NULL AND phash != ''")
            hv = np.array([int(x["phash"], 16) for x in rows], dtype=np.uint64)
            d = _popcount(hv ^ np.uint64(int(r["phash"], 16)))
            order = np.argsort(d)[:60]
            sim = [rows[i]["id"] for i in order if d[i] <= 18]
        else:
            sim = [ids[0]] + [pid for pid, _ in clip.similar_to(self.lib, ids[0], 80)]
        self.collection = ("ids", sim)
        self.reload_collection()

    def place_selected(self):
        ids = self._tray_ids()
        self._go("places", None)
        self.places.set_selection(ids)
        if ids:
            self.places._start_pick()

    def _show_on_map(self, lat, lon):
        self._go("places", None)
        self.places.map.go(lat, lon, 14)

    # ================================================================== import
    def add_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Add a folder to Fotohaus", default_pictures())
        if d:
            self.lib.add_root(d)
            self.start_scan()

    def add_file(self):
        p, _ = QFileDialog.getOpenFileName(self, "Add a file to Fotohaus", default_pictures())
        if p:
            r = self.lib.ensure_in_library(p)
            if r:
                self.locate(r["id"])

    def import_from(self):
        from .manage import ImportDialog
        ImportDialog(self.lib, self.jobs, self).exec()

    def import_takeout(self):
        d = QFileDialog.getExistingDirectory(self, "Choose the unzipped Google Takeout folder", str(Path.home()))
        if not d:
            return
        from ..takeout import import_takeout

        def work(job):
            return import_takeout(self.lib, d, progress=lambda m: job.report(m), should_stop=job.should_stop)

        def done(stats):
            QMessageBox.information(self, "Google Takeout", (
                f"Imported info for {stats['photos']} items: {stats['albums']} albums, "
                f"{stats['favorites']} favorites, {stats['captions']} descriptions, {stats['places']} locations."))
            self.start_index()
        self.jobs.submit("takeout", work, on_done=done,
                         on_fail=lambda m: QMessageBox.warning(self, "Import failed", m))

    def add_apple_photos(self):
        default = Path.home() / "Pictures" / "Photos Library.photoslibrary"
        path = None
        if default.exists():
            r = QMessageBox.question(self, "Apple Photos", f"Add “{default.name}” (read-only)?\n\n"
                                     "Fotohaus will show its original files but never modify the library.",
                                     QMessageBox.Yes | QMessageBox.No | QMessageBox.Open)
            if r == QMessageBox.Yes:
                path = default
            elif r != QMessageBox.Open:
                return
        if path is None:
            d = QFileDialog.getExistingDirectory(self, "Choose a .photoslibrary", str(Path.home() / "Pictures"))
            if not d:
                return
            path = Path(d)
        orig = next((path / s for s in ("originals", "Masters") if (path / s).exists()), None)
        if orig is None:
            QMessageBox.warning(self, "Apple Photos", "Couldn't read that library. On recent macOS you may need to "
                                "give Fotohaus Full Disk Access in System Settings ▸ Privacy & Security.")
            return
        self.lib.add_root(str(orig), readonly=True, kind="applephotos")
        self.start_scan()

    def import_contacts(self):
        p, _ = QFileDialog.getOpenFileName(self, "Picasa contacts.xml", str(Path.home()), "XML (*.xml)")
        if p:
            n = self.lib.import_contacts_xml(p)
            QMessageBox.information(self, "Contacts", f"Updated {n} people.")

    # ================================================================== output
    def export_selected(self):
        ids = self._tray_ids() or [r["id"] for r in self.grid.model_.photo_rows()]
        self.export_ids(ids)

    def export_ids(self, ids):
        if not ids:
            return
        dlg = ExportDialog(self.lib, len(ids), self)
        if not dlg.exec():
            return
        opts = dlg.options()
        from ..exporter import export_photo

        def work(job):
            out = []
            rows = self.lib.photos_by_ids(ids)
            for i, r in enumerate(rows):
                if job.should_stop():
                    break
                job.report(f"Exporting {r['filename']}", i, len(rows))
                try:
                    out.append(str(export_photo(r, opts)))
                except Exception as e:
                    print("export failed", r["path"], e)
            return out
        self.jobs.submit(f"export-{id(opts)}", work, on_done=lambda paths: self._done_reveal(paths, "Export finished"),
                         dedupe=False)

    def _done_reveal(self, paths, title):
        if not paths:
            return
        paths = paths if isinstance(paths, list) else [paths]
        m = QMessageBox(self)
        m.setWindowTitle(title)
        m.setText(f"{len(paths)} file{'s' if len(paths) != 1 else ''} created." if len(paths) > 1
                  else f"Created {os.path.basename(str(paths[0]))}.")
        show = m.addButton("Show in Finder", QMessageBox.AcceptRole)
        m.addButton(QMessageBox.Ok)
        m.exec()
        if m.clickedButton() == show:
            reveal_in_finder(str(paths[0]))

    def email_selected(self):
        rows = self.lib.photos_by_ids(self._tray_ids())
        if not rows:
            return
        from .tools import EmailDialog
        d = EmailDialog(len(rows), int(self.lib.settings.get("email_size") or 800), self)
        if not d.exec():
            return
        size, subject = d.size.currentData(), d.subject.text()
        self.lib.settings.set("email_size", size)

        def work(job):
            job.report("Preparing pictures for email…")
            photos = [r for r in rows if r["kind"] != "video"]
            paths = macos.resized_copies(photos, size)
            return paths + [r["path"] for r in rows if r["kind"] == "video"]
        self.jobs.submit("email", work, on_done=lambda paths: macos.email_files(paths, subject))

    def print_selected(self, contact=False):
        rows = self.lib.photos_by_ids(self._tray_ids()) or self.grid.model_.photo_rows()
        if not rows:
            return
        from .create import PrintDialog
        PrintDialog(self.lib, rows, contact, self).exec()

    def export_html(self, ids=None):
        ids = ids or self._tray_ids() or [r["id"] for r in self.grid.model_.photo_rows()]
        if not ids:
            return
        from .tools import HtmlExportDialog
        d = HtmlExportDialog(len(ids), self)
        if not d.exec():
            return
        title, tpl, size = d.title.text().strip() or "Photos", d.template.currentData(), d.size.currentData()
        dest = os.path.join(d.dest.text(), title)
        from ..exporter import export_html

        def work(job):
            return str(export_html(self.lib.photos_by_ids(ids), dest, title, tpl, size,
                                   progress=lambda i, t: job.report("Making web page…", i, t)))
        self.jobs.submit("html", work, on_done=lambda p: QDesktopServices.openUrl(QUrl.fromLocalFile(p)))

    def export_kml(self):
        ids = self._tray_ids()
        rows = self.lib.photos_by_ids(ids) if ids else self.lib.geotagged()
        path, _ = QFileDialog.getSaveFileName(self, "Export to Google Earth", os.path.join(default_pictures(),
                                              "Fotohaus Places.kml"), "KML (*.kml)")
        if path:
            from ..exporter import export_kml
            n = export_kml(rows, path)
            QMessageBox.information(self, "Google Earth", f"Exported {n} geotagged pictures.")

    def make_collage(self):
        ids = self._tray_ids()
        if len(ids) < 2:
            QMessageBox.information(self, "Collage", "Select two or more pictures first (or hold them in the tray).")
            return
        from .create import CollageDialog
        dlg = CollageDialog(self.lib, self.lib.photos_by_ids(ids), self)
        if not dlg.exec():
            return
        p = dlg.params(scale=1.0)
        out_dir = Path(default_pictures()) / "Fotohaus Collages"
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"Collage {_dt.datetime.now():%Y-%m-%d %H.%M.%S}.jpg"
        from ..exporter import make_collage

        def work(job):
            job.report("Making collage…")
            rows = p.pop("rows")
            make_collage(rows, str(out), **p)
            return str(out)

        def done(path):
            r = self.lib.ensure_in_library(path)
            if r:
                self.locate(r["id"])
                self.open_editor(r["id"])
        self.jobs.submit("collage", work, on_done=done)

    def make_movie(self):
        ids = self._tray_ids()
        if not ids:
            QMessageBox.information(self, "Movie", "Select the pictures and videos for the movie first.")
            return
        from .create import MovieMakerDialog
        dlg = MovieMakerDialog(self.lib, self.lib.photos_by_ids(ids), self)
        if not dlg.exec():
            return
        movies = Path.home() / "Movies"
        default = (movies if movies.exists() else Path(default_pictures())) / f"Fotohaus Movie {_dt.datetime.now():%Y-%m-%d %H.%M}.mp4"
        path, _ = QFileDialog.getSaveFileName(self, "Save movie", str(default), "MPEG-4 movie (*.mp4)")
        if not path:
            return
        opts = dlg.options(path)
        rows = dlg.ordered_rows()
        from ..movie import make_movie

        def work(job):
            return make_movie(rows, opts, progress=lambda i, t: job.report("Making movie…", i, t),
                              should_stop=job.should_stop)
        self.jobs.submit("movie", work, on_done=lambda p: self._done_reveal([p], "Movie created"),
                         on_fail=lambda m: QMessageBox.warning(self, "Movie failed", m))

    def make_poster(self):
        rows = self.lib.photos_by_ids(self._sel()[:1])
        if not rows:
            return
        from .tools import PosterDialog
        d = PosterDialog(self)
        if not d.exec():
            return
        dest = QFileDialog.getExistingDirectory(self, "Save poster pages to", default_pictures())
        if not dest:
            return
        enlarge, paper, overlap = d.enlarge.currentData(), d.paper.currentText(), d.overlap.isChecked()
        from ..exporter import make_poster

        def work(job):
            job.report("Making poster pages…")
            return str(make_poster(rows[0], os.path.join(dest, f"{Path(rows[0]['filename']).stem} poster"),
                                   enlarge, paper, overlap))
        self.jobs.submit("poster", work, on_done=lambda p: self._done_reveal([p], "Poster created"))

    def make_passport(self):
        rows = self.lib.photos_by_ids(self._sel()[:1])
        if not rows:
            return
        from .tools import PassportDialog
        d = PassportDialog(self)
        if not d.exec():
            return
        spec = d.spec.currentText()
        r = rows[0]
        out = fileops.unique_path(os.path.join(os.path.dirname(r["path"]) if os.access(os.path.dirname(r["path"]),
                                  os.W_OK) else default_pictures(), f"{Path(r['filename']).stem}-passport.jpg"))
        from ..exporter import make_passport_sheet

        def work(job):
            job.report("Making passport photo sheet…")
            return str(make_passport_sheet(r, out, spec))

        def done(p):
            nr = self.lib.ensure_in_library(p)
            if nr:
                self.open_editor(nr["id"])
        self.jobs.submit("passport", work, on_done=done)

    def make_gift(self):
        ids = self._tray_ids() or [r["id"] for r in self.grid.model_.photo_rows()]
        if not ids:
            return
        from .tools import GiftDiskDialog
        d = GiftDiskDialog(len(ids), self)
        if not d.exec():
            return
        name, size, html = d.name.text().strip() or "Photos", d.size.currentData(), d.html.isChecked()
        out, _ = QFileDialog.getSaveFileName(self, "Save disk image", os.path.join(default_pictures(), f"{name}.dmg"),
                                             "Disk image (*.dmg)")
        if not out:
            return
        from ..exporter import ExportOptions, export_html, export_photo

        def work(job):
            tmp = Path(tempfile.mkdtemp()) / name
            (tmp / "Pictures").mkdir(parents=True)
            rows = self.lib.photos_by_ids(ids)
            for i, r in enumerate(rows):
                job.report("Preparing pictures…", i, len(rows))
                export_photo(r, ExportOptions(dest=str(tmp / "Pictures"), max_size=size or None,
                                              fmt="ORIGINAL" if not size else "JPEG"))
            if html:
                export_html(rows, str(tmp / "Slideshow"), name, "dark", 1600)
            if sys.platform == "darwin":
                subprocess.run(["hdiutil", "create", "-volname", name, "-srcfolder", str(tmp), "-ov", "-format",
                                "UDZO", out], check=True, capture_output=True)
                return out
            zip_path = shutil.make_archive(os.path.splitext(out)[0], "zip", str(tmp))
            return zip_path
        self.jobs.submit("gift", work, on_done=lambda p: self._done_reveal([p], "Gift disk image created"),
                         on_fail=lambda m: QMessageBox.warning(self, "Gift disk", m))

    def set_desktop(self):
        rows = self.lib.photos_by_ids(self._sel()[:1])
        if not rows or rows[0]["kind"] == "video":
            return
        r = rows[0]
        path = r["path"]
        if r["filters"] or r["rotate"]:
            from ..exporter import ExportOptions, export_photo
            d = Path(default_pictures()) / "Fotohaus Desktop"
            path = str(export_photo(r, ExportOptions(dest=str(d), fmt="JPEG", quality=95)))
        if not macos.set_desktop_picture(path):
            QMessageBox.information(self, "Set as Desktop", "Couldn't change the desktop picture on this system.")

    def add_to_screensaver(self):
        rows = self.lib.photos_by_ids(self._tray_ids())
        if not rows:
            return
        from ..exporter import ExportOptions, export_photo
        folder = macos.screensaver_folder()
        for r in rows:
            if r["kind"] != "video":
                export_photo(r, ExportOptions(dest=folder, max_size=3840, fmt="JPEG", quality=92))
        if QMessageBox.question(self, "Screensaver", f"Added {len(rows)} pictures to “{folder}”.\n\nIn Screen Saver "
                                "settings, choose a photo screen saver and pick this folder as its source. "
                                "Open Screen Saver settings now?") == QMessageBox.Yes:
            macos.open_screensaver_settings()

    def start_slideshow(self, from_start=False):
        rows = [r for r in self.grid.model_.photo_rows() if r["kind"] != "video"]
        if not rows:
            return
        sel = self.grid.selected_ids()
        start = 0
        if sel and not from_start:
            ids = [r["id"] for r in rows]
            start = ids.index(sel[0]) if sel[0] in ids else 0
        self.slideshow = Slideshow(rows, start, int(self.lib.settings.get("slideshow_seconds") or 4), lib=self.lib)
        self.slideshow.closed.connect(lambda pid: pid > 0 and self.grid.select_ids([pid]))
        self.slideshow.start()

    # ================================================================== tools
    def folder_manager(self):
        from .manage import FolderManagerDialog
        FolderManagerDialog(self.lib, self).exec()
        self.start_scan()
        self._refresh_all()

    def backup(self):
        from .manage import BackupDialog
        BackupDialog(self.lib, self.jobs, self).exec()

    def open_settings(self, tab=0):
        from .manage import OptionsDialog
        dlg = OptionsDialog(self.lib, self)
        dlg.rescan.connect(self.start_scan)
        dlg.reindex.connect(self._reindex_all)
        dlg.download_clip.connect(lambda: self.download_clip(dlg))
        dlg.folder_manager.connect(self.folder_manager)
        if isinstance(tab, int):
            dlg.set_page(tab)
        before = (self.lib.settings.get("faces_enabled"), self.lib.settings.get("vision_enabled"),
                  self.lib.settings.get("clip_enabled"), tuple(self.lib.settings.get("file_types") or []))
        if dlg.exec():
            after = (self.lib.settings.get("faces_enabled"), self.lib.settings.get("vision_enabled"),
                     self.lib.settings.get("clip_enabled"), tuple(self.lib.settings.get("file_types") or []))
            if after[3] != before[3]:
                self.start_scan()
            elif after != before:
                self.start_index()
            self.tray.set_buttons(list(self.lib.settings.get("button_bar") or []))
            self.tray.zoom.setValue(int(self.lib.settings.get("thumb_size") or 160))
            self._caption_mode()
            self._refresh_all()

    def _reindex_all(self):
        self.lib.db.x("UPDATE photos SET faces_done=0, labels_done=0, clip_done=0, phash=NULL")
        self.start_index()

    def download_clip(self, dlg=None):
        def work(job):
            clip.download(progress=lambda f, m: job.report(m, int(f * 100), 100), should_stop=job.should_stop)
            return True

        def done(_):
            self.lib.settings.set("clip_enabled", True)
            if dlg is not None:
                try:
                    dlg.clip_status.setText("Model installed.")
                    dlg.clip_cb.setChecked(True)
                except RuntimeError:
                    pass
            QMessageBox.information(self, "Natural-language search", "Model installed. Fotohaus will now analyze "
                                    "your pictures so you can search by description (e.g. “sunset over water”).")
            self.start_index()
        self.jobs.submit("clip-download", work, on_done=done,
                         on_fail=lambda m: QMessageBox.warning(self, "Download failed", m))

    def show_cli_help(self):
        exe = sys.executable
        cli = os.path.join(os.path.dirname(exe), "fotohaus-cli") if exe.endswith("Fotohaus") else f"{exe} -m fotohaus.cli"
        QMessageBox.information(self, "Command line & Claude", (
            f"Fotohaus includes a command-line tool:\n\n  {cli} search \"beach 2019\"\n  {cli} stats\n\n"
            "To let Claude (or another MCP client) browse and organize your library, add this MCP server:\n\n"
            f'  "fotohaus": {{ "command": "{cli}", "args": ["mcp"] }}\n\n'
            "The server can search, describe, star, caption and add pictures to albums."))

    def show_shortcuts(self):
        from .tools import ShortcutsDialog
        ShortcutsDialog(self).exec()

    def search_help(self):
        from .. import search as s
        QMessageBox.information(self, "Search tips", s.__doc__.strip())

    def compat_help(self):
        QMessageBox.information(self, "Picasa compatibility", (
            "Fotohaus reads and writes Picasa's .picasa.ini files, so your Picasa stars, captions, albums, name tags "
            "and edits appear here and stay visible to Picasa.\n\n"
            "• Edits are non-destructive until you choose File ▸ Save; originals go into .picasaoriginals, "
            "exactly like Picasa.\n"
            "• Captions and tags can be stored inside JPEGs (IPTC/XMP) — see Options ▸ Name Tags & Metadata.\n"
            "• Picasa's contacts.xml (name tags) can be imported from Options ▸ Name Tags & Metadata.\n\n"
            "Picasa Web Albums, Google+ sharing, Blogger and print ordering were Google services that no longer "
            "exist; Fotohaus offers the macOS Share menu, Email, web-page export and printing instead."))

    def about(self):
        QMessageBox.about(self, f"About {APP_NAME}", (
            f"<h3>{APP_NAME} {__version__}</h3>"
            "<p>A private, on-device photo organizer for macOS, compatible with Picasa's "
            "<code>.picasa.ini</code> metadata.</p>"
            "<p>Picasa is a trademark of Google LLC. Fotohaus is an independent project.</p>"
            "<p>Map data © OpenStreetMap contributors. Face models from OpenCV Zoo. "
            "See the Licenses folder inside the app for third-party licenses.</p>"
            "<p>Free to use for everyone. Selling or bundling Fotohaus requires a license "
            "from the author.</p>"
            f'<p>Enjoying {APP_NAME}? <a href="{SUPPORT_URL}">☕ Buy me a coffee</a></p>'))

    # ================================================================== window state
    def _restore(self):
        s = QSettings(APP_NAME, APP_NAME)
        g = s.value("geometry")
        if isinstance(g, QByteArray):
            self.restoreGeometry(g)
        sp = s.value("splitter")
        if isinstance(sp, QByteArray):
            self.splitter.restoreState(sp)
        info = s.value("info_visible")
        if info is not None:
            self.act_info.setChecked(str(info).lower() in ("true", "1"))

    def closeEvent(self, e):
        s = QSettings(APP_NAME, APP_NAME)
        s.setValue("geometry", self.saveGeometry())
        s.setValue("splitter", self.splitter.saveState())
        s.setValue("info_visible", self.act_info.isChecked())
        self.jobs.stop_all()
        super().closeEvent(e)
