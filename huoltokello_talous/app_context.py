"""Sovelluksen ajonaikainen konteksti: datakansio, tietokanta ja salaisuusvarasto."""
from __future__ import annotations

import os
import sys

from . import db, ledger
from .security import SecretStore, restrict_file_acl

_DATA_DIR: str | None = None
_SECRETS: SecretStore | None = None


def default_data_dir() -> str:
    env = os.environ.get("HUOLTOKELLO_TALOUS_DATA")
    if env:
        return env
    if sys.platform == "win32":
        return os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "Huoltokello-Talous", "Data")
    return os.path.join(os.path.expanduser("~"), ".local", "share", "huoltokello-talous")


def init(data_dir: str | None = None) -> str:
    """Alustaa datakansion, tietokannan (migraatiot) ja oletustilikartan."""
    global _DATA_DIR, _SECRETS
    _DATA_DIR = os.path.abspath(data_dir or default_data_dir())
    os.makedirs(_DATA_DIR, exist_ok=True)
    for sub in ("varmuuskopiot", "viennit", "lokit"):
        os.makedirs(os.path.join(_DATA_DIR, sub), exist_ok=True)
    db_file = os.path.join(_DATA_DIR, "talous.db")
    new = not os.path.exists(db_file)
    db.configure(db_file)
    db.migrate()
    ledger.ensure_accounts()
    if new:
        restrict_file_acl(db_file)
    _SECRETS = SecretStore(os.path.join(_DATA_DIR, "salaisuudet.json"))
    return _DATA_DIR


def data_dir() -> str:
    if _DATA_DIR is None:
        raise RuntimeError("Sovellusta ei ole alustettu (app_context.init).")
    return _DATA_DIR


def secret_store() -> SecretStore:
    if _SECRETS is None:
        raise RuntimeError("Sovellusta ei ole alustettu (app_context.init).")
    return _SECRETS
