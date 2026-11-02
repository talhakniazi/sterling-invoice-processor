"""Unit tests for the generic parsing rules."""
from datetime import date

import pytest

from invoice_processor.config import Config
from invoice_processor.extract import extract, find_supplier, parse_money, find_dates, supplier_key
from invoice_processor.validate import check


@pytest.mark.parametrize("text,value,cur", [
    ("£1,234.56", 1234.56, "GBP"), ("GBP 1,234.56", 1234.56, "GBP"), ("1,234.56", 1234.56, None), ("£450", 450.0, "GBP"),
    ("£-92.00", -92.0, "GBP"), ("(£92.00)", -92.0, "GBP"), ("-£92.00", -92.0, "GBP"), ("€99.90", 99.9, "EUR"), ("12.50 GBP", 12.5, "GBP"),
])
def test_money_formats(text, value, cur):
    m = parse_money(text)
    assert m and m[0][0] == value and m[0][1] == cur


@pytest.mark.parametrize("text", ["VAT @ 20%", "Qty 12", "12/08/26", "Order PO-55120", "page 2 of 2"])
def test_not_money(text):
    assert parse_money(text) == []


@pytest.mark.parametrize("text,expected", [
    ("21/07/2026", date(2026, 7, 21)), ("2026-07-21", date(2026, 7, 21)), ("21.07.2026", date(2026, 7, 21)), ("21/07/26", date(2026, 7, 21)),
    ("21 July 2026", date(2026, 7, 21)), ("21st Jul 2026", date(2026, 7, 21)), ("03-Mar-26", date(2026, 3, 3)), ("July 21, 2026", date(2026, 7, 21)),
    ("03/04/2026", date(2026, 4, 3)),  # UK day-first
])
def test_dates(text, expected):
    assert find_dates(text)[0][0] == expected


def test_us_dates_when_configured():
    assert find_dates("03/04/2026", day_first=False)[0][0] == date(2026, 3, 4)


def test_label_and_value_on_same_line_with_other_column_text():
    ex = extract("ACME SUPPLIES LTD\nInvoice # AC-991 Billed to\nIssued 4 May 2026 Sterling Office Supplies Ltd\nTotal due £120.00\nVAT £20.00\nSubtotal £100.00")
    assert (ex.invoice_number, ex.invoice_date, ex.subtotal, ex.vat, ex.total) == ("AC-991", date(2026, 5, 4), 100.0, 20.0, 120.0)


def test_header_row_with_values_underneath():
    ex = extract("Foo Ltd\nINVOICE NO. INVOICE DATE PAYMENT DUE\nX-77 01/05/26 31/05/26\nNet amount VAT Total due\n£50.00 £10.00 £60.00")
    assert (ex.invoice_number, ex.invoice_date, ex.due_date, ex.total) == ("X-77", date(2026, 5, 1), date(2026, 5, 31), 60.0)


def test_items_header_is_not_mistaken_for_totals():
    ex = extract("Foo Ltd\nInvoice No: 1\nDescription Qty Price Net\nWidgets 4 £5.00 £20.00\nGoods total 20.00\nVAT 4.00\nInvoice total 24.00")
    assert (ex.subtotal, ex.vat, ex.total) == (20.0, 4.0, 24.0)


def test_printed_mismatch_is_kept_not_hidden():
    """If the supplier's sums are wrong we must report it, never 'fix' it by inventing a VAT figure."""
    ex = extract("Foo Ltd\nInvoice No: 9\nSubtotal £175.00\nVAT @ 20% £35.00\nTotal £310.00")
    assert (ex.subtotal, ex.vat, ex.total) == (175.0, 35.0, 310.0)
    assert "TOTAL_MISMATCH" in [i.code for i in check(ex, Config(), "text", date(2026, 11, 2))]


def test_due_date_from_terms():
    ex = extract("Foo Ltd\nInvoice No: 5\nDate 2026-07-01\nTerms 30 days net\nTotal £12.00\nVAT £2.00")
    assert ex.due_date == date(2026, 7, 31) and "DUE_DATE_FROM_TERMS" in ex.info_flags


def test_net_30_is_terms_not_an_amount():
    ex = extract("Foo Ltd\nInvoice No: 5\nInvoice Date: 01/07/2026\nPayment: Net 30\nSubtotal £10.00\nVAT £2.00\nTotal £12.00")
    assert ex.subtotal == 10.0 and ex.due_date == date(2026, 7, 31)


def test_own_company_is_never_the_supplier():
    lines = ["INVOICE", "Bill To: Sterling Office Supplies Ltd", "Leeds", "Thank you", "Regards, Northfield Widgets Ltd"]
    assert find_supplier(lines, ["Sterling Office Supplies Ltd"]) == "Northfield Widgets Ltd"


def test_supplier_from_label_without_company_suffix():
    assert find_supplier(["Invoice", "From: Amara Okafor Design Invoice number 0042"], []) == "Amara Okafor Design"


def test_supplier_in_footer_only():
    lines = ["VERDANT Sales Invoice", "Invoice Number: V1", "Net total £1.00", "Verdant Office Products Limited · Registered in England"]
    assert find_supplier(lines, []) == "Verdant Office Products Limited"


def test_vat_registration_line_is_not_vat_amount():
    ex = extract("Foo Ltd\nInvoice No: 5\nSubtotal £10.00\nVAT £2.00\nTotal £12.00\nVAT Reg No: GB 123 4567 89")
    assert ex.vat == 2.0


def test_supplier_key_ignores_legal_suffix_and_case():
    assert supplier_key("CLEARLINE TONER SUPPLIES LTD") == supplier_key("Clearline Toner Supplies Limited")


def test_validation_flags():
    cfg = Config()
    ex = extract("Foo Ltd\nInvoice No: 5\nInvoice Date: 01/12/2026\nDue Date: 01/11/2026\nSubtotal £20,000.00\nVAT £4,000.00\nTotal £24,000.00")
    codes = {i.code for i in check(ex, cfg, "text", date(2026, 11, 2))}
    assert {"FUTURE_INVOICE_DATE", "DUE_BEFORE_INVOICE_DATE", "HIGH_VALUE"} <= codes


def test_unusual_vat_rate():
    ex = extract("Foo Ltd\nInvoice No: 5\nInvoice Date: 01/10/2026\nDue Date: 01/11/2026\nSubtotal £100.00\nVAT £30.00\nTotal £130.00")
    assert "VAT_RATE_UNUSUAL" in {i.code for i in check(ex, Config(), "text", date(2026, 11, 2))}


def test_foreign_currency_flagged():
    ex = extract("Foo GmbH\nInvoice No: 5\nInvoice Date: 01/10/2026\nDue Date: 01/11/2026\nSubtotal €100.00\nVAT €20.00\nTotal €120.00")
    assert ex.currency == "EUR" and "FOREIGN_CURRENCY" in {i.code for i in check(ex, Config(), "text", date(2026, 11, 2))}


def test_two_invoices_in_one_pdf_are_flagged():
    text = ("Foo Ltd\nInvoice No: A-1\nInvoice Date: 01/10/2026\nSubtotal £10.00\nVAT £2.00\nTotal £12.00\n"
            "Foo Ltd\nInvoice No: A-2\nInvoice Date: 02/10/2026\nSubtotal £20.00\nVAT £4.00\nTotal £24.00")
    ex = extract(text)
    assert "MULTIPLE_INVOICE_NUMBERS" in {i.code for i in check(ex, Config(), "text", date(2026, 11, 2))}


def test_repeated_number_on_page_two_is_not_flagged():
    ex = extract("Foo Ltd\nInvoice No: A-1\nItems...\nFoo Ltd - Invoice No: A-1 - page 2\nTotal £12.00\nVAT £2.00")
    assert "MULTIPLE_INVOICE_NUMBERS" not in ex.info_flags



# ---------------------------------------------------------------- other languages and formats
@pytest.mark.parametrize("text,value,cur", [
    ("1.234,56 €", 1234.56, "EUR"), ("€ 1.234,56", 1234.56, "EUR"), ("310,00 EURO", 310.0, "EUR"), ("40,00€", 40.0, "EUR"),
    ("₹6,54,321.00", 654321.0, "INR"), ("Rs 1,939.00", 1939.0, "INR"), ("$56,651.49", 56651.49, "USD"), ("12,50", 12.5, None),
])
def test_international_money(text, value, cur):
    m = parse_money(text)
    assert m and m[0][0] == value and m[0][1] == cur, m


@pytest.mark.parametrize("text,expected", [
    ("15 septembre 2021", date(2021, 9, 15)), ("1. Februar 2020", date(2020, 2, 1)), ("2 januari 2020", date(2020, 1, 2)),
    ("3 de octubre de 2026", date(2026, 10, 3)), ("10 ottobre 2026", date(2026, 10, 10)), ("9 de outubro de 2026", date(2026, 10, 9)),
    ("01.02.2020", date(2020, 2, 1)),
])
def test_foreign_dates(text, expected):
    assert find_dates(text)[0][0] == expected


def test_us_date_order_detected_from_document():
    ex = extract("Acme Inc.\nInvoice #: 9\nDate: 03/04/2026\nDue Date: 04/15/2026\nSubtotal $10.00\nTax $1.00\nTotal $11.00")
    assert ex.invoice_date == date(2026, 3, 4) and ex.due_date == date(2026, 4, 15) and "DATES_MONTH_FIRST" in ex.info_flags


def test_uk_date_order_kept_when_document_proves_it():
    ex = extract("Acme Ltd\nInvoice No: 9\nDate: 03/04/2026\nDue Date: 15/04/2026\nSubtotal £10.00\nVAT £2.00\nTotal £12.00")
    assert ex.invoice_date == date(2026, 4, 3)


@pytest.mark.parametrize("text,num,total,vat", [
    ("Atelier Dubois SARL\nFacture n° F-12\nDate de facture : 12/10/2026\nTotal HT 100,00 €\nTVA 20 % 20,00 €\nTotal TTC 120,00 €", "F-12", 120.0, 20.0),
    ("Müller GmbH\nRechnungsnummer: RE-7\nRechnungsdatum: 05.10.2026\nNettobetrag 100,00 €\nMwSt. 19 % 19,00 €\nGesamtbetrag 119,00 €", "RE-7", 119.0, 19.0),
    ("De Vries B.V.\nFactuurnummer: 2026-1\nFactuurdatum: 14-10-2026\nSubtotaal € 100,00\nBTW 21% € 21,00\nTotaal € 121,00", "2026-1", 121.0, 21.0),
    ("Iberia S.L.\nNº Factura: A-78\nFecha de emisión: 3 de octubre de 2026\nBase imponible 100,00 €\nIVA 21% 21,00 €\nTotal factura 121,00 €", "A-78", 121.0, 21.0),
    ("Rossi S.r.l.\nFattura n. 457\nData fattura: 21/10/2026\nImponibile 100,00 €\nIVA 22% 22,00 €\nTotale fattura 122,00 €", "457", 122.0, 22.0),
    ("Lusitana, Lda.\nFatura nº FT-112\nData de emissão: 9 de outubro de 2026\nTotal líquido 100,00 €\nIVA 23% 23,00 €\nTotal a pagar 123,00 €", "FT-112", 123.0, 23.0),
])
def test_european_labels(text, num, total, vat):
    ex = extract(text)
    assert (ex.invoice_number, ex.total, ex.vat, ex.currency) == (num, total, vat, "EUR")
    assert ex.invoice_date is not None and ex.supplier


def test_vat_number_labels_are_not_vat_amounts():
    ex = extract("Rossi S.r.l.\nPartita IVA 1234567890\nFattura n. 5\nImponibile 100,00 €\nIVA 22% 22,00 €\nTotale 122,00 €")
    assert ex.vat == 22.0


def test_language_detection():
    from invoice_processor.lang import detect_language
    assert detect_language("Rechnung Rechnungsdatum Menge Einzelpreis MwSt Zwischensumme") == "de"
    assert detect_language("Invoice date Quantity Price Total due VAT") == "en"


def test_line_items_plain_text():
    text = ("Foo Ltd\nInvoice No: 1\nDescription Qty Unit price Amount\nWidgets 4 £5.00 £20.00\nGadgets 2 £15.00 £30.00\n"
            "Subtotal £50.00\nVAT £10.00\nTotal £60.00")
    ex = extract(text)
    assert [i["amount"] for i in ex.line_items] == [20.0, 30.0] and ex.line_items_check == "net"


def test_line_items_that_do_not_add_up_are_reported():
    from invoice_processor.config import Config
    text = ("Foo Ltd\nInvoice No: 1\nInvoice Date: 01/10/2026\nDescription Qty Unit price Amount\nWidgets 4 £5.00 £20.00\n"
            "Subtotal £50.00\nVAT £10.00\nTotal £60.00")
    ex = extract(text)
    assert ex.line_items_check == "mismatch"
    assert "LINE_ITEMS_INCOMPLETE" in {i.code for i in check(ex, Config(), "text", date(2026, 11, 2))}


def test_german_e_invoice_conventions():
    text = ("Stylesheet-Version: 2.7 vom 06.05.2026\nHandelsrechnung (380) Nr. 471102 vom 15.05.2026\nLiefer- und Leistungsdatum: 14.05.2026\n"
            "Verkäufer\nNummer: 549910\nName: Handwerker GmbH\nUmsatzsteuer (S) 100,00 19% 19,00\nGesamtbetrag der Abschläge -0,00\n"
            "Rechnungssumme ohne USt. 100,00\nSteuerbetrag in EUR 19,00\nBruttosumme 119,00\nZahlbar innerhalb 30 Tagen netto bis 15.06.2026")
    ex = extract(text)
    assert ex.invoice_number == "471102" and ex.invoice_date == date(2026, 5, 15) and ex.due_date == date(2026, 6, 15)
    assert (ex.subtotal, ex.vat, ex.total) == (100.0, 19.0, 119.0) and ex.supplier == "Handwerker GmbH"


def test_vat_above_27_percent_of_net_is_not_chosen():
    ex = extract("Foo GmbH\nRechnungsnummer: 1\nUmsatzsteuer (Z) 3.273,00 0% 0,00\nRechnungssumme ohne USt. 3.273,00\nBruttosumme 3.273,00")
    assert ex.vat == 0.0 and ex.total == 3273.0


def test_calculated_total_needs_review():
    from invoice_processor.config import Config
    ex = extract("Foo Ltd\nInvoice No: 5\nInvoice Date: 01/10/2026\nDue Date: 01/11/2026\nSubtotal £100.00\nVAT £20.00")
    assert ex.total == 120.0
    assert "TOTAL_CALCULATED" in {i.code for i in check(ex, Config(), "text", date(2026, 11, 2)) if i.severity == "review"}


def test_dark_border_is_cropped():
    from PIL import Image
    from invoice_processor.reader import _crop_dark_border
    im = Image.new("L", (1000, 800), 0); im.paste(Image.new("L", (600, 800), 255), (200, 0))
    assert _crop_dark_border(im).size == (600, 800)
