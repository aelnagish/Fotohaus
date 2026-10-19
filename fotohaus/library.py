"""High-level photo library API shared by the GUI, the CLI and the MCP server."""
from __future__ import annotations

import datetime as _dt
import os
import secrets
import sys
import shutil
import threading
import time
from pathlib import Path
from typing import Callable, Iterable

from . import media
from .db import Database
from .edits import render
from .paths import DB_PATH, ensure_dirs, settings
from .picasa.contacts import find_picasa_contacts_files, load_contacts_xml
from .picasa.ini import Filter, PicasaIni, UNKNOWN_CONTACT, parse_filters, serialize_filters

BUNDLE_SUFFIXES = (".app", ".photoslibrary", ".aplibrary", ".migratedphotolibrary", ".photolibrary",
                   ".framework", ".bundle", ".pkg", ".mpkg", ".lrdata", ".xcodeproj", ".git",
                   ".fcpbundle", ".imovielibrary", ".tvlibrary")
ORIGINALS_DIR = ".picasaoriginals"


def _iou(a, b) -> float:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix = max(0.0, min(ax1, bx1) - max(ax0, bx0))
    iy = max(0.0, min(ay1, by1) - max(ay0, by0))
    inter = ix * iy
    ua = (ax1 - ax0) * (ay1 - ay0) + (bx1 - bx0) * (by1 - by0) - inter
    return inter / ua if ua > 0 else 0.0


class Library:
    def __init__(self, db_path: Path | None = None):
        ensure_dirs()
        self.db = Database(db_path or DB_PATH)
        self.settings = settings()
        self._ini_locks: dict[str, threading.Lock] = {}
        self._ini_locks_guard = threading.Lock()
        self._global_contacts: dict[str, str] | None = None
        self.listeners: list[Callable[[str, object], None]] = []

    # ------------------------------------------------------------ events
    def emit(self, what: str, payload=None) -> None:
        for fn in list(self.listeners):
            try:
                fn(what, payload)
            except Exception:
                pass

    # ------------------------------------------------------------ roots
    def roots(self):
        return self.db.q("SELECT * FROM roots ORDER BY path")

    def add_root(self, path: str, readonly: bool = False, kind: str = "folder") -> int:
        path = os.path.abspath(os.path.expanduser(path))
        self.db.x("INSERT INTO roots(path, readonly, kind, added) VALUES(?,?,?,?) "
                  "ON CONFLICT(path) DO UPDATE SET kind=excluded.kind, readonly=excluded.readonly",
                  (path, int(readonly), kind, time.time()))
        rid = self.db.scalar("SELECT id FROM roots WHERE path=?", (path,))
        self.emit("roots")
        return rid

    def remove_root(self, root_id: int) -> None:
        fids = [r[0] for r in self.db.q("SELECT id FROM folders WHERE root_id=?", (root_id,))]
        for fid in fids:
            self._delete_folder(fid)
        self.db.x("DELETE FROM roots WHERE id=?", (root_id,))
        self.emit("roots")
        self.emit("library")

    def set_folder_mode(self, path: str, mode: str | None) -> None:
        """Folder Manager: mode = 'folder' (scan always) | 'once' | 'exclude' | None (inherit)."""
        path = os.path.abspath(path)
        row = self.db.one("SELECT id FROM roots WHERE path=?", (path,))
        if mode is None:
            if row:
                self.remove_root(row["id"]) if self.db.one("SELECT kind FROM roots WHERE id=?", (row["id"],))["kind"] != "exclude" \
                    else self.db.x("DELETE FROM roots WHERE id=?", (row["id"],))
            return
        if mode == "exclude":
            self.add_root(path, kind="exclude")
            o = path.rstrip(os.sep)
            for f in self.db.q("SELECT id FROM folders WHERE path=? OR path LIKE ?", (o, o + os.sep + "%")):
                self._delete_folder(f["id"])
            self.emit("library")
            return
        self.add_root(path, kind=mode)
        self.db.x("UPDATE roots SET last_scan=NULL WHERE path=?", (path,))

    def folder_mode(self, path: str) -> tuple[str | None, bool]:
        """(mode, inherited) for a directory in the Folder Manager."""
        path = os.path.abspath(path).rstrip(os.sep)
        best = None
        for r in self.roots():
            rp = r["path"].rstrip(os.sep)
            if path == rp or path.startswith(rp + os.sep):
                if best is None or len(rp) > len(best["path"].rstrip(os.sep)):
                    best = r
        if best is None:
            return None, False
        return best["kind"], best["path"].rstrip(os.sep) != path

    def set_root_readonly(self, root_id: int, ro: bool) -> None:
        self.db.x("UPDATE roots SET readonly=? WHERE id=?", (int(ro), root_id))

    # ------------------------------------------------------------ contacts
    def global_contacts(self) -> dict[str, str]:
        if self._global_contacts is None:
            g: dict[str, str] = {}
            for p in find_picasa_contacts_files():
                g.update(load_contacts_xml(p))
            self._global_contacts = g
        return self._global_contacts

    def import_contacts_xml(self, path: str) -> int:
        c = load_contacts_xml(path)
        self.global_contacts().update(c)
        n = 0
        for cid, name in c.items():
            row = self.db.one("SELECT id, name FROM people WHERE contact_id=?", (cid,))
            if row and not row["name"]:
                self.db.x("UPDATE people SET name=? WHERE id=?", (name, row["id"]))
                n += 1
        self.emit("people")
        return n

    def _person_for_contact(self, cid: str, name_hint: str | None) -> int | None:
        if not cid or cid == UNKNOWN_CONTACT:
            return None
        row = self.db.one("SELECT id, name FROM people WHERE contact_id=?", (cid,))
        name = name_hint or self.global_contacts().get(cid)
        if row:
            if name and not row["name"]:
                self.db.x("UPDATE people SET name=? WHERE id=?", (name, row["id"]))
            return row["id"]
        if name:
            # merge with an existing person of the same name created in Fotohaus
            same = self.db.one("SELECT id FROM people WHERE name=? AND (contact_id IS NULL OR contact_id='')", (name,))
            if same:
                self.db.x("UPDATE people SET contact_id=? WHERE id=?", (cid, same["id"]))
                return same["id"]
        cur = self.db.x("INSERT INTO people(name, contact_id) VALUES(?,?)", (name or "", cid))
        return cur.lastrowid

    # ------------------------------------------------------------ scanning
    def scan(self, progress: Callable[[str, int], None] | None = None,
             should_stop: Callable[[], bool] | None = None, root_ids: Iterable[int] | None = None,
             include_once: bool = False) -> dict:
        """Find new, changed and removed files. "Scan once" folders are only rescanned when
        `include_once` is set (an explicit Rescan Now) or when they have never been scanned."""
        stats = {"folders": 0, "new": 0, "updated": 0, "removed": 0}
        skip = set(self.settings.get("skip_folder_names") or [])
        all_roots = self.roots()
        excluded = [r["path"].rstrip(os.sep) for r in all_roots if r["kind"] == "exclude"]
        skip_ext = {e.lower() for e in (self.settings.get("file_types") or [])}
        if root_ids is not None:
            ids = set(root_ids)
            roots = [r for r in all_roots if r["id"] in ids and r["kind"] != "exclude"]
        else:
            roots = [r for r in all_roots if r["kind"] != "exclude"
                     and (include_once or not (r["kind"] == "once" and r["last_scan"]))]

        def is_excluded(p):
            return any(p == e or p.startswith(e + os.sep) for e in excluded)
        for root in roots:
            seen: set[str] = set()
            rpath = root["path"]
            if not os.path.isdir(rpath):
                continue
            for dirpath, dirnames, filenames in os.walk(rpath):
                if should_stop and should_stop():
                    return stats
                dirnames[:] = sorted(d for d in dirnames
                                     if not d.startswith(".") and d not in skip
                                     and not d.lower().endswith(BUNDLE_SUFFIXES)
                                     and not is_excluded(os.path.join(dirpath, d)))
                if is_excluded(dirpath):
                    continue
                files = sorted(f for f in filenames if not f.startswith(".") and media.kind_of(f)
                               and os.path.splitext(f)[1].lower() not in skip_ext)
                seen.add(dirpath)
                if files or self.db.scalar("SELECT 1 FROM folders WHERE path=?", (dirpath,)):
                    if progress:
                        progress(dirpath, stats["new"])
                    self._scan_folder(dirpath, files, root, stats)
                    stats["folders"] += 1
            for f in self.db.q("SELECT id, path FROM folders WHERE root_id=?", (root["id"],)):
                if f["path"] not in seen:
                    self._delete_folder(f["id"])
                    stats["removed"] += 1
            self.db.x("UPDATE roots SET last_scan=? WHERE id=?", (time.time(), root["id"]))
        try:
            self.rebuild_stacks()
            self.update_place_names()
        except Exception:
            pass
        self.emit("library")
        return stats

    def _delete_folder(self, fid: int) -> None:
        pids = [r[0] for r in self.db.q("SELECT id FROM photos WHERE folder_id=?", (fid,))]
        self._delete_photo_rows(pids)
        self.db.x("DELETE FROM folders WHERE id=?", (fid,))

    def _delete_photo_rows(self, pids: list[int]) -> None:
        if not pids:
            return
        with self.db.write_lock:
            c = self.db.conn
            for i in range(0, len(pids), 500):
                chunk = pids[i:i + 500]
                qs = ",".join("?" * len(chunk))
                for t in ("faces", "album_photos", "labels", "clip"):
                    c.execute(f"DELETE FROM {t} WHERE photo_id IN ({qs})", chunk)
                c.execute(f"DELETE FROM photos WHERE id IN ({qs})", chunk)
            c.commit()

    def _scan_folder(self, dirpath: str, files: list[str], root, stats: dict) -> None:
        db = self.db
        frow = db.one("SELECT * FROM folders WHERE path=?", (dirpath,))
        if frow is None:
            cur = db.x("INSERT INTO folders(path, name, root_id) VALUES(?,?,?)",
                       (dirpath, os.path.basename(dirpath) or dirpath, root["id"]))
            fid = cur.lastrowid
            old_ini_mtime = -1.0
        else:
            fid = frow["id"]
            old_ini_mtime = frow["ini_mtime"] or 0.0
        existing = {r["filename"]: r for r in db.q("SELECT * FROM photos WHERE folder_id=?", (fid,))}
        ini = PicasaIni(dirpath)
        ini_mtime = ini.mtime()
        changed_any = False
        now = time.time()
        rows_new, rows_upd = [], []
        for fn in files:
            p = os.path.join(dirpath, fn)
            try:
                st = os.stat(p)
            except OSError:
                continue
            row = existing.pop(fn, None)
            if row is not None and abs((row["mtime"] or 0) - st.st_mtime) < 1e-3 and row["size"] == st.st_size:
                continue
            md = media.read_metadata(p)
            kind = media.kind_of(fn)
            vals = (md["width"], md["height"], md["taken"], md["camera"], md["lens"], md["exposure"],
                    md["lat"], md["lon"], md["duration"], st.st_size, st.st_mtime)
            if row is None:
                rows_new.append((fid, p, fn, kind) + vals + (md["caption"] or "", ",".join(md["keywords"]), now))
            else:
                rows_upd.append(vals + (row["id"],))
                if md["caption"] and not row["caption"]:
                    db.x("UPDATE photos SET caption=? WHERE id=?", (md["caption"], row["id"]))
            changed_any = True
        with db.write_lock:
            c = db.conn
            if rows_new:
                c.executemany(
                    "INSERT OR IGNORE INTO photos(folder_id, path, filename, kind, width, height, taken, camera, lens,"
                    " exposure, lat, lon, duration, size, mtime, caption, keywords, added)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows_new)
                stats["new"] += len(rows_new)
            if rows_upd:
                c.executemany(
                    "UPDATE photos SET width=?, height=?, taken=?, camera=?, lens=?, exposure=?, lat=?, lon=?,"
                    " duration=?, size=?, mtime=?, phash=NULL, faces_done=0, labels_done=0, clip_done=0 WHERE id=?",
                    rows_upd)
                stats["updated"] += len(rows_upd)
            c.commit()
        if existing:
            self._delete_photo_rows([r["id"] for r in existing.values()])
            stats["removed"] += len(existing)
            changed_any = True
        if ini.exists and (abs(ini_mtime - old_ini_mtime) > 1e-3 or rows_new):
            self._import_ini(fid, ini)
        db.x("UPDATE folders SET ini_mtime=?, count=(SELECT COUNT(*) FROM photos WHERE folder_id=?),"
             " date=(SELECT MIN(taken) FROM photos WHERE folder_id=?) WHERE id=?",
             (ini_mtime, fid, fid, fid))
        if ini.exists:
            meta = ini.folder_meta()
            if meta.get("name"):
                db.x("UPDATE folders SET name=? WHERE id=?", (meta["name"], fid))
            if meta.get("description") or meta.get("location"):
                db.x("UPDATE folders SET description=?, place=? WHERE id=?",
                     (meta.get("description", ""), meta.get("location", ""), fid))
        if changed_any:
            self.emit("folder", fid)

    def _import_ini(self, fid: int, ini: PicasaIni) -> None:
        db = self.db
        contacts = ini.contacts()
        albums_meta = ini.albums()
        photos = {r["filename"].lower(): r for r in db.q("SELECT * FROM photos WHERE folder_id=?", (fid,))}
        for sec in ini.photo_sections():
            row = photos.get(sec.lower())
            if row is None:
                continue
            pid = row["id"]
            star = 1 if ini.star(sec) else 0
            caption = ini.caption(sec) or row["caption"] or ""
            kws = ini.keywords(sec)
            old_kws = [k for k in (row["keywords"] or "").split(",") if k]
            merged = list(dict.fromkeys(old_kws + kws))
            chain = ini.filters(sec)
            rot = ini.rotate(sec)
            hidden = 1 if (ini.get(sec, "hidden") or "").lower() == "yes" else 0
            db.x("UPDATE photos SET star=?, caption=?, keywords=?, filters=?, rotate=?, hidden=? WHERE id=?",
                 (star, caption, ",".join(merged), serialize_filters(chain), rot, hidden, pid))
            # albums
            tokens = ini.photo_albums(sec)
            for tok in tokens:
                aid = self._album_for_token(tok, albums_meta.get(tok, {}))
                db.x("INSERT OR IGNORE INTO album_photos(album_id, photo_id, position) VALUES(?,?,?)",
                     (aid, pid, int(time.time())))
            if tokens:
                qs = ",".join("?" * len(tokens))
                db.x(f"DELETE FROM album_photos WHERE photo_id=? AND album_id IN "
                     f"(SELECT id FROM albums WHERE token NOT IN ({qs}))", [pid] + tokens)
            # faces
            ini_faces = ini.faces(sec)
            existing = db.q("SELECT * FROM faces WHERE photo_id=?", (pid,))
            matched: set[int] = set()
            for rect, cid in ini_faces:
                person = self._person_for_contact(cid, contacts.get(cid))
                best, best_iou = None, 0.0
                for f in existing:
                    fr = (f["x"], f["y"], f["x"] + f["w"], f["y"] + f["h"])
                    v = _iou(rect, fr)
                    if v > best_iou:
                        best, best_iou = f, v
                if best is not None and best_iou > 0.45:
                    matched.add(best["id"])
                    if person is not None:
                        db.x("UPDATE faces SET person_id=?, confirmed=1, suggestion=NULL WHERE id=?",
                             (person, best["id"]))
                else:
                    l, t, r, b = rect
                    cur = db.x("INSERT INTO faces(photo_id, x, y, w, h, person_id, source, confirmed, score)"
                               " VALUES(?,?,?,?,?,?,?,?,?)",
                               (pid, l, t, r - l, b - t, person, "picasa", 1 if person else 0, 1.0))
                    matched.add(cur.lastrowid)
            stale = [f["id"] for f in existing if f["source"] == "picasa" and f["id"] not in matched]
            for s in stale:
                db.x("DELETE FROM faces WHERE id=?", (s,))
        self.emit("people")

    def _album_for_token(self, token: str, meta: dict) -> int:
        row = self.db.one("SELECT id, name FROM albums WHERE token=?", (token,))
        name = meta.get("name") or ""
        if row:
            if name and not row["name"]:
                self.db.x("UPDATE albums SET name=? WHERE id=?", (name, row["id"]))
            return row["id"]
        created = time.time()
        if meta.get("date"):
            try:
                created = _dt.datetime.fromisoformat(meta["date"].replace("Z", "+00:00")).timestamp()
            except ValueError:
                pass
        cur = self.db.x("INSERT INTO albums(token, name, description, created) VALUES(?,?,?,?)",
                        (token, name or "Untitled album", meta.get("description", ""), created))
        self.emit("albums")
        return cur.lastrowid

    # ------------------------------------------------------------ ini writing
    def _ini_lock(self, folder: str) -> threading.Lock:
        with self._ini_locks_guard:
            if folder not in self._ini_locks:
                self._ini_locks[folder] = threading.Lock()
            return self._ini_locks[folder]

    def _can_write_ini(self, folder_path: str) -> bool:
        if not self.settings.get("write_picasa_ini"):
            return False
        row = self.db.one("SELECT r.readonly FROM folders f JOIN roots r ON r.id=f.root_id WHERE f.path=?",
                          (folder_path,))
        if row and row["readonly"]:
            return False
        return os.access(folder_path, os.W_OK)

    def _edit_ini(self, folder_path: str, fn: Callable[[PicasaIni], None]) -> None:
        if not self._can_write_ini(folder_path):
            return
        with self._ini_lock(folder_path):
            try:
                ini = PicasaIni(folder_path)
                fn(ini)
                ini.save()
                self.db.x("UPDATE folders SET ini_mtime=? WHERE path=?", (ini.mtime(), folder_path))
            except OSError:
                pass

    def _by_folder(self, ids: Iterable[int]) -> dict[str, list]:
        out: dict[str, list] = {}
        for pid in ids:
            r = self.photo(pid)
            if r:
                out.setdefault(os.path.dirname(r["path"]), []).append(r)
        return out

    # ------------------------------------------------------------ queries
    def photo(self, pid: int):
        return self.db.one("SELECT * FROM photos WHERE id=?", (pid,))

    # ------------------------------------------------------------------ what the library shows
    def vis(self, a: str = "p", stacks: bool = True) -> str:
        """SQL for 'normally visible': not archived, private, recently deleted or hidden inside a stack."""
        v = (f"COALESCE({a}.archived,0)=0 AND COALESCE({a}.private,0)=0 AND {a}.deleted IS NULL")
        if stacks and self.settings.get("stack_photos", True) is not False:
            v += f" AND COALESCE({a}.stack_member,0)=0"
        return v

    def photo_by_path(self, path: str):
        return self.db.one("SELECT * FROM photos WHERE path=?", (os.path.abspath(path),))

    def _order(self) -> str:
        s = self.settings.get("sort") or "date"
        rev = bool(self.settings.get("sort_reverse"))
        d = " DESC" if rev else ""
        col = {"date": f"p.taken{d}, p.filename{d}", "name": f"p.filename COLLATE NOCASE{d}",
               "size": f"p.size{'' if rev else ' DESC'}"}.get(s, f"p.taken{d}")
        return "ORDER BY " + col

    def folders(self, include_hidden: bool | None = None):
        if include_hidden is None:
            include_hidden = bool(self.settings.get("show_hidden"))
        sort = self.settings.get("folder_sort") or "date"
        rev = bool(self.settings.get("folder_sort_reverse"))
        order = {"date": "date DESC, name", "name": "name COLLATE NOCASE", "recent": "id DESC",
                 "size": "(SELECT COALESCE(SUM(size),0) FROM photos WHERE folder_id=folders.id) DESC"}.get(sort, "date DESC")
        rows = self.db.q(f"SELECT folders.*, (SELECT COUNT(*) FROM photos p WHERE p.folder_id=folders.id "
                         f"AND {self.vis()}) AS shown FROM folders WHERE count > 0 "
                         f"{'' if include_hidden else 'AND hidden=0'} ORDER BY {order}")
        return list(reversed(rows)) if rev else rows

    def set_folder_hidden(self, fid: int, hidden: bool) -> None:
        self.db.x("UPDATE folders SET hidden=? WHERE id=?", (int(hidden), fid))
        self.emit("library")

    def folder(self, fid: int):
        return self.db.one("SELECT * FROM folders WHERE id=?", (fid,))

    def photos_in_folder(self, fid: int, include_hidden=False):
        h = "" if include_hidden else "AND hidden=0"
        return self.db.q(f"SELECT * FROM photos p WHERE folder_id=? {h} AND {self.vis()} {self._order()}", (fid,))

    def all_photos(self, where: str = "1", args=(), include_hidden=False):
        h = "" if include_hidden or self.settings.get("show_hidden") else "AND p.hidden=0 AND f.hidden=0"
        order = {f["id"]: i for i, f in enumerate(self.folders())}
        rows = self.db.q(f"SELECT p.* FROM photos p JOIN folders f ON f.id=p.folder_id WHERE ({where}) {h} "
                         f"AND {self.vis()} {self._order()}", args)
        return sorted(rows, key=lambda r: order.get(r["folder_id"], 1 << 30))

    def photos_by_ids(self, ids: list[int]):
        if not ids:
            return []
        rows = {}
        for i in range(0, len(ids), 900):
            chunk = ids[i:i + 900]
            for r in self.db.q(f"SELECT * FROM photos WHERE id IN ({','.join('?' * len(chunk))})", chunk):
                rows[r["id"]] = r
        return [rows[i] for i in ids if i in rows]

    def starred(self):
        return self.db.q(f"SELECT * FROM photos p WHERE star=1 AND hidden=0 AND {self.vis()} ORDER BY taken DESC")

    def videos(self):
        return self.db.q(f"SELECT * FROM photos p WHERE kind='video' AND hidden=0 AND {self.vis()} ORDER BY taken DESC")

    def hidden(self):
        return self.db.q(f"SELECT * FROM photos p WHERE hidden=1 AND {self.vis()} ORDER BY taken DESC")

    def recent(self, days: int = 30):
        return self.db.q(f"SELECT * FROM photos p WHERE added > ? AND hidden=0 AND {self.vis()} "
                         "ORDER BY added DESC, taken DESC",
                         (time.time() - days * 86400,))

    def unplaced(self):
        return self.db.q(f"SELECT * FROM photos p WHERE lat IS NULL AND hidden=0 AND {self.vis()} ORDER BY taken DESC")

    def geotagged(self):
        return self.db.q(f"SELECT * FROM photos p WHERE lat IS NOT NULL AND hidden=0 AND {self.vis()} ORDER BY taken")

    def counts(self) -> dict:
        v = self.vis()
        return {
            "photos": self.db.scalar(f"SELECT COUNT(*) FROM photos p WHERE kind!='video' AND {v}", default=0),
            "videos": self.db.scalar(f"SELECT COUNT(*) FROM photos p WHERE kind='video' AND {v}", default=0),
            "starred": self.db.scalar(f"SELECT COUNT(*) FROM photos p WHERE star=1 AND {v}", default=0),
            "folders": self.db.scalar("SELECT COUNT(*) FROM folders WHERE count>0", default=0),
            "albums": self.db.scalar("SELECT COUNT(*) FROM albums", default=0),
            "people": self.db.scalar("SELECT COUNT(*) FROM people WHERE name!='' AND hidden=0", default=0),
            "faces": self.db.scalar("SELECT COUNT(*) FROM faces WHERE ignored=0", default=0),
            "geotagged": self.db.scalar(f"SELECT COUNT(*) FROM photos p WHERE lat IS NOT NULL AND {v}", default=0),
            "hidden": self.db.scalar(f"SELECT COUNT(*) FROM photos p WHERE hidden=1 AND {v}", default=0),
            "archived": self.db.scalar("SELECT COUNT(*) FROM photos WHERE archived=1 AND deleted IS NULL "
                                       "AND COALESCE(private,0)=0", default=0),
            "private": self.db.scalar("SELECT COUNT(*) FROM photos WHERE private=1 AND deleted IS NULL", default=0),
            "deleted": self.db.scalar("SELECT COUNT(*) FROM photos WHERE deleted IS NOT NULL", default=0),
            "memories": len(self.memories()),
        }

    # ------------------------------------------------------------------ Memories ("On this day")
    def memories(self, window_days: int = 3, today: _dt.date | None = None):
        """Photos taken around today's date in earlier years, newest year first."""
        today = today or _dt.date.today()
        rows = self.db.q(f"SELECT * FROM photos p WHERE taken > 0 AND hidden=0 AND {self.vis()} "
                         f"AND kind!='video' AND CAST(strftime('%Y', taken, 'unixepoch', 'localtime') AS INTEGER) < ?",
                         (today.year,))
        out = []
        for r in rows:
            d = _dt.datetime.fromtimestamp(r["taken"]).date()
            try:
                same = d.replace(year=today.year)
            except ValueError:                      # 29 February
                same = d.replace(year=today.year, day=28)
            if abs((same - today).days) <= window_days:
                out.append(r)
        out.sort(key=lambda r: (-_dt.datetime.fromtimestamp(r["taken"]).year, r["taken"]))
        return out

    # ------------------------------------------------------------------ Archive / Private / Recently Deleted
    def set_archived(self, ids: Iterable[int], on: bool) -> None:
        ids = list(ids)
        self._set_flag("archived", ids, int(bool(on)))

    def set_private(self, ids: Iterable[int], on: bool) -> None:
        self._set_flag("private", list(ids), int(bool(on)))

    def _set_flag(self, col: str, ids: list[int], val) -> None:
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            self.db.x(f"UPDATE photos SET {col}=? WHERE id IN ({','.join('?' * len(chunk))})", [val] + chunk)
        self.emit("library")

    def archived_photos(self):
        return self.db.q("SELECT * FROM photos WHERE archived=1 AND deleted IS NULL AND COALESCE(private,0)=0 "
                         "ORDER BY taken DESC")

    def private_photos(self):
        return self.db.q("SELECT * FROM photos WHERE private=1 AND deleted IS NULL ORDER BY taken DESC")

    def deleted_photos(self):
        return self.db.q("SELECT * FROM photos WHERE deleted IS NOT NULL ORDER BY deleted DESC")

    def soft_delete(self, ids: Iterable[int]) -> int:
        """Move to Recently Deleted: hidden everywhere, the file stays put until it's purged (30 days)."""
        ids = list(ids)
        self._set_flag("deleted", ids, time.time())
        return len(ids)

    def restore_deleted(self, ids: Iterable[int]) -> None:
        self._set_flag("deleted", list(ids), None)

    def purge_deleted(self, ids: Iterable[int] | None = None, older_than_days: float | None = None) -> int:
        """Move recently deleted photos to the macOS Trash for good."""
        if ids is None:
            cut = time.time() - (older_than_days if older_than_days is not None else 30) * 86400
            ids = [r["id"] for r in self.db.q("SELECT id FROM photos WHERE deleted IS NOT NULL AND deleted < ?",
                                              (cut,))]
        ids = list(ids)
        # a stack goes together: deleting the cover also removes its RAW / Live partners
        extra = []
        for r in self.photos_by_ids(ids):
            if r["stack_key"] and r["stack_count"]:
                extra += [m["id"] for m in self.db.q("SELECT id FROM photos WHERE stack_key=? AND stack_member=1",
                                                      (r["stack_key"],))]
        return self.move_to_trash(ids + extra) if ids else 0

    # ------------------------------------------------------------------ Stacks (RAW+JPEG, bursts, Live Photos)
    def stack_members(self, key: str):
        return self.db.q("SELECT * FROM photos WHERE stack_key=? AND deleted IS NULL "
                         "ORDER BY stack_member, taken, filename", (key,))

    def rebuild_stacks(self, folder_ids: Iterable[int] | None = None) -> int:
        """Group RAW+JPEG pairs, Live Photo .mov halves and bursts (3+ shots within a second)."""
        if folder_ids is None:
            folder_ids = [r["id"] for r in self.db.q("SELECT id FROM folders")]
        n_stacks = 0
        for fid in folder_ids:
            rows = self.db.q("SELECT id, filename, kind, taken, camera, duration, star, path FROM photos "
                             "WHERE folder_id=? AND deleted IS NULL", (fid,))
            updates = {r["id"]: (None, 0, 0, "", "") for r in rows}   # key, member, count, kind, live
            by_stem: dict[str, list] = {}
            for r in rows:
                by_stem.setdefault(os.path.splitext(r["filename"])[0].lower(), []).append(r)
            used = set()
            for stem, grp in by_stem.items():
                if len(grp) < 2:
                    continue
                stills = [r for r in grp if r["kind"] == "photo"]
                raws = [r for r in grp if r["kind"] == "raw"]
                movs = [r for r in grp if r["kind"] == "video" and r["filename"].lower().endswith(".mov")
                        and (r["duration"] or 0) <= 4.5]
                cover = (stills or raws or [None])[0]
                if cover is None:
                    continue
                key = f"{fid}:{stem}"
                members = [r for r in grp if r is not cover and (r in raws or r in movs or r in stills)]
                if not members:
                    continue
                live = movs[0]["path"] if movs and stills else ""
                kind = "live" if live and not raws else ("raw" if raws else "pair")
                updates[cover["id"]] = (key, 0, len(members) + 1, kind, live)
                for m in members:
                    updates[m["id"]] = (key, 1, 0, kind, "")
                used.update(r["id"] for r in [cover] + members)
                n_stacks += 1
            # bursts: 3+ stills from the same camera, each within 1 s of the previous
            seq = sorted((r for r in rows if r["id"] not in used and r["kind"] in ("photo", "raw") and r["taken"]),
                         key=lambda r: (r["camera"] or "", r["taken"], r["filename"]))
            run = []

            def flush(run):
                nonlocal n_stacks
                if len(run) >= 3:
                    cover = next((r for r in run if r["star"]), run[0])
                    key = f"{fid}:burst:{run[0]['id']}"
                    updates[cover["id"]] = (key, 0, len(run), "burst", "")
                    for m in run:
                        if m is not cover:
                            updates[m["id"]] = (key, 1, 0, "burst", "")
                    n_stacks += 1

            for r in seq:
                if run and (r["camera"] or "") == (run[-1]["camera"] or "") and r["taken"] - run[-1]["taken"] <= 1.0:
                    run.append(r)
                else:
                    flush(run)
                    run = [r]
            flush(run)
            with self.db.write_lock:
                c = self.db.conn
                c.executemany("UPDATE photos SET stack_key=?, stack_member=?, stack_count=?, stack_kind=?, "
                              "live_video=? WHERE id=?", [v + (pid,) for pid, v in updates.items()])
                c.commit()
        return n_stacks

    # ------------------------------------------------------------------ place names (offline)
    def update_place_names(self, limit: int | None = None) -> int:
        from . import geocode
        if not geocode.available():
            return 0
        rows = self.db.q("SELECT id, lat, lon FROM photos WHERE lat IS NOT NULL AND place_name IS NULL"
                         + (f" LIMIT {int(limit)}" if limit else ""))
        cache, ups = {}, []
        for r in rows:
            k = (round(r["lat"], 3), round(r["lon"], 3))
            if k not in cache:
                cache[k] = geocode.full_label(r["lat"], r["lon"])
            ups.append((cache[k], r["id"]))
        if ups:
            with self.db.write_lock:
                self.db.conn.executemany("UPDATE photos SET place_name=? WHERE id=?", ups)
                self.db.conn.commit()
        return len(ups)

    # ------------------------------------------------------------ file-level helpers (used by the Photo Viewer)
    def describe_file(self, path: str) -> dict:
        """A row-like dict for any media file, whether or not it is in the library."""
        path = os.path.abspath(path)
        r = self.photo_by_path(path)
        if r is not None:
            return dict(r)
        st = os.stat(path)
        folder, fn = os.path.split(path)
        ini = PicasaIni(folder)
        chain = ini.filters(fn)
        return {"id": None, "path": path, "filename": fn, "kind": media.kind_of(fn), "mtime": st.st_mtime,
                "size": st.st_size, "filters": serialize_filters(chain), "rotate": ini.rotate(fn),
                "star": 1 if ini.star(fn) else 0, "caption": ini.caption(fn), "keywords": ",".join(ini.keywords(fn)),
                "taken": None, "width": 0, "height": 0, "duration": None, "lat": None, "lon": None,
                "camera": "", "exposure": "", "folder_id": None}

    def set_star_path(self, path: str, on: bool) -> None:
        r = self.photo_by_path(path)
        if r is not None:
            self.set_star([r["id"]], on)
            return
        folder, fn = os.path.split(os.path.abspath(path))
        if self.settings.get("write_picasa_ini") and os.access(folder, os.W_OK):
            ini = PicasaIni(folder)
            ini.set_star(fn, on)
            ini.save()

    def rotate_path(self, path: str, delta: int) -> None:
        r = self.photo_by_path(path)
        if r is not None:
            self.rotate([r["id"]], delta)
            return
        folder, fn = os.path.split(os.path.abspath(path))
        if self.settings.get("write_picasa_ini") and os.access(folder, os.W_OK):
            ini = PicasaIni(folder)
            ini.set_rotate(fn, ini.rotate(fn) + delta)
            ini.save()

    def ensure_in_library(self, path: str):
        """Picasa's 'Add File to Picasa': make sure the file's folder is known, return its row."""
        path = os.path.abspath(path)
        r = self.photo_by_path(path)
        if r is not None:
            return r
        folder = os.path.dirname(path)
        covering = next((x for x in self.roots() if (folder + os.sep).startswith(x["path"].rstrip(os.sep) + os.sep)), None)
        rid = covering["id"] if covering else self.add_root(folder, kind="once")
        root = next(x for x in self.roots() if x["id"] == rid)
        files = sorted(f for f in os.listdir(folder) if not f.startswith(".") and media.kind_of(f))
        self._scan_folder(folder, files, root, {"new": 0, "updated": 0, "removed": 0})
        self.emit("library")
        return self.photo_by_path(path)

    # ------------------------------------------------------------ simple edits
    def set_star(self, ids: Iterable[int], on: bool) -> None:
        ids = list(ids)
        self.db.xmany("UPDATE photos SET star=? WHERE id=?", [(int(on), i) for i in ids])
        for folder, rows in self._by_folder(ids).items():
            self._edit_ini(folder, lambda ini, rows=rows: [ini.set_star(r["filename"], on) for r in rows])
        self.emit("photos", ids)

    def set_hidden(self, ids: Iterable[int], on: bool) -> None:
        ids = list(ids)
        self.db.xmany("UPDATE photos SET hidden=? WHERE id=?", [(int(on), i) for i in ids])
        for folder, rows in self._by_folder(ids).items():
            self._edit_ini(folder, lambda ini, rows=rows: [ini.set(r["filename"], "hidden", "yes" if on else None)
                                                           for r in rows])
        self.emit("library")

    def set_caption(self, pid: int, text: str) -> None:
        text = (text or "").replace("\n", " ").strip()
        self.db.x("UPDATE photos SET caption=? WHERE id=?", (text, pid))
        r = self.photo(pid)
        if r:
            self._edit_ini(os.path.dirname(r["path"]), lambda ini: ini.set(r["filename"], "caption", text or None))
            self.write_file_meta(pid)
        self.emit("photos", [pid])

    def write_file_meta(self, pid: int, force: bool = False) -> bool:
        """Store caption, tags (and optionally name tags) inside a JPEG, like Picasa did."""
        if not (force or self.settings.get("write_meta_in_files")):
            return False
        r = self.photo(pid)
        if r is None or not r["path"].lower().endswith((".jpg", ".jpeg", ".jpe", ".jfif")):
            return False
        if not self._can_write_ini(os.path.dirname(r["path"])) or not os.access(r["path"], os.W_OK):
            return False
        regions = None
        if self.settings.get("write_faces_in_files"):
            regions = [(f["person_name"], (f["x"], f["y"], f["w"], f["h"])) for f in self.faces_for_photo(pid)
                       if f["person_name"] and f["confirmed"]]
        try:
            from .metadata_write import write_jpeg_metadata
            ok = write_jpeg_metadata(r["path"], r["caption"] or "",
                                     [k for k in (r["keywords"] or "").split(",") if k],
                                     regions, (r["width"], r["height"]) if r["width"] else None)
        except Exception as e:
            print("metadata write failed", r["path"], e)
            return False
        if ok:
            st = os.stat(r["path"])
            self.db.x("UPDATE photos SET mtime=?, size=? WHERE id=?", (st.st_mtime, st.st_size, pid))
        return ok

    def set_keywords(self, pid: int, keywords: list[str]) -> None:
        kws = [k.strip().replace(",", " ") for k in keywords if k.strip()]
        kws = list(dict.fromkeys(kws))
        self.db.x("UPDATE photos SET keywords=? WHERE id=?", (",".join(kws), pid))
        r = self.photo(pid)
        if r:
            self._edit_ini(os.path.dirname(r["path"]),
                           lambda ini: ini.set(r["filename"], "keywords", ",".join(kws) or None))
            self.write_file_meta(pid)
        self.emit("photos", [pid])

    def remove_keyword(self, ids, kw: str) -> None:
        for pid in ids:
            r = self.photo(pid)
            if r:
                cur = [k for k in (r["keywords"] or "").split(",") if k and k.lower() != kw.lower()]
                self.set_keywords(pid, cur)

    def all_keywords(self) -> list[tuple[str, int]]:
        counts: dict[str, int] = {}
        for (kws,) in self.db.q("SELECT keywords FROM photos WHERE keywords != ''"):
            for k in kws.split(","):
                if k:
                    counts[k] = counts.get(k, 0) + 1
        return sorted(counts.items(), key=lambda x: (-x[1], x[0].lower()))

    def add_keyword(self, ids: Iterable[int], kw: str) -> None:
        for pid in ids:
            r = self.photo(pid)
            if r:
                cur = [k for k in (r["keywords"] or "").split(",") if k]
                if kw not in cur:
                    self.set_keywords(pid, cur + [kw])

    def rotate(self, ids: Iterable[int], delta: int) -> None:
        ids = list(ids)
        for r in self.photos_by_ids(ids):
            q = ((r["rotate"] or 0) + delta) % 4
            self.db.x("UPDATE photos SET rotate=? WHERE id=?", (q, r["id"]))
            self._edit_ini(os.path.dirname(r["path"]), lambda ini, r=r, q=q: ini.set_rotate(r["filename"], q))
        self.emit("photos", ids)

    def filters(self, pid: int) -> list[Filter]:
        r = self.photo(pid)
        return parse_filters(r["filters"]) if r else []

    def set_filters(self, pid: int, chain: list[Filter]) -> None:
        self.db.x("UPDATE photos SET filters=? WHERE id=?", (serialize_filters(chain), pid))
        r = self.photo(pid)
        if r:
            self._edit_ini(os.path.dirname(r["path"]), lambda ini: ini.set_filters(r["filename"], chain))
        self.emit("photos", [pid])

    def set_location(self, ids: Iterable[int], lat: float | None, lon: float | None,
                     write_exif: bool = True) -> None:
        ids = list(ids)
        for r in self.photos_by_ids(ids):
            self.db.x("UPDATE photos SET lat=?, lon=?, place_name=NULL WHERE id=?", (lat, lon, r["id"]))
            if write_exif and lat is not None and self._can_write_ini(os.path.dirname(r["path"])):
                if media.write_gps_exif(r["path"], lat, lon):
                    st = os.stat(r["path"])
                    self.db.x("UPDATE photos SET mtime=?, size=? WHERE id=?", (st.st_mtime, st.st_size, r["id"]))
        self.update_place_names()
        self.emit("photos", ids)

    # ------------------------------------------------------------ albums
    def albums(self):
        return self.db.q(
            "SELECT a.*, (SELECT COUNT(*) FROM album_photos ap WHERE ap.album_id=a.id) AS n "
            "FROM albums a ORDER BY a.name COLLATE NOCASE")

    def album(self, aid: int):
        return self.db.one("SELECT * FROM albums WHERE id=?", (aid,))

    def album_by_name(self, name: str):
        return self.db.one("SELECT * FROM albums WHERE name=? COLLATE NOCASE", (name,))

    def photos_in_album(self, aid: int):
        return self.db.q(f"SELECT p.* FROM photos p JOIN album_photos ap ON ap.photo_id=p.id "
                         f"WHERE ap.album_id=? AND {self.vis(stacks=False)} ORDER BY ap.position, p.taken", (aid,))

    def create_album(self, name: str, description: str = "") -> int:
        token = secrets.token_hex(8)
        cur = self.db.x("INSERT INTO albums(token, name, description, created) VALUES(?,?,?,?)",
                        (token, name, description, time.time()))
        self.emit("albums")
        return cur.lastrowid

    def rename_album(self, aid: int, name: str) -> None:
        self.db.x("UPDATE albums SET name=? WHERE id=?", (name, aid))
        a = self.album(aid)
        folders = {os.path.dirname(r["path"]) for r in self.photos_in_album(aid)}
        for folder in folders:
            self._edit_ini(folder, lambda ini: ini.set(f".album:{a['token']}", "name", name))
        self.emit("albums")

    def _sync_album_ini(self, pids: list[int]) -> None:
        for folder, rows in self._by_folder(pids).items():
            def apply(ini: PicasaIni, rows=rows):
                for r in rows:
                    albs = self.db.q("SELECT a.token, a.name, a.created FROM albums a JOIN album_photos ap "
                                     "ON ap.album_id=a.id WHERE ap.photo_id=?", (r["id"],))
                    for a in albs:
                        ini.ensure_album(a["token"], a["name"],
                                         _dt.datetime.fromtimestamp(a["created"] or time.time()).astimezone()
                                         .isoformat(timespec="seconds"))
                    ini.set(r["filename"], "albums", ",".join(a["token"] for a in albs) or None)
            self._edit_ini(folder, apply)

    def add_to_album(self, aid: int, ids: Iterable[int]) -> None:
        ids = list(ids)
        base = self.db.scalar("SELECT COALESCE(MAX(position),0) FROM album_photos WHERE album_id=?", (aid,), 0)
        self.db.xmany("INSERT OR IGNORE INTO album_photos(album_id, photo_id, position) VALUES(?,?,?)",
                      [(aid, pid, base + i + 1) for i, pid in enumerate(ids)])
        self._sync_album_ini(ids)
        self.emit("albums")

    def remove_from_album(self, aid: int, ids: Iterable[int]) -> None:
        ids = list(ids)
        self.db.xmany("DELETE FROM album_photos WHERE album_id=? AND photo_id=?", [(aid, i) for i in ids])
        self._sync_album_ini(ids)
        self.emit("albums")

    def reorder_album(self, aid: int, ordered_ids: list[int]) -> None:
        self.db.xmany("UPDATE album_photos SET position=? WHERE album_id=? AND photo_id=?",
                      [(i, aid, pid) for i, pid in enumerate(ordered_ids)])

    def delete_album(self, aid: int) -> None:
        a = self.album(aid)
        pids = [r["id"] for r in self.photos_in_album(aid)]
        self.db.x("DELETE FROM album_photos WHERE album_id=?", (aid,))
        self.db.x("DELETE FROM albums WHERE id=?", (aid,))
        if a:
            self._sync_album_ini(pids)
            for folder in {os.path.dirname(r["path"]) for r in self.photos_by_ids(pids)}:
                self._edit_ini(folder, lambda ini: ini.remove_section(f".album:{a['token']}"))
        self.emit("albums")

    def albums_for_photo(self, pid: int):
        return self.db.q("SELECT a.* FROM albums a JOIN album_photos ap ON ap.album_id=a.id WHERE ap.photo_id=?",
                         (pid,))

    # ------------------------------------------------------------ people / faces
    def people(self, include_unnamed=False):
        where = "" if include_unnamed else "WHERE p.name != ''"
        return self.db.q(
            f"SELECT p.*, (SELECT COUNT(DISTINCT f.photo_id) FROM faces f WHERE f.person_id=p.id AND f.confirmed=1"
            f" AND f.ignored=0) AS n, (SELECT COUNT(*) FROM faces f WHERE f.person_id=p.id AND f.confirmed=0"
            f" AND f.ignored=0) AS suggestions FROM people p {where} ORDER BY p.name COLLATE NOCASE")

    def person(self, person_id: int):
        return self.db.one("SELECT * FROM people WHERE id=?", (person_id,))

    def person_by_name(self, name: str):
        return self.db.one("SELECT * FROM people WHERE name=? COLLATE NOCASE", (name,))

    def photos_for_person(self, person_id: int, include_suggested=False):
        c = "" if include_suggested else "AND f.confirmed=1"
        return self.db.q(f"SELECT DISTINCT p.* FROM photos p JOIN faces f ON f.photo_id=p.id "
                         f"WHERE f.person_id=? AND f.ignored=0 {c} AND {self.vis(stacks=False)} "
                         f"ORDER BY p.taken DESC", (person_id,))

    def faces_for_photo(self, pid: int):
        return self.db.q("SELECT f.*, pe.name AS person_name FROM faces f LEFT JOIN people pe ON pe.id=f.person_id "
                         "WHERE f.photo_id=? AND f.ignored=0", (pid,))

    def faces_for_person(self, person_id: int, confirmed: int | None = None, limit: int = 500):
        c = "" if confirmed is None else f"AND f.confirmed={int(confirmed)}"
        return self.db.q(f"SELECT f.*, p.path, p.rotate, p.filters FROM faces f JOIN photos p ON p.id=f.photo_id "
                         f"WHERE f.person_id=? AND f.ignored=0 {c} ORDER BY f.suggestion DESC, p.taken DESC LIMIT ?",
                         (person_id, limit))

    def unnamed_clusters(self, min_size: int = 2, limit: int = 60):
        return self.db.q(
            "SELECT cluster, COUNT(*) AS n FROM faces WHERE person_id IS NULL AND ignored=0 AND cluster IS NOT NULL "
            "GROUP BY cluster HAVING n >= ? ORDER BY n DESC LIMIT ?", (min_size, limit))

    def faces_in_cluster(self, cluster: int, limit: int = 200):
        return self.db.q("SELECT f.*, p.path, p.rotate, p.filters FROM faces f JOIN photos p ON p.id=f.photo_id "
                         "WHERE f.cluster=? AND f.person_id IS NULL AND f.ignored=0 LIMIT ?", (cluster, limit))

    def unnamed_faces(self, limit: int = 300):
        return self.db.q("SELECT f.*, p.path, p.rotate, p.filters FROM faces f JOIN photos p ON p.id=f.photo_id "
                         "WHERE f.person_id IS NULL AND f.ignored=0 ORDER BY f.score DESC LIMIT ?", (limit,))

    def ensure_person(self, name: str) -> int:
        name = name.strip()
        row = self.person_by_name(name)
        if row:
            if row["hidden"]:
                self.db.x("UPDATE people SET hidden=0 WHERE id=?", (row["id"],))
            return row["id"]
        cur = self.db.x("INSERT INTO people(name, contact_id) VALUES(?,?)", (name, secrets.token_hex(8)))
        self.emit("people")
        return cur.lastrowid

    def assign_faces(self, face_ids: Iterable[int], person_id: int | None, confirmed: bool = True) -> None:
        face_ids = list(face_ids)
        self.db.xmany("UPDATE faces SET person_id=?, confirmed=?, suggestion=NULL, cluster=CASE WHEN ? IS NULL "
                      "THEN cluster ELSE NULL END WHERE id=?",
                      [(person_id, int(confirmed and person_id is not None), person_id, f) for f in face_ids])
        self._sync_faces_ini(face_ids)
        self.emit("people")

    def name_faces(self, face_ids: Iterable[int], name: str) -> int:
        pid = self.ensure_person(name)
        self.assign_faces(face_ids, pid, True)
        return pid

    def reject_suggestions(self, face_ids: Iterable[int]) -> None:
        self.db.xmany("UPDATE faces SET person_id=NULL, confirmed=0, suggestion=NULL WHERE id=?",
                      [(f,) for f in face_ids])
        self.emit("people")

    def ignore_faces(self, face_ids: Iterable[int]) -> None:
        face_ids = list(face_ids)
        self.db.xmany("UPDATE faces SET ignored=1, person_id=NULL WHERE id=?", [(f,) for f in face_ids])
        self._sync_faces_ini(face_ids)
        self.emit("people")

    def add_manual_face(self, pid: int, rect: tuple[float, float, float, float], name: str | None) -> int:
        l, t, r, b = rect
        person = self.ensure_person(name) if name else None
        cur = self.db.x("INSERT INTO faces(photo_id, x, y, w, h, person_id, source, confirmed, score) "
                        "VALUES(?,?,?,?,?,?,?,?,?)", (pid, l, t, r - l, b - t, person, "manual", int(bool(person)), 1.0))
        self._sync_faces_ini([cur.lastrowid])
        self.emit("people")
        return cur.lastrowid

    def rename_person(self, person_id: int, name: str) -> None:
        other = self.person_by_name(name)
        if other and other["id"] != person_id:
            self.merge_people(person_id, other["id"])
            return
        self.db.x("UPDATE people SET name=? WHERE id=?", (name, person_id))
        p = self.person(person_id)
        folders = {os.path.dirname(r["path"]) for r in self.photos_for_person(person_id)}
        for folder in folders:
            self._edit_ini(folder, lambda ini: ini.set_contact(p["contact_id"], name))
        self.emit("people")

    def merge_people(self, src: int, dst: int) -> None:
        faces = [r["id"] for r in self.db.q("SELECT id FROM faces WHERE person_id=?", (src,))]
        self.db.x("UPDATE faces SET person_id=? WHERE person_id=?", (dst, src))
        self.db.x("DELETE FROM people WHERE id=?", (src,))
        self._sync_faces_ini(faces)
        self.emit("people")

    def remove_person(self, person_id: int) -> None:
        faces = [r["id"] for r in self.db.q("SELECT id FROM faces WHERE person_id=?", (person_id,))]
        self.db.x("UPDATE faces SET person_id=NULL, confirmed=0 WHERE person_id=?", (person_id,))
        self.db.x("DELETE FROM people WHERE id=?", (person_id,))
        self._sync_faces_ini(faces)
        self.emit("people")

    def _sync_faces_ini(self, face_ids: list[int]) -> None:
        if not face_ids:
            return
        qs = ",".join("?" * len(face_ids))
        pids = [r[0] for r in self.db.q(f"SELECT DISTINCT photo_id FROM faces WHERE id IN ({qs})", face_ids)]
        for folder, rows in self._by_folder(pids).items():
            def apply(ini: PicasaIni, rows=rows):
                for r in rows:
                    fs = self.db.q("SELECT f.*, pe.contact_id, pe.name FROM faces f LEFT JOIN people pe "
                                   "ON pe.id=f.person_id WHERE f.photo_id=? AND f.ignored=0", (r["id"],))
                    out = []
                    for f in fs:
                        rect = (f["x"], f["y"], f["x"] + f["w"], f["y"] + f["h"])
                        if f["person_id"] and f["confirmed"] and f["contact_id"]:
                            out.append((rect, f["contact_id"]))
                            ini.set_contact(f["contact_id"], f["name"])
                        elif f["source"] == "picasa":
                            out.append((rect, UNKNOWN_CONTACT))
                    ini.set_faces(r["filename"], out)
            self._edit_ini(folder, apply)
            if self.settings.get("write_faces_in_files"):
                for r in rows:
                    self.write_file_meta(r["id"])

    # ------------------------------------------------------------ labels / text
    def labels_for_photo(self, pid: int):
        return self.db.q("SELECT label, conf, source FROM labels WHERE photo_id=? ORDER BY conf DESC", (pid,))

    def top_labels(self, limit=40):
        return self.db.q("SELECT label, COUNT(*) AS n FROM labels GROUP BY label ORDER BY n DESC LIMIT ?", (limit,))

    # ------------------------------------------------------------ saving edits (Picasa-style)
    def originals_path(self, r) -> Path:
        p = Path(r["path"])
        return p.parent / ORIGINALS_DIR / p.name

    def has_backup(self, pid: int) -> bool:
        r = self.photo(pid)
        return bool(r) and self.originals_path(r).exists()

    def render_full(self, pid: int):
        r = self.photo(pid)
        im = media.load_image(r["path"])
        return render(im, parse_filters(r["filters"]), r["rotate"] or 0)

    def save_edits(self, pid: int) -> None:
        """Write edits into the file, keeping the untouched original in .picasaoriginals/."""
        r = self.photo(pid)
        if r is None:
            raise ValueError("photo not found")
        ext = os.path.splitext(r["path"])[1].lower()
        if ext not in media.WRITABLE_EXT:
            raise ValueError("This file type can't be overwritten; use Export instead.")
        if not os.access(os.path.dirname(r["path"]), os.W_OK):
            raise PermissionError("The folder is read-only; use Export instead.")
        im = self.render_full(pid)
        backup = self.originals_path(r)
        backup.parent.mkdir(exist_ok=True)
        if not backup.exists():
            shutil.copy2(r["path"], backup)
        _save_like_original(im, r["path"], str(backup))
        st = os.stat(r["path"])
        self.db.x("UPDATE photos SET filters='', rotate=0, mtime=?, size=?, width=?, height=?, phash=NULL, "
                  "faces_done=0, labels_done=0, clip_done=0 WHERE id=?",
                  (st.st_mtime, st.st_size, im.width, im.height, pid))
        self._edit_ini(os.path.dirname(r["path"]), lambda ini: (ini.set_filters(r["filename"], []),
                                                                ini.set_rotate(r["filename"], 0)))
        self.emit("photos", [pid])

    def revert_saved(self, pid: int) -> bool:
        r = self.photo(pid)
        if r is None:
            return False
        backup = self.originals_path(r)
        if not backup.exists():
            return False
        os.replace(backup, r["path"])
        try:
            backup.parent.rmdir()
        except OSError:
            pass
        md = media.read_metadata(r["path"])
        st = os.stat(r["path"])
        self.db.x("UPDATE photos SET filters='', rotate=0, mtime=?, size=?, width=?, height=?, phash=NULL, "
                  "faces_done=0, labels_done=0, clip_done=0 WHERE id=?",
                  (st.st_mtime, st.st_size, md["width"], md["height"], pid))
        self._edit_ini(os.path.dirname(r["path"]), lambda ini: (ini.set_filters(r["filename"], []),
                                                                ini.set_rotate(r["filename"], 0)))
        self.emit("photos", [pid])
        return True

    # ------------------------------------------------------------ files
    def move_to_trash(self, ids: Iterable[int]) -> int:
        ids = list(ids)
        done = []
        for r in self.photos_by_ids(ids):
            if _trash(r["path"]):
                done.append(r["id"])
        self._delete_photo_rows(done)
        self.db.x("UPDATE folders SET count=(SELECT COUNT(*) FROM photos WHERE folder_id=folders.id)")
        self.emit("library")
        return len(done)

    def rename_folder_title(self, fid: int, title: str, description: str | None = None) -> None:
        f = self.folder(fid)
        if not f:
            return
        self.db.x("UPDATE folders SET name=?, description=COALESCE(?, description) WHERE id=?",
                  (title, description, fid))
        self._edit_ini(f["path"], lambda ini: (ini.set("Picasa", "name", title),
                                               description is not None and ini.set("Picasa", "description",
                                                                                   description)))
        self.emit("library")


def _trash(path: str) -> bool:
    try:
        from PySide6.QtCore import QFile
        return bool(QFile.moveToTrash(path))
    except Exception:
        pass
    if sys.platform == "darwin":
        import subprocess
        script = f'tell application "Finder" to delete POSIX file "{path}"'
        return subprocess.call(["osascript", "-e", script], stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL) == 0
    return False


def _save_like_original(im, dest: str, original: str) -> None:
    from PIL import Image
    ext = os.path.splitext(dest)[1].lower()
    tmp = dest + ".fotohaus-tmp"
    exif_bytes = None
    try:
        src = Image.open(original)
        exif = src.getexif()
        if exif:
            exif[0x0112] = 1  # pixels are now upright
            exif_bytes = exif.tobytes()
        icc = src.info.get("icc_profile")
    except Exception:
        icc = None
    kw = {}
    if exif_bytes:
        kw["exif"] = exif_bytes
    if icc:
        kw["icc_profile"] = icc
    if ext in (".jpg", ".jpeg", ".jpe", ".jfif"):
        im.save(tmp, "JPEG", quality=95, subsampling=0, optimize=True, **kw)
    elif ext == ".png":
        im.save(tmp, "PNG", **{k: v for k, v in kw.items() if k != "exif"})
    elif ext in (".tif", ".tiff"):
        im.save(tmp, "TIFF", compression="tiff_lzw")
    elif ext == ".webp":
        im.save(tmp, "WEBP", quality=95, **kw)
    elif ext in (".heic", ".heif"):
        im.save(tmp, "HEIF", quality=92, **kw)
    else:
        im.save(tmp)
    os.replace(tmp, dest)


_lib: Library | None = None


def get_library() -> Library:
    global _lib
    if _lib is None:
        _lib = Library()
    return _lib
