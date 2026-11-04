# Data dictionary

Rules for source fields are in `configs/data_contract.yaml`, and model eligibility is in `configs/feature_policy.yaml`.

## Silver and warehouse fields

| Field | Source | Meaning |
|---|---|---|
| loan_id | id | Loan identifier. Non-numeric values are footer rows |
| issue_date, issue_year, issue_quarter | issue_d | Origination month |
| term_months, term_label | term | 36 or 60 |
| loan_amnt, funded_amnt | same | Requested and funded amount (USD) |
| int_rate, installment | same | LendingClub rate (%) and monthly payment |
| grade, sub_grade, sub_grade_rank | same | LendingClub's risk grade. Rank A1 = 1 to G5 = 35 |
| annual_inc | same | Stated annual income |
| dti | same | Debt-to-income. Null allowed, outside 0 to 100 quarantined |
| fico_range_low/high, fico_mid | same | FICO band at origination, and its midpoint |
| revol_bal, revol_util | same | Revolving balance and utilization (%) |
| delinq_2yrs, inq_last_6mths, open_acc, total_acc, pub_rec, mort_acc, pub_rec_bankruptcies | same | Credit-history counts at origination |
| emp_years | emp_length | 0.5 for under 1 year, 10 for 10+ years, else null or years |
| credit_age_months | earliest_cr_line | Months from earliest credit line to issue |
| loan_to_income, installment_to_income | derived | Loan / income, and annual payments / income |
| log_* | derived | log(1 + value) |
| status_group | loan_status | resolved_good, resolved_bad, open_performing, open_delinquent, excluded_policy |
| default_flag | loan_status | 1 Charged Off or Default, 0 Fully Paid, else null |
| is_resolved, is_matured_cohort | derived | Final status; term ended by the data cut |
| split | issue date | train, validation, test, not_in_cohort |
| *_band | derived | Reporting bands, cut-offs in `spark_etl.BANDS` |

## Gold outputs

Files in `data/gold/`: `gold_*` (Spark aggregates), `sql_*` (warehouse views), `model_*`, `risk_findings`, `strategy_*`, `monitoring_*`, `kpi_indicators`, `reconciliation_*`, `dq_layer_checks`.
