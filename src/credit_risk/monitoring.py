"""Monitoring in two strictly separate modes.

Mode A compares real later vintages with a real reference vintage. Mode B partitions held-out loans
into synthetic ingestion batches and injects controlled faults, to test alerts and the investigation
workflow. Mode B is never portfolio history.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

TARGET = "default_flag"
SIMULATION_BANNER = "Simulation — not observed portfolio history."
PSI_EPSILON = 1e-4


@dataclass
class SimBatch:
    batch_id: str
    simulated_date: str
    scenario: str
    frame: pd.DataFrame
    expected_rows: int


def psi(baseline, current, bins: int = 10) -> float:
    """Population stability index on baseline-quantile bins, smoothing empty bins."""
    baseline, current = np.asarray(baseline, float), np.asarray(current, float)
    baseline, current = baseline[~np.isnan(baseline)], current[~np.isnan(current)]
    edges = np.unique(np.quantile(baseline, np.linspace(0, 1, bins + 1)))
    edges[0], edges[-1] = -np.inf, np.inf
    base = np.clip(np.histogram(baseline, edges)[0] / len(baseline), PSI_EPSILON, None)
    cur = np.clip(np.histogram(current, edges)[0] / len(current), PSI_EPSILON, None)
    return float(np.sum((cur - base) * np.log(cur / base)))


def classify(value: float, rule: dict[str, Any]) -> str:
    """OK, WARN or CRITICAL. A missing value is NOT_APPLICABLE, never OK."""
    if value is None or np.isnan(value):
        return "NOT_APPLICABLE"
    if value >= rule["critical"]:
        return "CRITICAL"
    if value >= rule["warn"]:
        return "WARN"
    return "OK"


def metrics_for(
    frame: pd.DataFrame,
    reference: pd.DataFrame,
    reference_auc: float,
    fields: list[str],
    expected_rows: int | None = None,
    baseline_gap: float = 0.0,
) -> dict[str, float]:
    """baseline_gap is subtracted from the calibration gap. Real vintages use 0 (absolute calibration).

    Simulated batches pass the pool's own gap, so that the alert tests an injected change and not the
    real 2015 under-prediction, which is reported separately under mode A.
    """
    y = frame[TARGET].to_numpy()
    expected = expected_rows if expected_rows is not None else len(frame)
    return {
        "max_field_null_share": float(frame[fields].isna().mean().max()),
        "row_count_gap_share": abs(expected - len(frame)) / expected,
        "default_rate_abs_deviation": abs(float(y.mean()) - float(reference[TARGET].mean())),
        "score_psi": psi(reference["score"], frame["score"]),
        "auc_drop": reference_auc - float(roc_auc_score(y, frame["score"])),
        "calibration_abs_gap": abs(float(frame["score"].mean()) - float(y.mean()) - baseline_gap),
    }


def real_vintage_monitoring(
    scored: pd.DataFrame, rules: list[dict[str, Any]], reference_year: int, fields: list[str]
) -> pd.DataFrame:
    """Mode A: each real 2015 quarter (unseen by the model) against the real reference vintage."""
    reference = scored[scored["issue_year"] == reference_year]
    reference_auc = float(roc_auc_score(reference[TARGET], reference["score"]))
    rows = []
    for quarter, part in scored[scored["split"] == "test"].groupby("issue_quarter"):
        values = metrics_for(part, reference, reference_auc, fields)
        values.pop("row_count_gap_share")
        for rule in rules:
            if rule["metric"] in values:
                value = values[rule["metric"]]
                rows.append(
                    {
                        "batch_id": str(quarter),
                        "rule": rule["name"],
                        "metric": rule["metric"],
                        "value": value,
                        "warn_level": rule["warn"],
                        "critical_level": rule["critical"],
                        "severity": classify(value, rule),
                        "simulation": False,
                        "provenance": f"Real data: {quarter} vintage vs {reference_year} reference",
                        "triage": rule["triage"],
                    }
                )
    return pd.DataFrame(rows)


def feature_drift(scored: pd.DataFrame, reference_year: int, features: list[str]) -> pd.DataFrame:
    reference = scored[scored["issue_year"] == reference_year]
    rows = []
    for quarter, part in scored[scored["split"] == "test"].groupby("issue_quarter"):
        for feature in features:
            rows.append(
                {
                    "issue_quarter": quarter,
                    "feature": feature,
                    "psi": psi(reference[feature], part[feature]),
                    "reference_mean": float(reference[feature].mean()),
                    "current_mean": float(part[feature].mean()),
                }
            )
    return pd.DataFrame(rows)


def build_simulated_batches(
    test: pd.DataFrame, cfg: dict[str, Any], seed: int, score_fn
) -> list[SimBatch]:
    """Partition held-out loans into batches, then inject the configured controlled faults."""
    rng = np.random.default_rng(seed)
    n_batches = cfg["n_batches"]
    pool = test.reset_index(drop=True)
    parts = np.array_split(rng.permutation(len(pool)), n_batches)
    dates = pd.date_range(cfg["first_batch_date"], periods=n_batches, freq="ME")
    high_risk = pool["grade"].isin(["D", "E", "F", "G"])
    batches = []
    for number, index in enumerate(parts, start=1):
        frame = pool.iloc[index].copy()
        expected = len(frame)
        scenario = "clean_partition"
        if number == cfg["row_loss_batch"]:
            scenario = "injected_row_loss"
            keep = rng.permutation(len(frame))[int(round(cfg["row_loss_share"] * len(frame))) :]
            frame = frame.iloc[np.sort(keep)].copy()
        elif number == cfg["null_spike_batch"]:
            scenario = "injected_null_spike"
            hit = rng.choice(
                frame.index, int(round(cfg["null_spike_share"] * len(frame))), replace=False
            )
            frame.loc[hit, cfg["null_spike_field"]] = np.nan
        elif number == cfg["mix_shift_batch"]:
            scenario = "injected_mix_shift"
            n_high = int(round(cfg["mix_shift_high_risk_share"] * expected))
            frame = pd.concat(
                [
                    pool[high_risk].sample(n_high, random_state=seed),
                    pool[~high_risk].sample(expected - n_high, random_state=seed),
                ]
            )
        elif number == cfg["outcome_shift_batch"]:
            scenario = "injected_outcome_deterioration"
            survivors = frame.index[frame[TARGET] == 0]
            flips = rng.choice(
                survivors,
                int(round(cfg["outcome_shift_flip_share"] * len(survivors))),
                replace=False,
            )
            frame.loc[flips, TARGET] = 1
        frame["score"] = score_fn(frame)
        batches.append(
            SimBatch(
                f"SIM-{number:02d}",
                dates[number - 1].date().isoformat(),
                scenario,
                frame.reset_index(drop=True),
                expected,
            )
        )
    return batches


def monitor_batches(
    batches: list[SimBatch],
    reference: pd.DataFrame,
    reference_auc: float,
    rules: list[dict[str, Any]],
    fields: list[str],
) -> pd.DataFrame:
    rows = []
    for batch in batches:
        pool_gap = float(reference["score"].mean() - reference[TARGET].mean())
        values = metrics_for(
            batch.frame, reference, reference_auc, fields, batch.expected_rows, pool_gap
        )
        for rule in rules:
            value = values[rule["metric"]]
            rows.append(
                {
                    "batch_id": batch.batch_id,
                    "simulated_date": batch.simulated_date,
                    "scenario": batch.scenario,
                    "rule": rule["name"],
                    "metric": rule["metric"],
                    "value": value,
                    "warn_level": rule["warn"],
                    "critical_level": rule["critical"],
                    "severity": classify(value, rule),
                    "simulation": True,
                    "provenance": SIMULATION_BANNER,
                    "triage": rule["triage"],
                }
            )
    return pd.DataFrame(rows)


def mix_rate_decomposition(a: pd.DataFrame, b: pd.DataFrame, by: str) -> dict[str, float]:
    """Kitagawa decomposition of rate(a) - rate(b) into mix and within-segment rate effects."""
    share_a, share_b = a[by].value_counts(normalize=True), b[by].value_counts(normalize=True)
    rate_a, rate_b = a.groupby(by)[TARGET].mean(), b.groupby(by)[TARGET].mean()
    keys = share_a.index.union(share_b.index)
    share_a, share_b = share_a.reindex(keys, fill_value=0), share_b.reindex(keys, fill_value=0)
    rate_a, rate_b = rate_a.reindex(keys), rate_b.reindex(keys)
    rate_a, rate_b = rate_a.fillna(rate_b).fillna(0), rate_b.fillna(rate_a).fillna(0)
    return {
        "rate_a": float(a[TARGET].mean()),
        "rate_b": float(b[TARGET].mean()),
        "total_gap": float(a[TARGET].mean() - b[TARGET].mean()),
        "mix_effect": float(((share_a - share_b) * (rate_a + rate_b) / 2).sum()),
        "rate_effect": float(((rate_a - rate_b) * (share_a + share_b) / 2).sum()),
    }


def interpret(
    total: float, mix: float, minimum_gap: float = 0.01, dominance: float = 0.6
) -> tuple[float, str]:
    share = mix / total if abs(total) > 1e-12 else float("nan")
    if abs(total) < minimum_gap:
        return share, "Within normal variation for this sample size"
    if share >= dominance:
        return share, "Mainly borrower mix: more high-risk grades, similar rates within each grade"
    if share <= 1 - dominance:
        return share, "Mainly within-grade rate change: outcomes differ for similar borrowers"
    return share, "Mixed: both borrower mix and within-grade rates moved"


def investigate(
    batches: list[SimBatch], reference: pd.DataFrame, by: str = "grade"
) -> pd.DataFrame:
    rows = []
    for batch in batches:
        out = mix_rate_decomposition(batch.frame, reference, by)
        share, finding = interpret(out["total_gap"], out["mix_effect"])
        rows.append(
            {
                "batch_id": batch.batch_id,
                "scenario": batch.scenario,
                "batch_rate": out["rate_a"],
                "reference_rate": out["rate_b"],
                "total_gap": out["total_gap"],
                "mix_effect": out["mix_effect"],
                "rate_effect": out["rate_effect"],
                "mix_share_of_gap": share,
                "finding": finding,
                "simulation": True,
                "provenance": SIMULATION_BANNER,
            }
        )
    return pd.DataFrame(rows)
