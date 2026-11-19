# Palauttaa AppData-kansion valitusta varmuuskopiosta (zip).
#
# KORJATTU VERSIO:
#  - sovellus suljetaan aina ennen palautusta (aiemmin vain jos /health vastasi)
#  - nykyinen AppData SIIRRETAAN sivuun ennen purkua: aiemmin purku kirjoitti
#    vanhan kannan nykyisen paalle, jolloin vanhat -wal/-shm-tiedostot saattoivat
#    jaada ja korruptoida palautetun SQLite-kannan
#  - varmuuskopion rakenne ja polut tarkistetaan (zip slip -esto), SHA-256 jos saatavilla
#  - virhetilanteessa alkuperainen AppData palautetaan automaattisesti
param([string]$Varmuuskopio)
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\_yhteinen.ps1"

$dir = Assert-HkSafeInstallDir (Get-HkInstallDir)
$port = Get-HkPort
$appData = Join-Path $dir 'AppData'
$backupDir = Get-HkBackupDir

$zip = $Varmuuskopio
if (-not $zip) {
    $cands = @(Get-ChildItem -LiteralPath $backupDir -Filter '*.zip' | Sort-Object LastWriteTime -Descending)
    if ($cands.Count -eq 0) { throw "Varmuuskopioita ei loytynyt kansiosta: $backupDir" }
    Write-Host "Saatavilla olevat varmuuskopiot:"
    for ($i = 0; $i -lt $cands.Count; $i++) {
        Write-Host (" [{0}] {1} ({2:N1} Mt, {3})" -f ($i + 1), $cands[$i].Name, ($cands[$i].Length / 1MB), $cands[$i].LastWriteTime)
    }
    $sel = Read-Host ("Valitse numero (1-" + $cands.Count + ")")
    $n = 0
    if ([int]::TryParse($sel, [ref]$n) -and $n -ge 1 -and $n -le $cands.Count) { $zip = $cands[$n - 1].FullName }
}
if (-not $zip -or -not (Test-Path -LiteralPath $zip)) { throw "Varmuuskopiota ei valittu." }

# Eheys- ja rakennetarkistus ennen kuin mitaan muutetaan
$shaFile = $zip + '.sha256'
if (Test-Path -LiteralPath $shaFile) {
    $expected = ((Get-Content -LiteralPath $shaFile -Raw).Trim() -split '\s+')[0]
    $actual = (Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash
    if ($expected -ne $actual) { throw "Varmuuskopion tarkiste ei tasmaa - tiedosto on vioittunut tai muutettu." }
    Write-Host "SHA-256 tarkistettu."
}
$bad = Test-HkZipEntries -ZipPath $zip
if ($bad) { throw $bad }
Add-Type -AssemblyName System.IO.Compression.FileSystem
$z = [System.IO.Compression.ZipFile]::OpenRead($zip)
try {
    $hasDb = @($z.Entries | Where-Object { $_.FullName.Replace('\', '/') -eq 'AppData/huoltokello.db' }).Count -gt 0
} finally { $z.Dispose() }
if (-not $hasDb) { throw "Tiedosto ei ole Huoltokellon varmuuskopio (AppData/huoltokello.db puuttuu)." }

$confirm = Read-Host "Kaikki sovelluksen nykyinen data korvataan varmuuskopiolla. Jatketaanko? (k/o)"
if ($confirm -notmatch '^[kK]') { Write-Host "Peruutettu."; exit 0 }

$wasRunning = (Get-HkProcess).Count -gt 0
if ($wasRunning) { Stop-HkApp | Out-Null }

$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$aside = Join-Path $dir ("AppData.ennen-palautusta-" + $stamp)
try {
    if (Test-Path -LiteralPath $appData) {
        $pre = Join-Path $backupDir ("ennen-palautusta-" + $stamp + ".zip")
        New-HkZipFromFolder -Folder $appData -ZipPath $pre | Out-Null
        Write-Host "Nykyinen data varmuuskopioitu ensin: $pre"
        Move-Item -LiteralPath $appData -Destination $aside
    }
    $n = Expand-HkZipSafe -ZipPath $zip -Destination $dir
    if (-not (Test-Path -LiteralPath (Join-Path $appData 'huoltokello.db'))) { throw 'Palautus ei tuottanut tietokantaa.' }
    # Aiemman asennuksen avaintiedosto ei ole AppDatassa, joten push-avaimet sailyvat.
    Write-Host "Palautus valmis ($n tiedostoa). Edellinen data: $aside"
}
catch {
    Write-Warning "Palautus epaonnistui: $($_.Exception.Message) - palautetaan alkuperainen data."
    if (Test-Path -LiteralPath $aside) {
        if (Test-Path -LiteralPath $appData) { Remove-Item -LiteralPath $appData -Recurse -Force }
        Move-Item -LiteralPath $aside -Destination $appData
    }
    throw
}
finally {
    Start-HkApp -NoBrowser
    if (Wait-HkHealth -Port $port -Seconds 90) { Write-Host "Huoltokello kaynnissa." }
    else { Write-Warning "Kaynnista manuaalisesti: Tyokalut\Kaynnista.ps1" }
}
