# Tekee varmuuskopion AppData-kansiosta (tietokanta + avaimet + liitteet)
# kansioon %USERPROFILE%\Huoltokello-Varmuuskopiot ja pitaa 30 uusinta.
#
# KORJATTU VERSIO: sovellus suljetaan aina, jos sen prosessi on kaynnissa (aiemmin
# vain jos /health vastasi -> SQLite-kanta saatettiin kopioida kesken kirjoituksen),
# .NET-zip ilman 2 Gt:n rajaa, arkiston tarkistus ja varmuuskopiokansion ACL.
param([int]$Sailyta = 30)
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\_yhteinen.ps1"

$dir = Assert-HkSafeInstallDir (Get-HkInstallDir)
$port = Get-HkPort
$appData = Join-Path $dir 'AppData'
if (-not (Test-Path -LiteralPath $appData)) { throw "AppData-kansiota ei loytynyt: $appData" }

$wasRunning = (Get-HkProcess).Count -gt 0
if ($wasRunning) {
    Write-Host "Suljetaan Huoltokello varmuuskopion ajaksi..."
    Stop-HkApp | Out-Null
}

try {
    $destDir = Get-HkBackupDir
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $dest = Join-Path $destDir "huoltokello-varmuuskopio-$stamp.zip"
    New-HkZipFromFolder -Folder $appData -ZipPath $dest | Out-Null
    $hash = (Get-FileHash -LiteralPath $dest -Algorithm SHA256).Hash
    Set-Content -LiteralPath ($dest + '.sha256') -Value "$hash  $(Split-Path -Leaf $dest)" -Encoding ASCII

    Get-ChildItem -LiteralPath $destDir -Filter 'huoltokello-varmuuskopio-*.zip' -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -Skip $Sailyta |
        ForEach-Object {
            Remove-Item -LiteralPath $_.FullName -Force -ErrorAction SilentlyContinue
            Remove-Item -LiteralPath ($_.FullName + '.sha256') -Force -ErrorAction SilentlyContinue
        }

    Write-Host "Varmuuskopio valmis: $dest"
    Write-Host ("Koko: {0:N1} Mt   SHA-256: {1}" -f ((Get-Item -LiteralPath $dest).Length / 1MB), $hash)
    Write-Host "Vinkki: kopioi varmuuskopiot saannollisesti myos toiselle laitteelle (3-2-1-saanto)."
}
finally {
    if ($wasRunning) {
        Write-Host "Kaynnistetaan Huoltokello uudelleen..."
        Start-HkApp -NoBrowser
        if (Wait-HkHealth -Port $port -Seconds 90) { Write-Host "Huoltokello taas kaynnissa." }
        else { Write-Warning "Huoltokello ei vastannut - kaynnista: Tyokalut\Kaynnista.ps1" }
    }
}
