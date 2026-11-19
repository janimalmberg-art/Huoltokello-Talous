# ============================================================
# HUEOLTOKELLO-TALOUS: topiikit + release + asennuspaketin liite
# (jatkoskripti Julkaise-Talous-GitHub.ps1:n peraan pushin jalkeen)
# ============================================================

$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

function Get-GitHubCredential {
    $qFile = Join-Path $env:TEMP ('hk-talous-rel-q-' + [IO.Path]::GetRandomFileName() + '.txt')
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
    if ([string]::IsNullOrEmpty(($pass | Out-String))) { throw 'Ei tallennettua GitHub-tunnusta.' }
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
        Uri = $Uri; Method = $Metodi; Headers = $Otsakkeet
        ContentType = $SisaltoTyyppi; UseBasicParsing = $true; ErrorAction = 'Stop'
    }
    if ($null -ne $Body) { $ap.Body = $Body }
    try { return Invoke-RestMethod @ap }
    catch {
        if ($_.Exception.Response) {
            try { $script:ApiStatus = [int]$_.Exception.Response.StatusCode } catch { $script:ApiStatus = -1 }
        } else { $script:ApiStatus = -1 }
        return $null
    }
}

$cred = Get-GitHubCredential
$User = $cred.Username
if ([string]::IsNullOrWhiteSpace($User)) { $User = 'janimalmberg-art' }
$Nimi = 'Huoltokello-Talous'
$hdr = @{
    Authorization = "token $($cred.Token)"
    'User-Agent'  = 'huoltokello-julkaisija'
    Accept        = 'application/vnd.github+json'
}

# --- topiikit ---
$topics = @('huoltokello','kiinteistonhuolto','talous','laskutus','kirjanpito','tyoaika','palkat','finvoice','netvisor','sqlite','python','windows','asennuspaketti')
$r = Invoke-GhApi "https://api.github.com/repos/$User/$Nimi/topics" -Metodi Put -Otsakkeet $hdr -Body (@{ names = $topics } | ConvertTo-Json)
Write-Host ("Topics: status " + $script:ApiStatus + " (204 = ok)")

# --- release ---
$versio = 'v1.1.0'
$rel = Invoke-GhApi "https://api.github.com/repos/$User/$Nimi/releases/tags/$versio" -Otsakkeet $hdr
if ($null -eq $rel) {
    $relBody = @{
        tag_name         = $versio
        target_commitish = 'main'
        name             = "Huoltokello-Talous $versio - asennuspaketti"
        body             = @"
Huoltokello-Talous 1.1.0 - asennuspaketti (Windows).

- Pura ja kaksoisnapsauta **Asenna-Talous.cmd** (vaatii Python 3.10+)
- Tyoaika, laskutus, kirjanpito, palkkakoosteet ja vinnit - selaimessa @ http://127.0.0.1:8090
- Uutta 1.1.0:ssa: Huoltokello-silta - talous lukee Huoltokellon kirjaukset suoraan tietokannasta
  (vain luku): asiakkaat, tyontekijat, tyolajit, kuittaukset ja tarvikkeet. Esikatselu:
  python -m huoltokello_talous huoltokello-synk --esikatselu

Muutoshistoria: repositorion **VERSIO.txt**.
"@
        draft      = $false
        prerelease = $false
    } | ConvertTo-Json
    $rel = Invoke-GhApi "https://api.github.com/repos/$User/$Nimi/releases" -Metodi Post -Otsakkeet $hdr -Body $relBody
    if ($null -eq $rel) { throw "Releasen luonti epaonnistui (status: $($script:ApiStatus))." }
    Write-Host "Release $versio luotu." -ForegroundColor Green
} else {
    Write-Host "Release $versio oli jo olemassa - paivitetaan liite." -ForegroundColor DarkGray
}

# --- liite ---
$repoRoot = Split-Path -Parent $PSScriptRoot
$zipPath = Join-Path $repoRoot 'Huoltokello-Talous-1.1.0.zip'
if (-not (Test-Path -LiteralPath $zipPath)) { throw 'Zipia ei loytynyt: ' + $zipPath }
$zipName = Split-Path -Leaf $zipPath
$assets = Invoke-GhApi $rel.assets_url -Otsakkeet $hdr
foreach ($a in @($assets)) {
    if ($a.name -eq $zipName) {
        $null = Invoke-GhApi $a.url -Metodi Delete -Otsakkeet $hdr
        Write-Host "Vanha liite poistettu: $zipName" -ForegroundColor DarkGray
    }
}
$upUrl = $rel.upload_url -replace '\{\?name.*?\}', "?name=$zipName"
Write-Host "Lisataan liite ($zipName)..."
$bytes = [System.IO.File]::ReadAllBytes((Resolve-Path -LiteralPath $zipPath))
$asset = Invoke-GhApi $upUrl -Metodi Post -Otsakkeet $hdr -Body $bytes -SisaltoTyyppi 'application/octet-stream'
if ($null -eq $asset) { throw "Liitteen lataus epaonnistui (status: $($script:ApiStatus))." }
Write-Host 'Liite valmis.' -ForegroundColor Green
Write-Host ''
Write-Host "  Release   : $($rel.html_url)"
Write-Host "  Download  : $($asset.browser_download_url)"
