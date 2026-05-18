#!/bin/bash
# Hourly pipeline: fetch → describe → classify → render → publish

SCRAPER_DIR="$(cd "$(dirname "$0")" && pwd)"
JOBS_DIR="$(dirname "$SCRAPER_DIR")/jobs"
export PYTHONPATH="$SCRAPER_DIR"

# Load API keys (cron doesn't source shell config)
[ -f "$HOME/.zshenv" ] && source "$HOME/.zshenv"

cd "$SCRAPER_DIR"

LOG="$SCRAPER_DIR/data/pipeline.log"
TS="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"

log() { echo "[$TS] pipeline: $1" | tee -a "$LOG"; }

log "=== pipeline start ==="

python3 pipeline/fetch_jobs.py 2>&1 | tee -a "$LOG"
python3 pipeline/fetch_job_descriptions.py 2>&1 | tee -a "$LOG"
python3 pipeline/classify_companies.py 2>&1 | tee -a "$LOG"
python3 pipeline/classify_jobs.py 2>&1 | tee -a "$LOG"
python3 pipeline/render_job_listings.py "$JOBS_DIR/jobs" 2>&1 | tee -a "$LOG"
python3 pipeline/render_job_indexes.py "$JOBS_DIR" 2>&1 | tee -a "$LOG"

LABEL="$(date -u '+%b %-d at %H:%M UTC')"

cd "$JOBS_DIR"
if ! git diff --quiet || ! git diff --cached --quiet; then
    git add -A
    git commit -m "$LABEL"
    git push
    log "jobs repo pushed"
else
    log "jobs repo unchanged"
fi

cd "$SCRAPER_DIR"
if ! git diff --quiet data/ || ! git diff --cached --quiet data/; then
    git add data/seen_jobs.json data/seen_companies.json data/jobs_classified.json data/companies_classified.json
    git commit -m "Pipeline data — $LABEL"
    git push
    log "scraper repo pushed"
else
    log "scraper repo unchanged"
fi

log "=== pipeline done ==="
