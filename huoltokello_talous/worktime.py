"""Työajan laskenta: kirjausten kesto, aikalisät, ylityöt ja arkipyhät.

Oletussäännöt noudattavat työaikalakia (872/2019):
- säännöllinen työaika 8 h/vrk ja 40 h/vko (TAL 5 §)
- vuorokautinen ylityö: 2 ensimmäistä tuntia +50 %, seuraavat +100 % (TAL 20 §)
- viikoittainen ylityö +50 % (TAL 20 §), vain vuorokautisen ylityön lisäksi jäävä osa
- sunnuntai- ja kirkollisena juhlapäivänä tehty työ +100 % (TAL 16 §)
Ilta-, yö- ja lauantailisät ovat TES-kohtaisia euromääriä (asetuksissa).
Työehtosopimus voi määrätä toisin: tarkista oma TES ja säädä asetukset.
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

from .money import D, r2

MIN_PER_H = Decimal(60)


def easter_sunday(year: int) -> dt.date:
    """Pääsiäispäivä (anonyymi gregoriaaninen algoritmi)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l_ = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l_) // 451
    month, day = divmod(h + l_ - 7 * m + 114, 31)
    return dt.date(year, month, day + 1)


def _weekday_between(year: int, month: int, day_from: int, day_to: int, weekday: int) -> dt.date:
    for d in range(day_from, day_to + 1):
        cand = dt.date(year, month, d)
        if cand.weekday() == weekday:
            return cand
    raise ValueError


def finnish_holidays(year: int) -> dict[dt.date, str]:
    """Suomen juhlapyhät ja aatot. Avain: päivämäärä, arvo: nimi."""
    e = easter_sunday(year)
    pyhainpaiva = (_weekday_between(year, 10, 31, 31, 5) if dt.date(year, 10, 31).weekday() == 5
                   else _weekday_between(year, 11, 1, 6, 5))
    return {
        dt.date(year, 1, 1): "Uudenvuodenpäivä",
        dt.date(year, 1, 6): "Loppiainen",
        e - dt.timedelta(days=2): "Pitkäperjantai",
        e: "Pääsiäispäivä",
        e + dt.timedelta(days=1): "2. pääsiäispäivä",
        dt.date(year, 5, 1): "Vappu",
        e + dt.timedelta(days=39): "Helatorstai",
        e + dt.timedelta(days=49): "Helluntaipäivä",
        _weekday_between(year, 6, 19, 25, 4): "Juhannusaatto",
        _weekday_between(year, 6, 20, 26, 5): "Juhannuspäivä",
        pyhainpaiva: "Pyhäinpäivä",
        dt.date(year, 12, 6): "Itsenäisyyspäivä",
        dt.date(year, 12, 24): "Jouluaatto",
        dt.date(year, 12, 25): "Joulupäivä",
        dt.date(year, 12, 26): "Tapaninpäivä",
    }


AATOT = {"Juhannusaatto", "Jouluaatto"}


def is_sunday_like(day: dt.date, holiday_as_sunday: bool = True) -> bool:
    if day.weekday() == 6:
        return True
    if not holiday_as_sunday:
        return False
    name = finnish_holidays(day.year).get(day)
    return bool(name) and name not in AATOT


def parse_hhmm(s: str) -> dt.time:
    s = (s or "").strip().replace(".", ":")
    if ":" not in s and s.isdigit() and len(s) in (3, 4):
        s = s[:-2] + ":" + s[-2:]
    h, m = s.split(":")
    t = dt.time(int(h) % 24, int(m))
    if int(h) not in range(0, 25) or int(m) not in range(0, 60):
        raise ValueError("Virheellinen kellonaika.")
    return t


def entry_interval(work_date: str, start: str, end: str) -> tuple[dt.datetime, dt.datetime]:
    d = dt.date.fromisoformat(work_date)
    s = dt.datetime.combine(d, parse_hhmm(start))
    e = dt.datetime.combine(d, parse_hhmm(end))
    if e <= s:  # yön yli
        e += dt.timedelta(days=1)
    if e - s > dt.timedelta(hours=24):
        raise ValueError("Kirjaus ei voi olla yli 24 tuntia.")
    return s, e


def entry_minutes(work_date: str, start: str, end: str, break_min: int) -> int:
    s, e = entry_interval(work_date, start, end)
    total = int((e - s).total_seconds() // 60)
    if break_min < 0 or break_min >= total:
        raise ValueError("Tauko ei voi olla negatiivinen eikä yhtä pitkä kuin koko työaika.")
    return total - break_min


def _window_minutes(s: dt.datetime, e: dt.datetime, w_start: dt.time, w_end: dt.time) -> int:
    """Montako minuuttia välistä [s,e) osuu päivittäiseen aikaikkunaan (ikkuna voi ylittää keskiyön)."""
    total = 0
    day = s.date() - dt.timedelta(days=1)
    while dt.datetime.combine(day, dt.time()) < e:
        ws = dt.datetime.combine(day, w_start)
        we = dt.datetime.combine(day, w_end)
        if we <= ws:
            we += dt.timedelta(days=1)
        lo, hi = max(s, ws), min(e, we)
        if hi > lo:
            total += int((hi - lo).total_seconds() // 60)
        day += dt.timedelta(days=1)
    return total


@dataclass
class DayCategory:
    minutes: int = 0
    evening: int = 0
    night: int = 0
    saturday: int = 0
    sunday: int = 0


def classify_entry(work_date: str, start: str, end: str, break_min: int, rules: dict) -> DayCategory:
    """Jakaa kirjauksen ilta-, yö-, lauantai- ja sunnuntai/pyhäminuutteihin.

    Tauko vähennetään suhteessa kaikista luokista.
    """
    s, e = entry_interval(work_date, start, end)
    gross = int((e - s).total_seconds() // 60)
    net = gross - break_min
    ratio = Decimal(net) / Decimal(gross) if gross else Decimal(0)
    ev = _window_minutes(s, e, parse_hhmm(rules["work.evening_start"]), parse_hhmm(rules["work.evening_end"]))
    ni = _window_minutes(s, e, parse_hhmm(rules["work.night_start"]), parse_hhmm(rules["work.night_end"]))
    sat = sun = 0
    cur = s
    has = bool(rules.get("work.holiday_as_sunday", True))
    while cur < e:
        nxt = min(e, dt.datetime.combine(cur.date() + dt.timedelta(days=1), dt.time()))
        mins = int((nxt - cur).total_seconds() // 60)
        if is_sunday_like(cur.date(), has):
            sun += mins
        elif cur.date().weekday() == 5:
            sat += mins
        cur = nxt

    def sc(x: int) -> int:
        return int((Decimal(x) * ratio).to_integral_value())

    return DayCategory(minutes=net, evening=sc(ev), night=sc(ni), saturday=sc(sat), sunday=sc(sun))


@dataclass
class EmployeeSummary:
    employee_id: int
    number: str
    name: str
    pay_type: str
    hourly_wage: Decimal
    monthly_salary: Decimal
    cost_center: str
    minutes_total: int = 0
    minutes_regular: int = 0
    ot50_daily: int = 0
    ot100_daily: int = 0
    ot50_weekly: int = 0
    evening: int = 0
    night: int = 0
    saturday: int = 0
    sunday: int = 0
    billable_minutes: int = 0
    km: Decimal = Decimal(0)
    allowance_partial: int = 0
    allowance_full: int = 0
    days: dict = field(default_factory=dict)

    def h(self, minutes: int) -> Decimal:
        return (Decimal(minutes) / MIN_PER_H).quantize(Decimal("0.01"))


def summarize(entries: list[dict], employees: dict[int, dict], period_start: dt.date, period_end: dt.date,
              rules: dict) -> dict[int, EmployeeSummary]:
    """Laskee työntekijäkohtaisen palkkakoosteen.

    `entries` sisältää kirjaukset koko niiltä ISO-viikoilta, jotka leikkaavat kautta,
    jotta viikkoylityö lasketaan oikein. Viikkoylityö kohdistetaan viikon viimeiselle
    työpäivälle ja otetaan mukaan, jos se päivä kuuluu kauteen.
    """
    daily_reg = int(D(rules["work.daily_regular_hours"]) * 60)
    weekly_reg_default = int(D(rules["work.weekly_regular_hours"]) * 60)
    ot_first = int(D(rules["work.daily_ot_first_hours"]) * 60)

    per_emp_day: dict[int, dict[dt.date, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for en in entries:
        per_emp_day[en["employee_id"]][dt.date.fromisoformat(en["work_date"])].append(en)

    out: dict[int, EmployeeSummary] = {}
    for emp_id, days in per_emp_day.items():
        emp = employees[emp_id]
        weekly_reg = int(D(emp.get("weekly_hours") or rules["work.weekly_regular_hours"]) * 60) or weekly_reg_default
        s = EmployeeSummary(emp_id, emp["number"], emp["name"], emp["pay_type"], D(emp["hourly_wage"]),
                            D(emp["monthly_salary"]), emp.get("cost_center") or "")
        weeks: dict[tuple[int, int], list[dt.date]] = defaultdict(list)
        day_regular: dict[dt.date, int] = {}
        for day in sorted(days):
            weeks[day.isocalendar()[:2]].append(day)
            day_min = 0
            cat_total = DayCategory()
            for en in days[day]:
                c = classify_entry(en["work_date"], en["start_time"], en["end_time"], int(en["break_min"]), rules)
                day_min += c.minutes
                cat_total.evening += c.evening
                cat_total.night += c.night
                cat_total.saturday += c.saturday
                cat_total.sunday += c.sunday
                if period_start <= day <= period_end:
                    if en.get("billable"):
                        s.billable_minutes += c.minutes
                    s.km += D(en.get("km") or 0)
                    if en.get("allowance") == "osa":
                        s.allowance_partial += 1
                    elif en.get("allowance") == "koko":
                        s.allowance_full += 1
            daily_ot = max(0, day_min - daily_reg)
            day_regular[day] = day_min - daily_ot
            if period_start <= day <= period_end:
                s.minutes_total += day_min
                s.ot50_daily += min(daily_ot, ot_first)
                s.ot100_daily += max(0, daily_ot - ot_first)
                s.evening += cat_total.evening
                s.night += cat_total.night
                s.saturday += cat_total.saturday
                s.sunday += cat_total.sunday
                s.days[day.isoformat()] = day_min
        for _wk, wdays in weeks.items():
            reg_sum = sum(day_regular[d] for d in wdays)
            weekly_ot = max(0, reg_sum - weekly_reg)
            last = max(wdays)
            if weekly_ot and period_start <= last <= period_end:
                s.ot50_weekly += weekly_ot
        s.minutes_regular = s.minutes_total - s.ot50_daily - s.ot100_daily - s.ot50_weekly
        out[emp_id] = s
    return out


@dataclass
class PayLine:
    code: str
    name: str
    qty: Decimal
    unit: str
    unit_price: Decimal
    amount: Decimal
    income_type: str = ""   # tulorekisterin tulolaji


# Palkkalajit ja tulorekisterin tulolajit (vero.fi, Palkkojen tulolajit: 201, 206, 211, 221,
# 235, 236, 311, 331). Ylityöstä ilmoitetaan tulorekisteriin vain korotusosa (235).
PAY_CODES = {
    "PERUS": ("Perustuntipalkka", "201"),
    "KK": ("Kuukausipalkka", "201"),
    "YT50": ("Ylityö +50 % (vrk)", "235"),
    "YT100": ("Ylityö +100 % (vrk)", "235"),
    "VYT50": ("Viikkoylityö +50 %", "235"),
    "ILTA": ("Iltatyökorvaus", "206"),
    "YO": ("Yötyökorvaus", "236"),
    "LA": ("Lauantaityökorvaus", "211"),
    "SU": ("Sunnuntaityökorvaus +100 %", "221"),
    "KM": ("Kilometrikorvaus (verovapaa)", "311"),
    "PR_OSA": ("Osapäiväraha (verovapaa)", "331"),
    "PR_KOKO": ("Kokopäiväraha (verovapaa)", "331"),
}


def pay_lines(s: EmployeeSummary, rules: dict) -> list[PayLine]:
    """Muuntaa koosteen palkkariveiksi (arvio; lopullinen laskenta palkanlaskennassa)."""
    lines: list[PayLine] = []
    wage = s.hourly_wage

    def add(code, qty, unit, price):
        qty = D(qty)
        if qty == 0:
            return
        name, tulolaji = PAY_CODES[code]
        lines.append(PayLine(code, name, qty, unit, r2(price), r2(qty * D(price)), tulolaji))

    if s.pay_type == "kk":
        add("KK", 1, "kk", s.monthly_salary)
        # Kuukausipalkkaisen ylityön tuntipalkka: kk-palkka / jakaja (oletus 160)
        wage = r2(s.monthly_salary / Decimal(160)) if s.monthly_salary else wage
    else:
        add("PERUS", s.h(s.minutes_total), "h", wage)
    if s.ot50_daily:
        add("YT50", s.h(s.ot50_daily), "h", wage * Decimal("0.5"))
    if s.ot100_daily:
        add("YT100", s.h(s.ot100_daily), "h", wage)
    if s.ot50_weekly:
        add("VYT50", s.h(s.ot50_weekly), "h", wage * Decimal("0.5"))
    add("ILTA", s.h(s.evening), "h", D(rules["work.evening_bonus_eur"]))
    add("YO", s.h(s.night), "h", D(rules["work.night_bonus_eur"]))
    add("LA", s.h(s.saturday), "h", D(rules["work.saturday_bonus_eur"]))
    add("SU", s.h(s.sunday), "h", wage * D(rules["work.sunday_pct"]) / Decimal(100))
    add("KM", s.km, "km", D(rules["work.km_rate"]))
    add("PR_OSA", s.allowance_partial, "pv", D(rules["work.allowance_partial"]))
    add("PR_KOKO", s.allowance_full, "pv", D(rules["work.allowance_full"]))
    return [ln for ln in lines if ln.amount != 0 or ln.code in ("PERUS", "KK")]
