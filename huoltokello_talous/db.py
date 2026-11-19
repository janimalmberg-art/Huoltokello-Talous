"""SQLite-tietokanta: skeema, migraatiot ja yhteydet.

- WAL-tila (samanaikainen luku), foreign_keys päällä, busy_timeout
- jokainen säie saa oman yhteyden (sqlite3-yhteydet eivät ole säieturvallisia)
- migraatiot ovat idempotentteja ja versioituja (PRAGMA user_version)
- rahamäärät tallennetaan TEXT-muotoisina Decimal-arvoina (ei liukulukuja)
"""
from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from decimal import Decimal

sqlite3.register_adapter(Decimal, lambda d: str(d))

_local = threading.local()
_DB_PATH: str | None = None

SCHEMA_V1 = r"""
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS employees (
    id              INTEGER PRIMARY KEY,
    number          TEXT NOT NULL UNIQUE,
    name            TEXT NOT NULL,
    email           TEXT,
    pay_type        TEXT NOT NULL DEFAULT 'tunti' CHECK (pay_type IN ('tunti','kk')),
    hourly_wage     TEXT NOT NULL DEFAULT '0',
    monthly_salary  TEXT NOT NULL DEFAULT '0',
    weekly_hours    TEXT NOT NULL DEFAULT '40',
    cost_center     TEXT NOT NULL DEFAULT '',
    tes             TEXT NOT NULL DEFAULT '',
    active          INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS users (
    id               INTEGER PRIMARY KEY,
    email            TEXT NOT NULL UNIQUE COLLATE NOCASE,
    name             TEXT NOT NULL,
    role             TEXT NOT NULL CHECK (role IN ('johto','tyonjohto','tyontekija','kirjanpitaja')),
    pw_hash          TEXT NOT NULL,
    active           INTEGER NOT NULL DEFAULT 1,
    must_change_pw   INTEGER NOT NULL DEFAULT 0,
    failed_count     INTEGER NOT NULL DEFAULT 0,
    locked_until     TEXT,
    employee_id      INTEGER REFERENCES employees(id) ON DELETE SET NULL,
    created_at       TEXT NOT NULL DEFAULT (datetime('now')),
    pw_changed_at    TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash  TEXT PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    csrf        TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL,
    last_seen   TEXT NOT NULL,
    ip          TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS api_tokens (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    token_hash  TEXT NOT NULL UNIQUE,
    scope       TEXT NOT NULL DEFAULT 'tyoajat:write',
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    created_by  INTEGER REFERENCES users(id),
    last_used   TEXT,
    active      INTEGER NOT NULL DEFAULT 1
);

-- Muutosloki ketjutetulla tiivisteellä (muutokset jälkikäteen havaittavissa)
CREATE TABLE IF NOT EXISTS audit (
    id         INTEGER PRIMARY KEY,
    ts         TEXT NOT NULL,
    user_id    INTEGER,
    user_email TEXT NOT NULL DEFAULT '',
    action     TEXT NOT NULL,
    entity     TEXT NOT NULL DEFAULT '',
    entity_id  TEXT NOT NULL DEFAULT '',
    details    TEXT NOT NULL DEFAULT '',
    ip         TEXT NOT NULL DEFAULT '',
    prev_hash  TEXT NOT NULL,
    row_hash   TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit
BEGIN SELECT RAISE(ABORT, 'Muutoslokia ei voi muokata'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit
BEGIN SELECT RAISE(ABORT, 'Muutoslokista ei voi poistaa'); END;

CREATE TABLE IF NOT EXISTS customers (
    id                 INTEGER PRIMARY KEY,
    number             TEXT NOT NULL UNIQUE,
    name               TEXT NOT NULL,
    business_id        TEXT NOT NULL DEFAULT '',
    is_consumer        INTEGER NOT NULL DEFAULT 0,
    street             TEXT NOT NULL DEFAULT '',
    postcode           TEXT NOT NULL DEFAULT '',
    city               TEXT NOT NULL DEFAULT '',
    country            TEXT NOT NULL DEFAULT 'FI',
    email              TEXT NOT NULL DEFAULT '',
    phone              TEXT NOT NULL DEFAULT '',
    einvoice_address   TEXT NOT NULL DEFAULT '',
    einvoice_operator  TEXT NOT NULL DEFAULT '',
    payment_term_days  INTEGER NOT NULL DEFAULT 14,
    reverse_charge     INTEGER NOT NULL DEFAULT 0,
    contact_person     TEXT NOT NULL DEFAULT '',
    notes              TEXT NOT NULL DEFAULT '',
    active             INTEGER NOT NULL DEFAULT 1,
    created_at         TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS work_types (
    id                   INTEGER PRIMARY KEY,
    code                 TEXT NOT NULL UNIQUE,
    name                 TEXT NOT NULL,
    unit                 TEXT NOT NULL DEFAULT 'h',
    price                TEXT NOT NULL DEFAULT '0',
    min_billing_min      INTEGER NOT NULL DEFAULT 0,
    rounding_min         INTEGER NOT NULL DEFAULT 15,
    vat_rate             TEXT NOT NULL DEFAULT '25.5',
    revenue_account      TEXT NOT NULL DEFAULT '3000',
    construction_service INTEGER NOT NULL DEFAULT 0,
    household_deductible INTEGER NOT NULL DEFAULT 1,
    billable             INTEGER NOT NULL DEFAULT 1,
    active               INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS customer_prices (
    customer_id  INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    work_type_id INTEGER NOT NULL REFERENCES work_types(id) ON DELETE CASCADE,
    price        TEXT NOT NULL,
    PRIMARY KEY (customer_id, work_type_id)
);

CREATE TABLE IF NOT EXISTS materials (
    id              INTEGER PRIMARY KEY,
    code            TEXT NOT NULL UNIQUE,
    name            TEXT NOT NULL,
    unit            TEXT NOT NULL DEFAULT 'kpl',
    purchase_price  TEXT NOT NULL DEFAULT '0',
    sale_price      TEXT NOT NULL DEFAULT '0',
    vat_rate        TEXT NOT NULL DEFAULT '25.5',
    revenue_account TEXT NOT NULL DEFAULT '3100',
    active          INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS time_entries (
    id            INTEGER PRIMARY KEY,
    employee_id   INTEGER NOT NULL REFERENCES employees(id),
    work_date     TEXT NOT NULL,
    start_time    TEXT NOT NULL,
    end_time      TEXT NOT NULL,
    break_min     INTEGER NOT NULL DEFAULT 0,
    minutes       INTEGER NOT NULL,
    work_type_id  INTEGER NOT NULL REFERENCES work_types(id),
    customer_id   INTEGER REFERENCES customers(id),
    site          TEXT NOT NULL DEFAULT '',
    task_ref      TEXT NOT NULL DEFAULT '',
    description   TEXT NOT NULL DEFAULT '',
    billable      INTEGER NOT NULL DEFAULT 1,
    km            TEXT NOT NULL DEFAULT '0',
    allowance     TEXT NOT NULL DEFAULT '' CHECK (allowance IN ('','osa','koko')),
    status        TEXT NOT NULL DEFAULT 'luonnos'
                  CHECK (status IN ('luonnos','lahetetty','hyvaksytty','hylatty','laskutettu')),
    reject_reason TEXT NOT NULL DEFAULT '',
    approved_by   INTEGER REFERENCES users(id),
    approved_at   TEXT,
    invoice_id    INTEGER REFERENCES invoices(id),
    payroll_run_id INTEGER REFERENCES payroll_runs(id),
    source        TEXT NOT NULL DEFAULT 'kasin',
    created_by    INTEGER REFERENCES users(id),
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_te_emp_date ON time_entries(employee_id, work_date);
CREATE INDEX IF NOT EXISTS ix_te_status ON time_entries(status);
CREATE INDEX IF NOT EXISTS ix_te_customer ON time_entries(customer_id, status);

CREATE TABLE IF NOT EXISTS open_clocks (
    employee_id  INTEGER PRIMARY KEY REFERENCES employees(id),
    started_at   TEXT NOT NULL,
    work_type_id INTEGER NOT NULL REFERENCES work_types(id),
    customer_id  INTEGER REFERENCES customers(id),
    site         TEXT NOT NULL DEFAULT '',
    task_ref     TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS material_entries (
    id           INTEGER PRIMARY KEY,
    customer_id  INTEGER NOT NULL REFERENCES customers(id),
    material_id  INTEGER NOT NULL REFERENCES materials(id),
    employee_id  INTEGER REFERENCES employees(id),
    work_date    TEXT NOT NULL,
    qty          TEXT NOT NULL,
    site         TEXT NOT NULL DEFAULT '',
    description  TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'hyvaksytty' CHECK (status IN ('hyvaksytty','laskutettu')),
    invoice_id   INTEGER REFERENCES invoices(id),
    created_by   INTEGER REFERENCES users(id),
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS invoices (
    id                  INTEGER PRIMARY KEY,
    number              INTEGER UNIQUE,
    kind                TEXT NOT NULL DEFAULT 'lasku' CHECK (kind IN ('lasku','hyvitys')),
    credited_invoice_id INTEGER REFERENCES invoices(id),
    customer_id         INTEGER NOT NULL REFERENCES customers(id),
    customer_snapshot   TEXT NOT NULL DEFAULT '{}',
    invoice_date        TEXT,
    due_date            TEXT,
    delivery_start      TEXT,
    delivery_end        TEXT,
    reference           TEXT NOT NULL DEFAULT '',
    rf_reference        TEXT NOT NULL DEFAULT '',
    our_reference       TEXT NOT NULL DEFAULT '',
    your_reference      TEXT NOT NULL DEFAULT '',
    notes               TEXT NOT NULL DEFAULT '',
    reverse_charge      INTEGER NOT NULL DEFAULT 0,
    total_net           TEXT NOT NULL DEFAULT '0',
    total_vat           TEXT NOT NULL DEFAULT '0',
    total_gross         TEXT NOT NULL DEFAULT '0',
    household_work      TEXT NOT NULL DEFAULT '0',
    paid_amount         TEXT NOT NULL DEFAULT '0',
    status              TEXT NOT NULL DEFAULT 'luonnos'
                        CHECK (status IN ('luonnos','avoin','osittain','maksettu','hyvitetty','mitatoity')),
    journal_id          INTEGER REFERENCES journals(id),
    reminder_count      INTEGER NOT NULL DEFAULT 0,
    created_by          INTEGER REFERENCES users(id),
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    finalized_by        INTEGER REFERENCES users(id),
    finalized_at        TEXT
);
-- Vahvistettu lasku on muuttumaton (KPL 2:5 ja 2:7): korjaus vain hyvityslaskulla.
CREATE TRIGGER IF NOT EXISTS invoice_immutable BEFORE UPDATE OF
    number, kind, customer_id, customer_snapshot, invoice_date, due_date, reference,
    total_net, total_vat, total_gross, reverse_charge ON invoices
WHEN OLD.status <> 'luonnos'
BEGIN SELECT RAISE(ABORT, 'Vahvistettua laskua ei voi muuttaa - tee hyvityslasku'); END;
CREATE TRIGGER IF NOT EXISTS invoice_no_delete BEFORE DELETE ON invoices
WHEN OLD.status <> 'luonnos'
BEGIN SELECT RAISE(ABORT, 'Vahvistettua laskua ei voi poistaa'); END;

CREATE TABLE IF NOT EXISTS invoice_lines (
    id              INTEGER PRIMARY KEY,
    invoice_id      INTEGER NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    pos             INTEGER NOT NULL,
    description     TEXT NOT NULL,
    line_date       TEXT,
    qty             TEXT NOT NULL,
    unit            TEXT NOT NULL DEFAULT 'kpl',
    unit_price      TEXT NOT NULL,
    discount_pct    TEXT NOT NULL DEFAULT '0',
    vat_rate        TEXT NOT NULL,
    vat_code        TEXT NOT NULL,
    net             TEXT NOT NULL,
    vat             TEXT NOT NULL,
    gross           TEXT NOT NULL,
    account         TEXT NOT NULL,
    work_type_id    INTEGER REFERENCES work_types(id),
    household_work  INTEGER NOT NULL DEFAULT 0,
    source_ref      TEXT NOT NULL DEFAULT ''
);
CREATE TRIGGER IF NOT EXISTS invoice_lines_immutable BEFORE UPDATE ON invoice_lines
WHEN (SELECT status FROM invoices WHERE id = OLD.invoice_id) <> 'luonnos'
BEGIN SELECT RAISE(ABORT, 'Vahvistetun laskun rivejä ei voi muuttaa'); END;
CREATE TRIGGER IF NOT EXISTS invoice_lines_nodelete BEFORE DELETE ON invoice_lines
WHEN (SELECT status FROM invoices WHERE id = OLD.invoice_id) NOT IN ('luonnos')
BEGIN SELECT RAISE(ABORT, 'Vahvistetun laskun rivejä ei voi poistaa'); END;

CREATE TABLE IF NOT EXISTS payments (
    id          INTEGER PRIMARY KEY,
    invoice_id  INTEGER NOT NULL REFERENCES invoices(id),
    pay_date    TEXT NOT NULL,
    amount      TEXT NOT NULL,
    method      TEXT NOT NULL DEFAULT 'pankki',
    archive_ref TEXT NOT NULL DEFAULT '',
    journal_id  INTEGER REFERENCES journals(id),
    created_by  INTEGER REFERENCES users(id),
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS accounts (
    number   TEXT PRIMARY KEY,
    name     TEXT NOT NULL,
    kind     TEXT NOT NULL CHECK (kind IN ('vastaavaa','vastattavaa','tulo','meno')),
    active   INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS journals (
    id           INTEGER PRIMARY KEY,
    series       TEXT NOT NULL,
    number       INTEGER NOT NULL,
    entry_date   TEXT NOT NULL,
    description  TEXT NOT NULL,
    source_type  TEXT NOT NULL DEFAULT '',
    source_id    INTEGER,
    created_by   INTEGER REFERENCES users(id),
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (series, number)
);
CREATE TABLE IF NOT EXISTS journal_lines (
    id          INTEGER PRIMARY KEY,
    journal_id  INTEGER NOT NULL REFERENCES journals(id),
    account     TEXT NOT NULL REFERENCES accounts(number),
    debit       TEXT NOT NULL DEFAULT '0',
    credit      TEXT NOT NULL DEFAULT '0',
    vat_code    TEXT NOT NULL DEFAULT '',
    vat_rate    TEXT NOT NULL DEFAULT '',
    vat_base    TEXT NOT NULL DEFAULT '0',
    cost_center TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_jl_account ON journal_lines(account);
CREATE INDEX IF NOT EXISTS ix_journal_date ON journals(entry_date);
-- Kirjauksia ei muuteta eikä poisteta: virheet korjataan oikaisukirjauksella.
CREATE TRIGGER IF NOT EXISTS journal_no_update BEFORE UPDATE ON journals
BEGIN SELECT RAISE(ABORT, 'Kirjausta ei voi muuttaa - tee oikaisukirjaus'); END;
CREATE TRIGGER IF NOT EXISTS journal_no_delete BEFORE DELETE ON journals
BEGIN SELECT RAISE(ABORT, 'Kirjausta ei voi poistaa - tee oikaisukirjaus'); END;
CREATE TRIGGER IF NOT EXISTS jl_no_update BEFORE UPDATE ON journal_lines
BEGIN SELECT RAISE(ABORT, 'Kirjausriviä ei voi muuttaa'); END;
CREATE TRIGGER IF NOT EXISTS jl_no_delete BEFORE DELETE ON journal_lines
BEGIN SELECT RAISE(ABORT, 'Kirjausriviä ei voi poistaa'); END;

CREATE TABLE IF NOT EXISTS locked_periods (
    period     TEXT PRIMARY KEY,           -- 'YYYY-MM'
    locked_by  INTEGER REFERENCES users(id),
    locked_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS payroll_runs (
    id            INTEGER PRIMARY KEY,
    period_start  TEXT NOT NULL,
    period_end    TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'luonnos' CHECK (status IN ('luonnos','lukittu')),
    data          TEXT NOT NULL,
    created_by    INTEGER REFERENCES users(id),
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    locked_at     TEXT
);

CREATE TABLE IF NOT EXISTS quotes (
    id           INTEGER PRIMARY KEY,
    number       INTEGER NOT NULL UNIQUE,
    customer_id  INTEGER REFERENCES customers(id),
    customer_name TEXT NOT NULL DEFAULT '',
    quote_date   TEXT NOT NULL,
    valid_until  TEXT NOT NULL,
    title        TEXT NOT NULL DEFAULT '',
    lines        TEXT NOT NULL,
    total_net    TEXT NOT NULL,
    total_vat    TEXT NOT NULL,
    total_gross  TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'luonnos' CHECK (status IN ('luonnos','lahetetty','hyvaksytty','hylatty')),
    created_by   INTEGER REFERENCES users(id),
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS exports (
    id              INTEGER PRIMARY KEY,
    ts              TEXT NOT NULL,
    kind            TEXT NOT NULL,
    period          TEXT NOT NULL DEFAULT '',
    target          TEXT NOT NULL DEFAULT '',
    filename        TEXT NOT NULL DEFAULT '',
    sha256          TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL CHECK (status IN ('ok','virhe','odottaa')),
    message         TEXT NOT NULL DEFAULT '',
    attempts        INTEGER NOT NULL DEFAULT 1,
    idempotency_key TEXT NOT NULL DEFAULT '',
    user_id         INTEGER REFERENCES users(id)
);
"""

SCHEMA_V2 = r"""
-- Huoltokello-silta: Huoltokellon tunnisteiden kytkentä Talouden riveihin
CREATE TABLE IF NOT EXISTS hk_map (
    kind      TEXT NOT NULL,           -- customer | user | category | confirmation | material
    hk_id     TEXT NOT NULL,
    local_id  INTEGER NOT NULL,
    synced_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (kind, hk_id)
);
CREATE TABLE IF NOT EXISTS hk_sync_log (
    id       INTEGER PRIMARY KEY,
    ts       TEXT NOT NULL,
    mode     TEXT NOT NULL,           -- kasin | automaattinen | komentorivi
    ok       INTEGER NOT NULL,
    summary  TEXT NOT NULL,
    user_id  INTEGER REFERENCES users(id)
);
"""

MIGRATIONS = [SCHEMA_V1, SCHEMA_V2]


def configure(path: str) -> None:
    global _DB_PATH
    _DB_PATH = os.path.abspath(path)
    os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)
    close()


def db_path() -> str:
    if not _DB_PATH:
        raise RuntimeError("Tietokantaa ei ole määritetty (db.configure).")
    return _DB_PATH


def connect() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is not None and getattr(_local, "path", None) == _DB_PATH:
        return conn
    conn = sqlite3.connect(db_path(), timeout=30, isolation_level=None,
                           detect_types=0, check_same_thread=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = FULL")
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA secure_delete = ON")
    _local.conn = conn
    _local.path = _DB_PATH
    return conn


def close() -> None:
    conn = getattr(_local, "conn", None)
    if conn is not None:
        conn.close()
        _local.conn = None


@contextmanager
def tx():
    """Kirjoitustransaktio (BEGIN IMMEDIATE): joko kaikki tai ei mitään."""
    conn = connect()
    if conn.in_transaction:
        yield conn
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def q(sql: str, params=()) -> list[sqlite3.Row]:
    return connect().execute(sql, params).fetchall()


def q1(sql: str, params=()):
    return connect().execute(sql, params).fetchone()


def ex(sql: str, params=()) -> sqlite3.Cursor:
    return connect().execute(sql, params)


def migrate() -> int:
    conn = connect()
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    for version, script in enumerate(MIGRATIONS, start=1):
        if version > current:
            conn.executescript("BEGIN;" + script + f"; PRAGMA user_version = {version}; COMMIT;")
    return conn.execute("PRAGMA user_version").fetchone()[0]


def backup_to(dest_path: str) -> str:
    """Johdonmukainen varmuuskopio käynnissä olevasta kannasta (SQLite online backup API)."""
    src = connect()
    os.makedirs(os.path.dirname(os.path.abspath(dest_path)), exist_ok=True)
    dst = sqlite3.connect(dest_path)
    try:
        src.backup(dst)
        ok = dst.execute("PRAGMA integrity_check").fetchone()[0]
        if ok != "ok":
            raise RuntimeError(f"Varmuuskopion eheystarkistus epäonnistui: {ok}")
    finally:
        dst.close()
    return dest_path


def integrity_check() -> str:
    return connect().execute("PRAGMA integrity_check").fetchone()[0]
