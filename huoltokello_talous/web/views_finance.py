"""Näkymät: laskutus, tarjoukset, hinnoittelu, kirjanpito, palkat, viennit, asetukset, tuonti ja rajapinta."""
from __future__ import annotations

import csv
import datetime as dt
import io
import json

from .. import db, invoicing, ledger, payroll, pricing, reports, security, settings, timesheet
from ..app_context import secret_store
from ..exports import finvoice, package, profiles, transport
from ..finref import tarkista_iban, tarkista_ytunnus
from ..money import D, ZERO, validate_vat_rate
from .server import HttpError, Response, download, flash, redirect, route
from .ui import (badge, button_form, checkbox, csrf, default_period, e, eur, fi, field, form, layout, num,
                 period_picker, select, table, textarea)
from .views import SUPERVISORS, _friendly, _options_customers, audit

FIN = ("johto", "kirjanpitaja")
INV_VIEW = ("johto", "tyonjohto", "kirjanpitaja")

# ------------------------------------------------------------------ laskut


@route("GET", r"/laskut", INV_VIEW)
def invoices(req):
    status = req.query.get("tila", "")
    start, end = default_period(req) if req.query.get("start") else ("0001-01-01", "9999-12-31")
    rows = db.q("SELECT i.*, c.name AS cname FROM invoices i JOIN customers c ON c.id = i.customer_id "
                "WHERE (? = '' OR i.status = ?) AND (i.status = 'luonnos' OR i.invoice_date BETWEEN ? AND ?) "
                "ORDER BY i.status = 'luonnos' DESC, i.number DESC LIMIT 500", (status, status, start, end))
    trs = [[f"<a href='/laskut/{r['id']}'>{r['number'] or 'luonnos #' + str(r['id'])}</a>",
            "Hyvitys" if r["kind"] == "hyvitys" else "Lasku", e(r["cname"]), fi(r["invoice_date"]), fi(r["due_date"]),
            eur(r["total_net"]), eur(r["total_gross"]), eur(D(r["total_gross"]) - D(r["paid_amount"])),
            badge(r["status"])] for r in rows]
    filters = " · ".join(f"<a href='/laskut?tila={k}'>{v}</a>" for k, v in
                         [("", "Kaikki"), ("luonnos", "Luonnokset"), ("avoin", "Avoimet"), ("osittain", "Osittain"),
                          ("maksettu", "Maksetut")])
    top = "" if req.session["role"] == "kirjanpitaja" else \
        "<p><a href='/laskut/uusi'>+ Laskuta työajat ja tarvikkeet</a> · <a href='/laskut/uusi?tyhja=1'>+ Tyhjä lasku</a></p>"
    return layout(req, "Laskutus", top + f"<p>{filters}</p>"
                  + table(["Nro", "Tyyppi", "Asiakas", "Pvm", "Eräpäivä", "Veroton", "Yhteensä", "Avoinna", "Tila"],
                          trs, (5, 6, 7)))


@route("GET", r"/laskut/uusi", SUPERVISORS)
def invoice_new(req):
    if req.query.get("tyhja"):
        inner = select("customer_id", "Asiakas", _options_customers(), "", True)
        return layout(req, "Uusi tyhjä lasku", form(req, "/laskut/tyhja", inner, "Luo luonnos"))
    pending = db.q("SELECT c.id, c.name, COUNT(t.id) AS n, SUM(t.minutes) AS m, MIN(t.work_date) AS a, "
                   "MAX(t.work_date) AS b FROM time_entries t JOIN customers c ON c.id = t.customer_id "
                   "WHERE t.status = 'hyvaksytty' AND t.billable = 1 AND t.invoice_id IS NULL GROUP BY c.id "
                   "ORDER BY c.name")
    mats = {r["customer_id"]: r["n"] for r in db.q("SELECT customer_id, COUNT(*) AS n FROM material_entries WHERE "
                                                   "status = 'hyvaksytty' AND invoice_id IS NULL GROUP BY customer_id")}
    trs = [[f"<input type='checkbox' name='customers' value='{r['id']}' checked>", e(r["name"]), r["n"],
            num(D(r["m"]) / 60), f"{fi(r['a'])}–{fi(r['b'])}", mats.get(r["id"], 0)] for r in pending]
    for cid, n in mats.items():
        if cid not in {r["id"] for r in pending}:
            c = db.q1("SELECT name FROM customers WHERE id = ?", (cid,))
            trs.append([f"<input type='checkbox' name='customers' value='{cid}' checked>", e(c["name"]), 0, "0,00", "",
                        n])
    inner = (table(["", "Asiakas", "Kirjauksia", "Tunnit", "Aikaväli", "Tarvikkeita"], trs, (2, 3, 5),
                   "Ei hyväksyttyjä laskuttamattomia työaikoja tai tarvikkeita.")
             + "<div class='grid3'>" + field("end", "Laskuta kirjaukset päivään asti", dt.date.today().isoformat(), "date")
             + select("group_by", "Laskurivit", [("tyolaji", "Yksi rivi työlajia kohden"),
                                                 ("rivi", "Jokainen kirjaus omana rivinään")], "tyolaji", True)
             + "</div>")
    return layout(req, "Laskuta työajat", form(req, "/laskut/uusi", inner, "Luo laskuluonnokset"))


@route("POST", r"/laskut/uusi", SUPERVISORS)
def invoice_create(req):
    created, errors = [], []
    for cid in req.flist("customers"):
        try:
            iid = invoicing.create_draft(int(cid), req.session["user_id"], end=req.f("end") or None,
                                         group_by=req.f("group_by") or "tyolaji")
            created.append(iid)
            audit(req, "laskuluonnos_luotu", "invoice", iid)
        except invoicing.InvoiceError as exc:
            errors.append(str(exc))
    if created:
        flash(req, "ok", f"{len(created)} laskuluonnosta luotu. Tarkista ja vahvista.")
    for err in errors:
        flash(req, "virhe", err)
    return redirect("/laskut?tila=luonnos")


@route("POST", r"/laskut/tyhja", SUPERVISORS)
def invoice_empty(req):
    try:
        iid = invoicing.create_empty_draft(int(req.f("customer_id") or 0), req.session["user_id"])
    except invoicing.InvoiceError as exc:
        flash(req, "virhe", str(exc))
        return redirect("/laskut/uusi?tyhja=1")
    audit(req, "laskuluonnos_luotu", "invoice", iid)
    return redirect(f"/laskut/{iid}")


@route("GET", r"/laskut/(?P<id>\d+)", INV_VIEW)
def invoice_view(req):
    iid = int(req.params["id"])
    try:
        b = invoicing.invoice_bundle(iid)
    except invoicing.InvoiceError:
        raise HttpError(404, "Laskua ei löydy.")
    inv = b["invoice"]
    role = req.session["role"]
    draft = inv["status"] == "luonnos"
    lines = [[str(ln["pos"]), e(ln["description"]), num(ln["qty"]) + " " + e(ln["unit"]), eur(ln["unit_price"]),
              f"{num(ln['vat_rate'], 1)} % {e(ln['vat_code'])}", eur(ln["net"]), eur(ln["gross"]),
              button_form(req, f"/laskut/{iid}/rivi/{ln['id']}/poista", "Poista", "Poistetaanko rivi?", "link")
              if draft and role in SUPERVISORS else ""] for ln in b["lines"]]
    body = (f"<div class='card'><b>{e(b['customer'].get('name', ''))}</b> · {badge(inv['status'])} · "
            f"{'Hyvityslasku' if inv['kind'] == 'hyvitys' else 'Lasku'} {inv['number'] or '(numero vahvistuksessa)'}"
            f"<br>Veroton {eur(inv['total_net'])} · ALV {eur(inv['total_vat'])} · <b>Yhteensä {eur(inv['total_gross'])}</b>"
            + (f" · Kotitalousvähennykseen oikeuttava työ {eur(inv['household_work'])}" if D(inv["household_work"]) else "")
            + (f"<br>Maksettu {eur(inv['paid_amount'])}" if not draft else "")
            + "".join(f"<p><b>{e(t)}</b></p>" for t in b["texts"])
            + f"<p><a href='/laskut/{iid}/tulosta' target='_blank'>Tulostettava lasku</a>"
            + (f" · <a href='/laskut/{iid}/finvoice.xml'>Finvoice 3.0 (XML)</a>" if not draft else "") + "</p></div>")
    body += table(["#", "Kuvaus", "Määrä", "á alv 0", "ALV", "Veroton", "Yhteensä", ""], lines, (3, 5, 6))
    if draft and role in SUPERVISORS:
        body += "<h2>Lisää rivi</h2>" + form(req, f"/laskut/{iid}/rivi", "<div class='grid3'>"
                                             + field("description", "Kuvaus", "", required=True)
                                             + field("qty", "Määrä", "1", required=True) + field("unit", "Yksikkö", "kpl")
                                             + field("unit_price", "á-hinta alv 0", "", required=True)
                                             + field("discount_pct", "Alennus %", "0")
                                             + select("vat_rate", "ALV", [("25.5", "25,5 %"), ("13.5", "13,5 %"),
                                                                         ("10", "10 %"), ("0", "0 %")], "25.5", True)
                                             + "</div>" + checkbox("construction", "Rakentamispalvelu")
                                             + checkbox("household", "Kotitalousvähennykseen oikeuttavaa työtä"),
                                             "Lisää rivi")
        body += "<h2>Laskun tiedot</h2>" + form(req, f"/laskut/{iid}/otsikko", "<div class='grid3'>"
                                                + field("invoice_date", "Laskupäivä (tyhjä = vahvistuspäivä)",
                                                        inv["invoice_date"] or "", "date")
                                                + field("due_date", "Eräpäivä (tyhjä = maksuehdon mukaan)",
                                                        inv["due_date"] or "", "date")
                                                + field("your_reference", "Viitteenne", inv["your_reference"])
                                                + field("our_reference", "Viitteemme", inv["our_reference"]) + "</div>"
                                                + textarea("notes", "Lisätiedot laskulle", inv["notes"]), "Tallenna tiedot")
        if role == "johto":
            body += (f"<div class='card'>{button_form(req, f'/laskut/{iid}/vahvista', 'Vahvista ja kirjaa lasku', 'Vahvistettua laskua ei voi enää muuttaa (korjaus hyvityslaskulla). Jatketaanko?')} "
                     f"{button_form(req, f'/laskut/{iid}/poista', 'Poista luonnos', 'Poistetaanko luonnos? Työajat vapautuvat uudelleen laskutettaviksi.', 'danger')}</div>")
        else:
            body += "<p class='muted'>Johto vahvistaa laskun.</p>"
    elif role == "johto" and inv["kind"] == "lasku":
        open_amt = D(inv["total_gross"]) - D(inv["paid_amount"])
        if inv["status"] in ("avoin", "osittain", "maksettu", "hyvitetty"):
            body += "<h2>Kirjaa suoritus</h2>" + form(req, f"/laskut/{iid}/suoritus", "<div class='grid3'>"
                                                      + field("amount", "Summa €", str(open_amt) if open_amt > 0 else "",
                                                              required=True)
                                                      + field("pay_date", "Maksupäivä", dt.date.today().isoformat(),
                                                              "date", True)
                                                      + field("archive_ref", "Arkistointitunnus") + "</div>",
                                                      "Kirjaa suoritus")
        actions = []
        if b["customer"].get("email"):
            actions.append(button_form(req, f"/laskut/{iid}/laheta", "Lähetä asiakkaalle sähköpostilla",
                                       f"Lähetetäänkö lasku osoitteeseen {b['customer']['email']}?"))
        if inv["status"] in ("avoin", "osittain") and inv["due_date"] < dt.date.today().isoformat():
            actions.append(button_form(req, f"/laskut/{iid}/muistutus", "Luo maksumuistutus (muistutusmaksu + korko)"))
        if inv["status"] in ("avoin", "osittain", "maksettu"):
            actions.append(f"<form method='post' action='/laskut/{iid}/hyvita' class='inline' data-confirm='Luodaanko "
                           f"täysi hyvityslasku?'>{csrf(req)}<input name='reason' placeholder='Hyvityksen syy' required> "
                           f"<button class='danger'>Hyvitä lasku</button></form>")
        body += "<div class='card'>" + " ".join(actions) + "</div>"
    if b["payments"]:
        body += "<h2>Suoritukset</h2>" + table(["Pvm", "Summa", "Arkistointitunnus"],
                                               [[fi(p["pay_date"]), eur(p["amount"]), e(p["archive_ref"])]
                                                for p in b["payments"]], (1,))
    title = f"Lasku {inv['number']}" if inv["number"] else f"Laskuluonnos #{iid}"
    return layout(req, title, body)


def _inv_action(req, fn, ok_msg, action):
    iid = int(req.params["id"])
    try:
        res = fn(iid)
        audit(req, action, "invoice", iid, json.dumps(res, default=str) if res else "")
        flash(req, "ok", ok_msg.format(res=res))
    except (invoicing.InvoiceError, ledger.LedgerError, ValueError) as exc:
        flash(req, "virhe", str(exc))
    return iid


@route("POST", r"/laskut/(?P<id>\d+)/rivi", SUPERVISORS)
def invoice_add_line(req):
    iid = _inv_action(req, lambda i: invoicing.add_manual_line(
        i, req.f("description"), D(req.f("qty")), req.f("unit"), D(req.f("unit_price")), req.f("vat_rate"),
        D(req.f("discount_pct") or 0), construction=bool(req.f("construction")), household=bool(req.f("household"))),
        "Rivi lisätty.", "laskurivi_lisatty")
    return redirect(f"/laskut/{iid}")


@route("POST", r"/laskut/(?P<id>\d+)/rivi/(?P<lid>\d+)/poista", SUPERVISORS)
def invoice_del_line(req):
    iid = _inv_action(req, lambda i: invoicing.delete_line(i, int(req.params["lid"])), "Rivi poistettu.",
                      "laskurivi_poistettu")
    return redirect(f"/laskut/{iid}")


@route("POST", r"/laskut/(?P<id>\d+)/otsikko", SUPERVISORS)
def invoice_header(req):
    iid = _inv_action(req, lambda i: invoicing.update_draft_header(
        i, your_reference=req.f("your_reference"), our_reference=req.f("our_reference"), notes=req.f("notes"),
        invoice_date=req.f("invoice_date"), due_date=req.f("due_date")), "Tiedot tallennettu.", "lasku_otsikko")
    return redirect(f"/laskut/{iid}")


@route("POST", r"/laskut/(?P<id>\d+)/vahvista", ("johto",))
def invoice_finalize(req):
    iid = _inv_action(req, lambda i: invoicing.finalize(i, req.session["user_id"]),
                      "Lasku {res[number]} vahvistettu ja kirjattu (viite {res[reference]}).", "lasku_vahvistettu")
    return redirect(f"/laskut/{iid}")


@route("POST", r"/laskut/(?P<id>\d+)/poista", ("johto",))
def invoice_delete(req):
    _inv_action(req, invoicing.delete_draft, "Luonnos poistettu.", "laskuluonnos_poistettu")
    return redirect("/laskut")


@route("POST", r"/laskut/(?P<id>\d+)/hyvita", ("johto",))
def invoice_credit(req):
    iid = int(req.params["id"])
    try:
        cn = invoicing.credit_note(iid, req.session["user_id"], req.f("reason"))
        audit(req, "lasku_hyvitetty", "invoice", iid, req.f("reason"))
        flash(req, "ok", "Hyvityslasku luotu ja kirjattu. Laskutetut työajat vapautuivat uudelleen laskutettaviksi.")
        return redirect(f"/laskut/{cn}")
    except (invoicing.InvoiceError, ledger.LedgerError) as exc:
        flash(req, "virhe", str(exc))
        return redirect(f"/laskut/{iid}")


@route("POST", r"/laskut/(?P<id>\d+)/suoritus", ("johto",))
def invoice_payment(req):
    iid = _inv_action(req, lambda i: invoicing.register_payment(i, D(req.f("amount")), req.f("pay_date"),
                                                                 req.session["user_id"], req.f("archive_ref")),
                      "Suoritus kirjattu.", "suoritus_kirjattu")
    return redirect(f"/laskut/{iid}")


@route("POST", r"/laskut/(?P<id>\d+)/muistutus", ("johto",))
def invoice_reminder(req):
    iid = int(req.params["id"])
    try:
        rid = invoicing.create_reminder(iid, req.session["user_id"])
        audit(req, "muistutus_luotu", "invoice", iid)
        flash(req, "ok", "Maksumuistutus luotu luonnokseksi – tarkista ja vahvista.")
        return redirect(f"/laskut/{rid}")
    except invoicing.InvoiceError as exc:
        flash(req, "virhe", str(exc))
        return redirect(f"/laskut/{iid}")


@route("POST", r"/laskut/(?P<id>\d+)/laheta", ("johto",))
def invoice_send(req):
    iid = int(req.params["id"])
    b = invoicing.invoice_bundle(iid)
    to = b["customer"].get("email")
    try:
        if b["invoice"]["status"] == "luonnos" or not to:
            raise transport.TransportError("Vain vahvistetun laskun voi lähettää, ja asiakkaalla on oltava sähköposti.")
        inv = b["invoice"]
        msg_body = (f"Hei,\n\nliitteenä {settings.get_str('company.name')}:n lasku {inv['number']}.\n"
                    f"Summa {eur(inv['total_gross'])}, eräpäivä {fi(inv['due_date'])}, viite {inv['reference']}.\n\n"
                    f"Ystävällisin terveisin\n{settings.get_str('company.name')}\n")
        import zipfile as zf
        buf = io.BytesIO()
        with zf.ZipFile(buf, "w", zf.ZIP_DEFLATED) as z:
            z.writestr(f"lasku_{inv['number']}.html", reports.invoice_html(b))
            z.writestr(f"lasku_{inv['number']}_finvoice.xml", finvoice.build(b))
        transport.smtp_send(to, f"Lasku {inv['number']} – {settings.get_str('company.name')}", msg_body,
                            (buf.getvalue(), f"lasku_{inv['number']}.zip"))
        audit(req, "lasku_lahetetty", "invoice", iid, f"sähköposti {to}")
        flash(req, "ok", f"Lasku lähetetty osoitteeseen {to}.")
    except (transport.TransportError, OSError, Exception) as exc:  # noqa: BLE001
        flash(req, "virhe", f"Lähetys epäonnistui: {exc}")
    return redirect(f"/laskut/{iid}")


@route("GET", r"/laskut/(?P<id>\d+)/tulosta", INV_VIEW)
def invoice_print(req):
    try:
        return Response(reports.invoice_html(invoicing.invoice_bundle(int(req.params["id"]))))
    except invoicing.InvoiceError:
        raise HttpError(404)


@route("GET", r"/laskut/(?P<id>\d+)/finvoice\.xml", INV_VIEW)
def invoice_xml(req):
    b = invoicing.invoice_bundle(int(req.params["id"]))
    try:
        return download(finvoice.build(b).encode("utf-8"), f"finvoice_{b['invoice']['number']}.xml",
                        "application/xml; charset=utf-8")
    except ValueError as exc:
        raise HttpError(400, str(exc))


# ------------------------------------------------------------------ tarjoukset

@route("GET", r"/tarjoukset", SUPERVISORS)
def quotes(req):
    rows = db.q("SELECT * FROM quotes ORDER BY id DESC LIMIT 200")
    wts = db.q("SELECT * FROM work_types WHERE active = 1 ORDER BY code")
    est = "".join(f"<label>{e(w['code'])} {e(w['name'])} (h)<input name='wt_{w['id']}' type='number' step='0.25' "
                  f"min='0'></label>" for w in wts)
    inner = ("<div class='grid3'>" + select("customer_id", "Asiakas", _options_customers(), "", blank="(uusi asiakas)")
             + field("customer_name", "Asiakkaan nimi (jos ei rekisterissä)") + field("title", "Tarjouksen otsikko", "",
                                                                                         required=True)
             + field("valid_days", "Voimassa (pv)", "30", "number") + "</div><h2>Työarvio</h2><div class='grid3'>" + est
             + "</div><h2>Lisärivi (tarvikkeet tms.)</h2><div class='grid3'>" + field("x_desc", "Kuvaus")
             + field("x_qty", "Määrä", "") + field("x_price", "á alv 0", "")
             + select("x_vat", "ALV", [("25.5", "25,5 %"), ("13.5", "13,5 %"), ("0", "0 %")], "25.5") + "</div>")
    return layout(req, "Tarjoukset", table(["Nro", "Asiakas", "Otsikko", "Pvm", "Voimassa", "Yhteensä", "Tila"],
                                          [[f"<a href='/tarjoukset/{r['id']}' target='_blank'>{r['number']}</a>",
                                            e(r["customer_name"]), e(r["title"]), fi(r["quote_date"]),
                                            fi(r["valid_until"]), eur(r["total_gross"]), badge(r["status"])]
                                           for r in rows], (5,))
                  + "<h2>Uusi tarjous / hinta-arvio asiakkaalle</h2>" + form(req, "/tarjoukset", inner, "Luo tarjous"))


@route("POST", r"/tarjoukset", SUPERVISORS)
def quote_create(req):
    cid = int(req.f("customer_id")) if req.f("customer_id") else None
    items = [(int(k[3:]), D(v)) for k, v in req.form.items() if k.startswith("wt_") and v.strip() and D(v) > 0]
    lines = pricing.estimate_lines(cid, items)
    if req.f("x_desc") and req.f("x_qty") and req.f("x_price"):
        lines.append({"description": req.f("x_desc"), "qty": D(req.f("x_qty")), "unit": "kpl",
                      "unit_price": D(req.f("x_price")), "vat_rate": validate_vat_rate(req.f("x_vat") or "25.5")})
    name = req.f("customer_name")
    if cid:
        name = db.q1("SELECT name FROM customers WHERE id = ?", (cid,))["name"]
    try:
        qid = pricing.create_quote(cid, name, req.f("title"), lines, int(req.f("valid_days") or 30),
                                   req.session["user_id"])
        audit(req, "tarjous_luotu", "quote", qid)
        return redirect(f"/tarjoukset/{qid}")
    except ValueError as exc:
        flash(req, "virhe", str(exc))
        return redirect("/tarjoukset")


@route("GET", r"/tarjoukset/(?P<id>\d+)", SUPERVISORS)
def quote_print(req):
    q = db.q1("SELECT * FROM quotes WHERE id = ?", (int(req.params["id"]),))
    if q is None:
        raise HttpError(404)
    s = settings.all_settings()
    lines = json.loads(q["lines"])
    rows = "".join(f"<tr><td>{e(ln['description'])}</td><td class='n'>{num(ln['qty'])} {e(ln['unit'])}</td>"
                   f"<td class='n'>{eur(ln['unit_price'])}</td><td class='n'>{num(ln['vat_rate'], 1)} %</td>"
                   f"<td class='n'>{eur(ln['net'])}</td><td class='n'>{eur(ln['gross'])}</td></tr>" for ln in lines)
    body = (f"<h1>TARJOUS {q['number']}</h1><p>{e(s['company.name'])} · Y-tunnus {e(s['company.business_id'])}</p>"
            f"<p><b>{e(q['customer_name'])}</b><br>{e(q['title'])}</p><p>Päiväys {fi(q['quote_date'])} · voimassa "
            f"{fi(q['valid_until'])} asti</p><table><tr><th>Kuvaus</th><th class='n'>Määrä</th><th class='n'>á alv 0</th>"
            f"<th class='n'>ALV</th><th class='n'>Veroton</th><th class='n'>Verollinen</th></tr>{rows}<tr class='sum'>"
            f"<td colspan='4'>Yhteensä</td><td class='n'>{eur(q['total_net'])}</td><td class='n'>{eur(q['total_gross'])}"
            f"</td></tr></table><p class='small muted'>Työn osuus perustuu tuntiarvioon; toteutunut työ laskutetaan "
            f"toteutuneiden tuntien mukaan sovitulla tuntihinnalla, ellei kiinteästä hinnasta ole sovittu.</p>")
    return Response(reports.page(f"Tarjous {q['number']}", body))


# ------------------------------------------------------------------ hinnoittelu (johto)

@route("GET", r"/hinnoittelu", ("johto",))
def pricing_view(req):
    s = settings.all_settings()
    wage = req.query.get("palkka") or str(pricing.average_wage() or "16")
    ov = {k: req.query[k] for k in ("statutory_pct", "indirect_pct", "billable_ratio_pct", "overhead_per_hour",
                                    "target_margin_pct") if req.query.get(k)}
    try:
        calc = pricing.calc_from_settings(wage, **ov)
        res = (f"<div class='grid3'><div class='card'><div class='muted'>Työnantajan kustannus / maksettu tunti</div>"
               f"<div class='kpi'>{eur(calc.employer_cost_per_paid_hour)}</div></div>"
               f"<div class='card'><div class='muted'>Omakustannus / laskutettava tunti</div>"
               f"<div class='kpi'>{eur(calc.cost_per_billable_hour)}</div></div>"
               f"<div class='card'><div class='muted'>Hintasuositus (alv 0) tavoitekatteella</div>"
               f"<div class='kpi'>{eur(calc.suggested_price)}</div>verollinen "
               f"{eur(calc.suggested_price * D('1.255'))}</div></div>")
    except ValueError as exc:
        calc, res = None, f"<p class='warn'>{e(str(exc))}</p>"
    inner = ("<div class='grid3'>" + field("palkka", "Tuntipalkka €", wage)
             + field("statutory_pct", "Lakisääteiset sivukulut %", ov.get("statutory_pct", s["pricing.statutory_pct"]),
                     help_="TyEL ~17,10 + sotu 1,91 + tyött. 0,31/1,23 + tapaturma ja ryhmähenki (2026)")
             + field("indirect_pct", "Välilliset palkkakustannukset %", ov.get("indirect_pct", s["pricing.indirect_pct"]),
                     help_="lomapalkat, lomarahat, pyhät, sairauspoissaolot")
             + field("billable_ratio_pct", "Laskutettavuusaste %",
                     ov.get("billable_ratio_pct", s["pricing.billable_ratio_pct"]))
             + field("overhead_per_hour", "Yleiskulut € / laskutettava tunti",
                     ov.get("overhead_per_hour", s["pricing.overhead_per_hour"]))
             + field("target_margin_pct", "Tavoitekate % myyntihinnasta",
                     ov.get("target_margin_pct", s["pricing.target_margin_pct"])) + "</div>")
    calc_form = (f"<form method='get' class='card'>{inner}<div class='actions'><button>Laske</button></div>"
                 f"<small>Oletusarvot muutetaan Asetuksissa. Laskuri ei tallenna mitään.</small></form>")
    wt_rows = []
    if calc:
        for w in db.q("SELECT * FROM work_types WHERE active = 1 AND billable = 1 ORDER BY code"):
            m_eur, m_pct = calc.margin_at(w["price"])
            wt_rows.append([e(f"{w['code']} {w['name']}"), eur(w["price"]), eur(calc.cost_per_billable_hour),
                            eur(m_eur), f"{num(m_pct, 1)} %",
                            "<b>alle omakustannuksen</b>" if m_eur < 0 else ("alle tavoitteen"
                                                                           if m_pct < calc.target_margin_pct else "ok")])
    start, end = default_period(req)
    prof = pricing.profitability(start, end)

    def prow(d):
        return [[e(k), num(v["hours"]), num(v["billable_hours"]), eur(v["revenue"]), eur(v["cost"]), eur(v["margin"]),
                 f"{num(v['margin_pct'], 1)} %", f"{num(v['utilization_pct'], 1)} %"] for k, v in d.items()]
    hdr = ["", "Tehdyt h", "Laskutettavat h", "Tuotto", "Työvoimakustannus", "Kate", "Kate %", "Laskutusaste"]
    body = (calc_form + res + "<h2>Hinnaston kate tällä palkalla</h2>"
            + table(["Työlaji", "Hinta", "Omakustannus", "Kate €/h", "Kate %", "Arvio"], wt_rows, (1, 2, 3, 4))
            + "<h2>Toteutunut kannattavuus</h2>" + period_picker(req, "/hinnoittelu", start, end)
            + "<h2>Työlajeittain</h2>" + table(hdr, prow(prof["by_work_type"]), (1, 2, 3, 4, 5, 6, 7))
            + "<h2>Asiakkaittain</h2>" + table(hdr, prow(prof["by_customer"]), (1, 2, 3, 4, 5, 6, 7))
            + "<p class='muted'>Asiakashinnat asetetaan asiakkaan sivulla (Rekisterit > Asiakkaat).</p>")
    return layout(req, "Työn hinnoittelu ja kannattavuus", body)


# ------------------------------------------------------------------ kirjanpito

REPORTS = {
    "paivakirja": ("Päiväkirja", reports.paivakirja_html, reports.paivakirja_csv),
    "paakirja": ("Pääkirja", reports.paakirja_html, reports.paakirja_csv),
    "tuloslaskelma": ("Tuloslaskelma", reports.tuloslaskelma_html, None),
    "alv": ("ALV-laskelma", reports.alv_html, reports.alv_csv),
    "laskulista": ("Laskulista", None, reports.laskulista_csv),
    "suoritukset": ("Suoritukset", None, reports.suoritukset_csv),
    "tyoajat": ("Työaikaraportti", None, reports.worktime_csv),
}


@route("GET", r"/kirjanpito", FIN)
def accounting(req):
    start, end = default_period(req)
    links = []
    for key, (label, h, c) in REPORTS.items():
        parts = []
        if h:
            parts.append(f"<a href='/raportti/{key}?start={start}&end={end}' target='_blank'>HTML</a>")
        if c:
            parts.append(f"<a href='/raportti/{key}?start={start}&end={end}&muoto=csv'>CSV</a>")
        links.append(f"<div class='card'><b>{e(label)}</b><br>{' · '.join(parts)}</div>")
    links.append(f"<div class='card'><b>Tase</b><br><a href='/raportti/tase?end={end}' target='_blank'>HTML</a></div>")
    links.append(f"<div class='card'><b>Myyntireskontra</b><br><a href='/raportti/reskontra?end={end}' "
                 f"target='_blank'>HTML</a> · <a href='/raportti/reskontra?end={end}&muoto=csv'>CSV</a></div>")
    alv = ledger.alv_laskelma(start, end)
    locked = db.q("SELECT * FROM locked_periods ORDER BY period DESC LIMIT 24")
    lock_form = ""
    if req.session["role"] in FIN:
        lock_form = form(req, "/kirjanpito/lukitse", field("period", "Lukittava kausi (VVVV-KK)",
                                                           (dt.date.today().replace(day=1) - dt.timedelta(days=1))
                                                           .strftime("%Y-%m"), required=True),
                         "Lukitse kausi", "card row",
                         "Lukittuun kauteen ei voi enää tehdä kirjauksia (laskuja, suorituksia). Jatketaanko?")
    body = (period_picker(req, "/kirjanpito", start, end)
            + f"<div class='grid3'><div class='card'><div class='muted'>Myynnin ALV kaudella</div><div class='kpi'>"
            f"{eur(alv['myynnin_vero_yhteensa'])}</div>25,5 %: {eur(alv['vero_255'])} · 13,5 %: {eur(alv['vero_135'])}"
            f"<br>Rakentamispalvelut (käännetty): {eur(alv['rakentamispalvelut'])}</div></div>"
            + "<h2>Raportit</h2><div class='grid3'>" + "".join(links) + "</div>"
            + "<h2>Kausien lukitus</h2>" + lock_form
            + table(["Kausi", "Lukittu"], [[e(r["period"]), e(r["locked_at"])] for r in locked]))
    return layout(req, "Kirjanpito", body)


@route("GET", r"/raportti/(?P<name>[a-z]+)", FIN)
def report(req):
    name = req.params["name"]
    start, end = default_period(req)
    as_csv = req.query.get("muoto") == "csv"
    if name == "tase":
        return Response(reports.tase_html(end))
    if name == "reskontra":
        if as_csv:
            return download(reports.reskontra_csv(end), f"myyntireskontra_{end}.csv", "text/csv; charset=utf-8")
        return Response(reports.reskontra_html(end))
    if name not in REPORTS:
        raise HttpError(404)
    label, h, c = REPORTS[name]
    if as_csv and c:
        return download(c(start, end), f"{name}_{start}_{end}.csv", "text/csv; charset=utf-8")
    if h:
        return Response(h(start, end))
    raise HttpError(404)


@route("POST", r"/kirjanpito/lukitse", FIN)
def lock_period(req):
    try:
        ledger.lock_period(req.f("period"), req.session["user_id"])
        audit(req, "kausi_lukittu", "period", req.f("period"))
        flash(req, "ok", f"Kausi {req.f('period')} lukittu.")
    except ledger.LedgerError as exc:
        flash(req, "virhe", str(exc))
    return redirect("/kirjanpito")


# ------------------------------------------------------------------ palkat

@route("GET", r"/palkat", FIN)
def payroll_view(req):
    start, end = default_period(req)
    data = payroll.build(start, end)
    rows = [[e(x["number"]), e(x["name"]), num(x["hours_total"]), num(x["ot50_daily"] + x["ot50_weekly"]),
             num(x["ot100_daily"]), num(x["evening"]), num(x["night"]), num(x["saturday"]), num(x["sunday"]),
             num(x["km"], 0), eur(x["taxable"]), eur(x["tax_free"])] for x in data["employees"]]
    runs = db.q("SELECT * FROM payroll_runs ORDER BY id DESC LIMIT 24")
    save = ""
    if req.session["role"] == "johto":
        save = (f"<form method='post' action='/palkat/tallenna' class='inline'>{csrf(req)}"
                f"<input type='hidden' name='start' value='{start}'><input type='hidden' name='end' value='{end}'>"
                f"<button>Tallenna palkkajakso</button></form>")
    body = (period_picker(req, "/palkat", start, end)
            + "".join(f"<p class='warn'>{e(w)}</p>" for w in data["warnings"])
            + table(["Nro", "Nimi", "Tunnit", "YT 50 %", "YT 100 %", "Ilta", "Yö", "La", "Su/pyhä", "Km", "Veronalainen",
                     "Verovapaat"], rows, tuple(range(2, 12)), "Ei hyväksyttyjä työaikoja jaksolla.")
            + f"<p>Veronalaiset yhteensä {eur(data['totals']['taxable'])} · verovapaat {eur(data['totals']['tax_free'])}"
              f"</p><p>{save} <a href='/palkat/esikatselu?start={start}&end={end}' target='_blank'>Palkkakooste (HTML)</a>"
              f" · <a href='/palkat/esikatselu?start={start}&end={end}&muoto=csv'>Palkkarivit (CSV)</a></p>"
            + "<h2>Tallennetut palkkajaksot</h2>"
            + table(["Jakso", "Tila", "Luotu", ""],
                    [[f"{fi(r['period_start'])}–{fi(r['period_end'])}", badge(r["status"]), e(r["created_at"]),
                      f"<a href='/palkat/{r['id']}' target='_blank'>Kooste</a> · <a href='/palkat/{r['id']}?muoto=csv'>"
                      f"CSV</a>" + (" · " + button_form(req, f"/palkat/{r['id']}/lukitse", "Lukitse",
                                                        "Lukitus kiinnittää jakson työajat. Jatketaanko?", "link")
                                    if r["status"] == "luonnos" and req.session["role"] == "johto" else "")]
                     for r in runs]))
    return layout(req, "Palkkakoosteet", body)


@route("GET", r"/palkat/esikatselu", FIN)
def payroll_preview(req):
    start, end = default_period(req)
    data = payroll.build(start, end)
    if req.query.get("muoto") == "csv":
        return download(reports.payroll_csv(data), f"palkkarivit_{start}_{end}.csv", "text/csv; charset=utf-8")
    return Response(reports.payroll_html(data))


@route("POST", r"/palkat/tallenna", ("johto",))
def payroll_save(req):
    rid = payroll.save_run(req.f("start"), req.f("end"), req.session["user_id"])
    audit(req, "palkkajakso_tallennettu", "payroll_run", rid)
    flash(req, "ok", "Palkkajakso tallennettu luonnokseksi.")
    return redirect("/palkat")


@route("GET", r"/palkat/(?P<id>\d+)", FIN)
def payroll_run(req):
    r = db.q1("SELECT * FROM payroll_runs WHERE id = ?", (int(req.params["id"]),))
    if r is None:
        raise HttpError(404)
    data = json.loads(r["data"])
    if req.query.get("muoto") == "csv":
        return download(reports.payroll_csv(data), f"palkkarivit_{r['period_start']}_{r['period_end']}.csv",
                        "text/csv; charset=utf-8")
    return Response(reports.payroll_html(data))


@route("POST", r"/palkat/(?P<id>\d+)/lukitse", ("johto",))
def payroll_lock(req):
    try:
        payroll.lock_run(int(req.params["id"]), req.session["user_id"], post_accrual=False)
        audit(req, "palkkajakso_lukittu", "payroll_run", req.params["id"])
        flash(req, "ok", "Palkkajakso lukittu.")
    except (ValueError, ledger.LedgerError) as exc:
        flash(req, "virhe", str(exc))
    return redirect("/palkat")


# ------------------------------------------------------------------ viennit

@route("GET", r"/viennit", FIN)
def exports_view(req):
    s = settings.all_settings()
    period = req.query.get("kausi") or package.previous_month()
    log_rows = db.q("SELECT * FROM exports ORDER BY id DESC LIMIT 50")
    prof_label = profiles.PROFILES.get(s["export.profile"], {"label": "Mukautettu"})["label"]
    body = (f"<div class='card'>Vientiprofiili: <b>{e(prof_label)}</b><br>Siirtotapa: <b>{e(s['export.transport'])}</b>"
            f" · Automaattinen kuukausivienti: <b>{'päällä, ' + str(s['export.auto_day']) + '. päivä' if s['export.auto_enabled'] else 'pois'}</b>"
            f"<br><a href='/asetukset#vienti'>Muuta asetuksia</a></div>"
            + form(req, "/viennit/paketti", field("kausi", "Kausi (VVVV-KK)", period, required=True)
                   + checkbox("palkat", "Sisällytä palkkakooste", bool(s["export.include_payroll"])),
                   "Lataa kirjanpitäjän paketti (ZIP)", "card row")
            + form(req, "/viennit/laheta", field("kausi", "Kausi (VVVV-KK)", period, required=True)
                   + checkbox("pakota", "Lähetä uudelleen, vaikka sama aineisto on jo siirretty"),
                   "Muodosta ja siirrä määritettyyn palveluun", "card row",
                   "Muodostetaanko paketti ja siirretäänkö se määritettyyn kohteeseen?")
            + "<h2>Yksittäiset tiedostot</h2><div class='card'>"
            + " · ".join(f"<a href='/viennit/kirjanpito.csv?kausi={period}&profiili={k}'>{e(v['label'])}</a>"
                         for k, v in profiles.PROFILES.items()) + "</div>"
            + "<h2>Vientiloki</h2>"
            + table(["Aika (UTC)", "Laji", "Kausi", "Kohde", "Tiedosto", "Tila", "Viesti"],
                    [[e(r["ts"]), e(r["kind"]), e(r["period"]), e(r["target"]), e(r["filename"]), badge(r["status"]),
                      e(r["message"][:150])] for r in log_rows]))
    return layout(req, "Viennit kirjanpitoon", body)


def _period_from(req) -> tuple[str, str, str]:
    period = req.f("kausi") or req.query.get("kausi", "")
    try:
        start, end = package.month_bounds(period)
    except (ValueError, TypeError):
        raise HttpError(400, "Kausi muodossa VVVV-KK.")
    return period, start, end


@route("POST", r"/viennit/paketti", FIN)
def export_package(req):
    period, start, end = _period_from(req)
    data, fname, _info = package.build_package(start, end, bool(req.f("palkat")))
    audit(req, "paketti_ladattu", "export", period)
    return download(data, fname, "application/zip")


@route("POST", r"/viennit/laheta", FIN)
def export_send(req):
    period, start, end = _period_from(req)
    data, fname, info = package.build_package(start, end)
    res = transport.send("kuukausipaketti", data, fname, period, req.session["user_id"], force=bool(req.f("pakota")),
                         journal_range=(start, end), content_key=info["content_key"])
    audit(req, "vienti", "export", period, f"{res['status']}: {res['message']}")
    flash(req, "ok" if res["status"] in ("ok", "ohitettu", "odottaa") else "virhe", res["message"])
    return redirect(f"/viennit?kausi={period}")


@route("GET", r"/viennit/kirjanpito\.csv", FIN)
def export_csv(req):
    period = req.query.get("kausi", "")
    try:
        start, end = package.month_bounds(period)
        data, fname = profiles.journal_csv(start, end, req.query.get("profiili", "yleinen"),
                                           settings.get_str("export.custom_profile"))
    except ValueError as exc:
        raise HttpError(400, str(exc))
    return download(data, fname, "text/csv")


# ------------------------------------------------------------------ asetukset

SETTING_GROUPS = [
    ("yritys", "Yritys", [
        ("company.name", "Yrityksen nimi"), ("company.business_id", "Y-tunnus"), ("company.street", "Katuosoite"),
        ("company.postcode", "Postinumero"), ("company.city", "Postitoimipaikka"), ("company.domicile", "Kotipaikka"),
        ("company.phone", "Puhelin"), ("company.email", "Sähköposti"), ("company.iban", "IBAN"), ("company.bic", "BIC"),
        ("company.einvoice_address", "Oma verkkolaskuosoite"), ("company.einvoice_operator", "Oma operaattori"),
        ("company.vat_registered", "Arvonlisäverovelvollinen (1/0)")]),
    ("laskutus", "Laskutus", [
        ("invoice.next_number", "Seuraava laskunumero"), ("invoice.payment_term_days", "Oletusmaksuehto (pv)"),
        ("invoice.penalty_interest", "Viivästyskorko % (kaupallinen, 1.7.–31.12.2026: 10,5)"),
        ("invoice.reminder_fee", "Muistutusmaksu €"), ("invoice.use_rf_reference", "RF-viite (1/0)"),
        ("invoice.footer", "Laskun alatunniste")]),
    ("tyoaika", "Työaika ja palkkasäännöt (tarkista TES)", [
        ("work.daily_regular_hours", "Säännöllinen työaika h/vrk"), ("work.weekly_regular_hours", "Säännöllinen h/vko"),
        ("work.daily_ot_first_hours", "Vrk-ylityön 50 %:n tunnit"), ("work.evening_start", "Iltatyö alkaa"),
        ("work.evening_end", "Iltatyö päättyy"), ("work.night_start", "Yötyö alkaa"), ("work.night_end", "Yötyö päättyy"),
        ("work.evening_bonus_eur", "Iltalisä €/h"), ("work.night_bonus_eur", "Yölisä €/h"),
        ("work.saturday_bonus_eur", "Lauantailisä €/h"), ("work.sunday_pct", "Sunnuntaikorotus %"),
        ("work.holiday_as_sunday", "Kirkolliset juhlapyhät kuten sunnuntai (1/0)"),
        ("work.km_rate", "Km-korvaus €/km (2026: 0,55)"), ("work.allowance_partial", "Osapäiväraha € (2026: 25)"),
        ("work.allowance_full", "Kokopäiväraha € (2026: 54)")]),
    ("hinnoittelu", "Hinnoittelun oletukset", [
        ("pricing.statutory_pct", "Lakisääteiset sivukulut %"), ("pricing.indirect_pct", "Välilliset palkkakulut %"),
        ("pricing.billable_ratio_pct", "Laskutettavuusaste %"), ("pricing.overhead_per_hour", "Yleiskulut €/h"),
        ("pricing.target_margin_pct", "Tavoitekate %")]),
    ("tilit", "Tilikartan kytkennät", [
        ("acc.receivables", "Myyntisaamiset"), ("acc.bank", "Pankkitili"), ("acc.vat_payable", "ALV-velka"),
        ("acc.revenue_rc", "Rakentamispalvelujen myynti"), ("acc.revenue_eu_services", "EU-palvelumyynti"),
        ("acc.revenue_zero", "0 %-myynti"), ("acc.penalty_interest", "Viivästyskorkotuotot"),
        ("acc.reminder_fees", "Muistutusmaksut")]),
    ("vienti", "Vienti kirjanpitoon", [
        ("export.profile", "Vientiprofiili"), ("export.transport", "Siirtotapa"), ("export.folder", "Vientikansio"),
        ("export.email_to", "Kirjanpitäjän sähköposti"), ("export.https_url", "HTTPS-osoite"),
        ("export.netvisor_env", "Netvisor-ympäristö (test/prod)"), ("export.procountor_env", "Procountor (test/prod)"),
        ("export.api_live", "Rajapintalähetys tuotannossa (1/0)"), ("export.auto_enabled", "Automaattinen kuukausivienti (1/0)"),
        ("export.auto_day", "Automaattiviennin päivä (1–28)"), ("export.include_payroll", "Palkkakooste pakettiin (1/0)"),
        ("export.custom_profile", "Mukautettu profiili (JSON)")]),
    ("sahkoposti", "Sähköposti (SMTP)", [
        ("smtp.host", "SMTP-palvelin"), ("smtp.port", "Portti (587 STARTTLS / 465 SSL)"), ("smtp.user", "Käyttäjätunnus"),
        ("smtp.sender", "Lähettäjän osoite")]),
    ("turva", "Tietoturva", [
        ("security.session_hours", "Istunnon enimmäiskesto h"), ("security.max_failed_logins", "Sallitut virheyritykset"),
        ("security.lockout_minutes", "Lukitusaika min"), ("security.min_password_length", "Salasanan vähimmäispituus")]),
]

SECRETS = [("smtp.password", "SMTP-salasana / sovellussalasana"), ("export.https_token", "HTTPS-siirron tunniste"),
           ("netvisor.customer_id", "Netvisor asiakastunnus"), ("netvisor.customer_key", "Netvisor asiakasavain"),
           ("netvisor.partner_id", "Netvisor kumppanitunnus"), ("netvisor.partner_key", "Netvisor kumppaniavain"),
           ("procountor.client_id", "Procountor client_id"), ("procountor.client_secret", "Procountor client_secret"),
           ("procountor.api_key", "Procountor API-avain")]


@route("GET", r"/asetukset", ("johto",))
def settings_view(req):
    s = settings.all_settings()
    sec = secret_store()
    parts = []
    for gid, title, keys in SETTING_GROUPS:
        inner = "<div class='grid3'>"
        for k, label in keys:
            v = s.get(k)
            if isinstance(v, bool):
                v = "1" if v else "0"
            if k == "export.profile":
                inner += select(k, label, [(p, d["label"]) for p, d in profiles.PROFILES.items()]
                                + [("mukautettu", "Mukautettu (JSON)")], v, True)
            elif k == "export.transport":
                inner += select(k, label, [("kansio", "Kansio"), ("sahkoposti", "Sähköposti kirjanpitäjälle"),
                                           ("https", "HTTPS-integraatio"), ("netvisor_api", "Netvisor-rajapinta"),
                                           ("procountor_api", "Procountor-rajapinta")], v, True)
            elif k in ("export.custom_profile", "invoice.footer"):
                inner += "</div>" + textarea(k, label, v or "", 3) + "<div class='grid3'>"
            else:
                inner += field(k, label, "" if v is None else v)
        inner += "</div>"
        parts.append(f"<h2 id='{gid}'>{e(title)}</h2>" + form(req, "/asetukset", inner + f"<input type='hidden' "
                                                             f"name='_group' value='{gid}'>"))
    sec_inner = "<div class='grid3'>" + "".join(
        field(k, f"{label} {'(tallennettu)' if sec.has(k) else ''}", "", "password", False, "autocomplete='off'")
        for k, label in SECRETS) + "</div><small>Tyhjä kenttä säilyttää aiemman arvon. Kirjoita - (viiva) poistaaksesi. " \
                                   "Salaisuudet tallennetaan erilliseen suojattuun tiedostoon (Windows DPAPI).</small>"
    parts.append("<h2 id='salaisuudet'>Salaisuudet</h2>" + form(req, "/asetukset/salaisuudet", sec_inner,
                                                                "Tallenna salaisuudet"))
    parts.append("<h2>Testit</h2><div class='card'>" + button_form(req, "/asetukset/smtp-testi",
                                                                   "Lähetä SMTP-testiviesti itselleni") + "</div>")
    return layout(req, "Asetukset", "".join(parts))


@route("POST", r"/asetukset", ("johto",))
def settings_save(req):
    gid = req.f("_group")
    group = next((g for g in SETTING_GROUPS if g[0] == gid), None)
    if group is None:
        raise HttpError(400)
    errors = []
    for k, _label in group[2]:
        raw = req.form.get(k)
        if raw is None:
            continue
        raw = raw.strip()
        default = settings.DEFAULTS.get(k)
        try:
            if isinstance(default, bool):
                val = raw in ("1", "true", "kyllä", "on")
            elif isinstance(default, int):
                val = int(raw)
            else:
                val = raw
            if k == "company.business_id" and val and not tarkista_ytunnus(val):
                raise ValueError("Y-tunnus on virheellinen.")
            if k == "company.iban" and val and not tarkista_iban(val):
                raise ValueError("IBAN on virheellinen.")
            if k == "export.custom_profile" and val:
                profiles.get_profile("mukautettu", val)
            if k == "export.auto_day" and not 1 <= int(val) <= 28:
                raise ValueError("Automaattiviennin päivä 1–28.")
            if k == "invoice.next_number":
                used = db.q1("SELECT MAX(number) AS m FROM invoices")["m"] or 0
                if int(val) <= used:
                    raise ValueError(f"Seuraavan laskunumeron on oltava suurempi kuin {used} (katkeamaton sarja).")
            if k.startswith(("pricing.", "work.", "invoice.penalty", "invoice.reminder")) and k not in (
                    "work.evening_start", "work.evening_end", "work.night_start", "work.night_end",
                    "work.holiday_as_sunday"):
                if D(val) < 0:
                    raise ValueError("Arvo ei voi olla negatiivinen.")
            settings.set(k, val)
        except (ValueError, json.JSONDecodeError) as exc:
            errors.append(f"{_label}: {exc}")
    audit(req, "asetukset_muutettu", "settings", gid)
    for err in errors:
        flash(req, "virhe", err)
    if not errors:
        flash(req, "ok", "Asetukset tallennettu.")
    return redirect(f"/asetukset#{gid}")


@route("POST", r"/asetukset/salaisuudet", ("johto",))
def secrets_save(req):
    sec = secret_store()
    changed = []
    for k, _label in SECRETS:
        v = req.form.get(k, "")
        if v == "-":
            sec.set(k, "")
            changed.append(k)
        elif v:
            sec.set(k, v)
            changed.append(k)
    audit(req, "salaisuudet_muutettu", "secrets", "", ",".join(changed))
    flash(req, "ok", f"{len(changed)} salaisuutta päivitetty.")
    return redirect("/asetukset#salaisuudet")


@route("POST", r"/asetukset/smtp-testi", ("johto",))
def smtp_test(req):
    try:
        transport.smtp_send(req.session["email"], "Huoltokello-Talous: SMTP-testi",
                            "Sähköpostiasetukset toimivat.\n")
        flash(req, "ok", f"Testiviesti lähetetty osoitteeseen {req.session['email']}.")
    except Exception as exc:  # noqa: BLE001
        flash(req, "virhe", f"SMTP-testi epäonnistui: {exc}")
    return redirect("/asetukset#sahkoposti")


# ------------------------------------------------------------------ tuonti

@route("GET", r"/tuonti", ("johto",))
def import_view(req):
    body = ("<div class='card'><p><b>Asiakkaat</b> (CSV, ; -erotin, UTF-8): sarakkeet "
            "<code>numero;nimi;ytunnus;katuosoite;postinumero;kaupunki;sahkoposti;verkkolaskuosoite;operaattori;"
            "kuluttaja;maksuehto</code></p></div>"
            + form(req, "/tuonti/asiakkaat", "<label>Tiedosto<input type='file' name='file' accept='.csv' required>"
                   "</label>", "Tuo asiakkaat", multipart=True)
            + "<div class='card'><p><b>Työajat Huoltokellosta tai muusta järjestelmästä</b> (CSV): sarakkeet "
              "<code>henkilonumero;paiva(VVVV-KK-PP);alku;loppu;tauko_min;tyolajikoodi;asiakasnumero;kohde;tehtava;"
              "kuvaus;laskutettava(1/0);km</code>. Tuodut kirjaukset menevät tilaan <i>lähetetty</i> hyväksyttäviksi."
              "</p></div>"
            + form(req, "/tuonti/tyoajat", "<label>Tiedosto<input type='file' name='file' accept='.csv' required>"
                   "</label>", "Tuo työajat", multipart=True))
    return layout(req, "Tuonti", body)


def _csv_rows(req) -> list[list[str]]:
    if "file" not in req.files:
        raise HttpError(400, "Tiedosto puuttuu.")
    raw = req.files["file"][1]
    for enc in ("utf-8-sig", "cp1252"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    rows = list(csv.reader(io.StringIO(text), delimiter=";"))
    if rows and rows[0] and not rows[0][0].strip().replace("-", "").isdigit() and rows[0][0].lower() in (
            "numero", "henkilonumero", "henkilönumero"):
        rows = rows[1:]
    if len(rows) > 20000:
        raise HttpError(400, "Liian monta riviä (enintään 20 000).")
    return rows


@route("POST", r"/tuonti/asiakkaat", ("johto",))
def import_customers(req):
    ok, errs = 0, []
    for i, r in enumerate(_csv_rows(req), 1):
        r = (r + [""] * 11)[:11]
        try:
            if not r[0] or not r[1]:
                raise ValueError("numero ja nimi pakollisia")
            if r[2] and not tarkista_ytunnus(r[2].strip()):
                raise ValueError("virheellinen Y-tunnus")
            db.ex("INSERT INTO customers(number, name, business_id, street, postcode, city, email, einvoice_address, "
                  "einvoice_operator, is_consumer, payment_term_days) VALUES(?,?,?,?,?,?,?,?,?,?,?) "
                  "ON CONFLICT(number) DO UPDATE SET name=excluded.name, business_id=excluded.business_id, "
                  "street=excluded.street, postcode=excluded.postcode, city=excluded.city, email=excluded.email, "
                  "einvoice_address=excluded.einvoice_address, einvoice_operator=excluded.einvoice_operator",
                  tuple(x.strip() for x in r[:9]) + (1 if r[9].strip() in ("1", "kyllä") else 0,
                                                      int(r[10]) if r[10].strip().isdigit() else 14))
            ok += 1
        except Exception as exc:  # noqa: BLE001
            errs.append(f"rivi {i}: {exc}")
    audit(req, "tuonti_asiakkaat", "customer", "", f"{ok} ok, {len(errs)} virhettä")
    flash(req, "ok", f"{ok} asiakasta tuotu/päivitetty.")
    for err in errs[:20]:
        flash(req, "virhe", err)
    return redirect("/tuonti")


@route("POST", r"/tuonti/tyoajat", ("johto",))
def import_entries(req):
    emps = {r["number"]: r["id"] for r in db.q("SELECT id, number FROM employees")}
    wts = {r["code"]: r["id"] for r in db.q("SELECT id, code FROM work_types")}
    custs = {r["number"]: r["id"] for r in db.q("SELECT id, number FROM customers")}
    ok, errs = 0, []
    for i, r in enumerate(_csv_rows(req), 1):
        r = (r + [""] * 12)[:12]
        try:
            emp = emps.get(r[0].strip())
            wt = wts.get(r[5].strip())
            if not emp or not wt:
                raise ValueError("tuntematon henkilönumero tai työlajikoodi")
            timesheet.create(emp, {"work_date": r[1].strip(), "start_time": r[2], "end_time": r[3],
                                   "break_min": r[4] or 0, "work_type_id": wt,
                                   "customer_id": custs.get(r[6].strip()), "site": r[7], "task_ref": r[8],
                                   "description": r[9], "billable": 1 if r[10].strip() in ("1", "kyllä", "") else 0,
                                   "km": r[11] or 0}, req.session["user_id"], source="tuonti", submit=True)
            ok += 1
        except (timesheet.EntryError, ValueError) as exc:
            errs.append(f"rivi {i}: {exc}")
    audit(req, "tuonti_tyoajat", "time_entry", "", f"{ok} ok, {len(errs)} virhettä")
    flash(req, "ok", f"{ok} työaikakirjausta tuotu hyväksyttäviksi.")
    for err in errs[:20]:
        flash(req, "virhe", err)
    return redirect("/tuonti")


# ------------------------------------------------------------------ rajapinta

@route("GET", r"/api-avaimet", ("johto",))
def api_tokens(req):
    rows = db.q("SELECT * FROM api_tokens ORDER BY id DESC")
    trs = [[e(r["name"]), e(r["scope"]), e(r["created_at"]), e(r["last_used"] or "–"),
            "aktiivinen" if r["active"] else "peruttu",
            button_form(req, f"/api-avaimet/{r['id']}/peru", "Peru", "Perutaanko avain?", "link") if r["active"] else ""]
           for r in rows]
    doc = ("<div class='card'><p>Huoltokello tai muu järjestelmä voi viedä työajat rajapintaan:</p><pre>POST /api/v1/tyoajat\n"
           "Authorization: Bearer hkt_...\nContent-Type: application/json\n\n[{\"henkilonumero\":\"T1\",\"paiva\":\"2026-11-16\","
           "\"alku\":\"07:00\",\"loppu\":\"15:30\",\"tauko_min\":30,\"tyolaji\":\"KH\",\"asiakasnumero\":\"1001\","
           "\"kohde\":\"As Oy Koivu\",\"tehtava\":\"HK-1234\",\"kuvaus\":\"Vuosihuolto\",\"laskutettava\":true,"
           "\"km\":12}]</pre><p>Kirjaukset menevät hyväksyttäviksi. Käytä rajapintaa vain HTTPS:n yli tai samalla "
           "koneella.</p></div>")
    return layout(req, "Rajapinta-avaimet", doc + table(["Nimi", "Oikeudet", "Luotu", "Käytetty", "Tila", ""], trs)
                  + form(req, "/api-avaimet", field("name", "Avaimen nimi", "Huoltokello", required=True),
                         "Luo avain"))


@route("POST", r"/api-avaimet", ("johto",))
def api_token_create(req):
    token = security.create_api_token(req.f("name") or "avain", "tyoajat:write", req.session["user_id"])
    audit(req, "api_avain_luotu", "api_token", "", req.f("name"))
    return layout(req, "Avain luotu", f"<div class='card'><p>Uusi avain (näytetään vain kerran):</p>"
                  f"<p><code>{e(token)}</code></p><p><a href='/api-avaimet'>Takaisin</a></p></div>")


@route("POST", r"/api-avaimet/(?P<id>\d+)/peru", ("johto",))
def api_token_revoke(req):
    db.ex("UPDATE api_tokens SET active = 0 WHERE id = ?", (int(req.params["id"]),))
    audit(req, "api_avain_peruttu", "api_token", req.params["id"])
    return redirect("/api-avaimet")


def _json(obj, status=200):
    return Response(json.dumps(obj, ensure_ascii=False, default=str), status, "application/json; charset=utf-8")


@route("POST", r"/api/v1/tyoajat", None)
def api_entries(req):
    auth = req.headers.get("Authorization", "")
    tok = security.check_api_token(auth[7:] if auth.startswith("Bearer ") else "", "tyoajat:write")
    if tok is None:
        return _json({"virhe": "Virheellinen tai puuttuva tunniste."}, 401)
    if not isinstance(req.json, list) or len(req.json) > 5000:
        return _json({"virhe": "Odotettiin JSON-taulukkoa (enintään 5000 kirjausta)."}, 400)
    emps = {r["number"]: r["id"] for r in db.q("SELECT id, number FROM employees")}
    wts = {r["code"]: r["id"] for r in db.q("SELECT id, code FROM work_types")}
    custs = {r["number"]: r["id"] for r in db.q("SELECT id, number FROM customers")}
    created, errors = [], []
    for i, it in enumerate(req.json):
        try:
            if not isinstance(it, dict):
                raise ValueError("kirjaus ei ole objekti")
            emp, wt = emps.get(str(it.get("henkilonumero"))), wts.get(str(it.get("tyolaji")))
            if not emp or not wt:
                raise ValueError("tuntematon henkilönumero tai työlaji")
            eid = timesheet.create(emp, {"work_date": it.get("paiva"), "start_time": it.get("alku"),
                                         "end_time": it.get("loppu"), "break_min": it.get("tauko_min", 0),
                                         "work_type_id": wt, "customer_id": custs.get(str(it.get("asiakasnumero"))),
                                         "site": str(it.get("kohde", "")), "task_ref": str(it.get("tehtava", "")),
                                         "description": str(it.get("kuvaus", "")),
                                         "billable": 1 if it.get("laskutettava", True) else 0, "km": it.get("km", 0)},
                                   tok["created_by"], source="api", submit=True)
            created.append(eid)
        except (timesheet.EntryError, ValueError, TypeError) as exc:
            errors.append({"indeksi": i, "virhe": str(exc)})
    security.audit("api_tyoajat", "time_entry", "", f"{len(created)} ok, {len(errors)} virhettä, avain {tok['name']}",
                   ip=req.ip)
    return _json({"luotu": created, "virheet": errors}, 200 if not errors else 207)


@route("GET", r"/api/v1/tila", None)
def api_status(req):
    return _json({"tila": "ok"})


# ------------------------------------------------------------------ Huoltokello-silta

HK_LABELS = {"asiakkaat_uudet": "asiakkaat (uudet/kytkettävät)", "asiakkaat_paivitetyt": "päivitettävät asiakkaat",
             "tyontekijat_uudet": "työntekijät", "tyolajit_uudet": "työlajit", "asiakashinnat": "asiakashinnat",
             "tyoajat": "työajat", "ohitetut": "ohitetaan", "tarvikkeet": "tarvikkeet", "varoitukset": "huomautukset"}


@route("GET", r"/huoltokello", ("johto",))
def hk_view(req):
    from .. import hk_bridge
    s = settings.all_settings()
    path = hk_bridge.hk_path()
    status, plan, err = "", None, ""
    try:
        conn = hk_bridge.open_hk(path)
        try:
            mode, why = hk_bridge.tz_mode(conn)
            status = f"<b>Yhteys kunnossa.</b> Aikaleimat: {e(mode)} ({e(why)})."
            if req.query.get("esikatselu"):
                plan = hk_bridge.build_plan(conn)
        finally:
            conn.close()
    except (hk_bridge.BridgeError, Exception) as exc:  # noqa: BLE001
        err = str(exc)
    last = db.q("SELECT * FROM hk_sync_log ORDER BY id DESC LIMIT 15")
    body = (f"<div class='card'>Huoltokellon tietokanta: <code>{e(path)}</code><br>"
            + (f"<span class='badge b-ok'>OK</span> {status}" if not err else f"<span class='badge b-virhe'>Virhe</span> {e(err)}")
            + f"<br>Viimeisin synkronointi: {e(s['hk.last_sync'] or '–')} · automaattinen: "
            f"<b>{'päällä, ' + str(s['hk.interval_minutes']) + ' min välein' if s['hk.enabled'] else 'pois'}</b>"
            "<p class='muted'>Talous lukee Huoltokellon tietokantaa vain lukutilassa – Huoltokellon tietoja ei muuteta. "
            "Tuodut työajat menevät Hyväksyntä-sivulle.</p></div>")
    inner = ("<div class='grid3'>"
             + field("hk.db_path", "Huoltokellon tietokanta (tyhjä = oletus)", s["hk.db_path"],
                     help_=hk_bridge.default_db_path())
             + field("hk.start_date", "Tuo kuittaukset alkaen (tyhjä = kuun alku)", s["hk.start_date"], "date")
             + select("hk.timezone", "Huoltokellon aikaleimat", [("auto", "Tunnista automaattisesti"), ("utc", "UTC"),
                                                                 ("paikallinen", "Paikallinen aika")], s["hk.timezone"], True)
             + field("hk.interval_minutes", "Synkronointiväli (min)", s["hk.interval_minutes"], "number", False,
                     "min=5 max=1440") + "</div>"
             + checkbox("hk.import_hours", "Tuo kuittausten työtunnit työajoiksi", bool(s["hk.import_hours"]))
             + checkbox("hk.import_materials", "Tuo kuittausten tarvikkeet", bool(s["hk.import_materials"]))
             + checkbox("hk.enabled", "Automaattinen synkronointi (kun Talous on käynnissä)", bool(s["hk.enabled"])))
    body += "<h2>Asetukset</h2>" + form(req, "/huoltokello/asetukset", inner)
    body += (f"<div class='card'><a href='/huoltokello?esikatselu=1'><b>Esikatsele tuonti</b></a> – näet mitä tuodaan "
             f"ennen kuin mitään tallennetaan. "
             + button_form(req, "/huoltokello/tuo", "Tuo nyt", "Tuodaanko Huoltokellon tiedot Talouteen?")
             + "</div>")
    if plan is not None:
        smy = plan.summary()
        body += ("<h2>Esikatselu</h2><div class='card'>" + " · ".join(f"{HK_LABELS.get(k, k)}: <b>{v}</b>"
                                                                      for k, v in smy.items()) + "</div>")
        body += "".join(f"<p class='warn'>{e(w)}</p>" for w in plan.warnings)
        if plan.customers_new:
            body += "<h2>Uudet asiakkaat</h2>" + table(
                ["Huoltokello-id", "Nimi", "Osoite", "Toiminto"],
                [[str(c["hk_id"]), e(c["name"]), e(" ".join(x for x in (c["street"], c["postcode"], c["city"]) if x)),
                  "kytketään olemassa olevaan" if c.get("match_id") else "luodaan"] for c in plan.customers_new])
        if plan.employees_new:
            body += "<h2>Työntekijät</h2>" + table(
                ["Nimi", "Sähköposti", "Toiminto"],
                [[e(u["name"]), e(u["email"]), "kytketään (sama sähköposti)" if u.get("match_id") else
                  "luodaan – täydennä palkka"] for u in plan.employees_new])
        if plan.worktypes_new:
            body += "<h2>Työlajit (Huoltokellon kategoriat)</h2>" + table(
                ["Kategoria", "Toiminto"], [[e(w["name"]), "kytketään" if w.get("match_id") else "luodaan, hinta 0 €"]
                                            for w in plan.worktypes_new])
        body += "<h2>Tuotavat työajat</h2>" + table(
            ["Päivä", "Aika", "h", "Työntekijä", "Tehtävä", "Kohde", "Laskutus"],
            [[fi(x["date"].isoformat()), f"{x['start']:%H:%M}–{x['end']:%H:%M}", num(x["hours"]), e(x["user_name"]),
              e(f"HK-{x['task']} {x['title']}"), e(x["site"]), e(x["bill_reason"])]
             for x in plan.entries if x["hours_only"]], (2,), "Ei uusia kuittauksia tuotavaksi.")
        if plan.materials:
            body += "<h2>Tarvikkeet</h2>" + table(
                ["Tehtävä", "Tarvike", "Määrä", "á"], [[f"HK-{m['conf']['task']}", e(m["name"]), num(m["qty"]),
                                                        eur(m["price"])] for m in plan.materials], (2, 3))
        if plan.skipped:
            body += "<h2>Ohitetaan</h2>" + table(["Tehtävä", "Syy"], [[f"HK-{x.get('task')}", e(x["reason"])]
                                                                    for x in plan.skipped])
    body += "<h2>Synkronointiloki</h2>" + table(
        ["Aika (UTC)", "Tapa", "Tila", "Yhteenveto"],
        [[e(r["ts"]), e(r["mode"]), badge("ok" if r["ok"] else "virhe"), e(r["summary"][:220])] for r in last])
    return layout(req, "Huoltokello-yhteys", body)


@route("POST", r"/huoltokello/asetukset", ("johto",))
def hk_settings(req):
    try:
        p = req.f("hk.db_path")
        if p and not p.lower().endswith(".db"):
            raise ValueError("Polun on osoitettava huoltokello.db-tiedostoon.")
        sd = req.f("hk.start_date")
        if sd:
            dt.date.fromisoformat(sd)
        tz = req.f("hk.timezone") or "auto"
        if tz not in ("auto", "utc", "paikallinen"):
            raise ValueError("Virheellinen aikavyöhyke.")
        iv = int(req.f("hk.interval_minutes") or 15)
        if not 5 <= iv <= 1440:
            raise ValueError("Synkronointiväli 5–1440 min.")
        settings.set("hk.db_path", p)
        settings.set("hk.start_date", sd)
        settings.set("hk.timezone", tz)
        settings.set("hk.interval_minutes", iv)
        settings.set("hk.import_hours", bool(req.f("hk.import_hours")))
        settings.set("hk.import_materials", bool(req.f("hk.import_materials")))
        settings.set("hk.enabled", bool(req.f("hk.enabled")))
        audit(req, "huoltokello_asetukset", "settings", "hk")
        flash(req, "ok", "Huoltokello-yhteyden asetukset tallennettu.")
        return redirect("/huoltokello/asetukset")
    except ValueError as exc:
        flash(req, "virhe", str(exc))
    return redirect("/huoltokello")


@route("POST", r"/huoltokello/tuo", ("johto",))
def hk_import(req):
    from .. import hk_bridge
    try:
        res = hk_bridge.sync("kasin", req.session["user_id"])
        flash(req, "ok", f"Tuotu {res['tuotu_tyoaikoja']} työaikaa ja {res['tuotu_tarvikkeita']} tarviketta "
                         f"({res['asiakkaat_uudet']} uutta asiakasta, {res['tyontekijat_uudet']} työntekijää). "
                         "Työajat odottavat hyväksyntää.")
        for w in res["varoitukset"][:10]:
            flash(req, "info", w)
        for r in res["ohitetut_syyt"][:10]:
            flash(req, "info", "Ohitettu: " + r)
    except hk_bridge.BridgeError as exc:
        flash(req, "virhe", str(exc))
    return redirect("/huoltokello")


# ------------------------------------------------------------------ lisenssi

@route("GET", r"/lisenssi", ("johto",))
def lisenssi_view(req):
    """Lisenssin tila ja aktivointi (avain + aktivointikoodi kehittäjältä)."""
    from .. import licenssi
    lis = licenssi.lataa_tila()
    kone = licenssi.muotoile_kone(licenssi.konekoodi())
    if lis.get("voimassa"):
        tila_html = ("<div class='card'><p><b>Lisenssi voimassa:</b> {} (taso {})</p>"
                     "<p>Avain: <code>{}</code><br>Aktivoitu: {}<br>Vanhentuu: {}</p>"
                     "<p>Tuote: {} (ohjelmat: {})</p></div>").format(
            e(lis.get("nimi", "")), e(str(lis.get("taso", ""))),
            e(str(lis.get("avain", ""))), e(str(lis.get("aktivoitu", ""))),
            e(str(lis.get("voimassa_paattyen", ""))), e(str(lis.get("tuote", ""))),
            e(", ".join(lis.get("ohjelmat", []))))
    else:
        tila_html = ("<div class='card'><p><b>Ei voimassa olevaa lisenssiä.</b> {}</p>"
                     "<p>Ilman lisenssiä ohjelma toimii 90 päivän kokeilujaksolla, joka "
                     "käynnistyy ensimmäisellä käyttökerralla.</p></div>").format(e(str(lis.get("virhe", ""))))
    lomake = ("<h2>Aktivoi lisenssi</h2>"
              + form(req, "/lisenssi",
                     "<div class='grid2'>"
                     + field("avain", "Lisenssiavain (XXXXX-XXXXX-XXXXX-XXXXX-XXXXX)", "", required=True)
                     + field("koodi", "Aktivointikoodi (XXXXXX-XXXXXX)", "", required=True)
                     + "</div><small>Kehittäjän antama koodi on sidottu tämän koneen konekoodiin: "
                       "sama avain ei kelpaa toisessa koneessa ilman uutta koodia.",
                     "Aktivoi lisenssi")
              + "<div class='card'><p>Tämän koneen <b>konekoodi</b>: <code>{}</code></p>"
              "<p>Lähetä konekoodi kehittäjälle sähköpostilla jani.malmberg@gmail.com; "
              "kehittäjä myöntää avaimen ja aktivointikoodin juuri tälle koneelle.</p></div>".format(e(kone)))
    return layout(req, "Lisenssi", tila_html + lomake)


@route("POST", r"/lisenssi", ("johto",))
def lisenssi_aktivoi(req):
    from .. import licenssi
    tulos = licenssi.aktivoi(req.f("avain"), req.f("koodi"))
    if tulos.get("ok"):
        audit(req, "lisenssi_aktivoitu", "lisenssi", str(tulos.get("taso", "")), tulos.get("nimi", ""))
        flash(req, "ok", "Lisenssi aktivoitu: {} (taso {}).".format(tulos.get("nimi", ""), tulos.get("taso", "")))
    else:
        flash(req, "virhe", str(tulos.get("virhe", "Aktivointi epäonnistui.")))
    return redirect("/lisenssi")

