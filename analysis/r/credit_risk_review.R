#!/usr/bin/env Rscript
# Independent R check of the Python/SQL results. It reads loan rows straight from PostgreSQL and
# recomputes everything with its own functions, then compares with the Python reference file.
# Usage: R_LIBS_USER=.local/Rlib Rscript analysis/r/credit_risk_review.R

suppressPackageStartupMessages({
  library(DBI); library(RPostgres); library(dplyr); library(ggplot2)
})

TOLERANCE <- 1e-6      # rates, counts and risk ratios
COEF_TOLERANCE <- 1e-3 # iterative regression fits

read_env_url <- function(path = ".env") {
  if (!file.exists(path)) stop("Missing .env. Copy .env.example to .env first.")
  line <- grep("^DATABASE_URL=", readLines(path), value = TRUE)
  if (length(line) != 1) stop("DATABASE_URL not found in .env")
  parts <- regmatches(line, regexec("//([^:]+):([^@]+)@([^:]+):([0-9]+)/(.+)$", line))[[1]]
  list(user = parts[2], password = parts[3], host = parts[4], port = as.integer(parts[5]), dbname = parts[6])
}

load_cohort <- function(con) {
  dbGetQuery(con, "
    SELECT f.loan_id, f.default_flag, f.split, d.calendar_year AS issue_year, p.term_months, p.grade,
           f.fico_mid::double precision AS fico_mid, f.dti, f.revol_util, f.annual_inc::double precision AS annual_inc
    FROM credit_risk.fact_loan f
    JOIN credit_risk.dim_date d USING (date_key)
    JOIN credit_risk.dim_product p USING (product_key)
    WHERE f.is_matured_cohort")
}

# Risk ratio, log-normal 95% interval and an uncorrected chi-square test.
risk_ratio <- function(segment, comparison) {
  if (length(segment) == 0 || length(comparison) == 0) stop("Empty group in risk_ratio")
  a <- sum(segment); n1 <- length(segment); c <- sum(comparison); n2 <- length(comparison)
  rr <- (a / n1) / (c / n2)
  se <- sqrt(1 / a - 1 / n1 + 1 / c - 1 / n2)
  test <- chisq.test(matrix(c(a, n1 - a, c, n2 - c), nrow = 2, byrow = TRUE), correct = FALSE)
  list(segment_loans = n1, comparison_loans = n2, segment_default_rate = a / n1, comparison_default_rate = c / n2,
       relative_risk = rr, rr_ci95_low = exp(log(rr) - qnorm(0.975) * se), rr_ci95_high = exp(log(rr) + qnorm(0.975) * se),
       p_value = test$p.value)
}

# ROC-AUC through the rank-sum identity, so it does not depend on any Python or ML library.
rank_auc <- function(y, score) {
  r <- rank(score, ties.method = "average")
  n1 <- sum(y == 1); n0 <- sum(y == 0)
  (sum(as.numeric(r[y == 1])) - n1 * (n1 + 1) / 2) / (as.numeric(n1) * n0)
}

tryCatch({
  cfg <- read_env_url()
  con <- dbConnect(RPostgres::Postgres(), host = cfg$host, port = cfg$port, user = cfg$user, password = cfg$password, dbname = cfg$dbname)
  on.exit(dbDisconnect(con), add = TRUE)
  loans <- load_cohort(con)
  dbDisconnect(con); on.exit()
}, error = function(e) stop("Could not read the warehouse: ", conditionMessage(e)))

test <- filter(loans, split == "test")
results <- list()
add <- function(name, value) results[[length(results) + 1]] <<- data.frame(metric = name, r = as.numeric(value))
add("cohort_loans", nrow(loans))
add("cohort_defaults", sum(loans$default_flag))
add("cohort_default_rate", mean(loans$default_flag))

# Segment definitions are written out here, not imported, so this is a separate implementation.
in_set <- function(x, cond) !is.na(cond) & cond
segments <- list(
  "High DTI (30+)" = list(in_set(test, test$dti >= 30), in_set(test, test$dti < 30)),
  "Lower FICO (under 680)" = list(test$fico_mid < 680, test$fico_mid >= 680),
  "Grade D to G" = list(test$grade %in% c("D", "E", "F", "G"), test$grade %in% c("A", "B", "C")),
  "Layered: FICO under 680, DTI 20+, utilization 60%+" = list(
    in_set(test, test$fico_mid < 680 & test$dti >= 20 & test$revol_util >= 60),
    !in_set(test, test$fico_mid < 680 & test$dti >= 20 & test$revol_util >= 60)),
  "Layered vs prime: FICO under 680, DTI 20+, utilization 60%+ vs FICO 720+, DTI under 15, utilization under 40%" = list(
    in_set(test, test$fico_mid < 680 & test$dti >= 20 & test$revol_util >= 60),
    in_set(test, test$fico_mid >= 720 & test$dti < 15 & test$revol_util < 40))
)
for (name in names(segments)) {
  out <- risk_ratio(test$default_flag[segments[[name]][[1]]], test$default_flag[segments[[name]][[2]]])
  for (k in c("segment_loans", "comparison_loans", "segment_default_rate", "comparison_default_rate", "relative_risk", "rr_ci95_low", "rr_ci95_high"))
    add(paste0("finding.", name, ".oot_", k), out[[k]])
}

# Baseline regression fitted with base R glm on the same complete-case rows.
model_rows <- loans %>%
  filter(!is.na(dti)) %>%
  mutate(log_annual_inc = log1p(annual_inc), term_60 = as.integer(term_months == 60))
train_rows <- filter(model_rows, split == "train")
test_rows <- filter(model_rows, split == "test")
fit <- glm(default_flag ~ fico_mid + dti + log_annual_inc + term_60, data = train_rows, family = binomial())
add("reduced_logit.train_rows", nrow(train_rows)); add("reduced_logit.test_rows", nrow(test_rows))
for (term in c("fico_mid", "dti", "log_annual_inc", "term_60")) add(paste0("reduced_logit.coef_", term), coef(fit)[[term]])
add("reduced_logit.test_auc", rank_auc(test_rows$default_flag, predict(fit, test_rows, type = "response")))

r_values <- bind_rows(results)
python <- read.csv("data/gold/python_reference_for_r.csv", check.names = FALSE)
comparison <- r_values %>%
  inner_join(python, by = "metric") %>%
  mutate(abs_difference = abs(r - python),
         tolerance = ifelse(grepl("coef_", metric), COEF_TOLERANCE, TOLERANCE),
         status = ifelse(abs_difference <= tolerance, "PASS", "FAIL"))
if (nrow(comparison) != nrow(r_values)) stop("Some R metrics have no Python counterpart.")
write.csv(comparison, "data/gold/reconciliation_r_python.csv", row.names = FALSE)

vintages <- loans %>%
  filter(term_months == 36, issue_year >= 2010) %>%
  group_by(issue_year) %>%
  summarise(loans = n(), defaults = sum(default_flag), rate = defaults / loans, .groups = "drop") %>%
  rowwise() %>%
  mutate(low = prop.test(defaults, loans)$conf.int[1], high = prop.test(defaults, loans)$conf.int[2]) %>%
  ungroup()
chart <- ggplot(vintages, aes(issue_year, rate)) +
  geom_line(colour = "#2a78d6", linewidth = 0.8) +
  geom_pointrange(aes(ymin = low, ymax = high), colour = "#2a78d6", size = 0.3) +
  scale_y_continuous(labels = function(x) paste0(round(100 * x), "%"), limits = c(0, NA)) +
  scale_x_continuous(breaks = vintages$issue_year) +
  labs(title = "Observed default rate of 36-month loans by issue year (R, independent check)",
       subtitle = "Public LendingClub data, matured vintages. Intervals are 95% (prop.test).",
       x = NULL, y = "Observed default rate") +
  theme_minimal(base_size = 11) + theme(panel.grid.minor = element_blank())
dir.create("dashboards/screenshots", showWarnings = FALSE, recursive = TRUE)
ggsave("dashboards/screenshots/r_vintage_default_rate.png", chart, width = 8, height = 4.2, dpi = 150)

cat(sprintf("R %s: %d of %d values within tolerance. Largest difference: %.3e\n",
            getRversion(), sum(comparison$status == "PASS"), nrow(comparison), max(comparison$abs_difference)))
print(comparison %>% filter(status == "FAIL"))
if (any(comparison$status == "FAIL")) quit(status = 1)
