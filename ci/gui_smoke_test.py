"""Real-GUI test, run on each operating system in CI.
Opens the actual window, clicks 'Process invoices' on the sample set, waits, checks the
result tiles, and saves screenshots of the window (proof it rendered on that OS)."""
import json, platform, shutil, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
from invoice_processor.config import Config      # noqa: E402
from invoice_processor.gui import App             # noqa: E402

OUT = ROOT / "ci-output"; OUT.mkdir(exist_ok=True)
work = OUT / "work"; shutil.rmtree(work, ignore_errors=True); (work / "inbox").mkdir(parents=True)
for f in (ROOT / "app/tests/data/sample_invoices").iterdir():
    shutil.copy2(f, work / "inbox" / f.name)
cfg = Config(inbox_folder=str(work / "inbox"), output_folder=str(work / "out"), min_file_age_seconds=0)
cfg.save = lambda *a, **k: None
truth = json.loads((ROOT / "app/tests/data/ground_truth.json").read_text())
expect = {"Processed": sum(t["expected_status"] == "PROCESSED" for t in truth.values()),
          "Needs review": sum(t["expected_status"] == "NEEDS_REVIEW" for t in truth.values()),
          "Duplicates": sum(t["expected_status"] == "DUPLICATE" for t in truth.values()),
          "Failed": sum(t["expected_status"] == "FAILED" for t in truth.values())}
app = App(cfg); app.geometry("820x660+40+40")
result = {"os": platform.platform(), "python": sys.version.split()[0]}


def shot(name):
    app.update(); time.sleep(0.5); app.update()
    try:
        from PIL import ImageGrab
        x, y = app.winfo_rootx(), app.winfo_rooty()
        ImageGrab.grab(bbox=(x, y, x + app.winfo_width(), y + app.winfo_height())).save(OUT / f"{name}.png")
    except Exception as e:  # screenshots are evidence only; never fail the test on them
        result.setdefault("screenshot_errors", []).append(f"{name}: {e}")


def start():
    shot("1_ready")
    app.btn.invoke()
    t0 = time.time()
    def wait():
        if str(app.btn["state"]) == "disabled" and time.time() - t0 < 600:
            app.after(300, wait); return
        shot("2_finished")
        result["tiles"] = {k: v.get() for k, v in app.tile_vals.items()}
        result["status"] = app.status.cget("text")
        result["expected"] = {k: str(v) for k, v in expect.items()}
        result["ok"] = result["tiles"] == result["expected"]
        app.destroy()
    app.after(500, wait)


app.after(800, start)
app.mainloop()
(OUT / f"gui_result_{platform.system()}.json").write_text(json.dumps(result, indent=1))
print(json.dumps(result, indent=1))
sys.exit(0 if result.get("ok") else 1)
