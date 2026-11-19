"""Huoltokello-Talous: yksikkö- ja integraatiotestit (python -m unittest discover tests)."""
from __future__ import annotations

import datetime as dt
import http.cookiejar
import io
import json
import os
import re
import shutil
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
import xml.dom.minidom
import zipfile
from decimal import Decimal

from huoltokello_talous import (app_context, db, finref, invoicing, ledger, money, payroll, pricing, security,
                                settings, timesheet, worktime)
from huoltokello_talous.exports import finvoice, package, profiles, transport

PW = "Kevät#Myrsky2026-x"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hkt-test-")
        app_context.init(self.tmp)
        for k, v in {"company.name": "Testi Huolto Oy", "company.business_id": "0112038-9",
                     "company.iban": "FI7944052020036082", "company.bic": "OKOYFIHH", "company.street": "Katu 1",
                     "company.postcode": "00100", "company.city": "Helsinki"}.items():
            settings.set(k, v)
        self.uid = security.create_user("johto@firma.fi", "Maija Johtaja", "johto", PW, must_change=False)
        db.ex("INSERT INTO employees(number, name, hourly_wage) VALUES('T1', 'Teppo', '20')")
        db.ex("INSERT INTO customers(number, name, business_id, street, postcode, city) "
              "VALUES('1001', 'As Oy Koivu', '0112038-9', 'Tie 2', '00200', 'Espoo')")
        db.ex("INSERT INTO customers(number, name, is_consumer) VALUES('1002', 'Kalle Kuluttaja', 1)")
        db.ex("INSERT INTO customers(number, name, business_id, reverse_charge) VALUES('1003', 'Rakennus Oy', "
              "'0112038-9', 1)")
        db.ex("INSERT INTO work_types(code, name, price) VALUES('KH', 'Kiinteistönhoito', '50')")
        db.ex("INSERT INTO work_types(code, name, price, construction_service) VALUES('RE', 'Korjaus', '60', 1)")
        self.today = dt.date.today()

    def tearDown(self):
        db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def entry(self, days_ago=1, start="08:00", end="16:30", brk=30, cust=1, wt=1, submit=True):
        d = (self.today - dt.timedelta(days=days_ago)).isoformat()
        return timesheet.create(1, {"work_date": d, "start_time": start, "end_time": end, "break_min": brk,
                                    "work_type_id": wt, "customer_id": cust, "billable": 1}, self.uid, submit=submit)


class TestFinref(unittest.TestCase):
    def test_viite(self):
        self.assertEqual(finref.luo_viite("1232"), "12328")
        self.assertTrue(finref.tarkista_viite("868516259619897"))
        self.assertFalse(finref.tarkista_viite("868516259619898"))

    def test_rf(self):
        rf = finref.luo_rf_viite("12345")
        self.assertTrue(finref.tarkista_rf(rf))
        self.assertTrue(finref.tarkista_rf("RF18539007547034"))  # ISO 11649 -esimerkki
        self.assertFalse(finref.tarkista_rf("RF19539007547034"))

    def test_iban_ytunnus(self):
        self.assertTrue(finref.tarkista_iban("FI79 4405 2020 0360 82"))
        self.assertFalse(finref.tarkista_iban("FI79 4405 2020 0360 83"))
        self.assertTrue(finref.tarkista_ytunnus("0112038-9"))
        self.assertFalse(finref.tarkista_ytunnus("0112038-8"))
        self.assertEqual(finref.ovt_tunnus("0112038-9"), "003701120389")

    def test_virtuaaliviivakoodi_finanssiala_esimerkki(self):
        code = finref.virtuaaliviivakoodi("FI79 4405 2020 0360 82", "4883.15", "86851 62596 19897",
                                          dt.date(2010, 6, 12))
        self.assertEqual(code, "479440520200360820048831500000000868516259619897100612")
        self.assertIn("<svg", finref.code128c_svg(code))


class TestMoney(unittest.TestCase):
    def test_vat(self):
        self.assertEqual(money.vat_amount("100", "25.5"), Decimal("25.50"))
        self.assertEqual(money.net_from_gross("125.50", "25.5"), Decimal("100.00"))
        self.assertEqual(money.r2("0.005"), Decimal("0.01"))
        self.assertEqual(money.D("1 234,50"), Decimal("1234.50"))
        with self.assertRaises(ValueError):
            money.validate_vat_rate("24")


class TestWorktime(unittest.TestCase):
    def test_holidays(self):
        h = worktime.finnish_holidays(2026)
        self.assertEqual(h[dt.date(2026, 4, 3)], "Pitkäperjantai")
        self.assertEqual(h[dt.date(2026, 6, 20)], "Juhannuspäivä")
        self.assertEqual(h[dt.date(2026, 10, 31)], "Pyhäinpäivä")
        self.assertEqual(worktime.easter_sunday(2027), dt.date(2027, 3, 28))

    def test_overnight_and_categories(self):
        self.assertEqual(worktime.entry_minutes("2026-11-16", "22:00", "06:00", 30), 450)
        c = worktime.classify_entry("2026-11-15", "10:00", "12:00", 0, settings.DEFAULTS)  # sunnuntai
        self.assertEqual(c.sunday, 120)
        with self.assertRaises(ValueError):
            worktime.entry_minutes("2026-11-16", "08:00", "09:00", 60)

    def test_overtime(self):
        emps = {1: dict(number="T1", name="A", pay_type="tunti", hourly_wage="20", monthly_salary="0",
                        weekly_hours="40")}
        entries = [dict(employee_id=1, work_date=f"2026-11-{d:02d}", start_time="07:00", end_time="19:00",
                        break_min=60, billable=1, km="0", allowance="") for d in range(16, 22)]  # ma–la 11 h
        s = worktime.summarize(entries, emps, dt.date(2026, 11, 1), dt.date(2026, 11, 30), settings.DEFAULTS)[1]
        self.assertEqual(s.h(s.minutes_total), Decimal("66.00"))
        self.assertEqual(s.h(s.ot50_daily), Decimal("12.00"))   # 6 pv × 2 h
        self.assertEqual(s.h(s.ot100_daily), Decimal("6.00"))   # 6 pv × 1 h
        self.assertEqual(s.h(s.ot50_weekly), Decimal("8.00"))   # 48 - 40
        self.assertEqual(s.h(s.minutes_regular), Decimal("40.00"))


class TestSecurity(Base):
    def test_password_policy(self):
        self.assertTrue(security.password_problems("lyhyt"))
        self.assertTrue(security.password_problems("Salasana12345!"))
        self.assertFalse(security.password_problems(PW))
        self.assertFalse(security.password_problems(security.generate_password()))

    def test_lockout(self):
        for _ in range(5):
            with self.assertRaises(security.LoginError):
                security.authenticate("johto@firma.fi", "väärä")
        with self.assertRaises(security.LoginError):
            security.authenticate("johto@firma.fi", PW)  # lukittu
        db.ex("UPDATE users SET locked_until = NULL")
        self.assertEqual(security.authenticate("JOHTO@firma.fi", PW)["id"], self.uid)

    def test_audit_chain_tamper_protection(self):
        security.audit("testi", "x", 1, "a")
        security.audit("testi", "x", 2, "b")
        self.assertTrue(security.verify_audit_chain()[0])
        with self.assertRaises(sqlite3.DatabaseError):
            db.ex("UPDATE audit SET details = 'muutettu'")

    def test_session(self):
        tok, csrf = security.create_session(self.uid)
        s = security.load_session(tok)
        self.assertEqual(s["user_id"], self.uid)
        self.assertTrue(security.csrf_ok(s, csrf))
        self.assertFalse(security.csrf_ok(s, "x"))
        self.assertIsNone(db.q1("SELECT 1 FROM sessions WHERE token_hash = ?", (tok,)))  # vain tiiviste
        security.destroy_session(tok)
        self.assertIsNone(security.load_session(tok))

    def test_secret_store(self):
        st = app_context.secret_store()
        st.set("smtp.password", "salainen")
        self.assertEqual(st.get("smtp.password"), "salainen")
        with open(st.path, encoding="utf-8") as f:
            self.assertNotIn("salainen", f.read())


class TestTimesheet(Base):
    def test_overlap_and_future(self):
        self.entry(1)
        with self.assertRaises(timesheet.EntryError):
            self.entry(1, "10:00", "11:00", 0)
        with self.assertRaises(timesheet.EntryError):
            self.entry(-5)

    def test_state_machine(self):
        eid = self.entry(1, submit=False)
        sess = {"employee_id": 1, "role": "tyontekija"}
        self.assertEqual(timesheet.submit([eid], sess), 1)
        timesheet.approve([eid], self.uid)
        with self.assertRaises(timesheet.EntryError):
            timesheet.update(eid, {}, sess)
        timesheet.reopen(eid)
        self.assertEqual(db.q1("SELECT status FROM time_entries WHERE id = ?", (eid,))["status"], "lahetetty")


class TestInvoicing(Base):
    def test_full_cycle(self):
        ids = [self.entry(1), self.entry(2)]
        timesheet.approve(ids, self.uid)
        iid = invoicing.create_draft(1, self.uid)
        res = invoicing.finalize(iid, self.uid)
        self.assertEqual(res["number"], 1000)
        inv = db.q1("SELECT * FROM invoices WHERE id = ?", (iid,))
        self.assertEqual(Decimal(inv["total_net"]), Decimal("800.00"))  # 2 × 8 h × 50
        self.assertEqual(Decimal(inv["total_vat"]), Decimal("204.00"))
        self.assertTrue(finref.tarkista_viite(inv["reference"]))
        with self.assertRaises(sqlite3.DatabaseError):
            db.ex("UPDATE invoices SET total_net = '1' WHERE id = ?", (iid,))
        with self.assertRaises(sqlite3.DatabaseError):
            db.ex("DELETE FROM journal_lines")
        invoicing.register_payment(iid, "1004.00", self.today.isoformat(), self.uid)
        self.assertEqual(db.q1("SELECT status FROM invoices WHERE id = ?", (iid,))["status"], "maksettu")
        self.assertEqual(sum(ledger.saldot("0001-01-01", "9999-12-31").values()), 0)
        xml_text = finvoice.build(invoicing.invoice_bundle(iid))
        doc = xml.dom.minidom.parseString(xml_text.encode())
        self.assertEqual(doc.getElementsByTagName("InvoiceTotalVatIncludedAmount")[0].firstChild.data, "1004,00")

    def test_reverse_charge_and_household(self):
        e1 = self.entry(1, cust=3, wt=2)
        e2 = self.entry(2, cust=2, wt=1)
        timesheet.approve([e1, e2], self.uid)
        rc = invoicing.create_draft(3, self.uid)
        invoicing.finalize(rc, self.uid)
        b = invoicing.invoice_bundle(rc)
        self.assertEqual(Decimal(b["invoice"]["total_vat"]), Decimal("0"))
        self.assertIn("AVL 8 c", b["texts"][0])
        hh = invoicing.create_draft(2, self.uid)
        invoicing.finalize(hh, self.uid)
        self.assertEqual(Decimal(invoicing.invoice_bundle(hh)["invoice"]["household_work"]), Decimal("502.00"))
        alv = ledger.alv_laskelma("0001-01-01", "9999-12-31")
        self.assertEqual(alv["rakentamispalvelut"], Decimal("480.00"))

    def test_credit_note_and_number_sequence(self):
        e1 = self.entry(1)
        timesheet.approve([e1], self.uid)
        iid = invoicing.create_draft(1, self.uid)
        invoicing.finalize(iid, self.uid)
        cn = invoicing.credit_note(iid, self.uid, "Virhe")
        self.assertEqual(db.q1("SELECT number FROM invoices WHERE id = ?", (cn,))["number"], 1001)
        self.assertEqual(db.q1("SELECT status FROM time_entries WHERE id = ?", (e1,))["status"], "hyvaksytty")
        self.assertEqual(ledger.alv_laskelma("0001-01-01", "9999-12-31")["vero_255"], Decimal("0.00"))
        self.assertTrue(ledger.tase(self.today.isoformat())["balanced"])

    def test_locked_period(self):
        e1 = self.entry(1)
        timesheet.approve([e1], self.uid)
        iid = invoicing.create_draft(1, self.uid)
        ledger.lock_period(self.today.strftime("%Y-%m"), self.uid)
        with self.assertRaises(invoicing.InvoiceError):
            invoicing.finalize(iid, self.uid)
        self.assertEqual(int(settings.get("invoice.next_number")), 1000)  # numero ei kulunut

    def test_penalty_interest(self):
        self.assertEqual(invoicing.penalty_interest("1000", "2026-01-01", "2026-12-31", "10.5"), Decimal("104.71"))  # 1000 × 10,5 % × 364/365

    def test_billable_rounding(self):
        wt = {"min_billing_min": 60, "rounding_min": 15}
        self.assertEqual(invoicing.billable_hours(20, wt), Decimal("1.00"))
        self.assertEqual(invoicing.billable_hours(70, wt), Decimal("1.25"))


class TestPayrollPricingExports(Base):
    def test_payroll_and_package(self):
        ids = [self.entry(i) for i in range(1, 4)]
        timesheet.approve(ids, self.uid)
        start, end = (self.today - dt.timedelta(days=10)).isoformat(), self.today.isoformat()
        data = payroll.build(start, end)
        self.assertEqual(data["employees"][0]["hours_total"], Decimal("24.00"))
        rid = payroll.save_run(start, end, self.uid)
        payroll.lock_run(rid, self.uid, post_accrual=True)
        self.assertIsNotNone(db.q1("SELECT 1 FROM journals WHERE series = 'PA'"))
        with self.assertRaises(timesheet.EntryError):
            timesheet.reopen(ids[0])
        iid = invoicing.create_draft(1, self.uid)
        invoicing.finalize(iid, self.uid)
        for name in profiles.PROFILES:
            data_b, _f = profiles.journal_csv(start, end, name)
            self.assertTrue(data_b)
        blob, fname, info = package.build_package(start, end)
        z = zipfile.ZipFile(io.BytesIO(blob))
        names = z.namelist()
        for must in ("00_LUE_MINUT.txt", "manifest.json", "alv-laskelma.csv", "palkat/palkkakooste.csv"):
            self.assertIn(must, names)
        man = json.loads(z.read("manifest.json"))
        import hashlib
        for n, h in man["files"].items():
            self.assertEqual(hashlib.sha256(z.read(n)).hexdigest(), h)

    def test_csv_injection_guard(self):
        self.assertEqual(profiles._safe_text("=HYPERLINK(1)"), "'=HYPERLINK(1)")

    def test_pricing(self):
        c = pricing.calc_from_settings("20", statutory_pct="20", indirect_pct="20", billable_ratio_pct="80",
                                       overhead_per_hour="5", target_margin_pct="20")
        self.assertEqual(c.employer_cost_per_paid_hour, Decimal("28.00"))
        self.assertEqual(c.cost_per_billable_hour, Decimal("40.00"))
        self.assertEqual(c.suggested_price, Decimal("50.00"))

    def test_transport_folder_idempotent(self):
        out = os.path.join(self.tmp, "ulos")
        os.makedirs(out)
        settings.set("export.transport", "kansio")
        settings.set("export.folder", out)
        r1 = transport.send("testi", b"abc", "a.csv", "2026-11", content_key="k1")
        r2 = transport.send("testi", b"abcd", "a.csv", "2026-11", content_key="k1")
        self.assertEqual(r1["status"], "ok")
        self.assertEqual(r2["status"], "ohitettu")
        self.assertTrue(os.path.exists(os.path.join(out, "a.csv")))

    def test_netvisor_dry_run(self):
        e1 = self.entry(1)
        timesheet.approve([e1], self.uid)
        invoicing.finalize(invoicing.create_draft(1, self.uid), self.uid)
        vouchers = transport.netvisor_vouchers("0001-01-01", "9999-12-31")
        self.assertEqual(len(vouchers), 1)
        xml.dom.minidom.parseString(vouchers[0].encode())
        self.assertIn('vatcode="KOMY"', vouchers[0])
        self.assertNotIn("<accountnumber>2939", vouchers[0])


class TestWeb(Base):
    """Käynnistää palvelimen ja ajaa keskeiset tietoturvatarkistukset HTTP:n yli."""

    @classmethod
    def setUpClass(cls):
        from huoltokello_talous.web import server, views  # noqa: F401
        cls.server_mod = server

    def setUp(self):
        super().setUp()
        from http.server import ThreadingHTTPServer
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), self.server_mod.Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.port}"
        self.cj = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cj))

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        super().tearDown()

    def get(self, path):
        try:
            r = self.op.open(self.base + path)
            return r.status, r.read().decode(), r.headers
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode(), exc.headers

    def post(self, path, data):
        try:
            r = self.op.open(urllib.request.Request(self.base + path, data=urllib.parse.urlencode(data).encode()))
            return r.status, r.read().decode()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode()

    def login(self):
        _s, html, _h = self.get("/login")
        token = re.search(r"name='csrf' value='([^']+)'", html).group(1)
        _s, html = self.post("/login", {"email": "johto@firma.fi", "password": PW, "csrf": token, "next": "/"})
        return re.search(r"name='csrf' value='([^']+)'", html).group(1)

    def test_headers_and_auth(self):
        s, _b, h = self.get("/laskut")
        self.assertEqual(s, 200)  # ohjattu kirjautumiseen
        self.assertIn("frame-ancestors 'none'", h["Content-Security-Policy"])
        self.assertEqual(h["X-Frame-Options"], "DENY")
        csrf = self.login()
        s, body, _h = self.get("/laskut")
        self.assertIn("Laskutus", body)
        # CSRF puuttuu -> 403
        s, _b = self.post("/asetukset", {"_group": "yritys", "company.name": "Hakkeri"})
        self.assertEqual(s, 403)
        self.assertEqual(settings.get_str("company.name"), "Testi Huolto Oy")
        s, _b = self.post("/asetukset", {"_group": "yritys", "company.name": "Uusi Nimi Oy", "csrf": csrf})
        self.assertEqual(settings.get_str("company.name"), "Uusi Nimi Oy")

    def test_xss_escaped(self):
        csrf = self.login()
        self.post("/asiakkaat/uusi", {"number": "9", "name": "<script>alert(1)</script>", "country": "FI",
                                      "payment_term_days": "14", "active": "1", "csrf": csrf})
        _s, body, _h = self.get("/asiakkaat")
        self.assertNotIn("<script>alert(1)</script>", body)
        self.assertIn("&lt;script&gt;", body)

    def test_role_enforcement(self):
        pw = security.generate_password()
        security.create_user("teppo@firma.fi", "Teppo", "tyontekija", pw, 1, must_change=False)
        _s, html, _h = self.get("/login")
        token = re.search(r"name='csrf' value='([^']+)'", html).group(1)
        self.post("/login", {"email": "teppo@firma.fi", "password": pw, "csrf": token, "next": "/"})
        s, _b, _h = self.get("/asetukset")
        self.assertEqual(s, 403)
        s, _b, _h = self.get("/tyoaika")
        self.assertEqual(s, 200)

    def test_api(self):
        token = security.create_api_token("hk", "tyoajat:write", self.uid)
        body = json.dumps([{"henkilonumero": "T1", "paiva": (self.today - dt.timedelta(days=1)).isoformat(),
                            "alku": "08:00", "loppu": "12:00", "tauko_min": 0, "tyolaji": "KH",
                            "asiakasnumero": "1001"}]).encode()
        req = urllib.request.Request(self.base + "/api/v1/tyoajat", data=body,
                                     headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"})
        r = urllib.request.urlopen(req)
        self.assertEqual(len(json.loads(r.read())["luotu"]), 1)
        bad = urllib.request.Request(self.base + "/api/v1/tyoajat", data=body,
                                     headers={"Content-Type": "application/json", "Authorization": "Bearer hkt_x"})
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(bad)
        self.assertEqual(cm.exception.code, 401)


if __name__ == "__main__":
    unittest.main()
