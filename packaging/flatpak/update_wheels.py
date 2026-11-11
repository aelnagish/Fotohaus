#!/usr/bin/env python3
"""Write python-wheels.json: the wheels the Flatpak installs, with their addresses and checksums.

Flathub builds without network access, so every file has to be listed up front. The versions are the
ones pinned in packaging/constraints.txt (the same as the Mac app, the Windows installer and the
AppImage), resolved by pip for the Python of the Flatpak runtime. Packages the AppImage build removes
again (packaging/_portable.py, SITE_REMOVE) are left out. LIEF is added: install.py uses it to keep
only the parts of Qt that Fotohaus needs.

Run it after changing constraints.txt or the runtime version in the manifest:
  python3 packaging/flatpak/update_wheels.py [--python 3.13]
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import _portable as P  # noqa: E402

BUILD_TOOLS = ["lief"]


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--python", default="3.13", help="Python version of the Flatpak runtime (default 3.13)")
    args = ap.parse_args()
    with tempfile.TemporaryDirectory() as td:
        report = Path(td) / "report.json"
        cmd = [sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check", "--dry-run",
               "--ignore-installed", "--report", report, "--target", Path(td) / "t",
               "--python-version", args.python, "--implementation", "cp", "--only-binary=:all:",
               "-c", P.CONSTRAINTS]
        for plat in P.PIP_PLATFORMS["linux"]:
            cmd += ["--platform", plat]
        subprocess.run([str(c) for c in cmd + P.REQUIREMENTS + BUILD_TOOLS], check=True)
        items = json.loads(report.read_text())["install"]
    dropped = {norm(r) for r in P.SITE_REMOVE if "/" not in r} | {"pyyaml", "hf-xet", "huggingface-hub", "protobuf"}
    sources = []
    for it in sorted(items, key=lambda i: norm(i["metadata"]["name"])):
        name = norm(it["metadata"]["name"])
        if name in dropped:
            continue
        info = it["download_info"]
        sources.append({"type": "file", "url": info["url"], "sha256": info["archive_info"]["hashes"]["sha256"],
                        "dest": "wheels"})
        print(f"{name:28} {it['metadata']['version']:12} {info['url'].rsplit('/', 1)[1]}")
    (HERE / "python-wheels.json").write_text(json.dumps(sources, indent=2) + "\n")
    print(f"\n{len(sources)} wheels for Python {args.python} -> {HERE / 'python-wheels.json'}")


if __name__ == "__main__":
    main()
