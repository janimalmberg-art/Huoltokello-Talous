# Ohjattu kayttajatilien luonti sovelluksen kautta (vaatii Johto-tilin kirjautumisen).
# Kayttaa sovelluksen omaa Hallinta > Kayttajat -lomaketta, joten salasanat hasheutuvat
# oikein eika tietokantaan kosketa suoraan.
#
# KORJATTU VERSIO:
#  - kirjautumisen tarkistus oli virheellinen: Invoke-WebRequest seuraa uudelleenohjausta
#    kirjautumissivulle (HTTP 200), joten epaonnistunutkin kirjautuminen naytti onnistuneelta.
#    Nyt tarkistetaan lopullinen osoite (ResponseUri) eika pelkkaa poikkeusta.
#  - lomakkeen CSRF-tunnisteen puuttuminen keskeyttaa luonnin
#  - salasanasaanto 12+ merkkia; vahvistus kirjoittamalla kahdesti
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\_yhteinen.ps1"

$dir = Assert-HkSafeInstallDir (Get-HkInstallDir)
$port = Get-HkPort
$base = "http://127.0.0.1:$port"

if (-not (Test-HkHealth -Port $port)) {
    Write-Host "Kaynnistetaan Huoltokello..."
    Start-HkApp -NoBrowser
    if (-not (Wait-HkHealth -Port $port -Seconds 90)) { throw "Huoltokello ei kaynnistynyt - aja ensin Tyokalut\Kaynnista.ps1" }
}

function Get-HkFormToken([string]$Html) {
    $t = [regex]::Match($Html, 'name="__RequestVerificationToken"[^>]*value="([^"]+)"').Groups[1].Value
    if (-not $t) { throw "Lomakkeelta ei loytynyt tietoturvatunnistetta (CSRF)." }
    return $t
}

function Test-HkLanded([object]$Resp) {
    try { return ($Resp.BaseResponse.ResponseUri.AbsolutePath -notlike '*Account/Login*') } catch { return $false }
}

Write-Host ""
Write-Host "=== HUOLTOKELLO - Ohjattu kayttajien luonti ===" -ForegroundColor Cyan
$adminEmail = (Read-Host "Johto-tilin sahkoposti").Trim()
$adminPw = ConvertTo-PlainText (Read-Host "Salasana" -AsSecureString)

$session = New-Object Microsoft.PowerShell.Commands.WebRequestSession
$loginPage = Invoke-WebRequest "$base/Account/Login" -WebSession $session -UseBasicParsing -TimeoutSec 10
$resp = Invoke-WebRequest "$base/Account/Login" -Method Post -WebSession $session -UseBasicParsing -TimeoutSec 15 `
    -Body @{ Email = $adminEmail; Password = $adminPw; RememberMe = 'false'; __RequestVerificationToken = (Get-HkFormToken $loginPage.Content) }
$adminPw = $null
$users = Invoke-WebRequest "$base/Admin/Users" -WebSession $session -UseBasicParsing -TimeoutSec 10
if (-not (Test-HkLanded $resp) -or -not (Test-HkLanded $users)) {
    Write-Host "Kirjautuminen epaonnistui tai tili ei ole Johto-roolissa." -ForegroundColor Red
    exit 1
}
Write-Host "Kirjautuminen OK." -ForegroundColor Green

$created = 0
while ($true) {
    Write-Host ""
    Write-Host "--- Uusi kayttaja (Enter = lopeta) ---" -ForegroundColor Cyan
    $name = (Read-Host "Koko nimi").Trim()
    if (-not $name) { break }
    do {
        $email = (Read-Host "Sahkoposti (tunnus)").Trim()
        if ($email -notmatch '^[^@\s]+@[^@\s]+\.[^@\s]+$') { Write-Host "   Kelvoton sahkopostiosoite." -ForegroundColor Yellow; $email = '' }
    } while (-not $email)
    $title = (Read-Host "Tehtavanimike (valinnainen)").Trim()
    Write-Host "Rooli: [1] Tyontekija [2] Tyonjohto [3] Johto"
    $role = switch (Read-Host "Valitse (1-3, Enter = 1)") { '2' { 'Työnjohto' } '3' { 'Johto' } default { 'Työntekijä' } }
    if ($role -eq 'Johto') {
        if ((Read-Host "Johto-rooli antaa taydet oikeudet. Vahvista kirjoittamalla JOHTO") -ne 'JOHTO') { continue }
    }
    while ($true) {
        $pw = ConvertTo-PlainText (Read-Host "Alkusalasana (min 12 merkkia)" -AsSecureString)
        $err = Test-HkPassword $pw
        if ($err) { Write-Host " $err" -ForegroundColor Yellow; continue }
        if ((ConvertTo-PlainText (Read-Host "Salasana uudelleen" -AsSecureString)) -ne $pw) { Write-Host " Ei tasmaa." -ForegroundColor Yellow; continue }
        break
    }
    $form = Invoke-WebRequest "$base/Admin/UserForm" -WebSession $session -UseBasicParsing -TimeoutSec 10
    $null = Invoke-WebRequest "$base/Admin/UserForm" -Method Post -WebSession $session -UseBasicParsing -TimeoutSec 15 `
        -Body @{ IsNew = 'true'; FullName = $name; Email = $email; Title = $title; Role = $role; WorkerGroupId = ''
                 IsActive = 'true'; Password = $pw; __RequestVerificationToken = (Get-HkFormToken $form.Content) }
    $pw = $null
    $usersPage = Invoke-WebRequest "$base/Admin/Users" -WebSession $session -UseBasicParsing -TimeoutSec 10
    if ($usersPage.Content -match [regex]::Escape($email)) {
        Write-Host "KAYTTAJA LUOTU: $name ($email, $role). Kayttaja vaihtaa salasanan ensimmaisella kirjautumisella." -ForegroundColor Green
        $created++
    } else {
        Write-Host "Kayttajaa ei havaittu listassa - tarkista Hallinta > Kayttajat selaimessa." -ForegroundColor Yellow
    }
    if ((Read-Host "Lisataanko viela toinen kayttaja? (k/o)") -notmatch '^[kK]') { break }
}
Write-Host ""
if ($created -gt 0) { Write-Host "Valmis - luotiin $created kayttajaa." -ForegroundColor Green } else { Write-Host "Kayttajia ei luotu." }
