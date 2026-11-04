"""Tableau-ready extracts with provenance columns, a manifest and reconciliation to the warehouse.

This writes data only. No Tableau workbook exists in this repository: Tableau is not installed on the
build machine. docs/tableau_build_steps.md explains how to build the dashboards from these files.
"""

from __future__ import annotations

import pandas as pd

from .audit import sha256_file, write_json
from .findings import wilson_interval

REAL = "Real: public LendingClub data"


def export(settings) -> dict:
    gold, out = settings.path("gold"), settings.path("tableau")
    scored = pd.read_parquet(gold / "scored_cohort.parquet")
    extracts: dict[str, pd.DataFrame] = {}
    cube = (
        scored.groupby(
            [
                "issue_year",
                "issue_quarter",
                "term_months",
                "split",
                "grade",
                "fico_band",
                "dti_band",
                "utilization_band",
                "income_band",
                "home_ownership",
            ],
            observed=True,
        )
        .agg(
            loans=("default_flag", "size"),
            defaults=("default_flag", "sum"),
            loan_amount=("loan_amnt", "sum"),
            score_sum=("score", "sum"),
        )
        .reset_index()
    )
    extracts["portfolio_cube"] = cube.assign(provenance=REAL)
    low, high = wilson_interval(
        pd.read_csv(gold / "sql_vintage_default_rates.csv")["defaults"],
        pd.read_csv(gold / "sql_vintage_default_rates.csv")["loans"],
    )
    extracts["vintage_trends"] = pd.read_csv(gold / "sql_vintage_default_rates.csv").assign(
        ci95_low=low, ci95_high=high, provenance=REAL
    )
    extracts["delinquency_snapshot"] = pd.read_csv(gold / "sql_delinquency_by_vintage.csv").assign(
        provenance=REAL + ", one snapshot at the data cut"
    )
    extracts["kpi_indicators"] = pd.read_csv(gold / "kpi_indicators.csv").assign(provenance=REAL)
    extracts["risk_findings"] = pd.read_csv(gold / "risk_findings.csv").assign(provenance=REAL)
    extracts["model_metrics"] = pd.read_csv(gold / "model_metrics.csv").assign(provenance=REAL)
    extracts["model_calibration"] = pd.read_csv(gold / "model_calibration.csv").assign(
        provenance=REAL + ", 2015 vintage"
    )
    extracts["model_score_deciles"] = pd.read_csv(gold / "sql_model_score_deciles.csv").assign(
        provenance=REAL + ", 2015 vintage"
    )
    extracts["model_segment_performance"] = pd.read_csv(
        gold / "model_segment_performance.csv"
    ).assign(provenance=REAL + ", 2015 vintage")
    extracts["strategy_comparison"] = pd.read_csv(gold / "strategy_comparison.csv").assign(
        provenance="Hypothetical policy on real 2015 outcomes. Retrospective, not causal."
    )
    real = (
        pd.read_csv(gold / "monitoring_real_quarters.csv")
        .drop(columns=["triage"])
        .assign(evidence="Real")
    )
    sim = (
        pd.read_csv(gold / "monitoring_simulated_batches.csv")
        .drop(columns=["triage"])
        .assign(evidence="Simulation")
    )
    extracts["monitoring_results"] = pd.concat([real, sim], ignore_index=True)
    extracts["data_quality_checks"] = pd.read_csv(gold / "sql_quality_summary.csv").assign(
        provenance=REAL
    )
    rows = []
    for name, frame in extracts.items():
        path = out / f"{name}.csv"
        frame.to_csv(path, index=False)
        rows.append(
            {
                "file": path.name,
                "rows": len(frame),
                "columns": frame.shape[1],
                "sha256": sha256_file(path),
            }
        )
    pd.DataFrame(rows).to_csv(out / "extract_manifest.csv", index=False)
    result = {
        "workbook_exists": False,
        "extracts": rows,
        "cube_loans": int(cube["loans"].sum()),
        "cube_defaults": int(cube["defaults"].sum()),
        "cube_matches_cohort": int(cube["loans"].sum()) == len(scored)
        and int(cube["defaults"].sum()) == int(scored["default_flag"].sum()),
    }
    write_json(settings.path("results") / "tableau_extracts.json", result)
    return result
