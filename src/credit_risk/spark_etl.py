"""PySpark ETL: typed parsing, contract enforcement, derived features, gold aggregates."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from pyspark.sql import DataFrame, SparkSession, Window
from pyspark.sql import functions as F


def spark_session(
    settings, app: str = "credit-risk-etl", master: str | None = None
) -> SparkSession:
    if "JAVA_HOME" not in os.environ:
        prefix = subprocess.run(
            ["brew", "--prefix", "openjdk@17"], capture_output=True, text=True
        ).stdout.strip()
        os.environ["JAVA_HOME"] = f"{prefix}/libexec/openjdk.jdk/Contents/Home"
    os.environ["PYSPARK_PYTHON"] = sys.executable
    os.environ["PYSPARK_DRIVER_PYTHON"] = sys.executable
    cfg = settings.pipeline["spark"]
    tmp = settings.root / ".local" / "spark-tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    spark = (
        SparkSession.builder.appName(app)
        .master(master or cfg["master"])
        .config("spark.driver.memory", cfg["driver_memory"])
        .config("spark.sql.shuffle.partitions", cfg["shuffle_partitions"])
        .config("spark.sql.session.timeZone", "UTC")
        .config("spark.local.dir", str(tmp))
        .config("spark.hadoop.dfs.replication", "1")
        .config("spark.hadoop.dfs.blocksize", "33554432")  # 32 MB: HDFS reserves a block per writer
        .config("spark.ui.enabled", "false")
        .config("spark.driver.bindAddress", "127.0.0.1")
        .config("spark.driver.host", "127.0.0.1")
        .getOrCreate()
    )
    spark.sparkContext.setLogLevel("ERROR")
    return spark


def read_source_csv(spark: SparkSession, path: str, required: list[str]) -> DataFrame:
    raw = spark.read.csv(path, header=True, inferSchema=False, escape='"')
    missing = [c for c in required if c not in raw.columns]
    if missing:
        raise ValueError(f"Source is missing expected columns: {missing}")
    return raw.select(*required)


def _num(col: str) -> F.Column:
    return F.col(col).cast("double")


def _month(col: str) -> F.Column:
    return F.to_date(F.concat(F.lit("01-"), F.col(col)), "dd-MMM-yyyy")


def to_typed(raw: DataFrame) -> DataFrame:
    """Type every field. An unparseable value becomes null and is then caught by a contract rule."""
    numeric = [
        "loan_amnt",
        "funded_amnt",
        "installment",
        "annual_inc",
        "dti",
        "fico_range_low",
        "fico_range_high",
        "delinq_2yrs",
        "inq_last_6mths",
        "open_acc",
        "pub_rec",
        "revol_bal",
        "total_acc",
        "mort_acc",
        "pub_rec_bankruptcies",
    ]
    frame = raw.select(
        F.when(F.col("id").rlike(r"^\d+$"), F.col("id").cast("long")).alias("loan_id"),
        F.trim("loan_status").alias("loan_status"),
        _month("issue_d").alias("issue_date"),
        _month("earliest_cr_line").alias("earliest_cr_date"),
        F.regexp_extract("term", r"(\d+)", 1).cast("int").alias("term_months"),
        _num("int_rate").alias("int_rate"),
        F.when(F.col("revol_util").isNotNull(), _num("revol_util")).alias("revol_util"),
        *[_num(c).alias(c) for c in numeric],
        *[
            F.trim(c).alias(c)
            for c in [
                "grade",
                "sub_grade",
                "home_ownership",
                "verification_status",
                "purpose",
                "addr_state",
                "application_type",
            ]
        ],
        F.when(F.col("emp_length") == "< 1 year", 0.5)
        .when(F.col("emp_length") == "10+ years", 10.0)
        .when(
            F.col("emp_length").rlike(r"^\d+ years?$"),
            F.regexp_extract("emp_length", r"(\d+)", 1).cast("double"),
        )
        .alias("emp_years"),
    )
    occurrence = Window.partitionBy("loan_id").orderBy(F.monotonically_increasing_id())
    return frame.withColumn(
        "id_occurrence", F.when(F.col("loan_id").isNotNull(), F.row_number().over(occurrence))
    )


def apply_contract(typed: DataFrame, contract: dict[str, Any]) -> DataFrame:
    """Add one boolean column per rule and an array holding the violated rule IDs."""
    rules = contract["quarantine_rules"]
    frame = typed
    for rule in rules:
        frame = frame.withColumn(rule["id"], F.coalesce(F.expr(rule["expr"]), F.lit(False)))
    ids = F.array(*[F.when(F.col(r["id"]), F.lit(r["id"])) for r in rules])
    return frame.withColumn("violations", F.array_compact(ids)).withColumn(
        "is_quarantined", F.size("violations") > 0
    )


def quality_counts(checked: DataFrame, contract: dict[str, Any]) -> dict[str, Any]:
    rules, flags = contract["quarantine_rules"], contract["warning_flags"]
    groups = contract["status_groups"]
    aggregates = [
        F.count(F.lit(1)).alias("input_rows"),
        F.sum(F.col("is_quarantined").cast("int")).alias("quarantined_rows"),
        F.countDistinct("loan_id").alias("distinct_loan_ids"),
    ]
    aggregates += [F.sum(F.col(r["id"]).cast("int")).alias(r["id"]) for r in rules]
    accepted = ~F.col("is_quarantined")
    aggregates += [
        F.sum((accepted & F.coalesce(F.expr(f["expr"]), F.lit(False))).cast("int")).alias(f["id"])
        for f in flags
    ]
    for name, statuses in groups.items():
        aggregates.append(
            F.sum((accepted & F.col("loan_status").isin(statuses)).cast("int")).alias(
                f"status_{name}"
            )
        )
    row = checked.agg(*aggregates).collect()[0].asDict()
    return {k: int(v or 0) for k, v in row.items()}


def add_features(
    accepted: DataFrame, contract: dict[str, Any], pipeline: dict[str, Any]
) -> DataFrame:
    groups = contract["status_groups"]
    cohort, split = pipeline["cohort"], pipeline["split"]
    resolved = F.col("loan_status").isin(groups["resolved_good"] + groups["resolved_bad"])
    frame = (
        accepted.drop(
            "is_quarantined",
            "violations",
            "id_occurrence",
            *[r["id"] for r in contract["quarantine_rules"]],
        )
        .withColumn("issue_year", F.year("issue_date"))
        .withColumn(
            "issue_quarter", F.concat(F.year("issue_date"), F.lit("-Q"), F.quarter("issue_date"))
        )
        .withColumn("is_resolved", resolved)
        .withColumn(
            "default_flag",
            F.when(F.col("loan_status").isin(groups["resolved_bad"]), 1).when(
                F.col("loan_status").isin(groups["resolved_good"]), 0
            ),
        )
        .withColumn(
            "status_group",
            F.when(F.col("loan_status").isin(groups["resolved_good"]), "resolved_good")
            .when(F.col("loan_status").isin(groups["resolved_bad"]), "resolved_bad")
            .when(F.col("loan_status").isin(groups["open_performing"]), "open_performing")
            .when(F.col("loan_status").isin(groups["open_delinquent"]), "open_delinquent")
            .when(F.col("loan_status").isin(groups["excluded_policy"]), "excluded_policy")
            .otherwise("unknown"),
        )
        .withColumn("term_label", F.concat(F.col("term_months"), F.lit(" months")))
        .withColumn("fico_mid", (F.col("fico_range_low") + F.col("fico_range_high")) / 2)
        .withColumn("credit_age_months", F.months_between("issue_date", "earliest_cr_date"))
        .withColumn("loan_to_income", F.col("loan_amnt") / F.col("annual_inc"))
        .withColumn("installment_to_income", F.col("installment") * 12 / F.col("annual_inc"))
        .withColumn("log_loan_amnt", F.log1p("loan_amnt"))
        .withColumn("log_installment", F.log1p("installment"))
        .withColumn("log_annual_inc", F.log1p("annual_inc"))
        .withColumn("log_revol_bal", F.log1p("revol_bal"))
        .withColumn(
            "sub_grade_rank",
            (F.ascii(F.col("grade")) - 65) * 5 + F.substring("sub_grade", 2, 1).cast("int"),
        )
    )
    frame = frame.withColumn(
        "is_matured_cohort",
        F.col("is_resolved")
        & (
            ((F.col("term_months") == 36) & (F.col("issue_year") <= cohort["max_issue_year_36m"]))
            | ((F.col("term_months") == 60) & (F.col("issue_year") <= cohort["max_issue_year_60m"]))
        ),
    ).withColumn(
        "split",
        F.when(~F.col("is_matured_cohort"), F.lit("not_in_cohort"))
        .when(F.col("issue_year") <= split["train_max_issue_year"], "train")
        .when(F.col("issue_year") == split["validation_issue_year"], "validation")
        .when(F.col("issue_year") == split["test_issue_year"], "test")
        .otherwise("not_in_cohort"),
    )
    return add_bands(frame)


def _band(
    col: str, cuts: list[tuple[float, str]], last: str, null_label: str = "Missing"
) -> F.Column:
    expr = F.when(F.col(col).isNull(), null_label)
    for cut, label in cuts:
        expr = expr.when(F.col(col) < cut, label)
    return expr.otherwise(last)


BANDS = {
    "fico_band": (
        "fico_mid",
        [
            (660, "1. Under 660"),
            (680, "2. 660-679"),
            (700, "3. 680-699"),
            (720, "4. 700-719"),
            (760, "5. 720-759"),
        ],
        "6. 760+",
    ),
    "dti_band": (
        "dti",
        [(10, "1. Under 10"), (20, "2. 10-20"), (30, "3. 20-30"), (40, "4. 30-40")],
        "5. 40+",
    ),
    "utilization_band": (
        "revol_util",
        [
            (20, "1. Under 20%"),
            (40, "2. 20-40%"),
            (60, "3. 40-60%"),
            (80, "4. 60-80%"),
            (100, "5. 80-100%"),
        ],
        "6. Over 100%",
    ),
    "income_band": (
        "annual_inc",
        [
            (40000, "1. Under 40k"),
            (60000, "2. 40-60k"),
            (80000, "3. 60-80k"),
            (120000, "4. 80-120k"),
        ],
        "5. 120k+",
    ),
    "loan_amount_band": (
        "loan_amnt",
        [(5000, "1. Under 5k"), (10000, "2. 5-10k"), (15000, "3. 10-15k"), (25000, "4. 15-25k")],
        "5. 25k+",
    ),
    "employment_band": (
        "emp_years",
        [(1, "1. Under 1 year"), (3, "2. 1-2 years"), (6, "3. 3-5 years"), (10, "4. 6-9 years")],
        "5. 10+ years",
    ),
}


def add_bands(frame: DataFrame) -> DataFrame:
    for name, (col, cuts, last) in BANDS.items():
        frame = frame.withColumn(name, _band(col, cuts, last))
    return frame


SEGMENT_COLUMNS = [
    "grade",
    "term_label",
    "fico_band",
    "dti_band",
    "utilization_band",
    "income_band",
    "loan_amount_band",
    "employment_band",
    "home_ownership",
    "purpose",
    "verification_status",
]


def gold_aggregates(silver: DataFrame) -> dict[str, DataFrame]:
    cohort = silver.filter("is_matured_cohort")

    def rates(frame: DataFrame, *keys: str) -> DataFrame:
        return (
            frame.groupBy(*keys)
            .agg(
                F.count(F.lit(1)).alias("loans"),
                F.sum("default_flag").alias("defaults"),
                F.sum("loan_amnt").alias("loan_amount"),
                F.avg("int_rate").alias("mean_int_rate"),
                F.avg("fico_mid").alias("mean_fico"),
                F.avg("dti").alias("mean_dti"),
            )
            .withColumn("default_rate", F.col("defaults") / F.col("loans"))
            .orderBy(*keys)
        )

    segments = None
    for column in SEGMENT_COLUMNS:
        part = (
            cohort.groupBy(F.lit(column).alias("segment_type"), F.col(column).alias("segment"))
            .agg(F.count(F.lit(1)).alias("loans"), F.sum("default_flag").alias("defaults"))
            .withColumn("default_rate", F.col("defaults") / F.col("loans"))
        )
        segments = part if segments is None else segments.unionByName(part)
    status_by_year = (
        silver.groupBy("issue_year", "term_label", "status_group", "loan_status")
        .agg(F.count(F.lit(1)).alias("loans"), F.sum("loan_amnt").alias("loan_amount"))
        .orderBy("issue_year", "term_label", "loan_status")
    )
    return {
        "gold_vintage_term": rates(cohort, "issue_year", "term_label"),
        "gold_vintage_quarter": rates(cohort.filter("term_months = 36"), "issue_quarter"),
        "gold_grade_year": rates(cohort, "issue_year", "term_label", "grade"),
        "gold_segment_rates": segments.orderBy("segment_type", "segment"),
        "gold_status_by_year": status_by_year,
    }


def write_single_csv(frame: DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.toPandas().to_csv(path, index=False)
