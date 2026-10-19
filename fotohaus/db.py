"""SQLite library database (one connection per thread)."""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS roots(
    id INTEGER PRIMARY KEY, path TEXT UNIQUE NOT NULL,
    readonly INTEGER DEFAULT 0, kind TEXT DEFAULT 'folder', added REAL);
CREATE TABLE IF NOT EXISTS folders(
    id INTEGER PRIMARY KEY, path TEXT UNIQUE NOT NULL, name TEXT, root_id INTEGER,
    ini_mtime REAL DEFAULT 0, date REAL, description TEXT DEFAULT '', place TEXT DEFAULT '',
    count INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS photos(
    id INTEGER PRIMARY KEY, folder_id INTEGER, path TEXT UNIQUE NOT NULL, filename TEXT,
    kind TEXT, size INTEGER, mtime REAL, width INTEGER, height INTEGER, taken REAL,
    camera TEXT DEFAULT '', lens TEXT DEFAULT '', exposure TEXT DEFAULT '',
    lat REAL, lon REAL, star INTEGER DEFAULT 0, caption TEXT DEFAULT '', keywords TEXT DEFAULT '',
    filters TEXT DEFAULT '', rotate INTEGER DEFAULT 0, hidden INTEGER DEFAULT 0, duration REAL,
    phash TEXT, ocr TEXT DEFAULT '', labels_done INTEGER DEFAULT 0, faces_done INTEGER DEFAULT 0,
    clip_done INTEGER DEFAULT 0, added REAL);
CREATE INDEX IF NOT EXISTS ix_photos_folder ON photos(folder_id);
CREATE INDEX IF NOT EXISTS ix_photos_taken ON photos(taken);
CREATE TABLE IF NOT EXISTS albums(
    id INTEGER PRIMARY KEY, token TEXT UNIQUE, name TEXT, description TEXT DEFAULT '',
    created REAL, cover_photo INTEGER);
CREATE TABLE IF NOT EXISTS album_photos(
    album_id INTEGER, photo_id INTEGER, position INTEGER DEFAULT 0,
    PRIMARY KEY(album_id, photo_id));
CREATE INDEX IF NOT EXISTS ix_album_photos_photo ON album_photos(photo_id);
CREATE TABLE IF NOT EXISTS people(
    id INTEGER PRIMARY KEY, name TEXT, contact_id TEXT UNIQUE, hidden INTEGER DEFAULT 0,
    cover_face INTEGER);
CREATE TABLE IF NOT EXISTS faces(
    id INTEGER PRIMARY KEY, photo_id INTEGER, x REAL, y REAL, w REAL, h REAL,
    person_id INTEGER, source TEXT, confirmed INTEGER DEFAULT 0, score REAL,
    embedding BLOB, cluster INTEGER, ignored INTEGER DEFAULT 0, suggestion REAL);
CREATE INDEX IF NOT EXISTS ix_faces_photo ON faces(photo_id);
CREATE INDEX IF NOT EXISTS ix_faces_person ON faces(person_id);
CREATE TABLE IF NOT EXISTS labels(
    photo_id INTEGER, label TEXT, conf REAL, source TEXT, PRIMARY KEY(photo_id, label));
CREATE INDEX IF NOT EXISTS ix_labels_label ON labels(label);
CREATE TABLE IF NOT EXISTS clip(photo_id INTEGER PRIMARY KEY, vec BLOB);
"""


MIGRATIONS = [
    "ALTER TABLE roots ADD COLUMN last_scan REAL",
    "ALTER TABLE folders ADD COLUMN hidden INTEGER DEFAULT 0",
    "ALTER TABLE albums ADD COLUMN place TEXT DEFAULT ''",
    "ALTER TABLE albums ADD COLUMN date REAL",
    "ALTER TABLE people ADD COLUMN email TEXT DEFAULT ''",
    "ALTER TABLE photos ADD COLUMN archived INTEGER DEFAULT 0",
    "ALTER TABLE photos ADD COLUMN private INTEGER DEFAULT 0",
    "ALTER TABLE photos ADD COLUMN deleted REAL",
    "ALTER TABLE photos ADD COLUMN stack_key TEXT",
    "ALTER TABLE photos ADD COLUMN stack_member INTEGER DEFAULT 0",
    "ALTER TABLE photos ADD COLUMN stack_count INTEGER DEFAULT 0",
    "ALTER TABLE photos ADD COLUMN stack_kind TEXT DEFAULT ''",
    "ALTER TABLE photos ADD COLUMN live_video TEXT DEFAULT ''",
    "ALTER TABLE photos ADD COLUMN place_name TEXT",
]


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self.write_lock = threading.RLock()
        c = self.conn
        c.executescript(SCHEMA)
        c.execute("INSERT OR IGNORE INTO meta(key, value) VALUES('schema', ?)", (str(SCHEMA_VERSION),))
        for sql in MIGRATIONS:
            try:
                c.execute(sql)
            except sqlite3.OperationalError:
                pass  # already applied
        c.commit()

    @property
    def conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(str(self.path), timeout=30, check_same_thread=False)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=NORMAL")
            c.execute("PRAGMA foreign_keys=OFF")
            self._local.conn = c
        return c

    def q(self, sql: str, args=()) -> list[sqlite3.Row]:
        return self.conn.execute(sql, args).fetchall()

    def one(self, sql: str, args=()):
        return self.conn.execute(sql, args).fetchone()

    def scalar(self, sql: str, args=(), default=None):
        r = self.conn.execute(sql, args).fetchone()
        return r[0] if r else default

    def x(self, sql: str, args=()):
        with self.write_lock:
            cur = self.conn.execute(sql, args)
            self.conn.commit()
            return cur

    def xmany(self, sql: str, seq):
        with self.write_lock:
            self.conn.executemany(sql, seq)
            self.conn.commit()
