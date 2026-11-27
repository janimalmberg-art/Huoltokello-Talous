"""Kokeilujakson hallinta (rajoitettu, 90 päivää toimiva versio).

Tämä tiedosto kuuluu Huoltokello-Talous KOKEILU-versioon. Kokeilujakso
alkaa ohjelman ensimmäisestä käynnistyksestä ja kestaa 90 päivää.
Alkupäivä kirjataan datakansioon tiedostoon kokeilu.json varmistekentällä
(SHA-256), jota ei ole tarkoitettu käsin muokattavaksi.

Kokeilujakson aikana ohjelma toimii täysin. Kun jakso päättyy, ohjelma
näyttää käynnistyksessä ruudun keskellä ikkunan, jossa lukee
"Kokeilujaksosi on päättynyt" sekä kehittäjän yhteystiedot; käynnistys
estetään (palvelinkomento palauttaa poistumiskoodin 2). Ohjelman täyden
version ja lisenssien voi pyytää kehittäjältä sähköpostitse.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os

KOKEILU_PAIVAA = 90

# Kehittäjätiedot, jotka näytetään kokeiluikkunoissa.
KEHITTJA_NIMI = "Jani Malmberg"
KEHITTJA_ROOLI = "ohjelman kehittäjä"
KEHITTJA_SAHKOPOSTI = "jani.malmberg@gmail.com"

# Ympäristömuuttuja HKT_KOKEILU_EI_IKKUNAA=1 ohittaa graafisen ikkunan
# (automatisoidut testit ja ajot ilman näyttöä).
_EI_IKKUNAA = "HKT_KOKEILU_EI_IKKUNAA"

_SUOLA = "Huoltokello-Talous-kokeilujakso-v1"


def _markkeripolku(data_dir: str) -> str:
    return os.path.join(data_dir, "kokeilu.json")


def _tarkiste(alku: str) -> str:
    """Muokkaussuojan tarkiste alkupäivämerkinnälle (ei salakirjoitus)."""
    return hashlib.sha256((_SUOLA + "|kokeilu|" + alku).encode("utf-8")).hexdigest()[:32]


def hae_tila(data_dir: str | None = None) -> dict:
    """Palauttaa kokeilujakson tilan ja kirjaa alkupäivän ensimmäisellä ajolla.

    Palautettava sanakirja:
      alku            ISO-merkkijono "YYYY-MM-DD"
      paivia_jaljella int (<= 0 tarkoittaa päättynyttä jaksoa)
      paattynyt       bool
      uusi            bool (True vain ensimmäisellä käynnistyksellä)
    """
    if data_dir is None:
        from . import app_context
        data_dir = app_context.data_dir()

    polku = _markkeripolku(data_dir)
    alkupm = ""
    merkki_rikki = False
    if os.path.isfile(polku):
        try:
            with open(polku, encoding="utf-8") as fh:
                raw = json.load(fh)
            ehdokas = str(raw.get("alku", ""))
            if raw.get("varmiste") == _tarkiste(ehdokas):
                alkupm = dt.date.fromisoformat(ehdokas).isoformat()
            else:
                merkki_rikki = True
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            merkki_rikki = True

    if merkki_rikki:
        # Merkkiä on muokattu tai se on vioittunut: kokeilua ei nollata vaan
        # käynnistys estetään (fail-closed).
        return {"alku": "", "paivia_jaljella": 0, "paattynyt": True, "uusi": False, "rikki": True}

    uusi = False
    if not alkupm:
        # Ensimmäinen käynnistys (tai muokattu/poistettu merkki): alkaa tänään.
        alkupm = dt.date.today().isoformat()
        uusi = True
        payload = {"kokeilujakso": KOKEILU_PAIVAA, "alku": alkupm, "varmiste": _tarkiste(alkupm)}
        tmp = polku + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, sort_keys=True)
        os.replace(tmp, polku)

    tanaan = dt.date.today()
    alku = dt.date.fromisoformat(alkupm)
    if alku > tanaan:
        # Kello liikutettu taaksepäin: ei rankaista, jakso laskee alusta.
        paivia_jaljella = KOKEILU_PAIVAA
    else:
        paivia_jaljella = KOKEILU_PAIVAA - (tanaan - alku).days
    return {
        "alku": alkupm,
        "paivia_jaljella": paivia_jaljella,
        "paattynyt": paivia_jaljella <= 0,
        "uusi": uusi,
        "rikki": False,
    }


# ------------------------------------------------------------------ tekstit

def kehittaja_rivit() -> list:
    return [
        "{} - {}".format(KEHITTJA_NIMI, KEHITTJA_ROOLI),
        "Sähköposti: {}".format(KEHITTJA_SAHKOPOSTI),
    ]


def paattynyt_rivit(tila: dict | None = None) -> list:
    """Tekstit, jotka näytetään kokeilujakson päätyttyä (ikkuna + konsoli)."""
    rivit = [
        "HUOLTOKELLO-TALOUS - KOKEILUVERSIO (90 päivää)",
        "",
        "Kokeilujaksosi on päättynyt.",
    ]
    if tila and tila.get("rikki"):
        rivit.append("Kokeilumerkin tarkistus epäonnistui (merkkiä on muutettu).")
    rivit.extend([
        "Ohjelma ei käynnisty enää kokeilujakson puitteissa.",
        "",
        *kehittaja_rivit(),
        "",
        "Ohjelman täyden version voi pyytää kehittäjältä sähköpostitse.",
        "Ohjelman lisensseistä voi kysyä kehittäjältä.",
    ])
    return rivit


def tervehdys_rivit(tila: dict) -> list:
    return [
        "HUOLTOKELLO-TALOUS - KOKEILUVERSIO (90 päivää)",
        "",
        "Kokeilujakso alkoi {}. Kokeiluaikaa jäljellä: {} päivää.".format(
            tila.get("alku", ""), KOKEILU_PAIVAA),
        "",
        *kehittaja_rivit(),
        "",
        "Kokeilujakson jälkeen ohjelma ilmoittaa: \"Kokeilujaksosi on päättynyt.\"",
        "Ohjelman täyden version voi pyytää kehittäjältä sähköpostitse.",
        "Ohjelman lisensseistä voi kysyä kehittäjältä.",
    ]


# ------------------------------------------------------------------ konsoli

def tulosta_tervehtiminen(tila: dict) -> None:
    for rivi in tervehdys_rivit(tila):
        print(rivi)
    print("")


def tulosta_paattymisilmoitus(tila: dict | None = None) -> None:
    for rivi in paattynyt_rivit(tila):
        print(rivi)
    print("")


def tulosta_tilarivi(tila: dict) -> None:
    paivia = int(tila.get("paivia_jaljella", 0))
    if paivia <= 0:
        print("Kokeilujakso: päättynyt.")
    else:
        print("Kokeilujakso (rajoitettu versio): {} päivää jäljellä (90 päivän kokeilujakso).".format(paivia))


# ------------------------------------------------------------------ ikkunat

def _nayta_ikkuna(otsikko: str, rivit: list) -> None:
    """Näyttää ruudun keskellä ikkunan; ei mitään jos näyttöä tai tkinteriä ei ole."""
    if os.environ.get(_EI_IKKUNAA):
        return
    try:
        import tkinter as tk

        root = tk.Tk()
        root.title(otsikko)
        root.configure(bg="#f6f7f9")
        root.attributes("-topmost", True)
        root.resizable(False, False)

        frm = tk.Frame(root, bg="#f6f7f9", padx=30, pady=24)
        frm.pack(expand=True, fill="both")

        for i, rivi in enumerate(rivit):
            if rivi == "":
                tk.Frame(frm, height=10, bg="#f6f7f9").pack()
                continue
            bold = i == 0
            koko = 12 if bold else 10
            tk.Label(
                frm, text=rivi, bg="#f6f7f9",
                font=("Segoe UI", koko, "bold" if bold else "normal"),
                justify="center", wraplength=430,
            ).pack(fill="x")

        ok = tk.Button(frm, text="OK", width=14, command=root.destroy,
                       font=("Segoe UI", 10, "bold"))
        ok.pack(pady=(18, 2))
        ok.focus_set()
        root.protocol("WM_DELETE_WINDOW", root.destroy)

        root.update_idletasks()
        lev = root.winfo_reqwidth() + 6
        kor = root.winfo_reqheight() + 6
        sw = max(0, root.winfo_screenwidth())
        sh = max(0, root.winfo_screenheight())
        root.geometry("+{}+{}".format(max(0, (sw - lev) // 2), max(0, (sh - kor) // 2)))
        root.mainloop()
    except Exception:  # noqa: BLE001 - ikkuna on best effort; konsolitekstit jäävät
        return


def ikkuna_paattynyt(tila: dict | None = None) -> None:
    """Ruudun keskellä ikkuna: kokeilujakso päättynyt + kehittäjän tiedot."""
    _nayta_ikkuna("Huoltokello-Talous - kokeilujakso päättynyt", paattynyt_rivit(tila))


def ikkuna_tervetuloa(tila: dict) -> None:
    """Ensimmäisen käynnistyksen tervetuloikkuna (ruudun keskellä)."""
    _nayta_ikkuna("Huoltokello-Talous - kokeilujakso alkoi", tervehdys_rivit(tila))

