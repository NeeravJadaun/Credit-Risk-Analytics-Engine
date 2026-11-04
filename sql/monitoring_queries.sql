-- Example analyst queries against the star schema. Each answers one business question.
SET search_path TO credit_risk;

-- 1. Which grades hold the most default risk, and how concentrated is it? (window share and rank)
SELECT segment AS grade, loans, defaults, ROUND(default_rate::numeric, 4) AS default_rate,
       ROUND(default_share::numeric, 4) AS share_of_defaults, risk_rank
FROM segment_default_rates WHERE segment_type = 'grade' ORDER BY segment;

-- 2. Are newer 36-month vintages performing worse? (LAG)
SELECT issue_year, loans, ROUND(default_rate::numeric, 4) AS default_rate,
       ROUND(change_from_prior_vintage::numeric, 4) AS change_from_prior, small_sample_warning
FROM vintage_default_rates WHERE term_months = 36 ORDER BY issue_year;

-- 3. How much default capture does each score decile add on the held-out vintage? (NTILE, running sum)
SELECT score_decile, loans, defaults, ROUND(observed_rate::numeric, 4) AS observed_rate,
       ROUND(cumulative_capture::numeric, 4) AS cumulative_capture
FROM model_score_deciles ORDER BY score_decile;

-- 4. Layered risk within a grade: does DTI still separate outcomes once grade is held constant?
SELECT p.grade, b.dti_band, COUNT(*) AS loans, ROUND(AVG(f.default_flag)::numeric, 4) AS default_rate
FROM fact_loan f JOIN dim_product p USING (product_key) JOIN dim_borrower_profile b USING (profile_key)
WHERE f.is_matured_cohort AND p.term_months = 36
GROUP BY p.grade, b.dti_band HAVING COUNT(*) >= 500 ORDER BY p.grade, b.dti_band;

-- 5. Alert queue. Real and simulated results are labelled in the output.
SELECT batch_id, rule, ROUND(value::numeric, 4) AS value, severity, provenance
FROM monitoring_alerts ORDER BY simulation, batch_id, rule;

-- 6. Delinquency among open loans at the data cut, by issue year.
SELECT issue_year, open_performing, open_delinquent, ROUND(delinquent_share_of_open::numeric, 4) AS delinquent_share_of_open
FROM delinquency_by_vintage WHERE open_performing > 0 ORDER BY issue_year;
