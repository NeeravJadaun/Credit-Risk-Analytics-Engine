"""Command line entry point. Each stage can run alone, and run-all runs them in order and stops on failure."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from . import excel_report, pipeline, reporting, results, tableau, visualizations
from .audit import git_state, machine_facts, utc_now, write_json
from .config import ROOT, load_settings

FINGERPRINT_EXCLUDES = {"reconciliation_r_python.csv"}


def _run(command: list[str], **env) -> None:
    done = subprocess.run(command, cwd=ROOT, env={**os.environ, **env})
    if done.returncode != 0:
        raise RuntimeError(
            f"Stage command failed with exit code {done.returncode}: {' '.join(command)}"
        )


def stage_hdfs() -> None:
    _run(["bash", "spark/ingest_hdfs.sh"])


def stage_spark() -> None:
    _run([sys.executable, "spark/spark_transform.py"])


def stage_reconcile() -> None:
    _run([sys.executable, "spark/reconcile_spark_pandas.py"])
    data = json.loads((ROOT / "results" / "pandas_etl.json").read_text())
    failed = [c["check_name"] for c in data["layer_checks"] if c["status"] == "FAIL"]
    if failed:
        raise RuntimeError(f"Hard stop. Failed layer controls: {failed}")


def stage_r() -> None:
    _run(["Rscript", "analysis/r/credit_risk_review.R"], R_LIBS_USER=str(ROOT / ".local" / "Rlib"))


def metrics_fingerprint(gold: Path) -> str:
    """Hash gold metric tables to compare repeat runs. Nothing in these tables holds a timestamp."""
    digest = hashlib.sha256()
    for path in sorted(gold.glob("*.csv")):
        if path.name not in FINGERPRINT_EXCLUDES:
            digest.update(path.name.encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def run_all(period: str, output: Path, skip_benchmark: bool = False) -> dict:
    settings = load_settings()
    previous_file = settings.path("results") / "pipeline_run.json"
    previous = (
        json.loads(previous_file.read_text()).get("metrics_fingerprint")
        if previous_file.exists()
        else None
    )
    timings: dict[str, float] = {}
    stages = [
        ("hdfs_ingest", stage_hdfs),
        ("spark_etl", stage_spark),
        ("pandas_reconcile", stage_reconcile),
        ("analysis_models_findings_monitoring", lambda: pipeline.analyze(settings)),
        ("warehouse_load_and_reconcile", lambda: pipeline.load_warehouse(settings)),
        ("r_cross_check", stage_r),
        ("charts", lambda: visualizations.build_all(settings)),
        ("tableau_extracts", lambda: tableau.export(settings)),
        ("excel_workbook", lambda: excel_report.build_and_verify(settings, period, output)),
        ("reports", lambda: reporting.publish(settings, output, period)),
    ]
    if not skip_benchmark:
        stages.insert(6, ("benchmarks", lambda: _run([sys.executable, "spark/benchmark.py"])))
    started = time.perf_counter()
    for name, function in stages:
        begin = time.perf_counter()
        print(f"--- {name}", flush=True)
        function()
        timings[name] = time.perf_counter() - begin
    fingerprint = metrics_fingerprint(settings.path("gold"))
    summary = {
        "previous_run_fingerprint": previous,
        "matches_previous_run": previous == fingerprint,
        "period": period,
        "finished_at": utc_now(),
        "status": "PUBLISHED",
        "seconds_by_stage": timings,
        "total_seconds": time.perf_counter() - started,
        "metrics_fingerprint": fingerprint,
        "machine": machine_facts(),
        "git": git_state(),
        "seed": settings.seed,
        "source_sha256": settings.pipeline["source"]["expected_sha256"],
        "processing_version": settings.version,
    }
    write_json(settings.path("results") / "pipeline_run.json", summary)
    write_json(output / "run_manifests" / f"{period}.json", summary)
    results.generate(settings)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="credit_risk", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in (
        "hdfs-ingest",
        "spark-etl",
        "reconcile",
        "analyze",
        "warehouse",
        "r-check",
        "charts",
        "tableau",
        "results",
        "benchmark",
    ):
        sub.add_parser(name)
    for name in ("excel", "report", "run-all"):
        command = sub.add_parser(name)
        command.add_argument("--period", default=time.strftime("%Y-%m"))
        command.add_argument("--output", type=Path, default=ROOT / "reports")
        if name == "run-all":
            command.add_argument("--skip-benchmark", action="store_true")
    args = parser.parse_args(argv)
    settings = load_settings()
    try:
        if args.command == "run-all":
            print(json.dumps(run_all(args.period, args.output, args.skip_benchmark), indent=2))
        elif args.command == "excel":
            excel_report.build_and_verify(settings, args.period, args.output)
        elif args.command == "report":
            reporting.publish(settings, args.output, args.period)
        else:
            {
                "hdfs-ingest": stage_hdfs,
                "spark-etl": stage_spark,
                "reconcile": stage_reconcile,
                "analyze": lambda: pipeline.analyze(settings),
                "warehouse": lambda: pipeline.load_warehouse(settings),
                "r-check": stage_r,
                "charts": lambda: visualizations.build_all(settings),
                "tableau": lambda: tableau.export(settings),
                "results": lambda: results.generate(settings),
                "benchmark": lambda: _run([sys.executable, "spark/benchmark.py"]),
            }[args.command]()
    except Exception as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        if args.command == "run-all":
            write_json(
                args.output / "LATEST_STATUS.json",
                {
                    "status": "FAILED",
                    "period": args.period,
                    "error": str(exc),
                    "written_at": utc_now(),
                },
            )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
