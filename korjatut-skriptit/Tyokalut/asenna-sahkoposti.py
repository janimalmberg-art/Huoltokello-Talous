# -*- coding: utf-8 -*-
"""
asenna-sahkoposti.py — dev@huolto.fi <-> Gmail -automaatiotyökalu (Huoltokello-työkalu)
Tekee cPanel/UAPI-toiminnot: laatikko, uudelleenohjaus -> Gmail, DKIM, DMARC,
alidomain dev.huolto.fi (+MX), SMTP-testiviesti, Gmail-asetussivun avaus.

KORJATTU VERSIO (tietoturvakatselmointi 16.11.2026):
  K1 KRIITTINEN: TLS-varmennetarkistus kytkeytyi HILJAA pois minkä tahansa virheen jälkeen
     (myös 401/aikakatkaisu), jonka jälkeen cPanel-tunnukset lähetettiin varmentamattomaan
     yhteyteen -> välimieshyökkäys mahdollinen. Nyt varmenne tarkistetaan aina; itse
     allekirjoitettu varmenne sallitaan vain --sormenjalki <SHA-256> -kiinnityksellä.
  K2 Tunnukset lähetettiin automaattisesti myös porttiin 2087 (WHM/root). Poistettu.
  K3 Uuden laatikon salasana kulki URL-osoitteessa (GET) -> palvelimen lokeihin. Nyt POST.
  K4 Salasana tallennettiin selkokielisenä työpöydälle. Nyt näytetään kerran; tallennus
     vain --tallenna-valinnalla omistajan oikeuksin.
  K5 Verkkotunnus ja Gmail-osoite parametreiksi (ei kovakoodattu henkilökohtainen osoite).
  K6 SMTP-testi: TLS-konteksti varmenteen tarkistuksella; ei viestiä itselle silmukkana.

Käyttö:   python asenna-sahkoposti.py --domain huolto.fi --gmail oma@gmail.com
          python asenna-sahkoposti.py --tarkistus
"""
import argparse
import base64
import getpass
import hashlib
import json
import os
import secrets
import smtplib
import socket
import ssl
import subprocess
import sys
import webbrowser
from urllib import error, parse, request as rq

CP = {"user": None, "pw": None, "base": None, "ctx": None, "fingerprint": None, "host": None}


def _ctx():
    """TLS-konteksti: aina varmennetarkistus, tai tiukka sormenjälkikiinnitys."""
    if CP["ctx"] is not None:
        return CP["ctx"]
    if CP["fingerprint"]:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with socket.create_connection((CP["host"], 2083), timeout=10) as s:
            with ctx.wrap_socket(s, server_hostname=CP["host"]) as tls:
                der = tls.getpeercert(binary_form=True)
        got = hashlib.sha256(der).hexdigest().upper()
        want = CP["fingerprint"].replace(":", "").upper()
        if got != want:
            sys.exit(f"VAROITUS: palvelimen varmenteen sormenjälki ei täsmää ({got}). Yhteys katkaistu.")
        CP["ctx"] = ctx
    else:
        CP["ctx"] = ssl.create_default_context()
    return CP["ctx"]


def uapi(module, func, params=None, api="execute"):
    params = params or {}
    if not CP["user"] or not CP["pw"]:
        print("Tarvitaan cPanel-tunnukset (suositus: API-token, cPanel > Security > Manage API Tokens).")
        CP["user"] = input(" cPanel-käyttäjä: ").strip()
        CP["pw"] = getpass.getpass(" Salasana/token: ")
    if api == "execute":
        url = f"{CP['base']}/execute/{module}/{func}"
        body = parse.urlencode(params).encode()
    else:
        url = f"{CP['base']}/json-api/cpanel"
        q = {"cpanel_jsonapi_apiversion": "2", "cpanel_jsonapi_module": module, "cpanel_jsonapi_func": func}
        q.update(params)
        body = parse.urlencode(q).encode()
    if len(CP["pw"]) >= 30 and " " not in CP["pw"]:
        auth = f"cpanel {CP['user']}:{CP['pw']}"            # API-token
    else:
        auth = "Basic " + base64.b64encode(f"{CP['user']}:{CP['pw']}".encode()).decode()
    req = rq.Request(url, data=body, method="POST",
                     headers={"Authorization": auth, "User-Agent": "hk-sahkoposti-tool/2.0",
                              "Content-Type": "application/x-www-form-urlencoded"})
    try:
        with rq.urlopen(req, timeout=25, context=_ctx()) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except ssl.SSLCertVerificationError as e:
        print(" TLS-varmenne ei kelpaa:", e.verify_message)
        print(" Jos palvelimella on itse allekirjoitettu varmenne, tarkista sen SHA-256-sormenjälki")
        print(" palveluntarjoajalta ja käynnistä uudelleen: --sormenjalki AA:BB:...")
        return {"status": 0, "errors": ["TLS-varmenne ei kelpaa"]}
    except error.HTTPError as e:
        if e.code in (401, 403):
            print(" Hylätty (401/403): tarkista käyttäjä ja salasana/token.")
            CP["pw"] = None
        return {"status": 0, "errors": [f"HTTP {e.code}"]}
    except (error.URLError, socket.timeout) as e:
        return {"status": 0, "errors": [f"Yhteysvirhe: {e}"]}


def ok(res, label):
    if res.get("status") in (1, True):
        print(f" ✓ {label} OK")
        return True
    print(" ✗", label, "->", res.get("errors") or res)
    return False


def dns_ja_portit(host):
    print("\n[1] DNS- ja porttitarkistus")
    for tyyppi in ("MX", "TXT", "NS"):
        r = subprocess.run(["nslookup", f"-type={tyyppi}", host, "1.1.1.1"], capture_output=True, text=True, timeout=20)
        rivit = [l.strip() for l in r.stdout.splitlines() if l.strip()]
        print(f" {tyyppi}: " + " | ".join(rivit[2:6]))
    for p, nimi in ((25, "SMTP 25"), (465, "SMTPS 465"), (587, "SMTP 587"), (993, "IMAPS 993"), (2083, "cPanel 2083")):
        with socket.socket() as s:
            s.settimeout(3)
            auki = s.connect_ex((host, p)) == 0
        print(f" {nimi:<12} {'AUKI' if auki else 'suljettu'}")


def luo_laatikko(host, tallenna):
    print("\n[2] Luo sähköpostilaatikko")
    local = input(f" Laatikon nimi @{host} (oletus dev): ").strip() or "dev"
    pw = secrets.token_urlsafe(18)
    res = uapi("Email", "add_pop", {"domain": host, "email": local, "password": pw, "quota": 0})
    if not ok(res, f"Laatikko {local}@{host}"):
        return None
    print(f"\n Laatikon salasana (näytetään vain kerran, tallenna salasanojen hallintaan):\n   {pw}\n")
    if tallenna:
        desk = os.path.join(os.path.expanduser("~"), "Desktop")
        tied = os.path.join(desk, f"{local}-{host}-SMTP-tunnukset.txt")
        fd = os.open(tied, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(f"SÄHKÖPOSTI: {local}@{host}\nSALASANA : {pw}\nSMTP: {host} 587 (STARTTLS) / 465 (SSL)\n")
        if os.name == "nt":
            subprocess.run(["icacls", tied, "/inheritance:r", "/grant:r", f"{os.environ.get('USERNAME')}:F"],
                           capture_output=True, check=False)
        print(f" -> Tallennettu (vain sinulle luettava): {tied}. POISTA tiedosto käytön jälkeen.")
    return {"local": local, "pw": pw}


def luo_uudelleenohjaus(host, gmail):
    print("\n[3] Uudelleenohjaus -> Gmail")
    dom = input(f" Domain [{host}]: ").strip() or host
    local = input(" Osoitteen nimi (ennen @-merkkiä) [dev]: ").strip() or "dev"
    dest = input(f" Kohdesähköposti [{gmail}]: ").strip() or gmail
    ok(uapi("Email", "add_forwarder", {"domain": dom, "email": local, "fwdopt": "fwd", "fwdemail": dest}),
       f"Uudelleenohjaus {local}@{dom} -> {dest}")


def dkim_dmarc(host, gmail):
    print("\n[4] DKIM + DMARC")
    ok(uapi("EmailAuth", "enable_dkim", {"domain": host}), "DKIM")
    r = subprocess.run(["nslookup", "-type=TXT", f"_dmarc.{host}", "1.1.1.1"], capture_output=True, text=True, timeout=20)
    if "v=DMARC1" in (r.stdout + r.stderr):
        print(" DMARC on jo olemassa")
        return
    ok(uapi("DNS", "add_zone_record", {"domain": host, "name": f"_dmarc.{host}.", "type": "TXT",
                                       "txtdata": f"v=DMARC1; p=quarantine; rua=mailto:{gmail}", "ttl": 3600}),
       "DMARC-tietue")


def smtp_testi(host, gmail, laatikko=None):
    print("\n[6] SMTP-testiviesti")
    if laatikko is None:
        local = input(" Lähettävä laatikko [dev]: ").strip() or "dev"
        laatikko = {"local": local, "pw": getpass.getpass(f" Salasana ({local}@{host}): ")}
    src = f"{laatikko['local']}@{host}"
    msg = (f"From: {src}\r\nTo: {gmail}\r\nSubject: Testi {secrets.token_hex(3)}\r\n\r\n"
           "Automaattinen kokoonpanotesti.\r\n")
    try:
        with smtplib.SMTP(host, 587, timeout=25) as s:
            s.starttls(context=ssl.create_default_context())
            s.login(src, laatikko["pw"])
            s.sendmail(src, [gmail], msg)
        print(f" ✓ Lähetys onnistui ({src} -> {gmail}).")
    except (smtplib.SMTPException, OSError, ssl.SSLError) as e:
        print(" ✗ SMTP-virhe:", e)


def main():
    ap = argparse.ArgumentParser(description="cPanel-sähköpostin käyttöönotto Gmail-ohjauksella")
    ap.add_argument("--domain", default="huolto.fi")
    ap.add_argument("--gmail", help="Gmail-kohdeosoite")
    ap.add_argument("--sormenjalki", help="cPanel-palvelimen varmenteen SHA-256 (vain itse allekirjoitetulle)")
    ap.add_argument("--tallenna", action="store_true", help="tallenna uuden laatikon tunnukset tiedostoon")
    ap.add_argument("--tarkistus", action="store_true", help="vain DNS/porttitarkistus")
    a = ap.parse_args()
    CP["host"] = a.domain
    CP["base"] = f"https://{a.domain}:2083"
    CP["fingerprint"] = a.sormenjalki
    if a.tarkistus:
        dns_ja_portit(a.domain)
        return
    gmail = a.gmail or input("Gmail-kohdeosoite: ").strip()
    laatikko = None
    while True:
        print("\n 1) DNS/portit  2) Luo laatikko  3) Uudelleenohjaus  4) DKIM+DMARC  6) SMTP-testi  "
              "7) Gmail-asetukset  0) Lopeta")
        v = input("Valintasi: ").strip()
        if v == "1":
            dns_ja_portit(a.domain)
        elif v == "2":
            laatikko = luo_laatikko(a.domain, a.tallenna) or laatikko
        elif v == "3":
            luo_uudelleenohjaus(a.domain, gmail)
        elif v == "4":
            dkim_dmarc(a.domain, gmail)
        elif v == "6":
            smtp_testi(a.domain, gmail, laatikko)
        elif v == "7":
            webbrowser.open("https://mail.google.com/mail/u/0/#settings/accounts")
            print(f" SMTP: {a.domain} | portti 587 | TLS | käyttäjä: dev@{a.domain}")
        elif v == "0":
            break


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nKeskeytetty.")
