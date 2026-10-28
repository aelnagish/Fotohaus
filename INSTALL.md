# Installing Fotohaus

Fotohaus runs on Macs with Apple Silicon (M1 or newer) and macOS 14 Sonoma or newer.

## Install

### With Homebrew

```bash
brew install --cask aelnagish/tap/fotohaus
```

This installs Fotohaus into Applications, adds the `fotohaus-cli` command, and takes care of the
first-launch warning below, so you can skip *First launch*. Update with `brew upgrade --cask fotohaus`;
remove with `brew uninstall --cask fotohaus` (add `--zap` to also remove the library database).

### From the disk image

1. Download `Fotohaus-1.0.1-arm64.dmg` from [Releases](../../releases).
2. Open the disk image and drag **Fotohaus** into the **Applications** folder.

## First launch

This build is not notarized by Apple yet, so macOS blocks it the first time.

> [!IMPORTANT]
> **If macOS says Fotohaus "is damaged and can't be opened": click Cancel, not Move to Trash.**
> Nothing is wrong with the app. macOS says this about apps downloaded from the internet that
> aren't notarized. The fix takes ten seconds:

1. Click **Cancel** (not *Move to Trash*).
2. Open **Terminal** (press ⌘Space, type *Terminal*, press Return) and run:

   ```bash
   xattr -cr /Applications/Fotohaus.app
   ```

3. Open Fotohaus normally. You only need to do this once per download.

**If macOS instead says it "could not verify" Fotohaus:** click **Done**, open
*System Settings ▸ Privacy & Security*, scroll down, click **Open Anyway** next to the message about
Fotohaus and confirm. (The Terminal command above works too.)

## Open photos from Finder

To open photos in Fotohaus's Photo Viewer when you double-click them, choose
*Tools ▸ Options ▸ Photo Viewer*, tick the file types and click **Make Fotohaus the Default**.
Then double-click a photo and flip through the whole folder with ← and →.

## Command line and Claude

```bash
/Applications/Fotohaus.app/Contents/MacOS/fotohaus-cli --help
```

To let Claude Desktop (or another MCP client) search and organize your library, add this to
`claude_desktop_config.json`:

```json
"fotohaus": { "command": "/Applications/Fotohaus.app/Contents/MacOS/fotohaus-cli", "args": ["mcp"] }
```

## Where things are

- Library database: `~/Library/Application Support/Fotohaus`
- Logs (if something goes wrong at startup): `~/Library/Logs/Fotohaus.log`
- Your photos stay where they are; Fotohaus never moves or uploads them.

## Uninstall

Drag Fotohaus from Applications to the Trash. To remove its library too, delete
`~/Library/Application Support/Fotohaus` and `~/Library/Caches/Fotohaus`. Your photos are not
affected. (Picasa-style `.picasa.ini` files Fotohaus wrote next to your photos are harmless and can
stay.)

## Windows

Fotohaus runs on 64-bit Windows 10 and 11.

1. Download `Fotohaus-<version>-Setup.exe` from [Releases](../../releases) and open it.
2. Until the installer is code-signed, Windows shows **"Windows protected your PC"** the first
   time. Click **More info**, then **Run anyway**.
3. The installer needs no administrator rights: it installs for you alone, or for everyone on the
   PC if you choose that. It adds Fotohaus to the Start menu and to *Open with* for photos and videos.

To make Fotohaus open photos when you double-click them, use *Tools ▸ Options ▸ Photo Viewer ▸
Open Default Apps Settings*, or right-click a photo, choose *Open with ▸ Choose another app*,
pick Fotohaus and tick *Always*.

- Library database: `%LOCALAPPDATA%\Fotohaus` (kept when you uninstall)
- Startup problems are logged to `%LOCALAPPDATA%\Fotohaus\Fotohaus.log`
- Command line and MCP: `fotohaus-cli.exe` in the install folder, for example
  `"%LOCALAPPDATA%\Programs\Fotohaus\fotohaus-cli.exe" mcp`
- Uninstall: *Settings ▸ Apps ▸ Installed apps ▸ Fotohaus ▸ Uninstall*

## Linux

Fotohaus runs on 64-bit Linux desktops from roughly 2019 on (glibc 2.28 or newer: Ubuntu 20.04,
Debian 10, Fedora 29 and later).

1. Download `Fotohaus-<version>-x86_64.AppImage` from [Releases](../../releases).
2. Make it executable: right-click ▸ *Properties* ▸ *Allow executing as program*, or
   `chmod +x Fotohaus-*.AppImage`.
3. Double-click it. To add it to your applications menu and make it the default photo viewer, use
   *Tools ▸ Options ▸ Photo Viewer ▸ Make Fotohaus the Default*.

- Library database: `~/.local/share/fotohaus`; thumbnails in `~/.cache/fotohaus`
- Command line and MCP: run the AppImage with a command, for example
  `./Fotohaus-<version>-x86_64.AppImage search "beach 2019"` or `… mcp`
- Uninstall: delete the AppImage, and `~/.local/share/applications/fotohaus.desktop` if you added
  it to the menu
- If it doesn't start, run it from a terminal to see why. Without FUSE (some containers and
  minimal systems), start it with `--appimage-extract-and-run`; a missing `libxkbcommon-x11` or
  `libxcb-*` library is fixed by installing that package.

## License

Fotohaus is free software under the GNU GPL, version 3 or later, and comes with no warranty.
Third-party licenses are in `Fotohaus.app/Contents/Resources/Licenses`.
