"""
Unit tests for grr_core.

Reference data: the 10-part / 3-appraiser / 3-trial example published in the
AIAG MSA Reference Manual (4th ed.). Expected Average & Range results are
the manual's worked answers; expected ANOVA results match the widely
published Minitab output for the same data set.

Run:  python -m pytest -q      (or)   python -m unittest discover tests
"""

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import grr_core as g  # noqa: E402

AIAG = {
    "A": [[0.29, -0.56, 1.34, 0.47, -0.80, 0.02, 0.59, -0.31, 2.26, -1.36],
          [0.41, -0.68, 1.17, 0.50, -0.92, -0.11, 0.75, -0.20, 1.99, -1.25],
          [0.64, -0.58, 1.27, 0.64, -0.84, -0.21, 0.66, -0.17, 2.01, -1.31]],
    "B": [[0.08, -0.47, 1.19, 0.01, -0.56, -0.20, 0.47, -0.63, 1.80, -1.68],
          [0.25, -1.22, 0.94, 1.03, -1.20, 0.22, 0.55, 0.08, 2.12, -1.62],
          [0.07, -0.68, 1.34, 0.20, -1.28, 0.06, 0.83, -0.34, 2.19, -1.50]],
    "C": [[0.04, -1.38, 0.88, 0.14, -1.46, -0.29, 0.02, -0.46, 1.77, -1.49],
          [-0.11, -1.13, 1.09, 0.20, -1.07, -0.67, 0.01, -0.56, 1.45, -1.77],
          [-0.15, -0.96, 0.67, 0.11, -1.45, -0.49, 0.21, -0.49, 1.87, -2.16]],
}


def aiag_df() -> pd.DataFrame:
    rows = []
    for op, trials in AIAG.items():
        for t, vals in enumerate(trials, start=1):
            for part, v in enumerate(vals, start=1):
                rows.append({"Part": part, "Operator": op, "Trial": t,
                             "Measurement": v})
    return pd.DataFrame(rows)


class TestAverageRangeAIAG(unittest.TestCase):
    """Matches the AIAG MSA manual worked example."""

    @classmethod
    def setUpClass(cls):
        cls.r = g.average_range(aiag_df())

    def test_ev(self):
        self.assertAlmostEqual(self.r.get("Repeatability (EV)").sigma, 0.20188, places=4)

    def test_av(self):
        self.assertAlmostEqual(self.r.get("Reproducibility (AV)").sigma, 0.22963, places=4)

    def test_grr(self):
        self.assertAlmostEqual(self.r.get("Gauge R&R").sigma, 0.30575, places=4)

    def test_pv(self):
        self.assertAlmostEqual(self.r.get("Part-to-Part (PV)").sigma, 1.10456, places=4)

    def test_tv(self):
        self.assertAlmostEqual(self.r.get("Total Variation (TV)").sigma, 1.14610, places=4)

    def test_pct_grr(self):
        self.assertAlmostEqual(self.r.get("Gauge R&R").pct_study, 26.68, places=1)

    def test_ndc_and_verdict(self):
        self.assertEqual(self.r.ndc, 5)
        self.assertEqual(self.r.verdict, "MARGINAL")


class TestAnovaAIAG(unittest.TestCase):
    """Matches published Minitab output for the same data (interaction pooled)."""

    @classmethod
    def setUpClass(cls):
        cls.r = g.anova(aiag_df())

    def test_interaction_pooled(self):
        self.assertTrue(self.r.interaction_pooled)

    def test_variance_components(self):
        self.assertAlmostEqual(self.r.get("Gauge R&R").sigma ** 2, 0.09143, places=4)
        self.assertAlmostEqual(self.r.get("Repeatability (EV)").sigma ** 2, 0.03997, places=4)
        self.assertAlmostEqual(self.r.get("Reproducibility (AV)").sigma ** 2, 0.05146, places=4)
        self.assertAlmostEqual(self.r.get("Part-to-Part (PV)").sigma ** 2, 1.08645, places=4)

    def test_pct_contribution(self):
        self.assertAlmostEqual(self.r.get("Gauge R&R").var_contrib, 7.76, places=1)

    def test_ndc(self):
        self.assertEqual(self.r.ndc, 4)

    def test_ss_partition(self):
        t = self.r.anova_table.set_index("Source")
        self.assertAlmostEqual(t.loc["Part", "SS"] + t.loc["Operator", "SS"]
                               + t.loc["Repeatability", "SS"], t.loc["Total", "SS"], places=8)


class TestTolerance(unittest.TestCase):
    def test_pct_tolerance_and_verdict_basis(self):
        r = g.average_range(aiag_df(), tolerance=5.0)
        self.assertEqual(r.verdict_basis, "% Tolerance")
        self.assertAlmostEqual(r.get("Gauge R&R").pct_tol, 100 * 6 * 0.30575 / 5.0, places=1)

    def test_k_515(self):
        r = g.average_range(aiag_df(), k=5.15, tolerance=5.0)
        self.assertAlmostEqual(r.get("Gauge R&R").pct_tol, 100 * 5.15 * 0.30575 / 5.0, places=1)


class TestValidation(unittest.TestCase):
    def test_missing_column(self):
        with self.assertRaises(g.StudyError):
            g.anova(aiag_df().drop(columns="Trial"))

    def test_unbalanced(self):
        with self.assertRaises(g.StudyError):
            g.anova(aiag_df().iloc[1:])

    def test_non_numeric(self):
        d = aiag_df()
        d["Measurement"] = d["Measurement"].astype(object)
        d.loc[5, "Measurement"] = "abc"
        with self.assertRaisesRegex(g.StudyError, "non-numeric"):
            g.validate(d)

    def test_duplicate(self):
        d = aiag_df()
        d.loc[1, ["Part", "Operator", "Trial"]] = d.loc[0, ["Part", "Operator", "Trial"]]
        with self.assertRaisesRegex(g.StudyError, "Duplicate"):
            g.validate(d)

    def test_single_operator(self):
        with self.assertRaises(g.StudyError):
            g.anova(aiag_df().query("Operator == 'A'"))


class TestHelpers(unittest.TestCase):
    def test_wide_long_roundtrip(self):
        d = g.validate(aiag_df())
        back = g.wide_to_long(g.long_to_wide(d), 3, 3)
        r1, r2 = g.anova(d), g.anova(back)
        self.assertAlmostEqual(r1.get("Gauge R&R").sigma, r2.get("Gauge R&R").sigma)

    def test_synthetic_runs(self):
        d = g.synthetic_study()
        self.assertEqual(len(d), 90)
        r = g.anova(d, tolerance=0.06)
        self.assertIn(r.verdict, {"ACCEPTABLE", "MARGINAL", "UNACCEPTABLE"})

    def test_chart_data(self):
        rd, rbar, ucl = g.range_chart_data(aiag_df())
        self.assertAlmostEqual(rbar, 0.3417, places=4)
        self.assertAlmostEqual(ucl, 2.574 * 0.3417, places=3)

    def test_methods_agree_roughly(self):
        a, b = g.average_range(aiag_df()), g.anova(aiag_df())
        self.assertLess(abs(a.get("Gauge R&R").sigma - b.get("Gauge R&R").sigma), 0.02)


if __name__ == "__main__":
    unittest.main()
