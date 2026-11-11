"""Locations for the library database, caches, models and settings."""
from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path

from . import APP_NAME

_HOME_OVERRIDE = os.environ.get("FOTOHAUS_HOME")


def _base_dirs() -> tuple[Path, Path]:
    if _HOME_OVERRIDE:
        base = Path(_HOME_OVERRIDE)
        return base / "data", base / "cache"
    home = Path.home()
    if sys.platform == "darwin":
        return (home / "Library" / "Application Support" / APP_NAME,
                home / "Library" / "Caches" / APP_NAME)
    if sys.platform == "win32":
        # Local (not Roaming) AppData, like Picasa: the database and thumbnails grow large and the
        # photo paths they hold only make sense on this PC.
        local = Path(os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local")
        return local / APP_NAME, local / APP_NAME / "Cache"
    data =Path(os.environ.get("XDG_DATA_HOME", home / ".local" / "share")) / APP_NAME.lower()
    cache = Path(os.environ.get("XDG_CACHE_HOME", home / ".cache")) / APP_NAME.lower()
    return data, cache


DATA_DIR, CACHE_DIR = _base_dirs()
_DEFAULT_DATA_DIR = DATA_DIR
_POINTER = DATA_DIR / "location.txt"   # Tools > Options > Move Database writes the new location here
try:
    _moved = _POINTER.read_text(encoding="utf-8").strip() if not _HOME_OVERRIDE else ""
    if _moved and Path(_moved).is_dir():
        DATA_DIR = Path(_moved)
except OSError:
    pass
DB_PATH = DATA_DIR / "library.db"
SETTINGS_PATH = DATA_DIR / "settings.json"
USER_MODELS_DIR = DATA_DIR / "models"
THUMB_DIR = CACHE_DIR / "thumbs"
FACE_DIR = CACHE_DIR / "faces"
TILE_DIR = CACHE_DIR / "tiles"
PREVIEW_DIR = CACHE_DIR / "previews"

# Models shipped inside the app bundle / source tree.
BUNDLED_MODELS_DIR = Path(__file__).resolve().parent / "models"


def set_data_dir(new_dir: str) -> None:
    """Copy the library database and settings to new_dir and use it from the next launch."""
    import shutil
    dst = Path(new_dir) / "Fotohaus Library"
    dst.mkdir(parents=True, exist_ok=True)
    import sqlite3
    # copy the live database with SQLite's backup API (copying the file while it is open can
    # produce a broken copy); settings are a plain file
    if (DATA_DIR / "library.db").exists():
        src_db = sqlite3.connect(str(DATA_DIR / "library.db"), timeout=30)
        dst_db = sqlite3.connect(str(dst / "library.db"))
        try:
            src_db.backup(dst_db)
        finally:
            dst_db.close()
            src_db.close()
    if (DATA_DIR / "settings.json").exists():
        shutil.copy2(DATA_DIR / "settings.json", dst / "settings.json")
    if (DATA_DIR / "models").exists() and not (dst / "models").exists():
        shutil.copytree(DATA_DIR / "models", dst / "models")
    _DEFAULT_DATA_DIR.mkdir(parents=True, exist_ok=True)
    _POINTER.write_text(str(dst), encoding="utf-8")


def ensure_dirs() -> None:
    for d in (DATA_DIR, CACHE_DIR, USER_MODELS_DIR, THUMB_DIR, FACE_DIR, TILE_DIR, PREVIEW_DIR):
        d.mkdir(parents=True, exist_ok=True)


DEFAULT_SETTINGS = {
    "theme": "system",               # system | light | dark
    "accent": "system",              # system | blue | purple | pink | red | orange | yellow | green | graphite
    "library_bg": "neutral",         # neutral | gallery
    "write_picasa_ini": True,        # keep .picasa.ini files in sync (Picasa compatibility)
    "faces_enabled": True,           # on-device face detection + recognition
    "vision_enabled": True,          # Apple Vision labels + text recognition (macOS only)
    "clip_enabled": False,           # optional semantic search model (downloaded on demand)
    "map_tiles": True,               # load OpenStreetMap tiles for the Places view
    "thumb_size": 160,
    "slideshow_seconds": 4,
    "sort": "date",                  # date | name
    "first_run_done": False,
    "export_dir": "",
    "open_files_in": "viewer",       # viewer | editor  (double-clicked files from Finder)
    "viewer_reuse": True,            # a file opened while a viewer is up replaces its photo (else: new window)
    "write_meta_in_files": True,     # store captions & tags inside JPEGs (IPTC/XMP), like Picasa
    "write_faces_in_files": False,   # store name tags inside JPEGs as XMP face regions
    "thumb_caption": "none",         # none | filename | caption | resolution | date | tags
    "folder_view": "flat",           # flat | tree
    "folder_sort": "date",           # date | name | size | recent
    "folder_sort_reverse": False,
    "show_hidden": False,
    "quick_tags": ["Family", "Friends", "Holiday", "Work", "Favorites", "Nature", "Food", "Pets"],
    "button_bar": ["share", "email", "print", "export", "collage", "movie", "geotag"],
    "slideshow_transition": "dissolve",
    "slideshow_captions": True,
    "email_size": 800,
    "file_types": [],                # extensions to skip when scanning (empty = scan all supported)
    "skip_folder_names": ["Applications", "Originals", "temp", "iPod Photo Cache",
                          "node_modules", "Library", "__pycache__"],
}


class Settings:
    """Tiny JSON-backed settings store (thread-safe)."""

    def __init__(self, path: Path = SETTINGS_PATH):
        self.path = path
        self._lock = threading.Lock()
        self._data = dict(DEFAULT_SETTINGS)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                saved = json.load(fh)
            if isinstance(saved, dict):
                self._data.update(saved)
        except (OSError, ValueError):
            pass

    def get(self, key, default=None):
        with self._lock:
            return self._data.get(key, DEFAULT_SETTINGS.get(key, default))

    def set(self, key, value) -> None:
        with self._lock:
            self._data[key] = value
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh, indent=2)
            os.replace(tmp, self.path)


_settings: Settings | None = None


def settings() -> Settings:
    global _settings
    if _settings is None:
        ensure_dirs()
        _settings = Settings()
    return _settings
