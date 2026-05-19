#!/usr/bin/env python3
"""Generate company summaries using Claude Haiku.

Sources descriptions from factual data only — never training knowledge.
Priority: one_liner → meta_description → job raw_text → homepage → skip.
Claude only reformats; it does not recall or infer anything not in the source.
"""

import json
import re
import sys
from pathlib import Path

from log import log_error as _log_error
from llm import BACKEND, chat


COMPANIES_FILE = Path(__file__).parent.parent / "data" / "companies.json"
JOBS_FILE = Path(__file__).parent.parent / "data" / "jobs_raw.json"
OUTPUT_FILE = Path(__file__).parent.parent / "data" / "companies_classified.json"
LOG_FILE = Path(__file__).parent.parent / "data" / "jobs.log"

SYSTEM_PROMPT = """\
You are rewriting a company description from provided source text.

Rewrite the source as 1-2 sentences of plain factual prose:
- Start with the company name followed by what it builds, makes, or does (e.g., "Acme builds X that does Y.")
- Do not add any information not present in the source text
- Remove marketing language: "leading", "innovative", "cutting-edge", "pioneering", "world-class", "best-in-class", "revolutionizing"
- Plain prose only — no markdown, no bullet points, no headers
- If the source contains no useful factual information about what the company builds or does, respond with exactly: insufficient
- Respond with only the rewritten description, nothing else
"""


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


def call_llm(user_message: str) -> str:
    return chat(SYSTEM_PROMPT, user_message, max_tokens=150, log_error=log_error)


def get_source(company: dict, jobs: list[dict], website: str) -> tuple[str, str]:
    """Return (source_text, source_label) using priority chain. Returns ("", "") if none."""
    if company.get("one_liner"):
        return company["one_liner"], "one_liner"

    if company.get("meta_description"):
        return company["meta_description"], "meta_description"

    sample = next((j for j in jobs if j.get("raw_text")), None)
    if sample:
        return sample["raw_text"][:3000], "job_posting"

    homepage_text = fetch_homepage(website)
    if homepage_text:
        return homepage_text, "homepage"

    return "", ""


SAVE_EVERY = 50  # checkpoint the cache to disk every N companies

CompanyKey = tuple[str, str]  # (ats, slug) — unique across ATSes


def load_existing(companies: list[dict]) -> dict[CompanyKey, dict]:
    """Load existing summaries keyed by (ats, slug).

    Migrates legacy entries that pre-date the composite key by looking up
    (ats, slug) from companies.json via company name (which is unique by design).
    """
    existing: dict[CompanyKey, dict] = {}
    if not OUTPUT_FILE.exists():
        return existing

    name_to_key: dict[str, CompanyKey] = {
        c["name"].lower(): (c["ats"], c["slug"])
        for c in companies
        if c.get("name") and c.get("ats") and c.get("slug")
    }

    for entry in json.loads(OUTPUT_FILE.read_text()):
        ats, slug = entry.get("ats"), entry.get("slug")
        if ats and slug:
            existing[(ats, slug)] = entry
            continue
        # Legacy entry — look up the canonical (ats, slug) from companies.json
        key = name_to_key.get((entry.get("name") or "").lower())
        if key:
            entry["ats"], entry["slug"] = key
            existing[key] = entry
    return existing


def main():
    classify_all = "--all" in sys.argv

    companies = json.loads(COMPANIES_FILE.read_text())
    existing = load_existing(companies)

    # Job lookup keyed by (source, company_slug) so different ATSes don't collide.
    job_lookup: dict[CompanyKey, list[dict]] = {}
    if JOBS_FILE.exists():
        for job in json.loads(JOBS_FILE.read_text()):
            source = job.get("source", "")
            slug = job.get("company_slug", "")
            if source and slug:
                job_lookup.setdefault((source, slug), []).append(job)

    def needs_classify(c: dict) -> bool:
        if classify_all:
            return True
        key = (c["ats"], c["slug"])
        if key not in existing:
            return True
        entry = existing[key]
        summary = entry.get("summary", "")
        # Reclassify entries that lack a source (generated from training knowledge)
        # or have no summary
        return not summary or not entry.get("source")

    to_process = [
        c for c in companies
        if c.get("status") == "active"
        and job_lookup.get((c["ats"], c["slug"]))
        and needs_classify(c)
    ]

    print(f"Backend: {BACKEND}")
    print(f"{len(to_process)} companies to classify ({len(existing)} already done)\n")

    if not to_process:
        print("All companies already classified. Use --all to reclassify.")
        return

    errors = 0
    for i, company in enumerate(to_process, 1):
        ats, slug, name = company["ats"], company["slug"], company["name"]
        website = company.get("website", "")
        key: CompanyKey = (ats, slug)

        jobs = job_lookup.get(key, [])
        source_text, source_label = get_source(company, jobs, website)

        if not source_text:
            print(f"  [{i:>3}/{len(to_process)}] SKIP {name}: no source available")
            continue

        user_message = f"Company: {name}\n\nSource:\n{source_text}"

        try:
            raw = call_llm(user_message)
            lines = [line for line in raw.splitlines() if not line.startswith("#")]
            summary = " ".join(line.strip() for line in lines if line.strip())

            if not summary or summary.lower().strip() == "insufficient":
                print(f"  [{i:>3}/{len(to_process)}] SKIP {name}: source insufficient ({source_label})")
                continue

            existing[key] = {"ats": ats, "slug": slug, "name": name, "summary": summary, "source": source_label}
            print(f"  [{i:>3}/{len(to_process)}] {name} [{source_label}]: {summary[:80]}")
        except Exception as e:
            errors += 1
            msg = f"{name}: {e}"
            print(f"  [{i:>3}/{len(to_process)}] ERROR {msg}")
            log_error(f"company error: {msg}")

        if i % SAVE_EVERY == 0:
            OUTPUT_FILE.write_text(json.dumps(list(existing.values()), indent=2))
            print(f"  [checkpoint] saved {i}/{len(to_process)}")

    OUTPUT_FILE.write_text(json.dumps(list(existing.values()), indent=2))
    print(f"\nDone. Written to {OUTPUT_FILE}")
    if errors:
        print(f"{errors} errors — check {LOG_FILE.name}")


if __name__ == "__main__":
    main()
