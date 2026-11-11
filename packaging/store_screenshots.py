#!/usr/bin/env python3
"""Screenshots of Fotohaus for the store listings (Microsoft Store, Flathub), made automatically.

It downloads a set of sample photos from Unsplash (free to use under the Unsplash License), puts them
into a few dated folders, opens Fotohaus on an empty library of its own and saves 1920 x 1080 PNGs of
the library, the photo viewer, the editor, Memories and Places. Your own library is not touched.

  python packaging/store_screenshots.py --out build/screenshots [--theme light|dark]

The GitHub workflow runs it on Windows; the PNGs are in the run's "Store-Screenshots" artifact.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent
W, H = 1920, 1080

# Unsplash photo ids, grouped into the folders the library shows. "years_ago" dates the folder on
# today's day in an earlier year, so Memories ("On this day") has something to show; "days_ago" dates
# it earlier this year.
PLACES = {
    "braies": (46.6944, 12.0856), "paris": (48.8584, 2.2945), "london": (51.5055, -0.0754),
    "sanfrancisco": (37.8199, -122.4783), "maldives": (3.2028, 73.2207), "aachen": (50.7753, 6.0839),
}
FOLDERS = [
    {"name": "{year} Dolomites", "days_ago": 12, "place": "braies",
     "photos": ["1476514525535-07fb3b4ae5f1", "1501785888041-af3ef285b470", "1464822759023-fed622ff2c3b",
                "1506905925346-21bda4d32df4", "1508739773434-c26b3d09e071", "1519681393784-d120267933ba",
                "1470071459604-3b5ec3a7fe05"]},
    {"name": "Family & Friends", "days_ago": 40, "place": "aachen",
     "photos": ["1511632765486-a01980e01a18", "1522202176988-66273c2fd55f", "1529156069898-49953e39b3ac",
                "1517841905240-472988babdf9", "1438761681033-6461ffad8d80", "1500648767791-00dcc994a43e",
                "1495474472287-4d71bcdd2085"]},
    {"name": "Pets", "days_ago": 70,
     "photos": ["1543466835-00a7907e9de1", "1514888286974-6c03e2ca1dba", "1518791841217-8f162f1e1131",
                "1425082661705-1834bfd09dca", "1452570053594-1b985d6ea890"]},
    {"name": "Food", "days_ago": 100,
     "photos": ["1414235077428-338989a2e8c0", "1473093295043-cdd812d0e601", "1504674900247-0877df9cc836",
                "1540189549336-e6e99c3679fe"]},
    {"name": "Nature", "days_ago": 160,
     "photos": ["1426604966848-d7adac402bff", "1433086966358-54859d0ed716", "1441974231531-c6227db76b6e",
                "1465146344425-f00d5f5c8f07", "1472214103451-9374bd1c798e", "1488646953014-85cb44e25828",
                "1490750967868-88aa4486c946", "1511884642898-4c92249e20b6"]},
    {"name": "{year} Beach Holiday", "years_ago": 1, "place": "maldives",
     "photos": ["1507525428034-b723cf961d3e", "1519046904884-53103b34b206", "1475924156734-496f6cac6ec1",
                "1505144808419-1957a94ca61e", "1494500764479-0c8f2919a3d8", "1500530855697-b586d89ba3ee",
                "1500534314209-a25ddb2bd429"]},
    {"name": "{year} City Trips", "years_ago": 2,
     "photos": [("1502602898657-3e91760cbb34", "paris"), ("1513635269975-59663e0ac1ad", "london"),
                ("1501594907352-04cda38ebc29", "sanfrancisco"), "1477959858617-67f85cf4f1df",
                "1449824913935-59a10b8d2000"]},
]
CAPTIONS = {
    "1476514525535-07fb3b4ae5f1": "Morning on the lake",
    "1507525428034-b723cf961d3e": "Our last evening on the beach",
    "1502602898657-3e91760cbb34": "Paris at dusk",
    "1543466835-00a7907e9de1": "Bruno's birthday",
}
STARRED = {"1476514525535-07fb3b4ae5f1", "1501785888041-af3ef285b470", "1507525428034-b723cf961d3e",
           "1502602898657-3e91760cbb34", "1543466835-00a7907e9de1", "1511632765486-a01980e01a18"}
SELECTED = "1476514525535-07fb3b4ae5f1"     # selected in the library shot
VIEWER = "1501785888041-af3ef285b470"       # shown in the photo viewer
EDITOR = "1506905925346-21bda4d32df4"       # opened in the editor
PLACE = "1502602898657-3e91760cbb34"        # selected on the map
WALLPAPER = "1519681393784-d120267933ba"    # the "desktop" behind the photo viewer


def _gps(lat: float, lon: float) -> dict:
    import piexif

    def dms(v):
        v = abs(v)
        d = int(v)
        m = int((v - d) * 60)
        s = round(((v - d) * 60 - m) * 60 * 100)
        return ((d, 1), (m, 1), (s, 100))
    return {piexif.GPSIFD.GPSLatitudeRef: b"N" if lat >= 0 else b"S", piexif.GPSIFD.GPSLatitude: dms(lat),
            piexif.GPSIFD.GPSLongitudeRef: b"E" if lon >= 0 else b"W", piexif.GPSIFD.GPSLongitude: dms(lon)}


def make_photos(dest: Path) -> dict[str, Path]:
    """Download the sample photos into dated folders, with the date (and place) in their EXIF."""
    import piexif
    from PIL import Image
    today = dt.date.today()
    cache = dest.parent / "_unsplash"
    cache.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    for f in FOLDERS:
        if "years_ago" in f:
            day = today.replace(year=today.year - f["years_ago"])
        else:
            day = today - dt.timedelta(days=f["days_ago"])
        folder = dest / f["name"].format(year=day.year)
        folder.mkdir(parents=True, exist_ok=True)
        for i, entry in enumerate(f["photos"]):
            pid, place = entry if isinstance(entry, tuple) else (entry, f.get("place"))
            raw = cache / f"{pid}.jpg"
            if not raw.exists():
                url = f"https://images.unsplash.com/photo-{pid}?w=2400&q=85&fm=jpg"
                for attempt in range(3):
                    try:
                        with urllib.request.urlopen(url, timeout=60) as r:
                            raw.write_bytes(r.read())
                        break
                    except OSError:
                        if attempt == 2:
                            raise
                        time.sleep(3)
            taken = dt.datetime.combine(day, dt.time(9, 0)) + dt.timedelta(minutes=37 * i)
            exif = {"0th": {}, "Exif": {piexif.ExifIFD.DateTimeOriginal: taken.strftime("%Y:%m:%d %H:%M:%S")},
                    "GPS": _gps(*PLACES[place]) if place else {}, "1st": {}}
            out = folder / f"IMG_{4100 + len(paths)}.jpg"
            with Image.open(raw) as im:
                im.convert("RGB").save(out, quality=90, exif=piexif.dump(exif))
            ts = taken.timestamp()
            os.utime(out, (ts, ts))
            paths[pid] = out
    return paths


def wait(ms: int) -> None:
    from PySide6.QtCore import QEventLoop, QTimer
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


WRONG_SIZE: list[str] = []


def save(widget_or_image, out: Path, name: str) -> None:
    from PySide6.QtGui import QImage
    img = widget_or_image if isinstance(widget_or_image, QImage) else widget_or_image.grab().toImage()
    if img.width() != W or img.height() != H:
        print(f"! {name}: {img.width()} x {img.height()} (expected {W} x {H})", flush=True)
        WRONG_SIZE.append(name)
    path = out / name
    img.save(str(path))
    print("saved", path, f"{img.width()} x {img.height()}", flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(SRC / "build" / "screenshots"))
    ap.add_argument("--theme", default="light", choices=["light", "dark"])
    ap.add_argument("--work", help="folder for the sample photos and the library (default: a temporary one)")
    args = ap.parse_args()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    work = Path(args.work or tempfile.mkdtemp(prefix="fotohaus-shots-")).resolve()
    os.environ["FOTOHAUS_HOME"] = str(work / "home")          # a library of its own, before fotohaus loads
    sys.path.insert(0, str(SRC))

    print("sample photos…", flush=True)
    photos = make_photos(work / "Pictures")

    from PySide6.QtCore import Qt
    from PySide6.QtGui import QIcon, QImage, QPainter
    from PySide6.QtWidgets import QApplication
    from fotohaus import APP_NAME
    from fotohaus.paths import settings
    st = settings()
    for k, v in {"first_run_done": True, "theme": args.theme, "accent": "blue", "library_bg": "neutral",
                 "map_tiles": True, "clip_enabled": False}.items():
        st.set(k, v)

    app = QApplication([sys.argv[0]])
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setWindowIcon(QIcon(str(SRC / "fotohaus" / "resources" / "icon.png")))
    from fotohaus.library import get_library
    from fotohaus.ui import theme
    lib = get_library()
    theme.apply_appearance(app, args.theme, "blue", "neutral")

    print("scanning and indexing…", flush=True)
    lib.add_root(str(work / "Pictures"))
    lib.scan()
    from fotohaus.ai.indexer import Indexer
    try:
        Indexer(lib).run()
    except Exception as e:                                      # faces are a bonus for these shots
        print("! indexing:", e, flush=True)
    ids = {pid: lib.db.scalar("SELECT id FROM photos WHERE path=?", (str(p),)) for pid, p in photos.items()}
    lib.set_star([ids[p] for p in STARRED], True)
    for pid, text in CAPTIONS.items():
        lib.set_caption(ids[pid], text)

    from fotohaus.ui.mainwindow import MainWindow
    m = MainWindow(lib)
    m.resize(W, H)
    m.show()
    m.move(0, 0)
    wait(3000)
    m.resize(W, H)

    # 1. the library, with a photo selected and its details in the info panel
    m._go("all", None)
    wait(1500)
    m.grid.select_ids([ids[SELECTED]])
    wait(8000)                                                  # thumbnails load in the background
    save(m, out, "01-library.png")

    # 2. Memories ("On this day")
    m._go("memories", None)
    wait(6000)
    save(m, out, "02-memories.png")

    # 3. the editor, opened from All Photos so the filmstrip shows the library
    m._go("all", None)
    wait(1500)
    m.open_editor(ids[EDITOR])
    wait(6000)
    save(m, out, "03-editor.png")
    m.close_editor()

    # 4. Places, with a photo from Paris selected
    m.grid.select_ids([ids[PLACE]])
    wait(1000)
    m._go("places", None)
    wait(15000)                                                 # map tiles come from OpenStreetMap
    save(m, out, "04-places.png")
    m._go("all", None)

    # 5. the photo viewer, over a "desktop" (it is a see-through window over whatever is behind it)
    from fotohaus.ui.viewer import PhotoViewer
    v = PhotoViewer(lib, str(photos[VIEWER]))
    v.show()
    v.setGeometry(0, 0, W, H)
    wait(6000)
    v._wake()                                                   # the control bar and filmstrip, as on
    v.hide_timer.stop()                                         # moving the mouse
    wait(1500)
    shot = v.grab().toImage()
    desk = QImage(str(photos[WALLPAPER])).scaled(W, H, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
    desk = desk.copy((desk.width() - W) // 2, (desk.height() - H) // 2, W, H).convertToFormat(QImage.Format_RGB32)
    p = QPainter(desk)
    p.drawImage(0, 0, shot)
    p.end()
    save(desk, out, "05-viewer.png")
    v.close()

    print("done:", out, flush=True)
    from fotohaus import hard_exit
    # 3: the screen was too small for 1920 x 1080 windows (try again with QT_QPA_PLATFORM=offscreen)
    hard_exit(3 if WRONG_SIZE else 0)


if __name__ == "__main__":
    main()
