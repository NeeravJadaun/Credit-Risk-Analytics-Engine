# Credit Risk Analytics Engine

An independent, educational credit-risk analytics project on public LendingClub loan data: HDFS and PySpark ETL, a PostgreSQL star schema, time-based default models, statistical risk findings, an R cross-check, and automated Excel and HTML reporting.

> **Not** a bank system, and not built for or endorsed by RBC or any other institution. Results describe historical public US peer-to-peer loans. Nothing here is lending advice or bank policy. Simulated monitoring evidence is labelled wherever it appears.

## Verified results

Generated from pipeline outputs. Every number, its calculation and its evidence are in [PROJECT_RESULTS.md](PROJECT_RESULTS.md). Resume claims are mapped to evidence in [RESUME_EVIDENCE.md](RESUME_EVIDENCE.md).

<!-- RESULTS:START -->
| Measure | Result |
|---|---|
| Real loans processed | 2,260,668 unique LendingClub loans, no synthetic rows |
| Validated and retained | 2,256,322 loans; 4,379 rows quarantined |
| Analysis cohort | 673,515 matured loans, 99,783 defaults |
| Best model, unseen 2015 loans | hist_gradient_boosting: ROC-AUC 0.685, precision 0.250, recall 0.532, F1 0.340 |
| Layered borrower risk, 2015 | 3.63x a low-risk comparison group (22.8% vs 6.3%) |
| Grade D-G vs A-C, 2015 | 2.27x (28.3% vs 12.4%) |
| Engine reconciliation | Spark vs pandas 62/62, SQL vs Spark 158/158, R vs Python 45/45 |
| Automated tests | 78 passed, 0 failed, 0 skipped |
<!-- RESULTS:END -->

**Not built:** the Tableau workbook. Tableau is not available in the build environment, so validated extracts and a build specification are provided instead.

## Architecture

```text
accepted_2007_to_2018Q4.csv.gz  (checksum verified)
        |  spark/ingest_hdfs.sh: hdfs dfs -put
        v
HDFS bronze -- PySpark: types, 18 quarantine rules, features --> HDFS silver + quarantine
        |                                                            |
        |  separate pandas engine: same rules, counts compared       v
        |                                                     Spark gold aggregates
        v                                                            |
Time-based models, risk findings, monitoring, strategy (Python)      |
        |                                                            v
        +---------------------------> PostgreSQL star schema <-- reconciled to gold
                                              |
             +--------------+-----------------+--------------+
             v              v                 v              v
       R cross-check   Excel workbook   HTML + memo   Tableau extracts
```

See [docs/architecture.md](docs/architecture.md).

## Reproduce

Requires macOS or Linux, 8 GB of free disk, Python 3.11, and:

```bash
brew install uv openjdk@17 hadoop r postgresql@16
```

Get the source data as described in [data/README.md](data/README.md), then:

```bash
make setup
```

```bash
make services
```

```bash
.venv/bin/python -m credit_risk.cli run-all --period 2026-11
```

```bash
make test
```

```bash
.venv/bin/python -m credit_risk.cli results
```

`make stop` shuts down HDFS and PostgreSQL. Both run privately on non-default ports (19000 and 55432) with data in `.local/`. Docker is not used, and `docker-compose.yml` is an untested alternative for PostgreSQL only.

## Main outputs

| Output | Path |
|---|---|
| Portfolio report | `reports/portfolio_report.html` |
| Management memo | `reports/management_memo.md` |
| Excel workbook | `reports/excel/credit_risk_portfolio_report.xlsx` |
| Interactive segment explorer | `dashboards/plotly/segment_explorer.html` |
| Charts | `dashboards/screenshots/` |
| Issue log | `reports/risk_issue_log.csv` |
| Tableau extracts | `data/gold/tableau/` |

## Documentation

[Requirements](docs/requirements.md) · [KPIs](docs/risk_kpis.md) · [Methodology](docs/methodology.md) · [Model card](docs/model_card.md) · [Data dictionary](docs/data_dictionary.md) · [Limitations](docs/assumptions_and_limitations.md) · [Tableau build](docs/tableau_build_steps.md) · [Runbook](docs/operations_runbook.md) · [Interview walkthrough](docs/interview_walkthrough.md) · [Status](docs/implementation_status.md)

## Data license

Source data: LendingClub public loan data through Kaggle, listed there as CC0. See [data/source_manifest.yaml](data/source_manifest.yaml). Code: MIT.
