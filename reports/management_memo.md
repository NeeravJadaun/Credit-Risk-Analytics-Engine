# Credit Risk Management Memo

**Report period:** 2026-11 (the run period, not a data month). **Generated:** by `python -m credit_risk.cli report`.
Independent educational project on public data. Not prepared for, or endorsed by, any financial institution. Nothing here is lending advice or bank policy.

## Executive question and scope

Is default risk changing across vintages, which borrower characteristics are associated with it, and what should a risk team monitor next?
Scope: 673,515 matured LendingClub loans (36-month issued through 2015, 60-month through 2013).

## Source and limitations

- Public LendingClub accepted-loan file, distributed through a Kaggle mirror (CC0 as listed there). LendingClub no longer publishes it directly, so the file could not be checked against the original publisher.
- 2,260,701 rows were read and 4,379 quarantined, including 33 footer rows that are not loans.
- Approved loans only: declined applicants have no outcome. US peer-to-peer consumer loans, 2007 to 2019, which may not generalize to other lenders.
- Associations, not causes. `member_id` is empty, so repeat borrowers cannot be identified.

## Three findings

1. **Default rates rose in 2015 despite a better grade mix.** The 36-month default rate rose 1.16 points from the 2014 vintage (13.73%) to the 2015 vintage (14.89%). Grade D-G loans fell to 15.5% of 2015 volume (2014 was 18.3%), so grade mix alone would have lowered the rate by 0.67 points. Within-grade default rates added 1.84 points. That is the part of the increase mix does not explain. See `dashboards/screenshots/01_vintage_default_rate.png`.
2. **Layered borrower risk is the strongest borrower-attribute finding.** On the unseen 2015 vintage, loans with fico_mid < 680, dti >= 20, revol_util >= 60 defaulted at 22.8% (5,458 of 23,963), against 6.3% (1,009 of 16,100) for fico_mid >= 720, dti < 15, revol_util < 40: **3.63x** (95% CI 3.41 to 3.88). This compares two ends of the book. Against all other loans the ratio is 1.61x. See `dashboards/screenshots/06_relative_risk.png` and `04_fico_dti_heatmap.png`.
3. **The model ranks moderately and is useful mainly for prioritizing review.** Out-of-time ROC-AUC was 0.685 (95% CI 0.683 to 0.688) on 283,007 loans. The top score decile held 20.9% of defaults, 2.1x what a random ranking would capture. See `dashboards/screenshots/07_capture_curve.png`.

LendingClub's own grade separates risk too: grade D-G defaulted 2.27x as often as A-C (28.3% vs 12.4%). As that repeats the lender's existing assessment, it is not presented as a new insight.

## One issue investigated

The model under-predicted the 2015 vintage: mean score 12.01% against 14.89% observed. It ranks loans more usefully than it estimates their level. To check the investigation method itself, controlled faults were injected into synthetic batches. *Simulation — not observed portfolio history.* Batch SIM-11 raised the default rate by 4.62 points and the decomposition attributed it to mix (4.69 points). Batch SIM-12 raised it by 8.88 points and attributed it to within-grade rates (8.87 points). Both match what was injected, so the same decomposition was applied to the real 2015 change, which it attributes to within-grade rates. Root cause is not established from this data.

## Two strategy options (hypothetical and retrospective)

| Option | Referred | Default capture | Default rate among referred | Trade-off |
|---|---:|---:|---:|---|
| Top 10% by score (A) | 10.0% | 20.9% | 31.2% | Fixed workload, but depends on the score alone |
| Score 0.20+ and DTI 20+ (C) | 9.2% | 18.7% | 30.2% | Easier to explain, but volume moves with the portfolio |

Under the illustrative objective (most defaults captured with at most 10% referred), option A was chosen on 2014 validation data. Moving capacity from 5% to 10% to 20% changes capture from 11.2% to 20.9% to 37.3%.
**Why not to deploy this blindly:** it shows what was already associated with default among approved loans. It does not show that a review would prevent a default, what a review costs, or how declined applicants would behave, and 68.8% of referred loans did not default.

## Recommended next analytical action

Add the layered segment (FICO under 680, DTI 20+, utilization 60%+) to the monitoring pack as a watchlist indicator. It was 8.5% of 2015 loans, with a 22.8% default rate against 14.2% for all other loans. Review the model with its owner and test recalibration on recent vintages before using its scores as probability estimates. Ask why within-grade default rates rose while interest rates fell. Grade alone did not capture it, so track default rates within grade and not only the grade mix.

## Open questions for a model owner or business partner

- Do stricter or looser credit policies explain the 2015 within-grade rise, and is it visible in later vintages?
- What review capacity is realistic, and what does a false referral cost?
- Is a borrower key available, so repeat borrowers can be handled?
- 2,563 rows had DTI outside 0 to 100 and were quarantined. What do those values mean, and should joint-application DTI be used for joint loans?
