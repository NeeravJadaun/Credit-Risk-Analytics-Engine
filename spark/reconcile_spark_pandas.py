"""Compare the Spark ETL with the separate pandas rule engine, on the same real file."""

from __future__ import annotations

import json
import time

import pandas as pd

from credit_risk.audit import read_json, sha256_file, write_json
from credit_risk.config import load_settings
from credit_risk.contracts import read_source, reconciliation_checks, validate


def main() -> None:
    settings = load_settings()
    source = settings.root / settings.pipeline["source"]["path"]
    started = time.perf_counter()
    raw = read_source(source, settings.contract["required_columns"])
    accepted, quarantined, counts = validate(raw, settings.contract)
    groups = settings.contract["status_groups"]
    bad = groups["resolved_bad"]
    resolved = accepted[accepted["loan_status"].isin(groups["resolved_good"] + bad)]
    year = resolved["issue_date"].dt.year
    term = resolved["term_months"]
    cohort = resolved[
        ((term == 36) & (year <= settings.pipeline["cohort"]["max_issue_year_36m"]))
        | ((term == 60) & (year <= settings.pipeline["cohort"]["max_issue_year_60m"]))
    ]
    elapsed = time.perf_counter() - started

    spark = read_json(settings.path("results") / "spark_etl.json")
    rows = [{"metric": k, "pandas": counts[k], "spark": spark["counts"].get(k)} for k in counts]
    rows += [
        {
            "metric": "accepted_rows",
            "pandas": len(accepted),
            "spark": spark["silver_rows_read_back_from_hdfs"],
        },
        {
            "metric": "matured_cohort_loans",
            "pandas": len(cohort),
            "spark": spark["matured_cohort_loans"],
        },
        {
            "metric": "matured_cohort_defaults",
            "pandas": int(cohort["loan_status"].isin(bad).sum()),
            "spark": spark["matured_cohort_defaults"],
        },
    ]
    # Vintage rates computed by Spark and written to gold must equal the pandas result.
    pandas_vintage = (
        cohort.assign(
            issue_year=cohort["issue_date"].dt.year,
            is_default=cohort["loan_status"].isin(bad).astype(int),
        )
        .groupby(["issue_year", "term_months"])["is_default"]
        .agg(["size", "sum"])
        .reset_index()
    )
    gold = pd.read_csv(settings.path("gold") / "gold_vintage_term.csv")
    gold["term_months"] = gold["term_label"].str.extract(r"(\d+)")[0].astype(int)
    merged = pandas_vintage.merge(gold, on=["issue_year", "term_months"], how="outer")
    for _, row in merged.iterrows():
        key = f"vintage_{int(row['issue_year'])}_{int(row['term_months'])}m"
        rows.append({"metric": f"{key}_loans", "pandas": row["size"], "spark": row["loans"]})
        rows.append({"metric": f"{key}_defaults", "pandas": row["sum"], "spark": row["defaults"]})
    table = pd.DataFrame(rows)
    table["match"] = table["pandas"] == table["spark"]
    table.to_csv(settings.path("gold") / "reconciliation_spark_pandas.csv", index=False)
    checks = reconciliation_checks(
        counts,
        settings.thresholds["data_quality"]["max_quarantine_share"],
        settings.pipeline["source"]["expected_data_rows"],
    )
    sha = sha256_file(source)
    checks.append(
        {
            "check_name": "source_checksum_matches_manifest",
            "status": "PASS" if sha == settings.pipeline["source"]["expected_sha256"] else "FAIL",
            "observed": sha,
            "expected": settings.pipeline["source"]["expected_sha256"],
        }
    )
    checks.append(
        {
            "check_name": "spark_and_pandas_agree_on_every_count",
            "status": "PASS" if table["match"].all() else "FAIL",
            "observed": f"{int(table['match'].sum())} of {len(table)} match",
            "expected": "all match",
        }
    )
    pd.DataFrame(checks).to_csv(settings.path("gold") / "dq_layer_checks.csv", index=False)
    result = {
        "engine": "pandas",
        "etl_seconds": elapsed,
        "counts": counts,
        "accepted_rows": len(accepted),
        "matured_cohort_loans": len(cohort),
        "compared_values": len(table),
        "matching_values": int(table["match"].sum()),
        "layer_checks": checks,
        "source_sha256": sha,
        "source_bytes_gz": source.stat().st_size,
    }
    write_json(settings.path("results") / "pandas_etl.json", result)
    print(json.dumps({k: v for k, v in result.items() if k != "counts"}, indent=2))
    print(table[~table["match"]].to_string())
    print(json.dumps(counts, indent=1))


if __name__ == "__main__":
    main()
