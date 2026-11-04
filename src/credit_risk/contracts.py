"""Pandas implementation of the data contract, written separately from the Spark version.

If both engines report the same count for every rule, a bug would have to exist in two different
implementations to go unnoticed. This module does not reuse the Spark SQL predicates.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


class ContractError(RuntimeError):
    """A structural failure that stops the pipeline. No report may be published."""


def read_source(path: Path, required: list[str]) -> pd.DataFrame:
    header = pd.read_csv(path, nrows=0).columns
    missing = [c for c in required if c not in header]
    if missing:
        raise ContractError(f"Source is missing expected columns: {missing}")
    return pd.read_csv(path, usecols=required, dtype=str, low_memory=False, keep_default_na=True)


def _month(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, format="%b-%Y", errors="coerce")


def _emp_years(series: pd.Series) -> pd.Series:
    out = series.str.extract(r"^(\d+) years?$")[0].astype(float)
    out = out.where(series != "10+ years", 10.0)
    return out.where(series != "< 1 year", 0.5)


def to_typed(raw: pd.DataFrame) -> pd.DataFrame:
    frame = pd.DataFrame(index=raw.index)
    is_numeric_id = raw["id"].astype(str).str.fullmatch(r"\d+")
    frame["loan_id"] = pd.to_numeric(raw["id"].where(is_numeric_id), errors="coerce")
    frame["loan_status"] = raw["loan_status"].str.strip()
    frame["issue_date"] = _month(raw["issue_d"])
    frame["earliest_cr_date"] = _month(raw["earliest_cr_line"])
    frame["term_months"] = pd.to_numeric(raw["term"].str.extract(r"(\d+)")[0], errors="coerce")
    for col in [
        "int_rate",
        "revol_util",
        "loan_amnt",
        "funded_amnt",
        "installment",
        "annual_inc",
        "dti",
        "fico_range_low",
        "fico_range_high",
        "delinq_2yrs",
        "inq_last_6mths",
        "open_acc",
        "pub_rec",
        "revol_bal",
        "total_acc",
        "mort_acc",
        "pub_rec_bankruptcies",
    ]:
        frame[col] = pd.to_numeric(raw[col], errors="coerce")
    for col in [
        "grade",
        "sub_grade",
        "home_ownership",
        "verification_status",
        "purpose",
        "application_type",
    ]:
        frame[col] = raw[col].str.strip()
    frame["emp_years"] = _emp_years(raw["emp_length"])
    return frame


def rule_masks(t: pd.DataFrame) -> dict[str, pd.Series]:
    """Each mask is True on violation. Comparisons with NaN are False, matching SQL null logic."""
    is_loan = t["loan_id"].notna()
    fico_low, fico_high = t["fico_range_low"], t["fico_range_high"]
    grade_letter = t["sub_grade"].str[:1]
    return {
        "Q01_non_loan_row": ~is_loan,
        "Q02_duplicate_id": is_loan & t["loan_id"].duplicated(keep="first"),
        "Q03_missing_status": is_loan & t["loan_status"].isna(),
        "Q04_invalid_issue_date": is_loan & t["issue_date"].isna(),
        "Q05_invalid_loan_amount": is_loan & ~t["loan_amnt"].between(0, 40000, inclusive="right"),
        "Q06_funded_above_requested": is_loan
        & (t["funded_amnt"].isna() | (t["funded_amnt"] <= 0) | (t["funded_amnt"] > t["loan_amnt"])),
        "Q07_invalid_term": is_loan & ~t["term_months"].isin([36, 60]),
        "Q08_invalid_interest_rate": is_loan & ~t["int_rate"].between(0, 40, inclusive="right"),
        "Q09_installment_below_principal": is_loan
        & (
            t["installment"].isna()
            | (t["installment"] <= 0)
            | (t["installment"] * t["term_months"] < t["funded_amnt"] * 0.99)
        ),
        "Q10_invalid_grade": is_loan & ~t["grade"].isin(list("ABCDEFG")),
        "Q11_subgrade_grade_mismatch": is_loan
        & (t["sub_grade"].isna() | (grade_letter != t["grade"])),
        "Q12_invalid_annual_income": is_loan & (t["annual_inc"].isna() | (t["annual_inc"] <= 0)),
        "Q13_invalid_dti": is_loan & ((t["dti"] < 0) | (t["dti"] > 100)),
        "Q14_invalid_fico": is_loan
        & (
            fico_low.isna()
            | fico_high.isna()
            | (fico_low > fico_high)
            | (fico_low < 300)
            | (fico_high > 850)
        ),
        "Q15_invalid_revolving": is_loan
        & (t["revol_bal"].isna() | (t["revol_bal"] < 0) | (t["revol_util"] < 0)),
        "Q16_negative_credit_counts": is_loan
        & (
            (t["delinq_2yrs"] < 0)
            | (t["pub_rec"] < 0)
            | (t["inq_last_6mths"] < 0)
            | (t["open_acc"] < 0)
            | (t["total_acc"] < 0)
        ),
        "Q17_open_above_total_accounts": is_loan & (t["open_acc"] > t["total_acc"]),
        "Q18_invalid_credit_history_date": is_loan
        & (t["earliest_cr_date"].isna() | (t["earliest_cr_date"] > t["issue_date"])),
    }


def warning_masks(t: pd.DataFrame) -> dict[str, pd.Series]:
    return {
        "W01_missing_employment_length": t["emp_years"].isna(),
        "W02_missing_dti": t["dti"].isna(),
        "W03_missing_revol_util": t["revol_util"].isna(),
        "W04_missing_mort_acc": t["mort_acc"].isna(),
        "W05_utilization_above_100": t["revol_util"] > 100,
        "W06_income_above_1m": t["annual_inc"] > 1_000_000,
        "W07_joint_application": t["application_type"] == "Joint App",
    }


def validate(
    raw: pd.DataFrame, contract: dict[str, Any]
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, int]]:
    """Return accepted rows, quarantined rows with reason codes, and every count."""
    typed = to_typed(raw)
    masks = rule_masks(typed)
    expected = {r["id"] for r in contract["quarantine_rules"]}
    if set(masks) != expected:
        raise ContractError(f"Pandas rules differ from the contract: {set(masks) ^ expected}")
    matrix = (
        np.column_stack([masks[r["id"]].to_numpy() for r in contract["quarantine_rules"]])
        if len(typed)
        else None
    )
    is_bad = matrix.any(axis=1) if matrix is not None else np.zeros(0, dtype=bool)
    reasons = [""] * len(typed)
    ids = [r["id"] for r in contract["quarantine_rules"]]
    for position in np.flatnonzero(is_bad):
        reasons[position] = "|".join(i for i, hit in zip(ids, matrix[position], strict=True) if hit)
    quarantined = typed.loc[is_bad].assign(
        reason_codes=[reasons[p] for p in np.flatnonzero(is_bad)]
    )
    accepted = typed.loc[~is_bad].copy()
    counts = {
        "input_rows": len(typed),
        "quarantined_rows": int(is_bad.sum()),
        "distinct_loan_ids": int(typed["loan_id"].nunique()),
    }
    counts.update({k: int(v.sum()) for k, v in masks.items()})
    warnings = warning_masks(accepted)
    counts.update({k: int(v.sum()) for k, v in warnings.items()})
    for name, statuses in contract["status_groups"].items():
        counts[f"status_{name}"] = int(accepted["loan_status"].isin(statuses).sum())
    return accepted, quarantined, counts


def reconciliation_checks(
    counts: dict[str, int], max_quarantine_share: float, expected_rows: int | None = None
) -> list[dict[str, Any]]:
    """Layer-level controls. Any FAIL is a hard stop for publishing."""
    n, bad = counts["input_rows"], counts["quarantined_rows"]
    accepted = n - bad
    statuses = sum(v for k, v in counts.items() if k.startswith("status_"))
    checks = [
        (
            "rows_partition_into_accepted_and_quarantined",
            accepted + bad == n,
            f"{accepted} + {bad}",
            n,
        ),
        (
            "quarantine_share_within_limit",
            (bad / n if n else 1) <= max_quarantine_share,
            f"{bad / n if n else 1:.6f}",
            f"<= {max_quarantine_share}",
        ),
        ("every_accepted_row_has_known_status", statuses == accepted, statuses, accepted),
        (
            "loan_ids_unique_among_loans",
            counts["distinct_loan_ids"] == n - counts["Q01_non_loan_row"],
            counts["distinct_loan_ids"],
            n - counts["Q01_non_loan_row"],
        ),
    ]
    if expected_rows is not None:
        checks.append(("row_count_matches_control_total", n == expected_rows, n, expected_rows))
    return [
        {"check_name": a, "status": "PASS" if b else "FAIL", "observed": str(c), "expected": str(d)}
        for a, b, c, d in checks
    ]
