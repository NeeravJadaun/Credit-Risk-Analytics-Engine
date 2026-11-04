# Resume Evidence

Generated from measured outputs by `python -m credit_risk.cli results`. Independent project on public data, never conducted for RBC or any other institution.

## Resume bullets

**Credit Risk Analytics Engine | Python, PySpark, SQL, Tableau, Hadoop, R**

- Engineered a PySpark ETL on HDFS and a PostgreSQL star schema processing 2,260,668 public LendingClub loans, with 32 automated data-quality checks quarantining 4,346 invalid loans and all counts reconciled across Spark, pandas, and SQL.
- Trained logistic regression, random forest, and gradient boosting default models on 673,515 matured loans with a time-based split and no post-origination fields, reaching 0.685 ROC-AUC on 283,007 unseen 2015 loans; automated Excel and HTML risk reporting across 12 indicators.
- Found borrowers with FICO under 680, DTI 20+, and utilization 60%+ defaulted 3.6x as often as a low-risk comparison group (22.8% vs 6.3%, n=23,963 and 16,100), reproduced in R; recommended a monitoring watchlist after within-grade rates rose 1.84 points.

> **Header warning:** remove `Tableau` from this header unless you build the workbook from `docs/tableau_build_steps.md`. No Tableau workbook exists yet.

## Claim to evidence

| Claim | Implementation | Evidence | How to verify | Status |
|---|---|---|---|---|
| Scale: real, unique loans, no duplication | `spark/spark_transform.py`, `spark/reconcile_spark_pandas.py` | `results/spark_etl.json`, `data/source_manifest.yaml` | `python -m credit_risk.cli reconcile` | Verified |
| PySpark ETL on real HDFS | `src/credit_risk/spark_etl.py`, `spark/ingest_hdfs.sh`, `scripts/local_hadoop.sh` | `results/spark_etl.json` (hdfs:// URIs) | `python -m credit_risk.cli hdfs-ingest && python -m credit_risk.cli spark-etl` | Verified, single-machine HDFS |
| PostgreSQL star schema and SQL views | `sql/`, `src/credit_risk/warehouse.py` | `results/warehouse.json`, `data/gold/reconciliation_sql_spark.csv` | `python -m credit_risk.cli warehouse` | Verified |
| Data-quality checks | `configs/data_contract.yaml`, `src/credit_risk/contracts.py` | `data/gold/dq_layer_checks.csv`, `PROJECT_RESULTS.md` | `pytest tests/test_contracts.py tests/test_spark_parity.py` | Verified |
| Models and held-out ROC-AUC | `src/credit_risk/modeling.py` | `results/model.json`, `data/gold/model_metrics.csv` | `python -m credit_risk.cli analyze` | Verified |
| No post-origination leakage, time-based split | `configs/feature_policy.yaml`, `tests/test_model_leakage.py` | `results/model.json` | `pytest tests/test_model_leakage.py` | Verified |
| Borrower-risk finding | `configs/findings.yaml`, `src/credit_risk/findings.py` | `data/gold/risk_findings.csv`, `dashboards/screenshots/06_relative_risk.png` | `python -m credit_risk.cli analyze` | Verified |
| Reproduced in R | `analysis/r/credit_risk_review.R` | `data/gold/reconciliation_r_python.csv` | `python -m credit_risk.cli r-check` | Verified |
| Excel reporting with PivotTable | `src/credit_risk/excel_report.py`, `scripts/lo_finalize.py` | `reports/excel/credit_risk_portfolio_report.xlsx`, `results/excel_verification.json` | `python -m credit_risk.cli excel` | Verified in LibreOffice, not Excel desktop |
| Automated reporting | `src/credit_risk/reporting.py`, `src/credit_risk/cli.py` | `reports/portfolio_report.html`, `reports/management_memo.md` | `python -m credit_risk.cli run-all --period 2026-11` | Verified |
| Tableau dashboards | `src/credit_risk/tableau.py`, `docs/tableau_build_steps.md` | `data/gold/tableau/` extracts only | Manual build needed | **Not built. Do not claim yet.** |

## Do not claim

- 0.82 ROC-AUC. Measured: 0.685.
- That any strategy reduced defaults. The strategy analysis is retrospective and hypothetical.
- Production scale, cluster operations, or that Spark beat pandas on this data.
- Any link to RBC or another financial institution.

## If asked in an interview

- **Why is your AUC only 0.685?** It uses only origination data and is scored on a later vintage than it was trained on. Payment fields would raise it, but they are only known after the loan starts, so using them would be leakage.
- **Is 3.6x fair?** It compares two ends of the book. Against all other loans the same segment is 1.61x. Both figures are real, and the second is the more cautious one.
- **How do you know it is correct?** Spark, a separate pandas engine, SQL and R each recomputed key numbers, and segments were written down before results were seen, then confirmed on a vintage not used for discovery.
