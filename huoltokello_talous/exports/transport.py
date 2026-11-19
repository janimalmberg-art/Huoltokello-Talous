"""Aineiston siirto määritettyyn kirjanpitopalveluun.

Siirtotavat (Asetukset > Vienti > Siirtotapa):
  kansio          tiedosto jaettuun kansioon (esim. tilitoimiston OneDrive/SharePoint/verkkolevy)
  sahkoposti      liitteenä kirjanpitäjälle (SMTP, pakotettu TLS ja varmenteen tarkistus)
  https           HTTPS POST omaan integraatioon / iPaaS-palveluun (Bearer-tunniste)
  netvisor_api    Visma Netvisor -rajapinta, tositteet (accounting.nv), MAC-tunnistus
  procountor_api  Procountor-rajapinta, tositteet (OAuth2 client credentials)

Rajapintasiirroissa on oletuksena koeajo: viesti muodostetaan ja tallennetaan lokiin
lähettämättä. Kytke tuotantolähetys päälle vasta, kun testiympäristössä on todettu
aineiston menevän oikein (Asetukset > Vienti > "Rajapintalähetys tuotannossa").
Sama aineisto ei lähde kahdesti (idempotenssiavain = tiedoston SHA-256 + kohde).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import smtplib
import ssl
import urllib.parse
import urllib.request
import uuid
from email.message import EmailMessage
from xml.sax.saxutils import escape

from .. import db, settings
from ..money import D
from ..security import iso, utcnow

MAX_EMAIL_BYTES = 20 * 1024 * 1024


class TransportError(Exception):
    pass


def _secrets():
    from ..app_context import secret_store
    return secret_store()


def _log(kind: str, period: str, target: str, filename: str, sha: str, status: str, message: str, key: str,
         user_id=None) -> int:
    cur = db.ex("INSERT INTO exports(ts, kind, period, target, filename, sha256, status, message, idempotency_key, "
                "user_id) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (iso(utcnow()), kind, period, target, filename, sha, status, message[:2000], key, user_id))
    return cur.lastrowid


def send(kind: str, content: bytes, filename: str, period: str, user_id=None, force: bool = False,
         journal_range: tuple[str, str] | None = None, content_key: str = "") -> dict:
    """content_key: aineiston sisällön tunniste ilman aikaleimoja (estää saman aineiston uudelleenlähetyksen)."""
    target = settings.get_str("export.transport")
    sha = hashlib.sha256(content).hexdigest()
    key = hashlib.sha256(f"{target}|{content_key or sha}".encode()).hexdigest()
    if not force and db.q1("SELECT 1 FROM exports WHERE idempotency_key = ? AND status = 'ok'", (key,)):
        return {"status": "ohitettu", "message": "Sama aineisto on jo siirretty onnistuneesti tähän kohteeseen."}
    try:
        if target == "kansio":
            msg = _to_folder(content, filename)
        elif target == "sahkoposti":
            msg = _to_email(content, filename, period)
        elif target == "https":
            msg = _to_https(content, filename, period, kind)
        elif target == "netvisor_api":
            if not journal_range:
                raise TransportError("Netvisor-rajapintaan siirretään kirjanpitotositteet (valitse kausi).")
            msg = _to_netvisor(*journal_range)
        elif target == "procountor_api":
            if not journal_range:
                raise TransportError("Procountor-rajapintaan siirretään kirjanpitotositteet (valitse kausi).")
            msg = _to_procountor(*journal_range)
        else:
            raise TransportError(f"Tuntematon siirtotapa: {target}")
    except (TransportError, OSError, smtplib.SMTPException, ValueError) as exc:
        _log(kind, period, target, filename, sha, "virhe", str(exc), key, user_id)
        return {"status": "virhe", "message": str(exc)}
    status = "odottaa" if msg.startswith("KOEAJO") else "ok"
    _log(kind, period, target, filename, sha, status, msg, key if status == "ok" else "", user_id)
    return {"status": status, "message": msg}


# ------------------------------------------------------------------ kansio

def _to_folder(content: bytes, filename: str) -> str:
    folder = settings.get_str("export.folder")
    if not folder or not os.path.isdir(folder):
        raise TransportError("Vientikansiota ei ole määritetty tai sitä ei löydy (Asetukset > Vienti).")
    safe = os.path.basename(filename)
    dest = os.path.join(folder, safe)
    tmp = dest + ".osittainen"
    with open(tmp, "wb") as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, dest)
    return f"Tallennettu: {dest}"


# ------------------------------------------------------------------ sähköposti

def smtp_send(to_addr: str, subject: str, body: str, attachment: tuple[bytes, str] | None = None) -> None:
    host = settings.get_str("smtp.host")
    port = int(settings.get("smtp.port") or 587)
    user = settings.get_str("smtp.user")
    sender = settings.get_str("smtp.sender") or user
    password = _secrets().get("smtp.password").replace(" ", "")
    if not host or not sender:
        raise TransportError("SMTP-asetukset puuttuvat (Asetukset > Sähköposti).")
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg["Message-ID"] = f"<{uuid.uuid4()}@huoltokello-talous>"
    msg.set_content(body)
    if attachment:
        data, name = attachment
        if len(data) > MAX_EMAIL_BYTES:
            raise TransportError("Liite on yli 20 Mt – käytä kansio- tai HTTPS-siirtoa.")
        maintype, subtype = ("application", "zip") if name.endswith(".zip") else ("text", "csv") \
            if name.endswith(".csv") else ("application", "octet-stream")
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=name)
    ctx = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=ctx, timeout=30) as s:
            if user:
                s.login(user, password)
            s.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=30) as s:
            s.ehlo()
            if not s.has_extn("starttls"):
                raise TransportError("SMTP-palvelin ei tarjoa STARTTLS-suojausta – salaamatonta lähetystä ei sallita.")
            s.starttls(context=ctx)
            s.ehlo()
            if user:
                s.login(user, password)
            s.send_message(msg)


def _to_email(content: bytes, filename: str, period: str) -> str:
    to = settings.get_str("export.email_to")
    if not to or "@" not in to:
        raise TransportError("Kirjanpitäjän sähköpostiosoite puuttuu (Asetukset > Vienti).")
    comp = settings.get_str("company.name")
    smtp_send(to, f"Kirjanpitoaineisto {comp} {period}",
              f"Hei,\n\nliitteenä {comp}:n kirjanpitoaineisto kaudelta {period}.\n"
              f"Tiedosto: {filename}\nSHA-256: {hashlib.sha256(content).hexdigest()}\n\n"
              f"Viesti on lähetetty automaattisesti Huoltokello-Talous-ohjelmasta.\n",
              (content, filename))
    return f"Lähetetty sähköpostilla: {to}"


# ------------------------------------------------------------------ HTTPS

def _https_post(url: str, body: bytes, headers: dict, timeout: int = 60) -> tuple[int, bytes]:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" and parsed.hostname not in ("127.0.0.1", "localhost"):
        raise TransportError("Vain HTTPS-osoitteet sallitaan.")
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return resp.status, resp.read(1_000_000)
    except urllib.error.HTTPError as exc:
        raise TransportError(f"HTTP {exc.code}: {exc.read(500).decode('utf-8', 'replace')}")
    except urllib.error.URLError as exc:
        raise TransportError(f"Yhteysvirhe: {exc.reason}")


def _to_https(content: bytes, filename: str, period: str, kind: str) -> str:
    url = settings.get_str("export.https_url")
    if not url:
        raise TransportError("HTTPS-osoite puuttuu (Asetukset > Vienti).")
    token = _secrets().get("export.https_token")
    headers = {"Content-Type": "application/zip" if filename.endswith(".zip") else "text/csv",
               "X-Filename": urllib.parse.quote(filename), "X-Period": period, "X-Kind": kind,
               "X-Content-SHA256": hashlib.sha256(content).hexdigest(),
               "Idempotency-Key": hashlib.sha256(content).hexdigest()}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    status, _body = _https_post(url, content, headers)
    return f"HTTPS {status}: {url}"


# ------------------------------------------------------------------ Netvisor

NETVISOR_HOSTS = {"test": "https://isvapi.netvisor.fi", "prod": "https://integration.netvisor.fi"}


def _netvisor_headers(url: str) -> dict:
    sec = _secrets()
    sender = "Huoltokello-Talous"
    customer_id = sec.get("netvisor.customer_id")
    partner_id = sec.get("netvisor.partner_id")
    customer_key = sec.get("netvisor.customer_key")
    partner_key = sec.get("netvisor.partner_key")
    org_id = settings.get_str("company.business_id")
    if not all([customer_id, partner_id, customer_key, partner_key, org_id]):
        raise TransportError("Netvisor-tunnukset puuttuvat (Asetukset > Vienti > Netvisor).")
    ts = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    lang = "FI"
    tx = uuid.uuid4().hex
    mac_src = "&".join([url, sender, customer_id, ts, lang, org_id, tx, customer_key, partner_key])
    return {
        "X-Netvisor-Authentication-Sender": sender,
        "X-Netvisor-Authentication-CustomerId": customer_id,
        "X-Netvisor-Authentication-PartnerId": partner_id,
        "X-Netvisor-Authentication-Timestamp": ts,
        "X-Netvisor-Authentication-TimestampFormat": "ISO-8601",
        "X-Netvisor-Interface-Language": lang,
        "X-Netvisor-Organisation-ID": org_id,
        "X-Netvisor-Authentication-TransactionId": tx,
        "X-Netvisor-Authentication-MAC": hashlib.sha256(mac_src.encode("utf-8")).hexdigest(),
        "X-Netvisor-Authentication-MACHashCalculationAlgorithm": "SHA256",
        "Content-Type": "text/xml; charset=utf-8",
    }


def netvisor_vouchers(start: str, end: str) -> list[str]:
    """Muodostaa tositteet Netvisorin accounting.nv-muotoon (netto, Netvisor laskee ALV:n)."""
    from .profiles import NETVISOR_VAT
    vat_acc = settings.get_str("acc.vat_payable")
    out = []
    for j in db.q("SELECT * FROM journals WHERE entry_date BETWEEN ? AND ? ORDER BY entry_date, series, number",
                  (start, end)):
        lines = db.q("SELECT * FROM journal_lines WHERE journal_id = ?", (j["id"],))
        xl = []
        for ln in lines:
            if ln["account"] == vat_acc:
                continue  # Netvisor muodostaa ALV-kirjaukset ALV-koodista
            amount = D(ln["debit"]) - D(ln["credit"])
            code = NETVISOR_VAT.get(ln["vat_code"], "NONE")
            pct = ln["vat_rate"] or "0"
            xl.append(f"<voucherline><linesum type=\"net\">{str(amount).replace('.', ',')}</linesum>"
                      f"<description>{escape(ln['description'][:255])}</description>"
                      f"<accountnumber>{escape(ln['account'])}</accountnumber>"
                      f"<vatpercent vatcode=\"{code}\">{pct.replace('.', ',')}</vatpercent></voucherline>")
        out.append("<root><voucher><calculationmode>net</calculationmode>"
                   f"<voucherdate format=\"ansi\">{j['entry_date']}</voucherdate>"
                   f"<number>{j['number']}</number><description>{escape(j['description'][:255])}</description>"
                   f"<voucherclass>{'Myyntilasku' if j['series'] in ('MY', 'HY') else 'Muistiotosite'}</voucherclass>"
                   + "".join(xl) + "</voucher></root>")
    return out


def _to_netvisor(start: str, end: str) -> str:
    vouchers = netvisor_vouchers(start, end)
    if not vouchers:
        raise TransportError("Kaudella ei ole kirjauksia.")
    host = NETVISOR_HOSTS["prod" if settings.get_str("export.netvisor_env") == "prod" else "test"]
    url = f"{host}/accounting.nv"
    if not settings.get("export.api_live"):
        _netvisor_headers(url)  # tarkistaa tunnusten olemassaolon
        return f"KOEAJO: {len(vouchers)} tositetta muodostettu Netvisoriin ({host}), ei lähetetty."
    sent = 0
    for v in vouchers:
        status, body = _https_post(url, v.encode("utf-8"), _netvisor_headers(url))
        if b"<Status>OK" not in body and b"<Status><![CDATA[OK" not in body and b">OK<" not in body:
            raise TransportError(f"Netvisor hylkäsi tositteen {sent + 1}: {body[:300].decode('utf-8', 'replace')}")
        sent += 1
    return f"Netvisor: {sent} tositetta siirretty ({host})."


# ------------------------------------------------------------------ Procountor

PROCOUNTOR_HOSTS = {"test": "https://pts-procountor.pubdev.azure.procountor.com",
                    "prod": "https://api.procountor.com"}


def procountor_receipts(start: str, end: str) -> list[dict]:
    vat_acc = settings.get_str("acc.vat_payable")
    out = []
    for j in db.q("SELECT * FROM journals WHERE entry_date BETWEEN ? AND ? ORDER BY entry_date, series, number",
                  (start, end)):
        tx = []
        for ln in db.q("SELECT * FROM journal_lines WHERE journal_id = ?", (j["id"],)):
            if ln["account"] == vat_acc:
                continue
            tx.append({"account": ln["account"], "accountingValue": float(D(ln["debit"]) - D(ln["credit"])),
                       "vatPercent": float(D(ln["vat_rate"] or 0)), "description": ln["description"][:255],
                       "transactionType": "JOURNAL"})
        out.append({"type": "JOURNAL", "name": j["description"][:80], "receiptDate": j["entry_date"],
                    "accountingDate": j["entry_date"], "vatType": "SALES" if j["series"] in ("MY", "HY") else "NONE",
                    "transactions": tx})
    return out


def _to_procountor(start: str, end: str) -> str:
    receipts = procountor_receipts(start, end)
    if not receipts:
        raise TransportError("Kaudella ei ole kirjauksia.")
    host = PROCOUNTOR_HOSTS["prod" if settings.get_str("export.procountor_env") == "prod" else "test"]
    sec = _secrets()
    cid, csecret, apikey = sec.get("procountor.client_id"), sec.get("procountor.client_secret"), \
        sec.get("procountor.api_key")
    if not all([cid, csecret, apikey]):
        raise TransportError("Procountor-tunnukset puuttuvat (Asetukset > Vienti > Procountor).")
    if not settings.get("export.api_live"):
        return f"KOEAJO: {len(receipts)} tositetta muodostettu Procountoriin ({host}), ei lähetetty."
    form = urllib.parse.urlencode({"grant_type": "client_credentials", "client_id": cid, "client_secret": csecret,
                                   "api_key": apikey}).encode()
    _s, body = _https_post(f"{host}/api/oauth/token", form, {"Content-Type": "application/x-www-form-urlencoded"})
    token = json.loads(body.decode()).get("access_token")
    if not token:
        raise TransportError("Procountor ei palauttanut käyttöoikeustunnusta.")
    for i, r in enumerate(receipts, 1):
        _https_post(f"{host}/api/ledgerreceipts", json.dumps(r).encode(),
                    {"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    return f"Procountor: {len(receipts)} tositetta siirretty ({host})."
