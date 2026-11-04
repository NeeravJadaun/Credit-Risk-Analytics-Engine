# Interview walkthrough (3 to 5 minutes)

Numbers come from `PROJECT_RESULTS.md`. Keep it open. Say first that this is an independent project on public data.

1. **Problem (30s).** Can a monitoring workflow show whether default risk is changing, for whom, and whether it can be trusted?
2. **Data (45s).** Open `PROJECT_RESULTS.md`, "Dataset and row counts". Real LendingClub loans, each ID unique, 33 footer rows caught. Explain why recent vintages are excluded: they have not resolved yet.
3. **Pipeline (45s).** HDFS to PySpark to PostgreSQL. Show `configs/data_contract.yaml`, then the Spark vs pandas reconciliation. Two separate engines agreeing is the point.
4. **Model (45s).** Time-based split and no post-origination fields. State the ROC-AUC plainly and why it is lower than many online results. Show the calibration chart: it ranks better than it estimates levels.
5. **Finding (45s).** Show `06_relative_risk.png`. Layered borrower risk, both ratios, and the large-loan segment that reversed out of time. That reversal is why discovery alone is not enough.
6. **Recommendation (30s).** Add a watchlist indicator and recalibrate. Do not claim any of this reduces defaults, since no intervention was tested.

## Questions to prepare

- **Why does this KPI matter?** Rates within grade reveal a change that a falling share of risky grades would hide. In 2015 mix improved while the rate still rose.
- **How do you know it is correct?** Checksums, row partition controls, four-way reconciliation, recalculation tests, and injected-fault tests.
- **What are the model's limits?** Moderate ranking, under-prediction in 2015, approved loans only, no fairness audit possible.
- **How would you scale it?** See the runbook. Move to a cluster, use incremental loads, and schedule the jobs. On this size, one machine is enough, and Spark did not beat pandas for simple work.
- **What would change with real bank data?** Monthly performance panels, true roll rates, approved and declined applicants with reject inference, formal model governance and validation, regulatory definitions of default, privacy controls.
- **Why isn't Tableau finished?** If you have not built it, say so plainly. The extracts and specification are ready, and it needs the desktop app.
