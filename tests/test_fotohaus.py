"""Functional tests. Run:  FOTOHAUS_HOME=/tmp/x python -m pytest tests  (or python tests/test_fotohaus.py)"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("FOTOHAUS_HOME", tempfile.mkdtemp(prefix="fotohaus-test-"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

from fotohaus.edits import FILTERS, apply_filter, render  # noqa: E402
from fotohaus.picasa.ini import (Filter, PicasaIni, decode_rect64, encode_rect64, parse_filters,  # noqa: E402
                                serialize_filters)


def _img(w=320, h=240, seed=1):
    rng = np.random.default_rng(seed)
    a = np.zeros((h, w, 3), np.uint8)
    a[..., 0] = np.linspace(0, 255, w)[None, :]
    a[..., 1] = np.linspace(0, 255, h)[:, None]
    a[..., 2] = 120
    a = np.clip(a + rng.integers(0, 30, a.shape), 0, 255).astype(np.uint8)
    return Image.fromarray(a)


def test_rect64_roundtrip():
    l, t, r, b = decode_rect64("3f845bcb59418507")
    assert abs(l - 0.248) < 0.001 and abs(t - 0.359) < 0.001 and abs(r - 0.349) < 0.001 and abs(b - 0.520) < 0.001
    assert decode_rect64(encode_rect64(0.1, 0.2, 0.3, 0.4)) == decode_rect64(encode_rect64(0.1, 0.2, 0.3, 0.4))
    assert decode_rect64("5bcb59418507")[0] == 0.0  # leading zeros stripped by Picasa


def test_filter_strings():
    s = "crop64=1,45930000ba03defe;enhance=1;finetune2=1,0.200000,0.100000,0.050000,00000000,0.300000;"
    fs = parse_filters(s)
    assert [f.id for f in fs] == ["crop64", "enhance", "finetune2"]
    assert serialize_filters(fs) == s


def test_ini_roundtrip_preserves_unknown(tmp_path=None):
    d = Path(tempfile.mkdtemp())
    (d / ".picasa.ini").write_text("[Picasa]\r\nname=Trip\r\nweird=keep me\r\n[a.jpg]\r\nstar=yes\r\n"
                                   "moddate=abc\r\n[.album:tok1]\r\nname=Alb\r\ntoken=tok1\r\n", encoding="utf-8")
    ini = PicasaIni(d)
    assert ini.star("A.JPG") and ini.folder_name == "Trip"
    ini.set_filters("a.jpg", [Filter.make("enhance"), Filter.make("fh_clarity", 0.4), Filter.make("sepia")])
    ini.set("a.jpg", "caption", "hello")
    ini.save()
    text = (d / ".picasa.ini").read_bytes().decode()
    assert "weird=keep me" in text and "moddate=abc" in text and "\r\n" in text
    assert "filters=enhance=1;sepia=1;" in text  # Picasa sees only its own filters
    ini2 = PicasaIni(d)
    assert [f.id for f in ini2.filters("a.jpg")] == ["enhance", "fh_clarity", "sepia"]  # order restored
    # Picasa edits the chain behind our back -> keep theirs, re-append ours
    ini2.set("a.jpg", "filters", "bw=1;")
    assert [f.id for f in ini2.filters("a.jpg")] == ["bw", "fh_clarity"]
    assert "tok1" in ini2.albums()


def test_all_filters_render():
    im = _img()
    for fid, info in FILTERS.items():
        args = [p[3] for p in info.params]
        if fid in ("radblur", "radsat", "dir_tint"):
            args = [0.5, 0.5] + args
        if fid == "crop64":
            args = [encode_rect64(0.1, 0.1, 0.9, 0.8)]
        if fid == "tilt":
            args = [0.3, 0.0]
        out = apply_filter(im, Filter.make(fid, *args))
        assert out.mode == "RGB" and out.size[0] > 10, fid
        a = np.asarray(out)
        assert np.isfinite(a).all(), fid
    out = render(im, [Filter("crop64", [encode_rect64(0, 0, 0.5, 0.5)])], rotate=1)
    assert out.size == (120, 160)


def _make_library():
    from fotohaus.library import Library
    import piexif
    root = Path(tempfile.mkdtemp(prefix="lib-"))
    (root / "A").mkdir()
    (root / "B").mkdir()
    exif = piexif.dump({"0th": {piexif.ImageIFD.Make: b"Apple", piexif.ImageIFD.Model: b"iPhone 15"},
                        "Exif": {piexif.ExifIFD.DateTimeOriginal: b"2020:01:02 03:04:05"}, "GPS": {}})
    _img(640, 480, 1).save(root / "A" / "one.jpg", quality=90, exif=exif)
    _img(640, 480, 2).save(root / "A" / "two.png")
    try:
        _img(400, 300, 3).save(root / "B" / "three.heic")
    except Exception:
        pass
    import cv2
    vw = cv2.VideoWriter(str(root / "B" / "clip.mp4"), cv2.VideoWriter_fourcc(*"mp4v"), 10, (160, 120))
    for i in range(20):
        vw.write(np.full((120, 160, 3), i * 10, np.uint8))
    vw.release()
    db = Path(tempfile.mkdtemp()) / "lib.db"
    lib = Library(db)
    lib.add_root(str(root))
    stats = lib.scan()
    return lib, root, stats


def test_scan_edit_save_revert_export():
    from fotohaus.exporter import ExportOptions, export_photo, make_collage
    lib, root, stats = _make_library()
    kinds = {r["filename"]: r["kind"] for r in lib.all_photos()}
    assert kinds["one.jpg"] == "photo" and kinds["clip.mp4"] == "video"
    one = lib.photo_by_path(str(root / "A" / "one.jpg"))
    assert "iPhone 15" in one["camera"] and one["taken"]
    vid = lib.photo_by_path(str(root / "B" / "clip.mp4"))
    assert vid["duration"] and vid["duration"] > 1.5
    # metadata writes go to .picasa.ini
    lib.set_star([one["id"]], True)
    lib.set_caption(one["id"], "First")
    aid = lib.create_album("Holiday")
    lib.add_to_album(aid, [one["id"]])
    ini = PicasaIni(root / "A")
    assert ini.star("one.jpg") and ini.caption("one.jpg") == "First"
    tok = lib.album(aid)["token"]
    assert ini.photo_albums("one.jpg") == [tok] and ini.albums()[tok]["name"] == "Holiday"
    # edits
    lib.set_filters(one["id"], [Filter.make("crop64", encode_rect64(0, 0, 0.5, 0.5)), Filter.make("bw")])
    lib.save_edits(one["id"])
    r = lib.photo(one["id"])
    assert r["filters"] == "" and (root / "A" / ".picasaoriginals" / "one.jpg").exists()
    assert Image.open(r["path"]).size == (320, 240)
    assert lib.revert_saved(one["id"])
    assert Image.open(r["path"]).size == (640, 480)
    # export
    out = Path(tempfile.mkdtemp())
    p = export_photo(lib.photo(one["id"]), ExportOptions(dest=str(out), max_size=200, watermark="© Me"))
    assert Image.open(p).size[0] == 200
    pv = export_photo(lib.photo(vid["id"]), ExportOptions(dest=str(out)))
    assert pv.exists()
    c = make_collage(lib.all_photos(), str(out / "c.jpg"), 600, 400, "mosaic")
    assert Image.open(c).size == (600, 400)
    for lay in ("grid", "pile"):
        make_collage(lib.all_photos(), str(out / f"{lay}.jpg"), 600, 400, lay)
    # search
    from fotohaus.search import search
    assert [x["filename"] for x in search(lib, "first")] == ["one.jpg"]
    assert [x["filename"] for x in search(lib, "album:holiday")] == ["one.jpg"]
    assert {x["filename"] for x in search(lib, "is:video")} == {"clip.mp4"}
    assert [x["filename"] for x in search(lib, "camera:iphone year:2020")] == ["one.jpg"]


def test_faces_named_written_to_ini():
    from skimage import data
    from fotohaus.ai.indexer import Indexer
    from fotohaus.library import Library
    root = Path(tempfile.mkdtemp(prefix="faces-"))
    Image.fromarray(data.astronaut()).save(root / "a.jpg", quality=92)
    Image.fromarray(np.ascontiguousarray(data.astronaut()[:, ::-1])).save(root / "b.jpg", quality=92)
    lib = Library(Path(tempfile.mkdtemp()) / "lib.db")
    lib.add_root(str(root))
    lib.scan()
    Indexer(lib).run()
    faces = lib.db.q("SELECT * FROM faces")
    assert len(faces) == 2
    clusters = lib.unnamed_clusters(min_size=1)
    assert len(clusters) == 1 and clusters[0]["n"] == 2  # same person clustered together
    a = lib.photo_by_path(str(root / "a.jpg"))
    fa = [f for f in faces if f["photo_id"] == a["id"]][0]
    lib.name_faces([fa["id"]], "Eileen")
    ini = PicasaIni(root)
    (rect, cid), = ini.faces("a.jpg")
    assert ini.contacts()[cid] == "Eileen"
    from fotohaus.ai.faces import update_suggestions_and_clusters
    update_suggestions_and_clusters(lib)
    b = lib.photo_by_path(str(root / "b.jpg"))
    fb = lib.db.one("SELECT * FROM faces WHERE photo_id=?", (b["id"],))
    assert fb["person_id"] == lib.person_by_name("Eileen")["id"] and fb["confirmed"] == 0  # suggested


def test_takeout():
    from fotohaus.library import Library
    from fotohaus.takeout import import_takeout
    root = Path(tempfile.mkdtemp(prefix="takeout-"))
    alb = root / "Takeout" / "Google Photos" / "Paris 2018"
    alb.mkdir(parents=True)
    _img().save(alb / "IMG_1.jpg")
    (alb / "IMG_1.jpg.supplemental-metadata.json").write_text(json.dumps({
        "title": "IMG_1.jpg", "description": "Eiffel tower", "favorited": True,
        "photoTakenTime": {"timestamp": "1530000000"},
        "geoData": {"latitude": 48.8584, "longitude": 2.2945}, "people": [{"name": "Anna"}]}))
    lib = Library(Path(tempfile.mkdtemp()) / "lib.db")
    st = import_takeout(lib, str(root))
    r = lib.photo_by_path(str(alb / "IMG_1.jpg"))
    assert r["caption"] == "Eiffel tower" and r["star"] == 1 and abs(r["lat"] - 48.8584) < 1e-6
    assert "Anna" in r["keywords"] and lib.album_by_name("Paris 2018") is not None
    assert st["albums"] == 1


def test_mcp_server():
    env = dict(os.environ)
    p = subprocess.run([sys.executable, "-m", "fotohaus.cli", "mcp"], cwd=str(ROOT), env=env, text=True,
                       capture_output=True, timeout=60,
                       input="\n".join(json.dumps(m) for m in [
                           {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
                           {"jsonrpc": "2.0", "method": "notifications/initialized"},
                           {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                           {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                            "params": {"name": "library_stats", "arguments": {}}},
                       ]) + "\n")
    lines = [json.loads(x) for x in p.stdout.strip().splitlines()]
    assert lines[0]["result"]["serverInfo"]["name"] == "fotohaus"
    assert any(t["name"] == "search_photos" for t in lines[1]["result"]["tools"])
    assert "photos" in json.loads(lines[2]["result"]["content"][0]["text"])


def test_rescan_picks_up_new_pictures():
    """New files in a watched folder appear on rescan; "scan once" folders need a manual rescan."""
    from PIL import Image
    lib, root, _ = _make_library()
    before = lib.counts()["photos"]
    Image.new("RGB", (64, 48), (10, 120, 200)).save(root / "A" / "added_later.jpg")
    lib.scan()
    assert lib.photo_by_path(str(root / "A" / "added_later.jpg")) is not None
    assert lib.counts()["photos"] == before + 1
    # a folder that was only opened once (e.g. a photo double-clicked in Finder)
    once = root.parent / (root.name + "_once")
    once.mkdir()
    Image.new("RGB", (64, 48), (200, 20, 20)).save(once / "first.jpg")
    rid = lib.add_root(str(once), kind="once")
    lib.scan(root_ids=[rid])
    Image.new("RGB", (64, 48), (20, 200, 20)).save(once / "second.jpg")
    lib.scan()
    assert lib.photo_by_path(str(once / "second.jpg")) is None          # automatic scans skip it
    lib.scan(include_once=True)
    assert lib.photo_by_path(str(once / "second.jpg")) is not None      # Rescan Now finds it


def test_fileops_and_metadata():
    from fotohaus import fileops
    from fotohaus.picasa.ini import PicasaIni
    lib, root, _ = _make_library()
    lib.settings.set("write_meta_in_files", True)
    one = lib.photo_by_path(str(root / "A" / "one.jpg"))
    lib.set_star([one["id"]], True)
    lib.set_caption(one["id"], "Caption in file")
    lib.set_keywords(one["id"], ["alpha", "beta"])
    from fotohaus import media
    md = media.read_metadata(str(root / "A" / "one.jpg"))
    assert md["caption"] == "Caption in file" and md["keywords"] == ["alpha", "beta"]
    assert lib.photo(one["id"])["mtime"] == os.stat(root / "A" / "one.jpg").st_mtime  # no rescan churn
    # move to another folder carries the ini section
    fileops.move_photos(lib, [one["id"]], str(root / "B"))
    moved = lib.photo(one["id"])
    assert moved["path"] == str(root / "B" / "one.jpg")
    assert PicasaIni(root / "B").star("one.jpg") and PicasaIni(root / "B").caption("one.jpg") == "Caption in file"
    assert not PicasaIni(root / "A").section("one.jpg")
    # rename + batch rename
    fileops.rename_photo(lib, one["id"], "renamed")
    assert lib.photo(one["id"])["filename"] == "renamed.jpg" and PicasaIni(root / "B").star("renamed.jpg")
    ids = [r["id"] for r in lib.photos_in_folder(lib.photo(one["id"])["folder_id"])]
    fileops.batch_rename(lib, ids, "Trip")
    names = sorted(r["filename"] for r in lib.photos_by_ids(ids))
    assert all(n.startswith("Trip-") for n in names), names
    # adjust dates (+1 day) writes EXIF for JPEGs
    jpg = [r for r in lib.photos_by_ids(ids) if r["filename"].endswith(".jpg")][0]
    before = jpg["taken"]
    fileops.adjust_dates(lib, [jpg["id"]], delta_seconds=86400)
    after = media.read_metadata(lib.photo(jpg["id"])["path"])["taken"]
    assert abs(after - before - 86400) < 2
    # folder rename / new folder / split / hide / remove
    fid = lib.photo(jpg["id"])["folder_id"]
    newp = fileops.rename_folder(lib, fid, "B renamed")
    assert lib.photo(jpg["id"])["path"].startswith(newp)
    two = lib.photo_by_path(str(root / "A" / "two.png"))
    p2 = fileops.move_to_new_folder(lib, [two["id"]], "Moved", parent=str(root), description="desc", place="Aachen")
    assert lib.photo(two["id"])["path"].startswith(p2) and PicasaIni(p2).get("Picasa", "location") == "Aachen"
    fid2 = lib.photo(two["id"])["folder_id"]
    fileops.remove_folder_from_library(lib, fid2)
    lib.scan()
    assert lib.photo_by_path(lib.photo(two["id"])["path"] if lib.photo(two["id"]) else "x") is None
    assert lib.folder_mode(p2) == ("exclude", False)


def test_backup_restore_and_import():
    from fotohaus import backup, importer
    lib, root, _ = _make_library()
    dest = Path(tempfile.mkdtemp())
    fids = [f["id"] for f in lib.folders(include_hidden=True)]
    bset = {"name": "Set1", "dest": str(dest), "types": "all", "folders": fids, "albums": []}
    st = backup.run_backup(lib, bset)
    assert st["copied"] >= 3
    st2 = backup.run_backup(lib, bset)
    assert st2["copied"] == 0 and st2["skipped"] >= 3     # incremental
    victim = root / "A" / "two.png"
    victim.unlink()
    rs = backup.restore(str(dest / "Set1"))
    assert victim.exists() and rs["restored"] == 1
    # import from a "card" folder: one duplicate, one new
    card = Path(tempfile.mkdtemp()) / "DCIM" / "100CANON"
    card.mkdir(parents=True)
    shutil.copy2(root / "A" / "one.jpg", card / "one.jpg")
    _img(200, 150, 9).save(card / "new.jpg")
    items = importer.scan_source(lib, str(card.parent.parent))
    dup = {Path(i.path).name: i.duplicate for i in items}
    assert dup == {"one.jpg": True, "new.jpg": False}
    out = Path(tempfile.mkdtemp())
    stats = importer.do_import(lib, items, str(out), "Imported")
    assert stats["copied"] == 1 and lib.photo_by_path(str(out / "Imported" / "new.jpg")) is not None


def test_create_outputs():
    from fotohaus import exporter as E
    from fotohaus.movie import MovieOptions, make_movie
    lib, root, _ = _make_library()
    rows = list(lib.all_photos())
    out = Path(tempfile.mkdtemp())
    for lay in E.COLLAGE_TYPES:
        E.make_collage(rows, str(out / f"{lay}.jpg"), 600, 400, lay, border="shadow", captions=True)
        assert Image.open(out / f"{lay}.jpg").size == (600, 400)
    idx = E.export_html(rows, str(out / "web"), "T", "captions")
    assert idx.exists() and (out / "web" / "0001.html").exists()
    assert E.export_kml(rows, str(out / "a.kml")) == 0
    pdf = E.make_poster(rows[0], str(out / "poster"), 2)
    assert pdf.exists()
    mv = make_movie(rows, MovieOptions(out_path=str(out / "m.mp4"), size=(320, 180), fps=10, slide_seconds=0.5,
                                       transition="dissolve", title="Hi", title_seconds=0.5))
    import av
    with av.open(mv) as c:
        assert c.streams.video[0].frames > 5 or c.duration > 0


def test_viewer_helpers():
    from fotohaus.library import Library
    from fotohaus.picasa.ini import PicasaIni
    lib = Library(Path(tempfile.mkdtemp()) / "lib.db")
    d = Path(tempfile.mkdtemp())
    _img().save(d / "loose.jpg")
    info = lib.describe_file(str(d / "loose.jpg"))
    assert info["id"] is None and info["kind"] == "photo"
    lib.set_star_path(str(d / "loose.jpg"), True)
    lib.rotate_path(str(d / "loose.jpg"), 1)
    ini = PicasaIni(d)
    assert ini.star("loose.jpg") and ini.rotate("loose.jpg") == 1
    r = lib.ensure_in_library(str(d / "loose.jpg"))
    assert r is not None and r["star"] == 1 and r["rotate"] == 1


def test_memories_archive_private_deleted_stacks_places():
    import datetime as dt
    import time as _time
    import piexif
    import cv2
    from fotohaus.library import Library
    from fotohaus import geocode
    root = Path(tempfile.mkdtemp(prefix="lib2-"))
    (root / "Trip").mkdir()
    today = dt.date.today()
    last_year = today.replace(year=today.year - 2, day=min(today.day, 28))

    def shot(name, when, seed, gps=None, camera=b"iPhone 15"):
        ex = {"0th": {piexif.ImageIFD.Make: b"Apple", piexif.ImageIFD.Model: camera},
              "Exif": {piexif.ExifIFD.DateTimeOriginal: when.strftime("%Y:%m:%d %H:%M:%S").encode()}, "GPS": {}}
        if gps:
            def dms(v):
                v = abs(v); d = int(v); m = int((v - d) * 60); s_ = round(((v - d) * 60 - m) * 60 * 100)
                return ((d, 1), (m, 1), (s_, 100))
            ex["GPS"] = {piexif.GPSIFD.GPSLatitudeRef: b"N", piexif.GPSIFD.GPSLatitude: dms(gps[0]),
                         piexif.GPSIFD.GPSLongitudeRef: b"E", piexif.GPSIFD.GPSLongitude: dms(gps[1])}
        _img(320, 240, seed).save(root / "Trip" / name, quality=88, exif=piexif.dump(ex))

    base = dt.datetime.combine(last_year, dt.time(12, 0, 0))
    shot("memory.jpg", base, 1, gps=(46.0207, 7.7491))
    shot("live.jpg", base + dt.timedelta(hours=1), 2)
    vw = cv2.VideoWriter(str(root / "Trip" / "live.mov"), cv2.VideoWriter_fourcc(*"mp4v"), 10, (160, 120))
    for i in range(15):
        vw.write(np.full((120, 160, 3), i * 10, np.uint8))
    vw.release()
    for i in range(4):                                   # a burst: 4 shots 0.5 s apart
        shot(f"burst_{i}.jpg", base + dt.timedelta(hours=2, seconds=i * 0.5), 10 + i, camera=b"Burst Cam")
    shot("old.jpg", base - dt.timedelta(days=60), 5)
    lib = Library(Path(tempfile.mkdtemp()) / "lib.db")
    lib.add_root(str(root))
    lib.scan()
    names = {r["filename"]: r for r in lib.db.q("SELECT * FROM photos")}
    # stacks: live.mov is hidden behind live.jpg, the burst collapses to one cover with a count of 4
    assert names["live.jpg"]["live_video"].endswith("live.mov") and names["live.mov"]["stack_member"] == 1
    covers = [r for r in names.values() if r["stack_kind"] == "burst" and r["stack_member"] == 0]
    assert len(covers) == 1 and covers[0]["stack_count"] == 4
    visible = {r["filename"] for r in lib.all_photos()}
    assert "live.mov" not in visible and sum(f.startswith("burst_") for f in visible) == 1
    assert len(lib.stack_members(covers[0]["stack_key"])) == 4
    # memories: everything from around this day two years ago, not the photo two months earlier
    mem = {r["filename"] for r in lib.memories()}
    assert "memory.jpg" in mem and "old.jpg" not in mem
    # places, offline
    assert geocode.label(46.0207, 7.7491) == "Zermatt, Switzerland"
    assert "Zermatt" in (lib.photo(names["memory.jpg"]["id"])["place_name"] or "")
    from fotohaus.search import search
    assert [r["filename"] for r in search(lib, "place:zermatt", semantic=False)] == ["memory.jpg"]
    # archive / private / recently deleted
    mid, oid = names["memory.jpg"]["id"], names["old.jpg"]["id"]
    lib.set_archived([mid], True)
    assert mid not in {r["id"] for r in lib.all_photos()} and mid in {r["id"] for r in lib.archived_photos()}
    lib.set_archived([mid], False)
    lib.set_private([mid], True)
    assert mid not in {r["id"] for r in search(lib, "zermatt", semantic=False)}
    assert mid in {r["id"] for r in lib.private_photos()}
    lib.soft_delete([oid])
    assert os.path.exists(names["old.jpg"]["path"]) and oid in {r["id"] for r in lib.deleted_photos()}
    assert oid not in {r["id"] for r in lib.all_photos()}
    lib.restore_deleted([oid])
    assert oid in {r["id"] for r in lib.all_photos()}
    lib.soft_delete([oid])
    lib.db.x("UPDATE photos SET deleted=? WHERE id=?", (_time.time() - 31 * 86400, oid))
    assert lib.purge_deleted(older_than_days=30) == 1 and not os.path.exists(names["old.jpg"]["path"])
    # nested tags: a parent tag finds its children
    lib.add_keyword([names["live.jpg"]["id"]], "Trips/2024/Alps")
    assert [r["filename"] for r in search(lib, "tag:Trips/2024", semantic=False)] == ["live.jpg"]


def test_private_password():
    from fotohaus import macos
    from fotohaus.paths import Settings
    st = Settings(Path(tempfile.mkdtemp()) / "settings.json")
    assert not macos.private_password_set(st)
    assert not macos.check_private_password(st, "anything")
    macos.set_private_password(st, "saffron")
    assert macos.private_password_set(st)
    assert macos.check_private_password(st, "saffron")
    assert not macos.check_private_password(st, "Saffron") and not macos.check_private_password(st, "")
    assert "saffron" not in json.dumps(st.get("private_password"))     # only a salted hash is kept
    # off macOS/Windows a refused password keeps Private shut (it used to open without asking)
    if sys.platform not in ("darwin", "win32"):
        saved = macos._password_prompt
        macos._password_prompt = lambda reason: False
        try:
            assert macos.authenticate("show your private photos") is False
        finally:
            macos._password_prompt = saved


def test_single_instance_and_viewer_reuse():
    """A second launch hands its photo to the running Fotohaus; the open viewer shows it (or a new
    viewer opens when that option is off)."""
    import time
    home = tempfile.mkdtemp(prefix="fotohaus-instance-")
    pics = Path(tempfile.mkdtemp(prefix="fotohaus-pics-"))
    a, b = pics / "a.jpg", pics / "b.jpg"
    _img(seed=3).save(a)
    _img(seed=4).save(b)

    def run(reuse: bool) -> str:
        data = Path(home) / ("reuse" if reuse else "new") / "data"
        data.mkdir(parents=True)
        (data / "settings.json").write_text(json.dumps({"viewer_reuse": reuse, "first_run_done": True}))
        env = dict(os.environ, FOTOHAUS_HOME=str(data.parent), QT_QPA_PLATFORM="offscreen",
                   FOTOHAUS_AUTOQUIT_MS="9000")
        first = subprocess.Popen([sys.executable, "-m", "fotohaus", str(a)], cwd=ROOT, env=env,
                                 stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        time.sleep(4)                                   # let the first copy start listening
        env2 = dict(env)
        env2.pop("FOTOHAUS_AUTOQUIT_MS")
        t = time.time()
        second = subprocess.run([sys.executable, "-m", "fotohaus", str(b)], cwd=ROOT, env=env2,
                                capture_output=True, text=True, timeout=30)
        assert second.returncode == 0, second.stderr
        assert time.time() - t < 15                     # handed over and quit, did not open a window
        out, _ = first.communicate(timeout=60)
        return out

    out = run(reuse=True)
    assert "FOTOHAUS_AUTOQUIT ok: viewers=1 " in out, out
    out = run(reuse=False)
    assert "FOTOHAUS_AUTOQUIT ok: viewers=2 " in out, out


def test_items_around_ignores_case_where_the_os_does():
    from fotohaus.ui.viewer import _items_around
    d = Path(tempfile.mkdtemp())
    for n in ("img_1.jpg", "img_2.jpg", "img_10.jpg"):
        _img(32, 24).save(d / n)
    items, i = _items_around(str(d / "img_2.jpg"))
    assert [os.path.basename(p) for p in items] == ["img_1.jpg", "img_2.jpg", "img_10.jpg"] and i == 1
    items, i = _items_around(str(d / "IMG_2.JPG"))
    if os.path.normcase("A") == "a":                    # Windows: same file, found in the folder
        assert len(items) == 3 and i == 1
    else:                                               # case-sensitive: a different (missing) name
        assert i == 0 and len(items) == 4


def test_platform_wording_and_linux_desktop_entry():
    from fotohaus import macos
    if sys.platform != "darwin":
        assert macos.keys("Archive (⇧⌘A)") == "Archive (Ctrl+Shift+A)"
        assert macos.keys("⌘⌫") == "Ctrl+Backspace" and macos.keys("⌥⌘A") == "Ctrl+Alt+A"
        assert "Finder" not in macos.SHOW_IN and not macos.HAS_SCREENSAVER_FOLDER
    else:
        assert macos.keys("⇧⌘A") == "⇧⌘A"
    if sys.platform.startswith("linux"):
        share = Path(tempfile.mkdtemp())
        saved = {k: os.environ.get(k) for k in ("XDG_DATA_HOME", "APPIMAGE")}
        os.environ["XDG_DATA_HOME"] = str(share)
        os.environ["APPIMAGE"] = "/opt/My Apps/Fotohaus.AppImage"
        try:
            macos.install_linux_desktop_entry(make_default=False)
        finally:
            for k, v in saved.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        entry = (share / "applications" / "fotohaus.desktop").read_text()
        assert "Exec='/opt/My Apps/Fotohaus.AppImage' %F" in entry
        assert "image/jpeg;" in entry and "Icon=fotohaus" in entry
        assert (share / "icons" / "hicolor" / "256x256" / "apps" / "fotohaus.png").exists()


if __name__ == "__main__":
    fails = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS", name)
            except Exception:
                import traceback
                traceback.print_exc()
                print("FAIL", name)
                fails += 1
    sys.exit(1 if fails else 0)
