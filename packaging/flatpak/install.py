#!/usr/bin/env python3
"""Install Fotohaus into the Flatpak (/app). Run by the manifest from the top of the source tree, inside
the Flatpak build, where there is no network: the wheels were downloaded by flatpak-builder into
wheels/ (python-wheels.json).

It can also write just the menu entry and the AppStream description, to look at or validate them:
  python3 packaging/flatpak/install.py --metadata-only build/flatpak-metadata
"""
from __future__ import annotations

import argparse
import html
import importlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent.parent
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(HERE.parent))

APP_ID = "io.github.aelnagish.Fotohaus"
REPO = "https://github.com/aelnagish/Fotohaus"
SUMMARY = "Private photo organizer and viewer"

LAUNCHER = """#!/bin/sh
# Python finds Fotohaus and its libraries in /app; -s keeps packages from ~/.local out of it.
unset PYTHONHOME PYTHONPATH
exec python3 -s -B -m {module} "$@"
"""

DESKTOP = """[Desktop Entry]
Type=Application
Name=Fotohaus
GenericName=Photo Organizer
Comment=Organize, find and edit your photos, privately
Exec=fotohaus %F
Icon={app_id}
Terminal=false
StartupWMClass=Fotohaus
Categories=Graphics;Photography;Viewer;
Keywords=photos;pictures;faces;albums;viewer;raw;
MimeType={mime};
"""

METAINFO = """<?xml version="1.0" encoding="UTF-8"?>
<component type="desktop-application">
  <id>{app_id}</id>
  <name>Fotohaus</name>
  <summary>{summary}</summary>
  <developer id="io.github.aelnagish">
    <name>Abdullah Yusuf</name>
  </developer>
  <metadata_license>CC0-1.0</metadata_license>
  <project_license>GPL-3.0-or-later</project_license>
  <launchable type="desktop-id">{app_id}.desktop</launchable>
  <description>
    <p>
      Fotohaus is a fast, private photo viewer and organizer. Point it at the folders already on your
      computer, and years of photos are sorted, searchable and easy to show off. Nothing is copied into
      a hidden library and nothing leaves your computer.
    </p>
    <ul>
      <li>Open a photo from your file manager and flip through its folder with the arrow keys</li>
      <li>Library by folder and date, with stars, captions, nested tags and albums</li>
      <li>Faces: name someone once and Fotohaus suggests the same person across the years</li>
      <li>Search by person, place, tag and date, or with the optional search model by description</li>
      <li>Places map with real place names, worked out offline</li>
      <li>Memories: photos taken on this day in earlier years</li>
      <li>Edits that never touch your originals: enhance, crop, straighten, light, colour and effects</li>
      <li>Stacks for RAW and JPEG pairs, bursts and Live Photos</li>
      <li>A password-locked Private collection, an Archive, and 30 days to undo a delete</li>
      <li>Collages, slideshows, movies with music, web galleries and backups</li>
    </ul>
    <p>
      Fotohaus has no account, no analytics and no tracking. It only goes online for map tiles, place
      search and the optional search model, and each of them can stay off.
    </p>
  </description>
  <categories>
    <category>Graphics</category>
    <category>Photography</category>
    <category>Viewer</category>
  </categories>
  <keywords>
    <keyword>photos</keyword>
    <keyword>pictures</keyword>
    <keyword>faces</keyword>
    <keyword>albums</keyword>
    <keyword>raw</keyword>
  </keywords>
  <url type="homepage">https://aelnagish.github.io/Fotohaus/</url>
  <url type="bugtracker">{repo}/issues</url>
  <url type="vcs-browser">{repo}</url>
  <url type="donation">https://buymeacoffee.com/aelnagish</url>
  <provides>
    <binary>fotohaus</binary>
    <binary>fotohaus-cli</binary>
  </provides>
  <supports>
    <control>pointing</control>
    <control>keyboard</control>
  </supports>
  <requires>
    <display_length compare="ge">768</display_length>
  </requires>
  <content_rating type="oars-1.1"/>
  <screenshots>
    <screenshot type="default">
      <caption>The library, grouped by folder</caption>
      <image>{raw}/docs/library.jpg</image>
    </screenshot>
    <screenshot>
      <caption>The photo viewer, with the rest of the folder one arrow key away</caption>
      <image>{raw}/docs/viewer.jpg</image>
    </screenshot>
    <screenshot>
      <caption>Editing with live previews and an edit history</caption>
      <image>{raw}/docs/editor.jpg</image>
    </screenshot>
    <screenshot>
      <caption>Memories: photos from this day in earlier years</caption>
      <image>{raw}/docs/memories.jpg</image>
    </screenshot>
  </screenshots>
  <releases>
{releases}
  </releases>
</component>
"""


def run(cmd) -> None:
    print("+", " ".join(str(c) for c in cmd), flush=True)
    subprocess.run([str(c) for c in cmd], check=True)


def desktop_entry() -> str:
    from fotohaus import macos
    return DESKTOP.format(app_id=APP_ID, mime=";".join(macos.LINUX_VIEWER_MIME))


def _plain(text: str) -> str:
    """Changelog Markdown as plain text: **bold**, `code`, [links](…) and the ▸ menu arrows stay readable."""
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"[*`]", "", text)
    return html.escape(re.sub(r"\s+", " ", text).strip(), quote=False)


def releases(limit: int = 6) -> str:
    """<release> entries from CHANGELOG.md ("## 1.0.3 — 2026-11-11", then a list)."""
    out, items, head = [], [], None

    def flush():
        if head:
            # a point that only introduces the points under it ("Groundwork for …:") is left out
            lis = "\n".join(f"          <li>{_plain(i)}</li>" for i in items if not i.rstrip().endswith(":"))
            body = f"\n      <description>\n        <ul>\n{lis}\n        </ul>\n      </description>\n    " if items else ""
            out.append(f'    <release version="{head[0]}" date="{head[1]}">{body}</release>')

    for line in (SRC / "CHANGELOG.md").read_text(encoding="utf-8").splitlines():
        m = re.match(r"##\s+(\d+(?:\.\d+)+)\s+\S+\s+(\d{4}-\d{2}-\d{2})", line)
        if m:
            flush()
            head, items = (m.group(1), m.group(2)), []
        elif head and re.match(r"\s*- ", line):                  # a point, or a point under a point
            items.append(line.split("- ", 1)[1])
        elif head and items and line.startswith(" ") and line.strip():
            items[-1] += " " + line                               # a wrapped line of the same point
    flush()
    return "\n".join(out[:limit])


def metainfo() -> str:
    from fotohaus import __version__
    return METAINFO.format(app_id=APP_ID, summary=SUMMARY, repo=REPO, releases=releases(),
                           raw=f"https://raw.githubusercontent.com/aelnagish/Fotohaus/v{__version__}")


def write_metadata(share: Path) -> None:
    from PIL import Image
    (share / "applications").mkdir(parents=True, exist_ok=True)
    (share / "metainfo").mkdir(parents=True, exist_ok=True)
    (share / "applications" / f"{APP_ID}.desktop").write_text(desktop_entry(), encoding="utf-8")
    (share / "metainfo" / f"{APP_ID}.metainfo.xml").write_text(metainfo(), encoding="utf-8")
    with Image.open(SRC / "fotohaus" / "resources" / "icon.png") as im:
        for size in (512, 256, 128, 64):
            d = share / "icons" / "hicolor" / f"{size}x{size}" / "apps"
            d.mkdir(parents=True, exist_ok=True)
            im.resize((size, size), Image.LANCZOS).save(d / f"{APP_ID}.png")


def install(app: Path) -> None:
    pyver = f"{sys.version_info.major}.{sys.version_info.minor}"
    site = app / "lib" / f"python{pyver}" / "site-packages"
    wheels = sorted((SRC / "wheels").glob("*.whl"))
    if not wheels:
        raise SystemExit("no wheels in wheels/: python-wheels.json is missing from the manifest's sources")
    tools = [w for w in wheels if w.name.lower().startswith("lief-")]
    libs = [w for w in wheels if w not in tools]
    pip = [sys.executable, "-m", "pip", "install", "--no-index", "--no-deps", "--no-compile",
           "--disable-pip-version-check", "--no-warn-script-location"]
    try:
        run(pip + ["--prefix", app] + libs)
        run(pip + ["--target", SRC / "_tools"] + tools)        # LIEF only reads Qt's libraries, below
    except subprocess.CalledProcessError:
        raise SystemExit(f"\nThe wheels don't fit this runtime's Python {pyver}. Write the list again with\n"
                         f"  python3 packaging/flatpak/update_wheels.py --python {pyver}\n")
    if not (site / "PySide6").is_dir():
        raise SystemExit(f"expected the libraries in {site}")
    sys.path.insert(0, str(SRC / "_tools"))
    sys.path.insert(0, str(site))                               # pillow, for the icons
    # Python looked into /app's library folder when it started, before anything was in it, and
    # remembers that it was missing: make it look again
    sys.path_importer_cache.clear()
    importlib.invalidate_caches()
    import _portable as P
    P.prune_site(site)
    P.prune_qt(site, "linux")
    P.add_package(site)
    P.link_duplicates(app / "lib")
    P.collect_licenses(site, app / "share" / "licenses" / APP_ID, None)
    bindir = app / "bin"
    if bindir.exists():
        shutil.rmtree(bindir)                                   # the libraries' own command line tools
    bindir.mkdir(parents=True)
    for name, module in (("fotohaus", "fotohaus"), ("fotohaus-cli", "fotohaus.cli")):
        (bindir / name).write_text(LAUNCHER.format(module=module))
        os.chmod(bindir / name, 0o755)
    write_metadata(app / "share")
    P.compile_pyc(sys.executable, site)
    print("Installed:", app, P.du(app), flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--metadata-only", metavar="DIR", help="only write the menu entry, description and icons here")
    args = ap.parse_args()
    if args.metadata_only:
        write_metadata(Path(args.metadata_only))
    else:
        install(Path(os.environ.get("FLATPAK_DEST", "/app")))


if __name__ == "__main__":
    main()
