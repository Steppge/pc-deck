; PC-Deck – Installer (Inno Setup 6)
; Bauen: build.bat (baut vorher die PC-Deck.exe)
;
; Installation nur für den aktuellen Benutzer, ohne Administratorrechte, nach
; %LOCALAPPDATA%\Programs\PC-Deck. Die Einstellungen liegen getrennt davon in
; %USERPROFILE%\PCDeck und bleiben bei Updates erhalten; der Deinstaller fragt, ob sie
; mit gelöscht werden sollen (Standard: nein).

#define AppName "PC-Deck"
#define AppVersion "1.5.2"
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
; Firmware für das Update aus der App (Fenster „Firmware ...“)
Source: "..\firmware\.pio\build\deck_s3_ts35\firmware.bin"; DestDir: "{app}\firmware"; DestName: "pcdeck_s3_ts35.bin"; Flags: ignoreversion
Source: "..\firmware\.pio\build\deck_esp32_tft35\firmware.bin"; DestDir: "{app}\firmware"; DestName: "pcdeck_esp32_tft35.bin"; Flags: ignoreversion

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
const
  UninstKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{EBCEBA8C-D40A-4400-ABFC-45B30471D3F4}_is1';

var
  OldVersion, OldUninstaller: String;
  HadAutostart, AutostartSet: Boolean;
  OldPage: TInputOptionWizardPage;

procedure KillApp();
var
  Code: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM {#AppExe}', '', SW_HIDE, ewWaitUntilTerminated, Code);
end;

function DataDir(): String;
begin
  Result := ExpandConstant('{%USERPROFILE}\PCDeck');
end;

// Vorhandene Installation (gleiche AppId) erkennen
function FindOld(): Boolean;
begin
  OldVersion := '';
  OldUninstaller := '';
  if RegQueryStringValue(HKCU, UninstKey, 'UninstallString', OldUninstaller) then
  begin
    OldUninstaller := RemoveQuotes(OldUninstaller);
    RegQueryStringValue(HKCU, UninstKey, 'DisplayVersion', OldVersion);
  end;
  Result := (OldUninstaller <> '') and FileExists(OldUninstaller);
end;

procedure InitializeWizard();
begin
  FindOld();
  HadAutostart := FileExists(ExpandConstant('{userstartup}\{#AppName}.lnk'));
  OldPage := CreateInputOptionPage(wpWelcome,
    'Vorhandene Installation',
    'PC-Deck ' + OldVersion + ' ist installiert und wird vorher deinstalliert.',
    'Die laufende App wird beendet und die alte Version entfernt. Was soll mit den Einstellungen ' +
    'in ' + DataDir() + ' passieren?',
    True, False);
  OldPage.Add('Einstellungen behalten (Kacheln, Seiten, Designs, Drehknopf, WLAN-Kopplung)');
  OldPage.Add('Alles löschen, inklusive Konfiguration und Protokoll – die App kennt die Decks danach ' +
              'nicht mehr: beim nächsten Anstecken werden leere Kacheln übertragen (das Design bleibt ' +
              'im Deck), WLAN muss neu gekoppelt werden');
  OldPage.SelectedValueIndex := 0;
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := (PageID = OldPage.ID) and (OldUninstaller = '');
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if (CurPageID = OldPage.ID) and (OldPage.SelectedValueIndex = 1) then
    Result := MsgBox('Wirklich alle Einstellungen löschen?' #13#10#13#10 + DataDir(),
                     mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES;
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  // Autostart war an: Häkchen wieder setzen (die Deinstallation entfernt die Verknüpfung)
  if (CurPageID = wpSelectTasks) and HadAutostart and not AutostartSet then
  begin
    WizardSelectTasks('autostart');
    AutostartSet := True;
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Code, Waited: Integer;
begin
  Result := '';
  KillApp();  // laufende Version beenden, damit die .exe ersetzt werden kann
  Sleep(500);
  if (OldUninstaller <> '') and FileExists(OldUninstaller) then
  begin
    Log('Alte Version ' + OldVersion + ' deinstallieren: ' + OldUninstaller);
    Exec(OldUninstaller, '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART', '', SW_HIDE, ewWaitUntilTerminated, Code);
    // der Deinstaller läuft als Kopie weiter – warten, bis er fertig ist (höchstens 30 s)
    Waited := 0;
    while (Waited < 30000) and (RegKeyExists(HKCU, UninstKey) or FileExists(OldUninstaller)) do
    begin
      Sleep(250);
      Waited := Waited + 250;
    end;
    if RegKeyExists(HKCU, UninstKey) then
      Log('Alte Version nicht vollständig entfernt – Installation überschreibt sie');
  end;
  if (OldUninstaller <> '') and (OldPage.SelectedValueIndex = 1) and DirExists(DataDir()) then
  begin
    Log('Einstellungen löschen: ' + DataDir());
    DelTree(DataDir(), True, True, True);
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Dir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    Dir := DataDir();
    if DirExists(Dir) and not UninstallSilent() then
      if MsgBox('Auch die Einstellungen löschen?' #13#10#13#10 +
                'Kacheln, Seiten, Design und Protokoll in' #13#10 + Dir + #13#10#13#10 +
                'Bei "Nein" bleiben sie erhalten und werden bei einer neuen Installation wieder verwendet.',
                mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(Dir, True, True, True);
  end;
end;
