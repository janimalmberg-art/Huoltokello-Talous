# Ohjattu uuden Johto-tilin luonti (jos sovellukseen ei paase kirjautumaan).
# Kirjoittaa AppData\first-admin.json -tiedoston, jonka sovellus lukee seuraavassa
# kaynnistyksessa ja luo tilin.
#
# KORJATTU VERSIO:
#  - first-admin.json sisaltaa selkokielisen salasanan: tiedoston ACL rajataan heti
#    nykyiseen kayttajaan + SYSTEMiin, ja tiedosto POISTETAAN kun tili on todettu
#    toimivaksi (aiemmin tiedosto saattoi jaada levylle pysyvasti)
#  - tilin toimivuus varmistetaan kirjautumalla (CSRF-tunnisteella)
#  - salasanasaanto 12+ merkkia
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\_yhteinen.ps1"

$dir = Assert-HkSafeInstallDir (Get-HkInstallDir)
$port = Get-HkPort
$base = "http://127.0.0.1:$port"

Write-Host ""
Write-Host "=== HUOLTOKELLO - Ohjattu yllapitajatilin luonti ===" -ForegroundColor Cyan
Write-Host "Sovellus luo tilin seuraavassa kaynnistyksessa (Johto-rooli)."
Write-Host "Huom: samalla sahkopostilla olevaa tilia EI muuteta."
Write-Host ""

do {
    $email = (Read-Host "Sahkoposti (kirjautumistunnus)").Trim()
    if ($email -notmatch '^[^@\s]+@[^@\s]+\.[^@\s]+$') { Write-Host "   Anna kelvollinen sahkopostiosoite." -ForegroundColor Yellow; $email = '' }
} while (-not $email)
do { $name = (Read-Host "Koko nimi").Trim() } while (-not $name)
$title = (Read-Host "Tehtavanimike (Enter = 'Vastaava')").Trim()
if (-not $title) { $title = 'Vastaava' }

while ($true) {
    $pw1 = ConvertTo-PlainText (Read-Host "Salasana (vahintaan 12 merkkia)" -AsSecureString)
    $err = Test-HkPassword $pw1
    if ($err) { Write-Host " $err" -ForegroundColor Yellow; continue }
    $pw2 = ConvertTo-PlainText (Read-Host "Salasana uudelleen" -AsSecureString)
    if ($pw1 -ne $pw2) { Write-Host " Salasanat eivat tasmaa." -ForegroundColor Yellow; continue }
    break
}

$appData = Join-Path $dir 'AppData'
New-Item -ItemType Directory -Force -Path $appData | Out-Null
$jsonPath = Join-Path $appData 'first-admin.json'
# Tyhja tiedosto ensin, ACL rajataan, vasta sitten salasana sisaan
[IO.File]::WriteAllText($jsonPath, '', (New-Object Text.UTF8Encoding $false))
if (-not (Protect-HkSecretFile -Path $jsonPath)) { Remove-Item -LiteralPath $jsonPath -Force; throw 'Tiedoston oikeuksia ei voitu rajata - keskeytetaan.' }
$json = @{ Email = $email; FullName = $name; Title = $title; Password = $pw1 } | ConvertTo-Json
[IO.File]::WriteAllText($jsonPath, $json, (New-Object Text.UTF8Encoding $false))

try {
    Write-Host "Kaynnistetaan Huoltokello uudelleen..." -ForegroundColor Cyan
    if ((Get-HkProcess).Count -gt 0) { Stop-HkApp | Out-Null }
    Start-HkApp -NoBrowser
    if (-not (Wait-HkHealth -Port $port -Seconds 90)) {
        throw "Sovellus ei kaynnistynyt. Tarkista: $(Join-Path $appData 'app-err.log')"
    }
    # Varmistus kirjautumalla
    $session = New-Object Microsoft.PowerShell.Commands.WebRequestSession
    $lp = Invoke-WebRequest "$base/Account/Login" -WebSession $session -UseBasicParsing -TimeoutSec 10
    $token = [regex]::Match($lp.Content, 'name="__RequestVerificationToken"[^>]*value="([^"]+)"').Groups[1].Value
    $resp = Invoke-WebRequest "$base/Account/Login" -Method Post -WebSession $session -UseBasicParsing -TimeoutSec 15 `
        -Body @{ Email = $email; Password = $pw1; RememberMe = 'false'; __RequestVerificationToken = $token }
    $landed = $resp.BaseResponse.ResponseUri.AbsolutePath
    if ($landed -like '*Account/Login*') { throw 'Kirjautuminen uudella tililla ei onnistunut.' }
    Write-Host "VALMIS! Kirjaudu: $base  (tunnus $email, rooli Johto)" -ForegroundColor Green
}
finally {
    # Selkokielinen salasana ei saa jaada levylle missaan tilanteessa
    if (Test-Path -LiteralPath $jsonPath) { Remove-Item -LiteralPath $jsonPath -Force -ErrorAction SilentlyContinue }
    $pw1 = $null; $pw2 = $null; $json = $null
}
