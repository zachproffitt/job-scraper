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
CLASSIFIED_JOBS_FILE = Path(__file__).parent.parent / "data" / "jobs_classified.json"
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


def get_sources(company: dict, jobs: list[dict]) -> list[tuple[str, str]]:
    """Ordered list of (text, label) to try. Homepage excluded — fetched lazily as last resort."""
    candidates = []
    if company.get("one_liner"):
        candidates.append((company["one_liner"], "one_liner"))
    if company.get("meta_description"):
        candidates.append((company["meta_description"], "meta_description"))
    sample = next((j for j in jobs if j.get("raw_text")), None)
    if sample:
        candidates.append((sample["raw_text"][:3000], "job_posting"))
    return candidates


def parse_llm_response(raw: str) -> str:
    lines = [line for line in raw.splitlines() if not line.startswith("#")]
    text = " ".join(line.strip() for line in lines if line.strip())
    return "" if text.lower().strip() == "insufficient" else text


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
    # Also builds the set of companies with active builder jobs on the board.
    job_lookup: dict[CompanyKey, list[dict]] = {}
    board_company_keys: set[CompanyKey] = set()
    if JOBS_FILE.exists():
        jobs_classified = json.loads(CLASSIFIED_JOBS_FILE.read_text()) if CLASSIFIED_JOBS_FILE.exists() else {}
        for job in json.loads(JOBS_FILE.read_text()):
            source = job.get("source", "")
            slug = job.get("company_slug", "")
            if not (source and slug):
                continue
            job_lookup.setdefault((source, slug), []).append(job)
            cl = jobs_classified.get(job.get("id", ""), {})
            if (cl.get("is_engineering") is True
                    and not cl.get("is_contract", False)
                    and cl.get("region") in ("us", "canada")):
                board_company_keys.add((source, slug))

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
        and (c["ats"], c["slug"]) in board_company_keys
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
        sources = get_sources(company, jobs)

        summary = source_label = None
        api_error = False

        for source_text, label in sources:
            try:
                summary = parse_llm_response(call_llm(f"Company: {name}\n\nSource:\n{source_text}"))
            except Exception as e:
                errors += 1
                msg = f"{name}: {e}"
                print(f"  [{i:>3}/{len(to_process)}] ERROR {msg}")
                log_error(f"company error: {msg}")
                api_error = True
                break
            if summary:
                source_label = label
                break

        if not api_error and summary is None:
            homepage_text = fetch_homepage(website)
            if homepage_text:
                try:
                    summary = parse_llm_response(call_llm(f"Company: {name}\n\nSource:\n{homepage_text}"))
                    if summary:
                        source_label = "homepage"
                except Exception as e:
                    errors += 1
                    msg = f"{name}: {e}"
                    print(f"  [{i:>3}/{len(to_process)}] ERROR {msg}")
                    log_error(f"company error: {msg}")
                    api_error = True

        if api_error:
            continue

        if not summary:
            reason = "no source available" if not sources and not website else "all sources insufficient"
            print(f"  [{i:>3}/{len(to_process)}] SKIP {name}: {reason}")
            continue

        existing[key] = {"ats": ats, "slug": slug, "name": name, "summary": summary, "source": source_label}
        print(f"  [{i:>3}/{len(to_process)}] {name} [{source_label}]: {summary[:80]}")

        if i % SAVE_EVERY == 0:
            OUTPUT_FILE.write_text(json.dumps(list(existing.values()), indent=2))
            print(f"  [checkpoint] saved {i}/{len(to_process)}")

    OUTPUT_FILE.write_text(json.dumps(list(existing.values()), indent=2))
    print(f"\nDone. Written to {OUTPUT_FILE}")
    if errors:
        print(f"{errors} errors — check {LOG_FILE.name}")


if __name__ == "__main__":
    main()
