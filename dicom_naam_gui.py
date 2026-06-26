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

import dicom_naam as dn
import pydicom


# ---------------------------------------------------------------------------
# COLOURS / STYLE
# ---------------------------------------------------------------------------
BG       = "#f5f5f5"
ACCENT   = "#0078d4"
WHITE    = "#ffffff"
FONT     = ("Segoe UI", 10)
FONT_B   = ("Segoe UI", 10, "bold")
FONT_H   = ("Segoe UI", 13, "bold")


class DicomNaamApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("DICOM Sequence Name Generator")
        self.configure(bg=BG)
        self.resizable(True, True)
        self.minsize(700, 420)

        self._bron_map = tk.StringVar()
        self._resultaten = []
        self._alle_bestanden = []

        self._bouw_kiezer_scherm()

    # -----------------------------------------------------------------------
    # SCREEN 1 — Folder picker
    # -----------------------------------------------------------------------
    def _bouw_kiezer_scherm(self):
        self._wis_scherm()
        self.geometry("640x280")

        frm = tk.Frame(self, bg=BG, padx=30, pady=30)
        frm.pack(fill="both", expand=True)

        tk.Label(frm, text="DICOM Sequence Name Generator",
                 font=FONT_H, bg=BG).grid(row=0, column=0, columnspan=3,
                                           sticky="w", pady=(0, 20))

        tk.Label(frm, text="DICOM folder:", font=FONT, bg=BG).grid(
            row=1, column=0, sticky="w", padx=(0, 10))

        tk.Entry(frm, textvariable=self._bron_map, font=FONT,
                 width=45).grid(row=1, column=1, sticky="ew")

        tk.Button(frm, text="Browse…", font=FONT,
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

        frm = tk.Frame(self, bg=BG, padx=30, pady=30)
        frm.pack(fill="both", expand=True)

        tk.Label(frm, text="Processing…",
                 font=FONT_H, bg=BG).pack(anchor="w")

        self._status_lbl = tk.Label(frm, text="Loading files…",
                                    font=FONT, bg=BG, anchor="w")
        self._status_lbl.pack(anchor="w", pady=(10, 4))

        self._prog_var = tk.DoubleVar()
        self._prog_bar = ttk.Progressbar(frm, variable=self._prog_var,
                                          maximum=totaal, length=560)
        self._prog_bar.pack(fill="x")

        self._pct_lbl = tk.Label(frm, text="0%", font=FONT, bg=BG)
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
        resultaten = []
        totaal = len(bestanden)

        for i, b in enumerate(bestanden):
            self.after(0, self._update_voortgang, i + 1, totaal, b)
            r = dn.verwerk_bestand(b)
            if r is not None:
                resultaten.append(r)

        series = dn.groepeer_per_serie(resultaten)

        # Sort by patient name + study + series number
        def _sort_sleutel(r):
            patient = (r.get("patient_naam", "") or r.get("patient_id", "")).upper()
            studie  = r.get("studie_uid", "") or r.get("studie_datum", "")
            try:
                nr = int(r.get("serienummer", 0))
            except (ValueError, TypeError):
                nr = 0
            return (patient, studie, nr)

        self._resultaten = sorted(series, key=_sort_sleutel)
        self._alle_resultaten_per_bestand = resultaten

        self.after(0, self._bouw_overzicht_scherm)

    # -----------------------------------------------------------------------
    # SCREEN 3 — Overview + manual edit
    # -----------------------------------------------------------------------
    def _bouw_overzicht_scherm(self):
        self._wis_scherm()
        self.geometry("1100x650")
        self._undo_stack = []

        # Toolbar
        toolbar = tk.Frame(self, bg=BG, padx=12, pady=8)
        toolbar.pack(fill="x")

        tk.Label(toolbar, text="Generated Names Overview",
                 font=FONT_H, bg=BG).pack(side="left")

        self._undo_btn = tk.Button(toolbar, text="↩  Undo", font=FONT,
                  padx=10, pady=4, state="disabled",
                  command=self._undo)
        self._undo_btn.pack(side="left", padx=(16, 0))
        self.bind("<Control-z>", lambda e: self._undo())

        tk.Button(toolbar, text="✔  Apply names", font=FONT_B,
                  bg=ACCENT, fg=WHITE, padx=14, pady=4,
                  relief="flat", cursor="hand2",
                  command=self._toepassen).pack(side="right", padx=(8, 0))

        tk.Button(toolbar, text="🌐  Browser", font=FONT,
                  padx=10, pady=4,
                  command=self._exporteer_html).pack(side="right", padx=(0, 4))

        tk.Button(toolbar, text="📋  CSV", font=FONT,
                  padx=10, pady=4,
                  command=self._exporteer_csv).pack(side="right", padx=(0, 4))

        tk.Button(toolbar, text="Choose new folder", font=FONT,
                  padx=10, pady=4, command=self._bouw_kiezer_scherm).pack(
                  side="right")

        # Table
        cols = ("serie", "beschrijving", "origineel", "nieuw")
        headers = ("Series #", "Series description",
                   "Original ProtocolName", "New name  (double-click to edit)")

        tbl_frm = tk.Frame(self, bg=BG)
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

            self._tabel.insert("", "end", values=(
                r.get("serienummer", ""),
                r.get("seriebeschrijving", ""),
                r.get("origineel_protocol", ""),
                naam_val,
            ), tags=rij_tag)

        self._tabel.bind("<Double-1>", self._bewerk_cel)

        # Legend
        leg_frm = tk.Frame(self, bg=BG)
        leg_frm.pack(pady=(0, 6))
        tk.Label(leg_frm, text="■", fg="#856404", bg=BG, font=FONT).pack(side="left")
        tk.Label(leg_frm, text=" Unknown  ", bg=BG, font=("Segoe UI", 9), fg="#666").pack(side="left")
        tk.Label(leg_frm, text="■", fg="#0c5460", bg=BG, font=FONT).pack(side="left")
        tk.Label(leg_frm, text=" Recon  ", bg=BG, font=("Segoe UI", 9), fg="#666").pack(side="left")
        tk.Label(leg_frm, text="Double-click a name to edit it.",
                 font=("Segoe UI", 9), bg=BG, fg="#666").pack(side="left", padx=(16, 0))

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
