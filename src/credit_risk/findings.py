"""Statistical deep-dive: relative risk with intervals, on pre-specified borrower segments."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from scipy import stats

TARGET = "default_flag"


def wilson_interval(successes, total, confidence: float = 0.95):
    successes, total = np.asarray(successes, float), np.asarray(total, float)
    z = stats.norm.ppf(0.5 + confidence / 2)
    with np.errstate(divide="ignore", invalid="ignore"):
        rate = successes / total
        centre = (rate + z**2 / (2 * total)) / (1 + z**2 / total)
        half = z * np.sqrt(rate * (1 - rate) / total + z**2 / (4 * total**2)) / (1 + z**2 / total)
    return centre - half, centre + half


def relative_risk(a: int, n1: int, c: int, n2: int) -> dict[str, float]:
    """Risk ratio of group 1 to group 2 with a 95% log-normal interval and a chi-square test."""
    r1, r2 = a / n1, c / n2
    rr = r1 / r2
    se = np.sqrt(1 / a - 1 / n1 + 1 / c - 1 / n2)
    chi2, p, _, _ = stats.chi2_contingency([[a, n1 - a], [c, n2 - c]], correction=False)
    return {
        "segment_default_rate": r1,
        "comparison_default_rate": r2,
        "relative_risk": rr,
        "rr_ci95_low": float(np.exp(np.log(rr) - 1.959963984540054 * se)),
        "rr_ci95_high": float(np.exp(np.log(rr) + 1.959963984540054 * se)),
        "absolute_difference": r1 - r2,
        "chi2": float(chi2),
        "p_value": float(p),
        "cohens_h": float(2 * np.arcsin(np.sqrt(r1)) - 2 * np.arcsin(np.sqrt(r2))),
    }


def mantel_haenszel_rr(
    frame: pd.DataFrame, in_segment: pd.Series, in_comparison: pd.Series, strata: list[str]
) -> float:
    """Risk ratio adjusted for vintage and term, so that mix across those cannot create the result."""
    data = frame.assign(_s=in_segment, _c=in_comparison)
    numerator = denominator = 0.0
    for _, part in data[data["_s"] | data["_c"]].groupby(strata, observed=True):
        n1, n2 = int(part["_s"].sum()), int(part["_c"].sum())
        if not n1 or not n2:
            continue
        a, c = int(part.loc[part["_s"], TARGET].sum()), int(part.loc[part["_c"], TARGET].sum())
        total = n1 + n2
        numerator += a * n2 / total
        denominator += c * n1 / total
    return numerator / denominator if denominator else float("nan")


def _mask(frame: pd.DataFrame, expression: str) -> pd.Series:
    return frame.eval(expression, engine="python").fillna(False).astype(bool)


def evaluate_segments(frame: pd.DataFrame, config: dict[str, Any]) -> pd.DataFrame:
    """Discovery uses vintages up to 2014 and confirmation uses the unseen 2015 vintage."""
    discovery, confirmation = frame[frame["split"] != "test"], frame[frame["split"] == "test"]
    specs = config["segments"]
    rows = []
    for spec in specs:
        seg_d, cmp_d = _mask(discovery, spec["segment"]), _mask(discovery, spec["comparison"])
        seg_t, cmp_t = _mask(confirmation, spec["segment"]), _mask(confirmation, spec["comparison"])
        n1, n2 = int(seg_d.sum()), int(cmp_d.sum())
        a, c = int(discovery.loc[seg_d, TARGET].sum()), int(discovery.loc[cmp_d, TARGET].sum())
        n1t, n2t = int(seg_t.sum()), int(cmp_t.sum())
        at, ct = (
            int(confirmation.loc[seg_t, TARGET].sum()),
            int(confirmation.loc[cmp_t, TARGET].sum()),
        )
        d, t = relative_risk(a, n1, c, n2), relative_risk(at, n1t, ct, n2t)
        rows.append(
            {
                "name": spec["name"],
                "attribute": spec["attribute"],
                "uses_lc_pricing": spec["uses_lc_pricing"],
                "segment_rule": spec["segment"],
                "comparison_rule": spec["comparison"],
                "disc_segment_loans": n1,
                "disc_segment_defaults": a,
                "disc_comparison_loans": n2,
                "disc_comparison_defaults": c,
                **{f"disc_{k}": v for k, v in d.items()},
                "disc_rr_adjusted_year_term": mantel_haenszel_rr(
                    discovery, seg_d, cmp_d, ["issue_year", "term_months"]
                ),
                "oot_segment_loans": n1t,
                "oot_segment_defaults": at,
                "oot_comparison_loans": n2t,
                "oot_comparison_defaults": ct,
                **{f"oot_{k}": v for k, v in t.items()},
            }
        )
    table = pd.DataFrame(rows)
    bonferroni = config["alpha"] / len(specs)
    table["bonferroni_alpha"] = bonferroni
    table["meets_min_size"] = (
        table[
            [
                "disc_segment_loans",
                "disc_comparison_loans",
                "oot_segment_loans",
                "oot_comparison_loans",
            ]
        ].min(axis=1)
        >= config["min_segment_records"]
    )
    table["confirmed"] = (
        (table["disc_p_value"] < bonferroni)
        & (table["oot_p_value"] < bonferroni)
        & (table["oot_rr_ci95_low"] > 1)
        & table["meets_min_size"]
    )
    return table


def strongest_finding(table: pd.DataFrame) -> pd.Series:
    """Selection rule fixed in advance: largest confirmed discovery RR among borrower attributes.

    It is ranked on discovery data, so the out-of-time number is an unbiased check of the choice.
    LendingClub's own grade is excluded, since repeating a lender's grade is not a new finding.
    """
    eligible = table[table["confirmed"] & ~table["uses_lc_pricing"]]
    if eligible.empty:
        raise ValueError("No borrower-attribute finding was confirmed out of time.")
    return eligible.sort_values("disc_relative_risk", ascending=False).iloc[0]
