"""Checks published outputs against the underlying data. Needs a completed pipeline run."""

import json
import zipfile
from pathlib import Path

import openpyxl
import pandas as pd
import pytest
from sklearn.metrics import roc_auc_score

from credit_risk import reporting, results
from credit_risk.config import ROOT

pytestmark = pytest.mark.integration
WORKBOOK = ROOT / "reports" / "excel" / "credit_risk_portfolio_report.xlsx"


def _require(path: Path) -> Path:
    if not path.exists():
        pytest.skip(f"{path.name} not generated. Run the pipeline first.")
    return path


def _json(name):
    return json.loads(_require(ROOT / "results" / f"{name}.json").read_text())


def test_auc_recalculated_from_scored_loans_matches_report():
    scored = pd.read_parquet(
        _require(ROOT / "data" / "gold" / "scored_cohort.parquet"),
        columns=["split", "default_flag", "score"],
    )
    test = scored[scored["split"] == "test"]
    assert roc_auc_score(test["default_flag"], test["score"]) == pytest.approx(
        _json("model")["test_metrics"]["roc_auc"], abs=1e-12
    )


def test_cohort_is_unique_real_loans_not_duplicated():
    scored = pd.read_parquet(
        _require(ROOT / "data" / "gold" / "scored_cohort.parquet"), columns=["loan_id"]
    )
    spark = _json("spark_etl")
    assert scored["loan_id"].is_unique and len(scored) == spark["matured_cohort_loans"]
    assert (
        spark["counts"]["distinct_loan_ids"]
        == spark["counts"]["input_rows"] - spark["counts"]["Q01_non_loan_row"]
    )
    assert spark["counts"]["Q02_duplicate_id"] == 0


def test_test_vintage_is_later_than_training():
    dates = _json("model")["split_issue_dates"]
    assert dates["train"][1] < dates["validation"][0] <= dates["validation"][1] < dates["test"][0]


def test_finding_recalculated_from_loans():
    scored = pd.read_parquet(_require(ROOT / "data" / "gold" / "scored_cohort.parquet"))
    test = scored[scored["split"] == "test"]
    segment = test[(test["fico_mid"] < 680) & (test["dti"] >= 20) & (test["revol_util"] >= 60)]
    comparison = test[(test["fico_mid"] >= 720) & (test["dti"] < 15) & (test["revol_util"] < 40)]
    ratio = segment["default_flag"].mean() / comparison["default_flag"].mean()
    strongest = _json("findings")["strongest_by_prespecified_rule"]
    assert (
        ratio == pytest.approx(strongest["oot_relative_risk"])
        and len(segment) == strongest["oot_segment_loans"]
    )


def test_r_reconciliation_all_within_tolerance():
    table = pd.read_csv(_require(ROOT / "data" / "gold" / "reconciliation_r_python.csv"))
    assert len(table) >= 40 and (table["status"] == "PASS").all()


def test_workbook_has_formulas_pivot_and_no_errors():
    with zipfile.ZipFile(_require(WORKBOOK)) as archive:
        names = archive.namelist()
    assert any(n.startswith("xl/pivotTables/pivotTable") for n in names) and any(
        n.startswith("xl/charts/") for n in names
    )
    values = openpyxl.load_workbook(WORKBOOK, data_only=True)
    formulas = openpyxl.load_workbook(WORKBOOK)
    assert {
        "Executive Summary",
        "Portfolio Segments",
        "Strategy What-If",
        "Data Quality",
        "Definitions",
        "Raw Export",
    } <= set(values.sheetnames)
    assert str(formulas["Executive Summary"]["B8"].value).startswith("=SUM")
    for ws in values.worksheets:
        for row in ws.iter_rows(values_only=True):
            assert not any(isinstance(v, str) and v.startswith("#") for v in row)
    assert all(values["Executive Summary"][f"D{r}"].value == "MATCH" for r in range(8, 16))


def test_no_tableau_workbook_is_claimed():
    assert _json("tableau_extracts")["workbook_exists"] is False
    assert not list((ROOT / "dashboards" / "tableau").glob("*.twb*"))
    assert _json("tableau_extracts")["cube_matches_cohort"] is True


def test_tableau_extracts_label_simulation():
    table = pd.read_csv(_require(ROOT / "data" / "gold" / "tableau" / "monitoring_results.csv"))
    assert (
        table.loc[table["evidence"] == "Simulation", "provenance"].str.contains(
            "not observed portfolio history"
        )
    ).all()
    assert table.loc[table["evidence"] == "Real", "simulation"].eq(False).all()


def test_report_is_not_published_when_a_check_failed(settings, tmp_path, monkeypatch):
    original = reporting.load_facts

    def broken(s):
        facts = original(s)
        facts["pandas_etl"]["layer_checks"][0]["status"] = "FAIL"
        return facts

    _require(ROOT / "results" / "warehouse.json")
    monkeypatch.setattr(reporting, "load_facts", broken)
    with pytest.raises(RuntimeError, match="not published"):
        reporting.publish(settings, tmp_path, "2026-11")
    assert json.loads((tmp_path / "LATEST_STATUS.json").read_text())["status"] == "FAILED"
    assert not (tmp_path / "portfolio_report.html").exists()


def test_generated_documents_use_measured_numbers_and_avoid_false_claims(settings):
    for name in ("pipeline_run", "benchmarks", "excel_verification"):
        _json(name)
    text = _require(ROOT / "PROJECT_RESULTS.md").read_text()
    evidence = _require(ROOT / "RESUME_EVIDENCE.md").read_text()
    auc = _json("model")["test_metrics"]["roc_auc"]
    assert f"{auc:.4f}" in text and "Not achieved" in text
    context = results.collect(settings)
    for bullet in results.bullets(context):
        assert bullet in evidence
        assert "RBC" not in bullet and "reduced default" not in bullet.lower()
