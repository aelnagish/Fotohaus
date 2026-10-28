#!/usr/bin/env python3
"""Build Fotohaus-<version>-x86_64.AppImage: one file that runs on most Linux desktops.

Layout of the AppDir that becomes the AppImage:
  AppRun                          <- starts usr/bin/fotohaus -m fotohaus "$@"
  fotohaus.desktop, fotohaus.png  <- menu entry and icon (also used by "Make Fotohaus the Default")
  usr/bin/fotohaus                <- the standalone CPython 3.12 binary
  usr/lib/python3.12/...          <- stdlib + site-packages (+ the fotohaus package)
  usr/share/licenses/fotohaus/    <- license texts

Requirements: Python 3 with pip and lief (`pip install lief`); appimagetool is downloaded if not given.
The libxcb-cursor library that Qt needs on X11 (missing on some distributions) is bundled when
`apt-get download` is available.

Usage:
  python3 packaging/build_linux.py --out build/
"""
from __future__ import annotations

import argparse
import os
import shutil
import stat
import subprocess
import tempfile
import urllib.request
from pathlib import Path

import _portable as P

APPIMAGETOOL_URL = ("https://github.com/AppImage/appimagetool/releases/download/continuous/"
                    "appimagetool-x86_64.AppImage")

APPRUN = r'''#!/bin/sh
# Fotohaus AppImage entry point. With photos/folders (or nothing) it opens the app; with a command
# (Fotohaus.AppImage search "beach 2019", … mcp) it runs the command line tool.
HERE="$(dirname "$(readlink -f "$0")")"
unset PYTHONHOME PYTHONPATH
exec "$HERE/usr/bin/fotohaus" -I -B -m fotohaus "$@"
'''

DESKTOP = """[Desktop Entry]
Type=Application
Name=Fotohaus
GenericName=Photo Organizer
Comment=Organize, find and edit your photos, privately
Exec=fotohaus %F
Icon=fotohaus
Terminal=false
StartupWMClass=Fotohaus
Categories=Graphics;Photography;Viewer;
Keywords=photos;pictures;picasa;faces;albums;
"""


XCB_PACKAGES = {"libxcb-cursor0": "libxcb-cursor.so.0", "libxcb-image0": "libxcb-image.so.0",
                "libxcb-render-util0": "libxcb-render-util.so.0", "libxcb-util1": "libxcb-util.so.1"}


def bundle_xcb_cursor(qt_lib: Path) -> None:
    """Qt's X11 plugin needs libxcb-cursor.so.0 (and the small xcb libraries it uses), which some
    distributions don't install by default. They go next to Qt's own libraries, where Qt finds them."""
    if not shutil.which("apt-get") or not shutil.which("dpkg-deb"):
        print("! apt-get not found: libxcb-cursor not bundled", flush=True)
        return
    with tempfile.TemporaryDirectory() as td:
        try:
            P.run(["apt-get", "download"] + list(XCB_PACKAGES), cwd=td)
        except subprocess.CalledProcessError:
            print("! couldn't download the xcb libraries (run apt-get update first)", flush=True)
            return
        for deb in Path(td).glob("*.deb"):
            P.run(["dpkg-deb", "-x", deb, Path(td) / "x"])
        for so_name in XCB_PACKAGES.values():
            for so in (Path(td) / "x").rglob(so_name):
                shutil.copy2(so, qt_lib / so_name, follow_symlinks=True)


def build(args) -> Path:
    out = Path(args.out).resolve()
    appdir = out / "Fotohaus.AppDir"
    if appdir.exists():
        shutil.rmtree(appdir)
    (out / "_cache").mkdir(parents=True, exist_ok=True)
    tar = P.fetch_python("linux", out / "_cache", args.python_tar)
    py = P.extract(tar, out / "_python_linux")
    usr = appdir / "usr"
    (usr / "bin").mkdir(parents=True)
    (usr / "lib").mkdir()
    # interpreter (statically linked; finds its stdlib in ../lib/python3.12)
    shutil.copy2(py / "bin" / f"python{P.PYVER}", usr / "bin" / "fotohaus")
    std = usr / "lib" / f"python{P.PYVER}"
    shutil.copytree(py / "lib" / f"python{P.PYVER}", std, symlinks=True,
                    ignore=shutil.ignore_patterns(f"config-{P.PYVER}-*", "site-packages"))
    P.prune_stdlib(std)
    site = std / "site-packages"
    P.install_site(site, "linux", Path(args.site) if args.site else None)
    P.prune_site(site)
    P.prune_qt(site, "linux")
    if not args.no_xcb_cursor:
        bundle_xcb_cursor(site / "PySide6" / "Qt" / "lib")
    P.add_package(site)
    P.link_duplicates(usr)
    P.collect_licenses(site, usr / "share" / "licenses" / "fotohaus", py / "lib" / f"python{P.PYVER}" / "LICENSE.txt")
    # AppImage metadata
    (appdir / "AppRun").write_text(APPRUN)
    os.chmod(appdir / "AppRun", 0o755)
    (appdir / "fotohaus.desktop").write_text(DESKTOP)
    from PIL import Image
    with Image.open(P.SRC / "fotohaus" / "resources" / "icon.png") as im:
        im.resize((256, 256), Image.LANCZOS).save(appdir / "fotohaus.png")
    (appdir / ".DirIcon").symlink_to("fotohaus.png")
    # bytecode, compiled by the bundled interpreter itself
    P.compile_pyc(str(py / "bin" / f"python{P.PYVER}"), std)
    print("AppDir:", appdir, P.du(appdir), flush=True)
    if args.no_appimage:
        return appdir
    tool = args.appimagetool
    if not tool:
        tool = out / "_cache" / "appimagetool-x86_64.AppImage"
        if not tool.exists():
            urllib.request.urlretrieve(APPIMAGETOOL_URL, tool)
            tool.chmod(tool.stat().st_mode | stat.S_IXUSR)
    image = out / f"Fotohaus-{P.__version__}-x86_64.AppImage"
    if image.exists():
        image.unlink()
    env = dict(os.environ, ARCH="x86_64", APPIMAGE_EXTRACT_AND_RUN="1")
    P.run([tool, "--no-appstream", appdir, image], env=env)
    print("AppImage:", image, f"{image.stat().st_size / 1e6:.0f} MB", flush=True)
    return image


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="build")
    ap.add_argument("--python-tar", help="python-build-standalone archive (downloaded if not given)")
    ap.add_argument("--site", help="prepared site-packages to copy instead of installing wheels")
    ap.add_argument("--appimagetool", help="path to appimagetool (downloaded if not given)")
    ap.add_argument("--no-xcb-cursor", action="store_true", help="don't bundle libxcb-cursor")
    ap.add_argument("--no-appimage", action="store_true", help="stop after assembling the AppDir")
    build(ap.parse_args())


if __name__ == "__main__":
    main()
