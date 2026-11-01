#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif

#define MyAppName "도깨비헬퍼"
#define MyAppExeName "GameAutoPlay.exe"

[Setup]
AppId={{A2D51B7A-10D1-46E3-900D-BD1A5A6C6441}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} v{#MyAppVersion}
AppPublisher=MrDH1004
AppPublisherURL=https://github.com/MrDH1004/GameAutoPlay
AppSupportURL=https://github.com/MrDH1004/GameAutoPlay/issues
AppUpdatesURL=https://github.com/MrDH1004/GameAutoPlay/releases
DefaultDirName={localappdata}\Programs\DokkaebiHelper
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=dist\{#MyAppVersion}
OutputBaseFilename=GameAutoPlay-Setup-{#MyAppVersion}
SetupIconFile=assets\app.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
MinVersion=10.0.17763

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"

[Tasks]
Name: "desktopicon"; Description: "바탕 화면에 바로가기 만들기"; GroupDescription: "추가 바로가기:"; Flags: unchecked

[Files]
Source: "dist\{#MyAppVersion}\GameAutoPlay\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{#MyAppName} v{#MyAppVersion} 실행"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}"
