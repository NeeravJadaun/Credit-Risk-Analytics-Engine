"""Analytical stages. Each reads earlier outputs from disk, so any stage can be re-run alone."""

from __future__ import annotations

import time
from typing import Any

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score

from . import findings, modeling, monitoring, strategy, warehouse
from .audit import Issue, read_json, utc_now, write_json
from .config import Settings

SCORED_COLUMNS = [
    "loan_id",
    "issue_date",
    "issue_year",
    "issue_quarter",
    "term_months",
    "split",
    "default_flag",
    "score",
    "grade",
    "fico_mid",
    "dti",
    "revol_util",
    "annual_inc",
    "loan_amnt",
    "int_rate",
    "emp_years",
    "inq_last_6mths",
    "loan_to_income",
    "fico_band",
    "dti_band",
    "utilization_band",
    "income_band",
    "loan_amount_band",
    "employment_band",
    "home_ownership",
    "purpose",
]
MONITORED_FIELDS = ["dti", "revol_util", "annual_inc", "fico_mid", "loan_amnt"]
DRIFT_FEATURES = ["fico_mid", "dti", "int_rate", "log_annual_inc", "revol_util", "loan_to_income"]
REDUCED_FEATURES = ["fico_mid", "dti", "log_annual_inc", "term_60"]


def _reduced_logit(cohort: pd.DataFrame) -> dict[str, Any]:
    """A small unpenalized logistic regression that R can reproduce from the same rows."""
    data = cohort.assign(term_60=(cohort["term_months"] == 60).astype(int)).dropna(
        subset=REDUCED_FEATURES
    )
    train, test = data[data["split"] == "train"], data[data["split"] == "test"]
    model = LogisticRegression(penalty=None, max_iter=5000, tol=1e-8)
    means, sds = train[REDUCED_FEATURES].mean(), train[REDUCED_FEATURES].std()
    model.fit((train[REDUCED_FEATURES] - means) / sds, train["default_flag"])
    coefficients = model.coef_[0] / sds.to_numpy()
    score = model.predict_proba((test[REDUCED_FEATURES] - means) / sds)[:, 1]
    out = {f"coef_{name}": float(c) for name, c in zip(REDUCED_FEATURES, coefficients, strict=True)}
    out.update(
        {
            "train_rows": len(train),
            "test_rows": len(test),
            "test_auc": float(roc_auc_score(test["default_flag"], score)),
        }
    )
    return out


def analyze(settings: Settings) -> dict[str, Any]:
    started = time.perf_counter()
    gold, results = settings.path("gold"), settings.path("results")
    cohort = modeling.load_cohort(settings)
    model = modeling.train_and_evaluate(cohort, settings)
    scored = model["scored"]
    training_seconds = time.perf_counter() - started

    # Rounded so that floating-point noise from parallel work cannot change repeat-run comparisons.
    model["metrics"].round(10).to_csv(gold / "model_metrics.csv", index=False)
    model["calibration"].to_csv(gold / "model_calibration.csv", index=False)
    model["coefficients"].to_csv(gold / "model_coefficients.csv", index=False)
    model["segment_performance"].to_csv(gold / "model_segment_performance.csv", index=False)
    scored[SCORED_COLUMNS].to_parquet(gold / "scored_cohort.parquet", index=False)

    table = findings.evaluate_segments(scored, settings.findings)
    table.to_csv(gold / "risk_findings.csv", index=False)
    strongest = findings.strongest_finding(table)

    # Strategies: selected on validation, reported on the unseen 2015 vintage.
    cfg = settings.thresholds["strategy"]
    valid, test = scored[scored["split"] == "validation"], scored[scored["split"] == "test"]
    valid_table, test_table = (
        strategy.compare_policies(valid, cfg),
        strategy.compare_policies(test, cfg),
    )
    choice = strategy.select_policy(valid_table, cfg["capacity_constraint"])
    capacity = strategy.capacity_sensitivity(valid_table, test_table, cfg["sensitivity_capacities"])
    costs = strategy.cost_sensitivity(test_table, cfg["cost_scenarios"])
    policy = next(p for p in strategy.build_policies(cfg) if p["policy"] == choice["policy"])
    referrals = strategy.referral_distribution(test, policy)
    test_table.assign(evaluated_on="test").to_csv(gold / "strategy_comparison.csv", index=False)
    valid_table.assign(evaluated_on="validation").to_csv(
        gold / "strategy_validation.csv", index=False
    )
    capacity.to_csv(gold / "strategy_capacity_sensitivity.csv", index=False)
    costs.to_csv(gold / "strategy_cost_sensitivity.csv", index=False)
    referrals.to_csv(gold / "strategy_referral_distribution.csv", index=False)

    # Monitoring mode A: real quarters. Mode B: simulated faults on partitioned held-out loans.
    rules = settings.thresholds["monitoring"]["rules"]
    reference_year = settings.thresholds["monitoring"]["reference_year"]
    real = monitoring.real_vintage_monitoring(scored, rules, reference_year, MONITORED_FIELDS)
    drift = monitoring.feature_drift(scored, reference_year, DRIFT_FEATURES)
    pipeline = model["pipeline"]
    features = model["features"]
    batches = monitoring.build_simulated_batches(
        test,
        settings.pipeline["simulation"],
        settings.seed,
        lambda f: pipeline.predict_proba(f[features])[:, 1],
    )
    test_auc = float(roc_auc_score(test["default_flag"], test["score"]))
    simulated = monitoring.monitor_batches(batches, test, test_auc, rules, MONITORED_FIELDS)
    case = monitoring.investigate(batches, test)
    real.to_csv(gold / "monitoring_real_quarters.csv", index=False)
    drift.to_csv(gold / "monitoring_feature_drift.csv", index=False)
    simulated.to_csv(gold / "monitoring_simulated_batches.csv", index=False)
    case.to_csv(gold / "monitoring_case_study.csv", index=False)
    real_gap = monitoring.mix_rate_decomposition(test, valid, "grade")

    reduced = _reduced_logit(scored.assign(log_annual_inc=np.log1p(scored["annual_inc"])))
    pd.DataFrame(
        [
            {"metric": "cohort_loans", "python": len(scored)},
            {"metric": "cohort_defaults", "python": int(scored["default_flag"].sum())},
            {"metric": "cohort_default_rate", "python": float(scored["default_flag"].mean())},
            *[
                {"metric": f"finding.{r['name']}.oot_{k}", "python": float(r[f"oot_{k}"])}
                for _, r in table.iterrows()
                for k in [
                    "segment_loans",
                    "comparison_loans",
                    "segment_default_rate",
                    "comparison_default_rate",
                    "relative_risk",
                    "rr_ci95_low",
                    "rr_ci95_high",
                ]
            ],
            *[{"metric": f"reduced_logit.{k}", "python": float(v)} for k, v in reduced.items()],
        ]
    ).to_csv(gold / "python_reference_for_r.csv", index=False)

    ablation = model["ablation"]
    metrics_long = model["metrics"]
    write_json(
        results / "model.json",
        {
            "generated_at": utc_now(),
            "training_seconds_all_models": training_seconds,
            "selected_model": model["selected"],
            "selection_rule": "highest validation ROC-AUC; test set never used to choose",
            "decision_threshold_selected_on_validation": model["threshold"],
            "split_definition": settings.pipeline["split"],
            "test_metrics": model["test_metrics"],
            "ablation_without_lc_pricing": ablation,
            "aspirational_auc": settings.pipeline["modeling"]["aspirational_auc"],
            "all_models": metrics_long.to_dict(orient="records"),
            "feature_count_before_encoding": len(features),
            "features": features,
            "split_rows": scored["split"].value_counts().to_dict(),
            "split_default_rate": scored.groupby("split")["default_flag"].mean().to_dict(),
            "split_issue_dates": {
                k: [str(v["min"].date()), str(v["max"].date())]
                for k, v in scored.groupby("split")["issue_date"]
                .agg(["min", "max"])
                .to_dict("index")
                .items()
            },
        },
    )
    write_json(
        results / "findings.json",
        {
            "strongest_by_prespecified_rule": strongest.to_dict(),
            "all_segments": table.to_dict(orient="records"),
            "alpha_bonferroni": float(table["bonferroni_alpha"].iloc[0]),
            "segments_tested": len(table),
            "segments_confirmed": int(table["confirmed"].sum()),
        },
    )
    write_json(
        results / "strategy.json",
        {
            "selected_policy_on_validation": choice["policy"],
            "constraint_max_referred": cfg["capacity_constraint"],
            "test_table": test_table.to_dict(orient="records"),
            "capacity_sensitivity": capacity.to_dict(orient="records"),
        },
    )
    write_json(
        results / "monitoring.json",
        {
            "real_alerts": real[real["severity"].isin(["WARN", "CRITICAL"])]
            .drop(columns=["triage"])
            .to_dict(orient="records"),
            "real_rows": len(real),
            "real_gap_2015_vs_2014_by_grade": real_gap,
            "simulated_alerts": simulated[simulated["severity"].isin(["WARN", "CRITICAL"])]
            .drop(columns=["triage"])
            .to_dict(orient="records"),
            "simulated_rows": len(simulated),
            "clean_batches_with_alerts": int(
                simulated[
                    (simulated["scenario"] == "clean_partition")
                    & simulated["severity"].isin(["WARN", "CRITICAL"])
                ]["batch_id"].nunique()
            ),
            "clean_batches": int(
                simulated[simulated["scenario"] == "clean_partition"]["batch_id"].nunique()
            ),
            "case_study": case.to_dict(orient="records"),
            "max_feature_psi": drift.sort_values("psi", ascending=False).iloc[0].to_dict(),
        },
    )
    compute_kpis(settings, scored, model["test_metrics"], real)
    write_issue_log(settings, simulated, case, real)
    return {"seconds": time.perf_counter() - started}


def compute_kpis(
    settings: Settings, scored: pd.DataFrame, test_metrics: dict[str, Any], real: pd.DataFrame
) -> pd.DataFrame:
    gold = settings.path("gold")
    spark = read_json(settings.path("results") / "spark_etl.json")["counts"]
    status = pd.read_csv(gold / "gold_status_by_year.csv")
    y14, y15 = (
        scored[(scored["issue_year"] == y) & (scored["term_months"] == 36)] for y in (2014, 2015)
    )
    low, high = findings.wilson_interval(scored["default_flag"].sum(), len(scored))
    open_loans = status[status["status_group"].isin(["open_performing", "open_delinquent"])][
        "loans"
    ].sum()
    delinquent = status[status["status_group"] == "open_delinquent"]["loans"].sum()
    decile = test_metrics["records"] // 10
    test = scored[scored["split"] == "test"].sort_values(
        ["score", "loan_id"], ascending=[False, True]
    )
    watch = (y15["fico_mid"] < 680) & (y15["dti"] >= 20) & (y15["revol_util"] >= 60)
    score_psi = monitoring.psi(y14["score"], y15["score"])
    rows = [
        (
            "K01",
            "Observed default rate",
            "Matured loans",
            scored["default_flag"].mean(),
            f"{int(scored['default_flag'].sum())} of {len(scored)}",
            f"95% CI {float(low):.2%} to {float(high):.2%}",
        ),
        (
            "K02",
            "Default-rate change, 2015 vs 2014 vintage",
            "36-month matured loans",
            y15["default_flag"].mean() - y14["default_flag"].mean(),
            f"{len(y15)} and {len(y14)} loans",
            "Same term, both fully matured",
        ),
        (
            "K03",
            "Delinquent share of open loans",
            "All open loans at data cut",
            delinquent / open_loans,
            f"{int(delinquent)} of {int(open_loans)}",
            "One snapshot, not a roll rate",
        ),
        (
            "K04",
            "Grade D-G share of volume, 2015",
            "36-month matured loans",
            y15["grade"].isin(list("DEFG")).mean(),
            f"{int(y15['grade'].isin(list('DEFG')).sum())} of {len(y15)}",
            f"2014 was {y14['grade'].isin(list('DEFG')).mean():.1%}",
        ),
        (
            "K05",
            "FICO under 680 share, 2015",
            "36-month matured loans",
            (y15["fico_mid"] < 680).mean(),
            f"{int((y15['fico_mid'] < 680).sum())} of {len(y15)}",
            f"2014 was {(y14['fico_mid'] < 680).mean():.1%}",
        ),
        (
            "K06",
            "Mean debt-to-income, 2015",
            "36-month matured loans",
            y15["dti"].mean(),
            f"{int(y15['dti'].notna().sum())} loans",
            f"2014 was {y14['dti'].mean():.2f}",
        ),
        (
            "K07",
            "Out-of-time ROC-AUC",
            "2015 vintage, unseen",
            test_metrics["roc_auc"],
            f"{test_metrics['records']} loans",
            f"95% bootstrap CI {test_metrics['roc_auc_ci95_low']:.4f} to {test_metrics['roc_auc_ci95_high']:.4f}",
        ),
        (
            "K08",
            "Calibration gap (mean score minus observed)",
            "2015 vintage, unseen",
            test_metrics["mean_score"] - test_metrics["prevalence"],
            f"{test_metrics['records']} loans",
            "Negative means the model under-predicts",
        ),
        (
            "K09",
            "Score PSI, 2015 vs 2014",
            "36-month matured loans",
            score_psi,
            f"{len(y15)} vs {len(y14)} loans",
            "PSI cut-offs are a heuristic",
        ),
        (
            "K10",
            "Default capture in top score decile",
            "2015 vintage, unseen",
            test.head(decile)["default_flag"].sum() / test["default_flag"].sum(),
            f"{int(test.head(decile)['default_flag'].sum())} of {int(test['default_flag'].sum())}",
            "Retrospective, not causal",
        ),
        (
            "K11",
            "Layered-risk watchlist share, 2015",
            "FICO under 680, DTI 20+, utilization 60%+",
            watch.mean(),
            f"{int(watch.sum())} of {len(y15)}",
            f"Observed default rate {y15[watch]['default_flag'].mean():.1%}",
        ),
        (
            "K12",
            "Data quarantine rate",
            "All input rows",
            spark["quarantined_rows"] / spark["input_rows"],
            f"{spark['quarantined_rows']} of {spark['input_rows']}",
            "Includes 33 footer rows",
        ),
    ]
    table = pd.DataFrame(
        rows, columns=["kpi_id", "indicator", "population", "value", "basis", "note"]
    )
    table["value"] = table["value"].astype(float)
    table["evidence_type"] = "real_source"
    table.to_csv(gold / "kpi_indicators.csv", index=False)
    return table


def write_issue_log(
    settings: Settings, simulated: pd.DataFrame, case: pd.DataFrame, real: pd.DataFrame
) -> None:
    spark = read_json(settings.path("results") / "spark_etl.json")["counts"]
    n = spark["input_rows"]
    issues: list[Issue] = []

    def add(
        check,
        severity,
        variables,
        count,
        denominator,
        description,
        fix,
        status="OPEN",
        evidence="real_source",
    ):
        issues.append(
            Issue(
                f"ISS-{len(issues) + 1:03d}",
                check,
                severity,
                variables,
                int(count),
                int(denominator),
                description,
                fix,
                status,
                evidence,
            )
        )

    for rule in settings.contract["quarantine_rules"]:
        if spark[rule["id"]]:
            add(
                rule["id"],
                "WARN",
                rule["category"],
                spark[rule["id"]],
                n,
                rule["description"],
                "Quarantined with a reason code. Review with the data owner before any reload.",
                "CONTAINED",
            )
    for flag in settings.contract["warning_flags"]:
        if spark[flag["id"]]:
            add(
                flag["id"],
                "INFO",
                "see description",
                spark[flag["id"]],
                n - spark["quarantined_rows"],
                flag["description"],
                "Kept and documented.",
                "ACCEPTED",
            )
    add(
        "member_id_empty",
        "WARN",
        "member_id",
        n,
        n,
        "member_id is empty on every row, so repeat borrowers cannot be identified. Uniqueness holds per loan, not per borrower.",
        "Treat each loan as one observation. Confirm with a data owner if a borrower key exists.",
    )
    add(
        "right_censoring",
        "WARN",
        "loan_status",
        spark["status_open_performing"] + spark["status_open_delinquent"],
        n,
        "Open loans have no final outcome, and recent vintages resolve only fast. Pooled rates across all vintages would be biased.",
        "Outcome analysis is restricted to matured vintages.",
        "CONTAINED",
    )
    for _, row in real[real["severity"].isin(["WARN", "CRITICAL"])].iterrows():
        add(
            f"real_monitoring_{row['rule']}",
            row["severity"],
            row["metric"],
            0,
            0,
            f"{row['provenance']}: {row['metric']} = {row['value']:.4f}.",
            row["triage"],
        )
    for _, row in simulated[simulated["severity"].isin(["WARN", "CRITICAL"])].iterrows():
        add(
            f"sim_{row['batch_id']}_{row['rule']}",
            row["severity"],
            row["metric"],
            0,
            0,
            f"{monitoring.SIMULATION_BANNER} {row['batch_id']} ({row['scenario']}): {row['metric']} = {row['value']:.4f}.",
            row["triage"],
            "CLOSED_TEST",
            "simulation",
        )
    pd.DataFrame([i.as_dict() for i in issues]).to_csv(
        settings.root / "reports" / "risk_issue_log.csv", index=False
    )


def load_warehouse(settings: Settings, engine=None) -> dict[str, Any]:
    started = time.perf_counter()
    engine = engine or warehouse.get_engine()
    gold, results = settings.path("gold"), settings.path("results")
    spark = read_json(results / "spark_etl.json")
    pandas_result = read_json(results / "pandas_etl.json")
    silver = pd.read_parquet(
        settings.path("silver") / "loans_parquet", columns=[c for c in warehouse.SILVER_COLUMNS]
    )
    counts = spark["counts"]
    batch_id = "LC-" + settings.pipeline["source"]["expected_sha256"][:12]
    star = warehouse.build_star(silver, batch_id)
    warehouse.initialize(engine)
    warehouse.append(
        engine,
        pd.DataFrame(
            [
                {
                    "batch_id": batch_id,
                    "source_filename": "accepted_2007_to_2018Q4.csv.gz",
                    "source_sha256": settings.pipeline["source"]["expected_sha256"],
                    "loaded_at": utc_now(),
                    "input_rows": counts["input_rows"],
                    "accepted_rows": counts["input_rows"] - counts["quarantined_rows"],
                    "quarantined_rows": counts["quarantined_rows"],
                    "processing_version": settings.version,
                }
            ]
        ),
        "dim_ingestion_batch",
    )
    for table in [
        "dim_date",
        "dim_product",
        "dim_borrower_profile",
        "dim_purpose",
        "dim_geography",
        "dim_loan_status",
        "fact_loan",
    ]:
        warehouse.append(engine, star[table], table)

    model = read_json(results / "model.json")
    version = f"{model['selected_model']}-{settings.version}"
    warehouse.append(
        engine,
        pd.DataFrame(
            [
                {
                    "model_version": version,
                    "model_type": model["selected_model"],
                    "feature_count": model["feature_count_before_encoding"],
                    "seed": settings.seed,
                    "source_sha256": settings.pipeline["source"]["expected_sha256"],
                    "decision_threshold": model["decision_threshold_selected_on_validation"],
                }
            ]
        ),
        "dim_model_version",
    )
    scored = pd.read_parquet(gold / "scored_cohort.parquet", columns=["loan_id", "split", "score"])
    warehouse.append(
        engine,
        scored[scored["split"].isin(["validation", "test"])].assign(model_version=version),
        "fact_model_score",
    )
    metrics = (
        pd.read_csv(gold / "model_metrics.csv")
        .melt(id_vars=["model", "split"], var_name="metric", value_name="value")
        .rename(columns={"model": "model_name"})
    )
    warehouse.append(engine, metrics, "fact_model_metric")

    checks = []
    for rule in settings.contract["quarantine_rules"]:
        checks.append(
            {
                "batch_id": batch_id,
                "check_name": rule["id"],
                "check_type": "quarantine_rule",
                "category": rule["category"],
                "status": "PASS",
                "affected_rows": counts[rule["id"]],
                "description": rule["description"],
            }
        )
    for flag in settings.contract["warning_flags"]:
        checks.append(
            {
                "batch_id": batch_id,
                "check_name": flag["id"],
                "check_type": "warning_flag",
                "category": "profile",
                "status": "WARN" if counts[flag["id"]] else "PASS",
                "affected_rows": counts[flag["id"]],
                "description": flag["description"],
            }
        )
    for layer in pandas_result["layer_checks"]:
        checks.append(
            {
                "batch_id": batch_id,
                "check_name": layer["check_name"],
                "check_type": "layer_control",
                "category": "reconciliation",
                "status": layer["status"],
                "affected_rows": None,
                "description": f"observed {layer['observed']}, expected {layer['expected']}",
            }
        )
    warehouse.append(
        engine, pd.DataFrame(checks).astype({"affected_rows": "Int64"}), "fact_data_quality_check"
    )

    real = pd.read_csv(gold / "monitoring_real_quarters.csv")
    sim = pd.read_csv(gold / "monitoring_simulated_batches.csv")
    columns = [
        "batch_id",
        "rule",
        "value",
        "warn_level",
        "critical_level",
        "severity",
        "simulation",
        "provenance",
    ]
    warehouse.append(
        engine,
        pd.concat([real[columns], sim[columns]], ignore_index=True),
        "fact_monitoring_result",
    )
    warehouse.post_load(engine)

    integrity = warehouse.run_reconciliation(engine)
    against_spark = warehouse.reconcile_with_spark(engine, gold)
    integrity.to_csv(gold / "reconciliation_sql_integrity.csv", index=False)
    against_spark.to_csv(gold / "reconciliation_sql_spark.csv", index=False)
    for view in [
        "portfolio_summary",
        "vintage_default_rates",
        "segment_default_rates",
        "delinquency_by_vintage",
        "model_score_deciles",
        "quality_summary",
    ]:
        warehouse.query(engine, f"SELECT * FROM {warehouse.SCHEMA}.{view}").round(10).to_csv(
            gold / f"sql_{view}.csv", index=False
        )
    benchmark = warehouse.index_benchmark(engine)
    tables = {
        t: int(
            warehouse.query(engine, f"SELECT COUNT(*) AS n FROM {warehouse.SCHEMA}.{t}")["n"].iloc[
                0
            ]
        )
        for t in [
            "fact_loan",
            "fact_model_score",
            "fact_model_metric",
            "fact_data_quality_check",
            "fact_monitoring_result",
            "dim_date",
            "dim_product",
            "dim_borrower_profile",
            "dim_purpose",
            "dim_geography",
            "dim_loan_status",
            "dim_ingestion_batch",
            "dim_model_version",
        ]
    }
    size = warehouse.query(
        engine,
        f"SELECT pg_size_pretty(SUM(pg_total_relation_size(c.oid))) AS s FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = '{warehouse.SCHEMA}' AND c.relkind = 'r'",
    )["s"].iloc[0]
    result = {
        "table_rows": tables,
        "storage_size": size,
        "seconds": time.perf_counter() - started,
        "integrity_checks": integrity.to_dict(orient="records"),
        "integrity_failed": int((integrity["status"] == "FAIL").sum()),
        "sql_spark_values_compared": len(against_spark),
        "sql_spark_values_matching": int((against_spark["status"] == "PASS").sum()),
        "max_abs_difference": float(against_spark["abs_difference"].max()),
        "index_benchmark": benchmark,
    }
    write_json(results / "warehouse.json", result)
    return result
