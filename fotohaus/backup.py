"""Back Up Pictures: incremental backup sets to another folder/drive, with restore."""
from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

MANIFEST = "fotohaus-backup.json"


def backup_sets(lib) -> list[dict]:
    return list(lib.settings.get("backup_sets") or [])


def save_backup_set(lib, bset: dict) -> None:
    sets = [s for s in backup_sets(lib) if s["name"] != bset["name"]]
    sets.append(bset)
    lib.settings.set("backup_sets", sets)


def delete_backup_set(lib, name: str) -> None:
    lib.settings.set("backup_sets", [s for s in backup_sets(lib) if s["name"] != name])


def _files_for(lib, bset: dict) -> list[tuple[str, str]]:
    """[(source path, path relative to the backup root)]"""
    out = []
    kinds = {"photo", "raw"} if bset.get("types") == "photos" else {"photo", "raw", "video"}
    folders = {lib.folder(fid)["path"] for fid in bset.get("folders", []) if lib.folder(fid)}
    rows = []
    for fp in folders:
        f = lib.db.one("SELECT id FROM folders WHERE path=?", (fp,))
        if f:
            rows += list(lib.photos_in_folder(f["id"], include_hidden=True))
    for aid in bset.get("albums", []):
        rows += list(lib.photos_in_album(aid))
    seen = set()
    for r in rows:
        if r["path"] in seen or r["kind"] not in kinds:
            continue
        seen.add(r["path"])
        rel = os.path.relpath(r["path"], "/").replace(":", "_")
        out.append((r["path"], rel))
        folder = os.path.dirname(r["path"])
        for extra in (os.path.join(folder, ".picasa.ini"), os.path.join(folder, ".picasaoriginals", r["filename"])):
            if os.path.exists(extra) and extra not in seen:
                seen.add(extra)
                out.append((extra, os.path.relpath(extra, "/").replace(":", "_")))
    return out


def run_backup(lib, bset: dict, progress=None, should_stop=None) -> dict:
    dest = Path(bset["dest"]) / bset["name"]
    dest.mkdir(parents=True, exist_ok=True)
    files = _files_for(lib, bset)
    stats = {"copied": 0, "skipped": 0, "bytes": 0, "errors": 0}
    manifest = {"name": bset["name"], "created": time.time(), "files": {}}
    for i, (src, rel) in enumerate(files):
        if should_stop and should_stop():
            break
        if progress:
            progress(i, len(files), os.path.basename(src))
        target = dest / rel
        try:
            st = os.stat(src)
            if target.exists():
                tst = target.stat()
                if tst.st_size == st.st_size and abs(tst.st_mtime - st.st_mtime) < 2:
                    stats["skipped"] += 1
                    manifest["files"][rel] = src
                    continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target)
            stats["copied"] += 1
            stats["bytes"] += st.st_size
            manifest["files"][rel] = src
        except OSError:
            stats["errors"] += 1
    (dest / MANIFEST).write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    bset["last_run"] = time.time()
    save_backup_set(lib, bset)
    return stats


def restore(backup_dir: str, only_missing: bool = True, progress=None) -> dict:
    """Copy files from a backup back to their original locations."""
    d = Path(backup_dir)
    man = json.loads((d / MANIFEST).read_text(encoding="utf-8"))
    stats = {"restored": 0, "skipped": 0}
    items = list(man["files"].items())
    for i, (rel, orig) in enumerate(items):
        if progress:
            progress(i, len(items), os.path.basename(orig))
        if only_missing and os.path.exists(orig):
            stats["skipped"] += 1
            continue
        src = d / rel
        if src.exists():
            os.makedirs(os.path.dirname(orig), exist_ok=True)
            shutil.copy2(src, orig)
            stats["restored"] += 1
    return stats
