-- Star schema for the LendingClub credit-risk warehouse. Independent educational project.
CREATE SCHEMA IF NOT EXISTS credit_risk;
SET search_path TO credit_risk;

-- Grain: one row per ingested source file.
CREATE TABLE dim_ingestion_batch (
    batch_id TEXT PRIMARY KEY, source_filename TEXT NOT NULL, source_sha256 CHAR(64) NOT NULL,
    loaded_at TIMESTAMPTZ NOT NULL, input_rows INTEGER NOT NULL, accepted_rows INTEGER NOT NULL,
    quarantined_rows INTEGER NOT NULL, processing_version TEXT NOT NULL,
    CONSTRAINT batch_partition CHECK (input_rows = accepted_rows + quarantined_rows)
);
-- Grain: one row per calendar month.
CREATE TABLE dim_date (
    date_key INTEGER PRIMARY KEY, month_start DATE NOT NULL UNIQUE, calendar_year SMALLINT NOT NULL,
    calendar_quarter TEXT NOT NULL
);
-- Grain: one row per term and sub-grade combination.
CREATE TABLE dim_product (
    product_key INTEGER PRIMARY KEY, term_months SMALLINT NOT NULL CHECK (term_months IN (36, 60)),
    grade CHAR(1) NOT NULL, sub_grade CHAR(2) NOT NULL, UNIQUE (term_months, sub_grade)
);
-- Grain: one row per combination of borrower bands (a junk dimension). No personal data.
CREATE TABLE dim_borrower_profile (
    profile_key INTEGER PRIMARY KEY, home_ownership TEXT NOT NULL, verification_status TEXT NOT NULL,
    application_type TEXT NOT NULL, fico_band TEXT NOT NULL, dti_band TEXT NOT NULL,
    utilization_band TEXT NOT NULL, income_band TEXT NOT NULL, employment_band TEXT NOT NULL,
    loan_amount_band TEXT NOT NULL
);
CREATE TABLE dim_purpose (purpose_key INTEGER PRIMARY KEY, purpose TEXT NOT NULL UNIQUE);
CREATE TABLE dim_geography (geography_key INTEGER PRIMARY KEY, state_code CHAR(2) NOT NULL UNIQUE);
CREATE TABLE dim_loan_status (
    status_key INTEGER PRIMARY KEY, loan_status TEXT NOT NULL UNIQUE, status_group TEXT NOT NULL,
    is_resolved BOOLEAN NOT NULL
);
CREATE TABLE dim_model_version (
    model_version TEXT PRIMARY KEY, model_type TEXT NOT NULL, feature_count INTEGER NOT NULL, seed INTEGER NOT NULL,
    source_sha256 CHAR(64) NOT NULL, decision_threshold DOUBLE PRECISION
);
-- Grain: one row per loan, one snapshot at the file's data cut. The primary key on loan_id means
-- joins to dimensions cannot double-count loans or defaults.
CREATE TABLE fact_loan (
    loan_id BIGINT PRIMARY KEY,
    batch_id TEXT NOT NULL REFERENCES dim_ingestion_batch (batch_id),
    date_key INTEGER NOT NULL REFERENCES dim_date (date_key),
    product_key INTEGER NOT NULL REFERENCES dim_product (product_key),
    profile_key INTEGER NOT NULL REFERENCES dim_borrower_profile (profile_key),
    purpose_key INTEGER NOT NULL REFERENCES dim_purpose (purpose_key),
    geography_key INTEGER NOT NULL REFERENCES dim_geography (geography_key),
    status_key INTEGER NOT NULL REFERENCES dim_loan_status (status_key),
    loan_amnt NUMERIC(10, 2) NOT NULL CHECK (loan_amnt > 0),
    int_rate NUMERIC(6, 3) NOT NULL,
    annual_inc NUMERIC(14, 2) NOT NULL CHECK (annual_inc > 0),
    dti DOUBLE PRECISION,
    fico_mid NUMERIC(5, 1) NOT NULL,
    revol_util DOUBLE PRECISION,
    default_flag SMALLINT CHECK (default_flag IN (0, 1)),
    is_matured_cohort BOOLEAN NOT NULL,
    split TEXT NOT NULL CHECK (split IN ('train', 'validation', 'test', 'not_in_cohort'))
);
-- Grain: one row per loan per model version. Validation and test scores only.
CREATE TABLE fact_model_score (
    loan_id BIGINT NOT NULL REFERENCES fact_loan (loan_id),
    model_version TEXT NOT NULL REFERENCES dim_model_version (model_version),
    split TEXT NOT NULL, score DOUBLE PRECISION NOT NULL CHECK (score BETWEEN 0 AND 1),
    PRIMARY KEY (loan_id, model_version)
);
-- Grain: one row per model, split and metric.
CREATE TABLE fact_model_metric (
    model_name TEXT NOT NULL, split TEXT NOT NULL, metric TEXT NOT NULL, value DOUBLE PRECISION,
    PRIMARY KEY (model_name, split, metric)
);
-- Grain: one row per contract rule or layer control per batch.
CREATE TABLE fact_data_quality_check (
    batch_id TEXT NOT NULL REFERENCES dim_ingestion_batch (batch_id), check_name TEXT NOT NULL,
    check_type TEXT NOT NULL CHECK (check_type IN ('quarantine_rule', 'warning_flag', 'layer_control')),
    category TEXT, status TEXT NOT NULL, affected_rows INTEGER, description TEXT,
    PRIMARY KEY (batch_id, check_name)
);
-- Grain: one row per batch or vintage quarter per rule. The simulation flag is repeated on purpose,
-- so a query of this table alone cannot present simulated results as observed history.
CREATE TABLE fact_monitoring_result (
    batch_id TEXT NOT NULL, rule TEXT NOT NULL, value DOUBLE PRECISION, warn_level DOUBLE PRECISION,
    critical_level DOUBLE PRECISION, severity TEXT NOT NULL, simulation BOOLEAN NOT NULL, provenance TEXT NOT NULL,
    PRIMARY KEY (batch_id, rule)
);
