"""Platform integration (macOS via PyObjC, Windows Hello via WinRT) with fallbacks elsewhere."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile

IS_MAC = sys.platform == "darwin"

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
    from PySide6.QtWidgets import QMenu, QMessageBox
    services = sharing_services(paths)
    m = QMenu(parent)
    for title, svc in services:
        m.addAction(title, lambda s=svc: perform_share(s, paths))
    if services:
        m.addSeparator()
    m.addAction("Email…", lambda: email_files(paths))
    m.addAction("Show in Finder", lambda: reveal(paths[0]))
    if not services and not IS_MAC:
        QMessageBox.information(parent, "Share", "Sharing services are available on macOS.")
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
    try:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        return QDesktopServices.openUrl(QUrl(f"mailto:?subject={subject}"))
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
    else:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        for p in paths:
            QDesktopServices.openUrl(QUrl.fromLocalFile(p))


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
