"""
report.py — Formatted Excel report for a Gauge R&R study.

Kept out of app.py so it can be tested without Streamlit.
"""

from __future__ import annotations

import io
import re
from datetime import date

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

import grr_core as g

# Colors
NAVY = "1F3864"
HEADER_FILL = PatternFill("solid", fgColor=NAVY)
HEADER_FONT = Font(bold=True, color="FFFFFF")
GRR_ROW_FILL = PatternFill("solid", fgColor="EAF0F8")
VERDICT_FILL = {
    "ACCEPTABLE": PatternFill("solid", fgColor="C6EFCE"),
    "MARGINAL": PatternFill("solid", fgColor="FFEB9C"),
    "UNACCEPTABLE": PatternFill("solid", fgColor="FFC7CE"),
}
THIN = Side(style="thin", color="BFBFBF")
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)


def safe_filename(characteristic: str, today: date | None = None) -> str:
    """Characters allowed in a download name: letters, digits, _ and -."""
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", characteristic.strip())[:50].strip("_")
    return f"GRR_{stem or 'study'}_{(today or date.today()):%Y%m%d}.xlsx"


def _write_table(ws, df: pd.DataFrame, start_row: int,
                 formats: dict[str, str] | None = None,
                 highlight_first: bool = False) -> int:
    """Write a DataFrame with a styled header. Returns the next free row."""
    formats = formats or {}
    for j, col in enumerate(df.columns, start=1):
        c = ws.cell(start_row, j, col)
        c.fill, c.font, c.border = HEADER_FILL, HEADER_FONT, BOX
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for i, row in enumerate(df.itertuples(index=False), start=start_row + 1):
        for j, (col, val) in enumerate(zip(df.columns, row), start=1):
            if isinstance(val, float) and pd.isna(val):
                val = None
            c = ws.cell(i, j, val.strip() if isinstance(val, str) else val)
            c.border = BOX
            if col in formats:
                c.number_format = formats[col]
            if highlight_first and i == start_row + 1:
                c.fill, c.font = GRR_ROW_FILL, Font(bold=True)
    return start_row + len(df) + 2


def _autowidth(ws, minimum: int = 10, maximum: int = 60) -> None:
    widths: dict[int, int] = {}
    for row in ws.iter_rows():
        for c in row:
            if c.value is not None and not (c.row <= 2 and c.column == 1):
                widths[c.column] = max(widths.get(c.column, 0), len(str(c.value)))
    for col, w in widths.items():
        ws.column_dimensions[get_column_letter(col)].width = max(minimum, min(maximum, w + 3))


def build_report(results: list[g.GRRResult], clean: pd.DataFrame, *,
                 characteristic: str, gauge: str, units: str,
                 lsl: float | None, usl: float | None,
                 today: date | None = None) -> bytes:
    main = results[0]
    p, o, r = g.study_shape(clean)
    today = today or date.today()
    grr = main.get("Gauge R&R")

    wb = Workbook()

    # ---- Summary --------------------------------------------------------
    ws = wb.active
    ws.title = "Summary"
    ws["A1"] = "Gauge R&R Study Report"
    ws["A1"].font = Font(bold=True, size=16, color=NAVY)
    ws["A2"] = "Measurement System Analysis per AIAG MSA Reference Manual, 4th ed."
    ws["A2"].font = Font(italic=True, color="595959")

    info = [
        ("Characteristic", characteristic),
        ("Gauge", gauge),
        ("Units", units),
        ("Study date", today.isoformat()),
        ("Method", main.method),
        ("Parts × Operators × Trials", f"{p} × {o} × {r}"),
        ("LSL / USL", f"{lsl:g} / {usl:g}" if main.tolerance else "Not evaluated"),
        ("Tolerance", main.tolerance if main.tolerance else "—"),
        ("Study variation multiplier (k)", f"{main.k:g}σ"),
    ]
    row = 4
    for label, val in info:
        ws.cell(row, 1, label).font = Font(bold=True)
        ws.cell(row, 2, val).alignment = Alignment(horizontal="left")
        row += 1

    row += 1
    ws.cell(row, 1, "VERDICT").font = Font(bold=True, size=13)
    v = ws.cell(row, 2, main.verdict)
    v.font, v.fill = Font(bold=True, size=13), VERDICT_FILL[main.verdict]
    ws.cell(row, 3, f"basis: {main.verdict_basis}").font = Font(italic=True, color="595959")
    row += 2

    metrics = pd.DataFrame([
        ["%GRR (study variation)", grr.pct_study / 100, "< 10% acceptable · 10–30% marginal · > 30% unacceptable"],
        ["%GRR (tolerance)", (grr.pct_tol / 100) if main.tolerance else None, "Same criteria; use for pass/fail inspection"],
        ["% Contribution (variance)", grr.var_contrib / 100, "< 1% acceptable · 1–9% marginal · > 9% unacceptable"],
        ["Number of distinct categories (ndc)", main.ndc, "≥ 5 required"],
    ], columns=["Metric", "Result", "AIAG guidance"])
    row = _write_table(ws, metrics, row)
    for rr in range(row - 2 - len(metrics) + 1, row - 1):
        if ws.cell(rr, 1).value != "Number of distinct categories (ndc)":
            ws.cell(rr, 2).number_format = "0.0%"

    if main.notes:
        ws.cell(row, 1, "Findings").font = Font(bold=True)
        row += 1
        for n in main.notes:
            ws.cell(row, 1, f"• {n}").alignment = Alignment(wrap_text=False)
            row += 1

    row += 1
    ws.cell(row, 1, "Prepared by:").font = Font(bold=True)
    ws.cell(row, 2, "________________________")
    ws.cell(row + 1, 1, "Reviewed by:").font = Font(bold=True)
    ws.cell(row + 1, 2, "________________________")
    _autowidth(ws)
    ws.column_dimensions["A"].width = 36
    ws.column_dimensions["B"].width = 28

    # ---- Results (one sheet per method) ---------------------------------
    pct_fmt = "0.00"
    for res in results:
        sheet = wb.create_sheet(res.method[:31])
        sheet["A1"] = f"Variance Components — {res.method}"
        sheet["A1"].font = Font(bold=True, size=13, color=NAVY)
        frame = res.to_frame()
        fmts = {"StdDev (σ)": "0.000000", f"Study Var ({res.k:g}σ)": "0.000000",
                "% Contribution (var)": pct_fmt, "% Study Var (%TV)": pct_fmt,
                "% Tolerance": pct_fmt}
        nxt = _write_table(sheet, frame, 3, fmts, highlight_first=True)
        sheet.cell(nxt, 1, f"ndc = {res.ndc}   ·   verdict = {res.verdict} ({res.verdict_basis})")
        _autowidth(sheet)
        sheet.freeze_panes = "A4"

    # ---- ANOVA table ----------------------------------------------------
    an = next((x for x in results if x.anova_table is not None), None)
    if an is not None:
        sheet = wb.create_sheet("ANOVA table")
        sheet["A1"] = "Two-way crossed ANOVA (random effects)"
        sheet["A1"].font = Font(bold=True, size=13, color=NAVY)
        t = an.anova_table.copy()
        t["P"] = t["P"].map(lambda x: None if pd.isna(x) else ("< 0.0001" if x < 1e-4 else round(float(x), 4)))
        nxt = _write_table(sheet, t, 3, {"SS": "0.000000E+00", "MS": "0.000000E+00", "F": "0.000"})
        sheet.cell(nxt, 1, "Interaction pooled into repeatability" if an.interaction_pooled
                   else "Operator × Part interaction retained (significant)")
        _autowidth(sheet)

    # ---- Raw data -------------------------------------------------------
    sheet = wb.create_sheet("Data")
    sheet["A1"] = f"Measurements ({units}) — columns are Operator-Trial"
    sheet["A1"].font = Font(bold=True, size=13, color=NAVY)
    wide = g.long_to_wide(clean)
    _write_table(sheet, wide, 3, {c: "0.0000" for c in wide.columns if c != "Part"})
    _autowidth(sheet)
    sheet.freeze_panes = "B4"

    for s in wb.worksheets:
        s.sheet_view.showGridLines = False
        s.page_setup.orientation = "landscape"
        s.sheet_properties.pageSetUpPr.fitToPage = True  # required for fitTo* to apply
        s.page_setup.fitToWidth = 1
        s.page_setup.fitToHeight = 0  # as many pages tall as needed

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
