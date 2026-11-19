# Ottaa Huoltokellon kayttoon puhelimista/lahiverkosta:
# 1) vaihtaa kuunteluosoitteeksi 0.0.0.0 (kaikki verkkokortit)
# 2) lisaa Windows-palomuurisaannon portille (vaatii yllapito-oikeudet)
# 3) kaynnistaa sovelluksen uudelleen ja nayttaa laitteiden osoitteet.
# -Poista palauttaa vain paikallisen kayton (127.0.0.1) ja poistaa palomuurisaannon.
#
# KORJATTU VERSIO:
#  - palomuurisaanto vain yksityiseen/toimialueverkkoon ja paikalliseen aliverkkoon
#    (aiemmin profile=any avasi portin myos julkisissa WiFi-verkoissa koko verkolle)
#  - kayttajaa varoitetaan, etta yhteys on salaamaton HTTP (salasanat kulkevat selkokielisina
#    WiFi-verkossa); suositus HTTPS-kaanteisvalityspalvelin tai VPN
#  - Huoltokello.ps1 kirjoitetaan UTF-8 BOM -muodossa (PS 5.1 lukee aakkoset oikein)
param([switch]$Poista)
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\_yhteinen.ps1"

$dir = Assert-HkSafeInstallDir (Get-HkInstallDir)
$port = Get-HkPort
$launcher = Join-Path $dir 'Huoltokello.ps1'
if (-not (Test-Path -LiteralPath $launcher)) { throw "Kaynnistinta ei loytynyt: $launcher" }

if (-not $Poista) {
    Write-Host "HUOMIO: lahiverkkokaytossa yhteys on salaamaton HTTP. Kayta vain luotetussa verkossa" -ForegroundColor Yellow
    Write-Host "(oma toimiston WiFi, WPA2/WPA3). Ala kayta julkisessa tai vierasverkossa." -ForegroundColor Yellow
    $ok = Read-Host "Jatketaanko? (k/o)"
    if ($ok -notmatch '^[kK]') { exit 0 }
}

$wasRunning = (Get-HkProcess).Count -gt 0
if ($wasRunning) { Write-Host "Suljetaan Huoltokello..."; Stop-HkApp | Out-Null }

$newBind = if ($Poista) { '127.0.0.1' } else { '0.0.0.0' }
$text = Get-Content -LiteralPath $launcher -Raw
$text = [regex]::Replace($text, "\`$Bind\s*=\s*'[^']*'", "`$Bind = '$newBind'")
[IO.File]::WriteAllText($launcher, $text, (New-Object Text.UTF8Encoding $true))

if ($Poista) {
    try { Start-Process netsh -ArgumentList 'advfirewall firewall delete rule name=Huoltokello' -Verb RunAs -Wait }
    catch { Write-Host " Saanto jai paikalleen. Poista yllapitajana: netsh advfirewall firewall delete rule name=Huoltokello" -ForegroundColor Yellow }
} else {
    $rule = 'advfirewall firewall delete rule name=Huoltokello & netsh advfirewall firewall add rule name=Huoltokello ' +
            'dir=in action=allow protocol=TCP localport=' + $port + ' profile=private,domain remoteip=localsubnet'
    try {
        Start-Process cmd.exe -ArgumentList ('/c netsh ' + $rule) -Verb RunAs -Wait -WindowStyle Hidden
        Write-Host " Palomuurisaanto lisatty (TCP $port, vain yksityinen verkko ja oma aliverkko)." -ForegroundColor Green
        $prof = Get-NetConnectionProfile -ErrorAction SilentlyContinue | Where-Object { $_.NetworkCategory -eq 'Public' }
        if ($prof) {
            Write-Warning "Nykyinen verkko on luokiteltu JULKISEKSI ($($prof.Name)). Puhelimet eivat yhdisty ennen kuin"
            Write-Warning "verkko merkitaan yksityiseksi (Asetukset > Verkko > Ominaisuudet > Yksityinen) - vain luotetussa verkossa."
        }
    } catch {
        Write-Host " Palomuurin muutosta ei hyvaksytty (UAC peruttiin)." -ForegroundColor Yellow
    }
}

Write-Host "Kaynnistetaan Huoltokello uudelleen..."
Start-HkApp -NoBrowser
if (-not (Wait-HkHealth -Port $port -Seconds 90)) { Write-Warning "Sovellus ei vastannut."; exit 1 }

if ($Poista) {
    Write-Host "VALMIS: Huoltokello vastaa nyt vain tasta koneesta (http://127.0.0.1:$port)." -ForegroundColor Green
} else {
    Write-Host "VALMIS! Avaa puhelimella (sama WiFi) jokin osoitteista:" -ForegroundColor Green
    Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } |
        Select-Object -ExpandProperty IPAddress -Unique | ForEach-Object { Write-Host (" http://{0}:{1}" -f $_, $port) }
    Write-Host "Kumoa myohemmin: Tyokalut\PuhelinKaytto.ps1 -Poista"
}
