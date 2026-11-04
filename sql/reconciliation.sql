-- Integrity checks that return one row each. A check passes when violations = 0.
SET search_path TO credit_risk;

SELECT 'fact_rows_match_accepted' AS check_name, ABS((SELECT COUNT(*) FROM fact_loan) - (SELECT SUM(accepted_rows) FROM dim_ingestion_batch)) AS violations;
SELECT 'loan_ids_not_null_and_unique' AS check_name, (SELECT COUNT(*) FROM fact_loan) - (SELECT COUNT(DISTINCT loan_id) FROM fact_loan) AS violations;
SELECT 'star_join_does_not_fan_out' AS check_name, ABS((SELECT COUNT(*) FROM fact_loan f JOIN dim_date USING (date_key) JOIN dim_product USING (product_key) JOIN dim_borrower_profile USING (profile_key) JOIN dim_purpose USING (purpose_key) JOIN dim_geography USING (geography_key) JOIN dim_loan_status USING (status_key)) - (SELECT COUNT(*) FROM fact_loan)) AS violations;
SELECT 'default_flag_set_only_when_resolved' AS check_name, COUNT(*) AS violations FROM fact_loan f JOIN dim_loan_status s USING (status_key) WHERE (f.default_flag IS NULL) = s.is_resolved;
SELECT 'cohort_loans_all_labelled' AS check_name, COUNT(*) AS violations FROM fact_loan WHERE is_matured_cohort AND default_flag IS NULL;
SELECT 'splits_do_not_overlap_in_time' AS check_name, COUNT(*) AS violations FROM (SELECT d.calendar_year FROM fact_loan f JOIN dim_date d USING (date_key) WHERE f.split <> 'not_in_cohort' GROUP BY 1 HAVING COUNT(DISTINCT CASE WHEN f.split = 'train' THEN 'train' ELSE f.split END) > 1) x;
SELECT 'scores_only_on_heldout_loans' AS check_name, COUNT(*) AS violations FROM fact_model_score s JOIN fact_loan f USING (loan_id) WHERE f.split NOT IN ('validation', 'test') OR f.split <> s.split;
