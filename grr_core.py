"""
grr_core.py — Gauge R&R (Measurement System Analysis) calculation engine.

Implements the two crossed-study methods from the AIAG MSA Reference Manual
(4th ed.):

  1. Average & Range (X-bar / R) method
  2. ANOVA method (two-way crossed, with operator x part interaction)

No UI code lives here, so every number can be unit-tested against
published reference results.

Data format ("long" / tidy):
    Part | Operator | Trial | Measurement
One row per measurement. The study must be balanced: every part measured by
every operator the same number of times.

Terminology
-----------
EV   Equipment Variation  (repeatability)    — same operator, same part
AV   Appraiser Variation  (reproducibility)  — differences between operators
GRR  Gauge R&R = sqrt(EV² + AV²)
PV   Part Variation                           — true part-to-part spread
TV   Total Variation = sqrt(GRR² + PV²)
ndc  Number of Distinct Categories = 1.41 · PV / GRR (truncated)

All variation values returned here are STANDARD DEVIATIONS (σ). "Study
variation" multiplies σ by k (6 by default per AIAG 4th ed.; older
studies used 5.15).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy import stats

REQUIRED_COLS = ["Part", "Operator", "Trial", "Measurement"]

# --------------------------------------------------------------------------
# AIAG constants (1 / d2*)
# K1 depends on number of trials; K2 on number of operators; K3 on parts.
# K1 values for 2–3 trials are from the AIAG table. 4–5 trials use 1/d2
# (large-g approximation), which is what most software does.
# K2/K3 use d2* with g = 1.
# --------------------------------------------------------------------------
K1 = {2: 0.8862, 3: 0.5908, 4: 0.4857, 5: 0.4299}
K2_K3 = {2: 0.7071, 3: 0.5231, 4: 0.4467, 5: 0.4030, 6: 0.3742,
         7: 0.3534, 8: 0.3375, 9: 0.3249, 10: 0.3146, 11: 0.3070,
         12: 0.3004, 13: 0.2950, 14: 0.2902, 15: 0.2863}

# Control-chart constants for subgroup size n = trials
D4 = {2: 3.267, 3: 2.574, 4: 2.282, 5: 2.114}
A2 = {2: 1.880, 3: 1.023, 4: 0.729, 5: 0.577}

# AIAG acceptance guidance
GRR_ACCEPT = 10.0     # % — acceptable
GRR_MARGINAL = 30.0   # % — may be acceptable depending on application
NDC_MIN = 5


class StudyError(ValueError):
    """Raised when the data can't support a valid study."""


# --------------------------------------------------------------------------
# Result containers
# --------------------------------------------------------------------------
@dataclass
class Component:
    name: str
    sigma: float          # standard deviation
    var_contrib: float    # % of total variance (σ²)
    pct_study: float      # % of total variation (σ) — AIAG "%TV"
    pct_tol: float | None # % of tolerance (k·σ / tolerance)


@dataclass
class GRRResult:
    method: str
    n_parts: int
    n_ops: int
    n_trials: int
    k: float
    tolerance: float | None
    components: list[Component]
    ndc: int
    verdict: str
    verdict_basis: str
    anova_table: pd.DataFrame | None = None
    interaction_pooled: bool = False
    notes: list[str] = field(default_factory=list)

    def get(self, name: str) -> Component:
        for c in self.components:
            if c.name == name:
                return c
        raise KeyError(name)

    def to_frame(self) -> pd.DataFrame:
        rows = []
        for c in self.components:
            row = {
                "Source": c.name,
                "StdDev (σ)": c.sigma,
                f"Study Var ({self.k:g}σ)": c.sigma * self.k,
                "% Contribution (var)": c.var_contrib,
                "% Study Var (%TV)": c.pct_study,
            }
            if self.tolerance:
                row["% Tolerance"] = c.pct_tol
            rows.append(row)
        return pd.DataFrame(rows)


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------
def validate(df: pd.DataFrame) -> pd.DataFrame:
    """Clean and check a study. Returns a cleaned copy or raises StudyError."""
    missing = [c for c in REQUIRED_COLS if c not in df.columns]
    if missing:
        raise StudyError(f"Missing column(s): {', '.join(missing)}. "
                         f"Expected: {', '.join(REQUIRED_COLS)}.")

    d = df[REQUIRED_COLS].copy()
    d = d.dropna(how="all")
    for col in ("Part", "Operator", "Trial"):
        d[col] = d[col].astype(str).str.strip()
    d["Measurement"] = pd.to_numeric(d["Measurement"], errors="coerce")

    bad = d["Measurement"].isna() | d[["Part", "Operator", "Trial"]].isin(["", "nan"]).any(axis=1)
    if bad.any():
        rows = (d.index[bad] + 2).tolist()[:10]  # +2 → spreadsheet row incl. header
        raise StudyError(f"{int(bad.sum())} row(s) have blank or non-numeric values "
                         f"(e.g. spreadsheet rows {rows}).")

    dupes = d.duplicated(["Part", "Operator", "Trial"])
    if dupes.any():
        ex = d[dupes].iloc[0]
        raise StudyError(f"Duplicate entry: Part {ex.Part}, Operator {ex.Operator}, "
                         f"Trial {ex.Trial} appears more than once.")

    p, o = d["Part"].nunique(), d["Operator"].nunique()
    if p < 2:
        raise StudyError("Need at least 2 parts (AIAG recommends 10).")
    if o < 2:
        raise StudyError("Need at least 2 operators (AIAG recommends 3).")

    counts = d.groupby(["Part", "Operator"]).size()
    if counts.nunique() != 1 or len(counts) != p * o:
        raise StudyError("Study is unbalanced: every operator must measure every "
                         "part the same number of times.")
    r = int(counts.iloc[0])
    if r < 2:
        raise StudyError("Need at least 2 trials per part per operator.")

    if np.isclose(d["Measurement"].std(), 0):
        raise StudyError("All measurements are identical — check gauge resolution.")
    return d.reset_index(drop=True)


def study_shape(d: pd.DataFrame) -> tuple[int, int, int]:
    p, o = d["Part"].nunique(), d["Operator"].nunique()
    return p, o, len(d) // (p * o)


# --------------------------------------------------------------------------
# Shared output builder
# --------------------------------------------------------------------------
def _build(method, sig, p, o, r, k, tol, extra_notes=None, **kw) -> GRRResult:
    """sig: dict with EV, AV, (optional Interaction), GRR, PV, TV sigmas."""
    tv = sig["TV"]
    tv_var = tv ** 2
    comps = []
    for name in ["GRR", "EV", "AV", "Interaction", "PV", "TV"]:
        if name not in sig:
            continue
        s = sig[name]
        comps.append(Component(
            name={"GRR": "Gauge R&R", "EV": "Repeatability (EV)",
                  "AV": "Reproducibility (AV)", "Interaction": "  Operator × Part",
                  "PV": "Part-to-Part (PV)", "TV": "Total Variation (TV)"}[name],
            sigma=s,
            var_contrib=100 * s ** 2 / tv_var if tv_var else 0.0,
            pct_study=100 * s / tv if tv else 0.0,
            pct_tol=(100 * k * s / tol) if tol else None,
        ))

    pv, grr = sig["PV"], sig["GRR"]
    ndc = int(1.41 * pv / grr) if grr > 0 else 99
    ndc = max(ndc, 1)

    pct_tv = 100 * grr / tv
    # AIAG: judge on %TV for process control; %Tolerance for product control.
    if tol:
        pct = 100 * k * grr / tol
        basis = "% Tolerance"
    else:
        pct = pct_tv
        basis = "% Study Variation"
    if pct < GRR_ACCEPT:
        verdict = "ACCEPTABLE"
    elif pct <= GRR_MARGINAL:
        verdict = "MARGINAL"
    else:
        verdict = "UNACCEPTABLE"

    notes = list(extra_notes or [])
    if ndc < NDC_MIN:
        notes.append(f"ndc = {ndc} (< {NDC_MIN}): the gauge can't adequately "
                     "distinguish between parts in this sample.")
    if p < 10:
        notes.append(f"Only {p} parts. AIAG recommends 10 parts spanning the "
                     "expected process range.")
    if sig.get("EV", 0) > 0 and sig.get("AV", 0) > 0:
        if sig["EV"] > 2 * sig["AV"]:
            notes.append("Repeatability dominates: look at the gauge itself "
                         "(wear, fixturing, resolution, maintenance).")
        elif sig["AV"] > 2 * sig["EV"]:
            notes.append("Reproducibility dominates: look at operators "
                         "(training, technique, reading the scale, procedure).")

    return GRRResult(method=method, n_parts=p, n_ops=o, n_trials=r, k=k,
                     tolerance=tol, components=comps, ndc=ndc,
                     verdict=verdict, verdict_basis=basis, notes=notes, **kw)


# --------------------------------------------------------------------------
# Method 1: Average & Range
# --------------------------------------------------------------------------
def average_range(df: pd.DataFrame, k: float = 6.0,
                  tolerance: float | None = None) -> GRRResult:
    d = validate(df)
    p, o, r = study_shape(d)
    if r not in K1:
        raise StudyError(f"Average & Range supports 2–5 trials (you have {r}). "
                         "Use the ANOVA method.")
    if o not in K2_K3 or p not in K2_K3:
        raise StudyError("Average & Range supports 2–15 operators and parts. "
                         "Use the ANOVA method.")

    cell = d.groupby(["Operator", "Part"])["Measurement"]
    ranges = cell.max() - cell.min()
    r_bar_by_op = ranges.groupby(level="Operator").mean()
    r_dbl_bar = r_bar_by_op.mean()

    x_bar_by_op = d.groupby("Operator")["Measurement"].mean()
    x_diff = x_bar_by_op.max() - x_bar_by_op.min()

    part_means = d.groupby("Part")["Measurement"].mean()
    r_p = part_means.max() - part_means.min()

    ev = r_dbl_bar * K1[r]
    av_sq = (x_diff * K2_K3[o]) ** 2 - ev ** 2 / (p * r)
    av = math.sqrt(av_sq) if av_sq > 0 else 0.0
    grr = math.hypot(ev, av)
    pv = r_p * K2_K3[p]
    tv = math.hypot(grr, pv)

    notes = []
    ucl_r = D4[r] * r_dbl_bar
    out = ranges[ranges > ucl_r]
    if len(out):
        notes.append(f"{len(out)} range(s) exceed UCL_R = {ucl_r:.4g}. Investigate "
                     "those readings before trusting the result.")
    if av == 0.0:
        notes.append("AV computed negative and was set to 0 (operators agree "
                     "within repeatability noise).")

    return _build("Average & Range", dict(EV=ev, AV=av, GRR=grr, PV=pv, TV=tv),
                  p, o, r, k, tolerance, notes)


# --------------------------------------------------------------------------
# Method 2: ANOVA (two-way crossed, random effects)
# --------------------------------------------------------------------------
def anova(df: pd.DataFrame, k: float = 6.0, tolerance: float | None = None,
          alpha_interaction: float = 0.05) -> GRRResult:
    """
    If the operator×part interaction p-value > alpha_interaction, the
    interaction is pooled into repeatability (same as Minitab's default
    behaviour, alpha = 0.05; AIAG mentions 0.25 as a more conservative choice).
    """
    d = validate(df)
    p, o, r = study_shape(d)
    y = d["Measurement"]
    grand = y.mean()
    n = len(d)

    part_m = d.groupby("Part")["Measurement"].mean()
    op_m = d.groupby("Operator")["Measurement"].mean()
    cell_m = d.groupby(["Part", "Operator"])["Measurement"].mean()

    ss_total = ((y - grand) ** 2).sum()
    ss_part = o * r * ((part_m - grand) ** 2).sum()
    ss_op = p * r * ((op_m - grand) ** 2).sum()
    ss_cell = r * ((cell_m - grand) ** 2).sum()
    ss_int = ss_cell - ss_part - ss_op
    ss_err = ss_total - ss_cell

    df_part, df_op = p - 1, o - 1
    df_int, df_err = df_part * df_op, p * o * (r - 1)
    df_total = n - 1

    ms_part, ms_op = ss_part / df_part, ss_op / df_op
    ms_int, ms_err = ss_int / df_int, ss_err / df_err

    # Interaction test against repeatability
    f_int = ms_int / ms_err if ms_err > 0 else float("inf")
    p_int = float(stats.f.sf(f_int, df_int, df_err))

    pooled = p_int > alpha_interaction
    if pooled:
        df_pool = df_int + df_err
        ms_pool = (ss_int + ss_err) / df_pool
        f_part, f_op = ms_part / ms_pool, ms_op / ms_pool
        table = pd.DataFrame([
            ["Part", df_part, ss_part, ms_part, f_part,
             stats.f.sf(f_part, df_part, df_pool)],
            ["Operator", df_op, ss_op, ms_op, f_op,
             stats.f.sf(f_op, df_op, df_pool)],
            ["Repeatability", df_pool, ss_int + ss_err, ms_pool, np.nan, np.nan],
            ["Total", df_total, ss_total, np.nan, np.nan, np.nan],
        ], columns=["Source", "DF", "SS", "MS", "F", "P"])
        var_rep = ms_pool
        var_op = max((ms_op - ms_pool) / (p * r), 0.0)
        var_int = 0.0
        var_part = max((ms_part - ms_pool) / (o * r), 0.0)
    else:
        f_part, f_op = ms_part / ms_int, ms_op / ms_int
        table = pd.DataFrame([
            ["Part", df_part, ss_part, ms_part, f_part,
             stats.f.sf(f_part, df_part, df_int)],
            ["Operator", df_op, ss_op, ms_op, f_op,
             stats.f.sf(f_op, df_op, df_int)],
            ["Operator × Part", df_int, ss_int, ms_int, f_int, p_int],
            ["Repeatability", df_err, ss_err, ms_err, np.nan, np.nan],
            ["Total", df_total, ss_total, np.nan, np.nan, np.nan],
        ], columns=["Source", "DF", "SS", "MS", "F", "P"])
        var_rep = ms_err
        var_op = max((ms_op - ms_int) / (p * r), 0.0)
        var_int = max((ms_int - ms_err) / r, 0.0)
        var_part = max((ms_part - ms_int) / (o * r), 0.0)

    var_repro = var_op + var_int
    var_grr = var_rep + var_repro
    var_tv = var_grr + var_part

    sig = dict(EV=math.sqrt(var_rep), AV=math.sqrt(var_repro),
               GRR=math.sqrt(var_grr), PV=math.sqrt(var_part),
               TV=math.sqrt(var_tv))
    if not pooled:
        sig["Interaction"] = math.sqrt(var_int)

    notes = []
    if pooled:
        notes.append(f"Operator × Part interaction not significant "
                     f"(p = {p_int:.3f} > α = {alpha_interaction}); pooled into "
                     "repeatability.")
    else:
        notes.append(f"Operator × Part interaction is significant "
                     f"(p = {p_int:.3f}). Operators measure some parts "
                     "differently than others — check part features, fixturing, "
                     "or measurement location.")

    return _build("ANOVA", sig, p, o, r, k, tolerance, notes,
                  anova_table=table, interaction_pooled=pooled)


# --------------------------------------------------------------------------
# Chart data helpers (used by the UI, tested here)
# --------------------------------------------------------------------------
def range_chart_data(df: pd.DataFrame) -> tuple[pd.DataFrame, float, float]:
    """Per-cell ranges, R̄, and UCL_R."""
    d = validate(df)
    _, _, r = study_shape(d)
    g = d.groupby(["Operator", "Part"])["Measurement"]
    out = (g.max() - g.min()).rename("Range").reset_index()
    r_bar = out["Range"].mean()
    return out, r_bar, D4.get(r, np.nan) * r_bar


def xbar_chart_data(df: pd.DataFrame) -> tuple[pd.DataFrame, float, float, float]:
    """Per-cell averages, grand mean, LCL, UCL (X̄ ± A2·R̄)."""
    d = validate(df)
    _, _, r = study_shape(d)
    out = d.groupby(["Operator", "Part"])["Measurement"].mean().rename("Average").reset_index()
    grand = d["Measurement"].mean()
    _, r_bar, _ = range_chart_data(d)
    a2 = A2.get(r, np.nan)
    return out, grand, grand - a2 * r_bar, grand + a2 * r_bar


# --------------------------------------------------------------------------
# Data helpers
# --------------------------------------------------------------------------
def wide_to_long(wide: pd.DataFrame, n_ops: int, n_trials: int,
                 op_names: list[str] | None = None) -> pd.DataFrame:
    """
    Convert the paper-form layout (one row per part; columns
    A-1, A-2, A-3, B-1, ...) into the long format.
    """
    op_names = op_names or [chr(ord("A") + i) for i in range(n_ops)]
    rows = []
    for _, row in wide.iterrows():
        part = row["Part"]
        for op in op_names:
            for t in range(1, n_trials + 1):
                rows.append({"Part": part, "Operator": op, "Trial": t,
                             "Measurement": row.get(f"{op}-{t}")})
    return pd.DataFrame(rows)


def part_sort_key(s) -> tuple:
    """Natural order: numeric IDs sort as numbers ('2' before '10'),
    anything else sorts alphabetically after them."""
    s = str(s)
    try:
        return (0, float(s), s)
    except ValueError:
        return (1, 0.0, s)


def long_to_wide(d: pd.DataFrame) -> pd.DataFrame:
    w = d.pivot_table(index="Part", columns=["Operator", "Trial"],
                      values="Measurement", aggfunc="first")
    w.columns = [f"{op}-{t}" for op, t in w.columns]
    w = w.loc[sorted(w.index, key=part_sort_key)]
    return w.reset_index()


def blank_study(n_parts: int, n_ops: int, n_trials: int,
                op_names: list[str] | None = None) -> pd.DataFrame:
    op_names = op_names or [chr(ord("A") + i) for i in range(n_ops)]
    cols = ["Part"] + [f"{op}-{t}" for op in op_names
                       for t in range(1, n_trials + 1)]
    w = pd.DataFrame(np.nan, index=range(n_parts), columns=cols)
    w["Part"] = [str(i + 1) for i in range(n_parts)]
    return w


def synthetic_study(seed: int = 7, n_parts: int = 10, n_ops: int = 3,
                    n_trials: int = 3, nominal: float = 1.250,
                    part_sd: float = 0.012, repeat_sd: float = 0.0015,
                    op_bias: tuple[float, ...] = (0.0, 0.0012, -0.0008)
                    ) -> pd.DataFrame:
    """
    Synthetic demo: transformer tank-flange thickness (in), measured with a
    micrometer. NOT real production data.
    """
    rng = np.random.default_rng(seed)
    parts = nominal + rng.normal(0, part_sd, n_parts)
    ops = [chr(ord("A") + i) for i in range(n_ops)]
    rows = []
    for i, true in enumerate(parts, start=1):
        for j, op in enumerate(ops):
            bias = op_bias[j] if j < len(op_bias) else 0.0
            for t in range(1, n_trials + 1):
                rows.append({"Part": str(i), "Operator": op, "Trial": t,
                             "Measurement": round(true + bias +
                                                  rng.normal(0, repeat_sd), 4)})
    return pd.DataFrame(rows)
