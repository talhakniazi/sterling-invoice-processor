"""Business checks on an extracted invoice. Each issue has a code, a severity and a plain-English message."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

REVIEW = "review"   # sends the invoice to Needs Review
INFO = "info"       # recorded, but the invoice is still processed normally


@dataclass
class Issue:
    code: str
    severity: str
    message: str


def gbp(v, cur="GBP"):
    sym = {"GBP": "£", "EUR": "€", "USD": "$"}.get(cur, cur + " ")
    return f"-{sym}{abs(v):,.2f}" if v < 0 else f"{sym}{v:,.2f}"


def check(ex, cfg, method: str, today: date | None = None, doc_notes=()) -> list[Issue]:
    today = today or date.today()
    out: list[Issue] = []
    add = lambda c, s, m: out.append(Issue(c, s, m))

    for f, label in [("supplier", "supplier name"), ("invoice_number", "invoice number"), ("invoice_date", "invoice date"),
                     ("due_date", "due date"), ("total", "invoice total")]:
        if getattr(ex, f) in (None, ""):
            add(f"MISSING_{f.upper()}", REVIEW, f"Could not find the {label}.")
    if ex.total is not None and ex.vat is None:
        add("MISSING_VAT", REVIEW, "Could not find the VAT amount.")

    if None not in (ex.subtotal, ex.vat, ex.total):
        diff = round(ex.subtotal + ex.vat - ex.total, 2)
        if abs(diff) > cfg.rounding_tolerance:
            add("TOTAL_MISMATCH", REVIEW, f"Net {gbp(ex.subtotal, ex.currency)} + VAT {gbp(ex.vat, ex.currency)} = {gbp(ex.subtotal + ex.vat, ex.currency)}, "
                                          f"but the invoice total is {gbp(ex.total, ex.currency)} (difference {gbp(abs(diff), ex.currency)}).")
        if ex.subtotal and abs(ex.subtotal) > 0.009:
            rate = ex.vat / ex.subtotal * 100
            top = max(cfg.standard_vat_rates) if ex.currency == "GBP" else 27.0   # other countries: up to 27% (Hungary)
            if rate > top + 0.5 or rate < -0.5:
                add("VAT_RATE_UNUSUAL", REVIEW, f"VAT is {rate:.1f}% of the net amount - higher than the {top:g}% standard rate.")
            elif ex.currency == "GBP" and not any(abs(rate - r) <= 0.3 for r in cfg.standard_vat_rates):
                add("MIXED_VAT_RATES", INFO, f"Effective VAT rate is {rate:.1f}% - the invoice probably mixes VAT rates.")

    if ex.total is not None:
        if ex.is_credit_note or ex.total < 0:
            add("CREDIT_NOTE", REVIEW, "This is a credit note (negative amount). Check it is matched to the original invoice.")
        elif ex.total == 0:
            add("ZERO_TOTAL", REVIEW, "The invoice total is zero.")
        if abs(ex.total) > cfg.high_value_threshold:
            add("HIGH_VALUE", REVIEW, f"Total {gbp(ex.total, ex.currency)} is above the {gbp(cfg.high_value_threshold)} approval threshold.")
    if ex.currency != "GBP":
        add("FOREIGN_CURRENCY", REVIEW, f"Invoice is in {ex.currency}, not GBP. Amounts are shown in {ex.currency} and are NOT converted.")

    if ex.invoice_date:
        if ex.invoice_date > today + timedelta(days=1):
            add("FUTURE_INVOICE_DATE", REVIEW, f"Invoice date {ex.invoice_date:%d/%m/%Y} is in the future.")
        elif (today - ex.invoice_date).days > cfg.old_invoice_days:
            add("OLD_INVOICE", REVIEW, f"Invoice date {ex.invoice_date:%d/%m/%Y} is more than {cfg.old_invoice_days} days ago.")
    if ex.invoice_date and ex.due_date:
        if ex.due_date < ex.invoice_date:
            add("DUE_BEFORE_INVOICE_DATE", REVIEW, f"Due date {ex.due_date:%d/%m/%Y} is before the invoice date {ex.invoice_date:%d/%m/%Y}.")
        elif (ex.due_date - ex.invoice_date).days > 180:
            add("LONG_PAYMENT_TERMS", REVIEW, f"Due date is {(ex.due_date - ex.invoice_date).days} days after the invoice date.")

    if method == "ocr":
        add("OCR_USED", INFO, "Scanned document - text was read with OCR.")
        if ex.invoice_number and re.search(r"(?<=\d)[OIlSB]|[OIlSB](?=\d)", ex.invoice_number):
            add("OCR_CHECK_REFERENCE", REVIEW, f"Invoice number '{ex.invoice_number}' was read by OCR and contains a character that is easily confused "
                                               f"(O/0, I/1, S/5, B/8). Please check it against the scan.")
    for code in ex.info_flags:
        if code == "DUE_DATE_FROM_TERMS":
            add(code, INFO, next((n for n in ex.notes if "terms" in n), "Due date calculated from payment terms."))
        elif code == "MULTIPLE_INVOICE_NUMBERS":
            add(code, REVIEW, next(n for n in ex.notes if "more than one invoice number" in n))
        elif code == "DATES_MONTH_FIRST":
            add(code, INFO, next(n for n in ex.notes if "month/day" in n))
        elif code == "VALUES_DERIVED":
            total_derived = any(n.startswith("Total not printed") for n in ex.notes)
            add("TOTAL_CALCULATED" if total_derived else code, REVIEW if total_derived else INFO,
                " ".join(n for n in ex.notes if "calculated as" in n) + (" The amount to pay could not be read - check it against the document." if total_derived else ""))
    if ex.line_items_check == "mismatch":
        tl = round(sum(i["amount"] for i in ex.line_items), 2)
        add("LINE_ITEMS_INCOMPLETE", INFO, f"The {len(ex.line_items)} line item(s) read add up to {gbp(tl, ex.currency)}, not the net or total - "
                                           f"some lines may not have been read. The invoice totals are not affected.")
    for n in ex.notes:
        if n.startswith("No currency is printed"):
            add("CURRENCY_ASSUMED", INFO, n)
    for n in doc_notes or ():
        if n.startswith("LOW_RESOLUTION: "):
            add("LOW_RESOLUTION", REVIEW, n[len("LOW_RESOLUTION: "):])
        else:
            add("OCR_LANGUAGE_PACK_MISSING", INFO, n)
    return out
