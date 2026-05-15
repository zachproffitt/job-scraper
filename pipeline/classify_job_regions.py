#!/usr/bin/env python3
"""
Batch-classify REGION only for jobs that are missing the field.
Uses the Anthropic batch API (50% discount) with a minimal prompt.

Run after adding the REGION field to classify_jobs.py to backfill
existing classified jobs without paying for full re-classification.

Usage:
    PYTHONPATH=. python pipeline/classify_job_regions.py
"""

import json
import time
from pathlib import Path

import anthropic

DATA_DIR = Path(__file__).parent.parent / "data"
JOBS_RAW_FILE = DATA_DIR / "jobs_raw.json"
JOBS_CLASSIFIED_FILE = DATA_DIR / "jobs_classified.json"

MODEL = "claude-haiku-4-5-20251001"

SYSTEM = """\
You determine where a job requires the candidate to be located.

Reply with exactly one word:
us            = role is in the US, or remote with no geographic restriction, or explicitly open to US candidates
canada        = role requires presence in Canada and is not open to US-based candidates
international = role requires presence or work authorization outside the US and Canada

Use the description to override location labels — "Remote - UK" with no mention of US eligibility is international.\
"""


def main():
    import html as html_lib

    client = anthropic.Anthropic()

    jobs_raw = json.loads(JOBS_RAW_FILE.read_text())
    classified = json.loads(JOBS_CLASSIFIED_FILE.read_text())

    # Index raw jobs by id for description lookup
    raw_by_id = {j["id"]: j for j in jobs_raw}

    # Jobs that need REGION: missing the field, and have a description
    to_classify = [
        j for j in jobs_raw
        if j["id"] in classified
        and "region" not in classified[j["id"]]
        and j.get("raw_text", "").strip()
    ]

    print(f"Jobs needing REGION: {len(to_classify)} "
          f"(of {len(classified)} classified, {len(jobs_raw)} total in window)")

    if not to_classify:
        print("Nothing to do.")
        return

    # Build batch requests
    requests = []
    for i, job in enumerate(to_classify):
        description = html_lib.unescape(job.get("raw_text", "")).strip()
        location = job.get("location") or ""
        content = (
            f"<job>\n"
            f"<title>{job['title']}</title>\n"
            f"<company>{job['company']}</company>\n"
            f"<location>{location}</location>\n"
            f"<description>\n{description}\n</description>\n"
            f"</job>"
        )
        requests.append({
            "custom_id": str(i),
            "params": {
                "model": MODEL,
                "max_tokens": 5,
                "system": SYSTEM,
                "messages": [{"role": "user", "content": content}],
            },
        })

    print(f"Submitting batch of {len(requests)} requests...")
    batch = client.messages.batches.create(requests=requests)
    print(f"Batch ID: {batch.id}")

    while batch.processing_status == "in_progress":
        counts = batch.request_counts
        print(f"  processing={counts.processing}  succeeded={counts.succeeded}  errored={counts.errored}")
        time.sleep(20)
        batch = client.messages.batches.retrieve(batch.id)

    counts = batch.request_counts
    print(f"Done — succeeded={counts.succeeded}  errored={counts.errored}  expired={counts.expired}")

    # Write results back
    updated = 0
    for result in client.messages.batches.results(batch.id):
        if result.result.type != "succeeded":
            continue
        idx = int(result.custom_id)
        job = to_classify[idx]
        text = result.result.message.content[0].text.strip().lower()
        region = text if text in ("us", "canada", "international") else "unclear"
        classified[job["id"]]["region"] = region
        updated += 1

    JOBS_CLASSIFIED_FILE.write_text(json.dumps(classified, indent=2))

    from collections import Counter
    counts = Counter(classified[j["id"]].get("region", "missing") for j in to_classify if j["id"] in classified)
    print(f"\nUpdated {updated} jobs")
    print(f"Region breakdown for backfilled jobs: {dict(counts)}")

    intl = sum(1 for j in to_classify if classified.get(j["id"], {}).get("region") == "international")
    print(f"\nInternational jobs that will be filtered from render: {intl}")


if __name__ == "__main__":
    main()
