"""Tests for the Excel report and natural part ordering."""

import io
import sys
import unittest
from datetime import date
from pathlib import Path

import pandas as pd
from openpyxl import load_workbook

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import grr_core as g  # noqa: E402
from report import build_report, safe_filename  # noqa: E402


class TestPartOrder(unittest.TestCase):
    def test_numeric_parts_sort_naturally(self):
        wide = g.long_to_wide(g.validate(g.synthetic_study()))
        self.assertEqual(wide["Part"].tolist(), [str(i) for i in range(1, 11)])

    def test_mixed_ids(self):
        self.assertEqual(sorted(["10", "B", "2", "A"], key=g.part_sort_key),
                         ["2", "10", "A", "B"])


class TestReport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        clean = g.validate(g.synthetic_study())
        res = [g.anova(clean, tolerance=0.06), g.average_range(clean, tolerance=0.06)]
        data = build_report(res, clean, characteristic="Tank flange thickness",
                            gauge="Micrometer", units="in", lsl=1.22, usl=1.28,
                            today=date(2026, 11, 19))
        cls.wb = load_workbook(io.BytesIO(data))
        cls.res = res

    def test_sheets(self):
        self.assertEqual(self.wb.sheetnames,
                         ["Summary", "ANOVA", "Average & Range", "ANOVA table", "Data"])

    def test_verdict_on_summary(self):
        vals = [c.value for row in self.wb["Summary"].iter_rows() for c in row]
        self.assertIn("MARGINAL", vals)

    def test_data_sheet_order(self):
        ws = self.wb["Data"]
        parts = [ws.cell(r, 1).value for r in range(4, 14)]
        self.assertEqual(parts, [str(i) for i in range(1, 11)])

    def test_number_formats(self):
        ws = self.wb["ANOVA"]
        self.assertEqual(ws.cell(4, 2).number_format, "0.000000")


class TestFilename(unittest.TestCase):
    def test_sanitized(self):
        self.assertEqual(safe_filename("../Flange: OD/ID", date(2026, 11, 19)),
                         "GRR_Flange_OD_ID_20261119.xlsx")
        self.assertEqual(safe_filename("", date(2026, 11, 19)), "GRR_study_20261119.xlsx")


if __name__ == "__main__":
    unittest.main()
