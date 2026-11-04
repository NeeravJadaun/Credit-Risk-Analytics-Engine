from __future__ import annotations

import pandas as pd
import pytest

from credit_risk.config import load_settings


@pytest.fixture(scope="session")
def settings():
    return load_settings()


def good_row(loan_id: int = 1, **overrides) -> dict:
    row = {
        "id": str(loan_id),
        "member_id": None,
        "loan_amnt": "10000",
        "funded_amnt": "10000",
        "term": " 36 months",
        "int_rate": "12.5",
        "installment": "334.5",
        "grade": "B",
        "sub_grade": "B3",
        "emp_length": "5 years",
        "home_ownership": "RENT",
        "annual_inc": "60000",
        "verification_status": "Verified",
        "issue_d": "Mar-2015",
        "loan_status": "Fully Paid",
        "purpose": "credit_card",
        "addr_state": "CA",
        "dti": "18.5",
        "delinq_2yrs": "0",
        "earliest_cr_line": "Jan-2005",
        "fico_range_low": "700",
        "fico_range_high": "704",
        "inq_last_6mths": "1",
        "open_acc": "8",
        "pub_rec": "0",
        "revol_bal": "5000",
        "revol_util": "40.0",
        "total_acc": "20",
        "application_type": "Individual",
        "mort_acc": "1",
        "pub_rec_bankruptcies": "0",
    }
    row.update(overrides)
    return row


def frame(*rows: dict) -> pd.DataFrame:
    return pd.DataFrame(list(rows), dtype=object)


# One row per rule, each violating that rule only. Shared by the pandas and Spark tests.
DIRTY_CASES = {
    "Q01_non_loan_row": {"id": "Total amount funded in policy code 1: 123"},
    "Q03_missing_status": {"loan_status": None},
    "Q04_invalid_issue_date": {"issue_d": "not-a-date"},
    "Q05_invalid_loan_amount": {"loan_amnt": "-5", "funded_amnt": "-5"},
    "Q06_funded_above_requested": {"funded_amnt": "20000"},
    "Q07_invalid_term": {"term": " 48 months"},
    "Q08_invalid_interest_rate": {"int_rate": "99"},
    "Q09_installment_below_principal": {"installment": "10"},
    "Q10_invalid_grade": {"grade": "Z", "sub_grade": "Z3"},
    "Q11_subgrade_grade_mismatch": {"sub_grade": "C3"},
    "Q12_invalid_annual_income": {"annual_inc": "0"},
    "Q13_invalid_dti": {"dti": "999"},
    "Q14_invalid_fico": {"fico_range_low": "710", "fico_range_high": "704"},
    "Q15_invalid_revolving": {"revol_bal": "-1"},
    "Q16_negative_credit_counts": {"delinq_2yrs": "-1"},
    "Q17_open_above_total_accounts": {"open_acc": "30"},
    "Q18_invalid_credit_history_date": {"earliest_cr_line": "Jan-2020"},
}


def dirty_frame() -> pd.DataFrame:
    rows = [good_row(i + 1) for i in range(5)]
    for offset, overrides in enumerate(DIRTY_CASES.values()):
        rows.append(good_row(100 + offset, **overrides))
    rows.append(good_row(1))  # exact duplicate of loan 1
    return frame(*rows)
