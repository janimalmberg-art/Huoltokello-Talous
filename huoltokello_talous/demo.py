"""Demodata Huoltokello-Taloukseen: realistinen esittelyaineisto tyhjään tietokantaan.

Lataa yritystiedot, hakemistot (työlajit, asiakkaat, työntekijät, tarvikkeet),
työaika- ja tarvikekirjauksia kahteen edelliseen kuukauteen ja kuluvaksi
kuukaudeksi, tarjouksia, laskuja eri tiloissa (maksettu, osittain maksettu,
erääntynyt + maksumuistutus, hyvitetty, luonnos), palkkajaksot (lukittu +
luonnos) sekä demo-käyttäjät kaikille rooleille.

Tärkeää: aineisto luodaan moduulien julkisilla funktioilla (timesheet,
invoicing, pricing, payroll), jolloin laskunumerointi, ALV-koodit, viiten-
numerot ja tiliöinnit noudattavat samaa logiikkaa kuin oikeassa käytössä.
Vahvistetut laskut ja tositteet muuttuvat muuttumattomiksi kuten normaalisti.

Käyttö:
    python -m huoltokello_talous demodata

Vain TYHJÄÄN tietokantaan (ei laskuja, työaikoja, asiakkaita eikä käyttäjiä).
Aineisto on kuvitteellista (Y-tunnukset generoitu tarkistussummaltaan kelvolliseksi,
mutta ei todellisille yrityksille). Älä käytä demo-tunnisteita todellisessä
laskutuksessa samassa tietokannassa.
"""
from __future__ import annotations

import datetime as dt
import json
import random
from decimal import Decimal, ROUND_HALF_UP

from . import (db, finref, invoicing, ledger, payroll, pricing, security, settings,  # noqa: F401 (ledger: varmistukset)
               timesheet)
from .security import iso, utcnow

FLAG = "demo.seeded"


class DemoError(Exception):
    """Demodataa ei voi ladata (tietokanta ei ole tyhjä tai se on jo ladattu)."""


# ------------------------------------------------------------------ aineisto

# Salasanat täyttävät politiikan (>= 12 merkkiä, 3 merkkiluokkaa; eivät sisällä
# tunnusta eikä nimeä). Näytetään/narataan vain latauksen yhteydessä.
PASSWORDS = {
    "johto": "PaaKayttaja2026#kx",
    "tyonjohto": "Esimies-Talous2026#kx",
    "kirjanpitaja": "Kirjanpito2026#kx",
    "tekijat": "Taloudellinen2026#",
}

USERS = [
    # (email, nimi, rooli, salasana-avain, tyontekijanro)
    ("johto@huolto.fi", "Pääjohto Huolto", "johto", "johto", ""),
    ("tyonjohto@huolto.fi", "Työnjohto Huolto", "tyonjohto", "tyonjohto", "T-099"),
    ("kirjanpitaja@huolto.fi", "Kirjanpitäjä Huolto", "kirjanpitaja", "kirjanpitaja", ""),
    ("aapo@huolto.fi", "Aapo Aalto", "tyontekija", "aapo", "T-101"),
    ("bea@huolto.fi", "Bea Berg", "tyontekija", "bea", "T-102"),
    ("cem@huolto.fi", "Cem Cantas", "tyontekija", "cem", "T-103"),
    ("dani@huolto.fi", "Dani Virtanen", "tyontekija", "dani", "T-104"),
    ("tyonjohto2@huolto.fi", "Matti Meikäläinen", "tyonjohto", "tyonjohto", ""),
]

EMPLOYEES = [
    # (nro, nimi, palkkatyyppi, tuntipalkka, kuukausipalkka)
    ("T-099", "Työnjohto Huolto", "tunti", "21.40", ""),
    ("T-101", "Aapo Aalto", "tunti", "14.50", ""),
    ("T-102", "Bea Berg", "tunti", "15.20", ""),
    ("T-103", "Cem Cantas", "tunti", "14.80", ""),
    ("T-104", "Dani Virtanen", "tunti", "15.60", ""),
    ("T-098", "Matti Meikäläinen", "kk", "", "3600"),
]

WORK_TYPES = [
    # (koodi, nimi, yksikkö, hinta, minveloitus, pyöristys, alv, tili, rakentamispalvelu, kotitalous, laskutettava)
    ("KI-001", "Kiinteistönhoito", "h", "48.50", 30, 15, "25.5", "3000", 0, 1, 1),
    ("LU-002", "Lumityö ja hiekoitus", "h", "52.40", 30, 15, "25.5", "3000", 0, 1, 1),
    ("PI-003", "Pihatyöt ja viheralueet", "h", "45.20", 30, 15, "25.5", "3000", 0, 1, 1),
    ("TA-004", "Toimintakyvyn tarkastus", "h", "58.00", 30, 15, "25.5", "3000", 0, 1, 1),
    ("SI-005", "Siivous", "h", "41.30", 30, 15, "25.5", "3000", 0, 1, 1),
    ("RU-006", "Rakentamispalvelut", "h", "62.00", 60, 15, "25.5", "3000", 1, 0, 1),
]

CUSTOMERS = [
    # (nro, nimi, ytu-siemen, kuluttaja, reverse_charge, osoite, postinro, kaupunki, s-posti)
    ("1001", "Aurinkorannikko As Oy", 112203, 0, 0, "Rantatie 14", "70820", "Kuopio", "taloyhtio@aurinkorannikko.demo"),
    ("1002", "Mäki-Matti Ky", 0, 1, 0, "Kauppakatu 25", "40100", "Jyväskylä", "maki.matti@pp.demo"),
    ("1003", "Joroisten Kaupunki", 246810, 0, 1, "Joroistentie 1", "78800", "Joroinen", "rakennuttamo@joroinen.demo"),
    ("1004", "Koivurinne Oy", 145670, 0, 0, "Teollisuustie 5", "70460", "Kuopio", "osto@koivurinne.demo"),
    ("1005", "Talviharju Taloyhtiö", 275640, 0, 0, "Harjutie 9", "78850", "Vaskivesi", "talviharju@vaskivesi.demo"),
]

CUSTOMER_PRICES = [
    # (asiakas-nro, tyolaji-koodi, hinta)
    ("1001", "KI-001", "51.50"),
    ("1002", "PI-003", "43.80"),
    ("1003", "SI-005", "39.90"),
]

MATERIALS = [
    # (koodi, nimi, yksikko, hankinta, myynti, alv, tili)
    ("TAR-001", "Hiekoitushiekka, säkki 20 kg", "kpl", "4.20", "6.50", "25.5", "3100"),
    ("TAR-002", "Ketjuöljy 1 l", "kpl", "6.80", "9.90", "25.5", "3100"),
    ("TAR-003", "Lumihaara 50 cm", "kpl", "12.00", "19.50", "25.5", "3100"),
    ("TAR-004", "Kevätkukkateline", "kpl", "15.00", "24.00", "25.5", "3100"),
]

SITES = {
    "1001": ["Aurinkorannikko, talo A", "Aurinkorannikko, talo B", "Aurinkorannikko, laituri"],
    "1002": ["Mäki-Matin pihapiiri", "Mäki-Matti, autokatokset"],
    "1003": ["Joroisten kaupungintalo", "Joroisten koulu", "Joroisten urheilukenttä"],
    "1004": ["Koivurinteen toimisto", "Koivurinteen varasto"],
    "1005": ["Talviharju, rakennus ja piha", "Talviharju, rappukäytävät"],
}
_TYO_TEKSTIT = [
    "Yleisalueiden hoito ja siivous", "Roskakatokset ja pihatilan siivous",
    "Porras- ja käytäväsiivous", "Lumityöt ja hiekoitus",
    "Piha-alueen kunnossapito", "Tarkastuskierros, vikailmoitukset",
    "Ikkunanpesu, sisäovet ja pintapuhtaus", "Rakennustyömaan loppusiivous",
    "Kiinteistön vastaanottokierros asiakkaan kanssa", "Harvennushakkuun jälkisiivous",
    "Suursiivous, keittiö ja varastotilat", "Aulakukat: hoito ja vaihto",
]
_MATERIAL_TEKSTIT = {
    "TAR-001": "Hiekoitushiekkaa pihaan ja porraskäytäviin",
    "TAR-002": "Ketjuöljy lumityökaluihin",
    "TAR-003": "Uusi lumihaara, raskasmalli",
    "TAR-004": "Kukkateline eteiseen",
}


# ------------------------------------------------------------------ apurit

def on_seeded() -> bool:
    """Onko demodata ladattu tähän tietokantaan."""
    return bool(settings.get(FLAG))


def _add_months(d: dt.date, months: int) -> dt.date:
    y, m = d.year + (d.month - 1 + months) // 12, (d.month - 1 + months) % 12 + 1
    return d.replace(year=y, month=m, day=1)


def _month_end(d: dt.date) -> dt.date:
    return (_add_months(d.replace(day=1), 1)) - dt.timedelta(days=1)


def _workdays(start: dt.date, end: dt.date):
    d = start
    while d <= end:
        if d.weekday() < 5:  # ma–pe
            yield d
        d += dt.timedelta(days=1)


def _pvm(days_offset: int) -> str:
    """Päivämäärä ISO-tekstinä suhteessa tähän päivään."""
    return (dt.date.today() + dt.timedelta(days=days_offset)).isoformat()


def _valid_ytunnus(seed: int) -> str:
    """Etsii seed-alueelta tarkistussummaltaan kelvollisen Y-tunnuksen (demo)."""
    for n in range(seed, seed + 240):
        for c in range(10):
            yt = f"{n:07d}-{c}"
            if finref.tarkista_ytunnus(yt):
                return yt
    raise DemoError("Y-tunnuksen generointi epäonnistui (demo.py: tarkista siementen alue).")


PASSWORD_SUFFIX = {"aapo": "A", "bea": "B", "cem": "C", "dani": "D"}


def _password_for(ukey: str) -> str:
    base = PASSWORDS.get(ukey) or PASSWORDS["tekijat"]
    return base + PASSWORD_SUFFIX.get(ukey, "")


# ------------------------------------------------------------------ siemenosat

def seed_company() -> str:
    """Asettaa demoyrityksen tiedot (laskujen vahvistus vaatii nimen/Y-tunnuksen/IBAN:n)."""
    yt = _valid_ytunnus(241804)
    for key, value in {
        "company.name": "Huoltokello Talous Demo Oy",
        "company.business_id": yt,
        "company.vat_registered": True,
        "company.street": "Nursinmäentie 12",
        "company.postcode": "78900",
        "company.city": "Joroinen",
        "company.phone": "044 555 0100",
        "company.email": "toimisto@demo-huoltokello.demo",
        "company.iban": "FI79 4405 2020 0360 82",
        "company.bic": "NDEAFIHH",
        "company.einvoice_address": finref.ovt_tunnus(yt),
        "company.einvoice_operator": "Maventa",
        "company.domicile": "Joroinen",
        "invoice.footer": "Kiitos työstä! Kysymykset: toimisto@demo-huoltokello.demo",
    }.items():
        settings.set(key, value)
    if not finref.tarkista_iban(settings.get_str("company.iban")):
        raise DemoError("Demoyrityksen IBAN ei ole kelvollinen - korjaa demo.py.")
    return yt


def seed_registry() -> tuple[dict, dict]:
    """Luo työlajit, asiakkaat (OVT:ineen), asiakaskohtaiset hinnat ja tarvikkeet."""
    wt_ids: dict[str, int] = {}
    for code, name, unit, price, minbill, rounding, vat, acc, constru, hh, billable in WORK_TYPES:
        cur = db.ex("INSERT INTO work_types(code, name, unit, price, min_billing_min, rounding_min, vat_rate, "
                    "revenue_account, construction_service, household_deductible, billable) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (code, name, unit, price, minbill, rounding, vat, acc, constru, hh, billable))
        wt_ids[code] = cur.lastrowid

    cust_ids: dict[str, int] = {}
    for number, name, yt_seed, consumer, reverse, street, postcode, city, email in CUSTOMERS:
        yt = "" if (yt_seed == 0 or consumer) else _valid_ytunnus(yt_seed)
        cur = db.ex("INSERT INTO customers(number, name, business_id, is_consumer, street, postcode, city, "
                    "email, einvoice_address, einvoice_operator, payment_term_days, reverse_charge) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,14,?)",
                    (number, name, yt, consumer, street, postcode, city, email,
                     "" if (consumer or not yt) else finref.ovt_tunnus(yt), "Maventa", reverse))
        cust_ids[number] = cur.lastrowid

    for cust_no, wt_code, price in CUSTOMER_PRICES:
        db.ex("INSERT INTO customer_prices(customer_id, work_type_id, price) VALUES(?,?,?)",
              (cust_ids[cust_no], wt_ids[wt_code], price))

    mat_ids: dict[str, int] = {}
    for code, name, unit, purch, sale, vat, acc in MATERIALS:
        cur = db.ex("INSERT INTO materials(code, name, unit, purchase_price, sale_price, vat_rate, revenue_account) "
                    "VALUES(?,?,?,?,?,?,?)", (code, name, unit, purch, sale, vat, acc))
        mat_ids[code] = cur.lastrowid
    return cust_ids, wt_ids, mat_ids


def seed_users(emp_ids: dict[str, int] | None = None) -> dict:
    """Luo demo-käyttäjät kaikille rooleille ja kytkee työntekijätunnukset työntekijöihin."""
    out: dict[str, int] = {}
    for email, name, role, pkey, emp_number in USERS:
        emp_id = (emp_ids or {}).get(emp_number, None) if emp_number else None
        out[email] = security.create_user(email, name, role, _password_for(pkey),
                                          employee_id=emp_id, must_change=False)
        if emp_id:
            db.ex("UPDATE employees SET email = ? WHERE id = ?", (email, emp_id))
    return out


def seed_employees() -> dict[str, int]:
    """Luo työntekijät (palkkatietoineen), palauttaa numeron -> id."""
    emp: dict[str, int] = {}
    for number, name, pay_type, hourly, monthly in EMPLOYEES:
        cur = db.ex("INSERT INTO employees(number, name, pay_type, hourly_wage, monthly_salary, tes) "
                    "VALUES(?,?,?,?,?,'TES 2026')", (number, name, pay_type, hourly or "0", monthly or "0"))
        emp[number] = cur.lastrowid
    return emp


# Työajan rytmitys: yksi "vuoro" (1 paikka) tai ketjutettu aamu+iltapäivä pari.
_SHIFTS = [
    (1, "06:30", "10:00"), (1, "07:30", "11:30"), (1, "08:00", "12:00"), (1, "09:00", "15:00"),
    (1, "10:00", "14:00"), (1, "12:00", "16:00"), (1, "13:00", "17:30"), (1, "07:00", "10:00"),
    (1, "06:30", "15:30"), (1, "07:00", "17:00"),  # pitkät päivät -> päiväraha
    (2, "07:00", "09:30", "09:45", "12:15"), (2, "07:30", "11:45", "12:15", "15:45"),
    (2, "08:00", "10:30", "10:45", "13:15"), (2, "09:00", "12:00", "12:30", "16:30"),
]

# Työlajivalikoimat per asiakas (nimi -> koodit).
CUSTOMER_WORK = {
    "1001": ("KI-001", "SI-005", "LU-002"),
    "1002": ("PI-003", "KI-001"),
    "1003": ("TA-004", "RU-006", "SI-005", "KI-001"),
    "1004": ("KI-001", "SI-005"),
    "1005": ("KI-001", "LU-002", "PI-003"),
}


def generate_work(cust_ids: dict, wt_ids: dict, mat_ids: dict, emp_ids: dict, johto_uid: int) -> dict:
    """Työaika- ja tarvikekirjaukset kahteen edelliseen kuukauteen + tähän kuukauteen.

    Enimmäiskuukausi (M1): kaikki hyväksytty -> laskutetaan. Tokakuukausi (M2):
    hyväksytty, muutama hylätty. Nykykuukausi (M3): puolet lähetetty hyväksyntään,
    viimeinen viikko luonnoksia. Palauttaa laskumittaukset.
    """
    rng = random.Random(20261125)  # deterministinen esittelyaineisto
    today = dt.date.today()
    m1_start = _add_months(today, -2).replace(day=1)
    m2_start = _add_months(today, -1).replace(day=1)
    m3_start = today.replace(day=1)
    gen_start = max(m1_start, today - dt.timedelta(days=100))  # timesheet: enintään 120 pv taaksepäin
    hourly = [emp_ids[str(n)] for n, _, pt, _, _ in EMPLOYEES if pt == "tunti"]
    out = {"days": 0, "time_lahetetty": 0, "time_luonnos": 0, "time_hylatty": 0, "approved_m12": 0, "materials": 0}

    approved_m12: list[int] = []
    rejected_candidates: list[int] = []

    for d in _workdays(gen_start, today - dt.timedelta(days=1)):
        out["days"] += 1
        in_m12 = d < m3_start
        for emp_id in hourly:
            if rng.random() < 0.22:  # loma / sisäpäivä
                continue
            cust_no = rng.choice(tuple(CUSTOMER_WORK))
            pool = tuple(wt_ids[c] for c in CUSTOMER_WORK[cust_no])
            shifts = rng.choice(_SHIFTS)
            if shifts[0] == 1:
                blocks = [(shifts[1], shifts[2])]
            else:
                blocks = [(shifts[1], shifts[2]), (shifts[3], shifts[4])]
            for bno, (start_t, end_t) in enumerate(blocks):
                wt_id = rng.choice(pool)
                minutes = (int(end_t[:2]) * 60 + int(end_t[3:]) - int(start_t[:2]) * 60 - int(start_t[3:]))
                form = {
                    "work_date": d.isoformat(), "start_time": start_t, "end_time": end_t,
                    "break_min": 30 if (bno == 1 and minutes >= 180 and rng.random() < 0.5) else 0,
                    "work_type_id": wt_id, "customer_id": cust_ids[cust_no], "billable": 1,
                    "site": rng.choice(SITES[cust_no]), "task_ref": "", "description": rng.choice(_TYO_TEKSTIT),
                }
                if rng.random() < 0.25:  # työmatkat
                    form["km"] = rng.choice([8, 12, 18, 24, 31])
                if minutes >= 330 and rng.random() < 0.4:  # yli 5,5 h -> päiväraha
                    net = minutes - form["break_min"]
                    form["allowance"] = "koko" if net >= 420 else "osa"
                status_draft = (not in_m12) and (today - d).days < 7  # viimeinen viikko luonnoksina
                submitted = in_m12 or not status_draft
                eid = timesheet.create(emp_id, form, johto_uid, submit=submitted)
                if in_m12:
                    approved_m12.append(eid)
                elif status_draft:
                    out["time_luonnos"] += 1
                else:
                    out["time_lahetetty"] += 1
                    if out["time_lahetetty"] == 3:
                        rejected_candidates.append(eid)
        if in_m12 and rng.random() < 0.18:  # tarvikkeita laskutuskuntoon
            code = rng.choice(("TAR-001", "TAR-002", "TAR-003", "TAR-004"))
            timesheet.add_material(cust_ids[rng.choice(("1001", "1004", "1005"))], mat_ids[code],
                                   rng.randint(2, 24), d.isoformat(), johto_uid,
                                   employee_id=rng.choice(hourly), site=rng.choice(SITES["1001"]),
                                   description=_MATERIAL_TEKSTIT[code])
            out["materials"] += 1

    for i in range(0, len(approved_m12), 200):  # erissä: SQLite IN-parametrien raja
        timesheet.approve(approved_m12[i:i + 200], johto_uid)
    out["approved_m12"] = len(approved_m12)
    if rejected_candidates:
        n = timesheet.reject(rejected_candidates[:2],
                             "Auton varaaminen epäselvä - kirjaa uudelleen oikealla autolla.")
        out["time_hylatty"] = n
        out["time_lahetetty"] -= n
    # muutama tarvikekirjaus nykykuukaudeksi (näkyy kirjauksina)
    tday = min(today - dt.timedelta(days=2), today)
    timesheet.add_material(cust_ids["1001"], mat_ids["TAR-001"], 10, tday.isoformat(), johto_uid,
                           employee_id=hourly[0], site=SITES["1001"][0],
                           description="Hiekoitushiekkaa ajokennille")
    out["materials"] += 1
    return out


# ------------------------------------------------------------------ tarjoukset ja laskut

def seed_quotes(cust_ids: dict, wt_ids: dict, johto_uid: int) -> list[int]:
    """Kaksi tarjouusta: toinen lähetetty, toinen luonnoksena hyväksyntää odottamassa."""
    out: list[int] = []
    names: dict[str, str] = {}
    for no, cid in cust_ids.items():
        r = db.q1("SELECT name FROM customers WHERE id = ?", (cid,))
        names[no] = r["name"]
    lines_b = pricing.estimate_lines(cust_ids["1005"], [(wt_ids["KI-001"], Decimal("60")),
                                                        (wt_ids["LU-002"], Decimal("24"))])
    q1 = pricing.create_quote(cust_ids["1005"], names["1005"], "Talviharjun kiinteistönhoito ja talvilisäpalvelut",
                              lines_b, 21, johto_uid)
    lines_j = pricing.estimate_lines(cust_ids["1003"], [(wt_ids["RU-006"], Decimal("35"))])
    q2 = pricing.create_quote(cust_ids["1003"], names["1003"], "Koulun ulkoseinien korjaustyö ja loppusiivous",
                              lines_j, 14, johto_uid)
    db.ex("UPDATE quotes SET status = 'lahetetty' WHERE id = ?", (q1,))
    db.ex("UPDATE quotes SET status = 'luonnos' WHERE id = ?", (q2,))
    out += [q1, q2]
    return out


def _cust(cust_ids: dict, no: str):
    return db.q1("SELECT * FROM customers WHERE id = ?", (cust_ids[no],))


def seed_invoices(cust_ids: dict, wt_ids: dict, johto_uid: int) -> dict:
    """Laskun elinkaari demona: luonnokset, vahvistukset, suoritukset, muistutus, hyvitys."""
    m1_start = _add_months(dt.date.today(), -2).replace(day=1)
    m1_end = _month_end(m1_start)
    m2_start = _add_months(dt.date.today(), -1).replace(day=1)
    m2_end = _month_end(m2_start)
    out = {"invoices": [], "reminders": 0, "credits": 0, "payments": 0, "drafts": []}

    def draft(no: str, start: str, end: str, your_ref: str = "") -> int:
        inv_id = invoicing.create_draft(cust_ids[no], johto_uid, start=start, end=end,
                                        your_reference=your_ref)
        out["invoices"].append(inv_id)
        return inv_id

    # --- edellisen edeltävä kuukausi: vahvistetut laskut, erilaiset maksutilat
    for no, iday, ref in (("1001", -44, "Sopimus KV-2026/12"), ("1002", -43, "Huoltosopimus 7"),
                          ("1003", -42, "Tilaus JRK-114")):
        inv_id = draft(no, m1_start.isoformat(), m1_end.isoformat(), ref)
        invoicing.finalize(inv_id, johto_uid, invoice_date=_pvm(iday))
    inv_aurora, inv_maki, inv_joro = out["invoices"]
    out["invoices"] = [inv_aurora, inv_maki, inv_joro]

    # Aurinkorannikko: maksettu kokonaan
    row = db.q1("SELECT total_gross FROM invoices WHERE id = ?", (inv_aurora,))
    invoicing.register_payment(inv_aurora, row["total_gross"], _pvm(-38), johto_uid, archive_ref="TILI-1")
    out["payments"] += 1

    # Mäki-Matti: osamaksu + maksumuistutus (muistutusmaksu + viivästyskorko)
    row = db.q1("SELECT total_gross FROM invoices WHERE id = ?", (inv_maki,))
    half = (Decimal(str(row["total_gross"])) / 2).quantize(Decimal("0.01"))
    invoicing.register_payment(inv_maki, half, _pvm(-27), johto_uid, archive_ref="TILI-3")
    out["payments"] += 1
    rid = invoicing.create_reminder(inv_maki, johto_uid, include_interest=True)
    invoicing.finalize(rid, johto_uid, invoice_date=_pvm(-11))
    out["reminders"] += 1
    # Joroisten: käännetty verovelvollisuus (RAK-rivit), erääntynyt, kokonaan maksamatta

    # --- edellinen kuukausi: kaksi vahvistettua sekä luonnos
    inv_koivu = draft("1004", m2_start.isoformat(), m2_end.isoformat(), "Tilaus KVR-88")
    invoicing.finalize(inv_koivu, johto_uid, invoice_date=_pvm(-9))  # avoin, erääntyy myöhemmin
    out["invoices"].append(inv_koivu)

    inv_harju = draft("1005", m2_start.isoformat(), m2_end.isoformat(), "Sopimus TH-3")
    invoicing.finalize(inv_harju, johto_uid, invoice_date=_pvm(-16))
    out["invoices"].append(inv_harju)
    row = db.q1("SELECT total_gross FROM invoices WHERE id = ?", (inv_harju,))
    invoicing.register_payment(inv_harju, row["total_gross"], _pvm(-12), johto_uid, archive_ref="TILI-2")
    out["payments"] += 1
    invoicing.credit_note(inv_harju, johto_uid, "Puretut tunnit palautettu sopimushintaan",
                          release_entries=False)
    out["credits"] += 1

    # Joroisten: laskuluonnos (+ käsin lisätty rivi) - jää vahvistatta
    inv_draft = draft("1003", m2_start.isoformat(), m2_end.isoformat(), "Sopimus 2026-47")
    invoicing.add_manual_line(inv_draft, "Kevätviherrunko: ennakko-erä", 1, "kpl", "350.00", "25.5")
    out["drafts"].append(inv_draft)
    out["invoices"].append(inv_draft)
    return out


def seed_payroll(johto_uid: int, tyonjohto_uid: int) -> dict:
    """Palkkajaksot: edellisen edeltävä kuukausi lukittuna (jaksotusviennit PA), edellinen luonnoksena."""
    today = dt.date.today()
    m1_start = _add_months(today, -2).replace(day=1)
    m2_start = _add_months(today, -1).replace(day=1)
    run1 = payroll.save_run(m1_start.isoformat(), _month_end(m1_start).isoformat(), johto_uid)
    payroll.lock_run(run1, johto_uid, post_accrual=True)
    run2 = payroll.save_run(m2_start.isoformat(), _month_end(m2_start).isoformat(), tyonjohto_uid)
    return {"payroll_runs": [run1, run2]}


# ------------------------------------------------------------------ laturi

def _assert_empty() -> None:
    """Estä lataus, jos tietokannassa on mitään liiketoiminta-aineistoa."""
    for table, label in (("users", "käyttäjiä"), ("companies", "yrityksiä"), ("customers", "asiakkaita"),
                         ("work_types", "työlajeja"), ("materials", "tarvikkeita"),
                         ("employees", "työntekijöitä"), ("time_entries", "työaikakirjauksia"),
                         ("material_entries", "tarvikekirjauksia"), ("invoices", "laskuja"),
                         ("quotes", "tarjouksia"), ("payroll_runs", "palkkajaksoja")):
        try:
            n = db.q1(f"SELECT COUNT(*) AS n FROM {table}")["n"]
        except Exception:  # noqa: BLE001, taulua ei ole (vanha skeema -> tyhjä)
            continue
        if n:
            raise DemoError(f"Tietokanta ei ole tyhjä: {label} jo {n} kpl. "
                            "Demodata ladataan vain tyhjään tietokantaan.")


def load() -> dict:
    """Lataa demodataa tyhjään tietokantaan ja palauttaa yhteenvedon ladatuista aineistoista.

    Vaatii db.migrate()-suoritetun, muuten tyhjän tietokannan. Merkitsee datan ladatuksi
    asetuksella demo.seeded ja kirjaa tapauksen tarkastusketjuun.
    """
    if on_seeded():
        raise DemoError("Demodata on jo ladattu tähän tietokantaan (asetus demo.seeded).")
    _assert_empty()

    summary: dict[str, object] = {"ytunnus": seed_company()}
    cust_ids, wt_ids, mat_ids = seed_registry()
    emp_ids = seed_employees()
    users = seed_users(emp_ids)
    johto = users["johto@huolto.fi"]
    tyonjohto = users["tyonjohto@huolto.fi"]

    work = generate_work(cust_ids, wt_ids, mat_ids, emp_ids, johto)
    quotes = seed_quotes(cust_ids, wt_ids, johto)
    invoices = seed_invoices(cust_ids, wt_ids, johto)
    payroll_runs = seed_payroll(johto, tyonjohto)

    settings.set(FLAG, iso(utcnow()))
    security.audit("demodata_ladattu", "settings", FLAG, "Demodata lisätty esittelytietokantaan")

    summary.update({
        "yhteyshenkilo": "toimisto@demo-huoltokello.demo",
        "tyolajit": len(wt_ids), "tarvikkeet": len(mat_ids),
        "asiakkaat": len(cust_ids), "tyontekijat": len(emp_ids), "kayttajat": len(users),
        "tarjoukset": len(quotes), **invoices, "palkkajaksot": payroll_runs["payroll_runs"],
        "tyopaivat": work.pop("days"),
    })
    summary.update(work)
    return summary


def summarize(summary: dict) -> str:
    """Ihmiskielinen yhteenveto latauksen tuloksesta (komentorivi ja web)."""
    lines = [
        "Demodata ladattu:",
        f"  Yritys: Huoltokello Talous Demo Oy (Y-tunnus {summary.get('ytunnus', '')})",
        f"  Työlajit {summary.get('tyolajit')} | Tarvikkeet {summary.get('tarvikkeet')} | "
        f"Asiakkaat {summary.get('asiakkaat')} | Työntekijät {summary.get('tyontekijat')} | "
        f"Käyttäjät {summary.get('kayttajat')}",
        f"  Työpäiviä kirjattu {summary.get('tyopaivat')}: hyväksyttyä {summary.get('approved_m12')} "
        f"tietoa, lähetettyä {summary.get('time_lahetetty')}, luonnoksia {summary.get('time_luonnos')}, "
        f"hylättyä {summary.get('time_hylatty')}, tarvikkeita {summary.get('materials')}",
        f"  Laskuja {len(summary.get('invoices', []))} (muistutuksia {summary.get('reminders')}), "
        f"hyvityksiä {summary.get('credits')}, suorituksia {summary.get('payments')}, "
        f"laskuluonnoksia {len(summary.get('drafts', []))}, tarjouksia {summary.get('tarjoukset')}",
        f"  Palkkajaksot: {', '.join(str(r) for r in summary.get('palkkajaksot', []))}",
        "",
        "Demo-käyttäjät (salasana ladataan vain tässä):",
    ]
    for email, pkey in ((u[0], u[3]) for u in USERS):
        lines.append(f"  {email}  ->  {_password_for(pkey)}")
    return "\n".join(lines)
# (DEMOPAATTY)