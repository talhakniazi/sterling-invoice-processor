# Invoice Processor

Reads supplier invoices (PDF, scanned PDF, PNG/JPG/TIFF) in English, French, German, Dutch,
Spanish, Italian and Portuguese. Extracts supplier, invoice number, invoice date, due date, net,
VAT, total and every line item; validates them; detects duplicates; files each document into
Processed / Needs Review / Duplicates / Failed; and maintains an Excel register with invoices,
line items, monthly and supplier summaries.

Non-technical staff: see **Documentation/2 - User Guide.pdf**.
Installation: see **Documentation/1 - Installation Guide.pdf** (short version below).

## Quick start

| | Windows | macOS / Linux |
|---|---|---|
| Requirements | Python 3.10+ (python.org, tick *Add to PATH*); optional Tesseract OCR (UB-Mannheim build) | Python 3.10+; optional `brew install tesseract` / `apt install tesseract-ocr` |
| Start | double-click `Start Invoice Processor.bat` | `./start_invoice_processor.sh` |
| Headless / scheduled | `.venv\Scripts\python process_invoices_cli.py [--inbox DIR] [--output DIR]` | same with `.venv/bin/python` |

The launcher creates `.venv` and installs `requirements.txt` on first run.
CLI exit codes: `0` all processed cleanly, `1` something needs attention, `2` could not run (already running).

## Folder layout

```
Invoice Processor/
  Start Invoice Processor.bat     Windows launcher (first run installs dependencies)
  start_invoice_processor.sh      macOS/Linux launcher
  Invoice Processor.pyw           GUI entry point
  process_invoices_cli.py         command-line entry point
  settings.json                   created on first run; edited from the Settings window
  requirements.txt
  invoice_processor/
    config.py      settings + folder locations
    reader.py      positioned text from PDFs (pdfplumber words) or OCR (pypdfium2 render + Tesseract,
                   dark-border crop, contrast/sharpen, dark-band inversion, language-specific 2nd pass)
    layout.py      lines that remember each word's x/y position
    lang.py        all language-specific vocabulary (labels, months, column headings, company forms)
    extract.py     generic field + line-item extraction - no supplier templates
    validate.py    business checks -> issues with severity (review / info)
    ledger.py      SQLite record of everything processed (idempotency, duplicate detection)
    workbook.py    Excel register (openpyxl): append rows, rebuild formula summaries
    processor.py   orchestration, file routing, logging, run report, CSV export (pandas)
    gui.py         Tkinter window
  tests/           pytest suite + sample invoices + ground truth
```

Results folder (configurable):

```
Invoice Output/
  Invoice Register.xlsx               the register (close it before running)
  Invoice Register (all records).csv  every file ever handled (pandas export)
  Processed/  Needs Review/  Duplicates/  Failed/
  Logs/  processing.log (rotating), Run report <timestamp>.txt, ledger.sqlite, .running.lock (during a run)
```

## Settings (`settings.json`)

| Key | Default | Meaning |
|---|---|---|
| `inbox_folder`, `output_folder` | inside the app folder | where invoices arrive / results go (can be network drives) |
| `our_company_names` | `["Sterling Office Supplies Ltd"]` | buyer names; never chosen as the supplier |
| `dates_are_day_first` | `true` | UK `03/04/2026` = 3 April |
| `standard_vat_rates` | `[0, 5, 20]` | rates above the max are flagged; in-between rates noted as mixed |
| `rounding_tolerance` | `0.02` | allowed £ difference in Net + VAT = Total |
| `high_value_threshold` | `10000` | totals above go to Needs Review |
| `old_invoice_days` | `365` | older invoice dates are flagged |
| `ocr_enabled`, `tesseract_path` | `true`, `""` | blank path = auto-detect (PATH, standard Windows/macOS locations) |
| `include_subfolders` | `false` | scan inbox recursively |
| `min_file_age_seconds` | `5` | skip files still being written |
| `max_pages` | `30` | larger PDFs are failed (probably merged batches) |

## How extraction works (no supplier templates)

1. Text per page via pdfplumber; if a document has under 40 characters of text it is treated as a
   scan: pages are rendered at 300 dpi, dark header bands are inverted, and Tesseract (`--psm 4`) is run.
2. Each line is scanned for label vocabulary (`Invoice No`, `Inv Ref`, `Tax point`, `Due by`,
   `Amount excl. VAT`, `Goods total`, `Balance due` … see `_L` in `extract.py`). A label owns the text
   up to the next label on the line. Also handled: value on the next line; a header row of labels
   with values on the row below.
3. Money candidates are reconciled: the subtotal/VAT/total combination that satisfies
   Net + VAT = Total wins, weighted by label strength and position. A printed amount is never
   dropped to make the sums balance - a supplier's arithmetic error is reported as `TOTAL_MISMATCH`.
4. Supplier: explicit `From:/Supplier:` label > company-suffix names (Ltd, LLP, plc, Inc, GmbH …) in
   the header > in the footer > document heading. Own company names and *Bill to / Customer* blocks are excluded.
5. Missing due date is derived from payment terms (`30 days net`, `Net 30`) and recorded as such.

6. Numbers: `1,234.56`, `1.234,56`, `1 234,56 €`, Indian `6,54,321.00`; currency from symbol/code, else from language.
7. Dates: English and foreign month names; day/month order is detected per document (13/02 vs 02/13), then
   US dollars => month-first, else the configured default.
8. Line items: the item table is found by its column headings (multilingual), rows are read by column
   position; if headings are unreadable, the rows above the totals are used ONLY if they add up exactly.
   `line_items_check` records whether lines add up to net / gross.

To support new wording, add a regex to `lang.py` (or `_EN` in `extract.py`) and a test in `tests/test_parsing.py`.

## Duplicate & re-run safety

* SHA-256 of each file: an identical file is a `DUPLICATE_FILE`.
* Normalised supplier + invoice number: `DUPLICATE_INVOICE_NUMBER` (the message warns if amounts differ).
* Same supplier + date + total with a different number: `POSSIBLE_DUPLICATE` (review).
* Failed records are ignored for duplicate checks, so a fixed file can be retried.
* The ledger tracks which records are in the workbook; if Excel is open (save fails), records are
  added on the next run. The workbook is saved to a temp file and swapped in atomically. A deleted
  register is rebuilt; an unreadable one is renamed aside, never overwritten.
* Rows are appended, so staff edits (Paid?, corrections) survive; summary sheets are formulas, rebuilt each run.

## Tests

```
python -m pip install pytest
python -m pytest            # 90 tests, ~90 s (OCR tests need Tesseract with fra/deu/nld/spa/ita/por)
```

`tests/data/sample_invoices` holds 41 invoice files (8 UK layouts, 6 European languages, a US invoice,
4 scans/photos, 10 problem cases) and `ground_truth.json` the expected fields, line items and status for each.

Cross-platform CI: `.github/workflows/test.yml` installs from `requirements.txt` into a fresh virtualenv on
Windows, macOS and Linux (Python 3.10 and 3.13), runs the tests, drives the real window (`ci/run_gui_check.py`),
and on Windows runs `Start Invoice Processor.bat` from scratch.

## Privacy

All processing is local. No invoice data is sent over the network. Network access is only
needed to install the Python packages on first run.
