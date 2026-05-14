#!/usr/bin/env python3
"""
Discover ATS metadata for companies listed in data/company_names.txt.

Each line in company_names.txt must be:  Company Name | domain.com
The domain is used to scrape the company's careers page and find the ATS.

For each company not already in data/companies.json, fetches the careers
page at domain/careers (and variations), follows redirects, and extracts
the ATS and slug from the page URL or HTML.

Supported ATS (scrapers exist): greenhouse, lever, ashby, smartrecruiters
Detected only (no scraper yet): workday, icims, taleo, bamboo, rippling, workable, breezy

Companies with unsupported ATS are still saved to companies.json so they
appear as [skip] entries in fetch_jobs.py output — use that list as a
task list for building new scrapers.

Usage:
    python discover.py             # resolve new companies only
    python discover.py --recheck   # also re-verify and fix existing entries

Results are written to data/companies.json. All output goes to stdout
(captured by pipeline.sh into logs/pipeline.log).

Note: If discover.py picks the wrong slug (e.g. for companies with ambiguous
names), edit data/companies.json directly. That entry is skipped on future runs.
"""

import json
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import httpx

WORKERS = 20

LOG_FILE = Path("data/discovery.log")


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    line = f"[{ts}] [ats] {msg}"
    print(line)
    with LOG_FILE.open("a") as f:
        f.write(line + "\n")

NAMES_FILE = Path("data/company_names.txt")
COMPANIES_FILE = Path("data/companies.json")

CAREERS_LINK_RE = re.compile(
    r'href=["\']([^"\']*(?:career|jobs|hiring|work-with-us|join-us|join-our-team|work-here|open-roles)[^"\']*)["\']',
    re.I,
)
CAREERS_FALLBACK_PATHS = ["/careers", "/jobs", "/work-with-us", "/join"]

# Greenhouse embed URLs: boards.greenhouse.io/embed/job_board?for=slug
#   or the JS variant:   boards.greenhouse.io/embed/job_board/js?for=slug
_GH_EMBED = re.compile(r"greenhouse\.io/embed[^?]*\?for=([A-Za-z0-9_-]+)", re.I)
_GH_BOARD = re.compile(r"(?:boards|job-boards(?:\.eu)?)\.greenhouse\.io/([A-Za-z0-9_-]+)", re.I)
# Slugs that are generic page names, not real ATS board IDs
_SLUG_BLACKLIST = {"embed", "job_board", "jobs", "careers", "apply", "boards", "assets-cdn", "assets", "cdn", "static"}

# ATS patterns: (regex, ats_name, group_for_slug)
# Supported = scraper exists; detected = saves to companies.json but fetch skips with [skip]
ATS_PATTERNS = [
    # Supported
    (_GH_EMBED, "greenhouse"),
    (_GH_BOARD, "greenhouse"),
    (re.compile(r"jobs\.lever\.co/([A-Za-z0-9_.+-]+)", re.I), "lever"),
    (re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9_.+-]+)", re.I), "ashby"),
    (re.compile(r"jobs\.smartrecruiters\.com/([A-Za-z0-9_.+-]+)", re.I), "smartrecruiters"),
    # Workday: capture tenant/partition/board as composite slug
    (re.compile(r"([A-Za-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}[_-][A-Z]{2}/)?([A-Za-z0-9_-]+)", re.I), "workday"),
    (re.compile(r"([A-Za-z0-9-]+)\.icims\.com", re.I), "icims"),
    (re.compile(r"([A-Za-z0-9-]+)\.taleo\.net", re.I), "taleo"),
    (re.compile(r"([A-Za-z0-9-]+)\.bamboohr\.com", re.I), "bamboo"),
    (re.compile(r"app\.rippling\.com/(?:jobs|hiring)/([A-Za-z0-9_-]+)", re.I), "rippling"),
    (re.compile(r"apply\.workable\.com/([A-Za-z0-9_-]+)", re.I), "workable"),
    (re.compile(r"([A-Za-z0-9-]+)\.breezy\.hr", re.I), "breezy"),
]

SUPPORTED_ATS = {"greenhouse", "lever", "ashby", "smartrecruiters", "bamboo", "breezy", "workable", "workday", "eightfold"}


def parse_names_file() -> list[tuple[str, str]]:
    """Parse company_names.txt, return list of (name, domain) tuples."""
    entries = []
    for line in NAMES_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "|" not in line:
            print(f"  WARNING: malformed line (missing domain): {line!r}")
            continue
        name, _, domain = line.partition("|")
        name = name.strip()
        domain = domain.strip()
        if not name or not domain or domain == "???":
            print(f"  WARNING: missing domain for {name!r} — skipping")
            continue
        entries.append((name, domain))
    return entries


def extract_ats(text: str) -> "tuple[str, str] | None":
    """Search text for any ATS URL and return (ats, slug) or None."""
    for pattern, ats in ATS_PATTERNS:
        m = pattern.search(text)
        if m:
            if ats == "workday" and len(m.groups()) >= 3:
                tenant, partition, board = m.group(1), m.group(2), m.group(3)
                if tenant.lower() in _SLUG_BLACKLIST or board.lower() in _SLUG_BLACKLIST:
                    continue
                return ats, f"{tenant}/{partition}/{board}"
            slug = m.group(1)
            if slug.lower() not in _SLUG_BLACKLIST:
                return ats, slug
    return None


def extract_meta_description(html: str) -> str:
    """Extract og:description or meta description from HTML. Returns empty string if not found."""
    patterns = [
        r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']{20,})',
        r'<meta[^>]+content=["\']([^"\']{20,})["\'][^>]+property=["\']og:description',
        r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']{20,})',
        r'<meta[^>]+content=["\']([^"\']{20,})["\'][^>]+name=["\']description',
    ]
    for pattern in patterns:
        m = re.search(pattern, html, re.I)
        if m:
            text = m.group(m.lastindex).strip()
            text = text.replace("&#x27;", "'").replace("&amp;", "&").replace("&quot;", '"')
            return text[:500]
    return ""


def check_response(r: httpx.Response) -> "tuple[str, str] | None":
    """Check redirect chain, final URL, and HTML of a response for ATS patterns."""
    for hist in r.history:
        result = extract_ats(str(hist.url))
        if result:
            return result
    result = extract_ats(str(r.url))
    if result:
        return result
    if r.status_code == 200:
        return extract_ats(r.text)
    return None


def scrape_company(domain: str, client: httpx.Client) -> "tuple[tuple[str, str] | None, str]":
    """
    Fetch the homepage once, extract ATS info and meta description in a single pass.

    Strategy:
    1. Fetch homepage — check for ATS directly, extract meta description, find careers link.
    2. Follow the careers link found on the homepage.
    3. Fall back to guessing common paths (/careers, /jobs, etc.).

    Returns ((ats, slug) | None, meta_description).
    """
    base = f"https://{domain}"
    meta = ""

    try:
        r = client.get(base, timeout=8, follow_redirects=True)

        if r.status_code == 200:
            meta = extract_meta_description(r.text)

        result = check_response(r)
        if result:
            return result, meta

        if r.status_code == 200:
            careers_url = None
            for href in CAREERS_LINK_RE.findall(r.text):
                if href.startswith("http"):
                    careers_url = href
                elif href.startswith("/"):
                    careers_url = base + href
                else:
                    careers_url = base + "/" + href
                break

            if careers_url:
                try:
                    rc = client.get(careers_url, timeout=8, follow_redirects=True)
                    result = check_response(rc)
                    if result:
                        return result, meta
                except Exception:
                    pass
    except Exception:
        pass

    # Fall back to common path guessing
    for path in CAREERS_FALLBACK_PATHS:
        try:
            r = client.get(base + path, timeout=8, follow_redirects=True)
            result = check_response(r)
            if result:
                return result, meta
            # Eightfold: both markers must be present to avoid false positives
            if r.status_code == 200 and "eightfold.ai" in r.text and "vscdn.net" in r.text:
                host = str(r.url).split("://", 1)[-1].split("/")[0]
                return ("eightfold", f"{host}|{domain}"), meta
        except Exception:
            continue

    return None, meta


def verify_entry(company: dict, client: httpx.Client) -> bool:
    """Check if the current ATS/slug is still valid."""
    ats = company.get("ats")
    slug = company.get("slug")
    if not ats or not slug:
        return False

    # Unsupported ATS — can't verify via API, assume still valid
    if ats not in SUPPORTED_ATS:
        return True

    try:
        if ats == "greenhouse":
            r = client.get(
                f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs", timeout=8
            )
            return r.status_code == 200 and "jobs" in r.json()
        elif ats == "lever":
            r = client.get(
                f"https://api.lever.co/v0/postings/{slug}",
                params={"mode": "json"},
                timeout=8,
            )
            return r.status_code == 200 and isinstance(r.json(), list)
        elif ats == "ashby":
            r = client.get(
                f"https://api.ashbyhq.com/posting-api/job-board/{slug}", timeout=8
            )
            return r.status_code == 200 and "jobs" in r.json()
        elif ats == "smartrecruiters":
            r = client.get(
                f"https://api.smartrecruiters.com/v1/companies/{slug}/postings",
                timeout=8,
            )
            return r.status_code == 200
    except Exception:
        pass
    return False


def main():
    recheck = "--recheck" in sys.argv

    entries = parse_names_file()  # [(name, domain), ...]
    name_domain = {name: domain for name, domain in entries}

    existing: dict[str, dict] = {}
    if COMPANIES_FILE.exists():
        for c in json.loads(COMPANIES_FILE.read_text()):
            existing[c["name"].lower()] = c

    changed = False
    fixed = []
    still_broken = []
    newly_found = []
    unresolved = []

    with httpx.Client(follow_redirects=True) as client:

        # --- Recheck: verify and fix existing entries ---
        if recheck:
            to_check = list(existing.items())
            print(f"Rechecking {len(to_check)} existing companies...")
            print()

            for i, (key, company) in enumerate(to_check, 1):
                label = f"{company['name']} ({company.get('ats','?')}/{company.get('slug','?')})"
                print(f"  [{i:>3}/{len(to_check)}] {label[:65]}", end=" ", flush=True)

                if verify_entry(company, client):
                    print("ok")
                    continue

                # Try to re-scrape via domain
                domain = name_domain.get(company["name"])
                if not domain:
                    print("broken (no domain)")
                    still_broken.append(company["name"])
                    continue

                result, _ = scrape_company(domain, client)
                if result:
                    ats, slug = result
                    old = f"{company.get('ats')}/{company.get('slug')}"
                    new = f"{ats}/{slug}"
                    company["ats"] = ats
                    company["slug"] = slug
                    existing[key] = company
                    fixed.append((company["name"], old, new))
                    changed = True
                    print(f"FIXED -> {new}")
                else:
                    still_broken.append(company["name"])
                    print("still broken")

            print(f"\nFixed {len(fixed)}, still broken: {len(still_broken)}\n")

        # --- Backfill website field for existing entries that have a known domain ---
        backfilled = 0
        for key, company in existing.items():
            if not company.get("website"):
                domain = name_domain.get(company["name"])
                if domain:
                    company["website"] = f"https://{domain}"
                    existing[key] = company
                    changed = True
                    backfilled += 1
        if backfilled:
            print(f"Backfilled website field for {backfilled} existing companies.")

        # --- Discover new companies ---
        new_entries = [(n, d) for n, d in entries if n.lower() not in existing]

        if not new_entries:
            print(f"All {len(entries)} companies already resolved.")
        else:
            print(f"Resolving {len(new_entries)} new companies ({len(existing)} already known)...")
            print()

            detected_unsupported = []  # (name, ats, slug)
            lock = threading.Lock()
            completed = 0

            with ThreadPoolExecutor(max_workers=WORKERS) as executor:
                futures = {
                    executor.submit(scrape_company, domain, client): (name, domain)
                    for name, domain in new_entries
                }
                for future in as_completed(futures):
                    name, domain = futures[future]
                    with lock:
                        completed += 1
                        n = completed

                    try:
                        result, meta = future.result()
                    except Exception as e:
                        print(f"  [{n:>3}/{len(new_entries)}] {name} ({domain})... error: {e}")
                        with lock:
                            unresolved.append((name, domain))
                        continue

                    if result:
                        ats, slug = result
                        entry = {
                            "name": name,
                            "ats": ats,
                            "slug": slug,
                            "website": f"https://{domain}",
                            "category": [],
                        }
                        if meta:
                            entry["meta_description"] = meta
                        with lock:
                            existing[name.lower()] = entry
                            changed = True
                            if ats in SUPPORTED_ATS:
                                newly_found.append(name)
                            else:
                                detected_unsupported.append((name, ats, slug))
                        label = f"{ats}/{slug}" if ats in SUPPORTED_ATS else f"{ats}/{slug} [no scraper]"
                    else:
                        with lock:
                            unresolved.append((name, domain))
                        label = "not found"

                    print(f"  [{n:>3}/{len(new_entries)}] {name} ({domain})... {label}")

                    # Save periodically so a timeout doesn't discard all progress
                    with lock:
                        current_n = n
                    if current_n % 500 == 0:
                        COMPANIES_FILE.write_text(json.dumps(list(existing.values()), indent=2))
                        log(f"[checkpoint] saved {current_n}/{len(new_entries)}")

        supported_count = len(newly_found)
        unsupported_count = len(detected_unsupported) if "detected_unsupported" in dir() else 0
        log(f"Resolved: {supported_count} supported, {unsupported_count} detected (no scraper), {len(unresolved)} not found")

        if "detected_unsupported" in dir() and detected_unsupported:
            by_ats: dict[str, list[str]] = {}
            for name, ats, slug in detected_unsupported:
                by_ats.setdefault(ats, []).append(name)
            for ats in sorted(by_ats):
                log(f"No scraper for [{ats}]: {', '.join(by_ats[ats])}")

        for name, domain in unresolved:
            log(f"ATS not detected: {name} ({domain})")

    # Write updated companies.json
    if changed:
        COMPANIES_FILE.write_text(json.dumps(list(existing.values()), indent=2))
        log(f"Written to {COMPANIES_FILE}")


if __name__ == "__main__":
    main()
