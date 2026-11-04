# Architecture and data flow

| Step | What happens | Technology | Code |
|---|---|---|---|
| 1. Source | File is checked against its SHA-256 | shell | `spark/ingest_hdfs.sh` |
| 2. Bronze | File is streamed uncompressed into HDFS under a checksum path, so Spark can split it | Hadoop HDFS | `spark/ingest_hdfs.sh`, `scripts/local_hadoop.sh` |
| 3. Validate | All values typed, 18 quarantine rules applied, 7 warning flags counted | PySpark | `src/credit_risk/spark_etl.py`, `configs/data_contract.yaml` |
| 4. Silver | Validated loans plus features, written to HDFS as Parquet partitioned by issue year. Rejected rows are written with reason codes | PySpark, HDFS | `spark/spark_transform.py` |
| 5. Gold | Vintage, grade, segment and status aggregates | PySpark | `spark_etl.gold_aggregates` |
| 6. Cross-check | A separate pandas rule engine recomputes every count from the raw file | pandas, NumPy | `src/credit_risk/contracts.py`, `spark/reconcile_spark_pandas.py` |
| 7. Analysis | Models, findings, strategies, monitoring, indicators | scikit-learn, SciPy | `src/credit_risk/pipeline.py` |
| 8. Warehouse | Star schema rebuilt from SQL files, loaded with COPY, then reconciled with Spark gold | PostgreSQL | `sql/`, `src/credit_risk/warehouse.py` |
| 9. R check | Reads loans from PostgreSQL, recomputes results, compares with Python | R, DBI, dplyr, ggplot2 | `analysis/r/credit_risk_review.R` |
| 10. Reporting | Charts, Excel, HTML, memo, Tableau extracts | Matplotlib, Seaborn, Plotly, openpyxl, LibreOffice | `visualizations.py`, `excel_report.py`, `reporting.py`, `tableau.py` |

## Star schema

Facts and their grain: `fact_loan` (one row per loan at the single data cut), `fact_model_score` (loan and model version), `fact_model_metric` (model, split, metric), `fact_data_quality_check` (batch and check), `fact_monitoring_result` (batch and rule).
Dimensions: `dim_date`, `dim_product`, `dim_borrower_profile`, `dim_purpose`, `dim_geography`, `dim_loan_status`, `dim_ingestion_batch`, `dim_model_version`.

`loan_id` is the primary key of `fact_loan`, and each dimension key is unique, so a star join cannot duplicate loans. `star_join_does_not_fan_out` in `sql/reconciliation.sql` checks this on every load.

## Why both Spark and PostgreSQL

Spark handles the wide raw file, and PostgreSQL serves narrow, indexed reporting queries. On one machine, Spark is not required for a file this size, as `results/benchmarks.json` shows. It is included to show the distributed workflow, and a real cluster would mainly change the Spark master and the HDFS addresses.
