.PHONY: run fetch describe describe-all classify classify-all render index discover

JOBS_REPO ?= ../jobs
export PYTHONPATH := $(shell pwd)

run: discover fetch describe classify render index

discover:
	python3 companies/discover_companies.py

fetch:
	python3 pipeline/fetch_jobs.py

describe:
	python3 pipeline/fetch_job_descriptions.py

describe-all:
	python3 pipeline/fetch_job_descriptions.py --all

classify:
	python3 pipeline/classify_jobs.py

classify-all:
	python3 pipeline/classify_jobs.py --all

render:
	python3 pipeline/render_job_listings.py

index:
	python3 pipeline/render_job_indexes.py $(JOBS_REPO)
