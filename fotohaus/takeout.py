"""Import metadata from a Google Takeout (Google Photos) export.

Takeout puts a JSON sidecar next to each media file, e.g. ``IMG_1234.JPG.json`` or
``IMG_1234.JPG.supplemental-metadata.json`` (names may be truncated to ~47 chars).
We read title/description, taken time, geo data, favourites and people, and each
non-date folder becomes an album.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from . import media

_YEAR_FOLDER = re.compile(r"^(Photos from \d{4}|Fotos von \d{4}|Photos de \d{4}|Fotos de \d{4})$", re.I)


def _sidecar_for(media_path: Path, jsons: dict[str, Path]) -> Path | None:
    name = media_path.name
    for cand in (name + ".json", name + ".supplemental-metadata.json"):
        if cand in jsons:
            return jsons[cand]
    # edited copies: "IMG_1234-edited.jpg" -> "IMG_1234.jpg.json"
    base = re.sub(r"-(edited|bearbeitet|modifié)(\.\w+)$", r"\2", name)
    if base != name and base + ".json" in jsons:
        return jsons[base + ".json"]
    # duplicate numbering: "IMG(1).jpg" -> "IMG.jpg(1).json"
    m = re.match(r"^(.*)\((\d+)\)(\.\w+)$", name)
    if m:
        alt = f"{m.group(1)}{m.group(3)}({m.group(2)}).json"
        if alt in jsons:
            return jsons[alt]
    # truncated sidecar names
    for k, p in jsons.items():
        stem = k.split(".json")[0].split(".supplemental")[0]
        if len(stem) >= 30 and name.startswith(stem[:30]) and (name[:46] in k or stem in name):
            return p
    return None


def import_takeout(lib, root: str, progress=None, should_stop=None) -> dict:
    """Add `root` to the library (if needed), scan it, then apply Takeout sidecars."""
    root = os.path.abspath(root)
    rid = lib.add_root(root)
    lib.scan(progress=lambda p, n: progress and progress(f"Scanning {os.path.basename(p)}"),
             should_stop=should_stop, root_ids=[rid])
    stats = {"photos": 0, "albums": 0, "favorites": 0, "captions": 0, "places": 0}
    for dirpath, dirnames, filenames in os.walk(root):
        if should_stop and should_stop():
            break
        jsons = {f: Path(dirpath) / f for f in filenames if f.lower().endswith(".json")}
        if not jsons:
            continue
        folder = Path(dirpath)
        album_id = None
        meta_json = jsons.get("metadata.json") or jsons.get("Metadaten.json")
        if not _YEAR_FOLDER.match(folder.name) and folder.name not in ("Google Photos", "Google Fotos", "Takeout"):
            title = folder.name
            if meta_json:
                try:
                    title = json.loads(meta_json.read_text("utf-8")).get("title") or title
                except (OSError, ValueError):
                    pass
            existing = lib.album_by_name(title)
            album_id = existing["id"] if existing else lib.create_album(title)
            if not existing:
                stats["albums"] += 1
        members = []
        for fn in filenames:
            if not media.kind_of(fn):
                continue
            mp = folder / fn
            row = lib.photo_by_path(str(mp))
            if row is None:
                continue
            members.append(row["id"])
            sc = _sidecar_for(mp, jsons)
            if not sc:
                continue
            try:
                data = json.loads(sc.read_text("utf-8"))
            except (OSError, ValueError):
                continue
            stats["photos"] += 1
            desc = (data.get("description") or "").strip()
            if desc and not row["caption"]:
                lib.set_caption(row["id"], desc)
                stats["captions"] += 1
            if data.get("favorited"):
                lib.set_star([row["id"]], True)
                stats["favorites"] += 1
            ts = (data.get("photoTakenTime") or {}).get("timestamp")
            if ts:
                try:
                    t = float(ts)
                    # trust EXIF when present; Takeout time is UTC-based and the file mtime is the export date
                    if not row["camera"]:
                        lib.db.x("UPDATE photos SET taken=? WHERE id=?", (t, row["id"]))
                except ValueError:
                    pass
            geo = data.get("geoDataExif") or {}
            if not geo.get("latitude"):
                geo = data.get("geoData") or {}
            lat, lon = geo.get("latitude"), geo.get("longitude")
            if lat and lon and row["lat"] is None and not (lat == 0 and lon == 0):
                lib.set_location([row["id"]], float(lat), float(lon), write_exif=False)
                stats["places"] += 1
            people = [p.get("name") for p in data.get("people") or [] if p.get("name")]
            for name in people:
                lib.add_keyword([row["id"]], name)
        if album_id and members:
            lib.add_to_album(album_id, members)
        if progress:
            progress(f"Applying Google Photos info: {folder.name}")
    lib.emit("library")
    return stats
