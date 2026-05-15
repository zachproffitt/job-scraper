#!/usr/bin/env python3
"""Update the ATS company-count table and last-updated line in the scraper README."""

import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
COMPANIES_FILE = ROOT / "data" / "companies.json"
README_FILE = ROOT / "README.md"

ATS_TABLE_HEADER = "## Supported ATS"
LAST_UPDATED_PREFIX = "<sub>Last updated "

SUPPORTED_ATS = [
    ("ashby",           "Ashby",           "scrapers/ats_ashby.py"),
    ("greenhouse",      "Greenhouse",       "scrapers/ats_greenhouse.py"),
    ("lever",           "Lever",            "scrapers/ats_lever.py"),
    ("workday",         "Workday",          "scrapers/ats_workday.py"),
    ("bamboo",          "BambooHR",         "scrapers/ats_bamboo.py"),
    ("breezy",          "Breezy",           "scrapers/ats_breezy.py"),
    ("workable",        "Workable",         "scrapers/ats_workable.py"),
    ("smartrecruiters", "SmartRecruiters",  "scrapers/ats_smartrecruiters.py"),
    ("eightfold",       "Eightfold",        "scrapers/ats_eightfold.py"),
]


def build_table(counts: dict[str, int]) -> list[str]:
    total = sum(counts.get(key, 0) for key, _, _ in SUPPORTED_ATS)
    rows = ["| ATS | Companies | Scraper |", "|---|---|---|"]
    for key, label, scraper in SUPPORTED_ATS:
        rows.append(f"| {label} | {counts.get(key, 0)} | `{scraper}` |")
    rows.append(f"| **Total** | **{total}** | |")
    return rows


def replace_table(lines: list[str], new_rows: list[str]) -> list[str]:
    """Replace the ATS table rows in the line list, return updated lines."""
    try:
        header_idx = lines.index(ATS_TABLE_HEADER)
    except ValueError:
        return lines
    # Find the blank line after the header, then the table block
    start = header_idx + 2  # skip header + blank line
    end = start
    while end < len(lines) and lines[end].startswith("|"):
        end += 1
    return lines[:start] + new_rows + lines[end:]


def replace_last_updated(lines: list[str], new_line: str) -> list[str]:
    """Replace an existing last-updated line or insert it after the first paragraph."""
    for i, line in enumerate(lines):
        if line.startswith(LAST_UPDATED_PREFIX):
            lines[i] = new_line
            return lines
    # Not found — insert after the first non-empty paragraph (first blank line after content)
    for i, line in enumerate(lines):
        if i > 0 and line == "" and lines[i - 1] != "":
            return lines[:i + 1] + [new_line, ""] + lines[i + 1:]
    return lines + [new_line]


def main() -> None:
    companies = json.loads(COMPANIES_FILE.read_text())
    counts: dict[str, int] = {}
    for c in companies:
        ats = c.get("ats", "")
        if ats in {key for key, _, _ in SUPPORTED_ATS}:
            counts[ats] = counts.get(ats, 0) + 1

    new_rows = build_table(counts)
    lines = README_FILE.read_text().splitlines()

    # Strip legacy *Updated …* lines
    lines = [l for l in lines if not (l.startswith("*Updated ") and l.endswith("*"))]

    updated = replace_table(lines, new_rows)
    table_changed = updated != lines

    has_timestamp = any(l.startswith(LAST_UPDATED_PREFIX) for l in updated)

    if table_changed or not has_timestamp:
        now = datetime.now(timezone.utc).strftime("%B %-d, %Y at %H:%M UTC")
        updated = replace_last_updated(updated, f"{LAST_UPDATED_PREFIX}{now}</sub>")
        README_FILE.write_text("\n".join(updated) + "\n")
        print("README updated")
    else:
        print("README unchanged")


if __name__ == "__main__":
    main()
