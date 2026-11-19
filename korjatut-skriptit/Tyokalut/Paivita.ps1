# Paivittaa Huoltokellon uudempaan versioon uudesta app.zip-paketista.
# Kayttajan data (AppData-kansio) sailytetaan ja varmuuskopioidaan ensin.
# Kaytto: Tyokalut\Paivita.ps1 [-ZipPath "C:\polku\app.zip"] [-Sha256 <tarkiste>]
#
# KORJATTU VERSIO:
#  - paketin eheys: SHA-256 (parametri tai app.zip.sha256-tiedosto) tarkistetaan
#  - paketti ei saa sisaltaa AppData-kansiota eika appsettings.Production.json-tiedostoa
#    (aiemmin Expand-Archive -Force olisi ylikirjoittanut tietokannan/avaimet)
#  - zip slip -esto; purku ensin valiaikaiseen kansioon ja ohjelmatiedoston tarkistus
#  - ohjelmatiedostot varmuuskopioidaan ja PALAUTETAAN automaattisesti, jos uusi
#    versio ei kaynnisty (rollback)
#  - Compress-Archiven 2 Gt:n raja poistettu (.NET ZipFile)
param([string]$ZipPath, [string]$Sha256)
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\_yhteinen.ps1"

$dir = Assert-HkSafeInstallDir (Get-HkInstallDir)
$port = Get-HkPort

$zip = $ZipPath
if (-not $zip) {
    Add-Type -AssemblyName System.Windows.Forms
    $dlg = New-Object System.Windows.Forms.OpenFileDialog
    $dlg.Title = 'Valitse uusi app.zip-paivityspaketti'
    $dlg.Filter = 'ZIP-paketit (*.zip)|*.zip'
    if ($dlg.ShowDialog() -eq 'OK') { $zip = $dlg.FileName }
}
if (-not $zip -or -not (Test-Path -LiteralPath $zip)) { throw "app.zip ei valittu tai ei loytynyt." }

# --- 1) Paketin tarkistukset ennen mitaan muutoksia ---
if (-not $Sha256 -and (Test-Path -LiteralPath ($zip + '.sha256'))) {
    $Sha256 = ((Get-Content -LiteralPath ($zip + '.sha256') -Raw).Trim() -split '\s+')[0]
}
$actual = (Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash
if ($Sha256) {
    if ($Sha256.Trim().ToUpper() -ne $actual) { throw "Paketin SHA-256 ei tasmaa. Odotettu $Sha256, saatu $actual." }
    Write-Host "Paketin SHA-256 tarkistettu."
} else {
    Write-Warning "Paketille ei annettu SHA-256-tarkistetta. Tarkiste: $actual"
    $ok = Read-Host "Vertaa tarkistetta julkaisijan ilmoittamaan. Jatketaanko? (k/o)"
    if ($ok -notmatch '^[kK]') { exit 1 }
}
$bad = Test-HkZipEntries -ZipPath $zip -Forbidden @('AppData/*', 'appsettings.Production.json', '*/appsettings.Production.json')
if ($bad) { throw $bad }

$stage = Join-Path $env:TEMP ("hk-paivitys-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Force -Path $stage | Out-Null
try {
    Expand-HkZipSafe -ZipPath $zip -Destination $stage | Out-Null
    $newExe = Join-Path $stage $script:HkExeName
    if (-not (Test-Path -LiteralPath $newExe)) { throw "Paketista puuttuu $script:HkExeName - vaara paketti?" }
    $sig = Get-AuthenticodeSignature -LiteralPath $newExe
    if ($sig.Status -eq 'Valid') { Write-Host "Ohjelmatiedoston allekirjoitus: $($sig.SignerCertificate.Subject)" }
    else { Write-Warning "Ohjelmatiedosto ei ole digitaalisesti allekirjoitettu (tila: $($sig.Status))." }

    # --- 2) Talteen asetukset ---
    $prodPath = Join-Path $dir 'appsettings.Production.json'
    $settingsPath = Join-Path $dir 'appsettings.json'
    $migratedVapid = $null
    $smtpKeep = $null
    $jOldAll = $null
    if (Test-Path -LiteralPath $settingsPath) {
        $jOld = Get-Content -LiteralPath $settingsPath -Raw | ConvertFrom-Json
        if (-not (Test-Path -LiteralPath $prodPath) -and $jOld.Vapid -and $jOld.Vapid.PrivateKey) {
            $migratedVapid = @{ Subject = [string]$jOld.Vapid.Subject; PublicKey = [string]$jOld.Vapid.PublicKey; PrivateKey = [string]$jOld.Vapid.PrivateKey }
        }
        if ($jOld.Smtp -and $jOld.Smtp.Host) { $smtpKeep = $jOld.Smtp }
        $jOldAll = $jOld
    }

    Write-Host "Suljetaan Huoltokello..."
    Stop-HkApp | Out-Null

    # --- 3) Varmuuskopiot: data + nykyiset ohjelmatiedostot (rollback) ---
    $backupDir = Get-HkBackupDir
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $appData = Join-Path $dir 'AppData'
    if (Test-Path -LiteralPath $appData) {
        $pre = New-HkZipFromFolder -Folder $appData -ZipPath (Join-Path $backupDir "ennen-paivitysta-$stamp.zip")
        Write-Host "Data varmuuskopioitu: $pre"
    }
    $rollback = Join-Path $env:TEMP "hk-rollback-$stamp"
    New-Item -ItemType Directory -Force -Path $rollback | Out-Null
    Get-ChildItem -LiteralPath $dir -Force | Where-Object { $_.Name -notin @('AppData', 'Tyokalut') -and $_.Name -notlike 'AppData.*' } |
        Copy-Item -Destination $rollback -Recurse -Force

    # --- 4) Uudet tiedostot paikalleen ---
    Get-ChildItem -LiteralPath $stage -Force | Copy-Item -Destination $dir -Recurse -Force
    if (Test-Path -LiteralPath $settingsPath) {
        $j = Get-Content -LiteralPath $settingsPath -Raw | ConvertFrom-Json
        $j.SeedDemoData = $false
        if ($j.Vapid -and $j.Vapid.PSObject.Properties['PrivateKey']) { $j.Vapid.PSObject.Properties.Remove('PrivateKey') }
        if ($smtpKeep) { $j | Add-Member -NotePropertyName Smtp -NotePropertyValue $smtpKeep -Force }
        # Kayttajan asetukset (esim. sallitut salasanavastaanottajat), joita uusi paketti ei sisalla, sailytetaan
        if ($jOldAll) {
            foreach ($prop in $jOldAll.PSObject.Properties) {
                if (-not $j.PSObject.Properties[$prop.Name]) {
                    $j | Add-Member -NotePropertyName $prop.Name -NotePropertyValue $prop.Value -Force
                }
            }
        }
        $j | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $settingsPath -Encoding UTF8
    }
    if (-not (Test-Path -LiteralPath $prodPath)) {
        $kp = if ($migratedVapid) { $migratedVapid } else { New-HkVapidKeys }
        @{ Vapid = @{ Subject = 'mailto:info@huoltokello.fi'; PublicKey = $kp.PublicKey; PrivateKey = $kp.PrivateKey } } |
            ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $prodPath -Encoding UTF8
        if (-not (Protect-HkSecretFile -Path $prodPath)) { Write-Warning "Avaintiedoston kayttooikeuksia ei voitu rajata." }
    }

    # --- 5) Kaynnistys ja tarkistus; epaonnistuessa rollback ---
    Write-Host "Kaynnistetaan Huoltokello..."
    Start-HkApp -NoBrowser
    if (Wait-HkHealth -Port $port -Seconds 120) {
        Write-Host "Paivitys valmis - Huoltokello kaynnissa: http://127.0.0.1:$port" -ForegroundColor Green
        Remove-Item -LiteralPath $rollback -Recurse -Force -ErrorAction SilentlyContinue
    } else {
        Write-Warning "Uusi versio ei vastannut - palautetaan edellinen versio."
        Stop-HkApp | Out-Null
        Get-ChildItem -LiteralPath $rollback -Force | Copy-Item -Destination $dir -Recurse -Force
        Start-HkApp -NoBrowser
        if (Wait-HkHealth -Port $port -Seconds 90) { Write-Host "Edellinen versio palautettu ja kaynnissa." }
        throw "Paivitys peruttiin. Tarkista loki: $(Join-Path $appData 'app-err.log')"
    }
}
finally {
    Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue
}
