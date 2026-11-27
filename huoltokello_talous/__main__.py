"""Komentorivi: python -m huoltokello_talous <komento>

  alusta              alustaa datakansion ja luo ensimmäisen johto-tunnuksen
  palvelin            käynnistää selainkäyttöliittymän (oletus http://127.0.0.1:8090)
  luo-johto           luo uuden johto-tunnuksen (jos salasana unohtui)
  varmuuskopio        johdonmukainen varmuuskopio tietokannasta (30 uusinta säilyy)
  paketti             muodostaa kirjanpitäjän paketin kaudelta (ja lähettää --laheta)
  automaattivienti    ajastettu tehtävä: edellisen kuun paketti määritettyyn palveluun
  demodata            lataa esittelyaineiston tyhjään tietokantaan (asiakkaat, aikakirjaukset,
                      laskut, palkat, demo-käyttäjät - ei tuotantokäyttöön)
  tarkista            tietokannan eheys, muutoslokin ketju ja kirjausten täsmäys
  lisenssi            lisenssin tila / aktivointi avaimella ja aktivointikoodilla
"""
from __future__ import annotations

import argparse
import datetime as dt
import getpass
import glob
import logging
import logging.handlers
import os
import sys
import threading
import time

from . import __version__, app_context, db, security, settings


def _setup_logging(data_dir: str, verbose: bool = False) -> None:
    log_file = os.path.join(data_dir, "lokit", "talous.log")
    handler = logging.handlers.RotatingFileHandler(log_file, maxBytes=5_000_000, backupCount=10, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    root = logging.getLogger("huoltokello_talous")
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    root.addHandler(handler)


def _ask_password(email: str, name: str) -> str:
    while True:
        pw1 = getpass.getpass("Salasana: ")
        probs = security.password_problems(pw1, email, name)
        if probs:
            print("  " + " ".join(probs))
            continue
        if getpass.getpass("Salasana uudelleen: ") != pw1:
            print("  Salasanat eivät täsmää.")
            continue
        return pw1


def cmd_create_admin(args) -> int:
    email = args.email or input("Sähköposti (tunnus): ").strip()
    name = args.name or input("Nimi: ").strip()
    pw = os.environ.get("HKT_ADMIN_PASSWORD") or _ask_password(email, name)
    try:
        uid = security.create_user(email, name, "johto", pw, must_change=False)
    except ValueError as exc:
        print(f"Virhe: {exc}")
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"Virhe: {exc}")
        return 1
    security.audit("johto_luotu_komentorivilla", "user", uid, "")
    print(f"Johto-tunnus luotu: {email}")
    return 0


def cmd_init(args) -> int:
    print(f"Datakansio: {app_context.data_dir()}")
    if db.q1("SELECT 1 FROM users WHERE role = 'johto' AND active = 1"):
        print("Johto-tunnus on jo olemassa.")
        return 0
    print("Luodaan ensimmäinen johto-tunnus.")
    return cmd_create_admin(args)


def backup(keep: int = 30) -> str:
    dest_dir = os.path.join(app_context.data_dir(), "varmuuskopiot")
    dest = os.path.join(dest_dir, f"talous-{dt.datetime.now():%Y%m%d-%H%M%S}.db")
    db.backup_to(dest)
    security.restrict_file_acl(dest)
    files = sorted(glob.glob(os.path.join(dest_dir, "talous-*.db")))
    for old in files[:-keep]:
        os.remove(old)
    return dest


def cmd_backup(args) -> int:
    print("Varmuuskopio:", backup(args.keep))
    return 0


def _period_bounds(period: str) -> tuple[str, str]:
    from .exports.package import month_bounds
    return month_bounds(period)


def cmd_package(args) -> int:
    from .exports import package, transport
    period = args.kausi or package.previous_month()
    start, end = _period_bounds(period)
    data, fname, info = package.build_package(start, end)
    out = args.ulos or os.path.join(app_context.data_dir(), "viennit", fname)
    with open(out, "wb") as f:
        f.write(data)
    print(f"Paketti: {out}")
    for txt, ok in info["checks"]:
        print(f"  [{'OK' if ok else '!!'}] {txt}")
    if args.laheta:
        res = transport.send("kuukausipaketti", data, fname, period, None, force=args.pakota,
                             journal_range=(start, end), content_key=info["content_key"])
        print(f"Siirto: {res['status']} – {res['message']}")
        return 0 if res["status"] in ("ok", "ohitettu", "odottaa") else 2
    return 0


def auto_export_due(today: dt.date | None = None) -> str | None:
    from .exports import package
    today = today or dt.date.today()
    if not settings.get("export.auto_enabled") or today.day < int(settings.get("export.auto_day")):
        return None
    period = package.previous_month(today)
    done = db.q1("SELECT 1 FROM exports WHERE kind = 'automaattinen' AND period = ? AND status IN ('ok','odottaa')",
                 (period,))
    return None if done else period


def run_auto_export(today: dt.date | None = None) -> dict | None:
    from .exports import package, transport
    period = auto_export_due(today)
    if not period:
        return None
    start, end = _period_bounds(period)
    data, fname, info = package.build_package(start, end)
    with open(os.path.join(app_context.data_dir(), "viennit", fname), "wb") as f:
        f.write(data)
    res = transport.send("automaattinen", data, fname, period, None, journal_range=(start, end),
                         content_key=info["content_key"])
    if res["status"] == "ohitettu":
        # Sama aineisto on jo siirretty käsin: merkitään kausi hoidetuksi, ettei yritystä toisteta päivittäin
        from .security import iso, utcnow
        db.ex("INSERT INTO exports(ts, kind, period, target, filename, status, message) VALUES(?,?,?,?,?,?,?)",
              (iso(utcnow()), "automaattinen", period, settings.get_str("export.transport"), fname, "ok",
               res["message"]))
    security.audit("automaattivienti", "export", period, f"{res['status']}: {res['message']}")
    return res


def cmd_auto(args) -> int:
    res = run_auto_export()
    print("Ei ajettavaa." if res is None else f"{res['status']}: {res['message']}")
    return 0 if res is None or res["status"] != "virhe" else 2


def cmd_hk(args) -> int:
    from . import hk_bridge
    try:
        if args.esikatselu:
            conn = hk_bridge.open_hk(args.polku)
            try:
                plan = hk_bridge.build_plan(conn)
            finally:
                conn.close()
            print(f"Aikaleimat: {plan.tz} ({plan.tz_reason})")
            for k, v in plan.summary().items():
                print(f"  {k}: {v}")
            for x in plan.entries:
                if x["hours_only"]:
                    print(f"  {x['date']} {x['start']:%H:%M}-{x['end']:%H:%M} {x['user_name']}: HK-{x['task']} "
                          f"{x['title']} ({x['bill_reason']})")
            for w in plan.warnings:
                print("  ! " + w)
            for s_ in plan.skipped:
                print(f"  ohitetaan HK-{s_.get('task')}: {s_['reason']}")
            return 0
        res = hk_bridge.sync("komentorivi", None, args.polku)
        print(f"Tuotu {res['tuotu_tyoaikoja']} työaikaa, {res['tuotu_tarvikkeita']} tarviketta.")
        return 0
    except hk_bridge.BridgeError as exc:
        print("Virhe:", exc)
        return 1


def cmd_verify(args) -> int:
    from . import ledger
    ok = True
    integ = db.integrity_check()
    print("Tietokannan eheys:", integ)
    ok &= integ == "ok"
    chain_ok, n, msg = security.verify_audit_chain()
    print(f"Muutosloki: {msg} ({n} tapahtumaa)")
    ok &= chain_ok
    s = ledger.saldot("0001-01-01", "9999-12-31")
    bal = sum(s.values())
    print("Kirjanpito täsmää:", "kyllä" if bal == 0 else f"EI (erotus {bal})")
    ok &= bal == 0
    return 0 if ok else 1


def _background(stop: threading.Event) -> None:
    log = logging.getLogger("huoltokello_talous")
    last_backup = None
    while not stop.wait(60):
        try:
            today = dt.date.today()
            if last_backup != today and dt.datetime.now().hour >= 2:
                backup()
                last_backup = today
                log.info("Päivittäinen varmuuskopio tehty")
            res = run_auto_export()
            if res:
                log.info("Automaattivienti: %s", res)
            from . import hk_bridge
            if hk_bridge.due():
                try:
                    log.info("Huoltokello-synkronointi: %s", hk_bridge.sync("automaattinen"))
                except hk_bridge.BridgeError as exc:
                    log.warning("Huoltokello-synkronointi epäonnistui: %s", exc)
                    settings.set("hk.last_sync", security.iso(security.utcnow()))
        except Exception:  # noqa: BLE001
            log.exception("Taustatehtävä epäonnistui")
        finally:
            db.close()


def cmd_serve(args) -> int:
    from .web import server
    if not db.q1("SELECT 1 FROM users WHERE role = 'johto' AND active = 1"):
        print("Johto-tunnusta ei ole. Aja ensin: python -m huoltokello_talous alusta")
        return 1
    stop = threading.Event()
    t = threading.Thread(target=_background, args=(stop,), daemon=True)
    t.start()
    # KOKEILUVERSIO: valvotaan kokeilujaksoa myös ajossa olevan palvelimen
    # aikana (tarkistus tunnin välein taustasäikeessä).
    w = threading.Thread(target=_kokeilu_valvonta, args=(args.data, stop), daemon=True)
    w.start()
    try:
        server.serve(args.host, args.port, args.cert or "", args.key or "")
    finally:
        stop.set()
    return 0


def _kokeilu_valvonta(data_dir: str | None, stop: threading.Event) -> None:
    """Lopettaa käynnissä olevan palvelimen, kun 90 päivän kokeilujakso päättyy."""
    import os
    from . import licenssi, trial
    while not stop.wait(3600):
        try:
            tila = trial.hae_tila(data_dir)
        except Exception:  # noqa: BLE001 - valvonta ei saa kaataa palvelinta
            return
        if not tila.get("paattynyt"):
            continue
        # Voimassa oleva lisenssi ohittaa kokeilujakson valvonnan.
        try:
            if licenssi.lataa_tila().get("voimassa"):
                continue
        except Exception:  # noqa: BLE001
            pass
        log.critical("Kokeilujakso päättynyt - palvelin pysäytetään.")
        trial.tulosta_paattymisilmoitus(tila)
        trial.ikkuna_paattynyt(tila)
        sys.stdout.flush()
        try:
            db.close()
        except Exception:  # noqa: BLE001
            pass
        os._exit(2)


def cmd_lisenssi(args) -> int:
    """Näyttää lisenssin tilan tai aktivoi avain + aktivointikoodilla."""
    from . import licenssi
    if args.toiminto == "aktivoi":
        if not args.avain or not args.koodi:
            print("Anna --avain ja --koodi (kehittäjältä). Konekoodi: "
                  + licenssi.muotoile_kone(licenssi.konekoodi()))
            return 1
        tulos = licenssi.aktivoi(args.avain, args.koodi)
        if tulos.get("ok"):
            print("Lisenssi aktivoitu: {} (taso {}).".format(tulos.get("nimi", ""), tulos.get("taso", "")))
            return 0
        print("Virhe: " + str(tulos.get("virhe", "aktivointi epäonnistui")))
        return 1
    tila = licenssi.lataa_tila()
    if tila.get("voimassa"):
        print("Lisenssi voimassa: {} (taso {}, avain {}...)".format(tila.get("nimi", ""), tila.get("taso", ""), tila.get("avain", "")[:5]))
        print("Vanhentuu: {}".format(tila.get("voimassa_paattyen", "ikuinen")))
    else:
        print("Ei voimassa olevaa lisenssiä: " + str(tila.get("virhe", "")))
    print("Konekoodi: {} (lähetä kehittäjälle aktivointiin)".format(licenssi.muotoile_kone(licenssi.konekoodi())))
    return 0


def cmd_demodata(args) -> int:
    """Lataa esittelyaineiston tyhjään tietokantaan (python -m huoltokello_talous demodata)."""
    from . import demo
    try:
        summary = demo.load()
    except demo.DemoError as exc:
        print(f"Virhe: {exc}")
        return 1
    print(demo.summarize(summary))
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="huoltokello_talous", description=f"Huoltokello-Talous {__version__}")
    p.add_argument("--data", help="datakansio (oletus %%LOCALAPPDATA%%\\Huoltokello-Talous\\Data)")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("alusta")
    a.add_argument("--email")
    a.add_argument("--name")
    a.set_defaults(fn=cmd_init)
    a = sub.add_parser("luo-johto")
    a.add_argument("--email")
    a.add_argument("--name")
    a.set_defaults(fn=cmd_create_admin)
    a = sub.add_parser("palvelin")
    a.add_argument("--host", default="127.0.0.1")
    a.add_argument("--port", type=int, default=8090)
    a.add_argument("--cert", help="TLS-varmenne (PEM) lähiverkkokäyttöön")
    a.add_argument("--key", help="TLS-yksityisavain (PEM)")
    a.set_defaults(fn=cmd_serve)
    a = sub.add_parser("varmuuskopio")
    a.add_argument("--keep", type=int, default=30)
    a.set_defaults(fn=cmd_backup)
    a = sub.add_parser("paketti")
    a.add_argument("--kausi", help="VVVV-KK (oletus edellinen kuukausi)")
    a.add_argument("--ulos", help="tallennuspolku")
    a.add_argument("--laheta", action="store_true", help="siirrä määritettyyn kirjanpitopalveluun")
    a.add_argument("--pakota", action="store_true", help="lähetä uudelleen vaikka jo siirretty")
    a.set_defaults(fn=cmd_package)
    a = sub.add_parser("automaattivienti")
    a.set_defaults(fn=cmd_auto)
    a = sub.add_parser("huoltokello-synk", help="tuo kuittaukset ja asiakkaat Huoltokellosta")
    a.add_argument("--esikatselu", action="store_true")
    a.add_argument("--polku", help="huoltokello.db-tiedoston polku")
    a.set_defaults(fn=cmd_hk)
    a = sub.add_parser("tarkista")
    a.set_defaults(fn=cmd_verify)
    a = sub.add_parser("demodata", help="lataa demodataa tyhjään tietokantaan (esittelykäyttöön)")
    a.set_defaults(fn=cmd_demodata)
    a = sub.add_parser("lisenssi", help="lisenssin tila ja aktivointi (ilman lisensiä 90 pv kokeilujakso)")
    a.add_argument("toiminto", nargs="?", choices=["tila", "aktivoi"], default="tila")
    a.add_argument("--avain", default="")
    a.add_argument("--koodi", default="")
    a.set_defaults(fn=cmd_lisenssi)
    args = p.parse_args(argv)
    data_dir = app_context.init(args.data)
    # KOKEILUVERSIO: kaikki komennot estetään kokeilujakson päätyttyä (90 päivää),
    # paitsi kun koneella on voimassa oleva lisenssi (kehittäjän myöntämä).
    from . import licenssi, trial
    kokeilu = trial.hae_tila(data_dir)
    trial.tulosta_tilarivi(kokeilu)
    lisensa = licenssi.lataa_tila()
    if lisensa.get("voimassa"):
        print("Lisenssi: {} (taso {}) - kokeilurajoitukset ohitettu.".format(lisensa.get("nimi", ""), lisensa.get("taso", "")))
    if kokeilu["paattynyt"] and not lisensa.get("voimassa"):
        trial.tulosta_paattymisilmoitus(kokeilu)
        aktivoitu_nyt = trial.ikkuna_paattynyt(kokeilu)
        if not aktivoitu_nyt:
            return 2
        lisensa = licenssi.lataa_tila()
        if not lisensa.get("voimassa"):
            return 2
        print("Lisenssi aktivoitu: {}. Ohjelma käynnistyy.".format(lisensa.get("nimi", "")))
    if kokeilu["uusi"]:
        trial.tulosta_tervehtiminen(kokeilu)
        trial.ikkuna_tervetuloa(kokeilu)
    _setup_logging(data_dir, args.verbose)
    try:
        return args.fn(args)
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
