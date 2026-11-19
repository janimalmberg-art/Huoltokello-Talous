"""Näkymät: kirjautuminen, etusivu, työaika ja leimaus, hyväksyntä, rekisterit ja käyttäjät."""
from __future__ import annotations

import datetime as dt
import secrets

from .. import db, security, settings, timesheet
from ..money import D, ZERO
from ..security import ROLES
from .server import HttpError, Request, Response, flash, rate_limited, redirect, route
from .ui import (CSS, JS, badge, button_form, checkbox, csrf, e, eur, fi, field, form, layout, num, select, table,
                 textarea)

ALL = ("johto", "tyonjohto", "tyontekija", "kirjanpitaja")
WORKERS = ("johto", "tyonjohto", "tyontekija")
SUPERVISORS = ("johto", "tyonjohto")


def error_page(req, status: int, message: str) -> Response:
    if req is None:
        return Response(f"<h1>Virhe {status}</h1>", status)
    titles = {400: "Virheellinen pyyntö", 401: "Kirjautuminen vaaditaan", 403: "Ei oikeutta", 404: "Ei löydy",
              413: "Liian suuri", 429: "Liian monta yritystä", 500: "Virhe"}
    body = f"<div class='card'><p>{e(message or titles.get(status, 'Virhe'))}</p><p><a href='/'>Etusivulle</a></p></div>"
    return layout(req, titles.get(status, "Virhe"), body, status)


def audit(req: Request, action: str, entity: str = "", entity_id="", details: str = "") -> None:
    security.audit(action, entity, entity_id, details, ip=req.ip, user=req.session)


# ------------------------------------------------------------------ staattiset, terveys

@route("GET", r"/static/app\.css", None)
def static_css(req):
    return Response(CSS, content_type="text/css; charset=utf-8", headers={"Cache-Control": "max-age=3600"})


@route("GET", r"/static/app\.js", None)
def static_js(req):
    return Response(JS, content_type="application/javascript; charset=utf-8", headers={"Cache-Control": "max-age=3600"})


@route("GET", r"/favicon\.ico", None)
def favicon(req):
    return Response(b"", 204, headers={"Cache-Control": "max-age=86400"})


@route("GET", r"/health", None)
def health(req):
    return Response("ok", content_type="text/plain; charset=utf-8")


# ------------------------------------------------------------------ kirjautuminen

@route("GET", r"/login", None)
def login_form(req):
    pre = req.cookie("hkt_pre") or secrets.token_urlsafe(24)
    req.set_cookie("hkt_pre", pre, max_age=3600)
    req.cookies["hkt_pre"] = pre
    nxt = req.query.get("next", "/")
    if not nxt.startswith("/") or nxt.startswith("//"):
        nxt = "/"
    body = f"""<form method='post' action='/login' class='card' style='max-width:420px'>
<input type='hidden' name='csrf' value='{e(pre)}'><input type='hidden' name='next' value='{e(nxt)}'>
{field('email', 'Sähköposti', '', 'email', True, "autocomplete='username' autofocus")}
{field('password', 'Salasana', '', 'password', True, "autocomplete='current-password'")}
<div class='actions'><button>Kirjaudu</button></div></form>"""
    return layout(req, "Kirjaudu", body)


@route("POST", r"/login", None)
def login_post(req):
    if rate_limited("login:" + req.ip, limit=10, window=60):
        raise HttpError(429, "Liian monta kirjautumisyritystä. Odota minuutti.")
    try:
        user = security.authenticate(req.f("email"), req.form.get("password", ""), ip=req.ip)
    except security.LoginError as exc:
        flash(req, "virhe", str(exc))
        return redirect("/login")
    token, _csrf = security.create_session(user["id"], req.ip)
    req.set_cookie("hkt_session", token, max_age=int(settings.get("security.session_hours")) * 3600)
    req.set_cookie("hkt_pre", "", max_age=0)
    nxt = req.f("next") or "/"
    return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else "/")


@route("POST", r"/logout", ())
def logout(req):
    audit(req, "uloskirjautuminen", "user", req.session["user_id"])
    security.destroy_session(req.cookie("hkt_session"))
    req.set_cookie("hkt_session", "", max_age=0)
    return redirect("/login")


@route("GET", r"/salasana", ())
def password_form(req):
    note = "<p class='warn'>Vaihda salasana ennen jatkamista.</p>" if req.session["must_change_pw"] else ""
    minlen = settings.get("security.min_password_length")
    body = note + form(req, "/salasana",
                       field("old", "Nykyinen salasana", "", "password", True, "autocomplete='current-password'")
                       + field("new1", "Uusi salasana", "", "password", True, "autocomplete='new-password'",
                               f"Vähintään {minlen} merkkiä ja kolme merkkityyppiä, tai 16+ merkin salalause.")
                       + field("new2", "Uusi salasana uudelleen", "", "password", True, "autocomplete='new-password'"),
                       "Vaihda salasana", "card")
    return layout(req, "Salasanan vaihto", body)


@route("POST", r"/salasana", ())
def password_post(req):
    user = db.q1("SELECT * FROM users WHERE id = ?", (req.session["user_id"],))
    if not security.verify_password(req.form.get("old", ""), user["pw_hash"]):
        flash(req, "virhe", "Nykyinen salasana on väärin.")
        return redirect("/salasana")
    new1, new2 = req.form.get("new1", ""), req.form.get("new2", "")
    if new1 != new2:
        flash(req, "virhe", "Salasanat eivät täsmää.")
        return redirect("/salasana")
    if new1 == req.form.get("old", ""):
        flash(req, "virhe", "Uusi salasana ei voi olla sama kuin vanha.")
        return redirect("/salasana")
    probs = security.password_problems(new1, user["email"], user["name"])
    if probs:
        flash(req, "virhe", " ".join(probs))
        return redirect("/salasana")
    security.set_password(user["id"], new1)
    audit(req, "salasana_vaihdettu", "user", user["id"])
    token, _ = security.create_session(user["id"], req.ip)
    req.set_cookie("hkt_session", token, max_age=int(settings.get("security.session_hours")) * 3600)
    req.cookies["hkt_session"] = token
    flash(req, "ok", "Salasana vaihdettu.")
    return redirect("/")


# ------------------------------------------------------------------ etusivu

@route("GET", r"/", ())
def dashboard(req):
    role = req.session["role"]
    today = dt.date.today()
    cards = []
    if req.session["employee_id"]:
        week_start = today - dt.timedelta(days=today.weekday())
        mins = db.q1("SELECT COALESCE(SUM(minutes),0) AS m FROM time_entries WHERE employee_id = ? AND work_date >= ? "
                     "AND status <> 'hylatty'", (req.session["employee_id"], week_start.isoformat()))["m"]
        rejected = db.q1("SELECT COUNT(*) AS n FROM time_entries WHERE employee_id = ? AND status = 'hylatty'",
                         (req.session["employee_id"],))["n"]
        cards.append(f"<div class='card'><div class='muted'>Työtunnit tällä viikolla</div>"
                     f"<div class='kpi'>{num(D(mins) / 60)} h</div><a href='/tyoaika'>Kirjaa työaikaa →</a>"
                     + (f"<p class='warn'>{rejected} hylättyä kirjausta odottaa korjausta.</p>" if rejected else "")
                     + "</div>")
    if role in SUPERVISORS:
        n = db.q1("SELECT COUNT(*) AS n FROM time_entries WHERE status = 'lahetetty'")["n"]
        cards.append(f"<div class='card'><div class='muted'>Odottaa hyväksyntää</div><div class='kpi'>{n}</div>"
                     f"<a href='/hyvaksynta'>Hyväksy työajat →</a></div>")
        u = db.q1("SELECT COUNT(*) AS n FROM time_entries WHERE status = 'hyvaksytty' AND billable = 1 "
                  "AND invoice_id IS NULL")["n"]
        cards.append(f"<div class='card'><div class='muted'>Laskuttamattomia työkirjauksia</div><div class='kpi'>{u}</div>"
                     f"<a href='/laskut/uusi'>Tee laskut →</a></div>")
    if role in ("johto", "kirjanpitaja"):
        rows = db.q("SELECT total_gross, paid_amount, due_date FROM invoices WHERE status IN ('avoin','osittain')")
        open_total = sum((D(r["total_gross"]) - D(r["paid_amount"]) for r in rows), ZERO)
        overdue = sum((D(r["total_gross"]) - D(r["paid_amount"]) for r in rows if r["due_date"] < today.isoformat()),
                      ZERO)
        cards.append(f"<div class='card'><div class='muted'>Avoimet myyntisaamiset</div><div class='kpi'>"
                     f"{eur(open_total)}</div>Erääntyneet: <b>{eur(overdue)}</b><br><a href='/kirjanpito'>Raportit →</a>"
                     f"</div>")
        m_start = today.replace(day=1).isoformat()
        sales = db.q1("SELECT COUNT(*) AS n FROM invoices WHERE status <> 'luonnos' AND invoice_date >= ?", (m_start,))
        net = sum((D(r["total_net"]) for r in db.q("SELECT total_net FROM invoices WHERE status <> 'luonnos' "
                                                    "AND invoice_date >= ?", (m_start,))), ZERO)
        cards.append(f"<div class='card'><div class='muted'>Laskutus kuluva kk (alv 0)</div><div class='kpi'>{eur(net)}"
                     f"</div>{sales['n']} laskua</div>")
    setup = ""
    if role == "johto" and (not settings.get_str("company.business_id") or not settings.get_str("company.iban")):
        setup = ("<div class='warn'>Aloita täyttämällä yrityksen tiedot: <a href='/asetukset'>Asetukset</a> → "
                 "lisää työlajit ja asiakkaat: <a href='/rekisterit'>Rekisterit</a>.</div>")
    return layout(req, "Etusivu", setup + "<div class='grid3'>" + "".join(cards) + "</div>")


# ------------------------------------------------------------------ työaika

def _options_worktypes():
    return [(r["id"], f"{r['code']} {r['name']}") for r in db.q("SELECT * FROM work_types WHERE active = 1 ORDER BY code")]


def _options_customers():
    return [(r["id"], f"{r['name']} ({r['number']})") for r in db.q("SELECT * FROM customers WHERE active = 1 "
                                                                     "ORDER BY name")]


def _options_employees():
    return [(r["id"], f"{r['number']} {r['name']}") for r in db.q("SELECT * FROM employees WHERE active = 1 "
                                                                    "ORDER BY name")]


def _entry_form(req, action: str, v: dict, employee_select: bool) -> str:
    emp = select("employee_id", "Työntekijä", _options_employees(), v.get("employee_id", ""), True) \
        if employee_select else ""
    inner = (f"<div class='grid3'>{emp}"
             f"{field('work_date', 'Päivä', v.get('work_date', dt.date.today().isoformat()), 'date', True)}"
             f"{field('start_time', 'Alku', v.get('start_time', '07:00'), 'time', True)}"
             f"{field('end_time', 'Loppu', v.get('end_time', '15:30'), 'time', True)}"
             f"{field('break_min', 'Tauko (min)', v.get('break_min', 30), 'number', False, 'min=0 max=600')}"
             f"{select('work_type_id', 'Työlaji', _options_worktypes(), v.get('work_type_id', ''), True)}"
             f"{select('customer_id', 'Asiakas', _options_customers(), v.get('customer_id', ''), blank='(sisäinen työ)')}"
             f"{field('site', 'Kohde / osoite', v.get('site', ''))}"
             f"{field('task_ref', 'Huoltokello-tehtävä (nro)', v.get('task_ref', ''))}"
             f"{field('km', 'Ajetut km (oma auto)', v.get('km', '0'), 'number', False, 'min=0 step=0.1')}"
             f"{select('allowance', 'Päiväraha', [('osa', 'Osapäiväraha (yli 6 h)'), ('koko', 'Kokopäiväraha (yli 10 h)')], v.get('allowance', ''), blank='Ei päivärahaa')}"
             f"</div>{textarea('description', 'Työn kuvaus', v.get('description', ''), 2)}"
             f"{checkbox('billable', 'Laskutettava asiakkaalta', bool(v.get('billable', 1)))}"
             f"{checkbox('submit', 'Lähetä hyväksyttäväksi heti', True)}")
    return form(req, action, inner, "Tallenna kirjaus")


@route("GET", r"/tyoaika", WORKERS)
def worktime(req):
    emp_id = req.session["employee_id"]
    sup = req.session["role"] in SUPERVISORS
    if not emp_id and not sup:
        return layout(req, "Työaika", "<p class='warn'>Käyttäjätunnusta ei ole liitetty työntekijään. "
                                       "Pyydä johtoa liittämään tunnus (Rekisterit > Käyttäjät).</p>")
    view_emp = int(req.query.get("tyontekija") or emp_id or 0)
    if view_emp != emp_id and not sup:
        raise HttpError(403, "Voit tarkastella vain omia kirjauksiasi.")
    today = dt.date.today()
    try:
        ws = dt.date.fromisoformat(req.query.get("viikko", ""))
    except ValueError:
        ws = today
    ws = ws - dt.timedelta(days=ws.weekday())
    we = ws + dt.timedelta(days=6)
    clock = ""
    if emp_id:
        oc = db.q1("SELECT o.*, w.name AS wname, c.name AS cname FROM open_clocks o JOIN work_types w ON "
                   "w.id = o.work_type_id LEFT JOIN customers c ON c.id = o.customer_id WHERE o.employee_id = ?",
                   (emp_id,))
        if oc:
            clock = (f"<div class='card'><h2>Leimaus käynnissä</h2><div class='clock' id='clock-elapsed' "
                     f"data-start='{e(oc['started_at'])}'></div><p>Alkoi {e(oc['started_at'][11:16])} · "
                     f"{e(oc['wname'])} · {e(oc['cname'] or 'sisäinen')} {e(oc['site'])}</p>"
                     + form(req, "/leimaus/ulos", "<div class='grid3'>"
                            + field("break_min", "Tauko (min)", 30, "number", False, "min=0")
                            + field("description", "Työn kuvaus") + "</div>"
                            + checkbox("billable", "Laskutettava", True), "Leimaa ulos", "") + "</div>")
        else:
            clock = form(req, "/leimaus/sisaan", "<h2>Leimaa sisään</h2><div class='grid3'>"
                         + select("work_type_id", "Työlaji", _options_worktypes(), "", True)
                         + select("customer_id", "Asiakas", _options_customers(), "", blank="(sisäinen työ)")
                         + field("site", "Kohde") + field("task_ref", "Huoltokello-tehtävä") + "</div>",
                         "Aloita työ", "card")
    rows = db.q("SELECT t.*, w.name AS wname, c.name AS cname FROM time_entries t JOIN work_types w ON "
                "w.id = t.work_type_id LEFT JOIN customers c ON c.id = t.customer_id WHERE t.employee_id = ? "
                "AND t.work_date BETWEEN ? AND ? ORDER BY t.work_date, t.start_time",
                (view_emp, ws.isoformat(), we.isoformat()))
    total = sum((r["minutes"] for r in rows), 0)
    trs = []
    editable_ids = []
    for r in rows:
        actions = ""
        if r["status"] in ("luonnos", "hylatty") or (sup and r["status"] == "lahetetty"):
            actions = (f"<a href='/tyoaika/{r['id']}'>Muokkaa</a> "
                       + button_form(req, f"/tyoaika/{r['id']}/poista", "Poista", "Poistetaanko kirjaus?", "link"))
            if r["status"] in ("luonnos", "hylatty"):
                editable_ids.append(r["id"])
        trs.append([fi(r["work_date"]), f"{r['start_time']}–{r['end_time']}", num(D(r["minutes"]) / 60),
                    e(r["wname"]), e(r["cname"] or "–"), e(r["site"]), e(r["description"][:60]),
                    badge(r["status"]) + (f"<br><small>{e(r['reject_reason'])}</small>" if r["reject_reason"] else ""),
                    actions])
    nav = (f"<p><a href='/tyoaika?viikko={ws - dt.timedelta(days=7)}&tyontekija={view_emp}'>← Edellinen viikko</a> · "
           f"<b>Viikko {ws.isocalendar()[1]}: {fi(ws.isoformat())}–{fi(we.isoformat())}</b> · "
           f"<a href='/tyoaika?viikko={ws + dt.timedelta(days=7)}&tyontekija={view_emp}'>Seuraava viikko →</a></p>")
    emp_pick = ""
    if sup:
        emp_pick = (f"<form method='get' class='card row'>{select('tyontekija', 'Näytä työntekijä', _options_employees(), view_emp, True)}"
                    f"<input type='hidden' name='viikko' value='{ws}'><div class='actions'><button>Näytä</button></div></form>")
    submit_btn = ""
    if editable_ids:
        hidden = "".join(f"<input type='hidden' name='ids' value='{i}'>" for i in editable_ids)
        submit_btn = (f"<form method='post' action='/tyoaika/laheta' class='inline'>{csrf(req)}{hidden}"
                      f"<button>Lähetä viikon luonnokset hyväksyttäväksi ({len(editable_ids)})</button></form>")
    body = (clock + emp_pick + nav
            + table(["Päivä", "Aika", "h", "Työlaji", "Asiakas", "Kohde", "Kuvaus", "Tila", ""], trs, (2,),
                    "Ei kirjauksia tällä viikolla.")
            + f"<p><b>Yhteensä {num(D(total) / 60)} h</b></p>{submit_btn}<h2>Uusi kirjaus</h2>"
            + _entry_form(req, "/tyoaika/uusi", {"employee_id": view_emp}, sup)
            + "<p><a href='/tarvikkeet/kirjaa'>Kirjaa käytetyt tarvikkeet →</a></p>")
    return layout(req, "Työaika", body)


@route("POST", r"/tyoaika/uusi", WORKERS)
def worktime_new(req):
    sup = req.session["role"] in SUPERVISORS
    emp_id = int(req.f("employee_id") or 0) if sup and req.f("employee_id") else req.session["employee_id"]
    if not emp_id:
        raise HttpError(400, "Työntekijä puuttuu.")
    try:
        eid = timesheet.create(emp_id, dict(req.form), req.session["user_id"], submit=bool(req.f("submit")))
        audit(req, "tyoaika_lisatty", "time_entry", eid)
        flash(req, "ok", "Kirjaus tallennettu.")
    except timesheet.EntryError as exc:
        flash(req, "virhe", str(exc))
    return redirect(f"/tyoaika?tyontekija={emp_id}&viikko={req.f('work_date')}")


@route("GET", r"/tyoaika/(?P<id>\d+)", WORKERS)
def worktime_edit(req):
    row = db.q1("SELECT * FROM time_entries WHERE id = ?", (int(req.params["id"]),))
    if row is None:
        raise HttpError(404, "Kirjausta ei löydy.")
    if row["employee_id"] != req.session["employee_id"] and req.session["role"] not in SUPERVISORS:
        raise HttpError(403)
    return layout(req, "Muokkaa kirjausta", _entry_form(req, f"/tyoaika/{row['id']}", dict(row), False))


@route("POST", r"/tyoaika/(?P<id>\d+)", WORKERS)
def worktime_update(req):
    eid = int(req.params["id"])
    try:
        timesheet.update(eid, dict(req.form), req.session)
        if req.f("submit"):
            timesheet.submit([eid], req.session)
        audit(req, "tyoaika_muokattu", "time_entry", eid)
        flash(req, "ok", "Kirjaus päivitetty.")
    except timesheet.EntryError as exc:
        flash(req, "virhe", str(exc))
        return redirect(f"/tyoaika/{eid}")
    row = db.q1("SELECT employee_id, work_date FROM time_entries WHERE id = ?", (eid,))
    return redirect(f"/tyoaika?tyontekija={row['employee_id']}&viikko={row['work_date']}")


@route("POST", r"/tyoaika/(?P<id>\d+)/poista", WORKERS)
def worktime_delete(req):
    eid = int(req.params["id"])
    try:
        timesheet.delete(eid, req.session)
        audit(req, "tyoaika_poistettu", "time_entry", eid)
        flash(req, "ok", "Kirjaus poistettu.")
    except timesheet.EntryError as exc:
        flash(req, "virhe", str(exc))
    return redirect("/tyoaika")


@route("POST", r"/tyoaika/laheta", WORKERS)
def worktime_submit(req):
    n = timesheet.submit([int(x) for x in req.flist("ids") if x.isdigit()], req.session)
    flash(req, "ok", f"{n} kirjausta lähetetty hyväksyttäväksi.")
    return redirect("/tyoaika")


@route("POST", r"/leimaus/sisaan", WORKERS)
def clock_in(req):
    if not req.session["employee_id"]:
        raise HttpError(400, "Tunnusta ei ole liitetty työntekijään.")
    try:
        timesheet.clock_in(req.session["employee_id"], int(req.f("work_type_id") or 0),
                           int(req.f("customer_id")) if req.f("customer_id") else None, req.f("site"), req.f("task_ref"))
        flash(req, "ok", "Leimattu sisään.")
    except timesheet.EntryError as exc:
        flash(req, "virhe", str(exc))
    return redirect("/tyoaika")


@route("POST", r"/leimaus/ulos", WORKERS)
def clock_out(req):
    try:
        eid = timesheet.clock_out(req.session["employee_id"], req.session["user_id"], int(req.f("break_min") or 0),
                                  req.f("description"), bool(req.f("billable")))
        audit(req, "leimaus_ulos", "time_entry", eid)
        flash(req, "ok", "Leimattu ulos – kirjaus luotu luonnokseksi. Tarkista ja lähetä hyväksyttäväksi.")
    except timesheet.EntryError as exc:
        flash(req, "virhe", str(exc))
    return redirect("/tyoaika")


@route("GET", r"/tarvikkeet/kirjaa", WORKERS)
def material_form(req):
    mats = [(r["id"], f"{r['code']} {r['name']} ({r['unit']})") for r in
            db.q("SELECT * FROM materials WHERE active = 1 ORDER BY code")]
    inner = ("<div class='grid3'>" + select("customer_id", "Asiakas", _options_customers(), "", True)
             + select("material_id", "Tarvike", mats, "", True)
             + field("qty", "Määrä", "1", "number", True, "step=0.01 min=0.01")
             + field("work_date", "Päivä", dt.date.today().isoformat(), "date", True)
             + field("site", "Kohde") + "</div>" + textarea("description", "Lisätieto", "", 2))
    recent = db.q("SELECT m.*, x.name, c.name AS cname FROM material_entries m JOIN materials x ON x.id = m.material_id "
                  "JOIN customers c ON c.id = m.customer_id WHERE m.created_by = ? ORDER BY m.id DESC LIMIT 20",
                  (req.session["user_id"],))
    return layout(req, "Tarvikekirjaus", form(req, "/tarvikkeet/kirjaa", inner, "Kirjaa tarvike")
                  + "<h2>Viimeisimmät kirjaukseni</h2>"
                  + table(["Päivä", "Asiakas", "Tarvike", "Määrä", "Tila"],
                          [[fi(r["work_date"]), e(r["cname"]), e(r["name"]), num(r["qty"]), badge(r["status"])]
                           for r in recent], (3,)))


@route("POST", r"/tarvikkeet/kirjaa", WORKERS)
def material_post(req):
    try:
        mid = timesheet.add_material(int(req.f("customer_id") or 0), int(req.f("material_id") or 0), req.f("qty"),
                                     req.f("work_date"), req.session["user_id"], req.session["employee_id"],
                                     req.f("site"), req.f("description"))
        audit(req, "tarvike_kirjattu", "material_entry", mid)
        flash(req, "ok", "Tarvike kirjattu.")
    except (timesheet.EntryError, ValueError) as exc:
        flash(req, "virhe", str(exc))
    return redirect("/tarvikkeet/kirjaa")


# ------------------------------------------------------------------ hyväksyntä

@route("GET", r"/hyvaksynta", SUPERVISORS)
def approvals(req):
    rows = db.q("SELECT t.*, e.name AS ename, w.name AS wname, c.name AS cname FROM time_entries t "
                "JOIN employees e ON e.id = t.employee_id JOIN work_types w ON w.id = t.work_type_id "
                "LEFT JOIN customers c ON c.id = t.customer_id WHERE t.status = 'lahetetty' "
                "ORDER BY e.name, t.work_date, t.start_time")
    trs = [[f"<input type='checkbox' name='ids' value='{r['id']}' checked>", e(r["ename"]), fi(r["work_date"]),
            f"{r['start_time']}–{r['end_time']} ({r['break_min']} min tauko)", num(D(r["minutes"]) / 60),
            e(r["wname"]), e(r["cname"] or "–"), e(r["site"]), e(r["description"][:80]),
            "kyllä" if r["billable"] else "ei", num(r["km"], 0) if D(r["km"]) else "",
            f"<a href='/tyoaika/{r['id']}'>Muokkaa</a>"] for r in rows]
    tbl = table(["<input type='checkbox' data-checkall='ids' checked>", "Työntekijä", "Päivä", "Aika", "h", "Työlaji",
                 "Asiakas", "Kohde", "Kuvaus", "Laskutettava", "Km", ""], trs, (4, 10), "Ei hyväksyttäviä kirjauksia.")
    tbl = tbl.replace("<th>&lt;input type=&#x27;checkbox&#x27; data-checkall=&#x27;ids&#x27; checked&gt;</th>",
                      "<th><input type='checkbox' data-checkall='ids' checked></th>")
    body = (f"<form method='post' action='/hyvaksynta' class='card'>{csrf(req)}{tbl}"
            f"<div class='row'>{field('reason', 'Hylkäyksen syy (pakollinen hylättäessä)')}</div>"
            f"<div class='actions'><button name='action' value='approve'>Hyväksy valitut</button> "
            f"<button name='action' value='reject' class='danger'>Hylkää valitut</button></div></form>")
    approved = db.q("SELECT t.id, t.work_date, e.name AS ename, t.minutes, t.status FROM time_entries t "
                    "JOIN employees e ON e.id = t.employee_id WHERE t.status = 'hyvaksytty' AND t.invoice_id IS NULL "
                    "AND t.payroll_run_id IS NULL ORDER BY t.approved_at DESC LIMIT 30")
    body += "<h2>Viimeksi hyväksytyt (palautettavissa)</h2>" + table(
        ["Päivä", "Työntekijä", "h", ""],
        [[fi(r["work_date"]), e(r["ename"]), num(D(r["minutes"]) / 60),
          button_form(req, f"/tyoaika/{r['id']}/palauta", "Palauta käsittelyyn", "", "link")] for r in approved], (2,))
    return layout(req, "Työaikojen hyväksyntä", body)


@route("POST", r"/hyvaksynta", SUPERVISORS)
def approvals_post(req):
    ids = [int(x) for x in req.flist("ids") if x.isdigit()]
    if not ids:
        flash(req, "virhe", "Valitse vähintään yksi kirjaus.")
        return redirect("/hyvaksynta")
    try:
        if req.f("action") == "reject":
            n = timesheet.reject(ids, req.f("reason"))
            audit(req, "tyoaika_hylatty", "time_entry", ",".join(map(str, ids)), req.f("reason"))
            flash(req, "ok", f"{n} kirjausta hylätty.")
        else:
            n = timesheet.approve(ids, req.session["user_id"])
            audit(req, "tyoaika_hyvaksytty", "time_entry", ",".join(map(str, ids)))
            flash(req, "ok", f"{n} kirjausta hyväksytty.")
    except timesheet.EntryError as exc:
        flash(req, "virhe", str(exc))
    return redirect("/hyvaksynta")


@route("POST", r"/tyoaika/(?P<id>\d+)/palauta", SUPERVISORS)
def worktime_reopen(req):
    try:
        timesheet.reopen(int(req.params["id"]))
        audit(req, "tyoaika_palautettu", "time_entry", req.params["id"])
        flash(req, "ok", "Kirjaus palautettu käsittelyyn.")
    except timesheet.EntryError as exc:
        flash(req, "virhe", str(exc))
    return redirect("/hyvaksynta")


# ------------------------------------------------------------------ rekisterit

@route("GET", r"/rekisterit", SUPERVISORS)
def registers(req):
    links = [("/asiakkaat", "Asiakkaat", "asiakastiedot, verkkolaskuosoitteet, asiakashinnat"),
             ("/tyolajit", "Työlajit ja hinnasto", "tuntihinnat, ALV, käännetty verovelvollisuus, kotitalousvähennys"),
             ("/tarvikerekisteri", "Tarvikkeet", "osto- ja myyntihinnat"),
             ("/tyontekijat", "Työntekijät", "palkat, kustannuspaikat"),
             ("/kayttajat", "Käyttäjät", "tunnukset ja roolit"),
             ("/huoltokello", "Huoltokello-yhteys", "kuittaukset, asiakkaat ja työntekijät Huoltokellosta"),
             ("/tuonti", "Tuonti", "asiakkaiden ja työaikojen CSV-tuonti"),
             ("/api-avaimet", "Rajapinta-avaimet", "Huoltokello-integraatio"),
             ("/muutosloki", "Muutosloki", "kaikki tapahtumat, eheystarkistus")]
    if req.session["role"] != "johto":
        links = links[:3]
    body = "<div class='grid3'>" + "".join(f"<a class='card' href='{h}'><b>{e(t)}</b><br><small>{e(d)}</small></a>"
                                           for h, t, d in links) + "</div>"
    return layout(req, "Rekisterit", body)


CUST_FIELDS = [("number", "Asiakasnumero", True), ("name", "Nimi", True), ("business_id", "Y-tunnus / ALV-tunniste", False),
               ("street", "Katuosoite", False), ("postcode", "Postinumero", False), ("city", "Postitoimipaikka", False),
               ("country", "Maakoodi (FI, SE...)", False), ("email", "Laskutussähköposti", False),
               ("phone", "Puhelin", False), ("contact_person", "Yhteyshenkilö", False),
               ("einvoice_address", "Verkkolaskuosoite (OVT/IBAN)", False),
               ("einvoice_operator", "Verkkolaskuoperaattori (välittäjätunnus)", False)]


@route("GET", r"/asiakkaat", SUPERVISORS)
def customers(req):
    qs = req.query.get("haku", "").strip()
    rows = db.q("SELECT * FROM customers WHERE (? = '' OR name LIKE ? OR number LIKE ?) ORDER BY active DESC, name",
                (qs, f"%{qs}%", f"%{qs}%"))
    body = (f"<form method='get' class='card row'>{field('haku', 'Haku', qs)}<div class='actions'><button>Hae</button>"
            f"</div></form><p><a href='/asiakkaat/uusi'>+ Uusi asiakas</a></p>"
            + table(["Nro", "Nimi", "Y-tunnus", "Kaupunki", "Tyyppi", "Maksuehto", "Tila"],
                    [[e(r["number"]), f"<a href='/asiakkaat/{r['id']}'>{e(r['name'])}</a>", e(r["business_id"]),
                      e(r["city"]), "Kuluttaja" if r["is_consumer"] else ("Yritys, käännetty ALV" if r["reverse_charge"]
                                                                          else "Yritys"),
                      f"{r['payment_term_days']} pv", "aktiivinen" if r["active"] else "passiivinen"] for r in rows]))
    return layout(req, "Asiakkaat", body)


def _customer_form(req, row) -> str:
    v = dict(row) if row else {"country": "FI", "payment_term_days": settings.get("invoice.payment_term_days"),
                               "active": 1, "number": _next_customer_number()}
    inner = "<div class='grid3'>" + "".join(field(k, label, v.get(k, ""), required=req_) for k, label, req_ in CUST_FIELDS)
    inner += field("payment_term_days", "Maksuehto (pv)", v.get("payment_term_days", 14), "number", True, "min=0 max=120")
    inner += "</div>" + checkbox("is_consumer", "Kuluttaja-asiakas (kotitalousvähennys, kuluttajan viivästyskorko)",
                                 bool(v.get("is_consumer")))
    inner += checkbox("reverse_charge", "Rakentamispalvelujen ostaja: käännetty verovelvollisuus (AVL 8 c §)",
                      bool(v.get("reverse_charge")))
    inner += checkbox("active", "Aktiivinen", bool(v.get("active", 1))) + textarea("notes", "Muistiinpanot", v.get("notes", ""))
    return form(req, f"/asiakkaat/{row['id']}" if row else "/asiakkaat/uusi", inner)


def _next_customer_number() -> str:
    r = db.q1("SELECT MAX(CAST(number AS INTEGER)) AS m FROM customers WHERE number GLOB '[0-9]*'")
    return str((r["m"] or 1000) + 1)


@route("GET", r"/asiakkaat/uusi", ("johto",))
def customer_new(req):
    return layout(req, "Uusi asiakas", _customer_form(req, None))


@route("GET", r"/asiakkaat/(?P<id>\d+)", SUPERVISORS)
def customer_view(req):
    row = db.q1("SELECT * FROM customers WHERE id = ?", (int(req.params["id"]),))
    if row is None:
        raise HttpError(404)
    prices = db.q("SELECT w.id, w.code, w.name, w.price, cp.price AS cprice FROM work_types w LEFT JOIN customer_prices cp "
                  "ON cp.work_type_id = w.id AND cp.customer_id = ? WHERE w.active = 1 ORDER BY w.code", (row["id"],))
    price_inner = table(["Työlaji", "Perushinta (alv 0)", "Asiakashinta (alv 0)"],
                        [[e(f"{p['code']} {p['name']}"), eur(p["price"]),
                          f"<input name='price_{p['id']}' value='{e(str(p['cprice'] or ''))}' placeholder='perushinta'>"]
                         for p in prices], (1,))
    body = _customer_form(req, row) if req.session["role"] == "johto" else ""
    if req.session["role"] == "johto":
        body += "<h2>Asiakaskohtainen hinnasto</h2>" + form(req, f"/asiakkaat/{row['id']}/hinnat", price_inner,
                                                              "Tallenna hinnat")
    invs = db.q("SELECT * FROM invoices WHERE customer_id = ? ORDER BY id DESC LIMIT 20", (row["id"],))
    body += "<h2>Laskut</h2>" + table(["Nro", "Pvm", "Summa", "Tila"],
                                      [[f"<a href='/laskut/{i['id']}'>{i['number'] or 'luonnos'}</a>",
                                        fi(i["invoice_date"]), eur(i["total_gross"]), badge(i["status"])] for i in invs],
                                      (2,))
    return layout(req, row["name"], body)


def _save_customer(req, cid=None):
    from ..finref import tarkista_ytunnus
    data = {k: req.f(k) for k, _l, _r in CUST_FIELDS}
    data["country"] = (data["country"] or "FI").upper()[:2]
    if not data["number"] or not data["name"]:
        raise ValueError("Asiakasnumero ja nimi ovat pakollisia.")
    if data["business_id"] and data["country"] == "FI" and not tarkista_ytunnus(data["business_id"]):
        raise ValueError("Y-tunnus on virheellinen (muoto 1234567-8, tarkiste).")
    vals = (data["number"], data["name"], data["business_id"], 1 if req.f("is_consumer") else 0, data["street"],
            data["postcode"], data["city"], data["country"], data["email"], data["phone"], data["einvoice_address"],
            data["einvoice_operator"], int(req.f("payment_term_days") or 14), 1 if req.f("reverse_charge") else 0,
            data["contact_person"], req.f("notes"), 1 if req.f("active") else 0)
    if cid:
        db.ex("UPDATE customers SET number=?, name=?, business_id=?, is_consumer=?, street=?, postcode=?, city=?, "
              "country=?, email=?, phone=?, einvoice_address=?, einvoice_operator=?, payment_term_days=?, "
              "reverse_charge=?, contact_person=?, notes=?, active=? WHERE id = ?", vals + (cid,))
        return cid
    return db.ex("INSERT INTO customers(number, name, business_id, is_consumer, street, postcode, city, country, email, "
                 "phone, einvoice_address, einvoice_operator, payment_term_days, reverse_charge, contact_person, notes, "
                 "active) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", vals).lastrowid


@route("POST", r"/asiakkaat/uusi", ("johto",))
def customer_create(req):
    try:
        cid = _save_customer(req)
        audit(req, "asiakas_lisatty", "customer", cid)
        flash(req, "ok", "Asiakas tallennettu.")
        return redirect(f"/asiakkaat/{cid}")
    except (ValueError, Exception) as exc:  # noqa: BLE001 - UNIQUE-virhe ym.
        flash(req, "virhe", _friendly(exc))
        return redirect("/asiakkaat/uusi")


@route("POST", r"/asiakkaat/(?P<id>\d+)", ("johto",))
def customer_update(req):
    cid = int(req.params["id"])
    try:
        _save_customer(req, cid)
        audit(req, "asiakas_muokattu", "customer", cid)
        flash(req, "ok", "Asiakas päivitetty.")
    except Exception as exc:  # noqa: BLE001
        flash(req, "virhe", _friendly(exc))
    return redirect(f"/asiakkaat/{cid}")


@route("POST", r"/asiakkaat/(?P<id>\d+)/hinnat", ("johto",))
def customer_prices(req):
    cid = int(req.params["id"])
    with db.tx():
        for k, v in req.form.items():
            if k.startswith("price_") and k[6:].isdigit():
                wt = int(k[6:])
                if v.strip():
                    p = D(v)
                    if p < 0:
                        continue
                    db.ex("INSERT INTO customer_prices(customer_id, work_type_id, price) VALUES(?,?,?) "
                          "ON CONFLICT(customer_id, work_type_id) DO UPDATE SET price = excluded.price", (cid, wt, str(p)))
                else:
                    db.ex("DELETE FROM customer_prices WHERE customer_id = ? AND work_type_id = ?", (cid, wt))
    audit(req, "asiakashinnat_muokattu", "customer", cid)
    flash(req, "ok", "Asiakashinnat tallennettu.")
    return redirect(f"/asiakkaat/{cid}")


def _friendly(exc: Exception) -> str:
    msg = str(exc)
    if "UNIQUE constraint failed" in msg:
        return "Tunniste (numero/koodi/sähköposti) on jo käytössä."
    if "FOREIGN KEY" in msg:
        return "Tietoon viitataan muualla, joten sitä ei voi poistaa – merkitse se passiiviseksi."
    return msg


@route("GET", r"/tyolajit", SUPERVISORS)
def work_types(req):
    from ..pricing import calc_from_settings, average_wage
    calc = calc_from_settings(average_wage())
    rows = db.q("SELECT * FROM work_types ORDER BY active DESC, code")
    trs = []
    for r in rows:
        m_eur, m_pct = calc.margin_at(r["price"]) if D(r["price"]) else (ZERO, ZERO)
        trs.append([e(r["code"]), f"<a href='/tyolajit/{r['id']}'>{e(r['name'])}</a>", eur(r["price"]),
                    f"{num(r['vat_rate'], 1)} %", e(r["revenue_account"]),
                    "kyllä" if r["construction_service"] else "", "kyllä" if r["household_deductible"] else "",
                    f"{r['rounding_min']} min / min {r['min_billing_min']} min",
                    (f"{eur(m_eur)} ({num(m_pct, 1)} %)" if req.session["role"] == "johto" else ""),
                    "" if r["active"] else "passiivinen"])
    body = ("<p><a href='/tyolajit/uusi'>+ Uusi työlaji</a></p>" if req.session["role"] == "johto" else "") + table(
        ["Koodi", "Nimi", "Hinta alv 0", "ALV", "Tili", "Rakentamispalvelu", "Kotitalousvähennys", "Pyöristys",
         "Kate keskipalkalla", ""], trs, (2,))
    return layout(req, "Työlajit ja hinnasto", body)


def _wt_form(req, row):
    v = dict(row) if row else {"unit": "h", "vat_rate": "25.5", "revenue_account": "3000", "rounding_min": 15,
                               "min_billing_min": 0, "billable": 1, "household_deductible": 1, "active": 1}
    vat_opts = [("25.5", "25,5 %"), ("13.5", "13,5 %"), ("10", "10 %"), ("0", "0 %")]
    accs = [(a["number"], f"{a['number']} {a['name']}") for a in db.q("SELECT * FROM accounts WHERE kind = 'tulo' "
                                                                       "ORDER BY number")]
    inner = ("<div class='grid3'>" + field("code", "Koodi", v.get("code", ""), required=True)
             + field("name", "Nimi", v.get("name", ""), required=True) + field("unit", "Yksikkö", v.get("unit", "h"))
             + field("price", "Hinta alv 0 (€/yks.)", v.get("price", ""), required=True)
             + select("vat_rate", "ALV-kanta", vat_opts, str(D(v.get("vat_rate", "25.5")).normalize()), True)
             + select("revenue_account", "Myyntitili", accs, v.get("revenue_account", "3000"), True)
             + field("rounding_min", "Laskutuksen pyöristys (min)", v.get("rounding_min", 15), "number")
             + field("min_billing_min", "Minimiveloitus (min)", v.get("min_billing_min", 0), "number") + "</div>"
             + checkbox("construction_service", "Rakentamispalvelu (käännetty ALV rakennusalan yritysostajalle)",
                        bool(v.get("construction_service")))
             + checkbox("household_deductible", "Kotitalousvähennykseen oikeuttavaa työtä",
                        bool(v.get("household_deductible")))
             + checkbox("billable", "Oletuksena laskutettava", bool(v.get("billable")))
             + checkbox("active", "Aktiivinen", bool(v.get("active"))))
    return form(req, f"/tyolajit/{row['id']}" if row else "/tyolajit/uusi", inner)


@route("GET", r"/tyolajit/uusi", ("johto",))
def wt_new(req):
    return layout(req, "Uusi työlaji", _wt_form(req, None))


@route("GET", r"/tyolajit/(?P<id>\d+)", ("johto",))
def wt_edit(req):
    row = db.q1("SELECT * FROM work_types WHERE id = ?", (int(req.params["id"]),))
    if row is None:
        raise HttpError(404)
    return layout(req, f"Työlaji {row['code']}", _wt_form(req, row))


def _save_wt(req, wid=None):
    from ..money import validate_vat_rate
    price = D(req.f("price"))
    if price < 0:
        raise ValueError("Hinta ei voi olla negatiivinen.")
    vals = (req.f("code"), req.f("name"), req.f("unit") or "h", str(price), int(req.f("min_billing_min") or 0),
            int(req.f("rounding_min") or 0), str(validate_vat_rate(req.f("vat_rate"))), req.f("revenue_account"),
            1 if req.f("construction_service") else 0, 1 if req.f("household_deductible") else 0,
            1 if req.f("billable") else 0, 1 if req.f("active") else 0)
    if not vals[0] or not vals[1]:
        raise ValueError("Koodi ja nimi ovat pakollisia.")
    if wid:
        db.ex("UPDATE work_types SET code=?, name=?, unit=?, price=?, min_billing_min=?, rounding_min=?, vat_rate=?, "
              "revenue_account=?, construction_service=?, household_deductible=?, billable=?, active=? WHERE id = ?",
              vals + (wid,))
        return wid
    return db.ex("INSERT INTO work_types(code, name, unit, price, min_billing_min, rounding_min, vat_rate, "
                 "revenue_account, construction_service, household_deductible, billable, active) "
                 "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", vals).lastrowid


@route("POST", r"/tyolajit/uusi", ("johto",))
def wt_create(req):
    try:
        wid = _save_wt(req)
        audit(req, "tyolaji_lisatty", "work_type", wid)
        flash(req, "ok", "Työlaji tallennettu.")
    except Exception as exc:  # noqa: BLE001
        flash(req, "virhe", _friendly(exc))
        return redirect("/tyolajit/uusi")
    return redirect("/tyolajit")


@route("POST", r"/tyolajit/(?P<id>\d+)", ("johto",))
def wt_update(req):
    wid = int(req.params["id"])
    try:
        _save_wt(req, wid)
        audit(req, "tyolaji_muokattu", "work_type", wid)
        flash(req, "ok", "Työlaji päivitetty.")
    except Exception as exc:  # noqa: BLE001
        flash(req, "virhe", _friendly(exc))
    return redirect("/tyolajit")


@route("GET", r"/tarvikerekisteri", SUPERVISORS)
def materials(req):
    rows = db.q("SELECT * FROM materials ORDER BY active DESC, code")
    trs = [[e(r["code"]), e(r["name"]), e(r["unit"]), eur(r["purchase_price"]), eur(r["sale_price"]),
            f"{num(r['vat_rate'], 1)} %",
            f"{num((D(r['sale_price']) - D(r['purchase_price'])) / D(r['sale_price']) * 100, 1)} %"
            if D(r["sale_price"]) else "", "" if r["active"] else "passiivinen"] for r in rows]
    add = ""
    if req.session["role"] == "johto":
        add = form(req, "/tarvikerekisteri", "<div class='grid3'>" + field("code", "Koodi", "", required=True)
                   + field("name", "Nimi", "", required=True) + field("unit", "Yksikkö", "kpl")
                   + field("purchase_price", "Ostohinta alv 0", "0") + field("sale_price", "Myyntihinta alv 0", "",
                                                                             required=True)
                   + select("vat_rate", "ALV", [("25.5", "25,5 %"), ("13.5", "13,5 %"), ("10", "10 %"), ("0", "0 %")],
                            "25.5", True) + "</div><small>Saman koodin tallennus päivittää tarvikkeen.</small>",
                   "Tallenna tarvike")
    return layout(req, "Tarvikkeet", add + table(["Koodi", "Nimi", "Yks.", "Ostohinta", "Myyntihinta", "ALV", "Kate",
                                                  ""], trs, (3, 4)))


@route("POST", r"/tarvikerekisteri", ("johto",))
def materials_post(req):
    from ..money import validate_vat_rate
    try:
        db.ex("INSERT INTO materials(code, name, unit, purchase_price, sale_price, vat_rate, revenue_account) "
              "VALUES(?,?,?,?,?,?,?) ON CONFLICT(code) DO UPDATE SET name=excluded.name, unit=excluded.unit, "
              "purchase_price=excluded.purchase_price, sale_price=excluded.sale_price, vat_rate=excluded.vat_rate",
              (req.f("code"), req.f("name"), req.f("unit") or "kpl", str(D(req.f("purchase_price"))),
               str(D(req.f("sale_price"))), str(validate_vat_rate(req.f("vat_rate"))),
               "3100" if req.f("vat_rate") == "25.5" else "3050"))
        audit(req, "tarvike_tallennettu", "material", req.f("code"))
        flash(req, "ok", "Tarvike tallennettu.")
    except Exception as exc:  # noqa: BLE001
        flash(req, "virhe", _friendly(exc))
    return redirect("/tarvikerekisteri")


@route("GET", r"/tyontekijat", ("johto",))
def employees(req):
    rows = db.q("SELECT * FROM employees ORDER BY active DESC, number")
    trs = [[e(r["number"]), f"<a href='/tyontekijat/{r['id']}'>{e(r['name'])}</a>",
            "Tuntipalkka" if r["pay_type"] == "tunti" else "Kuukausipalkka",
            eur(r["hourly_wage"]) + "/h" if r["pay_type"] == "tunti" else eur(r["monthly_salary"]) + "/kk",
            e(r["cost_center"]), e(r["tes"]), "" if r["active"] else "passiivinen"] for r in rows]
    return layout(req, "Työntekijät", "<p><a href='/tyontekijat/uusi'>+ Uusi työntekijä</a></p>"
                  + table(["Nro", "Nimi", "Palkkamuoto", "Palkka", "Kustannuspaikka", "TES", ""], trs))


def _emp_form(req, row):
    v = dict(row) if row else {"pay_type": "tunti", "weekly_hours": "40", "active": 1}
    inner = ("<div class='grid3'>" + field("number", "Henkilönumero (palkanlaskennan)", v.get("number", ""), required=True)
             + field("name", "Nimi", v.get("name", ""), required=True) + field("email", "Sähköposti", v.get("email", ""))
             + select("pay_type", "Palkkamuoto", [("tunti", "Tuntipalkka"), ("kk", "Kuukausipalkka")],
                      v.get("pay_type"), True)
             + field("hourly_wage", "Tuntipalkka €", v.get("hourly_wage", "0"))
             + field("monthly_salary", "Kuukausipalkka €", v.get("monthly_salary", "0"))
             + field("weekly_hours", "Säännöllinen viikkotyöaika h", v.get("weekly_hours", "40"))
             + field("cost_center", "Kustannuspaikka", v.get("cost_center", ""))
             + field("tes", "Työehtosopimus", v.get("tes", ""), help_="esim. Kiinteistöpalvelualan TES") + "</div>"
             + checkbox("active", "Aktiivinen", bool(v.get("active"))))
    return form(req, f"/tyontekijat/{row['id']}" if row else "/tyontekijat/uusi", inner)


@route("GET", r"/tyontekijat/uusi", ("johto",))
def emp_new(req):
    return layout(req, "Uusi työntekijä", _emp_form(req, None))


@route("GET", r"/tyontekijat/(?P<id>\d+)", ("johto",))
def emp_edit(req):
    row = db.q1("SELECT * FROM employees WHERE id = ?", (int(req.params["id"]),))
    if row is None:
        raise HttpError(404)
    return layout(req, row["name"], _emp_form(req, row))


def _save_emp(req, eid=None):
    if req.f("pay_type") not in ("tunti", "kk"):
        raise ValueError("Virheellinen palkkamuoto.")
    for k in ("hourly_wage", "monthly_salary", "weekly_hours"):
        if D(req.f(k) or 0) < 0:
            raise ValueError("Palkka- ja tuntiarvot eivät voi olla negatiivisia.")
    vals = (req.f("number"), req.f("name"), req.f("email"), req.f("pay_type"), str(D(req.f("hourly_wage") or 0)),
            str(D(req.f("monthly_salary") or 0)), str(D(req.f("weekly_hours") or 40)), req.f("cost_center"),
            req.f("tes"), 1 if req.f("active") else 0)
    if not vals[0] or not vals[1]:
        raise ValueError("Numero ja nimi ovat pakollisia.")
    if eid:
        db.ex("UPDATE employees SET number=?, name=?, email=?, pay_type=?, hourly_wage=?, monthly_salary=?, "
              "weekly_hours=?, cost_center=?, tes=?, active=? WHERE id = ?", vals + (eid,))
        return eid
    return db.ex("INSERT INTO employees(number, name, email, pay_type, hourly_wage, monthly_salary, weekly_hours, "
                 "cost_center, tes, active) VALUES(?,?,?,?,?,?,?,?,?,?)", vals).lastrowid


@route("POST", r"/tyontekijat/uusi", ("johto",))
def emp_create(req):
    try:
        eid = _save_emp(req)
        audit(req, "tyontekija_lisatty", "employee", eid)
        flash(req, "ok", "Työntekijä tallennettu.")
    except Exception as exc:  # noqa: BLE001
        flash(req, "virhe", _friendly(exc))
        return redirect("/tyontekijat/uusi")
    return redirect("/tyontekijat")


@route("POST", r"/tyontekijat/(?P<id>\d+)", ("johto",))
def emp_update(req):
    eid = int(req.params["id"])
    try:
        _save_emp(req, eid)
        audit(req, "tyontekija_muokattu", "employee", eid, "palkkatiedot")
        flash(req, "ok", "Työntekijä päivitetty.")
    except Exception as exc:  # noqa: BLE001
        flash(req, "virhe", _friendly(exc))
    return redirect("/tyontekijat")


@route("GET", r"/kayttajat", ("johto",))
def users(req):
    rows = db.q("SELECT u.*, e.name AS ename FROM users u LEFT JOIN employees e ON e.id = u.employee_id "
                "ORDER BY u.active DESC, u.name")
    trs = []
    for r in rows:
        acts = ""
        if r["id"] != req.session["user_id"]:
            acts = (button_form(req, f"/kayttajat/{r['id']}/tila", "Poista käytöstä" if r["active"] else "Ota käyttöön",
                                "Muutetaanko tilin tila?", "link")
                    + " " + button_form(req, f"/kayttajat/{r['id']}/salasana", "Uusi alkusalasana",
                                        "Luodaanko käyttäjälle uusi kertakäyttöinen salasana?", "link"))
        trs.append([e(r["name"]), e(r["email"]), e(ROLES[r["role"]]), e(r["ename"] or "–"),
                    "aktiivinen" if r["active"] else "poistettu käytöstä",
                    "lukittu" if r["locked_until"] else "", acts])
    inner = ("<div class='grid3'>" + field("name", "Nimi", "", required=True)
             + field("email", "Sähköposti (tunnus)", "", "email", True)
             + select("role", "Rooli", list(ROLES.items()), "tyontekija", True)
             + select("employee_id", "Liitä työntekijään", _options_employees(), "", blank="(ei liitetty)") + "</div>"
             + "<small>Ohjelma luo vahvan kertakäyttöisen salasanan, joka näytetään kerran. Käyttäjä vaihtaa sen "
               "ensimmäisellä kirjautumisella.</small>")
    return layout(req, "Käyttäjät", table(["Nimi", "Tunnus", "Rooli", "Työntekijä", "Tila", "", ""], trs)
                  + "<h2>Uusi käyttäjä</h2>" + form(req, "/kayttajat", inner, "Luo käyttäjä"))


@route("POST", r"/kayttajat", ("johto",))
def users_create(req):
    pw = security.generate_password()
    try:
        uid = security.create_user(req.f("email"), req.f("name"), req.f("role"), pw,
                                   int(req.f("employee_id")) if req.f("employee_id") else None, must_change=True)
        audit(req, "kayttaja_luotu", "user", uid, f"rooli={req.f('role')}")
    except Exception as exc:  # noqa: BLE001
        flash(req, "virhe", _friendly(exc))
        return redirect("/kayttajat")
    body = (f"<div class='card'><p>Käyttäjä <b>{e(req.f('email'))}</b> luotu.</p><p>Kertakäyttöinen salasana "
            f"(näytetään vain nyt – toimita käyttäjälle turvallisesti, esim. tekstiviestillä eri kanavaa pitkin):</p>"
            f"<p class='kpi'><code>{e(pw)}</code></p><p><a href='/kayttajat'>Takaisin</a></p></div>")
    return layout(req, "Käyttäjä luotu", body)


@route("POST", r"/kayttajat/(?P<id>\d+)/tila", ("johto",))
def users_toggle(req):
    uid = int(req.params["id"])
    if uid == req.session["user_id"]:
        raise HttpError(400, "Et voi poistaa omaa tunnustasi käytöstä.")
    row = db.q1("SELECT * FROM users WHERE id = ?", (uid,))
    if row["role"] == "johto" and row["active"]:
        n = db.q1("SELECT COUNT(*) AS n FROM users WHERE role = 'johto' AND active = 1")["n"]
        if n <= 1:
            raise HttpError(400, "Viimeistä aktiivista johto-tunnusta ei voi poistaa käytöstä.")
    db.ex("UPDATE users SET active = 1 - active WHERE id = ?", (uid,))
    db.ex("DELETE FROM sessions WHERE user_id = ?", (uid,))
    audit(req, "kayttaja_tila", "user", uid, "poistettu käytöstä" if row["active"] else "otettu käyttöön")
    return redirect("/kayttajat")


@route("POST", r"/kayttajat/(?P<id>\d+)/salasana", ("johto",))
def users_reset(req):
    uid = int(req.params["id"])
    pw = security.generate_password()
    security.set_password(uid, pw, must_change=True)
    audit(req, "salasana_nollattu", "user", uid)
    row = db.q1("SELECT email FROM users WHERE id = ?", (uid,))
    return layout(req, "Uusi alkusalasana", f"<div class='card'><p>Käyttäjän <b>{e(row['email'])}</b> uusi "
                  f"kertakäyttöinen salasana (näytetään vain nyt):</p><p class='kpi'><code>{e(pw)}</code></p>"
                  f"<p>Käyttäjän kaikki istunnot on kirjattu ulos.</p><p><a href='/kayttajat'>Takaisin</a></p></div>")


@route("GET", r"/muutosloki", ("johto", "kirjanpitaja"))
def audit_log(req):
    ok, n, msg = security.verify_audit_chain()
    rows = db.q("SELECT * FROM audit ORDER BY id DESC LIMIT 500")
    body = (f"<p class='{'flash ok' if ok else 'flash virhe'}'>{e(msg)} ({n} tapahtumaa tarkistettu).</p>"
            + table(["Aika (UTC)", "Käyttäjä", "Tapahtuma", "Kohde", "Tiedot", "IP"],
                    [[e(r["ts"]), e(r["user_email"]), e(r["action"]), e(f"{r['entity']} {r['entity_id']}"),
                      e(r["details"][:120]), e(r["ip"])] for r in rows]))
    return layout(req, "Muutosloki", body)


from . import views_finance  # noqa: E402,F401 - rekisteröi talousnäkymät
