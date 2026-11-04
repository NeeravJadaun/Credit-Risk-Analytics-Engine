# Methodology

**Cohort.** Open loans have no outcome, and among recent vintages only the fast outcomes have resolved, so pooling them would bias rates. Analysis therefore uses loans whose contractual term ended before the data cut: 36-month loans through 2015 and 60-month loans through 2013.

**Split.** Train on loans issued through 2013, select and set thresholds on 2014 (36-month), and test on 2015 (36-month). 60-month loans appear only in training, since later ones had not matured. This is why the test results apply to 36-month loans.

**Features.** Only fields known at the credit decision: see `configs/feature_policy.yaml`. Preprocessing is fitted on training data inside a scikit-learn pipeline. A second model without LendingClub's rate and grade shows how much of the signal comes from them.

**Models.** Prevalence baseline, logistic regression (with and without class weights), random forest and histogram gradient boosting. Selected by validation ROC-AUC. Hyperparameters were set once to sensible defaults and not searched, and no test result was used to change anything.

**Class imbalance.** About 15% of loans default. Probabilities were left unweighted, so they stay interpretable. The decision threshold is chosen to maximize F1 on validation data. Weighted logistic regression is a documented comparison.

**Findings.** Twelve segments were listed in `configs/findings.yaml` before any results were seen. Each is measured on discovery vintages (through 2014) and on the 2015 vintage. A finding is confirmed only if both p-values pass a Bonferroni-adjusted threshold, the 2015 interval excludes 1, and all groups have at least 1,000 loans. Mantel-Haenszel adjustment by issue year and term checks whether vintage or term mix created the result.

**Strategy.** Hypothetical review queues are chosen on 2014 under a capacity limit, then reported on 2015. Because only approved loans have outcomes, results carry selection bias and are not causal.

**Monitoring.** Mode A compares real 2015 quarters with the real 2014 vintage. Mode B partitions 2015 loans into 12 synthetic batches, four of them with injected faults, and is always labelled as simulation. A Kitagawa decomposition separates mix effects from within-grade effects.

**Cross-checks.** Spark vs a separate pandas engine, SQL vs Spark, R vs Python, and Excel formulas vs Python. R recalculates from warehouse rows, with its own functions.
