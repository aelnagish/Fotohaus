# Code signing for Windows

Unsigned, the Windows installer shows **"Windows protected your PC"** the first time; the user has to
click *More info → Run anyway*. Signing removes that warning. Fotohaus applies to the
**SignPath Foundation**, which signs open-source projects for free.

## What SignPath asks for, and where Fotohaus stands

| Requirement | Status |
|---|---|
| OSI license for everything, no proprietary parts | ✅ GPL-3.0-or-later; all bundled components are open source |
| Already released in the form to be signed | ⏳ publish one unsigned Windows release first |
| Only binaries built from our own source are signed | ✅ `Fotohaus.exe` / `fotohaus-cli.exe` are compiled from `packaging/windows/launcher.c`; Python, Qt etc. stay as their projects ship them |
| Built verifiably from source in CI | ✅ `.github/workflows/build.yml` builds everything on GitHub |
| Product name and version in every binary | ✅ set by the launcher's version resource (`ProductName` = Fotohaus) |
| Privacy policy, shown during installation, with a way to turn online features off | ✅ `PRIVACY.md`, shown by the installer; map tiles can be switched off, the model download is opt-in |
| Uninstaller | ✅ Inno Setup adds one (Settings → Apps) |
| Multi-factor authentication on GitHub and SignPath | ⏳ check GitHub → Settings → Password and authentication |
| A "Code signing policy" page linked from the home and download pages, with team roles | ⏳ add after approval (text below) |
| Each release approved by hand | happens in SignPath's web page for every release |

## Steps

1. **Turn on two-factor authentication** on GitHub, if it isn't on already.
2. **Publish a release that includes the unsigned Windows installer** (tag a version; the workflow
   attaches `Fotohaus-<version>-Setup.exe` to the release).
3. **Apply** at <https://signpath.org/apply> with the repository link, the release link and a
   short description ("Fotohaus is a free, private photo organizer, a successor to Picasa…").
4. **After approval**, add the code signing policy page to the website (below) and link it from the
   home page and the download section, using the words "Code signing policy".
5. **Connect the workflow**: SignPath gives an organization ID, a project slug, a signing policy
   and an API token. Store the token as a repository secret (`SIGNPATH_API_TOKEN`), then add the
   signing steps described in SignPath's GitHub Actions documentation to the `windows` job: first
   sign `Fotohaus.exe` and `fotohaus-cli.exe` in the app folder, then build the installer and sign
   `Fotohaus-<version>-Setup.exe`.

## Code signing policy (text for the website)

> **Code signing policy**
>
> Free code signing provided by [SignPath.io](https://about.signpath.io), certificate by
> [SignPath Foundation](https://signpath.org).
>
> Fotohaus's Windows programs are built from this repository's source by GitHub Actions and signed
> only after the maintainer approves each release.
>
> - Committers and reviewers: [Abdullah Yusuf](https://github.com/aelnagish)
> - Approvers: [Abdullah Yusuf](https://github.com/aelnagish)
>
> Privacy: Fotohaus doesn't send your photos or any information about you anywhere. It only goes
> online for map tiles, place search and an optional model download; see the
> [privacy policy](https://github.com/aelnagish/Fotohaus/blob/main/PRIVACY.md).
