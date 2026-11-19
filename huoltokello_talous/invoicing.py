"""Laskutus: luonnokset työajoista ja tarvikkeista, vahvistus, hyvitys, suoritukset ja muistutukset.

Laskutuksen periaatteet:
- laskunumerot ovat yhtenäinen, katkeamaton sarja (numero annetaan vasta vahvistuksessa)
- vahvistettu lasku on muuttumaton; korjaus tehdään hyvityslaskulla
- laskumerkinnät (AVL 209 e §): päiväys, numero, myyjän Y-tunnus, ostajan tiedot,
  toimitusaika, määrät, veroton yksikköhinta, verokanta, veron määrä verokannoittain,
  käännetyn verovelvollisuuden maininta ja ostajan Y-tunnus/ALV-tunniste
- kotitalousvähennykseen oikeuttava työn osuus eritellään kuluttaja-asiakkaille
"""
from __future__ import annotations

import datetime as dt
import json
import math
from decimal import Decimal

from . import db, ledger, settings
from .finref import luo_rf_viite, luo_viite
from .money import D, r2, vat_amount, ZERO

KAANNETTY_TEKSTI = "Käännetty verovelvollisuus, AVL 8 c § (rakentamispalvelu). Ostaja on verovelvollinen."
EU_TEKSTI = "Reverse charge – VAT Directive 2006/112/EC art. 196. Ostaja on verovelvollinen."


class InvoiceError(Exception):
    pass


def today() -> str:
    return dt.date.today().isoformat()


def price_for(customer_id, work_type) -> Decimal:
    if customer_id:
        row = db.q1("SELECT price FROM customer_prices WHERE customer_id = ? AND work_type_id = ?",
                    (customer_id, work_type["id"]))
        if row:
            return D(row["price"])
    return D(work_type["price"])


def billable_hours(minutes: int, work_type) -> Decimal:
    """Laskutettavat tunnit: pyöristys ylöspäin alkavaan jaksoon ja minimiveloitus."""
    m = max(int(minutes), int(work_type["min_billing_min"] or 0))
    step = int(work_type["rounding_min"] or 0)
    if step > 0:
        m = int(math.ceil(m / step) * step)
    return (Decimal(m) / Decimal(60)).quantize(Decimal("0.01"))


def _vat_setup(customer, rate, construction: bool, account: str):
    """Palauttaa (vat_code, vat_rate, account) asiakkaan ja palvelun mukaan."""
    eu = (customer["country"] or "FI").upper() != "FI" and not customer["is_consumer"]
    if customer["reverse_charge"] and construction and not customer["is_consumer"]:
        return "RAK", Decimal("0"), settings.get_str("acc.revenue_rc")
    if eu:
        return "EUPAL", Decimal("0"), settings.get_str("acc.revenue_eu_services")
    if not settings.get("company.vat_registered"):
        return "ALV0", Decimal("0"), settings.get_str("acc.revenue_zero")
    code = ledger.vat_code_for(rate)
    return code, D(rate), account


def _line_values(qty, unit_price, discount_pct, vat_rate) -> tuple[Decimal, Decimal, Decimal]:
    net = r2(D(qty) * D(unit_price) * (Decimal(100) - D(discount_pct)) / Decimal(100))
    vat = vat_amount(net, vat_rate)
    return net, vat, r2(net + vat)


def _price_str(value) -> str:
    """Yksikköhinta enintään neljällä desimaalilla (esim. 0,0125 €/kpl), muuten sentteinä."""
    v = D(value)
    q = Decimal("0.01") if v == v.quantize(Decimal("0.01")) else Decimal("0.0001")
    return str(v.quantize(q))


def _insert_line(conn, invoice_id: int, pos: int, **ln) -> None:
    net, vat, gross = _line_values(ln["qty"], ln["unit_price"], ln.get("discount_pct", 0), ln["vat_rate"])
    conn.execute("INSERT INTO invoice_lines(invoice_id, pos, description, line_date, qty, unit, unit_price, "
                 "discount_pct, vat_rate, vat_code, net, vat, gross, account, work_type_id, household_work, "
                 "source_ref) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 (invoice_id, pos, ln["description"][:500], ln.get("line_date"), str(D(ln["qty"])), ln["unit"],
                  _price_str(ln["unit_price"]),
                  str(D(ln.get("discount_pct", 0))), str(D(ln["vat_rate"])), ln["vat_code"], str(net), str(vat),
                  str(gross), ln["account"], ln.get("work_type_id"), 1 if ln.get("household_work") else 0,
                  ln.get("source_ref", "")))


def recalc(conn, invoice_id: int) -> None:
    rows = conn.execute("SELECT net, vat, gross, household_work FROM invoice_lines WHERE invoice_id = ?",
                        (invoice_id,)).fetchall()
    net = sum((D(r["net"]) for r in rows), ZERO)
    vat = sum((D(r["vat"]) for r in rows), ZERO)
    gross = sum((D(r["gross"]) for r in rows), ZERO)
    hh = sum((D(r["gross"]) for r in rows if r["household_work"]), ZERO)
    conn.execute("UPDATE invoices SET total_net = ?, total_vat = ?, total_gross = ?, household_work = ? "
                 "WHERE id = ? AND status = 'luonnos'", (str(net), str(vat), str(gross), str(hh), invoice_id))


def create_draft(customer_id: int, user_id: int, start: str | None = None, end: str | None = None,
                 group_by: str = "tyolaji", include_materials: bool = True, entry_ids: list[int] | None = None,
                 your_reference: str = "", notes: str = "") -> int:
    """Luo laskuluonnoksen asiakkaan hyväksytyistä, laskuttamattomista työajoista ja tarvikkeista."""
    customer = db.q1("SELECT * FROM customers WHERE id = ? AND active = 1", (customer_id,))
    if customer is None:
        raise InvoiceError("Asiakasta ei löydy.")
    where = ["t.customer_id = ?", "t.status = 'hyvaksytty'", "t.billable = 1", "t.invoice_id IS NULL"]
    params: list = [customer_id]
    if start:
        where.append("t.work_date >= ?")
        params.append(start)
    if end:
        where.append("t.work_date <= ?")
        params.append(end)
    if entry_ids:
        where.append(f"t.id IN ({','.join('?' * len(entry_ids))})")
        params += [int(x) for x in entry_ids]
    entries = db.q("SELECT t.*, e.name AS employee_name FROM time_entries t JOIN employees e ON e.id = t.employee_id "
                   f"WHERE {' AND '.join(where)} ORDER BY t.work_date, t.start_time", params)
    mats = []
    if include_materials:
        mp = [customer_id]
        mw = ["m.customer_id = ?", "m.status = 'hyvaksytty'", "m.invoice_id IS NULL"]
        if start:
            mw.append("m.work_date >= ?")
            mp.append(start)
        if end:
            mw.append("m.work_date <= ?")
            mp.append(end)
        mats = db.q("SELECT m.*, x.name, x.unit, x.sale_price, x.vat_rate, x.revenue_account FROM material_entries m "
                    f"JOIN materials x ON x.id = m.material_id WHERE {' AND '.join(mw)} ORDER BY m.work_date", mp)
    if not entries and not mats:
        raise InvoiceError("Asiakkaalla ei ole hyväksyttyjä laskuttamattomia työaikoja tai tarvikkeita valitulla "
                           "aikavälillä.")
    wts = {r["id"]: r for r in db.q("SELECT * FROM work_types")}
    dates = [e["work_date"] for e in entries] + [m["work_date"] for m in mats]
    with db.tx() as conn:
        cur = conn.execute("INSERT INTO invoices(customer_id, delivery_start, delivery_end, your_reference, notes, "
                           "reverse_charge, created_by) VALUES(?,?,?,?,?,?,?)",
                           (customer_id, min(dates), max(dates), your_reference, notes,
                            1 if customer["reverse_charge"] else 0, user_id))
        inv_id = cur.lastrowid
        pos = 0
        if group_by == "rivi":
            for en in entries:
                wt = wts[en["work_type_id"]]
                hours = billable_hours(en["minutes"], wt)
                code, rate, acc = _vat_setup(customer, wt["vat_rate"], bool(wt["construction_service"]),
                                             wt["revenue_account"])
                pos += 1
                desc = f"{wt['name']} {_fi_date(en['work_date'])}"
                if en["site"]:
                    desc += f", {en['site']}"
                if en["description"]:
                    desc += f": {en['description']}"
                _insert_line(conn, inv_id, pos, description=desc, line_date=en["work_date"], qty=hours,
                             unit=wt["unit"], unit_price=price_for(customer_id, wt), vat_rate=rate, vat_code=code,
                             account=acc, work_type_id=wt["id"],
                             household_work=customer["is_consumer"] and wt["household_deductible"],
                             source_ref=f"te:{en['id']}")
        else:
            groups: dict[int, list] = {}
            for en in entries:
                groups.setdefault(en["work_type_id"], []).append(en)
            for wt_id, ens in groups.items():
                wt = wts[wt_id]
                hours = sum((billable_hours(e["minutes"], wt) for e in ens), Decimal(0))
                code, rate, acc = _vat_setup(customer, wt["vat_rate"], bool(wt["construction_service"]),
                                             wt["revenue_account"])
                sites = sorted({e["site"] for e in ens if e["site"]})
                desc = f"{wt['name']} {_fi_date(min(e['work_date'] for e in ens))}–" \
                       f"{_fi_date(max(e['work_date'] for e in ens))}"
                if sites:
                    desc += " (" + ", ".join(sites[:5]) + (" ym." if len(sites) > 5 else "") + ")"
                pos += 1
                _insert_line(conn, inv_id, pos, description=desc, qty=hours, unit=wt["unit"],
                             unit_price=price_for(customer_id, wt), vat_rate=rate, vat_code=code, account=acc,
                             work_type_id=wt_id, household_work=customer["is_consumer"] and wt["household_deductible"],
                             source_ref="te:" + ",".join(str(e["id"]) for e in ens))
        for m in mats:
            code, rate, acc = _vat_setup(customer, m["vat_rate"], False, m["revenue_account"])
            pos += 1
            desc = f"{m['name']} {_fi_date(m['work_date'])}" + (f", {m['site']}" if m["site"] else "")
            _insert_line(conn, inv_id, pos, description=desc, line_date=m["work_date"], qty=m["qty"],
                         unit=m["unit"], unit_price=m["sale_price"], vat_rate=rate, vat_code=code, account=acc,
                         source_ref=f"me:{m['id']}")
        if entries:
            conn.execute(f"UPDATE time_entries SET invoice_id = ? WHERE id IN ({','.join('?' * len(entries))})",
                         [inv_id] + [e["id"] for e in entries])
        if mats:
            conn.execute(f"UPDATE material_entries SET invoice_id = ? WHERE id IN ({','.join('?' * len(mats))})",
                         [inv_id] + [m["id"] for m in mats])
        recalc(conn, inv_id)
    return inv_id


def create_empty_draft(customer_id: int, user_id: int) -> int:
    customer = db.q1("SELECT * FROM customers WHERE id = ?", (customer_id,))
    if customer is None:
        raise InvoiceError("Asiakasta ei löydy.")
    cur = db.ex("INSERT INTO invoices(customer_id, reverse_charge, created_by) VALUES(?,?,?)",
                (customer_id, 1 if customer["reverse_charge"] else 0, user_id))
    return cur.lastrowid


def add_manual_line(invoice_id: int, description: str, qty, unit: str, unit_price, vat_rate,
                    discount_pct=0, account: str = "", construction: bool = False, household: bool = False) -> None:
    inv = _draft(invoice_id)
    customer = db.q1("SELECT * FROM customers WHERE id = ?", (inv["customer_id"],))
    if not description.strip():
        raise InvoiceError("Rivin kuvaus puuttuu.")
    if D(qty) == 0:
        raise InvoiceError("Määrä ei voi olla nolla.")
    if not (Decimal(0) <= D(discount_pct) <= Decimal(100)):
        raise InvoiceError("Alennus 0–100 %.")
    code, rate, acc = _vat_setup(customer, vat_rate, construction, account or _default_revenue_account(vat_rate))
    with db.tx() as conn:
        pos = conn.execute("SELECT COALESCE(MAX(pos),0)+1 FROM invoice_lines WHERE invoice_id = ?",
                           (invoice_id,)).fetchone()[0]
        _insert_line(conn, invoice_id, pos, description=description.strip(), qty=qty, unit=unit or "kpl",
                     unit_price=unit_price, discount_pct=discount_pct, vat_rate=rate, vat_code=code, account=acc,
                     household_work=household and customer["is_consumer"])
        recalc(conn, invoice_id)


def _default_revenue_account(rate) -> str:
    return {"25.5": "3000", "13.5": "3010", "10": "3020"}.get(str(D(rate).normalize()), "3050")


def delete_line(invoice_id: int, line_id: int) -> None:
    _draft(invoice_id)
    with db.tx() as conn:
        conn.execute("DELETE FROM invoice_lines WHERE id = ? AND invoice_id = ?", (line_id, invoice_id))
        recalc(conn, invoice_id)


def delete_draft(invoice_id: int) -> None:
    _draft(invoice_id)
    with db.tx() as conn:
        conn.execute("UPDATE time_entries SET invoice_id = NULL WHERE invoice_id = ? AND status = 'hyvaksytty'",
                     (invoice_id,))
        conn.execute("UPDATE material_entries SET invoice_id = NULL WHERE invoice_id = ? AND status = 'hyvaksytty'",
                     (invoice_id,))
        conn.execute("DELETE FROM invoice_lines WHERE invoice_id = ?", (invoice_id,))
        conn.execute("DELETE FROM invoices WHERE id = ? AND status = 'luonnos'", (invoice_id,))


def update_draft_header(invoice_id: int, **fields) -> None:
    _draft(invoice_id)
    allowed = {"your_reference", "our_reference", "notes", "invoice_date", "due_date"}
    sets, vals = [], []
    for k, v in fields.items():
        if k in allowed:
            if k in ("invoice_date", "due_date") and v:
                dt.date.fromisoformat(v)
            sets.append(f"{k} = ?")
            vals.append(v or None if k in ("invoice_date", "due_date") else v)
    if sets:
        db.ex(f"UPDATE invoices SET {', '.join(sets)} WHERE id = ? AND status = 'luonnos'", vals + [invoice_id])


def _draft(invoice_id: int):
    inv = db.q1("SELECT * FROM invoices WHERE id = ?", (invoice_id,))
    if inv is None:
        raise InvoiceError("Laskua ei löydy.")
    if inv["status"] != "luonnos":
        raise InvoiceError("Vain luonnosta voi muokata. Vahvistettu lasku korjataan hyvityslaskulla.")
    return inv


def _fi_date(iso_date: str) -> str:
    d = dt.date.fromisoformat(iso_date)
    return f"{d.day}.{d.month}.{d.year}"


def customer_snapshot(customer) -> dict:
    keys = ["number", "name", "business_id", "is_consumer", "street", "postcode", "city", "country", "email",
            "einvoice_address", "einvoice_operator", "contact_person", "reverse_charge"]
    return {k: customer[k] for k in keys}


def validate_for_finalize(inv, lines, customer) -> list[str]:
    errs = []
    if not lines:
        errs.append("Laskulla ei ole rivejä.")
    if not settings.get_str("company.name") or not settings.get_str("company.business_id"):
        errs.append("Yrityksen nimi ja Y-tunnus puuttuvat (Asetukset > Yritys).")
    if not settings.get_str("company.iban"):
        errs.append("Yrityksen IBAN puuttuu (Asetukset > Yritys).")
    codes = {ln["vat_code"] for ln in lines}
    if ("RAK" in codes or "EUPAL" in codes) and not customer["business_id"]:
        errs.append("Käännetyssä verovelvollisuudessa ostajan Y-tunnus / ALV-tunniste on pakollinen laskumerkintä.")
    if not customer["is_consumer"] and not customer["business_id"] and (customer["country"] or "FI") == "FI":
        errs.append("Yritysasiakkaalta puuttuu Y-tunnus.")
    if D(inv["total_gross"]) < 0 and inv["kind"] == "lasku":
        errs.append("Lasku ei voi olla negatiivinen – tee hyvityslasku.")
    return errs


def finalize(invoice_id: int, user_id: int, invoice_date: str | None = None) -> dict:
    """Vahvistaa laskun: numero, viite, eräpäivä, asiakastietojen tallennus ja tiliöinti."""
    with db.tx() as conn:
        inv = conn.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
        if inv is None or inv["status"] != "luonnos":
            raise InvoiceError("Vain luonnoksen voi vahvistaa.")
        lines = conn.execute("SELECT * FROM invoice_lines WHERE invoice_id = ? ORDER BY pos", (invoice_id,)).fetchall()
        customer = conn.execute("SELECT * FROM customers WHERE id = ?", (inv["customer_id"],)).fetchone()
        errs = validate_for_finalize(inv, lines, customer)
        if errs:
            raise InvoiceError(" ".join(errs))
        idate = invoice_date or inv["invoice_date"] or today()
        if ledger.is_locked(idate):
            raise InvoiceError(f"Kausi {idate[:7]} on lukittu – valitse laskupäivä avoimelta kaudelta.")
        term = int(customer["payment_term_days"] or settings.get("invoice.payment_term_days"))
        due = inv["due_date"] or (dt.date.fromisoformat(idate) + dt.timedelta(days=term)).isoformat()
        if due < idate:
            raise InvoiceError("Eräpäivä ei voi olla ennen laskupäivää.")
        number = settings.next_sequence("invoice.next_number")
        ref = luo_viite(str(number))
        rf = luo_rf_viite(ref) if settings.get("invoice.use_rf_reference") else ""
        from .security import iso, utcnow
        conn.execute("UPDATE invoices SET number = ?, invoice_date = ?, due_date = ?, reference = ?, rf_reference = ?, "
                     "customer_snapshot = ?, status = 'avoin', finalized_by = ?, finalized_at = ? "
                     "WHERE id = ? AND status = 'luonnos'",
                     (number, idate, due, ref, rf, json.dumps(customer_snapshot(customer), ensure_ascii=False),
                      user_id, iso(utcnow()), invoice_id))
        conn.execute("UPDATE time_entries SET status = 'laskutettu', updated_at = datetime('now') "
                     "WHERE invoice_id = ?", (invoice_id,))
        conn.execute("UPDATE material_entries SET status = 'laskutettu' WHERE invoice_id = ?", (invoice_id,))
        inv2 = conn.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
        jid = ledger.post_invoice(inv2, lines, user_id)
        conn.execute("UPDATE invoices SET journal_id = ? WHERE id = ?", (jid, invoice_id))
    return {"number": number, "reference": ref, "journal_id": jid}


def credit_note(invoice_id: int, user_id: int, reason: str, release_entries: bool = True) -> int:
    """Täysi hyvityslasku alkuperäisen rivien vastaluvuilla."""
    reason = (reason or "").strip()
    if not reason:
        raise InvoiceError("Hyvityksen syy on pakollinen.")
    orig = db.q1("SELECT * FROM invoices WHERE id = ?", (invoice_id,))
    if orig is None or orig["kind"] != "lasku" or orig["status"] in ("luonnos", "hyvitetty", "mitatoity"):
        raise InvoiceError("Vain vahvistetun, hyvittämättömän laskun voi hyvittää.")
    lines = db.q("SELECT * FROM invoice_lines WHERE invoice_id = ? ORDER BY pos", (invoice_id,))
    with db.tx() as conn:
        cur = conn.execute("INSERT INTO invoices(kind, credited_invoice_id, customer_id, delivery_start, delivery_end, "
                           "your_reference, notes, reverse_charge, created_by) VALUES('hyvitys',?,?,?,?,?,?,?,?)",
                           (invoice_id, orig["customer_id"], orig["delivery_start"], orig["delivery_end"],
                            orig["your_reference"], f"Hyvitys laskulle {orig['number']}: {reason}",
                            orig["reverse_charge"], user_id))
        cn = cur.lastrowid
        for ln in lines:
            conn.execute("INSERT INTO invoice_lines(invoice_id, pos, description, line_date, qty, unit, unit_price, "
                         "discount_pct, vat_rate, vat_code, net, vat, gross, account, work_type_id, household_work, "
                         "source_ref) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (cn, ln["pos"], ln["description"], ln["line_date"], str(-D(ln["qty"])), ln["unit"],
                          ln["unit_price"], ln["discount_pct"], ln["vat_rate"], ln["vat_code"], str(-D(ln["net"])),
                          str(-D(ln["vat"])), str(-D(ln["gross"])), ln["account"], ln["work_type_id"],
                          ln["household_work"], ln["source_ref"]))
        recalc(conn, cn)
        # Hyvityslaskun vahvistus samassa transaktiossa
        customer = conn.execute("SELECT * FROM customers WHERE id = ?", (orig["customer_id"],)).fetchone()
        number = settings.next_sequence("invoice.next_number")
        idate = today()
        if ledger.is_locked(idate):
            raise InvoiceError("Kuluva kausi on lukittu.")
        from .security import iso, utcnow
        conn.execute("UPDATE invoices SET number = ?, invoice_date = ?, due_date = ?, reference = ?, "
                     "customer_snapshot = ?, status = 'maksettu', finalized_by = ?, finalized_at = ? WHERE id = ?",
                     (number, idate, idate, orig["reference"],
                      json.dumps(customer_snapshot(customer), ensure_ascii=False), user_id, iso(utcnow()), cn))
        cn_row = conn.execute("SELECT * FROM invoices WHERE id = ?", (cn,)).fetchone()
        cn_lines = conn.execute("SELECT * FROM invoice_lines WHERE invoice_id = ?", (cn,)).fetchall()
        jid = ledger.post_invoice(cn_row, cn_lines, user_id)
        conn.execute("UPDATE invoices SET journal_id = ?, paid_amount = total_gross WHERE id = ?", (jid, cn))
        # Alkuperäinen merkitään hyvitetyksi; saatavan saldo täsmäytyy reskontrassa
        # (osittain maksetun laskun liikasuoritus palautetaan negatiivisella suorituksella).
        conn.execute("UPDATE invoices SET status = 'hyvitetty' WHERE id = ?", (invoice_id,))
        if release_entries:
            conn.execute("UPDATE time_entries SET status = 'hyvaksytty', invoice_id = NULL WHERE invoice_id = ?",
                         (invoice_id,))
            conn.execute("UPDATE material_entries SET status = 'hyvaksytty', invoice_id = NULL WHERE invoice_id = ?",
                         (invoice_id,))
    return cn


def register_payment(invoice_id: int, amount, pay_date: str, user_id: int, archive_ref: str = "") -> int:
    amount = r2(amount)
    if amount == 0:
        raise InvoiceError("Suorituksen summa ei voi olla nolla.")
    dt.date.fromisoformat(pay_date)
    with db.tx() as conn:
        inv = conn.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
        if inv is None or inv["status"] not in ("avoin", "osittain", "maksettu", "hyvitetty"):
            raise InvoiceError("Suorituksen voi kirjata vain vahvistetulle laskulle.")
        cur = conn.execute("INSERT INTO payments(invoice_id, pay_date, amount, archive_ref, created_by) "
                           "VALUES(?,?,?,?,?)", (invoice_id, pay_date, str(amount), archive_ref[:60], user_id))
        pid = cur.lastrowid
        jid = ledger.post_payment(pid, inv, amount, pay_date, user_id)
        conn.execute("UPDATE payments SET journal_id = ? WHERE id = ?", (jid, pid))
        paid = r2(D(inv["paid_amount"]) + amount)
        total = D(inv["total_gross"])
        if inv["status"] == "hyvitetty":
            status = "hyvitetty"
        else:
            status = "maksettu" if paid >= total else ("osittain" if paid > 0 else "avoin")
        conn.execute("UPDATE invoices SET paid_amount = ?, status = ? WHERE id = ?", (str(paid), status, invoice_id))
    return pid


def penalty_interest(open_amount, due_date: str, until: str, rate_pct) -> Decimal:
    days = (dt.date.fromisoformat(until) - dt.date.fromisoformat(due_date)).days
    if days <= 0:
        return ZERO
    return r2(D(open_amount) * D(rate_pct) / Decimal(100) * Decimal(days) / Decimal(365))


def create_reminder(invoice_id: int, user_id: int, include_interest: bool = True) -> int:
    """Muistutuslasku: muistutusmaksu + viivästyskorko (ei ALV:n alaisia)."""
    inv = db.q1("SELECT * FROM invoices WHERE id = ?", (invoice_id,))
    if inv is None or inv["status"] not in ("avoin", "osittain"):
        raise InvoiceError("Muistutuksen voi tehdä vain avoimesta laskusta.")
    if inv["due_date"] >= today():
        raise InvoiceError("Lasku ei ole vielä erääntynyt.")
    customer = db.q1("SELECT * FROM customers WHERE id = ?", (inv["customer_id"],))
    open_amt = D(inv["total_gross"]) - D(inv["paid_amount"])
    fee = D(settings.get("invoice.reminder_fee"))
    rate = D(settings.get("invoice.penalty_interest"))
    if customer["is_consumer"]:
        # Kuluttajasaatavissa korkolain 4 § viivästyskorko (viitekorko + 7) - asetusarvo on kaupallinen (+8)
        rate = rate - Decimal(1)
    with db.tx() as conn:
        cur = conn.execute("INSERT INTO invoices(customer_id, your_reference, notes, created_by) VALUES(?,?,?,?)",
                           (inv["customer_id"], f"Muistutus laskusta {inv['number']}",
                            f"Maksumuistutus: lasku {inv['number']} ({_fi_date(inv['invoice_date'])}), eräpäivä "
                            f"{_fi_date(inv['due_date'])}, avoinna {open_amt} €. Maksathan alkuperäisen laskun "
                            f"sen omalla viitteellä {inv['reference']}.", user_id))
        rid = cur.lastrowid
        pos = 0
        if fee > 0:
            pos += 1
            _insert_line(conn, rid, pos, description=f"Muistutusmaksu, lasku {inv['number']}", qty=1, unit="kpl",
                         unit_price=fee, vat_rate=0, vat_code="EIALV", account=settings.get_str("acc.reminder_fees"))
        if include_interest:
            interest = penalty_interest(open_amt, inv["due_date"], today(), rate)
            if interest > 0:
                pos += 1
                _insert_line(conn, rid, pos, description=f"Viivästyskorko {rate} % {_fi_date(inv['due_date'])}–"
                             f"{_fi_date(today())}, lasku {inv['number']}", qty=1, unit="kpl", unit_price=interest,
                             vat_rate=0, vat_code="EIALV", account=settings.get_str("acc.penalty_interest"))
        if pos == 0:
            raise InvoiceError("Muistutusmaksu ja korko ovat nolla – muistutuslaskua ei tarvita.")
        recalc(conn, rid)
        conn.execute("UPDATE invoices SET reminder_count = reminder_count + 1 WHERE id = ?", (invoice_id,))
    return rid


def invoice_bundle(invoice_id: int) -> dict:
    inv = db.q1("SELECT * FROM invoices WHERE id = ?", (invoice_id,))
    if inv is None:
        raise InvoiceError("Laskua ei löydy.")
    lines = db.q("SELECT * FROM invoice_lines WHERE invoice_id = ? ORDER BY pos", (invoice_id,))
    if inv["status"] == "luonnos":
        cust = customer_snapshot(db.q1("SELECT * FROM customers WHERE id = ?", (inv["customer_id"],)))
    else:
        cust = json.loads(inv["customer_snapshot"])
    vat_spec: dict[tuple[str, str], list[Decimal]] = {}
    for ln in lines:
        k = (ln["vat_code"], ln["vat_rate"])
        spec = vat_spec.setdefault(k, [ZERO, ZERO])
        spec[0] += D(ln["net"])
        spec[1] += D(ln["vat"])
    texts = []
    codes = {ln["vat_code"] for ln in lines}
    if "RAK" in codes:
        texts.append(KAANNETTY_TEKSTI)
    if "EUPAL" in codes:
        texts.append(EU_TEKSTI)
    company = {k.split(".", 1)[1]: v for k, v in settings.all_settings().items() if k.startswith("company.")}
    credited = None
    if inv["credited_invoice_id"]:
        credited = db.q1("SELECT number, invoice_date FROM invoices WHERE id = ?", (inv["credited_invoice_id"],))
    payments = db.q("SELECT * FROM payments WHERE invoice_id = ? ORDER BY pay_date", (invoice_id,))
    return {"invoice": inv, "lines": lines, "customer": cust, "vat_spec": vat_spec, "texts": texts,
            "company": company, "credited": credited, "payments": payments,
            "penalty_interest": settings.get_str("invoice.penalty_interest")}
