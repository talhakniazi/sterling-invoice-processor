"""Turn a PDF or image file into positioned lines of text, using OCR only when the file has no text layer."""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import pdfplumber
from PIL import Image

from .lang import TESSERACT_CODE, detect_language
from .layout import Line, Word, group_into_lines

IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".tif", ".tiff"}
SUPPORTED_TYPES = {".pdf"} | IMAGE_TYPES
MIN_TEXT_CHARS = 40  # fewer characters than this per document => treat as a scan


class ReadError(Exception):
    """A file could not be read. The message is written for a non-technical user."""


@dataclass
class Document:
    text: str
    method: str          # "text" or "ocr"
    pages: int
    lines: list[Line] = field(default_factory=list)
    language: str = "en"
    notes: list[str] = field(default_factory=list)


def find_tesseract(configured: str = "") -> str | None:
    candidates = [configured] if configured else []
    which = shutil.which("tesseract")
    if which: candidates.append(which)
    candidates += [r"C:\Program Files\Tesseract-OCR\tesseract.exe", r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
                   os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe"),
                   "/opt/homebrew/bin/tesseract", "/usr/local/bin/tesseract", "/usr/bin/tesseract"]
    for c in candidates:
        if c and Path(c).is_file():
            return c
    return None


def _crop_dark_border(im):
    """Remove black/dark margins (screenshots, phone photos on a dark desk) that confuse OCR."""
    import numpy as np
    a = np.asarray(im)
    cols, rows = a.mean(axis=0), a.mean(axis=1)
    def span(v):
        light = np.where(v > 90)[0]
        return (light[0], light[-1] + 1) if len(light) else (0, len(v))
    x0, x1 = span(cols); y0, y1 = span(rows)
    if (x1 - x0) < 0.98 * a.shape[1] or (y1 - y0) < 0.98 * a.shape[0]:
        return im.crop((int(x0), int(y0), int(x1), int(y1)))
    return im


def _prepare(im):
    im = _crop_dark_border(im.convert("L"))
    if im.width < 1800:  # small images OCR badly; upscale
        f = 1800 / im.width; im = im.resize((int(im.width * f), int(im.height * f)), Image.LANCZOS)
    from PIL import ImageFilter, ImageOps
    im = ImageOps.autocontrast(im).filter(ImageFilter.UnsharpMask(radius=2, percent=120, threshold=3))
    return _lighten_dark_bands(im)


def _ocr_lines(im, page: int, lang: str) -> list[Line]:
    import pytesseract
    d = pytesseract.image_to_data(im, lang=lang, config="--psm 4", output_type=pytesseract.Output.DICT)
    rows: dict = {}
    for i, t in enumerate(d["text"]):
        t = (t or "").strip()
        if not t or float(d["conf"][i]) < 0:
            continue
        key = (d["block_num"][i], d["par_num"][i], d["line_num"][i])   # Tesseract's own lines cope with skewed scans
        rows.setdefault(key, []).append(Word(t, d["left"][i], d["left"][i] + d["width"][i], d["top"][i], d["top"][i] + d["height"][i]))
    return _merge_rows([sorted(ws, key=lambda w: w.x0) for ws in rows.values()], page)


def _merge_rows(rows, page) -> list[Line]:
    """Tesseract sometimes splits one visual row into separate blocks (e.g. a table heading).
    Re-join pieces on the same row, after correcting for the page's tilt."""
    import statistics
    slopes = []
    for ws in rows:
        if len(ws) >= 3:
            xs = [(w.x0 + w.x1) / 2 for w in ws]; ys = [(w.top + w.bottom) / 2 for w in ws]
            if xs[-1] - xs[0] > 100:
                slopes.append((ys[-1] - ys[0]) / (xs[-1] - xs[0]))
    slope = statistics.median(slopes) if slopes else 0.0
    heights = [w.bottom - w.top for ws in rows for w in ws]
    h = statistics.median(heights) if heights else 10
    centre = lambda ws: statistics.mean((w.top + w.bottom) / 2 - slope * (w.x0 + w.x1) / 2 for w in ws)
    rows = sorted(rows, key=centre)
    merged: list[list] = []
    for ws in rows:
        if merged:
            prev = merged[-1]
            overlap_x = min(max(w.x1 for w in prev), max(w.x1 for w in ws)) - max(min(w.x0 for w in prev), min(w.x0 for w in ws))
            if abs(centre(prev) - centre(ws)) < 0.5 * h and overlap_x < 0:
                merged[-1] = sorted(prev + ws, key=lambda w: w.x0); continue
        merged.append(ws)
    return [Line(ws, page) for ws in merged]


def _ocr(images, cfg) -> tuple[list[Line], str, list[str]]:
    if not cfg.ocr_enabled:
        raise ReadError("This looks like a scanned invoice (no readable text) and OCR is switched off in Settings.")
    exe = find_tesseract(cfg.tesseract_path)
    if not exe:
        raise ReadError("This looks like a scanned invoice (no readable text) and the OCR program Tesseract is not installed. "
                        "Install it (see the Installation Guide), then put this file back in the inbox.")
    import pytesseract
    pytesseract.pytesseract.tesseract_cmd = exe
    narrow = min(_crop_dark_border(im.convert("L")).width for im in images)
    prepared = [_prepare(im) for im in images]
    lines = [l for p, im in enumerate(prepared) for l in _ocr_lines(im, p, "eng")]
    lang = detect_language("\n".join(l.text for l in lines))
    notes = []
    if lang != "en":
        code = TESSERACT_CODE[lang]
        try:
            available = set(pytesseract.get_languages(config=""))
        except Exception:
            available = set()
        if code in available:   # re-read with the right language model (accents, letter shapes)
            lines = [l for p, im in enumerate(prepared) for l in _ocr_lines(im, p, f"{code}+eng")]
        else:
            notes.append(f"Scanned {lang.upper()} invoice read with the English OCR model - the '{code}' language pack for Tesseract is not installed.")
    if narrow < 1000:
        notes.append(f"LOW_RESOLUTION: The image is only {narrow} pixels wide (about {round(narrow / 8.27)} dpi), too small for reliable reading. "
                     f"Ask the supplier for the original PDF, or scan at 200 dpi or more.")
    return lines, lang, notes


def _lighten_dark_bands(im):
    """Invert rows that sit inside dark coloured bands (white-on-dark headers), so OCR can read them."""
    import numpy as np
    a = np.asarray(im).astype(np.uint8).copy()
    dark_rows = np.median(a, axis=1) < 110   # median ignores the light letters inside the band
    if dark_rows.any() and dark_rows.mean() < 0.6:
        a[dark_rows] = 255 - a[dark_rows]
    return Image.fromarray(a)


def _doc(lines, method, pages, lang=None, notes=()):
    text = "\n".join(l.text for l in lines)
    return Document(text, method, pages, lines, lang or detect_language(text), list(notes))


def read_document(path: Path, cfg) -> Document:
    ext = path.suffix.lower()
    if path.stat().st_size == 0:
        raise ReadError("The file is empty (0 bytes). It may not have downloaded or saved properly.")
    if ext in IMAGE_TYPES:
        try:
            im = Image.open(path); im.load()
        except Exception:
            raise ReadError("The image file is damaged or not a real image.")
        lines, lang, notes = _ocr([im], cfg)
        return _doc(lines, "ocr", 1, lang, notes)
    if ext != ".pdf":
        raise ReadError(f"Unsupported file type '{ext}'.")

    try:
        with pdfplumber.open(path) as pdf:
            n = len(pdf.pages)
            if n == 0:
                raise ReadError("The PDF has no pages.")
            if n > cfg.max_pages:
                raise ReadError(f"The PDF has {n} pages - more than the {cfg.max_pages}-page limit for one invoice. It may be several invoices merged together.")
            lines = []
            for p, page in enumerate(pdf.pages):
                ws = page.extract_words(x_tolerance=2, y_tolerance=3, keep_blank_chars=False)
                lines += group_into_lines([Word(w["text"], w["x0"], w["x1"], w["top"], w["bottom"]) for w in ws], p)
    except ReadError:
        raise
    except Exception as e:  # pdfminer raises several different types
        msg = f"{type(e).__name__}: {e!r} {getattr(e, 'args', '')!r}".lower()
        if "password" in msg or "encrypt" in msg:
            raise ReadError("The PDF is password-protected. Ask the supplier for an unprotected copy, or open it, 'Print to PDF' and put the new file in the inbox.")
        raise ReadError("The PDF is damaged or incomplete and cannot be opened. Ask the supplier to resend it.")

    if len("".join(l.text for l in lines).replace(" ", "")) >= MIN_TEXT_CHARS:
        return _doc(lines, "text", n)

    import pypdfium2 as pdfium
    try:
        pdf = pdfium.PdfDocument(str(path))
        images = [p.render(scale=300 / 72).to_pil() for p in pdf]
        pdf.close()
    except Exception:
        raise ReadError("The PDF is a scan but its pages could not be rendered for OCR. The file may be damaged.")
    lines, lang, notes = _ocr(images, cfg)
    return _doc(lines, "ocr", n, lang, notes)
