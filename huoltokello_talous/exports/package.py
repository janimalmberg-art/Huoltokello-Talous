"""Kirjanpitäjän kuukausipaketti: kaikki kauden aineisto yhteen ZIP-tiedostoon.

Sisältö:
  00_LUE_MINUT.txt              yhteenveto, tarkistuslista ja täsmäytys
  kirjanpito_<profiili>.csv     tiliöinnit valitun kirjanpito-ohjelman tuontimuodossa
  kirjanpito_yleinen.csv        sama aineisto kaikilla kentillä
  paivakirja.html/.csv, paakirja.html/.csv, tuloslaskelma.html, tase.html
  alv-laskelma.html/.csv        kausiveroilmoituksen myyntikentät
  myyntireskontra.html/.csv     avoimet laskut ja ikäjakauma kauden lopussa
  laskulista.csv, suoritukset.csv
  laskut/<nro>.html + <nro>.xml myyntilaskut tulostettavina ja Finvoice 3.0 -muodossa
  palkat/palkkakooste.html/.csv palkkarivit palkanlaskentaan (jos valittu)
  tyoajat.csv                   kauden työaikakirjaukset
  manifest.json                 SHA-256-tiivisteet kaikista tiedostoista (eheys)
"""
from __future__ import annotations

import calendar
import datetime as dt
import hashlib
import io
import json
import zipfile

from .. import db, invoicing, ledger, payroll, reports, settings, __version__
from ..money import fmt_eur
from . import finvoice, profiles


def month_bounds(period: str) -> tuple[str, str]:
    y, m = (int(x) for x in period.split("-"))
    return dt.date(y, m, 1).isoformat(), dt.date(y, m, calendar.monthrange(y, m)[1]).isoformat()


def previous_month(today: dt.date | None = None) -> str:
    t = today or dt.date.today()
    first = t.replace(day=1) - dt.timedelta(days=1)
    return first.strftime("%Y-%m")


def build_package(start: str, end: str, include_payroll: bool | None = None) -> tuple[bytes, str, dict]:
    s = settings.all_settings()
    include_payroll = s["export.include_payroll"] if include_payroll is None else include_payroll
    files: dict[str, bytes] = {}
    prof = s["export.profile"]
    data, _fname = profiles.journal_csv(start, end, prof, s.get("export.custom_profile") or "")
    files[f"kirjanpito_{prof}.csv"] = data
    if prof != "yleinen":
        files["kirjanpito_yleinen.csv"] = profiles.journal_csv(start, end, "yleinen")[0]
    files["paivakirja.html"] = reports.paivakirja_html(start, end).encode()
    files["paivakirja.csv"] = reports.paivakirja_csv(start, end)
    files["paakirja.html"] = reports.paakirja_html(start, end).encode()
    files["paakirja.csv"] = reports.paakirja_csv(start, end)
    files["tuloslaskelma.html"] = reports.tuloslaskelma_html(start, end).encode()
    files["tase.html"] = reports.tase_html(end).encode()
    files["alv-laskelma.html"] = reports.alv_html(start, end).encode()
    files["alv-laskelma.csv"] = reports.alv_csv(start, end)
    files["myyntireskontra.html"] = reports.reskontra_html(end).encode()
    files["myyntireskontra.csv"] = reports.reskontra_csv(end)
    files["laskulista.csv"] = reports.laskulista_csv(start, end)
    files["suoritukset.csv"] = reports.suoritukset_csv(start, end)
    files["tyoajat.csv"] = reports.worktime_csv(start, end)
    invs = db.q("SELECT id, number FROM invoices WHERE status <> 'luonnos' AND invoice_date BETWEEN ? AND ? "
                "ORDER BY number", (start, end))
    finvoice_errors = []
    for inv in invs:
        b = invoicing.invoice_bundle(inv["id"])
        files[f"laskut/{inv['number']}.html"] = reports.invoice_html(b).encode()
        try:
            files[f"laskut/{inv['number']}.xml"] = finvoice.build(b).encode("utf-8")
        except ValueError as exc:
            finvoice_errors.append(f"{inv['number']}: {exc}")
    pay = None
    if include_payroll:
        pay = payroll.build(start, end)
        files["palkat/palkkakooste.html"] = reports.payroll_html(pay).encode()
        files["palkat/palkkakooste.csv"] = reports.payroll_csv(pay)

    alv = ledger.alv_laskelma(start, end)
    tb = ledger.saldot(start, end)
    balanced = sum(tb.values()) == 0
    open_drafts = db.q1("SELECT COUNT(*) AS n FROM invoices WHERE status = 'luonnos'")["n"]
    unapproved = db.q1("SELECT COUNT(*) AS n FROM time_entries WHERE work_date BETWEEN ? AND ? "
                       "AND status IN ('luonnos','lahetetty')", (start, end))["n"]
    approved_uninvoiced = db.q1("SELECT COUNT(*) AS n FROM time_entries WHERE work_date BETWEEN ? AND ? "
                                "AND status = 'hyvaksytty' AND billable = 1 AND invoice_id IS NULL", (start, end))["n"]
    checks = [
        ("Kirjaukset täsmäävät (debet = kredit)", balanced),
        ("Ei laskuttamattomia hyväksyttyjä laskutettavia työaikoja", approved_uninvoiced == 0),
        ("Ei hyväksymättömiä työaikakirjauksia", unapproved == 0),
        ("Ei avoimia laskuluonnoksia", open_drafts == 0),
        ("Kausi lukittu kirjanpidossa", ledger.is_locked(end)),
        ("Finvoice-aineistot muodostuivat", not finvoice_errors),
    ]
    readme = [
        f"HUOLTOKELLO-TALOUS – KIRJANPITÄJÄN AINEISTO",
        f"Yritys: {s['company.name']} ({s['company.business_id']})",
        f"Kausi: {reports.fi(start)}–{reports.fi(end)}",
        f"Muodostettu: {dt.datetime.now().strftime('%d.%m.%Y %H:%M')} (Huoltokello-Talous {__version__})",
        f"Vientiprofiili: {profiles.get_profile(prof, s.get('export.custom_profile') or '')['label']}",
        "",
        f"Laskuja: {len(invs)}",
        f"Myynnin vero 25,5 %: {fmt_eur(alv['vero_255'])}",
        f"Myynnin vero 13,5 %: {fmt_eur(alv['vero_135'])}",
        f"Myynnin vero 10 %: {fmt_eur(alv['vero_10'])}",
        f"Rakentamispalvelujen myynti (käännetty ALV): {fmt_eur(alv['rakentamispalvelut'])}",
        f"Palvelumyynti EU: {fmt_eur(alv['eu_palvelut'])}",
        "",
        "TARKISTUSLISTA",
    ] + [f"  [{'OK' if ok else '!!'}] {txt}" for txt, ok in checks]
    if finvoice_errors:
        readme += ["", "Finvoice-virheet:"] + [f"  {x}" for x in finvoice_errors]
    if pay and pay["warnings"]:
        readme += ["", "Palkkakoosteen huomiot:"] + [f"  {w}" for w in pay["warnings"]]
    readme += ["", "Tiedostojen eheys: manifest.json sisältää SHA-256-tiivisteet.",
               "Tuo kirjanpito_<profiili>.csv kirjanpito-ohjelman tiliöintien tuonnilla;",
               "laskut/*.xml ovat Finvoice 3.0 -verkkolaskuja (myyntilaskujen arkisto / siirto)."]
    files["00_LUE_MINUT.txt"] = ("\r\n".join(readme) + "\r\n").encode("utf-8-sig")
    manifest = {"period": [start, end], "created": dt.datetime.now().isoformat(timespec="seconds"),
                "files": {name: hashlib.sha256(content).hexdigest() for name, content in sorted(files.items())}}
    files["manifest.json"] = json.dumps(manifest, indent=2, ensure_ascii=False).encode()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name in sorted(files):
            zi = zipfile.ZipInfo(name, date_time=dt.datetime.now().timetuple()[:6])
            zi.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(zi, files[name])
    company = "".join(ch for ch in (s["company.name"] or "yritys") if ch.isalnum())[:30] or "yritys"
    fname = f"Kirjanpitoaineisto_{company}_{start}_{end}.zip"
    data_hashes = {k: v for k, v in manifest["files"].items() if k != "00_LUE_MINUT.txt"}
    content_key = hashlib.sha256(json.dumps(data_hashes, sort_keys=True).encode()).hexdigest()
    return buf.getvalue(), fname, {"checks": checks, "invoices": len(invs), "manifest": manifest,
                                   "content_key": content_key}
