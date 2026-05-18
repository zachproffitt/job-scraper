#!/usr/bin/env python3
"""
Use Claude Haiku to enumerate top engineering companies by industry and
add new ones to companies.txt.

Run discover_companies.py afterward to detect ATS and update companies.json.

Usage:
    PYTHONPATH=. python tools/discover_industry_companies.py [--dry-run]
"""

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import anthropic

COMPANIES_FILE = Path("data/companies.json")
LOG_FILE = Path("data/discovery.log")
MODEL = "claude-haiku-4-5-20251001"


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    line = f"[{ts}] [industry] {msg}"
    print(line)
    with LOG_FILE.open("a") as f:
        f.write(line + "\n")

INDUSTRIES_PROMPT = """List comprehensive industry categories for tech and software engineering companies.
Cover the major sectors where software engineers actively work and that have notable hiring activity.
Include emerging and established sectors.

Respond with ONLY a JSON array of strings, one category per element, with a brief parenthetical describing subcategories.
Example format:
["AI and machine learning (foundation models, AI infrastructure, AI applications)", "developer tools (CI/CD, observability, databases)"]

Include 20-30 categories. Only the JSON array, no other text."""

PROMPT_TEMPLATE = """List the top 25 software engineering companies in this category: {industry}

Focus on companies known for strong engineering cultures that actively hire software engineers.
Include both established companies and well-known startups.

For each company respond with ONLY valid JSON in this exact format, one object per line:
{{"name": "Company Name", "domain": "example.com"}}

Only include the JSON lines, no other text."""


def fetch_industries(client: anthropic.Anthropic) -> list[str]:
    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=1024,
            messages=[{"role": "user", "content": INDUSTRIES_PROMPT}],
        )
        text = resp.content[0].text.strip()
        # Strip markdown code fences if present
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        industries = json.loads(text.strip())
        if isinstance(industries, list) and all(isinstance(i, str) for i in industries):
            return industries
    except Exception as e:
        log(f"ERROR: Failed to fetch industry list: {e}")
    return []


def load_existing() -> tuple[set[str], set[str]]:
    names, domains = set(), set()
    if not COMPANIES_FILE.exists():
        return names, domains
    for c in json.loads(COMPANIES_FILE.read_text()):
        if c.get("name"):
            names.add(c["name"].lower())
        if c.get("website"):
            domain = c["website"].removeprefix("https://").removeprefix("http://").split("/")[0].lstrip("www.")
            domains.add(domain.lower())
    return names, domains


def query_haiku(client: anthropic.Anthropic, industry: str) -> list[tuple[str, str]]:
    prompt = PROMPT_TEMPLATE.format(industry=industry)
    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text.strip()
        results = []
        for line in text.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                obj = json.loads(line)
                name = obj.get("name", "").strip()
                domain = obj.get("domain", "").strip().lstrip("www.")
                if name and domain:
                    results.append((name, domain))
            except json.JSONDecodeError:
                continue
        return results
    except Exception as e:
        log(f"ERROR: API error for '{industry}': {e}")
        return []


def main():
    dry_run = "--dry-run" in sys.argv
    client = anthropic.Anthropic()
    existing_names, existing_domains = load_existing()

    log("Fetching industry categories from Claude...")
    industries = fetch_industries(client)
    if not industries:
        log("ERROR: Could not fetch industry list — aborting")
        sys.exit(1)
    log(f"Got {len(industries)} industries")

    all_new: list[tuple[str, str]] = []

    for industry in industries:
        label = industry.split("(")[0].strip()
        companies = query_haiku(client, industry)

        new_here = []
        for name, domain in companies:
            domain_bare = domain.lower().lstrip("www.")
            if name.lower() in existing_names or domain_bare in existing_domains:
                continue
            new_here.append((name, domain))
            existing_names.add(name.lower())
            existing_domains.add(domain_bare)
            all_new.append((name, domain))

        log(f"{label}: {len(companies)} returned, {len(new_here)} new")
        time.sleep(0.5)

    log(f"Total new companies: {len(all_new)}")

    if not all_new:
        log("Nothing to add.")
        return

    all_new.sort(key=lambda x: x[0].lower())

    if dry_run:
        print("\n[dry-run] Would add:")
        for name, domain in all_new:
            print(f"  {name} | {domain}")
        return

    companies = json.loads(COMPANIES_FILE.read_text()) if COMPANIES_FILE.exists() else []
    existing_by_name = {c["name"].lower() for c in companies}
    for name, domain in all_new:
        if name.lower() not in existing_by_name:
            companies.append({"name": name, "website": f"https://{domain}", "status": "new"})
    COMPANIES_FILE.write_text(json.dumps(companies, indent=2))
    log(f"Added {len(all_new)} new stubs to {COMPANIES_FILE}")


if __name__ == "__main__":
    main()
