#!/usr/bin/env python3
"""Assemble Fotohaus.app (Apple Silicon) and a DMG — cross-built from Linux.

Layout:
  Fotohaus.app/Contents/MacOS/Fotohaus        <- the standalone CPython 3.12 binary itself
  Fotohaus.app/Contents/MacOS/fotohaus-cli    <- shell wrapper:  Fotohaus -m fotohaus.cli "$@"
  Fotohaus.app/Contents/lib/python3.12/...   <- stdlib + site-packages (+ the fotohaus package)
  Fotohaus.app/Contents/Resources/Fotohaus.icns, Licenses/

Python finds its stdlib by walking up from the executable (Contents/MacOS -> Contents/lib), and
`sitecustomize.py` starts the GUI when the binary is launched without arguments (from Finder).

Requirements (Linux): python3.12 (for .pyc), pip, llvm-lipo, xorriso, libdmg-hfsplus `dmg`,
optional `rcodesign` for ad-hoc signing.

Usage:
  python3 packaging/build_mac_app.py --python-tar cpython-3.12.*-aarch64-apple-darwin-install_only.tar.gz \
      --out build/ [--site prepared-site-packages-dir] [--dmg-tool path/to/dmg] [--rcodesign path]
"""
from __future__ import annotations

import argparse
import os
import plistlib
import shutil
import stat
import subprocess
import sys
import tarfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent
sys.path.insert(0, str(SRC))
from fotohaus import __version__  # noqa: E402

PYVER = "3.12"
BUNDLE_ID = "app.fotohaus.Fotohaus"
MIN_MACOS = "14.0"
REQUIREMENTS = ["PySide6-Essentials", "PySide6-Addons", "av", "pyobjc-framework-UniformTypeIdentifiers", "numpy", "pillow", "pillow-heif", "opencv-python-headless", "piexif",
                "onnxruntime", "tokenizers", "rawpy", "pyobjc-core", "pyobjc-framework-Cocoa",
                "pyobjc-framework-Quartz", "pyobjc-framework-Vision"]

# Qt pieces we ship (everything else in PySide6 is removed)
QT_MODULES = {"QtCore", "QtGui", "QtWidgets", "QtNetwork", "QtSvg", "QtMultimedia", "QtMultimediaWidgets",
              "QtPrintSupport"}
QT_PLUGIN_DIRS = {"platforms", "styles", "imageformats", "iconengines", "tls", "networkinformation", "multimedia",
                  "printsupport"}
SITE_REMOVE = ["pip", "setuptools", "wheel", "PyObjCTest", "google", "huggingface_hub", "hf_xet", "httpx2", "httpcore2", "anyio", "h11", "fsspec",
               "filelock", "click", "tqdm", "yaml", "_yaml", "sympy", "mpmath", "bin", "truststore",
               "coloredlogs", "humanfriendly", "onnxruntime/tools", "onnxruntime/transformers",
               "onnxruntime/quantization", "cv2/data", "numpy/tests", "numpy/_core/tests", "PIL/tests"]
STDLIB_REMOVE = ["test", "idlelib", "tkinter", "turtledemo", "ensurepip", "lib2to3", "pydoc_data",
                 f"config-{PYVER}-darwin"]

SITECUSTOMIZE = '''\
# Fotohaus launcher: when the bundle's main executable is started without arguments
# (from Finder / the Dock) or with only file/folder paths (Fotohaus photo.jpg), run the GUI.
# With any other arguments it behaves exactly like python3 (used by fotohaus-cli).
import os as _os, sys as _sys
_sys.dont_write_bytecode = True   # never modify the (signed) bundle; .pyc files are precompiled
_a = list(getattr(_sys, "orig_argv", _sys.argv))
_args = [x for x in _a[1:] if not x.startswith("-psn")]
if _os.path.basename(_sys.executable) == "Fotohaus" and all(
        not x.startswith("-") and not x.endswith(".py") and _os.path.exists(x) for x in _args):
    _os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")
    try:
        from fotohaus.app import main as _main
        _rc = _main([_a[0] if _a else "Fotohaus"] + _args)
    except BaseException:
        import traceback as _tb
        _log = _os.path.expanduser("~/Library/Logs/Fotohaus.log")
        try:
            with open(_log, "a") as _fh:
                _tb.print_exc(file=_fh)
        except OSError:
            pass
        _tb.print_exc()
        _rc = 1
    try:
        _sys.stdout.flush()
        _sys.stderr.flush()
    except Exception:
        pass
    _os._exit(int(_rc or 0))
'''

CLI_WRAPPER = '''#!/bin/sh
# Fotohaus command-line tool / MCP server.  Usage: fotohaus-cli --help
DIR="$(cd "$(dirname "$0")" && pwd -P)"
exec "$DIR/Fotohaus" -B -m fotohaus.cli "$@"
'''


def run(cmd, **kw):
    print("+", " ".join(str(c) for c in cmd))
    subprocess.run([str(c) for c in cmd], check=True, **kw)


def is_macho(p: Path) -> bool:
    try:
        with open(p, "rb") as fh:
            m = fh.read(4)
    except OSError:
        return False
    return m in (b"\xca\xfe\xba\xbe", b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xbe\xba\xfe\xca")


def is_fat(p: Path) -> bool:
    with open(p, "rb") as fh:
        return fh.read(4) == b"\xca\xfe\xba\xbe"


def install_site(site: Path, prepared: Path | None):
    if prepared:
        shutil.copytree(prepared, site, symlinks=True, dirs_exist_ok=True)
    else:
        run([sys.executable, "-m", "pip", "install", "--quiet", "--target", site,
             "--platform", "macosx_14_0_arm64", "--python-version", PYVER, "--implementation", "cp",
             "--only-binary=:all:"] + REQUIREMENTS)


def prune_site(site: Path):
    for rel in SITE_REMOVE:
        p = site / rel
        if p.is_dir():
            shutil.rmtree(p)
        elif p.exists():
            p.unlink()
    removed = {r.split("/")[0].lower().replace("_", "-") for r in SITE_REMOVE}
    removed |= {"httpx2", "httpcore2", "pyyaml", "hf-xet", "huggingface-hub", "protobuf"}
    for d in list(site.glob("*.dist-info")):
        if d.name.split("-")[0].lower().replace("_", "-") in removed:
            shutil.rmtree(d)
    for d in list(site.glob("*.dist-info")):
        # keep license files only
        for f in d.iterdir():
            if f.is_file() and not f.name.upper().startswith(("LICENSE", "COPYING", "METADATA", "NOTICE")):
                f.unlink()
    ps = site / "PySide6"
    for p in ps.iterdir():
        name = p.name
        if name.endswith(".app") or name in ("lupdate", "lrelease", "qmlformat", "qmllint", "qmlls", "svgtoqml",
                                             "include", "typesystems", "glue", "scripts", "doc"):
            shutil.rmtree(p) if p.is_dir() else p.unlink()
        elif name.endswith(".abi3.so") and name.split(".")[0] not in QT_MODULES:
            p.unlink()
        elif name.endswith(".pyi"):
            p.unlink()
        elif name.startswith("libpyside6qml"):
            p.unlink()
        elif p.is_file() and not p.suffix and is_macho(p):
            p.unlink()          # Qt command-line tools (balsam, qsb, …) the app never runs
    qt = ps / "Qt"
    for sub in ("qml", "libexec", "metatypes"):
        if (qt / sub).exists():
            shutil.rmtree(qt / sub)
    for d in (qt / "plugins").iterdir():
        if d.name not in QT_PLUGIN_DIRS:
            shutil.rmtree(d)
    # Qt frameworks: keep the dependency closure of the modules + plugins we ship
    needed = _qt_closure(site)
    for fw in (qt / "lib").glob("*.framework"):
        if fw.stem not in needed:
            shutil.rmtree(fw)
    tr = qt / "translations"
    if tr.exists():
        for f in tr.iterdir():
            if not f.name.startswith(("qtbase_", "qt_")):
                f.unlink()
    for p in list(site.rglob("*.dSYM")):
        shutil.rmtree(p, ignore_errors=True)
    pdf = qt / "plugins" / "imageformats" / "libqpdf.dylib"   # needs QtPdf (not shipped)
    if pdf.exists():
        pdf.unlink()
    for p in site.rglob("__pycache__"):
        shutil.rmtree(p, ignore_errors=True)


def _deps(path: Path) -> list[str]:
    import lief
    try:
        fat = lief.MachO.parse(str(path))
    except Exception:
        return []
    out = []
    if fat is None:
        return out
    for i in range(fat.size):
        b = fat.at(i)
        out.extend(l.name for l in b.libraries)
    return out


def _qt_closure(site: Path) -> set[str]:
    ps = site / "PySide6"
    lib = ps / "Qt" / "lib"
    todo = [ps / f"{m}.abi3.so" for m in QT_MODULES]
    todo += list((ps / "Qt" / "plugins").rglob("*.dylib"))
    todo += list(ps.glob("libpyside6.abi3*.dylib"))
    seen_fw: set[str] = set()
    done: set[Path] = set()
    while todo:
        f = todo.pop()
        if f in done or not f.exists():
            continue
        done.add(f)
        for dep in _deps(f):
            if ".framework/" in dep and "Qt" in dep:
                name = dep.split("/")[-1]
                fw = dep.split(".framework/")[0].split("/")[-1]
                if fw not in seen_fw and (lib / f"{fw}.framework").exists():
                    seen_fw.add(fw)
                    todo.append(lib / f"{fw}.framework" / "Versions" / "A" / name)
    return seen_fw


def thin(root: Path, lipo: str):
    n = 0
    for p in root.rglob("*"):
        if p.is_file() and not p.is_symlink() and is_macho(p) and is_fat(p):
            tmp = p.with_name(p.name + ".thin")
            r = subprocess.run([lipo, str(p), "-thin", "arm64", "-output", str(tmp)], capture_output=True)
            if r.returncode == 0:
                mode = p.stat().st_mode
                os.replace(tmp, p)
                os.chmod(p, mode)
                n += 1
            elif tmp.exists():
                tmp.unlink()
    print(f"thinned {n} universal binaries to arm64")


def build(args):
    out = Path(args.out).resolve()
    app = out / "Fotohaus.app"
    if app.exists():
        shutil.rmtree(app)
    contents = app / "Contents"
    (contents / "MacOS").mkdir(parents=True)
    (contents / "Resources").mkdir()
    work = out / "_python"
    if work.exists():
        shutil.rmtree(work)
    with tarfile.open(args.python_tar) as tf:
        tf.extractall(work, filter="tar") if hasattr(tarfile, "tar_filter") else tf.extractall(work)
    pyroot = work / "python"
    # stdlib
    shutil.copytree(pyroot / "lib" / f"python{PYVER}", contents / "lib" / f"python{PYVER}", symlinks=True)
    std = contents / "lib" / f"python{PYVER}"
    for rel in STDLIB_REMOVE:
        p = std / rel
        if p.exists():
            shutil.rmtree(p)
    for p in (std / "lib-dynload").glob("_tkinter*"):
        p.unlink()
    for p in std.rglob("__pycache__"):
        shutil.rmtree(p, ignore_errors=True)
    exe = contents / "MacOS" / "Fotohaus"
    shutil.copy2(pyroot / "bin" / f"python{PYVER}", exe)
    os.chmod(exe, 0o755)
    site = std / "site-packages"
    site.mkdir(exist_ok=True)
    install_site(site, Path(args.site) if args.site else None)
    prune_site(site)
    # our package
    shutil.copytree(SRC / "fotohaus", site / "fotohaus",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    (site / "sitecustomize.py").write_text(SITECUSTOMIZE)
    cli = contents / "MacOS" / "fotohaus-cli"
    cli.write_text(CLI_WRAPPER)
    os.chmod(cli, 0o755)
    if args.lipo:
        thin(contents, args.lipo)
    # licenses
    lic = contents / "Resources" / "Licenses"
    lic.mkdir()
    shutil.copy2(SRC / "LICENSE", lic / "Fotohaus-LICENSE.txt")
    shutil.copy2(SRC / "packaging" / "THIRD_PARTY_NOTICES.md", lic / "THIRD_PARTY_NOTICES.md")
    for d in site.glob("*.dist-info"):
        for f in d.rglob("*"):
            if f.is_file() and f.name.upper().startswith(("LICENSE", "COPYING", "NOTICE")):
                shutil.copy2(f, lic / f"{d.name.split('-')[0]}-{f.name}")
    # icon + plist
    shutil.copy2(args.icns, contents / "Resources" / "Fotohaus.icns")
    info = {
        "CFBundleName": "Fotohaus", "CFBundleDisplayName": "Fotohaus", "CFBundleExecutable": "Fotohaus",
        "CFBundleIdentifier": BUNDLE_ID, "CFBundleIconFile": "Fotohaus", "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": __version__, "CFBundleVersion": __version__,
        "CFBundleInfoDictionaryVersion": "6.0", "CFBundleDevelopmentRegion": "en",
        "LSMinimumSystemVersion": MIN_MACOS, "LSApplicationCategoryType": "public.app-category.photography",
        "LSArchitecturePriority": ["arm64"], "NSHighResolutionCapable": True,
        "NSSupportsAutomaticGraphicsSwitching": True, "NSRequiresAquaSystemAppearance": False,
        "NSPrincipalClass": "NSApplication",
        "NSHumanReadableCopyright": "Fotohaus — independent open-source project. Not affiliated with Google.",
        "NSDesktopFolderUsageDescription": "Fotohaus shows the photos in folders you choose to add.",
        "NSDocumentsFolderUsageDescription": "Fotohaus shows the photos in folders you choose to add.",
        "NSDownloadsFolderUsageDescription": "Fotohaus shows the photos in folders you choose to add.",
        "NSRemovableVolumesUsageDescription": "Fotohaus shows photos on drives you choose to add.",
        "NSNetworkVolumesUsageDescription": "Fotohaus shows photos on network drives you choose to add.",
        "NSPhotoLibraryUsageDescription": "Fotohaus can show the original files of your Photos library.",
        "NSDesktopUsageDescription": "Fotohaus can set a picture as your desktop background.",
        "NSAppleEventsUsageDescription": "Fotohaus uses Mail to create messages with your pictures attached.",
        # Finder "Open With" / default-viewer registration (Picasa Photo Viewer behaviour)
        "CFBundleDocumentTypes": [
            {"CFBundleTypeName": "Image", "CFBundleTypeRole": "Viewer", "LSHandlerRank": "Alternate",
             "CFBundleTypeIconSystemGenerated": True,
             "LSItemContentTypes": ["public.image", "public.jpeg", "public.png", "public.heic", "public.heif",
                                    "public.tiff", "com.compuserve.gif", "org.webmproject.webp", "com.microsoft.bmp",
                                    "public.avif", "com.adobe.photoshop-image", "public.camera-raw-image",
                                    "com.adobe.raw-image"]},
            {"CFBundleTypeName": "Movie", "CFBundleTypeRole": "Viewer", "LSHandlerRank": "Alternate",
             "CFBundleTypeIconSystemGenerated": True,
             "LSItemContentTypes": ["public.movie", "public.mpeg-4", "com.apple.quicktime-movie", "public.avi",
                                    "public.mpeg", "public.3gpp"]},
            {"CFBundleTypeName": "Folder", "CFBundleTypeRole": "Viewer", "LSHandlerRank": "None",
             "LSItemContentTypes": ["public.folder"]},
        ],
    }
    with open(contents / "Info.plist", "wb") as fh:
        plistlib.dump(info, fh)
    (contents / "PkgInfo").write_text("APPL????")
    # precompile bytecode with a matching interpreter (faster first launch, keeps the bundle untouched)
    if args.pyc_python:
        libdir = contents / "lib" / f"python{PYVER}"
        subprocess.run([args.pyc_python, "-m", "compileall", "-q", "-j", "0",
                        "--invalidation-mode", "unchecked-hash",
                        "-s", str(libdir), "-p", f"/Applications/Fotohaus.app/Contents/lib/python{PYVER}",
                        "-x", r"(lib2to3|test|tests|_test)/", str(libdir)], check=False)
    # permissions: everything readable, dirs traversable
    for p in app.rglob("*"):
        if p.is_symlink():
            continue
        m = p.stat().st_mode
        os.chmod(p, m | stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH |
                 ((stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH) if p.is_dir() or m & stat.S_IXUSR else 0))
    shutil.rmtree(work)
    if args.rcodesign:
        run([args.rcodesign, "sign", str(app)])
    return app


def make_dmg(app: Path, out: Path, dmg_tool: str | None):
    stage = out / "_dmg"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir()
    shutil.copytree(app, stage / app.name, symlinks=True)
    os.symlink("/Applications", stage / "Applications")
    shutil.copy2(SRC / "packaging" / "READ ME FIRST.txt", stage / "READ ME FIRST.txt")
    iso = out / "Fotohaus-uncompressed.dmg"
    run(["xorrisofs", "-D", "-l", "-V", "Fotohaus", "-no-pad", "-r", "-dir-mode", "0755", "-o", iso, stage],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    final = out / f"Fotohaus-{__version__}-arm64.dmg"
    if dmg_tool:
        run([dmg_tool, iso, final])
        iso.unlink()
    else:
        os.replace(iso, final)
    shutil.rmtree(stage)
    return final


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--python-tar", required=True)
    ap.add_argument("--out", default=str(SRC / "build"))
    ap.add_argument("--site", help="pre-installed macOS site-packages dir (pip --target)")
    ap.add_argument("--icns", default=str(SRC / "packaging" / "Fotohaus.icns"))
    ap.add_argument("--lipo", default=shutil.which("llvm-lipo") or shutil.which("llvm-lipo-18"))
    ap.add_argument("--pyc-python", default=shutil.which("python3.12"))
    ap.add_argument("--dmg-tool")
    ap.add_argument("--rcodesign")
    ap.add_argument("--no-dmg", action="store_true")
    a = ap.parse_args()
    app = build(a)
    print("built", app)
    if not a.no_dmg:
        print("dmg", make_dmg(app, Path(a.out).resolve(), a.dmg_tool))
