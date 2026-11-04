# Model card

Metrics: `results/model.json`, summarized in `PROJECT_RESULTS.md`.

- **Purpose:** an educational estimate of default risk on historical public loans, to demonstrate validation and monitoring.
- **Not for:** real credit decisions, pricing, capital or provisioning. It is not a validated regulatory probability-of-default model, and no independent model validation has been done.
- **Selected model:** chosen by validation ROC-AUC among logistic regression, random forest and histogram gradient boosting.
- **Target:** Charged Off or Default vs Fully Paid, over the life of the loan.
- **Training data:** matured LendingClub loans issued through 2013. Saved with its seed, source hash and feature list in `data/models/`; loading checks the schema.
- **Inputs:** see `configs/feature_policy.yaml`. Geographic and free-text fields are excluded.

## Limitations and failure modes

- Performance is moderate. It is better for ranking than for estimating levels.
- It under-predicted the 2015 vintage (see K08), so it needs recalibration before any probability use.
- Grade and interest rate are LendingClub's own assessments, so the model partly repeats them. A model without them is reported for comparison.
- Trained on approved loans only, it says nothing reliable about applicants who would have been declined.
- Discrimination is lower within a single grade, as `data/gold/model_segment_performance.csv` shows.
- Lending conditions, the economy and LendingClub's policies all changed over 2007 to 2015. Later vintages may behave differently.
- No protected attributes are in the data, so a fairness audit was not possible. Excluding proxies does not establish fairness.
