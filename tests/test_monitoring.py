import numpy as np
import pandas as pd
import pytest

from credit_risk import monitoring

RULE = {"warn": 0.1, "critical": 0.25}


def test_alert_and_no_alert():
    assert monitoring.classify(0.05, RULE) == "OK"
    assert monitoring.classify(0.12, RULE) == "WARN"
    assert monitoring.classify(0.30, RULE) == "CRITICAL"
    assert monitoring.classify(float("nan"), RULE) == "NOT_APPLICABLE"


def test_psi_is_zero_for_same_distribution_and_large_for_a_shift():
    rng = np.random.default_rng(1)
    base = rng.normal(size=50_000)
    assert monitoring.psi(base, base) == pytest.approx(0.0, abs=1e-9)
    assert monitoring.psi(base, base + 2) > 0.25


def test_decomposition_adds_up_and_identifies_the_cause():
    def group(n_high, n_low, rate_high, rate_low):
        rows = []
        for grade, n, rate in (("high", n_high, rate_high), ("low", n_low, rate_low)):
            hits = int(round(n * rate))
            rows += [{"grade": grade, "default_flag": int(i < hits)} for i in range(n)]
        return pd.DataFrame(rows)

    base = group(200, 800, 0.40, 0.10)
    mix = monitoring.mix_rate_decomposition(group(500, 500, 0.40, 0.10), base, "grade")
    assert mix["mix_effect"] + mix["rate_effect"] == pytest.approx(mix["total_gap"])
    assert "mix" in monitoring.interpret(mix["total_gap"], mix["mix_effect"])[1].lower()
    rate = monitoring.mix_rate_decomposition(group(200, 800, 0.60, 0.20), base, "grade")
    assert "within-grade" in monitoring.interpret(rate["total_gap"], rate["mix_effect"])[1]


def test_simulated_results_are_always_labelled():
    assert monitoring.SIMULATION_BANNER == "Simulation — not observed portfolio history."
    n = 400
    frame = pd.DataFrame(
        {
            "default_flag": [0, 1] * (n // 2),
            "score": np.linspace(0.05, 0.6, n),
            "grade": ["A", "D"] * (n // 2),
            "dti": 10.0,
        }
    )
    batch = monitoring.SimBatch("SIM-01", "2026-01-31", "clean_partition", frame, n)
    rules = [
        {
            "name": "field_null_share",
            "metric": "max_field_null_share",
            "warn": 0.01,
            "critical": 0.05,
            "triage": "t",
        }
    ]
    out = monitoring.monitor_batches([batch], frame, 0.5, rules, ["dti"])
    assert (
        out["simulation"].all()
        and (out["provenance"] == monitoring.SIMULATION_BANNER).all()
        and out["severity"].iloc[0] == "OK"
    )


def test_injected_null_spike_triggers_alert():
    frame = pd.DataFrame(
        {
            "default_flag": [0, 1] * 100,
            "score": np.linspace(0.05, 0.6, 200),
            "dti": [np.nan] * 20 + [10.0] * 180,
        }
    )
    batch = monitoring.SimBatch("SIM-10", "2026-10-31", "injected_null_spike", frame, 200)
    rules = [
        {
            "name": "field_null_share",
            "metric": "max_field_null_share",
            "warn": 0.01,
            "critical": 0.05,
            "triage": "t",
        }
    ]
    assert (
        monitoring.monitor_batches([batch], frame, 0.5, rules, ["dti"])["severity"].iloc[0]
        == "CRITICAL"
    )
