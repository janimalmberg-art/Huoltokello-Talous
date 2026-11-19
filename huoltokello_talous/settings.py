"""Sovellusasetukset (tietokannan settings-taulu) ja oletusarvot.

Lukuarvot 2026 on tarkistettu viranomaislähteistä (ks. docs/Lahteet.md).
Ne ovat oletuksia: johto voi muuttaa ne Asetukset-sivulla, kun arvot vaihtuvat
(esim. vuodenvaihde, viitekoron puolivuotiskausi, oma TyEL-prosentti).
"""
from __future__ import annotations

import json

from . import db

DEFAULTS: dict[str, object] = {
    # Yritys
    "company.name": "",
    "company.business_id": "",
    "company.vat_registered": True,
    "company.street": "",
    "company.postcode": "",
    "company.city": "",
    "company.phone": "",
    "company.email": "",
    "company.iban": "",
    "company.bic": "",
    "company.einvoice_address": "",
    "company.einvoice_operator": "",
    "company.domicile": "",
    # Laskutus
    "invoice.next_number": 1000,
    "invoice.payment_term_days": 14,
    "invoice.penalty_interest": "10.5",       # kaupallinen viivästyskorko 1.7.–31.12.2026 (viitekorko 2,5 % + 8)
    "invoice.reminder_fee": "5.00",           # muistutusmaksu (perintälain rajat huomioitava kuluttajilla)
    "invoice.use_rf_reference": False,
    "invoice.footer": "",
    "invoice.next_quote_number": 1,
    # Työaika ja palkka (Työaikalaki 872/2019; TES voi määrätä toisin)
    "work.daily_regular_hours": "8",
    "work.weekly_regular_hours": "40",
    "work.daily_ot_first_hours": "2",         # ensimmäiset 2 ylityötuntia +50 %, sen jälkeen +100 %
    "work.evening_start": "18:00",
    "work.evening_end": "22:00",
    "work.night_start": "22:00",
    "work.night_end": "06:00",
    "work.evening_bonus_eur": "0",            # TES-kohtainen €/h
    "work.night_bonus_eur": "0",              # TES-kohtainen €/h
    "work.saturday_bonus_eur": "0",           # TES-kohtainen €/h
    "work.sunday_pct": "100",                 # sunnuntaityö (TAL 16 §) +100 %
    "work.holiday_as_sunday": True,           # arkipyhät kuten sunnuntai (tarkista TES)
    "work.km_rate": "0.55",                   # verovapaa km-korvaus 2026, auto
    "work.allowance_partial": "25.00",        # kotimaan osapäiväraha 2026
    "work.allowance_full": "54.00",           # kotimaan kokopäiväraha 2026
    # Hinnoittelu (johto)
    "pricing.statutory_pct": "20.1",          # TyEL ~17,10 + sotu 1,91 + tyött. 0,31 + tapat./ryhmähenki ~0,8
    "pricing.indirect_pct": "18.0",           # lomapalkat, lomarahat, pyhät, sairauspoissaolot (arvio)
    "pricing.billable_ratio_pct": "75",       # laskutettavuusaste
    "pricing.overhead_per_hour": "0",         # yleiskulut €/laskutettava tunti
    "pricing.target_margin_pct": "25",        # tavoitekate myyntihinnasta
    # Kirjanpito: tilikartta-kytkennät
    "acc.receivables": "1700",
    "acc.bank": "1910",
    "acc.vat_payable": "2939",
    "acc.rounding": "8590",
    "acc.penalty_interest": "9160",
    "acc.reminder_fees": "3990",
    "acc.revenue_rc": "3030",                 # rakentamispalvelujen myynti (käännetty verovelvollisuus)
    "acc.revenue_eu_services": "3040",
    "acc.revenue_zero": "3050",
    "acc.wages": "5000",
    "acc.pension": "6130",
    "acc.social_security": "6410",
    "acc.other_side_costs": "6300",
    "acc.travel": "7700",
    "acc.accrued_liabilities": "2930",
    # Vienti kirjanpitoon
    "export.profile": "netvisor",
    "export.custom_profile": "",
    "export.transport": "kansio",             # kansio | sahkoposti | https | netvisor_api | procountor_api
    "export.folder": "",
    "export.email_to": "",
    "export.https_url": "",
    "export.netvisor_env": "test",
    "export.procountor_env": "test",
    "export.auto_enabled": False,
    "export.auto_day": 5,                     # kuukauden päivä, jolloin edellisen kuun paketti muodostetaan
    "export.include_payroll": True,
    "export.api_live": False,                 # rajapintasiirto tuotannossa (oletus: koeajo)
    # SMTP (vain lähetykseen; salasana salattuna secrets-tiedostossa)
    "smtp.host": "",
    "smtp.port": 587,
    "smtp.user": "",
    "smtp.sender": "",
    # Huoltokello-silta (luku vain lukutilassa Huoltokellon tietokannasta)
    "hk.db_path": "",                         # tyhjä = %LOCALAPPDATA%\\Huoltokello\\AppData\\huoltokello.db
    "hk.enabled": False,                      # automaattinen synkronointi
    "hk.interval_minutes": 15,
    "hk.import_hours": True,                  # kuittausten työtunnit -> työajat (hyväksyttäviksi)
    "hk.import_materials": True,
    "hk.start_date": "",                      # tuo kuittaukset tästä päivästä alkaen (tyhjä = kuluvan kuun alku)
    "hk.timezone": "auto",                    # auto | utc | paikallinen (Huoltokellon aikaleimojen muoto)
    "hk.last_sync": "",
    # Turvallisuus
    "security.session_hours": 10,
    "security.max_failed_logins": 5,
    "security.lockout_minutes": 15,
    "security.min_password_length": 12,
}


def get(key: str, default=None):
    row = db.q1("SELECT value FROM settings WHERE key = ?", (key,))
    if row is None:
        return DEFAULTS.get(key, default) if default is None else default
    return json.loads(row["value"])


def get_str(key: str) -> str:
    v = get(key)
    return "" if v is None else str(v)


def set(key: str, value) -> None:  # noqa: A001 - tarkoituksellinen nimi
    db.ex("INSERT INTO settings(key, value) VALUES(?, ?) "
          "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
          (key, json.dumps(value, ensure_ascii=False)))


def all_settings() -> dict:
    out = dict(DEFAULTS)
    for row in db.q("SELECT key, value FROM settings"):
        out[row["key"]] = json.loads(row["value"])
    return out


def next_sequence(key: str) -> int:
    """Palauttaa juoksevan numeron ja kasvattaa sitä (kutsuttava transaktion sisällä)."""
    current = int(get(key))
    set(key, current + 1)
    return current
