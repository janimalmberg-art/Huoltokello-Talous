"""Raporttien HTML- ja CSV-muodot (käyttöliittymä, tulostus ja kirjanpitäjän paketti)."""
from __future__ import annotations

import datetime as dt
import html
from decimal import Decimal

from . import db, ledger, settings
from .exports.profiles import rows_to_csv
from .finref import code128c_svg, muotoile_iban, muotoile_rf, muotoile_viite, virtuaaliviivakoodi
from .money import D, fmt_dec, fmt_eur, ZERO

e = html.escape

PRINT_CSS = """
*{box-sizing:border-box}body{font-family:Segoe UI,Arial,sans-serif;color:#111827;margin:24px;font-size:13px}
h1{font-size:20px;margin:0 0 4px}h2{font-size:15px;margin:18px 0 6px}
table{border-collapse:collapse;width:100%;margin:6px 0 12px}th,td{padding:4px 6px;border-bottom:1px solid #e5e7eb;
text-align:left;vertical-align:top}th{background:#f3f4f6;font-weight:600}td.n,th.n{text-align:right;white-space:nowrap}
tr.sum td{font-weight:700;border-top:2px solid #111827}.muted{color:#6b7280}.small{font-size:11px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}.box{border:1px solid #d1d5db;padding:10px;border-radius:6px}
.warn{background:#fef3c7;padding:8px;border-radius:6px}.right{text-align:right}
@media print{body{margin:10mm}.noprint{display:none}}
"""


def fi(d: str | None) -> str:
    if not d:
        return ""
    x = dt.date.fromisoformat(d[:10])
    return f"{x.day}.{x.month}.{x.year}"


def page(title: str, body: str) -> str:
    return (f"<!DOCTYPE html><html lang='fi'><head><meta charset='utf-8'><title>{e(title)}</title>"
            f"<style>{PRINT_CSS}</style></head><body>{body}</body></html>")


def _company_header() -> str:
    s = settings.all_settings()
    return (f"<div class='muted small'>{e(s['company.name'])} · Y-tunnus {e(s['company.business_id'])} · "
            f"tulostettu {dt.datetime.now().strftime('%d.%m.%Y %H:%M')}</div>")


# ----------------------------------------------------------------- lasku

def invoice_html(bundle: dict) -> str:
    inv, lines, cust, comp = bundle["invoice"], bundle["lines"], bundle["customer"], bundle["company"]
    credit = inv["kind"] == "hyvitys"
    title = "HYVITYSLASKU" if credit else "LASKU"
    if inv["status"] == "luonnos":
        title += " (LUONNOS – ei kelpaa maksuun)"
    rows = ""
    for ln in lines:
        rows += (f"<tr><td>{e(ln['description'])}</td><td class='n'>{fmt_dec(ln['qty'])} {e(ln['unit'])}</td>"
                 f"<td class='n'>{fmt_eur(ln['unit_price'], False)}</td>"
                 f"<td class='n'>{fmt_dec(ln['discount_pct'], 1) if D(ln['discount_pct']) else ''}</td>"
                 f"<td class='n'>{fmt_dec(ln['vat_rate'], 1)} %</td><td class='n'>{fmt_eur(ln['net'], False)}</td>"
                 f"<td class='n'>{fmt_eur(ln['vat'], False)}</td><td class='n'>{fmt_eur(ln['gross'], False)}</td></tr>")
    spec = "".join(f"<tr><td>{e(ledger.VAT_CODES.get(code, (code,))[0])}</td><td class='n'>{fmt_dec(rate, 1)} %</td>"
                   f"<td class='n'>{fmt_eur(b)}</td><td class='n'>{fmt_eur(v)}</td></tr>"
                   for (code, rate), (b, v) in bundle["vat_spec"].items())
    ref = inv["rf_reference"] or inv["reference"]
    ref_txt = muotoile_rf(ref) if ref.upper().startswith("RF") else muotoile_viite(ref)
    barcode = ""
    if inv["status"] != "luonnos" and not credit and D(inv["total_gross"]) > 0 and comp.get("iban"):
        try:
            code = virtuaaliviivakoodi(comp["iban"], inv["total_gross"], ref, dt.date.fromisoformat(inv["due_date"]))
            barcode = (f"<div class='box'><b>Virtuaaliviivakoodi</b><br><code>{code}</code><br>"
                       f"{code128c_svg(code, height=46, module=1.0)}</div>")
        except ValueError as exc:
            barcode = f"<div class='warn'>Viivakoodia ei muodostettu: {e(str(exc))}</div>"
    hh = ""
    if cust.get("is_consumer") and D(inv["household_work"]) != 0:
        hh = (f"<p><b>Kotitalousvähennykseen oikeuttava työn osuus:</b> {fmt_eur(inv['household_work'])} "
              f"(sis. ALV). Säilytä lasku verotusta varten.</p>")
    texts = "".join(f"<p><b>{e(t)}</b></p>" for t in bundle["texts"])
    buyer_id = ""
    if cust.get("business_id"):
        label = "ALV-tunniste" if (cust.get("country") or "FI") != "FI" else "Y-tunnus"
        buyer_id = f"<br>{label}: {e(cust['business_id'])}"
    credited = (f"<tr><td>Hyvitettävä lasku</td><td>{bundle['credited']['number']} "
                f"({fi(bundle['credited']['invoice_date'])})</td></tr>") if bundle.get("credited") else ""
    body = f"""
<div class='grid'>
 <div><h1>{e(comp.get('name',''))}</h1>{e(comp.get('street',''))}<br>{e(comp.get('postcode',''))} {e(comp.get('city',''))}
  <br>Y-tunnus {e(comp.get('business_id',''))}{'' if comp.get('vat_registered') else '<br>Ei arvonlisäverovelvollinen'}
  <br>{e(comp.get('phone',''))} {e(comp.get('email',''))}</div>
 <div><h1>{title}</h1><table>
  <tr><td>Laskun numero</td><td><b>{inv['number'] or '–'}</b></td></tr>
  <tr><td>Laskun päivämäärä</td><td>{fi(inv['invoice_date']) or '–'}</td></tr>
  <tr><td>Eräpäivä</td><td><b>{fi(inv['due_date']) or '–'}</b></td></tr>
  <tr><td>Viitenumero</td><td><b>{e(ref_txt) or '–'}</b></td></tr>
  <tr><td>Toimitusaika</td><td>{fi(inv['delivery_start'])}{'–' + fi(inv['delivery_end']) if inv['delivery_end'] else ''}</td></tr>
  {credited}
  <tr><td>Viitteenne</td><td>{e(inv['your_reference'])}</td></tr>
  <tr><td>Viitteemme</td><td>{e(inv['our_reference'])}</td></tr>
  <tr><td>Viivästyskorko</td><td>{e(str(bundle.get('penalty_interest','')).replace('.', ','))} %</td></tr>
 </table></div>
</div>
<div class='box' style='max-width:340px'>{e(cust.get('name',''))}<br>{e(cust.get('street',''))}<br>
{e(cust.get('postcode',''))} {e(cust.get('city',''))} {'' if (cust.get('country') or 'FI')=='FI' else e(cust.get('country'))}
{buyer_id}</div>
{f"<p>{e(inv['notes'])}</p>" if inv['notes'] else ''}
<table><tr><th>Kuvaus</th><th class='n'>Määrä</th><th class='n'>á (alv 0)</th><th class='n'>Ale %</th>
<th class='n'>ALV</th><th class='n'>Veroton</th><th class='n'>Vero</th><th class='n'>Yhteensä</th></tr>{rows}
<tr class='sum'><td colspan='5'>Yhteensä</td><td class='n'>{fmt_eur(inv['total_net'], False)}</td>
<td class='n'>{fmt_eur(inv['total_vat'], False)}</td><td class='n'>{fmt_eur(inv['total_gross'])}</td></tr></table>
<h2>Arvonlisäverot verokannoittain</h2>
<table><tr><th>Peruste</th><th class='n'>Verokanta</th><th class='n'>Veroton</th><th class='n'>Vero</th></tr>{spec}</table>
{texts}{hh}
<div class='grid'><div class='box'><b>Maksuohjeet</b><br>Saaja: {e(comp.get('name',''))}<br>
IBAN: {e(muotoile_iban(comp.get('iban','')))} {('BIC: ' + e(comp.get('bic',''))) if comp.get('bic') else ''}<br>
Viite: <b>{e(ref_txt)}</b><br>Eräpäivä: <b>{fi(inv['due_date'])}</b><br>
Summa: <b>{fmt_eur(inv['total_gross'])}</b></div>{barcode}</div>
<p class='small muted'>{e(settings.get_str('invoice.footer'))}</p>"""
    return page(f"{'Hyvityslasku' if credit else 'Lasku'} {inv['number'] or 'luonnos'}", body)


# ----------------------------------------------------------------- kirjanpito

def paivakirja_html(start: str, end: str) -> str:
    rows = ""
    td = tc = ZERO
    for j in ledger.paivakirja(start, end):
        for i, ln in enumerate(j["lines"]):
            rows += (f"<tr><td>{e(j['tosite']) if i == 0 else ''}</td><td>{fi(j['date']) if i == 0 else ''}</td>"
                     f"<td>{e(j['description']) if i == 0 else ''}</td><td>{ln['account']} {e(ln['account_name'])}</td>"
                     f"<td class='n'>{fmt_eur(ln['debit'], False) if ln['debit'] else ''}</td>"
                     f"<td class='n'>{fmt_eur(ln['credit'], False) if ln['credit'] else ''}</td>"
                     f"<td>{e(ln['vat_code'])}</td></tr>")
            td += ln["debit"]
            tc += ln["credit"]
    body = (f"<h1>Päiväkirja {fi(start)}–{fi(end)}</h1>{_company_header()}<table><tr><th>Tosite</th><th>Pvm</th>"
            f"<th>Selite</th><th>Tili</th><th class='n'>Debet</th><th class='n'>Kredit</th><th>ALV</th></tr>{rows}"
            f"<tr class='sum'><td colspan='4'>Yhteensä</td><td class='n'>{fmt_eur(td, False)}</td>"
            f"<td class='n'>{fmt_eur(tc, False)}</td><td></td></tr></table>")
    return page("Päiväkirja", body)


def paivakirja_csv(start: str, end: str) -> bytes:
    rows = []
    for j in ledger.paivakirja(start, end):
        for ln in j["lines"]:
            rows.append([j["tosite"], fi(j["date"]), j["description"], ln["account"], ln["account_name"],
                         ln["debit"], ln["credit"], ln["vat_code"]])
    return rows_to_csv(["Tosite", "Päivämäärä", "Selite", "Tili", "Tilin nimi", "Debet", "Kredit", "ALV-koodi"], rows)


def paakirja_html(start: str, end: str) -> str:
    out = f"<h1>Pääkirja {fi(start)}–{fi(end)}</h1>{_company_header()}"
    for acc in ledger.paakirja(start, end):
        rows = "".join(f"<tr><td>{e(r['tosite'])}</td><td>{fi(r['date'])}</td><td>{e(r['description'])}</td>"
                       f"<td class='n'>{fmt_eur(r['debit'], False) if r['debit'] else ''}</td>"
                       f"<td class='n'>{fmt_eur(r['credit'], False) if r['credit'] else ''}</td>"
                       f"<td class='n'>{fmt_eur(r['balance'], False)}</td></tr>" for r in acc["rows"])
        out += (f"<h2>{acc['account']} {e(acc['name'])}</h2><table><tr><th>Tosite</th><th>Pvm</th><th>Selite</th>"
                f"<th class='n'>Debet</th><th class='n'>Kredit</th><th class='n'>Saldo</th></tr>"
                f"<tr><td colspan='5'>Alkusaldo</td><td class='n'>{fmt_eur(acc['opening'], False)}</td></tr>{rows}"
                f"<tr class='sum'><td colspan='3'>Yhteensä / loppusaldo</td>"
                f"<td class='n'>{fmt_eur(acc['debit_total'], False)}</td>"
                f"<td class='n'>{fmt_eur(acc['credit_total'], False)}</td>"
                f"<td class='n'>{fmt_eur(acc['closing'], False)}</td></tr></table>")
    return page("Pääkirja", out)


def paakirja_csv(start: str, end: str) -> bytes:
    rows = []
    for acc in ledger.paakirja(start, end):
        rows.append([acc["account"], acc["name"], "", "Alkusaldo", "", "", "", acc["opening"]])
        for r in acc["rows"]:
            rows.append([acc["account"], acc["name"], r["tosite"], r["description"], fi(r["date"]), r["debit"],
                         r["credit"], r["balance"]])
    return rows_to_csv(["Tili", "Tilin nimi", "Tosite", "Selite", "Päivämäärä", "Debet", "Kredit", "Saldo"], rows)


def tuloslaskelma_html(start: str, end: str) -> str:
    t = ledger.tuloslaskelma(start, end)
    rows = ""
    for gname, g in t["groups"].items():
        if not g["rows"]:
            continue
        rows += f"<tr><th colspan='2'>{e(gname)}</th></tr>"
        rows += "".join(f"<tr><td>{r['account']} {e(r['name'])}</td><td class='n'>{fmt_eur(r['amount'])}</td></tr>"
                        for r in g["rows"])
        rows += f"<tr class='sum'><td>{e(gname)} yhteensä</td><td class='n'>{fmt_eur(g['total'])}</td></tr>"
    rows += f"<tr class='sum'><td>Tulos (ennen ostoja ja muita kirjanpidon eriä)</td>" \
            f"<td class='n'>{fmt_eur(t['result'])}</td></tr>"
    return page("Tuloslaskelma", f"<h1>Tuloslaskelma {fi(start)}–{fi(end)}</h1>{_company_header()}"
                f"<p class='warn'>Sisältää vain tässä järjestelmässä syntyneet kirjaukset (myynti, suoritukset, "
                f"palkkajaksotukset). Ostot ja muut kulut ovat kirjanpito-ohjelmassa.</p><table>{rows}</table>")


def tase_html(asof: str) -> str:
    t = ledger.tase(asof)
    a = "".join(f"<tr><td>{r['account']} {e(r['name'])}</td><td class='n'>{fmt_eur(r['amount'])}</td></tr>"
                for r in t["assets"])
    l_ = "".join(f"<tr><td>{r['account']} {e(r['name'])}</td><td class='n'>{fmt_eur(r['amount'])}</td></tr>"
                 for r in t["liabilities"])
    return page("Tase", f"<h1>Tase-erät {fi(asof)}</h1>{_company_header()}<div class='grid'>"
                f"<div><h2>Vastaavaa</h2><table>{a}<tr class='sum'><td>Yhteensä</td>"
                f"<td class='n'>{fmt_eur(t['assets_total'])}</td></tr></table></div>"
                f"<div><h2>Vastattavaa</h2><table>{l_}<tr class='sum'><td>Yhteensä</td>"
                f"<td class='n'>{fmt_eur(t['liabilities_total'])}</td></tr></table></div></div>"
                f"<p>{'Tase täsmää.' if t['balanced'] else '<b>Tase ei täsmää – tarkista kirjaukset.</b>'}</p>")


def alv_html(start: str, end: str) -> str:
    a = ledger.alv_laskelma(start, end)
    rows = "".join(f"<tr><td>{e(r['name'])}</td><td class='n'>{fmt_eur(r['base'])}</td>"
                   f"<td class='n'>{fmt_eur(r['tax'])}</td></tr>" for r in a["rows"])
    form = f"""<h2>Kausiveroilmoituksen kentät (myynnit)</h2><table>
<tr><td>Vero kotimaan myynnistä 25,5 %</td><td class='n'>{fmt_eur(a['vero_255'])}</td></tr>
<tr><td>Vero kotimaan myynnistä 13,5 %</td><td class='n'>{fmt_eur(a['vero_135'])}</td></tr>
<tr><td>Vero kotimaan myynnistä 10 %</td><td class='n'>{fmt_eur(a['vero_10'])}</td></tr>
<tr><td>Rakentamispalvelujen myynnit (käännetty verovelvollisuus)</td><td class='n'>{fmt_eur(a['rakentamispalvelut'])}</td></tr>
<tr><td>Palvelujen myynnit muihin EU-maihin</td><td class='n'>{fmt_eur(a['eu_palvelut'])}</td></tr>
<tr><td>0-verokannan alainen liikevaihto</td><td class='n'>{fmt_eur(a['nolla_verokanta'])}</td></tr>
<tr class='sum'><td>Myynnin vero yhteensä (ennen ostojen vähennyksiä)</td><td class='n'>{fmt_eur(a['myynnin_vero_yhteensa'])}</td></tr>
</table>"""
    return page("ALV-laskelma", f"<h1>ALV-laskelma {fi(start)}–{fi(end)}</h1>{_company_header()}"
                f"<table><tr><th>Peruste</th><th class='n'>Veroton myynti</th><th class='n'>Vero</th></tr>{rows}"
                f"</table>{form}<p class='warn'>Ostojen vähennettävä vero lisätään kirjanpito-ohjelmassa ennen "
                f"kausiveroilmoitusta (OmaVero).</p>")


def alv_csv(start: str, end: str) -> bytes:
    a = ledger.alv_laskelma(start, end)
    return rows_to_csv(["ALV-koodi", "Peruste", "Verokanta", "Veroton", "Vero"],
                       [[r["code"], r["name"], r["rate"], r["base"], r["tax"]] for r in a["rows"]])


def reskontra_html(asof: str) -> str:
    rows = ledger.myyntireskontra(asof)
    body = "".join(f"<tr><td>{r['number']}</td><td>{e(r['customer'])}</td><td>{fi(r['invoice_date'])}</td>"
                   f"<td>{fi(r['due_date'])}</td><td class='n'>{fmt_eur(r['total'])}</td>"
                   f"<td class='n'>{fmt_eur(r['open'])}</td><td class='n'>{r['days_overdue']}</td>"
                   f"<td>{e(r['bucket'])}</td></tr>" for r in rows)
    buckets: dict[str, Decimal] = {}
    for r in rows:
        buckets[r["bucket"]] = buckets.get(r["bucket"], ZERO) + r["open"]
    summary = "".join(f"<tr><td>{e(k)}</td><td class='n'>{fmt_eur(v)}</td></tr>" for k, v in buckets.items())
    total = sum((r["open"] for r in rows), ZERO)
    return page("Myyntireskontra", f"<h1>Myyntireskontra {fi(asof)}</h1>{_company_header()}<table><tr><th>Lasku</th>"
                f"<th>Asiakas</th><th>Pvm</th><th>Eräpäivä</th><th class='n'>Summa</th><th class='n'>Avoinna</th>"
                f"<th class='n'>Myöhässä pv</th><th>Ikäluokka</th></tr>{body}<tr class='sum'><td colspan='5'>"
                f"Avoinna yhteensä</td><td class='n'>{fmt_eur(total)}</td><td colspan='2'></td></tr></table>"
                f"<h2>Ikäjakauma</h2><table>{summary}</table>")


def reskontra_csv(asof: str) -> bytes:
    return rows_to_csv(["Lasku", "Asiakas", "Laskupvm", "Eräpäivä", "Summa", "Avoinna", "Myöhässä pv", "Ikäluokka"],
                       [[r["number"], r["customer"], fi(r["invoice_date"]), fi(r["due_date"]), r["total"], r["open"],
                         r["days_overdue"], r["bucket"]] for r in ledger.myyntireskontra(asof)])


def laskulista_csv(start: str, end: str) -> bytes:
    rows = db.q("SELECT i.*, c.name AS cname FROM invoices i JOIN customers c ON c.id = i.customer_id "
                "WHERE i.status <> 'luonnos' AND i.invoice_date BETWEEN ? AND ? ORDER BY i.number", (start, end))
    return rows_to_csv(["Numero", "Tyyppi", "Asiakas", "Laskupvm", "Eräpäivä", "Viite", "Veroton", "ALV", "Yhteensä",
                        "Maksettu", "Tila"],
                       [[r["number"], r["kind"], r["cname"], fi(r["invoice_date"]), fi(r["due_date"]), r["reference"],
                         D(r["total_net"]), D(r["total_vat"]), D(r["total_gross"]), D(r["paid_amount"]), r["status"]]
                        for r in rows])


def suoritukset_csv(start: str, end: str) -> bytes:
    rows = db.q("SELECT p.*, i.number, c.name AS cname FROM payments p JOIN invoices i ON i.id = p.invoice_id "
                "JOIN customers c ON c.id = i.customer_id WHERE p.pay_date BETWEEN ? AND ? ORDER BY p.pay_date",
                (start, end))
    return rows_to_csv(["Maksupäivä", "Lasku", "Asiakas", "Summa", "Arkistointitunnus"],
                       [[fi(r["pay_date"]), r["number"], r["cname"], D(r["amount"]), r["archive_ref"]] for r in rows])


# ----------------------------------------------------------------- työaika ja palkat

def payroll_html(data: dict) -> str:
    out = (f"<h1>Palkkakooste {fi(data['period_start'])}–{fi(data['period_end'])}</h1>{_company_header()}"
           + "".join(f"<p class='warn'>{e(w)}</p>" for w in data["warnings"]))
    out += ("<h2>Tunnit</h2><table><tr><th>Nro</th><th>Nimi</th><th class='n'>Yht. h</th><th class='n'>Säänn.</th>"
            "<th class='n'>YT50 vrk</th><th class='n'>YT100 vrk</th><th class='n'>VYT50</th><th class='n'>Ilta</th>"
            "<th class='n'>Yö</th><th class='n'>La</th><th class='n'>Su/pyhä</th><th class='n'>Km</th>"
            "<th class='n'>Pvr osa/koko</th></tr>")
    for emp in data["employees"]:
        out += (f"<tr><td>{e(emp['number'])}</td><td>{e(emp['name'])}</td>"
                + "".join(f"<td class='n'>{fmt_dec(emp[k])}</td>" for k in
                          ("hours_total", "hours_regular", "ot50_daily", "ot100_daily", "ot50_weekly", "evening",
                           "night", "saturday", "sunday"))
                + f"<td class='n'>{fmt_dec(emp['km'], 0)}</td>"
                  f"<td class='n'>{emp['allowance_partial']}/{emp['allowance_full']}</td></tr>")
    out += "</table><h2>Palkkarivit palkanlaskentaan</h2>"
    for emp in data["employees"]:
        out += (f"<h2>{e(emp['number'])} {e(emp['name'])}</h2><table><tr><th>Palkkalaji</th><th>Tulolaji</th>"
                f"<th class='n'>Määrä</th><th class='n'>á</th><th class='n'>Summa</th></tr>")
        out += "".join(f"<tr><td>{e(ln['code'])} {e(ln['name'])}</td><td>{e(ln['income_type'])}</td>"
                       f"<td class='n'>{fmt_dec(ln['qty'])} {e(ln['unit'])}</td>"
                       f"<td class='n'>{fmt_eur(ln['unit_price'], False)}</td>"
                       f"<td class='n'>{fmt_eur(ln['amount'])}</td></tr>" for ln in emp["lines"])
        out += (f"<tr class='sum'><td colspan='4'>Veronalainen / verovapaa</td><td class='n'>"
                f"{fmt_eur(emp['taxable'])} / {fmt_eur(emp['tax_free'])}</td></tr></table>")
    out += ("<p class='small muted'>Arvio perustuu hyväksyttyihin työaikoihin ja asetusten sääntöihin (TAL 872/2019 "
            "+ TES-lisät). Ennakonpidätys, lomapalkat ja tulorekisteri-ilmoitus tehdään palkanlaskennassa.</p>")
    return page("Palkkakooste", out)


def payroll_csv(data: dict) -> bytes:
    rows = []
    for emp in data["employees"]:
        for ln in emp["lines"]:
            rows.append([emp["number"], emp["name"], emp["cost_center"], ln["code"], ln["name"], ln["income_type"],
                         D(ln["qty"]), ln["unit"], D(ln["unit_price"]), D(ln["amount"]),
                         fi(data["period_start"]), fi(data["period_end"])])
    return rows_to_csv(["Henkilönumero", "Nimi", "Kustannuspaikka", "Palkkalaji", "Palkkalajin nimi", "Tulolaji",
                        "Määrä", "Yksikkö", "A-hinta", "Summa", "Jakso alkaa", "Jakso päättyy"], rows)


def worktime_csv(start: str, end: str) -> bytes:
    rows = db.q("SELECT t.*, e.number AS enum, e.name AS ename, w.code AS wcode, w.name AS wname, c.name AS cname "
                "FROM time_entries t JOIN employees e ON e.id = t.employee_id JOIN work_types w ON w.id = t.work_type_id "
                "LEFT JOIN customers c ON c.id = t.customer_id WHERE t.work_date BETWEEN ? AND ? "
                "ORDER BY e.number, t.work_date, t.start_time", (start, end))
    return rows_to_csv(["Henkilönumero", "Nimi", "Päivä", "Alku", "Loppu", "Tauko min", "Tunnit", "Työlaji", "Asiakas",
                        "Kohde", "Huoltokello-tehtävä", "Kuvaus", "Laskutettava", "Km", "Päiväraha", "Tila"],
                       [[r["enum"], r["ename"], fi(r["work_date"]), r["start_time"], r["end_time"], r["break_min"],
                         (Decimal(r["minutes"]) / 60).quantize(Decimal("0.01")), f"{r['wcode']} {r['wname']}",
                         r["cname"] or "", r["site"], r["task_ref"], r["description"],
                         "kyllä" if r["billable"] else "ei", D(r["km"]), r["allowance"], r["status"]] for r in rows])
