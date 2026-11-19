"""Palkanlaskennan koosteet työajanseurannasta.

Kooste lasketaan vain hyväksytyistä kirjauksista. Lukittu palkkajakso kiinnittää
kirjaukset (payroll_run_id), jolloin niitä ei voi enää palauttaa muokattavaksi.
Varsinainen palkanlaskenta (ennakonpidätys, tulorekisteri-ilmoitus) tehdään
palkanlaskentaohjelmassa - tämä moduuli tuottaa sille valmiit palkkarivit.
"""
from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal

from . import db, ledger, settings
from .money import D, r2, ZERO
from .worktime import EmployeeSummary, pay_lines, summarize


def _week_bounds(start: dt.date, end: dt.date) -> tuple[dt.date, dt.date]:
    return start - dt.timedelta(days=start.weekday()), end + dt.timedelta(days=6 - end.weekday())


def build(period_start: str, period_end: str) -> dict:
    ps, pe = dt.date.fromisoformat(period_start), dt.date.fromisoformat(period_end)
    if pe < ps:
        raise ValueError("Jakson loppu ennen alkua.")
    ws, we = _week_bounds(ps, pe)
    entries = [dict(r) for r in db.q(
        "SELECT * FROM time_entries WHERE work_date BETWEEN ? AND ? AND status IN ('hyvaksytty','laskutettu')",
        (ws.isoformat(), we.isoformat()))]
    employees = {r["id"]: dict(r) for r in db.q("SELECT * FROM employees")}
    rules = settings.all_settings()
    sums = summarize(entries, employees, ps, pe, rules)
    unapproved = db.q("SELECT e.name, COUNT(*) AS n FROM time_entries t JOIN employees e ON e.id = t.employee_id "
                      "WHERE t.work_date BETWEEN ? AND ? AND t.status IN ('luonnos','lahetetty','hylatty') "
                      "GROUP BY e.name", (period_start, period_end))
    result = {"period_start": period_start, "period_end": period_end, "employees": [],
              "warnings": [f"{r['name']}: {r['n']} hyväksymätöntä kirjausta jaksolla" for r in unapproved],
              "totals": {"amount": ZERO, "taxable": ZERO, "tax_free": ZERO}}
    for emp_id in sorted(sums, key=lambda k: sums[k].number):
        s: EmployeeSummary = sums[emp_id]
        lines = pay_lines(s, rules)
        taxable = sum((ln.amount for ln in lines if ln.income_type not in ("311", "331")), ZERO)
        tax_free = sum((ln.amount for ln in lines if ln.income_type in ("311", "331")), ZERO)
        result["employees"].append({
            "employee_id": emp_id, "number": s.number, "name": s.name, "cost_center": s.cost_center,
            "hours_total": s.h(s.minutes_total), "hours_regular": s.h(s.minutes_regular),
            "ot50_daily": s.h(s.ot50_daily), "ot100_daily": s.h(s.ot100_daily), "ot50_weekly": s.h(s.ot50_weekly),
            "evening": s.h(s.evening), "night": s.h(s.night), "saturday": s.h(s.saturday),
            "sunday": s.h(s.sunday), "billable_hours": s.h(s.billable_minutes), "km": s.km,
            "allowance_partial": s.allowance_partial, "allowance_full": s.allowance_full,
            "days": s.days,
            "lines": [ln.__dict__ for ln in lines], "taxable": r2(taxable), "tax_free": r2(tax_free),
        })
        result["totals"]["taxable"] += taxable
        result["totals"]["tax_free"] += tax_free
    result["totals"]["amount"] = r2(result["totals"]["taxable"] + result["totals"]["tax_free"])
    return result


def _jsonable(o):
    if isinstance(o, Decimal):
        return str(o)
    raise TypeError(type(o))


def save_run(period_start: str, period_end: str, user_id: int) -> int:
    data = build(period_start, period_end)
    cur = db.ex("INSERT INTO payroll_runs(period_start, period_end, data, created_by) VALUES(?,?,?,?)",
                (period_start, period_end, json.dumps(data, default=_jsonable, ensure_ascii=False), user_id))
    return cur.lastrowid


def lock_run(run_id: int, user_id: int, post_accrual: bool = False) -> None:
    run = db.q1("SELECT * FROM payroll_runs WHERE id = ?", (run_id,))
    if run is None or run["status"] != "luonnos":
        raise ValueError("Vain luonnosjakson voi lukita.")
    clash = db.q1("SELECT id FROM payroll_runs WHERE status = 'lukittu' AND NOT (period_end < ? OR period_start > ?)",
                  (run["period_start"], run["period_end"]))
    if clash:
        raise ValueError("Jakso menee päällekkäin aiemmin lukitun palkkajakson kanssa.")
    # Lasketaan uudelleen lukitushetkellä, jotta kooste vastaa kiinnitettyjä kirjauksia
    data = build(run["period_start"], run["period_end"])
    from .security import iso, utcnow
    with db.tx() as conn:
        conn.execute("UPDATE payroll_runs SET status='lukittu', data=?, locked_at=? WHERE id = ?",
                     (json.dumps(data, default=_jsonable, ensure_ascii=False), iso(utcnow()), run_id))
        conn.execute("UPDATE time_entries SET payroll_run_id = ? WHERE work_date BETWEEN ? AND ? "
                     "AND status IN ('hyvaksytty','laskutettu')", (run_id, run["period_start"], run["period_end"]))
        if post_accrual and data["employees"]:
            post_accrual_journal(data, run["period_end"], user_id, run_id)


def post_accrual_journal(data: dict, entry_date: str, user_id: int, run_id: int) -> int:
    """Palkkojen jaksotus kirjanpitoon (muistio): palkat ja arvioidut sivukulut siirtovelkoihin."""
    s = settings.all_settings()
    taxable = D(data["totals"]["taxable"])
    tax_free = D(data["totals"]["tax_free"])
    pension = r2(taxable * Decimal("17.10") / 100)
    sotu = r2(taxable * Decimal("1.91") / 100)
    other = r2(taxable * (D(s["pricing.statutory_pct"]) - Decimal("19.01")) / 100)
    lines = [
        {"account": s["acc.wages"], "debit": taxable, "description": "Palkat (jaksotus)"},
        {"account": s["acc.pension"], "debit": pension, "description": "TyEL työnantajan osuus (arvio)"},
        {"account": s["acc.social_security"], "debit": sotu, "description": "Sairausvakuutusmaksu 1,91 %"},
        {"account": s["acc.other_side_costs"], "debit": max(other, ZERO), "description": "Muut sivukulut (arvio)"},
        {"account": s["acc.travel"], "debit": tax_free, "description": "Km-korvaukset ja päivärahat"},
    ]
    total = sum((D(x["debit"]) for x in lines), ZERO)
    lines.append({"account": s["acc.accrued_liabilities"], "credit": total, "description": "Palkkojen siirtovelka"})
    return ledger.post("PA", entry_date, f"Palkkajakso {data['period_start']}–{data['period_end']} (jaksotus)",
                       lines, "payroll", run_id, user_id)
