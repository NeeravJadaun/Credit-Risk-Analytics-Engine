# Assumptions and limitations

- **Mirror source.** The file comes from Kaggle because LendingClub no longer publishes it. Row counts and fields look consistent with the original releases, but that could not be verified against the publisher.
- **Approved loans only.** Selection bias applies to all rates and strategies. The rejected-applications file has no outcomes and is not used.
- **One snapshot.** The file holds one final status per loan. "Delinquency trends" compare issue vintages at one data cut and are not month-by-month roll rates.
- **Loan, not borrower.** `member_id` is empty, so repeat borrowers cannot be identified, and loans may not be independent. Intervals could be somewhat too narrow.
- **Associations.** No finding is causal. Relative risks depend on the comparison group, so the extreme-group ratio is always reported with its ratio against all other loans.
- **DTI over 100.** These 2,500+ rows were quarantined as implausible under a rule set for this project. Some may be genuine, particularly for joint or low-income applicants.
- **Joint applications.** Single-applicant income and DTI are used, a simplification.
- **Illustrative thresholds.** All alert levels, capacities and costs are analyst assumptions and not bank policy.
- **Local prototype.** One machine, one NameNode, one DataNode, Spark local mode. Timings describe this setup only.
- **US consumer P2P data, 2007 to 2015.** It should not be generalized to Canadian banks or other products.
