import math

import pandas as pd
import pytest

from credit_risk import findings


def test_relative_risk_matches_hand_calculation():
    out = findings.relative_risk(30, 100, 10, 100)
    assert out["relative_risk"] == pytest.approx(3.0)
    se = math.sqrt(1 / 30 - 1 / 100 + 1 / 10 - 1 / 100)
    assert out["rr_ci95_low"] == pytest.approx(math.exp(math.log(3) - 1.959964 * se), abs=1e-5)
    assert out["p_value"] < 0.01


def test_wilson_interval_known_value():
    low, high = findings.wilson_interval(10, 100)
    assert float(low) == pytest.approx(0.0552, abs=1e-3) and float(high) == pytest.approx(
        0.1744, abs=1e-3
    )


def test_mantel_haenszel_removes_confounding_by_stratum():
    # Within each stratum RR is exactly 1, while the pooled ratio is far from 1.
    rows = [
        ("a", True, 1, 80),
        ("a", True, 0, 20),
        ("a", False, 1, 8),
        ("a", False, 0, 2),
        ("b", True, 1, 1),
        ("b", True, 0, 9),
        ("b", False, 1, 10),
        ("b", False, 0, 90),
    ]
    frame = pd.DataFrame(
        [{"stratum": s, "seg": g, "default_flag": d} for s, g, d, n in rows for _ in range(n)]
    )
    pooled = frame[frame.seg]["default_flag"].mean() / frame[~frame.seg]["default_flag"].mean()
    assert pooled > 3
    assert findings.mantel_haenszel_rr(
        frame, frame["seg"], ~frame["seg"], ["stratum"]
    ) == pytest.approx(1.0)


def test_strongest_finding_ignores_lc_pricing_and_unconfirmed():
    table = pd.DataFrame(
        [
            {
                "name": "grade",
                "uses_lc_pricing": True,
                "confirmed": True,
                "disc_relative_risk": 9.0,
            },
            {
                "name": "unconfirmed",
                "uses_lc_pricing": False,
                "confirmed": False,
                "disc_relative_risk": 5.0,
            },
            {
                "name": "real",
                "uses_lc_pricing": False,
                "confirmed": True,
                "disc_relative_risk": 2.0,
            },
        ]
    )
    assert findings.strongest_finding(table)["name"] == "real"


def test_segments_are_prespecified_with_comparisons(settings):
    specs = settings.findings["segments"]
    assert len(specs) >= 10 and all(s["segment"] and s["comparison"] for s in specs)
