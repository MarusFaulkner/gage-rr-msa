"""
app.py — Gauge R&R / MSA Studio (Streamlit UI).

Run locally:   streamlit run app.py
All calculations live in grr_core.py (unit-tested against the AIAG MSA
reference example); this file only handles input, layout, and charts.
"""

from __future__ import annotations

import io
from datetime import date

import altair as alt
import pandas as pd
import streamlit as st

import grr_core as g

st.set_page_config(page_title="Gauge R&R Studio", page_icon="📏", layout="wide")

VERDICT_STYLE = {
    "ACCEPTABLE": ("✅", "success"),
    "MARGINAL": ("⚠️", "warning"),
    "UNACCEPTABLE": ("❌", "error"),
}

# ----------------------------------------------------------------------------
# Sidebar: study setup
# ----------------------------------------------------------------------------
st.sidebar.title("📏 Gauge R&R Studio")
st.sidebar.caption("Measurement System Analysis per AIAG MSA (4th ed.)")

source = st.sidebar.radio(
    "Data source",
    ["Demo: flange thickness (synthetic)", "Enter data in a grid", "Upload CSV / Excel"],
)
method = st.sidebar.radio("Method", ["ANOVA", "Average & Range", "Both (compare)"])

st.sidebar.subheader("Study settings")
characteristic = st.sidebar.text_input("Characteristic", "Tank flange thickness")
gauge = st.sidebar.text_input("Gauge", "0–2 in outside micrometer")
units = st.sidebar.text_input("Units", "in")

use_tol = st.sidebar.checkbox("Evaluate against tolerance", value=True)
c1, c2 = st.sidebar.columns(2)
lsl = c1.number_input("LSL", value=1.220, format="%.4f", disabled=not use_tol)
usl = c2.number_input("USL", value=1.280, format="%.4f", disabled=not use_tol)
tolerance = (usl - lsl) if use_tol else None
if use_tol and tolerance is not None and tolerance <= 0:
    st.sidebar.error("USL must be greater than LSL.")
    tolerance = None

k = st.sidebar.selectbox("Study variation multiplier", [6.0, 5.15],
                         help="AIAG 4th ed. uses 6σ (99.73%). Older studies used 5.15σ (99%).")
alpha = st.sidebar.select_slider("ANOVA: α to pool interaction", [0.05, 0.10, 0.25], 0.05,
                                 help="If the Operator × Part p-value exceeds α, the "
                                      "interaction is pooled into repeatability.")

st.sidebar.divider()
st.sidebar.caption("Demo data is synthetic. Do not upload proprietary or "
                   "customer data to a public deployment.")

# ----------------------------------------------------------------------------
# Data input
# ----------------------------------------------------------------------------
st.title("Gauge R&R / MSA Studio")
st.caption("Is the measurement system good enough to trust its readings? "
           "Separates variation from the gauge (repeatability), the people "
           "(reproducibility), and the parts themselves.")

data: pd.DataFrame | None = None

if source.startswith("Demo"):
    data = g.synthetic_study()
    st.info("Demo: 10 flange samples × 3 inspectors × 3 trials with a micrometer. "
            "Synthetic data, not production records.")
    with st.expander("View demo data"):
        st.dataframe(g.long_to_wide(data), hide_index=True, use_container_width=True)

elif source.startswith("Enter"):
    a, b, c = st.columns(3)
    n_parts = a.number_input("Parts", 2, 15, 10)
    n_ops = b.number_input("Operators", 2, 6, 3)
    n_trials = c.number_input("Trials", 2, 5, 3)
    op_names = st.text_input("Operator names / initials (comma-separated)",
                             ", ".join(chr(65 + i) for i in range(n_ops)))
    names = [s.strip() for s in op_names.split(",") if s.strip()]
    if len(names) != n_ops or len(set(names)) != n_ops:
        st.error(f"Enter exactly {n_ops} unique operator names.")
        st.stop()

    key = f"grid_{n_parts}_{n_ops}_{n_trials}_{'_'.join(names)}"
    st.caption("Columns are Operator-Trial. Order matters: randomize the part "
               "order each trial so operators can't remember earlier readings.")
    wide = st.data_editor(g.blank_study(n_parts, n_ops, n_trials, names),
                          key=key, hide_index=True, use_container_width=True,
                          disabled=["Part"])
    if wide.drop(columns="Part").isna().any().any():
        st.warning("Fill every cell to run the study.")
        st.stop()
    data = g.wide_to_long(wide, n_ops, n_trials, names)

else:
    st.markdown("Upload a file with columns **Part, Operator, Trial, Measurement** "
                "(one row per reading).")
    template = g.synthetic_study().head(0)
    st.download_button("Download blank template (CSV)",
                       template.to_csv(index=False), "grr_template.csv", "text/csv")
    up = st.file_uploader("CSV or Excel", type=["csv", "xlsx"])
    if up is None:
        st.stop()
    if up.size > 5 * 1024 * 1024:
        st.error("File is larger than 5 MB.")
        st.stop()
    try:
        data = pd.read_csv(up) if up.name.lower().endswith(".csv") else pd.read_excel(up)
    except Exception as ex:  # noqa: BLE001 — show any parse problem to the user
        st.error(f"Couldn't read the file: {ex}")
        st.stop()

# ----------------------------------------------------------------------------
# Run the study
# ----------------------------------------------------------------------------
try:
    clean = g.validate(data)
    results = []
    if method in ("ANOVA", "Both (compare)"):
        results.append(g.anova(clean, k=k, tolerance=tolerance, alpha_interaction=alpha))
    if method in ("Average & Range", "Both (compare)"):
        results.append(g.average_range(clean, k=k, tolerance=tolerance))
except g.StudyError as ex:
    st.error(str(ex))
    st.stop()

main = results[0]
p, o, r = g.study_shape(clean)


def _part_key(s: str):
    """Sort '2' before '10' when part IDs are numeric; otherwise alphabetical."""
    return (0, float(s), s) if s.replace(".", "", 1).isdigit() else (1, 0.0, s)


PART_ORDER = sorted(clean["Part"].unique(), key=_part_key)

# ---- Headline ---------------------------------------------------------------
icon, kind = VERDICT_STYLE[main.verdict]
grr = main.get("Gauge R&R")
headline = (grr.pct_tol if main.tolerance else grr.pct_study)
getattr(st, kind)(f"{icon} **{main.verdict}** — Gauge R&R = **{headline:.1f}%** "
                  f"of {main.verdict_basis.lower().replace('% ', '')} "
                  f"({main.method}). AIAG: < 10% acceptable, 10–30% may be "
                  "acceptable depending on the application, > 30% unacceptable.")

m1, m2, m3, m4, m5 = st.columns(5)
m1.metric("%GRR (study var)", f"{grr.pct_study:.1f}%")
m2.metric("%GRR (tolerance)", f"{grr.pct_tol:.1f}%" if main.tolerance else "—")
m3.metric("% Contribution", f"{grr.var_contrib:.2f}%")
m4.metric("ndc", main.ndc, delta="OK" if main.ndc >= g.NDC_MIN else "< 5",
          delta_color="normal" if main.ndc >= g.NDC_MIN else "inverse")
m5.metric("Study", f"{p} × {o} × {r}", help="Parts × Operators × Trials")

for n in main.notes:
    st.markdown(f"- {n}")

# ---- Tables -----------------------------------------------------------------
tabs = st.tabs(["Results", "Charts", "ANOVA table", "Data", "How to read this"])

with tabs[0]:
    for res in results:
        st.subheader(res.method)
        fmt = {c: "{:.4f}" for c in ["StdDev (σ)", f"Study Var ({res.k:g}σ)"]}
        fmt.update({c: "{:.2f}" for c in ["% Contribution (var)", "% Study Var (%TV)",
                                          "% Tolerance"]})
        st.dataframe(res.to_frame().style.format(fmt, na_rep="—"),
                     hide_index=True, use_container_width=True)
        st.caption(f"ndc = {res.ndc} · verdict basis: {res.verdict_basis}")

# ---- Charts -----------------------------------------------------------------
with tabs[1]:
    a, b = st.columns(2)

    # Components of variation
    comp = main.to_frame()
    comp = comp[~comp["Source"].str.contains("Total")]
    comp["Source"] = comp["Source"].str.strip()
    melted = comp.melt(id_vars="Source",
                       value_vars=[c for c in ["% Contribution (var)", "% Study Var (%TV)",
                                               "% Tolerance"] if c in comp],
                       var_name="Measure", value_name="Percent")
    a.markdown("**Components of variation**")
    a.altair_chart(
        alt.Chart(melted).mark_bar().encode(
            x=alt.X("Source:N", sort=None, title=None),
            xOffset="Measure:N",
            y=alt.Y("Percent:Q", title="%"),
            color=alt.Color("Measure:N", legend=alt.Legend(orient="bottom")),
            tooltip=["Source", "Measure", alt.Tooltip("Percent:Q", format=".2f")],
        ).properties(height=320),
        use_container_width=True)

    # Measurements by part (spread across operators)
    b.markdown("**Measurements by part**")
    b.altair_chart(
        alt.Chart(clean).mark_circle(size=50, opacity=0.7).encode(
            x=alt.X("Part:N", sort=PART_ORDER),
            y=alt.Y("Measurement:Q", scale=alt.Scale(zero=False), title=units),
            color="Operator:N",
            tooltip=["Part", "Operator", "Trial", "Measurement"],
        ).properties(height=320),
        use_container_width=True)

    def control_panels(df: pd.DataFrame, value: str, limits: list[float],
                       zero: bool) -> alt.HConcatChart:
        """One small control chart per operator, with shared limit lines.
        (Built with hconcat because Altair can't facet layers that use
        different data sources.)"""
        y_scale = alt.Scale(zero=zero, domain=[
            min(df[value].min(), *limits), max(df[value].max(), *limits)])
        lim = pd.DataFrame({"y": limits})
        panels = []
        for op in sorted(df["Operator"].unique()):
            line = alt.Chart(df[df["Operator"] == op], title=f"Operator {op}").mark_line(
                point=True).encode(
                x=alt.X("Part:N", sort=PART_ORDER),
                y=alt.Y(f"{value}:Q", scale=y_scale),
                tooltip=["Part", alt.Tooltip(f"{value}:Q", format=".4f")])
            rule = alt.Chart(lim).mark_rule(strokeDash=[4, 4], color="#d62728").encode(y="y:Q")
            panels.append((line + rule).properties(width=150, height=180))
        return alt.hconcat(*panels).resolve_scale(y="shared")

    # Range chart by operator
    rd, rbar, ucl = g.range_chart_data(clean)
    st.markdown("**R chart by operator** (repeatability)")
    st.altair_chart(control_panels(rd, "Range", [rbar, ucl], zero=True))
    st.caption(f"R̄ = {rbar:.4g}, UCL = {ucl:.4g}. A point above UCL is a reading "
               "that didn't repeat, which points to a gauge or technique problem.")

    # X-bar chart by operator
    xd, grand, lcl_x, ucl_x = g.xbar_chart_data(clean)
    st.markdown("**X̄ chart by operator** (part discrimination)")
    st.altair_chart(control_panels(xd, "Average", [lcl_x, grand, ucl_x], zero=False))
    st.caption("Here you WANT most points outside the limits. That means the "
               "gauge can tell parts apart better than its own noise.")

    # Interaction plot
    st.markdown("**Operator × Part interaction**")
    inter = clean.groupby(["Part", "Operator"], as_index=False)["Measurement"].mean()
    st.altair_chart(
        alt.Chart(inter).mark_line(point=True).encode(
            x=alt.X("Part:N", sort=PART_ORDER),
            y=alt.Y("Measurement:Q", scale=alt.Scale(zero=False), title=f"Average ({units})"),
            color="Operator:N",
        ).properties(height=280),
        use_container_width=True)
    st.caption("Parallel lines = operators agree (possibly with a constant offset). "
               "Crossing lines = operators disagree on specific parts.")

# ---- ANOVA table ------------------------------------------------------------
with tabs[2]:
    an = next((x for x in results if x.anova_table is not None), None)
    if an is None:
        an = g.anova(clean, k=k, tolerance=tolerance, alpha_interaction=alpha)
    st.dataframe(an.anova_table.style.format(
        {"SS": "{:.6f}", "MS": "{:.6f}", "F": "{:.3f}", "P": "{:.4f}"}, na_rep=""),
        hide_index=True, use_container_width=True)
    st.caption("Two-way crossed ANOVA, parts and operators as random effects.")

# ---- Data -------------------------------------------------------------------
with tabs[3]:
    st.dataframe(g.long_to_wide(clean), hide_index=True, use_container_width=True)

# ---- Help -------------------------------------------------------------------
with tabs[4]:
    st.markdown("""
**Repeatability (EV)**: same person, same part, same gauge, different readings.
That's the gauge's own noise.

**Reproducibility (AV)**: different people get different averages. That's
technique, training, or how the procedure is written.

**Part-to-Part (PV)**: real differences between parts. You want this to dominate.

**%Study Variation (%TV)**: GRR as a share of total observed spread. Use for
process control and SPC.

**%Tolerance**: GRR study spread (6σ) as a share of the spec width. Use for
pass/fail inspection.

**ndc**: how many distinct groups the gauge can sort parts into. Below 5 it
can't really tell parts apart.

**Running a good study**
1. Pick ~10 parts that span the real process range, not 10 good parts.
2. Use the operators who normally take the measurement.
3. Randomize part order every trial, and hide the part IDs if you can.
4. Verify the gauge is in calibration before you start.
5. Record readings to at least one decimal place finer than the tolerance
   needs (the 10:1 rule of thumb for gauge resolution).
""")

# ----------------------------------------------------------------------------
# Report download
# ----------------------------------------------------------------------------
st.divider()
buf = io.BytesIO()
with pd.ExcelWriter(buf, engine="openpyxl") as xw:
    pd.DataFrame({
        "Field": ["Characteristic", "Gauge", "Units", "Date", "Parts", "Operators",
                  "Trials", "Tolerance", "k", "Verdict", "Basis", "ndc"],
        "Value": [characteristic, gauge, units, date.today().isoformat(), p, o, r,
                  tolerance if tolerance else "—", k, main.verdict,
                  main.verdict_basis, main.ndc],
    }).to_excel(xw, sheet_name="Summary", index=False)
    for res in results:
        res.to_frame().to_excel(xw, sheet_name=res.method[:31], index=False)
    if main.anova_table is not None:
        main.anova_table.to_excel(xw, sheet_name="ANOVA table", index=False)
    g.long_to_wide(clean).to_excel(xw, sheet_name="Data", index=False)
st.download_button("Download study report (Excel)", buf.getvalue(),
                   f"GRR_{characteristic.replace(' ', '_')}_{date.today():%Y%m%d}.xlsx",
                   "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
