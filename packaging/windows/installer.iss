; Inno Setup 6 script for Macra Downloader.
; Wraps the PyInstaller one-folder build (dist\MacraDownloader\) into
; MacraDownloader-Setup-x64.exe.
;
; Build (from the repository root, after PyInstaller):
;   iscc /DAppVersion=1.2.1 packaging\windows\installer.iss
;
; NOTE: never change AppId. Windows uses it to recognise upgrades/uninstalls.

#ifndef AppVersion
  #define AppVersion "1.2.1"
#endif
#define AppName      "Macra Downloader"
#define AppPublisher "Macra Systems"
#define AppExe       "MacraDownloader.exe"
#define Root         "..\.."

[Setup]
AppId={{475E3023-A256-436B-9FA4-785262462172}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
VersionInfoVersion={#AppVersion}
VersionInfoCompany={#AppPublisher}
VersionInfoCopyright=Copyright (c) 2026 {#AppPublisher}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; Let the user choose "just for me" (no admin rights) or "all users".
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
LicenseFile={#Root}\LICENSE
InfoBeforeFile={#Root}\DISCLAIMER.txt
SetupIconFile={#Root}\assets\icon.ico
UninstallDisplayIcon={app}\{#AppExe}
OutputDir={#Root}\Output
OutputBaseFilename=MacraDownloader-Setup-x64
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ChangesAssociations=yes
CloseApplications=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "magnet";  Description: "Open &magnet: links with {#AppName}"; GroupDescription: "File and link handling:"; Flags: unchecked
Name: "torrent"; Description: "Open .&torrent files with {#AppName}"; GroupDescription: "File and link handling:"; Flags: unchecked

[Files]
Source: "{#Root}\dist\MacraDownloader\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#Root}\LICENSE"; DestDir: "{app}"; DestName: "LICENSE.txt"; Flags: ignoreversion
Source: "{#Root}\THIRD_PARTY_NOTICES.md"; DestDir: "{app}"; DestName: "THIRD_PARTY_NOTICES.txt"; Flags: ignoreversion
Source: "{#Root}\DISCLAIMER.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#Root}\PRIVACY.md"; DestDir: "{app}"; DestName: "PRIVACY.txt"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
; magnet: protocol handler (HKA = HKCU for per-user installs, HKLM for all users)
Root: HKA; Subkey: "Software\Classes\magnet"; ValueType: string; ValueData: "URL:Magnet link"; Flags: uninsdeletekey; Tasks: magnet
Root: HKA; Subkey: "Software\Classes\magnet"; ValueType: string; ValueName: "URL Protocol"; ValueData: ""; Tasks: magnet
Root: HKA; Subkey: "Software\Classes\magnet\DefaultIcon"; ValueType: string; ValueData: """{app}\{#AppExe}"",0"; Tasks: magnet
Root: HKA; Subkey: "Software\Classes\magnet\shell\open\command"; ValueType: string; ValueData: """{app}\{#AppExe}"" ""%1"""; Tasks: magnet
; .torrent file association
Root: HKA; Subkey: "Software\Classes\.torrent"; ValueType: string; ValueData: "MacraDownloader.torrent"; Flags: uninsdeletevalue; Tasks: torrent
Root: HKA; Subkey: "Software\Classes\MacraDownloader.torrent"; ValueType: string; ValueData: "BitTorrent file"; Flags: uninsdeletekey; Tasks: torrent
Root: HKA; Subkey: "Software\Classes\MacraDownloader.torrent\DefaultIcon"; ValueType: string; ValueData: """{app}\{#AppExe}"",0"; Tasks: torrent
Root: HKA; Subkey: "Software\Classes\MacraDownloader.torrent\shell\open\command"; ValueType: string; ValueData: """{app}\{#AppExe}"" ""%1"""; Tasks: torrent

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

; The download history in %LOCALAPPDATA%\MacraDownloader is deliberately kept
; on uninstall, so reinstalling/upgrading doesn't lose it.
