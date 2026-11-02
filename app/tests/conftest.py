import json
import os
import shutil
import sys
import time
from datetime import date
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP))
DATA = Path(__file__).resolve().parent / "data"
TODAY = date(2026, 11, 2)   # fixed "today" so date checks are repeatable


@pytest.fixture(scope="session")
def truth():
    return json.loads((DATA / "ground_truth.json").read_text())


def copy_samples(dest: Path, names=None):
    dest.mkdir(parents=True, exist_ok=True)
    src = DATA / "sample_invoices"
    # Files arrive in a defined order: originals first, then the copies that are expected to be
    # detected as duplicates (resent / re-issued). Timestamps from a git checkout cannot be relied on.
    truth = json.loads((DATA / "ground_truth.json").read_text())
    later = lambda p: truth.get(p.name, {}).get("expected_status") == "DUPLICATE"
    for k, f in enumerate(sorted(src.iterdir(), key=lambda p: (later(p), p.name.lower()))):
        if names and f.name not in names:
            continue
        shutil.copy2(f, dest / f.name)
        t = time.time() - 3600 + k
        os.utime(dest / f.name, (t, t))


@pytest.fixture
def cfg(tmp_path):
    from invoice_processor.config import Config
    c = Config(inbox_folder=str(tmp_path / "inbox"), output_folder=str(tmp_path / "out"), min_file_age_seconds=0)
    return c


@pytest.fixture
def first_run(cfg, tmp_path):
    from invoice_processor.processor import run
    copy_samples(cfg.inbox)
    return run(cfg, today=TODAY)
