# Requirements (simulated stakeholder brief)

> Fictional examples written to structure the work. No real stakeholder, employer or meeting was involved.

## Personas

- **Credit strategy analyst:** needs segment rates they can trust and reproduce.
- **Risk oversight reviewer:** needs limitations, lineage and evidence that controls ran.
- **Product manager:** needs short findings and hypothetical trade-offs in plain terms.

## User stories and acceptance criteria

| # | Story | Acceptance | Verified by |
|---|---|---|---|
| 1 | As an analyst, I can trace any row count back to the source | Checksum, and input = accepted + quarantined | `dq_layer_checks.csv` |
| 2 | As a reviewer, I can see why each rejected row was rejected | Each has reason codes | HDFS quarantine, `test_contracts.py` |
| 3 | As a reviewer, I know the checks would catch real faults | An injected fault triggers each rule | `test_contracts.py` |
| 4 | As an analyst, I get default rates free of censoring bias | Matured vintages only | `test_spark_parity.py` |
| 5 | As an analyst, I can filter segment rates with sample sizes | Filters and hover details | `segment_explorer.html` |
| 6 | As a reviewer, model results are out of time | Test loans issued after all training loans | `test_model_leakage.py` |
| 7 | As a reviewer, no post-origination field reaches a model | Leakage check fails on any barred field | `test_model_leakage.py` |
| 8 | As a manager, I see capture at 5%, 10% and 20% review volume | Table, labelled hypothetical | `strategy_capacity_sensitivity.csv` |
| 9 | As an analyst, alerts fire on faults and stay quiet otherwise | Clean batches have no alerts, injected ones do | `test_monitoring.py`, `monitoring.json` |
| 10 | As a reviewer, simulated evidence is never mistaken for real | Labels on every simulated row and chart | `test_reports.py` |
| 11 | As a reviewer, Python, SQL and R agree | Reconciliation files are all PASS | `reconciliation_*.csv` |
| 12 | As a manager, a failed check blocks the report | Nothing published, status file says FAILED | `test_reports.py` |
| 13 | As an analyst, reports refresh with one command | `run-all` regenerates all outputs | `pipeline_run.json` |

## Simulated change request

*Fictional credit strategy lead:* "Please add a payment-behaviour filter and put data quality up front."
This dataset has no monthly payment history available at origination, so the request was adapted: DTI, FICO, utilization and employment filters went into the segment explorer, and the Data Quality sheet and quality indicators went into the reports.

## Ethical limits

Geography and free-text fields are excluded from models because they can act as proxies for protected characteristics. This does not make the model fair, as other inputs can also act as proxies, and this file has no protected attributes to audit against. Scores must not be used for real credit decisions.
