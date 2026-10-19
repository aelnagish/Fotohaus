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
    data = Path(os.environ.get("XDG_DATA_HOME", home / ".local" / "share")) / APP_NAME.lower()
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
    for name in ("library.db", "library.db-wal", "library.db-shm", "settings.json"):
        src = DATA_DIR / name
        if src.exists():
            shutil.copy2(src, dst / name)
    if (DATA_DIR / "models").exists() and not (dst / "models").exists():
        shutil.copytree(DATA_DIR / "models", dst / "models")
    _DEFAULT_DATA_DIR.mkdir(parents=True, exist_ok=True)
    _POINTER.write_text(str(dst), encoding="utf-8")


def ensure_dirs() -> None:
    for d in (DATA_DIR, CACHE_DIR, USER_MODELS_DIR, THUMB_DIR, FACE_DIR, TILE_DIR, PREVIEW_DIR):
        d.mkdir(parents=True, exist_ok=True)


DEFAULT_SETTINGS = {
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
                self._data.update(json.load(fh))
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
