"""Needs the local PostgreSQL build. Skips, visibly, if the warehouse is not available."""

import pandas as pd
import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from credit_risk import warehouse

pytestmark = [pytest.mark.db, pytest.mark.integration]


@pytest.fixture(scope="module")
def engine():
    engine = warehouse.get_engine()
    try:
        with engine.connect() as conn:
            exists = conn.execute(text("SELECT to_regclass('credit_risk.fact_loan')")).scalar()
    except OperationalError:
        pytest.skip("PostgreSQL is not running. Run scripts/local_postgres.sh start.")
    if not exists:
        pytest.skip("Warehouse not loaded.")
    return engine


def test_integrity_checks_pass(engine):
    table = warehouse.run_reconciliation(engine)
    assert len(table) >= 7 and (table["violations"] == 0).all(), table[table["violations"] != 0]


def test_sql_views_match_spark_gold(engine, settings):
    table = warehouse.reconcile_with_spark(engine, settings.path("gold"))
    assert (table["status"] == "PASS").all(), table[table["status"] != "PASS"]


def test_fact_row_count_matches_spark(engine, settings):
    import json

    spark = json.loads((settings.path("results") / "spark_etl.json").read_text())
    rows = warehouse.query(engine, "SELECT COUNT(*) AS n FROM credit_risk.fact_loan")["n"].iloc[0]
    assert rows == spark["silver_rows_read_back_from_hdfs"]


def test_foreign_keys_exist(engine):
    keys = warehouse.query(
        engine,
        "SELECT COUNT(*) AS n FROM information_schema.table_constraints WHERE constraint_schema='credit_risk' AND constraint_type='FOREIGN KEY'",
    )["n"].iloc[0]
    assert keys >= 8


def test_broken_foreign_key_is_rejected(engine):
    with engine.begin() as conn:
        with pytest.raises(Exception, match="foreign key"):
            conn.execute(
                text("INSERT INTO credit_risk.fact_model_score VALUES (-1, 'none', 'test', 0.5)")
            )
            raise AssertionError("no foreign key error raised")


def test_simulated_rows_flagged_in_database(engine):
    table = warehouse.query(
        engine, "SELECT batch_id, simulation FROM credit_risk.fact_monitoring_result"
    )
    assert isinstance(table, pd.DataFrame)
    assert table[table["batch_id"].str.startswith("SIM-")]["simulation"].all()
    assert not table[~table["batch_id"].str.startswith("SIM-")]["simulation"].any()
