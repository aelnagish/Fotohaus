; Inno Setup script for the Fotohaus installer. Built by packaging/build_windows.py --iscc ISCC.exe,
; which passes AppVersion, SourceDir (the app folder), OutputDir and AssocFile (file types, generated
; from fotohaus/media.py so they always match what Fotohaus opens).
;
; Installs for the current user without an admin prompt by default (to %LOCALAPPDATA%\Programs),
; or for all users when chosen. Fotohaus is offered under "Open with" for photos and videos and
; listed in Settings > Apps > Default apps; Windows lets only the user make it the default.
; The library (%LOCALAPPDATA%\Fotohaus) is kept on uninstall.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{7B0C3F5E-2A61-4C8E-9D35-6F1E8A2B4C70}
AppName=Fotohaus
AppVersion={#AppVersion}
AppVerName=Fotohaus {#AppVersion}
AppPublisher=Abdullah Yusuf
AppPublisherURL=https://aelnagish.github.io/Fotohaus/
AppSupportURL=https://github.com/aelnagish/Fotohaus/issues
AppUpdatesURL=https://github.com/aelnagish/Fotohaus/releases
AppCopyright=© 2026 Abdullah Yusuf. Free software under the GNU GPL v3 or later.
VersionInfoVersion={#AppVersion}
DefaultDirName={autopf}\Fotohaus
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#OutputDir}
OutputBaseFilename=Fotohaus-{#AppVersion}-Setup
SetupIconFile={#SourceDir}\Fotohaus.ico
UninstallDisplayIcon={app}\Fotohaus.exe
UninstallDisplayName=Fotohaus
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
#ifdef PrivacyFile
; shown before installing: what Fotohaus sends online (map tiles, place search, optional model)
InfoBeforeFile={#PrivacyFile}
#endif
ChangesAssociations=yes
CloseApplications=yes
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "foldermenu"; Description: "Add ""Open in Fotohaus"" to the right-click menu of folders"; GroupDescription: "File Explorer:"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; an update replaces the whole Python library, so files removed in a newer version don't linger
Type: filesandordirs; Name: "{app}\Lib"
Type: filesandordirs; Name: "{app}\DLLs"

[Icons]
Name: "{autoprograms}\Fotohaus"; Filename: "{app}\Fotohaus.exe"; IconFilename: "{app}\Fotohaus.ico"; AppUserModelID: "Fotohaus.Fotohaus"
Name: "{autodesktop}\Fotohaus"; Filename: "{app}\Fotohaus.exe"; IconFilename: "{app}\Fotohaus.ico"; AppUserModelID: "Fotohaus.Fotohaus"; Tasks: desktopicon

[Registry]
; ProgIDs: how Fotohaus opens a photo or a video
Root: HKA; Subkey: "Software\Classes\Fotohaus.Image"; ValueType: string; ValueData: "Fotohaus Photo"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\Fotohaus.Image\DefaultIcon"; ValueType: string; ValueData: "{app}\Fotohaus.ico"
Root: HKA; Subkey: "Software\Classes\Fotohaus.Image\shell\open\command"; ValueType: string; ValueData: """{app}\Fotohaus.exe"" ""%1"""
Root: HKA; Subkey: "Software\Classes\Fotohaus.Video"; ValueType: string; ValueData: "Fotohaus Video"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\Fotohaus.Video\DefaultIcon"; ValueType: string; ValueData: "{app}\Fotohaus.ico"
Root: HKA; Subkey: "Software\Classes\Fotohaus.Video\shell\open\command"; ValueType: string; ValueData: """{app}\Fotohaus.exe"" ""%1"""
; the application itself (name shown in "Open with")
Root: HKA; Subkey: "Software\Classes\Applications\Fotohaus.exe"; ValueType: string; ValueName: "FriendlyAppName"; ValueData: "Fotohaus"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\Applications\Fotohaus.exe\shell\open\command"; ValueType: string; ValueData: """{app}\Fotohaus.exe"" ""%1"""
; Settings > Apps > Default apps
Root: HKA; Subkey: "Software\Fotohaus\Capabilities"; ValueType: string; ValueName: "ApplicationName"; ValueData: "Fotohaus"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Fotohaus\Capabilities"; ValueType: string; ValueName: "ApplicationDescription"; ValueData: "Organize, find and edit your photos, privately"
Root: HKA; Subkey: "Software\Fotohaus\Capabilities"; ValueType: string; ValueName: "ApplicationIcon"; ValueData: "{app}\Fotohaus.ico"
Root: HKA; Subkey: "Software\RegisteredApplications"; ValueType: string; ValueName: "Fotohaus"; ValueData: "Software\Fotohaus\Capabilities"; Flags: uninsdeletevalue
; "Open in Fotohaus" for folders (adds the folder to the library)
Root: HKA; Subkey: "Software\Classes\Directory\shell\Fotohaus"; ValueType: string; ValueData: "Open in Fotohaus"; Flags: uninsdeletekey; Tasks: foldermenu
Root: HKA; Subkey: "Software\Classes\Directory\shell\Fotohaus"; ValueType: string; ValueName: "Icon"; ValueData: "{app}\Fotohaus.ico"; Tasks: foldermenu
Root: HKA; Subkey: "Software\Classes\Directory\shell\Fotohaus\command"; ValueType: string; ValueData: """{app}\Fotohaus.exe"" ""%1"""; Tasks: foldermenu
; every file type Fotohaus opens: Open with list, supported types, Default apps
#include AssocFile

[Run]
Filename: "{app}\Fotohaus.exe"; Description: "{cm:LaunchProgram,Fotohaus}"; Flags: nowait postinstall skipifsilent
