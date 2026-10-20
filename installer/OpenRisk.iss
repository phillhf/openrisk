; OpenRisk – grafisches Setup und Deinstallation (Inno Setup 6)
; Gebaut von tools/build_setup.py:  ISCC.exe /DAppVersion=x.y.z /DPkgDir=<Programmdateien> installer\OpenRisk.iss
;
; Das Setup ist die Oberfläche; die eigentliche Arbeit (Python prüfen, Python-Umgebung, Abhängigkeiten,
; Bibliotheken, Offline-Karte, Startdatei) erledigt install.ps1 unsichtbar im Hintergrund.
; Ohne Administratorrechte. Stilles Setup:  OpenRisk-Setup-x.y.z.exe /VERYSILENT [/DIR="D:\OpenRisk"] [/PORT=8642]
;   [/MAPZOOM=7] [/PYTHON=1 (fehlendes Python über winget installieren)] [/NODESKTOP=1] [/AUTOSTART=1]
;   [/NOPIPELINES=1] [/NOBASES=1]

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef PkgDir
  #define PkgDir "..\dist\_setup\OpenRisk-" + AppVersion
#endif

[Setup]
AppId={{6E1C4B2A-93F7-4D58-A0B6-2F5D8C71E4A9}
AppName=OpenRisk
AppVersion={#AppVersion}
AppVerName=OpenRisk {#AppVersion}
AppPublisher=OpenRisk
AppPublisherURL=https://github.com/phillhf/openrisk
AppSupportURL=https://github.com/phillhf/openrisk
AppUpdatesURL=https://github.com/phillhf/openrisk/releases
VersionInfoVersion={#AppVersion}
VersionInfoDescription=OpenRisk Setup
DefaultDirName=C:\Tools\OpenRisk
AppendDefaultDirName=no
DirExistsWarning=no
UsePreviousAppDir=no
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=OpenRisk-Setup-{#AppVersion}
SetupIconFile=app.ico
WizardStyle=modern
WizardSizePercent=110
UninstallDisplayName=OpenRisk
UninstallDisplayIcon={app}\app.ico
Compression=lzma2/max
SolidCompression=yes
CloseApplications=no
RestartApplications=no

[Languages]
Name: "de"; MessagesFile: "compiler:Languages\German.isl"

[Messages]
de.WelcomeLabel1=Willkommen bei OpenRisk
de.FinishedHeadingLabel=OpenRisk ist eingerichtet

[Files]
; Programm nur entpacken – install.ps1 kopiert es selbst in den Zielordner (Daten und Karten bleiben dort unberührt)
Source: "{#PkgDir}\*"; DestDir: "{tmp}\pkg"; Flags: recursesubdirs createallsubdirs ignoreversion
; für die Erkennung vor dem Assistenten
Source: "{#PkgDir}\install.ps1"; Flags: dontcopy
; zuletzt: danach läuft install.ps1 - noch bevor das Setup die Verknüpfungen anlegt (die setzen eine fertige Einrichtung voraus)
Source: "app.ico"; DestDir: "{app}"; Flags: ignoreversion; AfterInstall: RunInstaller

[Icons]
Name: "{userprograms}\OpenRisk\OpenRisk"; Filename: "{app}\OpenRisk.bat"; WorkingDir: "{app}"; IconFilename: "{app}\app.ico"; Flags: runminimized; Check: InstallOk
Name: "{userprograms}\OpenRisk\OpenRisk deinstallieren"; Filename: "{uninstallexe}"
Name: "{userdesktop}\OpenRisk"; Filename: "{app}\OpenRisk.bat"; WorkingDir: "{app}"; IconFilename: "{app}\app.ico"; Flags: runminimized; Check: WantDesktop
Name: "{userstartup}\OpenRisk"; Filename: "{app}\OpenRisk.bat"; WorkingDir: "{app}"; IconFilename: "{app}\app.ico"; Flags: runminimized; Check: WantAutostart

[Run]
Filename: "{app}\OpenRisk.bat"; WorkingDir: "{app}"; Description: "OpenRisk jetzt starten und im Browser öffnen"; Flags: postinstall nowait skipifsilent runminimized; Check: InstallOk

[Code]
var
  Found, OldDir, OldVersion, OldPort, HaveMap, Python, PyVersion, Winget, OldDesktop, OldAutostart: String;
  PortPage: TInputQueryWizardPage;
  MapPage: TInputOptionWizardPage;
  OptPage: TInputOptionWizardPage;
  ProgressPage: TOutputMarqueeProgressWizardPage;
  Ok: Boolean;
  LogFile: String;
  OptDesktop, OptAutostart, OptPipelines, OptBases, OptPython: Integer;

const
  MapZooms = '0,5,6,7,8,9,10';

function PowerShellExe(): String;
begin
  Result := ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe');
end;

function Value(const Lines: TArrayOfString; const Key: String): String;
var
  I: Integer;
  L: String;
begin
  Result := '';
  for I := 0 to GetArrayLength(Lines) - 1 do
  begin
    L := Lines[I];
    if (Length(L) > 0) and (L[1] = #$FEFF) then L := Copy(L, 2, Length(L));
    if Pos(Key + '=', L) = 1 then
    begin
      Result := Trim(Copy(L, Length(Key) + 2, Length(L)));
      Exit;
    end;
  end;
end;

// Versionen wie 2026.10.20.1 vergleichen: <0 älter, 0 gleich, >0 neuer
function CompareVersion(A, B: String): Integer;
var
  PA, PB, NA, NB: Integer;
begin
  Result := 0;
  while (Result = 0) and ((A <> '') or (B <> '')) do
  begin
    PA := Pos('.', A); PB := Pos('.', B);
    if PA = 0 then begin NA := StrToIntDef(A, 0); A := ''; end else begin NA := StrToIntDef(Copy(A, 1, PA - 1), 0); A := Copy(A, PA + 1, Length(A)); end;
    if PB = 0 then begin NB := StrToIntDef(B, 0); B := ''; end else begin NB := StrToIntDef(Copy(B, 1, PB - 1), 0); B := Copy(B, PB + 1, Length(B)); end;
    Result := NA - NB;
  end;
end;

// Vor dem Assistenten: vorhandene Installation, Port, Karte und Python erkennen (install.ps1 -Detect)
function InitializeSetup(): Boolean;
var
  Code: Integer;
  F, D: String;
  Lines: TArrayOfString;
begin
  Result := True;
  ExtractTemporaryFile('install.ps1');
  F := ExpandConstant('{tmp}\detect.txt');
  // mit /DIR=... nur diesen Ordner prüfen, sonst die üblichen Orte
  D := ExpandConstant('{param:DIR|}');
  if D <> '' then D := ' -InstallDir "' + D + '"';
  Exec(PowerShellExe(), '-NoProfile -ExecutionPolicy Bypass -File "' + ExpandConstant('{tmp}\install.ps1') +
    '" -Detect -DetectFile "' + F + '"' + D, '', SW_HIDE, ewWaitUntilTerminated, Code);
  if LoadStringsFromFile(F, Lines) then
  begin
    Found := Value(Lines, 'found');
    OldDir := Value(Lines, 'dir');
    OldVersion := Value(Lines, 'version');
    OldPort := Value(Lines, 'port');
    HaveMap := Value(Lines, 'map');
    Python := Value(Lines, 'python');
    PyVersion := Value(Lines, 'pyversion');
    Winget := Value(Lines, 'winget');
    OldDesktop := Value(Lines, 'desktop');
    OldAutostart := Value(Lines, 'autostart');
  end;
  if (Found = '1') and (OldVersion <> '') and (CompareVersion(OldVersion, '{#AppVersion}') > 0) and not WizardSilent() then
    Result := MsgBox('Installiert ist bereits die neuere Version ' + OldVersion + '.' + #13#10 +
      'Trotzdem die ältere Version {#AppVersion} einspielen?', mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES;
end;

function IsUpdate(): Boolean;
begin
  Result := Found = '1';
end;

procedure InitializeWizard;
var
  T: String;
  Zoom: Integer;
begin
  if IsUpdate() then
  begin
    WizardForm.DirEdit.Text := OldDir;
    if OldVersion = '' then OldVersion := '(ältere Version)';
    T := 'OpenRisk ' + OldVersion + ' ist bereits installiert in' + #13#10 + OldDir + #13#10#13#10 +
      'Das Setup aktualisiert auf Version {#AppVersion}. Datenbank, Karten, Höhendaten, Einstellungen und ' +
      'API-Schlüssel bleiben erhalten. Läuft OpenRisk gerade, wird es dafür kurz beendet.';
  end else
    T := 'Dieses Setup installiert OpenRisk {#AppVersion} – den Offline-Risikomonitor: Lage weltweit, Unwetter, ' +
      'Regenradar, Strahlung, Konflikte und kritische Infrastruktur auf einer Karte.' + #13#10#13#10 +
      'OpenRisk läuft komplett auf diesem Rechner und bleibt auch ohne Internet nutzbar. Für die Einrichtung ' +
      'wird einmal Internet gebraucht (Python-Pakete, Bibliotheken, Karte).';
  WizardForm.WelcomeLabel2.Caption := T;

  PortPage := CreateInputQueryPage(wpSelectDir, 'Weboberfläche', 'Unter welchem Port soll OpenRisk erreichbar sein?',
    'OpenRisk öffnet sich danach im Browser unter http://localhost:<Port>. Ohne besonderen Grund den Vorschlag übernehmen.');
  PortPage.Add('Port (1024–65535):', False);
  if OldPort <> '' then PortPage.Values[0] := OldPort else PortPage.Values[0] := '8642';
  if ExpandConstant('{param:PORT|}') <> '' then PortPage.Values[0] := ExpandConstant('{param:PORT|}');

  MapPage := CreateInputOptionPage(PortPage.ID, 'Offline-Karte', 'Wie genau soll die Weltkarte offline vorliegen?',
    'Später jederzeit unter Einstellungen änderbar – dort lassen sich auch einzelne Länder in voller Detailstufe laden.',
    True, False);
  MapPage.Add('Keine Karte jetzt laden (nur Länderumrisse; vorhandene Karte bleibt)');
  MapPage.Add('Stufe 5 – Länder, Großstädte (15 MB)');
  MapPage.Add('Stufe 6 – Regionen, Fernstraßen (45 MB)');
  MapPage.Add('Stufe 7 – mittlere Städte (190 MB, empfohlen)');
  MapPage.Add('Stufe 8 – Kleinstädte, Landstraßen (560 MB)');
  MapPage.Add('Stufe 9 – Ortsteile (1,6 GB)');
  MapPage.Add('Stufe 10 – Stadtstraßen grob (3,8 GB)');
  if HaveMap = '1' then MapPage.SelectedValueIndex := 0 else MapPage.SelectedValueIndex := 3;
  Zoom := StrToIntDef(ExpandConstant('{param:MAPZOOM|-1}'), -1);
  case Zoom of
    0: MapPage.SelectedValueIndex := 0;
    5, 6, 7, 8, 9, 10: MapPage.SelectedValueIndex := Zoom - 4;
  end;

  OptPage := CreateInputOptionPage(MapPage.ID, 'Optionen', 'Was soll das Setup zusätzlich einrichten?', '', False, False);
  OptDesktop := OptPage.Add('Verknüpfung „OpenRisk“ auf dem Desktop');
  OptAutostart := OptPage.Add('OpenRisk beim Anmelden automatisch starten');
  OptPipelines := OptPage.Add('Öl-Pipelines jetzt laden (OpenStreetMap, 10–20 Minuten)');
  OptBases := OptPage.Add('Militärbasen jetzt laden (OpenStreetMap, 10–20 Minuten)');
  // bei einem Update übernehmen, was schon eingerichtet war
  OptPage.Values[OptDesktop] := not IsUpdate() or (OldDesktop = '1');
  OptPage.Values[OptAutostart] := IsUpdate() and (OldAutostart = '1');
  OptPage.Values[OptPipelines] := not IsUpdate();
  OptPage.Values[OptBases] := not IsUpdate();
  if ExpandConstant('{param:NODESKTOP|0}') = '1' then OptPage.Values[OptDesktop] := False;
  if ExpandConstant('{param:AUTOSTART|0}') = '1' then OptPage.Values[OptAutostart] := True;
  if ExpandConstant('{param:NOPIPELINES|0}') = '1' then OptPage.Values[OptPipelines] := False;
  if ExpandConstant('{param:NOBASES|0}') = '1' then OptPage.Values[OptBases] := False;
  OptPython := -1;
  if Python = '' then
  begin
    OptPython := OptPage.Add('Python (wird benötigt, fehlt auf diesem Rechner) automatisch über winget installieren');
    OptPage.Values[OptPython] := (Winget = '1') and (not WizardSilent() or (ExpandConstant('{param:PYTHON|0}') = '1'));
  end;
  ProgressPage := CreateOutputMarqueeProgressPage('OpenRisk wird eingerichtet',
    'Bitte warten – je nach Internet und Kartengröße einige Minuten.');
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  // Bei einem Update bleibt der Ordner – nur bei einer Neuinstallation fragen
  Result := (PageID = wpSelectDir) and IsUpdate();
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  Port: Integer;
  D: String;
begin
  Result := True;
  if CurPageID = wpSelectDir then
  begin
    D := WizardForm.DirEdit.Text;
    if (Copy(D, 1, 2) = '\\') or (Length(D) <= 3) then
    begin
      MsgBox('Bitte einen Unterordner auf einer lokalen Festplatte wählen, z. B. C:\Tools\OpenRisk.', mbError, MB_OK);
      Result := False;
    end;
  end;
  if CurPageID = PortPage.ID then
  begin
    Port := StrToIntDef(Trim(PortPage.Values[0]), 0);
    if (Port < 1024) or (Port > 65535) then
    begin
      MsgBox('Bitte einen Port zwischen 1024 und 65535 eingeben.', mbError, MB_OK);
      Result := False;
    end;
  end;
  if (CurPageID = OptPage.ID) and (OptPython >= 0) and not OptPage.Values[OptPython] then
  begin
    MsgBox('OpenRisk benötigt Python (ab Version 3.11).' + #13#10#13#10 +
      'Entweder den Haken bei „Python … automatisch installieren“ setzen oder Python selbst von python.org installieren ' +
      'und dieses Setup danach erneut starten.', mbInformation, MB_OK);
    Result := False;
  end;
end;

function YesNo(B: Boolean): String;
begin
  if B then Result := 'ja' else Result := 'nein';
end;

function MapZoom(): String;
var
  I, N: Integer;
  S: String;
begin
  S := MapZooms + ',';
  N := MapPage.SelectedValueIndex;
  for I := 1 to N do S := Copy(S, Pos(',', S) + 1, Length(S));
  Result := Copy(S, 1, Pos(',', S) - 1);
end;

function UpdateReadyMemo(Space, NewLine, MemoUserInfoInfo, MemoDirInfo, MemoTypeInfo,
  MemoComponentsInfo, MemoGroupInfo, MemoTasksInfo: String): String;
begin
  if IsUpdate() then
    Result := 'Vorgang:' + NewLine + Space + 'Update von ' + OldVersion + ' auf {#AppVersion} (Daten bleiben erhalten)'
  else
    Result := 'Vorgang:' + NewLine + Space + 'Neuinstallation von OpenRisk {#AppVersion}';
  Result := Result + NewLine + NewLine + 'Ordner:' + NewLine + Space + WizardForm.DirEdit.Text +
    NewLine + NewLine + 'Adresse:' + NewLine + Space + 'http://localhost:' + Trim(PortPage.Values[0]) +
    NewLine + NewLine + 'Offline-Karte:' + NewLine + Space + MapPage.CheckListBox.ItemCaption[MapPage.SelectedValueIndex] +
    NewLine + NewLine + 'Optionen:' + NewLine +
    Space + 'Desktop-Verknüpfung: ' + YesNo(OptPage.Values[OptDesktop]) + NewLine +
    Space + 'Automatisch starten: ' + YesNo(OptPage.Values[OptAutostart]) + NewLine +
    Space + 'Öl-Pipelines laden: ' + YesNo(OptPage.Values[OptPipelines]) + NewLine +
    Space + 'Militärbasen laden: ' + YesNo(OptPage.Values[OptBases]);
  if OptPython >= 0 then Result := Result + NewLine + Space + 'Python installieren: ' + YesNo(OptPage.Values[OptPython])
  else Result := Result + NewLine + NewLine + 'Python:' + NewLine + Space + PyVersion + ' (' + Python + ')';
end;

function WantDesktop(): Boolean;
begin
  Result := Ok and OptPage.Values[OptDesktop];
end;

function WantAutostart(): Boolean;
begin
  Result := Ok and OptPage.Values[OptAutostart];
end;

function InstallOk(): Boolean;
begin
  Result := Ok;
end;

function ReadFirstLine(const F: String): String;
var
  L: TArrayOfString;
begin
  Result := '';
  if LoadStringsFromFile(F, L) and (GetArrayLength(L) > 0) then Result := Trim(L[0]);
end;

function LogTail(const F: String; N: Integer): String;
var
  L: TArrayOfString;
  I, S: Integer;
begin
  Result := '';
  if not LoadStringsFromFile(F, L) then Exit;
  S := GetArrayLength(L) - N;
  if S < 0 then S := 0;
  for I := S to GetArrayLength(L) - 1 do Result := Result + L[I] + #13#10;
end;

// Nach dem Entpacken: install.ps1 unsichtbar ausführen und dabei den aktuellen Schritt anzeigen
procedure RunInstaller();
var
  Args, Done, Prog, Step: String;
  Code, I: Integer;
begin
  LogFile := ExpandConstant('{tmp}\install-ausgabe.log');
  Done := ExpandConstant('{tmp}\fertig.txt');
  Prog := ExpandConstant('{tmp}\schritt.txt');
  // Desktop- und Autostart-Verknüpfungen legt das Setup selbst an (und entfernt sie bei der Deinstallation)
  Args := '-NoProfile -ExecutionPolicy Bypass -File "' + ExpandConstant('{tmp}\pkg\install.ps1') + '" -Unattended -NoShortcut' +
    ' -InstallDir "' + WizardForm.DirEdit.Text + '" -Port ' + Trim(PortPage.Values[0]) + ' -MapZoom ' + MapZoom() +
    ' -ProgressFile "' + Prog + '"';
  if not OptPage.Values[OptPipelines] then Args := Args + ' -NoPipelines';
  if not OptPage.Values[OptBases] then Args := Args + ' -NoBases';
  if (OptPython >= 0) and OptPage.Values[OptPython] then Args := Args + ' -AllowPythonInstall';

  ProgressPage.SetText('Wird vorbereitet …', '');
  ProgressPage.Show;
  try
    Exec(ExpandConstant('{cmd}'), '/V:ON /S /C ""' + PowerShellExe() + '" ' + Args +
      ' > "' + LogFile + '" 2>&1 & echo !ERRORLEVEL!> "' + Done + '""', '', SW_HIDE, ewNoWait, Code);
    I := 0;
    while not FileExists(Done) and (I < 54000) do   // höchstens 90 Minuten (große Karte, Pipelines, Basen)
    begin
      ProgressPage.Animate;
      Sleep(100);
      I := I + 1;
      if I mod 5 = 0 then
      begin
        Step := ReadFirstLine(Prog);
        if Step <> '' then ProgressPage.SetText(Step + ' …', '');
      end;
    end;
    Sleep(300);
    Ok := ReadFirstLine(Done) = '0';
  finally
    ProgressPage.Hide;
  end;

  ForceDirectories(WizardForm.DirEdit.Text + '\data');
  FileCopy(LogFile, WizardForm.DirEdit.Text + '\data\setup-ausgabe.log', False);
  if not Ok then
    MsgBox('Die Einrichtung ist nicht vollständig gelungen.' + #13#10#13#10 + LogTail(LogFile, 12) + #13#10 +
      'Vollständiges Protokoll: ' + WizardForm.DirEdit.Text + '\data\setup-ausgabe.log', mbError, MB_OK);
end;

procedure CurPageChanged(CurPageID: Integer);
var
  T: String;
begin
  if CurPageID = wpFinished then
  begin
    if Ok then
    begin
      T := 'OpenRisk {#AppVersion} ist eingerichtet.' + #13#10#13#10 +
        'Oberfläche im Browser:' + #13#10 + '    http://localhost:' + Trim(PortPage.Values[0]) + #13#10#13#10 +
        'Starten später über „OpenRisk“ im Startmenü';
      if OptPage.Values[OptDesktop] then T := T + ' oder auf dem Desktop';
      T := T + '.' + #13#10 + 'Updates kommen danach über Einstellungen → Software-Update, Update-Quelle:' + #13#10 +
        '    https://github.com/phillhf/openrisk/releases/latest/download';
    end else
      T := 'Die Einrichtung ist nicht vollständig gelungen. Details stehen im Protokoll:' + #13#10 +
        WizardForm.DirEdit.Text + '\data\setup-ausgabe.log';
    WizardForm.FinishedLabel.Caption := T;
    WizardForm.AdjustLabelHeight(WizardForm.FinishedLabel);
    WizardForm.RunList.Top := WizardForm.FinishedLabel.Top + WizardForm.FinishedLabel.Height + ScaleY(12);
  end;
end;

// ---- Deinstallation: OpenRisk beenden, Programm entfernen, Daten auf Wunsch behalten
var
  DeleteData: Boolean;

function InitializeUninstall(): Boolean;
begin
  Result := True;
  DeleteData := False;
  if not UninstallSilent() then
    DeleteData := MsgBox('Sollen auch die Daten gelöscht werden?' + #13#10#13#10 +
      'Datenbank (Verlauf, Spuren), Offline-Karten, Höhendaten, Einstellungen und API-Schlüssel – das können ' +
      'mehrere GB sein.' + #13#10 +
      'Bei „Nein“ bleiben sie im Ordner ' + ExpandConstant('{app}') + ' erhalten – eine spätere Neuinstallation ' +
      'übernimmt sie.', mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Args: String;
  Code: Integer;
begin
  // erst wenn das Setup seine eigenen Dateien entfernt hat: install.ps1 räumt Programm und Python-Umgebung weg
  if CurUninstallStep <> usPostUninstall then Exit;
  if not FileExists(ExpandConstant('{app}\install.ps1')) then Exit;
  FileCopy(ExpandConstant('{app}\install.ps1'), ExpandConstant('{tmp}\uninstall.ps1'), False);
  Args := '-NoProfile -ExecutionPolicy Bypass -File "' + ExpandConstant('{tmp}\uninstall.ps1') + '" -Uninstall -Unattended' +
    ' -InstallDir "' + ExpandConstant('{app}') + '"';
  if not DeleteData then Args := Args + ' -KeepData';
  Exec(PowerShellExe(), Args, '', SW_HIDE, ewWaitUntilTerminated, Code);
end;
