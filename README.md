# 📏 Gauge R&R / MSA Studio

An interactive **Measurement System Analysis** app built with Python and Streamlit. It answers a basic quality question: **can we trust the numbers our gauges give us?**

A Gauge R&R study splits the observed variation into three sources:

| Source | Question it answers |
|---|---|
| **Repeatability (EV)** | Does the *gauge* give the same reading twice? |
| **Reproducibility (AV)** | Do different *inspectors* get the same answer? |
| **Part-to-Part (PV)** | How much do the *parts* really differ? |

If the gauge and inspectors contribute too much of the total variation, accept/reject decisions and SPC charts become unreliable.

## Features

- **Two methods**: ANOVA, and Average & Range (X̄/R), per the AIAG MSA Reference Manual, 4th ed. You can also compare them side by side.
- **Acceptance metrics**: %Study Variation, %Tolerance, %Contribution, and number of distinct categories (ndc), with an automatic verdict (Acceptable / Marginal / Unacceptable).
- **Charts**: components of variation, measurements by part, R and X̄ control charts by operator, and an Operator × Part interaction plot.
- **ANOVA table**: automatic pooling of a non-significant interaction (α selectable).
- **Three input modes**: demo data, a grid laid out like the paper form, or a CSV/Excel upload.
- **Diagnostics in plain language**: tells you whether the problem is the gauge or the people, and flags out-of-control ranges.
- **Formatted Excel report**: summary sheet with the verdict, AIAG guidance, findings, and prepared/reviewed signoff lines, plus variance components, the ANOVA table, and raw data.

## Verified against published results

The calculation engine (`grr_core.py`) is unit-tested against the 10-part, 3-appraiser, 3-trial worked example in the AIAG MSA manual:

| Metric | Published | This app |
|---|---|---|
| EV (Avg & Range) | 0.20188 | 0.2019 |
| AV (Avg & Range) | 0.22963 | 0.2297 |
| %GRR (Avg & Range) | 26.68% | 26.68% |
| ndc (Avg & Range) | 5 | 5 |
| %Contribution GRR (ANOVA) | 7.76% | 7.76% |
| %Study Var GRR (ANOVA) | 27.86% | 27.86% |

```bash
python -m unittest discover tests -v     # 30 tests
```

## Run locally

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

## Data format

One row per reading. The study must be balanced: every operator measures every part the same number of times.

```
Part,Operator,Trial,Measurement
1,A,1,1.2507
1,A,2,1.2506
...
```

A sample file is in `sample_data/`. AIAG recommends 10 parts that span the real process range, 3 operators, and 2–3 trials, with the part order randomized on every trial.

## Acceptance criteria (AIAG)

| %GRR | Decision |
|---|---|
| < 10% | Acceptable |
| 10–30% | May be acceptable, depending on the application, gauge cost, and repair cost |
| > 30% | Unacceptable: improve the measurement system |

ndc should be **≥ 5**.

## Project structure

```
app.py               Streamlit UI (input, charts, report)
grr_core.py          Calculation engine, no UI code
report.py            Formatted Excel report (summary, verdict, signoff lines)
tests/               Unit tests against the AIAG reference example
sample_data/         Synthetic demo study
```

## Data notice

The demo data is **synthetic** (a simulated transformer tank-flange thickness study). It is not real production data. Don't upload proprietary or customer measurement data to a public deployment.

---

Built by **Marcus Faulkner**, Quality Inspector | [Portfolio](https://marusfaulkner.github.io) | [LinkedIn](https://linkedin.com/in/marcus-f-0a622b70)
