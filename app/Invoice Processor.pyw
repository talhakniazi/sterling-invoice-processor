"""Double-click entry point (Windows runs .pyw files without a console window)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from invoice_processor.gui import main  # noqa: E402

if __name__ == "__main__":
    main()
