"""Settings, stored in settings.json next to the application. Editable from the app's Settings window."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
SETTINGS_FILE = APP_DIR / "settings.json"


@dataclass
class Config:
    inbox_folder: str = str(APP_DIR / "Invoices Inbox")
    output_folder: str = str(APP_DIR / "Invoice Output")
    excel_file_name: str = "Invoice Register.xlsx"
    # Your own company name(s). Used only so the app never mistakes YOU (the buyer) for the supplier.
    our_company_names: list[str] = field(default_factory=lambda: ["Sterling Office Supplies Ltd"])
    dates_are_day_first: bool = True            # UK style 03/04/2026 = 3 April
    standard_vat_rates: list[float] = field(default_factory=lambda: [0.0, 5.0, 20.0])
    rounding_tolerance: float = 0.02            # £ difference allowed when checking Net + VAT = Total
    high_value_threshold: float = 10000.0       # invoices above this go to Needs Review
    old_invoice_days: int = 365                 # invoices older than this are flagged
    ocr_enabled: bool = True
    tesseract_path: str = ""                    # leave blank to auto-detect
    include_subfolders: bool = False
    min_file_age_seconds: int = 5               # skip files still being copied/saved
    max_pages: int = 30

    # ---- derived paths
    @property
    def output(self) -> Path: return Path(self.output_folder)
    @property
    def inbox(self) -> Path: return Path(self.inbox_folder)
    @property
    def processed_dir(self) -> Path: return self.output / "Processed"
    @property
    def review_dir(self) -> Path: return self.output / "Needs Review"
    @property
    def failed_dir(self) -> Path: return self.output / "Failed"
    @property
    def duplicate_dir(self) -> Path: return self.output / "Duplicates"
    @property
    def logs_dir(self) -> Path: return self.output / "Logs"
    @property
    def excel_path(self) -> Path: return self.output / self.excel_file_name
    @property
    def ledger_path(self) -> Path: return self.output / "Logs" / "ledger.sqlite"

    def ensure_folders(self) -> None:
        for p in (self.inbox, self.processed_dir, self.review_dir, self.failed_dir, self.duplicate_dir, self.logs_dir):
            p.mkdir(parents=True, exist_ok=True)

    def save(self, path: Path = SETTINGS_FILE) -> None:
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path = SETTINGS_FILE) -> "Config":
        if not path.exists():
            cfg = cls(); cfg.save(path); return cfg
        data = json.loads(path.read_text(encoding="utf-8"))
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)
