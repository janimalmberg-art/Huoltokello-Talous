# Asennusohje: Huoltokello-Talous (Windows)

Tämä ohje on tarkoitettu tavalliselle käyttäjälle – ei tarvita ohjelmointitaitoja.
Asennus kestää noin 10 minuuttia ja vaatii internet-yhteyden vain, jos Python pitää asentaa.

---

## Ennen aloitusta

Tarvitset:

- Windows-tietokone (Windows 10 tai 11), jolle voit asentaa ohjelmia omalla Windows-tunnuksellasi (ei esim. koulun tai lainakoneen rajoitettu tunnus)
- Internet-selaimen (Edge, Chrome tai Firefox)
- Asennuspaketin: `Huoltokello-Talous-1.1.0.zip`
  - lataa tästä: https://github.com/janimalmberg-art/Huoltokello-Talous/releases/latest/download/Huoltokello-Talous-1.1.0.zip

---

## Vaihe 1: Lataa ja pura paketti

1. Avaa yllä oleva latauslinkki selaimella. Tiedosto `Huoltokello-Talous-1.1.0.zip` tallentuu yleensä **Lataukset**-kansioon.
2. Avaa **Lataukset**-kansio (Resurssienhallinta → Lataukset).
3. Etsi tiedosto `Huoltokello-Talous-1.1.0.zip`.
4. Napsauta tiedostoa **oikealla hiirennapilla** → **Pura kaikki…** → näkyviin tulevassa ikkunassa **Pura**.
5. Syntyy uusi kansio (esim. `Huoltokello-Talous`). Avaa se. Sisällä pitää lukea mm. **`Asenna-Talous.cmd`**, `README.md` ja `VERSIO.txt`.

> Jos selain tai Windows varoittaa tiedostosta ("tiedosto voi olla haitallinen" tms.), valitse **säilytä/salli** – kyseessä on sama paketti, joka on ladattavissa julkisesta GitHub-releasesta.

## Vaihe 2: Käynnistä asennus

1. Kaksoisnapsauta **`Asenna-Talous.cmd`**.
2. Avautuu teksti-ikkuna (PowerShell, musta tai valkoinen). **Älä sulje ikkunaa** asennuksen ajaksi – se tekee kaiken ja odottaa sinua lopussa.

Ikkuna tekee automaattisesti:

- tarkistaa, onko koneessa Python 3.10 tai uudempi (ohjelman ainoa riippuvuus)
- kopioi ohjelmatiedostot paikoilleen (`%LOCALAPPDATA%\Huoltokello-Talous\app`)
- luo työpöydälle pikakuvakkeen **Huoltokello-Talous**
- asettaa ohjelman käynnistymään automaattisesti, kun kirjaudut Windowsiin
- asettaa yöajon klo 03:15 (varmuuskopio + kuukausivinnit; **30** varmuuskopiota säilytetään kansiossa `Data\varmuuskopiot`)
- alustaa tietokannan ja luo **johto-tunnuksen** (kysyy tietoja kohdassa Vaihe 3)

**Jos ikkunaan tulee kysymys** "Asennetaanko Python 3.12 winget-tyokalulla ...? (k/o)", koneessa ei vielä ole Pythonia:

1. Kirjoita näppäimistöllä **k** ja paina **Enter**.
2. Odota pari minuuttia – Python asentuu Microsoftin pakettilähteestä (vaatii internetin).
3. Asennus jatkuu itsestään.

Vanhempi kone ilman winget-työkalua: asenna Python käsin osoitteesta <https://www.python.org/downloads/windows/> (Windows installer), käynnistä tietokone uudelleen ja aja `Asenna-Talous.cmd` uudelleen.

## Vaihe 3: Ilmoita johto-tunnuksesi (vain ensimmäisellä asennuskerralla)

Teksti-ikkunaan tulee rivi **"Luodaan ensimmäinen johto-tunnus."**, jota seuraavat kolme kysymystä. Vastaa näppäimistöllä ja paina jokaisen jälkeen **Enter**:

1. **Sähköposti (tunnus):** esim. `johto@yritykseni.fi` – tämä on kirjautumisnimesi.
   Käytä jatkossa kaikilla käyttäjillä todellisia sähköpostiosoitteita: esitiedot ja
   Huoltokello-silta täsmäytetään niiden perusteella.
2. **Nimi:** oma nimesi, esim. `Matti Meikäläinen`
3. **Salasana:** vahva salasana, **kirjoittaminen ei näy ruudulla** (normaali turvaominaisuus)
4. **Salasana uudelleen:** sama salasana vielä kerran

Salasanan vaatimukset:

- vähintään **12 merkkiä**; joko **kolme merkkityyppiä** (pienet, isot, numerot/erikoismerkit) **tai vähintään 16 merkin salalause**
- ei sisällä nimeäsi tai käyttäjätunnustasi

Hyviä esimerkkejä: `Pihapiiri Ja Aita 2026!` (17 merkin salalause) tai `TalPalkat2026!`.

## Vaihe 4: Ota käyttöön

Kun ikkunaan tulee rivi, joka alkaa **"VALMIS."** (teksti-ikkunassa ääkköset puuttuvat konsolin
rajoituksen vuoksi – tarkoittaa "VALMIS, käynnistä työpöydän kuvakkeesta"), asennus on valmis:

1. Sulje teksti-ikkuna painamalla mitä tahansa näppäintä.
2. Avaa työpöydältä kuvake **Huoltokello-Talous**.
3. Selain aukeaa osoitteeseen `http://127.0.0.1:8090` muutaman sekunnin viiveellä.
4. Kirjautumislomakkeeseen: **tunnus = sähköposti, jonka annoit**, **salasana = Vaihe 3:ssa laatimasi**.
5. Lisää osoite selainkirjanmerkkeihin (pikanäppäin Ctrl+D) – jatkossa pääset suoraan sisään.

Ensimmäiset työt ohjelmassa:

1. **Asetukset > Yritys**: Y-tunnus, tilinumero (IBAN) ja muut yritystiedot.
2. **Rekisterit**: työlajit ja hinnat → asiakkaat → työntekijät → käyttäjät (liitä tunnus työn­tekijään).

---

## Käyttö päivittäin

- Käynnistys: työpöydän kuvake **Huoltokello-Talous**, TAI kirjoita selaimen osoitekenttään
  `http://127.0.0.1:8090` (palvelin käynnistyy itsekin, kun kirjaudut Windowsiin).
- Sulje selain vain vapaasti – ohjelma voi jäädä taustalle odottamaan; se ei häiritse muuta työtä.
- Jokainen käyttäjä kirjautuu omalla tunnuksellaan **samalla koneella** (työntekijä vie omat työajat,
  työnjohto hyväksyy, johto hoitaa laskutuksen ja asetukset).

Uudet käyttäjät: johto kirjautuu sisään ja luo muut tunnukset **Rekisterit > Käyttäjät** – asennusta
tai ohjelmaa ei tarvitse koskea enää.

## Uuden version asennus (päivitys)

1. Lataa GitHub-sivulta uusin asennuspaketti (releases → uusin) ja pura se kuten kohta Vaihe 1.
2. Kaksoisnapsauta uuden kansion **`Asenna-Talous.cmd`**.
3. Asennin korvaa ohjelmakoodin uudella versiolla. **Tallentamasi tiedot säilyvät**, koska tietokanta
   on eri kansiossa (`%LOCALAPPDATA%\Huoltokello-Talous\Data`), jota asennin ei koske.
4. Teksti-ikkunassa lukee "Johto-tunnus on jo olemassa." – se on hyvä merkki: kirjaudut samalla
   vanhalla tunnuksella.

Missä tiedot oikein ovat (varmuuskopiointia varten):

```
%LOCALAPPDATA%\Huoltokello-Talous\Data   (talous.db, lokit, varmuuskopiot\)
```

Kopioi koko Data-kansio toiselle levylle/verkkoasemaan silloin tällöin – ohjelman oma
yöajo (klo 03:15) tekee 30 viimeisintä varmuuskopiota koneen sisällä.

## Salasana unohtui?

1. Paina **Windows-näppäintä**, kirjoita `powershell`, avaa **Windows PowerShell**.
2. Liitä alla olevat rivit yksi kerrallaan ja paina kunkin jälkeen Enter:

```powershell
cd $env:LOCALAPPDATA\Huoltokello-Talous\app
py -m huoltokello_talous --data "$env:LOCALAPPDATA\Huoltokello-Talous\Data" luo-johto
```

3. Kysymykset ovat samat kuin asennuksessa (sähköposti, nimi, salasana). Komento luo **uuden
   johto-tunnuksen** – kirjaudu seuraavaksi sillä. Vanha tunnus jää voimaan, jos se muistetaan myöhemmin.

## Asennuksen purku

1. Jos tietoissa on arvoa: kopioi ensin kansio `%LOCALAPPDATA%\Huoltokello-Talous\Data` talteen
   muualle (esim. USB-tikulle). Purku poistaa ne.
2. Avaa Windows PowerShell ja liitä rivit yksi kerrallaan:

```powershell
schtasks /Delete /TN "Huoltokello-Talous" /F
schtasks /Delete /TN "Huoltokello-Talous-Yoajo" /F
Remove-Item "$env:LOCALAPPDATA\Huoltokello-Talous" -Recurse -Force
Remove-Item "$([Environment]::GetFolderPath('Desktop'))\Huoltokello-Talous.lnk" -Force
```

Tämä poistaa ohjelman, tiedot, työpöydän kuvakkeen ja molemmat ajastetut tehtävät.

## Yleisimmät ongelmat

| Oire | Ratkaisu |
|---|---|
| Teksti-ikkuna välähtää heti kiinni | Avaa puramasi paketin kansio, kirjoita Explorerin osoitekenttään `powershell` → Enter, ja aja rivi: `powershell -ExecutionPolicy Bypass -File .\Asenna-Talous.ps1 -Automaattikaynnistys -Ajastettu; pause` |
| "Python puuttuu" – vaikka asensit Pythonin | Käynnistä tietokone uudelleen ja aja `Asenna-Talous.cmd` uudelleen (uusi ohjelma löytyy polulta vasta uudessa istunnossa) |
| Pythonin asennus epäonnistuu tai `winget` puuttuu | Asenna Python käsin: https://www.python.org/downloads/windows/ → **Windows installer (64-bit)** → käynnistä kone uudelleen → aja `Asenna-Talous.cmd` |
| Selain: "Tätä sivua ei voida saavuttaa" | Ohjelma ei ole ehkä käynnissä: avaa työpöydän kuvake ja odota 5–10 s. Varmista myös osoite tarkasti: `http://127.0.0.1:8090` |
| Salasana ei kelpaa | Lue vaatimukset kohdasta Vaihe 3; kokeile vähintään 16 merkin salalausetta |
| Kuvake ei näy työpöydällä | Työpöytä voi olla OneDriveen siirretty: kuvake on OneDrive-työpöydällä. Voit myös käyttää ohjelmaa suoraan selaimella `http://127.0.0.1:8090` |
| Windows varoittaa asennuksesta | Valitse "Salli" / "Suorita silti"; tiedostot tulevat julkisesta GitHub-releasestasi |

## Tiedot ja tietoturva lyhyesti

- Ohjelma kuuntelee vain tätä konetta (`127.0.0.1`): muut verkossa olevat laitteet eivät pääse
  oletus­asennukseen. Data­kansio on Windowsin oikeuksilla vain sinun käyttäjällesi.
- Salasanat tallennetaan vahvasti suolattuna tiivisteenä (PBKDF2, 600 000 kierrosta), istunnot
  evästeessä ja salaisuudet Windows DPAPI:lla – jokainen muutos merkitään kirjoitussuojattuun muutoslokiin.
- **Puhelin/lähiverkko** (ei pakollinen): vaatii HTTPS-varmenteen ja palomuurisäännön – katso
  README.md:n osio **"Lähiverkko ja puhelimet"**.
- **Huoltokello-silta** (jos käytössä on myös Huoltokello): talous lukee Huoltokellon kirjaukset
  suoraan: **Rekisterit > Huoltokello-yhteys** (esikatselu ennen tuontia).

---

Lisää teknisestä sisällöstä: **README.md** samassa kansiossa (roolit, toiminnot, vientimuodot,
komentorivitoiminnot). Versiohistoria: **VERSIO.txt**. Tuki: GitHub-repon Issues-sivu
https://github.com/janimalmberg-art/Huoltokello-Talous/issues.
