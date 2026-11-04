# Implementation status

Results are in `PROJECT_RESULTS.md`. Commands are in `README.md`.

| Area | Status | Notes |
|---|---|---|
| Real data at 500K+ | DONE, TESTED | LendingClub, Kaggle mirror |
| HDFS and PySpark | DONE, TESTED | Real single-machine HDFS |
| Data-quality controls | DONE, TESTED | Faults injected for each rule |
| PostgreSQL star schema | DONE, TESTED | Private local cluster |
| Models, time-based | DONE, TESTED | The 0.82 target was not reached |
| Findings | DONE, TESTED | Recalculated in tests and in R |
| R cross-check | DONE, TESTED | Base R glm and own functions |
| Monitoring, real and simulated | DONE, TESTED | |
| Strategy what-if | DONE, TESTED | Hypothetical |
| Charts and Plotly | DONE | Reviewed by eye |
| Excel workbook | PARTIAL | Verified through LibreOffice, not opened in Excel desktop |
| Reports and CLI | DONE, TESTED | |
| Tableau | BLOCKED | Not installed. Extracts and specification ready |
| Docker Compose | NOT VERIFIED | Docker not installed, native services used instead. `docker-compose.hadoop.yml` was not written |
| GitHub Actions | NOT VERIFIED | Nothing pushed |

## Changes from the original guide

- The core dataset moved from UCI Credit Card to LendingClub at the owner's request. It has real dates, so true time-based validation and real vintage monitoring replace much of the planned simulation.
- Synthetic scale expansion was dropped, since the real data is larger than the target and the brief forbids artificial duplication.
- `XLOOKUP` is used for the indicator table, and recalculates correctly in LibreOffice. It requires Excel 2021 or 365.

## Next steps

1. Build Tableau from `docs/tableau_build_steps.md`.
2. Open the workbook in Excel desktop and refresh the PivotTable.
3. Commit, push when ready, and confirm the CI run.
