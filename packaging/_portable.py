"""Shared steps for the Windows and Linux builds: a standalone CPython, the dependencies as wheels for
the target system, our package, and only the parts of Qt that Fotohaus uses.

Like build_mac_app.py this needs no compiler and can run on any system: wheels are fetched for the
target platform with pip, and Qt's dependencies are read from the binaries with LIEF.
"""
from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent
sys.path.insert(0, str(SRC))
from fotohaus import __version__  # noqa: E402,F401

PYVER = "3.12"
# python-build-standalone release used for both builds (pinned with checksums).
PBS_TAG = "20261026"
PBS_PY = "3.12.16"
PBS = {
    "linux": ("x86_64-unknown-linux-gnu", "6a79e922e50006b601f29e688407659da1c2b86ebcd4e9c7254cf234be8b89de"),
    "windows": ("x86_64-pc-windows-msvc", "590dfcbe3a34c07301e6332e13e2188b98655cd072b08afa45a7998dc4c22bc5"),
}

# Same pins as the Mac build, so all three ship the same versions (and the same GPL sources apply).
REQUIREMENTS = ["PySide6-Essentials==6.9.3", "PySide6-Addons==6.9.3", "av==19.0.1", "numpy", "pillow",
                "pillow-heif==1.8.1", "opencv-python-headless==5.0.0.93", "piexif", "rawpy", "onnxruntime",
                "tokenizers"]
WINDOWS_EXTRA = ["winrt-runtime", "winrt-Windows.Foundation", "winrt-Windows.Security.Credentials.UI"]
PIP_PLATFORMS = {
    "linux": ["manylinux_2_28_x86_64", "manylinux_2_27_x86_64", "manylinux_2_17_x86_64",
              "manylinux2014_x86_64", "linux_x86_64"],
    "windows": ["win_amd64"],
}

# Qt modules Fotohaus imports; everything else in PySide6 is removed.
QT_MODULES = {"QtCore", "QtGui", "QtWidgets", "QtNetwork", "QtSvg", "QtMultimedia", "QtMultimediaWidgets",
              "QtPrintSupport"}
QT_PLUGIN_DIRS = {
    "linux": {"platforms", "platformthemes", "platforminputcontexts", "xcbglintegrations",
              "wayland-decoration-client", "wayland-graphics-integration-client", "wayland-shell-integration",
              "styles", "imageformats", "iconengines", "tls", "networkinformation", "multimedia",
              "printsupport"},
    "windows": {"platforms", "styles", "imageformats", "iconengines", "tls", "networkinformation", "multimedia",
                "printsupport"},
}
# Plugins inside the kept plugin folders that would pull in unused parts of Qt (QML, Quick, EGLFS…)
DROP_PLUGINS = {
    "linux": ["imageformats/*qpdf*", "platforminputcontexts/*virtualkeyboard*", "platforms/*eglfs*",
              "platforms/*linuxfb*", "platforms/*vnc*", "platforms/*vkkhrdisplay*", "platforms/*minimalegl*"],
    "windows": ["imageformats/*qpdf*", "platforminputcontexts/*virtualkeyboard*", "platforms/*direct2d*"],
}
SITE_REMOVE = ["pip", "setuptools", "wheel", "google", "huggingface_hub", "hf_xet", "fsspec", "filelock", "click",
               "tqdm", "yaml", "_yaml", "sympy", "mpmath", "bin", "coloredlogs", "humanfriendly",
               "onnxruntime/tools", "onnxruntime/transformers", "onnxruntime/quantization", "cv2/data",
               "numpy/tests", "numpy/_core/tests", "PIL/tests"]
STDLIB_REMOVE = ["test", "idlelib", "tkinter", "turtledemo", "ensurepip", "lib2to3", "pydoc_data"]


def run(cmd, **kw):
    print("+", " ".join(str(c) for c in cmd), flush=True)
    subprocess.run([str(c) for c in cmd], check=True, **kw)


def fetch_python(target: str, cache: Path, tar: str | None = None) -> Path:
    """The standalone CPython archive for target ("linux" / "windows"), downloaded once and checked."""
    triple, sha = PBS[target]
    name = f"cpython-{PBS_PY}+{PBS_TAG}-{triple}-install_only.tar.gz"
    path = Path(tar) if tar else cache / name
    if not path.exists():
        cache.mkdir(parents=True, exist_ok=True)
        url = (f"https://github.com/astral-sh/python-build-standalone/releases/download/{PBS_TAG}/"
               + name.replace("+", "%2B"))
        print("downloading", url, flush=True)
        tmp = path.with_suffix(".part")
        urllib.request.urlretrieve(url, tmp)
        tmp.replace(path)
    got = hashlib.sha256(path.read_bytes()).hexdigest()
    if got != sha:
        raise SystemExit(f"{path.name}: checksum mismatch ({got})")
    return path


def extract(tar: Path, dest: Path) -> Path:
    if dest.exists():
        shutil.rmtree(dest)
    with tarfile.open(tar) as tf:
        tf.extractall(dest, filter="tar")
    return dest / "python"


def install_site(site: Path, target: str, prepared: Path | None = None) -> None:
    site.mkdir(parents=True, exist_ok=True)
    if prepared:
        shutil.copytree(prepared, site, symlinks=True, dirs_exist_ok=True)
        return
    reqs = REQUIREMENTS + (WINDOWS_EXTRA if target == "windows" else [])
    cmd = [sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check", "--target", site,
           "--python-version", PYVER, "--implementation", "cp", "--only-binary=:all:"]
    for plat in PIP_PLATFORMS[target]:
        cmd += ["--platform", plat]
    run(cmd + reqs)


def prune_stdlib(std: Path) -> None:
    for rel in STDLIB_REMOVE:
        p = std / rel
        if p.is_dir():
            shutil.rmtree(p)
    for p in list(std.rglob("_tkinter*")) + list(std.rglob("__pycache__")):
        shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink()


def prune_site(site: Path) -> None:
    for rel in SITE_REMOVE:
        p = site / rel
        if p.is_dir():
            shutil.rmtree(p)
        elif p.exists():
            p.unlink()
    removed = {r.lower().replace("_", "-") for r in SITE_REMOVE if "/" not in r}
    removed |= {"pyyaml", "hf-xet", "huggingface-hub", "protobuf"}
    for d in list(site.glob("*.dist-info")):
        if d.name.split("-")[0].lower().replace("_", "-") in removed:
            shutil.rmtree(d)
            continue
        for f in d.iterdir():   # keep license files and metadata only
            if f.is_file() and not f.name.upper().startswith(("LICENSE", "COPYING", "METADATA", "NOTICE")):
                f.unlink()
    for pkg in ("numpy", "PIL", "cv2", "av", "onnxruntime", "rawpy", "pillow_heif"):
        for t in list((site / pkg).rglob("tests")) if (site / pkg).exists() else []:
            if t.is_dir():
                shutil.rmtree(t, ignore_errors=True)   # the packages' own test suites
    for p in list(site.rglob("__pycache__")):
        shutil.rmtree(p, ignore_errors=True)


def _imports(path: Path) -> list[str]:
    import lief
    lief.logging.disable()
    try:
        b = lief.parse(str(path))
    except Exception:
        return []
    if b is None:
        return []
    if isinstance(b, lief.PE.Binary):
        return [i.name for i in b.imports]
    return list(b.libraries)


def prune_qt(site: Path, target: str) -> None:
    """Keep the PySide6 modules Fotohaus uses, the plugins it needs, and the Qt libraries they load."""
    ps = site / "PySide6"
    win = target == "windows"
    qt_root = ps if win else ps / "Qt"
    plugins = qt_root / "plugins"
    lib_dir = ps if win else qt_root / "lib"
    ext = ".pyd" if win else ".abi3.so"
    for p in list(ps.iterdir()):
        n = p.name
        if p.is_dir() and n in ("include", "typesystems", "glue", "scripts", "doc", "qml", "metatypes",
                                "examples", "resources"):
            shutil.rmtree(p)
        elif p.is_file() and n.endswith(ext) and n.split(".")[0] not in QT_MODULES:
            p.unlink()
        elif p.is_file() and (n.endswith(".pyi") or n.startswith(("libpyside6qml", "pyside6qml"))):
            p.unlink()
        elif p.is_file() and n.endswith(".exe"):
            p.unlink()         # designer, QtWebEngineProcess, … (the app runs none of them)
    if not win:
        for sub in ("qml", "libexec", "metatypes", "resources"):
            if (qt_root / sub).exists():
                shutil.rmtree(qt_root / sub)
    for d in plugins.iterdir():
        if d.name not in QT_PLUGIN_DIRS[target]:
            shutil.rmtree(d)
    for pattern in DROP_PLUGINS[target]:
        for p in plugins.glob(pattern):
            p.unlink()
    # dependency closure of the kept modules and plugins
    roots = [p for p in ps.glob(f"*{ext}")] + [p for p in plugins.rglob("*") if p.is_file()]
    roots += [p for p in ps.glob("*shiboken*") if p.is_file()] + [p for p in ps.glob("*pyside6*") if p.is_file()]
    shib = site / "shiboken6"
    roots += [p for p in shib.glob("*") if p.is_file()] if shib.exists() else []
    # follow dependencies through every library in Qt's folder (Qt's FFmpeg needs Qt6FFmpegStub-*,
    # for example), but only remove Qt's own libraries that nothing reaches
    libs = {p.name: p for p in lib_dir.iterdir()
            if p.is_file() and (p.suffix.lower() == ".dll" if win else ".so" in p.name)}
    is_qt = (lambda n: n.startswith("Qt6")) if win else (lambda n: n.startswith("libQt6"))
    keep: set[str] = set()
    todo = list(roots)
    while todo:
        for dep in _imports(todo.pop()):
            if dep in libs and dep not in keep:
                keep.add(dep)
                todo.append(libs[dep])
    for name, p in libs.items():
        if is_qt(name) and name not in keep:
            p.unlink()
    tr = qt_root / "translations"
    if tr.exists():
        for f in tr.iterdir():
            if not f.name.startswith(("qtbase_", "qt_")) or f.name.startswith("qt_help"):
                f.unlink() if f.is_file() else shutil.rmtree(f)


def add_package(site: Path) -> None:
    shutil.copytree(SRC / "fotohaus", site / "fotohaus", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))


def collect_licenses(site: Path, lic: Path, python_license: Path | None) -> None:
    lic.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SRC / "LICENSE", lic / "Fotohaus-LICENSE.txt")
    shutil.copy2(SRC / "packaging" / "THIRD_PARTY_NOTICES.md", lic / "THIRD_PARTY_NOTICES.md")
    shutil.copy2(SRC / "PRIVACY.md", lic / "PRIVACY.md")
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
    if python_license and python_license.exists():
        shutil.copy2(python_license, lic / "CPython-LICENSE.txt")


def compile_pyc(python: str | None, libdir: Path) -> None:
    """Precompile bytecode (faster first launch; the installed app folder is usually read-only)."""
    if not python:
        print("! no Python 3.12 given for precompiling; first launch will be slower", flush=True)
        return
    run([python, "-m", "compileall", "-q", "-j", "0", "--invalidation-mode", "unchecked-hash", str(libdir)])


def link_duplicates(root: Path) -> None:
    """Wheels unpack libfoo.so, libfoo.so.1 and libfoo.so.1.2 as three copies; keep one, link the rest."""
    import os
    seen: dict[tuple[int, str], Path] = {}
    for p in sorted(root.rglob("*.so*"), key=lambda x: (len(x.name), str(x)), reverse=True):
        if not p.is_file() or p.is_symlink() or p.stat().st_size < 100_000:
            continue
        key = (p.stat().st_size, hashlib.sha1(p.read_bytes()).hexdigest())
        if key in seen and seen[key].parent == p.parent:
            p.unlink()
            p.symlink_to(os.path.relpath(seen[key], p.parent))
        else:
            seen.setdefault(key, p)


def du(path: Path) -> str:
    total = sum(f.stat().st_size for f in path.rglob("*") if f.is_file() and not f.is_symlink())
    return f"{total / 1e6:.0f} MB"
