"""The Excel 'Invoice Register'.

Rows are APPENDED to the Invoices and Exceptions sheets, so anything staff type
into the workbook (for example 'Yes' in the Paid? column, or a note) is kept
between runs. The summary sheets contain only formulas and are rebuilt each run.
The file is saved to a temporary copy first and then swapped in, so a crash or
an open copy in Excel can never leave a half-written register.
"""
from __future__ import annotations

import os
from datetime import date, datetime
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.formatting.rule import CellIsRule, FormulaRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

FONT = "Arial"
NAVY = "1F3A5F"
HDR_FONT = Font(name=FONT, bold=True, color="FFFFFF")
HDR_FILL = PatternFill("solid", fgColor=NAVY)
INPUT_FILL = PatternFill("solid", fgColor="FFF2CC")
AMBER = PatternFill("solid", fgColor="FCE4C4")
RED = PatternFill("solid", fgColor="F8CBAD")
GREY = Side(style="thin", color="D9D9D9")
MONEY = '£#,##0.00;[Red]-£#,##0.00;"-"'
DATE = "dd/mm/yyyy"
MAXR = 50000  # formula ranges cover up to this many invoices

INV_COLS = [("Record ID", 9), ("Status", 14), ("Supplier", 32), ("Invoice No", 16), ("Invoice Date", 12), ("Due Date", 12),
            ("Net", 12), ("VAT", 11), ("Total", 12), ("Net + VAT", 12), ("Check", 11), ("VAT %", 8), ("Currency", 9),
            ("Paid?", 8), ("Issues / notes", 60), ("File", 34), ("Processed", 16), ("Read by", 8), ("Line items", 9), ("Lines add up?", 13)]
LINE_COLS = [("Record ID", 9), ("Supplier", 30), ("Invoice No", 16), ("Invoice Date", 12), ("Line", 6), ("Description", 50),
             ("Qty", 8), ("Unit price", 12), ("Amount", 12), ("Currency", 9), ("Lines add up?", 13)]
CHECK_LABEL = {"net": "Yes (net)", "gross": "Yes (total)", "mismatch": "No - check"}
EXC_COLS = [("Record ID", 9), ("Result", 12), ("File", 38), ("Why it was not added", 80), ("Supplier", 28), ("Invoice No", 14),
            ("Total", 12), ("File saved in", 40), ("Processed", 16)]
STATUS_LABEL = {"PROCESSED": "Processed", "NEEDS_REVIEW": "Needs Review", "DUPLICATE": "Duplicate", "FAILED": "Failed"}


class WorkbookLocked(Exception):
    pass


def _header(ws, cols, row=1):
    for i, (name, width) in enumerate(cols, 1):
        c = ws.cell(row, i, name); c.font = HDR_FONT; c.fill = HDR_FILL
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        ws.column_dimensions[get_column_letter(i)].width = width
    ws.freeze_panes = ws.cell(row + 1, 1)
    ws.row_dimensions[row].height = 22


def _d(s):
    return datetime.strptime(s, "%Y-%m-%d") if s else None


def _next_row(ws):
    r = ws.max_row
    while r > 1 and ws.cell(r, 1).value in (None, ""):
        r -= 1
    return r + 1


def _new_workbook():
    wb = Workbook()
    wb._named_styles["Normal"].font = Font(name=FONT, size=10)
    wb.active.title = "Monthly Summary"
    inv = wb.create_sheet("Invoices"); _header(inv, INV_COLS)
    exc = wb.create_sheet("Exceptions"); _header(exc, EXC_COLS)
    li = wb.create_sheet("Line Items"); _header(li, LINE_COLS)
    wb.create_sheet("Supplier Summary"); wb.create_sheet("Run History")
    _how_to(wb.create_sheet("How To Use"))
    rng = f"$A$2:$R${MAXR}"
    inv.conditional_formatting.add(f"B2:B{MAXR}", CellIsRule(operator="equal", formula=['"Needs Review"'], fill=AMBER))
    inv.conditional_formatting.add(f"K2:K{MAXR}", CellIsRule(operator="equal", formula=['"MISMATCH"'], fill=RED))
    inv.conditional_formatting.add(f"N2:N{MAXR}", FormulaRule(formula=['AND($A2<>"",$N2="")'], fill=INPUT_FILL))
    return wb


def _how_to(ws):
    lines = [("How to use this register", True),
             ("This workbook is updated automatically by the Invoice Processor each time you click 'Process invoices'.", False), ("", False),
             ("Monthly Summary - totals per month and what is due for payment. Do not type in this sheet; it is rebuilt each run.", False),
             ("Invoices - one row per invoice that was read successfully. Rows marked 'Needs Review' are included in the totals but", False),
             ("   should be checked against the PDF (click the file name). The 'Issues / notes' column says what to check.", False),
             ("   You CAN type in the 'Paid?' column (enter Yes when paid) and correct values - your changes are kept.", False),
             ("   'Check' compares Net + VAT with the printed Total. MISMATCH means the supplier's figures do not add up.", False),
             ("Exceptions - files that were NOT added: duplicates and files that could not be read, with the reason.", False),
             ("Line Items - the individual lines of each invoice (description, quantity, unit price, amount). 'Lines add up?' says", False),
             ("   whether they add up to the invoice's net or total; if not, some lines may not have been read (the totals are unaffected).", False),
             ("Supplier Summary - totals per supplier. Rebuilt each run.", False),
             ("Run History - one line per run.", False), ("", False),
             ("Please close this workbook before running the Invoice Processor. If it is open, your invoices are still processed", False),
             ("and saved, and they will be added to this workbook the next time you run it with the workbook closed.", False),
             ("Totals in the summaries include GBP invoices only. Invoices in other currencies appear in the Invoices sheet.", False)]
    ws.column_dimensions["A"].width = 120
    for i, (t, b) in enumerate(lines, 1):
        ws.cell(i, 1, t).font = Font(name=FONT, bold=b, size=14 if b else 10, color=NAVY if b else "000000")


def _upgrade(wb):
    """Registers made by an older version: add the new columns / sheet."""
    inv = wb["Invoices"]
    for i, (name, width) in enumerate(INV_COLS, 1):
        if inv.cell(1, i).value in (None, ""):
            c = inv.cell(1, i, name); c.font = HDR_FONT; c.fill = HDR_FILL
            c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            inv.column_dimensions[get_column_letter(i)].width = width
    if "Line Items" not in wb.sheetnames:
        _header(wb.create_sheet("Line Items", wb.sheetnames.index("Exceptions") + 1), LINE_COLS)


def _append(wb, records):
    _upgrade(wb)
    inv, exc, li = wb["Invoices"], wb["Exceptions"], wb["Line Items"]
    ri, re_, rl = _next_row(inv), _next_row(exc), _next_row(li)
    border = Border(bottom=GREY)
    for r in records:
        issues = "; ".join(i["message"] for i in r["issues"])
        processed = datetime.strptime(r["processed_at"], "%Y-%m-%d %H:%M:%S")
        if r["status"] in ("PROCESSED", "NEEDS_REVIEW"):
            row = ri; ri += 1
            vals = [r["id"], STATUS_LABEL[r["status"]], r["supplier"], r["invoice_number"], _d(r["invoice_date"]), _d(r["due_date"]),
                    r["subtotal"], r["vat"], r["total"], f'=IF(OR(G{row}="",H{row}=""),"",G{row}+H{row})',
                    f'=IF(OR(I{row}="",J{row}=""),"",IF(ABS(J{row}-I{row})<=0.02,"OK","MISMATCH"))',
                    f'=IF(OR(G{row}="",G{row}=0,H{row}=""),"",H{row}/G{row})', r["currency"], None, issues,
                    Path(r["stored_path"]).name, processed, "OCR" if r["method"] == "ocr" else "Text",
                    len(r["line_items"]) if r.get("line_items") else 0, CHECK_LABEL.get(r.get("line_items_check"), "-")]
            for c, v in enumerate(vals, 1):
                cell = inv.cell(row, c, v); cell.border = border
            for c in (5, 6): inv.cell(row, c).number_format = DATE
            for c in (7, 8, 9, 10): inv.cell(row, c).number_format = MONEY
            inv.cell(row, 12).number_format = "0.0%"; inv.cell(row, 17).number_format = "dd/mm/yyyy hh:mm"
            inv.cell(row, 15).alignment = Alignment(wrap_text=True, vertical="top")
            f = inv.cell(row, 16); f.hyperlink = Path(r["stored_path"]).resolve().as_uri(); f.font = Font(name=FONT, color="0563C1", underline="single")
            for n, it in enumerate(r.get("line_items") or [], 1):
                vals = [r["id"], r["supplier"], r["invoice_number"], _d(r["invoice_date"]), n, it["description"], it["qty"], it["unit_price"],
                        it["amount"], r["currency"], CHECK_LABEL.get(r.get("line_items_check"), "-")]
                for c, v in enumerate(vals, 1):
                    cell = li.cell(rl, c, v); cell.border = border
                li.cell(rl, 4).number_format = DATE
                for c in (8, 9): li.cell(rl, c).number_format = '#,##0.00;[Red]-#,##0.00;"-"'
                rl += 1
        else:
            row = re_; re_ += 1
            reason = r["reason"] or issues
            vals = [r["id"], STATUS_LABEL[r["status"]], r["original_name"], reason, r["supplier"], r["invoice_number"], r["total"],
                    str(Path(r["stored_path"]).parent), processed]
            for c, v in enumerate(vals, 1):
                cell = exc.cell(row, c, v); cell.border = border
            exc.cell(row, 4).alignment = Alignment(wrap_text=True, vertical="top")
            exc.cell(row, 7).number_format = MONEY; exc.cell(row, 9).number_format = "dd/mm/yyyy hh:mm"
            f = exc.cell(row, 3); f.hyperlink = Path(r["stored_path"]).resolve().as_uri(); f.font = Font(name=FONT, color="0563C1", underline="single")
    inv.auto_filter.ref = f"A1:T{max(ri - 1, 2)}"; exc.auto_filter.ref = f"A1:I{max(re_ - 1, 2)}"; li.auto_filter.ref = f"A1:K{max(rl - 1, 2)}"


def _replace_sheet(wb, name):
    idx = wb.sheetnames.index(name) if name in wb.sheetnames else len(wb.sheetnames)
    if name in wb.sheetnames:
        del wb[name]
    return wb.create_sheet(name, idx)


def _summaries(wb, runs):
    inv = wb["Invoices"]
    dates, suppliers = [], []
    for row in inv.iter_rows(min_row=2, values_only=True):
        if row[0] in (None, ""): continue
        if isinstance(row[4], (datetime, date)): dates.append(row[4])
        if row[2]: suppliers.append(row[2])
    months = sorted({datetime(d.year, d.month, 1) for d in dates})
    I = lambda col: f"Invoices!${col}$2:${col}${MAXR}"
    GBP = f'{I("M")},"GBP"'

    # ---------------- Monthly Summary
    ws = _replace_sheet(wb, "Monthly Summary")
    ws.sheet_view.showGridLines = False
    ws["A1"] = "Supplier invoices - monthly summary"; ws["A1"].font = Font(name=FONT, size=16, bold=True, color=NAVY)
    ws["A2"] = "Rebuilt automatically on every run. GBP invoices only. 'Unpaid' = Paid? column not set to Yes."; ws["A2"].font = Font(name=FONT, italic=True, color="595959")
    cols = [("Month", 14), ("Invoices", 10), ("Net", 14), ("VAT", 13), ("Total", 14), ("Needs review", 12), ("Credit notes", 13), ("Unpaid total", 14)]
    _header(ws, cols, row=4)
    r = 5
    for m in months:
        rng = f'{I("E")},">="&$A{r},{I("E")},"<"&EDATE($A{r},1),{GBP}'
        ws.cell(r, 1, m).number_format = "mmm yyyy"
        ws.cell(r, 2, f"=COUNTIFS({rng})")
        ws.cell(r, 3, f"=SUMIFS({I('G')},{rng})"); ws.cell(r, 4, f"=SUMIFS({I('H')},{rng})"); ws.cell(r, 5, f"=SUMIFS({I('I')},{rng})")
        ws.cell(r, 6, f'=COUNTIFS({rng},{I("B")},"Needs Review")')
        ws.cell(r, 7, f'=SUMIFS({I("I")},{rng},{I("I")},"<0")')
        ws.cell(r, 8, f'=SUMIFS({I("I")},{rng},{I("N")},"<>Yes")')
        r += 1
    first, last = 5, r - 1
    ws.cell(r, 1, "Total").font = Font(name=FONT, bold=True)
    for c in range(2, 9):
        L = get_column_letter(c)
        cell = ws.cell(r, c, f"=SUM({L}{first}:{L}{last})" if last >= first else 0); cell.font = Font(name=FONT, bold=True)
        cell.border = Border(top=Side(style="medium"))
    for rr in range(first, r + 1):
        for c in (3, 4, 5, 7, 8): ws.cell(rr, c).number_format = MONEY
    ws.cell(r + 1, 1, "Check: total of all GBP invoices in the Invoices sheet").font = Font(name=FONT, size=8, color="808080")
    ws.cell(r + 1, 5, f"=SUMIFS({I('I')},{GBP})").number_format = MONEY
    ws.cell(r + 1, 6, f'=IF(ABS(E{r+1}-E{r})<0.005,"OK","Some invoices have no date")').font = Font(name=FONT, size=8, color="808080")
    ws.cell(r + 1, 5).font = Font(name=FONT, size=8, color="808080")

    b = r + 4
    ws.cell(b, 1, "Payments due (unpaid GBP invoices)").font = Font(name=FONT, size=13, bold=True, color=NAVY)
    _header(ws, [("", 14), ("Invoices", 10), ("Amount", 14)], row=b + 1); ws.freeze_panes = "A5"
    unpaid = f'{I("N")},"<>Yes",{GBP},{I("I")},">0"'
    blocks = [("Overdue", f'{I("F")},"<"&TODAY()'),
              ("Due in next 7 days", f'{I("F")},">="&TODAY(),{I("F")},"<"&TODAY()+7'),
              ("Due in 8-30 days", f'{I("F")},">="&TODAY()+7,{I("F")},"<"&TODAY()+31'),
              ("Due later", f'{I("F")},">="&TODAY()+31')]
    for k, (lab, crit) in enumerate(blocks, b + 2):
        ws.cell(k, 1, lab)
        ws.cell(k, 2, f"=COUNTIFS({crit},{unpaid})")
        ws.cell(k, 3, f"=SUMIFS({I('I')},{crit},{unpaid})").number_format = MONEY
    ws.cell(b + 6, 1, "Calculated against today's date each time the workbook is opened.").font = Font(name=FONT, size=8, italic=True, color="808080")
    ws.column_dimensions["A"].width = 22

    # ---------------- Supplier Summary
    ws = _replace_sheet(wb, "Supplier Summary")
    ws["A1"] = "Supplier summary (GBP invoices)"; ws["A1"].font = Font(name=FONT, size=14, bold=True, color=NAVY)
    _header(ws, [("Supplier", 36), ("Invoices", 10), ("Net", 14), ("VAT", 13), ("Total", 14), ("Unpaid", 14), ("Latest invoice", 14)], row=3)
    tot_by = {}
    for s in suppliers: tot_by[s] = tot_by.get(s, 0) + 1
    for k, s in enumerate(sorted(tot_by, key=str.lower), 4):
        crit = f'{I("C")},$A{k},{GBP}'
        ws.cell(k, 1, s); ws.cell(k, 2, f"=COUNTIFS({crit})")
        ws.cell(k, 3, f"=SUMIFS({I('G')},{crit})"); ws.cell(k, 4, f"=SUMIFS({I('H')},{crit})"); ws.cell(k, 5, f"=SUMIFS({I('I')},{crit})")
        ws.cell(k, 6, f'=SUMIFS({I("I")},{crit},{I("N")},"<>Yes")')
        ws.cell(k, 7, f'=IFERROR(_xlfn.MAXIFS({I("E")},{I("C")},$A{k}),"")').number_format = DATE
        for c in (3, 4, 5, 6): ws.cell(k, c).number_format = MONEY

    # ---------------- Run History
    ws = _replace_sheet(wb, "Run History")
    _header(ws, [("Run started", 18), ("Files found", 11), ("Processed", 11), ("Needs review", 12), ("Duplicates", 11), ("Failed", 9),
                 ("Skipped (still copying)", 14), ("Note", 70)])
    for k, run in enumerate(runs, 2):
        vals = [datetime.strptime(run["started"], "%Y-%m-%d %H:%M:%S"), run["found"], run["processed"], run["review"], run["duplicates"],
                run["failed"], run["skipped"], run["note"]]
        for c, v in enumerate(vals, 1): ws.cell(k, c, v)
        ws.cell(k, 1).number_format = "dd/mm/yyyy hh:mm"


def write_register(path: Path, records, runs) -> list[int]:
    """Append `records` and rebuild summaries. Returns the ids written. Raises WorkbookLocked if Excel has the file open."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            wb = load_workbook(path)
            if "Invoices" not in wb.sheetnames or "Exceptions" not in wb.sheetnames:
                raise ValueError("register sheets missing")
        except PermissionError:
            raise WorkbookLocked()
        except Exception:
            # Unreadable / not ours: keep it aside, never overwrite it
            aside = path.with_name(f"{path.stem} (unreadable {datetime.now():%Y%m%d-%H%M%S}){path.suffix}")
            os.replace(path, aside)
            raise RuntimeError(f"REBUILD:{aside.name}")
    else:
        wb = _new_workbook()
    _append(wb, records)
    _summaries(wb, runs)
    wb.active = 0
    wb.calculation.fullCalcOnLoad = True
    tmp = path.with_name(f"~tmp_{path.name}")
    wb.save(tmp)
    try:
        os.replace(tmp, path)
    except PermissionError:
        tmp.unlink(missing_ok=True)
        raise WorkbookLocked()
    return [r["id"] for r in records]
