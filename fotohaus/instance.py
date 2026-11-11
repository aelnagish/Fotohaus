"""One running Fotohaus per library.

macOS hands files opened from Finder to the app that is already running. Windows and Linux start a
new process for every double-click instead, so Fotohaus does it itself: the first copy listens on a
local socket, and a later launch sends it the photos and folders it was given, then quits. Without
this, two copies would share one library database (locked-database errors, duplicate face indexing,
settings overwriting each other).
"""
from __future__ import annotations

import hashlib
import json
import os
import sys

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

TIMEOUT_MS = 1500


def server_name(data_dir) -> str:
    """Per user and per library: two different libraries (FOTOHAUS_HOME) may run side by side."""
    try:
        user = os.getlogin()
    except OSError:
        user = os.environ.get("USER") or os.environ.get("USERNAME") or ""
    key = f"{user}|{os.path.normcase(os.path.abspath(str(data_dir)))}"
    return "fotohaus-" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def send_to_running(name: str, paths: list[str]) -> bool:
    """Hand paths to a Fotohaus that is already running. True if one took them (this launch should
    then quit); False if none is running."""
    sock = QLocalSocket()
    sock.connectToServer(name)
    if not sock.waitForConnected(TIMEOUT_MS):
        return False
    if sys.platform == "win32":
        # Windows only lets the foreground process bring a window to the front. This launch was
        # just started by Explorer, so it is in the foreground: pass that right on to the running copy.
        try:
            import ctypes
            ctypes.windll.user32.AllowSetForegroundWindow(-1)   # ASFW_ANY
        except Exception:
            pass
    sock.write(json.dumps({"paths": [os.path.abspath(p) for p in paths]}).encode("utf-8") + b"\n")
    ok = sock.waitForBytesWritten(TIMEOUT_MS)
    # wait for the reply so the message is not lost if this process exits too quickly
    ok = ok and sock.waitForReadyRead(TIMEOUT_MS)
    sock.disconnectFromServer()
    return bool(ok)


class InstanceServer(QObject):
    """Listens for later launches. Emits `received` with their paths (empty list: just come to the
    front)."""
    received = Signal(list)

    def __init__(self, name: str, parent=None):
        super().__init__(parent)
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.UserAccessOption)
        if not self.server.listen(name):
            probe = QLocalSocket()
            probe.connectToServer(name)
            if not probe.waitForConnected(300):
                # a socket file left behind by a crashed copy (Linux/macOS): nobody answers on it
                QLocalServer.removeServer(name)
                self.server.listen(name)
            else:
                probe.disconnectFromServer()
        self.server.newConnection.connect(self._accept)

    @property
    def listening(self) -> bool:
        return self.server.isListening()

    def _accept(self):
        while self.server.hasPendingConnections():
            sock = self.server.nextPendingConnection()
            buf = bytearray()

            def ready(sock=sock, buf=buf):
                buf.extend(bytes(sock.readAll()))
                if not buf.endswith(b"\n"):
                    return
                try:
                    paths = json.loads(buf.decode("utf-8")).get("paths") or []
                except ValueError:
                    paths = []
                sock.write(b"ok\n")
                sock.flush()
                sock.disconnectFromServer()
                self.received.emit([str(p) for p in paths if isinstance(p, str)])
            sock.readyRead.connect(ready)
            sock.disconnected.connect(sock.deleteLater)
