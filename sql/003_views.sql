SET search_path TO credit_risk;

-- Headline KPI with a 95% Wilson interval. Matured loans only, to avoid censoring bias.
CREATE VIEW portfolio_summary AS
WITH totals AS (
    SELECT COUNT(*)::numeric AS loans, SUM(default_flag)::numeric AS defaults, SUM(loan_amnt) AS loan_amount
    FROM fact_loan WHERE is_matured_cohort
), r AS (SELECT loans, defaults, loan_amount, defaults / loans AS p, 1.959963984540054::numeric AS z FROM totals)
SELECT loans::bigint, defaults::bigint, loan_amount, p::double precision AS default_rate,
       ((p + z*z/(2*loans) - z*SQRT(p*(1-p)/loans + z*z/(4*loans*loans))) / (1 + z*z/loans))::double precision AS ci95_low,
       ((p + z*z/(2*loans) + z*SQRT(p*(1-p)/loans + z*z/(4*loans*loans))) / (1 + z*z/loans))::double precision AS ci95_high
FROM r;

-- Vintage trend. LAG gives the change from the prior vintage of the same term.
CREATE VIEW vintage_default_rates AS
WITH v AS (
    SELECT d.calendar_year AS issue_year, p.term_months, COUNT(*) AS loans, SUM(f.default_flag) AS defaults,
           AVG(f.fico_mid) AS mean_fico, AVG(f.dti) AS mean_dti, AVG(f.int_rate) AS mean_int_rate
    FROM fact_loan f JOIN dim_date d USING (date_key) JOIN dim_product p USING (product_key)
    WHERE f.is_matured_cohort
    GROUP BY d.calendar_year, p.term_months
)
SELECT issue_year, term_months, loans, defaults::bigint, defaults::double precision / loans AS default_rate,
       mean_fico::double precision, mean_dti, mean_int_rate::double precision,
       defaults::double precision / loans - LAG(defaults::double precision / loans)
           OVER (PARTITION BY term_months ORDER BY issue_year) AS change_from_prior_vintage,
       loans < 500 AS small_sample_warning
FROM v;

-- Long-format segment rates on the same dimensions used in the Spark gold layer.
CREATE VIEW segment_default_rates AS
WITH j AS (
    SELECT f.default_flag, p.grade::text AS grade, p.term_months::text || ' months' AS term_label,
           b.fico_band, b.dti_band, b.utilization_band, b.income_band, b.loan_amount_band, b.employment_band,
           b.home_ownership, b.verification_status, u.purpose
    FROM fact_loan f JOIN dim_product p USING (product_key) JOIN dim_borrower_profile b USING (profile_key)
    JOIN dim_purpose u USING (purpose_key)
    WHERE f.is_matured_cohort
), u AS (
    SELECT 'grade' AS segment_type, grade AS segment, default_flag FROM j
    UNION ALL SELECT 'term_label', term_label, default_flag FROM j
    UNION ALL SELECT 'fico_band', fico_band, default_flag FROM j
    UNION ALL SELECT 'dti_band', dti_band, default_flag FROM j
    UNION ALL SELECT 'utilization_band', utilization_band, default_flag FROM j
    UNION ALL SELECT 'income_band', income_band, default_flag FROM j
    UNION ALL SELECT 'loan_amount_band', loan_amount_band, default_flag FROM j
    UNION ALL SELECT 'employment_band', employment_band, default_flag FROM j
    UNION ALL SELECT 'home_ownership', home_ownership, default_flag FROM j
    UNION ALL SELECT 'purpose', purpose, default_flag FROM j
    UNION ALL SELECT 'verification_status', verification_status, default_flag FROM j
), g AS (SELECT segment_type, segment, COUNT(*) AS loans, SUM(default_flag) AS defaults FROM u GROUP BY 1, 2)
SELECT segment_type, segment, loans, defaults::bigint, defaults::double precision / loans AS default_rate,
       loans::double precision / SUM(loans) OVER (PARTITION BY segment_type) AS loan_share,
       defaults::double precision / SUM(defaults) OVER (PARTITION BY segment_type) AS default_share,
       RANK() OVER (PARTITION BY segment_type ORDER BY defaults::double precision / loans DESC) AS risk_rank,
       loans < 500 AS small_sample_warning
FROM g;

-- Delinquency snapshot by issue year, at the single data cut. Not a month-by-month roll rate.
CREATE VIEW delinquency_by_vintage AS
SELECT d.calendar_year AS issue_year, COUNT(*) AS loans,
       COUNT(*) FILTER (WHERE s.status_group = 'open_performing') AS open_performing,
       COUNT(*) FILTER (WHERE s.status_group = 'open_delinquent') AS open_delinquent,
       COUNT(*) FILTER (WHERE s.status_group = 'resolved_bad') AS charged_off_or_default,
       COUNT(*) FILTER (WHERE s.status_group = 'open_delinquent')::double precision
         / NULLIF(COUNT(*) FILTER (WHERE s.status_group IN ('open_performing', 'open_delinquent')), 0) AS delinquent_share_of_open
FROM fact_loan f JOIN dim_date d USING (date_key) JOIN dim_loan_status s USING (status_key)
GROUP BY d.calendar_year;

-- Capture table for the held-out vintage. NTILE ranks loans by score.
CREATE VIEW model_score_deciles AS
WITH ranked AS (
    SELECT f.default_flag, s.score, NTILE(10) OVER (ORDER BY s.score DESC, s.loan_id) AS score_decile
    FROM fact_model_score s JOIN fact_loan f USING (loan_id) WHERE s.split = 'test'
), g AS (SELECT score_decile, COUNT(*) AS loans, SUM(default_flag) AS defaults, AVG(score) AS mean_score FROM ranked GROUP BY 1)
SELECT score_decile, loans, defaults::bigint, mean_score, defaults::double precision / loans AS observed_rate,
       SUM(defaults) OVER (ORDER BY score_decile)::double precision / SUM(defaults) OVER () AS cumulative_capture
FROM g;

CREATE VIEW quality_summary AS
SELECT b.batch_id, b.input_rows, b.accepted_rows, b.quarantined_rows,
       COUNT(*) AS checks,
       COUNT(*) FILTER (WHERE c.check_type = 'quarantine_rule') AS quarantine_rules,
       COUNT(*) FILTER (WHERE c.check_type = 'quarantine_rule' AND c.affected_rows > 0) AS quarantine_rules_triggered,
       COUNT(*) FILTER (WHERE c.check_type = 'warning_flag') AS warning_flags,
       COUNT(*) FILTER (WHERE c.check_type = 'layer_control') AS layer_controls,
       COUNT(*) FILTER (WHERE c.status = 'FAIL') AS failed_checks
FROM dim_ingestion_batch b JOIN fact_data_quality_check c USING (batch_id)
GROUP BY b.batch_id, b.input_rows, b.accepted_rows, b.quarantined_rows;

CREATE VIEW monitoring_alerts AS
SELECT batch_id, rule, value, severity, simulation, provenance
FROM fact_monitoring_result WHERE severity IN ('WARN', 'CRITICAL');
