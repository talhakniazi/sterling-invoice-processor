"""End-to-end tests: the acceptance criteria from the client brief, run against the sample invoices."""
import shutil
import time
from pathlib import Path

import openpyxl
import pytest

from conftest import TODAY, copy_samples
from invoice_processor import processor as proc
from invoice_processor import workbook as wbmod
from invoice_processor.processor import AlreadyRunning, run

FIELDS = ["supplier", "invoice_number", "invoice_date", "due_date", "subtotal", "vat", "total"]
import json as _json
from conftest import DATA
N_INVOICES = sum(t["expected_status"] in ("PROCESSED", "NEEDS_REVIEW") for t in _json.loads((DATA / "ground_truth.json").read_text()).values())


def _invoice_rows(cfg):
    ws = openpyxl.load_workbook(cfg.excel_path)["Invoices"]
    return [r for r in ws.iter_rows(min_row=2, values_only=True) if r[0] is not None]


def _exception_rows(cfg):
    ws = openpyxl.load_workbook(cfg.excel_path)["Exceptions"]
    return [r for r in ws.iter_rows(min_row=2, values_only=True) if r[0] is not None]


# ---------------------------------------------------------------- acceptance: accuracy & routing
def test_every_sample_gets_the_expected_status_and_flags(first_run, truth):
    got = {r["original_name"]: r for r in first_run.records}
    assert set(got) == set(truth), "every invoice file must be handled"
    for name, t in truth.items():
        r = got[name]
        assert r["status"] == t["expected_status"], (name, r["status"], r.get("reason"))
        codes = [i["code"] for i in r["issues"]]
        for f in t["expected_flags"]:
            assert f in codes, (name, f, codes)


def test_field_extraction_accuracy(first_run, truth):
    """At least 20 different invoices must be read correctly - we require every field of every readable invoice."""
    got = {r["original_name"]: r for r in first_run.records}
    checked, wrong = 0, []
    for name, t in truth.items():
        if t["expected_status"] == "FAILED":
            continue
        for f in FIELDS:
            if name == "photo_of_invoice.png" and f == "invoice_number":
                continue  # O vs 0 in a photo: app deliberately sends to review instead of guessing (asserted above)
            checked += 1
            if got[name][f] != t[f]:
                wrong.append((name, f, got[name][f], t[f]))
    assert not wrong, wrong
    assert checked >= 20 * len(FIELDS)


def test_line_items(first_run, truth, cfg):
    got = {r["original_name"]: r for r in first_run.records}
    wrong = []
    for name, t in truth.items():
        if t.get("lines") is None or t["expected_status"] in ("FAILED", "DUPLICATE"):
            continue
        r = got[name]
        amounts = sorted(round(i["amount"], 2) for i in (r.get("line_items") or []))
        if amounts != sorted(t["lines"]) or r.get("line_items_check") not in ("net", "gross"):
            wrong.append((name, amounts, r.get("line_items_check")))
    assert not wrong, wrong
    ws = openpyxl.load_workbook(cfg.excel_path)["Line Items"]
    rows = [r for r in ws.iter_rows(min_row=2, values_only=True) if r[0] is not None]
    expected = sum(len(t["lines"]) for t in truth.values() if t.get("lines") and t["expected_status"] in ("PROCESSED", "NEEDS_REVIEW"))
    assert len(rows) == expected
    assert all(r[5] for r in rows), "every line has a description"


def test_european_invoices_read_correctly(first_run, truth):
    got = {r["original_name"]: r for r in first_run.records}
    eu = [n for n in truth if n.startswith(("facture", "rechnung", "factuur", "factura", "fattura", "fatura", "SCAN_rechnung"))]
    assert len(eu) == 7
    for n in eu:
        assert got[n]["currency"] == "EUR" and got[n]["language"] in ("fr", "de", "nl", "es", "it", "pt"), n


def test_vat_and_totals_add_up_in_register(first_run, cfg, truth):
    for r in _invoice_rows(cfg):
        net, vat, total = r[6], r[7], r[8]
        if r[3] == "P-4410":
            assert abs(net + vat - total) > 1  # the deliberately wrong supplier invoice keeps its printed figures
        else:
            assert abs(net + vat - total) <= 0.02, r
        assert str(r[9]).startswith("=") and str(r[10]).startswith("=IF"), "Net+VAT and Check must be live formulas"


def test_nothing_is_silently_discarded(first_run, cfg, truth):
    out_files = [p for d in (cfg.processed_dir, cfg.review_dir, cfg.duplicate_dir, cfg.failed_dir) for p in d.iterdir()]
    assert len(out_files) == len(truth)
    assert [p.name for p in cfg.inbox.iterdir()] == ["notes.txt"], "non-invoice files are left alone"
    fails = {r[2]: r[3] for r in _exception_rows(cfg) if r[1] == "Failed"}
    assert set(fails) == {"corrupted_download.pdf", "delivery_note_DN-5521.pdf", "kestrel_protected.pdf"}
    assert "password" in fails["kestrel_protected.pdf"].lower()
    assert all(len(v) > 30 for v in fails.values()), "every failure has a plain-English reason"
    assert (cfg.logs_dir / "processing.log").read_text().count("FAILED") >= 3
    assert first_run.report_path.exists() and "kestrel_protected.pdf" in first_run.report_path.read_text()


def test_duplicates_detected(first_run):
    dups = {r["original_name"]: r for r in first_run.records if r["status"] == "DUPLICATE"}
    assert "already processed" in dups["Fwd_ invoice from Northgate (resent).pdf"]["reason"]
    assert "amounts are different" in dups["clearline_CT-2026-0519_revised.pdf"]["reason"]


def test_csv_export(first_run, cfg, truth):
    import pandas as pd
    df = pd.read_csv(cfg.output / "Invoice Register (all records).csv")
    assert len(df) == len(truth) and set(df["Status"]) == {"Processed", "Needs Review", "Duplicate", "Failed"}


# ---------------------------------------------------------------- acceptance: safe to re-run
def test_rerun_with_empty_inbox_adds_nothing(first_run, cfg):
    before = len(_invoice_rows(cfg))
    r = run(cfg, today=TODAY)
    assert r.found == 0 and len(_invoice_rows(cfg)) == before


def test_rerun_with_same_files_does_not_duplicate_records(first_run, cfg):
    before_inv, before_exc = len(_invoice_rows(cfg)), len(_exception_rows(cfg))
    for p in list(cfg.processed_dir.iterdir())[:5]:
        shutil.copy2(p, cfg.inbox / ("again_" + p.name))
    r = run(cfg, today=TODAY)
    assert r.duplicates == 5 and r.processed == 0
    assert len(_invoice_rows(cfg)) == before_inv
    assert len(_exception_rows(cfg)) == before_exc + 5


def test_staff_edits_in_register_are_kept(first_run, cfg):
    wb = openpyxl.load_workbook(cfg.excel_path)
    wb["Invoices"]["N2"] = "Yes"; wb["Invoices"]["O3"] = "Checked by Sam"
    wb.save(cfg.excel_path)
    copy_samples(cfg.inbox, names=None)  # all duplicates now, but triggers a rewrite of the register
    run(cfg, today=TODAY)
    ws = openpyxl.load_workbook(cfg.excel_path)["Invoices"]
    assert ws["N2"].value == "Yes" and ws["O3"].value == "Checked by Sam"


# ---------------------------------------------------------------- robustness
def test_excel_open_during_run_loses_nothing(cfg, monkeypatch, truth):
    copy_samples(cfg.inbox)
    real = wbmod.os.replace
    monkeypatch.setattr(wbmod.os, "replace", lambda *a: (_ for _ in ()).throw(PermissionError("locked")))
    r = run(cfg, today=TODAY)
    assert not r.excel_ok and any("Excel register is open" in m for m in r.messages)
    monkeypatch.setattr(wbmod.os, "replace", real)
    run(cfg, today=TODAY)      # workbook closed again
    assert len(_invoice_rows(cfg)) == N_INVOICES and len(_exception_rows(cfg)) == 5


def test_deleted_register_is_rebuilt(first_run, cfg):
    cfg.excel_path.unlink()
    run(cfg, today=TODAY)
    assert len(_invoice_rows(cfg)) == N_INVOICES


def test_unreadable_register_is_set_aside_not_overwritten(first_run, cfg):
    cfg.excel_path.write_bytes(b"not an excel file")
    r = run(cfg, today=TODAY)
    assert any("renamed" in m for m in r.messages)
    assert len(list(cfg.output.glob("Invoice Register (unreadable*"))) == 1
    assert len(_invoice_rows(cfg)) == N_INVOICES


def test_scans_without_ocr_fail_clearly_then_retry(cfg, monkeypatch):
    copy_samples(cfg.inbox, names={"SCAN_0001.pdf"})
    monkeypatch.setattr("invoice_processor.reader.find_tesseract", lambda *_: None)
    r = run(cfg, today=TODAY)
    assert r.failed == 1 and "Tesseract" in r.records[0]["reason"]
    monkeypatch.undo()
    shutil.move(str(cfg.failed_dir / "SCAN_0001.pdf"), str(cfg.inbox / "SCAN_0001.pdf"))
    r = run(cfg, today=TODAY)
    assert r.processed == 1, "a previously failed file can be retried once the problem is fixed"


def test_file_still_being_copied_is_left_for_next_run(cfg):
    copy_samples(cfg.inbox, names={"northgate_INV-10422.pdf"})
    p = cfg.inbox / "northgate_INV-10422.pdf"; now = time.time(); import os; os.utime(p, (now, now))
    cfg.min_file_age_seconds = 60
    r = run(cfg, today=TODAY)
    assert r.skipped == 1 and p.exists()


def test_file_locked_by_another_program_stays_in_inbox(cfg, monkeypatch):
    copy_samples(cfg.inbox, names={"northgate_INV-10422.pdf", "northgate_INV-10587.pdf"})
    real = proc.shutil.move
    def move(src, dst):
        if "10422" in src: raise PermissionError("in use")
        return real(src, dst)
    monkeypatch.setattr(proc.shutil, "move", move)
    r = run(cfg, today=TODAY)
    assert r.processed == 1 and (cfg.inbox / "northgate_INV-10422.pdf").exists()
    monkeypatch.undo()
    r = run(cfg, today=TODAY)
    assert r.processed == 1 and r.duplicates == 0, "retried cleanly next run, not marked duplicate"


def test_unexpected_crash_on_one_file_does_not_stop_the_batch(cfg, monkeypatch):
    copy_samples(cfg.inbox, names={"northgate_INV-10422.pdf", "penmark_88123.pdf"})
    real = proc.extract
    def boom(src, *a, **k):
        if "Penmark" in " ".join(l.text for l in src):
            raise ValueError("boom")
        return real(src, *a, **k)
    monkeypatch.setattr(proc, "extract", boom)
    r = run(cfg, today=TODAY)
    assert r.processed == 1 and r.failed == 1
    assert (cfg.failed_dir / "penmark_88123.pdf").exists() and "Unexpected error" in [x for x in r.records if x["status"] == "FAILED"][0]["reason"]


def test_two_runs_at_once_are_prevented(cfg):
    cfg.ensure_folders(); (cfg.logs_dir / ".running.lock").write_text("x")
    with pytest.raises(AlreadyRunning):
        run(cfg, today=TODAY)
