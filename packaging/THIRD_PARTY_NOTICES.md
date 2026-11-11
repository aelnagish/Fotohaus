# Third-party components in Fotohaus

Fotohaus is free software under the GNU General Public License, version 3 or later (see `LICENSE`).
The Mac app, the Windows installer and the Linux AppImage also contain the components below, each
under its own license. Full license texts are in the **Licenses** folder that comes with the app:

- Mac: `Fotohaus.app/Contents/Resources/Licenses/`
- Windows: `Licenses\` in the Fotohaus program folder
- Linux: `usr/share/licenses/fotohaus/` inside the AppImage

## On every system

| Component | License |
|---|---|
| CPython 3.12 (python-build-standalone) | PSF License |
| Qt 6.9, PySide6 6.9.3, Shiboken6 (incl. Qt Multimedia, Qt Print Support) and the libraries Qt bundles | LGPL-3.0; bundled libraries under permissive licenses |
| FFmpeg libraries inside Qt Multimedia (LGPL build) | LGPL-2.1 or later |
| OpenCV 5 (opencv-python-headless 5.0.0.93) | Apache-2.0 |
| PyAV 19.0.1 (Movie Maker) | BSD-3-Clause |
| — with FFmpeg 9.0.2 (built with x264 and x265, `--enable-version3`) | GPL-3.0 or later |
| — with x264 (core 165, commit b35605ac) and x265 4.3+1 (commit e9b8812) | GPL-2.0 or later |
| pillow-heif 1.8.1 | BSD-3-Clause |
| — with libheif 1.23.6 and libde265 0.2.3 | LGPL-3.0 |
| — with x265 4.3+1 (commit e9b8812, HEIC encoding) | GPL-2.0 or later |
| NumPy | BSD-3-Clause |
| Pillow, with libjpeg-turbo, libpng, libtiff, libwebp, libavif, OpenJPEG, FreeType, HarfBuzz, Little CMS, zlib-ng and others | MIT-CMU (HPND) and permissive licenses (BSD, MIT, zlib, FTL) |
| rawpy, with LibRaw | MIT; LibRaw LGPL-2.1 or CDDL-1.0 |
| ONNX Runtime | MIT |
| Hugging Face tokenizers | Apache-2.0 |
| piexif | MIT |
| YuNet face detector (OpenCV Zoo) | MIT |
| SFace face recognizer (OpenCV Zoo) | Apache-2.0 |
| Place names (`fotohaus/data/`, from GeoNames cities1000) | CC BY 4.0, © GeoNames (geonames.org) |
| CLIP ViT-B/32 (optional download, not bundled) | MIT (OpenAI) |
| Map tiles (loaded at runtime, not bundled) | © OpenStreetMap contributors, ODbL |

## Mac only

| Component | License |
|---|---|
| FFmpeg 7.1.1 inside OpenCV (built with `--enable-gpl --enable-version3`), incl. libpostproc | GPL-3.0 or later |
| — with x264 (core 164, revision 31e19f92) and x265 4.1+1 (commit 1d117be) | GPL-2.0 or later |
| PyObjC (Cocoa, Quartz, Vision, UniformTypeIdentifiers, LocalAuthentication) | MIT |

## Windows only

| Component | License |
|---|---|
| FFmpeg 7.1 inside OpenCV (`opencv_videoio_ffmpeg500_64.dll`, LGPL build with libaom, libvpx and OpenH264) | LGPL-2.1 or later; libaom, libvpx and OpenH264 BSD-2/3-Clause |
| Microsoft Visual C++ runtime (`vcruntime140.dll`, `vcruntime140_1.dll`) | Microsoft Visual C++ Redistributable license |
| winrt-runtime and Windows.Foundation / Windows.Security.Credentials.UI projections (Windows Hello) | MIT |
| Installer built with Inno Setup | Inno Setup License (permissive) |

## Linux only

| Component | License |
|---|---|
| FFmpeg 8.1.1 inside OpenCV (LGPL build, no x264/x265) | LGPL-2.1 or later |
| OpenBLAS (inside OpenCV) | BSD-3-Clause |
| libgfortran and libquadmath (inside OpenCV) | GPL-3.0 with the GCC Runtime Library Exception; libquadmath LGPL-2.1 |
| OpenSSL 1.1.1k (inside OpenCV, used by its FFmpeg) | OpenSSL License and SSLeay License |
| libdrm, libavif, libpng and libaom (inside OpenCV) | MIT, BSD-2-Clause, libpng license, BSD-2-Clause |
| libxcb-cursor, libxcb-image, libxcb-render-util, libxcb-util (from Ubuntu) | MIT/X11 |
| AppImage runtime | MIT |

The other libraries bundled inside the OpenCV and PyAV wheels (for example dav1d, libvpx, Opus,
SVT-AV1, libaom, OpenEXR) are under permissive or LGPL licenses; their license texts are in
`opencv_python_headless-LICENSE-3RD-PARTY.txt` and `av-LICENSE.txt` in the Licenses folder.

## Source code

- **Fotohaus:** https://github.com/aelnagish/fotohaus (each release has the matching source).
- **GPL components** (FFmpeg 7.1.1 and 9.0.2, x264 and x265 at the exact revisions above, and the
  build scripts used for the OpenCV and PyAV wheels): the archive
  `fotohaus-1.0.3-gpl-sources.tar` attached to the same GitHub release as the app. The same archive
  covers all three systems: PyAV and pillow-heif ship identical GPL builds on Mac, Windows and
  Linux, and OpenCV's GPL build is only in the Mac app.
- **LGPL components** (Qt, PySide6, FFmpeg in Qt Multimedia, FFmpeg 7.1 and 8.1.1 in OpenCV on
  Windows and Linux, libheif, libde265, LibRaw): https://download.qt.io, https://ffmpeg.org,
  https://github.com/opencv/opencv-python, https://github.com/strukturag/libheif,
  https://github.com/strukturag/libde265, https://www.libraw.org. These libraries are dynamically
  linked and can be replaced with your own builds (they are in the app's `site-packages` folder).

**Written offer.** For at least three years after each release, and for as long as that release is
offered, the author will provide the complete corresponding source code of any GPL- or
LGPL-licensed component in the app, for no more than the cost of distribution. Ask by opening an
issue at https://github.com/aelnagish/fotohaus/issues.

## Picasa

No code, icons, images or text from Picasa are included. Picasa compatibility is implemented from
public, community documentation of the `.picasa.ini` format.
