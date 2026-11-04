"""Hypothetical review policies, compared retrospectively. Not causal, and not bank policy.

Only outcomes for loans LendingClub actually approved are observed. Declined applicants have no
outcome, so every result carries selection bias, and nobody here was actually referred or treated differently.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from .findings import TARGET, wilson_interval


def top_share_flags(score: pd.Series, ids: pd.Series, share: float) -> np.ndarray:
    """Exact top-share queue. Ties break on customer_id so that the result is deterministic."""
    order = pd.DataFrame({"score": score.to_numpy(), "id": ids.to_numpy()})
    order = order.sort_values(["score", "id"], ascending=[False, True])
    flags = np.zeros(len(order), dtype=bool)
    flags[order.index[: int(np.floor(share * len(order)))]] = True  # never exceeds capacity
    return flags


def build_policies(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    policies: list[dict[str, Any]] = [
        {"policy": "Baseline: no referrals", "family": "Baseline", "parameter": None}
    ]
    for share in cfg["top_share_grid"]:
        policies.append(
            {"policy": f"A: top {share:.0%} by score", "family": "A", "parameter": share}
        )
    for threshold in cfg["score_thresholds"]:
        policies.append(
            {"policy": f"B: score >= {threshold:.2f}", "family": "B", "parameter": threshold}
        )
    min_dti = cfg["combo_min_dti"]
    for threshold in cfg["combo_score_thresholds"]:
        policies.append(
            {
                "policy": f"C: score >= {threshold:.2f} and DTI >= {min_dti}",
                "family": "C",
                "parameter": threshold,
                "min_dti": min_dti,
            }
        )
    return policies


def apply_policy(policy: dict[str, Any], frame: pd.DataFrame) -> np.ndarray:
    frame = frame.reset_index(drop=True)
    if policy["family"] == "Baseline":
        return np.zeros(len(frame), dtype=bool)
    if policy["family"] == "A":
        return top_share_flags(frame["score"], frame["loan_id"], policy["parameter"])
    if policy["family"] == "B":
        return (frame["score"] >= policy["parameter"]).to_numpy()
    if policy["family"] == "C":
        return (
            (frame["score"] >= policy["parameter"]) & (frame["dti"] >= policy["min_dti"])
        ).to_numpy()
    raise ValueError(f"Unknown policy family: {policy['family']}")


def policy_metrics(flags: np.ndarray, y: np.ndarray) -> dict[str, float]:
    y = np.asarray(y).astype(int)
    n, defaults, flagged = len(y), int(y.sum()), int(flags.sum())
    caught = int((flags & (y == 1)).sum())
    unflagged = n - flagged
    missed = defaults - caught
    low, high = wilson_interval(caught, defaults)
    return {
        "records": n,
        "defaults": defaults,
        "flagged": flagged,
        "flagged_share": flagged / n,
        "defaults_flagged": caught,
        "default_capture": caught / defaults if defaults else np.nan,
        "default_capture_ci95_low": float(low),
        "default_capture_ci95_high": float(high),
        "default_rate_flagged": caught / flagged if flagged else np.nan,
        "default_rate_unflagged": missed / unflagged if unflagged else np.nan,
        "false_positives": flagged - caught,
        "false_negatives": missed,
    }


def compare_policies(frame: pd.DataFrame, cfg: dict[str, Any]) -> pd.DataFrame:
    rows = []
    for policy in build_policies(cfg):
        flags = apply_policy(policy, frame)
        rows.append(
            {
                "policy": policy["policy"],
                "family": policy["family"],
                "parameter": policy["parameter"],
                **policy_metrics(flags, frame[TARGET].to_numpy()),
            }
        )
    return pd.DataFrame(rows)


def select_policy(validation: pd.DataFrame, capacity: float) -> pd.Series:
    """Objective: maximize default capture subject to flagged share <= capacity."""
    eligible = validation[
        (validation["family"] != "Baseline") & (validation["flagged_share"] <= capacity)
    ]
    if eligible.empty:
        raise ValueError(f"No policy meets the capacity constraint of {capacity}.")
    ordered = eligible.sort_values(
        ["default_capture", "flagged_share", "policy"], ascending=[False, True, True]
    )
    return ordered.iloc[0]


def capacity_sensitivity(validation, test, capacities) -> pd.DataFrame:
    rows = []
    for capacity in capacities:
        best = select_policy(validation, capacity)
        row = test[test["policy"] == best["policy"]].iloc[0]
        rows.append(
            {
                "capacity": capacity,
                "selected_on_validation": best["policy"],
                "validation_capture": best["default_capture"],
                "test_flagged_share": row["flagged_share"],
                "test_capture": row["default_capture"],
                "test_default_rate_flagged": row["default_rate_flagged"],
            }
        )
    return pd.DataFrame(rows)


def cost_sensitivity(test: pd.DataFrame, scenarios: list[dict[str, Any]]) -> pd.DataFrame:
    """Hypothetical units from a user-set configuration. Not revenue, loss or capital figures."""
    rows = []
    for scenario in scenarios:
        costs = (
            test["false_negatives"] * scenario["false_negative"]
            + test["false_positives"] * scenario["false_positive"]
        )
        for policy, cost in zip(test["policy"], costs, strict=True):
            rows.append(
                {
                    "cost_scenario": scenario["name"],
                    "fn_cost": scenario["false_negative"],
                    "fp_cost": scenario["false_positive"],
                    "policy": policy,
                    "hypothetical_cost_units": float(cost),
                }
            )
    table = pd.DataFrame(rows)
    table["rank_in_scenario"] = (
        table.groupby("cost_scenario")["hypothetical_cost_units"].rank(method="min").astype(int)
    )
    return table


def referral_distribution(frame: pd.DataFrame, policy: dict[str, Any]) -> pd.DataFrame:
    """Who would be referred, by segment. Shown for review of concentration."""
    data = frame.reset_index(drop=True).copy()
    data["flagged"] = apply_policy(policy, data)
    rows = []
    for column in ["grade", "fico_band", "dti_band", "income_band", "home_ownership"]:
        for segment, part in data.groupby(column, observed=True):
            defaults = int(part[TARGET].sum())
            rows.append(
                {
                    "segment_type": column,
                    "segment": segment,
                    "records": len(part),
                    "defaults": defaults,
                    "flagged": int(part["flagged"].sum()),
                    "flagged_share": float(part["flagged"].mean()),
                    "default_capture": float(
                        (part["flagged"] & (part[TARGET] == 1)).sum() / defaults
                    )
                    if defaults
                    else np.nan,
                    "small_sample_warning": len(part) < 100 or defaults < 20,
                }
            )
    return pd.DataFrame(rows)
