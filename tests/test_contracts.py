import pandas as pd
import pytest

from conftest import DIRTY_CASES, dirty_frame, frame, good_row
from credit_risk.contracts import ContractError, read_source, reconciliation_checks, validate


def test_clean_rows_all_accepted(settings):
    accepted, quarantined, counts = validate(frame(good_row(1), good_row(2)), settings.contract)
    assert len(accepted) == 2 and quarantined.empty and counts["quarantined_rows"] == 0


@pytest.mark.parametrize("rule,overrides", DIRTY_CASES.items())
def test_each_rule_catches_its_injected_fault(settings, rule, overrides):
    _, quarantined, counts = validate(
        frame(good_row(1), good_row(2, **overrides)), settings.contract
    )
    assert (
        counts[rule] == 1 and len(quarantined) == 1 and rule in quarantined.iloc[0]["reason_codes"]
    )


def test_duplicate_id_keeps_first_only(settings):
    accepted, quarantined, counts = validate(frame(good_row(7), good_row(7)), settings.contract)
    assert counts["Q02_duplicate_id"] == 1 and len(accepted) == 1 and len(quarantined) == 1


def test_non_numeric_amount_is_quarantined(settings):
    _, _, counts = validate(frame(good_row(1, loan_amnt="abc")), settings.contract)
    assert counts["Q05_invalid_loan_amount"] == 1


def test_null_dti_is_kept_and_flagged_not_removed(settings):
    accepted, _, counts = validate(frame(good_row(1, dti=None)), settings.contract)
    assert len(accepted) == 1 and counts["W02_missing_dti"] == 1


def test_missing_column_raises(settings, tmp_path):
    path = tmp_path / "bad.csv"
    frame(good_row(1)).drop(columns=["loan_status"]).to_csv(path, index=False)
    with pytest.raises(ContractError, match="loan_status"):
        read_source(path, settings.contract["required_columns"])


def test_partition_reconciles_on_dirty_data(settings):
    accepted, quarantined, counts = validate(dirty_frame(), settings.contract)
    assert len(accepted) + len(quarantined) == counts["input_rows"]


def test_reconciliation_fails_on_row_count_and_excess_quarantine(settings):
    _, _, counts = validate(dirty_frame(), settings.contract)
    checks = {
        c["check_name"]: c["status"] for c in reconciliation_checks(counts, 0.02, expected_rows=999)
    }
    assert checks["row_count_matches_control_total"] == "FAIL"
    assert checks["quarantine_share_within_limit"] == "FAIL"


def test_rule_ids_match_contract(settings):
    assert len(settings.contract["quarantine_rules"]) == 18
    _, _, counts = validate(frame(good_row(1)), settings.contract)
    assert all(rule["id"] in counts for rule in settings.contract["quarantine_rules"])
    assert isinstance(counts["input_rows"], int) and isinstance(pd.DataFrame(), pd.DataFrame)
