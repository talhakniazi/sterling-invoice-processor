"""Simple window for non-technical staff: one big button, clear results."""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .config import Config
from .processor import AlreadyRunning, run
from .reader import find_tesseract

NAVY = "#1F3A5F"
UI = "Segoe UI"  # replaced at start-up by the best font installed
TILE = {"Processed": "#1e7b4a", "Needs review": "#b06a00", "Duplicates": "#5a5a5a", "Failed": "#b3261e"}


def open_path(p: Path):
    p = Path(p)
    if not p.exists():
        messagebox.showinfo("Not found yet", f"{p}\n\ndoes not exist yet. It is created the first time you process invoices."); return
    if sys.platform.startswith("win"):
        os.startfile(str(p))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(p)])
    else:
        subprocess.Popen(["xdg-open", str(p)])


class App(tk.Tk):
    def __init__(self, cfg: Config | None = None):
        super().__init__()
        self.cfg = cfg or Config.load()
        try:
            self.cfg.ensure_folders()   # so 'Open' works before the first run
        except OSError as e:
            messagebox.showwarning("Folders", f"Could not create the invoice folders:\n{e}\n\nUse 'Change…' to pick folders you can write to.")
        self.title("Invoice Processor")
        self.geometry("780x620"); self.minsize(700, 560)
        self.configure(bg="white")
        self.q: queue.Queue = queue.Queue()
        self._style()
        self._build()
        self._refresh_paths()
        self._check_ocr()
        self.after(150, self._poll)

    def _style(self):
        global UI
        from tkinter import font as tkfont
        fams = set(tkfont.families(self))
        F = next((f for f in ("Segoe UI", "Helvetica Neue", "Arial", "Liberation Sans", "DejaVu Sans") if f in fams), "TkDefaultFont")
        UI = F
        s = ttk.Style(self)
        try: s.theme_use("clam")
        except tk.TclError: pass
        s.configure(".", font=(UI, 10), background="white")
        s.configure("TFrame", background="white"); s.configure("TLabel", background="white")
        s.configure("Head.TLabel", font=(UI, 18, "bold"), foreground=NAVY)
        s.configure("Sub.TLabel", foreground="#555555")
        s.configure("Path.TLabel", foreground="#333333", font=(UI, 9))
        s.configure("Big.TButton", font=(UI, 13, "bold"), padding=(18, 10), foreground="white", background=NAVY)
        s.map("Big.TButton", background=[("disabled", "#9aa7b5"), ("active", "#2b5582")])
        s.configure("Warn.TLabel", background="#fff4ce", foreground="#6b4e00", padding=8)
        s.configure("Horizontal.TProgressbar", troughcolor="#eef1f5", background=NAVY)

    def _build(self):
        root = ttk.Frame(self, padding=18); root.pack(fill="both", expand=True)
        ttk.Label(root, text="Invoice Processor", style="Head.TLabel").pack(anchor="w")
        company = self.cfg.our_company_names[0] if self.cfg.our_company_names else ""
        ttk.Label(root, text=f"{company}  ·  reads supplier invoices and updates the Excel register", style="Sub.TLabel").pack(anchor="w", pady=(0, 10))

        self.warn = ttk.Label(root, style="Warn.TLabel", wraplength=720)

        box = ttk.LabelFrame(root, text=" Folders ", padding=10); box.pack(fill="x", pady=(0, 12))
        self.inbox_var, self.out_var = tk.StringVar(), tk.StringVar()
        for r, (label, var, which) in enumerate([("Put new invoices in:", self.inbox_var, "inbox"), ("Results go to:", self.out_var, "output")]):
            ttk.Label(box, text=label, width=20).grid(row=r, column=0, sticky="w", pady=3)
            ttk.Label(box, textvariable=var, style="Path.TLabel", width=58).grid(row=r, column=1, sticky="w")
            ttk.Button(box, text="Open", command=lambda w=which: open_path(self.cfg.inbox if w == "inbox" else self.cfg.output)).grid(row=r, column=2, padx=4)
            ttk.Button(box, text="Change…", command=lambda w=which: self._change(w)).grid(row=r, column=3)
        box.columnconfigure(1, weight=1)

        self.btn = ttk.Button(root, text="▶  Process invoices", style="Big.TButton", command=self._start)
        self.btn.pack(pady=(4, 8))
        self.bar = ttk.Progressbar(root, mode="determinate", maximum=1.0); self.bar.pack(fill="x")
        self.status = ttk.Label(root, text="Ready. Put invoice PDFs in the inbox folder, then click the button.", style="Sub.TLabel")
        self.status.pack(anchor="w", pady=(4, 10))

        tiles = ttk.Frame(root); tiles.pack(fill="x", pady=(0, 10))
        self.tile_vals = {}
        for i, (name, col) in enumerate(TILE.items()):
            f = tk.Frame(tiles, bg="#f6f8fb", highlightbackground="#dde3ea", highlightthickness=1)
            f.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 8, 0)); tiles.columnconfigure(i, weight=1)
            v = tk.StringVar(value="–"); self.tile_vals[name] = v
            tk.Label(f, textvariable=v, font=(UI, 20, "bold"), fg=col, bg="#f6f8fb").pack(pady=(8, 0))
            tk.Label(f, text=name, font=(UI, 9), fg="#444", bg="#f6f8fb").pack(pady=(0, 8))

        logf = ttk.Frame(root); logf.pack(fill="both", expand=True)
        self.log = tk.Text(logf, height=10, wrap="word", font=("Consolas", 9), relief="flat", bg="#fafafa", highlightthickness=1, highlightbackground="#dde3ea")
        sb = ttk.Scrollbar(logf, command=self.log.yview); self.log.configure(yscrollcommand=sb.set, state="disabled")
        self.log.pack(side="left", fill="both", expand=True); sb.pack(side="right", fill="y")

        bottom = ttk.Frame(root); bottom.pack(fill="x", pady=(10, 0))
        ttk.Button(bottom, text="Open Excel register", command=lambda: open_path(self.cfg.excel_path)).pack(side="left")
        ttk.Button(bottom, text="Needs Review folder", command=lambda: open_path(self.cfg.review_dir)).pack(side="left", padx=6)
        ttk.Button(bottom, text="Failed folder", command=lambda: open_path(self.cfg.failed_dir)).pack(side="left")
        ttk.Button(bottom, text="Last run report", command=self._open_report).pack(side="left", padx=6)
        ttk.Button(bottom, text="Settings…", command=self._settings).pack(side="right")

    # ---------------------------------------------------------------- helpers
    def _refresh_paths(self):
        self.inbox_var.set(self.cfg.inbox_folder); self.out_var.set(self.cfg.output_folder)

    def _check_ocr(self):
        if self.cfg.ocr_enabled and not find_tesseract(self.cfg.tesseract_path):
            self.warn.configure(text="⚠  The OCR program (Tesseract) was not found, so SCANNED invoices cannot be read yet - they will go to "
                                     "the Failed folder with an explanation. Normal PDF invoices work fine. See the Installation Guide, step 3.")
            self.warn.pack(fill="x", pady=(0, 10), before=self.btn.master.winfo_children()[2])
        else:
            self.warn.pack_forget()

    def _write(self, line):
        self.log.configure(state="normal"); self.log.insert("end", line + "\n"); self.log.see("end"); self.log.configure(state="disabled")

    def _change(self, which):
        start = self.cfg.inbox if which == "inbox" else self.cfg.output
        d = filedialog.askdirectory(initialdir=str(start) if start.exists() else str(Path.home()), title="Choose folder")
        if not d: return
        if which == "inbox": self.cfg.inbox_folder = d
        else: self.cfg.output_folder = d
        self.cfg.save(); self._refresh_paths()

    def _open_report(self):
        reps = sorted(self.cfg.logs_dir.glob("Run report *.txt")) if self.cfg.logs_dir.exists() else []
        if reps: open_path(reps[-1])
        else: messagebox.showinfo("No reports yet", "Process some invoices first.")

    # ---------------------------------------------------------------- running
    def _start(self):
        self.btn.configure(state="disabled"); self.bar["value"] = 0
        for v in self.tile_vals.values(): v.set("…")
        self.log.configure(state="normal"); self.log.delete("1.0", "end"); self.log.configure(state="disabled")
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self):
        try:
            res = run(self.cfg, progress=lambda m, f=None: self.q.put(("progress", m, f)))
            self.q.put(("done", res, None))
        except AlreadyRunning as e:
            self.q.put(("error", str(e), None))
        except Exception as e:  # noqa
            import traceback
            self.q.put(("error", f"Something went wrong: {e}\n\nNo invoices have been lost - any not yet processed are still in the inbox.\n\n"
                                 f"Technical details:\n{traceback.format_exc()[-800:]}", None))

    def _poll(self):
        try:
            while True:
                kind, a, b = self.q.get_nowait()
                if kind == "progress":
                    self.status.configure(text=a)
                    if b is not None: self.bar["value"] = b
                    if ": " in a and not a.startswith("Reading"): self._write(a)
                elif kind == "done":
                    self._finished(a)
                elif kind == "error":
                    self.btn.configure(state="normal"); self.status.configure(text="Stopped.")
                    messagebox.showerror("Invoice Processor", a)
        except queue.Empty:
            pass
        self.after(150, self._poll)

    def _finished(self, res):
        self.btn.configure(state="normal"); self.bar["value"] = 1.0
        for k, v in [("Processed", res.processed), ("Needs review", res.review), ("Duplicates", res.duplicates), ("Failed", res.failed)]:
            self.tile_vals[k].set(str(v))
        if res.found == 0 and not res.skipped:
            self.status.configure(text="The inbox is empty - nothing to do." + ("" if res.excel_ok else " (Excel register still needs updating - close it and run again.)"))
        else:
            self.status.configure(text="Finished. " + res.summary())
        if res.ignored:
            self._write(f"Left in inbox (not PDF/image): {', '.join(res.ignored)}")
        for m in res.messages:
            self._write("⚠ " + m)
        if not res.excel_ok:
            messagebox.showwarning("Close the Excel register", res.messages[-1])
        elif res.review or res.failed or res.duplicates:
            self._write("See the Excel register (Invoices + Exceptions sheets) or 'Last run report' for what to check.")

    # ---------------------------------------------------------------- settings
    def _settings(self):
        w = tk.Toplevel(self); w.title("Settings"); w.configure(bg="white"); w.transient(self); w.grab_set()
        f = ttk.Frame(w, padding=16); f.pack(fill="both", expand=True)
        v_company = tk.StringVar(value="; ".join(self.cfg.our_company_names))
        v_thresh = tk.StringVar(value=f"{self.cfg.high_value_threshold:.0f}")
        v_day = tk.BooleanVar(value=self.cfg.dates_are_day_first)
        v_ocr = tk.BooleanVar(value=self.cfg.ocr_enabled)
        v_tess = tk.StringVar(value=self.cfg.tesseract_path)
        v_sub = tk.BooleanVar(value=self.cfg.include_subfolders)
        rows = [("Your company name(s) - separate with ;", ttk.Entry(f, textvariable=v_company, width=50)),
                ("Send invoices above this amount (£) to review", ttk.Entry(f, textvariable=v_thresh, width=12)),
                ("Dates are UK style (day first, 03/04 = 3 April)", ttk.Checkbutton(f, variable=v_day)),
                ("Read scanned invoices with OCR", ttk.Checkbutton(f, variable=v_ocr)),
                ("Tesseract program (leave blank to find it automatically)", ttk.Entry(f, textvariable=v_tess, width=50)),
                ("Also look in sub-folders of the inbox", ttk.Checkbutton(f, variable=v_sub))]
        for r, (lab, wdg) in enumerate(rows):
            ttk.Label(f, text=lab).grid(row=r, column=0, sticky="w", pady=4, padx=(0, 10)); wdg.grid(row=r, column=1, sticky="w")

        def save():
            try:
                th = float(v_thresh.get().replace(",", "").replace("£", ""))
            except ValueError:
                messagebox.showerror("Settings", "The amount must be a number, e.g. 10000", parent=w); return
            self.cfg.our_company_names = [s.strip() for s in v_company.get().split(";") if s.strip()]
            self.cfg.high_value_threshold = th; self.cfg.dates_are_day_first = v_day.get(); self.cfg.ocr_enabled = v_ocr.get()
            self.cfg.tesseract_path = v_tess.get().strip(); self.cfg.include_subfolders = v_sub.get()
            self.cfg.save(); self._check_ocr(); w.destroy()
        b = ttk.Frame(f); b.grid(row=len(rows), column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(b, text="Cancel", command=w.destroy).pack(side="right")
        ttk.Button(b, text="Save", command=save).pack(side="right", padx=6)


def main():
    App().mainloop()
