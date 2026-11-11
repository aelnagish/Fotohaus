# Changelog

## 1.0.2 — 2026-11-11

- Fotohaus now also runs on Windows (installer) and Linux (AppImage), built and tested on GitHub
  for every release
- Photo Viewer: Delete now really deletes. Like Picasa, the photo (and its Live Photo video) goes
  straight to the Trash (the Recycle Bin on Windows), the next picture slides in, and a small bar offers **Undo** (or ⌘Z / Ctrl+Z)
  for a few seconds. Before, library photos stayed in the folder and came back the next time the
  viewer opened, and the filmstrip kept showing them
- Photo Viewer: no more plain system warning when deleting a photo outside the library
- Photo Viewer: no error messages in the log when it closes while thumbnails are still loading
- Photo Viewer: a photo opened while the viewer is already up now shows in that viewer, and ← and →
  move through its folder. *Tools ▸ Options ▸ When a viewer is already open* can bring back a new
  window for each photo
- Groundwork for Windows and Linux:
  - Only one Fotohaus runs per library: opening more photos from File Explorer or the file
    manager hands them to the copy that is already open
  - The Private collection is locked with Windows Hello on Windows, and with a Fotohaus password
    wherever Touch ID or Windows Hello isn't available (before, it opened without asking off macOS)
  - On Windows the library is kept in `%LOCALAPPDATA%\Fotohaus`

## 1.0.1 — 2026-10-28

- Photo Viewer: the mouse wheel now zooms in and out around the pointer with every mouse,
  including smooth-scrolling mice and the Magic Mouse; zooming out past the whole photo settles
  back on "fit". Two-finger trackpad scrolling still pans a zoomed photo and pinch still zooms
- The same mouse-or-trackpad detection is used in the editor and the Places map

## 1.0.0 — 2026-10-20

First public release.

- Library of the folders already on your Mac, grouped by folder and date, with stars, captions,
  nested tags and albums; Timeline, Places map with offline place names, and Memories ("on this day")
- Photo Viewer: double-click a photo in Finder and flip through the folder with ← and →
- On-device faces, objects, scenes and text recognition; optional natural-language search
- Stacks for RAW+JPEG pairs, bursts and Live Photos
- Archive, a Touch ID-locked Private collection and Recently Deleted (30 days)
- Non-destructive editor with Picasa's fixes and effects plus retouch, text, smart enhance,
  denoise and clarity; Save to File keeps the original in `.picasaoriginals`
- Collages, slideshows, movies with music, posters, passport photos, web pages, printing, email
  and the macOS Share menu
- Import from cameras and cards, Google Takeout import, backups with restore, duplicate finder
- Reads and writes Picasa's `.picasa.ini` files
- Command line (`fotohaus-cli`) and an MCP server for Claude and other AI assistants
