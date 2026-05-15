#!/usr/bin/env python3
"""
Batch-classify all companies as us/canada/international using the Anthropic
batch API, then propagate international status to jobs_classified.json so
render_jobs.py can filter them immediately.

Usage:
    PYTHONPATH=. python pipeline/classify_company_regions.py
    PYTHONPATH=. python pipeline/classify_company_regions.py --propagate-only
"""

import json
import sys
import time
from collections import Counter
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
COMPANIES_FILE = DATA_DIR / "companies.json"
JOBS_RAW_FILE = DATA_DIR / "jobs_raw.json"
JOBS_CLASSIFIED_FILE = DATA_DIR / "jobs_classified.json"

MODEL = "claude-haiku-4-5-20251001"

SYSTEM = """\
You determine whether a company hires software engineers based in the United States.

Respond with exactly one word:
us         = company hires US-based engineers (US HQ, or international company with meaningful US engineering presence)
canada     = company hires in Canada but has no significant US engineering presence
international = company does not hire US-based engineers (operates outside US/Canada, no US engineering offices)

Focus on where the engineers work, not where the company is incorporated. A UK company with a large SF office is "us". A UK property portal with no US presence is "international".\
"""


def classify_companies(companies: list[dict]) -> dict[str, str]:
    """Submit batch job. Returns {company_key: region}."""
    import anthropic
    client = anthropic.Anthropic()

    to_classify = [c for c in companies if not c.get("region")]
    print(f"Classifying {len(to_classify)} companies via batch API "
          f"({len(companies) - len(to_classify)} already have region)")

    if not to_classify:
        return {}

    requests = []
    for i, company in enumerate(to_classify):
        parts = [f"Company: {company['name']}"]
        if company.get("website"):
            parts.append(f"Website: {company['website']}")
        if company.get("meta_description"):
            parts.append(f"Description: {company['meta_description'][:400]}")
        requests.append({
            "custom_id": str(i),
            "params": {
                "model": MODEL,
                "max_tokens": 5,
                "system": SYSTEM,
                "messages": [{"role": "user", "content": "\n".join(parts)}],
            },
        })

    print(f"Submitting {len(requests)} requests...")
    batch = client.messages.batches.create(requests=requests)
    print(f"Batch ID: {batch.id}")

    while batch.processing_status == "in_progress":
        counts = batch.request_counts
        print(f"  processing={counts.processing}  succeeded={counts.succeeded}  errored={counts.errored}")
        time.sleep(20)
        batch = client.messages.batches.retrieve(batch.id)

    counts = batch.request_counts
    print(f"Done — succeeded={counts.succeeded}  errored={counts.errored}  expired={counts.expired}")

    results: dict[str, str] = {}
    for result in client.messages.batches.results(batch.id):
        if result.result.type != "succeeded":
            continue
        idx = int(result.custom_id)
        company = to_classify[idx]
        key = f"{company['ats']}:{company['slug']}"
        text = result.result.message.content[0].text.strip().lower()
        results[key] = text if text in ("us", "canada", "international") else "unclear"

    return results


def propagate_to_jobs(companies: list[dict]) -> int:
    """Mark jobs as international when their company is international."""
    company_region = {
        c["name"].lower(): c["region"]
        for c in companies
        if c.get("region")
    }

    if not JOBS_RAW_FILE.exists() or not JOBS_CLASSIFIED_FILE.exists():
        print("jobs_raw.json or jobs_classified.json missing — skipping propagation")
        return 0

    jobs_raw = json.loads(JOBS_RAW_FILE.read_text())
    classified = json.loads(JOBS_CLASSIFIED_FILE.read_text())

    job_company = {j["id"]: j["company"].lower() for j in jobs_raw}

    updated = 0
    for job_id, cl in classified.items():
        company_name = job_company.get(job_id, "")
        if company_region.get(company_name) == "international" and cl.get("region") != "international":
            cl["region"] = "international"
            updated += 1

    if updated:
        JOBS_CLASSIFIED_FILE.write_text(json.dumps(classified, indent=2))

    return updated


def main():
    propagate_only = "--propagate-only" in sys.argv

    companies = json.loads(COMPANIES_FILE.read_text())

    if not propagate_only:
        results = classify_companies(companies)

        key_index = {f"{c['ats']}:{c['slug']}": c for c in companies}
        changed = 0
        for key, region in results.items():
            if key in key_index:
                key_index[key]["region"] = region
                changed += 1

        if changed:
            COMPANIES_FILE.write_text(json.dumps(companies, indent=2))
            print(f"Wrote region to {changed} companies")

        breakdown = Counter(c.get("region", "unclassified") for c in companies)
        print(f"Region breakdown: {dict(breakdown)}")

    updated = propagate_to_jobs(companies)
    print(f"Propagated region to {updated} classified jobs")


if __name__ == "__main__":
    main()
