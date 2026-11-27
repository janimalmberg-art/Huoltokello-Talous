#!/usr/bin/env python3
"""Lisenssijärjestelmä (avaimet, konekoodi, aktivointi) - testit.

Kultavektorit on tuotettu kehittäjän Lisenssityökalulla
(Lisenssityokalu.ps1 -Testi). Sama kultavektoriajo ajetaan
PowerShell-toteutuksessa _lisenssi.ps1:ää käyttäen - jos molemmat
kielet tuottavat samat tulokset, järjestelmät keskustelevat oikein.

Aja: python -m unittest discover -s tests -t .
"""
import datetime as dt
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

_TAMA = Path(__file__).resolve().parent
sys.path.insert(0, str(_TAMA.parent))

os.environ.setdefault("HKT_KOKEILU_EI_IKKUNAA", "1")

from huoltokello_talous import licenssi, trial  # noqa: E402

# Kultavektorit (Lisenssityokalu.ps1 -Testi): sama algoritmi molemmissa kielissä.
KULTAVEKTORIT = [
    {"avain": "10100-00001-01231-23123-32212", "taso": "01", "kesto": 0,
     "konekoodi": "A1B2C3D4E5F6", "aktivointikoodi": "910815458486"},
    {"avain": "10200-00001-01231-23123-25710", "taso": "02", "kesto": 0,
     "konekoodi": "A1B2C3D4E5F6", "aktivointikoodi": "571447553907"},
    {"avain": "10300-00001-31231-23123-88057", "taso": "03", "kesto": 3,
     "konekoodi": "A1B2C3D4E5F6", "aktivointikoodi": "444024513334"},
    {"avain": "10400-00001-11231-23123-40634", "taso": "04", "kesto": 1,
     "konekoodi": "A1B2C3D4E5F6", "aktivointikoodi": "349016716053"},
]


class TestAvaimet(unittest.TestCase):

    def test_01_kultavektori_kelpuu(self):
        for v in KULTAVEKTORIT:
            t = licenssi.avain_tarkistus(v["avain"])
            self.assertTrue(t["ok"], t.get("virhe", ""))
            self.assertEqual(t["taso"], v["taso"])
            self.assertEqual(t["kesto"], v["kesto"])
            self.assertEqual(t["numero"], v["avain"].replace("-", ""))

    def test_02_kultavektori_aktivointikoodi(self):
        for v in KULTAVEKTORIT:
            koodi = licenssi.aktivointikoodi(v["konekoodi"], v["avain"])
            self.assertEqual(koodi, v["aktivointikoodi"])
            self.assertEqual(licenssi.muotoile_koodi(koodi), "{}-{}".format(koodi[:6], koodi[6:]))
            # näyttömuodolla sama tulos
            nab = licenssi.muotoile_kone(v["konekoodi"])
            self.assertEqual(licenssi.aktivointikoodi(nab, v["avain"]), v["aktivointikoodi"])

    def test_03_muokattu_avain_ei_kelpaa(self):
        for v in KULTAVEKTORIT:
            n = v["avain"].replace("-", "")
            i = 9
            uusi = "9" if n[i] != "9" else "7"
            viallinen = n[:i] + uusi + n[i + 1:]
            t = licenssi.avain_tarkistus(viallinen)
            self.assertFalse(t["ok"])
            self.assertIn("ei täsmää", t["virhe"])

    def test_04_muoto_ja_pituus(self):
        huonot = ["", "12345", "12345-67890", "x" * 25, "1234567890123456789012a1"]
        for h in huonot:
            self.assertFalse(licenssi.avain_tarkistus(h)["ok"])

    def test_05_eri_kone_eri_koodi(self):
        k1 = licenssi.aktivointikoodi("A1B2C3D4E5F6", KULTAVEKTORIT[0]["avain"])
        k2 = licenssi.aktivointikoodi("123456789ABC", KULTAVEKTORIT[0]["avain"])
        self.assertNotEqual(k1, k2)

    def test_06_konekoodi_muoto(self):
        kk = licenssi.konekoodi()
        self.assertEqual(len(kk), 12)
        for c in kk:
            self.assertIn(c, "0123456789ABCDEF")
        self.assertEqual(licenssi.muotoile_kone(kk), "{}-{}-{}".format(kk[:4], kk[4:8], kk[8:12]))
class TestAktivointi(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.polku = os.path.join(self._tmp.name, "lisenssi.json")
        os.environ["HK_LISENSSI_TIEDOSTO"] = self.polku

    def tearDown(self):
        os.environ.pop("HK_LISENSSI_TIEDOSTO", None)
        self._tmp.cleanup()

    def test_10_aktivointi_onnistuu_ja_tila_voimassa(self):
        v = KULTAVEKTORIT[0]
        tulos = licenssi.aktivoi(v["avain"], v["aktivointikoodi"], konekoodi_ohitus=v["konekoodi"])
        self.assertTrue(tulos["ok"], tulos.get("virhe", ""))
        self.assertTrue(os.path.isfile(self.polku))
        tila = licenssi.lataa_tila(konekoodi_ohitus=v["konekoodi"])
        self.assertTrue(tila["voimassa"])
        self.assertEqual(tila["taso"], v["taso"])
        self.assertEqual(tila["tuote"], "molemmat")
        self.assertIn("talous", tila["ohjelmat"])
        self.assertIn("multiasennus", tila["ohjelmat"])
        self.assertEqual(tila["voimassa_paattyen"], "ikuinen")
        self.assertEqual(tila["nimi"], "Huoltokello Yritys Pieni")
        # ilman ohitusta (tosi kone on eri) sama tiedosto ei kelpaa talle koneelle
        paikallinen = licenssi.lataa_tila()
        if paikallinen.get("konekoodi", "") != v["konekoodi"]:
            self.assertFalse(paikallinen["voimassa"])

    def test_11_vaara_koodi_ei_kelpaa(self):
        v = KULTAVEKTORIT[1]
        tulos = licenssi.aktivoi(v["avain"], "000000000000")
        self.assertFalse(tulos["ok"])
        self.assertFalse(os.path.isfile(self.polku))

    def test_12_aktivointi_toisen_koneen_konekoodilla_kun_konekoodi_ohittaa(self):
        v = KULTAVEKTORIT[2]
        tulos = licenssi.aktivoi(v["avain"], v["aktivointikoodi"], konekoodi_ohitus=v["konekoodi"])
        self.assertTrue(tulos["ok"], tulos.get("virhe", ""))

    def test_13_vioittunut_tila_ei_voimassa(self):
        v = KULTAVEKTORIT[1]
        self.assertTrue(licenssi.aktivoi(v["avain"], v["aktivointikoodi"], konekoodi_ohitus=v["konekoodi"])["ok"])
        with open(self.polku, encoding="utf-8") as fh:
            tila = json.load(fh)
        tila["avain"] = tila["avain"][:-1] + ("0" if tila["avain"][-1] != "0" else "5")
        with open(self.polku, "w", encoding="utf-8") as fh:
            json.dump(tila, fh)
        tila2 = licenssi.lataa_tila(konekoodi_ohitus=v["konekoodi"])
        self.assertFalse(tila2["voimassa"])
        self.assertIn("varmiste", tila2["virhe"])

    def test_14_vanhentunut_lisenssi(self):
        v = KULTAVEKTORIT[0]
        aktivoitu = (dt.date.today() - dt.timedelta(days=2 * 365 + 5)).isoformat()
        paattyen = (dt.date.fromisoformat(aktivoitu) + dt.timedelta(days=365 * 2)).isoformat()
        numero = v["avain"].replace("-", "")
        kone = v["konekoodi"]
        tila = {
            "versio": 1, "avain": v["avain"], "taso": "01", "kesto": 2,
            "konekoodi": kone, "aktivoitu": aktivoitu,
            "voimassa_paattyen": paattyen,
            "tuote": "molemmat", "ohjelmat": ["talous", "multiasennus"],
            "varmiste": licenssi.tila_varmiste(numero, "01", 2, kone, aktivoitu, paattyen),
        }
        with open(self.polku, "w", encoding="utf-8") as fh:
            json.dump(tila, fh)
        tulos = licenssi.lataa_tila(konekoodi_ohitus=kone)
        self.assertFalse(tulos["voimassa"])
        self.assertIn("vanhentui", tulos["virhe"])


class TestKokeiluLiitos(unittest.TestCase):

    def test_20_paattymisrivit_kertovat_aktivointimahdollisuudesta(self):
        rivit = "\n".join(trial.paattynyt_rivit({"alku": "2026-01-01"}))
        self.assertIn("Kokeilujaksosi on päättynyt", rivit)
        self.assertIn("Lisenssiavain", rivit)

    def test_21_ikkunaton_ajo_palauttaa_false(self):
        self.assertEqual(trial.ikkuna_paattynyt({"alku": "2026-01-01"}), False)


if __name__ == "__main__":
    unittest.main()

