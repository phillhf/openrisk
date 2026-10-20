<#
.SYNOPSIS
  OpenRisk unter Windows installieren (oder mit -Uninstall entfernen).

.DESCRIPTION
  Fragt Installationsort, Port und Kartendetail ab, kopiert das Programm,
  richtet Python-Umgebung und Abhängigkeiten ein, lädt Karte und Geodaten und
  legt Startverknüpfungen an. Ohne Administratorrechte.

  Start per Doppelklick auf install.bat oder:
    powershell -ExecutionPolicy Bypass -File install.ps1
  Ohne Rückfragen:
    install.ps1 -InstallDir D:\OpenRisk -Port 8642 -MapZoom 7 -Unattended
  Das grafische Setup (installer\OpenRisk.iss) ruft dieses Skript unsichtbar auf:
    -Detect -DetectFile <Datei>   vorhandene Installation, Port und Python erkennen (key=value-Zeilen)
    -ProgressFile <Datei>         aktuellen Schritt für die Fortschrittsanzeige schreiben
    -AllowPythonInstall           fehlendes Python ohne Rückfrage über winget installieren
    -Uninstall -KeepData          Programm entfernen, Datenbank, Karten und config.toml behalten
#>
param(
    [string]$InstallDir,
    [int]$Port = 0,
    [int]$MapZoom = -1,       # 0 = keine Karte laden, 5..15 = Detailstufe
    [switch]$NoPipelines,     # Öl-Pipelines nicht laden (dauert 10-20 Minuten)
    [switch]$NoBases,         # Militärbasen nicht laden (dauert 10-20 Minuten)
    [switch]$Autostart,       # beim Anmelden automatisch starten
    [switch]$NoShortcut,      # keine Desktop-Verknüpfung
    [switch]$Unattended,      # keine Rückfragen, Standardwerte verwenden
    [switch]$AllowPythonInstall,
    [switch]$Detect,
    [string]$DetectFile,
    [string]$ProgressFile,
    [switch]$KeepData,
    [switch]$Uninstall
)

$ErrorActionPreference = 'Stop'
trap { Write-Host "`nFehler: $_" -ForegroundColor Red; exit 1 }
$Source = $PSScriptRoot
$DefaultDir = Join-Path $env:LOCALAPPDATA 'OpenRisk'
$DefaultPort = 8642
$MapZoomLimit = 15
$MinPython = [version]'3.11'
$Desktop = [Environment]::GetFolderPath('Desktop')
$Startup = [Environment]::GetFolderPath('Startup')

function Write-Step($text) {
    Write-Host "`n== $text" -ForegroundColor Cyan
    if ($ProgressFile) { try { [System.IO.File]::WriteAllText($ProgressFile, $text) } catch { } }
}

function Stop-OpenRisk($dir) {
    # läuft OpenRisk aus diesem Ordner, ließen sich Python-Umgebung und Dateien nicht ersetzen
    $venv = (Join-Path $dir '.venv').TrimEnd('\') + '\'
    $running = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($venv, [System.StringComparison]::OrdinalIgnoreCase) }
    foreach ($process in $running) {
        Write-Host "OpenRisk wird beendet (Prozess $($process.ProcessId))."
        Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
    }
    if ($running) { Start-Sleep -Seconds 2 }
}

function Ask($question, $default) {
    if ($Unattended) { return $default }
    $answer = Read-Host "$question [$default]"
    if ([string]::IsNullOrWhiteSpace($answer)) { return $default }
    return $answer.Trim()
}

function Ask-Int($question, [int]$default) {
    while ($true) {
        $number = 0
        if ([int]::TryParse((Ask $question $default), [ref]$number)) { return $number }
        Write-Warning 'Bitte eine Zahl eingeben.'
    }
}

function Ask-YesNo($question, [bool]$default) {
    if ($Unattended) { return $default }
    $hint = if ($default) { 'J/n' } else { 'j/N' }
    $answer = Read-Host "$question [$hint]"
    if ([string]::IsNullOrWhiteSpace($answer)) { return $default }
    return $answer.Trim().ToLower().StartsWith('j')
}

function Test-PortFree([int]$candidate) {
    # Schlägt auch bei Ports fehl, die Windows reserviert hat (z. B. häufig 8080)
    try {
        $listener = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, $candidate)
        $listener.Start()
        $listener.Stop()
        return $true
    } catch { return $false }
}

function Get-PythonVersion($exe, $exeArgs) {
    # Fehlerausgaben (z. B. des Store-Platzhalters "python.exe") dürfen das Skript nicht abbrechen
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $text = & $exe @exeArgs -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($LASTEXITCODE -eq 0 -and $text -match '^\d+\.\d+$') { return [version]$text }
    } catch { }
    finally { $ErrorActionPreference = $previous }
    return $null
}

function Find-Python {
    $candidates = @(
        @{ Exe = 'py'; Args = @('-3') },
        @{ Exe = 'python'; Args = @() },
        @{ Exe = 'python3'; Args = @() }
    )
    Get-ChildItem (Join-Path $env:LOCALAPPDATA 'Programs\Python') -Filter python.exe -Recurse -Depth 1 -ErrorAction SilentlyContinue |
        ForEach-Object { $candidates += @{ Exe = $_.FullName; Args = @() } }
    foreach ($candidate in $candidates) {
        if (-not (Get-Command $candidate.Exe -ErrorAction SilentlyContinue)) { continue }
        $version = Get-PythonVersion $candidate.Exe $candidate.Args
        if ($version -and $version -ge $MinPython) { return $candidate }
    }
    return $null
}

function Invoke-Native($description, $exe, $exeArgs, [switch]$Optional) {
    & $exe @exeArgs | Out-Host
    if ($LASTEXITCODE -ne 0) {
        if (-not $Optional) { throw "$description fehlgeschlagen (Exit-Code $LASTEXITCODE)." }
        Write-Warning "$description fehlgeschlagen - kann später nachgeholt werden."
    }
}

function New-Shortcut($path, $target, $workDir) {
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($path)
    $shortcut.TargetPath = $target
    $shortcut.WorkingDirectory = $workDir
    $shortcut.WindowStyle = 7   # minimiert
    $shortcut.Description = 'OpenRisk Risikomonitor'
    $shortcut.Save()
}

# ------------------------------------------------------------------ Erkennen
if ($Detect) {
    # für das grafische Setup: was ist schon da? (key=value, eine Angabe je Zeile)
    # nur der angegebene Ordner, sonst die üblichen Orte; eine Installation mit Update-System (updater.py) hat Vorrang
    $candidates = if ($InstallDir) { @($InstallDir) } else { @('C:\Tools\OpenRisk', $DefaultDir) }
    $dir = $candidates | Where-Object { Test-Path (Join-Path $_ 'openrisk\updater.py') } | Select-Object -First 1
    if (-not $dir) { $dir = $candidates | Where-Object { Test-Path (Join-Path $_ 'openrisk\__main__.py') } | Select-Object -First 1 }
    $lines = @()
    $init = if ($dir) { Join-Path $dir 'openrisk\__init__.py' } else { '' }
    if ($init -and (Test-Path $init)) {
        $lines += 'found=1', "dir=$dir"
        $match = Select-String -Path $init -Pattern '__version__\s*=\s*"([^"]+)"' | Select-Object -First 1
        if ($match) { $lines += "version=$($match.Matches[0].Groups[1].Value)" }
        $config = Join-Path $dir 'config.toml'
        if (Test-Path $config) {
            $portMatch = Select-String -Path $config -Pattern '^\s*port\s*=\s*(\d+)' | Select-Object -First 1
            if ($portMatch) { $lines += "port=$($portMatch.Matches[0].Groups[1].Value)" }
        }
        if (Test-Path (Join-Path $dir 'data\map\world.pmtiles')) { $lines += 'map=1' }
        $lines += "desktop=$([int](Test-Path (Join-Path $Desktop 'OpenRisk.lnk')))"
        $lines += "autostart=$([int](Test-Path (Join-Path $Startup 'OpenRisk.lnk')))"
    } else { $lines += 'found=0' }
    $python = Find-Python
    if ($python) { $lines += "python=$($python.Exe)", "pyversion=$(Get-PythonVersion $python.Exe $python.Args)" }
    if (Get-Command winget -ErrorAction SilentlyContinue) { $lines += 'winget=1' }
    if ($DetectFile) { [System.IO.File]::WriteAllLines($DetectFile, $lines) } else { $lines }
    return
}

# ------------------------------------------------------------------ Entfernen
if ($Uninstall) {
    if (-not $InstallDir) { $InstallDir = Ask 'Installationsort von OpenRisk' $DefaultDir }
    if (-not (Test-Path (Join-Path $InstallDir 'openrisk\__main__.py')) -and -not (Test-Path (Join-Path $InstallDir 'data'))) {
        throw "In '$InstallDir' liegt keine OpenRisk-Installation."
    }
    if (-not $KeepData -and -not $Unattended -and
        -not (Ask-YesNo "OpenRisk samt Datenbank und Karten in '$InstallDir' löschen?" $false)) { return }
    Stop-OpenRisk $InstallDir
    # nur Verknüpfungen, die auf diese Installation zeigen - eine andere Installation behält ihre
    $shell = New-Object -ComObject WScript.Shell
    $prefix = $InstallDir.TrimEnd('\') + '\'
    foreach ($link in (Join-Path $Desktop 'OpenRisk.lnk'), (Join-Path $Startup 'OpenRisk.lnk')) {
        if ((Test-Path $link) -and $shell.CreateShortcut($link).TargetPath.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
            Remove-Item $link -ErrorAction SilentlyContinue
        }
    }
    Set-Location $env:TEMP   # nicht im zu löschenden Ordner stehen
    if ($KeepData) {
        # Programm weg, eigene Daten bleiben: eine spätere Neuinstallation übernimmt sie
        foreach ($item in '.venv', 'openrisk', 'web', 'tools', 'deploy', 'OpenRisk.bat', 'requirements.txt', 'README.md',
                          'config.example.toml', 'install.ps1', 'install.bat') {
            Remove-Item (Join-Path $InstallDir $item) -Recurse -Force -ErrorAction SilentlyContinue
        }
        Write-Host "OpenRisk wurde entfernt. Daten und config.toml liegen weiter in $InstallDir."
        return
    }
    Remove-Item $InstallDir -Recurse -Force -ErrorAction SilentlyContinue
    if (Test-Path $InstallDir) {
        Write-Warning "Nicht alles ließ sich löschen (läuft OpenRisk noch?). Rest bitte von Hand entfernen: $InstallDir"
    } else {
        Write-Host 'OpenRisk wurde entfernt.'
    }
    return
}

# ------------------------------------------------------------------- Abfragen
Write-Host 'OpenRisk - Installation' -ForegroundColor Cyan
if (-not (Test-Path (Join-Path $Source 'openrisk\__main__.py'))) {
    throw "install.ps1 muss im OpenRisk-Ordner liegen (neben 'openrisk' und 'tools')."
}

if (-not $InstallDir) { $InstallDir = Ask 'Installationsort' $DefaultDir }
$InstallDir = [System.IO.Path]::GetFullPath($InstallDir)
$InPlace = $InstallDir.TrimEnd('\') -ieq $Source.TrimEnd('\')

while ($true) {
    if ($Port -eq 0) { $Port = Ask-Int 'Port der Weboberfläche' $DefaultPort }
    if ($Port -lt 1024 -or $Port -gt 65535) {
        Write-Warning "Port $Port liegt außerhalb von 1024-65535."
    } elseif (-not (Test-PortFree $Port)) {
        Write-Warning "Port $Port ist belegt oder von Windows reserviert."
    } else { break }
    if ($Unattended) { throw "Port $Port ist nicht verwendbar." }
    $Port = 0
}

$sourceMap = Join-Path $Source 'data\map\world.pmtiles'
$targetMap = Join-Path $InstallDir 'data\map\world.pmtiles'
$haveMap = (Test-Path $sourceMap) -or (Test-Path $targetMap)
if ($MapZoom -lt 0) {
    if ($haveMap) {
        Write-Host 'Vorhandene Offline-Karte wird übernommen (Detailstufe später unter Einstellungen änderbar).'
        $MapZoom = 0
    } else {
        Write-Host "`nOffline-Karte, ganze Welt - Größe je Detailstufe:"
        Write-Host '   5 = 15 MB    6 = 45 MB    7 = 190 MB   8 = 560 MB'
        Write-Host '   9 = 1,6 GB  10 = 3,8 GB  11 = 8 GB    12 = 18 GB   15 = 138 GB (alles)'
        Write-Host '   0 = keine Karte  (einzelne Länder in voller Detailstufe später unter Einstellungen)'
        $MapZoom = Ask-Int 'Detailstufe' 7
    }
}
if ($MapZoom -gt $MapZoomLimit) {
    throw "Detailstufe $MapZoom gibt es nicht (höchstens $MapZoomLimit)."
}

$wantPipelines = -not $NoPipelines
$havePipelines = (Test-Path (Join-Path $Source 'data\geo\pipelines.geojson')) -or (Test-Path (Join-Path $InstallDir 'data\geo\pipelines.geojson'))
if ($wantPipelines -and -not $havePipelines) {
    $wantPipelines = Ask-YesNo 'Öl-Pipelines jetzt laden (dauert 10-20 Minuten)?' $true
}
$wantBases = -not $NoBases
$haveBases = (Test-Path (Join-Path $Source 'data\geo\bases.geojson')) -or (Test-Path (Join-Path $InstallDir 'data\geo\bases.geojson'))
if ($wantBases -and -not $haveBases) {
    $wantBases = Ask-YesNo 'Militärbasen jetzt laden (dauert 10-20 Minuten)?' $true
}
$wantShortcut = (-not $NoShortcut) -and (Ask-YesNo 'Desktop-Verknüpfung anlegen?' $true)
$wantAutostart = $Autostart -or ((-not $Unattended) -and (Ask-YesNo 'Beim Anmelden automatisch starten?' $false))

# --------------------------------------------------------------------- Python
Write-Step 'Python'
$python = Find-Python
if (-not $python) {
    Write-Host "Python $MinPython oder neuer wurde nicht gefunden."
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    $installPython = $AllowPythonInstall -or ((-not $Unattended) -and
        (Ask-YesNo 'Python 3.13 jetzt über winget installieren (stimmt dessen Lizenzbedingungen zu)?' $true))
    if ($winget -and $installPython) {
        Invoke-Native 'Python-Installation' 'winget' @('install', '-e', '--id', 'Python.Python.3.13', '--scope', 'user',
            '--accept-package-agreements', '--accept-source-agreements')
        $python = Find-Python
    }
    if (-not $python) { throw "Bitte Python $MinPython+ von https://www.python.org/downloads/ installieren und den Installer erneut starten." }
}
Write-Host "Verwende $($python.Exe) $($python.Args) (Version $(Get-PythonVersion $python.Exe $python.Args))"

# -------------------------------------------------------------------- Dateien
Stop-OpenRisk $InstallDir
Write-Step "Programmdateien nach $InstallDir"
if ($InPlace) {
    Write-Host 'Installation im Quellordner - nichts zu kopieren.'
} else {
    New-Item -ItemType Directory -Force $InstallDir | Out-Null
    foreach ($dir in 'openrisk', 'tools', 'web', 'deploy', 'data\map', 'data\geo') {
        $from = Join-Path $Source $dir
        if (-not (Test-Path $from)) { continue }
        # /XO: neuere Dateien am Ziel (z. B. eine größere Karte) bleiben erhalten
        robocopy $from (Join-Path $InstallDir $dir) /E /XO /XD __pycache__ pipelines_cache bases_cache /XF *.part /NFL /NDL /NJH /NJS /NP | Out-Null
        if ($LASTEXITCODE -ge 8) { throw "Kopieren von '$dir' fehlgeschlagen (robocopy $LASTEXITCODE)." }
    }
    foreach ($file in 'requirements.txt', 'README.md', 'config.example.toml', 'install.ps1', 'install.bat') {
        Copy-Item (Join-Path $Source $file) $InstallDir -Force
    }
}

# ------------------------------------------------------------- Abhängigkeiten
Write-Step 'Python-Umgebung und Abhängigkeiten'
$venvPython = Join-Path $InstallDir '.venv\Scripts\python.exe'
if (-not (Test-Path $venvPython)) {
    Invoke-Native 'Anlegen der Python-Umgebung' $python.Exe ($python.Args + @('-m', 'venv', (Join-Path $InstallDir '.venv')))
}
Invoke-Native 'Installation der Abhängigkeiten' $venvPython @('-m', 'pip', 'install', '--disable-pip-version-check',
    '--quiet', '-r', (Join-Path $InstallDir 'requirements.txt'))

# ------------------------------------------------------------- Konfiguration
Write-Step 'Konfiguration'
$configPath = Join-Path $InstallDir 'config.toml'
$utf8 = New-Object System.Text.UTF8Encoding($false)   # ohne BOM, sonst liest Python die Datei nicht
if (Test-Path $configPath) {
    $text = [System.IO.File]::ReadAllText($configPath)
    $pattern = New-Object regex '(?m)^(\s*port\s*=\s*)\d+'
    if ($pattern.IsMatch($text)) {
        [System.IO.File]::WriteAllText($configPath, $pattern.Replace($text, "`${1}$Port", 1), $utf8)
        Write-Host "Vorhandene config.toml behalten, Port auf $Port gesetzt."
    } else {
        Write-Warning "Vorhandene config.toml enthält keinen Port - bitte dort unter [server] 'port = $Port' eintragen."
    }
} else {
    $text = "# OpenRisk - weitere Optionen siehe config.example.toml`r`n[server]`r`nhost = `"127.0.0.1`"`r`nport = $Port`r`n"
    [System.IO.File]::WriteAllText($configPath, $text, $utf8)
    Write-Host "config.toml angelegt (Port $Port)."
}

# ----------------------------------------------------------- Offline-Daten
$env:PYTHONIOENCODING = 'utf-8'
Push-Location $InstallDir
try {
    Write-Step 'Bibliotheken, Schriften und Geodaten'
    Invoke-Native 'Download der Offline-Dateien' $venvPython @('tools\fetch_assets.py')

    if ($MapZoom -gt 0) {
        Write-Step "Offline-Karte bis Zoomstufe $MapZoom"
        Invoke-Native 'Karten-Download' $venvPython @('tools\fetch_map.py', '--maxzoom', "$MapZoom") -Optional
    }
    if ($wantPipelines -and -not (Test-Path 'data\geo\pipelines.geojson')) {
        Write-Step 'Öl-Pipelines (OpenStreetMap)'
        Invoke-Native 'Pipeline-Download' $venvPython @('tools\fetch_pipelines.py') -Optional
    }
    if ($wantBases -and -not (Test-Path 'data\geo\bases.geojson')) {
        Write-Step 'Militärbasen (OpenStreetMap)'
        Invoke-Native 'Basen-Download' $venvPython @('tools\fetch_bases.py') -Optional
    }
} finally { Pop-Location }

# ------------------------------------------------------------ Startdateien
Write-Step 'Startdateien'
$launcher = Join-Path $InstallDir 'OpenRisk.bat'
# Exit-Code 75 = nach einem Software-Update neu starten (ohne den Browser erneut zu öffnen)
$batch = @(
    '@echo off', 'cd /d "%~dp0"', 'title OpenRisk', 'set OPENRISK_LAUNCHER=1',
    '".venv\Scripts\python.exe" -m openrisk --open %*',
    ':loop', 'if not "%errorlevel%"=="75" goto :eof',
    '".venv\Scripts\python.exe" -m openrisk %*', 'goto loop', ''
) -join "`r`n"
[System.IO.File]::WriteAllText($launcher, $batch, [System.Text.Encoding]::ASCII)
if ($wantShortcut) { New-Shortcut (Join-Path $Desktop 'OpenRisk.lnk') $launcher $InstallDir }
if ($wantAutostart) { New-Shortcut (Join-Path $Startup 'OpenRisk.lnk') $launcher $InstallDir }

Write-Host "`nFertig." -ForegroundColor Green
Write-Host "  Starten:      $launcher"
Write-Host "  Oberfläche:   http://localhost:$Port"
Write-Host "  Einstellungen: $configPath (Port, Quellen) und Zahnrad in der Oberfläche (Karte)"
Write-Host "  Entfernen:    install.bat -Uninstall"
