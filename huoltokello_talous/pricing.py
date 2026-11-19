"""Työn hinnoittelu johdolle ja asiakashinnoittelu.

Omakustannushinta laskutettavaa tuntia kohden:
    palkka × (1 + lakisääteiset sivukulut % + välilliset palkkakustannukset %)
    ÷ laskutettavuusaste + yleiskulut €/h
Hintasuositus tavoitekatteella: omakustannus ÷ (1 − tavoitekate %).
"""
from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from decimal import Decimal

from . import db, settings
from .invoicing import billable_hours, price_for
from .money import D, r2, vat_amount, ZERO

H100 = Decimal(100)


@dataclass
class CostCalc:
    wage: Decimal
    statutory_pct: Decimal
    indirect_pct: Decimal
    billable_ratio_pct: Decimal
    overhead_per_hour: Decimal
    target_margin_pct: Decimal

    @property
    def employer_cost_per_paid_hour(self) -> Decimal:
        return r2(self.wage * (H100 + self.statutory_pct + self.indirect_pct) / H100)

    @property
    def cost_per_billable_hour(self) -> Decimal:
        ratio = self.billable_ratio_pct / H100
        if ratio <= 0:
            raise ValueError("Laskutettavuusasteen on oltava yli 0 %.")
        return r2(self.employer_cost_per_paid_hour / ratio + self.overhead_per_hour)

    @property
    def suggested_price(self) -> Decimal:
        m = self.target_margin_pct / H100
        if m >= 1:
            raise ValueError("Tavoitekatteen on oltava alle 100 %.")
        return r2(self.cost_per_billable_hour / (1 - m))

    def margin_at(self, price) -> tuple[Decimal, Decimal]:
        price = D(price)
        cost = self.cost_per_billable_hour
        eur = r2(price - cost)
        pct = r2(eur / price * H100) if price else ZERO
        return eur, pct


def calc_from_settings(wage, **override) -> CostCalc:
    s = settings.all_settings()
    vals = {
        "statutory_pct": D(override.get("statutory_pct", s["pricing.statutory_pct"])),
        "indirect_pct": D(override.get("indirect_pct", s["pricing.indirect_pct"])),
        "billable_ratio_pct": D(override.get("billable_ratio_pct", s["pricing.billable_ratio_pct"])),
        "overhead_per_hour": D(override.get("overhead_per_hour", s["pricing.overhead_per_hour"])),
        "target_margin_pct": D(override.get("target_margin_pct", s["pricing.target_margin_pct"])),
    }
    return CostCalc(wage=D(wage), **vals)


def average_wage() -> Decimal:
    rows = db.q("SELECT pay_type, hourly_wage, monthly_salary FROM employees WHERE active = 1")
    wages = []
    for r in rows:
        if r["pay_type"] == "kk" and D(r["monthly_salary"]) > 0:
            wages.append(r2(D(r["monthly_salary"]) / Decimal(160)))
        elif D(r["hourly_wage"]) > 0:
            wages.append(D(r["hourly_wage"]))
    return r2(sum(wages, ZERO) / len(wages)) if wages else ZERO


def profitability(start: str, end: str) -> dict:
    """Kateanalyysi: laskutettu tuotto vs. toteutunut työvoimakustannus työlajeittain ja asiakkaittain."""
    s = settings.all_settings()
    factor = (H100 + D(s["pricing.statutory_pct"]) + D(s["pricing.indirect_pct"])) / H100
    rows = db.q("SELECT t.*, e.pay_type, e.hourly_wage, e.monthly_salary, w.name AS wt_name, w.code AS wt_code, "
                "c.name AS customer_name FROM time_entries t JOIN employees e ON e.id = t.employee_id "
                "JOIN work_types w ON w.id = t.work_type_id LEFT JOIN customers c ON c.id = t.customer_id "
                "WHERE t.work_date BETWEEN ? AND ? AND t.status IN ('hyvaksytty','laskutettu')", (start, end))
    wts = {r["id"]: r for r in db.q("SELECT * FROM work_types")}
    by_wt: dict[str, dict] = {}
    by_cust: dict[str, dict] = {}
    tot = {"hours": ZERO, "billable_hours": ZERO, "revenue": ZERO, "cost": ZERO}
    for r in rows:
        wage = D(r["hourly_wage"]) if r["pay_type"] == "tunti" else r2(D(r["monthly_salary"]) / Decimal(160))
        hours = Decimal(r["minutes"]) / Decimal(60)
        cost = r2(hours * wage * factor)
        revenue = ZERO
        bh = ZERO
        if r["billable"] and r["customer_id"]:
            bh = billable_hours(r["minutes"], wts[r["work_type_id"]])
            revenue = r2(bh * price_for(r["customer_id"], wts[r["work_type_id"]]))
        for key, bucket in ((f"{r['wt_code']} {r['wt_name']}", by_wt), (r["customer_name"] or "(sisäinen työ)", by_cust)):
            b = bucket.setdefault(key, {"hours": ZERO, "billable_hours": ZERO, "revenue": ZERO, "cost": ZERO})
            b["hours"] += hours
            b["billable_hours"] += bh
            b["revenue"] += revenue
            b["cost"] += cost
        tot["hours"] += hours
        tot["billable_hours"] += bh
        tot["revenue"] += revenue
        tot["cost"] += cost

    def finish(d):
        for v in d.values():
            v["hours"] = r2(v["hours"])
            v["margin"] = r2(v["revenue"] - v["cost"])
            v["margin_pct"] = r2(v["margin"] / v["revenue"] * H100) if v["revenue"] else ZERO
            v["utilization_pct"] = r2(v["billable_hours"] / v["hours"] * H100) if v["hours"] else ZERO
        return dict(sorted(d.items(), key=lambda kv: -kv[1]["revenue"]))

    finish({"_": tot})
    return {"by_work_type": finish(by_wt), "by_customer": finish(by_cust), "total": tot}


def quote_totals(lines: list[dict]) -> tuple[list[dict], Decimal, Decimal, Decimal]:
    out = []
    tn = tv = ZERO
    for ln in lines:
        qty, price, rate = D(ln["qty"]), D(ln["unit_price"]), D(ln["vat_rate"])
        net = r2(qty * price)
        vat = vat_amount(net, rate)
        out.append({**{k: str(v) for k, v in ln.items()}, "net": str(net), "vat": str(vat), "gross": str(net + vat)})
        tn += net
        tv += vat
    return out, tn, tv, tn + tv


def create_quote(customer_id, customer_name: str, title: str, lines: list[dict], valid_days: int, user_id: int) -> int:
    if not lines:
        raise ValueError("Tarjouksella ei ole rivejä.")
    calc_lines, tn, tv, tg = quote_totals(lines)
    with db.tx():
        number = settings.next_sequence("invoice.next_quote_number")
        today = dt.date.today()
        cur = db.ex("INSERT INTO quotes(number, customer_id, customer_name, quote_date, valid_until, title, lines, "
                    "total_net, total_vat, total_gross, created_by) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (number, customer_id, customer_name, today.isoformat(),
                     (today + dt.timedelta(days=valid_days)).isoformat(), title,
                     json.dumps(calc_lines, ensure_ascii=False), str(tn), str(tv), str(tg), user_id))
    return cur.lastrowid


def estimate_lines(customer_id, items: list[tuple[int, Decimal]]) -> list[dict]:
    """Työlaji + tuntiarvio -> tarjousrivit asiakkaan hinnastolla."""
    out = []
    for wt_id, hours in items:
        wt = db.q1("SELECT * FROM work_types WHERE id = ?", (wt_id,))
        if not wt or D(hours) <= 0:
            continue
        out.append({"description": wt["name"], "qty": D(hours), "unit": wt["unit"],
                    "unit_price": price_for(customer_id, wt), "vat_rate": D(wt["vat_rate"])})
    return out
