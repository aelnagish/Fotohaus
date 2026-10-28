# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Abdullah Yusuf
#
# This program is free software: you can redistribute it and/or modify it under the terms of the
# GNU General Public License as published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version. This program is distributed in the hope that it
# will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
# FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License (LICENSE) for more details.
"""Fotohaus — a fast, private photo organizer for macOS, Windows and Linux.

A clean-room, Picasa-compatible photo manager: it reads and writes the
`.picasa.ini` sidecar files that Picasa used, so existing stars, captions,
albums, face tags and non-destructive edits carry over.
"""

__version__ = "1.0.1"
APP_NAME = "Fotohaus"
SUPPORT_URL = "https://buymeacoffee.com/aelnagish"


def hard_exit(code: int = 0) -> None:
    """End the process at once, without Python's and PySide's teardown (PySide deletes leftover Qt
    objects in an arbitrary order, which can crash after every window has closed). Everything is
    saved by then: the database commits each change.

    On Windows os._exit still runs every library's unload code, and one of them (in the video
    stack) can wait forever for a thread that is already gone, so the process never ends there;
    TerminateProcess skips that step."""
    import os
    import sys
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream:
                stream.flush()
        except Exception:
            pass
    if sys.platform == "win32":
        try:
            import ctypes
            k32 = ctypes.windll.kernel32
            k32.GetCurrentProcess.restype = ctypes.c_void_p          # a HANDLE, not a C int
            k32.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint]
            k32.TerminateProcess(k32.GetCurrentProcess(), int(code) & 0xFFFFFFFF)
        except Exception:
            pass
    os._exit(int(code))
