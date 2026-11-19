"""Kevyt, riippuvuudeton HTTP-palvelin (http.server) tietoturvaominaisuuksin.

- jokainen pyyntö omassa säikeessä (ThreadingHTTPServer), oma SQLite-yhteys per säie
- istuntoeväste: HttpOnly, SameSite=Strict, Secure (HTTPS:ssä), __Host- -etuliite HTTPS:ssä
- CSRF-tarkistus kaikille POST-pyynnöille (paitsi tunnisteella suojattu /api/)
- tiukat turvaotsakkeet (CSP, X-Frame-Options, nosniff, Referrer-Policy, HSTS HTTPS:ssä)
- kirjautumisyritysten nopeusrajoitus IP-osoitteittain
- pyynnön koon rajat; virheissä ei paljasteta sisäisiä tietoja (lokiin kyllä)
"""
from __future__ import annotations

import email.parser
import email.policy
import http.cookies
import json
import logging
import re
import secrets
import ssl
import threading
import time
import traceback
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .. import db, security

log = logging.getLogger("huoltokello_talous")

MAX_FORM = 2 * 1024 * 1024
MAX_UPLOAD = 25 * 1024 * 1024
COOKIE = "hkt_session"
PRE_COOKIE = "hkt_pre"

ROUTES: list[tuple[str, re.Pattern, object, tuple[str, ...] | None]] = []
TLS_ENABLED = False
_flash: dict[str, list[tuple[str, str]]] = {}
_flash_lock = threading.Lock()
_rate: dict[str, list[float]] = {}
_rate_lock = threading.Lock()


def route(method: str, pattern: str, roles: tuple[str, ...] | None = ("johto",)):
    """Rekisteröi reitin. roles=None: julkinen; () = kaikki kirjautuneet."""
    rx = re.compile("^" + pattern + "$")

    def deco(fn):
        ROUTES.append((method, rx, fn, roles))
        return fn
    return deco


class HttpError(Exception):
    def __init__(self, status: int, message: str = ""):
        super().__init__(message)
        self.status = status
        self.message = message


class Request:
    def __init__(self, handler: "Handler", method: str):
        self.handler = handler
        self.method = method
        parsed = urllib.parse.urlsplit(handler.path)
        self.path = urllib.parse.unquote(parsed.path)
        self.query = {k: v[-1] for k, v in urllib.parse.parse_qs(parsed.query, keep_blank_values=True).items()}
        self.headers = handler.headers
        self.ip = handler.client_address[0]
        self.cookies = http.cookies.SimpleCookie()
        try:
            self.cookies.load(self.headers.get("Cookie", ""))
        except http.cookies.CookieError:
            pass
        self.form: dict[str, str] = {}
        self.form_lists: dict[str, list[str]] = {}
        self.files: dict[str, tuple[str, bytes]] = {}
        self.json = None
        self.session = None
        self.params: dict[str, str] = {}
        self._new_cookies: list[str] = []
        if method == "POST":
            self._read_body()

    def cookie(self, name: str) -> str:
        c = self.cookies.get(name)
        return c.value if c else ""

    def _read_body(self) -> None:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise HttpError(400, "Virheellinen pyyntö.")
        ctype = self.headers.get("Content-Type", "")
        limit = MAX_UPLOAD if ctype.startswith("multipart/form-data") else MAX_FORM
        if length > limit:
            raise HttpError(413, "Pyyntö on liian suuri.")
        body = self.handler.rfile.read(length) if length else b""
        if ctype.startswith("application/x-www-form-urlencoded"):
            parsed = urllib.parse.parse_qs(body.decode("utf-8", "replace"), keep_blank_values=True,
                                           max_num_fields=5000)
            self.form_lists = parsed
            self.form = {k: v[-1] for k, v in parsed.items()}
        elif ctype.startswith("multipart/form-data"):
            msg = email.parser.BytesParser(policy=email.policy.HTTP).parsebytes(
                b"Content-Type: " + ctype.encode("latin-1") + b"\r\nMIME-Version: 1.0\r\n\r\n" + body)
            for part in msg.iter_parts():
                name = part.get_param("name", header="content-disposition")
                if not name:
                    continue
                filename = part.get_filename()
                payload = part.get_payload(decode=True) or b""
                if filename:
                    self.files[name] = (filename, payload)
                else:
                    val = payload.decode("utf-8", "replace")
                    self.form[name] = val
                    self.form_lists.setdefault(name, []).append(val)
        elif ctype.startswith("application/json"):
            try:
                self.json = json.loads(body.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                raise HttpError(400, "Virheellinen JSON.")

    def f(self, name: str, default: str = "") -> str:
        return (self.form.get(name, default) or "").strip()

    def flist(self, name: str) -> list[str]:
        return self.form_lists.get(name, [])

    def set_cookie(self, name: str, value: str, max_age: int | None = None, http_only: bool = True) -> None:
        parts = [f"{name}={value}", "Path=/", "SameSite=Strict"]
        if http_only:
            parts.append("HttpOnly")
        if TLS_ENABLED:
            parts.append("Secure")
        if max_age is not None:
            parts.append(f"Max-Age={max_age}")
        self._new_cookies.append("; ".join(parts))


class Response:
    def __init__(self, body: bytes | str = b"", status: int = 200, content_type: str = "text/html; charset=utf-8",
                 headers: dict | None = None):
        self.body = body.encode("utf-8") if isinstance(body, str) else body
        self.status = status
        self.headers = {"Content-Type": content_type, **(headers or {})}


def redirect(location: str) -> Response:
    if not location.startswith("/") or location.startswith("//"):
        location = "/"
    return Response(b"", 303, headers={"Location": location})


def download(data: bytes, filename: str, content_type: str = "application/octet-stream") -> Response:
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", filename)
    return Response(data, 200, content_type,
                    {"Content-Disposition": f"attachment; filename=\"{safe}\"; filename*=UTF-8''"
                                            f"{urllib.parse.quote(filename)}"})


def flash(req: Request, kind: str, message: str) -> None:
    key = req.cookie(COOKIE) or req.cookie(PRE_COOKIE)
    if not key:
        return
    with _flash_lock:
        _flash.setdefault(key, []).append((kind, message))


def take_flash(req: Request) -> list[tuple[str, str]]:
    key = req.cookie(COOKIE) or req.cookie(PRE_COOKIE)
    with _flash_lock:
        return _flash.pop(key, []) if key else []


def rate_limited(key: str, limit: int = 10, window: float = 60.0) -> bool:
    now = time.monotonic()
    with _rate_lock:
        hits = [t for t in _rate.get(key, []) if now - t < window]
        hits.append(now)
        _rate[key] = hits
        if len(_rate) > 10000:
            _rate.clear()
        return len(hits) > limit


CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; "
       "form-action 'self'; frame-ancestors 'none'; base-uri 'none'; object-src 'none'")


class Handler(BaseHTTPRequestHandler):
    server_version = "HuoltokelloTalous"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # noqa: N802 - kantaluokan nimi
        log.info("%s %s", self.client_address[0], fmt % args)

    def do_GET(self):  # noqa: N802
        self._handle("GET")

    def do_HEAD(self):  # noqa: N802
        self._handle("GET", head=True)

    def do_POST(self):  # noqa: N802
        self._handle("POST")

    def _handle(self, method: str, head: bool = False) -> None:
        req = None
        try:
            req = Request(self, method)
            resp = self._dispatch(req)
        except HttpError as exc:
            from .views import error_page
            resp = error_page(req, exc.status, exc.message)
        except Exception:  # noqa: BLE001
            ref = secrets.token_hex(4)
            log.error("Virhe %s: %s", ref, traceback.format_exc())
            from .views import error_page
            resp = error_page(req, 500, f"Odottamaton virhe (viite {ref}). Tiedot on kirjattu lokiin.")
        finally:
            db.close()
        self._send(req, resp, head)

    def _dispatch(self, req: Request) -> Response:
        for method, rx, fn, roles in ROUTES:
            if method != req.method:
                continue
            m = rx.match(req.path)
            if not m:
                continue
            req.params = m.groupdict()
            if req.path.startswith("/api/") or roles is None:
                if req.method == "POST" and roles is None and not req.path.startswith("/api/"):
                    pre = req.cookie(PRE_COOKIE)
                    if not pre or not secrets.compare_digest(pre, req.f("csrf")):
                        raise HttpError(400, "Lomakkeen tarkiste puuttuu tai on vanhentunut. Lataa sivu uudelleen.")
                return fn(req)
            req.session = security.load_session(req.cookie(COOKIE))
            if req.session is None:
                if req.method == "GET":
                    return redirect("/login?next=" + urllib.parse.quote(req.path))
                raise HttpError(401, "Istunto on vanhentunut. Kirjaudu uudelleen.")
            if req.method == "POST" and not security.csrf_ok(req.session, req.f("csrf")):
                raise HttpError(403, "Lomakkeen tarkiste ei kelpaa. Lataa sivu uudelleen.")
            if req.session["must_change_pw"] and req.path not in ("/salasana", "/logout"):
                return redirect("/salasana")
            if roles and req.session["role"] not in roles:
                raise HttpError(403, "Sinulla ei ole oikeutta tähän toimintoon.")
            return fn(req)
        raise HttpError(404, "Sivua ei löydy.")

    def _send(self, req: Request | None, resp: Response, head: bool) -> None:
        self.send_response(resp.status)
        h = dict(resp.headers)
        h.setdefault("Cache-Control", "no-store")
        h["Content-Security-Policy"] = CSP
        h["X-Content-Type-Options"] = "nosniff"
        h["X-Frame-Options"] = "DENY"
        h["Referrer-Policy"] = "no-referrer"
        h["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        h["Cross-Origin-Opener-Policy"] = "same-origin"
        if TLS_ENABLED:
            h["Strict-Transport-Security"] = "max-age=31536000"
        h["Content-Length"] = str(len(resp.body))
        for k, v in h.items():
            self.send_header(k, v)
        if req is not None:
            for c in req._new_cookies:
                self.send_header("Set-Cookie", c)
        self.end_headers()
        if not head:
            self.wfile.write(resp.body)


def serve(host: str, port: int, certfile: str = "", keyfile: str = "") -> None:
    global TLS_ENABLED
    from . import views  # noqa: F401 - rekisteröi reitit
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    if certfile and keyfile:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(certfile, keyfile)
        httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
        TLS_ENABLED = True
    scheme = "https" if TLS_ENABLED else "http"
    log.info("Huoltokello-Talous kuuntelee %s://%s:%s", scheme, host, port)
    print(f"Huoltokello-Talous käynnissä: {scheme}://{'127.0.0.1' if host in ('0.0.0.0', '') else host}:{port}")
    if host not in ("127.0.0.1", "localhost", "::1") and not TLS_ENABLED:
        print("VAROITUS: palvelu kuuntelee verkossa ilman HTTPS-salausta. Määritä --cert/--key "
              "tai käytä käänteisvälityspalvelinta, jotta salasanat eivät kulje selkokielisinä.")
    try:
        httpd.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
