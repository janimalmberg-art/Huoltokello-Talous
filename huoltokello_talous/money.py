"""Rahamäärät ja ALV-laskenta Decimal-tarkkuudella.

Kaikki rahamäärät käsitellään Decimal-tyyppinä ja pyöristetään sentteihin
kaupallisella pyöristyksellä (ROUND_HALF_UP). Liukulukuja ei käytetä rahaan.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP, InvalidOperation

NBSP = "\u00a0"
CENT = Decimal("0.01")
ZERO = Decimal("0.00")

# Suomen arvonlisäverokannat 1.1.2026 alkaen (AVL 84 §, 85 §, 85 a §).
# 25,5 % yleinen, 13,5 % alennettu (ent. 14 %), 10 % lehdet, 0 % verottomat.
ALV_KANNAT = (Decimal("25.5"), Decimal("13.5"), Decimal("10"), Decimal("0"))
ALV_YLEINEN = Decimal("25.5")


def D(value) -> Decimal:
    """Muuntaa arvon Decimaliksi. Hyväksyy pilkun desimaalierottimena."""
    if isinstance(value, Decimal):
        return value
    if value is None or value == "":
        return Decimal("0")
    if isinstance(value, float):
        # float -> str ensin, ettei binääriesityksen virhe siirry
        value = repr(value)
    s = str(value).strip().replace(" ", "").replace(" ", "").replace(",", ".")
    try:
        return Decimal(s)
    except InvalidOperation as exc:
        raise ValueError(f"Virheellinen luku: {value!r}") from exc


def r2(value) -> Decimal:
    """Pyöristys sentteihin (ROUND_HALF_UP)."""
    return D(value).quantize(CENT, rounding=ROUND_HALF_UP)


def vat_amount(net, rate) -> Decimal:
    """Veron määrä verottomasta hinnasta."""
    return r2(D(net) * D(rate) / Decimal(100))


def gross_from_net(net, rate) -> Decimal:
    return r2(D(net) + vat_amount(net, rate))


def net_from_gross(gross, rate) -> Decimal:
    """Verottoman osuuden irrotus verollisesta hinnasta."""
    rate = D(rate)
    return r2(D(gross) * Decimal(100) / (Decimal(100) + rate))


def fmt_eur(value, symbol: bool = True) -> str:
    """Suomalainen muotoilu: 1 234,50 €."""
    v = r2(value)
    sign = "-" if v < 0 else ""
    v = abs(v)
    whole, frac = f"{v:.2f}".split(".")
    groups = []
    while len(whole) > 3:
        groups.insert(0, whole[-3:])
        whole = whole[:-3]
    groups.insert(0, whole)
    s = sign + NBSP.join(groups) + "," + frac
    return s + NBSP + "€" if symbol else s


def fmt_dec(value, places: int = 2) -> str:
    q = Decimal(1).scaleb(-places)
    return str(D(value).quantize(q, rounding=ROUND_HALF_UP)).replace(".", ",")


def validate_vat_rate(rate) -> Decimal:
    rate = D(rate)
    if rate not in ALV_KANNAT:
        raise ValueError(
            f"ALV-kanta {rate} % ei ole voimassa oleva Suomen verokanta "
            f"({', '.join(str(k) for k in ALV_KANNAT)})."
        )
    return rate
