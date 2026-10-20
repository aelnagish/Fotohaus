#!/usr/bin/env python3
"""Assemble Fotohaus.app (Apple Silicon) and a DMG — cross-built from Linux.

Layout:
  Fotohaus.app/Contents/MacOS/Fotohaus        <- the standalone CPython 3.12 binary itself
  Fotohaus.app/Contents/MacOS/fotohaus-cli    <- shell wrapper:  Fotohaus -m fotohaus.cli "$@"
  Fotohaus.app/Contents/lib/python3.12/...   <- stdlib + site-packages (+ the fotohaus package)
  Fotohaus.app/Contents/Resources/Fotohaus.icns, Licenses/

Python finds its stdlib by walking up from the executable (Contents/MacOS -> Contents/lib), and
`sitecustomize.py` starts the GUI when the binary is launched without arguments (from Finder).

Requirements (Linux): python3.12 (for .pyc), pip, lief, llvm-lipo, mkfs.hfsplus (hfsprogs) and Mozilla's
libdmg-hfsplus (`hfsplus` and `dmg`), and `rcodesign` for ad-hoc signing (required on Apple Silicon).

Usage:
  python3 packaging/build_mac_app.py --python-tar cpython-3.12.*-aarch64-apple-darwin-install_only.tar.gz \
      --out build/ --hfsplus path/to/hfsplus --dmg-tool path/to/dmg --rcodesign path [--site dir]
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
# The GPL-bundling wheels are pinned: THIRD_PARTY_NOTICES.md and the GPL source archive attached to
# each release name the exact FFmpeg/x264/x265 versions inside these. Update all three together.
# PySide6 6.10+ builds its bindings for macOS 15 (6.12: macOS 26) whatever the wheel tag says; 6.9 targets
# macOS 12, so the app really runs on macOS 14 as advertised. Raise this together with MIN_MACOS.
REQUIREMENTS = ["PySide6-Essentials==6.9.3", "PySide6-Addons==6.9.3", "av==19.0.1", "numpy", "pillow", "pillow-heif==1.8.1",
                "opencv-python-headless==5.0.0.93", "piexif", "rawpy", "onnxruntime", "tokenizers", "pyobjc-core",
                "pyobjc-framework-Cocoa", "pyobjc-framework-Quartz", "pyobjc-framework-Vision",
                "pyobjc-framework-UniformTypeIdentifiers", "pyobjc-framework-LocalAuthentication"]

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
    removed = {r.lower().replace("_", "-") for r in SITE_REMOVE if "/" not in r}
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
    for f in (SRC / "packaging" / "licenses").glob("*.txt"):
        shutil.copy2(f, lic / f.name)
    for d in site.glob("*.dist-info"):
        pkg = d.name.split("-")[0]
        for f in d.rglob("*"):
            if f.is_file() and f.name.upper().startswith(("LICENSE", "LICENCE", "COPYING", "NOTICE")):
                rel = f.relative_to(d).as_posix()
                rel = rel[len("licenses/"):] if rel.startswith("licenses/") else rel
                shutil.copy2(f, lic / f"{pkg}-{rel.replace('/', '_')}")
    for rel, name in (("onnxruntime/LICENSE", "onnxruntime-LICENSE"),
                      ("onnxruntime/ThirdPartyNotices.txt", "onnxruntime-ThirdPartyNotices.txt")):
        if (site / rel).exists():
            shutil.copy2(site / rel, lic / name)
    py_lic = contents / "lib" / f"python{PYVER}" / "LICENSE.txt"
    if py_lic.exists():
        shutil.copy2(py_lic, lic / "CPython-LICENSE.txt")
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
        "NSHumanReadableCopyright": "© 2026 Abdullah Yusuf. Free software under the GNU GPL v3 or later.",
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
    problems = verify_bundle(app)
    for p in problems:
        print("BUNDLE PROBLEM:", p)
    if problems and not args.allow_problems:
        sys.exit(f"{len(problems)} bundle problems (see above); not making a DMG")
    print("bundle check: every binary is arm64, signed, runs on macOS", MIN_MACOS, "and finds its libraries")
    return app


def verify_bundle(app: Path) -> list[str]:
    """Check every Mach-O file the way macOS will load it: arm64, signed, minimum macOS no newer than
    MIN_MACOS, and every library it links to present in the bundle or the system. Returns problems."""
    import lief
    lief.logging.disable()
    problems: list[str] = []
    min_os = tuple(int(x) for x in MIN_MACOS.split("."))
    contents = app / "Contents"
    for f in sorted(p for p in contents.rglob("*") if p.is_file() and not p.is_symlink() and is_macho(p)):
        rel = f.relative_to(app)
        fat = lief.MachO.parse(str(f))
        if fat is None or fat.size == 0:
            problems.append(f"{rel}: unreadable Mach-O")
            continue
        if fat.size != 1 or fat.at(0).header.cpu_type != lief.MachO.Header.CPU_TYPE.ARM64:
            problems.append(f"{rel}: not a thin arm64 binary")
        b = fat.at(0)
        if not b.has_code_signature:
            problems.append(f"{rel}: not signed")
        if b.has_build_version:
            v = tuple(b.build_version.minos[:2])
            if v > min_os:
                problems.append(f"{rel}: needs macOS {v[0]}.{v[1]}")
        rpaths = []
        for c in b.commands:
            if c.command == lief.MachO.LoadCommand.TYPE.RPATH:
                rpaths.append(c.path)
        def resolve(name: str) -> list[Path]:
            if name.startswith("@loader_path/"):
                return [f.parent / name[len("@loader_path/"):]]
            if name.startswith("@executable_path/"):
                return [contents / "MacOS" / name[len("@executable_path/"):]]
            if name.startswith("@rpath/"):
                out = []
                for rp in rpaths:
                    for c in resolve(rp.rstrip("/") + "/" + name[len("@rpath/"):]) if rp.startswith("@") else \
                            [Path(rp) / name[len("@rpath/"):]]:
                        out.append(c)
                return out
            return []
        for lib in b.libraries:
            if lib.command == lief.MachO.LoadCommand.TYPE.ID_DYLIB:
                continue                       # the library's own install name
            name = lib.name
            if name.startswith(("/usr/lib/", "/System/")):
                continue
            if not name.startswith("@"):
                problems.append(f"{rel}: links to {name} outside the bundle")
                continue
            cands = resolve(name)
            if not any(c.exists() for c in cands):
                weak = lib.command == lief.MachO.LoadCommand.TYPE.LOAD_WEAK_DYLIB
                if not weak:
                    problems.append(f"{rel}: missing {name}")
    return problems


def make_dmg(app: Path, out: Path, dmg_tool: str | None, hfsplus: str | None = None):
    """Disk image with the app, an Applications link and the READ ME.

    With `hfsplus` (Mozilla's libdmg-hfsplus) and `mkfs.hfsplus` the image is a native HFS+ volume, the
    format macOS itself uses: files keep normal permissions (writable by the owner once copied), so
    `xattr -cr` can clear the quarantine flag. Without them it falls back to an ISO 9660 image, whose
    files are read-only (Finder copies keep that, and `xattr` then fails with "Permission denied")."""
    stage = out / "_dmg"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir()
    shutil.copytree(app, stage / app.name, symlinks=True)
    os.symlink("/Applications", stage / "Applications")
    shutil.copy2(SRC / "packaging" / "READ ME FIRST.txt", stage / "READ ME FIRST.txt")
    os.chmod(stage / "READ ME FIRST.txt", 0o644)
    final = out / f"Fotohaus-{__version__}-arm64.dmg"
    if final.exists():
        final.unlink()
    mkfs = shutil.which("mkfs.hfsplus")
    if hfsplus and mkfs and dmg_tool:
        img = out / "Fotohaus.hfs"
        size = sum(p.stat().st_size for p in stage.rglob("*") if p.is_file() and not p.is_symlink())
        files = sum(1 for _ in stage.rglob("*"))
        mb = int(size * 1.15 / 2**20 + files * 8 / 1024 + 64)        # data + 8 KiB per entry + slack
        with open(img, "wb") as fh:
            fh.truncate(mb * 2**20)
        run([mkfs, "-v", "Fotohaus", img], stdout=subprocess.DEVNULL)
        run([hfsplus, img, "addall", stage, "-s", "clone_link"], stdout=subprocess.DEVNULL)
        run([dmg_tool, "build", img, final], stdout=subprocess.DEVNULL)
        img.unlink()
    else:
        iso = out / "Fotohaus-uncompressed.dmg"
        run(["xorrisofs", "-D", "-l", "-V", "Fotohaus", "-no-pad", "-r", "-dir-mode", "0755", "-o", iso, stage],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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
    ap.add_argument("--dmg-tool", help="libdmg-hfsplus `dmg` (Mozilla's, with --hfsplus; otherwise any)")
    ap.add_argument("--hfsplus", help="libdmg-hfsplus `hfsplus` tool: build a native HFS+ image (recommended)")
    ap.add_argument("--rcodesign")
    ap.add_argument("--no-dmg", action="store_true")
    ap.add_argument("--allow-problems", action="store_true", help="make the DMG even if the bundle check fails")
    ap.add_argument("--unsigned", action="store_true", help="allow building without rcodesign (won't run on a Mac)")
    a = ap.parse_args()
    if not a.rcodesign and not a.unsigned:
        ap.error("--rcodesign is required: an unsigned bundle shows as damaged on Apple Silicon (or pass --unsigned)")
    app = build(a)
    print("built", app)
    if not a.no_dmg:
        print("dmg", make_dmg(app, Path(a.out).resolve(), a.dmg_tool, a.hfsplus))
