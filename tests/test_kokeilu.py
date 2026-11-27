#!/usr/bin/env python3
"""Kokeilujakso (90 paivaa) - testit.

Aja esimerkiksi repojen juurikansiosta:
    python -m unittest discover -s tests -t .
"""
import datetime as dt
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

_TAMA = Path(__file__).resolve().parent
sys.path.insert(0, str(_TAMA.parent))

os.environ.setdefault("HKT_KOKEILU_EI_IKKUNAA", "1")  # ei ikkunoita testeissa

from huoltokello_talous import trial  # noqa: E402

KOKEILU_NIMI = "jani.malmberg@gmail.com"


def _kirjoita_merkki(data_dir: str, alku: str, varmiste: str) -> None:
    payload = {"kokeilujakso": trial.KOKEILU_PAIVAA, "alku": alku, "varmiste": varmiste}
    with open(trial._markkeripolku(data_dir), "w", encoding="utf-8") as fh:
        json.dump(payload, fh)


class TestKokeilujakso(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.data_dir = self._tmp.name
        self.tanaan = dt.date.today()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_01_ensimmainen_kaynnistys_luo_merkin(self):
        tila = trial.hae_tila(self.data_dir)
        self.assertTrue(tila["uusi"])
        self.assertFalse(tila["paattynyt"])
        self.assertEqual(tila["paivia_jaljella"], trial.KOKEILU_PAIVAA)
        self.assertTrue(os.path.isfile(trial._markkeripolku(self.data_dir)))
        # Toinen kaynnistys ei ole enaa "uusi"
        tila2 = trial.hae_tila(self.data_dir)
        self.assertFalse(tila2["uusi"])
        self.assertEqual(tila2["alku"], tila["alku"])

    def test_02_kokeilu_paattyy_90_paivan_jalkeen(self):
        alku = (self.tanaan - dt.timedelta(days=trial.KOKEILU_PAIVAA + 1)).isoformat()
        _kirjoita_merkki(self.data_dir, alku, trial._tarkiste(alku))
        tila = trial.hae_tila(self.data_dir)
        self.assertTrue(tila["paattynyt"])
        self.assertLessEqual(tila["paivia_jaljella"], 0)
        rivit = "\n".join(trial.paattynyt_rivit(tila))
        self.assertIn("Kokeilujaksosi on päättynyt", rivit)
        self.assertIn(KOKEILU_NIMI, rivit)
        self.assertIn("Jani Malmberg", rivit)
        self.assertIn("lisensseistä", rivit)

    def test_03_muokattu_merkki_ei_nollaa_kokeilua(self):
        alku = self.tanaan.isoformat()
        _kirjoita_merkki(self.data_dir, alku, "vaaravarmitetulosmursu")
        tila = trial.hae_tila(self.data_dir)
        self.assertTrue(tila["paattynyt"])
        self.assertTrue(tila.get("rikki"))
        self.assertFalse(tila["uusi"])
        rivit = "\n".join(trial.paattynyt_rivit(tila))
        self.assertIn("Kokeilujaksosi on päättynyt", rivit)

    def test_04_voittoa_merkki_sailyttaa_alkupaivan(self):
        alku = (self.tanaan - dt.timedelta(days=30)).isoformat()
        _kirjoita_merkki(self.data_dir, alku, trial._tarkiste(alku))
        tila = trial.hae_tila(self.data_dir)
        self.assertEqual(tila["alku"], alku)
        self.assertEqual(tila["paivia_jaljella"], trial.KOKEILU_PAIVAA - 30)
        self.assertFalse(tila["paattynyt"])

    def test_05_kello_liikutettu_taaksepin_ei_rankaise(self):
        alku = (self.tanaan + dt.timedelta(days=1)).isoformat()
        _kirjoita_merkki(self.data_dir, alku, trial._tarkiste(alku))
        tila = trial.hae_tila(self.data_dir)
        self.assertFalse(tila["paattynyt"])
        self.assertEqual(tila["paivia_jaljella"], trial.KOKEILU_PAIVAA)

    def test_06_tulosteet_sisaltavat_kehittajatiedot(self):
        puskuri = io.StringIO()
        with redirect_stdout(puskuri):
            trial.tulosta_paattymisilmoitus()
            trial.tulosta_tilarivi({"paivia_jaljella": 0})
            trial.tulosta_tilarivi({"paivia_jaljella": 77})
        tuloste = puskuri.getvalue()
        self.assertIn("Kokeilujaksosi on päättynyt", tuloste)
        self.assertIn("Jani Malmberg", tuloste)
        self.assertIn(KOKEILU_NIMI, tuloste)
        self.assertIn("lisensseistä", tuloste)
        self.assertIn("77 päivää jäljellä", tuloste)

    def test_07_tervehdys_rivit(self):
        rivit = "\n".join(trial.tervehdys_rivit({"alku": self.tanaan.isoformat()}))
        self.assertIn("90 päivää", rivit)
        self.assertIn(KOKEILU_NIMI, rivit)
        self.assertIn("Kokeilujaksosi on päättynyt", rivit)


if __name__ == "__main__":
    unittest.main()
