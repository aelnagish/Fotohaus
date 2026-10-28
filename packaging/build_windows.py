#!/usr/bin/env python3
"""Build Fotohaus for Windows: the app folder, and with Inno Setup the installer
Fotohaus-<version>-Setup.exe.

Layout of the app folder (installed to Program Files or %LOCALAPPDATA%\\Programs\\Fotohaus):
  Fotohaus.exe          <- packaging/windows/launcher.c, GUI build (no console window)
  fotohaus-cli.exe      <- the same launcher, console build (command line tool and MCP server)
  python312.dll, vcruntime140*.dll, DLLs\\, Lib\\  <- CPython 3.12 (python-build-standalone)
  Lib\\site-packages\\   <- dependencies + the fotohaus package
  Licenses\\, Fotohaus.ico

Assembling the folder runs on any system (wheels are fetched for Windows with pip). Compiling the
launchers needs the Microsoft C compiler (cl and rc on PATH, e.g. a "Developer Command Prompt"),
and the installer needs Inno Setup's ISCC.exe; the GitHub workflow provides both.

Usage:
  python packaging/build_windows.py --out build/ [--iscc "C:\\Program Files (x86)\\Inno Setup 6\\ISCC.exe"]
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

import _portable as P

WIN = Path(__file__).resolve().parent / "windows"
DLLS = ["python312.dll", "python3.dll", "vcruntime140.dll", "vcruntime140_1.dll"]

RC = """#include <winver.h>
1 ICON "{ico}"
1 VERSIONINFO
FILEVERSION {v4}
PRODUCTVERSION {v4}
FILEFLAGSMASK 0x3fL
FILEOS VOS_NT_WINDOWS32
FILETYPE VFT_APP
BEGIN
  BLOCK "StringFileInfo"
  BEGIN
    BLOCK "040904b0"
    BEGIN
      VALUE "CompanyName", "Abdullah Yusuf"
      VALUE "FileDescription", "{desc}"
      VALUE "FileVersion", "{version}"
      VALUE "InternalName", "{name}"
      VALUE "LegalCopyright", "Copyright (C) 2026 Abdullah Yusuf. GPL-3.0-or-later."
      VALUE "OriginalFilename", "{name}"
      VALUE "ProductName", "Fotohaus"
      VALUE "ProductVersion", "{version}"
    END
  END
  BLOCK "VarFileInfo"
  BEGIN
    VALUE "Translation", 0x409, 1200
  END
END
"""

MANIFEST = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<assembly xmlns="urn:schemas-microsoft-com:asm.v1" manifestVersion="1.0">
  <assemblyIdentity type="win32" name="Fotohaus.Fotohaus" version="{v4dots}"/>
  <compatibility xmlns="urn:schemas-microsoft-com:compatibility.v1">
    <application><supportedOS Id="{{8e0f7a12-bfb3-4fe8-b9a5-48fd50a15a9a}}"/></application>
  </compatibility>
  <application xmlns="urn:schemas-microsoft-com:asm.v3">
    <windowsSettings>
      <longPathAware xmlns="http://schemas.microsoft.com/SMI/2016/WindowsSettings">true</longPathAware>
      <activeCodePage xmlns="http://schemas.microsoft.com/SMI/2019/WindowsSettings">UTF-8</activeCodePage>
    </windowsSettings>
  </application>
</assembly>
"""


def make_ico(dest: Path) -> None:
    from PIL import Image
    with Image.open(P.SRC / "fotohaus" / "resources" / "icon.png") as im:
        im.convert("RGBA").save(dest, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128),
                                             (256, 256)])


def compile_launchers(py: Path, app: Path, work: Path) -> bool:
    """Fotohaus.exe and fotohaus-cli.exe from launcher.c. False if the compiler isn't available."""
    if not (shutil.which("cl") and shutil.which("rc")):
        print("! cl/rc not on PATH: launchers not compiled (run from a Developer Command Prompt)", flush=True)
        return False
    work.mkdir(parents=True, exist_ok=True)
    nums = [int(x) for x in P.__version__.split(".")[:3]] + [0, 0, 0]
    v4 = ",".join(str(x) for x in nums[:4])
    (work / "fotohaus.manifest").write_text(MANIFEST.format(v4dots=v4.replace(",", ".")), encoding="utf-8")
    for exe, desc, gui in (("Fotohaus.exe", "Fotohaus", True), ("fotohaus-cli.exe", "Fotohaus command line", False)):
        stem = exe[:-4]
        rc = work / f"{stem}.rc"
        rc.write_text(RC.format(ico=str(app / "Fotohaus.ico").replace("\\", "\\\\"), v4=v4, desc=desc,
                                version=P.__version__, name=exe), encoding="utf-8")
        P.run(["rc", "/nologo", f"/fo{work / (stem + '.res')}", rc])
        P.run(["cl", "/nologo", "/O2", "/MD", "/W3"] + (["/DFOTOHAUS_GUI"] if gui else [])
              + [f"/I{py / 'include'}", WIN / "launcher.c", work / f"{stem}.res", f"/Fo{work / (stem + '.obj')}",
                 f"/Fe{app / exe}", "/link", f"/LIBPATH:{py / 'libs'}",
                 "/SUBSYSTEM:WINDOWS" if gui else "/SUBSYSTEM:CONSOLE",
                 "/MANIFEST:EMBED", f"/MANIFESTINPUT:{work / 'fotohaus.manifest'}"])
    return True


def write_associations(dest: Path) -> None:
    """Inno Setup [Registry] lines for every extension Fotohaus opens (from fotohaus/media.py)."""
    from fotohaus import media
    lines = []
    for ext in sorted(media.ALL_EXT):
        prog = "Fotohaus.Video" if ext in media.VIDEO_EXT else "Fotohaus.Image"
        lines += [
            f'Root: HKA; Subkey: "Software\\Classes\\{ext}\\OpenWithProgids"; ValueType: string; '
            f'ValueName: "{prog}"; ValueData: ""; Flags: uninsdeletevalue',
            f'Root: HKA; Subkey: "Software\\Classes\\Applications\\Fotohaus.exe\\SupportedTypes"; '
            f'ValueType: string; ValueName: "{ext}"; ValueData: ""',
            f'Root: HKA; Subkey: "Software\\Fotohaus\\Capabilities\\FileAssociations"; ValueType: string; '
            f'ValueName: "{ext}"; ValueData: "{prog}"',
        ]
    dest.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build(args) -> Path:
    out = Path(args.out).resolve()
    app = out / "Fotohaus"
    if app.exists():
        shutil.rmtree(app)
    app.mkdir(parents=True)
    (out / "_cache").mkdir(exist_ok=True)
    tar = P.fetch_python("windows", out / "_cache", args.python_tar)
    py = P.extract(tar, out / "_python_windows")
    for name in DLLS:
        shutil.copy2(py / name, app / name)
    shutil.copytree(py / "DLLs", app / "DLLs", ignore=shutil.ignore_patterns("*.pdb", "_tkinter*", "tcl*", "tk*"))
    std = app / "Lib"
    shutil.copytree(py / "Lib", std, ignore=shutil.ignore_patterns("site-packages", "__pycache__", "*.pdb"))
    P.prune_stdlib(std)
    site = std / "site-packages"
    P.install_site(site, "windows", Path(args.site) if args.site else None)
    P.prune_site(site)
    P.prune_qt(site, "windows")
    P.add_package(site)
    P.collect_licenses(site, app / "Licenses", py / "LICENSE.txt")
    make_ico(app / "Fotohaus.ico")
    P.compile_pyc(args.pyc_python, std)
    launchers = compile_launchers(py, app, out / "_launcher")
    print("App folder:", app, P.du(app), flush=True)
    if not args.iscc:
        return app
    if not launchers:
        raise SystemExit("can't build the installer without Fotohaus.exe (cl and rc are needed)")
    assoc = out / "associations.iss"
    write_associations(assoc)
    P.run([args.iscc, f"/DAppVersion={P.__version__}", f"/DSourceDir={app}", f"/DOutputDir={out}",
           f"/DAssocFile={assoc}", f"/DPrivacyFile={P.SRC / 'PRIVACY.md'}", WIN / "Fotohaus.iss"])
    setup = out / f"Fotohaus-{P.__version__}-Setup.exe"
    print("Installer:", setup, f"{setup.stat().st_size / 1e6:.0f} MB", flush=True)
    return setup


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="build")
    ap.add_argument("--python-tar", help="python-build-standalone archive (downloaded if not given)")
    ap.add_argument("--site", help="prepared site-packages to copy instead of installing wheels")
    ap.add_argument("--pyc-python", default=sys.executable if sys.version_info[:2] == (3, 12) else None,
                    help="a Python 3.12 to precompile bytecode with (default: this one, if it is 3.12)")
    ap.add_argument("--iscc", help="Inno Setup's ISCC.exe, to build the installer")
    build(ap.parse_args())


if __name__ == "__main__":
    main()
