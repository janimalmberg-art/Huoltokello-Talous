"""Työaikakirjaukset: kirjaus, leimaus (sisään/ulos), hyväksyntä ja tarvikekirjaukset.

Tilakone: luonnos -> lahetetty -> hyvaksytty -> laskutettu
                            \\-> hylatty -> (korjaus) -> lahetetty
Työntekijä muokkaa vain omia luonnos/hylätty-kirjauksiaan. Hyväksytyn kirjauksen voi
palauttaa vain työnjohto/johto, ja vain jos se ei ole laskulla tai lukitussa palkkajaksossa.
"""
from __future__ import annotations

import datetime as dt

from . import db
from .money import D
from .security import iso, utcnow
from .worktime import entry_minutes, parse_hhmm

EDITABLE = ("luonnos", "hylatty")
MAX_FUTURE_DAYS = 1
MAX_PAST_DAYS = 120


class EntryError(Exception):
    pass


def _validate(data: dict) -> dict:
    try:
        d = dt.date.fromisoformat(data["work_date"])
    except (KeyError, ValueError):
        raise EntryError("Virheellinen päivämäärä.")
    today = dt.date.today()
    if d > today + dt.timedelta(days=MAX_FUTURE_DAYS):
        raise EntryError("Työaikaa ei voi kirjata tulevaisuuteen.")
    if d < today - dt.timedelta(days=MAX_PAST_DAYS):
        raise EntryError(f"Yli {MAX_PAST_DAYS} päivää vanhaa työaikaa ei voi kirjata - ota yhteys työnjohtoon.")
    try:
        start = parse_hhmm(data["start_time"]).strftime("%H:%M")
        end = parse_hhmm(data["end_time"]).strftime("%H:%M")
        brk = int(data.get("break_min") or 0)
        minutes = entry_minutes(d.isoformat(), start, end, brk)
    except (KeyError, ValueError) as exc:
        raise EntryError(f"Virheellinen aika: {exc}")
    wt = db.q1("SELECT * FROM work_types WHERE id = ? AND active = 1", (int(data["work_type_id"]),))
    if wt is None:
        raise EntryError("Valitse työlaji.")
    cust = data.get("customer_id") or None
    if cust:
        if db.q1("SELECT 1 FROM customers WHERE id = ? AND active = 1", (int(cust),)) is None:
            raise EntryError("Asiakasta ei löydy.")
        cust = int(cust)
    billable = 1 if data.get("billable") in (1, "1", True, "on") and cust else 0
    km = D(data.get("km") or 0)
    if km < 0 or km > 2000:
        raise EntryError("Kilometrimäärä 0–2000.")
    allowance = data.get("allowance") or ""
    if allowance not in ("", "osa", "koko"):
        raise EntryError("Virheellinen päiväraha.")
    return {"work_date": d.isoformat(), "start_time": start, "end_time": end, "break_min": brk, "minutes": minutes,
            "work_type_id": wt["id"], "customer_id": cust, "site": (data.get("site") or "")[:200],
            "task_ref": (data.get("task_ref") or "")[:100], "description": (data.get("description") or "")[:1000],
            "billable": billable, "km": str(km), "allowance": allowance}


def _overlaps(employee_id: int, v: dict, exclude_id: int | None = None) -> bool:
    from .worktime import entry_interval
    s, e = entry_interval(v["work_date"], v["start_time"], v["end_time"])
    day = dt.date.fromisoformat(v["work_date"])
    rows = db.q("SELECT id, work_date, start_time, end_time FROM time_entries WHERE employee_id = ? "
                "AND work_date BETWEEN ? AND ? AND status <> 'hylatty' AND id <> ?",
                (employee_id, (day - dt.timedelta(days=1)).isoformat(), (day + dt.timedelta(days=1)).isoformat(),
                 exclude_id or -1))
    for r in rows:
        s2, e2 = entry_interval(r["work_date"], r["start_time"], r["end_time"])
        if s < e2 and s2 < e:
            return True
    return False


def create(employee_id: int, data: dict, user_id: int, source: str = "kasin", submit: bool = False) -> int:
    v = _validate(data)
    if _overlaps(employee_id, v):
        raise EntryError("Kirjaus menee päällekkäin aiemman kirjauksen kanssa.")
    status = "lahetetty" if submit else "luonnos"
    cur = db.ex("INSERT INTO time_entries(employee_id, work_date, start_time, end_time, break_min, minutes, "
                "work_type_id, customer_id, site, task_ref, description, billable, km, allowance, status, source, "
                "created_by) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (employee_id, v["work_date"], v["start_time"], v["end_time"], v["break_min"], v["minutes"],
                 v["work_type_id"], v["customer_id"], v["site"], v["task_ref"], v["description"], v["billable"],
                 v["km"], v["allowance"], status, source, user_id))
    return cur.lastrowid


def update(entry_id: int, data: dict, session) -> None:
    row = db.q1("SELECT * FROM time_entries WHERE id = ?", (entry_id,))
    if row is None:
        raise EntryError("Kirjausta ei löydy.")
    _check_edit_rights(row, session)
    v = _validate(data)
    if _overlaps(row["employee_id"], v, exclude_id=entry_id):
        raise EntryError("Kirjaus menee päällekkäin aiemman kirjauksen kanssa.")
    db.ex("UPDATE time_entries SET work_date=?, start_time=?, end_time=?, break_min=?, minutes=?, work_type_id=?, "
          "customer_id=?, site=?, task_ref=?, description=?, billable=?, km=?, allowance=?, status='luonnos', "
          "reject_reason='', updated_at=datetime('now') WHERE id = ?",
          (v["work_date"], v["start_time"], v["end_time"], v["break_min"], v["minutes"], v["work_type_id"],
           v["customer_id"], v["site"], v["task_ref"], v["description"], v["billable"], v["km"], v["allowance"],
           entry_id))


def _check_edit_rights(row, session) -> None:
    own = session["employee_id"] and row["employee_id"] == session["employee_id"]
    supervisor = session["role"] in ("johto", "tyonjohto")
    if row["status"] not in EDITABLE and not (supervisor and row["status"] == "lahetetty"):
        raise EntryError("Kirjausta ei voi enää muokata (lähetetty, hyväksytty tai laskutettu).")
    if not own and not supervisor:
        raise EntryError("Voit muokata vain omia kirjauksiasi.")


def delete(entry_id: int, session) -> None:
    row = db.q1("SELECT * FROM time_entries WHERE id = ?", (entry_id,))
    if row is None:
        raise EntryError("Kirjausta ei löydy.")
    _check_edit_rights(row, session)
    db.ex("DELETE FROM time_entries WHERE id = ? AND status IN ('luonnos','hylatty','lahetetty') "
          "AND invoice_id IS NULL", (entry_id,))


def submit(entry_ids: list[int], session) -> int:
    n = 0
    for eid in entry_ids:
        row = db.q1("SELECT * FROM time_entries WHERE id = ?", (eid,))
        if row and row["status"] in EDITABLE and (row["employee_id"] == session["employee_id"]
                                                  or session["role"] in ("johto", "tyonjohto")):
            db.ex("UPDATE time_entries SET status='lahetetty', updated_at=datetime('now') WHERE id = ?", (eid,))
            n += 1
    return n


def approve(entry_ids: list[int], user_id: int) -> int:
    n = 0
    now = iso(utcnow())
    for eid in entry_ids:
        cur = db.ex("UPDATE time_entries SET status='hyvaksytty', approved_by=?, approved_at=?, reject_reason='', "
                    "updated_at=datetime('now') WHERE id = ? AND status IN ('lahetetty','luonnos')",
                    (user_id, now, eid))
        n += cur.rowcount
    return n


def reject(entry_ids: list[int], reason: str) -> int:
    if not reason.strip():
        raise EntryError("Anna hylkäyksen syy.")
    n = 0
    for eid in entry_ids:
        cur = db.ex("UPDATE time_entries SET status='hylatty', reject_reason=?, updated_at=datetime('now') "
                    "WHERE id = ? AND status IN ('lahetetty','luonnos')", (reason.strip()[:300], eid))
        n += cur.rowcount
    return n


def reopen(entry_id: int) -> None:
    row = db.q1("SELECT * FROM time_entries WHERE id = ?", (entry_id,))
    if row is None or row["status"] != "hyvaksytty":
        raise EntryError("Vain hyväksytyn kirjauksen voi palauttaa.")
    if row["invoice_id"]:
        raise EntryError("Kirjaus on laskulla - poista se ensin laskuluonnokselta tai hyvitä lasku.")
    if row["payroll_run_id"]:
        raise EntryError("Kirjaus kuuluu lukittuun palkkajaksoon.")
    db.ex("UPDATE time_entries SET status='lahetetty', approved_by=NULL, approved_at=NULL WHERE id = ?", (entry_id,))


# ------------------------------------------------------------------ leimaus

def clock_in(employee_id: int, work_type_id: int, customer_id=None, site: str = "", task_ref: str = "") -> None:
    if db.q1("SELECT 1 FROM open_clocks WHERE employee_id = ?", (employee_id,)):
        raise EntryError("Olet jo leimannut sisään.")
    if db.q1("SELECT 1 FROM work_types WHERE id = ? AND active = 1", (work_type_id,)) is None:
        raise EntryError("Valitse työlaji.")
    now = dt.datetime.now().replace(second=0, microsecond=0)
    db.ex("INSERT INTO open_clocks(employee_id, started_at, work_type_id, customer_id, site, task_ref) "
          "VALUES(?,?,?,?,?,?)", (employee_id, now.isoformat(timespec="minutes"), work_type_id,
                                  customer_id or None, site[:200], task_ref[:100]))


def clock_out(employee_id: int, user_id: int, break_min: int = 0, description: str = "",
              billable: bool = True) -> int:
    oc = db.q1("SELECT * FROM open_clocks WHERE employee_id = ?", (employee_id,))
    if oc is None:
        raise EntryError("Et ole leimannut sisään.")
    start = dt.datetime.fromisoformat(oc["started_at"])
    end = dt.datetime.now().replace(second=0, microsecond=0)
    if end - start > dt.timedelta(hours=16):
        db.ex("DELETE FROM open_clocks WHERE employee_id = ?", (employee_id,))
        raise EntryError("Leimaus oli auki yli 16 tuntia - kirjaa työaika käsin.")
    if (end - start).total_seconds() < 60 * max(1, break_min + 1):
        db.ex("DELETE FROM open_clocks WHERE employee_id = ?", (employee_id,))
        raise EntryError("Leimaus oli liian lyhyt kirjattavaksi.")
    with db.tx():
        eid = create(employee_id, {
            "work_date": start.date().isoformat(), "start_time": start.strftime("%H:%M"),
            "end_time": end.strftime("%H:%M"), "break_min": break_min, "work_type_id": oc["work_type_id"],
            "customer_id": oc["customer_id"], "site": oc["site"], "task_ref": oc["task_ref"],
            "description": description, "billable": 1 if billable else 0}, user_id, source="leimaus")
        db.ex("DELETE FROM open_clocks WHERE employee_id = ?", (employee_id,))
    return eid


def add_material(customer_id: int, material_id: int, qty, work_date: str, user_id: int, employee_id=None,
                 site: str = "", description: str = "") -> int:
    qty = D(qty)
    if qty <= 0:
        raise EntryError("Määrän on oltava positiivinen.")
    dt.date.fromisoformat(work_date)
    if db.q1("SELECT 1 FROM materials WHERE id = ? AND active = 1", (material_id,)) is None:
        raise EntryError("Tarviketta ei löydy.")
    cur = db.ex("INSERT INTO material_entries(customer_id, material_id, employee_id, work_date, qty, site, "
                "description, created_by) VALUES(?,?,?,?,?,?,?,?)",
                (customer_id, material_id, employee_id, work_date, str(qty), site[:200], description[:500], user_id))
    return cur.lastrowid
