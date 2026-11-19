"""Suomalaiset maksu- ja yritystunnisteet.

- kotimainen viitenumero (painot 7-3-1), Finanssiala ry:n standardi
- kansainvälinen RF-viite (ISO 11649)
- IBAN-tarkistus (ISO 13616, mod 97)
- Y-tunnuksen tarkistus ja OVT-tunnuksen muodostus
- pankkiviivakoodi / virtuaaliviivakoodi versiot 4 ja 5 (Finanssiala ry)
"""
from __future__ import annotations

import datetime as _dt
import re
from decimal import Decimal

from .money import D, r2

_WEIGHTS_731 = (7, 3, 1)


def _digits(s: str) -> str:
    return re.sub(r"\s", "", str(s or ""))


def viite_tarkiste(base: str) -> int:
    base = _digits(base)
    if not base.isdigit() or not 3 <= len(base) <= 19:
        raise ValueError("Viitenumeron perusosan on oltava 3–19 numeroa.")
    total = 0
    for i, ch in enumerate(reversed(base)):
        total += int(ch) * _WEIGHTS_731[i % 3]
    return (10 - total % 10) % 10


def luo_viite(base) -> str:
    """Muodostaa viitenumeron perusosasta (esim. laskunumero)."""
    base = _digits(str(base))
    if len(base) < 3:
        base = base.rjust(3, "0") if base else "100"
        # Etunollat ovat sallittuja perusosassa mutta pudotetaan esitysmuodosta;
        # käytetään vähintään kolmea merkitsevää numeroa.
        base = "1" + base if base.startswith("0") else base
    return base + str(viite_tarkiste(base))


def tarkista_viite(viite: str) -> bool:
    v = _digits(viite)
    if not v.isdigit() or not 4 <= len(v) <= 20:
        return False
    return viite_tarkiste(v[:-1]) == int(v[-1])


def muotoile_viite(viite: str) -> str:
    """Viidellä numerolla ryhmitelty esitysmuoto oikealta lukien."""
    v = _digits(viite)
    groups = []
    while v:
        groups.insert(0, v[-5:])
        v = v[:-5]
    return " ".join(groups)


def _iso7064_numeric(s: str) -> str:
    out = []
    for ch in s.upper():
        if ch.isdigit():
            out.append(ch)
        elif "A" <= ch <= "Z":
            out.append(str(ord(ch) - 55))
        else:
            raise ValueError(f"Kielletty merkki: {ch!r}")
    return "".join(out)


def luo_rf_viite(kotimainen_viite: str) -> str:
    ref = _digits(kotimainen_viite).upper()
    check = 98 - int(_iso7064_numeric(ref + "RF00")) % 97
    return f"RF{check:02d}{ref}"


def tarkista_rf(rf: str) -> bool:
    s = _digits(rf).upper()
    if not s.startswith("RF") or len(s) < 5 or len(s) > 25:
        return False
    try:
        return int(_iso7064_numeric(s[4:] + s[:4])) % 97 == 1
    except ValueError:
        return False


def muotoile_rf(rf: str) -> str:
    s = _digits(rf).upper()
    return " ".join(s[i:i + 4] for i in range(0, len(s), 4))


def normalisoi_iban(iban: str) -> str:
    return _digits(iban).upper()


def tarkista_iban(iban: str) -> bool:
    s = normalisoi_iban(iban)
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{10,30}", s):
        return False
    if s.startswith("FI") and len(s) != 18:
        return False
    try:
        return int(_iso7064_numeric(s[4:] + s[:4])) % 97 == 1
    except ValueError:
        return False


def muotoile_iban(iban: str) -> str:
    s = normalisoi_iban(iban)
    return " ".join(s[i:i + 4] for i in range(0, len(s), 4))


_YT_WEIGHTS = (7, 9, 10, 5, 8, 4, 2)


def tarkista_ytunnus(yt: str) -> bool:
    s = (yt or "").strip()
    m = re.fullmatch(r"(\d{6,7})-(\d)", s)
    if not m:
        return False
    body = m.group(1).rjust(7, "0")
    total = sum(int(c) * w for c, w in zip(body, _YT_WEIGHTS))
    rem = total % 11
    if rem == 1:
        return False
    check = 0 if rem == 0 else 11 - rem
    return check == int(m.group(2))


def ovt_tunnus(ytunnus: str, tarkenne: str = "") -> str:
    """OVT-tunnus: 0037 + Y-tunnus ilman väliviivaa + vapaaehtoinen tarkenne."""
    if not tarkista_ytunnus(ytunnus):
        raise ValueError("Virheellinen Y-tunnus.")
    body = ytunnus.replace("-", "").rjust(8, "0")
    return "0037" + body + re.sub(r"\D", "", tarkenne or "")[:5]


def virtuaaliviivakoodi(iban: str, summa, viite: str, erapaiva: _dt.date | None) -> str:
    """Pankkiviivakoodin 54-merkkinen numerosarja (versio 4 tai 5).

    Versio 4: kotimainen viite. Versio 5: RF-viite.
    Summan ylittäessä 999 999,99 € summakenttä on nollia (maksaja syöttää summan).
    """
    ib = normalisoi_iban(iban)
    if not ib.startswith("FI") or not tarkista_iban(ib):
        raise ValueError("Virtuaaliviivakoodi vaatii kelvollisen suomalaisen IBANin.")
    tili = ib[2:]  # 16 numeroa: tarkiste + BBAN
    summa = r2(summa)
    if summa < 0:
        raise ValueError("Negatiiviselle summalle ei muodosteta viivakoodia.")
    if summa > Decimal("999999.99"):
        euros, cents = "000000", "00"
    else:
        euros = f"{int(summa):06d}"
        cents = f"{int((summa * 100) % 100):02d}"
    pvm = erapaiva.strftime("%y%m%d") if erapaiva else "000000"
    v = _digits(viite).upper()
    if v.startswith("RF"):
        if not tarkista_rf(v):
            raise ValueError("Virheellinen RF-viite.")
        check, body = v[2:4], v[4:]
        if not body.isdigit() or len(body) > 21:
            raise ValueError("RF-viitteen on oltava numeerinen viivakoodia varten.")
        code = "5" + tili + euros + cents + check + body.rjust(21, "0") + pvm
    else:
        if not tarkista_viite(v):
            raise ValueError("Virheellinen viitenumero.")
        code = "4" + tili + euros + cents + "000" + v.rjust(20, "0") + pvm
    assert len(code) == 54, len(code)
    return code


def code128c_svg(numerot: str, height: int = 50, module: float = 1.0) -> str:
    """Piirtää Code 128 (merkistö C) -viivakoodin SVG-muodossa (pankkiviivakoodi)."""
    if len(numerot) % 2 or not numerot.isdigit():
        raise ValueError("Code 128C vaatii parillisen määrän numeroita.")
    patterns = _CODE128_PATTERNS
    values = [105]  # Start C
    values += [int(numerot[i:i + 2]) for i in range(0, len(numerot), 2)]
    checksum = values[0] + sum(v * (i + 1) for i, v in enumerate(values[1:]))
    values.append(checksum % 103)
    bars = "".join(patterns[v] for v in values) + "2331112"  # Stop
    x = 10 * module  # hiljainen alue
    rects = []
    black = True
    for w in bars:
        width = int(w) * module
        if black:
            rects.append(f'<rect x="{x:.2f}" y="0" width="{width:.2f}" height="{height}"/>')
        x += width
        black = not black
    total = x + 10 * module
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{total:.0f}" height="{height}" '
            f'viewBox="0 0 {total:.2f} {height}" role="img" aria-label="Pankkiviivakoodi">'
            + "".join(rects) + "</svg>")


# Code 128 -leveyskuviot (arvot 0..105), lähde: ISO/IEC 15417.
_CODE128_PATTERNS = [
    "212222", "222122", "222221", "121223", "121322", "131222", "122213", "122312", "132212", "221213",
    "221312", "231212", "112232", "122132", "122231", "113222", "123122", "123221", "223211", "221132",
    "221231", "213212", "223112", "312131", "311222", "321122", "321221", "312212", "322112", "322211",
    "212123", "212321", "232121", "111323", "131123", "131321", "112313", "132113", "132311", "211313",
    "231113", "231311", "112133", "112331", "132131", "113123", "113321", "133121", "313121", "211331",
    "231131", "213113", "213311", "213131", "311123", "311321", "331121", "312113", "312311", "332111",
    "314111", "221411", "431111", "111224", "111422", "121124", "121421", "141122", "141221", "112214",
    "112412", "122114", "122411", "142112", "142211", "241211", "221114", "413111", "241112", "134111",
    "111242", "121142", "121241", "114212", "124112", "124211", "411212", "421112", "421211", "212141",
    "214121", "412121", "111143", "111341", "131141", "114113", "114311", "411113", "411311", "113141",
    "114131", "311141", "411131", "211412", "211214", "211232",
]
