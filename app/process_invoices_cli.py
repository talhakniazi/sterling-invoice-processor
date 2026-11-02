"""Command-line version (for IT / scheduled tasks).

    python process_invoices_cli.py                 # uses settings.json
    python process_invoices_cli.py --inbox D:\\Invoices\\Inbox --output D:\\Invoices\\Output

Exit code: 0 = all processed, 1 = some need review/duplicates/failed, 2 = could not run.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from invoice_processor.config import Config  # noqa: E402
from invoice_processor.processor import AlreadyRunning, run  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="Process supplier invoices from the inbox folder into the Excel register.")
    ap.add_argument("--inbox"); ap.add_argument("--output")
    a = ap.parse_args()
    cfg = Config.load()
    if a.inbox: cfg.inbox_folder = a.inbox
    if a.output: cfg.output_folder = a.output
    try:
        res = run(cfg, progress=lambda m, f=None: print(m) if not m.startswith("Reading") else None)
    except AlreadyRunning as e:
        print(e); return 2
    print(res.summary())
    for m in res.messages: print("WARNING:", m)
    if res.report_path: print("Report:", res.report_path)
    return 0 if (res.review + res.duplicates + res.failed) == 0 and res.excel_ok else 1


if __name__ == "__main__":
    sys.exit(main())
