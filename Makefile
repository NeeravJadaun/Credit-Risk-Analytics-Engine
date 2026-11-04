.PHONY: setup services lint test run stop
PY := .venv/bin/python

setup:
	uv venv --python 3.11 .venv
	uv pip install --python $(PY) -r requirements.lock
	uv pip install --python $(PY) --no-deps -e .
	[ -f .env ] || cp .env.example .env
	mkdir -p .local/Rlib
	R_LIBS_USER=.local/Rlib Rscript -e 'install.packages(c("DBI","RPostgres","dplyr","ggplot2"), lib=Sys.getenv("R_LIBS_USER"), repos="https://cloud.r-project.org")'

services:
	./scripts/local_postgres.sh start
	./scripts/local_hadoop.sh start

lint:
	.venv/bin/ruff check src tests spark

test:
	$(PY) -m pytest --junitxml=results/pytest.xml

run:
	$(PY) -m credit_risk.cli run-all --period $$(date +%Y-%m)

stop:
	./scripts/local_hadoop.sh stop
	./scripts/local_postgres.sh stop
