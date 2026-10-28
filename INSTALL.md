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

**If macOS says Fotohaus "is damaged and can't be opened":** nothing is wrong with the app.
macOS says this about apps downloaded from the internet that aren't notarized.

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

## License

Fotohaus is free software under the GNU GPL, version 3 or later, and comes with no warranty.
Third-party licenses are in `Fotohaus.app/Contents/Resources/Licenses`.
