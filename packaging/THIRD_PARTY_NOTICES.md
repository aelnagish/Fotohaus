# Third-party components in Fotohaus.app

Fotohaus's own code is licensed under the Fotohaus License (MIT with a no-resale condition; see LICENSE). The app bundle redistributes these components,
unmodified, under their own licenses (full texts are in this folder where the
upstream package ships them):

| Component | License |
|---|---|
| CPython 3.12 (python-build-standalone) | PSF License |
| Qt 6 / PySide6 / Shiboken6 | LGPL-3.0 (Qt is dynamically linked; the Qt libraries in `Contents/lib/python3.12/site-packages/PySide6/Qt/lib` can be replaced) |
| OpenCV (opencv-python-headless), incl. FFmpeg | Apache-2.0; FFmpeg LGPL-2.1 |
| YuNet face detector, SFace face recognizer (OpenCV Zoo) | MIT / Apache-2.0 |
| NumPy | BSD-3-Clause |
| Pillow | MIT-CMU (HPND) |
| pillow-heif (libheif, libde265, x265) | BSD-3-Clause; libheif/libde265 LGPL-3.0; x265 GPL-2.0 |
| rawpy (LibRaw) | MIT; LibRaw LGPL-2.1 / CDDL-1.0 |
| ONNX Runtime | MIT |
| PyAV (FFmpeg) — Movie Maker | BSD-3-Clause; bundled FFmpeg libraries LGPL/GPL (see PyAV license) |
| Qt Multimedia / Qt Print Support (PySide6-Addons) | LGPL-3.0 |
| Hugging Face tokenizers | Apache-2.0 |
| piexif | MIT |
| PyObjC | MIT |
| CLIP ViT-B/32 (optional download, not bundled) | MIT (OpenAI) |
| Map tiles (loaded at runtime, not bundled) | © OpenStreetMap contributors, ODbL |
| Place names (bundled, `fotohaus/data/`) | GeoNames (geonames.org), CC BY 4.0 |
| PyObjC LocalAuthentication (Touch ID) | MIT |

No code, icons, images or text from Picasa are included. Picasa compatibility is
implemented from public, community documentation of the `.picasa.ini` format.
