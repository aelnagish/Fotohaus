"""Platform integration (macOS via PyObjC, Windows Hello via WinRT) with fallbacks elsewhere."""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile

IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform == "win32"

# What the system's file browser is called, for menu items like "Show in Finder".
FILE_MANAGER = "Finder" if IS_MAC else "File Explorer" if IS_WIN else "File Manager"
SHOW_IN = f"Show in {FILE_MANAGER}"
TRASH = "Recycle Bin" if IS_WIN else "Trash"
TRASH_PLACE = "the Mac's Trash" if IS_MAC else f"the {TRASH}"
UNLOCK_WITH = "Touch ID" if IS_MAC else "Windows Hello" if IS_WIN else "your Fotohaus password"


def keys(text: str) -> str:
    """Shortcut text written with Mac symbols (⌘8, ⇧⌘A) as the reader's keyboard shows it."""
    if IS_MAC:
        return text
    out = re.sub(r"([⌃⌥⇧⌘]+)", lambda m: "".join({"⌃": "Ctrl+", "⌥": "Alt+", "⇧": "Shift+", "⌘": "Ctrl+"}[c]
                                                  for c in sorted(m.group(1), key="⌘⌃⌥⇧".index)), text)
    return out.replace("⌫", "Backspace").replace("+Return", "+Enter")

# Uniform Type Identifiers Fotohaus can open (used for "Configure Photo Viewer").
VIEWER_TYPES = [
    ("JPEG", "public.jpeg"), ("PNG", "public.png"), ("HEIC/HEIF", "public.heic"), ("HEIF", "public.heif"),
    ("TIFF", "public.tiff"), ("GIF", "com.compuserve.gif"), ("WebP", "org.webmproject.webp"),
    ("BMP", "com.microsoft.bmp"), ("AVIF", "public.avif"), ("Photoshop", "com.adobe.photoshop-image"),
    ("Camera RAW (all)", "public.camera-raw-image"), ("DNG", "com.adobe.raw-image"),
    ("MPEG-4 video", "public.mpeg-4"), ("QuickTime movie", "com.apple.quicktime-movie"),
    ("AVI video", "public.avi"),
]


def applescript_str(s: str) -> str:
    """A quoted AppleScript string literal (paths may contain quotes or backslashes)."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _ns():
    import AppKit  # noqa: F401
    import Foundation
    return AppKit, Foundation


def reveal(path: str) -> None:
    if IS_MAC:
        subprocess.Popen(["open", "-R", path])
    elif IS_WIN:
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
    else:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path)))


# ------------------------------------------------------------------ sharing / email

def sharing_services(paths: list[str]) -> list:
    """[(title, service)] of macOS share services that accept these files."""
    if not IS_MAC:
        return []
    try:
        AppKit, Foundation = _ns()
        items = [Foundation.NSURL.fileURLWithPath_(p) for p in paths]
        services = AppKit.NSSharingService.sharingServicesForItems_(items) or []
        return [(str(s.title()), s) for s in services]
    except Exception:
        return []


def perform_share(service, paths: list[str]) -> bool:
    try:
        AppKit, Foundation = _ns()
        items = [Foundation.NSURL.fileURLWithPath_(p) for p in paths]
        if service.canPerformWithItems_(items):
            service.performWithItems_(items)
            return True
    except Exception:
        pass
    return False


def share_files(paths: list[str], parent=None) -> None:
    """Show a Share menu (Mail, Messages, AirDrop, Notes, …) at the cursor."""
    from PySide6.QtGui import QCursor
    from PySide6.QtWidgets import QMenu
    services = sharing_services(paths)
    m = QMenu(parent)
    for title, svc in services:
        m.addAction(title, lambda s=svc: perform_share(s, paths))
    if services:
        m.addSeparator()
    m.addAction("Email…", lambda: email_files(paths))
    m.addAction(SHOW_IN, lambda: reveal(paths[0]))
    m.exec(QCursor.pos())


def email_files(paths: list[str], subject: str = "", body: str = "") -> bool:
    """Open a new email with the files attached (default mail app)."""
    if IS_MAC:
        try:
            AppKit, Foundation = _ns()
            svc = AppKit.NSSharingService.sharingServiceNamed_(AppKit.NSSharingServiceNameComposeEmail)
            if svc is not None:
                if subject:
                    svc.setSubject_(subject)
                items = ([body] if body else []) + [Foundation.NSURL.fileURLWithPath_(p) for p in paths]
                if svc.canPerformWithItems_(items):
                    svc.performWithItems_(items)
                    return True
        except Exception:
            pass
        return subprocess.call(["open", "-a", "Mail"] + list(paths)) == 0
    import shutil
    if not IS_WIN and shutil.which("xdg-email"):
        cmd = ["xdg-email"] + (["--subject", subject] if subject else []) + (["--body", body] if body else [])
        for p in paths:
            cmd += ["--attach", p]
        try:
            if subprocess.call(cmd) == 0:
                return True
        except OSError:
            pass
    # A mailto: link can't carry attachments, so show the prepared pictures next to the new email
    # for dragging in.
    try:
        from urllib.parse import quote
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        ok = QDesktopServices.openUrl(QUrl(f"mailto:?subject={quote(subject)}&body={quote(body)}"))
        if paths:
            reveal(paths[0])
        return ok
    except Exception:
        return False


def resized_copies(rows, max_size: int, quality: int = 85) -> list[str]:
    """Picasa-style email preparation: edited, resized JPEG copies in a temp folder."""
    from .exporter import ExportOptions, export_photo
    d = tempfile.mkdtemp(prefix="fotohaus-mail-")
    opts = ExportOptions(dest=d, max_size=max_size or None, fmt="JPEG", quality=quality)
    out = []
    for r in rows:
        try:
            out.append(str(export_photo(r, opts)))
        except Exception:
            pass
    return out


# ------------------------------------------------------------------ desktop picture

def set_desktop_picture(path: str) -> bool:
    if IS_MAC:
        try:
            AppKit, Foundation = _ns()
            url = Foundation.NSURL.fileURLWithPath_(path)
            ws = AppKit.NSWorkspace.sharedWorkspace()
            ok = True
            for screen in AppKit.NSScreen.screens():
                res = ws.setDesktopImageURL_forScreen_options_error_(url, screen, {}, None)
                ok = ok and bool(res[0] if isinstance(res, tuple) else res)
            return ok
        except Exception:
            script = ('tell application "System Events" to tell every desktop to set picture to POSIX file '
                      + applescript_str(path))
            return subprocess.call(["osascript", "-e", script]) == 0
    if IS_WIN:
        try:
            import ctypes
            SPI_SETDESKWALLPAPER, SPIF_UPDATEINIFILE, SPIF_SENDCHANGE = 20, 1, 2
            return bool(ctypes.windll.user32.SystemParametersInfoW(
                SPI_SETDESKWALLPAPER, 0, os.path.abspath(path), SPIF_UPDATEINIFILE | SPIF_SENDCHANGE))
        except Exception:
            return False
    return _linux_desktop_picture(path)


def _linux_desktop_picture(path: str) -> bool:
    """GNOME / Cinnamon / MATE via gsettings, KDE Plasma via its scripting interface."""
    import shutil
    from pathlib import Path
    uri = Path(path).resolve().as_uri()
    desktop = (os.environ.get("XDG_CURRENT_DESKTOP") or "").lower()
    try:
        if "kde" in desktop and shutil.which("qdbus"):
            js = ("desktops().forEach(d => { d.wallpaperPlugin = 'org.kde.image'; "
                  "d.currentConfigGroup = ['Wallpaper', 'org.kde.image', 'General']; "
                  f"d.writeConfig('Image', {uri!r}); }});")
            return subprocess.call(["qdbus", "org.kde.plasmashell", "/PlasmaShell",
                                    "org.kde.PlasmaShell.evaluateScript", js]) == 0
        if shutil.which("gsettings"):
            schemas = [("org.gnome.desktop.background", "picture-uri", uri),
                       ("org.gnome.desktop.background", "picture-uri-dark", uri),
                       ("org.cinnamon.desktop.background", "picture-uri", uri),
                       ("org.mate.background", "picture-filename", os.path.abspath(path))]
            ok = False
            for schema, key, value in schemas:
                ok = subprocess.call(["gsettings", "set", schema, key, value], stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL) == 0 or ok
            return ok
    except OSError:
        pass
    return False


# ------------------------------------------------------------------ open with / default apps

def apps_for(path: str) -> list[tuple[str, str]]:
    """[(app name, app path)] that can open this file."""
    if not IS_MAC:
        return []
    try:
        AppKit, Foundation = _ns()
        url = Foundation.NSURL.fileURLWithPath_(path)
        ws = AppKit.NSWorkspace.sharedWorkspace()
        urls = ws.URLsForApplicationsToOpenURL_(url) or []
        out = []
        for u in urls:
            p = str(u.path())
            name = os.path.splitext(os.path.basename(p))[0]
            if name != "Fotohaus":
                out.append((name, p))
        seen, uniq = set(), []
        for n, p in out:
            if n not in seen:
                seen.add(n)
                uniq.append((n, p))
        return sorted(uniq)[:20]
    except Exception:
        return []


def open_with(paths: list[str], app_path: str | None) -> None:
    if IS_MAC:
        cmd = ["open"] + (["-a", app_path] if app_path else []) + list(paths)
        subprocess.Popen(cmd)
    elif app_path:
        subprocess.Popen([app_path] + list(paths))
    else:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        for p in paths:
            QDesktopServices.openUrl(QUrl.fromLocalFile(p))


# Screensaver folders are a macOS feature (Photos screen saver pointed at a folder).
HAS_SCREENSAVER_FOLDER = IS_MAC


def bundle_path() -> str | None:
    exe = sys.executable
    marker = ".app/Contents/MacOS/"
    if marker in exe:
        return exe.split(marker)[0] + ".app"
    return None


def default_app_for_type(uti: str) -> str | None:
    if not IS_MAC:
        return None
    try:
        AppKit, Foundation = _ns()
        import UniformTypeIdentifiers as UT
        t = UT.UTType.typeWithIdentifier_(uti)
        if t is None:
            return None
        u = AppKit.NSWorkspace.sharedWorkspace().URLForApplicationToOpenContentType_(t)
        return str(u.path()) if u is not None else None
    except Exception:
        return None


def set_default_viewer(utis: list[str]) -> list[str]:
    """Make Fotohaus the default app for these content types. Returns types that failed."""
    failed = []
    bp = bundle_path()
    if not IS_MAC or not bp:
        return list(utis)
    try:
        AppKit, Foundation = _ns()
        import UniformTypeIdentifiers as UT
        ws = AppKit.NSWorkspace.sharedWorkspace()
        app_url = Foundation.NSURL.fileURLWithPath_(bp)
        for uti in utis:
            t = UT.UTType.typeWithIdentifier_(uti)
            if t is None:
                failed.append(uti)
                continue
            ws.setDefaultApplicationAtURL_toOpenContentType_completionHandler_(app_url, t, None)
    except Exception:
        return list(utis)
    return failed


# MIME types for Linux, matching VIEWER_TYPES.
LINUX_VIEWER_MIME = ["image/jpeg", "image/png", "image/heic", "image/heif", "image/tiff", "image/gif",
                     "image/webp", "image/bmp", "image/avif", "image/vnd.adobe.photoshop", "image/x-dcraw",
                     "image/x-adobe-dng", "image/x-canon-cr2", "image/x-canon-cr3", "image/x-nikon-nef",
                     "image/x-sony-arw", "image/x-fuji-raf", "image/x-panasonic-rw2", "image/x-olympus-orf",
                     "video/mp4", "video/quicktime", "video/x-msvideo"]


def linux_launch_command() -> list[str]:
    """How the desktop should start this Fotohaus: the AppImage itself, or Python from the source tree."""
    appimage = os.environ.get("APPIMAGE")
    if appimage:
        return [appimage]
    src = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return ["env", f"PYTHONPATH={src}", sys.executable, "-m", "fotohaus"]


def install_linux_desktop_entry(make_default: bool = True) -> bool:
    """Add Fotohaus to the applications menu and "Open With" (a .desktop file and icon in the user's
    ~/.local/share), and with make_default, make it the default app for photos and videos."""
    import shlex
    import shutil
    from pathlib import Path
    share = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    apps = share / "applications"
    icons = share / "icons" / "hicolor" / "256x256" / "apps"
    apps.mkdir(parents=True, exist_ok=True)
    icons.mkdir(parents=True, exist_ok=True)
    src_icon = Path(__file__).resolve().parent / "resources" / "icon.png"
    try:
        from PIL import Image
        with Image.open(src_icon) as im:
            im.resize((256, 256), Image.LANCZOS).save(icons / "fotohaus.png")
    except Exception:
        pass
    exe = " ".join(shlex.quote(a) for a in linux_launch_command())
    (apps / "fotohaus.desktop").write_text(
        "[Desktop Entry]\nType=Application\nName=Fotohaus\nGenericName=Photo Organizer\n"
        "Comment=Organize, find and edit your photos, privately\n"
        f"Exec={exe} %F\nIcon=fotohaus\nTerminal=false\nStartupWMClass=Fotohaus\n"
        "Categories=Graphics;Photography;Viewer;\n"
        f"MimeType={';'.join(LINUX_VIEWER_MIME)};inode/directory;\n", encoding="utf-8")
    if shutil.which("update-desktop-database"):
        subprocess.call(["update-desktop-database", str(apps)], stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL)
    if not make_default:
        return True
    if not shutil.which("xdg-mime"):
        return False
    return subprocess.call(["xdg-mime", "default", "fotohaus.desktop"] + LINUX_VIEWER_MIME) == 0


def open_windows_default_apps() -> None:
    """Windows 10/11 let only the user pick default apps, so open that page of Settings."""
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QDesktopServices
    QDesktopServices.openUrl(QUrl("ms-settings:defaultapps"))


# ------------------------------------------------------------------ screensaver folder

def screensaver_folder() -> str:
    from pathlib import Path
    p = Path.home() / "Pictures" / "Fotohaus Screensaver"
    p.mkdir(parents=True, exist_ok=True)
    return str(p)


def open_screensaver_settings() -> None:
    if IS_MAC:
        subprocess.Popen(["open", "x-apple.systempreferences:com.apple.ScreenSaver-Settings.extension"])


# ---------------------------------------------------------------------------- Touch ID / password
def authenticate(reason: str = "unlock") -> bool:
    """Ask the user to prove it's them before the Private collection opens. Returns True on success.

    macOS: Touch ID or the Mac password. Windows: Windows Hello (face, fingerprint or PIN).
    Elsewhere, or when neither is set up: a Fotohaus password, chosen the first time.
    """
    if sys.platform == "darwin":
        ok = _touch_id(reason)
    elif sys.platform == "win32":
        ok = _windows_hello(reason)
    else:
        ok = None
    return _password_prompt(reason) if ok is None else ok


def _wait_responsive(done) -> None:
    """Keep the app responsive while a system prompt is up."""
    from PySide6.QtCore import QCoreApplication, QEventLoop
    while not done.wait(0.05):
        QCoreApplication.processEvents(QEventLoop.AllEvents, 50)


def _touch_id(reason: str) -> bool | None:
    """True/False from Touch ID or the Mac password; None if it can't be used here."""
    try:
        import threading
        from LocalAuthentication import LAContext  # pyobjc-framework-LocalAuthentication
    except Exception:
        return None
    ctx = LAContext.alloc().init()
    policy = 2                                  # LAPolicyDeviceOwnerAuthentication: Touch ID or password
    ok, _err = ctx.canEvaluatePolicy_error_(policy, None)
    if not ok:
        return None
    done = threading.Event()
    result = {"ok": False}

    def reply(success, error):
        result["ok"] = bool(success)
        done.set()

    ctx.evaluatePolicy_localizedReason_reply_(policy, f"Fotohaus wants to {reason}", reply)
    _wait_responsive(done)
    return result["ok"]


def _windows_hello(reason: str) -> bool | None:
    """True/False from Windows Hello; None if it isn't available (not set up, or no winrt)."""
    try:
        import asyncio
        import threading
        from winrt.windows.security.credentials.ui import (  # winrt-Windows.Security.Credentials.UI
            UserConsentVerificationResult, UserConsentVerifier, UserConsentVerifierAvailability)
    except Exception:
        return None
    done = threading.Event()
    result: dict = {"ok": None}

    async def ask():
        avail = await UserConsentVerifier.check_availability_async()
        if avail != UserConsentVerifierAvailability.AVAILABLE:
            return None
        res = await UserConsentVerifier.request_verification_async(f"Fotohaus wants to {reason}")
        return res == UserConsentVerificationResult.VERIFIED

    def run():
        try:
            result["ok"] = asyncio.run(ask())
        except Exception:
            result["ok"] = None
        finally:
            done.set()

    threading.Thread(target=run, daemon=True).start()
    _wait_responsive(done)
    return result["ok"]


# A Fotohaus password for the Private collection, used where the system offers no check of its own.
# Only a salted PBKDF2 hash is stored (in settings.json). Like Touch ID, it locks the collection in
# Fotohaus; it does not encrypt the files on disk.
_PW_KEY = "private_password"
_PW_ITERATIONS = 200_000
_PW_MIN = 4


def private_password_set(settings) -> bool:
    rec = settings.get(_PW_KEY)
    return isinstance(rec, dict) and bool(rec.get("hash"))


def set_private_password(settings, password: str) -> None:
    import hashlib
    salt = os.urandom(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PW_ITERATIONS)
    settings.set(_PW_KEY, {"salt": salt.hex(), "hash": h.hex(), "iterations": _PW_ITERATIONS})


def check_private_password(settings, password: str) -> bool:
    import hashlib
    import hmac
    rec = settings.get(_PW_KEY)
    if not isinstance(rec, dict) or not rec.get("hash"):
        return False
    try:
        salt = bytes.fromhex(rec["salt"])
        h = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt,
                                int(rec.get("iterations") or _PW_ITERATIONS))
        return hmac.compare_digest(h.hex(), rec["hash"])
    except (KeyError, ValueError, TypeError):
        return False


def _password_prompt(reason: str) -> bool:
    from PySide6.QtWidgets import QInputDialog, QLineEdit, QMessageBox
    from .paths import settings as _settings
    st = _settings()
    if not private_password_set(st):
        QMessageBox.information(None, "Private",
                                "Choose a password for your Private photos. Fotohaus will ask for it "
                                "each time you open the Private collection.")
        while True:
            pw, ok = QInputDialog.getText(None, "Private", "New password:", QLineEdit.Password)
            if not ok:
                return False
            if len(pw) < _PW_MIN:
                QMessageBox.warning(None, "Private", f"Please use at least {_PW_MIN} characters.")
                continue
            again, ok = QInputDialog.getText(None, "Private", "Type it again:", QLineEdit.Password)
            if not ok:
                return False
            if again != pw:
                QMessageBox.warning(None, "Private", "The two passwords don't match. Please try again.")
                continue
            set_private_password(st, pw)
            return True
    for attempt in range(3):
        label = f"Password to {reason}:" if attempt == 0 else "Wrong password. Try again:"
        pw, ok = QInputDialog.getText(None, "Private", label, QLineEdit.Password)
        if not ok:
            return False
        if check_private_password(st, pw):
            return True
    return False
