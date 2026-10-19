# Third-party components in Fotohaus.app

Fotohaus's own code is licensed under the Fotohaus License (MIT with a no-resale condition; see
`LICENSE`). The app bundle also contains the components below, each under its own license. Full
license texts are in `Fotohaus.app/Contents/Resources/Licenses/`.

Fotohaus.app contains **no GPL-licensed code**. Video and HEIC photos are handled by macOS's own
frameworks (AVFoundation and ImageIO), and image effects and face recognition are implemented in
Fotohaus itself on top of NumPy and ONNX Runtime. The build script checks every bundled library and
refuses to build if a GPL-only component (such as x264, x265 or a GPL build of FFmpeg) is found.

| Component | License |
|---|---|
| CPython 3.12 (python-build-standalone) | PSF License |
| Qt 6, PySide6, Shiboken6 (incl. Qt Multimedia, Qt Print Support) | LGPL-3.0 |
| FFmpeg 9.0.1 libraries shipped inside Qt Multimedia (avcodec, avformat, avutil, swresample, swscale) | LGPL-2.1 or later (built without `--enable-gpl`) |
| NumPy | BSD-3-Clause |
| Pillow, with libjpeg-turbo, libpng, libtiff, libwebp, libavif, OpenJPEG, FreeType, HarfBuzz, Little CMS, zlib-ng, Brotli, XZ, libxcb | MIT-CMU (HPND); bundled libraries under BSD, MIT, zlib, FreeType (FTL) and similar permissive licenses |
| rawpy, with LibRaw, JasPer, libjpeg-turbo, Little CMS | MIT; LibRaw LGPL-2.1 or CDDL-1.0; JasPer license (MIT-style) |
| ONNX Runtime | MIT |
| Hugging Face tokenizers | Apache-2.0 |
| piexif | MIT |
| PyObjC (core, Cocoa, Quartz, Vision, AVFoundation, CoreMedia, CoreAudio, UniformTypeIdentifiers, LocalAuthentication) | MIT |
| YuNet face detector (`face_detection_yunet_2023mar.onnx`, OpenCV Zoo) | MIT. Modified by Fotohaus: the model's input size was made flexible; weights are unchanged. |
| SFace face recognizer (`face_recognition_sface_2021dec.onnx`, OpenCV Zoo) | Apache-2.0 |
| Place names (`fotohaus/data/`, from GeoNames cities1000) | CC BY 4.0, © GeoNames (geonames.org) |
| CLIP ViT-B/32 (optional download, not bundled) | MIT (OpenAI) |
| Map tiles (loaded at runtime, not bundled) | © OpenStreetMap contributors, ODbL |

## LGPL components

Qt, PySide6, the FFmpeg libraries inside Qt Multimedia and LibRaw are dynamically linked and are
used unmodified. You may replace them with your own builds: the Qt and FFmpeg libraries are in
`Contents/lib/python3.12/site-packages/PySide6/Qt/lib`, LibRaw is inside
`Contents/lib/python3.12/site-packages/rawpy`.

Their source code is available from the upstream projects:

- Qt: https://download.qt.io/official_releases/qt/ (and https://code.qt.io)
- PySide6 and Shiboken6: https://download.qt.io/official_releases/QtForPython/
- FFmpeg: https://ffmpeg.org/download.html (release 9.0.1)
- LibRaw: https://www.libraw.org/download

For three years after each Fotohaus release, the author will also provide the complete source code
of these LGPL components, as shipped, on request: open an issue at
https://github.com/aelnagish/fotohaus/issues.

## Picasa

No code, icons, images or text from Picasa are included. Picasa compatibility is implemented from
public, community documentation of the `.picasa.ini` format.
