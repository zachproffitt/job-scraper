#!/usr/bin/env python3
"""Generate company summaries using Claude Haiku."""

import json
import re
import sys
from pathlib import Path

from log import log_error as _log_error
from llm import BACKEND, CLAUDE_MODEL, OLLAMA_MODEL, call_claude as _call_claude, call_ollama as _call_ollama

SUPPORTED_ATS = {"greenhouse", "lever", "ashby", "smartrecruiters", "bamboo", "breezy", "workable", "workday", "eightfold"}

COMPANIES_FILE = Path(__file__).parent.parent / "data" / "companies.json"
JOBS_FILE = Path(__file__).parent.parent / "data" / "jobs_raw.json"
OUTPUT_FILE = Path(__file__).parent.parent / "data" / "companies_classified.json"
LOG_FILE = Path(__file__).parent.parent / "data" / "pipeline.log"

SYSTEM_PROMPT = """\
Write 1-2 sentences describing what the company builds and what domain they operate in.
Be specific and factual. Plain prose only — no markdown, no bullet points, no headers.
Do not use "leading", "innovative", "cutting-edge", "pioneering". Do not say you lack web access.
Start directly with the company name or what they build.
Respond with only the description — no intro, no headers, no labels.
"""

BAD_PHRASES = [
    "don't have access", "cannot browse", "can't browse",
    "can't verify", "cannot verify", "i don't have",
    "could you provide", "please provide",
]


def log_error(message: str) -> None:
    _log_error("classify_companies", message, LOG_FILE)


def fetch_homepage(url: str) -> str:
    """Fetch homepage and extract visible text (max 2000 chars)."""
    import httpx
    if not url:
        return ""
    try:
        resp = httpx.get(url, timeout=10, follow_redirects=True,
                         headers={"User-Agent": "Mozilla/5.0 (compatible; bot)"})
        resp.raise_for_status()
        text = resp.text
        # Strip tags, collapse whitespace
        text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.S | re.I)
        text = re.sub(r"<script[^>]*>.*?</script>", " ", text, flags=re.S | re.I)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:2000]
    except Exception as e:
        log_error(f"homepage fetch failed for {url}: {e}")
        return ""


def call_llm(system: str, user_message: str) -> str:
    if BACKEND == "claude":
        return _call_claude(system, user_message, max_tokens=150, log_error=log_error)
    return _call_ollama(system + "\n\n" + user_message, num_ctx=2048)


def is_bad(summary: str) -> bool:
    return any(phrase in summary.lower() for phrase in BAD_PHRASES)


def main():
    classify_all = "--all" in sys.argv

    companies = json.loads(COMPANIES_FILE.read_text())

    existing: dict[str, dict] = {}
    if OUTPUT_FILE.exists():
        for c in json.loads(OUTPUT_FILE.read_text()):
            existing[c["slug"]] = c

    # Build job lookup by company slug for context
    job_lookup: dict[str, list[dict]] = {}
    if JOBS_FILE.exists():
        for job in json.loads(JOBS_FILE.read_text()):
            slug = job.get("company_slug", "")
            if slug:
                job_lookup.setdefault(slug, []).append(job)

    def needs_classify(c: dict) -> bool:
        if classify_all:
            return True
        if c["slug"] not in existing:
            return True
        summary = existing[c["slug"]].get("summary", "")
        return is_bad(summary) or not summary

    to_process = [c for c in companies if c.get("ats") in SUPPORTED_ATS and needs_classify(c)]

    print(f"Backend: {BACKEND}")
    print(f"{len(to_process)} companies to classify ({len(existing)} already done)\n")

    if not to_process:
        print("All companies already classified. Use --all to reclassify.")
        return

    errors = 0
    for i, company in enumerate(to_process, 1):
        slug = company["slug"]
        name = company["name"]
        website = company.get("website", "")

        # Sample job title for extra context
        jobs = job_lookup.get(slug, [])
        sample = next((j for j in jobs if j.get("raw_text")), None)
        job_context = f"\nSample job title: {sample['title']}" if sample else ""

        # First attempt: training knowledge only
        user_message = f"Company: {name}\nWebsite: {website}{job_context}"

        try:
            raw = call_llm(SYSTEM_PROMPT, user_message)
            lines = [line for line in raw.splitlines() if not line.startswith("#")]
            summary = " ".join(line.strip() for line in lines if line.strip())

            # If model refused, scrape the homepage and retry once
            if is_bad(summary) or not summary:
                print(f"  [{i:>3}/{len(to_process)}] {name}: no training knowledge — scraping homepage...")
                homepage_text = fetch_homepage(website)
                if homepage_text:
                    # Homepage content (long document) precedes the company identifier
                    user_message2 = f"<homepage>\n{homepage_text}\n</homepage>\n\nCompany: {name}\nWebsite: {website}{job_context}"
                    raw = call_llm(SYSTEM_PROMPT, user_message2)
                    lines = [line for line in raw.splitlines() if not line.startswith("#")]
                    summary = " ".join(line.strip() for line in lines if line.strip())

            if is_bad(summary) or not summary:
                log_error(f"model refused for {name} even after homepage scrape")
                print(f"  [{i:>3}/{len(to_process)}] SKIP {name}: model refused (will retry with --all)")
                continue

            existing[slug] = {"slug": slug, "name": name, "summary": summary}
            print(f"  [{i:>3}/{len(to_process)}] {name}: {summary[:80]}")
        except Exception as e:
            errors += 1
            msg = f"{name}: {e}"
            print(f"  [{i:>3}/{len(to_process)}] ERROR {msg}")
            log_error(f"company error: {msg}")

    OUTPUT_FILE.write_text(json.dumps(list(existing.values()), indent=2))
    print(f"\nDone. Written to {OUTPUT_FILE}")
    if errors:
        print(f"{errors} errors — check {LOG_FILE.name}")


if __name__ == "__main__":
    main()
