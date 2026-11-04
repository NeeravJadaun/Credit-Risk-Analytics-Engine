"""Run the Spark ETL against HDFS. Usage: python spark/spark_transform.py"""

from __future__ import annotations

import json
import subprocess
import time

from pyspark import StorageLevel

from credit_risk import spark_etl
from credit_risk.audit import write_json
from credit_risk.config import load_settings


def main() -> None:
    settings = load_settings()
    started = time.perf_counter()
    sha = settings.pipeline["source"]["expected_sha256"]
    uri, base = settings.pipeline["hdfs"]["uri"], settings.pipeline["hdfs"]["base"]
    source = f"{uri}{base}/bronze/{sha[:12]}/accepted.csv"
    silver_path = f"{uri}{base}/silver/{sha[:12]}/loans"
    quarantine_path = f"{uri}{base}/quarantine/{sha[:12]}"

    spark = spark_etl.spark_session(settings)
    raw = spark_etl.read_source_csv(spark, source, settings.contract["required_columns"])
    partitions = raw.rdd.getNumPartitions()
    checked = spark_etl.apply_contract(spark_etl.to_typed(raw), settings.contract)
    checked.persist(StorageLevel.DISK_ONLY)
    counts = spark_etl.quality_counts(checked, settings.contract)

    (
        checked.filter("is_quarantined")
        .selectExpr("loan_id", "array_join(violations, '|') AS reason_codes", "loan_status")
        .coalesce(1)
        .write.mode("overwrite")
        .parquet(quarantine_path)
    )
    silver = spark_etl.add_features(
        checked.filter("NOT is_quarantined"), settings.contract, settings.pipeline
    )
    silver.repartition("issue_year").write.mode("overwrite").partitionBy("issue_year").parquet(
        silver_path
    )

    stored = spark.read.parquet(silver_path)
    layers = stored.groupBy("split").count().collect()
    aggregates = spark_etl.gold_aggregates(stored)
    gold = settings.path("gold")
    for name, frame in aggregates.items():
        spark_etl.write_single_csv(frame, gold / f"{name}.csv")
    stored_rows = stored.count()
    matured = stored.filter("is_matured_cohort").count()
    matured_defaults = stored.filter("is_matured_cohort AND default_flag = 1").count()
    elapsed = time.perf_counter() - started
    spark.stop()

    local = settings.path("silver") / "loans_parquet"
    subprocess.run(
        [
            "bash",
            "-c",
            f'eval "$(./scripts/local_hadoop.sh env)"; rm -rf "{local}"; '
            f'hdfs dfs -get "{silver_path}" "{local}" 2>/dev/null',
        ],
        cwd=settings.root,
        check=True,
    )
    result = {
        "engine": "pyspark",
        "source_uri": source,
        "silver_uri": silver_path,
        "input_partitions": partitions,
        "counts": counts,
        "silver_rows_read_back_from_hdfs": stored_rows,
        "matured_cohort_loans": matured,
        "matured_cohort_defaults": matured_defaults,
        "split_counts": {r["split"]: r["count"] for r in layers},
        "etl_seconds": elapsed,
    }
    write_json(settings.path("results") / "spark_etl.json", result)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
