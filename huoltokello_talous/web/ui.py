"""Käyttöliittymän apurit: sivupohja, navigaatio, lomakkeet ja taulukot. Kaikki tulosteet escapoidaan."""
from __future__ import annotations

import datetime as dt
import html
import re
from decimal import Decimal

from .. import __version__, settings
from ..money import fmt_dec, fmt_eur
from ..security import ROLES
from .server import Request, Response, take_flash

e = html.escape

def _c(cx, cy, r):
    return f"M{cx - r} {cy}a{r} {r} 0 1 0 {2 * r} 0a{r} {r} 0 1 0 {-2 * r} 0"


ICONS = {
    "home": "M3 11l9-8 9 8M5 10v10h5v-6h4v6h5V10",
    "clock": _c(12, 12, 9) + "M12 7v5l3 2",
    "check": _c(12, 12, 9) + "M8 12.5l2.8 2.8L16.5 9",
    "receipt": "M6 3h12v18l-3-2-3 2-3-2-3 2zM9 8h6M9 12h6M9 16h3",
    "tag": "M3 12V3h9l9 9-9 9zM7.5 7.5h.01",
    "trend": "M3 17l6-6 4 4 8-8M14 7h7v7",
    "book": "M4 19.5A2.5 2.5 0 016.5 17H20V3H6.5A2.5 2.5 0 004 5.5zM4 19.5A2.5 2.5 0 006.5 22H20v-5",
    "wallet": "M3 7a2 2 0 012-2h14v4M3 7v11a2 2 0 002 2h16V9H5a2 2 0 01-2-2zM16 14.5h.01",
    "send": "M22 2L11 13M22 2l-7 20-4-9-9-4z",
    "db": "M4 5c0 1.7 3.6 3 8 3s8-1.3 8-3-3.6-3-8-3-8 1.3-8 3zM4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3",
    "settings": _c(12, 12, 3) + "M12 2v3M12 19v3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M2 12h3M19 12h3M4.9 19.1L7 17M17 7l2.1-2.1",
    "logout": "M9 21H5a2 2 0 01-2-2V5a2 2 0 012-2h4M16 17l5-5-5-5M21 12H9",
    "lock": "M7 11V7a5 5 0 0110 0v4M5 11h14v10H5z",
    "key": _c(7.5, 15.5, 4.5) + "M10.7 12.3L21 2M17 6l3 3",
    "users": _c(9, 8, 4) + "M2 21c0-4 3-6 7-6s7 2 7 6M16 4.1a4 4 0 010 7.8M22 21c0-3-1.7-5-4-5.7",
    "box": "M21 8l-9-5-9 5 9 5zM3 8v8l9 5 9-5V8M12 13v8",
    "link": "M10 14a5 5 0 007 0l3-3a5 5 0 00-7-7l-1 1M14 10a5 5 0 00-7 0l-3 3a5 5 0 007 7l1-1",
    "upload": "M12 16V4M7 9l5-5 5 5M4 20h16",
    "history": "M3 12a9 9 0 103-6.7L3 8M3 3v5h5M12 7v5l3 2",
    "case": "M3 9a2 2 0 012-2h14a2 2 0 012 2v10a2 2 0 01-2 2H5a2 2 0 01-2-2zM8 7V5a2 2 0 012-2h4a2 2 0 012 2v2M3 13h18",
    "menu": "M4 6h16M4 12h16M4 18h16",
    "chart": "M4 20V11M10 20V4M16 20v-7M22 20H2",
    "user": _c(12, 8, 4) + "M4 21c0-4 3.6-6 8-6s8 2 8 6",
    "plus": "M12 5v14M5 12h14",
    "arrow": "M5 12h14M13 6l6 6-6 6",
    "alert": "M12 3l10 18H2zM12 10v4M12 17.5h.01",
    "info": _c(12, 12, 9) + "M12 11v5M12 7.5h.01",
    "calendar": "M4 6a2 2 0 012-2h12a2 2 0 012 2v14H4zM4 10h16M8 2v4M16 2v4",
}


def icon(name: str, cls: str = "ic") -> str:
    return (f"<svg class='{cls}' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='1.9' "
            f"stroke-linecap='round' stroke-linejoin='round' aria-hidden='true'><path d='{ICONS.get(name, ICONS['info'])}'/></svg>")


ALL = ("johto", "tyonjohto", "tyontekija", "kirjanpitaja")
# (ryhmä, väri, [(osoite, nimi, kuvake, roolit)])
NAV_GROUPS = [
    ("Arki", "teal", [("/", "Etusivu", "home", ALL),
                      ("/tyoaika", "Työaika", "clock", ("johto", "tyonjohto", "tyontekija")),
                      ("/hyvaksynta", "Hyväksyntä", "check", ("johto", "tyonjohto"))]),
    ("Myynti", "amber", [("/laskut", "Laskutus", "receipt", ("johto", "tyonjohto", "kirjanpitaja")),
                         ("/tarjoukset", "Tarjoukset", "tag", ("johto", "tyonjohto")),
                         ("/hinnoittelu", "Hinnoittelu", "trend", ("johto",))]),
    ("Talous", "violet", [("/kirjanpito", "Kirjanpito", "book", ("johto", "kirjanpitaja")),
                          ("/palkat", "Palkat", "wallet", ("johto", "kirjanpitaja")),
                          ("/viennit", "Viennit", "send", ("johto", "kirjanpitaja"))]),
    ("Hallinta", "blue", [("/rekisterit", "Rekisterit", "db", ("johto", "tyonjohto")),
                          ("/asetukset", "Asetukset", "settings", ("johto",)),
                          ("/lisenssi", "Lisenssi", "key", ("johto",))]),
]
NAV = [(h, label, roles) for _g, _c2, items in NAV_GROUPS for h, label, _i, roles in items]
SECTION_OF = {
    "/asiakkaat": "/rekisterit", "/tyolajit": "/rekisterit", "/tarvikerekisteri": "/rekisterit",
    "/tyontekijat": "/rekisterit", "/kayttajat": "/rekisterit", "/huoltokello": "/rekisterit", "/tuonti": "/rekisterit",
    "/api-avaimet": "/rekisterit", "/muutosloki": "/rekisterit", "/raportti": "/kirjanpito", "/tarvikkeet": "/tyoaika",
}


def _section(path: str):
    for prefix, target in SECTION_OF.items():
        if path.startswith(prefix):
            path = target
            break
    best = None
    for g, color, items in NAV_GROUPS:
        for href, label, ic, _r in items:
            if path == href or (href != "/" and path.startswith(href)):
                if best is None or len(href) > len(best[0]):
                    best = (href, label, ic, g, color)
    return best or ("/", "Etusivu", "home", "Arki", "teal")


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


_ACTION_RE = re.compile(r"<a href='([^'<>]+)'>\+ ([^<]+)</a>")
WEEKDAYS = ("ma", "ti", "ke", "to", "pe", "la", "su")


def layout(req: Request, title: str, body: str, status: int = 200) -> Response:
    sess = req.session
    flashes = "".join(f"<div class='flash {e(k)}'>{icon({'ok': 'check', 'virhe': 'alert'}.get(k, 'info'))}"
                      f"<span>{e(m)}</span></div>" for k, m in take_flash(req))
    company = e(settings.get_str("company.name") or "Huoltokello-Talous")
    head = f"""<!DOCTYPE html><html lang="fi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><meta name="theme-color" content="#0f172a">
<title>{e(title)} – Huoltokello-Talous</title>
<link rel="stylesheet" href="/static/app.css"><script src="/static/app.js" defer></script></head>"""
    logo = f"<span class='logo'>{icon('clock')}</span>"
    if not sess:
        page = f"""{head}<body class='auth'><div class='auth-hero'><div class='auth-in'>
<div class='brand big'>{logo}<div>Huoltokello-Talous<span>{company}</span></div></div>
<h2>Laskutus, työajat, palkat ja kirjanpito – yhdessä paikassa.</h2>
<ul class='feats'><li>{icon('clock')}Työajat puhelimella kentältä</li><li>{icon('receipt')}Laskut viitteineen ja Finvoicena</li>
<li>{icon('wallet')}Palkkakoosteet ja tulorekisterin tulolajit</li><li>{icon('send')}Aineisto kirjanpitäjälle yhdellä napilla</li></ul>
</div></div><main class='auth-main'>{flashes}<h1>{e(title)}</h1>{body}
<p class='muted small'>Huoltokello-Talous {__version__}</p></main></body></html>"""
        return Response(page, status)
    href_now, sec_label, sec_icon, group, color = _section(req.path)
    body = _ACTION_RE.sub(lambda m: f"<a class='pill' href='{m.group(1)}'>{icon('plus')}{m.group(2)}</a>", body)
    body = re.sub(r"(</a>)\s*·\s*(<a class='pill')", r"\1\2", body)
    nav = ""
    for g, gcolor, items in NAV_GROUPS:
        links = ""
        for href, label, ic, roles in items:
            if sess["role"] not in roles:
                continue
            active = " class='active'" if href == href_now else ""
            links += f"<a href='{href}'{active}>{icon(ic)}<span>{e(label)}</span></a>"
        if links:
            nav += f"<div class='ng c-{gcolor}'><div class='ngt'>{e(g)}</div>{links}</div>"
    initials = "".join(w[0] for w in str(sess["name"]).split()[:2]).upper() or "?"
    today = dt.date.today()
    crumb = f"<span class='crumb c-{color}'>{e(group)}</span><span class='sep'>/</span>{e(sec_label)}"
    page = f"""{head}<body><input type='checkbox' id='navtoggle' class='navtoggle'>
<div class='app'><aside class='side'>
<a class='brand' href='/'>{logo}<div>Huoltokello-Talous<span>{company}</span></div></a>
<nav>{nav}</nav>
<div class='side-foot'><a href='/salasana'>{icon('lock')}<span>Salasana</span></a>
<form method='post' action='/logout' class='inline'>{csrf(req)}<button class='side-out'>{icon('logout')}<span>Kirjaudu ulos</span></button></form></div>
</aside><label for='navtoggle' class='scrim'></label>
<div class='content'><header class='top'><label for='navtoggle' class='burger' title='Valikko'>{icon('menu')}</label>
<div class='trail'>{crumb}</div>
<div class='today'>{icon('calendar')}{WEEKDAYS[today.weekday()]} {today.day}.{today.month}.{today.year}</div>
<div class='user'><span class='avatar'>{e(initials)}</span><div><b>{e(sess['name'])}</b><small>{e(ROLES.get(sess['role'], ''))}</small></div></div>
</header>
<main><div class='pagehead'><span class='pic c-{color}'>{icon(sec_icon)}</span><h1>{e(title)}</h1></div>{flashes}{body}</main>
<footer>Huoltokello-Talous {__version__} · {company}</footer></div></div></body></html>"""
    return Response(page, status)


def kpi_card(ic: str, color: str, label: str, value: str, link: str = "", link_text: str = "", extra: str = "") -> str:
    a = f"<a class='more' href='{e(link)}'>{e(link_text)} {icon('arrow')}</a>" if link else ""
    return (f"<div class='card kcard c-{color}'><div class='khead'><span class='kic'>{icon(ic)}</span>"
            f"<span class='muted'>{e(label)}</span></div><div class='kpi'>{value}</div>{extra}{a}</div>")


def tile(href: str, ic: str, color: str, title: str, desc: str = "") -> str:
    return (f"<a class='tile c-{color}' href='{e(href)}'><span class='kic'>{icon(ic)}</span>"
            f"<span><b>{e(title)}</b>{f'<small>{e(desc)}</small>' if desc else ''}</span></a>")


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
:root{--ink:#0f172a;--t:#1e293b;--m:#64748b;--line:#e2e8f0;--bg:#f1f5f9;--card:#fff;
--b:#2563eb;--b2:#4f46e5;--ok:#16a34a;--er:#dc2626;--wa:#d97706;
--teal:#0d9488;--teal-s:#ccfbf1;--amber:#d97706;--amber-s:#fef3c7;--violet:#7c3aed;--violet-s:#ede9fe;--blue:#2563eb;--blue-s:#dbeafe;
--rose:#e11d48;--rose-s:#ffe4e6;--green:#16a34a;--green-s:#dcfce7;
--r:14px;--sh:0 1px 2px rgba(15,23,42,.05),0 4px 16px rgba(15,23,42,.06);--sh2:0 10px 30px rgba(15,23,42,.12);--side:256px}
*{box-sizing:border-box}html{scroll-behavior:smooth}
body{margin:0;font-family:"Segoe UI Variable","Segoe UI",Inter,system-ui,-apple-system,Arial,sans-serif;background:var(--bg);
color:var(--t);font-size:15px;line-height:1.45;-webkit-font-smoothing:antialiased}
a{color:var(--b);text-decoration:none}a:hover{text-decoration:underline}
.ic{width:20px;height:20px;flex:none;vertical-align:middle}
.c-teal{--ac:var(--teal);--acs:var(--teal-s)}.c-amber{--ac:var(--amber);--acs:var(--amber-s)}
.c-violet{--ac:var(--violet);--acs:var(--violet-s)}.c-blue{--ac:var(--blue);--acs:var(--blue-s)}
.c-rose{--ac:var(--rose);--acs:var(--rose-s)}.c-green{--ac:var(--green);--acs:var(--green-s)}

/* ---- sivupalkki ---- */
.navtoggle{display:none}.app{display:flex;min-height:100vh}
.side{position:fixed;inset:0 auto 0 0;width:var(--side);display:flex;flex-direction:column;z-index:30;
background:radial-gradient(120% 60% at 0% 0%,#1e3a8a 0%,transparent 60%),radial-gradient(90% 50% at 100% 100%,#134e4a 0%,transparent 60%),#0b1224;
color:#cbd5e1;padding:18px 14px;overflow-y:auto;box-shadow:4px 0 24px rgba(2,6,23,.25)}
.brand{display:flex;align-items:center;gap:12px;color:#fff;font-weight:700;font-size:17px;padding:4px 6px 18px;text-decoration:none!important}
.brand div span{display:block;font-weight:500;font-size:12px;color:#94a3b8;margin-top:2px}
.logo{display:grid;place-items:center;width:42px;height:42px;border-radius:12px;color:#fff;flex:none;
background:linear-gradient(135deg,#3b82f6,#6366f1);box-shadow:0 6px 18px rgba(59,130,246,.45)}
.logo .ic{width:24px;height:24px}
.ng{margin:6px 0 12px}.ngt{font-size:11px;letter-spacing:.12em;text-transform:uppercase;color:#64748b;font-weight:700;
padding:4px 12px 6px;display:flex;align-items:center;gap:8px}
.ngt::before{content:"";width:7px;height:7px;border-radius:50%;background:var(--ac)}
nav a{display:flex;align-items:center;gap:12px;color:#cbd5e1;padding:9px 12px;border-radius:10px;margin:2px 0;
font-weight:500;position:relative;transition:background .18s,color .18s,transform .18s;text-decoration:none!important}
nav a .ic{color:#94a3b8;transition:color .18s,transform .25s}
nav a:hover{background:rgba(255,255,255,.07);color:#fff;transform:translateX(3px)}nav a:hover .ic{color:var(--ac);transform:scale(1.12)}
nav a.active{color:#fff;background:linear-gradient(90deg,color-mix(in srgb,var(--ac) 38%,transparent),rgba(255,255,255,.04));
box-shadow:inset 0 0 0 1px rgba(255,255,255,.06)}
nav a.active::before{content:"";position:absolute;left:-14px;top:8px;bottom:8px;width:4px;border-radius:0 4px 4px 0;background:var(--ac);
box-shadow:0 0 12px var(--ac)}
nav a.active .ic{color:#fff}
.side-foot{margin-top:auto;border-top:1px solid rgba(255,255,255,.08);padding-top:12px;display:flex;flex-direction:column;gap:2px}
.side-foot a,.side-out{display:flex;align-items:center;gap:12px;color:#94a3b8;padding:8px 12px;border-radius:10px;background:none;
font:inherit;width:100%;text-align:left;box-shadow:none;text-decoration:none!important}
.side-foot a:hover,.side-out:hover{background:rgba(255,255,255,.07);color:#fff;transform:none}
.side-foot form{display:block}

/* ---- sisältö ---- */
.content{flex:1;margin-left:var(--side);min-width:0;display:flex;flex-direction:column}
.top{position:sticky;top:0;z-index:20;display:flex;align-items:center;gap:16px;padding:12px 28px;
background:rgba(241,245,249,.82);backdrop-filter:saturate(1.6) blur(12px);-webkit-backdrop-filter:saturate(1.6) blur(12px);
border-bottom:1px solid rgba(226,232,240,.8)}
.burger{display:none;cursor:pointer;color:var(--ink)}
.trail{font-size:14px;color:var(--m);display:flex;align-items:center;gap:8px}.trail .sep{opacity:.5}
.crumb{color:var(--ac);font-weight:700}
.today{margin-left:auto;display:flex;align-items:center;gap:8px;font-size:13px;color:var(--m);background:#fff;
border:1px solid var(--line);padding:6px 12px;border-radius:999px}.today .ic{width:16px;height:16px}
.user{display:flex;align-items:center;gap:10px;font-size:13px}.user b{display:block;color:var(--ink)}.user small{color:var(--m)}
.avatar{display:grid;place-items:center;width:38px;height:38px;border-radius:50%;color:#fff;font-weight:700;font-size:14px;
background:linear-gradient(135deg,#f59e0b,#ef4444);box-shadow:0 4px 12px rgba(239,68,68,.3)}
main{width:100%;max-width:1320px;margin:0 auto;padding:22px 28px 10px;animation:rise .35s ease-out}
@keyframes rise{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}
.pagehead{display:flex;align-items:center;gap:14px;margin:2px 0 18px}
.pic{display:grid;place-items:center;width:46px;height:46px;border-radius:14px;color:var(--ac);background:var(--acs);
box-shadow:inset 0 0 0 1px color-mix(in srgb,var(--ac) 22%,transparent)}.pic .ic{width:24px;height:24px}
h1{font-size:26px;margin:0;color:var(--ink);letter-spacing:-.02em}
h2{font-size:18px;margin:26px 0 10px;color:var(--ink);letter-spacing:-.01em;display:flex;align-items:center;gap:10px}
h2::before{content:"";width:4px;height:18px;border-radius:4px;background:linear-gradient(var(--b),var(--b2))}
footer{text-align:center;color:var(--m);font-size:12px;padding:26px}

/* ---- kortit ---- */
.card{background:var(--card);border:1px solid var(--line);border-radius:var(--r);padding:18px;margin:0 0 16px;box-shadow:var(--sh);
transition:box-shadow .2s,transform .2s}
a.card{display:block;color:var(--t);text-decoration:none}a.card:hover{transform:translateY(-3px);box-shadow:var(--sh2)}
.grid2{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:16px}
.grid3{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:16px}
.grid3>.card,.grid2>.card{margin:0}
.kpi{font-size:30px;font-weight:800;color:var(--ink);letter-spacing:-.02em;margin:6px 0 4px}
.kcard{position:relative;overflow:hidden}
.kcard::after{content:"";position:absolute;right:-40px;top:-40px;width:130px;height:130px;border-radius:50%;
background:radial-gradient(circle,color-mix(in srgb,var(--ac) 22%,transparent),transparent 70%)}
.kcard:hover{transform:translateY(-3px);box-shadow:var(--sh2)}
.khead{display:flex;align-items:center;gap:10px;font-size:13px}
.kic{display:grid;place-items:center;width:38px;height:38px;border-radius:11px;color:var(--ac);background:var(--acs);flex:none}
.more{display:inline-flex;align-items:center;gap:6px;font-weight:600;color:var(--ac);margin-top:8px;font-size:14px}
.more .ic{width:16px;height:16px;transition:transform .2s}.more:hover{text-decoration:none}.more:hover .ic{transform:translateX(4px)}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:14px;margin-bottom:16px}
.tile{display:flex;gap:14px;align-items:center;background:#fff;border:1px solid var(--line);border-radius:var(--r);padding:16px;
color:var(--t);box-shadow:var(--sh);transition:transform .2s,box-shadow .2s,border-color .2s;text-decoration:none!important}
.tile:hover{transform:translateY(-3px);box-shadow:var(--sh2);border-color:color-mix(in srgb,var(--ac) 45%,var(--line))}
.tile b{display:block;color:var(--ink)}.tile small{color:var(--m)}
.hello{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:12px;color:#fff;border-radius:18px;
padding:22px 26px;margin-bottom:18px;background:linear-gradient(120deg,#1d4ed8,#4f46e5 55%,#0d9488);box-shadow:0 12px 30px rgba(79,70,229,.25)}
.hello h3{margin:0;font-size:22px}.hello p{margin:4px 0 0;opacity:.85}
.hello .btn{background:rgba(255,255,255,.16);box-shadow:inset 0 0 0 1px rgba(255,255,255,.3)}
.bars{display:flex;align-items:flex-end;gap:12px;height:170px;padding:10px 4px 0}
.bar{flex:1;display:flex;flex-direction:column;align-items:center;gap:6px;height:100%;justify-content:flex-end;font-size:12px;color:var(--m)}
.bar i{display:block;width:100%;max-width:56px;border-radius:9px 9px 4px 4px;background:linear-gradient(180deg,#6366f1,#3b82f6);
animation:grow .8s cubic-bezier(.2,.8,.2,1) both;transform-origin:bottom}
.bar.now i{background:linear-gradient(180deg,#14b8a6,#0d9488)}.bar b{color:var(--ink);font-size:12px}
@keyframes grow{from{transform:scaleY(0)}to{transform:scaleY(1)}}

.pill{display:inline-flex;align-items:center;gap:6px;padding:7px 14px 7px 10px;margin:2px 4px 6px 0;border-radius:999px;font-weight:600;
font-size:14px;color:var(--b);background:var(--blue-s);border:1px solid #bfdbfe;transition:transform .15s,box-shadow .15s;text-decoration:none!important}
.pill .ic{width:16px;height:16px}.pill:hover{transform:translateY(-1px);box-shadow:0 6px 16px rgba(37,99,235,.18)}

/* ---- lomakkeet ---- */
.row{display:flex;flex-wrap:wrap;gap:12px;align-items:flex-end}.row label{flex:1 1 160px}
label{display:block;font-size:13px;font-weight:600;color:#334155;margin:6px 0}
label.check{display:flex;gap:10px;align-items:center;font-weight:500}
input,select,textarea{width:100%;padding:10px 12px;border:1px solid #cbd5e1;border-radius:10px;font:inherit;font-size:15px;margin-top:5px;
background:#fff;color:var(--t);transition:border-color .15s,box-shadow .15s}
input:hover,select:hover,textarea:hover{border-color:#94a3b8}
input:focus,select:focus,textarea:focus{outline:none;border-color:var(--b);box-shadow:0 0 0 4px rgba(37,99,235,.15)}
label.check input{width:18px;height:18px;margin:0;accent-color:var(--b)}
small{display:block;color:var(--m);font-size:12px;font-weight:400;margin-top:3px}
button,.btn{display:inline-flex;align-items:center;gap:8px;background:linear-gradient(135deg,var(--b),var(--b2));color:#fff;border:0;
border-radius:10px;padding:10px 18px;font:inherit;font-size:15px;font-weight:600;cursor:pointer;
box-shadow:0 4px 14px rgba(37,99,235,.28);transition:transform .15s,box-shadow .15s,filter .15s;text-decoration:none!important}
button:hover,.btn:hover{transform:translateY(-1px);box-shadow:0 8px 20px rgba(37,99,235,.35);filter:brightness(1.06)}
button:active{transform:translateY(0)}
button.secondary{background:#fff;color:var(--ink);box-shadow:inset 0 0 0 1px var(--line),var(--sh)}
button.danger{background:linear-gradient(135deg,#ef4444,#dc2626);box-shadow:0 4px 14px rgba(220,38,38,.28)}
button.link{background:none;padding:0;color:var(--b);box-shadow:none;font-weight:500;font-size:13px;text-decoration:underline}
button.link:hover{transform:none;box-shadow:none}
.actions{margin-top:14px;display:flex;gap:10px;flex-wrap:wrap}form.inline{display:inline}

/* ---- taulukot ---- */
.tablewrap{overflow-x:auto;background:#fff;border:1px solid var(--line);border-radius:var(--r);box-shadow:var(--sh);margin-bottom:16px}
.card .tablewrap{box-shadow:none}
table{border-collapse:separate;border-spacing:0;width:100%;background:#fff}
th,td{padding:11px 14px;border-bottom:1px solid #eef2f7;text-align:left;vertical-align:top;font-size:14px}
th{background:#f8fafc;font-weight:700;font-size:12px;text-transform:uppercase;letter-spacing:.05em;color:#475569;position:sticky;top:0}
tbody tr{transition:background .12s}tbody tr:hover{background:#f5f8ff}tbody tr:last-child td{border-bottom:0}
td.n,th.n{text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}

/* ---- merkit ja viestit ---- */
.badge{display:inline-flex;align-items:center;gap:6px;padding:3px 10px;border-radius:999px;font-size:12px;font-weight:600;
background:#f1f5f9;color:#475569}
.badge::before{content:"";width:6px;height:6px;border-radius:50%;background:currentColor}
.b-hyvaksytty,.b-maksettu,.b-ok,.b-laskutettu{background:var(--green-s);color:#15803d}
.b-hylatty,.b-virhe,.b-mitatoity{background:var(--rose-s);color:#be123c}
.b-lahetetty,.b-avoin,.b-odottaa{background:var(--blue-s);color:#1d4ed8}
.b-osittain,.b-luonnos{background:var(--amber-s);color:#b45309}.b-lukittu,.b-hyvitetty{background:var(--violet-s);color:#6d28d9}
.flash{display:flex;align-items:center;gap:10px;padding:12px 16px;border-radius:12px;margin-bottom:14px;font-weight:500;
border:1px solid transparent;animation:slide .4s ease-out}
.flash.ok{background:var(--green-s);color:#14532d;border-color:#bbf7d0}.flash.virhe{background:var(--rose-s);color:#881337;border-color:#fecdd3}
.flash.info{background:var(--blue-s);color:#1e3a8a;border-color:#bfdbfe}
@keyframes slide{from{opacity:0;transform:translateY(-6px)}to{opacity:1;transform:none}}
.muted{color:var(--m)}.small{font-size:12px}
.warn{background:var(--amber-s);border:1px solid #fde68a;color:#78350f;padding:12px 14px;border-radius:12px}
.quick{font-size:13px;width:100%}.clock{font-size:40px;font-weight:800;color:var(--ink);letter-spacing:-.02em}
code{background:#f1f5f9;padding:2px 6px;border-radius:6px;font-size:.92em}

/* ---- kirjautuminen ---- */
body.auth{display:grid;grid-template-columns:1.1fr 1fr;min-height:100vh;background:#fff}
.auth-hero{color:#e2e8f0;display:flex;align-items:center;padding:48px;position:relative;overflow:hidden;
background:radial-gradient(70% 60% at 10% 10%,#2563eb 0%,transparent 60%),radial-gradient(60% 60% at 90% 90%,#0d9488 0%,transparent 60%),#0b1224}
.auth-hero::after{content:"";position:absolute;inset:0;background-image:radial-gradient(rgba(255,255,255,.08) 1px,transparent 1px);background-size:26px 26px}
.auth-in{position:relative;z-index:1;max-width:520px}
.brand.big{font-size:24px;padding:0 0 30px}.brand.big .logo{width:56px;height:56px;border-radius:16px}.brand.big div span{font-size:14px}
.auth-hero h2{color:#fff;font-size:34px;line-height:1.2;margin:0 0 26px}.auth-hero h2::before{display:none}
.feats{list-style:none;padding:0;margin:0;display:grid;gap:12px}
.feats li{display:flex;align-items:center;gap:12px;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.1);
padding:12px 14px;border-radius:12px;animation:rise .5s ease-out both}
.feats li:nth-child(2){animation-delay:.08s}.feats li:nth-child(3){animation-delay:.16s}.feats li:nth-child(4){animation-delay:.24s}
.feats .ic{color:#5eead4}
.auth-main{align-self:center;max-width:460px;width:100%;margin:0 auto;padding:40px 28px}
.auth-main h1{margin-bottom:18px}.auth-main .card{max-width:none!important;padding:24px}
.auth-main button{width:100%;justify-content:center;padding:12px}

/* ---- puhelin ja tabletti ---- */
.scrim{display:none}
@media (max-width:980px){
.side{transform:translateX(-102%);transition:transform .28s cubic-bezier(.2,.8,.2,1)}
.navtoggle:checked~.app .side{transform:none}
.navtoggle:checked~.app .scrim{display:block;position:fixed;inset:0;background:rgba(2,6,23,.45);z-index:25}
.content{margin-left:0}.burger{display:grid;place-items:center;width:40px;height:40px;border-radius:10px;background:#fff;border:1px solid var(--line)}
.today{display:none}.user div{display:none}.top{padding:10px 14px}main{padding:16px 14px}
body.auth{grid-template-columns:1fr}.auth-hero{padding:28px 22px}.auth-hero h2{font-size:22px;margin-bottom:0}.feats{display:none}
.brand.big{padding-bottom:14px}}
@media (max-width:640px){button{width:100%;justify-content:center;padding:12px}form.inline button,button.link{width:auto}
td,th{font-size:13px;padding:9px 10px}h1{font-size:21px}.pic{width:40px;height:40px}.kpi{font-size:26px}}
@media print{.side,.top,footer{display:none}.content{margin:0}main{animation:none;padding:0}.card,.tablewrap{box-shadow:none}}
@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
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
