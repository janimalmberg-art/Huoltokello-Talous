# ============================================================
# HUOLTOKELLO-TALOUS - asennus Windowsiin (PowerShell 5.1)
#
# Asentaa talousmoduulin Huoltokellon rinnalle:
#   ohjelma  -> %LOCALAPPDATA%\Huoltokello-Talous\app
#   data     -> %LOCALAPPDATA%\Huoltokello-Talous\Data (vain nykyinen kayttaja + SYSTEM)
#   kaynnistin, tyopoydan pikakuvake, valinnaiset ajastukset
#
# Vaatii Python 3.10+ (python.org tai: winget install Python.Python.3.12).
# Ei muita riippuvuuksia (vain Pythonin vakiokirjasto).
#
# Kaytto: powershell -ExecutionPolicy Bypass -File Asenna-Talous.ps1 [-Portti 8090]
#         [-Automaattikaynnistys] [-Ajastettu] [-Testaa]
# ============================================================
param(
    [int]$Portti = 8090,
    [switch]$Automaattikaynnistys,
    [switch]$Ajastettu,
    [switch]$Testaa,
    [string]$LisenssiAvain = '',
    [string]$LisenssiKoodi = ''
)
$ErrorActionPreference = 'Stop'
$src = $PSScriptRoot
$root = Join-Path $env:LOCALAPPDATA 'Huoltokello-Talous'
$app = Join-Path $root 'app'
$data = Join-Path $root 'Data'

function Find-Python {
    foreach ($cand in @(@('py', '-3'), @('python'), @('python3'))) {
        try {
            $exe = $cand[0]; $pre = @($cand | Select-Object -Skip 1)
            $v = & $exe @pre -c "import sys; print('%d.%d' % sys.version_info[:2]); print(sys.executable)" 2>$null
            if ($LASTEXITCODE -eq 0 -and $v) {
                $parts = $v[0].Split('.')
                if ([int]$parts[0] -eq 3 -and [int]$parts[1] -ge 10) { return $v[1].Trim() }
            }
        } catch { }
    }
    return $null
}

Write-Host "=== Huoltokello-Talous - asennus ===" -ForegroundColor Cyan
$py = Find-Python
if (-not $py) {
    Write-Host "Python 3.10 tai uudempi puuttuu." -ForegroundColor Yellow
    $ans = Read-Host "Asennetaanko Python 3.12 winget-tyokalulla (Microsoftin paketinhallinta)? (k/o)"
    if ($ans -match '^[kK]') {
        winget install --id Python.Python.3.12 -e --scope user --accept-package-agreements --accept-source-agreements
        $env:Path = [Environment]::GetEnvironmentVariable('Path', 'User') + ';' + [Environment]::GetEnvironmentVariable('Path', 'Machine')
        $py = Find-Python
    }
    if (-not $py) { throw "Asenna Python 3.10+ (https://www.python.org/downloads/windows/) ja aja asennus uudelleen." }
}
Write-Host "Python: $py"

# Ohjelmatiedostot
New-Item -ItemType Directory -Force -Path $app, $data | Out-Null
$sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$null = & icacls.exe @($root, '/inheritance:r', '/grant:r', "*$($sid):(OI)(CI)F", '/grant:r', '*S-1-5-18:(OI)(CI)F')
if (Test-Path (Join-Path $app 'huoltokello_talous')) { Remove-Item (Join-Path $app 'huoltokello_talous') -Recurse -Force }
Copy-Item (Join-Path $src 'huoltokello_talous') (Join-Path $app 'huoltokello_talous') -Recurse -Force
Copy-Item (Join-Path $src 'tests') (Join-Path $app 'tests') -Recurse -Force
foreach ($f in @('README.md', 'KATSELMOINTI.md')) { if (Test-Path (Join-Path $src $f)) { Copy-Item (Join-Path $src $f) $app -Force } }
Get-ChildItem (Join-Path $src 'docs') -ErrorAction SilentlyContinue | Copy-Item -Destination $app -Force
Write-Host "Ohjelma kopioitu: $app"

if ($Testaa) {
    Write-Host "Ajetaan testit..."
    Push-Location $app
    try { & $py -m unittest discover -s tests; if ($LASTEXITCODE -ne 0) { throw 'Testit epaonnistuivat.' } } finally { Pop-Location }
}

# Tietokanta ja ensimmainen johto-tunnus
$env:HUOLTOKELLO_TALOUS_DATA = $data
Push-Location $app
try { & $py -m huoltokello_talous --data $data alusta; if ($LASTEXITCODE -ne 0) { throw 'Alustus epaonnistui.' } }
finally { Pop-Location }

# LISSENSI (valinnainen): ilman lisenssia ohjelma toimii 90 paivan kokeilujaksolla.
# Aktivointi: konekoodi lahetetaan kehittajalle (jani.malmberg@gmail.com), joka
# myontaa avaimen + aktivointikoodin juuri talle koneelle Lisenssityokalulla.
Write-Host ''
Write-Host '=== Lisenssi (valinnainen) ===' -ForegroundColor Cyan
Push-Location $app
try { & $py -m huoltokello_talous --data $data lisenssi tila } finally { Pop-Location }
$av = $LisenssiAvain
if (-not $av) { $av = (Read-Host 'Lisenssiavain (tyhja = jatka kokeilujaksolla 90 pv)').Trim() }
if ($av) {
    $ko = $LisenssiKoodi
    if (-not $ko) { $ko = (Read-Host 'Aktivointikoodi (kehittajalta)').Trim() }
    Push-Location $app
    try {
        & $py -m huoltokello_talous --data $data lisenssi aktivoi --avain $av --koodi $ko
        if ($LASTEXITCODE -ne 0) { Write-Warning 'Aktivointi epaonnistui - ohjelma jatkaa kokeilujaksolla (90 pv). Aktivointi voi tehda jalkikateen Asetukset > Lisenssi -sivulla.' }
    } finally { Pop-Location }
} else {
    Write-Host 'Ohjelma jatkaa 90 paivan kokeilujaksolla. Aktivointi: ohjelman Asetukset > Lisenssi -sivu.'
}

# Kaynnistin
$launcher = Join-Path $root 'Kaynnista-Talous.cmd'
$cmd = "@echo off`r`ncd /d `"$app`"`r`nstart `"`" /min `"$py`" -m huoltokello_talous --data `"$data`" palvelin --port $Portti`r`n" +
       "timeout /t 3 /nobreak >nul`r`nstart `"`" http://127.0.0.1:$Portti`r`n"
[IO.File]::WriteAllText($launcher, $cmd, (New-Object Text.UTF8Encoding $false))
$wsh = New-Object -ComObject WScript.Shell
$sc = $wsh.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) 'Huoltokello-Talous.lnk'))
$sc.TargetPath = $launcher
$sc.WorkingDirectory = $app
$sc.Description = 'Huoltokello-Talous: tyoaika, laskutus, kirjanpito ja palkat'
$sc.IconLocation = "$env:SystemRoot\System32\imageres.dll,109"
$sc.Save()
Write-Host "Tyopoydan pikakuvake luotu: Huoltokello-Talous"

if ($Automaattikaynnistys) {
    $action = New-ScheduledTaskAction -Execute $py -Argument "-m huoltokello_talous --data `"$data`" palvelin --port $Portti" -WorkingDirectory $app
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
    Register-ScheduledTask -TaskName 'Huoltokello-Talous' -Action $action -Trigger $trigger -RunLevel Limited -Force | Out-Null
    Write-Host "Kaynnistyy automaattisesti kirjautumisen yhteydessa."
}
if ($Ajastettu) {
    # Ajastettu varmuuskopio + automaattinen kuukausivienti (vaikka palvelin ei olisi kaynnissa)
    $a1 = New-ScheduledTaskAction -Execute $py -Argument "-m huoltokello_talous --data `"$data`" varmuuskopio" -WorkingDirectory $app
    $a2 = New-ScheduledTaskAction -Execute $py -Argument "-m huoltokello_talous --data `"$data`" automaattivienti" -WorkingDirectory $app
    $t = New-ScheduledTaskTrigger -Daily -At '03:15'
    $set = New-ScheduledTaskSettingsSet -StartWhenAvailable
    Register-ScheduledTask -TaskName 'Huoltokello-Talous-Yoajo' -Action $a1, $a2 -Trigger $t -Settings $set -RunLevel Limited -Force | Out-Null
    Write-Host "Ajastettu yoajo (varmuuskopio + automaattivienti) klo 03.15."
}

Write-Host ""
Write-Host "VALMIS. Kaynnista tyopoydan kuvakkeesta 'Huoltokello-Talous' -> http://127.0.0.1:$Portti" -ForegroundColor Green
Write-Host "Data: $data  (varmuuskopiot: $data\varmuuskopiot)"
Write-Host "Lahiverkko/puhelimet: kayta HTTPS:aa (--host 0.0.0.0 --cert ... --key ...) - katso README.md."
