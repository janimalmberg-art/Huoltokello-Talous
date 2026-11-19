# Poistaa Huoltokellon asennuksen: prosessi, ajastus, pikakuvakkeet,
# Windowsin Sovellukset-listauksesta, (palomuurisaanto) ja asennuskansion.
# Data voidaan sailyttaa varmuuskopiona tyopoydalle.
#
# KORJATTU VERSIO:
#  - KRIITTINEN: poisto kohdistuu vain kansioon, jossa on Huoltokellon ohjelmatiedosto
#    (aiemmin Get-HkInstallDir saattoi palauttaa arvatun kansion ja Remove-Item -Recurse
#    poisti sen; -InstallDir "C:\Users\Nimi" olisi tyhjentanyt koko profiilin)
#  - KRIITTINEN: jos datan varmuuskopiointi epaonnistuu, dataa EI poisteta
#    (aiemmin virhe vain tulostettiin ja kansio poistettiin silti)
#  - varmuuskopio .NET-zipilla (ei 2 Gt:n rajaa) ja arkiston tarkistus
param(
    [switch]$Silent,
    [switch]$PoistaData,
    [string]$InstallDir
)
if ($InstallDir) { $script:HkInstallDirOverride = $InstallDir.TrimEnd('\') }
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\_yhteinen.ps1"

$dir = Assert-HkSafeInstallDir (Get-HkInstallDir)
Write-Host "=== HUOLTOKELLO - Asennuksen poisto ===" -ForegroundColor Yellow
Write-Host "Poistettava asennus: $dir"
if (-not $Silent) {
    $ok = Read-Host "Poistetaanko Huoltokello tasta kansiosta? (k/o)"
    if ($ok -notmatch '^[kK]') { Write-Host "Peruutettu."; exit 0 }
}

# 1) Data talteen ENSIN - jos tama epaonnistuu, mitaan ei poisteta
$appData = Join-Path $dir 'AppData'
$desktop = [Environment]::GetFolderPath('Desktop')
$keepData = -not $PoistaData
if (-not $Silent -and -not $PoistaData) {
    $ans = Read-Host "Sailytetaanko tietokanta ja liitteet varmuuskopiona tyopoydalle? (k/o, Enter = k)"
    if ($ans -match '^[oO]') {
        $c = Read-Host "Data poistetaan LOPULLISESTI ilman varmuuskopiota. Kirjoita POISTA vahvistaaksesi"
        if ($c -ne 'POISTA') { Write-Host "Peruutettu."; exit 0 }
        $keepData = $false
    }
}
Stop-HkApp | Out-Null
if ($keepData -and (Test-Path -LiteralPath $appData)) {
    $dest = Join-Path $desktop ("Huoltokello-data-" + (Get-Date -Format 'yyyyMMdd-HHmmss') + ".zip")
    try {
        New-HkZipFromFolder -Folder $appData -ZipPath $dest | Out-Null
        $null = Protect-HkSecretFile -Path $dest
        Write-Host "Data varmuuskopioitu: $dest"
    } catch {
        Write-Host "Varmuuskopiointi epaonnistui: $($_.Exception.Message)" -ForegroundColor Red
        Write-Host "Poisto keskeytettiin, jotta tietoja ei menetetä. Mitaan ei poistettu." -ForegroundColor Red
        exit 3
    }
}

# 2) Automaattinen kaynnistys pois
$null = schtasks.exe /Delete /TN "Huoltokello" /F 2>$null

# 3) Pikakuvakkeet
$startMenu = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'
foreach ($lnk in @(
    (Join-Path $desktop 'Huoltokello.lnk'), (Join-Path $desktop 'Huoltokello-Ohjeet.lnk'),
    (Join-Path $startMenu 'Huoltokello.lnk'), (Join-Path $startMenu 'Huoltokello-Ohjeet.lnk'),
    (Join-Path ([Environment]::GetFolderPath('Startup')) 'Huoltokello.lnk')
)) { Remove-Item -LiteralPath $lnk -Force -ErrorAction SilentlyContinue }

# 4) Windowsin Sovellukset-listaus (HKCU)
Remove-Item 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Huoltokello' -Recurse -Force -ErrorAction SilentlyContinue

# 5) Palomuurisaanto
try {
    if ($Silent) {
        $null = Start-Process netsh -ArgumentList 'advfirewall firewall delete rule name=Huoltokello' -Wait -WindowStyle Hidden -PassThru
    } else {
        Start-Process netsh -ArgumentList 'advfirewall firewall delete rule name=Huoltokello' -Verb RunAs -Wait
    }
} catch { Write-Host "Palomuurisaantoa ei poistettu (yllapito-oikeuksia ei annettu)." }

# 6) Asennuskansio (3 yritysta tiedostolukkojen varalta)
for ($i = 1; $i -le 3 -and (Test-Path -LiteralPath $dir); $i++) {
    if ($i -gt 1) { Start-Sleep -Seconds (2 * $i) }
    Remove-Item -LiteralPath $dir -Recurse -Force -ErrorAction SilentlyContinue
}
if (Test-Path -LiteralPath $dir) { Write-Warning "Kansio ei tyhjentynyt kokonaan (jokin tiedosto kaytossa): $dir" }
else { Write-Host "Asennuskansio poistettu: $dir" }

Write-Host ""
Write-Host "Poisto valmis." -ForegroundColor Green
if (-not $Silent) { Read-Host "Sulje painamalla Enter" | Out-Null }
