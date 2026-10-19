"""File and folder operations that keep Picasa metadata (.picasa.ini, .picasaoriginals) consistent."""
from __future__ import annotations

import datetime as _dt
import os
import shutil
import time

from . import media
from .picasa.ini import PicasaIni

ORIG = ".picasaoriginals"


def unique_path(path: str) -> str:
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    i = 2
    while os.path.exists(f"{base} ({i}){ext}"):
        i += 1
    return f"{base} ({i}){ext}"


def _move_ini_section(src_dir: str, src_name: str, dst_dir: str, dst_name: str, copy: bool = False) -> None:
    src = PicasaIni(src_dir)
    sec = src.section(src_name)
    if not sec and not src.exists:
        return
    data = dict(sec or {})
    dst = src if os.path.abspath(src_dir) == os.path.abspath(dst_dir) else PicasaIni(dst_dir)
    if data:
        for k, v in data.items():
            dst.set(dst_name, k, v)
        # albums referenced by the photo
        for tok in [t for t in data.get("albums", "").split(",") if t]:
            meta = src.albums().get(tok)
            if meta:
                for k, v in meta.items():
                    dst.set(f".album:{tok}", k, v)
        # contacts referenced by face tags
        contacts = src.contacts()
        for _, cid in src.faces(src_name):
            if cid in contacts:
                dst.set_contact(cid, contacts[cid])
    if not copy and dst is not src:
        src.remove_section(src_name)
        src.save()
    elif not copy and src_name.lower() != dst_name.lower():
        src.remove_section(src_name)
    dst.save()


def move_file(lib, pid: int, dest_dir: str, new_name: str | None = None, copy: bool = False) -> str:
    """Move (or copy) a photo to another folder and/or name, carrying its metadata."""
    r = lib.photo(pid)
    if r is None:
        raise FileNotFoundError(pid)
    src = r["path"]
    src_dir, src_name = os.path.split(src)
    dest_dir = os.path.abspath(dest_dir)
    os.makedirs(dest_dir, exist_ok=True)
    target = os.path.join(dest_dir, new_name or src_name)
    if os.path.abspath(target) == os.path.abspath(src):
        return src
    if os.path.exists(target) and os.path.abspath(target).lower() != src.lower():
        target = unique_path(target)
    dst_name = os.path.basename(target)
    if copy:
        shutil.copy2(src, target)
    else:
        shutil.move(src, target)
    # .picasaoriginals backup travels with the photo
    bsrc = os.path.join(src_dir, ORIG, src_name)
    if os.path.exists(bsrc):
        bdst_dir = os.path.join(dest_dir, ORIG)
        os.makedirs(bdst_dir, exist_ok=True)
        (shutil.copy2 if copy else shutil.move)(bsrc, os.path.join(bdst_dir, dst_name))
        if not copy:
            try:
                os.rmdir(os.path.join(src_dir, ORIG))
            except OSError:
                pass
    if lib._can_write_ini(src_dir) or lib.settings.get("write_picasa_ini"):
        try:
            _move_ini_section(src_dir, src_name, dest_dir, dst_name, copy)
        except OSError:
            pass
    # database
    fid = _folder_id(lib, dest_dir)
    if copy:
        lib.db.x("INSERT INTO photos(folder_id, path, filename, kind, size, mtime, width, height, taken, camera, lens,"
                 " exposure, lat, lon, star, caption, keywords, filters, rotate, hidden, duration, phash, ocr,"
                 " labels_done, faces_done, clip_done, added) SELECT ?, ?, ?, kind, size, mtime, width, height, taken,"
                 " camera, lens, exposure, lat, lon, star, caption, keywords, filters, rotate, hidden, duration, phash,"
                 " ocr, labels_done, 0, clip_done, ? FROM photos WHERE id=?", (fid, target, dst_name, time.time(), pid))
    else:
        lib.db.x("UPDATE photos SET folder_id=?, path=?, filename=? WHERE id=?", (fid, target, dst_name, pid))
    _refresh_folder_counts(lib)
    return target


def _folder_id(lib, path: str) -> int:
    row = lib.db.one("SELECT id FROM folders WHERE path=?", (path,))
    if row:
        return row["id"]
    root = next((x for x in lib.roots() if (path + os.sep).startswith(x["path"].rstrip(os.sep) + os.sep)
                 and x["kind"] != "exclude"), None)
    cur = lib.db.x("INSERT INTO folders(path, name, root_id, date) VALUES(?,?,?,?)",
                   (path, os.path.basename(path), root["id"] if root else None, time.time()))
    return cur.lastrowid


def _refresh_folder_counts(lib) -> None:
    lib.db.x("UPDATE folders SET count=(SELECT COUNT(*) FROM photos WHERE folder_id=folders.id), "
             "date=COALESCE((SELECT MIN(taken) FROM photos WHERE folder_id=folders.id), date)")


def move_photos(lib, ids, dest_dir: str, copy: bool = False) -> list[str]:
    out = []
    for pid in list(ids):
        try:
            out.append(move_file(lib, pid, dest_dir, copy=copy))
        except OSError as e:
            print("move failed", pid, e)
    lib.emit("library")
    return out


def rename_photo(lib, pid: int, new_name: str) -> str:
    r = lib.photo(pid)
    ext = os.path.splitext(r["filename"])[1]
    if not os.path.splitext(new_name)[1]:
        new_name += ext
    out = move_file(lib, pid, os.path.dirname(r["path"]), new_name)
    lib.emit("library")
    return out


def batch_rename(lib, ids, base: str, include_date: bool = False, include_resolution: bool = False,
                 start: int = 1) -> list[str]:
    """Picasa-style batch rename: base[-date][-WxH][-n].ext"""
    rows = lib.photos_by_ids(list(ids))
    n = len(rows)
    out = []
    for i, r in enumerate(rows):
        parts = [base.strip() or "Photo"]
        if include_date and r["taken"]:
            parts.append(_dt.datetime.fromtimestamp(r["taken"]).strftime("%Y-%m-%d %H.%M.%S"))
        if include_resolution and r["width"]:
            parts.append(f"{r['width']}x{r['height']}")
        if n > 1:
            parts.append(str(start + i))
        name = "-".join(parts) + os.path.splitext(r["filename"])[1].lower()
        out.append(move_file(lib, r["id"], os.path.dirname(r["path"]), name))
    lib.emit("library")
    return out


def adjust_dates(lib, ids, delta_seconds: float | None = None, absolute: float | None = None,
                 write_file: bool = True) -> int:
    """Shift photo dates by a delta, or set them all to one time. Writes EXIF for JPEGs."""
    n = 0
    for r in lib.photos_by_ids(list(ids)):
        old = r["taken"] or r["mtime"] or time.time()
        new = absolute if absolute is not None else old + (delta_seconds or 0)
        lib.db.x("UPDATE photos SET taken=? WHERE id=?", (new, r["id"]))
        if write_file and r["path"].lower().endswith((".jpg", ".jpeg", ".jpe", ".jfif")) \
                and os.access(r["path"], os.W_OK):
            try:
                import piexif
                ex = piexif.load(r["path"])
                stamp = _dt.datetime.fromtimestamp(new).strftime("%Y:%m:%d %H:%M:%S").encode()
                ex.setdefault("Exif", {})[piexif.ExifIFD.DateTimeOriginal] = stamp
                ex["Exif"][piexif.ExifIFD.DateTimeDigitized] = stamp
                ex.setdefault("0th", {})[piexif.ImageIFD.DateTime] = stamp
                if "thumbnail" in ex and ex["thumbnail"] is None:
                    ex.pop("thumbnail")
                piexif.insert(piexif.dump(ex), r["path"])
                st = os.stat(r["path"])
                lib.db.x("UPDATE photos SET mtime=?, size=? WHERE id=?", (st.st_mtime, st.st_size, r["id"]))
            except Exception as e:
                print("date write failed", r["path"], e)
        elif write_file and os.access(r["path"], os.W_OK):
            try:
                os.utime(r["path"], (new, new))
                st = os.stat(r["path"])
                lib.db.x("UPDATE photos SET mtime=? WHERE id=?", (st.st_mtime, r["id"]))
            except OSError:
                pass
        n += 1
    _refresh_folder_counts(lib)
    lib.emit("library")
    return n


# ------------------------------------------------------------------ folders

def new_folder(lib, parent: str, name: str) -> str:
    path = unique_path(os.path.join(parent, name.strip() or "New Folder"))
    os.makedirs(path)
    _folder_id(lib, path)
    lib.emit("library")
    return path


def move_to_new_folder(lib, ids, name: str, parent: str | None = None, description: str = "",
                       place: str = "", date: float | None = None) -> str:
    rows = lib.photos_by_ids(list(ids))
    if not rows:
        raise ValueError("nothing selected")
    parent = parent or os.path.dirname(os.path.dirname(rows[0]["path"]))
    path = new_folder(lib, parent, name)
    move_photos(lib, ids, path)
    _write_folder_meta(lib, path, name, description, place, date)
    return path


def _write_folder_meta(lib, path, name, description, place, date):
    fid = _folder_id(lib, path)
    lib.db.x("UPDATE folders SET name=?, description=?, place=?, date=COALESCE(?, date) WHERE id=?",
             (name, description, place, date, fid))
    if lib._can_write_ini(path) or lib.settings.get("write_picasa_ini"):
        ini = PicasaIni(path)
        ini.set("Picasa", "name", name)
        ini.set("Picasa", "description", description or None)
        ini.set("Picasa", "location", place or None)
        if date:
            ini.set("Picasa", "date", f"{date / 86400 + 25569:.6f}")  # OLE automation date, as Picasa wrote it
        ini.save()


def set_folder_properties(lib, fid: int, name: str, description: str, place: str, date: float | None):
    f = lib.folder(fid)
    _write_folder_meta(lib, f["path"], name, description, place, date)
    lib.emit("library")


def split_folder_here(lib, fid: int, first_pid: int, new_name: str) -> str:
    """Move the selected photo and every photo after it into a new sibling folder."""
    rows = list(lib.photos_in_folder(fid, include_hidden=True))
    ids = [r["id"] for r in rows]
    if first_pid not in ids:
        raise ValueError("photo not in folder")
    tail = ids[ids.index(first_pid):]
    f = lib.folder(fid)
    return move_to_new_folder(lib, tail, new_name, parent=os.path.dirname(f["path"]))


def _repath_tree(lib, old: str, new: str) -> None:
    o, n = old.rstrip(os.sep), new.rstrip(os.sep)
    with lib.db.write_lock:
        c = lib.db.conn
        for fid, p in c.execute("SELECT id, path FROM folders WHERE path=? OR path LIKE ?", (o, o + os.sep + "%")).fetchall():
            np_ = n + p[len(o):]
            c.execute("UPDATE folders SET path=?, name=CASE WHEN path=? THEN ? ELSE name END WHERE id=?",
                      (np_, o, os.path.basename(n), fid))
        for pid, p in c.execute("SELECT id, path FROM photos WHERE path LIKE ?", (o + os.sep + "%",)).fetchall():
            c.execute("UPDATE photos SET path=? WHERE id=?", (n + p[len(o):], pid))
        c.commit()


def rename_folder(lib, fid: int, new_name: str) -> str:
    f = lib.folder(fid)
    old = f["path"]
    new = os.path.join(os.path.dirname(old), new_name.strip())
    if os.path.exists(new):
        raise FileExistsError(new)
    os.rename(old, new)
    _repath_tree(lib, old, new)
    lib.emit("library")
    return new


def move_folder(lib, fid: int, new_parent: str) -> str:
    f = lib.folder(fid)
    old = f["path"]
    new = unique_path(os.path.join(new_parent, os.path.basename(old)))
    shutil.move(old, new)
    _repath_tree(lib, old, new)
    if not any((new + os.sep).startswith(r["path"].rstrip(os.sep) + os.sep) for r in lib.roots()):
        lib.add_root(new)
    lib.emit("library")
    return new


def delete_folder(lib, fid: int) -> bool:
    """Move a whole folder to the Trash."""
    from .library import _trash
    f = lib.folder(fid)
    if not f or not _trash(f["path"]):
        return False
    o = f["path"].rstrip(os.sep)
    ids = [r[0] for r in lib.db.q("SELECT id FROM photos WHERE path LIKE ?", (o + os.sep + "%",))]
    lib._delete_photo_rows(ids)
    lib.db.x("DELETE FROM folders WHERE path=? OR path LIKE ?", (o, o + os.sep + "%"))
    lib.emit("library")
    return True


def remove_folder_from_library(lib, fid: int) -> None:
    """Picasa's 'Remove from Picasa': stop showing this folder (files are untouched)."""
    f = lib.folder(fid)
    lib.add_root(f["path"], kind="exclude")
    lib._delete_folder(fid)
    lib.emit("library")


# ------------------------------------------------------------------ clipboard-style transfer

def copy_to_clipboard_image(path_or_row) -> None:
    from PySide6.QtGui import QGuiApplication
    from .edits import render
    from .picasa.ini import parse_filters
    from .ui.editor import pil_to_qimage
    r = path_or_row
    im = media.load_image(r["path"], max_dim=4096)
    im = render(im, parse_filters(r["filters"]), r["rotate"] or 0)
    QGuiApplication.clipboard().setImage(pil_to_qimage(im))
