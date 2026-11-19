"""Kirjanpitoaineiston vientiprofiilit suomalaisiin kirjanpito-ohjelmiin.

Kaikki Suomessa yleiset kirjanpito-ohjelmat ottavat vastaan tiliöintiaineistoa
(päiväkirjarivit) CSV-tiedostona ja myyntilaskuja Finvoice-verkkolaskuina.
Profiili määrää sarakkeet, erottimen, desimaalimerkin, päivämäärämuodon ja merkistön.

TÄRKEÄÄ: Ohjelmatoimittajat muuttavat tuontipohjiaan. Jokainen profiili on rakennettu
ohjelman yleisen kirjanpitotietojen CSV-tuonnin mukaiseksi, mutta tarkista sarakkeet
ohjelman ajantasaisesta tuontiohjeesta ennen tuotantokäyttöä. Netvisorissa ja
Procountorissa sarakkeet kohdistetaan tuontivaiheessa, joten nimetyt sarakkeet riittävät.
Poikkeavan pohjan voi määrittää profiililla "mukautettu" (Asetukset > Vienti).
"""
from __future__ import annotations

import csv
import io
import json
from decimal import Decimal

from ..ledger import journal_rows
from ..money import D

# Netvisorin ALV-luokat myynnille (tarkista Netvisorin ALV-koodilistasta)
NETVISOR_VAT = {"ALV25.5": "KOMY", "ALV13.5": "KOMY", "ALV10": "KOMY", "RAK": "RAMY", "EUPAL": "EUPM309",
                "ALV0": "NONE", "EIALV": "NONE", "": "NONE"}

FIELDS = {
    "date": "Päivämäärä",
    "voucher": "Tositenumero",
    "series": "Tositelaji",
    "number": "Tositenumero (luku)",
    "description": "Selite",
    "account": "Tili",
    "account_name": "Tilin nimi",
    "debit": "Debet",
    "credit": "Kredit",
    "amount": "Summa",
    "vat_code": "ALV-koodi",
    "vat_pct": "ALV-%",
    "vat_base": "ALV-peruste",
    "netvisor_vat": "ALV-luokka",
    "cost_center": "Kustannuspaikka",
}

PROFILES: dict[str, dict] = {
    "yleinen": {
        "label": "Yleinen päiväkirja-CSV (kaikki kentät, UTF-8)",
        "delimiter": ";", "decimal": ",", "date_fmt": "%d.%m.%Y", "encoding": "utf-8-sig", "header": True,
        "columns": ["date", "voucher", "series", "description", "account", "account_name", "debit", "credit",
                    "amount", "vat_code", "vat_pct", "vat_base", "cost_center"],
    },
    "netvisor": {
        "label": "Visma Netvisor – Kirjanpitotietojen tuonti (CSV, luetteloerotin)",
        "delimiter": ";", "decimal": ",", "date_fmt": "%d.%m.%Y", "encoding": "cp1252", "header": True,
        "columns": ["date", "voucher", "description", "account", "amount", "netvisor_vat", "vat_pct",
                    "cost_center"],
        "headers": {"date": "Tositepäivä", "voucher": "Tositenumero", "description": "Selite",
                    "account": "Tilinumero", "amount": "Summa", "netvisor_vat": "ALV-koodi",
                    "vat_pct": "ALV-prosentti", "cost_center": "Laskentakohde"},
    },
    "procountor": {
        "label": "Procountor (Finago) – Tiliöintitietojen tuonti (CSV)",
        "delimiter": ";", "decimal": ",", "date_fmt": "%d.%m.%Y", "encoding": "utf-8-sig", "header": True,
        "columns": ["date", "voucher", "description", "account", "debit", "credit", "vat_pct", "vat_code",
                    "cost_center"],
        "headers": {"date": "Kirjauspäivä", "voucher": "Tositenumero", "description": "Selite",
                    "account": "Tili", "debit": "Debet", "credit": "Kredit", "vat_pct": "ALV-%",
                    "vat_code": "ALV-status", "cost_center": "Dimensio"},
    },
    "fennoa": {
        "label": "Fennoa – Kirjausten tuonti (CSV)",
        "delimiter": ";", "decimal": ",", "date_fmt": "%Y-%m-%d", "encoding": "utf-8-sig", "header": True,
        "columns": ["date", "voucher", "description", "account", "debit", "credit", "vat_code", "vat_pct",
                    "cost_center"],
    },
    "visma_eekonomi": {
        "label": "Visma eEkonomi / eAccounting – Tositetuonti (CSV)",
        "delimiter": ";", "decimal": ",", "date_fmt": "%Y-%m-%d", "encoding": "utf-8-sig", "header": True,
        "columns": ["voucher", "date", "description", "account", "debit", "credit", "vat_code", "cost_center"],
    },
    "visma_fivaldi": {
        "label": "Visma Fivaldi – Kirjanpitoaineiston tuonti (CSV)",
        "delimiter": ";", "decimal": ",", "date_fmt": "%d.%m.%Y", "encoding": "cp1252", "header": True,
        "columns": ["date", "voucher", "account", "amount", "vat_code", "vat_pct", "description", "cost_center"],
    },
    "heeros": {
        "label": "Heeros – Tiliöintien tuonti (CSV)",
        "delimiter": ";", "decimal": ",", "date_fmt": "%d.%m.%Y", "encoding": "utf-8-sig", "header": True,
        "columns": ["date", "voucher", "account", "debit", "credit", "vat_code", "vat_pct", "description",
                    "cost_center"],
    },
    "lemonsoft": {
        "label": "Lemonsoft – Kirjanpitoviennit (CSV)",
        "delimiter": ";", "decimal": ",", "date_fmt": "%d.%m.%Y", "encoding": "cp1252", "header": True,
        "columns": ["voucher", "date", "account", "debit", "credit", "description", "vat_code", "cost_center"],
    },
    "tikon": {
        "label": "Tikon – Tositesiirto (CSV)",
        "delimiter": ";", "decimal": ",", "date_fmt": "%d.%m.%Y", "encoding": "cp1252", "header": False,
        "columns": ["series", "number", "date", "account", "amount", "description", "vat_code", "cost_center"],
    },
    "asteri": {
        "label": "Asteri Kirjanpito – Tositteiden tuonti (CSV)",
        "delimiter": ";", "decimal": ",", "date_fmt": "%d.%m.%Y", "encoding": "cp1252", "header": False,
        "columns": ["number", "date", "account", "amount", "description", "vat_code"],
    },
    "kitsas": {
        "label": "Kitsas – Kirjausten tuonti (CSV)",
        "delimiter": ";", "decimal": ",", "date_fmt": "%d.%m.%Y", "encoding": "utf-8-sig", "header": True,
        "columns": ["date", "voucher", "account", "debit", "credit", "description", "vat_code", "vat_pct",
                    "cost_center"],
    },
    "merit": {
        "label": "Passeli Merit – Kirjanpitotapahtumien tuonti (CSV)",
        "delimiter": ";", "decimal": ",", "date_fmt": "%d.%m.%Y", "encoding": "utf-8-sig", "header": True,
        "columns": ["date", "voucher", "account", "debit", "credit", "description", "vat_code", "cost_center"],
    },
    "talenom": {
        "label": "Talenom / Accountor / tilitoimisto – yleinen päiväkirja (CSV)",
        "delimiter": ";", "decimal": ",", "date_fmt": "%d.%m.%Y", "encoding": "utf-8-sig", "header": True,
        "columns": ["date", "voucher", "description", "account", "account_name", "debit", "credit", "vat_code",
                    "vat_pct", "vat_base", "cost_center"],
    },
    "excel": {
        "label": "Microsoft Excel / Google Sheets (CSV, UTF-8)",
        "delimiter": ";", "decimal": ",", "date_fmt": "%d.%m.%Y", "encoding": "utf-8-sig", "header": True,
        "columns": ["date", "voucher", "description", "account", "account_name", "debit", "credit", "vat_code",
                    "vat_pct", "vat_base", "cost_center"],
    },
}


def get_profile(name: str, custom_json: str = "") -> dict:
    if name == "mukautettu":
        if not custom_json:
            raise ValueError("Mukautettu profiili on määrittämättä (Asetukset > Vienti).")
        prof = json.loads(custom_json)
        unknown = [c for c in prof.get("columns", []) if c not in FIELDS]
        if unknown or not prof.get("columns"):
            raise ValueError(f"Mukautetussa profiilissa tuntemattomia sarakkeita: {unknown}")
        prof.setdefault("label", "Mukautettu")
        prof.setdefault("delimiter", ";")
        prof.setdefault("decimal", ",")
        prof.setdefault("date_fmt", "%d.%m.%Y")
        prof.setdefault("encoding", "utf-8-sig")
        prof.setdefault("header", True)
        return prof
    if name not in PROFILES:
        raise ValueError(f"Tuntematon vientiprofiili: {name}")
    return PROFILES[name]


def _num(v: Decimal, decimal: str) -> str:
    s = f"{D(v):.2f}"
    return s.replace(".", decimal)


def _safe_text(s: str) -> str:
    """CSV-kaavainjektion esto (Excel/LibreOffice): kaavamerkillä alkava solu tekstiksi."""
    s = (s or "").replace("\r", " ").replace("\n", " ")
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t") else s


def journal_csv(start: str, end: str, profile_name: str, custom_json: str = "") -> tuple[bytes, str]:
    import datetime as dt
    prof = get_profile(profile_name, custom_json)
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=prof["delimiter"], quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    headers = prof.get("headers", {})
    if prof["header"]:
        w.writerow([headers.get(c, FIELDS[c]) for c in prof["columns"]])
    for r in journal_rows(start, end):
        debit, credit = D(r["debit"]), D(r["credit"])
        vals = {
            "date": dt.date.fromisoformat(r["entry_date"]).strftime(prof["date_fmt"]),
            "voucher": f"{r['series']}{r['number']}",
            "series": r["series"],
            "number": str(r["number"]),
            "description": _safe_text(r["description"]),
            "account": r["account"],
            "account_name": _safe_text(r["account_name"]),
            "debit": _num(debit, prof["decimal"]) if debit else "",
            "credit": _num(credit, prof["decimal"]) if credit else "",
            "amount": _num(debit - credit, prof["decimal"]),
            "vat_code": r["vat_code"],
            "vat_pct": (r["vat_rate"] or "").replace(".", prof["decimal"]),
            "vat_base": _num(D(r["vat_base"]), prof["decimal"]) if r["vat_code"] else "",
            "netvisor_vat": NETVISOR_VAT.get(r["vat_code"], "NONE"),
            "cost_center": _safe_text(r["cost_center"]),
        }
        w.writerow([vals[c] for c in prof["columns"]])
    data = buf.getvalue().encode(prof["encoding"], errors="replace")
    fname = f"kirjanpito_{profile_name}_{start}_{end}.csv"
    return data, fname


def rows_to_csv(header: list[str], rows: list[list], encoding: str = "utf-8-sig") -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    w.writerow(header)
    for r in rows:
        w.writerow([_safe_text(str(x)) if isinstance(x, str) else (f"{x}".replace(".", ",")
                    if isinstance(x, Decimal) else x) for x in r])
    return buf.getvalue().encode(encoding, errors="replace")
