"""Multilingual vocabulary: English, French, German, Dutch, Spanish, Italian, Portuguese.

Everything language-specific lives here so new wording can be added in one place.
"""
from __future__ import annotations

import re

# Month names -> month number (lower-case, accents included). English handled by dateutil.
MONTHS = {
    # French
    "janvier": 1, "février": 2, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6, "juillet": 7, "août": 8, "aout": 8,
    "septembre": 9, "octobre": 10, "novembre": 11, "décembre": 12, "decembre": 12, "janv": 1, "févr": 2, "avr": 4, "juil": 7, "sept": 9, "déc": 12,
    # German
    "januar": 1, "jänner": 1, "februar": 2, "märz": 3, "maerz": 3, "april": 4, "juni": 6, "juli": 7, "oktober": 10, "dezember": 12, "okt": 10, "dez": 12, "mär": 3,
    # Dutch
    "januari": 1, "februari": 2, "maart": 3, "mei": 5, "augustus": 8, "mrt": 3,
    # Spanish
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6, "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9,
    "octubre": 10, "noviembre": 11, "diciembre": 12, "ene": 1, "dic": 12,
    # Italian
    "gennaio": 1, "febbraio": 2, "aprile": 4, "maggio": 5, "giugno": 6, "luglio": 7, "settembre": 9, "ottobre": 10, "dicembre": 12, "gen": 1, "giu": 6, "lug": 7, "ott": 10,
    # Portuguese
    "janeiro": 1, "fevereiro": 2, "março": 3, "marco": 3, "maio": 5, "junho": 6, "julho": 7, "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12, "fev": 2, "set": 9, "out": 10,
}
EN_MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]

# Extra label regexes per field: (field, regex, strength). English ones live in extract.py.
LABELS = [
    # ---- invoice number
    ("invoice_number", r"\b(?:num[ée]ro\s*(?:de\s*)?(?:la\s*)?facture|n[°ºo]\.?\s*(?:de\s*)?facture|facture\s*n[°ºo]\.?)\s*:?", 3),
    ("invoice_number", r"\b(?:rechnungs?[\s\-]*(?:nummer|nr\.?|no\.?)|rechnung\s*nr\.?|beleg[\s\-]*(?:nummer|nr\.?))\s*:?", 3),
    ("invoice_number", r"\b\w*rechnung\b[^\n:]{0,20}?\bnr\.?\s*:?", 2),                     # "Handelsrechnung (380) Nr. 471102" 
    ("invoice_number", r"\b(?:factuur[\s\-]*(?:nummer|nr\.?|no\.?)|factuurnr\.?)\s*:?", 3),
    ("invoice_number", r"\b(?:n[uú]mero\s*(?:de\s*)?(?:la\s*)?factura|n[°º]\.?\s*(?:de\s*)?factura|factura\s*n[°ºo]\.?)\s*:?", 3),
    ("invoice_number", r"\b(?:numero\s*(?:della\s*)?(?:fattura|documento)|n\.?\s*(?:fattura|documento)|fattura\s*n[°º.]?)\s*:?", 3),
    ("invoice_number", r"\b(?:n[uú]mero\s*(?:da\s*)?fatura|fatura\s*n[°º.]?|n[°º.]+\s*(?:da\s*)?fatura)\s*:?", 3),
    ("invoice_number", r"^\s*(?:facture|rechnung|factuur|factura|fattura|fatura)\s*(?::|(?=\s+#?[A-Z0-9][\w/\-.]*\d))", 2),
    ("invoice_number", r"^\s*n[uú]mero\b\s*:?", 1),
    # ---- invoice date
    ("invoice_date", r"\b(?:date\s*(?:de\s*)?(?:la\s*)?(?:facture|facturation|d['’]?\s*[ée]mission)|date\s*d['’]\s*[ée]mission)\b\s*:?", 3),
    ("invoice_date", r"\b(?:rechnungs?datum|belegdatum|ausstellungsdatum)\b\s*:?", 3),
    ("invoice_date", r"(?<=\d)\s+vom\b\s*:?", 2),                                                # "Nr. 471102 vom 15.05.2026"
    ("invoice_date", r"\b(?:liefer[\s\-]*(?:und\s*)?leistungsdatum|leistungsdatum|lieferdatum)\b\s*:?", 1),   # service date: weak
    ("invoice_date", r"\b(?:factuurdatum)\b\s*:?", 3),
    ("invoice_date", r"\b(?:fecha\s*(?:de\s*)?(?:emisi[oó]n|(?:la\s*)?factura|expedici[oó]n))\b\s*:?", 3),
    ("invoice_date", r"\b(?:data\s*(?:della\s*)?(?:fattura|documento|emissione))\b\s*:?", 3),
    ("invoice_date", r"\b(?:data\s*(?:da|de)\s*(?:emiss[aã]o|fatura))\b\s*:?", 3),
    ("invoice_date", r"\b(?:datum|fecha)\b\s*:?", 1),
    ("invoice_date", r"\bdata\b\s*:", 1),
    # ---- due date
    ("due_date", r"\b(?:date\s*d['’]\s*[ée]ch[ée]ance|[ée]ch[ée]ance(?:\s*(?:le\s*)?(?:du\s*)?paiement)?|[àa]\s*payer\s*avant\s*le|date\s*limite\s*de\s*paiement)\b\s*:?", 3),
    ("due_date", r"\b(?:f[äa]lligkeits?datum|f[äa]llig\s*(?:am|bis)?|zahlbar\s*bis|zahlungsziel)\b\s*:?", 3),
    ("due_date", r"\bzahlbar\b[^\n]{0,40}?\bbis\b(?:\s*zum)?\s*:?", 3),
    ("due_date", r"\b(?:f[äa]lligkeit|f[äa]llig)\b\s*:?", 2),
    ("due_date", r"\b(?:[üu]berweisen|zahlen|begleichen)\b[^\n]{0,40}?\bbis\s*(?:zum|sp[äa]testens)?\b\s*:?", 2),                                       # "Zahlbar innerhalb 30 Tagen netto bis 15.06." 
    ("due_date", r"\b(?:vervaldatum|vervaldag|te\s*betalen\s*v[oó][oó]r|uiterste\s*betaaldatum)\b\s*:?", 3),
    ("due_date", r"\b(?:fecha\s*(?:de\s*)?vencimiento|vencimiento|fecha\s*l[ií]mite\s*de\s*pago)\b\s*:?", 3),
    ("due_date", r"\b(?:data\s*(?:di\s*)?scadenza|scadenza)\b\s*:?", 3),
    ("due_date", r"\b(?:data\s*(?:de\s*)?vencimento|vencimento)\b\s*:?", 3),
    # ---- subtotal
    ("subtotal", r"\b(?:sous[\s\-]*total(?:\s*ht)?|total\s*h\.?t\.?|montant\s*h\.?t\.?|total\s*hors\s*taxes?)\b\s*:?", 3),
    ("subtotal", r"\b(?:zwischensumme|nettobetrag|nettosumme|gesamt\s*netto|summe\s*netto|netto\s*summe|rechnungsbetrag\s*netto|"
                 r"rechnungssumme\s*ohne\s*(?:ust|mwst|umsatzsteuer)\.?|summe\s*(?:der\s*)?nettobetr[äa]ge|gesamtsumme\s*netto|warenwert)\b\s*:?", 3),
    ("subtotal", r"\b(?:subtotaal|totaal\s*excl\.?\s*(?:btw)?|netto\s*bedrag|bedrag\s*excl\.?\s*btw)\b\s*:?", 3),
    ("subtotal", r"\b(?:base\s*imponible|importe\s*neto|subtotal)\b\s*:?", 3),
    ("subtotal", r"\b(?:subtotale|imponibile|totale\s*imponibile)\b\s*:?", 3),
    ("subtotal", r"\b(?:total\s*l[ií]quido|valor\s*l[ií]quido|base\s*tribut[aá]vel|total\s*sem\s*iva)\b\s*:?", 3),
    ("subtotal", r"\bnetto\b\s*:?", 1),
    # ---- VAT
    ("vat", r"\b(?:(?:montant|total)\s*)?t\.?v\.?a\.?(?:\s*\d{1,2}(?:[.,]\d{1,2})?\s*%)?\s*:?", 2),
    ("vat", r"\b(?:mwst|mehrwertsteuer|ust|umsatzsteuer|steuer)\.?(?:\s*\d{1,2}(?:[.,]\d{1,2})?\s*%)?\s*:?", 2),
    ("vat", r"\b(?:steuerbetrag|umsatzsteuerbetrag|(?:ust|mwst)\.?[\s\-]*betrag|summe\s*(?:ust|mwst|umsatzsteuer))\b\s*:?", 3),
    ("vat", r"\b(?:b\.?t\.?w\.?|omzetbelasting)(?:\s*\d{1,2}(?:[.,]\d{1,2})?\s*%)?\s*:?", 2),
    ("vat", r"\b(?:(?:total|importe|imposta|imposto|valor)\s*)?(?:del?\s*)?i\.?v\.?a\.?(?:\s*\d{1,2}(?:[.,]\d{1,2})?\s*%)?\s*:?", 2),
    ("vat", r"\b(?:igst|cgst|sgst|gst)\b\s*:?", 1),
    # ---- total
    ("total", r"\b(?:total\s*t\.?t\.?c\.?|montant(?:e)?\s*t\.?t\.?c\.?|net\s*[àa]\s*payer|total\s*[àa]\s*payer|montant\s*total|montant\s*d[uû])\b\s*:?", 3),
    ("total", r"\b(?:gesamtbetrag|rechnungsbetrag|endbetrag|gesamtsumme|zu\s*zahlen(?:der\s*betrag)?|summe\s*brutto|bruttobetrag|bruttosumme|"
              r"rechnungssumme(?:\s*(?:inkl|mit)\.?\s*(?:ust|mwst))?|gesamtsumme\s*brutto)\b\s*:?", 3),
    ("total", r"\b(?:zahlbetrag|zahlungsbetrag)\b\s*:?", 2),
    ("total", r"\b(?:totaal(?:bedrag)?(?:\s*incl\.?\s*(?:btw)?)?|te\s*betalen)\b\s*:?", 3),
    ("total", r"\b(?:total\s*a\s*pagar|importe\s*total|total\s*factura|total\s*con\s*iva)\b\s*:?", 3),
    ("total", r"\b(?:totale(?:\s*(?:fattura|documento|da\s*pagare|ivato))?)\b\s*:?", 3),
    ("total", r"\b(?:valor\s*(?:total|a\s*pagar)|total\s*(?:da\s*fatura|com\s*iva))\b\s*:?", 3),
]

# words that mark the BUYER block (so it is not taken as the supplier)
BUYER = (r"invoice\s*for|factur[ée]\s*[àa]|adresse\s*de\s*facturation|destinataire|client|"
         r"rechnungs(?:empf[äa]nger|adresse)|kunde|an\s*:|factuuradres|klant|aan\s*:|"
         r"facturar\s*a|cliente|nombre\s*:|fatturare\s*a|intestatario|exmo|destinat[aá]rio")

COMPANY_SUFFIXES = r"ug|gbr|e\.k|e\.v|a\.g|kgaa|se|mbh|ltd|limited|llp|plc|inc|llc|gmbh|co|company|corporation|corp|group|pty|pvt|private|bv|b\.v|nv|n\.v|sarl|s\.a\.r\.l|sas|s\.a\.s|sa|s\.a|srl|s\.r\.l|spa|s\.p\.a|sl|s\.l|ag|kg|ohg|lda|ltda|oy|ab|as|aps"

TITLE_WORDS = r"facture|rechnung|factuur|factura|fattura|fatura|nota\s*de\s*cr[eé]dito|avoir|gutschrift|creditnota"

# Column headings in item tables
COLS = {
    "desc": r"description|descripci[oó]n|descri[çc][aã]o|descrizione|d[ée]signation|beschreibung|bezeichnung|omschrijving|produkt|product|producto|prodotto|produto|items?|details|particulars|article|artikel|servicio|leistung|service",
    "qty": r"qty|quantity|quantit[ée]|qt[ée]|quantit[àa]|q\.?\s*t[àa]|qt\.?|menge|anzahl|aantal|cantidad|cant\.?|quantidade|quant\.?|hours|hrs|units",
    "price": r"unit\s*price|price|rate|each|unit\s*cost|prix\s*unitaire|prix\s*u\.?|einzelpreis|e-?preis|nettopreis|bruttopreis|st[üu]ckpreis|preis|prijs|stukprijs|precio\s*unitario|precio|prezzo(?:\s*unitario)?|pre[çc]o\s*unit[aá]rio|pre[çc]o",
    "amount": r"line\s*total|amount|total|net|value|montant(?:\s*ht)?|prix\s*total|total\s*ht|nettobetrag|gesamtbetrag|betrag|gesamtpreis|gesamt|bedrag|totaal|importe|importo|valor|gross",
    "tax": r"vat\s*%|vat|tax|steuersatz|ust\.?\s*%|mwst\.?\s*%|tva|mwst|ust|btw|b\.?t\.?w\.?|iva|igst|cgst|sgst|gst",
    "code": r"pos\.?|item\s*code|code|sku|ref(?:erence|erencia)?|codice|c[oó]digo|artikel[\s\-]*nr\.?|art\.?\s*nr\.?|s\.?\s*no\.?|hsn(?:/sac)?",
    "date": r"date|datum|fecha|data",
    "unit": r"um|uom|unit|unit[ée]|einheit|eenheid|unidad|unit[àa]",
    "discount": r"disc(?:ount)?\.?\s*%?|remise|rabatt|korting|descuento|sconto|desconto",
}
# Preference when several amount-like columns exist (first match wins)
AMOUNT_PREF = [r"line\s*total", r"amount|montant|betrag|bedrag|importe|importo|valor", r"prix\s*total|gesamtpreis|total", r"net", r"value", r"gross"]

STOP_WORDS = r"thank\s*you|merci|danke|vielen\s*dank|bedankt|gracias|grazie|obrigado|bank\s*details|payment\s*details|remit\s*to|notes?\s*:"

DAYS_WORDS = r"days|jours|tage|dagen|d[ií]as|giorni|gg|dias"

# A few very common words per language, for language detection
DETECT = {
    "fr": r"\b(?:facture|montant|tva|total\s*ttc|date\s*d['’]|[ée]ch[ée]ance|merci|adresse|quantit[ée]|prix)\b",
    "de": r"\b(?:rechnung|betrag|mwst|ust|datum|menge|einzelpreis|steuer|zwischensumme|vielen\s*dank|zahlbar)\b",
    "nl": r"\b(?:factuur|bedrag|btw|totaal|aantal|prijs|omschrijving|vervaldatum|bedankt)\b",
    "es": r"\b(?:factura|importe|iva|fecha|cantidad|precio|descripci[oó]n|gracias|vencimiento)\b",
    "it": r"\b(?:fattura|importo|imposta|data|quantit[àa]|prezzo|descrizione|grazie|scadenza|totale)\b",
    "pt": r"\b(?:fatura|valor|data\s*da|quantidade|pre[çc]o|descri[çc][aã]o|obrigado|vencimento|contribuinte)\b",
    "en": r"\b(?:invoice|amount|total|date|quantity|price|description|thank|due|vat|tax)\b",
}
TESSERACT_CODE = {"en": "eng", "fr": "fra", "de": "deu", "nl": "nld", "es": "spa", "it": "ita", "pt": "por"}
EURO_LANGS = {"fr", "de", "nl", "es", "it", "pt"}


def detect_language(text: str) -> str:
    t = text.lower()
    scores = {k: len(re.findall(rx, t)) for k, rx in DETECT.items()}
    best = max(scores, key=scores.get)
    if best != "en" and scores[best] >= 3 and scores[best] > scores["en"] * 0.8:
        return best
    return "en"
