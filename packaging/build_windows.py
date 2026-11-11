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

With --msix the same app folder is also packed as Fotohaus-<version>-x64.msix for the Microsoft Store
(needs makeappx and makepri from the Windows SDK, which sit next to rc). The Store signs the package
itself, so it is left unsigned. See packaging/STORES.md.

Usage:
  python packaging/build_windows.py --out build/ [--iscc "C:\\Program Files (x86)\\Inno Setup 6\\ISCC.exe"] [--msix]
"""
from __future__ import annotations

import argparse
import os
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


# ---------------------------------------------------------------- Microsoft Store package (MSIX)

# Identity of the package. The real values come from Partner Center (Product identity page) once the
# name "Fotohaus" is reserved; these placeholders only make a package for testing.
MSIX_DEV_IDENTITY = ("Fotohaus.Dev", "CN=Fotohaus Dev", "Abdullah Yusuf")

APPX_MANIFEST = """<?xml version="1.0" encoding="utf-8"?>
<Package
  xmlns="http://schemas.microsoft.com/appx/manifest/foundation/windows10"
  xmlns:uap="http://schemas.microsoft.com/appx/manifest/uap/windows10"
  xmlns:uap5="http://schemas.microsoft.com/appx/manifest/uap/windows10/5"
  xmlns:desktop4="http://schemas.microsoft.com/appx/manifest/desktop/windows10/4"
  xmlns:rescap="http://schemas.microsoft.com/appx/manifest/foundation/windows10/restrictedcapabilities"
  IgnorableNamespaces="uap uap5 desktop4 rescap">
  <Identity Name="{name}" Publisher="{publisher}" Version="{version}" ProcessorArchitecture="x64"/>
  <Properties>
    <DisplayName>Fotohaus</DisplayName>
    <PublisherDisplayName>{publisher_name}</PublisherDisplayName>
    <Description>Organize, find and edit your photos, privately</Description>
    <Logo>Assets\\StoreLogo.png</Logo>
  </Properties>
  <Dependencies>
    <TargetDeviceFamily Name="Windows.Desktop" MinVersion="10.0.17763.0" MaxVersionTested="10.0.26100.0"/>
  </Dependencies>
  <Resources>
    <Resource Language="en-us"/>
  </Resources>
  <Applications>
    <Application Id="Fotohaus" Executable="Fotohaus.exe" EntryPoint="Windows.FullTrustApplication">
      <uap:VisualElements DisplayName="Fotohaus" Description="Organize, find and edit your photos, privately"
        BackgroundColor="transparent" Square150x150Logo="Assets\\Square150x150Logo.png"
        Square44x44Logo="Assets\\Square44x44Logo.png">
        <uap:DefaultTile Wide310x150Logo="Assets\\Wide310x150Logo.png" Square310x310Logo="Assets\\Square310x310Logo.png"
          Square71x71Logo="Assets\\Square71x71Logo.png"/>
      </uap:VisualElements>
      <Extensions>
        <uap:Extension Category="windows.fileTypeAssociation">
          <uap:FileTypeAssociation Name="fotohaus.image">
            <uap:DisplayName>Fotohaus Photo</uap:DisplayName>
            <uap:SupportedFileTypes>
{image_types}
            </uap:SupportedFileTypes>
          </uap:FileTypeAssociation>
        </uap:Extension>
        <uap:Extension Category="windows.fileTypeAssociation">
          <uap:FileTypeAssociation Name="fotohaus.video">
            <uap:DisplayName>Fotohaus Video</uap:DisplayName>
            <uap:SupportedFileTypes>
{video_types}
            </uap:SupportedFileTypes>
          </uap:FileTypeAssociation>
        </uap:Extension>
      </Extensions>
    </Application>
    <!-- The command line tool: not in the Start menu, started by typing fotohaus-cli in a terminal. -->
    <Application Id="FotohausCli" Executable="fotohaus-cli.exe" EntryPoint="Windows.FullTrustApplication"
      desktop4:Subsystem="console" desktop4:SupportsMultipleInstances="true">
      <uap:VisualElements DisplayName="Fotohaus command line" Description="Search and organize your Fotohaus library from a terminal"
        BackgroundColor="transparent" Square150x150Logo="Assets\\Square150x150Logo.png"
        Square44x44Logo="Assets\\Square44x44Logo.png" AppListEntry="none"/>
      <Extensions>
        <uap5:Extension Category="windows.appExecutionAlias" Executable="fotohaus-cli.exe"
          EntryPoint="Windows.FullTrustApplication">
          <uap5:AppExecutionAlias desktop4:Subsystem="console">
            <uap5:ExecutionAlias Alias="fotohaus-cli.exe"/>
          </uap5:AppExecutionAlias>
        </uap5:Extension>
      </Extensions>
    </Application>
  </Applications>
  <Capabilities>
    <rescap:Capability Name="runFullTrust"/>
  </Capabilities>
</Package>
"""

# Which of the package's images Windows shows where (sizes, light and dark taskbar) is looked up in
# resources.pri, which makepri builds from the file names in Assets\ using this configuration.
PRI_CONFIG = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<resources targetOsVersion="10.0.0" majorVersion="1">
  <index root="\\" startIndexAt="\\">
    <default>
      <qualifier name="Language" value="en-US"/>
      <qualifier name="Contrast" value="standard"/>
      <qualifier name="Scale" value="100"/>
      <qualifier name="HomeRegion" value="001"/>
      <qualifier name="TargetSize" value="256"/>
      <qualifier name="LayoutDirection" value="LTR"/>
      <qualifier name="Theme" value="dark"/>
      <qualifier name="AlternateForm" value=""/>
      <qualifier name="DXFeatureLevel" value="DX9"/>
      <qualifier name="Configuration" value=""/>
      <qualifier name="DeviceFamily" value="Universal"/>
      <qualifier name="Custom" value=""/>
    </default>
    <indexer-config type="folder" foldernameAsQualifier="true" filenameAsQualifier="true" qualifierDelimiter="."/>
    <indexer-config type="resw" convertDotsToSlashes="true" initialPath=""/>
    <indexer-config type="resjson" initialPath=""/>
    <indexer-config type="PRI"/>
  </index>
</resources>
"""


def msix_version() -> str:
    """Major.minor.patch.0: the Store keeps the fourth number for itself."""
    nums = [int(x) for x in P.__version__.split(".")[:3]] + [0, 0, 0]
    return ".".join(str(x) for x in nums[:3]) + ".0"


def write_msix_assets(assets: Path) -> None:
    """Tiles and icons from the app icon, in the sizes Windows asks for."""
    from PIL import Image
    assets.mkdir(parents=True, exist_ok=True)
    with Image.open(P.SRC / "fotohaus" / "resources" / "icon.png") as im:
        icon = im.convert("RGBA")

    def square(size: int, name: str) -> None:
        icon.resize((size, size), Image.LANCZOS).save(assets / name)

    def padded(w: int, h: int, name: str, fill: float = 0.66) -> None:
        side = int(min(w, h) * fill)
        canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        canvas.paste(icon.resize((side, side), Image.LANCZOS), ((w - side) // 2, (h - side) // 2))
        canvas.save(assets / name)

    square(50, "StoreLogo.png")
    square(100, "StoreLogo.scale-200.png")
    square(44, "Square44x44Logo.png")
    square(88, "Square44x44Logo.scale-200.png")
    for size in (16, 20, 24, 32, 48, 256):      # taskbar, Start list, Alt+Tab, file icons
        square(size, f"Square44x44Logo.targetsize-{size}.png")
        square(size, f"Square44x44Logo.targetsize-{size}_altform-unplated.png")
        square(size, f"Square44x44Logo.targetsize-{size}_altform-lightunplated.png")
    padded(71, 71, "Square71x71Logo.png", 0.8)
    padded(142, 142, "Square71x71Logo.scale-200.png", 0.8)
    padded(150, 150, "Square150x150Logo.png")
    padded(300, 300, "Square150x150Logo.scale-200.png")
    padded(310, 150, "Wide310x150Logo.png")
    padded(620, 300, "Wide310x150Logo.scale-200.png")
    padded(310, 310, "Square310x310Logo.png")
    padded(620, 620, "Square310x310Logo.scale-200.png")


def appx_manifest(name: str, publisher: str, publisher_name: str) -> str:
    """AppxManifest.xml, with every file type Fotohaus opens (from fotohaus/media.py)."""
    from xml.sax.saxutils import escape
    from fotohaus import media

    def types(exts) -> str:
        return "\n".join(f"              <uap:FileType>{e}</uap:FileType>" for e in sorted(exts))

    return APPX_MANIFEST.format(
        name=escape(name, {'"': "&quot;"}), publisher=escape(publisher, {'"': "&quot;"}),
        publisher_name=escape(publisher_name), version=msix_version(),
        image_types=types(media.ALL_EXT - media.VIDEO_EXT), video_types=types(media.VIDEO_EXT))


def build_msix(app: Path, out: Path, name: str, publisher: str, publisher_name: str) -> Path:
    """Pack the app folder as an MSIX for the Microsoft Store."""
    for tool in ("makeappx", "makepri"):
        if not shutil.which(tool):
            raise SystemExit(f"{tool} not on PATH: the MSIX needs the Windows SDK (run from a Developer "
                             "Command Prompt)")
    stage = out / "_msix"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    write_msix_assets(stage / "Assets")
    (stage / "AppxManifest.xml").write_text(appx_manifest(name, publisher, publisher_name), encoding="utf-8")
    (out / "_msix_priconfig.xml").write_text(PRI_CONFIG, encoding="utf-8")
    # index only the manifest and Assets (not the thousands of app files), then add the app itself
    P.run(["makepri", "new", "/o", "/pr", stage, "/cf", out / "_msix_priconfig.xml",
           "/mn", stage / "AppxManifest.xml", "/of", stage / "resources.pri"])
    shutil.copytree(app, stage, dirs_exist_ok=True)
    package = out / f"Fotohaus-{P.__version__}-x64.msix"
    P.run(["makeappx", "pack", "/o", "/h", "SHA256", "/d", stage, "/p", package])
    shutil.rmtree(stage)
    print("Store package:", package, f"{package.stat().st_size / 1e6:.0f} MB", f"({name}, {publisher})", flush=True)
    return package


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
    if not args.iscc and not args.msix:
        return app
    if not launchers:
        raise SystemExit("can't build the installer or the Store package without Fotohaus.exe (cl and rc "
                         "are needed)")
    if args.msix:
        package = build_msix(app, out, args.msix_name, args.msix_publisher, args.msix_publisher_name)
        if not args.iscc:
            return package
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
    ap.add_argument("--msix", action="store_true", help="also pack the Microsoft Store package (MSIX)")
    ap.add_argument("--msix-name", default=os.environ.get("MSIX_IDENTITY_NAME") or MSIX_DEV_IDENTITY[0],
                    help="Package/Identity/Name from Partner Center (or MSIX_IDENTITY_NAME)")
    ap.add_argument("--msix-publisher", default=os.environ.get("MSIX_PUBLISHER") or MSIX_DEV_IDENTITY[1],
                    help="Package/Identity/Publisher from Partner Center, like CN=1234… (or MSIX_PUBLISHER)")
    ap.add_argument("--msix-publisher-name",
                    default=os.environ.get("MSIX_PUBLISHER_DISPLAY_NAME") or MSIX_DEV_IDENTITY[2],
                    help="Package/Properties/PublisherDisplayName from Partner Center "
                         "(or MSIX_PUBLISHER_DISPLAY_NAME)")
    build(ap.parse_args())


if __name__ == "__main__":
    main()
