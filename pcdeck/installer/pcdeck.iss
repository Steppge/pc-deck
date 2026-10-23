; PC-Deck – Installer (Inno Setup 6)
; Bauen: build.bat (baut vorher die PC-Deck.exe)
;
; Installation nur für den aktuellen Benutzer, ohne Administratorrechte, nach
; %LOCALAPPDATA%\Programs\PC-Deck. Die Einstellungen liegen getrennt davon in
; %USERPROFILE%\PCDeck und bleiben bei Updates erhalten; der Deinstaller fragt, ob sie
; mit gelöscht werden sollen (Standard: nein).

#define AppName "PC-Deck"
#define AppVersion "1.4.0"
#define AppPublisher "PC-Deck"
#define AppExe "PC-Deck.exe"

[Setup]
AppId={{EBCEBA8C-D40A-4400-ABFC-45B30471D3F4}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=Output
OutputBaseFilename=PC-Deck-Setup
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=force
RestartApplications=no

[Languages]
Name: "german"; MessagesFile: "compiler:Languages\German.isl"

[Tasks]
Name: "desktopicon"; Description: "Verknüpfung auf dem Desktop anlegen"; GroupDescription: "Zusätzlich:"
Name: "autostart"; Description: "Mit Windows starten"; GroupDescription: "Zusätzlich:"; Flags: unchecked

[Files]
Source: "..\pc-app\dist\{#AppExe}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{userprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"
Name: "{userdesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"; Tasks: desktopicon
; gleicher Name wie der Autostart-Schalter im Tray-Menü der App
Name: "{userstartup}\{#AppName}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"; Tasks: autostart

[Run]
Filename: "{app}\{#AppExe}"; Description: "PC-Deck jetzt starten"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; laufende App beenden, sonst lässt sich die .exe nicht löschen
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM {#AppExe}"; Flags: runhidden; RunOnceId: "KillPCDeck"

[UninstallDelete]
; Autostart-Verknüpfung, auch wenn sie über das Tray-Menü der App angelegt wurde
Type: files; Name: "{userstartup}\{#AppName}.lnk"

[Code]
procedure KillApp();
var
  Code: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM {#AppExe}', '', SW_HIDE, ewWaitUntilTerminated, Code);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  KillApp();  // Update: laufende Version beenden, damit die .exe ersetzt werden kann
  Result := '';
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{%USERPROFILE}\PCDeck');
    if DirExists(DataDir) and not UninstallSilent() then
      if MsgBox('Auch die Einstellungen löschen?' #13#10#13#10 +
                'Kacheln, Seiten, Design und Protokoll in' #13#10 + DataDir + #13#10#13#10 +
                'Bei "Nein" bleiben sie erhalten und werden bei einer neuen Installation wieder verwendet.',
                mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(DataDir, True, True, True);
  end;
end;
