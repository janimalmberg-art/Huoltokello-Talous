# Huoltokello-Talous 1.0

Työajanseuranta, työn hinnoittelu, laskutus, kirjanpito, palkkakoosteet ja viennit kirjanpitoon –
Huoltokello-työhallinnan rinnalle. Toimii selaimessa (myös puhelimessa), ei ulkoisia riippuvuuksia
(Python 3.10+ ja sen vakiokirjasto), tietokantana SQLite.

## Asennus (Windows)

1. Asenna Python 3.10 tai uudempi (python.org) – asennusohjelma tarjoaa myös winget-asennuksen.
2. Pura paketti ja kaksoisnapsauta **`Asenna-Talous.cmd`** (asentaa, luo johto-tunnuksen, työpöydän
   kuvakkeen, automaattisen käynnistyksen sekä yöajon: varmuuskopio + kuukausivienti klo 03.15).
3. Avaa **Huoltokello-Talous**-kuvake → `http://127.0.0.1:8090` ja kirjaudu.
4. Täytä **Asetukset > Yritys** (Y-tunnus, IBAN) → **Rekisterit**: työlajit ja hinnat, asiakkaat, työntekijät,
   käyttäjät (liitä työntekijän tunnus työntekijään).

Komentorivi: `python -m huoltokello_talous --help` (alusta, palvelin, luo-johto, varmuuskopio, paketti,
automaattivienti, huoltokello-synk, demodata, tarkista).

**Demodata (esittely):** tyhjään tietokantaan saa valmiin esittelyaineiston komennolla
`python -m huoltokello_talous demodata`. Lataus lisää demoyrityksen, työlajit, asiakkaat
(Y-tunnukset ja OVT-tunnukset generoituna), tarvikkeet, työntekijät ja demo-käyttäjät,
kaksi kuukautta työaika- ja tarvikekirjauksia, tarjouksia sekä laskuja eri tiloissa
(maksettu, osamaksu, erääntynyt + maksumuistutus, hyvitetty, luonnos) ja kaksi palkkajaksoa.
Lataus onnistuu vain tyhjään tietokantaan ja vain kerran; demo-käyttäjien salasanat
näytetään vain latauksen tulosteessa. Ei tuotantokäyttöön.

**Lähiverkko ja puhelimet:** käynnistä HTTPS:llä, jotta salasanat eivät kulje selkokielisinä:
`python -m huoltokello_talous palvelin --host 0.0.0.0 --port 8443 --cert varmenne.pem --key avain.pem`
(tai käänteisvälityspalvelin, esim. Caddy). Rajaa palomuurisääntö yksityiseen verkkoon.

## Roolit

| Rooli | Oikeudet |
|---|---|
| Työntekijä | oma työaika (kirjaus, leimaus sisään/ulos, km, päivärahat), tarvikekirjaukset |
| Työnjohto | edellisten lisäksi kaikkien työajat, hyväksyntä/hylkäys, laskuluonnokset, tarjoukset |
| Johto | kaikki: laskujen vahvistus, hyvitys, suoritukset, hinnoittelu, palkat, asetukset, käyttäjät |
| Kirjanpitäjä | lukuoikeus laskuihin, raportit, palkkakoosteet, viennit, kausien lukitus |

## Toiminnot

**Työajanseuranta** – viikkonäkymä, kirjaus (alku–loppu, tauko, työlaji, asiakas, kohde, Huoltokello-tehtävä,
km, päiväraha), leimaus sisään/ulos puhelimella, päällekkäisyyksien esto, hyväksyntäketju
(luonnos → lähetetty → hyväksytty/hylätty → laskutettu), tarvikekirjaukset asiakkaalle.

**Palkanlaskennan koosteet** – työaikalain (872/2019) mukaiset vuorokautinen ylityö (2 h +50 %, sitten +100 %),
viikkoylityö (+50 %), sunnuntai- ja kirkollisten juhlapyhien työ (+100 %), ilta-, yö- ja lauantailisät (TES-
kohtaiset €/h asetuksissa), km-korvaukset (0,55 €/km) ja päivärahat (25 € / 54 €) vuoden 2026 arvoilla.
Palkkarivit tulorekisterin tulolajeineen (201, 206, 211, 221, 235, 236, 311, 331) CSV:nä palkanlaskentaan.
Palkkajakson lukitus kiinnittää työajat. Valinnainen palkkojen jaksotuskirjaus.

**Työn hinnoittelu johdolle** – omakustannus laskutettavaa tuntia kohden (palkka, lakisääteiset sivukulut
2026, välilliset palkkakulut, laskutettavuusaste, yleiskulut), hintasuositus tavoitekatteella, hinnaston kate
työlajeittain, toteutunut kannattavuus työlajeittain ja asiakkaittain.

**Asiakashinnoittelu** – työlajien perushinnasto, asiakaskohtaiset tuntihinnat, laskutuksen pyöristys ja
minimiveloitus, tarjoukset/hinta-arviot tuntiarviosta.

**Laskutus** – laskuluonnokset hyväksytyistä työajoista ja tarvikkeista (rivi per työlaji tai per kirjaus),
katkeamaton laskunumerosarja, viitenumero (7-3-1) tai RF-viite, virtuaaliviivakoodi ja pankkiviivakoodi,
ALV-erittely (25,5 / 13,5 / 10 / 0 %), **rakentamispalvelujen käännetty verovelvollisuus (AVL 8 c §)**,
EU-palvelumyynti, **kotitalousvähennykseen oikeuttavan työn osuus**, vahvistettu lasku muuttumaton
(tietokantatasolla), hyvityslaskut, suoritukset, maksumuistutus (muistutusmaksu + korkolain mukainen
viivästyskorko 10,5 % / kuluttajat 9,5 %, 1.7.–31.12.2026), lähetys sähköpostilla, **Finvoice 3.0** -verkkolasku.

**Kirjanpito** – automaattinen kahdenkertainen kirjaus laskuista, hyvityksistä ja suorituksista
(muuttumattomat tositesarjat MY/HY/SU/PA), kausien lukitus. Raportit: päiväkirja, pääkirja, tuloslaskelma,
tase-erät, ALV-laskelma (kausiveroilmoituksen myyntikentät), myyntireskontra ikäjakaumineen, laskulista,
suoritukset, työaikaraportti (HTML ja CSV).

**Kirjanpitäjän paketti ja viennit** – yhdellä painalluksella tai automaattisesti joka kuukausi ZIP:
tiliöinnit valitun ohjelman muodossa, kaikki raportit, laskut HTML + Finvoice, palkkakooste, tarkistuslista
ja SHA-256-manifesti. Siirto määritettyyn palveluun: jaettu kansio (esim. tilitoimiston OneDrive), sähköposti
kirjanpitäjälle, HTTPS-integraatio, Netvisor-rajapinta tai Procountor-rajapinta. Sama aineisto ei lähde kahdesti.

### Tuetut kirjanpito-ohjelmat

| Ohjelma | Tiliöinnit | Laskut | Rajapinta |
|---|---|---|---|
| Visma Netvisor | CSV (kirjanpitotietojen tuonti) | Finvoice | accounting.nv (koeajo oletuksena) |
| Procountor (Finago) | CSV | Finvoice | ledgerreceipts (koeajo oletuksena) |
| Fennoa, Visma eEkonomi, Visma Fivaldi, Heeros, Lemonsoft, Tikon, Asteri, Kitsas, Passeli Merit | CSV-profiili | Finvoice | – |
| Talenom, Accountor ja muut tilitoimistot | yleinen päiväkirja-CSV | Finvoice | kansio / sähköposti / HTTPS |
| Muut / oma pohja | **mukautettu profiili** (sarakkeet, erotin, merkistö JSONina) | Finvoice | – |

**Tärkeää:** ohjelmatoimittajat muuttavat tuontipohjiaan. Profiilit noudattavat kunkin ohjelman yleistä
kirjanpitotietojen CSV-tuontia, mutta tarkista sarakkeet ohjelman ajantasaisesta tuontiohjeesta ennen
tuotantokäyttöä (Netvisor ja Procountor kohdistavat sarakkeet tuonnissa). Rajapintasiirrot ovat oletuksena
**koeajossa** – kytke `export.api_live = 1` vasta, kun aineisto on todettu oikeaksi testiympäristössä.
Rajapintoja ei ole voitu testata oikeita palveluja vastaan ilman asiakkaan API-tunnuksia.

## Huoltokello-integraatio

### Suora yhteys (v1.1)
**Rekisterit > Huoltokello-yhteys** (johto). Talous lukee Huoltokellon tietokantaa
(`%LOCALAPPDATA%\Huoltokello\AppData\huoltokello.db`) **vain lukutilassa**, eikä se koskaan kirjoita Huoltokellon tietoihin.

| Huoltokello | → Talous |
|---|---|
| Asiakkaat (Customers) | asiakkaat, numerona `HK<id>`, tai täsmäys nimellä |
| Käyttäjät (AspNetUsers) | työntekijät, täsmäys **sähköpostilla** |
| Tehtäväkategoriat | työlajit, täsmäys nimellä |
| Sopimuksen tuntihinta | asiakaskohtainen hinta |
| Kuittaukset (HoursWorked) | työaikakirjaukset tilaan *lähetetty* (vaativat hyväksynnän), task_ref `HK-<tehtävä>` |
| Kuittausten tarvikkeet | tarvikekirjaukset |

- Kellonajat: loppu = kuittausaika, alku = loppu − tunnit. Saman päivän kuittaukset ketjutetaan peräkkäin.
- Kirjausta **ei laskuteta**, jos tehtävä on jo laskutettu Huoltokellossa (InvoiceId) tai jos asiakkaalla on
  kuukausihintainen sopimus ilman tuntihintaa.
- Tuonti voidaan ajaa uudelleen turvallisesti. Jo tuotuja, päällekkäisiä tai lukittujen palkkakausien kirjauksia ei tuoda.
- Ennen tuontia näet **esikatselun**. Automaattinen synkronointi tehdään valitulla välillä.
- Komentorivi: `python -m huoltokello_talous huoltokello-synk [--esikatselu] [--polku X]`.

**Suositus:** laskuta vain toisessa ohjelmassa (suositus Talous), jotta mitään ei laskuteta kahdesti.
Käytä molemmissa ohjelmissa samoja sähköpostiosoitteita ja tehtäväkategorioiden nimiä.

### CSV ja rajapinta
Työajat Huoltokellosta (tai mistä tahansa) **CSV-tuonnilla** (Rekisterit > Tuonti) tai **rajapinnalla**
`POST /api/v1/tyoajat` (Bearer-avain, Rekisterit > Rajapinta-avaimet). Kirjaukset menevät hyväksyttäviksi.
`task_ref`-kenttään tallentuu Huoltokellon tehtävänumero.

## Tietoturva

PBKDF2-SHA256 (600 000 kierrosta), vahva salasanapolitiikka, kertakäyttöiset alkusalasanat, tilin lukitus
(5 yritystä / 15 min) ja IP-kohtainen nopeusrajoitus, istunnot 10 h + 2 h tyhjäkäynti (vain tiiviste kannassa),
CSRF kaikissa lomakkeissa, SameSite=Strict + HttpOnly (+ Secure HTTPS:ssä), tiukat turvaotsakkeet (CSP,
X-Frame-Options, nosniff), roolipohjaiset oikeudet, kaikki tuloste escapoitu, CSV-kaavainjektion esto,
SQL vain parametreilla, salaisuudet Windows DPAPI:lla erillisessä tiedostossa, datakansion ACL vain
käyttäjälle, **muutosloki ketjutetulla SHA-256-tiivisteellä** (muuttaminen ja poisto estetty tietokannassa),
kirjausten ja vahvistettujen laskujen muuttumattomuus triggereillä, SMTP vain TLS:llä, HTTPS-siirrot
varmenteen tarkistuksella, johdonmukaiset varmuuskopiot (SQLite online backup + eheystarkistus, 30 kpl).

## Testit

`python -m unittest discover -s tests` – 30 testiä: viitenumerot, RF, IBAN, Y-tunnus, Finanssialan
virtuaaliviivakoodiesimerkki, ALV, pyhäpäivät, ylityöt, salasanapolitiikka, lukitus, muutoslokin suojaus,
istunnot, salaisuudet, tilakone, laskutussykli, käännetty ALV, kotitalousvähennys, hyvitys ja numerosarja,
lukittu kausi, viivästyskorko, palkkakooste ja lukitus, kaikki vientiprofiilit, paketin manifesti, kansio-
siirron idempotenssi, Netvisor-tositteet sekä HTTP-tason testit (turvaotsakkeet, CSRF, XSS, roolit, API).

## Rajoitukset

- Ostolaskut ja ostojen ALV kirjataan kirjanpito-ohjelmassa; tämä moduuli tuottaa myynnin, suoritukset ja
  palkkajaksotukset. Tuloslaskelma ja tase ovat siksi osittaisia.
- Palkkakooste on palkanlaskennan syöte, ei palkanlaskenta (ennakonpidätys, lomapalkat ja tulorekisteri-
  ilmoitus tehdään palkkaohjelmassa). Tarkista oman TES:n lisät ja ylityösäännöt asetuksista.
- Finvoice-aineisto on rakennettu Finvoice 3.0 -elementtijärjestyksen mukaan; validoi ensimmäiset laskut
  operaattorin tai Finanssiala ry:n validaattorilla ennen verkkolaskutuksen aloittamista.
- Vuosittain päivitettävät luvut (ALV, km, päivärahat, viitekorko, sivukulut) ovat asetuksissa – lähteet:
  `docs/Lahteet.md`.

## Muutoshistoria

- **1.2.0** – Uudistettu ulkoasu: ryhmitelty sivuvalikko kuvakkeineen (Arki, Myynti, Talous, Hallinta),
  tilannekuva etusivulla (tervehdys, tunnusluvut, laskutus kuukausittain, pikatoiminnot), uusi kirjautumissivu,
  värikoodatut tilamerkit, selkeämmät lomakkeet ja taulukot sekä puhelimeen liukuva valikko. Toiminnot ja tiedot ennallaan.
- **1.1.0** – Suora Huoltokello-yhteys (kuittaukset, asiakkaat, työntekijät, tarvikkeet).
- **1.0.1** – Korjauksia hinnoitteluun ja vientien siirtoon.
