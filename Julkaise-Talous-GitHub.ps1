# ============================================================
# HUEOLTOKELLO-TALOUS -> GITHUB (automaattinen julkaisu)
# ============================================================
# Tekee yhdella ajolla (sama tapa kuin Julkaise-GitHub.ps1):
#   1. Tarkistaa gitin ja Git Credential Managerin
#   2. Hakee GitHub-tunnistautumisen koneen tallennetusta
#      tunnuksesta (tokenia EI tulosteta eika tallenneta)
#   3. Luo repositorion jos sita ei ole (oletus: julkinen)
#   4. git init + ensimmainen commit
#   5. Tyontaa main-haaran GitHubiin
#   6. Lisaa aihekoodit (topics)
#   7. Luo releasen v-merkilla VERSIO.txt:sta ja vie
#      asennuspaketin (Huoltokello-Talous-1.1.0.zip) liitteena
#
# KAYTTO: powershell -ExecutionPolicy Bypass -File .\Julkaise-Talous-GitHub.ps1
# ============================================================

[CmdletBinding()]
param(
    [string] $Nimi     = 'Huoltokello-Talous',
    [ValidateSet('julkinen','yksityinen')]
    [string] $Nakyvyys = 'julkinen',
    [string] $Kuvaus   = 'Huoltokello-Talous - pienyrityksen talous- ja laskutusmoduuli Huoltokellon rinnalle: tyoaika, laskutus, kirjanpito, palkkakoosteet ja Finvoice/Netvisor-vinnit. Sisaltaa Huoltokello-sillan (vain luku).',
    [string] $Haara    = 'main'
)

$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

# ------------------------------------------------------------
# APUFUNKTIOT
# ------------------------------------------------------------

function Get-GitExe {
    $g = Get-Command git -ErrorAction SilentlyContinue
    if (-not $g) {
        throw 'Git ei loytynyt polusta. Asenna se: winget install --id Git.Git -e'
    }
    return $g.Source
}

function Invoke-Git {
    # Ajaa git-komennon siten etta gitin viestit stderrin kautta (varoitukset,
    # edistymistiedot) eivat kaada skriptia; oikeat epaonnistukset havaitaan
    # exit-koodista.
    param([Parameter(Mandatory)][string[]]$GitArgs)
    $out = $null
    try { $out = (& git @GitArgs 2>&1 | Out-String) } catch { }
    if ($LASTEXITCODE -ne 0) {
        throw ('git ' + ($GitArgs -join ' ') + ' epaonnistui (koodi ' + $LASTEXITCODE + ')')
    }
    return $out
}

function Get-GitHubCredential {
    # Koneen tallennettu GitHub-tunnus (Git Credential Manager).
    # Token ei tulostu eika tallennu mihinkaan tiedostoon.
    $qFile = Join-Path $env:TEMP ('hk-talous-cred-q-' + [IO.Path]::GetRandomFileName() + '.txt')
    $pass = $null; $user = $null
    try {
        $q = "protocol=https" + [Environment]::NewLine +
             "host=github.com" + [Environment]::NewLine +
             [Environment]::NewLine
        Set-Content -LiteralPath $qFile -Value $q -Encoding ASCII
        $rows = (cmd /c "git credential fill < ""$qFile""" 2>$null) | ForEach-Object { ($_ -replace "\r", '') }
        $pass = $rows | Where-Object { $_ -like 'password=*' } | ForEach-Object { $_.Substring(9) } | Select-Object -First 1
        $user = $rows | Where-Object { $_ -like 'username=*' } | ForEach-Object { $_.Substring(9) } | Select-Object -First 1
    } finally {
        Remove-Item -LiteralPath $qFile -Force -ErrorAction SilentlyContinue
    }
    if ([string]::IsNullOrEmpty(($pass | Out-String))) {
        throw 'Ei tallennettua GitHub-tunnusta. Kirjaudu: git credential-manager github login'
    }
    [pscustomobject]@{ Token = $pass; Username = $user }
}

$script:ApiStatus = $null
function Invoke-GhApi {
    param(
        [Parameter(Mandatory)] [string]$Uri,
        [string]$Metodi = 'Get',
        [Parameter(Mandatory)] [hashtable]$Otsakkeet,
        $Body = $null,
        [string]$SisaltoTyyppi = 'application/json'
    )
    $script:ApiStatus = $null
    $ap = @{
        Uri             = $Uri
        Method          = $Metodi
        Headers         = $Otsakkeet
        ContentType     = $SisaltoTyyppi
        UseBasicParsing = $true
        ErrorAction     = 'Stop'
    }
    if ($null -ne $Body) { $ap.Body = $Body }
    try {
        return Invoke-RestMethod @ap
    } catch {
        if ($_.Exception.Response) {
            try { $script:ApiStatus = [int]$_.Exception.Response.StatusCode } catch { $script:ApiStatus = -1 }
        } else {
            $script:ApiStatus = -1
        }
        return $null
    }
}

# ------------------------------------------------------------
# KANSIO JA GIT-ALUSTUS
# ------------------------------------------------------------

Set-Location -LiteralPath $PSScriptRoot
Write-Host ''
Write-Host '=== HUOLTOKELLO-TALOUS -> GITHUB ===' -ForegroundColor Cyan
Write-Host "Kansio:      $PSScriptRoot" -ForegroundColor Gray
Write-Host "Repositorio: $Nimi  ($Nakyvyys)" -ForegroundColor Gray
Write-Host ''

$gitExe = Get-GitExe
Write-Host "Git loyty: $gitExe" -ForegroundColor DarkGray

# Gitin viestit voivat tulla stderrin kautta (esim. LF/CRLF-varoitus) eika ne ole
# virheita - salli jatkaminen ja tarkista oikeat epaonnistukset $LASTEXITCODE:sta.
$ErrorActionPreference = 'Continue'

try {
    $ch = git config --get credential.helper 2>$null
} catch { $ch = $null }
if ([string]::IsNullOrWhiteSpace(($ch | Out-String))) {
    $null = Invoke-Git @('config','--global','credential.helper','manager')
    Write-Host 'Asetettiin credential.helper = manager' -ForegroundColor DarkGray
}

# ------------------------------------------------------------
# GITHUB-TUNNISTAUTUMINEN
# ------------------------------------------------------------

Write-Host 'Haetaan GitHub-tunnistautuminen koneen tallennetuista tunnuksista...'
try {
    $cred = Get-GitHubCredential
} catch {
    Write-Host ''
    Write-Host ('VIRHE GitHub-tunnistautumisessa: ' + $_.Exception.Message) -ForegroundColor Red
    Write-Host 'Kirjaudu ensin: git credential-manager github login   - aja sitten skripti uudelleen.' -ForegroundColor Yellow
    exit 2
}

$hdr = @{
    Authorization = "token $($cred.Token)"
    'User-Agent'  = 'huoltokello-julkaisija'
    Accept        = 'application/vnd.github+json'
}

$ghMe = Invoke-GhApi 'https://api.github.com/user' -Otsakkeet $hdr
if ($null -eq $ghMe) {
    throw "GitHub-tunnistautuminen ei toiminut (status: $script:ApiStatus). Token tarvitsee 'repo'-oikeuden."
}
$User     = $ghMe.login
$gitEmail = "$($ghMe.id)+$User@users.noreply.github.com"
Write-Host "GitHub-tili: $User" -ForegroundColor Green

# ------------------------------------------------------------
# REPOSITORION LUONTI
# ------------------------------------------------------------

$repo = Invoke-GhApi "https://api.github.com/repos/$User/$Nimi" -Otsakkeet $hdr
if ($null -eq $repo) {
    if ($script:ApiStatus -ne 404) {
        throw "Repositorion tarkistus epaonnistui (status: $script:ApiStatus)."
    }
    $priv = ($Nakyvyys -eq 'yksityinen')
    $body = @{
        name        = $Nimi
        description = $Kuvaus
        private     = $priv
        auto_init   = $false
        has_issues  = $true
        has_wiki    = $false
    } | ConvertTo-Json
    $created = Invoke-GhApi 'https://api.github.com/user/repos' -Metodi Post -Otsakkeet $hdr -Body $body
    if ($null -eq $created -and $script:ApiStatus -ne 422) {
        throw "Repositorion luonti epaonnistui (status: $script:ApiStatus). Token tarvitsee 'repo'-oikeuden."
    }
    Write-Host "Repositorio luotu: https://github.com/$User/$Nimi" -ForegroundColor Green
} else {
    Write-Host "Repositorio on jo olemassa: https://github.com/$User/$Nimi" -ForegroundColor DarkGray
}

# ------------------------------------------------------------
# ENSIMMAINEN COMMIT + PUSH
# ------------------------------------------------------------

if (-not (Test-Path (Join-Path $PSScriptRoot '.git'))) {
    $null = Invoke-Git @('init','-b',$Haara)
    $null = Invoke-Git @('config','user.name',$User)
    $null = Invoke-Git @('config','user.email',$gitEmail)
    $null = Invoke-Git @('config','core.autocrlf','false')
    Write-Host "Git-repositorio alustettiin (haara: $Haara)."
} else {
    $null = Invoke-Git @('config','core.autocrlf','false')
}

$null = Invoke-Git @('add','--all')
$staged = (git status --porcelain | Out-String)
if ([string]::IsNullOrWhiteSpace($staged)) {
    Write-Host 'Ei uusia muutoksia commitoitavana.' -ForegroundColor DarkGray
} else {
    $null = Invoke-Git @('commit','-m','Ensijulkaisu: Huoltokello-Talous 1.1.0 (Huoltokello-silta, asennuspaketti mukana)')
    Write-Host 'Commit OK.'
}

$remoteUrl = "https://github.com/$User/$Nimi.git"
$cur = $null
try { $cur = git remote get-url origin 2>$null } catch { }
if ([string]::IsNullOrWhiteSpace(($cur | Out-String))) {
    $null = Invoke-Git @('remote','add','origin',$remoteUrl)
} else {
    $null = Invoke-Git @('remote','set-url','origin',$remoteUrl)
}

Write-Host "Tyonnetaan haara $Haara GitHubiin (hetkeen menessa)..."
try {
    $null = Invoke-Git @('push','-u','origin',$Haara)
} catch {
    throw ("Push epaonnistui. Tarkista tunnistautuminen: git credential-manager github login - Syy: " + $_.Exception.Message)
}
Write-Host 'Push OK.' -ForegroundColor Green

# ------------------------------------------------------------
# AIHEEKOODIT (TOPICS)
# ------------------------------------------------------------

$topics = @('huoltokello','kiinteistonhuolto','talous','laskutus','kirjanpito','tyoaika','palkat','finvoice','netvisor','sqlite','python','windows','asennuspaketti')
$null = Invoke-GhApi "https://api.github.com/repos/$User/$Nimi/topics" -Metodi Put -Otsakkeet $hdr -Body (@{ names = $topics } | ConvertTo-Json)
Write-Host 'Aihekoodit (topics) lisatty.' -ForegroundColor DarkGray

# ------------------------------------------------------------
# RELEASE: ASENNUSPAKETTI (ZIP) LIITTEENA
# ------------------------------------------------------------

$versio = $null
try {
    $vlhead = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'VERSIO.txt') -TotalCount 15 -ErrorAction Stop
    foreach ($l in $vlhead) {
        if ($l -match '^(v\d+\.\d+\.\d+)') { $versio = $Matches[1]; break }
    }
} catch { }
if ([string]::IsNullOrWhiteSpace($versio)) { $versio = 'v1.1.0' }
Write-Host "Luettiin versionumero: $versio"

$rel = Invoke-GhApi "https://api.github.com/repos/$User/$Nimi/releases/tags/$versio" -Otsakkeet $hdr
if ($null -eq $rel) {
    $relBody = @{
        tag_name         = $versio
        target_commitish = $Haara
        name             = "Huoltokello-Talous $versio - asennuspaketti"
        body             = @"
Huoltokello-Talous $versio - asennuspaketti (Windows).

- Pura ja kaksoisnapsauta **Asenna-Talous.cmd** (vaatii Python 3.10+)
- Tyoaika, laskutus, kirjanpito, palkkakoosteet ja vinnit - selaimessa @ http://127.0.0.1:8090
- Uutta $versio:ssa: Huoltokello-silta - talous lukee Huoltokellon kirjaukset suoraan tietokannasta
  (vain luku): asiakkaat, tyontekijat, tyolajit, kuittaukset ja tarvikkeet. Esikatselu:
  python -m huoltokello_talous huoltokello-synk --esikatselu

Muutoshistoria: repositorion **VERSIO.txt**.
"@
        draft      = $false
        prerelease = $false
    } | ConvertTo-Json
    $rel = Invoke-GhApi "https://api.github.com/repos/$User/$Nimi/releases" -Metodi Post -Otsakkeet $hdr -Body $relBody
    if ($null -ne $rel) { Write-Host "Release $versio luotu." -ForegroundColor Green }
} else {
    Write-Host "Release $versio on jo olemassa - paivitetaan liite." -ForegroundColor DarkGray
}

$asset = $null
if ($null -ne $rel) {
    $zipName = "Huoltokello-Talous-$($versio.TrimStart('v')).zip"
    $zipPath = Join-Path $PSScriptRoot $zipName
    if (-not (Test-Path -LiteralPath $zipPath)) {
        $zipPath = Get-ChildItem -Path $PSScriptRoot -Filter 'Huoltokello-Talous-*.zip' -File |
            Select-Object -First 1 -ExpandProperty FullName
    }
    if ($null -eq $zipPath -or -not (Test-Path -LiteralPath $zipPath)) {
        Write-Warning 'Asennuspakettia ei loytynyt repositoriokansiosta - liite jai tekematta.'
    } else {
        $zipName = Split-Path -Leaf $zipPath
        $assets = Invoke-GhApi $rel.assets_url -Otsakkeet $hdr
        foreach ($a in @($assets)) {
            if ($a.name -eq $zipName) {
                $null = Invoke-GhApi $a.url -Metodi Delete -Otsakkeet $hdr
                Write-Host "Vanha liite poistettu: $zipName" -ForegroundColor DarkGray
            }
        }
        $upUrl = $rel.upload_url -replace '\{\?name.*?\}', "?name=$zipName"
        Write-Host "Lisataan asennuspaketti liitteeksi ($zipName)..."
        $bytes = [System.IO.File]::ReadAllBytes((Resolve-Path -LiteralPath $zipPath))
        $asset = Invoke-GhApi $upUrl -Metodi Post -Otsakkeet $hdr -Body $bytes -SisaltoTyyppi 'application/octet-stream'
        if ($null -eq $asset) {
            Write-Warning "Liitteen lataus epaonnistui (status: $script:ApiStatus). Release on GitHubissa, mutta ilman pakettia."
        } else {
            Write-Host 'Asennuspaketti on nyt liitteena.' -ForegroundColor Green
        }
    }
}

# ------------------------------------------------------------
# VALMIS
# ------------------------------------------------------------

Write-Host ''
Write-Host '==============================' -ForegroundColor Green
Write-Host ' JULKAISU VALMIS             ' -ForegroundColor Green
Write-Host '==============================' -ForegroundColor Green
Write-Host ''
Write-Host "  Repositorio : https://github.com/$User/$Nimi"
if ($asset) {
    Write-Host "  Paketti     : $($asset.name)  (Release-liite)"
    Write-Host "  Download    : $($asset.browser_download_url)"
} elseif ($rel) {
    Write-Host "  Release     : $($rel.html_url)"
}
Write-Host ''
Write-Host 'Myohemmat muutokset: tavallisilla git-komennoilla:'
Write-Host ('    git add --all ; git commit -m "kuvaus" ; git push origin ' + $Haara)
Write-Host ''
