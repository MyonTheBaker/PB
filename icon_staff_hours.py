#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Timesheet Consolidator — Full UI Build (Most-Dates Month Corridor) v4

What's new vs v3:
- Corridor month is automatically chosen as the calendar month (YYYY-MM) that contains the
  MOST unique dates across ALL uploaded files.
  - Tie-breaker: prefers the more recent month.
- Everything else remains the same:
  - UI (always opens on start): Locate Timesheets -> Preview -> Generate CSV.
  - "Generate CSV" opens a Save-As dialog so the user selects the destination, then reveals the file.
  - Drops blank-ish staff names ("", "-", "–", "—", "_") at ingest, preview, and final save.
  - STAFF NAME left-aligned; numeric columns centered.
  - Auto output name: "Staff Hours <Month> <Year>.xlsx" after you load files.
"""

import argparse
import os
import re
import sys
import calendar
import subprocess
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional, Tuple, List, Dict

import pandas as pd

# ---------- Logging ----------
def log(msg: str) -> None:
    print(f"[TS] {msg}")

# ---------- Date parsing helpers ----------
DATE_TOKEN_RE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{2,4})")

def _try_parse_date(token: str, date_order: str) -> Optional[datetime]:
    def _fix_year(y: int) -> int:
        return y + 2000 if y < 100 else y
    m = DATE_TOKEN_RE.search(token)
    if not m:
        return None
    a, b, y = m.groups()
    a, b, y = int(a), int(b), _fix_year(int(y))
    try:
        if date_order == "dmy":   # a=day, b=month
            return datetime(y, b, a)
        return datetime(y, a, b)  # mdy default
    except ValueError:
        return None

def parse_date_from_header(colname: str, date_order: str = "mdy") -> Optional[datetime]:
    last = colname.strip().split()[-1]
    dt = _try_parse_date(last, date_order)
    if dt:
        return dt
    return _try_parse_date(colname, date_order)

def eom(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]

def compute_month_window_with_most_dates(all_dates: List[datetime]) -> Tuple[datetime, datetime]:
    """
    Choose the calendar month (YYYY-MM) that contains the most unique dates across all files.
    Tie-breaker: prefer the more recent month.
    """
    if not all_dates:
        raise ValueError("No dates detected in uploaded files.")

    month_to_days: Dict[Tuple[int, int], set] = defaultdict(set)  # (y,m) -> set(date)
    for dt in all_dates:
        d = dt.date()
        month_to_days[(d.year, d.month)].add(d)

    # max by (unique_days_count, year, month) => ties go to more recent month
    (best_y, best_m), _ = max(
        month_to_days.items(),
        key=lambda kv: (len(kv[1]), kv[0][0], kv[0][1])
    )

    start = datetime(best_y, best_m, 1)
    end   = datetime(best_y, best_m, eom(best_y, best_m))
    return start, end

# ---------- Name cleaning ----------
def valid_name_mask(series: pd.Series) -> pd.Series:
    """
    True for rows that have a real staff name.
    Treats "", "-", en-dash, em-dash, "_" (with/without spaces) as blank.
    Also strips non-breaking spaces.
    """
    s = series.astype("string").str.replace("\xa0", "", regex=False).str.strip()
    return ~(
        s.isna() |
        s.eq("") |
        s.eq("-") |
        s.eq("–") |
        s.eq("—") |
        s.eq("_")
    )

# ---------- IO / cleaning ----------
def load_and_clean(csv_path: str) -> pd.DataFrame:
    """
    - Reads CSV
    - Drops 'total'/'summary' rows (in first column)
    - Drops rows with blank-ish staff name in first column
    - Drops rows where 2nd column equals '-'
    """
    df = pd.read_csv(csv_path)
    if df.empty:
        return df

    first_col = df.columns[0]
    mask_total = df[first_col].astype(str).str.lower().str.contains(
        r"\b(?:total|summary)\b", na=False, regex=True
    )
    df = df[~mask_total]

    # drop blank-ish staff names
    df = df[valid_name_mask(df[first_col])]

    # remove rows where 2nd col is "-"
    if len(df.columns) > 1:
        second_col = df.columns[1]
        df = df[df[second_col].astype(str) != "-"]

    return df

# ---------- Month-block logic ----------
def _prepare_month_block_for_ui(
    file_paths: List[str],
    date_order: str = "mdy"
) -> Tuple[pd.DataFrame, List[str], List[str], Optional[datetime], Optional[datetime]]:
    """
    Returns:
      filtered_df: id cols + normalized date columns inside chosen month (rows with any non-zero kept)
      id_cols:     the first two ID columns (original header names)
      norm_names:  date columns in mm/dd/YYYY order that exist in filtered_df
      period_start, period_end: chosen month window (or None/None if no date columns)
    """
    dfs: List[pd.DataFrame] = []
    for f in file_paths:
        try:
            df = load_and_clean(f)
        except Exception:
            continue
        if df is None or df.empty:
            continue
        dfs.append(df)
    if not dfs:
        return pd.DataFrame(), [], [], None, None

    base = dfs[0].copy()
    if base.shape[1] < 2:
        return pd.DataFrame(), [], [], None, None

    id_cols = base.columns[:2].tolist()

    aligned = [base]
    for df in dfs[1:]:
        if df.shape[1] < 2:
            continue
        rename_map = dict(zip(df.columns[:2], id_cols))
        aligned.append(df.rename(columns=rename_map))

    merged = aligned[0]
    for df in aligned[1:]:
        merged = pd.merge(merged, df, on=id_cols, how="outer", suffixes=("", "_dup"))
        dup_cols = [c for c in merged.columns if c.endswith("_dup")]
        if dup_cols:
            merged = merged.drop(columns=dup_cols)

    # Map header -> datetime (for date-like columns)
    date_cols_map: Dict[str, datetime] = {}
    for col in merged.columns:
        if col in id_cols:
            continue
        dt = parse_date_from_header(col, date_order=date_order)
        if dt:
            date_cols_map[col] = dt

    if not date_cols_map:
        filtered = merged[id_cols].copy()
        if id_cols:
            filtered = filtered[valid_name_mask(filtered[id_cols[0]])]
        return filtered, id_cols, [], None, None

    all_detected = sorted(set(date_cols_map.values()))
    period_start, period_end = compute_month_window_with_most_dates(all_detected)

    # Select only columns inside chosen month window
    selected_cols = [c for c, dt in date_cols_map.items() if period_start <= dt <= period_end]
    if not selected_cols:
        filtered = merged[id_cols].copy()
        if id_cols:
            filtered = filtered[valid_name_mask(filtered[id_cols[0]])]
        return filtered, id_cols, [], period_start, period_end

    # Combine duplicates by logical date & normalize names
    dt_to_cols: Dict[datetime, List[str]] = defaultdict(list)
    for col in selected_cols:
        dt_to_cols[date_cols_map[col]].append(col)

    combined = merged[id_cols].copy()
    ordered_dates = sorted(dt_to_cols.keys())

    normalized_names: List[str] = []
    for dt in ordered_dates:
        cols_for_dt = dt_to_cols[dt]
        series = merged[cols_for_dt[0]].copy()
        for extra_col in cols_for_dt[1:]:
            series = series.combine_first(merged[extra_col])
        series = series.replace("-", pd.NA)
        col_name = dt.strftime("%m/%d/%Y")
        combined[col_name] = series
        normalized_names.append(col_name)

    # Keep rows with any non-zero hours inside corridor
    if normalized_names:
        num_block = combined[normalized_names].apply(pd.to_numeric, errors="coerce").fillna(0)
        has_any_hours = (num_block != 0).any(axis=1)
        filtered = combined.loc[has_any_hours].copy()
    else:
        filtered = combined.copy()

    # Drop blank-ish staff names (safety)
    if id_cols:
        filtered = filtered[valid_name_mask(filtered[id_cols[0]])]

    return filtered, id_cols, normalized_names, period_start, period_end

def consolidate_timesheets(
    file_paths: List[str],
    output_file: str,
    date_order: str = "mdy",
    drop_ext_id: bool = True,
) -> None:
    log(f"Reading {len(file_paths)} file(s)...")
    dfs: List[pd.DataFrame] = []
    for i, f in enumerate(file_paths, start=1):
        log(f"  [{i}/{len(file_paths)}] {os.path.basename(f)}")
        try:
            df = load_and_clean(f)
        except Exception as e:
            log(f"    (error reading {f}: {e}; skipping)")
            continue
        if df is None or df.empty:
            log("    (empty or unreadable; skipping)")
            continue
        dfs.append(df)

    if not dfs:
        log("All files were empty/unreadable. Exiting.")
        return

    base = dfs[0].copy()
    if base.shape[1] < 2:
        log("First CSV has fewer than 2 columns; need two ID columns. Exiting.")
        return

    id_cols = base.columns[:2].tolist()
    log(f"ID columns assumed: {id_cols}")

    aligned = [base]
    for df in dfs[1:]:
        if df.shape[1] < 2:
            log("  A CSV has fewer than 2 columns; skipping.")
            continue
        rename_map = dict(zip(df.columns[:2], id_cols))
        aligned.append(df.rename(columns=rename_map))

    merged = aligned[0]
    for i, df in enumerate(aligned[1:], start=2):
        log(f"Merging file {i}/{len(aligned)} ...")
        merged = pd.merge(merged, df, on=id_cols, how="outer", suffixes=("", "_dup"))
        dup_cols = [c for c in merged.columns if c.endswith("_dup")]
        if dup_cols:
            merged = merged.drop(columns=dup_cols)

    # Map header -> datetime (for date-like columns)
    date_cols_map: Dict[str, datetime] = {}
    for col in merged.columns:
        if col in id_cols:
            continue
        dt = parse_date_from_header(col, date_order=date_order)
        if dt:
            date_cols_map[col] = dt

    if not date_cols_map:
        debug_out = output_file.replace(".xlsx", "_DEBUG_no_date_columns.xlsx")
        merged.to_excel(debug_out, index=False)
        log(f"No date-like columns found; wrote debug export: {debug_out}")
        return

    # Month window with most dates
    all_detected_dates = sorted(set(date_cols_map.values()))
    period_start, period_end = compute_month_window_with_most_dates(all_detected_dates)
    log(f"Applying month-with-most-dates window: {period_start.strftime('%Y-%m-%d')} -> {period_end.strftime('%Y-%m-%d')}")

    # Select only columns inside the window
    selected_cols = [c for c, dt in date_cols_map.items() if period_start <= dt <= period_end]
    if not selected_cols:
        debug_out = output_file.replace(".xlsx", "_DEBUG_window_matched_zero.xlsx")
        merged.to_excel(debug_out, index=False)
        log(f"No columns fell within the chosen month window; wrote: {debug_out}")
        return

    # Combine duplicates (same logical date), normalize names, keep chronological order
    dt_to_cols: Dict[datetime, List[str]] = defaultdict(list)
    for col in selected_cols:
        dt_to_cols[date_cols_map[col]].append(col)

    combined = merged[id_cols].copy()
    ordered_dates = sorted(dt_to_cols.keys())

    # Optional completeness report (within chosen month)
    expected: List[datetime] = []
    d = period_start
    while d <= period_end:
        expected.append(d)
        d += timedelta(days=1)

    present = [dt for dt in ordered_dates if period_start <= dt <= period_end]
    missing = sorted(set(expected) - set(present))
    if missing:
        report_path = output_file.replace(".xlsx", "_MISSING_DATES.txt")
        with open(report_path, "w", encoding="utf-8") as fh:
            fh.write(f"Missing {len(missing)} day(s) in window {period_start.date()} -> {period_end.date()}:\n")
            for dt in missing:
                fh.write(dt.strftime("%Y-%m-%d") + "\n")
        log(f"Missing-date report saved to: {os.path.abspath(report_path)}")

    normalized_names: List[str] = []
    for dt in ordered_dates:
        if not (period_start <= dt <= period_end):
            continue
        cols_for_dt = dt_to_cols[dt]
        series = merged[cols_for_dt[0]].copy()
        for extra_col in cols_for_dt[1:]:
            series = series.combine_first(merged[extra_col])
        series = series.replace("-", pd.NA)
        col_name = dt.strftime("%m/%d/%Y")
        combined[col_name] = series
        normalized_names.append(col_name)

    # Remove rows with all-zero hours across window
    if normalized_names:
        numeric_block = combined[normalized_names].apply(pd.to_numeric, errors="coerce").fillna(0)
        has_any_hours = (numeric_block != 0).any(axis=1)
        filtered = combined.loc[has_any_hours].copy()
    else:
        filtered = combined.copy()

    # Stable sort by ID columns if present
    sort_cols = [c for c in id_cols if c in filtered.columns]
    if sort_cols:
        filtered = filtered.sort_values(by=sort_cols, kind="stable")

    # Optionally drop Ext. ID
    if drop_ext_id:
        filtered = filtered.drop(columns=["Ext. ID"], errors="ignore")

    # Final safety: drop blank-ish staff rows
    if id_cols:
        filtered = filtered[valid_name_mask(filtered[id_cols[0]])]

    # Save
    os.makedirs(os.path.dirname(os.path.abspath(output_file)), exist_ok=True)
    if output_file.lower().endswith(".csv"):
        filtered.to_csv(output_file, index=False)
    else:
        filtered.to_excel(output_file, index=False)
    log(f"✅ Saved consolidated file to: {os.path.abspath(output_file)}")
    log(f"Rows: {len(filtered):,} | Date columns: {len(normalized_names):,}")

# ---------- UI helpers ----------
def reveal_in_file_manager(path: str) -> None:
    """Open the OS file manager to show the saved file."""
    try:
        if sys.platform.startswith("darwin"):
            subprocess.run(["open", "-R", path], check=False)
        elif os.name == "nt":
            norm = os.path.normpath(path)
            subprocess.run(["explorer", "/select,", norm], check=False)
        else:
            folder = os.path.dirname(os.path.abspath(path))
            subprocess.run(["xdg-open", folder], check=False)
    except Exception as e:
        log(f"Could not reveal file in file manager: {e}")

# ---------- UI (always created on launch) ----------
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

class TimesheetApp(tk.Tk):
    def __init__(self, date_order: str = "mdy"):
        super().__init__()
        self.title("Timesheet Consolidator")
        self.geometry("900x620")

        try:
            self.attributes("-topmost", True); self.update(); self.attributes("-topmost", False)
        except Exception:
            pass

        self.files: List[str] = []
        self.id_cols: List[str] = []
        self.norm_cols: List[str] = []
        self.filtered_df: Optional[pd.DataFrame] = None
        self.period_start: Optional[datetime] = None
        self.period_end: Optional[datetime] = None
        self.date_order = date_order

        # Top controls
        top = tk.Frame(self, padx=12, pady=12); top.pack(fill="x")
        self.locate_btn = tk.Button(top, text="Locate Timesheets", command=self.on_locate, width=20); self.locate_btn.pack(side="left")
        self.info_lbl = tk.Label(top, text="Select weekly CSV files to preview month totals.", anchor="w"); self.info_lbl.pack(side="left", padx=12)

        # Table
        table_frame = tk.Frame(self, padx=12, pady=6); table_frame.pack(fill="both", expand=True)
        cols = ("STAFF NAME", "TOTAL DAYS", "HOURS WORKED")
        self.tree = ttk.Treeview(table_frame, columns=cols, show="headings", height=18)
        for c in cols: self.tree.heading(c, text=c)
        self.tree.column("STAFF NAME", width=320, anchor="w")
        self.tree.column("TOTAL DAYS", width=160, anchor="center")
        self.tree.column("HOURS WORKED", width=180, anchor="center")
        self.tree.pack(fill="both", expand=True, side="left")
        yscroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview); self.tree.configure(yscroll=yscroll.set); yscroll.pack(side="right", fill="y")

        # Bottom controls
        bottom = tk.Frame(self, padx=12, pady=12); bottom.pack(fill="x")
        self.output_entry = tk.Entry(bottom, width=75); self.output_entry.insert(0, "Staff Hours .xlsx"); self.output_entry.pack(side="left")
        self.gen_btn = tk.Button(bottom, text="Generate File", command=self.on_generate, width=18, state="disabled"); self.gen_btn.pack(side="left", padx=12)
        self.quit_btn = tk.Button(bottom, text="Quit", command=self.destroy, width=10); self.quit_btn.pack(side="left")

    def on_locate(self):
        self.locate_btn.config(text="Opening...", state="disabled"); self.update_idletasks()
        initial_dir = os.path.join(os.path.expanduser("~"), "Downloads")
        if not os.path.isdir(initial_dir): initial_dir = os.path.expanduser("~")
        paths = filedialog.askopenfilenames(
            parent=self,
            title="Select Weekly Timesheet CSV Files",
            filetypes=[("CSV Files", "*.csv")],
            initialdir=initial_dir
        )
        self.locate_btn.config(text="Locate Timesheets", state="normal")
        if not paths: return
        self.files = list(paths)
        self.load_preview()

    def load_preview(self):
        df, id_cols, norm_cols, p_start, p_end = _prepare_month_block_for_ui(self.files, date_order=self.date_order)
        self.filtered_df, self.id_cols, self.norm_cols = df, id_cols, norm_cols
        self.period_start, self.period_end = p_start, p_end

        for i in self.tree.get_children(): self.tree.delete(i)

        if df is None or df.empty or not id_cols or not norm_cols:
            if self.period_start is not None:
                self.info_lbl.config(
                    text=f"No usable data / no columns found for chosen month {self.period_start.strftime('%B %Y')}."
                )
            else:
                self.info_lbl.config(text="No usable data / no month columns detected.")
            self.gen_btn.config(state="disabled")
            return

        staff_col = id_cols[0]

        num_block = df[norm_cols].apply(pd.to_numeric, errors="coerce").fillna(0)
        total_hours = num_block.sum(axis=1)
        total_days = (num_block > 0).sum(axis=1)

        preview = pd.DataFrame({
            "STAFF NAME": df[staff_col].astype(str).fillna(""),
            "TOTAL DAYS": total_days.astype(int),
            "HOURS WORKED": total_hours.round(2),
        })

        for _, row in preview.iterrows():
            self.tree.insert("", "end", values=(row["STAFF NAME"], int(row["TOTAL DAYS"]), float(row["HOURS WORKED"])))

        # Default output filename "Staff Hours <Month> <Year>.xlsx"
        if self.period_start is not None:
            out_name = f"Staff Hours {self.period_start.strftime('%B')} {self.period_start.strftime('%Y')}.xlsx"
            self.output_entry.delete(0, "end"); self.output_entry.insert(0, out_name)

        month_txt = self.period_start.strftime("%B %Y") if self.period_start else "?"
        self.info_lbl.config(text=f"Loaded {len(self.files)} file(s). Month: {month_txt}. Rows: {len(preview)}")
        self.gen_btn.config(state="normal")

    def on_generate(self):
        if not self.files:
            messagebox.showwarning("No files", "Please locate timesheets first.")
            return

        suggested = self.output_entry.get().strip() or "Staff Hours.xlsx"
        base, ext = os.path.splitext(suggested)
        if ext.lower() not in (".xlsx", ".csv"):
            ext = ".xlsx"
        filetypes = [("Excel Workbook", "*.xlsx"), ("CSV (Comma delimited)", "*.csv"), ("All Files", "*.*")]
        save_path = filedialog.asksaveasfilename(
            parent=self,
            title="Save Consolidated File As",
            initialfile=base + ext,
            defaultextension=ext,
            filetypes=filetypes,
        )
        if not save_path:
            return

        self.gen_btn.config(text="Generating...", state="disabled")
        self.update_idletasks()

        try:
            consolidate_timesheets(
                file_paths=self.files,
                output_file=save_path,
                date_order=self.date_order,
                drop_ext_id=True,
            )
            reveal_in_file_manager(save_path)
            messagebox.showinfo("Done", f"File created:\n{os.path.abspath(save_path)}")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to generate output:\n{e}")
        finally:
            self.gen_btn.config(text="Generate File", state="normal")

# ---------- Entry point ----------
def main():
    parser = argparse.ArgumentParser(description="Consolidate weekly timesheet CSVs into one Excel/CSV.")
    parser.add_argument("--date-order", choices=["mdy", "dmy"], default="mdy",
                        help="How dates appear in headers (default mdy).")
    args, _ = parser.parse_known_args()

    log("Launching UI window...")
    app = TimesheetApp(date_order=args.date_order)
    app.mainloop()
    log("UI closed.")

if __name__ == "__main__":
    main()
