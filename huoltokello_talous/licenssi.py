"""Lisenssijärjestelmä: avaimet, konekoodi ja aktivointi (kehittäjän työkalun kumppani).

Tämä tiedosto puhuu saman kielen kuin kehittäjän Lisenssityökalu
(PowerShell) sekä Huoltokellon Tyokalut\\_lisenssi.ps1:

  Lisenssiavain   25 numeroa, muoto XXXXX-XXXXX-XXXXX-XXXXX-XXXXX
      D1 malli (1), D2-3 taso 01..04, D4-10 sarjanumero,
      D11 kesto (0 = ikuinen, 1..9 = vuotta), D12-20 satunnainen,
      D21-25 tiiviste = HMAC-SHA256(SALT_AVAIN, D1..D20)[0:4] mod 100000

  Konekoodi       12 heksamerkkiä (näyttömuoto XXXX-XXXX-XXXX):
      peruste = "MachineGuid|C-aseman sarjanumero|KONEEN NIMI"
      konekoodi = HMAC-SHA256(SALT_KONE, peruste).hex()[:12]

  Aktivointikoodi 12 numeroa, muoto XXXXXX-XXXXXX:
      HMAC-SHA256(SALT_AKTIV, konekoodi + '|' + avainNumerot)[0:6] mod 10^12
      Koodi kelpaa vain sille koneelle, jolle kehittäjä sen on laskenut:
      samaa avainta ei voi ottaa toiseen koneeseen ilman uutta koodia.

  Lisenssitila    yhteinen tiedosto %APPDATA%\\Hk-Lisenssi\\lisenssi.json
      (testauksessa ympäristömuuttuja HK_LISENSI_TIEDOSTO),
      varmiste = HMAC-SHA256(SALT_TILA, "avain|taso|kesto|konekoodi|
      aktivoitu|voimassa_paattyen").hex()[:32]

Molemmat Huoltokello-ohjelmat (Talous ja pääohjelma) hyväksyvät samat
avain: yksi avain aktivoi molemmat samassa koneessa, kun kehittäjä on
myöntänyt aktivointikoodin tälle konekoodille. """
from __future__ import annotations

import ctypes
import datetime as dt
import hashlib
import hmac
import json
import os

# ---------------------------------------------------------------- vakiot

TASOT = {
    "01": {"nimi": "Huoltokello Yritys Pieni", "serverit": 1, "tyontekijat": 50, "johto": 5},
    "02": {"nimi": "Huoltokello Yritys", "serverit": 1, "tyontekijat": 300, "johto": 10},
    "03": {"nimi": "Huoltokello Yritys Suuri", "serverit": 1, "tyontekijat": 900, "johto": 20},
    "04": {"nimi": "Huoltokello Yritys Enterprise", "serverit": 5, "tyontekijat": 2000, "johto": 100},
}

# Sama kovakoodattu suola kuin Lisenssityokalu.ps1 / _lisenssi.ps1.
_SALT_AVAIN = "Hk-Lisenssi-Avain-v1|2026|Malmberg"
_SALT_AKTIV = "Hk-Lisenssi-Aktivointi-v1|2026|Malmberg"
_SALT_KONE = "Hk-Konetunnus-v1|2026"
_SALT_TILA = "Hk-Lisenssi-Tila-v1|2026"

_TILAKANSIO = os.path.join(os.environ.get("APPDATA", ""), "Hk-Lisenssi")


def tila_polku() -> str:
    return os.environ.get("HK_LISENSSI_TIEDOSTO") or os.path.join(_TILAKANSIO, "lisenssi.json")


# ---------------------------------------------------------------- apurit


def _hmac_sha256(suola: str, data: str) -> bytes:
    return hmac.new(suola.encode("utf-8"), data.encode("utf-8"), hashlib.sha256).digest()


def _pelkat_numerot(teksti: str) -> str:
    return "".join(c for c in teksti if c.isdigit())


def _hex(teksti: str) -> str:
    return "".join(c for c in teksti.upper() if c in "0123456789ABCDEF")


def muotoile_avain(avain: str) -> str:
    n = _pelkat_numerot(avain)
    return "-".join(n[i:i + 5] for i in range(0, 25, 5)) if len(n) == 25 else avain


def muotoile_kone(konekoodi_teksti: str) -> str:
    h = _hex(konekoodi_teksti)
    return "{}-{}-{}".format(h[:4], h[4:8], h[8:12]) if len(h) == 12 else konekoodi_teksti


def muotoile_koodi(koodi: str) -> str:
    n = _pelkat_numerot(koodi)
    return "{}-{}".format(n[:6], n[6:12]) if len(n) == 12 else koodi


# ---------------------------------------------------------------- konekoodi


def kone_peruste() -> str:
    """Koneen pysyvät tunnisteet: MachineGuid | C-aseman sarjanumero | koneen nimi."""
    guid = ""
    try:
        import winreg  # noqa: PLC0415 - vain Windows

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography") as avain:
            guid = str(winreg.QueryValueEx(avain, "MachineGuid")[0])
    except OSError:
        pass
    voit = ""
    try:
        sarja = ctypes.c_ulong(0)
        if ctypes.windll.kernel32.GetVolumeInformationW("C:\\", None, 0, ctypes.byref(sarja), None, None, None, 0):
            voit = "{:08X}".format(sarja.value)
    except OSError:
        pass
    nimi = os.environ.get("COMPUTERNAME", "").upper()
    return "{}|{}|{}".format(guid, voit, nimi)


def konekoodi() -> str:
    """12 heksamerkkiä; sama kuin PowerShellin Get-HkLisKoneKoodi samassa koneessa."""
    return _hmac_sha256(_SALT_KONE, kone_peruste()).hex()[:12].upper()


# ---------------------------------------------------------------- avain


def avain_tarkistus(avain: str) -> dict:
    """Parsii ja tarkistaa avaimen: {'ok', 'virhe', 'taso', 'nimi', 'kesto', 'sarja', 'numero'}."""
    n = _pelkat_numerot(avain)
    if len(n) != 25:
        return {"ok": False, "virhe": "Avaimen on oltava 25 numeroa: XXXXX-XXXXX-XXXXX-XXXXX-XXXXX."}
    if n[0] != "1":
        return {"ok": False, "virhe": "Tuntematon malli (ensimmäinen numero ei ole 1)."}
    taso = n[1:3]
    if taso not in TASOT:
        return {"ok": False, "virhe": "Tuntematon taso: {}. Käytettävät: 01, 02, 03, 04.".format(taso)}
    tiiviste = int.from_bytes(_hmac_sha256(_SALT_AVAIN, n[:20])[:4], "big") % 100000
    if "{:05d}".format(tiiviste) != n[20:25]:
        return {"ok": False, "virhe": "Avaimen tiiviste ei täsmää: avainta ei ole myönnetty lisenssityökalulla."}
    return {
        "ok": True,
        "taso": taso,
        "nimi": TASOT[taso]["nimi"],
        "kesto": int(n[10]),
        "sarja": n[3:10],
        "numero": n,
    }


def aktivointikoodi(konekoodi_tai_muoto: str, avain: str) -> str:
    """Laskee aktivointikoodin tietylle konekoodille ja avaimelle."""
    kk = _hex(konekoodi_tai_muoto)
    av = _pelkat_numerot(avain)
    arvo = int.from_bytes(_hmac_sha256(_SALT_AKTIV, kk + "|" + av)[:6], "big") % 10 ** 12
    return "{:012d}".format(arvo)


# ---------------------------------------------------------------- tilatiedosto


def tila_varmiste(numero: str, taso: str, kesto: int, kone: str, aktivoitu: str, paattyen: str) -> str:
    rivi = "{}|{}|{}|{}|{}|{}".format(numero, taso, kesto, kone, aktivoitu, paattyen)
    return _hmac_sha256(_SALT_TILA, rivi).hex()[:32]


def _kirjoita_tila(polu: str, tila: dict) -> None:
    kansio = os.path.dirname(polu)
    if kansio:
        os.makedirs(kansio, exist_ok=True)
    tmp = polu + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(tila, fh, ensure_ascii=False, sort_keys=True)
    os.replace(tmp, polu)


def aktivoi(avain: str, koodi: str, konekoodi_ohitus: str | None = None) -> dict:
    """Aktivoi avain tälle koneelle: {'ok', 'virhe', ...} + onnistuessa tilakentät.

    Aktivointikoodin on vastattava TÄTÄ konetta (kehittäjä laskee koodin
    Lisenssityökalulla konekoodille). Onnistuminen tallennetaan kaikille
    ohjelmille yhteiseen lisenssitilaan.
    """
    tark = avain_tarkistus(avain)
    if not tark["ok"]:
        return {"ok": False, "virhe": tark["virhe"]}
    kone = _hex(konekoodi_ohitus or konekoodi())
    odotettu = aktivointikoodi(kone, tark["numero"])
    if _pelkat_numerot(koodi) != odotettu:
        return {"ok": False, "virhe": "Aktivointikoodi ei kelpaa tälle koneelle "
                "({}). Tarkista konekoodi ja pyydä sille omistettu koodi kehittäjältä.".format(muotoile_kone(kone))}
    aktivoitu = dt.date.today().isoformat()
    kesto = int(tark["kesto"])
    if kesto > 0:
        paattyen = (dt.date.fromisoformat(aktivoitu) + dt.timedelta(days=365 * kesto)).isoformat()
    else:
        paattyen = "ikuinen"
    tila = {
        "versio": 1,
        "avain": muotoile_avain(tark["numero"]),
        "taso": tark["taso"],
        "kesto": kesto,
        "konekoodi": kone,
        "aktivoitu": aktivoitu,
        "voimassa_paattyen": paattyen,
        "tuote": "molemmat",
        "ohjelmat": ["talous", "multiasennus"],
        "varmiste": tila_varmiste(tark["numero"], tark["taso"], kesto, kone, aktivoitu, paattyen),
    }
    try:
        _kirjoita_tila(tila_polku(), tila)
    except OSError as exc:
        return {"ok": False, "virhe": "Lisenssitiedoston tallennus epäonnistui: {}".format(exc)}
    tulos = dict(tila)
    tulos.update({"ok": True, "nimi": tark["nimi"]})
    return tulos


def lataa_tila(polu: str | None = None, konekoodi_ohitus: str | None = None) -> dict:
    """Lukee lisenssitilan ja tarkistaa eheyden: {'voimassa', 'virhe', ...}."""
    p = polu or tila_polku()
    kone = _hex(konekoodi_ohitus) if konekoodi_ohitus else konekoodi()
    if not os.path.isfile(p):
        return {"voimassa": False, "virhe": "Ei lisenssiä (kokeilujakso)."}
    try:
        with open(p, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {"voimassa": False, "virhe": "Lisenssitiedosto on vioittunut."}
    for k in ("avain", "taso", "kesto", "konekoodi", "aktivoitu", "voimassa_paattyen"):
        if k not in raw:
            return {"voimassa": False, "virhe": "Lisenssitiedostossa puuttuu kenttä {}.".format(k)}
    numero = _pelkat_numerot(raw["avain"])
    odotettu = tila_varmiste(numero, str(raw["taso"]), int(raw["kesto"]),
                             str(raw["konekoodi"]), str(raw["aktivoitu"]), str(raw["voimassa_paattyen"]))
    if raw.get("varmiste") != odotettu:
        return {"voimassa": False, "virhe": "Lisenssitiedoston varmiste ei täsmää (tiedostoa on muokattu)."}
    tark = avain_tarkistus(numero)
    if not tark["ok"]:
        return {"voimassa": False, "virhe": tark["virhe"]}
    if _hex(str(raw["konekoodi"])) != kone:
        return {"voimassa": False, "virhe": "Lisenssi on aktivoitu toiselle koneelle (konekoodi ei täsmää)."}
    if int(raw["kesto"]) > 0:
        try:
            paattyy = dt.date.fromisoformat(str(raw["voimassa_paattyen"]))
        except ValueError:
            return {"voimassa": False, "virhe": "Voimassaolopäivä on virheellinen."}
        if dt.date.today() > paattyy:
            return {"voimassa": False, "vanhentunut": paattyy.isoformat(),
                    "virhe": "Lisenssi vanhentui {}.".format(paattyy.isoformat())}
    tulos = dict(raw)
    tulos.update({"voimassa": True, "nimi": tark["nimi"]})
    return tulos


