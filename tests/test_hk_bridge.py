"""Huoltokello-sillan testit Huoltokellon todellisella taulurakenteella (v2.7.3)."""
import datetime as dt
import os
import shutil
import sqlite3
import tempfile
import unittest
from decimal import Decimal

from huoltokello_talous import app_context, db, hk_bridge, security, settings

HK_SCHEMA = """
CREATE TABLE AspNetUsers (Id TEXT PRIMARY KEY, FullName TEXT, Title TEXT, WorkerGroupId INTEGER, IsActive INTEGER,
  UserName TEXT, Email TEXT, PasswordHash TEXT);
CREATE TABLE Customers (Id INTEGER PRIMARY KEY, Name TEXT, ContactPerson TEXT, Email TEXT, Phone TEXT, Address TEXT,
  Notes TEXT);
CREATE TABLE Sites (Id INTEGER PRIMARY KEY, CustomerId INTEGER, Name TEXT, Address TEXT, Description TEXT, AreaM2 REAL);
CREATE TABLE TaskCategories (Id INTEGER PRIMARY KEY, Name TEXT, Color TEXT, Description TEXT, DefaultRequiredInfo TEXT);
CREATE TABLE WorkTasks (Id INTEGER PRIMARY KEY, Title TEXT, Description TEXT, CategoryId INTEGER, SiteId INTEGER,
  ScheduledStart TEXT, ScheduledEnd TEXT, Priority INTEGER, Status INTEGER, Instructions TEXT, RequiredInfoLabels TEXT,
  EstimatedHours REAL, AssignedGroupId INTEGER, AssignedUserId TEXT, CreatedAt TEXT, CreatedById TEXT, InvoiceId INTEGER);
CREATE TABLE TaskConfirmations (Id INTEGER PRIMARY KEY, WorkTaskId INTEGER, UserId TEXT, ConfirmedAt TEXT,
  Result INTEGER, Note TEXT, CollectedInfo TEXT, DeficiencyDescription TEXT, DeficiencyResolved INTEGER,
  DeficiencyResolvedAt TEXT, DeficiencyResolveNote TEXT, HoursWorked TEXT);
CREATE TABLE ConfirmationMaterials (Id INTEGER PRIMARY KEY, ConfirmationId INTEGER, Name TEXT, Quantity TEXT,
  Unit TEXT, UnitPrice TEXT, IsPassThrough INTEGER);
CREATE TABLE Contracts (Id INTEGER PRIMARY KEY, CustomerId INTEGER, Name TEXT, Description TEXT, StartDate TEXT,
  EndDate TEXT, MonthlyPrice TEXT, PricingType INTEGER, HourlyRate TEXT, MarginPercent TEXT, VatRate TEXT,
  BillingInterval INTEGER, PaymentTerms INTEGER, ContractNumber TEXT, ContactPerson TEXT, NotifyDaysBefore INTEGER,
  LastReminderAt TEXT, CreatedAt TEXT);
CREATE TABLE AuditLogs (Id INTEGER PRIMARY KEY, UserId TEXT, Action TEXT, Details TEXT, Entity TEXT, EntityId TEXT,
  CreatedAt TEXT);
"""


class TestBridge(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hkb-")
        app_context.init(os.path.join(self.tmp, "talous"))
        self.uid = security.create_user("johto@firma.fi", "Johto", "johto", "Kevät#Myrsky2026-x", must_change=False)
        db.ex("INSERT INTO employees(number, name, email, hourly_wage) VALUES('1001', 'Teppo', 'teppo@firma.fi', '18')")
        self.hk = os.path.join(self.tmp, "huoltokello.db")
        c = sqlite3.connect(self.hk)
        c.executescript(HK_SCHEMA)
        self.day = dt.date.today() - dt.timedelta(days=1)
        d = self.day.isoformat()
        c.executemany("INSERT INTO AspNetUsers(Id, FullName, Email, IsActive) VALUES(?,?,?,1)",
                      [("u-teppo", "Teppo Virtanen", "Teppo@Firma.fi"), ("u-sanna", "Sanna Laine", "sanna@firma.fi")])
        c.executemany("INSERT INTO Customers(Id, Name, ContactPerson, Email, Address) VALUES(?,?,?,?,?)",
                      [(1, "As Oy Koivu", "Isännöitsijä", "is@koivu.fi", "Koivukuja 3, 33200 Tampere"),
                       (2, "As Oy Helmi", "", "", "Pyynikintie 12 33230 Tampere")])
        c.executemany("INSERT INTO Sites(Id, CustomerId, Name, Address) VALUES(?,?,?,?)",
                      [(10, 1, "Koivu porras A", "Koivukuja 3"), (20, 2, "Helmi", "Pyynikintie 12")])
        c.executemany("INSERT INTO TaskCategories(Id, Name) VALUES(?,?)", [(1, "Kiinteistönhoito"), (2, "Vikakorjaus")])
        c.executemany("INSERT INTO Contracts(Id, CustomerId, Name, StartDate, EndDate, MonthlyPrice, HourlyRate) "
                      "VALUES(?,?,?,?,?,?,?)",
                      [(1, 1, "Tuntisopimus", "2026-01-01 00:00:00", None, "0", "55.00"),
                       (2, 2, "Kuukausihuolto", "2026-01-01 00:00:00", None, "890.00", "0")])
        c.executemany("INSERT INTO WorkTasks(Id, Title, CategoryId, SiteId, InvoiceId) VALUES(?,?,?,?,?)",
                      [(100, "Vuosihuolto", 1, 10, None), (101, "Hanan vaihto", 2, 10, None),
                       (102, "Porrassiivous", 1, 20, None), (103, "Jo laskutettu työ", 2, 10, 7)])
        c.executemany("INSERT INTO TaskConfirmations(Id, WorkTaskId, UserId, ConfirmedAt, Note, HoursWorked) "
                      "VALUES(?,?,?,?,?,?)",
                      [(1, 100, "u-teppo", f"{d} 12:00:00.1234567", "Tehty", "4.5"),
                       (2, 101, "u-teppo", f"{d} 13:00:00", "", "2"),          # ketjutetaan ennen klo 13 kuittausta
                       (3, 102, "u-sanna", f"{d} 15:30:00", "", "3"),
                       (4, 103, "u-sanna", f"{d} 10:00:00", "", "1.5"),
                       (5, 100, "u-sanna", f"{d} 16:00:00", "ei tunteja", None)])
        c.execute("INSERT INTO ConfirmationMaterials(Id, ConfirmationId, Name, Quantity, Unit, UnitPrice, IsPassThrough) "
                  "VALUES(1, 1, 'LED-lamppu E27', '3', 'kpl', '12.90', 0)")
        c.execute("INSERT INTO ConfirmationMaterials(Id, ConfirmationId, Name, Quantity, Unit, UnitPrice, IsPassThrough) "
                  "VALUES(2, 4, 'Hana', '1', 'kpl', '89.00', 1)")
        c.commit()
        c.close()
        settings.set("hk.db_path", self.hk)
        settings.set("hk.timezone", "paikallinen")
        settings.set("hk.start_date", (self.day - dt.timedelta(days=3)).isoformat())

    def tearDown(self):
        db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_read_only(self):
        conn = hk_bridge.open_hk(self.hk)
        with self.assertRaises(sqlite3.OperationalError):
            conn.execute("DELETE FROM Customers")
        conn.close()

    def test_plan_and_apply(self):
        conn = hk_bridge.open_hk(self.hk)
        plan = hk_bridge.build_plan(conn)
        conn.close()
        self.assertEqual(len(plan.customers_new), 2)
        self.assertEqual(plan.customers_new[0]["postcode"], "33200")
        emails = {u["email"]: u.get("match_id") for u in plan.employees_new}
        self.assertEqual(emails["teppo@firma.fi"], 1)        # täsmäys sähköpostilla (kirjainkoko ei haittaa)
        self.assertIsNone(emails["sanna@firma.fi"])
        res = hk_bridge.apply_plan(plan, self.uid)
        self.assertEqual(res["tyoajat"], 4)
        rows = {r["task_ref"]: r for r in db.q("SELECT * FROM time_entries")}
        # klo 13 kuittaus 2 h -> 11:00–13:00; klo 12 kuittaus 4,5 h ketjutetaan alkamaan ennen 11:00
        self.assertEqual((rows["HK-101"]["start_time"], rows["HK-101"]["end_time"]), ("11:00", "13:00"))
        self.assertEqual((rows["HK-100"]["start_time"], rows["HK-100"]["end_time"]), ("06:30", "11:00"))
        self.assertEqual(rows["HK-100"]["status"], "lahetetty")
        self.assertEqual(rows["HK-100"]["billable"], 1)
        self.assertEqual(rows["HK-102"]["billable"], 0)      # kuukausisopimus
        self.assertEqual(rows["HK-103"]["billable"], 0)      # laskutettu jo Huoltokellossa
        # sopimuksen tuntihinta asiakashinnaksi
        cust = db.q1("SELECT id FROM customers WHERE number = 'HK1'")["id"]
        self.assertEqual(Decimal(db.q1("SELECT price FROM customer_prices WHERE customer_id = ? LIMIT 1",
                                       (cust,))["price"]), Decimal("55.00"))
        # tarvike tuotiin, mutta Huoltokellossa laskutetun tehtävän tarviketta ei
        mats = db.q("SELECT m.*, x.name FROM material_entries m JOIN materials x ON x.id = m.material_id")
        self.assertEqual([m["name"] for m in mats], ["LED-lamppu E27"])
        # uusi työntekijä luotiin
        self.assertIsNotNone(db.q1("SELECT 1 FROM employees WHERE email = 'sanna@firma.fi'"))

    def test_idempotent_and_duplicate_guard(self):
        hk_bridge.sync("kasin", self.uid)
        again = hk_bridge.sync("kasin", self.uid)
        self.assertEqual(again["tuotu_tyoaikoja"], 0)
        self.assertEqual(again["tuotu_tarvikkeita"], 0)
        self.assertEqual(db.q1("SELECT COUNT(*) AS n FROM time_entries")["n"], 4)

    def test_existing_manual_entry_not_duplicated(self):
        db.ex("INSERT INTO work_types(code, name, price) VALUES('KH', 'Kiinteistönhoito', '52')")
        db.ex("INSERT INTO time_entries(employee_id, work_date, start_time, end_time, minutes, work_type_id, task_ref, "
              "status) VALUES(1, ?, '07:00', '11:30', 270, 1, 'HK-100', 'hyvaksytty')", (self.day.isoformat(),))
        res = hk_bridge.sync("kasin", self.uid)
        self.assertEqual(db.q1("SELECT COUNT(*) AS n FROM time_entries WHERE task_ref = 'HK-100'")["n"], 1)
        # kategoria kytkettiin samannimiseen olemassa olevaan työlajiin
        self.assertIsNone(db.q1("SELECT 1 FROM work_types WHERE code = 'HK1'"))
        self.assertTrue(any("päällekkäin" in s for s in res["ohitetut_syyt"]) or res["tuotu_tyoaikoja"] >= 2)

    def test_timestamp_parsing(self):
        self.assertEqual(hk_bridge.parse_hk_dt("2026-11-19 07:15:00.1234567"), dt.datetime(2026, 11, 19, 7, 15))
        t = hk_bridge.parse_hk_dt("2026-11-19T05:15:00Z")
        self.assertIsNotNone(t.tzinfo)
        self.assertEqual(hk_bridge.parse_hk_dt("2026-11-19T07:15:00+02:00").hour, 5)


if __name__ == "__main__":
    unittest.main()
