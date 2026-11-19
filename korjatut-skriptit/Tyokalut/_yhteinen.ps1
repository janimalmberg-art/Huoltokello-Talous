# ============================================================
# Huoltokello - yhteiset apurit Tyokalut-kansion skripteille.
# Dot-sourcataan: . "$PSScriptRoot\_yhteinen.ps1"
#
# KORJATTU VERSIO (tietoturvakatselmointi 16.11.2026):
#  - Get-HkInstallDir ei enaa palauta arvattua kansiota, jos siella ei ole
#    Huoltokellon ohjelmatiedostoa (esti vaaran kansion poiston).
#  - Assert-HkSafeInstallDir estaa toiminnot levyn juureen, profiiliin,
#    Windows-/Program Files -kansioihin ja tyopoytaan.
#  - Protect-HkSecretFile kayttaa kayttajan SID-tunnusta (ei nimea).
#  - Zip-kasittely .NET ZipFile -luokalla: ei 2 Gt:n rajaa (Compress-Archive
#    PS 5.1) ja purku tarkistaa polut (zip slip -esto).
#  - Salasanasaanto 12 merkkia (sovelluksen saanto on minimi; tyokalut vaativat enemman).
# ============================================================

$script:HkExeName = 'KiinteistoHuolto.Web.exe'

function Test-HkInstallDir {
    param([string]$Path)
    if ([string]::IsNullOrWhiteSpace($Path)) { return $false }
    return (Test-Path -LiteralPath (Join-Path $Path $script:HkExeName))
}

function Get-HkInstallDir {
    # 1) Kutsujan asettama ohitus - hyvaksytaan vain, jos kansiossa on Huoltokello
    if ($script:HkInstallDirOverride) {
        $o = $script:HkInstallDirOverride.TrimEnd('\')
        if (Test-HkInstallDir $o) { return $o }
        throw "Kansiossa '$o' ei ole Huoltokellon asennusta ($script:HkExeName puuttuu)."
    }
    # 2) Windowsin Sovellukset-listaus (HKCU)
    try {
        $reg = Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Huoltokello' -ErrorAction Stop
        if ($reg.InstallLocation -and (Test-HkInstallDir $reg.InstallLocation)) {
            return ($reg.InstallLocation.TrimEnd('\'))
        }
    } catch { }
    # 3) Taman skriptin ylempi kansio
    $parent = Split-Path -Parent $PSScriptRoot
    if (Test-HkInstallDir $parent) { return $parent }
    # 4) Oletusasennuskansio
    $default = Join-Path $env:LOCALAPPDATA 'Huoltokello'
    if (Test-HkInstallDir $default) { return $default }
    # 5) Ei arvata: palautetaan $null, jotta mikaan toiminto ei kohdistu vaaraan kansioon
    return $null
}

function Assert-HkSafeInstallDir {
    param([string]$Path)
    if ([string]::IsNullOrWhiteSpace($Path)) { throw 'Huoltokellon asennuskansiota ei loytynyt.' }
    $full = [IO.Path]::GetFullPath($Path).TrimEnd('\')
    $forbidden = @(
        [IO.Path]::GetPathRoot($full).TrimEnd('\'),
        $env:USERPROFILE, $env:LOCALAPPDATA, $env:APPDATA, $env:SystemRoot, $env:ProgramFiles,
        ${env:ProgramFiles(x86)}, $env:ProgramData, [Environment]::GetFolderPath('Desktop'),
        [Environment]::GetFolderPath('MyDocuments')
    ) | Where-Object { $_ } | ForEach-Object { $_.TrimEnd('\') }
    if ($forbidden -contains $full) { throw "Turvaesto: kansioon '$full' ei kohdisteta asennustoimintoja." }
    if (-not (Test-HkInstallDir $full)) { throw "Turvaesto: '$full' ei ole Huoltokellon asennuskansio." }
    return $full
}

function Get-HkPort {
    $dir = Get-HkInstallDir
    if ($dir) {
        $launcher = Join-Path $dir 'Huoltokello.ps1'
        if (Test-Path -LiteralPath $launcher) {
            $m = [regex]::Match((Get-Content -LiteralPath $launcher -Raw), "(?m)^\s*\`$Port\s*=\s*(\d+)")
            if ($m.Success) { return [int]$m.Groups[1].Value }
        }
    }
    return 8088
}

function Get-HkBind {
    $dir = Get-HkInstallDir
    if ($dir) {
        $launcher = Join-Path $dir 'Huoltokello.ps1'
        if (Test-Path -LiteralPath $launcher) {
            $m = [regex]::Match((Get-Content -LiteralPath $launcher -Raw), "(?m)^\s*\`$Bind\s*=\s*'([^']*)'")
            if ($m.Success) { return $m.Groups[1].Value }
        }
    }
    return '127.0.0.1'
}

function Test-HkHealth {
    param([int]$Port = (Get-HkPort))
    try {
        $r = Invoke-WebRequest "http://127.0.0.1:$Port/health" -UseBasicParsing -TimeoutSec 2
        return ($r.StatusCode -eq 200)
    } catch { return $false }
}

function Wait-HkHealth {
    param([int]$Port = (Get-HkPort), [int]$Seconds = 60)
    $tries = [Math]::Max(1, [int]($Seconds * 1000 / 700))
    for ($i = 0; $i -lt $tries; $i++) {
        if (Test-HkHealth -Port $Port) { return $true }
        Start-Sleep -Milliseconds 700
    }
    return $false
}

function Get-HkProcess {
    $dir = Get-HkInstallDir
    if (-not $dir) { return @() }
    return @(Get-Process -Name 'KiinteistoHuolto.Web' -ErrorAction SilentlyContinue |
             Where-Object { $_.Path -and ($_.Path -like "$dir\*") })
}

function Stop-HkApp {
    param([int]$WaitSeconds = 15)
    $procs = Get-HkProcess
    if ($procs.Count -eq 0) { return $false }
    # Siisti sammutus ensin (SQLite ehtii kirjoittaa WAL-tiedoston), sitten pakotus
    foreach ($p in $procs) { try { [void]$p.CloseMainWindow() } catch { } }
    $deadline = (Get-Date).AddSeconds([Math]::Min(5, $WaitSeconds))
    while ((Get-HkProcess).Count -gt 0 -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 300 }
    Get-HkProcess | Stop-Process -Force -ErrorAction SilentlyContinue
    $deadline = (Get-Date).AddSeconds($WaitSeconds)
    while ((Get-HkProcess).Count -gt 0 -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 400 }
    if ((Get-HkProcess).Count -gt 0) { throw 'Huoltokello-prosessi ei sulkeutunut - toimintoa ei jatketa.' }
    Start-Sleep -Milliseconds 600
    return $true
}

function Start-HkApp {
    param([switch]$NoBrowser)
    $dir = Get-HkInstallDir
    if (-not $dir) { throw 'Huoltokellon asennusta ei loytynyt.' }
    $launcher = Join-Path $dir 'Huoltokello.ps1'
    if (-not (Test-Path -LiteralPath $launcher)) { throw "Kaynnistinta ei loytynyt: $launcher" }
    & $launcher -NoBrowser:$NoBrowser
}

function New-HkShortcut {
    param([string]$Path, [string]$TargetFile, [string]$Arguments = '', [string]$WorkingDir = '',
          [string]$IconPath = '', [string]$Description = '')
    $wsh = New-Object -ComObject WScript.Shell
    $sc = $wsh.CreateShortcut($Path)
    $sc.TargetPath = $TargetFile
    if ($Arguments) { $sc.Arguments = $Arguments }
    if ($WorkingDir) { $sc.WorkingDirectory = $WorkingDir }
    if ($IconPath) { $sc.IconLocation = $IconPath }
    if ($Description) { $sc.Description = $Description }
    $sc.Save()
}

function Test-HkPassword {
    param([string]$Password)
    if ([string]::IsNullOrWhiteSpace($Password)) { return 'Salasana vaaditaan.' }
    if ($Password.Length -lt 12) { return 'Salasanan on oltava vahintaan 12 merkkia pitka.' }
    if ($Password -notmatch '[A-ZÅÄÖÜ]') { return 'Salasanassa on oltava vahintaan yksi iso kirjain.' }
    if ($Password -notmatch '[a-zåäöü]') { return 'Salasanassa on oltava vahintaan yksi pieni kirjain.' }
    if ($Password -notmatch '\d') { return 'Salasanassa on oltava vahintaan yksi numero.' }
    if ($Password -match '(?i)salasana|password|huoltokello|qwerty|123456') { return 'Salasana on liian arvattava.' }
    return $null
}

function ConvertTo-PlainText {
    param([System.Security.SecureString]$SecurePassword)
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($SecurePassword)
    try { [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
}

function New-HkVapidKeys {
    $curve = [System.Security.Cryptography.ECCurve]::CreateFromValue('1.2.840.10045.3.1.7')
    $ec = [System.Security.Cryptography.ECDsa]::Create($curve)
    if (-not $ec) { $ec = New-Object System.Security.Cryptography.ECDsaCng -ArgumentList $curve }
    try {
        $p = $ec.ExportParameters($true)
        if (-not $p.D -or $p.D.Length -eq 0) { throw 'Yksityista avainta ei saatu vietya.' }
        $b64u = { param([byte[]]$b) [Convert]::ToBase64String($b).TrimEnd('=').Replace('+', '-').Replace('/', '_') }
        $pub = New-Object byte[] (1 + $p.Q.X.Length + $p.Q.Y.Length)
        $pub[0] = 4
        [Array]::Copy($p.Q.X, 0, $pub, 1, $p.Q.X.Length)
        [Array]::Copy($p.Q.Y, 0, $pub, 1 + $p.Q.X.Length, $p.Q.Y.Length)
        return @{ PublicKey = (& $b64u $pub); PrivateKey = (& $b64u $p.D) }
    } finally { $ec.Dispose() }
}

function Protect-HkSecretFile {
    # Salaisuustiedoston ACL: vain nykyinen kayttaja (SID) ja SYSTEM; periytyvat oikeudet pois.
    param([string]$Path, [string[]]$ExtraSids = @())
    try {
        $sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
        # Kansiolle periytyvat oikeudet (OI)(CI), tiedostolle suorat
        $inh = if (Test-Path -LiteralPath $Path -PathType Container) { '(OI)(CI)' } else { '' }
        $icArgs = @($Path, '/inheritance:r', '/grant:r', "*$($sid):$($inh)F", '/grant:r', "*S-1-5-18:$($inh)F")
        foreach ($x in $ExtraSids) { $icArgs += @('/grant:r', "*$($x):$($inh)R") }
        $null = & icacls.exe @icArgs
        return ($LASTEXITCODE -eq 0)
    } catch { return $false }
}

# ---------- Turvallinen zip-kasittely (.NET, ei 2 Gt:n rajaa, zip slip -esto) ----------

function Test-HkZipEntries {
    # Palauttaa virheilmoituksen tai $null. Tarkistaa, ettei yksikaan polku osoita kohdekansion ulkopuolelle.
    param([string]$ZipPath, [string[]]$Forbidden = @())
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [System.IO.Compression.ZipFile]::OpenRead($ZipPath)
    try {
        foreach ($en in $zip.Entries) {
            $n = $en.FullName.Replace('\', '/')
            if ($n.StartsWith('/') -or $n -match '(^|/)\.\.(/|$)' -or $n -match '^[A-Za-z]:') {
                return "Paketissa on vaarallinen polku: $($en.FullName)"
            }
            foreach ($f in $Forbidden) {
                if ($n -like $f) { return "Paketissa on kielletty tiedosto: $($en.FullName)" }
            }
        }
    } finally { $zip.Dispose() }
    return $null
}

function Expand-HkZipSafe {
    param([string]$ZipPath, [string]$Destination, [string[]]$SkipPatterns = @())
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $destFull = [IO.Path]::GetFullPath($Destination).TrimEnd('\') + '\'
    $zip = [System.IO.Compression.ZipFile]::OpenRead($ZipPath)
    $count = 0
    try {
        foreach ($en in $zip.Entries) {
            $rel = $en.FullName.Replace('/', '\')
            $skip = $false
            foreach ($s in $SkipPatterns) { if ($en.FullName.Replace('\', '/') -like $s) { $skip = $true } }
            if ($skip) { continue }
            $target = [IO.Path]::GetFullPath((Join-Path $destFull $rel))
            if (-not $target.StartsWith($destFull, [StringComparison]::OrdinalIgnoreCase)) {
                throw "Zip slip -esto: $($en.FullName)"
            }
            if ($en.FullName.EndsWith('/') -or $en.FullName.EndsWith('\')) {
                [void][IO.Directory]::CreateDirectory($target)
                continue
            }
            [void][IO.Directory]::CreateDirectory((Split-Path -Parent $target))
            [System.IO.Compression.ZipFileExtensions]::ExtractToFile($en, $target, $true)
            $count++
        }
    } finally { $zip.Dispose() }
    return $count
}

function New-HkZipFromFolder {
    # Pakkaa kansion (sisaltaen kansion nimen) - ei Compress-Archiven 2 Gt:n rajaa.
    param([string]$Folder, [string]$ZipPath)
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    if (Test-Path -LiteralPath $ZipPath) { Remove-Item -LiteralPath $ZipPath -Force }
    [System.IO.Compression.ZipFile]::CreateFromDirectory($Folder, $ZipPath,
        [System.IO.Compression.CompressionLevel]::Optimal, $true)
    # Varmistus: arkisto aukeaa ja sisaltaa tiedostoja
    $z = [System.IO.Compression.ZipFile]::OpenRead($ZipPath)
    try { if ($z.Entries.Count -eq 0) { throw 'Varmuuskopio on tyhja.' } } finally { $z.Dispose() }
    return $ZipPath
}

function Get-HkBackupDir {
    $d = Join-Path $env:USERPROFILE 'Huoltokello-Varmuuskopiot'
    if (-not (Test-Path -LiteralPath $d)) {
        New-Item -ItemType Directory -Force -Path $d | Out-Null
        # Varmuuskopiot sisaltavat henkilotietoja ja avaimia: vain omistaja ja SYSTEM
        $null = Protect-HkSecretFile -Path $d
    }
    return $d
}
