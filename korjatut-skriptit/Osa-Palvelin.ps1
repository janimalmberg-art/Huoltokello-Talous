# ============================================================
# HUOLTOKELLO MULTIASENNUS - OSA-PALVELIN (korjattu versio)
# Asentaa Huoltokellon yrityksen omaan Windows-palvelimeen
# kayttaen WinRM:aa (PowerShell-remoting) ja SMB-tiedostojakoa.
#
# KORJAUKSET (katselmointi 16.11.2026):
#  K1 Palvelinasennus ei toiminut lainkaan: velho ja hiljainen asennus valittivat
#     salasanan avaimella 'ServerPass', mutta Get-HkServerCredential luki vain
#     'ServerPassword'/'ServerPasswordSecure' -> ConvertTo-SecureString tyhjalla
#     merkkijonolla kaatui. Nyt kaikki kolme avainta hyvaksytaan.
#  K2 Sovellus ajettiin SYSTEM-tililla (RunLevel Highest): web-sovelluksen haavoittuvuus
#     olisi antanut taydet oikeudet palvelimeen. Nyt ajo LOCAL SERVICE -tililla, jolla
#     on muokkausoikeus vain AppData-kansioon ja lukuoikeus ohjelmaan.
#  K3 VAPID-avaimet luettiin paivityksessa vain appsettings.jsonista; v2.4.1:sta alkaen
#     ne ovat appsettings.Production.json:ssa -> paivitys loi uudet avaimet ja kaikki
#     push-tilaukset katkesivat. Nyt luetaan ensin Production-tiedostosta.
#  K4 Palomuurisaanto avasi portin kaikkiin profiileihin ja osoitteisiin: nyt vain
#     toimialue/yksityinen ja paikallinen aliverkko (muutettavissa -SallitutOsoitteet).
#  K5 TrustedHosts-korjausskripti jai %TEMP%-kansioon: nyt poistetaan.
#  K6 Siirtotiedostolla oli kiintea nimi (_hk-stage.zip) eika sita poistettu virhetilanteessa,
#     eika siirron eheytta tarkistettu. Nyt satunnainen nimi, SHA-256-tarkistus palvelimella
#     ja poisto aina (finally).
#  K7 first-admin.json (selkokielinen salasana) rajataan ACL:lla ja poistetaan, kun
#     sovellus on kaynnistynyt.
# ============================================================

function Get-HkServerCredential {
    param([hashtable]$s)
    if ($s.ServerPasswordSecure) {
        $pass = $s.ServerPasswordSecure
    } else {
        $plain = [string]$s.ServerPassword
        if (-not $plain) { $plain = [string]$s.ServerPass }          # K1
        if (-not $plain) { throw 'Palvelimen salasana puuttuu.' }
        $pass = ConvertTo-SecureString -String $plain -AsPlainText -Force
    }
    return New-Object System.Management.Automation.PSCredential ([string]$s.ServerUser), $pass
}

function Invoke-HkServerFixTrustedHosts {
    param([string]$ComputerName, [string]$ServerUser, $OnStep)
    if ($OnStep) { & $OnStep 'server-yhteys' 'info' 'Lisataan palvelin taman koneen TrustedHosts-luetteloon (Windows kysyy yllapitoluvan)...' }
    if ($ComputerName -notmatch '^[A-Za-z0-9.\-]+$') { throw "Virheellinen palvelimen nimi: $ComputerName" }
    $fix = Join-Path $env:TEMP ('huoltokello-th-fix-' + [guid]::NewGuid().ToString('N').Substring(0, 8) + '.ps1')
    $svc = 'param($h) $cur = (Get-Item WSMan:\localhost\Client\TrustedHosts).Value; $list = @(); if ($cur -ne ''*'') { $list = @($cur -split "," | Where-Object { $_.Trim() }) }; $h2 = $h.Trim().ToLower(); if (($list | ForEach-Object { $_.Trim().ToLower() }) -notcontains $h2) { $list += $h; Set-Item WSMan:\localhost\Client\TrustedHosts -Value ($list -join ",") -Force }'
    try {
        [IO.File]::WriteAllText($fix, $svc, (New-Object Text.UTF8Encoding $true))
        $pr = Start-Process powershell.exe -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $fix, $ComputerName) -Verb RunAs -Wait -PassThru
        if ($pr.ExitCode -ne 0) { throw 'TrustedHosts-korjaus peruttiin tai epaonnistui.' }
    } finally {
        Remove-Item -LiteralPath $fix -Force -ErrorAction SilentlyContinue     # K5
    }
}

function Invoke-HkServerCommand {
    param([string]$ComputerName, $Credential, [scriptblock]$Script, $Arguments = @())
    try {
        return Invoke-Command -ComputerName $ComputerName -Credential $Credential -ScriptBlock $Script -ArgumentList $Arguments -ErrorAction Stop
    } catch {
        $msg = $_.Exception.Message
        if ($msg -match 'cannot be resolved|ei loytynyt|getaddrinfo|The RPC server is unavailable|connection.*refused|Connection attempted') {
            throw ("Yhteytta palvelimeen '$ComputerName' ei saatu. Tarkista nimi, verkko ja WinRM (5985/5986).`nYksityiskohta: $msg")
        }
        if ($msg -match 'access is denied|Access denied|logon failure|kielletty|authentication') {
            throw ("Tunnistautuminen palvelimelle epaonnistui. Tarkista tunnus, salasana ja yllapito-oikeudet.`nYksityiskohta: $msg")
        }
        if ($msg -match 'winrm|wsman|cannot be found.*session|remoting') {
            throw ("PowerShell-remoting (WinRM) ei ole kaytossa palvelimella '$ComputerName'. Aja palvelimella yllapitajana: Enable-PSRemoting -Force`nYksityiskohta: $msg")
        }
        throw ("Palvelinoperaatio epaonnistui: $msg")
    }
}

function Invoke-HkServerInstall {
    param([hashtable]$s, [scriptblock]$OnStep)

    $hostName = $s.ServerHost.Trim()
    if ($hostName -notmatch '^[A-Za-z0-9.\-]+$') { throw "Virheellinen palvelimen nimi: $hostName" }
    $path = $s.ServerPath.Trim().TrimEnd('\')
    if ($path -notmatch '^[A-Za-z]:\\[^"<>|*?]+$' -or $path.Length -lt 4) { throw "Virheellinen asennuskansio: $path" }
    $port = [int]$s.ServerPort
    $url = ('http://{0}:{1}' -f $hostName, $port)
    $cred = Get-HkServerCredential -s $s
    $allowed = if ($s.SallitutOsoitteet) { [string]$s.SallitutOsoitteet } else { 'localsubnet' }

    # --- 1) yhteys ja olemassa oleva asennus ---
    & $OnStep 'server-yhteys' 'start' ''
    if ($s.ServerNotDomain) { Invoke-HkServerFixTrustedHosts -ComputerName $hostName -ServerUser $s.ServerUser -OnStep $OnStep }
    $remote = Invoke-HkServerCommand -ComputerName $hostName -Credential $cred -Script {
        param($p)
        $existsExe = Test-Path (Join-Path $p 'KiinteistoHuolto.Web.exe')
        $isUpdate = Test-Path (Join-Path $p 'AppData\huoltokello.db')
        $vapid = $null
        if ($isUpdate) {
            foreach ($f in @('appsettings.Production.json', 'appsettings.json')) {      # K3
                try {
                    $cfg = Get-Content (Join-Path $p $f) -Raw -ErrorAction Stop | ConvertFrom-Json
                    if ($cfg.Vapid -and $cfg.Vapid.PublicKey -and $cfg.Vapid.PrivateKey) { $vapid = $cfg.Vapid; break }
                } catch { }
            }
        }
        @{ Name = $env:COMPUTERNAME; Exe = $existsExe; Update = $isUpdate; Vapid = $vapid }
    } -Arguments @($path)
    $isUpdate = [bool]$remote.Update
    & $OnStep 'server-yhteys' 'ok' ('Yhteys ok: {0}{1}' -f $remote.Name, $(if ($isUpdate) { ' (paivitys)' } else { ' (uusi asennus)' }))

    # --- 2) paketin valmistelu ---
    $keys = $null
    if ($isUpdate -and $remote.Vapid) {
        $keys = @{ Subject = [string]$remote.Vapid.Subject; PublicKey = [string]$remote.Vapid.PublicKey; PrivateKey = [string]$remote.Vapid.PrivateKey }
    } elseif (-not $isUpdate) {
        $keys = New-HkVapidKeys
    } else {
        & $OnStep 'pakkaus' 'info' 'Aiempia ilmoitusavaimia ei loytynyt - luodaan uudet (selaimen ilmoitusluvat on annettava uudelleen).'
        $keys = New-HkVapidKeys
    }
    $pkg = New-HkAppPackage -Mode 'palvelin' -AdminName $s.AdminName -AdminEmail $s.AdminEmail -AdminTitle $s.AdminTitle `
        -AdminPassword $s.AdminPassword -IsUpdate:$isUpdate -VapidKeys $keys -ServicePort $port -OnStep $OnStep

    # --- 3) siirto (SMB) ja purku (WinRM) ---
    & $OnStep 'server-tiedostot' 'start' ''
    $null = Invoke-HkServerCommand -ComputerName $hostName -Credential $cred -Script {
        param($p)
        Get-Process -Name 'KiinteistoHuolto.Web' -ErrorAction SilentlyContinue |
            Where-Object { $_.Path -and ($_.Path -like ($p + '\*')) } | Stop-Process -Force -ErrorAction SilentlyContinue
        try { Stop-ScheduledTask -TaskName 'Huoltokello-Palvelu' -ErrorAction SilentlyContinue } catch { }
        New-Item -ItemType Directory -Force -Path $p | Out-Null
    } -Arguments @($path)
    $drive = $path.Substring(0, 1)
    $stageName = '_hk-stage-' + [guid]::NewGuid().ToString('N').Substring(0, 8) + '.zip'
    $share = ("\\{0}\{1}`$" -f $hostName, $drive)
    $tempZip = Join-Path $share $stageName
    $remoteZip = ($drive + ':\' + $stageName)
    try {
        Copy-Item -LiteralPath $pkg.Zip -Destination $tempZip -ErrorAction Stop
        $count = Invoke-HkServerCommand -ComputerName $hostName -Credential $cred -Script {
            param($p, $z, $expectedHash)
            if ((Get-FileHash -LiteralPath $z -Algorithm SHA256).Hash -ne $expectedHash) { throw 'Siirretty paketti on vioittunut.' }
            Expand-Archive -LiteralPath $z -DestinationPath $p -Force -ErrorAction Stop
            (Get-ChildItem -LiteralPath $p -Recurse -File | Measure-Object).Count
        } -Arguments @($path, $remoteZip, (Get-FileHash -LiteralPath $pkg.Zip -Algorithm SHA256).Hash)
    } catch {
        throw ("Tiedostojen siirto palvelimelle epaonnistui ($share). Tarkista yllapito-oikeudet ja hallinnollinen jako.`nYksityiskohta: {0}" -f $_.Exception.Message)
    } finally {
        Remove-Item -LiteralPath $tempZip -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $pkg.Zip -Force -ErrorAction SilentlyContinue
        if ($pkg.Root) { Remove-Item -LiteralPath $pkg.Root -Recurse -Force -ErrorAction SilentlyContinue }
    }
    & $OnStep 'server-tiedostot' 'ok' ('{0} tiedostoa palvelimella ({1})' -f $count, $path)

    # --- 4) kayttooikeudet: ohjelma luku, AppData muokkaus LOCAL SERVICE -tilille, salaisuudet rajattu (K2, K7) ---
    & $OnStep 'server-ajo' 'start' ''
    $null = Invoke-HkServerCommand -ComputerName $hostName -Credential $cred -Script {
        param($p)
        $ls = '*S-1-5-19'      # NT AUTHORITY\LOCAL SERVICE
        $admins = '*S-1-5-32-544'; $system = '*S-1-5-18'
        & icacls.exe @($p, '/inheritance:r', '/grant:r', "$($admins):(OI)(CI)F", "$($system):(OI)(CI)F", "$($ls):(OI)(CI)RX") | Out-Null
        $data = Join-Path $p 'AppData'
        New-Item -ItemType Directory -Force -Path $data | Out-Null
        & icacls.exe @($data, '/grant:r', "$($ls):(OI)(CI)M") | Out-Null
        # Sovellus tallentaa SMTP-asetukset appsettings.jsoniin (v2.6.0->) -> muokkausoikeus vain tahan tiedostoon
        $cfgFile = Join-Path $p 'appsettings.json'
        if (Test-Path -LiteralPath $cfgFile) { & icacls.exe @($cfgFile, '/grant:r', "$($ls):M") | Out-Null }
        foreach ($secret in @('appsettings.Production.json', 'AppData\first-admin.json')) {
            $f = Join-Path $p $secret
            if (Test-Path -LiteralPath $f) {
                & icacls.exe @($f, '/inheritance:r', '/grant:r', "$($admins):F", "$($system):F", "$($ls):R") | Out-Null
            }
        }
    } -Arguments @($path)
    & $OnStep 'server-ajo' 'ok' 'Ohjelma vain luku, AppData muokattava palvelutilille, salaisuudet rajattu'

    # --- 5) palomuuri (K4) ---
    & $OnStep 'server-palomuuri' 'start' ''
    $null = Invoke-HkServerCommand -ComputerName $hostName -Credential $cred -Script {
        param($port, $allowed)
        $null = netsh.exe @('advfirewall', 'firewall', 'delete', 'rule', 'name=Huoltokello-Palvelin') 2>$null
        $null = netsh.exe @('advfirewall', 'firewall', 'add', 'rule', 'name=Huoltokello-Palvelin', 'dir=in', 'action=allow',
                            'protocol=TCP', ('localport=' + $port), 'profile=domain,private', ('remoteip=' + $allowed))
    } -Arguments @($port, $allowed)
    & $OnStep 'server-palomuuri' 'ok' ("TCP $port sallittu: $allowed (toimialue/yksityinen verkko)")

    # --- 6) tehtava-ajastus LOCAL SERVICE -tililla (K2) ---
    & $OnStep 'server-ajastus' 'start' ''
    $null = Invoke-HkServerCommand -ComputerName $hostName -Credential $cred -Script {
        param($p)
        $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument ('-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + (Join-Path $p 'Huoltokello-Palvelu.ps1') + '"')
        $trigger = New-ScheduledTaskTrigger -AtStartup
        $principal = New-ScheduledTaskPrincipal -UserId 'NT AUTHORITY\LOCALSERVICE' -LogonType ServiceAccount -RunLevel Limited
        $set = New-ScheduledTaskSettingsSet -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)
        $null = Register-ScheduledTask -TaskName 'Huoltokello-Palvelu' -Action $action -Trigger $trigger -Principal $principal -Settings $set -Force -ErrorAction Stop
        Start-ScheduledTask -TaskName 'Huoltokello-Palvelu'
    } -Arguments @($path)
    & $OnStep 'server-ajastus' 'ok' 'Huoltokello-Palvelu (LOCAL SERVICE, kaynnistyy palvelimen kaynnistyessa)'

    # --- 7) kaynnistys ---
    & $OnStep 'server-kaynnistys' 'start' ''
    if (-not (Wait-HkHealthUrl -Url $url -Seconds 240 -OnStep $OnStep -StepId 'server-kaynnistys')) {
        & $OnStep 'server-kaynnistys' 'fail' ("Sovellus ei vastannut osoitteessa " + $url)
        throw ("Sovellus ei vastannut kohteessa $url. Tarkista palomuuri (TCP $port) ja loki $path\AppData\app-err.log.")
    }
    & $OnStep 'server-kaynnistys' 'ok' $url

    # --- 8) varmistus + first-admin.json pois (K7) ---
    & $OnStep 'server-varmistus' 'start' ''
    if (-not (Test-HkUrlOk -Url ($url + '/Account/Login'))) {
        & $OnStep 'server-varmistus' 'fail' 'Kirjautumissivu ei vastannut.'
        throw 'Kirjautumissivu ei vastannut palvelimelta.'
    }
    Start-Sleep -Seconds 5
    $null = Invoke-HkServerCommand -ComputerName $hostName -Credential $cred -Script {
        param($p)
        Remove-Item -LiteralPath (Join-Path $p 'AppData\first-admin.json') -Force -ErrorAction SilentlyContinue
    } -Arguments @($path)
    & $OnStep 'server-varmistus' 'ok' 'Kirjautumissivu vastaa; alkusalasanatiedosto poistettu'

    # --- 9) pikakuvake ---
    if ($s.MakeShortcut) {
        & $OnStep 'server-pikakuva' 'start' ''
        try {
            $desktop = [Environment]::GetFolderPath('Desktop')
            $sc = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $desktop 'Huoltokello.url'))
            $sc.TargetPath = $url
            $sc.Save()
            & $OnStep 'server-pikakuva' 'ok' 'Tyopoydan pikakuvake Huoltokello.url luotu'
        } catch { & $OnStep 'server-pikakuva' 'fail' ('Pikakuvaketta ei voitu luoda: ' + $_.Exception.Message) }
    }

    & $OnStep 'valmis' 'ok' $url
    if ($url -like 'http://*') {
        & $OnStep 'valmis' 'info' 'HUOM: yhteys on salaamaton HTTP. Ota kayttoon HTTPS (IIS/ARR- tai Caddy-kaanteisvalitys, varmenne) ennen tuotantokayttoa.'
    }
    return @{ Ok = $true; Url = $url; ServerName = $hostName; Path = $path; Port = $port; Email = [string]$s.AdminEmail; Updated = $isUpdate; Mode = 'palvelin' }
}
