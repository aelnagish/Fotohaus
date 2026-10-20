"""Import from cameras, memory cards and folders (Picasa's Import panel)."""
from __future__ import annotations

import datetime as _dt
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from . import media


@dataclass
class ImportItem:
    path: str
    size: int
    taken: float
    duplicate: bool = False
    selected: bool = True


def sources() -> list[tuple[str, str]]:
    """[(label, path)] of removable media that look like camera cards (have a DCIM folder)."""
    out = []
    roots = []
    if sys.platform == "darwin":
        roots = [os.path.join("/Volumes", v) for v in os.listdir("/Volumes")] if os.path.isdir("/Volumes") else []
    else:
        for base in ("/media", "/run/media", "/mnt"):
            if os.path.isdir(base):
                for v in os.listdir(base):
                    p = os.path.join(base, v)
                    roots.append(p)
                    if os.path.isdir(p):
                        roots += [os.path.join(p, x) for x in os.listdir(p)]
    for r in roots:
        if os.path.isdir(os.path.join(r, "DCIM")):
            out.append((f"{os.path.basename(r)} (camera card)", r))
    return out


def scan_source(lib, src: str, progress=None, should_stop=None) -> list[ImportItem]:
    items = []
    known = {(r["filename"].lower(), r["size"]) for r in lib.db.q("SELECT filename, size FROM photos")}
    for dirpath, dirnames, filenames in os.walk(src):
        if should_stop and should_stop():
            break
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for f in filenames:
            if f.startswith(".") or not media.kind_of(f):
                continue
            p = os.path.join(dirpath, f)
            try:
                st = os.stat(p)
            except OSError:
                continue
            md = media.read_metadata(p) if media.kind_of(f) != "video" else {"taken": st.st_mtime}
            it = ImportItem(p, st.st_size, md.get("taken") or st.st_mtime)
            it.duplicate = (f.lower(), st.st_size) in known
            it.selected = not it.duplicate
            items.append(it)
            if progress and len(items) % 20 == 0:
                progress(len(items))
    items.sort(key=lambda i: i.taken)
    return items


def do_import(lib, items: list[ImportItem], base_dir: str, title: str, by_date: bool = False,
              delete_after: bool = False, progress=None, should_stop=None) -> dict:
    stats = {"copied": 0, "deleted": 0, "folders": set()}
    chosen = [i for i in items if i.selected]
    for k, it in enumerate(chosen):
        if should_stop and should_stop():
            break
        if by_date:
            folder = Path(base_dir) / _dt.datetime.fromtimestamp(it.taken).strftime("%Y-%m-%d")
        else:
            folder = Path(base_dir) / (title.strip() or _dt.date.today().isoformat())
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / os.path.basename(it.path)
        if target.exists():
            from .fileops import unique_path
            target = Path(unique_path(str(target)))
        shutil.copy2(it.path, target)
        stats["copied"] += 1
        stats["folders"].add(str(folder))
        # only delete from the card once the copy is verifiably complete
        if delete_after and os.path.getsize(target) == os.path.getsize(it.path):
            try:
                os.remove(it.path)
                stats["deleted"] += 1
            except OSError:
                pass
        if progress:
            progress(k + 1, len(chosen), os.path.basename(it.path))
    # make sure the destination is watched, then scan it
    for f in stats["folders"]:
        if not any((f + os.sep).startswith(r["path"].rstrip(os.sep) + os.sep) and r["kind"] != "exclude"
                   for r in lib.roots()):
            lib.add_root(base_dir)
            break
    lib.scan()
    stats["folders"] = sorted(stats["folders"])
    return stats
