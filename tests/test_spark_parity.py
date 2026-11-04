"""The same dirty data must produce identical counts from the Spark and pandas engines."""

import pytest

from conftest import dirty_frame, frame, good_row
from credit_risk import contracts, spark_etl


@pytest.fixture(scope="module")
def spark(settings):
    session = spark_etl.spark_session(settings, "tests", master="local[1]")
    yield session
    session.stop()


def _spark_frame(spark, pdf, settings):
    columns = settings.contract["required_columns"]
    rows = [
        tuple(None if v is None else str(v) for v in r)
        for r in pdf[columns]
        .astype(object)
        .where(pdf[columns].notna(), None)
        .itertuples(index=False)
    ]
    return spark.createDataFrame(rows, schema=", ".join(f"`{c}` string" for c in columns))


def test_engines_agree_on_every_count(spark, settings):
    data = dirty_frame()
    checked = spark_etl.apply_contract(
        spark_etl.to_typed(_spark_frame(spark, data, settings)), settings.contract
    )
    spark_counts = spark_etl.quality_counts(checked, settings.contract)
    _, _, pandas_counts = contracts.validate(data, settings.contract)
    assert spark_counts == pandas_counts


def _features(spark, settings, *rows):
    checked = spark_etl.apply_contract(
        spark_etl.to_typed(_spark_frame(spark, frame(*rows), settings)), settings.contract
    )
    return (
        spark_etl.add_features(
            checked.filter("NOT is_quarantined"), settings.contract, settings.pipeline
        )
        .toPandas()
        .set_index("loan_id")
    )


def test_features_and_cohort_rules(spark, settings):
    out = _features(
        spark,
        settings,
        good_row(1, loan_status="Charged Off", issue_d="Jun-2015"),
        good_row(2, loan_status="Fully Paid", issue_d="Jun-2014"),
        good_row(3, loan_status="Fully Paid", issue_d="Jun-2012"),
        good_row(4, loan_status="Current", issue_d="Jun-2015"),
        good_row(5, loan_status="Fully Paid", issue_d="Jun-2016"),
        good_row(
            6, loan_status="Fully Paid", issue_d="Jun-2014", term=" 60 months", installment="250"
        ),
        good_row(7, loan_status="Late (31-120 days)", issue_d="Jun-2017"),
    )
    assert len(out) == 7
    assert out.loc[1, "default_flag"] == 1 and out.loc[1, "split"] == "test"
    assert out.loc[2, "default_flag"] == 0 and out.loc[2, "split"] == "validation"
    assert out.loc[3, "split"] == "train"
    assert out.loc[4, "split"] == "not_in_cohort" and not out.loc[4, "is_resolved"]
    assert not out.loc[5, "is_matured_cohort"], "A 2016 36-month loan has not matured."
    assert not out.loc[6, "is_matured_cohort"], "A 2014 60-month loan has not matured."
    assert out.loc[7, "status_group"] == "open_delinquent"
    assert out.loc[1, "fico_mid"] == 702 and out.loc[1, "loan_to_income"] == pytest.approx(
        10000 / 60000
    )
    assert (
        out.loc[1, "sub_grade_rank"] == 8
        and out.loc[1, "dti_band"] == "2. 10-20"
        and out.loc[1, "emp_years"] == 5
    )


def test_employment_length_parsing(spark, settings):
    out = _features(
        spark,
        settings,
        good_row(1, emp_length="< 1 year"),
        good_row(2, emp_length="10+ years"),
        good_row(3, emp_length=None),
        good_row(4, emp_length="1 year"),
    )
    assert (
        out.loc[1, "emp_years"] == 0.5
        and out.loc[2, "emp_years"] == 10
        and out.loc[4, "emp_years"] == 1
    )
    assert out.loc[3, "employment_band"] == "Missing"
