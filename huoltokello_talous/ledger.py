"""Kahdenkertainen kirjanpito: tilikartta, kirjaukset ja raportit.

Kirjaukset syntyvät automaattisesti laskuista, hyvityksistä ja suorituksista.
Kirjauksia ei voi muuttaa tai poistaa (tietokantatriggerit) - virheet korjataan
oikaisukirjauksella, kuten kirjanpitolaki (1336/1997) 2 luku edellyttää.
Lukittuun kauteen (YYYY-MM) ei voi kirjata.
"""
from __future__ import annotations

import datetime as dt
from collections import OrderedDict, defaultdict
from decimal import Decimal

from . import db, settings
from .money import D, r2, ZERO

DEFAULT_ACCOUNTS = [
    ("1700", "Myyntisaamiset", "vastaavaa"),
    ("1910", "Pankkitili", "vastaavaa"),
    ("2930", "Siirtovelat", "vastattavaa"),
    ("2939", "Arvonlisäverovelka", "vastattavaa"),
    ("3000", "Myynti 25,5 %", "tulo"),
    ("3010", "Myynti 13,5 %", "tulo"),
    ("3020", "Myynti 10 %", "tulo"),
    ("3030", "Rakentamispalvelujen myynti (käännetty ALV)", "tulo"),
    ("3040", "Palvelujen myynti muihin EU-maihin", "tulo"),
    ("3050", "Myynti 0 % / veroton", "tulo"),
    ("3100", "Tarvikemyynti 25,5 %", "tulo"),
    ("3990", "Muut liiketoiminnan tuotot (muistutusmaksut)", "tulo"),
    ("5000", "Palkat", "meno"),
    ("6130", "TyEL-maksut", "meno"),
    ("6300", "Muut henkilösivukulut", "meno"),
    ("6410", "Sosiaaliturvamaksut", "meno"),
    ("7700", "Matkakulut (km-korvaukset, päivärahat)", "meno"),
    ("8590", "Pyöristyserot", "meno"),
    ("9160", "Viivästyskorkotuotot", "tulo"),
]

# ALV-koodit: (selite, verokanta, ilmoitetaanko vero)
VAT_CODES = OrderedDict([
    ("ALV25.5", ("Kotimaan myynti 25,5 %", Decimal("25.5"))),
    ("ALV13.5", ("Kotimaan myynti 13,5 %", Decimal("13.5"))),
    ("ALV10", ("Kotimaan myynti 10 %", Decimal("10"))),
    ("RAK", ("Rakentamispalvelujen myynti, käännetty verovelvollisuus (AVL 8 c §)", Decimal("0"))),
    ("EUPAL", ("Palvelujen myynti muihin EU-maihin (käännetty verovelvollisuus)", Decimal("0"))),
    ("ALV0", ("0-verokannan alainen liikevaihto", Decimal("0"))),
    ("EIALV", ("Ei arvonlisäveron alainen (korot, muistutusmaksut)", Decimal("0"))),
])

SERIES = {"MY": "Myyntilaskut", "HY": "Hyvityslaskut", "SU": "Suoritukset", "PA": "Palkkajaksotus",
          "MU": "Muistiotositteet"}


class LedgerError(Exception):
    pass


def ensure_accounts() -> None:
    for num, name, kind in DEFAULT_ACCOUNTS:
        db.ex("INSERT OR IGNORE INTO accounts(number, name, kind) VALUES(?,?,?)", (num, name, kind))


def vat_code_for(rate, reverse_charge: bool = False, eu_service: bool = False) -> str:
    if reverse_charge:
        return "RAK"
    if eu_service:
        return "EUPAL"
    rate = D(rate)
    for code, (_n, r) in VAT_CODES.items():
        if code.startswith("ALV") and code != "ALV0" and r == rate:
            return code
    if rate == 0:
        return "ALV0"
    raise LedgerError(f"Tuntematon ALV-kanta {rate}")


def period_of(date_str: str) -> str:
    return date_str[:7]


def is_locked(date_str: str) -> bool:
    return db.q1("SELECT 1 FROM locked_periods WHERE period = ?", (period_of(date_str),)) is not None


def lock_period(period: str, user_id: int) -> None:
    if not (len(period) == 7 and period[4] == "-"):
        raise LedgerError("Kausi muodossa VVVV-KK.")
    from .security import iso, utcnow
    db.ex("INSERT OR IGNORE INTO locked_periods(period, locked_by, locked_at) VALUES(?,?,?)",
          (period, user_id, iso(utcnow())))


def post(series: str, entry_date: str, description: str, lines: list[dict], source_type: str = "",
         source_id=None, user_id=None) -> int:
    """Kirjaa tositteen. lines: [{account, debit, credit, vat_code, vat_rate, vat_base, cost_center, description}]"""
    if series not in SERIES:
        raise LedgerError("Tuntematon tositesarja.")
    dt.date.fromisoformat(entry_date)
    if is_locked(entry_date):
        raise LedgerError(f"Kausi {period_of(entry_date)} on lukittu. Kirjaa avoimelle kaudelle.")
    clean = []
    total_d = total_c = ZERO
    for ln in lines:
        # Rivi nettoutetaan: negatiivinen debet on kredit ja päinvastoin.
        net = r2(ln.get("debit") or 0) - r2(ln.get("credit") or 0)
        d_, c_ = (net, ZERO) if net > 0 else (ZERO, -net)
        if net == 0:
            continue
        if not db.q1("SELECT 1 FROM accounts WHERE number = ? AND active = 1", (ln["account"],)):
            raise LedgerError(f"Tiliä {ln['account']} ei ole tilikartassa.")
        total_d += d_
        total_c += c_
        clean.append((ln["account"], str(d_), str(c_), ln.get("vat_code", ""), str(ln.get("vat_rate", "")),
                      str(r2(ln.get("vat_base") or 0)), ln.get("cost_center", ""), ln.get("description", "")))
    if not clean:
        raise LedgerError("Tositteella ei ole rivejä.")
    if total_d != total_c:
        raise LedgerError(f"Tosite ei täsmää: debet {total_d} ≠ kredit {total_c}.")
    with db.tx() as conn:
        nxt = conn.execute("SELECT COALESCE(MAX(number), 0) + 1 FROM journals WHERE series = ?",
                           (series,)).fetchone()[0]
        cur = conn.execute("INSERT INTO journals(series, number, entry_date, description, source_type, source_id, "
                           "created_by) VALUES(?,?,?,?,?,?,?)",
                           (series, nxt, entry_date, description[:300], source_type, source_id, user_id))
        jid = cur.lastrowid
        conn.executemany("INSERT INTO journal_lines(journal_id, account, debit, credit, vat_code, vat_rate, "
                         "vat_base, cost_center, description) VALUES(?,?,?,?,?,?,?,?,?)",
                         [(jid, *c) for c in clean])
    return jid


def post_invoice(inv, lines, user_id=None) -> int:
    """Myyntilaskun / hyvityslaskun tiliöinti."""
    rec = settings.get_str("acc.receivables")
    vat_acc = settings.get_str("acc.vat_payable")
    gross = D(inv["total_gross"])
    jl = []
    credit_note = inv["kind"] == "hyvitys"
    desc = f"{'Hyvityslasku' if credit_note else 'Lasku'} {inv['number']} {_cust_name(inv)}"
    jl.append({"account": rec, "debit": gross if gross > 0 else 0, "credit": -gross if gross < 0 else 0,
               "description": desc})
    by_acc: dict[tuple[str, str, str], list[Decimal]] = defaultdict(lambda: [ZERO, ZERO])
    vat_by_rate: dict[str, list[Decimal]] = defaultdict(lambda: [ZERO, ZERO])
    for ln in lines:
        key = (ln["account"], ln["vat_code"], str(ln["vat_rate"]))
        by_acc[key][0] += D(ln["net"])
        if D(ln["vat"]) != 0:
            vat_by_rate[ln["vat_code"]][0] += D(ln["vat"])
            vat_by_rate[ln["vat_code"]][1] += D(ln["net"])
    for (acc, code, rate), (net, _x) in by_acc.items():
        jl.append({"account": acc, "credit": net if net > 0 else 0, "debit": -net if net < 0 else 0,
                   "vat_code": code, "vat_rate": rate, "vat_base": net, "description": desc})
    for code, (vat, base) in vat_by_rate.items():
        jl.append({"account": vat_acc, "credit": vat if vat > 0 else 0, "debit": -vat if vat < 0 else 0,
                   "vat_code": code, "vat_rate": str(VAT_CODES[code][1]), "vat_base": base,
                   "description": f"ALV {desc}"})
    return post("HY" if credit_note else "MY", inv["invoice_date"], desc, jl, "invoice", inv["id"], user_id)


def post_payment(payment_id: int, inv, amount, pay_date: str, user_id=None) -> int:
    amount = r2(amount)
    desc = f"Suoritus lasku {inv['number']} {_cust_name(inv)}"
    jl = [{"account": settings.get_str("acc.bank"), "debit": amount if amount > 0 else 0,
           "credit": -amount if amount < 0 else 0, "description": desc},
          {"account": settings.get_str("acc.receivables"), "credit": amount if amount > 0 else 0,
           "debit": -amount if amount < 0 else 0, "description": desc}]
    return post("SU", pay_date, desc, jl, "payment", payment_id, user_id)


def _cust_name(inv) -> str:
    import json
    try:
        return json.loads(inv["customer_snapshot"]).get("name", "")[:60]
    except (ValueError, TypeError):
        return ""


# ------------------------------------------------------------------ raportit

def journal_rows(start: str, end: str):
    return db.q("SELECT j.id, j.series, j.number, j.entry_date, j.description, l.account, a.name AS account_name, "
                "l.debit, l.credit, l.vat_code, l.vat_rate, l.vat_base, l.cost_center "
                "FROM journals j JOIN journal_lines l ON l.journal_id = j.id "
                "JOIN accounts a ON a.number = l.account "
                "WHERE j.entry_date BETWEEN ? AND ? ORDER BY j.entry_date, j.series, j.number, l.id",
                (start, end))


def paivakirja(start: str, end: str) -> list[dict]:
    out: "OrderedDict[int, dict]" = OrderedDict()
    for r in journal_rows(start, end):
        j = out.setdefault(r["id"], {"tosite": f"{r['series']}{r['number']}", "date": r["entry_date"],
                                     "description": r["description"], "lines": []})
        j["lines"].append({"account": r["account"], "account_name": r["account_name"], "debit": D(r["debit"]),
                           "credit": D(r["credit"]), "vat_code": r["vat_code"]})
    return list(out.values())


def _balance_before(account: str, start: str) -> Decimal:
    total = ZERO  # summataan Decimalina (ei SQL:n liukulukusummaa)
    for row in db.q("SELECT l.debit, l.credit FROM journal_lines l JOIN journals j ON j.id = l.journal_id "
                    "WHERE l.account = ? AND j.entry_date < ?", (account, start)):
        total += D(row["debit"]) - D(row["credit"])
    return total


def paakirja(start: str, end: str) -> list[dict]:
    accounts = OrderedDict()
    for a in db.q("SELECT * FROM accounts ORDER BY number"):
        accounts[a["number"]] = {"account": a["number"], "name": a["name"], "kind": a["kind"],
                                 "opening": _balance_before(a["number"], start), "rows": []}
    for r in journal_rows(start, end):
        accounts[r["account"]]["rows"].append({"tosite": f"{r['series']}{r['number']}", "date": r["entry_date"],
                                               "description": r["description"], "debit": D(r["debit"]),
                                               "credit": D(r["credit"])})
    out = []
    for acc in accounts.values():
        if not acc["rows"] and acc["opening"] == 0:
            continue
        bal = acc["opening"]
        for row in acc["rows"]:
            bal += row["debit"] - row["credit"]
            row["balance"] = bal
        acc["closing"] = bal
        acc["debit_total"] = sum((r["debit"] for r in acc["rows"]), ZERO)
        acc["credit_total"] = sum((r["credit"] for r in acc["rows"]), ZERO)
        out.append(acc)
    return out


def saldot(start: str, end: str) -> dict[str, Decimal]:
    """Tilien muutos kaudella (debet - kredit)."""
    out: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for r in journal_rows(start, end):
        out[r["account"]] += D(r["debit"]) - D(r["credit"])
    return out


def _acc_name(num: str) -> str:
    r = db.q1("SELECT name FROM accounts WHERE number = ?", (num,))
    return r["name"] if r else num


def tuloslaskelma(start: str, end: str) -> dict:
    s = saldot(start, end)
    groups = OrderedDict([
        ("Liikevaihto", lambda n: "3000" <= n <= "3899"),
        ("Liiketoiminnan muut tuotot", lambda n: "3900" <= n <= "3999"),
        ("Henkilöstökulut", lambda n: "5000" <= n <= "6999"),
        ("Liiketoiminnan muut kulut", lambda n: "7000" <= n <= "8999"),
        ("Rahoitustuotot ja -kulut", lambda n: "9000" <= n <= "9999"),
    ])
    out = OrderedDict()
    total = ZERO
    for gname, pred in groups.items():
        rows = []
        gsum = ZERO
        for acc in sorted(s):
            if pred(acc):
                val = -s[acc]  # tuotot kredit-saldoisia -> positiivisiksi
                rows.append({"account": acc, "name": _acc_name(acc), "amount": val})
                gsum += val
        out[gname] = {"rows": rows, "total": gsum}
        total += gsum
    return {"groups": out, "result": total}


def tase(asof: str) -> dict:
    s = saldot("0001-01-01", asof)
    assets, liabilities = [], []
    result = ZERO
    for acc in sorted(s):
        if acc < "2000":
            assets.append({"account": acc, "name": _acc_name(acc), "amount": s[acc]})
        elif acc < "3000":
            liabilities.append({"account": acc, "name": _acc_name(acc), "amount": -s[acc]})
        else:
            result += -s[acc]
    liabilities.append({"account": "", "name": "Tulos (avoimet tulos- ja kulutilit)", "amount": result})
    ta = sum((a["amount"] for a in assets), ZERO)
    tl = sum((a["amount"] for a in liabilities), ZERO)
    return {"assets": assets, "liabilities": liabilities, "assets_total": ta, "liabilities_total": tl,
            "balanced": ta == tl}


def alv_laskelma(start: str, end: str) -> dict:
    """Kausiveroilmoituksen myyntiä koskevat kentät tämän järjestelmän kirjauksista.

    Ostojen vähennettävä vero kirjataan kirjanpito-ohjelmassa, joten maksettava vero
    on tässä myynnin vero ennen ostojen vähennyksiä.
    """
    vat_acc = settings.get_str("acc.vat_payable")
    tax = defaultdict(lambda: ZERO)
    base = defaultdict(lambda: ZERO)
    for r in journal_rows(start, end):
        code = r["vat_code"]
        if not code:
            continue
        if r["account"] == vat_acc:
            tax[code] += D(r["credit"]) - D(r["debit"])
        else:
            base[code] += D(r["credit"]) - D(r["debit"])
    rows = []
    for code, (name, rate) in VAT_CODES.items():
        if code == "EIALV":
            continue
        rows.append({"code": code, "name": name, "rate": rate, "base": r2(base[code]), "tax": r2(tax[code])})
    return {
        "rows": rows,
        "vero_255": r2(tax["ALV25.5"]),
        "vero_135": r2(tax["ALV13.5"]),
        "vero_10": r2(tax["ALV10"]),
        "rakentamispalvelut": r2(base["RAK"]),
        "eu_palvelut": r2(base["EUPAL"]),
        "nolla_verokanta": r2(base["ALV0"]),
        "myynnin_vero_yhteensa": r2(tax["ALV25.5"] + tax["ALV13.5"] + tax["ALV10"]),
    }


def myyntireskontra(asof: str) -> list[dict]:
    asof_d = dt.date.fromisoformat(asof)
    out = []
    for inv in db.q("SELECT i.*, c.name AS customer_name FROM invoices i JOIN customers c ON c.id = i.customer_id "
                    "WHERE i.status IN ('avoin','osittain') AND i.invoice_date <= ? ORDER BY i.due_date", (asof,)):
        open_amt = D(inv["total_gross"]) - D(inv["paid_amount"])
        due = dt.date.fromisoformat(inv["due_date"])
        days = (asof_d - due).days
        bucket = ("ei erääntynyt" if days <= 0 else "1–30 pv" if days <= 30 else "31–60 pv" if days <= 60
                  else "61–90 pv" if days <= 90 else "yli 90 pv")
        out.append({"number": inv["number"], "customer": inv["customer_name"], "invoice_date": inv["invoice_date"],
                    "due_date": inv["due_date"], "total": D(inv["total_gross"]), "open": r2(open_amt),
                    "days_overdue": max(0, days), "bucket": bucket, "id": inv["id"]})
    return out
