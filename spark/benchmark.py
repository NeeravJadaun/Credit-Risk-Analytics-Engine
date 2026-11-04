"""Benchmarks on the same real data and machine. Reports medians of repeated runs, whichever way they fall."""

from __future__ import annotations

import statistics
import subprocess
import time

import pandas as pd
from pyspark.sql import functions as F

from credit_risk import spark_etl
from credit_risk.audit import machine_facts, read_json, write_json
from credit_risk.config import load_settings

REPEATS = 3


def timed(function, repeats=REPEATS) -> dict:
    runs = []
    answer = None
    for _ in range(repeats):
        start = time.perf_counter()
        answer = function()
        runs.append(time.perf_counter() - start)
    return {"median_seconds": statistics.median(runs), "runs": runs, "answer": answer}


def main() -> None:
    settings = load_settings()
    results = settings.path("results")
    sha = settings.pipeline["source"]["expected_sha256"]
    uri, base = settings.pipeline["hdfs"]["uri"], settings.pipeline["hdfs"]["base"]
    csv_path = f"{uri}{base}/bronze/{sha[:12]}/accepted.csv"
    silver = f"{uri}{base}/silver/{sha[:12]}/loans"
    spark = spark_etl.spark_session(settings, "credit-risk-benchmark")

    def from_csv():
        frame = spark.read.csv(csv_path, header=True, escape='"')
        return (
            frame.filter(F.col("grade").isNotNull())
            .groupBy("grade")
            .agg(F.count(F.lit(1)).alias("n"), F.avg(F.col("loan_amnt").cast("double")).alias("m"))
            .count()
        )

    def from_parquet():
        return (
            spark.read.parquet(silver)
            .groupBy("grade")
            .agg(F.count(F.lit(1)).alias("n"), F.avg("loan_amnt").alias("m"))
            .count()
        )

    def rows_csv():
        return (
            spark.read.csv(csv_path, header=True, escape='"')
            .filter(F.col("grade").isNotNull())
            .count()
        )

    def rows_parquet():
        return spark.read.parquet(silver).count()

    csv_time, parquet_time = timed(from_csv), timed(from_parquet)
    csv_rows, parquet_rows = rows_csv(), rows_parquet()
    spark.stop()
    du = subprocess.run(
        [
            "bash",
            "-c",
            f'eval "$(./scripts/local_hadoop.sh env)"; hdfs dfs -du -s {base}/bronze/{sha[:12]} {base}/silver/{sha[:12]} 2>/dev/null',
        ],
        capture_output=True,
        text=True,
        cwd=settings.root,
    ).stdout.split("\n")
    sizes = [int(line.split()[0]) for line in du if line.strip()]
    pandas_source = settings.root / settings.pipeline["source"]["path"]

    def pandas_group():
        frame = pd.read_csv(pandas_source, usecols=["grade", "loan_amnt"], low_memory=False)
        return frame.dropna(subset=["grade"]).groupby("grade")["loan_amnt"].mean().size

    pandas_time = timed(pandas_group, 1)
    spark_etl_result, pandas_etl_result = (
        read_json(results / "spark_etl.json"),
        read_json(results / "pandas_etl.json"),
    )
    write_json(
        results / "benchmarks.json",
        {
            "machine": machine_facts(),
            "spark_master": settings.pipeline["spark"]["master"],
            "execution_mode": "single machine, local Spark with a one-DataNode HDFS. Not a cluster.",
            "input_rows": spark_etl_result["counts"]["input_rows"],
            "repeats": REPEATS,
            "etl_full": {
                "spark_on_hdfs_seconds": spark_etl_result["etl_seconds"],
                "pandas_rules_and_cohort_seconds": pandas_etl_result["etl_seconds"],
                "note": "Single runs, not identical scope. The Spark run also writes silver and quarantine Parquet to HDFS, derives features and builds gold tables.",
            },
            "group_by_grade_query": {
                "csv_on_hdfs": {k: csv_time[k] for k in ("median_seconds", "runs")},
                "parquet_on_hdfs": {k: parquet_time[k] for k in ("median_seconds", "runs")},
                "speedup_factor_parquet_vs_csv": csv_time["median_seconds"]
                / parquet_time["median_seconds"],
                "rows_csv": csv_rows,
                "rows_parquet": parquet_rows,
                "pandas_csv_gz_seconds_single_run": pandas_time["median_seconds"],
                "comparability_note": "Parquet holds validated rows and CSV holds all loan rows, a difference of about 0.2%.",
            },
            "storage_bytes": {
                "csv_on_hdfs": sizes[0] if sizes else None,
                "silver_parquet_on_hdfs": sizes[1] if len(sizes) > 1 else None,
            },
            "postgres_index": read_json(results / "warehouse.json")["index_benchmark"],
        },
    )
    print((results / "benchmarks.json").read_text()[:1500])


if __name__ == "__main__":
    main()
