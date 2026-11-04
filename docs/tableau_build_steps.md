# Tableau build steps

**Status: not built.** Tableau is not available on the build machine. Extracts in `data/gold/tableau/` are validated (`results/tableau_extracts.json`). Tableau Public is free and is enough for this. Regenerate the extracts with `python -m credit_risk.cli tableau`.

## Connect

1. Open Tableau Public or Desktop and connect to the text files in `data/gold/tableau/`.
2. Check counts first: `SUM(loans)` on `portfolio_cube.csv` must equal the matured cohort in `PROJECT_RESULTS.md`.
3. Use a calculated field for rates, so filters recalculate them correctly: `SUM([defaults]) / SUM([loans])`. Do not average a rate column.

## Dashboard 1: Credit Risk Overview (`portfolio_cube`, `kpi_indicators`)

- KPI tiles: loans, defaults, default rate, K12 quarantine rate.
- Bars: default rate by grade, with loan count in the tooltip.
- Line: default rate by `issue_year`, with `term_months` as color.
- Filters: `grade`, `fico_band`, `term_months`, `issue_year`. Caption: "Public LendingClub loans, matured vintages. Independent educational project."

## Dashboard 2: Risk Drivers (`portfolio_cube`, `risk_findings`)

- Heatmap: `fico_band` by `dti_band`, color by the rate field, with counts as labels.
- Dot plot: `oot_relative_risk` by `name`, with error bars from `oot_rr_ci95_low` and `oot_rr_ci95_high`.
- Mark cells with fewer than 500 loans using a calculated "small sample" field.
- Tooltip: rule, both rates and both sample sizes. Note: "Association, not causation."

## Dashboard 3: Model and Strategy Monitoring (`model_metrics`, `model_calibration`, `model_score_deciles`, `strategy_comparison`, `monitoring_results`)

- Table: metrics by model for the test split.
- Line: `mean_predicted` and `observed_rate` by `score_decile`.
- Bars: `flagged_share` and `default_capture` by `policy`, captioned "Hypothetical and retrospective."
- Alert table from `monitoring_results`, filtered and split by `evidence`. Show `provenance` on every simulated view, and add the title "Simulation — not observed portfolio history."

## Dashboard 4: Data Quality Operations (`data_quality_checks`, plus the "Data Quality" sheet or `dq_layer_checks.csv`)

- KPIs: input, accepted and quarantined rows.
- Bars: rows affected per check. Table: layer-control status.
- Trend: `delinquency_snapshot` by `issue_year`, captioned "One snapshot at the data cut, not a roll rate."

## Finish

Save as `dashboards/tableau/credit_risk.twbx`, publish to your Tableau Public profile if you want, and put genuine screenshots in `dashboards/screenshots/`. Then update `tableau.py`'s `workbook_exists`, the status docs, and the resume header. Until then, do not list Tableau as a completed dashboard.
