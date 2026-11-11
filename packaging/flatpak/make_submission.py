#!/usr/bin/env python3
"""Write the files for Flathub into build/flathub/: the manifest with a release tag as its source
(Flathub builds from a tagged release, not from a folder), python-wheels.json and flathub.json.

  python3 packaging/flatpak/make_submission.py            # the tag of this version (v1.2.0)
  python3 packaging/flatpak/make_submission.py --tag v1.2.0

Copy the three files into your fork of flathub/flathub (first submission), or later into
flathub/io.github.aelnagish.Fotohaus (every new version). See packaging/STORES.md.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent.parent
sys.path.insert(0, str(SRC))
from fotohaus import __version__  # noqa: E402

MANIFEST = "io.github.aelnagish.Fotohaus.yml"
DIR_SOURCE = """      - type: dir
        path: ../..
        skip: [.git, .github, build, .flatpak-builder, docs, tests]
"""
GIT_SOURCE = """      - type: git
        url: https://github.com/aelnagish/Fotohaus.git
        tag: {tag}
        commit: {commit}
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tag", default=f"v{__version__}")
    ap.add_argument("--out", default=str(SRC / "build" / "flathub"))
    args = ap.parse_args()
    try:
        commit = subprocess.run(["git", "-C", str(SRC), "rev-list", "-n", "1", args.tag], check=True,
                                capture_output=True, text=True).stdout.strip()
    except subprocess.CalledProcessError:
        raise SystemExit(f"there is no tag {args.tag} here yet: release that version first (git fetch --tags)")
    text = (HERE / MANIFEST).read_text(encoding="utf-8")
    if DIR_SOURCE not in text:
        raise SystemExit(f"{MANIFEST}: the source lines changed; update DIR_SOURCE in this script")
    lines = text.replace(DIR_SOURCE, GIT_SOURCE.format(tag=args.tag, commit=commit)).splitlines(keepends=True)
    # the notes at the top are about building inside this repository; Flathub's copy starts at "id:"
    start = next(i for i, line in enumerate(lines) if line.startswith("id:"))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / MANIFEST).write_text("".join(lines[start:]), encoding="utf-8")
    for name in ("python-wheels.json", "flathub.json"):
        shutil.copy2(HERE / name, out / name)
    print(f"Flathub files for {args.tag} ({commit[:12]}) in {out}:")
    for f in sorted(out.iterdir()):
        print("  ", f.name)


if __name__ == "__main__":
    main()
