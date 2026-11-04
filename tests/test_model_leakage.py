import numpy as np
import pandas as pd
import pytest

from credit_risk import modeling


def test_configured_features_pass_the_leakage_check(settings):
    numeric, categorical = modeling.feature_lists(settings.features)
    modeling.assert_no_leakage(numeric + categorical, settings.features)


@pytest.mark.parametrize(
    "column",
    [
        "total_pymnt",
        "recoveries",
        "last_fico_range_high",
        "loan_status",
        "out_prncp",
        "settlement_amount",
    ],
)
def test_post_origination_fields_are_rejected(settings, column):
    with pytest.raises(modeling.LeakageError):
        modeling.assert_no_leakage(["fico_mid", column], settings.features)


@pytest.mark.parametrize(
    "column", ["default_flag", "zip_code", "addr_state", "emp_title", "loan_id", "score", "split"]
)
def test_outcome_identifier_and_proxy_fields_are_rejected(settings, column):
    with pytest.raises(modeling.LeakageError):
        modeling.assert_no_leakage([column], settings.features)


def test_no_listed_feature_is_also_barred(settings):
    policy = settings.features
    barred = (
        set(policy["post_origination_columns"])
        | set(policy["excluded_by_design"])
        | set(policy["identifier_and_outcome_columns"])
    )
    assert not barred & set(policy["numeric_features"] + policy["categorical_features"])


def _split_frame(train_max="2013-12-01", valid="2014-06-01"):
    return pd.DataFrame(
        {
            "loan_id": [1, 2, 3],
            "split": ["train", "validation", "test"],
            "issue_date": pd.to_datetime([train_max, valid, "2015-06-01"]),
        }
    )


def test_time_order_passes_then_fails_on_overlap():
    modeling.assert_time_ordered(_split_frame())
    with pytest.raises(modeling.LeakageError):
        modeling.assert_time_ordered(_split_frame(train_max="2014-08-01"))


def test_duplicate_loan_across_splits_is_rejected():
    frame = _split_frame()
    frame.loc[2, "loan_id"] = 1
    with pytest.raises(modeling.LeakageError):
        modeling.assert_time_ordered(frame)


def test_evaluate_matches_hand_calculation():
    y = np.array([1, 1, 0, 0, 0, 1])
    score = np.array([0.9, 0.2, 0.8, 0.1, 0.3, 0.7])
    out = modeling.evaluate(y, score, 0.5)
    assert (out["tp"], out["fp"], out["tn"], out["fn"]) == (2, 1, 2, 1)
    assert (
        out["precision"] == pytest.approx(2 / 3)
        and out["recall"] == pytest.approx(2 / 3)
        and out["f1"] == pytest.approx(2 / 3)
    )


def test_saved_model_rejects_incompatible_schema(tmp_path):
    from sklearn.dummy import DummyClassifier

    path = tmp_path / "model.joblib"
    modeling.save_bundle(path, DummyClassifier(), ["a", "b"], {"model": "dummy"})
    assert modeling.load_bundle(path, pd.DataFrame(columns=["a", "b"]))["model"] == "dummy"
    with pytest.raises(modeling.ModelSchemaError):
        modeling.load_bundle(path, pd.DataFrame(columns=["a"]))
