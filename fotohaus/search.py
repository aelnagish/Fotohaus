"""Search: keyword/metadata matching plus optional semantic (CLIP) ranking.

Query syntax (all terms are ANDed):
    beach dog                 words match filename, caption, tags, folder, album, people,
                              recognised objects/scenes and text found in the photo
    "new york"                exact phrase
    person:anna  album:trip   folder:2019  camera:iphone  text:invoice  tag:family
    year:2019  before:2020-05-01  after:2018
    is:starred  is:video  is:raw  has:faces  has:location  has:text  -word (exclude)
"""
from __future__ import annotations

import datetime as _dt
import re
import shlex

_TEXT_FIELDS = ("p.filename", "p.caption", "p.keywords", "p.camera", "p.ocr", "f.name", "f.path", "p.place_name")


def _date(s: str, end: bool = False) -> float | None:
    s = s.strip()
    for fmt, step in (("%Y-%m-%d", "d"), ("%Y-%m", "m"), ("%Y", "y")):
        try:
            d = _dt.datetime.strptime(s, fmt)
        except ValueError:
            continue
        if end:
            if step == "y":
                d = d.replace(year=d.year + 1)
            elif step == "m":
                d = (d.replace(day=28) + _dt.timedelta(days=4)).replace(day=1)
            else:
                d = d + _dt.timedelta(days=1)
        return d.timestamp()
    return None


def _word_clause(word: str) -> tuple[str, list]:
    like = f"%{word}%"
    parts = [f"{c} LIKE ?" for c in _TEXT_FIELDS]
    args = [like] * len(_TEXT_FIELDS)
    parts.append("EXISTS(SELECT 1 FROM labels l WHERE l.photo_id=p.id AND l.label LIKE ?)")
    args.append(like)
    parts.append("EXISTS(SELECT 1 FROM faces fc JOIN people pe ON pe.id=fc.person_id "
                 "WHERE fc.photo_id=p.id AND fc.confirmed=1 AND pe.name LIKE ?)")
    args.append(like)
    parts.append("EXISTS(SELECT 1 FROM album_photos ap JOIN albums a ON a.id=ap.album_id "
                 "WHERE ap.photo_id=p.id AND a.name LIKE ?)")
    args.append(like)
    return "(" + " OR ".join(parts) + ")", args


def build_query(q: str) -> tuple[str, list, list[str]]:
    """Return (where_sql, args, free_words)."""
    try:
        toks = shlex.split(q)
    except ValueError:
        toks = q.split()
    where, args, words = [], [], []
    for t in toks:
        neg = t.startswith("-") and len(t) > 1
        if neg:
            t = t[1:]
        key, sep, val = t.partition(":")
        key = key.lower()
        clause, cargs = None, []
        if sep and val:
            like = f"%{val}%"
            if key in ("person", "people", "who"):
                clause = ("EXISTS(SELECT 1 FROM faces fc JOIN people pe ON pe.id=fc.person_id "
                          "WHERE fc.photo_id=p.id AND fc.confirmed=1 AND pe.name LIKE ?)")
                cargs = [like]
            elif key == "album":
                clause = ("EXISTS(SELECT 1 FROM album_photos ap JOIN albums a ON a.id=ap.album_id "
                          "WHERE ap.photo_id=p.id AND a.name LIKE ?)")
                cargs = [like]
            elif key == "folder":
                clause, cargs = "(f.name LIKE ? OR f.path LIKE ?)", [like, like]
            elif key in ("place", "where", "in"):
                clause, cargs = "(p.place_name LIKE ? OR f.place LIKE ?)", [like, like]
            elif key == "camera":
                clause, cargs = "p.camera LIKE ?", [like]
            elif key in ("tag", "keyword"):
                clause, cargs = "p.keywords LIKE ?", [like]
            elif key == "text":
                clause, cargs = "p.ocr LIKE ?", [like]
            elif key in ("label", "thing"):
                clause = "EXISTS(SELECT 1 FROM labels l WHERE l.photo_id=p.id AND l.label LIKE ?)"
                cargs = [like]
            elif key == "file":
                clause, cargs = "p.filename LIKE ?", [like]
            elif key == "year" and val.isdigit():
                a, b = _date(val), _date(val, end=True)
                clause, cargs = "(p.taken >= ? AND p.taken < ?)", [a, b]
            elif key == "before":
                d = _date(val)
                if d:
                    clause, cargs = "p.taken < ?", [d]
            elif key == "after":
                d = _date(val, end=True) if re.fullmatch(r"\d{4}", val) else _date(val)
                if d:
                    clause, cargs = "p.taken >= ?", [d]
            elif key == "is":
                v = val.lower()
                clause = {"starred": "p.star=1", "star": "p.star=1", "video": "p.kind='video'",
                          "raw": "p.kind='raw'", "photo": "p.kind!='video'", "edited": "p.filters!=''",
                          "hidden": "p.hidden=1", "live": "p.stack_kind='live'",
                          "archived": "p.archived=1"}.get(v)
            elif key == "has":
                v = val.lower()
                clause = {"faces": "EXISTS(SELECT 1 FROM faces fc WHERE fc.photo_id=p.id AND fc.ignored=0)",
                          "people": "EXISTS(SELECT 1 FROM faces fc WHERE fc.photo_id=p.id AND fc.confirmed=1)",
                          "location": "p.lat IS NOT NULL", "gps": "p.lat IS NOT NULL",
                          "text": "p.ocr != ''", "caption": "p.caption != ''"}.get(v)
        if clause is None:
            if re.fullmatch(r"(19|20)\d\d", t):
                a, b = _date(t), _date(t, end=True)
                wc, wa = _word_clause(t)
                clause, cargs = f"((p.taken >= ? AND p.taken < ?) OR {wc})", [a, b] + wa
            else:
                clause, cargs = _word_clause(t)
                if not neg:
                    words.append(t)
        if neg:
            clause = f"NOT {clause}"
        where.append(clause)
        args.extend(cargs)
    return (" AND ".join(where) or "1"), args, words


def search(lib, q: str, limit: int = 5000, semantic: bool = True) -> list:
    q = (q or "").strip()
    if not q:
        return []
    where, args, words = build_query(q)
    rows = lib.db.q(
        f"SELECT p.* FROM photos p JOIN folders f ON f.id=p.folder_id WHERE {where} "
        f"{'AND p.hidden=0' if 'is:hidden' not in q else ''} "
        f"AND COALESCE(p.private,0)=0 AND p.deleted IS NULL "
        f"{'AND COALESCE(p.archived,0)=0' if 'is:archived' not in q else ''} "
        f"ORDER BY p.taken DESC LIMIT ?", args + [limit])
    rows = list(rows)
    if semantic and words and lib.settings.get("clip_enabled"):
        try:
            from .ai import clip
            if clip.available():
                seen = {r["id"] for r in rows}
                hits = clip.search_text(lib, " ".join(words), top_k=300)
                extra = [pid for pid, _ in hits if pid not in seen]
                rows += lib.photos_by_ids(extra)
        except Exception:
            pass
    return rows
