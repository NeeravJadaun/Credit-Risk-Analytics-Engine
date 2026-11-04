SET search_path TO credit_risk;
CREATE INDEX idx_loan_date ON fact_loan (date_key);
CREATE INDEX idx_loan_product ON fact_loan (product_key);
CREATE INDEX idx_loan_profile ON fact_loan (profile_key);
CREATE INDEX idx_loan_status ON fact_loan (status_key);
-- Most reporting reads only the matured cohort.
CREATE INDEX idx_loan_matured ON fact_loan (date_key, default_flag) WHERE is_matured_cohort;
CREATE INDEX idx_score_split ON fact_model_score (model_version, split, score DESC);
CREATE INDEX idx_monitoring_alerts ON fact_monitoring_result (severity) WHERE severity IN ('WARN', 'CRITICAL');
