"""Automated reports. Every number is read from pipeline outputs at generation time."""

from __future__ import annotations

import base64
import html
from pathlib import Path
from typing import Any

import pandas as pd

from .audit import read_json, utc_now, write_json
from .monitoring import SIMULATION_BANNER


def load_facts(settings) -> dict[str, Any]:
    results, gold = settings.path("results"), settings.path("gold")
    facts = {
        name: read_json(results / f"{name}.json")
        for name in (
            "spark_etl",
            "pandas_etl",
            "model",
            "findings",
            "strategy",
            "monitoring",
            "warehouse",
        )
    }
    facts["kpis"] = pd.read_csv(gold / "kpi_indicators.csv").set_index("kpi_id")
    facts["findings_table"] = pd.read_csv(gold / "risk_findings.csv").set_index("name")
    facts["capacity"] = pd.read_csv(gold / "strategy_capacity_sensitivity.csv")
    facts["strategy_table"] = pd.read_csv(gold / "strategy_comparison.csv").set_index("policy")
    facts["segments"] = pd.read_csv(gold / "gold_segment_rates.csv")
    facts["drift"] = pd.read_csv(gold / "monitoring_feature_drift.csv")
    facts["vintage"] = pd.read_csv(gold / "gold_vintage_term.csv")
    facts["case"] = pd.read_csv(gold / "monitoring_case_study.csv").set_index("batch_id")
    return facts


def _pct(value: float, digits: int = 1) -> str:
    return f"{value * 100:.{digits}f}%"


def narrative(f: dict[str, Any]) -> dict[str, Any]:
    """The computed statements shared by the HTML report and the memo."""
    k, t = f["kpis"]["value"], f["model"]["test_metrics"]
    gap = f["monitoring"]["real_gap_2015_vs_2014_by_grade"]
    strongest = f["findings"]["strongest_by_prespecified_rule"]
    grade = f["findings_table"].loc["Grade D to G"]
    versus_rest = f["findings_table"].loc["Layered: FICO under 680, DTI 20+, utilization 60%+"]
    drift = f["monitoring"]["max_feature_psi"]
    return {
        "changes": [
            f"The 36-month default rate rose {gap['total_gap'] * 100:.2f} points from the 2014 vintage ({_pct(gap['rate_b'], 2)}) to the 2015 vintage ({_pct(gap['rate_a'], 2)}).",
            f"Grade D-G loans fell to {_pct(k['K04'])} of 2015 volume ({f['kpis'].loc['K04', 'note']}), so grade mix alone would have lowered the rate by {abs(gap['mix_effect']) * 100:.2f} points.",
            f"Within-grade default rates added {gap['rate_effect'] * 100:.2f} points. That is the part of the increase mix does not explain.",
        ],
        "concerns": [
            f"The model under-predicted the 2015 vintage: mean score {_pct(t['mean_score'], 2)} against {_pct(t['prevalence'], 2)} observed. It ranks loans more usefully than it estimates their level.",
            f"Interest rates shifted: the {drift['feature']} PSI reached {drift['psi']:.3f} in {drift['issue_quarter']} (mean {drift['current_mean']:.2f} vs {drift['reference_mean']:.2f} in 2014), so interest rates fell while outcomes worsened.",
            f"{len(f['monitoring']['real_alerts'])} real alerts fired across the four 2015 quarters, for default-rate deviation and calibration.",
        ],
        "insights": [
            f"Add the layered segment (FICO under 680, DTI 20+, utilization 60%+) to the monitoring pack as a watchlist indicator. It was {_pct(k['K11'])} of 2015 loans, with a {_pct(versus_rest['oot_segment_default_rate'])} default rate against {_pct(versus_rest['oot_comparison_default_rate'])} for all other loans.",
            "Review the model with its owner and test recalibration on recent vintages before using its scores as probability estimates.",
            "Ask why within-grade default rates rose while interest rates fell. Grade alone did not capture it, so track default rates within grade and not only the grade mix.",
        ],
        "strongest": strongest,
        "grade": grade,
        "versus_rest": versus_rest,
    }


def write_memo(settings, out_dir: Path, f: dict[str, Any], period: str) -> Path:
    n = narrative(f)
    s, g, r = n["strongest"], n["grade"], n["versus_rest"]
    t, k = f["model"]["test_metrics"], f["kpis"]["value"]
    counts = f["spark_etl"]["counts"]
    capacity = f["capacity"].set_index("capacity")
    case = f["case"]
    a10 = f["strategy_table"].loc["A: top 10% by score"]
    c20 = f["strategy_table"].loc["C: score >= 0.20 and DTI >= 20"]
    text = f"""# Credit Risk Management Memo

**Report period:** {period} (the run period, not a data month). **Generated:** by `python -m credit_risk.cli report`.
Independent educational project on public data. Not prepared for, or endorsed by, any financial institution. Nothing here is lending advice or bank policy.

## Executive question and scope

Is default risk changing across vintages, which borrower characteristics are associated with it, and what should a risk team monitor next?
Scope: {f["spark_etl"]["matured_cohort_loans"]:,} matured LendingClub loans (36-month issued through 2015, 60-month through 2013).

## Source and limitations

- Public LendingClub accepted-loan file, distributed through a Kaggle mirror (CC0 as listed there). LendingClub no longer publishes it directly, so the file could not be checked against the original publisher.
- {counts["input_rows"]:,} rows were read and {counts["quarantined_rows"]:,} quarantined, including {counts["Q01_non_loan_row"]} footer rows that are not loans.
- Approved loans only: declined applicants have no outcome. US peer-to-peer consumer loans, 2007 to 2019, which may not generalize to other lenders.
- Associations, not causes. `member_id` is empty, so repeat borrowers cannot be identified.

## Three findings

1. **Default rates rose in 2015 despite a better grade mix.** {n["changes"][0]} {n["changes"][1]} {n["changes"][2]} See `dashboards/screenshots/01_vintage_default_rate.png`.
2. **Layered borrower risk is the strongest borrower-attribute finding.** On the unseen 2015 vintage, loans with {s["segment_rule"].replace(" and ", ", ")} defaulted at {_pct(s["oot_segment_default_rate"])} ({int(s["oot_segment_defaults"]):,} of {int(s["oot_segment_loans"]):,}), against {_pct(s["oot_comparison_default_rate"])} ({int(s["oot_comparison_defaults"]):,} of {int(s["oot_comparison_loans"]):,}) for {s["comparison_rule"].replace(" and ", ", ")}: **{s["oot_relative_risk"]:.2f}x** (95% CI {s["oot_rr_ci95_low"]:.2f} to {s["oot_rr_ci95_high"]:.2f}). This compares two ends of the book. Against all other loans the ratio is {r["oot_relative_risk"]:.2f}x. See `dashboards/screenshots/06_relative_risk.png` and `04_fico_dti_heatmap.png`.
3. **The model ranks moderately and is useful mainly for prioritizing review.** Out-of-time ROC-AUC was {t["roc_auc"]:.3f} (95% CI {t["roc_auc_ci95_low"]:.3f} to {t["roc_auc_ci95_high"]:.3f}) on {t["records"]:,} loans. The top score decile held {_pct(k["K10"])} of defaults, {k["K10"] / 0.10:.1f}x what a random ranking would capture. See `dashboards/screenshots/07_capture_curve.png`.

LendingClub's own grade separates risk too: grade D-G defaulted {g["oot_relative_risk"]:.2f}x as often as A-C ({_pct(g["oot_segment_default_rate"])} vs {_pct(g["oot_comparison_default_rate"])}). As that repeats the lender's existing assessment, it is not presented as a new insight.

## One issue investigated

{n["concerns"][0]} To check the investigation method itself, controlled faults were injected into synthetic batches. *{SIMULATION_BANNER}* Batch SIM-11 raised the default rate by {case.loc["SIM-11", "total_gap"] * 100:.2f} points and the decomposition attributed it to mix ({case.loc["SIM-11", "mix_effect"] * 100:.2f} points). Batch SIM-12 raised it by {case.loc["SIM-12", "total_gap"] * 100:.2f} points and attributed it to within-grade rates ({case.loc["SIM-12", "rate_effect"] * 100:.2f} points). Both match what was injected, so the same decomposition was applied to the real 2015 change, which it attributes to within-grade rates. Root cause is not established from this data.

## Two strategy options (hypothetical and retrospective)

| Option | Referred | Default capture | Default rate among referred | Trade-off |
|---|---:|---:|---:|---|
| Top 10% by score (A) | {_pct(a10["flagged_share"])} | {_pct(a10["default_capture"])} | {_pct(a10["default_rate_flagged"])} | Fixed workload, but depends on the score alone |
| Score 0.20+ and DTI 20+ (C) | {_pct(c20["flagged_share"])} | {_pct(c20["default_capture"])} | {_pct(c20["default_rate_flagged"])} | Easier to explain, but volume moves with the portfolio |

Under the illustrative objective (most defaults captured with at most 10% referred), option A was chosen on 2014 validation data. Moving capacity from 5% to 10% to 20% changes capture from {_pct(capacity.loc[0.05, "test_capture"])} to {_pct(capacity.loc[0.10, "test_capture"])} to {_pct(capacity.loc[0.20, "test_capture"])}.
**Why not to deploy this blindly:** it shows what was already associated with default among approved loans. It does not show that a review would prevent a default, what a review costs, or how declined applicants would behave, and {_pct(1 - a10["default_rate_flagged"])} of referred loans did not default.

## Recommended next analytical action

{n["insights"][0]} {n["insights"][1]} {n["insights"][2]}

## Open questions for a model owner or business partner

- Do stricter or looser credit policies explain the 2015 within-grade rise, and is it visible in later vintages?
- What review capacity is realistic, and what does a false referral cost?
- Is a borrower key available, so repeat borrowers can be handled?
- {counts["Q13_invalid_dti"]:,} rows had DTI outside 0 to 100 and were quarantined. What do those values mean, and should joint-application DTI be used for joint loans?
"""
    path = out_dir / "management_memo.md"
    path.write_text(text)
    return path


def _embed(path: Path) -> str:
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode()


def write_html(settings, out_dir: Path, f: dict[str, Any], period: str) -> Path:
    n = narrative(f)
    figures = settings.path("figures")
    k = f["kpis"]

    def fmt(row):
        if row.name in ("K06",):
            return f"{row['value']:.2f}"
        if row.name in ("K07", "K09"):
            return f"{row['value']:.3f}"
        return f"{row['value'] * 100:.2f}%"

    kpi_rows = "".join(
        f"<tr><td>{i}</td><td>{html.escape(r['indicator'])}</td><td class=n>{fmt(r)}</td><td>{html.escape(r['basis'])}</td><td>{html.escape(str(r['note']))}</td></tr>"
        for i, r in k.iterrows()
    )
    ul = lambda items: "<ul>" + "".join(f"<li>{html.escape(x)}</li>" for x in items) + "</ul>"  # noqa: E731
    images = [
        ("01_vintage_default_rate.png", "Observed default rate by issue year"),
        ("06_relative_risk.png", "Relative risk of pre-specified segments"),
        ("04_fico_dti_heatmap.png", "Default rate by FICO and DTI"),
        ("07_capture_curve.png", "Default capture by review volume"),
        ("03_calibration.png", "Calibration on the 2015 vintage"),
        ("08_simulated_monitoring.png", "Simulated monitoring batches"),
    ]
    gallery = "".join(
        f'<figure><img src="{_embed(figures / name)}" alt="{alt}"><figcaption>{alt}</figcaption></figure>'
        for name, alt in images
    )
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Credit Risk Report</title>
<style>body{{font-family:Arial,sans-serif;color:#0b0b0b;background:#fcfcfb;max-width:1000px;margin:24px auto;padding:0 16px;line-height:1.45}}h1{{font-size:26px;margin:0}}h2{{font-size:18px;margin-top:28px}}.src{{color:#52514e;font-size:13px}}table{{border-collapse:collapse;width:100%;font-size:13px}}th,td{{border-bottom:1px solid #e4e3df;padding:6px 8px;text-align:left;vertical-align:top}}.n{{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}}img{{width:100%;height:auto}}figure{{margin:16px 0}}figcaption{{font-size:12px;color:#52514e}}.sim{{background:#eef3f9;padding:8px 12px;font-size:13px}}.wrap{{overflow-x:auto}}</style></head><body>
<h1>Credit risk portfolio report</h1><p class=src>Report period {html.escape(period)} (the run period, not a data month). Public LendingClub loans, matured vintages. Independent educational project, not a bank system.</p>
<h2>Major portfolio changes</h2>{ul(n["changes"])}<h2>Concerning trends</h2>{ul(n["concerns"])}<h2>Actionable insights</h2>{ul(n["insights"])}
<h2>Reporting indicators</h2><div class=wrap><table><tr><th>ID</th><th>Indicator</th><th>Value</th><th>Basis</th><th>Note</th></tr>{kpi_rows}</table></div>
<p class=sim>Charts below use real data except the last one. {SIMULATION_BANNER}</p><h2>Evidence</h2>{gallery}</body></html>"""
    path = out_dir / "portfolio_report.html"
    path.write_text(page)
    return path


def publish(settings, out_dir: Path, period: str) -> dict[str, Any]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    facts = load_facts(settings)
    failed = [c["check_name"] for c in facts["pandas_etl"]["layer_checks"] if c["status"] == "FAIL"]
    failed += [
        c["check_name"] for c in facts["warehouse"]["integrity_checks"] if c["status"] == "FAIL"
    ]
    status_file = out_dir / "LATEST_STATUS.json"
    if failed:
        write_json(
            status_file,
            {
                "status": "FAILED",
                "period": period,
                "failed_checks": failed,
                "written_at": utc_now(),
            },
        )
        raise RuntimeError(f"Report not published. Failed checks: {failed}")
    memo, page = (
        write_memo(settings, out_dir, facts, period),
        write_html(settings, out_dir, facts, period),
    )
    write_json(
        status_file,
        {"status": "PUBLISHED", "period": period, "failed_checks": [], "written_at": utc_now()},
    )
    return {"memo": str(memo), "html": str(page)}
