# Third-party components in Fotohaus.app

Fotohaus is free software under the GNU General Public License, version 3 or later (see `LICENSE`).
The app bundle also contains the components below, each under its own license. Full license texts
are in `Fotohaus.app/Contents/Resources/Licenses/`.

| Component | License |
|---|---|
| CPython 3.12 (python-build-standalone) | PSF License |
| Qt 6.9, PySide6 6.9.3, Shiboken6 (incl. Qt Multimedia, Qt Print Support) | LGPL-3.0 |
| FFmpeg 7.1.1 libraries inside Qt Multimedia (LGPL build) | LGPL-2.1 or later |
| OpenCV 5 (opencv-python-headless 5.0.0.93) | Apache-2.0 |
| — with FFmpeg 7.1.1 (built with `--enable-gpl --enable-version3`), incl. libpostproc | GPL-3.0 or later |
| — with x264 (core 164, revision 31e19f92) and x265 4.1+1 (commit 1d117be) | GPL-2.0 or later |
| PyAV 19.0.1 (Movie Maker) | BSD-3-Clause |
| — with FFmpeg 9.0.2 (built with x264 and x265, `--enable-version3`) | GPL-3.0 or later |
| — with x264 (core 165, commit b35605ac) and x265 4.3 | GPL-2.0 or later |
| pillow-heif 1.8.1 | BSD-3-Clause |
| — with libheif 1.23.6 and libde265 | LGPL-3.0 |
| — with x265 4.3 (HEIC encoding) | GPL-2.0 or later |
| NumPy | BSD-3-Clause |
| Pillow, with libjpeg-turbo, libpng, libtiff, libwebp, libavif, OpenJPEG, FreeType, HarfBuzz, Little CMS, zlib-ng and others | MIT-CMU (HPND) and permissive licenses (BSD, MIT, zlib, FTL) |
| rawpy, with LibRaw | MIT; LibRaw LGPL-2.1 or CDDL-1.0 |
| ONNX Runtime | MIT |
| Hugging Face tokenizers | Apache-2.0 |
| piexif | MIT |
| PyObjC | MIT |
| YuNet face detector, SFace face recognizer (OpenCV Zoo) | MIT / Apache-2.0 |
| Place names (`fotohaus/data/`, from GeoNames cities1000) | CC BY 4.0, © GeoNames (geonames.org) |
| CLIP ViT-B/32 (optional download, not bundled) | MIT (OpenAI) |
| Map tiles (loaded at runtime, not bundled) | © OpenStreetMap contributors, ODbL |

The other libraries bundled inside the OpenCV and PyAV wheels (for example dav1d, libvpx, Opus,
SVT-AV1, libaom, libwebp, OpenEXR) are under permissive or LGPL licenses; their license texts are in
`opencv_python_headless-LICENSE-3RD-PARTY.txt` and `av-LICENSE.txt` in the Licenses folder.

## Source code

- **Fotohaus:** https://github.com/aelnagish/fotohaus (each release has the matching source).
- **GPL components** (FFmpeg 7.1.1 and 9.0.2, x264 and x265 at the exact revisions above, and the
  build scripts used for the OpenCV and PyAV wheels): the archive
  `fotohaus-1.0.1-gpl-sources.tar` attached to the same GitHub release as the app.
- **LGPL components** (Qt, PySide6, FFmpeg in Qt Multimedia, libheif, libde265, LibRaw):
  https://download.qt.io, https://ffmpeg.org, https://github.com/strukturag/libheif,
  https://github.com/strukturag/libde265, https://www.libraw.org. These libraries are dynamically
  linked and can be replaced with your own builds (they are in
  `Contents/lib/python3.12/site-packages`).

**Written offer.** For at least three years after each release, and for as long as that release is
offered, the author will provide the complete corresponding source code of any GPL- or
LGPL-licensed component in the app, for no more than the cost of distribution. Ask by opening an
issue at https://github.com/aelnagish/fotohaus/issues.

## Picasa

No code, icons, images or text from Picasa are included. Picasa compatibility is implemented from
public, community documentation of the `.picasa.ini` format.
