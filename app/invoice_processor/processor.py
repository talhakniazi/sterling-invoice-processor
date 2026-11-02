"""Runs one batch: inbox -> read -> extract -> check -> ledger -> move file -> Excel register -> reports."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import time
import traceback
from dataclasses import dataclass, field
from datetime import date, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .extract import extract, supplier_key
from .ledger import Ledger
from .reader import SUPPORTED_TYPES, ReadError, read_document
from .validate import REVIEW, Issue, check, gbp
from .workbook import WorkbookLocked, write_register

log = logging.getLogger("invoice_processor")


class AlreadyRunning(Exception):
    pass


@dataclass
class RunResult:
    found: int = 0
    processed: int = 0
    review: int = 0
    duplicates: int = 0
    failed: int = 0
    skipped: int = 0
    ignored: list[str] = field(default_factory=list)
    excel_ok: bool = True
    messages: list[str] = field(default_factory=list)
    report_path: Path | None = None
    records: list[dict] = field(default_factory=list)

    def summary(self) -> str:
        s = f"{self.found} file(s) found: {self.processed} processed, {self.review} need review, {self.duplicates} duplicate(s), {self.failed} failed."
        if self.skipped: s += f" {self.skipped} skipped (still being copied - run again in a minute)."
        return s


def _setup_logging(cfg):
    cfg.logs_dir.mkdir(parents=True, exist_ok=True)
    target = str(cfg.logs_dir / "processing.log")
    for h in list(log.handlers):
        if getattr(h, "baseFilename", None) != os.path.abspath(target):
            log.removeHandler(h); h.close()
    if not log.handlers:
        h = RotatingFileHandler(target, maxBytes=2_000_000, backupCount=5, encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-7s  %(message)s", "%Y-%m-%d %H:%M:%S"))
        log.addHandler(h)
    log.setLevel(logging.INFO)


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _number_key(n):
    if not n: return ""
    return re.sub(r"[^A-Z0-9]", "", n.upper()).lstrip("0")


def _unique_dest(folder: Path, name: str) -> Path:
    dest = folder / name
    k = 2
    while dest.exists():
        dest = folder / f"{Path(name).stem} ({k}){Path(name).suffix}"; k += 1
    return dest


def _list_inbox(cfg, result):
    pattern = "**/*" if cfg.include_subfolders else "*"
    files, now = [], time.time()
    for p in cfg.inbox.glob(pattern):
        if not p.is_file() or p.name.startswith((".", "~$", "~tmp")):
            continue
        if p.suffix.lower() not in SUPPORTED_TYPES:
            result.ignored.append(p.name); continue
        if now - p.stat().st_mtime < cfg.min_file_age_seconds:
            result.skipped += 1; log.info("Skipped (file still being written): %s", p.name); continue
        files.append(p)
    return sorted(files, key=lambda p: (p.stat().st_mtime, p.name.lower()))


def _lock(cfg):
    lock = cfg.logs_dir / ".running.lock"
    if lock.exists() and time.time() - lock.stat().st_mtime < 2 * 3600:
        raise AlreadyRunning("The Invoice Processor is already running (possibly on another computer). Wait for it to finish. "
                             f"If you are sure it is not running, delete the file {lock}.")
    lock.write_text(f"{os.getpid()} {datetime.now():%Y-%m-%d %H:%M:%S}")
    return lock


def process_file(path: Path, cfg, ledger: Ledger, today: date) -> dict:
    """Decide what a single file is. Returns a record dict (not yet saved)."""
    rec = dict(original_name=path.name, file_hash=_sha256(path), status="FAILED", supplier=None, supplier_key=None,
               invoice_number=None, invoice_number_key=None, invoice_date=None, due_date=None, subtotal=None, vat=None,
               total=None, currency=None, method=None, issues=[], reason=None, duplicate_of=None, line_items=None, line_items_check=None, language=None)

    prev = ledger.find_by_hash(rec["file_hash"])
    if prev:
        rec.update({k: prev[k] for k in ("supplier", "supplier_key", "invoice_number", "invoice_number_key", "invoice_date", "due_date",
                                         "subtotal", "vat", "total", "currency", "method", "language")})
        rec.update(status="DUPLICATE", duplicate_of=prev["id"], issues=[vars(Issue("DUPLICATE_FILE", REVIEW, ""))],
                   reason=f"This exact file was already processed on {prev['processed_at'][:10]} (as '{prev['original_name']}', record {prev['id']}). It has not been added again.")
        return rec

    try:
        doc = read_document(path, cfg)
    except ReadError as e:
        rec["reason"] = str(e); return rec
    rec["method"] = doc.method
    ex = extract(doc.lines, cfg.our_company_names, cfg.dates_are_day_first, cfg.rounding_tolerance)
    rec.update(supplier=ex.supplier, supplier_key=supplier_key(ex.supplier), invoice_number=ex.invoice_number,
               invoice_number_key=_number_key(ex.invoice_number), invoice_date=ex.invoice_date and ex.invoice_date.isoformat(),
               due_date=ex.due_date and ex.due_date.isoformat(), subtotal=ex.subtotal, vat=ex.vat, total=ex.total, currency=ex.currency,
               line_items=ex.line_items, line_items_check=ex.line_items_check, language=ex.language)

    if ex.total is None and not ex.invoice_number:
        what = "a scanned document" if doc.method == "ocr" else "this document"
        rec["reason"] = (f"No invoice number or total could be found in {what}. It may not be an invoice (for example a delivery note, "
                         f"statement or remittance), or the layout is not one the app can read. Please enter it manually.")
        return rec

    issues = check(ex, cfg, doc.method, today, doc.notes)
    prev = ledger.find_by_invoice(rec["supplier_key"], rec["invoice_number_key"])
    if prev:
        same = prev["total"] is not None and ex.total is not None and abs(prev["total"] - ex.total) < 0.005
        amounts = ("The amounts are identical." if same else
                   f"WARNING: the amounts are different - previously {gbp(prev['total'] or 0)}, this file {gbp(ex.total or 0)}. "
                   f"This may be a corrected/re-issued invoice; check with the supplier before paying either.")
        rec.update(status="DUPLICATE", duplicate_of=prev["id"], issues=[vars(i) for i in issues] + [vars(Issue("DUPLICATE_INVOICE_NUMBER", REVIEW, ""))],
                   reason=f"Invoice {ex.invoice_number} from {ex.supplier} was already processed on {prev['processed_at'][:10]} "
                          f"(file '{prev['original_name']}', record {prev['id']}). {amounts}")
        return rec
    sim = ledger.find_similar(rec["supplier_key"], ex.total, rec["invoice_date"])
    if sim:
        issues.append(Issue("POSSIBLE_DUPLICATE", REVIEW, f"Same supplier, date and total as invoice {sim['invoice_number']} (record {sim['id']}) "
                                                         f"but a different invoice number. Check it is not charged twice."))
    rec["issues"] = [vars(i) for i in issues]
    rec["status"] = "NEEDS_REVIEW" if any(i.severity == REVIEW for i in issues) else "PROCESSED"
    return rec


def run(cfg, progress=lambda msg, frac=None: None, today: date | None = None) -> RunResult:
    today = today or date.today()
    cfg.ensure_folders()
    _setup_logging(cfg)
    result = RunResult()
    lock = _lock(cfg)
    ledger = Ledger(cfg.ledger_path)
    started = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    run_id = ledger.start_run(started)
    try:
        log.info("=== Run %s started. Inbox: %s", run_id, cfg.inbox)
        if not cfg.excel_path.exists():
            ledger.reset_workbook_flags()   # register missing or deleted -> rebuild it from the ledger
        files = _list_inbox(cfg, result)
        result.found = len(files) + result.skipped
        if result.ignored:
            log.info("Ignored non-invoice file types: %s", ", ".join(result.ignored))
        dest_dir = {"PROCESSED": cfg.processed_dir, "NEEDS_REVIEW": cfg.review_dir, "DUPLICATE": cfg.duplicate_dir, "FAILED": cfg.failed_dir}

        for n, path in enumerate(files, 1):
            progress(f"Reading {path.name} ({n} of {len(files)})", (n - 1) / max(len(files), 1))
            try:
                rec = process_file(path, cfg, ledger, today)
            except Exception as e:  # never lose a file because of an unexpected bug
                log.error("Unexpected error on %s:\n%s", path.name, traceback.format_exc())
                rec = dict(original_name=path.name, file_hash=None, status="FAILED", issues=[],
                           reason=f"Unexpected error while processing this file ({type(e).__name__}: {e}). Details are in Logs/processing.log.")
            dest = _unique_dest(dest_dir[rec["status"]], path.name)
            rec.update(run_id=run_id, processed_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"), stored_path=str(dest))
            rid = ledger.add(rec)
            try:
                shutil.move(str(path), str(dest))
                ledger.commit()
            except Exception as e:
                ledger.delete(rid); ledger.commit()
                msg = f"Could not move '{path.name}' out of the inbox ({e}). It is probably open in another program. It was left in the inbox and will be retried next run."
                log.error(msg); result.messages.append(msg)
                continue
            rec["id"] = rid
            result.records.append(rec)
            key = {"PROCESSED": "processed", "NEEDS_REVIEW": "review", "DUPLICATE": "duplicates", "FAILED": "failed"}[rec["status"]]
            setattr(result, key, getattr(result, key) + 1)
            detail = rec["reason"] or "; ".join(i["message"] for i in rec["issues"] if i["message"]) or "OK"
            log.info("%-12s %s | %s | %s | %s", rec["status"], path.name, rec.get("supplier"), rec.get("invoice_number"), detail)
            progress(f"{path.name}: {rec['status'].replace('_', ' ').title()}", n / max(len(files), 1))

        progress("Updating the Excel register...", None)
        pending = ledger.pending_workbook()
        note = ""
        try:
            runs_so_far = ledger.runs() + [dict(started=started, found=result.found, processed=result.processed, review=result.review,
                                                duplicates=result.duplicates, failed=result.failed, skipped=result.skipped, note="")]
            if True:  # always refresh: rebuilds summaries/run history and repairs a damaged register
                try:
                    ids = write_register(cfg.excel_path, pending, runs_so_far)
                except RuntimeError as e:
                    if str(e).startswith("REBUILD:"):
                        note = f"The existing register could not be opened, so it was renamed to '{str(e)[8:]}' and a new one was built."
                        result.messages.append(note); log.warning(note)
                        ledger.reset_workbook_flags()
                        ids = write_register(cfg.excel_path, ledger.pending_workbook(), runs_so_far)
                    else:
                        raise
                ledger.mark_in_workbook(ids)
        except WorkbookLocked:
            result.excel_ok = False
            note = ("The Excel register is open, so it could not be updated. Nothing is lost: close the workbook and click "
                    "'Process invoices' again (with an empty inbox) to add these invoices.")
            result.messages.append(note); log.warning(note)

        try:
            export_csv(ledger, cfg.output / "Invoice Register (all records).csv")
        except PermissionError:
            result.messages.append("A CSV export is open in another program and was not updated this run.")
        result.report_path = _write_report(cfg, result, started)
        ledger.finish_run(run_id, finished=datetime.now().strftime("%Y-%m-%d %H:%M:%S"), found=result.found, processed=result.processed,
                          review=result.review, duplicates=result.duplicates, failed=result.failed, skipped=result.skipped,
                          excel_ok=int(result.excel_ok), note=note)
        log.info("=== Run %s finished. %s", run_id, result.summary())
        progress("Finished. " + result.summary(), 1.0)
        return result
    finally:
        ledger.close()
        lock.unlink(missing_ok=True)


def export_csv(ledger: Ledger, path: Path) -> None:
    """Every record ever handled, one row each - for accounts software or audit. Built with pandas."""
    import pandas as pd
    rows = [dict(r) for r in ledger.db.execute("SELECT * FROM records ORDER BY id")]
    df = pd.DataFrame(rows, columns=["id", "status", "supplier", "invoice_number", "invoice_date", "due_date", "subtotal", "vat", "total",
                                     "currency", "issues", "reason", "original_name", "stored_path", "processed_at", "method", "duplicate_of"])
    df["issues"] = df["issues"].map(lambda j: "; ".join(i["message"] for i in json.loads(j or "[]") if i.get("message")))
    df["status"] = df["status"].str.replace("_", " ").str.title()
    df = df.rename(columns={"id": "Record ID", "status": "Status", "supplier": "Supplier", "invoice_number": "Invoice No", "invoice_date": "Invoice Date",
                            "due_date": "Due Date", "subtotal": "Net", "vat": "VAT", "total": "Total", "currency": "Currency", "issues": "Issues",
                            "reason": "Reason not added", "original_name": "Original file name", "stored_path": "Saved to", "processed_at": "Processed",
                            "method": "Read by", "duplicate_of": "Duplicate of record"})
    df.to_csv(path, index=False, encoding="utf-8-sig")
    items = []
    for r in rows:
        for n, it in enumerate(json.loads(r["line_items"]) if r.get("line_items") else [], 1):
            items.append({"Record ID": r["id"], "Supplier": r["supplier"], "Invoice No": r["invoice_number"], "Line": n,
                          "Description": it["description"], "Qty": it["qty"], "Unit price": it["unit_price"], "Amount": it["amount"],
                          "Lines add up": {"net": "Yes (to net)", "gross": "Yes (to total)"}.get(r.get("line_items_check"), "No")})
    pd.DataFrame(items, columns=["Record ID", "Supplier", "Invoice No", "Line", "Description", "Qty", "Unit price", "Amount", "Lines add up"]) \
        .to_csv(path.with_name("Line Items (all records).csv"), index=False, encoding="utf-8-sig")


def _write_report(cfg, result, started) -> Path:
    stamp = started.replace(":", "").replace("-", "").replace(" ", "_")
    p = cfg.logs_dir / f"Run report {stamp}.txt"
    L = [f"Invoice Processor - run report", f"Started: {started}", "", result.summary(), ""]
    for status, title in [("FAILED", "FAILED - could not be processed (files are in the 'Failed' folder)"),
                          ("DUPLICATE", "DUPLICATES - not added again (files are in the 'Duplicates' folder)"),
                          ("NEEDS_REVIEW", "NEEDS REVIEW - added to the register, please check (files are in 'Needs Review')")]:
        rows = [r for r in result.records if r["status"] == status]
        if rows:
            L.append(title)
            for r in rows:
                why = r["reason"] or "; ".join(i["message"] for i in r["issues"] if i["severity"] == REVIEW and i["message"])
                L.append(f"  - {r['original_name']}: {why}")
            L.append("")
    if result.ignored:
        L += ["Left in the inbox (not a PDF or image): " + ", ".join(result.ignored), ""]
    L += result.messages
    p.write_text("\n".join(L), encoding="utf-8")
    return p
