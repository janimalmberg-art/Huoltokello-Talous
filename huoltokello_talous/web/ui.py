"""Käyttöliittymän apurit: sivupohja, navigaatio, lomakkeet ja taulukot. Kaikki tulosteet escapoidaan."""
from __future__ import annotations

import datetime as dt
import html
from decimal import Decimal

from .. import __version__, settings
from ..money import fmt_dec, fmt_eur
from ..security import ROLES
from .server import Request, Response, take_flash

e = html.escape

NAV = [
    ("/", "Etusivu", ("johto", "tyonjohto", "tyontekija", "kirjanpitaja")),
    ("/tyoaika", "Työaika", ("johto", "tyonjohto", "tyontekija")),
    ("/hyvaksynta", "Hyväksyntä", ("johto", "tyonjohto")),
    ("/laskut", "Laskutus", ("johto", "tyonjohto", "kirjanpitaja")),
    ("/tarjoukset", "Tarjoukset", ("johto", "tyonjohto")),
    ("/hinnoittelu", "Hinnoittelu", ("johto",)),
    ("/kirjanpito", "Kirjanpito", ("johto", "kirjanpitaja")),
    ("/palkat", "Palkat", ("johto", "kirjanpitaja")),
    ("/viennit", "Viennit", ("johto", "kirjanpitaja")),
    ("/rekisterit", "Rekisterit", ("johto", "tyonjohto")),
    ("/asetukset", "Asetukset", ("johto",)),
]

STATUS_FI = {
    "luonnos": "Luonnos", "lahetetty": "Lähetetty", "hyvaksytty": "Hyväksytty", "hylatty": "Hylätty",
    "laskutettu": "Laskutettu", "avoin": "Avoin", "osittain": "Osittain maksettu", "maksettu": "Maksettu",
    "hyvitetty": "Hyvitetty", "mitatoity": "Mitätöity", "lukittu": "Lukittu", "ok": "OK", "virhe": "Virhe",
    "odottaa": "Koeajo",
}


def fi(d) -> str:
    if not d:
        return ""
    x = dt.date.fromisoformat(str(d)[:10])
    return f"{x.day}.{x.month}.{x.year}"


def eur(v) -> str:
    return fmt_eur(v)


def num(v, places=2) -> str:
    return fmt_dec(v, places)


def badge(status: str) -> str:
    return f"<span class='badge b-{e(status)}'>{e(STATUS_FI.get(status, status))}</span>"


def csrf(req: Request) -> str:
    token = req.session["csrf"] if req.session else req.cookie("hkt_pre")
    return f"<input type='hidden' name='csrf' value='{e(token or '')}'>"


def layout(req: Request, title: str, body: str, status: int = 200) -> Response:
    sess = req.session
    nav = ""
    user = ""
    if sess:
        for href, label, roles in NAV:
            if sess["role"] in roles:
                active = " class='active'" if (req.path == href or (href != "/" and req.path.startswith(href))) else ""
                nav += f"<a href='{href}'{active}>{e(label)}</a>"
        user = (f"<div class='user'>{e(sess['name'])} · {e(ROLES.get(sess['role'], ''))} "
                f"<a href='/salasana'>Salasana</a> <form method='post' action='/logout' class='inline'>{csrf(req)}"
                f"<button class='link'>Kirjaudu ulos</button></form></div>")
    flashes = "".join(f"<div class='flash {e(k)}'>{e(m)}</div>" for k, m in take_flash(req))
    company = e(settings.get_str("company.name") or "Huoltokello-Talous")
    page = f"""<!DOCTYPE html><html lang="fi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{e(title)} – Huoltokello-Talous</title>
<link rel="stylesheet" href="/static/app.css"><script src="/static/app.js" defer></script></head><body>
<header><div class="brand">🕰 Huoltokello-Talous <span>{company}</span></div>{user}<nav>{nav}</nav></header>
<main>{flashes}<h1>{e(title)}</h1>{body}</main>
<footer>Huoltokello-Talous {__version__}</footer></body></html>"""
    return Response(page, status)


def field(name: str, label: str, value="", type_: str = "text", required: bool = False, attrs: str = "",
          help_: str = "") -> str:
    req = " required" if required else ""
    v = "" if value is None else value
    return (f"<label>{e(label)}{' *' if required else ''}<input type='{type_}' name='{e(name)}' value='{e(str(v))}'"
            f"{req} {attrs}>{f'<small>{e(help_)}</small>' if help_ else ''}</label>")


def textarea(name: str, label: str, value="", rows: int = 3) -> str:
    return f"<label>{e(label)}<textarea name='{e(name)}' rows='{rows}'>{e(str(value or ''))}</textarea></label>"


def select(name: str, label: str, options: list[tuple], selected="", required: bool = False, blank: str = "") -> str:
    opts = f"<option value=''>{e(blank)}</option>" if blank or not required else ""
    for val, text in options:
        sel = " selected" if str(val) == str(selected) else ""
        opts += f"<option value='{e(str(val))}'{sel}>{e(str(text))}</option>"
    return f"<label>{e(label)}{' *' if required else ''}<select name='{e(name)}'{' required' if required else ''}>" \
           f"{opts}</select></label>"


def checkbox(name: str, label: str, checked: bool = False) -> str:
    return f"<label class='check'><input type='checkbox' name='{e(name)}' value='1'" \
           f"{' checked' if checked else ''}> {e(label)}</label>"


def form(req: Request, action: str, inner: str, submit: str = "Tallenna", cls: str = "card",
         confirm: str = "", multipart: bool = False) -> str:
    c = f" data-confirm='{e(confirm)}'" if confirm else ""
    enc = " enctype='multipart/form-data'" if multipart else ""
    return f"<form method='post' action='{e(action)}' class='{cls}'{c}{enc}>{csrf(req)}{inner}" \
           f"<div class='actions'><button>{e(submit)}</button></div></form>"


def button_form(req: Request, action: str, label: str, confirm: str = "", cls: str = "", hidden: dict | None = None) -> str:
    c = f" data-confirm='{e(confirm)}'" if confirm else ""
    h = "".join(f"<input type='hidden' name='{e(k)}' value='{e(str(v))}'>" for k, v in (hidden or {}).items())
    return f"<form method='post' action='{e(action)}' class='inline'{c}>{csrf(req)}{h}" \
           f"<button class='{cls}'>{e(label)}</button></form>"


def table(headers: list[str], rows: list[list], num_cols: tuple[int, ...] = (), empty: str = "Ei rivejä.") -> str:
    if not rows:
        return f"<p class='muted'>{e(empty)}</p>"
    th = "".join(f"<th{' class=n' if i in num_cols else ''}>{e(h)}</th>" for i, h in enumerate(headers))
    body = ""
    for r in rows:
        body += "<tr>" + "".join(f"<td{' class=n' if i in num_cols else ''}>{c}</td>" for i, c in enumerate(r)) + "</tr>"
    return f"<div class='tablewrap'><table><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table></div>"


def period_picker(req: Request, action: str, start: str, end: str, extra: str = "") -> str:
    today = dt.date.today()
    first = today.replace(day=1)
    prev_end = first - dt.timedelta(days=1)
    prev_start = prev_end.replace(day=1)
    quick = (f"<a href='{action}?start={prev_start}&end={prev_end}'>Edellinen kk</a> · "
             f"<a href='{action}?start={first}&end={today}'>Kuluva kk</a> · "
             f"<a href='{action}?start={today.replace(month=1, day=1)}&end={today}'>Vuosi</a>")
    return (f"<form method='get' action='{action}' class='card row'>"
            f"{field('start', 'Alkaen', start, 'date')}{field('end', 'Päättyen', end, 'date')}{extra}"
            f"<div class='actions'><button>Näytä</button></div><div class='quick'>{quick}</div></form>")


def default_period(req: Request) -> tuple[str, str]:
    today = dt.date.today()
    start = req.query.get("start") or today.replace(day=1).isoformat()
    end = req.query.get("end") or today.isoformat()
    try:
        dt.date.fromisoformat(start)
        dt.date.fromisoformat(end)
    except ValueError:
        start, end = today.replace(day=1).isoformat(), today.isoformat()
    return start, end


def dec_input(v) -> str:
    return str(v).replace(".", ",") if isinstance(v, (Decimal, str)) else str(v)


CSS = """
:root{--b:#1e3a8a;--b2:#3b82f6;--bg:#f8fafc;--t:#111827;--m:#6b7280;--ok:#15803d;--er:#b91c1c;--wa:#b45309}
*{box-sizing:border-box}body{margin:0;font-family:Segoe UI,system-ui,Arial,sans-serif;background:var(--bg);color:var(--t);font-size:15px}
header{background:var(--b);color:#fff;padding:10px 18px;display:flex;flex-wrap:wrap;align-items:center;gap:10px}
.brand{font-weight:700;font-size:18px}.brand span{font-weight:400;opacity:.8;font-size:14px;margin-left:8px}
nav{width:100%;display:flex;flex-wrap:wrap;gap:4px}nav a{color:#dbeafe;text-decoration:none;padding:6px 10px;border-radius:6px}
nav a.active,nav a:hover{background:#ffffff22;color:#fff}.user{margin-left:auto;font-size:13px}.user a,.user .link{color:#dbeafe}
main{max-width:1250px;margin:0 auto;padding:16px}h1{font-size:22px;margin:4px 0 14px}h2{font-size:17px;margin:20px 0 8px}
.card{background:#fff;border:1px solid #e5e7eb;border-radius:10px;padding:14px;margin:0 0 14px}
.row{display:flex;flex-wrap:wrap;gap:10px;align-items:flex-end}.row label{flex:1 1 160px}
.grid2{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:14px}
.grid3{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px}
label{display:block;font-size:13px;color:#374151;margin:4px 0}label.check{display:flex;gap:6px;align-items:center}
input,select,textarea{width:100%;padding:8px;border:1px solid #cbd5e1;border-radius:6px;font-size:15px;margin-top:3px;background:#fff}
label.check input{width:auto;margin:0}small{display:block;color:var(--m);font-size:12px}
button{background:var(--b);color:#fff;border:0;border-radius:6px;padding:9px 16px;font-size:15px;cursor:pointer}
button:hover{background:#1d4ed8}button.secondary{background:#e5e7eb;color:#111827}button.danger{background:var(--er)}
button.link{background:none;padding:0;text-decoration:underline;font-size:13px}.actions{margin-top:10px}
form.inline{display:inline}.tablewrap{overflow-x:auto}table{border-collapse:collapse;width:100%;background:#fff}
th,td{padding:7px 8px;border-bottom:1px solid #e5e7eb;text-align:left;vertical-align:top;font-size:14px}
th{background:#f1f5f9;font-weight:600}td.n,th.n{text-align:right;white-space:nowrap}
.badge{display:inline-block;padding:2px 8px;border-radius:10px;font-size:12px;background:#e5e7eb}
.b-hyvaksytty,.b-maksettu,.b-ok,.b-laskutettu{background:#dcfce7;color:#166534}.b-hylatty,.b-virhe{background:#fee2e2;color:#991b1b}
.b-lahetetty,.b-avoin,.b-odottaa{background:#dbeafe;color:#1e40af}.b-osittain{background:#fef3c7;color:#92400e}
.flash{padding:10px 12px;border-radius:8px;margin-bottom:10px}.flash.ok{background:#dcfce7}.flash.virhe{background:#fee2e2}
.flash.info{background:#dbeafe}.muted{color:var(--m)}.kpi{font-size:26px;font-weight:700}.warn{background:#fef3c7;padding:10px;border-radius:8px}
.quick{font-size:13px;width:100%}.clock{font-size:34px;font-weight:700}footer{text-align:center;color:var(--m);font-size:12px;padding:20px}
@media (max-width:640px){main{padding:10px}button{width:100%;padding:12px}td,th{font-size:13px}}
"""

JS = """
document.addEventListener('submit',function(ev){var f=ev.target;var m=f.getAttribute('data-confirm');
if(m&&!window.confirm(m)){ev.preventDefault();}});
document.addEventListener('click',function(ev){var t=ev.target;if(t.matches('[data-checkall]')){
document.querySelectorAll('input[name="'+t.getAttribute('data-checkall')+'"]').forEach(function(c){c.checked=t.checked;});}});
(function(){var el=document.getElementById('clock-elapsed');if(!el)return;var s=new Date(el.getAttribute('data-start'));
function tick(){var d=Math.max(0,Math.floor((new Date()-s)/60000));el.textContent=Math.floor(d/60)+' h '+(d%60)+' min';}
tick();setInterval(tick,30000);})();
"""
