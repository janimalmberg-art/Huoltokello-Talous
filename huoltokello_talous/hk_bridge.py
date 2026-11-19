"""Huoltokello-silta: tuo Huoltokellon tiedot Talouteen.

Lukee Huoltokellon SQLite-tietokantaa VAIN LUKUTILASSA (mode=ro) – Huoltokellon tietoihin
ei kirjoiteta koskaan. Tuonti on yksisuuntainen ja toistettava: sama kuittaus ei tule kahdesti
(kytkentätaulu hk_map).

Kytkennät:
  Customers + Sites           -> asiakkaat (asiakasnumero HK<id>), kohde kirjauksille
  AspNetUsers (sähköposti)    -> työntekijät (täsmäys sähköpostilla, puuttuvat luodaan)
  TaskCategories              -> työlajit (täsmäys nimellä, puuttuvat luodaan hinnalla 0)
  Contracts.HourlyRate        -> asiakashinta työlajeille (ei ylikirjoita käsin asetettua)
  TaskConfirmations           -> työaikakirjaukset tilaan "lähetetty" (työnjohto hyväksyy)
  ConfirmationMaterials       -> tarvikekirjaukset asiakkaalle

Laskutussäännöt (estävät kaksinkertaisen laskutuksen):
  - tehtävä on jo laskutettu Huoltokellossa (WorkTasks.InvoiceId) -> ei laskutettava, tarvikkeita ei tuoda
  - asiakkaalla voimassa kuukausihintainen sopimus ilman tuntihintaa -> ei laskutettava (sisältyy hintaan)

Kellonajat: Huoltokello tallentaa kuittaukseen vain tunnit ja kuittausajan, joten kirjauksen loppu
= kuittausaika ja alku = loppu − tunnit. Saman päivän kuittaukset ketjutetaan päällekkäin menemättä.
"""
from __future__ import annotations

import calendar
import datetime as dt
import json
import os
import pathlib
import re
import sqlite3
from dataclasses import dataclass, field
from decimal import Decimal

from . import db, settings
from .money import D
from .security import iso, utcnow


class BridgeError(Exception):
    pass


def default_db_path() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "Huoltokello", "AppData", "huoltokello.db")


def hk_path() -> str:
    return settings.get_str("hk.db_path") or default_db_path()


def open_hk(path: str | None = None) -> sqlite3.Connection:
    p = path or hk_path()
    if not os.path.isfile(p):
        raise BridgeError(f"Huoltokellon tietokantaa ei löydy: {p}")
    uri = pathlib.Path(p).resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    need = {"Customers", "Sites", "WorkTasks", "TaskConfirmations", "AspNetUsers", "TaskCategories"}
    have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    missing = need - have
    if missing:
        conn.close()
        raise BridgeError(f"Tiedosto ei ole Huoltokellon tietokanta (puuttuu: {', '.join(sorted(missing))}).")
    return conn


def _cols(conn, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')}


# ------------------------------------------------------------------ aikaleimat

_DT_RX = re.compile(r"(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?")


def parse_hk_dt(v) -> dt.datetime | None:
    """EF Coren SQLite-aikaleimat: '2026-11-19 07:15:00', '...0.1234567', '...T07:15:00Z', '+02:00'."""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        # .NET ticks tai Unix-aika
        if v > 1e15:
            return dt.datetime(1, 1, 1) + dt.timedelta(microseconds=v / 10)
        return dt.datetime.utcfromtimestamp(v)
    s = str(v).strip()
    m = _DT_RX.match(s)
    if not m:
        try:
            return dt.datetime.combine(dt.date.fromisoformat(s[:10]), dt.time())
        except ValueError:
            return None
    y, mo, d, h, mi, se = (int(x) if x else 0 for x in m.groups())
    base = dt.datetime(y, mo, d, h, mi, se)
    tail = s[m.end():]
    tz = re.search(r"([+-])(\d{2}):?(\d{2})$", tail)
    if tz:  # aikavyöhyke mukana -> UTC
        sign = 1 if tz.group(1) == "+" else -1
        base -= sign * dt.timedelta(hours=int(tz.group(2)), minutes=int(tz.group(3)))
        return base.replace(tzinfo=dt.timezone.utc)
    if tail.endswith("Z"):
        return base.replace(tzinfo=dt.timezone.utc)
    return base


def to_local(t: dt.datetime, mode: str) -> dt.datetime:
    """Muuntaa Huoltokellon aikaleiman paikalliseksi (Windowsin aikavyöhyke, kesäaika huomioiden)."""
    if t.tzinfo is not None or mode == "utc":
        ts = calendar.timegm(t.replace(tzinfo=None).timetuple())
        return dt.datetime.fromtimestamp(ts).replace(second=0, microsecond=0)
    return t.replace(second=0, microsecond=0)


def detect_timezone(conn) -> tuple[str, str]:
    """Päättelee, tallentaako Huoltokello aikaleimat UTC- vai paikallisaikana tuoreimmista tapahtumista."""
    cands = []
    for table, col in (("AuditLogs", "CreatedAt"), ("TaskConfirmations", "ConfirmedAt"), ("Notifications", "CreatedAt")):
        try:
            if col in _cols(conn, table):
                r = conn.execute(f'SELECT MAX("{col}") FROM "{table}"').fetchone()
                t = parse_hk_dt(r[0]) if r else None
                if t:
                    cands.append(t)
        except sqlite3.Error:
            continue
    if not cands:
        return "paikallinen", "ei tapahtumia – oletus paikallinen aika"
    t = max(c.replace(tzinfo=None) for c in cands)
    if any(c.tzinfo is not None for c in cands):
        return "utc", "aikaleimoissa aikavyöhyke"
    now_l = dt.datetime.now()
    now_u = dt.datetime.utcnow()
    du, dl = abs((now_u - t).total_seconds()), abs((now_l - t).total_seconds())
    offset_h = abs((now_l - now_u).total_seconds()) / 3600
    if min(du, dl) > 6 * 3600 or offset_h < 0.5:
        return "paikallinen", "ei tuoreita tapahtumia – oletus paikallinen aika (tarkista esikatselusta)"
    return ("utc", "tuorein tapahtuma vastaa UTC-aikaa") if du < dl else ("paikallinen", "tuorein tapahtuma vastaa paikallista aikaa")


def tz_mode(conn) -> tuple[str, str]:
    mode = settings.get_str("hk.timezone") or "auto"
    if mode in ("utc", "paikallinen"):
        return mode, "asetettu käsin"
    return detect_timezone(conn)


# ------------------------------------------------------------------ suunnitelma

@dataclass
class Plan:
    tz: str = ""
    tz_reason: str = ""
    customers_new: list = field(default_factory=list)
    customers_upd: list = field(default_factory=list)
    employees_new: list = field(default_factory=list)
    worktypes_new: list = field(default_factory=list)
    prices: list = field(default_factory=list)
    entries: list = field(default_factory=list)
    skipped: list = field(default_factory=list)
    materials: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    def summary(self) -> dict:
        return {"asiakkaat_uudet": len(self.customers_new), "asiakkaat_paivitetyt": len(self.customers_upd),
                "tyontekijat_uudet": len(self.employees_new), "tyolajit_uudet": len(self.worktypes_new),
                "asiakashinnat": len(self.prices), "tyoajat": len(self.entries), "ohitetut": len(self.skipped),
                "tarvikkeet": len(self.materials), "varoitukset": len(self.warnings)}


def _split_address(addr: str) -> tuple[str, str, str]:
    a = (addr or "").strip()
    m = re.match(r"^(.*?)[,\s]+(\d{5})\s+(.+)$", a)
    if m:
        return m.group(1).strip(" ,"), m.group(2), m.group(3).strip()
    return a, "", ""


def _mapped(kind: str, hk_id) -> int | None:
    r = db.q1("SELECT local_id FROM hk_map WHERE kind = ? AND hk_id = ?", (kind, str(hk_id)))
    return r["local_id"] if r else None


def _start_date() -> dt.date:
    s = settings.get_str("hk.start_date")
    if s:
        return dt.date.fromisoformat(s)
    return dt.date.today().replace(day=1)


def _active_contract(conn, customer_id, day: dt.date, has_contracts: bool):
    if not has_contracts or customer_id is None:
        return None
    best = None
    for c in conn.execute("SELECT * FROM Contracts WHERE CustomerId = ?", (customer_id,)):
        s = parse_hk_dt(c["StartDate"])
        e = parse_hk_dt(c["EndDate"])
        if (s is None or s.date() <= day) and (e is None or e.date() >= day):
            best = c
    return best


def build_plan(conn) -> Plan:
    plan = Plan()
    plan.tz, plan.tz_reason = tz_mode(conn)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    has_contracts = "Contracts" in tables
    has_mats = "ConfirmationMaterials" in tables

    # --- asiakkaat
    hk_customers = {r["Id"]: r for r in conn.execute("SELECT * FROM Customers")}
    for cid, c in hk_customers.items():
        street, pc, city = _split_address(c["Address"])
        local = _mapped("customer", cid)
        data = {"hk_id": cid, "number": f"HK{cid}", "name": (c["Name"] or f"Asiakas {cid}").strip(),
                "contact_person": c["ContactPerson"] or "", "email": c["Email"] or "", "phone": c["Phone"] or "",
                "street": street, "postcode": pc, "city": city}
        if local is None:
            same = db.q1("SELECT id FROM customers WHERE lower(name) = lower(?)", (data["name"],))
            data["match_id"] = same["id"] if same else None
            plan.customers_new.append(data)
        else:
            row = db.q1("SELECT * FROM customers WHERE id = ?", (local,))
            if row and any((row[k] or "") != (data[k] or "") for k in ("name", "contact_person", "email", "phone")):
                data["local_id"] = local
                plan.customers_upd.append(data)

    # --- tuotavat kuittaukset
    start = _start_date()
    tc_cols = _cols(conn, "TaskConfirmations")
    hours_col = "HoursWorked" if "HoursWorked" in tc_cols else None
    if not hours_col:
        plan.warnings.append("Huoltokellon kuittauksissa ei ole työtuntikenttää (HoursWorked) – työaikoja ei voi tuoda.")
    confs = conn.execute(
        "SELECT tc.*, wt.Title AS TaskTitle, wt.CategoryId, wt.SiteId, wt.InvoiceId AS TaskInvoiceId, "
        "s.Name AS SiteName, s.Address AS SiteAddress, s.CustomerId, u.Email AS UserEmail, u.FullName AS UserName "
        "FROM TaskConfirmations tc JOIN WorkTasks wt ON wt.Id = tc.WorkTaskId "
        "LEFT JOIN Sites s ON s.Id = wt.SiteId LEFT JOIN AspNetUsers u ON u.Id = tc.UserId "
        "ORDER BY tc.ConfirmedAt").fetchall()
    cats = {r["Id"]: r for r in conn.execute("SELECT * FROM TaskCategories")}
    users_needed: dict[str, dict] = {}
    cats_needed: set = set()
    per_user_day: dict[tuple, list] = {}
    for c in confs:
        t = parse_hk_dt(c["ConfirmedAt"])
        if t is None:
            plan.skipped.append({"hk_id": c["Id"], "task": c["WorkTaskId"], "reason": "kuittausaika puuttuu"})
            continue
        local_t = to_local(t, plan.tz)
        if local_t.date() < start:
            continue
        if _mapped("confirmation", c["Id"]) is not None:
            continue
        hours = D(c[hours_col]) if hours_col and c[hours_col] not in (None, "") else Decimal(0)
        base = {"hk_id": c["Id"], "task": c["WorkTaskId"], "title": c["TaskTitle"] or "", "date": local_t.date(),
                "end": local_t, "hours": hours, "user": c["UserId"], "user_name": c["UserName"] or "",
                "email": (c["UserEmail"] or "").lower(), "category": c["CategoryId"], "customer_hk": c["CustomerId"],
                "site": " ".join(x for x in [c["SiteName"] or "", c["SiteAddress"] or ""] if x).strip(),
                "note": c["Note"] or "", "task_invoiced": c["TaskInvoiceId"] is not None}
        if not settings.get("hk.import_hours"):
            base["hours"] = Decimal(0)
        if hours <= 0 or hours > 16:
            if hours > 16:
                plan.skipped.append({**base, "reason": f"epätavallinen tuntimäärä {hours} h"})
            elif settings.get("hk.import_hours"):
                plan.skipped.append({**base, "reason": "kuittauksessa ei työtunteja"})
            base["hours"] = Decimal(0)
        if not c["UserId"]:
            plan.skipped.append({**base, "reason": "kuittaajaa ei tiedossa"})
            continue
        users_needed[c["UserId"]] = {"hk_id": c["UserId"], "email": base["email"], "name": base["user_name"]}
        cats_needed.add(c["CategoryId"])
        per_user_day.setdefault((c["UserId"], base["date"]), []).append(base)

    # --- työntekijät
    for uid, u in users_needed.items():
        if _mapped("user", uid) is not None:
            continue
        match = db.q1("SELECT id, name FROM employees WHERE lower(email) = ? AND email <> ''", (u["email"],)) \
            if u["email"] else None
        u["match_id"] = match["id"] if match else None
        if not match:
            plan.warnings.append(f"Uusi työntekijä {u['name'] or u['email']}: täydennä palkkatiedot (Rekisterit > "
                                 "Työntekijät) ennen palkkakoostetta.")
        plan.employees_new.append(u)

    # --- työlajit
    for cat_id in cats_needed:
        key = "none" if cat_id is None else str(cat_id)
        if _mapped("category", key) is not None:
            continue
        name = (cats[cat_id]["Name"] if cat_id in cats else "Huoltotyö (Huoltokello)") or "Huoltotyö"
        match = db.q1("SELECT id FROM work_types WHERE lower(name) = lower(?)", (name,))
        plan.worktypes_new.append({"hk_id": key, "name": name, "code": f"HK{cat_id if cat_id is not None else 0}",
                                   "match_id": match["id"] if match else None})
        if not match:
            plan.warnings.append(f"Uusi työlaji '{name}' hinnalla 0 € – aseta tuntihinta (Rekisterit > Työlajit).")

    # --- työajat (ketjutus päivän sisällä, uusimmasta vanhimpaan)
    for (_uid, _day), items in per_user_day.items():
        items.sort(key=lambda x: x["end"], reverse=True)
        next_start = None
        for it in items:
            if it["hours"] <= 0:
                it["start"] = it["end"]
                plan.entries.append({**it, "billable": 0, "bill_reason": "ei tunteja", "hours_only": False})
                continue
            end = it["end"] if next_start is None else min(it["end"], next_start)
            start_dt = end - dt.timedelta(minutes=int((it["hours"] * 60).to_integral_value()))
            if start_dt.date() != end.date():
                plan.skipped.append({**it, "reason": "työaika ulottuisi edelliselle päivälle – kirjaa käsin"})
                continue
            it["start"], it["end"] = start_dt, end
            next_start = start_dt
            contract = _active_contract(conn, it["customer_hk"], it["date"], has_contracts)
            if it["task_invoiced"]:
                bill, why = 0, "laskutettu jo Huoltokellossa"
            elif contract is not None and D(contract["MonthlyPrice"] or 0) > 0 and D(contract["HourlyRate"] or 0) == 0:
                bill, why = 0, "sisältyy kuukausisopimukseen"
            elif it["customer_hk"] is None:
                bill, why = 0, "ei asiakasta (sisäinen työ)"
            else:
                bill, why = 1, "laskutettava"
            plan.entries.append({**it, "billable": bill, "bill_reason": why, "hours_only": True})

    # --- asiakashinnat sopimuksista
    if has_contracts:
        today = dt.date.today()
        for c in conn.execute("SELECT * FROM Contracts"):
            rate = D(c["HourlyRate"] or 0)
            if rate <= 0:
                continue
            e = parse_hk_dt(c["EndDate"])
            if e is not None and e.date() < today:
                continue
            plan.prices.append({"customer_hk": c["CustomerId"], "rate": rate, "contract": c["Name"] or ""})

    # --- tarvikkeet
    if has_mats and settings.get("hk.import_materials"):
        planned = {e["hk_id"]: e for e in plan.entries}
        for m in conn.execute("SELECT * FROM ConfirmationMaterials"):
            e = planned.get(m["ConfirmationId"])
            if e is None or e["task_invoiced"] or e["customer_hk"] is None:
                continue
            if _mapped("material", m["Id"]) is not None:
                continue
            qty = D(m["Quantity"] or 0)
            if qty <= 0:
                continue
            plan.materials.append({"hk_id": m["Id"], "name": (m["Name"] or "Tarvike").strip()[:100],
                                   "unit": m["Unit"] or "kpl", "price": D(m["UnitPrice"] or 0), "qty": qty,
                                   "pass_through": bool(m["IsPassThrough"]), "conf": e})
    return plan


# ------------------------------------------------------------------ tuonti

def _map(kind, hk_id, local_id):
    db.ex("INSERT OR REPLACE INTO hk_map(kind, hk_id, local_id) VALUES(?,?,?)", (kind, str(hk_id), local_id))


def _next_emp_number() -> str:
    r = db.q1("SELECT MAX(CAST(number AS INTEGER)) AS m FROM employees WHERE number GLOB '[0-9]*'")
    return str(max(int(r["m"] or 0), 1000) + 1)


def _overlaps_local(emp_id: int, day: str, s: str, e: str) -> bool:
    for r in db.q("SELECT start_time, end_time FROM time_entries WHERE employee_id = ? AND work_date = ? "
                  "AND status <> 'hylatty'", (emp_id, day)):
        if s < r["end_time"] and r["start_time"] < e:
            return True
    return False


def apply_plan(plan: Plan, user_id=None) -> dict:
    res = {"tyoajat": 0, "tarvikkeet": 0, "ohitetut": [dict(reason=s["reason"], task=s.get("task")) for s in plan.skipped]}
    with db.tx():
        for c in plan.customers_new:
            if c.get("match_id"):
                _map("customer", c["hk_id"], c["match_id"])
                continue
            number = c["number"]
            if db.q1("SELECT 1 FROM customers WHERE number = ?", (number,)):
                number = f"{number}-{c['hk_id']}"
            cur = db.ex("INSERT INTO customers(number, name, contact_person, email, phone, street, postcode, city, notes) "
                        "VALUES(?,?,?,?,?,?,?,?,?)",
                        (number, c["name"], c["contact_person"], c["email"], c["phone"], c["street"], c["postcode"],
                         c["city"], "Tuotu Huoltokellosta – täydennä Y-tunnus ja laskutustiedot."))
            _map("customer", c["hk_id"], cur.lastrowid)
        for c in plan.customers_upd:
            db.ex("UPDATE customers SET name = ?, contact_person = ?, email = ?, phone = ? WHERE id = ?",
                  (c["name"], c["contact_person"], c["email"], c["phone"], c["local_id"]))
        for u in plan.employees_new:
            if u.get("match_id"):
                _map("user", u["hk_id"], u["match_id"])
                continue
            cur = db.ex("INSERT INTO employees(number, name, email) VALUES(?,?,?)",
                        (_next_emp_number(), u["name"] or u["email"] or "Huoltokello-käyttäjä", u["email"]))
            _map("user", u["hk_id"], cur.lastrowid)
        for w in plan.worktypes_new:
            if w.get("match_id"):
                _map("category", w["hk_id"], w["match_id"])
                continue
            code = w["code"]
            if db.q1("SELECT 1 FROM work_types WHERE code = ?", (code,)):
                code = f"{code}-{w['hk_id']}"
            cur = db.ex("INSERT INTO work_types(code, name, price) VALUES(?,?,?)", (code, w["name"], "0"))
            _map("category", w["hk_id"], cur.lastrowid)
        hk_cats = [r["local_id"] for r in db.q("SELECT local_id FROM hk_map WHERE kind = 'category'")]
        for pr in plan.prices:
            cust = _mapped("customer", pr["customer_hk"])
            if cust is None:
                continue
            for wt in hk_cats:
                db.ex("INSERT OR IGNORE INTO customer_prices(customer_id, work_type_id, price) VALUES(?,?,?)",
                      (cust, wt, str(pr["rate"])))
        locked = db.q("SELECT period_start, period_end FROM payroll_runs WHERE status = 'lukittu'")
        conf_to_entry = {}
        for e in plan.entries:
            emp = _mapped("user", e["user"])
            wt = _mapped("category", "none" if e["category"] is None else str(e["category"]))
            cust = _mapped("customer", e["customer_hk"]) if e["customer_hk"] is not None else None
            day = e["date"].isoformat()
            task_ref = f"HK-{e['task']}"
            if emp is None or wt is None:
                res["ohitetut"].append({"reason": "työntekijä tai työlaji puuttuu", "task": e["task"]})
                continue
            if not e["hours_only"]:
                _map("confirmation", e["hk_id"], 0)
                continue
            if any(lr["period_start"] <= day <= lr["period_end"] for lr in locked):
                res["ohitetut"].append({"reason": f"{day} kuuluu lukittuun palkkajaksoon", "task": e["task"]})
                continue
            dup = db.q1("SELECT id FROM time_entries WHERE employee_id = ? AND work_date = ? AND task_ref = ?",
                        (emp, day, task_ref))
            if dup:
                _map("confirmation", e["hk_id"], dup["id"])
                conf_to_entry[e["hk_id"]] = dup["id"]
                continue
            s, en = e["start"].strftime("%H:%M"), e["end"].strftime("%H:%M")
            if _overlaps_local(emp, day, s, en):
                res["ohitetut"].append({"reason": f"{day} {s}–{en} menee päällekkäin Taloudessa olevan kirjauksen "
                                                  "kanssa – tarkista, ettei työ ole jo kirjattu", "task": e["task"]})
                continue
            minutes = int((e["end"] - e["start"]).total_seconds() // 60)
            desc = e["title"] + (f" – {e['note']}" if e["note"] else "")
            if e["billable"] == 0 and e["bill_reason"] not in ("laskutettava",):
                desc += f" [{e['bill_reason']}]"
            cur = db.ex("INSERT INTO time_entries(employee_id, work_date, start_time, end_time, break_min, minutes, "
                        "work_type_id, customer_id, site, task_ref, description, billable, status, source, created_by) "
                        "VALUES(?,?,?,?,0,?,?,?,?,?,?,?, 'lahetetty', 'huoltokello', ?)",
                        (emp, day, s, en, minutes, wt, cust, e["site"][:200], task_ref, desc[:1000], e["billable"],
                         user_id))
            _map("confirmation", e["hk_id"], cur.lastrowid)
            conf_to_entry[e["hk_id"]] = cur.lastrowid
            res["tyoajat"] += 1
        for m in plan.materials:
            e = m["conf"]
            cust = _mapped("customer", e["customer_hk"])
            if cust is None:
                continue
            code = "HK-" + re.sub(r"[^A-Za-z0-9]+", "-", m["name"].upper())[:24].strip("-")
            mat = db.q1("SELECT id FROM materials WHERE code = ?", (code,))
            if mat is None:
                mid = db.ex("INSERT INTO materials(code, name, unit, sale_price) VALUES(?,?,?,?)",
                            (code, m["name"], m["unit"][:10], str(m["price"]))).lastrowid
            else:
                mid = mat["id"]
            emp = _mapped("user", e["user"])
            cur = db.ex("INSERT INTO material_entries(customer_id, material_id, employee_id, work_date, qty, site, "
                        "description, created_by) VALUES(?,?,?,?,?,?,?,?)",
                        (cust, mid, emp, e["date"].isoformat(), str(m["qty"]), e["site"][:200],
                         f"HK-{e['task']}" + (" (läpilaskutus)" if m["pass_through"] else ""), user_id))
            _map("material", m["hk_id"], cur.lastrowid)
            res["tarvikkeet"] += 1
    return res


def sync(mode: str = "kasin", user_id=None, path: str | None = None) -> dict:
    """Esikatselu + tuonti yhdellä kertaa. Kirjaa tuloksen synkronointilokiin."""
    try:
        conn = open_hk(path)
    except BridgeError as exc:
        _log(mode, False, {"virhe": str(exc)}, user_id)
        raise
    try:
        plan = build_plan(conn)
    finally:
        conn.close()
    res = apply_plan(plan, user_id)
    summary = {**plan.summary(), "tuotu_tyoaikoja": res["tyoajat"], "tuotu_tarvikkeita": res["tarvikkeet"],
               "ohitetut_syyt": [o["reason"] for o in res["ohitetut"]][:50], "aikavyohyke": plan.tz,
               "varoitukset": plan.warnings[:30]}
    settings.set("hk.last_sync", iso(utcnow()))
    _log(mode, True, summary, user_id)
    from .security import audit
    audit("huoltokello_synkronointi", "hk", mode, f"{res['tyoajat']} työaikaa, {res['tarvikkeet']} tarviketta")
    return summary


def _log(mode, ok, summary, user_id):
    db.ex("INSERT INTO hk_sync_log(ts, mode, ok, summary, user_id) VALUES(?,?,?,?,?)",
          (iso(utcnow()), mode, 1 if ok else 0, json.dumps(summary, ensure_ascii=False, default=str), user_id))


def due() -> bool:
    if not settings.get("hk.enabled"):
        return False
    last = settings.get_str("hk.last_sync")
    if not last:
        return True
    from .security import parse_iso
    return utcnow() - parse_iso(last) >= dt.timedelta(minutes=int(settings.get("hk.interval_minutes") or 15))
