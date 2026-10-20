"""Regression tests for the October 2026 code review (data safety, privacy, robustness) and a full
click-through of every window. Run:  python tests/test_review.py"""
import os
import stat
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("FOTOHAUS_HOME", tempfile.mkdtemp(prefix="fotohaus-review-"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FOTOHAUS_NO_AUDIO", "1")

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

from fotohaus.library import Library  # noqa: E402


def _jpg(path, w=400, h=300, seed=1, exif_date=None):
    rng = np.random.default_rng(seed)
    a = rng.integers(0, 255, (h, w, 3), dtype=np.uint8)
    im = Image.fromarray(a)
    kw = {}
    if exif_date:
        ex = Image.Exif()
        ex.get_ifd(0x8769)[36867] = exif_date
        kw["exif"] = ex.tobytes()
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path, "JPEG", quality=85, **kw)
    return path


def _lib():
    root = Path(tempfile.mkdtemp())
    lib = Library(Path(tempfile.mkdtemp()) / "lib.db")
    return lib, root


# ---------------------------------------------------------------- folder names with wildcards

def test_rename_folder_does_not_touch_lookalike_siblings():
    """'2019_trip' must not match '2019-trip' (LIKE treats _ as a wildcard)."""
    from fotohaus import fileops
    lib, root = _lib()
    _jpg(root / "2019_trip" / "a.jpg")
    _jpg(root / "2019-trip" / "b.jpg", seed=2)
    lib.add_root(str(root))
    lib.scan()
    fid = lib.db.scalar("SELECT id FROM folders WHERE path=?", (str(root / "2019_trip"),))
    fileops.rename_folder(lib, fid, "Renamed")
    b = lib.db.one("SELECT path FROM photos WHERE filename='b.jpg'")
    assert b["path"] == str(root / "2019-trip" / "b.jpg"), b["path"]
    a = lib.db.one("SELECT path FROM photos WHERE filename='a.jpg'")
    assert a["path"] == str(root / "Renamed" / "a.jpg")
    assert os.path.exists(a["path"]) and os.path.exists(b["path"])


def test_names_with_slash_are_refused():
    from fotohaus import fileops
    lib, root = _lib()
    _jpg(root / "F" / "a.jpg")
    lib.add_root(str(root))
    lib.scan()
    fid = lib.db.scalar("SELECT id FROM folders")
    for bad in ("x/y", "", "..", "."):
        try:
            fileops.rename_folder(lib, fid, bad)
        except ValueError:
            continue
        raise AssertionError(f"accepted {bad!r}")
    pid = lib.db.scalar("SELECT id FROM photos")
    try:
        fileops.rename_photo(lib, pid, "a/b")
        raise AssertionError("accepted a/b")
    except ValueError:
        pass
    out = fileops.batch_rename(lib, [pid], "Trip/2024")
    assert os.path.basename(out[0]) == "Trip-2024.jpg" and os.path.exists(out[0])


# ---------------------------------------------------------------- scanning never forgets unreadable folders

def test_unreadable_folder_keeps_its_data():
    if os.geteuid() == 0:
        print("  (skipped as root: permissions are not enforced)")
        return
    lib, root = _lib()
    _jpg(root / "Locked" / "a.jpg")
    lib.add_root(str(root))
    lib.scan()
    pid = lib.db.scalar("SELECT id FROM photos")
    lib.set_star([pid], True)
    aid = lib.create_album("Keep")
    lib.add_to_album(aid, [pid])
    os.chmod(root / "Locked", 0)
    try:
        lib.scan(include_once=True)
        r = lib.photo(pid)
        assert r is not None and r["star"] == 1
        assert [x["id"] for x in lib.photos_in_album(aid)] == [pid]
    finally:
        os.chmod(root / "Locked", stat.S_IRWXU)


def test_removed_folder_is_still_forgotten():
    import shutil
    lib, root = _lib()
    _jpg(root / "Gone" / "a.jpg")
    _jpg(root / "Stays" / "b.jpg", seed=3)
    lib.add_root(str(root))
    lib.scan()
    shutil.rmtree(root / "Gone")
    st = lib.scan()
    assert st["removed"] >= 1
    assert [r["filename"] for r in lib.all_photos()] == ["b.jpg"]


# ---------------------------------------------------------------- stacks: rescans don't rewrite every row

def test_rebuild_stacks_only_writes_changes():
    lib, root = _lib()
    for i in range(3):
        _jpg(root / "S" / f"IMG_{i}.jpg", seed=i)
    (root / "S" / "IMG_0.dng").write_bytes(b"not really raw")
    lib.add_root(str(root))
    lib.scan()
    before = lib.db.conn.total_changes
    lib.rebuild_stacks()
    assert lib.db.conn.total_changes == before, "unchanged stacks were rewritten"


# ---------------------------------------------------------------- EXIF writes are atomic and keep permissions

def test_gps_write_atomic_and_keeps_mode():
    from fotohaus import media
    p = _jpg(Path(tempfile.mkdtemp()) / "g.jpg", exif_date="2020:01:02 03:04:05")
    os.chmod(p, 0o640)
    assert media.write_gps_exif(str(p), 47.5, -122.33)
    md = media.read_metadata(str(p))
    assert abs(md["lat"] - 47.5) < 1e-4 and abs(md["lon"] + 122.33) < 1e-4
    assert md["taken"] and time.localtime(md["taken"]).tm_year == 2020      # other EXIF kept
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o640
    assert not [f for f in os.listdir(p.parent) if f.endswith(".tmp")]
    # 59.9999″ must carry into the next minute, never produce 60″
    d, m, s = media._to_dms(10.99999999)
    assert s[0] / s[1] < 60 and m[0] < 60


def test_adjust_dates_writes_exif():
    from fotohaus import fileops, media
    lib, root = _lib()
    p = _jpg(root / "D" / "a.jpg", exif_date="2020:01:02 03:04:05")
    lib.add_root(str(root))
    lib.scan()
    pid = lib.db.scalar("SELECT id FROM photos")
    fileops.adjust_dates(lib, [pid], delta_seconds=3600)
    assert time.localtime(media.read_metadata(str(p))["taken"]).tm_hour == 4


def test_caption_written_into_jpeg_keeps_mode():
    lib, root = _lib()
    p = _jpg(root / "C" / "a.jpg")
    os.chmod(p, 0o600)
    lib.add_root(str(root))
    lib.scan()
    pid = lib.db.scalar("SELECT id FROM photos")
    lib.set_caption(pid, "Grüße aus Aachen")
    from fotohaus import media
    assert media.read_metadata(str(p))["caption"] == "Grüße aus Aachen"
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600


# ---------------------------------------------------------------- video dates when 'moov' is at the end

def test_video_creation_time_moov_at_end():
    from fotohaus import media
    p = Path(tempfile.mkdtemp()) / "v.mp4"
    secs = 1_600_000_000 + 2082844800
    mvhd = b"mvhd" + bytes([0, 0, 0, 0]) + secs.to_bytes(4, "big") + bytes(80)
    moov_body = (8 + len(mvhd)).to_bytes(4, "big") + mvhd
    ftyp = (16).to_bytes(4, "big") + b"ftypisom" + bytes(4)
    mdat = (8 + 5_000_000).to_bytes(4, "big") + b"mdat" + bytes(5_000_000)     # > old 4 MB read window
    moov = (8 + len(moov_body)).to_bytes(4, "big") + b"moov" + moov_body
    p.write_bytes(ftyp + mdat + moov)
    assert media._video_creation_time(str(p)) == 1_600_000_000


# ---------------------------------------------------------------- .picasa.ini encoding marker

def test_ini_marks_utf8_for_picasa():
    from fotohaus.picasa.ini import PicasaIni
    d = Path(tempfile.mkdtemp())
    ini = PicasaIni(d)
    ini.set("a.jpg", "caption", "Café")
    ini.save()
    text = (d / ".picasa.ini").read_text("utf-8")
    assert text.startswith("[encoding]") and "utf8=1" in text and "caption=Café" in text
    assert PicasaIni(d).caption("a.jpg") == "Café" and "encoding" not in PicasaIni(d).photo_sections()
    d2 = Path(tempfile.mkdtemp())
    ini = PicasaIni(d2)
    ini.set("a.jpg", "star", "yes")
    ini.save()
    assert "[encoding]" not in (d2 / ".picasa.ini").read_text()


# ---------------------------------------------------------------- privacy: Private photos stay private

def test_private_photos_hidden_from_people_and_search():
    from fotohaus.search import search
    lib, root = _lib()
    _jpg(root / "P" / "secret.jpg")
    _jpg(root / "P" / "public.jpg", seed=5)
    lib.add_root(str(root))
    lib.scan()
    sec = lib.photo_by_path(str(root / "P" / "secret.jpg"))["id"]
    pub = lib.photo_by_path(str(root / "P" / "public.jpg"))["id"]
    for pid in (sec, pub):
        lib.add_manual_face(pid, (0.1, 0.1, 0.4, 0.4), "Anna")
    lib.add_manual_face(sec, (0.5, 0.5, 0.7, 0.7), None)
    lib.db.x("UPDATE faces SET cluster=1 WHERE person_id IS NULL")
    lib.set_private([sec], True)
    anna = lib.person_by_name("Anna")["id"]
    assert {f["photo_id"] for f in lib.faces_for_person(anna)} == {pub}
    assert [p["n"] for p in lib.people() if p["id"] == anna] == [1]
    assert not lib.unnamed_clusters(min_size=1) and not lib.faces_in_cluster(1)
    assert sec not in [r["id"] for r in search(lib, "secret")]


# ---------------------------------------------------------------- faces: empty embeddings, rejected suggestions

def test_face_clustering_survives_failed_embeddings_and_remembers_rejections():
    from fotohaus.ai import faces as F
    lib, root = _lib()
    _jpg(root / "F" / "a.jpg")
    lib.add_root(str(root))
    lib.scan()
    pid = lib.db.scalar("SELECT id FROM photos")
    rng = np.random.default_rng(0)
    base = rng.normal(size=128).astype(np.float32)
    base /= np.linalg.norm(base)
    anna = lib.ensure_person("Anna")

    def face(vec, person=None, confirmed=0):
        return lib.db.x("INSERT INTO faces(photo_id,x,y,w,h,person_id,source,confirmed,score,embedding) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?)", (pid, 0, 0, .1, .1, person, "auto", confirmed, .9,
                                                       F.to_blob(vec) if vec is not None else b"")).lastrowid
    face(base, anna, 1)
    near = face(base + rng.normal(scale=0.02, size=128).astype(np.float32))
    face(None)                                   # embedding failed: stored as an empty blob
    for _ in range(300):
        v = rng.normal(size=128).astype(np.float32)
        face(v / np.linalg.norm(v))
    st = F.update_suggestions_and_clusters(lib)
    assert st["suggested"] >= 1
    assert lib.db.scalar("SELECT person_id FROM faces WHERE id=?", (near,)) == anna
    lib.reject_suggestions([near])
    F.update_suggestions_and_clusters(lib)
    assert lib.db.scalar("SELECT person_id FROM faces WHERE id=?", (near,)) is None, "rejected suggestion came back"


# ---------------------------------------------------------------- thumbnails: concurrent renders of one photo

def test_concurrent_thumbnails():
    from concurrent.futures import ThreadPoolExecutor
    from fotohaus import thumbs
    lib, root = _lib()
    _jpg(root / "T" / "a.jpg", 1600, 1200)
    lib.add_root(str(root))
    lib.scan()
    r = lib.all_photos()[0]
    thumbs.drop_cached(r)
    with ThreadPoolExecutor(8) as ex:
        outs = list(ex.map(lambda _: thumbs.make_thumb(r), range(16)))
    assert all(o.exists() for o in outs)
    assert not list(outs[0].parent.glob("*.tmp"))


# ---------------------------------------------------------------- saving edits: sRGB, never a stray temp file

def test_save_edits_png_with_alpha_and_no_temp_left():
    lib, root = _lib()
    d = root / "E"
    d.mkdir(parents=True)
    Image.new("RGBA", (200, 100), (10, 200, 30, 128)).save(d / "a.png")
    lib.add_root(str(root))
    lib.scan()
    pid = lib.db.scalar("SELECT id FROM photos")
    from fotohaus.picasa.ini import Filter
    lib.set_filters(pid, [Filter.make("bw")])
    lib.save_edits(pid)
    assert Image.open(d / "a.png").size == (200, 100)
    assert not [f for f in os.listdir(d) if "tmp" in f]


# ---------------------------------------------------------------- moving the database copies it safely

def test_move_database_uses_sqlite_backup():
    import importlib
    import sqlite3
    from fotohaus import paths
    home = tempfile.mkdtemp()
    os.environ["FOTOHAUS_HOME"] = home
    try:
        P = importlib.reload(paths)
        P.ensure_dirs()
        con = sqlite3.connect(str(P.DB_PATH))
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("CREATE TABLE t(x)")
        con.executemany("INSERT INTO t VALUES(?)", [(i,) for i in range(1000)])
        con.commit()                              # rows still in the WAL, connection open
        dst = tempfile.mkdtemp()
        P.set_data_dir(dst)
        c2 = sqlite3.connect(str(Path(dst) / "Fotohaus Library" / "library.db"))
        assert c2.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 1000
        con.close()
    finally:
        importlib.reload(paths)


# ---------------------------------------------------------------- MCP: stdout carries only JSON

def test_mcp_stdout_is_clean():
    import json
    import subprocess
    env = dict(os.environ, FOTOHAUS_HOME=tempfile.mkdtemp())
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "set_caption", "arguments": {"id": 999, "caption": "x"}}},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/list"}]
    code = "import builtins,sys; sys.argv=['x','mcp']; from fotohaus.cli import main; print('noise'); main(['mcp'])"
    p = subprocess.run([sys.executable, "-c", "import fotohaus.cli as c, sys; sys.stdout.write(''); "
                        "import builtins; orig=c._mcp_call\n"
                        "def noisy(*a, **k):\n    print('warning from a library')\n    return orig(*a, **k)\n"
                        "c._mcp_call = noisy\nsys.exit(c.main(['mcp']))"],
                       input="\n".join(json.dumps(m) for m in msgs) + "\n", capture_output=True, text=True,
                       env=env, cwd=str(ROOT), timeout=120)
    del code
    lines = [x for x in p.stdout.splitlines() if x.strip()]
    for line in lines:
        json.loads(line)                          # every line must be JSON-RPC
    assert len(lines) == 3, p.stdout


# ---------------------------------------------------------------- movie maker streams clips

def test_movie_with_video_clip_and_audio():
    import av
    from fotohaus.movie import MovieOptions, make_movie
    d = Path(tempfile.mkdtemp())
    clip = d / "clip.mp4"
    c = av.open(str(clip), "w")
    s = c.add_stream("mpeg4", rate=10)
    s.width, s.height, s.pix_fmt = 160, 120, "yuv420p"
    for i in range(30):
        fr = av.VideoFrame.from_ndarray(np.full((120, 160, 3), i * 8, np.uint8), format="rgb24")
        for pk in s.encode(fr):
            c.mux(pk)
    for pk in s.encode():
        c.mux(pk)
    c.close()
    wav = d / "a.wav"
    import wave
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(44100)
        w.writeframes((np.sin(np.arange(44100 * 3) / 20) * 8000).astype(np.int16).repeat(2).tobytes())
    rows = [{"kind": "photo", "path": str(_jpg(d / "p1.jpg")), "filters": "", "rotate": 0, "caption": "Hi"},
            {"kind": "video", "path": str(clip), "filters": "", "rotate": 0, "caption": ""},
            {"kind": "photo", "path": str(_jpg(d / "p2.jpg", seed=4)), "filters": "", "rotate": 0, "caption": ""}]
    for tr in ("dissolve", "panzoom", "wipe", "none"):
        out = d / f"m-{tr}.mp4"
        make_movie(rows, MovieOptions(out_path=str(out), size=(320, 240), fps=10, slide_seconds=1,
                                      transition=tr, transition_seconds=0.3, title="T", title_seconds=0.5,
                                      audio_path=str(wav)))
        with av.open(str(out)) as m:
            assert m.streams.video and m.streams.audio
            n = sum(1 for _ in m.decode(m.streams.video[0]))
        assert n >= 10 + 5 + 10 + 3 - 2, (tr, n)


# ---------------------------------------------------------------- the whole UI, clicked through

def test_ui_click_through():
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QAction
    from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox
    app = QApplication.instance() or QApplication([])
    from fotohaus.ui import theme
    theme.apply_appearance(app, "system", "blue", "neutral")
    # dialogs answer themselves
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
    QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.Ok)
    QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.Ok)
    QInputDialog.getText = staticmethod(lambda *a, **k: ("Anna", True))
    QInputDialog.getItem = staticmethod(lambda *a, **k: ("Anna", True))
    import fotohaus.ui.mainwindow as MW
    lib, root = _lib()
    for i in range(6):
        _jpg(root / "Trip_2024" / f"IMG_{i}.jpg", seed=i, exif_date=f"2021:10:{20 + i % 3} 10:00:0{i}")
    _jpg(root / "Home" / "home.jpg", seed=9)
    lib.add_root(str(root))
    lib.settings.set("first_run_done", True)
    lib.settings.set("faces_enabled", False)
    lib.settings.set("vision_enabled", False)
    lib.scan()
    w = MW.MainWindow(lib)
    w.show()

    def pump(ms=150):
        end = time.time() + ms / 1000
        while time.time() < end:
            app.processEvents()
            time.sleep(0.01)

    def wait_jobs(timeout=60):
        end = time.time() + timeout
        while w.jobs.busy() and time.time() < end:
            pump(50)
    pump(600)
    wait_jobs()
    # shortcuts must be unique (an ambiguous shortcut does nothing on the Mac)
    seen = {}
    for a in w.findChildren(QAction):
        for sc in a.shortcuts():
            k = sc.toString()
            if k == "Ctrl+Y" and sys.platform != "darwin":
                continue            # Linux also binds Redo to Ctrl+Y; on the Mac Redo is ⇧⌘Z only
            if k and k in seen and seen[k] is not a:
                raise AssertionError(f"shortcut {k} used by '{seen[k].text()}' and '{a.text()}'")
            seen[k] = a
    # every sidebar collection and view
    for kind in ("all", "memories", "starred", "recent", "places", "timeline", "dupes", "archived", "deleted",
                 "people", "private"):
        w._go(kind, None)
        pump()
    fid = lib.db.scalar("SELECT id FROM folders WHERE path=?", (str(root / "Trip_2024"),))
    w._go("folder", fid)
    pump(400)
    ids = [r["id"] for r in w.grid.model_.photo_rows()]
    assert len(ids) == 6
    # header click selects the whole folder in one go
    w._go("all", None)
    pump(300)
    w.grid._clicked(w.grid.model_.index(0))
    assert len(w.grid.selected_ids()) in (1, 6)
    w._go("folder", fid)
    pump(200)
    w.grid.select_ids(ids[:3])
    assert w.grid.selected_ids() == ids[:3]
    w.toggle_star()
    w.rotate_selected(1)
    w.hold_selection()
    w.batch_effect("sepia")
    w.copy_effects()
    w.grid.select_ids(ids[3:4])
    w.paste_effects()
    w.invert_selection()
    w.select_starred()
    pump()
    # albums, tags, captions
    w.grid.select_ids(ids[:2])
    aid = lib.create_album("Best")
    w._fill_album_menu(w.album_menu, ids[:2])
    lib.add_to_album(aid, ids[:2])
    w._go("album", aid)
    pump()
    w._album_reordered(list(reversed(ids[:2])))
    w.grid.select_ids(ids[:1])
    w.add_tag_selected()
    w.caption_selected()
    assert lib.photo(ids[0])["caption"] == "Anna"
    w.panel.setCurrentIndex(1)
    w.panel.setCurrentIndex(2)
    w.panel.setCurrentIndex(3)
    w.panel.setCurrentIndex(0)
    # search
    w.search_box.setText("anna")
    w._do_search()
    pump()
    assert ids[0] in [r["id"] for r in w.grid.model_.photo_rows()]
    w.search_box.clear()
    w._do_search()
    # editor: open, effects, crop, undo/redo, tune, text, retouch, navigate, back
    w._go("folder", fid)
    pump(200)
    w.open_editor(ids[2])
    end = time.time() + 20
    while w.editor.committed is None and time.time() < end:
        pump(50)
    ed = w.editor
    assert ed.committed is not None
    ed.commit(ed.chain[0] if ed.chain else __import__("fotohaus.picasa.ini", fromlist=["Filter"]).Filter.make("bw"))
    ed._effect("Vignette")
    pump(300)
    ed.apply_session()
    ed._start_crop()
    ed.canvas.set_aspect("Square")
    ed._apply_crop()
    ed.do_undo()
    ed.do_redo()
    ed.tabs.setCurrentIndex(1)
    ed.tune["fill"].set_value(0.3)
    ed._tune_changed()
    pump(300)
    ed._tune_apply()
    ed.tabs.setCurrentIndex(2)
    pump(300)
    ed._start_text()
    ed.text_edit.setPlainText("Hello")
    pump(300)
    ed.apply_session()
    ed._start_retouch()
    ed._brushed(0.5, 0.5, 0.02)
    ed._retouch_render()
    pump(300)
    ed.apply_session()
    ed._start_redeye()
    ed._canvas_rect(0.4, 0.4, 0.45, 0.45)
    ed.apply_session()
    ed._step(1)
    pump(300)
    ed._step(-1)
    pump(300)
    ed.overflow_btn.toggle()
    ed.faces_btn.toggle()
    pump(200)
    ed._save()
    wait_jobs()
    ed._back()
    pump(200)
    assert w.stack.currentIndex() == MW.PAGE_LIBRARY
    # viewer with keyboard navigation
    from fotohaus.ui.viewer import PhotoViewer
    v = PhotoViewer(lib, lib.photo(ids[0])["path"])
    v.show()
    pump(500)
    start = v.i
    v.step(1)
    pump(300)
    assert v.i == (start + 1) % len(v.items)
    v.zoom_by(2.0)
    v.toggle_zoom()
    v.fit()
    v.rotate(1)
    v.toggle_star()
    v.toggle_info()
    pump(300)
    v.close()
    # slideshow
    w.start_slideshow(from_start=True)
    pump(400)
    w.slideshow.next()
    w.slideshow.back()
    w.slideshow.toggle_play()
    w.slideshow.close()
    # archive / private / delete / restore
    w._go("folder", fid)
    pump(200)
    w.grid.select_ids(ids[4:5])
    w.archive_selected()
    assert lib.photo(ids[4])["archived"] == 1
    w._go("archived", None)
    pump()
    w.grid.select_ids(ids[4:5])
    w.archive_selected()
    w._go("folder", fid)
    pump(200)
    w.grid.select_ids(ids[5:6])
    w.trash_selected()
    assert lib.photo(ids[5])["deleted"]
    w._go("deleted", None)
    pump()
    w.grid.select_ids(ids[5:6])
    w.restore_selected()
    assert lib.photo(ids[5])["deleted"] is None
    # private: refreshes never prompt, only opening does
    calls = []
    import fotohaus.macos as M
    M.authenticate = lambda reason="": calls.append(reason) or False
    w._private_unlocked = False
    lib.set_private([ids[3]], True)
    w._go("private", None)
    pump()
    n = len(calls)
    for _ in range(3):
        w._refresh_all()
        pump(50)
    assert len(calls) == n, "background refresh asked for Touch ID"
    assert not w.grid.model_.photo_rows()
    # find similar never reveals private photos
    w._go("folder", fid)
    pump()
    w.grid.select_ids(ids[:1])
    w.find_similar()
    pump()
    assert ids[3] not in [r["id"] for r in w.grid.model_.photo_rows()]
    lib.set_private([ids[3]], False)
    # folder actions
    w._go("folder", fid)
    pump()
    w.grid.select_ids(ids[:1])
    w.split_folder()
    wait_jobs()
    pump(300)
    w._rename_folder(fid)
    pump(200)
    # dialogs: open and close each one
    from PySide6.QtWidgets import QDialog as _D
    real_exec = _D.exec
    opened = []

    def fake_exec(self):
        opened.append(type(self).__name__)
        QTimer.singleShot(0, self.reject)
        return real_exec(self)
    _D.exec = fake_exec
    try:
        w.open_settings()
        w.folder_manager()
        w.backup()
        w.import_from()
        w.show_shortcuts()
        w._go("all", None)
        pump()
        w.grid.select_ids([r["id"] for r in w.grid.model_.photo_rows()][:3])
        w.make_collage()
        w.make_movie()
        w.export_selected()
        w.print_selected()
        w.show_properties()
        w.adjust_dates()
        w.rename_selected()
        w.make_poster()
        w.make_passport()
        w.export_html()
        w.make_gift()
        w.email_selected()
        w.new_album()
    finally:
        _D.exec = real_exec
    wait_jobs()
    for name in ("OptionsDialog", "FolderManagerDialog", "BackupDialog", "ImportDialog", "ShortcutsDialog",
                 "CollageDialog", "MovieMakerDialog", "ExportDialog", "PrintDialog", "ExifDialog",
                 "AdjustDateDialog", "BatchRenameDialog", "PosterDialog", "PassportDialog", "HtmlExportDialog",
                 "GiftDiskDialog", "EmailDialog", "FolderPropertiesDialog"):
        assert name in opened, f"{name} didn't open"
    # people view with faces
    pid0 = [r["id"] for r in lib.all_photos()][0]
    lib.add_manual_face(pid0, (0.2, 0.2, 0.5, 0.5), "Anna")
    w._go("people", None)
    pump(500)
    w._go("person", lib.person_by_name("Anna")["id"])
    pump(300)
    w.close()
    pump(200)
    print(f"  clicked through {len(opened)} dialogs and every view")


if __name__ == "__main__":
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS", name)
            except Exception:
                import traceback
                traceback.print_exc()
                print("FAIL", name)
                failed += 1
    sys.stdout.flush()
    os._exit(1 if failed else 0)        # skip PySide's teardown, as the app itself does
