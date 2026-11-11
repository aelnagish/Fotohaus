# Fotohaus in the app stores

Fotohaus is built for two stores besides the downloads on GitHub. Both are free for the developer.

| Store | Package | Built by | Where it appears |
|---|---|---|---|
| Microsoft Store (Windows 10/11) | `Fotohaus-<version>-x64.msix` | `packaging/build_windows.py --msix` | workflow artifact **Store-Windows-MSIX** |
| Flathub (every Linux distribution) | Flatpak `io.github.aelnagish.Fotohaus` | `packaging/flatpak/` | workflow artifact **Store-Linux-Flatpak** (to try it); Flathub builds its own |

The GitHub workflow builds and starts both on every push, so a broken store package shows up as a red
check. Neither is attached to GitHub releases: they reach users through the stores.

## Microsoft Store

The Store signs the package itself, so no certificate is needed and Windows shows no
"Windows protected your PC" warning. Updates arrive through the Store.

### Once

1. **Developer account.** Sign up at <https://storedeveloper.microsoft.com> with a personal Microsoft
   account, as an *individual*. It is free; Microsoft checks an ID and a selfie.
2. **Reserve the name.** In Partner Center: *Apps and games ▸ New product ▸ MSIX or PWA app*, name
   **Fotohaus**. (Not "EXE or MSI app": that one needs a paid certificate.)
3. **Copy the identity.** *Product management ▸ Product identity* shows three values. Save them in
   the GitHub repository under *Settings ▸ Secrets and variables ▸ Actions ▸ Variables*:

   | Partner Center | Repository variable | Looks like |
   |---|---|---|
   | Package/Identity/Name | `MSIX_IDENTITY_NAME` | `12345AbdullahYusuf.Fotohaus` |
   | Package/Identity/Publisher | `MSIX_PUBLISHER` | `CN=1A2B3C4D-…` |
   | Package/Properties/PublisherDisplayName | `MSIX_PUBLISHER_DISPLAY_NAME` | `Abdullah Yusuf` |

   They are not secret. Until they are set, the workflow builds the package with a test identity,
   which the Store refuses.
4. **Run the workflow again** (*Actions ▸ Build ▸ Run workflow*) and download **Store-Windows-MSIX**.

### The submission

In Partner Center, *Start submission* and fill in:

- **Pricing and availability:** free, all markets.
- **Properties:** category *Photo & video*. Privacy policy:
  `https://github.com/aelnagish/Fotohaus/blob/main/PRIVACY.md`. Support contact: the Issues page.
- **Age ratings:** answer the questionnaire (no violence, no user-to-user communication, no
  purchases, no data shared).
- **Packages:** upload `Fotohaus-<version>-x64.msix`.
- **Store listing** (English): the text below, at least one screenshot taken on Windows
  (1366 × 768 or larger), and the icon.
- **Submission options ▸ restricted capabilities.** The form asks why the package uses
  `runFullTrust`. Answer:

  > Fotohaus is a classic desktop (Win32) application written in Python and Qt. It needs full trust
  > to read and organize the photo folders the user chooses anywhere on the PC and to write edits,
  > captions and tags next to those photos.

Certification takes up to three business days.

**Short description**

> A fast, private photo viewer and organizer. Finds faces, searches your photos and edits without
> touching your originals. Nothing is uploaded.

**Description**

> Fotohaus is one home for every picture you have ever taken. Point it at the folders already on
> your PC, and years of photos are sorted, searchable and easy to show off. Nothing is copied into
> a hidden library and nothing leaves your computer.
>
> • Open a photo from File Explorer and flip through its folder with the arrow keys
> • Library by folder and date, with stars, captions, nested tags and albums
> • Faces: name someone once and Fotohaus suggests the same person across the years
> • Search by person, place, tag and date, or by description with the optional search model
> • Places map with real place names, worked out offline
> • Memories: photos taken on this day in earlier years
> • Edits that never touch your originals: enhance, crop, straighten, light, colour and 40+ effects
> • Stacks for RAW and JPEG pairs, bursts and Live Photos
> • A Private collection locked with Windows Hello, an Archive, and 30 days to undo a delete
> • Collages, slideshows, movies with music, web galleries and backups
>
> No account, no cloud, no tracking. Fotohaus is free software (GPL-3.0-or-later).

### Every new version

Tag the release as usual, download **Store-Windows-MSIX** from that workflow run, and in Partner
Center choose *Update* and replace the package.

### What is different from the installer

- The command line tool is on the PATH as `fotohaus-cli` (an app execution alias).
- The library is kept in the package's own copy of `%LOCALAPPDATA%\Fotohaus`, and **Windows removes
  it when the Store app is uninstalled**. Photos, and the captions, stars and edits saved next to
  them, are not affected. *Tools ▸ Options ▸ Move Database* puts the library somewhere that stays.
- There is no "Open in Fotohaus" in the right-click menu of folders (a Store package would need a
  separate shell extension for it). Photos and videos still offer Fotohaus under *Open with*.

### Build it yourself

In a *Developer Command Prompt* (the Windows SDK provides `makeappx` and `makepri`):

```
python packaging\build_windows.py --out build --msix --msix-name <Name> --msix-publisher "<CN=…>" --msix-publisher-name "<Display name>"
```

## Flathub

The Flatpak holds exactly what the AppImage holds: the wheels pinned in `packaging/constraints.txt`,
trimmed to the parts of Qt that Fotohaus uses. It runs on the Freedesktop runtime 25.08, the newest
whose Python (3.13) PySide6 6.9 supports.

| File in `packaging/flatpak/` | What it is |
|---|---|
| `io.github.aelnagish.Fotohaus.yml` | the manifest: runtime, permissions, sources |
| `install.py` | installs Fotohaus into the Flatpak and writes the menu entry, the store description (from this file and `CHANGELOG.md`) and the icons |
| `python-wheels.json` | every wheel with its address and checksum (Flathub builds without network access) |
| `update_wheels.py` | writes `python-wheels.json` again; run it after changing `constraints.txt` |
| `flathub.json` | tells Flathub to build for x86_64 only, like the AppImage |
| `make_submission.py` | writes the files for Flathub with a release tag as the source |

### Once: the submission

Flathub builds from a tagged release, so the first release that contains this folder comes first.

1. **Release** a version as usual (tag `v1.x.y`).
2. **Write the Flathub files:**

   ```
   git fetch --tags
   python3 packaging/flatpak/make_submission.py --tag v1.x.y
   ```

   They are in `build/flathub/`.
3. **Open the pull request** against Flathub's `new-pr` branch (not `master`):

   ```
   gh repo fork --clone flathub/flathub && cd flathub
   git checkout --track origin/new-pr
   git checkout -b fotohaus-submission
   cp ../Fotohaus/build/flathub/* .
   git add . && git commit -m "Add io.github.aelnagish.Fotohaus"
   git push -u origin fotohaus-submission
   gh pr create --repo flathub/flathub --base new-pr --title "Add io.github.aelnagish.Fotohaus"
   ```

4. **Answer the reviewers.** Things they are likely to ask about:
   - **`--filesystem=home` and the drive folders.** Fotohaus organizes the folders that are already
     on the computer and writes `.picasa.ini` files, edits and tags next to the photos, so it can't
     work through the file chooser alone. Flathub's checker reports this permission until a
     reviewer grants an exception; say the above in the pull request.
   - **Ready-made wheels** instead of building Qt, FFmpeg and OpenCV from source. They are the
     projects' own releases from PyPI, pinned by checksum; `packaging/THIRD_PARTY_NOTICES.md` lists
     them and the GPL source archive is attached to every release.
   - **Screenshots.** The description uses `docs/*.jpg` from the release tag. Screenshots taken on
     Linux are preferred; replace the files in `docs/` or point `install.py` at new ones.
5. After approval Flathub creates `flathub/io.github.aelnagish.Fotohaus` and invites you to it.
   Log in at <https://flathub.org> with GitHub to get the *verified* badge (the `io.github.aelnagish`
   ID is verified through the GitHub account).

### Every new version

```
python3 packaging/flatpak/make_submission.py --tag v1.x.y
```

and commit the three files to `flathub/io.github.aelnagish.Fotohaus` (through a pull request there;
Flathub builds it and publishes after the merge).

### What is different from the AppImage

- Deleted photos go to the desktop's Trash as usual. Inside Flatpak the app's data folder is private,
  so Fotohaus links its Trash folder to `~/.local/share/Trash` the first time something is deleted.
- *Tools ▸ Options ▸ Photo Viewer* can't change the desktop's default apps from inside the sandbox;
  it explains how to pick Fotohaus under *Open With* instead.
- Folders don't offer Fotohaus under *Open With* (it would compete with the file manager).
- The library is in `~/.var/app/io.github.aelnagish.Fotohaus/data/fotohaus`.
- The command line tool: `flatpak run --command=fotohaus-cli io.github.aelnagish.Fotohaus search "beach 2019"`.

### Build it yourself

```
flatpak-builder --user --install --install-deps-from=flathub --force-clean \
    --state-dir=build/flatpak-state build/flatpak packaging/flatpak/io.github.aelnagish.Fotohaus.yml
flatpak run io.github.aelnagish.Fotohaus
```
