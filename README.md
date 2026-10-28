<div align="center">

# Fotohaus

**Your photos. Your Mac. Nobody else's business.**

A fast, private photo organizer for macOS that finds faces, reads text in photos, edits without
touching your originals, and never uploads a single pixel.

[![macOS](https://img.shields.io/badge/macOS%2014%2B-Apple%20Silicon-black?logo=apple)](#install)
[![License: GPL v3](https://img.shields.io/badge/license-GPL--3.0--or--later-blue)](LICENSE)
[![Buy Me a Coffee](https://img.shields.io/badge/Buy%20me%20a%20coffee-%E2%98%95-FFDD00?logo=buymeacoffee&logoColor=black)](https://buymeacoffee.com/aelnagish)

**[Website](https://aelnagish.github.io/Fotohaus/)** · **[Download](https://github.com/aelnagish/Fotohaus/releases/latest)** · **[Install guide](INSTALL.md)**

<img src="docs/demo.gif" alt="Fotohaus demo: flipping through a folder with the arrow keys in the photo viewer, then browsing the library grouped by folder with details in the info panel" width="100%">

</div>

---

*Fotohaus*: one home for every picture you've ever taken.

Point it at the folders already on your Mac, and in a few minutes years of photos are sorted,
searchable and easy to show off. Nothing is copied into a hidden library and nothing leaves your
computer. Your files stay exactly where they are.

## Why Fotohaus

- **Private by design.** Face recognition, search and editing all run on your Mac. No account,
  no cloud, no tracking.
- **Your folders, untouched.** No import into a hidden library. Fotohaus works on the folders you
  already have, and every edit is reversible.
- **Fast.** Open a photo from Finder and it appears instantly over your desktop, with the rest of
  the folder one arrow key away.
- **Clever where it counts.** Search for "dog on the beach", "receipt" or a friend's name and get
  answers, not filenames.

## Flip through a folder in a blink

Double-click any photo in Finder and it opens instantly over your desktop. Then just press
**←** and **→** to glide through every other photo in the same folder. No importing, no waiting,
no library to set up first. Zoom with the scroll wheel, star or rotate as you go, and press
**Esc** to get back to what you were doing.

<div align="center">
<img src="docs/viewer.jpg" alt="Fotohaus photo viewer open over the desktop, with a filmstrip of the folder and a control bar" width="100%">
</div>

## On this day

Each day Fotohaus looks back through your library for pictures taken on today's date in
earlier years, and gathers them into **Memories**: "2 years ago", "3 years ago", ready to play as a
slideshow. Each photo also knows where it was taken ("Lauterbrunnen, Bern, Switzerland"), worked out
on your Mac from a built-in atlas, so you can simply search for a town.

<div align="center">
<img src="docs/memories.jpg" alt="Fotohaus Memories: photos from this day two, three and four years ago, with the place shown in the info panel" width="100%">
</div>

## Some photos are just for you

Select pictures and choose **Move to Private**. They vanish from the library, search, Memories,
People and Places, and the **Private** collection opens only with **Touch ID** (or your Mac
password). It locks again when you quit.

Two quieter places help keep the library tidy:

- **Archive** tucks away screenshots, receipts and the photos you want to keep but not see every
  day. They stay on disk and come back with one click.
- **Recently Deleted** holds anything you delete for **30 days**, so a slip of the finger is never
  final. Only then do files move to the Mac's Trash.

## Find anyone, and anything

Name a face once and Fotohaus suggests the same person across the years, all on your Mac. Search
understands people, places, tags and dates (`person:anna place:zermatt year:2019`), the objects and
scenes in a photo ("dog", "beach"), and even the **words in it**: type "invoice" or a street sign
and find the picture.

## Bursts, RAW pairs and Live Photos, tidied up

A burst of twelve near-identical shots, the RAW and JPEG of the same moment, the short video
behind an iPhone Live Photo: Fotohaus folds each into one **stack** with a small badge, so the
library shows moments, not clutter. Open a Live Photo and press **L** to see it move.

## Coming from Picasa?

Point Fotohaus at your old Picasa folders and your stars, captions, albums, face tags and edits
are there, read straight from the `.picasa.ini` files Picasa left behind. Fotohaus keeps writing
them, so nothing is locked in.

## What it does

<table>
<tr>
<td width="50%" valign="top">

### Organize
- Library grouped by folder and date, with stars, captions, tags and albums
- **Photo Tray**: hold pictures from different folders and act on them together
- Timeline and **Places** map with real place names ("Zermatt, Switzerland"), worked out offline
- **Memories**: "On this day" photos from earlier years, right at the top of your library
- **Stacks**: RAW+JPEG pairs, bursts and Live Photos fold into one thumbnail
- **Archive**, a Touch ID-locked **Private** collection, and **Recently Deleted** (30 days to change your mind)
- Folder tools: rename, move, split, hide, batch rename, fix dates
- Import from cameras and cards, skipping duplicates

</td>
<td width="50%" valign="top">

### Find
- **Faces**: on-device detection and recognition. Name someone once and Fotohaus suggests the rest
- **Smart search** for objects, scenes and text inside photos, using Apple's on-device Vision
- Optional natural-language search ("kids playing in the snow")
- Duplicate and near-duplicate finder that keeps the best copy
- Nested tags like `Trips/2024/Alps`, browsable as a tree

</td>
</tr>
<tr>
<td valign="top">

### Edit
- One-click fixes: auto enhance, contrast, colour, straighten, crop, red-eye
- Fine tuning for light, shadows, highlights and colour temperature
- More than 40 creative effects, plus retouch, text, smart enhance, denoise and clarity
- Non-destructive: originals are kept and every edit can be undone
- Copy and paste a whole look onto other photos, or batch edit at once

</td>
<td valign="top">

### Share and create
- Collages (six styles), slideshows, and **movies with music**
- Posters, contact sheets, passport photos, and print or PDF
- Web gallery export, map export, email and the macOS Share menu
- Backups to any drive, with incremental updates and restore

</td>
</tr>
</table>

<div align="center">
<img src="docs/editor.jpg" alt="Fotohaus editor with live effect previews, a filmstrip and an edit history" width="100%">
</div>

**Reads what your camera writes:** JPEG, HEIC/HEIF, AVIF, PNG, TIFF, WebP, GIF, PSD, camera RAW
(CR2, CR3, NEF, ARW, DNG, RAF and more) and common video formats, colour-managed for accurate
display. Captions and tags can be written into the files themselves, so other apps see them too.

**Comes with a command line, too.** `fotohaus-cli search "beach 2019"` searches from the Terminal,
and `fotohaus-cli mcp` lets AI assistants that support MCP search and organize your library.

## Install

Fotohaus runs on Apple Silicon Macs (M1 or newer) with macOS 14 Sonoma or newer.

**With Homebrew** (opens without the first-launch warning, and adds `fotohaus-cli`):

```bash
brew install --cask aelnagish/tap/fotohaus
```

**Or download it yourself:**

1. Download `Fotohaus-1.0.1-arm64.dmg` from [Releases](../../releases).
2. Open the disk image and drag **Fotohaus** into **Applications**.
3. Open Fotohaus. It isn't notarized by Apple yet, so macOS blocks it the first time:
   - If macOS says it **"is damaged and can't be opened"**, click *Cancel*, then run this once in
     Terminal: `xattr -cr /Applications/Fotohaus.app`. Nothing is wrong with the app; this is how
     macOS treats apps from the internet that aren't notarized.
   - If it says it **"could not verify"** Fotohaus, open *System Settings ▸ Privacy & Security*,
     scroll down and click **Open Anyway** next to the message about Fotohaus.
4. To open photos in Fotohaus when you double-click them, use *Tools ▸ Options ▸ Photo Viewer*.

The full guide, including the command line, the Claude (MCP) setup and how to uninstall, is in
[INSTALL.md](INSTALL.md).

### Run from source

```bash
./run.sh                     # first run sets everything up, then opens the app
# or manually:
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m fotohaus            # app
python -m fotohaus.cli --help # command line
```

Your library database lives in `~/Library/Application Support/Fotohaus` (set `FOTOHAUS_HOME` to use
a different place). Run the tests with `pip install -r requirements-dev.txt`, then `python tests/test_fotohaus.py` and
`python tests/test_review.py`. To build the app and disk
image, see `packaging/build_mac_app.py`.

## Support

Fotohaus is free, built to make everyday things a little easier. If it saved you an
afternoon of sorting photos, you can [buy me a coffee ☕](https://buymeacoffee.com/aelnagish).

Bug reports and ideas are welcome in [Issues](../../issues). Pull requests are covered by
[CONTRIBUTING.md](CONTRIBUTING.md).

## License

Fotohaus is free software, licensed under the **GNU General Public License, version 3 or later**
(see [LICENSE](LICENSE)). You may use, study, share and change it; if you distribute it, modified
or not, you must pass on the same freedoms and the source code. Copyright © 2026 Abdullah Yusuf.

The app bundles third-party components under their own licenses, listed in
[`packaging/THIRD_PARTY_NOTICES.md`](packaging/THIRD_PARTY_NOTICES.md). The source code of the
GPL libraries inside the app (FFmpeg, x264, x265) is attached to each release as
`fotohaus-1.0.1-gpl-sources.tar`.

<sub>Screenshots use freely licensed sample images from the scikit-image, imageio and Pillow
projects. Fotohaus is an independent project.</sub>
