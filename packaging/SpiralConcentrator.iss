; Inno Setup 6 script. Build the executable first, then compile this file
; with ISCC.exe packaging\SpiralConcentrator.iss
; The installer is written to dist\SpiralConcentrator-Setup.exe

#define MyAppName "Spiral Concentrator"
#define MyAppVersion "1.0.0"
#define MyAppExeName "SpiralConcentrator.exe"

[Setup]
AppId={{8F4C2A91-6B17-4E3D-A5C8-1D90E7B24F63}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppName}
DefaultDirName={localappdata}\Spiral Concentrator
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename=SpiralConcentrator-Setup
SetupIconFile=spiral.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
CloseApplications=force

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Add a desktop icon"; GroupDescription: "Icons:"; Flags: checkedonce

[Files]
Source: "..\dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Comment: "Open the spiral concentrator simulator"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Open Spiral Concentrator"; Flags: nowait postinstall skipifsilent
