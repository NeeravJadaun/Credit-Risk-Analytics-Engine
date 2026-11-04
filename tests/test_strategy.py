import numpy as np
import pandas as pd
import pytest

from credit_risk import strategy


def _book(n=1000):
    rng = np.random.default_rng(3)
    score = rng.random(n)
    return pd.DataFrame(
        {
            "loan_id": range(n),
            "score": score,
            "default_flag": (rng.random(n) < score).astype(int),
            "dti": rng.uniform(0, 40, n),
        }
    )


def test_top_share_never_exceeds_capacity():
    book = _book(1003)
    flags = strategy.top_share_flags(book["score"], book["loan_id"], 0.10)
    assert flags.sum() == 100 and flags.mean() <= 0.10


def test_policy_metrics_match_hand_calculation():
    flags = np.array([True, True, False, False])
    out = strategy.policy_metrics(flags, np.array([1, 0, 1, 0]))
    assert (
        out["default_capture"] == 0.5
        and out["flagged_share"] == 0.5
        and out["default_rate_flagged"] == 0.5
        and out["false_positives"] == 1
    )


def test_baseline_flags_nobody_and_combo_uses_both_conditions():
    book = _book()
    assert not strategy.apply_policy({"family": "Baseline"}, book).any()
    flags = strategy.apply_policy({"family": "C", "parameter": 0.5, "min_dti": 20}, book)
    assert ((book["score"] >= 0.5) & (book["dti"] >= 20)).sum() == flags.sum()


def test_selection_respects_capacity_and_excludes_baseline():
    table = pd.DataFrame(
        [
            {
                "policy": "Baseline",
                "family": "Baseline",
                "flagged_share": 0.0,
                "default_capture": 0.0,
            },
            {"policy": "over", "family": "A", "flagged_share": 0.2, "default_capture": 0.9},
            {"policy": "within", "family": "B", "flagged_share": 0.08, "default_capture": 0.3},
        ]
    )
    assert strategy.select_policy(table, 0.10)["policy"] == "within"
    with pytest.raises(ValueError):
        strategy.select_policy(table, 0.01)


def test_cost_sensitivity_ranks_within_each_scenario():
    test = pd.DataFrame(
        {"policy": ["a", "b"], "false_negatives": [10, 5], "false_positives": [0, 20]}
    )
    out = strategy.cost_sensitivity(
        test,
        [
            {"name": "x", "false_negative": 1, "false_positive": 1},
            {"name": "y", "false_negative": 10, "false_positive": 1},
        ],
    )
    assert out[(out.cost_scenario == "x") & (out.rank_in_scenario == 1)]["policy"].iloc[0] == "a"
    assert out[(out.cost_scenario == "y") & (out.rank_in_scenario == 1)]["policy"].iloc[0] == "b"
