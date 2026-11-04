"""Credit default models on real loans, validated on later vintages than they were trained on.

These are estimates from a historical public dataset, not validated regulatory PD models.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.dummy import DummyClassifier
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    confusion_matrix,
    precision_recall_curve,
    roc_auc_score,
    roc_curve,
)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

TARGET = "default_flag"
SEGMENTS = [
    "grade",
    "fico_band",
    "dti_band",
    "utilization_band",
    "income_band",
    "loan_amount_band",
    "employment_band",
    "home_ownership",
    "purpose",
    "issue_quarter",
]


class LeakageError(RuntimeError):
    """A post-origination or outcome column was offered as a model input, or splits overlap in time."""


class ModelSchemaError(RuntimeError):
    """A saved model was loaded against data with an incompatible schema."""


def feature_lists(
    policy: dict[str, Any], use_lc_pricing: bool = True
) -> tuple[list[str], list[str]]:
    drop = set() if use_lc_pricing else set(policy["lc_pricing_features"])
    numeric = [f for f in policy["numeric_features"] if f not in drop]
    return numeric, list(policy["categorical_features"])


def assert_no_leakage(features: list[str], policy: dict[str, Any]) -> None:
    banned = (
        set(policy["post_origination_columns"])
        | set(policy["excluded_by_design"])
        | set(policy["identifier_and_outcome_columns"])
    )
    bad = sorted(set(features) & banned)
    if bad:
        raise LeakageError(f"Columns unavailable or barred at the credit decision: {bad}")


def assert_time_ordered(frame: pd.DataFrame) -> None:
    """Every training loan must be issued before every validation loan, and so on."""
    limits = frame.groupby("split")["issue_date"].agg(["min", "max"])
    if not (
        limits.loc["train", "max"] < limits.loc["validation", "min"]
        and limits.loc["validation", "max"] < limits.loc["test", "min"]
    ):
        raise LeakageError(f"Splits overlap in time: {limits.to_dict()}")
    if frame["loan_id"].duplicated().any():
        raise LeakageError("A loan appears more than once across the splits.")


def _preprocess(numeric: list[str], categorical: list[str], scale: bool) -> ColumnTransformer:
    steps: list[tuple[str, Any]] = [
        ("impute", SimpleImputer(strategy="median", add_indicator=True))
    ]
    if scale:
        steps.append(("scale", StandardScaler()))
    cat = Pipeline(
        [
            ("impute", SimpleImputer(strategy="constant", fill_value="missing")),
            (
                "onehot",
                OneHotEncoder(
                    handle_unknown="infrequent_if_exist", min_frequency=200, sparse_output=False
                ),
            ),
        ]
    )
    return ColumnTransformer(
        [("num", Pipeline(steps), numeric), ("cat", cat, categorical)],
        verbose_feature_names_out=False,
    )


def build_models(numeric: list[str], categorical: list[str], seed: int) -> dict[str, Pipeline]:
    prep = lambda scale: _preprocess(numeric, categorical, scale)  # noqa: E731
    return {
        "prevalence_dummy": Pipeline(
            [("prep", prep(True)), ("model", DummyClassifier(strategy="prior"))]
        ),
        "logistic_regression": Pipeline(
            [("prep", prep(True)), ("model", LogisticRegression(max_iter=1000))]
        ),
        "logistic_regression_balanced": Pipeline(
            [
                ("prep", prep(True)),
                ("model", LogisticRegression(max_iter=1000, class_weight="balanced")),
            ]
        ),
        "random_forest": Pipeline(
            [
                ("prep", prep(False)),
                (
                    "model",
                    RandomForestClassifier(
                        n_estimators=150,
                        max_depth=14,
                        min_samples_leaf=50,
                        max_samples=0.5,
                        n_jobs=4,
                        random_state=seed,
                    ),
                ),
            ]
        ),
        "hist_gradient_boosting": Pipeline(
            [
                ("prep", prep(False)),
                (
                    "model",
                    HistGradientBoostingClassifier(
                        max_iter=300,
                        learning_rate=0.05,
                        max_depth=5,
                        min_samples_leaf=100,
                        random_state=seed,
                    ),
                ),
            ]
        ),
    }


def f1_threshold(y, score) -> float:
    """Threshold with the highest F1. Chosen on validation data only, then frozen."""
    precision, recall, thresholds = precision_recall_curve(y, score)
    with np.errstate(divide="ignore", invalid="ignore"):
        f1 = np.where(precision + recall > 0, 2 * precision * recall / (precision + recall), 0)
    return float(thresholds[int(np.argmax(f1[:-1]))]) if len(thresholds) else 0.5


def evaluate(y, score, threshold: float) -> dict[str, float]:
    y, score = np.asarray(y).astype(int), np.asarray(score)
    flagged = score >= threshold
    tn, fp, fn, tp = confusion_matrix(y, flagged, labels=[0, 1]).ravel()
    precision = tp / (tp + fp) if tp + fp else float("nan")
    recall = tp / (tp + fn) if tp + fn else float("nan")
    fpr, tpr, _ = roc_curve(y, score)
    return {
        "records": len(y),
        "defaults": int(y.sum()),
        "prevalence": float(y.mean()),
        "roc_auc": float(roc_auc_score(y, score)),
        "pr_auc": float(average_precision_score(y, score)),
        "brier": float(brier_score_loss(y, score)),
        "ks": float(np.max(tpr - fpr)),
        "threshold": float(threshold),
        "tp": int(tp),
        "fp": int(fp),
        "tn": int(tn),
        "fn": int(fn),
        "precision": float(precision),
        "recall": float(recall),
        "f1": float(2 * precision * recall / (precision + recall)) if tp else 0.0,
        "flagged_share": float(flagged.mean()),
        "mean_score": float(score.mean()),
    }


def bootstrap_auc(y, score, iterations: int, seed: int) -> dict[str, float]:
    rng = np.random.default_rng(seed)
    y, score = np.asarray(y), np.asarray(score)
    draws = [
        roc_auc_score(y[i], score[i])
        for i in (rng.integers(0, len(y), len(y)) for _ in range(iterations))
    ]
    low, high = np.percentile(draws, [2.5, 97.5])
    return {
        "roc_auc_ci95_low": float(low),
        "roc_auc_ci95_high": float(high),
        "bootstrap_iterations": iterations,
    }


def calibration_table(y, score, bins: int = 10) -> pd.DataFrame:
    frame = pd.DataFrame({"y": np.asarray(y), "score": np.asarray(score)})
    frame["score_decile"] = pd.qcut(
        frame["score"].rank(method="first"), bins, labels=range(1, bins + 1)
    ).astype(int)
    return (
        frame.groupby("score_decile")
        .agg(
            loans=("y", "size"),
            defaults=("y", "sum"),
            mean_predicted=("score", "mean"),
            observed_rate=("y", "mean"),
        )
        .reset_index()
    )


def coefficient_table(pipeline: Pipeline) -> pd.DataFrame:
    names = pipeline.named_steps["prep"].get_feature_names_out()
    coef = pipeline.named_steps["model"].coef_[0]
    table = pd.DataFrame(
        {"feature": names, "coefficient_per_sd_or_category": coef, "odds_ratio": np.exp(coef)}
    )
    return table.reindex(
        table["coefficient_per_sd_or_category"].abs().sort_values(ascending=False).index
    ).reset_index(drop=True)


def segment_performance(frame: pd.DataFrame, threshold: float) -> pd.DataFrame:
    rows = []
    for column in SEGMENTS:
        for segment, part in frame.groupby(column, observed=True):
            y, score = part[TARGET].to_numpy(), part["score"].to_numpy()
            both = len(set(y)) == 2
            flagged = score >= threshold
            positives, negatives = int(y.sum()), int((1 - y).sum())
            rows.append(
                {
                    "segment_type": column,
                    "segment": segment,
                    "loans": len(part),
                    "defaults": positives,
                    "observed_rate": float(y.mean()),
                    "mean_score": float(score.mean()),
                    "calibration_gap": float(score.mean() - y.mean()),
                    "roc_auc": float(roc_auc_score(y, score)) if both else float("nan"),
                    "flagged_share": float(flagged.mean()),
                    "recall": float((flagged & (y == 1)).sum() / positives)
                    if positives
                    else float("nan"),
                    "false_positive_rate": float((flagged & (y == 0)).sum() / negatives)
                    if negatives
                    else float("nan"),
                    "small_sample_warning": len(part) < 500 or positives < 30,
                }
            )
    return pd.DataFrame(rows)


def schema_hash(features: list[str]) -> str:
    return hashlib.sha256(json.dumps(features).encode()).hexdigest()[:16]


def save_bundle(
    path: Path, pipeline: Pipeline, features: list[str], metadata: dict[str, Any]
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(
        {
            "pipeline": pipeline,
            "features": features,
            "schema_hash": schema_hash(features),
            **metadata,
        },
        path,
    )


def load_bundle(path: Path, frame: pd.DataFrame | None = None) -> dict[str, Any]:
    bundle = joblib.load(path)
    if bundle.get("schema_hash") != schema_hash(bundle.get("features", [])):
        raise ModelSchemaError("Stored schema hash does not match the stored feature list.")
    if frame is not None:
        missing = [f for f in bundle["features"] if f not in frame.columns]
        if missing:
            raise ModelSchemaError(f"Data lacks model features: {missing}")
    return bundle


def load_cohort(settings) -> pd.DataFrame:
    frame = pd.read_parquet(
        settings.path("silver") / "loans_parquet", filters=[("is_matured_cohort", "==", True)]
    )
    frame["issue_year"] = frame["issue_year"].astype(int)
    frame["issue_date"] = pd.to_datetime(frame["issue_date"])
    frame[TARGET] = frame[TARGET].astype(int)
    return frame.sort_values("loan_id").reset_index(drop=True)


def train_and_evaluate(frame: pd.DataFrame, settings) -> dict[str, Any]:
    policy, seed, cfg = settings.features, settings.seed, settings.pipeline["modeling"]
    numeric, categorical = feature_lists(policy)
    features = numeric + categorical
    assert_no_leakage(features, policy)
    assert_time_ordered(frame)
    train, valid, test = (frame[frame["split"] == s] for s in ("train", "validation", "test"))

    fitted: dict[str, Pipeline] = {}
    rows, thresholds = [], {}
    for name, pipeline in build_models(numeric, categorical, seed).items():
        pipeline.fit(train[features], train[TARGET])
        fitted[name] = pipeline
        valid_score = pipeline.predict_proba(valid[features])[:, 1]
        thresholds[name] = (
            f1_threshold(valid[TARGET], valid_score) if name != "prevalence_dummy" else 0.5
        )
        rows.append(
            {
                "model": name,
                "split": "train",
                **evaluate(
                    train[TARGET], pipeline.predict_proba(train[features])[:, 1], thresholds[name]
                ),
            }
        )
        rows.append(
            {
                "model": name,
                "split": "validation",
                **evaluate(valid[TARGET], valid_score, thresholds[name]),
            }
        )
        rows.append(
            {
                "model": name,
                "split": "test",
                **evaluate(
                    test[TARGET], pipeline.predict_proba(test[features])[:, 1], thresholds[name]
                ),
            }
        )
    metrics = pd.DataFrame(rows)

    # Selection uses validation AUC only. Test results are reported, never used to choose.
    candidates = metrics[
        (metrics["split"] == "validation") & (metrics["model"] != "prevalence_dummy")
    ]
    selected = candidates.sort_values("roc_auc", ascending=False).iloc[0]["model"]
    pipeline = fitted[selected]

    # Ablation: the selected model family without LendingClub's own grade and rate fields.
    numeric_no_lc, _ = feature_lists(policy, use_lc_pricing=False)
    no_lc = build_models(numeric_no_lc, categorical, seed)[selected]
    no_lc.fit(train[numeric_no_lc + categorical], train[TARGET])
    no_lc_valid = no_lc.predict_proba(valid[numeric_no_lc + categorical])[:, 1]
    no_lc_test = no_lc.predict_proba(test[numeric_no_lc + categorical])[:, 1]
    ablation = {
        "model": f"{selected}_without_lc_pricing",
        "removed_features": policy["lc_pricing_features"],
        **evaluate(test[TARGET], no_lc_test, f1_threshold(valid[TARGET], no_lc_valid)),
    }

    scored = frame.copy()
    scored["score"] = pipeline.predict_proba(scored[features])[:, 1]
    test_scored = scored[scored["split"] == "test"]
    threshold = thresholds[selected]
    test_metrics = (
        metrics[(metrics["model"] == selected) & (metrics["split"] == "test")].iloc[0].to_dict()
    )
    test_metrics.update(
        bootstrap_auc(test_scored[TARGET], test_scored["score"], cfg["bootstrap_iterations"], seed)
    )

    bundle_path = settings.path("models") / f"{selected}-{settings.version}.joblib"
    save_bundle(
        bundle_path,
        pipeline,
        features,
        {
            "model": selected,
            "threshold": threshold,
            "seed": seed,
            "source_sha256": settings.pipeline["source"]["expected_sha256"],
        },
    )
    importance = coefficient_table(fitted["logistic_regression"])
    return {
        "scored": scored,
        "metrics": metrics,
        "selected": selected,
        "threshold": threshold,
        "test_metrics": test_metrics,
        "ablation": ablation,
        "coefficients": importance,
        "calibration": calibration_table(test_scored[TARGET], test_scored["score"]),
        "segment_performance": segment_performance(test_scored, threshold),
        "pipeline": pipeline,
        "features": features,
        "bundle_path": bundle_path,
        "fitted": fitted,
    }
