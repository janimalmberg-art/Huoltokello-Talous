"""Tietoturva: salasanat, istunnot, CSRF, kirjautumislukitus, muutosloki ja salaisuudet.

- Salasanat: PBKDF2-HMAC-SHA256, 600 000 kierrosta, 16 tavun suola (OWASP 2023+ suositus)
- Istuntotunnisteet: 256-bittinen satunnainen, kannassa vain SHA-256-tiiviste
- CSRF: istuntokohtainen tunniste, vertailu vakioajassa
- Muutosloki: jokainen rivi ketjutetaan edellisen rivin tiivisteeseen
- Salaisuudet (API-avaimet, SMTP-salasana): Windowsissa DPAPI (CurrentUser),
  muualla tiedosto jonka oikeudet rajataan omistajalle (0600)
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import json
import os
import re
import secrets
import sys

from . import db, settings

PBKDF2_ITERATIONS = 600_000
ROLES = {
    "johto": "Johto",
    "tyonjohto": "Työnjohto",
    "tyontekija": "Työntekijä",
    "kirjanpitaja": "Kirjanpitäjä",
}


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def iso(t: dt.datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s: str) -> dt.datetime:
    return dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)


# ---------------------------------------------------------------- salasanat

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return "pbkdf2_sha256${}${}${}".format(
        PBKDF2_ITERATIONS, base64.b64encode(salt).decode(), base64.b64encode(dk).decode())


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters, salt_b64, dk_b64 = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                 base64.b64decode(salt_b64), int(iters))
        return hmac.compare_digest(dk, base64.b64decode(dk_b64))
    except (ValueError, TypeError):
        return False


# Valmiiksi laskettu tiiviste ajoitushyökkäysten tasaamiseen (tuntematon käyttäjä).
_DUMMY_HASH = None


def _dummy_hash() -> str:
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = hash_password(secrets.token_urlsafe(16))
    return _DUMMY_HASH


_COMMON = {"salasana", "password", "qwerty", "huoltokello", "kiinteisto", "123456", "letmein"}


def password_problems(password: str, email: str = "", name: str = "") -> list[str]:
    """Salasanapolitiikka (NIST SP 800-63B -henkinen: pituus ennen monimutkaisuutta)."""
    minlen = int(settings.get("security.min_password_length") or 12)
    probs = []
    if len(password) < minlen:
        probs.append(f"Salasanan on oltava vähintään {minlen} merkkiä.")
    if len(password) > 256:
        probs.append("Salasana on liian pitkä (enintään 256 merkkiä).")
    classes = sum(bool(re.search(p, password)) for p in (r"[a-zåäö]", r"[A-ZÅÄÖ]", r"\d", r"[^\w\s]"))
    if classes < 3 and len(password) < 16:
        probs.append("Käytä vähintään kolmea merkkityyppiä (pienet, isot, numerot, erikoismerkit) "
                     "tai vähintään 16 merkin salalausetta.")
    low = password.lower()
    if any(w in low for w in _COMMON):
        probs.append("Salasana sisältää yleisen tai arvattavan sanan.")
    local = (email or "").split("@")[0].lower()
    if local and len(local) >= 4 and local in low:
        probs.append("Salasana ei saa sisältää käyttäjätunnusta.")
    for part in (name or "").lower().split():
        if len(part) >= 4 and part in low:
            probs.append("Salasana ei saa sisältää nimeäsi.")
            break
    if re.search(r"(.)\1{3,}", password):
        probs.append("Salasanassa ei saa olla samaa merkkiä yli kolmea kertaa peräkkäin.")
    return probs


def generate_password(length: int = 16) -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789!#%+-=?"
    while True:
        pw = "".join(secrets.choice(alphabet) for _ in range(length))
        if not password_problems(pw):
            return pw


# ---------------------------------------------------------------- kirjautuminen

class LoginError(Exception):
    pass


def authenticate(email: str, password: str, ip: str = ""):
    email = (email or "").strip().lower()
    user = db.q1("SELECT * FROM users WHERE email = ?", (email,))
    now = utcnow()
    if user is None or not user["active"]:
        verify_password(password, _dummy_hash())  # tasainen vasteaika
        audit("kirjautuminen_epaonnistui", "user", "", f"tunnus={_mask_email(email)}", ip=ip)
        raise LoginError("Väärä tunnus tai salasana.")
    if user["locked_until"] and parse_iso(user["locked_until"]) > now:
        audit("kirjautuminen_lukittu", "user", user["id"], "", ip=ip, user=user)
        raise LoginError("Tili on tilapäisesti lukittu liian monen epäonnistuneen yrityksen vuoksi. "
                         "Yritä myöhemmin uudelleen.")
    if not verify_password(password, user["pw_hash"]):
        maxf = int(settings.get("security.max_failed_logins"))
        failed = user["failed_count"] + 1
        locked = None
        if failed >= maxf:
            locked = iso(now + dt.timedelta(minutes=int(settings.get("security.lockout_minutes"))))
            failed = 0
        db.ex("UPDATE users SET failed_count = ?, locked_until = ? WHERE id = ?", (failed, locked, user["id"]))
        audit("kirjautuminen_epaonnistui", "user", user["id"], "lukittu" if locked else "", ip=ip, user=user)
        raise LoginError("Väärä tunnus tai salasana.")
    db.ex("UPDATE users SET failed_count = 0, locked_until = NULL WHERE id = ?", (user["id"],))
    # Tiivisteen päivitys, jos kierrosmäärä on noussut
    if f"${PBKDF2_ITERATIONS}$" not in user["pw_hash"]:
        db.ex("UPDATE users SET pw_hash = ? WHERE id = ?", (hash_password(password), user["id"]))
    audit("kirjautuminen", "user", user["id"], "", ip=ip, user=user)
    return user


def set_password(user_id: int, password: str, must_change: bool = False) -> None:
    db.ex("UPDATE users SET pw_hash = ?, must_change_pw = ?, pw_changed_at = ?, failed_count = 0, "
          "locked_until = NULL WHERE id = ?",
          (hash_password(password), 1 if must_change else 0, iso(utcnow()), user_id))
    # Salasanan vaihto mitätöi muut istunnot
    db.ex("DELETE FROM sessions WHERE user_id = ?", (user_id,))


def create_user(email: str, name: str, role: str, password: str, employee_id=None,
                must_change: bool = True) -> int:
    if role not in ROLES:
        raise ValueError("Tuntematon rooli.")
    email = email.strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise ValueError("Virheellinen sähköpostiosoite.")
    probs = password_problems(password, email, name)
    if probs:
        raise ValueError(" ".join(probs))
    cur = db.ex("INSERT INTO users(email, name, role, pw_hash, must_change_pw, employee_id, pw_changed_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (email, name.strip(), role, hash_password(password), 1 if must_change else 0,
                 employee_id, iso(utcnow())))
    return cur.lastrowid


# ---------------------------------------------------------------- istunnot

def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(user_id: int, ip: str = "") -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    now = utcnow()
    hours = int(settings.get("security.session_hours"))
    db.ex("DELETE FROM sessions WHERE expires_at < ?", (iso(now),))
    db.ex("INSERT INTO sessions(token_hash, user_id, csrf, created_at, expires_at, last_seen, ip) "
          "VALUES(?,?,?,?,?,?,?)",
          (_token_hash(token), user_id, csrf, iso(now), iso(now + dt.timedelta(hours=hours)), iso(now), ip))
    return token, csrf


def load_session(token: str):
    if not token or len(token) > 100:
        return None
    row = db.q1("SELECT s.*, u.email, u.name, u.role, u.active, u.must_change_pw, u.employee_id "
                "FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token_hash = ?",
                (_token_hash(token),))
    if row is None:
        return None
    now = utcnow()
    if parse_iso(row["expires_at"]) < now or not row["active"]:
        db.ex("DELETE FROM sessions WHERE token_hash = ?", (row["token_hash"],))
        return None
    # Tyhjäkäyntiaikakatkaisu 2 h
    if parse_iso(row["last_seen"]) < now - dt.timedelta(hours=2):
        db.ex("DELETE FROM sessions WHERE token_hash = ?", (row["token_hash"],))
        return None
    db.ex("UPDATE sessions SET last_seen = ? WHERE token_hash = ?", (iso(now), row["token_hash"]))
    return row


def destroy_session(token: str) -> None:
    db.ex("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))


def csrf_ok(session, supplied: str) -> bool:
    return bool(session) and bool(supplied) and hmac.compare_digest(session["csrf"], supplied)


# ---------------------------------------------------------------- API-tunnisteet

def create_api_token(name: str, scope: str, user_id: int) -> str:
    token = "hkt_" + secrets.token_urlsafe(32)
    db.ex("INSERT INTO api_tokens(name, token_hash, scope, created_by) VALUES(?,?,?,?)",
          (name, _token_hash(token), scope, user_id))
    return token


def check_api_token(token: str, scope: str):
    if not token or not token.startswith("hkt_") or len(token) > 100:
        return None
    row = db.q1("SELECT * FROM api_tokens WHERE token_hash = ? AND active = 1", (_token_hash(token),))
    if row is None or scope not in row["scope"].split(","):
        return None
    db.ex("UPDATE api_tokens SET last_used = ? WHERE id = ?", (iso(utcnow()), row["id"]))
    return row


# ---------------------------------------------------------------- muutosloki

def _mask_email(email: str) -> str:
    if "@" not in email:
        return "***"
    local, dom = email.split("@", 1)
    return (local[:2] + "***@" + dom) if local else "***@" + dom


def audit(action: str, entity: str = "", entity_id="", details: str = "", ip: str = "", user=None) -> None:
    with db.tx() as conn:
        prev = conn.execute("SELECT row_hash FROM audit ORDER BY id DESC LIMIT 1").fetchone()
        prev_hash = prev["row_hash"] if prev else "0" * 64
        ts = iso(utcnow())
        uid = user["id"] if user is not None and "id" in user.keys() else (
            user["user_id"] if user is not None and "user_id" in user.keys() else None)
        uemail = user["email"] if user is not None else ""
        payload = json.dumps([ts, uid, uemail, action, entity, str(entity_id), details, ip, prev_hash],
                             ensure_ascii=False)
        row_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        conn.execute("INSERT INTO audit(ts, user_id, user_email, action, entity, entity_id, details, ip, "
                     "prev_hash, row_hash) VALUES(?,?,?,?,?,?,?,?,?,?)",
                     (ts, uid, uemail, action, entity, str(entity_id), details[:2000], ip, prev_hash, row_hash))


def verify_audit_chain() -> tuple[bool, int, str]:
    prev_hash = "0" * 64
    n = 0
    for r in db.q("SELECT * FROM audit ORDER BY id"):
        payload = json.dumps([r["ts"], r["user_id"], r["user_email"], r["action"], r["entity"],
                              r["entity_id"], r["details"], r["ip"], prev_hash], ensure_ascii=False)
        if r["prev_hash"] != prev_hash or hashlib.sha256(payload.encode()).hexdigest() != r["row_hash"]:
            return False, n, f"Ketju katkeaa rivillä {r['id']}"
        prev_hash = r["row_hash"]
        n += 1
    return True, n, "Muutosloki eheä"


# ---------------------------------------------------------------- salaisuudet

class SecretStore:
    """Salaisuudet erillisessä tiedostossa, ei tietokannassa eikä varmuuskopioissa selkokielisenä."""

    def __init__(self, path: str):
        self.path = path

    def _load(self) -> dict:
        if not os.path.exists(self.path):
            return {}
        with open(self.path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _save(self, data: dict) -> None:
        tmp = self.path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, self.path)
        restrict_file_acl(self.path)

    def set(self, name: str, value: str) -> None:
        data = self._load()
        if value:
            data[name] = _protect(value.encode("utf-8"))
        else:
            data.pop(name, None)
        self._save(data)

    def get(self, name: str) -> str:
        blob = self._load().get(name)
        return _unprotect(blob).decode("utf-8") if blob else ""

    def has(self, name: str) -> bool:
        return name in self._load()


def restrict_file_acl(path: str) -> None:
    if sys.platform == "win32":
        import subprocess
        user = os.environ.get("USERNAME", "")
        dom = os.environ.get("USERDOMAIN", "")
        ident = f"{dom}\\{user}" if dom else user
        subprocess.run(["icacls", path, "/inheritance:r", "/grant:r", f"{ident}:F", "/grant:r", "*S-1-5-18:F"],
                       capture_output=True, check=False)
    else:
        os.chmod(path, 0o600)


if sys.platform == "win32":  # pragma: no cover - testataan Windowsissa
    import ctypes
    from ctypes import wintypes

    class _BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def _to_blob(data: bytes) -> _BLOB:
        buf = ctypes.create_string_buffer(data, len(data))
        return _BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))

    def _from_blob(b: _BLOB) -> bytes:
        out = ctypes.string_at(b.pbData, b.cbData)
        ctypes.windll.kernel32.LocalFree(b.pbData)
        return out

    def _protect(data: bytes) -> str:
        out = _BLOB()
        if not ctypes.windll.crypt32.CryptProtectData(ctypes.byref(_to_blob(data)), "HuoltokelloTalous",
                                                      None, None, None, 0x01, ctypes.byref(out)):
            raise OSError("DPAPI-salaus epäonnistui")
        return "dpapi:" + base64.b64encode(_from_blob(out)).decode()

    def _unprotect(s: str) -> bytes:
        if not s.startswith("dpapi:"):
            return base64.b64decode(s.split(":", 1)[1])
        out = _BLOB()
        if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(_to_blob(base64.b64decode(s[6:]))), None,
                                                        None, None, None, 0x01, ctypes.byref(out)):
            raise OSError("DPAPI-purku epäonnistui (eri käyttäjä tai kone?)")
        return _from_blob(out)
else:
    def _protect(data: bytes) -> str:
        # Ei käyttöjärjestelmän avainsäilöä: suojaus perustuu tiedosto-oikeuksiin (0600).
        return "file:" + base64.b64encode(data).decode()

    def _unprotect(s: str) -> bytes:
        return base64.b64decode(s.split(":", 1)[1])
