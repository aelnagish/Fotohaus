"""GUI entry point: library window, plus the Photo Viewer for files opened from Finder."""
from __future__ import annotations

import os
import sys

os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")


def _mac_app_name(name: str) -> None:
    """Make the menu bar show the app name when not launched from a bundle."""
    if sys.platform != "darwin":
        return
    try:
        from Foundation import NSBundle  # type: ignore
        info = NSBundle.mainBundle().localizedInfoDictionary() or NSBundle.mainBundle().infoDictionary()
        if info is not None and info.get("CFBundleName") != name:
            info["CFBundleName"] = name
    except Exception:
        pass


def main(argv=None) -> int:
    from . import APP_NAME
    from .paths import ensure_dirs
    ensure_dirs()
    _mac_app_name(APP_NAME)
    from PySide6.QtCore import QEvent, QObject, Qt, QTimer, Signal
    from PySide6.QtGui import QGuiApplication, QIcon
    from PySide6.QtWidgets import QApplication

    class FotohausApp(QApplication):
        file_open = Signal(str)

        def event(self, e):
            if e.type() == QEvent.FileOpen:
                path = e.file() or e.url().toLocalFile()
                if path:
                    self.file_open.emit(path)
                return True
            return super().event(e)

    argv = list(argv if argv is not None else sys.argv)
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = FotohausApp(argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setOrganizationName(APP_NAME)
    icon_path = os.path.join(os.path.dirname(__file__), "resources", "icon.png")
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))
    from .library import get_library
    from .ui import theme
    lib = get_library()
    st = lib.settings
    theme.apply_appearance(app, st.get("theme") or "system", st.get("accent") or "system",
                           st.get("library_bg") or "neutral")

    class Controller(QObject):
        def __init__(self):
            super().__init__()
            self.main = None
            self.viewers = []
            app.file_open.connect(self.open_path)

        def show_main(self):
            if self.main is None:
                from .ui.mainwindow import MainWindow
                self.main = MainWindow(lib)
                self.main.open_in_viewer.connect(self.open_viewer)
            self.main.show()
            self.main.raise_()
            self.main.activateWindow()
            return self.main

        def open_path(self, path):
            path = os.path.abspath(path)
            if os.path.isdir(path):
                m = self.show_main()
                lib.add_root(path)
                m.start_scan()
                return
            from . import media
            if not media.kind_of(path):
                return
            if lib.settings.get("open_files_in") == "editor":
                self.edit(path)
            else:
                self.open_viewer(path)

        def open_viewer(self, path):
            from .ui.viewer import PhotoViewer
            v = PhotoViewer(lib, path)
            v.edit_requested.connect(lambda p, v=v: (v.close(), self.edit(p)))
            v.destroyed.connect(lambda *_ , v=v: self.viewers.remove(v) if v in self.viewers else None)
            self.viewers.append(v)
            v.show()
            v.raise_()
            v.activateWindow()

        def edit(self, path):
            m = self.show_main()
            row = lib.ensure_in_library(path)
            if row is not None:
                m.locate(row["id"])
                m.open_editor(row["id"])

    ctl = Controller()
    for a in argv[1:]:
        if os.path.exists(a):
            ctl.open_path(a)

    def decide():
        if not ctl.viewers and ctl.main is None:
            ctl.show_main()
    # Files double-clicked in Finder arrive as Apple Events just after launch: wait a moment so a
    # photo opens straight into the viewer instead of the full library.
    QTimer.singleShot(350, decide)

    quit_ms = os.environ.get("FOTOHAUS_AUTOQUIT_MS")  # used by automated launch tests
    if quit_ms:
        def _done():
            print(f"FOTOHAUS_AUTOQUIT ok: viewers={len(ctl.viewers)} main={ctl.main is not None} "
                  f"{lib.counts()}", flush=True)
            app.quit()
        QTimer.singleShot(int(quit_ms), _done)
    app._controller = ctl
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
