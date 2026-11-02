"""Generic invoice field extraction - no supplier templates.

How it works
------------
1. The document arrives as lines whose words keep their page positions
   (plain text also works). Labels such as "Invoice No", "Tax point",
   "Rechnungsnummer", "Montant TTC" are found with a multilingual vocabulary
   (English here, other languages in lang.py).
2. Each label owns the text up to the next label on the same line; the first
   value of the right kind (date / money / reference) becomes a candidate.
   Labels with nothing after them take the value printed UNDER them, matched
   by column position (table-style headers), or at the start of the next line.
3. Money candidates are reconciled: the subtotal / VAT / total combination that
   satisfies Net + VAT = Total wins, weighted by label strength and position.
   A printed amount is never dropped to make the sums balance.
4. Dates: the day/month order is worked out from the document itself
   (13/02 must be day-first, 02/13 month-first), else US-dollar documents are
   read month-first, else the configured default (UK) applies.
5. Supplier: explicit From/Supplier label > company-suffix names (Ltd, GmbH,
   B.V., S.A. ...) not in a "Bill to / Client / Kunde" block > document heading.
6. Line items: the item table is found by its column headings, rows are read
   by column, and their sum is checked against the net or gross total.
"""
from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from dateutil import parser as dparser

from . import lang as L
from .layout import Line, lines_from_text

# ------------------------------------------------------------------ value patterns
EN_MONTHS = r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
FOREIGN_MONTHS = "|".join(sorted((re.escape(m) for m in L.MONTHS), key=len, reverse=True))
MONTHS = f"(?:{FOREIGN_MONTHS}|{EN_MONTHS})"
DATE_RE = re.compile(
    r"(?<![\w/.\-])(?:"
    r"\d{4}-\d{1,2}-\d{1,2}"
    r"|\d{1,2}[/.\-]\d{1,2}[/.\-](?:\d{4}|\d{2})"
    r"|\d{1,2}(?:st|nd|rd|th|er|º|°)?\.?[\s\-]+(?:de\s+)?" + MONTHS + r"\.?,?[\s\-]+(?:de\s+)?(?:\d{4}|\d{2})"
    r"|" + MONTHS + r"\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}"
    r")(?![\w/])", re.I)
NUMERIC_DATE = re.compile(r"(?<![\d/.\-])(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4}|\d{2})(?![\d/])")
CURRENCY = r"(?:£|€|\$|₹|GBP|EUROS?|EUR|USD|INR|CHF|Rs\.?)"
NUM = (r"\d{1,2}(?:,\d{2})+,\d{3}(?:\.\d{1,2})?"     # Indian 6,54,321.00
       r"|\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?"          # 1,234.56
       r"|\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?"          # 1.234,56
       r"|\d+,\d{1,2}|\d+\.\d{1,2}|\d+")
MONEY_RE = re.compile(
    r"(?<![\w.,/\-])(?P<lp>\()?\s*(?P<s1>-)?\s*(?P<cur>" + CURRENCY + r")?\s*(?P<s2>-)?\s*"
    r"(?P<num>" + NUM + r")(?P<rp>\))?\s*(?P<cur2>" + CURRENCY + r")?"
    r"(?!\s?%)(?!\d)(?![.,]\d)(?![/\-]\d)", re.I)
REF_RE = re.compile(r"(?<![\w])#?([A-Z0-9][A-Z0-9\-/_.]{0,30}[A-Z0-9]|[0-9])(?![\w])", re.I)
CUR_CODE = {"£": "GBP", "€": "EUR", "$": "USD", "₹": "INR", "EURO": "EUR", "EUROS": "EUR", "RS": "INR", "RS.": "INR"}

# ------------------------------------------------------------------ label vocabulary
_EN = [
    ("invoice_number", r"\binv(?:oice)?\.?\s*(?:no\b\.?|number|num\b\.?|#|ref(?:erence)?\b\.?|id\b)\s*[:#.]?", 3),
    ("invoice_number", r"\b(?:document|doc|bill|receipt)\s*(?:#\s*)?(?:no\b\.?|number|#)\s*:?", 2),
    ("invoice_number", r"\binvoice\s*:(?!\s*(?:date|to)\b)", 2),
    ("invoice_number", r"^\s*(?:number|no\.)\s*:?", 1),
    ("invoice_number", r"^\s*(?:tax\s*|sales\s*)?invoice\s+(?=#?[A-Z0-9][\w/\-.]*\d)", 2),
    ("invoice_number", r"^\s*#\s*(?=\S*\d)", 1),
    ("invoice_number", r"\b(?:number|n[uú]mero|nummer|num[ée]ro)\s*:", 1),   # "NUMBER: 654123" anywhere - see NUMBER_HEAD exclusions
    ("invoice_date", r"\b(?:invoice|issue|billing|document)\s*date\b\s*:?", 3),
    ("invoice_date", r"\bdate\s*(?:of\s*)?(?:issue|invoice)\b\s*:?", 3),
    ("invoice_date", r"\btax\s*point(?:\s*date)?\b\s*:?", 3),
    ("invoice_date", r"\bissued(?:\s*on)?\b\s*:?", 2),
    ("invoice_date", r"\bdated?\b\s*:?", 1),
    ("due_date", r"\b(?:invoice\s*)?(?:payment\s*)?due\s*date\b\s*:?", 3),
    ("due_date", r"\bpayment\s*due\b(?:\s*(?:by|on))?\s*:?", 3),
    ("due_date", r"\b(?:due|pay(?:able)?)\s*(?:by|on|before)\b\s*:?", 3),
    ("due_date", r"\bdue\b\s*:", 2),
    ("subtotal", r"\bsub[\s\-]*total\b(?:\s*\(?(?:ex(?:cl)?\.?|excluding)\s*vat\)?)?\s*:?", 3),
    ("subtotal", r"\b(?:total\s*net|net\s*(?:amount|total|value|invoice\s*value))\b\s*:?", 3),
    ("subtotal", r"\b(?:goods|invoice)\s*(?:total|value)\s*(?:\(?(?:ex(?:cl)?\.?|excluding)\s*vat\)?)\s*:?", 3),
    ("subtotal", r"\bgoods\s*(?:total|value)\b\s*:?", 3),
    ("subtotal", r"\b(?:total\s*)?amount\s*\(?(?:ex(?:cl)?\.?|excluding|before)\s*(?:vat|tax)\)?\s*:?", 3),
    ("subtotal", r"\btotal\s*\(?(?:ex(?:cl)?\.?|excluding|before)\s*(?:vat|tax)\)?\s*:?", 3),
    ("subtotal", r"\bnet\b\s*:?", 1),
    ("subtotal", r"^\s*amount\b\s*:?", 1),
    ("vat", r"\b(?:total\s*)?(?:vat|(?:sales\s*)?tax)(?:\s*amount)?(?:\s*(?:@|at)?\s*\(?\d{1,2}(?:\.\d{1,2})?\s*%\)?)?(?:\s*on\s*\S+\s*[\d.,]+)?\s*:?", 2),
    ("total", r"\b(?:grand|invoice)\s*total\b\s*:?", 3),
    ("total", r"\btotal\s*(?:due|payable|amount(?:\s*due)?|to\s*pay|for\s*this\s*invoice|(?:gbp|eur|usd)|\(?inc(?:l|luding)?\.?\s*(?:vat|tax)\)?)\b\s*:?", 3),
    ("total", r"\b(?:amount|balance)\s*(?:due|payable|to\s*pay|owing)\b\s*:?", 3),
    ("total", r"\btotal\b\s*:?", 2),
    ("total", r"\bplease\s*pay\b\s*:?", 2),
]
LABELS = [(f, re.compile(rx, re.I), s) for f, rx, s in _EN + L.LABELS]
DATE_FIELDS = {"invoice_date", "due_date"}
MONEY_FIELDS = {"subtotal", "vat", "total"}

BUYER_LABEL = re.compile(r"\b(?:bill(?:ed)?\s*to|invoice\s*to|sold\s*to|ship(?:ped)?\s*to|deliver(?:y|ed)?\s*(?:to|address)|customer\b|account\s*name|"
                         + L.BUYER + r"|^to\b\s*:?)", re.I)
SUPPLIER_LABEL = re.compile(r"\b(?:from|supplier|vendor|seller|bill(?:ed)?\s*from|payee|fournisseur|vendeur|lieferant|verk[äa]ufer|rechnungssteller|"
                            r"leistungserbringer|leverancier|verkoper|proveedor|vendedor|fornitore|cedente|fornecedor)\b\s*:?\s*", re.I)
COMPANY_SUFFIX = re.compile(r"^(?:" + L.COMPANY_SUFFIXES + r")\.?,?$", re.I)
WAIT_SUFFIX = {"co", "company", "group", "pvt", "private"}
TITLE_WORDS = re.compile(r"^(?:tax\s*|sales\s*|vat\s*|proforma\s*|commercial\s*)?(?:invoice|receipt|bill|statement|credit\s*note|" + L.TITLE_WORDS + r")\b", re.I)
POSTCODE = re.compile(r"\b[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}\b", re.I)
VAT_ID_TAIL = re.compile(r"[\s.\-]*(?:reg|registration|no\b|number|nummer|nr\b|id\b|idnr|registered|intracom|[%]\s*$)", re.I)
VAT_ID_HEAD = re.compile(r"(?:excl?\.?|incl?\.?|ex|not|plus|partita|n[°º]\.?|no\.|numero|n[uú]mero|ust-|sans|hors|zzgl\.?|inkl\.?|excl\.?)\s*$", re.I)
NUMBER_HEAD = re.compile(r"(?:account|customer|vat|tax|po|p\.o\.|order|phone|tel|fax|company|registration|reg|client|kunden|debiteur|debiteuren|"
                         r"contribuinte|cliente|btw|iban|sort|card|mobile|reference|ref|id|telefon|steuer|partita|purchase|campaign|internal|"
                         r"account\s*no|document|kunde|customer\s*id)[\s.:/]*$", re.I)
COL_RE = {k: re.compile(r"(?<![\w])(?:" + v + r")(?![\w])", re.I) for k, v in L.COLS.items()}


@dataclass
class Candidate:
    field: str
    value: object
    strength: int
    line: int


@dataclass
class Extraction:
    supplier: str | None = None
    invoice_number: str | None = None
    invoice_date: date | None = None
    due_date: date | None = None
    subtotal: float | None = None
    vat: float | None = None
    total: float | None = None
    currency: str = "GBP"
    language: str = "en"
    notes: list[str] = field(default_factory=list)
    info_flags: list[str] = field(default_factory=list)
    is_credit_note: bool = False
    line_items: list[dict] | None = None
    line_items_check: str | None = None      # "net", "gross", "mismatch" or None (no table found)


# ------------------------------------------------------------------ value parsers
def _to_float(num: str):
    """Return (value, has_decimals) understanding 1,234.56 / 1.234,56 / 6,54,321.00 / 12,50."""
    if "," in num and "." in num:
        dec = "," if num.rfind(",") > num.rfind(".") else "."
    elif "," in num:
        dec = "," if re.search(r",\d{1,2}$", num) else None
    elif "." in num:
        dec = "." if re.search(r"\.\d{1,2}$", num) else None
    else:
        dec = None
    thou = {",": ".", ".": ",", None: ",."}[dec]
    s = num
    for t in thou: s = s.replace(t, "")
    if dec == ",": s = s.replace(",", ".")
    return float(s), dec is not None


def parse_money(s: str):
    """Return list of (value, currency, start, end) found in s."""
    out = []
    for m in MONEY_RE.finditer(s):
        num = m.group("num"); cur = m.group("cur") or m.group("cur2")
        v, has_dec = _to_float(num)
        if not cur and not has_dec and not re.fullmatch(r"\d{1,3}(?:,\d{3})+", num):
            continue  # bare integers are quantities / references, not money
        if m.group("s1") or m.group("s2") or (m.group("lp") and m.group("rp")):
            v = -v
        code = CUR_CODE.get(cur.upper() if cur else "", CUR_CODE.get(cur, (cur or "").upper())) if cur else None
        out.append((round(v, 2), code, m.start(), m.end()))
    return out


def parse_date(s: str, day_first: bool = True):
    s = s.strip().lower()
    s = re.sub(r"\bde\b", " ", s)
    s = re.sub(r"(\d)(st|nd|rd|th|er|º|°)\b", r"\1", s)
    s = re.sub(r"(\d)\.\s+(?=[a-zäéûç])", r"\1 ", s)        # "1. Februar"
    for word in sorted(L.MONTHS, key=len, reverse=True):
        if re.search(r"(?<![a-zäéûçáíóú])" + re.escape(word) + r"\.?(?![a-zäéûçáíóú])", s):
            s = re.sub(r"(?<![a-zäéûçáíóú])" + re.escape(word) + r"\.?(?![a-zäéûçáíóú])", L.EN_MONTHS[L.MONTHS[word] - 1], s, count=1)
            break
    s = re.sub(r"\s+", " ", s).strip()
    try:
        if re.match(r"^\d{4}-\d{1,2}-\d{1,2}$", s):
            d = dparser.parse(s, yearfirst=True, dayfirst=False)
        else:
            d = dparser.parse(s, dayfirst=day_first, fuzzy=False)
    except (ValueError, OverflowError):
        return None
    return d.date() if 2000 <= d.year <= 2100 else None


def find_dates(s: str, day_first=True):
    out = []
    for m in DATE_RE.finditer(s):
        d = parse_date(m.group(0), day_first)
        if d: out.append((d, m.start(), m.end()))
    return out


def find_refs(s: str):
    out = []
    taken = [(a, b) for _, a, b in find_dates(s)] + [(a, b) for _, _, a, b in parse_money(s)]
    for m in REF_RE.finditer(s):
        tok = m.group(1)
        if not re.search(r"\d", tok):
            continue
        if any(a <= m.start(1) < b for a, b in taken) and not re.search(r"[A-Za-z]", tok):
            continue
        if re.fullmatch(r"\d{1,2}", tok):
            continue
        out.append((tok.rstrip("./-"), m.start(), m.end()))
    return out


def first_value(fieldname: str, text: str, day_first: bool):
    if fieldname in DATE_FIELDS:
        d = find_dates(text, day_first); return (d[0][0], d[0][1]) if d else None
    if fieldname in MONEY_FIELDS:
        m = parse_money(text); return ((m[0][0], m[0][1]), m[0][2]) if m else None
    r = find_refs(text); return (r[0][0], r[0][1]) if r else None


def _value_tokens(line: str, day_first: bool):
    """Tokenise a line made only of values (plain-text header-row layouts). None if the line contains words."""
    spans = [("date", v, a, b) for v, a, b in find_dates(line, day_first)]
    spans += [("money", (v, c), a, b) for v, c, a, b in parse_money(line) if not any(a < y and x < b for _, _, x, y in spans)]
    spans += [("ref", v, a, b) for v, a, b in find_refs(line) if not any(a < y and x < b for _, _, x, y in spans)]
    spans.sort(key=lambda t: t[2])
    leftover = line
    for _, _, a, b in sorted(spans, key=lambda t: -t[2]):
        leftover = leftover[:a] + " " * (b - a) + leftover[b:]
    if re.search(r"[A-Za-z]{2,}", leftover) or not spans:
        return None
    return spans


# ------------------------------------------------------------------ labels
def find_labels(line: str):
    hits = []
    for f, rx, s in LABELS:
        for m in rx.finditer(line):
            if m.end() == m.start():
                continue
            tail = line[m.end():m.end() + 14]
            head = line[max(0, m.start() - 9):m.start()]
            if f == "vat" and (VAT_ID_TAIL.match(tail) or VAT_ID_HEAD.search(head)):
                continue
            if f == "invoice_number" and s == 1 and NUMBER_HEAD.search(line[max(0, m.start() - 20):m.start()]):
                continue
            if f == "invoice_date" and re.fullmatch(r"\s+vom\b\s*:?", m.group(0), re.I) and not re.search(r"rechnung|gutschrift|invoice", line[:m.start()], re.I):
                continue   # "<no.> vom <date>" is the invoice date only on the line that names the invoice
            if f == "total" and re.search(r"(?:line|sub|item|prix|ligne|zeilen|regel|previous|unpaid)\s*$", head, re.I):
                continue
            if f == "total" and re.match(r"\s*der\s*(?:zu|ab)schl[äa]ge|\s*der\s*(?:rabatte|nachl[äa]sse|skonti)", tail, re.I):
                continue   # "Gesamtbetrag der Abschläge" = total of discounts, not the invoice total
            if f == "subtotal" and rx.pattern.startswith(r"\bnet\b") and re.match(r"\s*(?:\d+\s*(?:days)?|monthly)", tail, re.I):
                continue  # "Net 30" payment terms
            hits.append((m.start(), m.end(), f, s))
    hits.sort(key=lambda h: (-(h[1] - h[0]), h[0]))
    kept = []
    for h in hits:
        if all(h[1] <= k[0] or h[0] >= k[1] for k in kept):
            kept.append(h)
    return sorted(kept)


def column_headings(line: str):
    """Item-table column headings found in a line: list of (start, end, category, text)."""
    hits = []
    for cat, rx in COL_RE.items():
        for m in rx.finditer(line):
            hits.append((m.start(), m.end(), cat, m.group(0)))
    hits.sort(key=lambda h: (-(h[1] - h[0]), h[0]))
    kept = []
    for h in hits:
        if all(h[1] <= k[0] or h[0] >= k[1] for k in kept):
            kept.append(h)
    return sorted(kept)


def is_item_header(line: str) -> bool:
    cats = {h[2] for h in column_headings(line)}
    return ("desc" in cats and len(cats) >= 3) or (len(cats) >= 4 and ("amount" in cats or "price" in cats))


def _assign_below(label_spans, nxt: Line):
    """Map words of the next line to the label above them (by horizontal overlap). Returns list of text per label."""
    cells = [[] for _ in label_spans]
    for w in nxt.words:
        best, best_ov, best_d = None, 0.0, 1e9
        for k, (x0, x1) in enumerate(label_spans):
            ov = min(x1, w.x1) - max(x0, w.x0)
            d = abs((x0 + x1) / 2 - (w.x0 + w.x1) / 2)
            if ov > best_ov or (best_ov <= 0 and ov <= 0 and d < best_d):
                best, best_ov, best_d = k, ov, d
        if best is not None:
            # unrelated words far from every label are ignored
            x0, x1 = label_spans[best]
            if best_ov > 0 or best_d < max(60, (x1 - x0) * 1.5):
                cells[best].append(w.text)
    return [" ".join(c) for c in cells]


def collect_candidates(lines: list[Line], day_first=True):
    cands = []
    for i, ln in enumerate(lines):
        line = ln.text
        labels = find_labels(line)
        if not labels:
            continue
        segs = []
        for j, (a, b, f, s) in enumerate(labels):
            end = labels[j + 1][0] if j + 1 < len(labels) else len(line)
            segs.append((f, s, line[b:end], a, b))
        empty = []
        for f, s, seg, a, b in segs:
            v = first_value(f, seg, day_first)
            if f == "vat" and v is not None and re.search(r"\d\s?%", seg):
                ms = parse_money(seg)      # VAT breakdown row "base  rate%  tax": the tax is the last amount
                if len(ms) >= 2:
                    v = ((ms[-1][0], ms[-1][1]), ms[-1][2])
            if v is not None:
                cands.append(Candidate(f, v[0], s, i))
            elif not seg.strip(" :#.") or len(segs) >= 2:
                empty.append((f, s, a, b))
        if not empty or i + 1 >= len(lines) or is_item_header(line):
            continue
        nxt = lines[i + 1]
        if ln.real_layout and nxt.real_layout and nxt.page == ln.page:
            spans = [ln.xspan(a, b) for _, _, a, b in empty]
            ok = [(e, sp) for e, sp in zip(empty, spans) if sp]
            todo = list(range(len(ok)))
            for below in lines[i + 1:i + 2]:          # the value sits on the row under its heading
                if below.page != ln.page or not todo or find_labels(below.text) or is_item_header(below.text):
                    break
                cells = _assign_below([ok[k][1] for k in todo], below)
                found = []
                for k, cell in zip(todo, cells):
                    (f, s, _, _), _ = ok[k]
                    v = first_value(f, cell, day_first)
                    if v is not None:
                        cands.append(Candidate(f, v[0], s, lines.index(below))); found.append(k)
                todo = [k for k in todo if k not in found]
            continue
        toks = _value_tokens(nxt.text, day_first)
        if len(segs) >= 2 and toks:            # plain text: header row -> value row, in order
            pool = list(toks)
            for f, s, _, _, _ in segs:
                want = "date" if f in DATE_FIELDS else "money" if f in MONEY_FIELDS else "ref"
                for k, t in enumerate(pool):
                    if t[0] == want or (want == "ref" and t[0] in ("ref", "money")):
                        val = t[1] if want != "ref" or t[0] == "ref" else str(t[1][0])
                        cands.append(Candidate(f, val, s, i + 1)); pool = pool[k + 1:]; break
        elif len(segs) == 1:
            f, s, _, _, _ = segs[0]
            v = first_value(f, nxt.text, day_first)
            if v is not None and v[1] <= 2:
                cands.append(Candidate(f, v[0], s, i + 1))
    return cands


# ------------------------------------------------------------------ supplier
def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def supplier_key(name: str | None) -> str:
    if not name: return ""
    words = [w for w in re.split(r"[\s,&]+", name.lower()) if w and not COMPANY_SUFFIX.match(w)]
    return _norm(" ".join(words))


def _tidy_name(name: str) -> str:
    name = name.strip(" ,:;-·|")
    if name.isupper() and len(name) > 4:
        fix = {"LTD": "Ltd", "LTD.": "Ltd.", "PLC": "plc", "LLP": "LLP", "LLC": "LLC", "IT": "IT", "UK": "UK", "&": "&", "GMBH": "GmbH",
               "BV": "BV", "B.V.": "B.V.", "SA": "SA", "S.A.": "S.A.", "AG": "AG", "SRL": "Srl", "PVT": "Pvt", "PVT.": "Pvt."}
        name = " ".join(fix.get(w, w.capitalize()) for w in name.split())
    return re.sub(r"\s+", " ", name)


def _company_in(line: str):
    found = []
    for chunk in re.split(r"[:;·|()\[\]]|,(?!\s*(?:" + L.COMPANY_SUFFIXES + r")\b)|\s{3,}|\s[-–]\s", line, flags=re.I):
        found += _company_in_chunk(chunk)
    return found


def _company_in_chunk(chunk: str):
    toks = chunk.split()
    found = []
    for k, t in enumerate(toks):
        if COMPANY_SUFFIX.match(t) and k > 0:
            if t.lower().rstrip(".,") in WAIT_SUFFIX and k + 1 < len(toks) and COMPANY_SUFFIX.match(toks[k + 1]):
                continue  # "Bright Paper Co Limited", "Oravel Stays Pvt. Ltd."
            if t.lower().rstrip(".,") in ("as", "ab", "co", "sa", "sl") and not (t[0].isupper()):
                continue  # ordinary words "as", "co"
            j = k - 1; words = [t.rstrip(",")]
            while j >= 0 and len(words) < 7:
                w = toks[j]
                if COMPANY_SUFFIX.match(w) and w.lower().rstrip(".,") not in WAIT_SUFFIX:
                    break
                if w[0].isupper() or w[0].isdigit() or w in ("&", "and", "of", "the", "+", "et", "und", "en", "y", "e"):
                    words.insert(0, w if len(words) == 1 else w.rstrip(",")); j -= 1   # keep "Lusitana, Lda."
                else:
                    break
            while words and words[0].lower() in ("and", "of", "the", "&", "et", "und", "en", "y", "e"):
                words.pop(0)
            if len(words) >= 2 and not BUYER_LABEL.search(" ".join(words[:1])) and not TITLE_WORDS.match(words[0]):
                found.append(" ".join(words))
    return found


def find_supplier(lines, own_names):
    if lines and isinstance(lines[0], str):
        lines = lines_from_text("\n".join(lines))
    texts = [l.text for l in lines]
    own = {_norm(n) for n in own_names if n} | {supplier_key(n) for n in own_names if n}
    is_own = lambda n: bool(n) and (_norm(n) in own or supplier_key(n) in own or any(o and o in _norm(n) for o in own))
    buyer_zone = set()
    for i, l in enumerate(texts):
        if BUYER_LABEL.search(l):
            buyer_zone.update({i, i + 1, i + 2})
    scored = []
    n = max(len(texts), 1)
    for i, l in enumerate(texts):
        m = SUPPLIER_LABEL.search(l)
        if m and m.start() <= 2:
            rest = l[m.end():]
            nxt_lab = find_labels(rest)
            if nxt_lab: rest = rest[:nxt_lab[0][0]]
            rest = rest.strip()
            rest = re.split(r"\s{2,}|\b(?:to|date|invoice)\b\s*:", rest, flags=re.I)[0].strip()
            if not rest and i + 1 < n:   # heading alone: prefer a "Name:" line just below, else the next line
                below = texts[i + 1:i + 4]
                named = next((re.split(r":\s*", b, 1)[1] for b in below if re.match(r"\s*(?:name|firma|raison\s*sociale|naam|nombre|nome)\s*:", b, re.I)), None)
                rest = (named or (texts[i + 1] if not find_labels(texts[i + 1]) and ":" not in texts[i + 1] else "")).strip()
            if rest and not is_own(rest) and re.search(r"[A-Za-z]{2}", rest):
                scored.append((100, i, rest))
        for c in _company_in(l):
            if is_own(c):
                continue
            if i in buyer_zone and BUYER_LABEL.search(l[: l.find(c.split()[0]) + 1] if c.split()[0] in l else l):
                continue
            score = 80 if i < max(6, n * 0.35) else 60
            if i in buyer_zone: score -= 30
            scored.append((score, i, c))
    if not scored:  # fallback: document heading
        for i, l in enumerate(texts[:4]):
            t = l.strip()
            if re.search(r"\b(?:qty|quantity|units?|rate|price|description|item|amount|issued|billed|due|page|address)\b|#|@", t, re.I):
                continue
            if (len(t) >= 3 and re.search(r"[A-Za-z]{3}", t) and not TITLE_WORDS.match(t) and not find_labels(t)
                    and not POSTCODE.search(t) and not re.match(r"^\d", t) and not is_own(t) and len(t) < 60
                    and not BUYER_LABEL.search(t)):
                t = re.split(r"\s{2,}|\s+(?:tax\s*|sales\s*|project\s*)?(?:invoice|statement|receipt|" + L.TITLE_WORDS + r")\b", t, flags=re.I)[0]
                if t.strip():
                    scored.append((30, i, t)); break
    if not scored:
        return None
    scored.sort(key=lambda x: (-x[0], x[1]))
    return _tidy_name(scored[0][2])


# ------------------------------------------------------------------ amounts
def reconcile(cands, tol):
    by = {f: [c for c in cands if c.field == f] for f in MONEY_FIELDS}
    opts = {f: (by[f] + [None]) for f in MONEY_FIELDS}
    best, best_score = None, -1e9
    for s, v, t in itertools.product(opts["subtotal"], opts["vat"], opts["total"]):
        vals = [x for x in (s, v, t) if x]
        if not vals:
            continue
        score = sum(x.strength for x in vals) + 0.02 * sum(x.line for x in vals)
        if s and v and t:
            ok = abs(s.value[0] + v.value[0] - t.value[0]) <= tol
            score += 12 if ok else -3
            if ok and t.value[0] != 0 and t.value[0] == s.value[0] and v.value[0] == 0: score -= 1
        if t: score += 3
        for f, x in (("subtotal", s), ("vat", v), ("total", t)):
            score += 2 if x else (-4 if by[f] else 0)
        if s and t and abs(s.value[0]) > abs(t.value[0]) + tol: score -= 6
        if v and t and abs(v.value[0]) > abs(t.value[0]): score -= 6
        if s and v and abs(s.value[0]) > 0.009 and abs(v.value[0]) > 0.275 * abs(s.value[0]): score -= 8   # VAT above 27% of net is implausible
        if score > best_score:
            best, best_score = (s, v, t), score
    return best or (None, None, None)


# ------------------------------------------------------------------ date order
def detect_day_first(text: str, default: bool):
    dm = md = 0
    for a, b, _ in NUMERIC_DATE.findall(text):
        a, b = int(a), int(b)
        if a > 12 and b <= 12: dm += 1
        elif b > 12 and a <= 12: md += 1
    if dm and not md: return True, None
    if md and not dm: return False, "Dates read as month/day (US style) because the document contains dates such as 12/15."
    if not dm and not md and re.search(r"\$\s?\d|\bUSD\b", text) and not re.search(r"£|€|\bGBP\b|\bEUR", text) and default:
        return False, "Dates read as month/day (US style) because the invoice is in US dollars and no date settles the order."
    return default, None


# ------------------------------------------------------------------ line items
def _col_bounds(heads, line):
    cols = []
    for a, b, cat, txt in heads:
        sp = line.xspan(a, b)
        if sp: cols.append([cat, txt, sp[0], sp[1]])
    cols.sort(key=lambda c: c[2])
    for k, c in enumerate(cols):   # boundaries halfway between neighbouring headings
        c.append(-1e9 if k == 0 else (cols[k - 1][3] + c[2]) / 2)
        c.append(1e9 if k == len(cols) - 1 else (c[3] + cols[k + 1][2]) / 2)
    return cols


def _num(s):
    m = re.search(r"-?\d+(?:[.,]\d+)?", s or "")
    if not m: return None
    v, _ = _to_float(m.group(0).lstrip("-"))
    return -v if m.group(0).startswith("-") else v


def _amount_col(cols):
    amounts = [c for c in cols if c[0] == "amount"]
    if not amounts:
        return None
    for pref in L.AMOUNT_PREF:
        for c in reversed(amounts):
            if re.fullmatch(r"(?:" + pref + r")(?:\s*\w{0,4})?", c[1].strip(), re.I) or re.search(pref, c[1], re.I):
                return c
    return amounts[-1]


def extract_line_items(lines: list[Line], summary_lines: set[int]):
    items = []
    i = 0
    while i < len(lines):
        ln = lines[i]
        if not is_item_header(ln.text):
            i += 1; continue
        heads = column_headings(ln.text)
        cols = _col_bounds(heads, ln)
        amt = _amount_col(cols)
        if amt is None:
            i += 1; continue
        money_cols = [c for c in cols if c[0] in ("amount", "price", "tax", "discount") and "%" not in c[1]]
        j = i + 1
        while j < len(lines) and j - i < 80:
            row = lines[j]
            if row.page != ln.page or j in summary_lines or is_item_header(row.text) or re.search(L.STOP_WORDS, row.text, re.I):
                break
            if row.real_layout and ln.real_layout:
                cells = {id(c): [] for c in cols}
                for w in row.words:
                    numeric = bool(re.search(r"\d", w.text)) and not re.search(r"[A-Za-z]{3}", w.text)
                    x = w.x1 if numeric else (w.x0 + w.x1) / 2
                    col = next((c for c in cols if c[4] <= x < c[5]), None)
                    if col is not None: cells[id(col)].append(w.text)
                txt = {id(c): " ".join(v) for c, v in zip(cols, cells.values())}
                amount = parse_money(txt[id(amt)])
                if amount:
                    desc = " ".join(txt[id(c)] for c in cols if c[0] == "desc").strip()
                    if not desc:  # no description column: use the words that are not numbers
                        desc = " ".join(w.text for w in row.words if not re.search(r"\d", w.text))
                    q = next((_num(txt[id(c)]) for c in cols if c[0] == "qty"), None)
                    p = next((parse_money(txt[id(c)]) for c in cols if c[0] == "price"), None)
                    items.append(dict(description=desc.strip(), qty=q, unit_price=p[0][0] if p else None, amount=amount[-1][0]))
                elif items and any(txt[id(c)] for c in cols if c[0] == "desc") and not any(txt[id(c)] for c in cols if c[0] != "desc"):
                    items[-1]["description"] = (items[-1]["description"] + " " + " ".join(txt[id(c)] for c in cols if c[0] == "desc")).strip()
            else:  # plain text: map money values to the money columns in order
                ms = parse_money(row.text)
                if ms:
                    if len(ms) == len(money_cols):
                        val = ms[money_cols.index(amt)][0]
                    else:
                        val = ms[-1][0]
                    first_num = re.search(r"\s\d", " " + row.text)
                    desc = row.text[: first_num.start()].strip() if first_num else row.text
                    items.append(dict(description=desc, qty=None, unit_price=None, amount=val))
            j += 1
        i = j
    return items or None


def _items_reconcile(items, sub, tot, tol):
    s = round(sum(i["amount"] for i in items), 2)
    t = max(tol, 0.01 * len(items))
    return (sub is not None and abs(s - sub) <= t) or (tot is not None and abs(s - tot) <= t)


def rows_above_summary(lines: list[Line], summary_lines: set[int]):
    """Fallback when the table heading is unreadable (e.g. a shaded heading bar in a poor scan):
    the block of rows directly above the first total line, each with a description and an amount
    at the end. The caller keeps them only if they add up to the net or total."""
    if not summary_lines:
        return None
    first = min(summary_lines)
    rows = []
    j = first - 1
    while j >= 0 and len(rows) < 60:
        t = lines[j].text
        ms = parse_money(t)
        ends_with_amount = bool(ms) and re.fullmatch(r"[\s|€$£₹]*(?:EUROS?|EUR|GBP|USD)?[\s|]*", t[ms[-1][3]:], re.I)
        if ends_with_amount and re.search(r"[A-Za-zÀ-ÿ]{3}", t) and not find_labels(t):
            desc = re.sub(r"^[A-Z]{0,3}\d+[A-Z]?\s+", "", t[: ms[0][2]]).strip(" |")
            desc = re.sub(r"\s+\d+(?:[.,]\d+)?\s*(?:kg|pcs|units?|x)?\s*$", "", desc, flags=re.I).strip(" |")
            rows.insert(0, dict(description=desc, qty=None, unit_price=ms[-2][0] if len(ms) >= 2 else None, amount=ms[-1][0]))
            j -= 1
        elif rows and not ms and re.search(r"[A-Za-z]", t) and not find_labels(t) and not is_item_header(t) and len(rows) < 60:
            j -= 1   # wrapped description line between items
        else:
            break
    return rows or None


# ------------------------------------------------------------------ main
def extract(src, own_names=(), day_first=True, tol=0.02) -> Extraction:
    lines = lines_from_text(src) if isinstance(src, str) else list(src)
    text = "\n".join(l.text for l in lines)
    ex = Extraction(language=L.detect_language(text))
    day_first, why = detect_day_first(text, day_first)
    if why:
        ex.notes.append(why); ex.info_flags.append("DATES_MONTH_FIRST")
    cands = collect_candidates(lines, day_first)

    def best(f):
        cs = [c for c in cands if c.field == f]
        return sorted(cs, key=lambda c: (-c.strength, c.line))[0] if cs else None

    c = best("invoice_number"); ex.invoice_number = str(c.value).lstrip("#") if c else None
    strong = {re.sub(r"[^A-Z0-9]", "", str(x.value).upper()) for x in cands if x.field == "invoice_number" and x.strength >= 2}
    if len(strong) > 1:
        ex.info_flags.append("MULTIPLE_INVOICE_NUMBERS")
        ex.notes.append("This file contains more than one invoice number (" + ", ".join(sorted(strong)) + "). It may be several invoices in one PDF - only the first was recorded.")
    c = best("invoice_date"); ex.invoice_date = c.value if c else None
    due = best("due_date"); ex.due_date = due.value if due else None
    if ex.invoice_date and ex.due_date and ex.invoice_date == ex.due_date:
        others = [c for c in cands if c.field == "invoice_date" and c.value != ex.due_date]
        if others: ex.invoice_date = sorted(others, key=lambda c: (-c.strength, c.line))[0].value

    s, v, t = reconcile(cands, tol)
    sub, vat, tot = (s.value[0] if s else None), (v.value[0] if v else None), (t.value[0] if t else None)
    curs = [x.value[1] for x in (s, v, t) if x and x.value[1]]
    if curs:
        ex.currency = curs[0]
    elif re.search(r"€|\bEURO?S?\b", text):
        ex.currency = "EUR"
    elif re.search(r"\bUSD\b|\$\s?\d", text):
        ex.currency = "USD"
    elif re.search(r"₹|\bINR\b|\bRs\.?\s?\d", text):
        ex.currency = "INR"
    elif re.search(r"£|\bGBP\b", text):
        ex.currency = "GBP"
    elif ex.language in L.EURO_LANGS:
        ex.currency = "EUR"; ex.notes.append(f"No currency is printed; assumed EUR because the invoice is in {ex.language.upper()}.")

    if tot is not None and sub is not None and vat is None:
        vat = round(tot - sub, 2); ex.notes.append("VAT not printed separately - calculated as Total minus Net."); ex.info_flags.append("VALUES_DERIVED")
    elif tot is not None and vat is not None and sub is None:
        sub = round(tot - vat, 2); ex.notes.append("Net amount not printed - calculated as Total minus VAT."); ex.info_flags.append("VALUES_DERIVED")
    elif sub is not None and vat is not None and tot is None:
        tot = round(sub + vat, 2); ex.notes.append("Total not printed - calculated as Net plus VAT."); ex.info_flags.append("VALUES_DERIVED")
    ex.subtotal, ex.vat, ex.total = sub, vat, tot

    if not ex.due_date and ex.invoice_date:
        D = L.DAYS_WORDS
        m = re.search(r"(?:terms|within|payable|payment|net|paiement|zahlbar|binnen|pago|pagamento|termini)[^\n\d]{0,25}?(\d{1,3})\s*(?:" + D + r")\b"
                      r"|(\d{1,3})\s*(?:" + D + r")\s*(?:net|from|fin\s*de\s*mois|netto|fecha|df|d\.f\.)|\bnet\s*(\d{1,3})\b", text, re.I)
        if m:
            days = int(next(g for g in m.groups() if g))
            if 0 < days <= 180:
                ex.due_date = ex.invoice_date + timedelta(days=days)
                ex.notes.append(f"Due date not printed - calculated from payment terms ({days} days).")
                ex.info_flags.append("DUE_DATE_FROM_TERMS")

    ex.supplier = find_supplier(lines, own_names)
    ex.is_credit_note = bool(re.search(r"\bcredit\s*note\b|\bavoir\b|\bgutschrift\b|\bcreditnota\b|nota\s*de\s*cr[eé]dito", text, re.I)) or (tot is not None and tot < 0)

    summary = {c.line for c in cands if c.field in MONEY_FIELDS and c.strength >= 2}
    items = extract_line_items(lines, summary)
    if not items or not _items_reconcile(items, sub, tot, tol):
        alt = rows_above_summary(lines, summary)
        if alt and _items_reconcile(alt, sub, tot, tol):   # accepted ONLY when the rows add up exactly
            items = alt
    if items:
        ex.line_items = items
        total_lines = round(sum(it["amount"] for it in items), 2)
        tol_l = max(tol, 0.01 * len(items))
        if sub is not None and abs(total_lines - sub) <= tol_l:
            ex.line_items_check = "net"
        elif tot is not None and abs(total_lines - tot) <= tol_l:
            ex.line_items_check = "gross"
        else:
            ex.line_items_check = "mismatch"
    return ex
