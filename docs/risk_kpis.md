# KPI and calculation definitions

Current values: `data/gold/kpi_indicators.csv`.

## Twelve reporting indicators

| ID | Indicator | Formula | Population |
|---|---|---|---|
| K01 | Observed default rate | defaults / loans | Matured cohort |
| K02 | Vintage change | rate(2015) - rate(2014) | 36-month matured |
| K03 | Delinquent share of open | open delinquent / (open performing + open delinquent) | All validated open loans |
| K04 | Grade D-G share | grade D-G loans / loans | 2015, 36-month |
| K05 | FICO under 680 share | loans with mid FICO under 680 / loans | 2015, 36-month |
| K06 | Mean DTI | mean(dti) | 2015, 36-month, non-null |
| K07 | Out-of-time ROC-AUC | Rank statistic of score vs outcome | 2015 |
| K08 | Calibration gap | mean(score) - mean(outcome) | 2015 |
| K09 | Score PSI | See below | 2015 vs 2014, 36-month |
| K10 | Top-decile capture | defaults in top 10% of scores / all defaults | 2015 |
| K11 | Layered watchlist share | FICO under 680, DTI 20+, utilization 60%+ / loans | 2015, 36-month |
| K12 | Quarantine rate | quarantined rows / input rows | All input |

## Other definitions

| Term | Definition | Caveat |
|---|---|---|
| Default | `loan_status` is Charged Off or Default | Fully Paid is 0, and open loans are unlabelled |
| Precision / recall / F1 | TP/(TP+FP), TP/(TP+FN), harmonic mean | Depend on the threshold chosen on validation data |
| Brier score | mean((score - outcome)^2) | Held-out data |
| Relative risk | segment default rate / comparison default rate | Association. Depends on the comparison chosen |
| Flagged share / capture | flagged / scored; defaults flagged / all defaults | Hypothetical and retrospective |
| PSI | sum((current - base) x ln(current / base)) over baseline deciles, smoothing empty bins at 0.0001 | Cut-offs of 0.10 and 0.25 are a heuristic |
| Missingness | nulls / rows, per field | Reported per flag |
| Duplicate rate | later occurrences of an ID / input rows | Per loan. `member_id` is empty, so per-borrower is unknown |
| Reconciliation | input = accepted + quarantined, and engines agree | Hard stop on failure |
