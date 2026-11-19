"""Finvoice 3.0 -verkkolasku (Finanssiala ry).

Finvoice on suomalaisten pankkien ja verkkolaskuoperaattoreiden yhteinen verkkolaskumuoto,
jonka lähes kaikki Suomessa käytössä olevat taloushallinto-ohjelmat ja tilitoimistot
pystyvät vastaanottamaan (myyntilaskujen siirto kirjanpitoon / ostolaskuna asiakkaalle).
Elementtien järjestys noudattaa Finvoice 3.0 -skeemaa.
"""
from __future__ import annotations

import datetime as dt
import re
from xml.sax.saxutils import escape, quoteattr

from ..finref import normalisoi_iban
from ..money import D, r2

VAT_CODE_MAP = {"ALV25.5": "S", "ALV13.5": "S", "ALV10": "S", "RAK": "AE", "EUPAL": "AE", "ALV0": "Z", "EIALV": "O"}
UNIT_MAP = {"h": "h", "kpl": "kpl", "km": "km", "m": "m", "m2": "m2", "l": "l", "kg": "kg", "pv": "pv", "kk": "kk"}


def _amt(v) -> str:
    return f"{r2(v):.2f}".replace(".", ",")


def _qty(v) -> str:
    d = D(v)
    s = f"{d.normalize():f}" if d == d.to_integral_value() else f"{d:f}".rstrip("0").rstrip(".")
    return s.replace(".", ",")


def _date(iso: str) -> str:
    return dt.date.fromisoformat(iso).strftime("%Y%m%d")


def _el(name: str, text, **attrs) -> str:
    if text is None or text == "":
        return ""
    a = "".join(f" {k}={quoteattr(str(v))}" for k, v in attrs.items())
    return f"<{name}{a}>{escape(str(text))}</{name}>"


def _vat_number(business_id: str) -> str:
    return "FI" + re.sub(r"\D", "", business_id or "") if business_id else ""


def build(bundle: dict) -> str:
    inv, lines, cust, comp = bundle["invoice"], bundle["lines"], bundle["customer"], bundle["company"]
    if inv["status"] == "luonnos":
        raise ValueError("Luonnoksesta ei muodosteta verkkolaskua.")
    credit = inv["kind"] == "hyvitys"
    eur = {"AmountCurrencyIdentifier": "EUR"}
    x: list[str] = ['<?xml version="1.0" encoding="UTF-8"?>',
                    '<Finvoice Version="3.0" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
                    'xsi:noNamespaceSchemaLocation="Finvoice3.0.xsd">']
    if comp.get("einvoice_address") and cust.get("einvoice_address"):
        x.append("<MessageTransmissionDetails><MessageSenderDetails>"
                 + _el("FromIdentifier", comp["einvoice_address"])
                 + _el("FromIntermediator", comp.get("einvoice_operator")) + "</MessageSenderDetails>"
                 + "<MessageReceiverDetails>" + _el("ToIdentifier", cust["einvoice_address"])
                 + _el("ToIntermediator", cust.get("einvoice_operator")) + "</MessageReceiverDetails>"
                 + "<MessageDetails>" + _el("MessageIdentifier", f"HKT-{inv['number']}")
                 + _el("MessageTimeStamp", dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"))
                 + "</MessageDetails></MessageTransmissionDetails>")
    x.append("<SellerPartyDetails>" + _el("SellerPartyIdentifier", comp.get("business_id"))
             + _el("SellerOrganisationName", comp.get("name"))
             + _el("SellerOrganisationTaxCode", _vat_number(comp.get("business_id")) if comp.get("vat_registered") else "")
             + "<SellerPostalAddressDetails>" + _el("SellerStreetName", comp.get("street"))
             + _el("SellerTownName", comp.get("city")) + _el("SellerPostCodeIdentifier", comp.get("postcode"))
             + _el("CountryCode", "FI") + "</SellerPostalAddressDetails></SellerPartyDetails>")
    x.append(_el("SellerOrganisationUnitNumber", comp.get("einvoice_address")))
    if comp.get("phone") or comp.get("email"):
        x.append("<SellerCommunicationDetails>" + _el("SellerPhoneNumberIdentifier", comp.get("phone"))
                 + _el("SellerEmailaddressIdentifier", comp.get("email")) + "</SellerCommunicationDetails>")
    x.append("<SellerInformationDetails>" + _el("SellerHomeTownName", comp.get("domicile"))
             + "<SellerAccountDetails>"
             + _el("SellerAccountID", normalisoi_iban(comp.get("iban", "")), IdentificationSchemeName="IBAN")
             + _el("SellerBic", comp.get("bic"), IdentificationSchemeName="BIC")
             + "</SellerAccountDetails></SellerInformationDetails>")
    buyer_tax = _vat_number(cust.get("business_id")) if cust.get("business_id") and not cust.get("is_consumer") else ""
    if (cust.get("country") or "FI") != "FI" and cust.get("business_id"):
        buyer_tax = cust["business_id"]
    x.append("<BuyerPartyDetails>"
             + _el("BuyerPartyIdentifier", cust.get("business_id") if not cust.get("is_consumer") else "")
             + _el("BuyerOrganisationName", cust.get("name")) + _el("BuyerOrganisationTaxCode", buyer_tax)
             + "<BuyerPostalAddressDetails>" + _el("BuyerStreetName", cust.get("street") or "-")
             + _el("BuyerTownName", cust.get("city") or "-") + _el("BuyerPostCodeIdentifier", cust.get("postcode") or "-")
             + _el("CountryCode", cust.get("country") or "FI") + "</BuyerPostalAddressDetails></BuyerPartyDetails>")
    x.append(_el("BuyerOrganisationUnitNumber", cust.get("einvoice_address")))
    x.append(_el("BuyerContactPersonName", cust.get("contact_person")))
    if cust.get("email"):
        x.append("<BuyerCommunicationDetails>" + _el("BuyerEmailaddressIdentifier", cust["email"])
                 + "</BuyerCommunicationDetails>")
    if inv["delivery_start"] and inv["delivery_end"]:
        x.append("<DeliveryDetails><DeliveryPeriodDetails>"
                 + _el("StartDate", _date(inv["delivery_start"]), Format="CCYYMMDD")
                 + _el("EndDate", _date(inv["delivery_end"]), Format="CCYYMMDD")
                 + "</DeliveryPeriodDetails></DeliveryDetails>")
    vat_xml = ""
    for (code, rate), (base, vat) in bundle["vat_spec"].items():
        free = {"RAK": "Käännetty verovelvollisuus AVL 8 c §", "EUPAL": "Reverse charge",
                "EIALV": "Ei arvonlisäveron alainen"}.get(code, "")
        vat_xml += ("<VatSpecificationDetails>" + _el("VatBaseAmount", _amt(base), **eur)
                    + _el("VatRatePercent", _qty(rate)) + _el("VatCode", VAT_CODE_MAP.get(code, "S"))
                    + _el("VatRateAmount", _amt(vat), **eur) + _el("VatFreeText", free)
                    + "</VatSpecificationDetails>")
    free_text = " ".join(t for t in [inv["notes"]] + bundle["texts"] if t)[:512]
    if inv["household_work"] and D(inv["household_work"]) != 0:
        free_text = (free_text + f" Kotitalousvähennykseen oikeuttava työn osuus {_amt(inv['household_work'])} € "
                     "(sis. ALV).").strip()[:512]
    rate = bundle.get("penalty_interest") or ""
    x.append("<InvoiceDetails>"
             + _el("InvoiceTypeCode", "INV02" if credit else "INV01")
             + _el("InvoiceTypeText", "HYVITYSLASKU" if credit else "LASKU")
             + _el("OriginCode", "Original")
             + _el("InvoiceNumber", inv["number"])
             + _el("InvoiceDate", _date(inv["invoice_date"]), Format="CCYYMMDD")
             + (_el("OriginalInvoiceNumber", bundle["credited"]["number"]) if credit and bundle.get("credited") else "")
             + _el("SellerReferenceIdentifier", inv["our_reference"])
             + _el("BuyerReferenceIdentifier", inv["your_reference"])
             + _el("InvoiceTotalVatExcludedAmount", _amt(inv["total_net"]), **eur)
             + _el("InvoiceTotalVatAmount", _amt(inv["total_vat"]), **eur)
             + _el("InvoiceTotalVatIncludedAmount", _amt(inv["total_gross"]), **eur)
             + vat_xml
             + _el("InvoiceFreeText", free_text)
             + "<PaymentTermsDetails>"
             + _el("PaymentTermsFreeText", "Heti" if credit else
                   f"{(dt.date.fromisoformat(inv['due_date']) - dt.date.fromisoformat(inv['invoice_date'])).days} "
                   "pv netto")
             + _el("InvoiceDueDate", _date(inv["due_date"]), Format="CCYYMMDD")
             + ("<PaymentOverDueFineDetails>" + _el("PaymentOverDueFineFreeText", "Viivästyskorko korkolain mukaan")
                + _el("PaymentOverDueFinePercent", _qty(rate)) + "</PaymentOverDueFineDetails>" if rate else "")
             + "</PaymentTermsDetails></InvoiceDetails>")
    x.append("<PaymentStatusDetails>" + _el("PaymentStatusCode", "PAID" if credit else "NOTPAID")
             + "</PaymentStatusDetails>")
    for ln in lines:
        x.append("<InvoiceRow>"
                 + _el("ArticleName", ln["description"][:100])
                 + _el("DeliveredQuantity", _qty(ln["qty"]), QuantityUnitCode=UNIT_MAP.get(ln["unit"], ln["unit"]))
                 + _el("UnitPriceAmount", _amt(ln["unit_price"]) if D(ln["unit_price"]) == r2(ln["unit_price"])
                       else f"{D(ln['unit_price']):f}".replace(".", ","), **eur)
                 + (_el("RowDeliveryDate", _date(ln["line_date"]), Format="CCYYMMDD") if ln["line_date"] else "")
                 + (_el("RowDiscountPercent", _qty(ln["discount_pct"])) if D(ln["discount_pct"]) else "")
                 + _el("RowVatRatePercent", _qty(ln["vat_rate"]))
                 + _el("RowVatCode", VAT_CODE_MAP.get(ln["vat_code"], "S"))
                 + _el("RowVatAmount", _amt(ln["vat"]), **eur)
                 + _el("RowVatExcludedAmount", _amt(ln["net"]), **eur)
                 + _el("RowAmount", _amt(ln["gross"]), **eur)
                 + "</InvoiceRow>")
    ref = inv["rf_reference"] or inv["reference"]
    x.append("<EpiDetails><EpiIdentificationDetails>"
             + _el("EpiDate", _date(inv["invoice_date"]), Format="CCYYMMDD")
             + "<EpiReference/></EpiIdentificationDetails>"
             + "<EpiPartyDetails><EpiBfiPartyDetails>"
             + _el("EpiBfiIdentifier", comp.get("bic"), IdentificationSchemeName="BIC")
             + "</EpiBfiPartyDetails><EpiBeneficiaryPartyDetails>"
             + _el("EpiNameAddressDetails", comp.get("name"))
             + _el("EpiBei", comp.get("business_id"))
             + _el("EpiAccountID", normalisoi_iban(comp.get("iban", "")), IdentificationSchemeName="IBAN")
             + "</EpiBeneficiaryPartyDetails></EpiPartyDetails>"
             + "<EpiPaymentInstructionDetails>"
             + _el("EpiRemittanceInfoIdentifier", ref.replace(" ", ""),
                   IdentificationSchemeName="ISO" if ref.upper().startswith("RF") else "SPY")
             + _el("EpiInstructedAmount", _amt(max(D(inv["total_gross"]), D(0))), **eur)
             + _el("EpiCharge", "SHA", ChargeOption="SHA")
             + _el("EpiDateOptionDate", _date(inv["due_date"]), Format="CCYYMMDD")
             + "</EpiPaymentInstructionDetails></EpiDetails>")
    x.append("</Finvoice>")
    return "\n".join(p for p in x if p)
