"""
DICOM Sequence Name Generator — GUI
=====================================
Tkinter application that calls dicom_naam.py with a graphical interface.

Usage:
    python dicom_naam_gui.py
    or as .exe via PyInstaller:
    pyinstaller --onefile --windowed dicom_naam_gui.py
"""

import os
import sys
import threading
import tkinter as tk
from tkinter import filedialog, ttk, messagebox

import json
import dicom_naam as dn
import pydicom

# Config file stored next to the exe (or script)
_CONFIG_PAD = os.path.join(os.path.dirname(sys.executable
              if getattr(sys, "frozen", False) else __file__),
              "dicom_naam_config.json")

def _laad_config():
    try:
        with open(_CONFIG_PAD, "r") as f:
            return json.load(f)
    except Exception:
        return {}

def _sla_config_op(data):
    try:
        with open(_CONFIG_PAD, "w") as f:
            json.dump(data, f)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# COLOURS / STYLE
# ---------------------------------------------------------------------------
THEMES = {
    "light": {
        "BG":     "#f5f5f5",
        "FG":     "#1a1a1a",
        "ENTRY":  "#ffffff",
        "ROW_A":  "#ffffff",
        "ROW_B":  "#f0f4f8",
        "SEL":    "#e8f0fe",
    },
    "dark": {
        "BG":     "#1e1e1e",
        "FG":     "#e0e0e0",
        "ENTRY":  "#2d2d2d",
        "ROW_A":  "#252526",
        "ROW_B":  "#2d2d2d",
        "SEL":    "#094771",
    },
}

ACCENT   = "#0078d4"
WHITE    = "#ffffff"
FONT     = ("Segoe UI", 10)
FONT_B   = ("Segoe UI", 10, "bold")
FONT_H   = ("Segoe UI", 13, "bold")


class DicomNaamApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("DICOM Sequence Name Generator")
        self.resizable(True, True)
        self.minsize(700, 420)
        self._dark = False      # start in light mode
        self._theme = THEMES["light"]

        self._bron_map = tk.StringVar()
        self._resultaten = []
        self._alle_bestanden = []

        # Load saved preference
        cfg = _laad_config()
        self._dark = cfg.get("dark_mode", False)
        self._theme = THEMES["dark"] if self._dark else THEMES["light"]

        self._pas_thema_toe()
        self._bouw_kiezer_scherm()

    def _pas_thema_toe(self):
        t = self._theme
        self.configure(bg=t["BG"])
        # Update ttk style for Treeview
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("Treeview",
                        background=t["ROW_A"], foreground=t["FG"],
                        fieldbackground=t["ROW_A"], rowheight=24)
        style.configure("Treeview.Heading",
                        background=ACCENT, foreground=WHITE)
        style.map("Treeview", background=[("selected", t["SEL"])],
                  foreground=[("selected", t["FG"])])

    def _wissel_thema(self):
        self._dark = not self._dark
        self._theme = THEMES["dark"] if self._dark else THEMES["light"]
        _sla_config_op({"dark_mode": self._dark})
        self._pas_thema_toe()
        self._bouw_kiezer_scherm()

    # -----------------------------------------------------------------------
    # SCREEN 1 — Folder picker
    # -----------------------------------------------------------------------
    def _bouw_kiezer_scherm(self):
        self._wis_scherm()
        self.geometry("640x280")
        t = self._theme
        BG = t["BG"]; FG = t["FG"]

        frm = tk.Frame(self, bg=BG, padx=30, pady=30)
        frm.pack(fill="both", expand=True)

        # Small theme toggle top-right
        lbl = "☀" if self._dark else "🌙"
        tk.Button(self, text=lbl, font=("Segoe UI", 11),
                  bg=t["ENTRY"], fg=FG, relief="flat", cursor="hand2",
                  width=2, command=self._wissel_thema).place(relx=1.0, x=-8, y=6, anchor="ne")

        tk.Label(frm, text="DICOM Sequence Name Generator",
                 font=FONT_H, bg=BG, fg=FG).grid(row=0, column=0, columnspan=3,
                                                   sticky="w", pady=(0, 20))

        tk.Label(frm, text="DICOM folder:", font=FONT, bg=BG, fg=FG).grid(
            row=1, column=0, sticky="w", padx=(0, 10))

        tk.Entry(frm, textvariable=self._bron_map, font=FONT,
                 bg=t["ENTRY"], fg=FG, insertbackground=FG,
                 width=45).grid(row=1, column=1, sticky="ew")

        tk.Button(frm, text="Browse…", font=FONT,
                  bg=t["ENTRY"], fg=FG,
                  command=self._kies_map).grid(row=1, column=2, padx=(8, 0))

        frm.columnconfigure(1, weight=1)

        self._start_btn = tk.Button(
            frm, text="▶  Start renaming", font=FONT_B,
            bg=ACCENT, fg=WHITE, padx=20, pady=8,
            relief="flat", cursor="hand2",
            command=self._start_verwerking)
        self._start_btn.grid(row=3, column=0, columnspan=3, pady=(30, 0))

    def _kies_map(self):
        pad = filedialog.askdirectory(title="Select the DICOM folder")
        if pad:
            self._bron_map.set(pad)

    # -----------------------------------------------------------------------
    # SCREEN 2 — Progress
    # -----------------------------------------------------------------------
    def _bouw_voortgang_scherm(self, totaal):
        self._wis_scherm()
        self.geometry("640x220")
        BG = self._theme["BG"]; FG = self._theme["FG"]

        frm = tk.Frame(self, bg=BG, padx=30, pady=30)
        frm.pack(fill="both", expand=True)

        tk.Label(frm, text="Processing…",
                 font=FONT_H, bg=BG, fg=FG).pack(anchor="w")

        self._status_lbl = tk.Label(frm, text="Loading files…",
                                    font=FONT, bg=BG, fg=FG, anchor="w")
        self._status_lbl.pack(anchor="w", pady=(10, 4))

        self._prog_var = tk.DoubleVar()
        self._prog_bar = ttk.Progressbar(frm, variable=self._prog_var,
                                          maximum=totaal, length=560)
        self._prog_bar.pack(fill="x")

        self._pct_lbl = tk.Label(frm, text="0%", font=FONT, bg=BG, fg=FG)
        self._pct_lbl.pack(anchor="e", pady=(2, 0))

    def _update_voortgang(self, gedaan, totaal, bestandsnaam):
        pct = int(gedaan / totaal * 100) if totaal else 0
        self._prog_var.set(gedaan)
        self._pct_lbl.config(text=f"{pct}%")
        self._status_lbl.config(text=f"Processing: {os.path.basename(bestandsnaam)}")

    # -----------------------------------------------------------------------
    # PROCESSING (background thread)
    # -----------------------------------------------------------------------
    def _start_verwerking(self):
        pad = self._bron_map.get().strip()
        if not pad or not os.path.isdir(pad):
            messagebox.showerror("Error", "Please select a valid DICOM folder.")
            return

        bestanden = dn.verzamel_bestanden(pad)
        if not bestanden:
            messagebox.showinfo("Empty", "No files found in the selected folder.")
            return

        self._alle_bestanden = bestanden
        self._bouw_voortgang_scherm(len(bestanden))

        thread = threading.Thread(
            target=self._verwerk_thread, args=(pad, bestanden), daemon=True)
        thread.start()

    def _verwerk_thread(self, pad, bestanden):
        import concurrent.futures, threading

        totaal = len(bestanden)
        resultaten = []
        teller = threading.Lock()
        gedaan = [0]

        def verwerk_een(b):
            r = dn.verwerk_bestand(b)
            with teller:
                gedaan[0] += 1
                self.after(0, self._update_voortgang, gedaan[0], totaal, b)
            return r

        # Use 8 threads for parallel file reading (I/O bound)
        workers = min(8, max(1, totaal // 50))
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            for r in pool.map(verwerk_een, bestanden):
                if r is not None:
                    resultaten.append(r)

        series = dn.groepeer_per_serie(resultaten)

        # Sort by study_uid first (keeps all series from same exam together)
        # then series number within each study
        def _sort_sleutel(r):
            studie = r.get("studie_uid", "") or r.get("studie_datum", "") or \
                     r.get("patient_naam", "") or r.get("patient_id", "")
            try:
                nr = int(r.get("serienummer", 0))
            except (ValueError, TypeError):
                nr = 0
            return (studie, nr)

        self._resultaten = sorted(series, key=_sort_sleutel)
        # Backfill composite serie_uid for files where SeriesInstanceUID was absent
        for r in resultaten:
            if not r.get("serie_uid"):
                r["serie_uid"] = (f"{r.get('studie_uid','')}#{r.get('serienummer','')}"
                                   or r["bestand"])
        self._alle_resultaten_per_bestand = resultaten

        self.after(0, self._bouw_overzicht_scherm)

    # -----------------------------------------------------------------------
    # SCREEN 3 — Overview + manual edit
    # -----------------------------------------------------------------------
    def _bouw_overzicht_scherm(self):
        self._wis_scherm()
        self.geometry("1100x650")
        self._undo_stack = []
        BG = self._theme["BG"]; FG = self._theme["FG"]

        # Toolbar
        toolbar = tk.Frame(self, bg=BG, padx=12, pady=8)
        toolbar.pack(fill="x")

        tk.Label(toolbar, text="Generated Names Overview",
                 font=FONT_H, bg=BG, fg=FG).pack(side="left")

        # Uniform button style
        _btn  = dict(font=FONT,  padx=10, pady=5, relief="flat", cursor="hand2")
        _btnp = dict(font=FONT_B, padx=10, pady=5, relief="flat", cursor="hand2")

        self._undo_btn = tk.Button(toolbar, text="↩  Undo", state="disabled",
                  command=self._undo, **_btn)
        self._undo_btn.pack(side="left", padx=(16, 0))
        self.bind("<Control-z>", lambda e: self._undo())

        self._view_btn = tk.Button(toolbar, text="👁  View", state="disabled",
                  command=self._open_viewer, **_btn)
        self._view_btn.pack(side="left", padx=(6, 0))

        tk.Button(toolbar, text="🗑  Delete", fg="#c00000",
                  command=self._verwijder_geselecteerd, **_btn).pack(side="left", padx=(6, 0))

        tk.Button(toolbar, text="✔  Copy & rename", bg=ACCENT, fg=WHITE,
                  command=self._toepassen, **_btnp).pack(side="right", padx=(6, 0))

        tk.Button(toolbar, text="✎  Rename in place", bg="#107c10", fg=WHITE,
                  command=self._rename_inplace, **_btnp).pack(side="right", padx=(6, 0))

        tk.Button(toolbar, text="📋  Export CSV",
                  command=self._exporteer_csv, **_btn).pack(side="right", padx=(6, 0))

        tk.Button(toolbar, text="New folder",
                  command=self._bouw_kiezer_scherm, **_btn).pack(side="right")

        # Table
        cols = ("serie", "beschrijving", "origineel", "nieuw")
        headers = ("Series #", "Series description",
                   "Original ProtocolName", "New name  (double-click to edit)")

        tbl_frm = tk.Frame(self, bg=self._theme["BG"])
        tbl_frm.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        vsb = ttk.Scrollbar(tbl_frm, orient="vertical")
        hsb = ttk.Scrollbar(tbl_frm, orient="horizontal")

        self._tabel = ttk.Treeview(
            tbl_frm, columns=cols, show="headings",
            yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        vsb.config(command=self._tabel.yview)
        hsb.config(command=self._tabel.xview)

        breedtes = (70, 220, 280, 280)
        for col, hdr, breedte in zip(cols, headers, breedtes):
            self._tabel.heading(col, text=hdr)
            self._tabel.column(col, width=breedte, minwidth=60)

        vsb.pack(side="right", fill="y")
        hsb.pack(side="bottom", fill="x")
        self._tabel.pack(fill="both", expand=True)

        # Row colours
        self._tabel.tag_configure("patient_header",
                                   background="#0078d4", foreground="white")
        self._tabel.tag_configure("onbekend",
                                   background="#fff3cd", foreground="#856404")
        self._tabel.tag_configure("recon",
                                   background="#d1ecf1", foreground="#0c5460")

        # Map item_id -> resultaat record for quick lookup
        self._rij_naar_resultaat = {}

        # Fill table grouped by patient/study
        huidige_studie = None
        for r in self._resultaten:
            studie_key = r.get("studie_uid", "") or r.get("patient_id", "")
            if studie_key != huidige_studie:
                huidige_studie = studie_key
                pnaam = r.get("patient_naam", "") or r.get("patient_id", "unknown")
                datum = r.get("studie_datum", "")
                omschr = r.get("studie_beschrijving", "")
                label = f"  👤  {pnaam}"
                if datum:
                    label += f"   |   {datum[:4]}-{datum[4:6]}-{datum[6:]}" if len(datum) == 8 else f"   |   {datum}"
                if omschr:
                    label += f"   —   {omschr}"
                self._tabel.insert("", "end", values=("", label, "", ""),
                                   tags=("patient_header",))

            naam_val = r.get("naam", "")
            if "unknown" in naam_val.lower() or "onbekend" in naam_val.lower():
                rij_tag = ("onbekend",)
            elif "_recon" in naam_val.lower():
                rij_tag = ("recon",)
            else:
                rij_tag = ()

            item_id = self._tabel.insert("", "end", values=(
                r.get("serienummer", ""),
                r.get("seriebeschrijving", ""),
                r.get("origineel_protocol", ""),
                naam_val,
            ), tags=rij_tag)
            self._rij_naar_resultaat[item_id] = r

        self._tabel.bind("<Double-1>", self._bewerk_cel)
        self._tabel.bind("<<TreeviewSelect>>", self._on_selectie)
        self._tabel.bind("<Button-1>", lambda e: self.after(100, self._on_selectie))

        # Legend
        _bg = self._theme["BG"]; _fg = self._theme["FG"]
        leg_frm = tk.Frame(self, bg=_bg)
        leg_frm.pack(pady=(0, 6))
        tk.Label(leg_frm, text="■", fg="#856404", bg=_bg, font=FONT).pack(side="left")
        tk.Label(leg_frm, text=" Unknown  ", bg=_bg, font=("Segoe UI", 9), fg=_fg).pack(side="left")
        tk.Label(leg_frm, text="■", fg="#0c5460", bg=_bg, font=FONT).pack(side="left")
        tk.Label(leg_frm, text=" Recon  ", bg=_bg, font=("Segoe UI", 9), fg=_fg).pack(side="left")
        tk.Label(leg_frm, text="Double-click a name to edit it.",
                 font=("Segoe UI", 9), bg=_bg, fg=_fg).pack(side="left", padx=(16, 0))

    # -----------------------------------------------------------------------
    # EXPORT
    # -----------------------------------------------------------------------
    def _exporteer_csv(self):
        import csv
        pad = filedialog.asksaveasfilename(
            title="Save as CSV",
            defaultextension=".csv",
            filetypes=[("CSV file", "*.csv"), ("All files", "*.*")],
            initialfile="dicom_names_overview.csv")
        if not pad:
            return
        with open(pad, "w", newline="", encoding="utf-8-sig") as f:
            f.write("sep=,\n")
            writer = csv.writer(f)
            writer.writerow(["Series #", "Series description",
                             "Original ProtocolName", "New name"])
            for item_id in self._tabel.get_children():
                writer.writerow(self._tabel.item(item_id, "values"))
        os.startfile(pad)

    def _exporteer_html(self):
        import tempfile, webbrowser
        rijen = []
        for item_id in self._tabel.get_children():
            rijen.append(self._tabel.item(item_id, "values"))

        html = """<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>DICOM Names Overview</title>
<style>
  body { font-family: Segoe UI, Arial, sans-serif; margin: 30px; background: #f5f5f5; }
  h2 { color: #0078d4; }
  table { border-collapse: collapse; width: 100%; background: white;
          box-shadow: 0 1px 4px rgba(0,0,0,.15); }
  th { background: #0078d4; color: white; padding: 10px 14px; text-align: left; }
  td { padding: 8px 14px; border-bottom: 1px solid #e0e0e0; }
  tr:hover td { background: #e8f0fe; }
  .changed { color: #107c10; font-weight: bold; }
  .unknown { background: #fff3cd; }
  .recon   { background: #d1ecf1; }
</style></head><body>
<h2>DICOM Sequence Names Overview</h2>
<table>
<tr><th>Series #</th><th>Series description</th>
<th>Original ProtocolName</th><th>New name</th></tr>
"""
        for r in rijen:
            gewijzigd = r[2] != r[3]
            is_unknown = "unknown" in str(r[3]).lower() or "onbekend" in str(r[3]).lower()
            is_recon   = "_recon" in str(r[3]).lower()
            row_cls = ' class="unknown"' if is_unknown else (' class="recon"' if is_recon else '')
            col_cls = ' class="changed"' if gewijzigd else ''
            html += (f"<tr{row_cls}><td>{r[0]}</td><td>{r[1]}</td>"
                     f"<td>{r[2]}</td><td{col_cls}>{r[3]}</td></tr>\n")
        html += "</table></body></html>"

        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".html",
                                          mode="w", encoding="utf-8")
        tmp.write(html)
        tmp.close()
        webbrowser.open(f"file:///{tmp.name}")

    # -----------------------------------------------------------------------
    # UNDO
    # -----------------------------------------------------------------------
    def _verwijder_geselecteerd(self):
        """Permanently delete selected series files from disk."""
        sel = [i for i in self._tabel.selection()
               if "patient_header" not in self._tabel.item(i, "tags")]
        if not sel:
            messagebox.showinfo("Nothing selected", "Please select one or more series rows to delete.")
            return

        # Collect all files for selected series
        te_verwijderen = []
        for i in sel:
            r = self._rij_naar_resultaat.get(i, {})
            serie_uid = r.get("serie_uid", "")
            serie_nr  = str(r.get("serienummer", ""))
            for rec in getattr(self, "_alle_resultaten_per_bestand", []):
                if (serie_uid and rec.get("serie_uid") == serie_uid) or \
                   (not serie_uid and str(rec.get("serienummer", "")) == serie_nr):
                    te_verwijderen.append(rec["bestand"])

        namen = [self._tabel.set(i, "nieuw") or self._tabel.set(i, "beschrijving")
                 for i in sel]
        preview = "\n".join(f"  • {n}" for n in namen[:10])
        if len(namen) > 10:
            preview += f"\n  … and {len(namen)-10} more"

        if not messagebox.askyesno(
                "⚠ Permanently delete files",
                f"This will PERMANENTLY DELETE {len(te_verwijderen)} files from disk:\n\n"
                f"{preview}\n\n"
                "This cannot be undone. Are you sure?",
                icon="warning"):
            return

        # Second confirmation for safety
        if not messagebox.askyesno(
                "⚠ Final confirmation",
                f"Permanently delete {len(te_verwijderen)} DICOM files?\n\nThis is irreversible.",
                icon="warning"):
            return

        errors = []
        for pad in te_verwijderen:
            try:
                os.remove(pad)
            except Exception as e:
                errors.append(f"{os.path.basename(pad)}: {e}")

        # Remove from list and internal records
        for i in sel:
            self._rij_naar_resultaat.pop(i, None)
            self._tabel.delete(i)

        deleted = len(te_verwijderen) - len(errors)
        msg = f"Deleted {deleted} of {len(te_verwijderen)} files."
        if errors:
            msg += f"\n\nErrors:\n" + "\n".join(errors[:5])
        messagebox.showinfo("Done", msg)

    def _on_selectie(self, event=None):
        """Enable View button and remember selected series."""
        self._geselecteerde_serie_nr = None
        for item_id in self._tabel.selection():
            tags = self._tabel.item(item_id, "tags")
            if "patient_header" not in tags:
                self._geselecteerde_serie_nr = self._tabel.set(item_id, "serie")
                self._geselecteerde_naam = self._tabel.set(item_id, "nieuw")
                # Store the exact serie_uid for precise file matching
                r = self._rij_naar_resultaat.get(item_id, {})
                self._geselecteerde_serie_uid = r.get("serie_uid", "")
                self._geselecteerde_studie = r.get("studie_uid", "")
                self._view_btn.config(state="normal")
                return
        self._view_btn.config(state="disabled")

    def _open_viewer(self):
        """Open a slice viewer for the selected series."""
        try:
            from PIL import Image, ImageTk
            import numpy as np
        except Exception as e:
            messagebox.showerror("Missing library", f"Cannot load viewer: {e}")
            return
        try:
            self._open_viewer_impl(Image, ImageTk, np)
        except Exception as e:
            messagebox.showerror("Viewer error", f"Error opening viewer:\n{e}")

    def _open_viewer_impl(self, Image, ImageTk, np):

        serie_nr   = getattr(self, "_geselecteerde_serie_nr", None)
        serie_naam = getattr(self, "_geselecteerde_naam", "")
        if not serie_nr:
            messagebox.showinfo("No selection", "Please click a series row first, then click View.")
            return

        alle = getattr(self, "_alle_resultaten_per_bestand", [])
        if not alle:
            messagebox.showinfo("No data", "No series data available. Please run the renaming first.")
            return

        try:
            serie_uid = getattr(self, "_geselecteerde_serie_uid", "")
            # Filter by exact serie_uid for precise matching

            if serie_uid:
                recs = [r for r in alle if r.get("serie_uid", "") == serie_uid]
            if not recs:
                # Fallback: match by (study_uid, series_number) for missing SeriesInstanceUID
                studie = getattr(self, "_geselecteerde_studie", "")
                recs = [r for r in alle
                        if str(r.get("serienummer", "")) == serie_nr
                        and r.get("studie_uid", "") == studie]
            if not recs:
                # Last resort: series number only
                recs = [r for r in alle
                        if str(r.get("serienummer", "")) == serie_nr]
            if not recs:
                messagebox.showinfo("No images",
                    f"Series {serie_nr}: 0 files found.\n"
                    f"UID used: {serie_uid[:50] if serie_uid else '(empty — using study+nr)'}\n"
                    f"Study: {getattr(self,'_geselecteerde_studie','')[:50]}")
                return
            # Sort by InstanceNumber from already-read metadata
            def _inst_nr(r):
                try:
                    ds = pydicom.dcmread(r["bestand"], force=True, stop_before_pixels=True)
                    return int(getattr(ds, "InstanceNumber", 0))
                except Exception:
                    return 0
            bestanden_raw = sorted([r["bestand"] for r in recs],
                                   key=lambda p: _inst_nr({"bestand": p}))
            # Expand Enhanced MR multi-frame files into (path, frame_idx) tuples
            bestanden = []  # list of (path, frame_idx)
            for pad in bestanden_raw:
                try:
                    ds_check = pydicom.dcmread(pad, force=True, stop_before_pixels=True)
                    sop = str(ds_check.get("SOPClassUID", ""))
                    if sop in dn.ENHANCED_MR_STORAGE:
                        ds_full = pydicom.dcmread(pad, force=True)
                        n_frames = ds_full.pixel_array.shape[0] if ds_full.pixel_array.ndim == 3 else 1
                        for i in range(n_frames):
                            bestanden.append((pad, i))
                    else:
                        bestanden.append((pad, 0))
                except Exception:
                    bestanden.append((pad, 0))
        except Exception as e:
            messagebox.showerror("Error", f"Could not load series: {e}")
            return

        popup = tk.Toplevel(self)
        popup.title(f"Series viewer — {serie_naam} [{len(bestanden)} slices]")
        popup.geometry("560x780")
        BG = self._theme["BG"]; FG = self._theme["FG"]
        popup.configure(bg=BG)

        info_lbl = tk.Label(popup, text="", font=FONT, bg=BG, fg=FG)
        info_lbl.pack(pady=(8, 0))

        canvas = tk.Canvas(popup, bg="#000000", width=512, height=512)
        canvas.pack(padx=12, pady=4)

        # Slice slider
        slider_var = tk.IntVar(value=0)
        slider = ttk.Scale(popup, from_=0, to=len(bestanden)-1,
                           variable=slider_var, orient="horizontal")
        slider.pack(fill="x", padx=12, pady=(0, 4))

        # Window/Level sliders
        wl_frm = tk.Frame(popup, bg=BG)
        wl_frm.pack(fill="x", padx=12, pady=(0, 2))

        tk.Label(wl_frm, text="Level (WC):", font=("Segoe UI", 8),
                 bg=BG, fg=FG, width=10, anchor="w").grid(row=0, column=0)
        wc_var = tk.IntVar(value=500)
        wc_lbl = tk.Label(wl_frm, text="500", font=("Segoe UI", 8),
                          bg=BG, fg=FG, width=5)
        wc_lbl.grid(row=0, column=2)
        wc_slider = ttk.Scale(wl_frm, from_=-2000, to=4000,
                              variable=wc_var, orient="horizontal")
        wc_slider.grid(row=0, column=1, sticky="ew", padx=4)

        tk.Label(wl_frm, text="Window (WW):", font=("Segoe UI", 8),
                 bg=BG, fg=FG, width=10, anchor="w").grid(row=1, column=0)
        ww_var = tk.IntVar(value=2000)
        ww_lbl = tk.Label(wl_frm, text="2000", font=("Segoe UI", 8),
                          bg=BG, fg=FG, width=5)
        ww_lbl.grid(row=1, column=2)
        ww_slider = ttk.Scale(wl_frm, from_=1, to=8000,
                              variable=ww_var, orient="horizontal")
        ww_slider.grid(row=1, column=1, sticky="ew", padx=4)
        wl_frm.columnconfigure(1, weight=1)

        # Metadata panel
        meta_lbl = tk.Label(popup, text="", font=("Consolas", 9),
                            bg=BG, fg=FG, justify="left")
        meta_lbl.pack(pady=(0, 4))

        def _laad_meta(ds):
            tr    = ds.get("RepetitionTime")
            te    = ds.get("EchoTime")
            dikte = ds.get("SliceThickness")
            ps    = ds.get("PixelSpacing")
            parts = []
            if tr is not None:
                parts.append(f"TR: {float(tr):.0f} ms")
            if te is not None:
                parts.append(f"TE: {float(te):.1f} ms")
            if ps is not None:
                try:
                    row, col = float(ps[0]), float(ps[1])
                    z = float(dikte) if dikte is not None else None
                    if z is not None:
                        parts.append(f"Voxel: {row:.2f}×{col:.2f}×{z:.1f} mm")
                    else:
                        parts.append(f"Voxel: {row:.2f}×{col:.2f} mm")
                except Exception:
                    pass
            elif dikte is not None:
                parts.append(f"Thickness: {float(dikte):.1f} mm")
            # Use same scan time logic as renaming script (searches nested + Philips private tag)
            scan_time = dn.onderdeel_acquisitietijd(ds)
            if scan_time and scan_time != "noTime":
                parts.append(f"Scan time: {scan_time}")
            meta_lbl.config(text="   ".join(parts) if parts else "")

        _cache = {}
        _tk_img = [None]

        def _laad_slice(idx):
            if idx in _cache:
                return _cache[idx]
            try:
                pad, frame_idx = bestanden[idx]
                ds = pydicom.dcmread(pad, force=True)
                raw = ds.pixel_array
                # Select specific frame for Enhanced MR, or 2D slice
                if raw.ndim == 3:
                    raw = raw[frame_idx]
                elif raw.ndim > 3:
                    raw = raw[frame_idx][0]
                arr = raw.astype(float)
                slope = float(getattr(ds, "RescaleSlope", 1))
                intercept = float(getattr(ds, "RescaleIntercept", 0))
                arr = arr * slope + intercept
                # Use slider values; initialise from DICOM on first slice
                wc = float(wc_var.get())
                ww = float(ww_var.get())
                if idx == 0:
                    dicom_wc = ds.get("WindowCenter", None)
                    dicom_ww = ds.get("WindowWidth", None)
                    if dicom_wc is not None:
                        if hasattr(dicom_wc, "__iter__"): dicom_wc = float(list(dicom_wc)[0])
                        else: dicom_wc = float(dicom_wc)
                        wc_var.set(int(dicom_wc)); wc = dicom_wc
                        wc_lbl.config(text=str(int(dicom_wc)))
                    else:
                        wc_var.set(int(arr.mean())); wc = arr.mean()
                    if dicom_ww is not None:
                        if hasattr(dicom_ww, "__iter__"): dicom_ww = float(list(dicom_ww)[0])
                        else: dicom_ww = float(dicom_ww)
                        ww_var.set(int(dicom_ww)); ww = dicom_ww
                        ww_lbl.config(text=str(int(dicom_ww)))
                    else:
                        ww_var.set(int(max(arr.std() * 4, 1))); ww = max(arr.std() * 4, 1)
                lo, hi = wc - ww / 2, wc + ww / 2
                arr = np.clip((arr - lo) / max(hi - lo, 1) * 255, 0, 255).astype(np.uint8)
                # Handle RGB
                if arr.ndim == 3:
                    img = Image.fromarray(arr)
                else:
                    img = Image.fromarray(arr, mode="L").convert("RGB")
                img = img.resize((512, 512), Image.LANCZOS)
                _cache[idx] = img
                return img
            except Exception as e:
                info_lbl.config(text=f"Error loading slice {idx+1}: {e}")
                return None

        def _toon(idx):
            img = _laad_slice(idx)
            if img is None:
                return
            _tk_img[0] = ImageTk.PhotoImage(img)
            canvas.delete("all")
            canvas.create_image(256, 256, image=_tk_img[0])
            info_lbl.config(text=f"Slice {idx + 1} / {len(bestanden)}")
            # Load metadata from DICOM (only first slice, cached implicitly via ds)
            try:
                pad_meta, _ = bestanden[idx]
                ds_meta = pydicom.dcmread(pad_meta, force=True,
                                          stop_before_pixels=True)
                _laad_meta(ds_meta)
            except Exception:
                pass

        def _on_wl(val=None):
            _cache.clear()  # clear cache so new WL is applied
            wc_lbl.config(text=str(wc_var.get()))
            ww_lbl.config(text=str(ww_var.get()))
            _toon(int(slider_var.get()))

        wc_slider.config(command=_on_wl)
        ww_slider.config(command=_on_wl)

        def _on_slider(val=None):
            _toon(int(slider_var.get()))

        def _on_scroll(event):
            cur = int(slider_var.get())
            delta = -1 if event.delta > 0 else 1
            new = max(0, min(len(bestanden) - 1, cur + delta))
            slider_var.set(new)
            _toon(new)

        slider.config(command=_on_slider)
        # bind_all captures scroll from any widget in the popup regardless of focus
        popup.bind_all("<MouseWheel>", _on_scroll)
        popup.bind("<Up>",   lambda e: _on_scroll(type("E", (), {"delta": 120})()))
        popup.bind("<Down>", lambda e: _on_scroll(type("E", (), {"delta": -120})()))
        popup.focus_set()
        # Unbind when popup closes to avoid affecting main window
        popup.protocol("WM_DELETE_WINDOW",
                       lambda: [popup.unbind_all("<MouseWheel>"), popup.destroy()])

        _toon(0)

    def _undo(self):
        if not self._undo_stack:
            return
        item_id, kolom, oude_waarde = self._undo_stack.pop()
        self._tabel.set(item_id, kolom, oude_waarde)
        if not self._undo_stack:
            self._undo_btn.config(state="disabled")

    def _sla_op_en_undo(self, item_id, kolom, oude_waarde, nieuwe_waarde):
        if nieuwe_waarde and nieuwe_waarde != oude_waarde:
            self._undo_stack.append((item_id, kolom, oude_waarde))
            self._tabel.set(item_id, kolom, nieuwe_waarde)
            self._undo_btn.config(state="normal")

    def _bewerk_cel(self, event):
        item = self._tabel.identify_row(event.y)
        col  = self._tabel.identify_column(event.x)
        if not item:
            return

        # Patient header: popup dialog
        tags = self._tabel.item(item, "tags")
        if "patient_header" in tags and col == "#2":
            huidige = self._tabel.set(item, "beschrijving")
            popup = tk.Toplevel(self)
            popup.title("Edit patient name")
            popup.resizable(False, False)
            popup.grab_set()
            frm = tk.Frame(popup, padx=20, pady=16)
            frm.pack()
            tk.Label(frm, text="Name:", font=FONT).grid(row=0, column=0, sticky="w")
            invoer = tk.Entry(frm, font=FONT, width=50)
            invoer.grid(row=0, column=1, padx=(8, 0))
            invoer.insert(0, huidige)
            invoer.select_range(0, tk.END)
            invoer.focus_set()

            def bevestig(event=None):
                self._sla_op_en_undo(item, "beschrijving", huidige, invoer.get().strip())
                popup.destroy()

            tk.Button(frm, text="OK", font=FONT_B, bg=ACCENT, fg=WHITE,
                      padx=12, command=bevestig).grid(row=1, column=1,
                      sticky="e", pady=(12, 0))
            invoer.bind("<Return>", bevestig)
            invoer.bind("<Escape>", lambda e: popup.destroy())
            return

        if col != "#4":
            return

        x, y, w, h = self._tabel.bbox(item, col)
        huidige = self._tabel.set(item, "nieuw")

        invoer = tk.Entry(self._tabel, font=FONT)
        invoer.place(x=x, y=y, width=w, height=h)
        invoer.insert(0, huidige)
        invoer.select_range(0, tk.END)
        invoer.focus_set()

        def opslaan(event=None):
            nieuwe = invoer.get().strip()
            self._sla_op_en_undo(item, "nieuw", huidige, nieuwe)
            invoer.destroy()

        invoer.bind("<Return>", opslaan)
        invoer.bind("<Escape>", lambda e: invoer.destroy())
        invoer.bind("<FocusOut>", opslaan)

    # -----------------------------------------------------------------------
    # APPLY NAMES
    # -----------------------------------------------------------------------
    def _rename_inplace(self):
        """Rename ProtocolName in-place in the original folder."""
        pad = self._bron_map.get().strip()
        if not messagebox.askyesno(
                "Rename in same folder",
                f"This will modify the ProtocolName tag in the original files:\n\n{pad}\n\n"
                "The folder structure, filenames and all other tags remain unchanged.\n\n"
                "Continue?"):
            return

        naam_map = {}
        for item_id in self._tabel.get_children():
            vals = self._tabel.item(item_id, "values")
            naam_map[vals[0]] = vals[3]

        for r in self._alle_resultaten_per_bestand:
            nr = str(r.get("serienummer", ""))
            if nr in naam_map:
                r["naam"] = naam_map[nr]

        self._bouw_voortgang_scherm(len(self._alle_resultaten_per_bestand))

        thread = threading.Thread(
            target=self._schrijf_inplace_thread,
            args=(self._alle_resultaten_per_bestand,),
            daemon=True)
        thread.start()

    def _schrijf_inplace_thread(self, resultaten):
        totaal = len(resultaten)
        for i, r in enumerate(resultaten):
            self.after(0, self._update_voortgang, i + 1, totaal, r["bestand"])
            try:
                ds = pydicom.dcmread(r["bestand"], force=True)
                ds.ProtocolName = dn._veilige_mapnaam(r["naam"])
                ds.save_as(r["bestand"])   # overwrite in place
            except Exception:
                pass

        self.after(0, lambda: messagebox.showinfo(
            "Done",
            f"Done! ProtocolName updated in {totaal} file(s) in the original folder."))
        self.after(0, self._bouw_kiezer_scherm)

    def _toepassen(self):
        pad = self._bron_map.get().strip()

        naam_map = {}
        for item_id in self._tabel.get_children():
            vals = self._tabel.item(item_id, "values")
            serie_nr = vals[0]
            nieuwe_naam = vals[3]
            naam_map[serie_nr] = nieuwe_naam

        for r in self._alle_resultaten_per_bestand:
            nr = str(r.get("serienummer", ""))
            if nr in naam_map:
                r["naam"] = naam_map[nr]

        output = filedialog.askdirectory(
            title="Choose output folder (copy with new ProtocolName)")
        if not output:
            return

        self._bouw_voortgang_scherm(len(self._alle_resultaten_per_bestand))

        thread = threading.Thread(
            target=self._schrijf_thread,
            args=(pad, output, self._alle_resultaten_per_bestand),
            daemon=True)
        thread.start()

    def _schrijf_thread(self, bron, output, resultaten):
        import shutil
        totaal = len(resultaten)
        naam_per_pad = {r["bestand"]: r["naam"] for r in resultaten}

        for b in self._alle_bestanden:
            if b in naam_per_pad:
                continue
            try:
                rel  = os.path.relpath(b, bron)
                doel = os.path.join(output, rel)
                os.makedirs(os.path.dirname(doel), exist_ok=True)
                shutil.copy2(b, doel)
            except Exception:
                pass

        for i, r in enumerate(resultaten):
            self.after(0, self._update_voortgang, i + 1, totaal, r["bestand"])
            try:
                rel  = os.path.relpath(r["bestand"], bron)
                doel = os.path.join(output, rel)
                os.makedirs(os.path.dirname(doel), exist_ok=True)
                ds = pydicom.dcmread(r["bestand"], force=True)
                ds.ProtocolName = dn._veilige_mapnaam(r["naam"])
                ds.save_as(doel)
            except Exception:
                pass

        self.after(0, lambda: messagebox.showinfo(
            "Done",
            f"Names applied!\n{totaal} file(s) written to:\n{output}"))
        self.after(0, self._bouw_kiezer_scherm)

    # -----------------------------------------------------------------------
    # HELPER
    # -----------------------------------------------------------------------
    def _wis_scherm(self):
        for widget in self.winfo_children():
            widget.destroy()


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    app = DicomNaamApp()
    app.mainloop()
