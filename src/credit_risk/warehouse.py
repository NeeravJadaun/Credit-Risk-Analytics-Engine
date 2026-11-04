"""PostgreSQL star schema: a reproducible rebuild from the SQL files, then reconciliation."""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import Engine, create_engine, text

from .config import ROOT, database_url

SCHEMA = "credit_risk"
TOLERANCE = 1e-9
PROFILE_COLUMNS = [
    "home_ownership",
    "verification_status",
    "application_type",
    "fico_band",
    "dti_band",
    "utilization_band",
    "income_band",
    "employment_band",
    "loan_amount_band",
]
SILVER_COLUMNS = [
    "loan_id",
    "issue_date",
    "term_months",
    "grade",
    "sub_grade",
    "purpose",
    "addr_state",
    "loan_status",
    "status_group",
    "is_resolved",
    "loan_amnt",
    "int_rate",
    "annual_inc",
    "dti",
    "fico_mid",
    "revol_util",
    "default_flag",
    "is_matured_cohort",
    "split",
    *PROFILE_COLUMNS,
]


def get_engine(url: str | None = None) -> Engine:
    return create_engine(url or database_url(), future=True)


def _copy(table, conn, keys, data_iter):
    columns = ", ".join(f'"{k}"' for k in keys)
    with conn.connection.cursor() as cursor:
        with cursor.copy(f"COPY {SCHEMA}.{table.name} ({columns}) FROM STDIN") as copy:
            for row in data_iter:
                copy.write_row(row)


def append(engine: Engine, frame: pd.DataFrame, table: str) -> None:
    frame.astype(object).where(frame.notna(), None).to_sql(
        table,
        engine,
        schema=SCHEMA,
        if_exists="append",
        index=False,
        method=_copy,
        chunksize=100_000,
    )


def _run_script(conn, path: Path) -> None:
    """Run a multi-statement SQL file as it is, with no parameter parsing of % characters."""
    conn.connection.driver_connection.execute(path.read_text())


def initialize(engine: Engine, sql_dir: Path = ROOT / "sql") -> None:
    with engine.begin() as conn:
        conn.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
        _run_script(conn, sql_dir / "001_schema.sql")


def post_load(engine: Engine, sql_dir: Path = ROOT / "sql") -> None:
    """Indexes are built after loading, which is much faster than maintaining them during COPY."""
    with engine.begin() as conn:
        _run_script(conn, sql_dir / "002_indexes.sql")
        _run_script(conn, sql_dir / "003_views.sql")
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.exec_driver_sql(f"ANALYZE {SCHEMA}.fact_loan")
        conn.exec_driver_sql(f"ANALYZE {SCHEMA}.fact_model_score")


def _dimension(frame: pd.DataFrame, columns: list[str], key: str) -> pd.DataFrame:
    dim = frame[columns].drop_duplicates().sort_values(columns).reset_index(drop=True)
    dim.insert(0, key, range(1, len(dim) + 1))
    return dim


def build_star(silver: pd.DataFrame, batch_id: str) -> dict[str, pd.DataFrame]:
    data = silver.copy()
    data["issue_date"] = pd.to_datetime(data["issue_date"])
    data["date_key"] = data["issue_date"].dt.year * 100 + data["issue_date"].dt.month
    dates = (
        data[["date_key", "issue_date"]]
        .drop_duplicates()
        .sort_values("date_key")
        .rename(columns={"issue_date": "month_start"})
    )
    dates["calendar_year"] = dates["month_start"].dt.year
    dates["calendar_quarter"] = (
        dates["month_start"].dt.year.astype(str)
        + "-Q"
        + dates["month_start"].dt.quarter.astype(str)
    )
    product = _dimension(data, ["term_months", "grade", "sub_grade"], "product_key")
    profile = _dimension(data, PROFILE_COLUMNS, "profile_key")
    purpose = _dimension(data, ["purpose"], "purpose_key")
    geography = _dimension(
        data.rename(columns={"addr_state": "state_code"}), ["state_code"], "geography_key"
    )
    status = _dimension(data, ["loan_status", "status_group", "is_resolved"], "status_key")
    fact = (
        data.merge(product, on=["term_months", "grade", "sub_grade"])
        .merge(profile, on=PROFILE_COLUMNS)
        .merge(purpose, on="purpose")
        .merge(geography.rename(columns={"state_code": "addr_state"}), on="addr_state")
        .merge(status, on=["loan_status", "status_group", "is_resolved"])
    )
    if len(fact) != len(data):
        raise AssertionError("Dimension joins changed the fact row count.")
    fact["batch_id"] = batch_id
    fact["default_flag"] = fact["default_flag"].astype("Int64")
    fact = fact[
        [
            "loan_id",
            "batch_id",
            "date_key",
            "product_key",
            "profile_key",
            "purpose_key",
            "geography_key",
            "status_key",
            "loan_amnt",
            "int_rate",
            "annual_inc",
            "dti",
            "fico_mid",
            "revol_util",
            "default_flag",
            "is_matured_cohort",
            "split",
        ]
    ]
    return {
        "dim_date": dates,
        "dim_product": product,
        "dim_borrower_profile": profile,
        "dim_purpose": purpose,
        "dim_geography": geography,
        "dim_loan_status": status,
        "fact_loan": fact,
    }


def run_reconciliation(
    engine: Engine, sql_path: Path = ROOT / "sql" / "reconciliation.sql"
) -> pd.DataFrame:
    statements = [s.strip() for s in sql_path.read_text().split(";\n") if "check_name" in s]
    rows = []
    with engine.begin() as conn:
        conn.execute(text(f"SET search_path TO {SCHEMA}"))
        for statement in statements:
            row = conn.execute(text(re.sub(r"--.*", "", statement).strip())).one()
            rows.append(
                {
                    "check_name": row[0],
                    "violations": int(row[1]),
                    "status": "PASS" if int(row[1]) == 0 else "FAIL",
                }
            )
    return pd.DataFrame(rows)


def query(engine: Engine, sql: str) -> pd.DataFrame:
    return pd.read_sql(text(sql), engine)


def reconcile_with_spark(engine: Engine, gold_dir: Path) -> pd.DataFrame:
    """SQL views must reproduce the rates that Spark wrote to the gold layer."""
    rows = []
    sql_seg = query(engine, f"SELECT * FROM {SCHEMA}.segment_default_rates")
    spark_seg = pd.read_csv(gold_dir / "gold_segment_rates.csv")
    merged = spark_seg.merge(
        sql_seg, on=["segment_type", "segment"], how="outer", suffixes=("_spark", "_sql")
    )
    for _, row in merged.iterrows():
        key = f"segment.{row['segment_type']}.{row['segment']}"
        rows.append(
            {
                "metric": f"{key}.loans",
                "spark": float(row["loans_spark"]),
                "sql": float(row["loans_sql"]),
            }
        )
        rows.append(
            {
                "metric": f"{key}.rate",
                "spark": float(row["default_rate_spark"]),
                "sql": float(row["default_rate_sql"]),
            }
        )
    sql_v = query(engine, f"SELECT * FROM {SCHEMA}.vintage_default_rates")
    spark_v = pd.read_csv(gold_dir / "gold_vintage_term.csv")
    spark_v["term_months"] = spark_v["term_label"].str.extract(r"(\d+)")[0].astype(int)
    mv = spark_v.merge(
        sql_v, on=["issue_year", "term_months"], how="outer", suffixes=("_spark", "_sql")
    )
    for _, row in mv.iterrows():
        key = f"vintage.{int(row['issue_year'])}.{int(row['term_months'])}m"
        rows.append(
            {
                "metric": f"{key}.loans",
                "spark": float(row["loans_spark"]),
                "sql": float(row["loans_sql"]),
            }
        )
        rows.append(
            {
                "metric": f"{key}.rate",
                "spark": float(row["default_rate_spark"]),
                "sql": float(row["default_rate_sql"]),
            }
        )
    table = pd.DataFrame(rows)
    table["abs_difference"] = (table["spark"] - table["sql"]).abs()
    table["status"] = (table["abs_difference"] <= TOLERANCE).map({True: "PASS", False: "FAIL"})
    return table


BENCHMARK_QUERY = (
    f"SELECT p.grade, COUNT(*), AVG(f.default_flag) FROM {SCHEMA}.fact_loan f "
    f"JOIN {SCHEMA}.dim_product p USING (product_key) "
    "WHERE f.is_matured_cohort AND f.date_key BETWEEN 201501 AND 201503 GROUP BY p.grade"
)


def index_benchmark(engine: Engine, repeats: int = 5) -> dict[str, Any]:
    """Time one cohort query with and without its partial index, on the same data and session."""

    def timed(conn) -> tuple[float, str]:
        runs = []
        for _ in range(repeats):
            start = time.perf_counter()
            conn.execute(text(BENCHMARK_QUERY)).fetchall()
            runs.append(time.perf_counter() - start)
        plan = "\n".join(conn.execute(text("EXPLAIN " + BENCHMARK_QUERY)).scalars().all())
        return sorted(runs)[len(runs) // 2], plan

    with engine.connect() as conn:
        conn.execute(text("SET max_parallel_workers_per_gather = 0"))
        with_index, plan_with = timed(conn)
        # Everything runs in one transaction, and the rollback restores both indexes afterwards.
        for index in ["idx_loan_matured", "idx_loan_date"]:
            conn.execute(text(f"DROP INDEX {SCHEMA}.{index}"))
        without_index, plan_without = timed(conn)
        conn.rollback()
    return {
        "query": BENCHMARK_QUERY,
        "repeats": repeats,
        "median_seconds_with_index": with_index,
        "median_seconds_without_index": without_index,
        "speedup_factor": without_index / with_index,
        "plan_with_index": plan_with,
        "plan_without_index": plan_without,
        "parallel_workers": 0,
    }
