#!/usr/bin/env python3
"""Shared constants and utilities for all render scripts."""

import os
import re
from datetime import datetime, timezone

SUPPORTED_ATS = {
    "greenhouse", "lever", "ashby", "smartrecruiters",
    "bamboo", "breezy", "workable", "workday", "eightfold",
}

# US state codes and common country codes that ATSs append to job titles.
_LOCATION_CODES = frozenset({
    "AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA",
    "HI","ID","IL","IN","IA","KS","KY","LA","ME","MD",
    "MA","MI","MN","MS","MO","MT","NE","NV","NH","NJ",
    "NM","NY","NC","ND","OH","OK","OR","PA","RI","SC",
    "SD","TN","TX","UT","VT","VA","WA","WV","WI","WY","DC",
    "USA","UK","GB","DE","FR","AU","SG","NL","SE","CH",
    "ES","PL","JP","KR","BR","MX","IE","HK","AE","IN",
})

_US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming", "DC": "D.C.",
}
_STATE_RE = re.compile(r',\s*\b(' + '|'.join(_US_STATES) + r')\b')


def clean_location(location: str, is_remote: bool, expand_state: bool = True) -> str:
    """Strip remote/hybrid noise and optionally expand US state abbreviations.

    expand_state should be False for non-US locations to avoid false matches —
    e.g. "Bangalore, IN" would otherwise become "Bangalore, Indiana".
    """
    if not location:
        return location
    if is_remote:
        location = re.sub(r"\bremote[\s-]friendly\b", "", location, flags=re.I)
        location = re.sub(r"\s*\(\s*(?:remote|hybrid)\s*\)", "", location, flags=re.I)
        location = re.sub(r"\s*[-–,|]\s*(?:remote|hybrid)\b", "", location, flags=re.I)
        location = re.sub(r"\b(?:remote|hybrid)\s*[-–,|]\s*", "", location, flags=re.I)
        location = re.sub(r"^\s*(?:remote|hybrid)\s*$", "", location, flags=re.I)
        location = location.strip().strip("-").strip(",").strip("|").strip()
    if expand_state:
        return _STATE_RE.sub(lambda m: f", {_US_STATES[m.group(1)]}", location)
    return location


def strip_location_from_title(title: str) -> str:
    """Remove ATS-appended location suffix for grouping (e.g. ' - Birmingham, AL, USA')."""
    m = re.search(
        r'\s*[-–]\s*[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)*,\s+([A-Z]{2,3})(?:,\s+[A-Z]{2,3})?\s*$',
        title,
    )
    if m and m.group(1) in _LOCATION_CODES:
        return title[:m.start()].strip()
    return title


def company_logo_html(domain: str) -> str:
    """Render a favicon img wrapped in a link to the company homepage."""
    if not domain:
        return ""
    return (
        f'<a href="https://{domain}">'
        f'<img src="https://www.google.com/s2/favicons?domain={domain}&sz=32"'
        f' width="16" height="16" align="absmiddle"></a>&ensp;'
    )


def abbrev_comp(comp: str) -> str:
    """Normalize $100,000 → $100k for any currency with large numbers."""
    def shorten(m: re.Match) -> str:
        n = int(m.group(0).replace(",", ""))
        return f"{n // 1000}k"
    return re.sub(r"\d{1,3}(?:,\d{3})+", shorten, comp)


def is_new_within(j: dict, cutoff: datetime) -> bool:
    """True if a job was first seen at or after `cutoff` (a rolling window from now).

    Uses the precise first_seen_at timestamp when available. For jobs that lack a
    timestamp (older entries from before the field was added, or archived jobs),
    falls back to comparing the first_seen date against the cutoff's date. That
    fallback is intentionally over-inclusive — a job with date == cutoff_date
    could be anywhere from 0 to ~24h beyond the window — but matches the user
    expectation of "include all jobs from the last 24 hours."
    """
    ts = j.get("first_seen_at", "")
    if ts:
        try:
            dt = datetime.fromisoformat(ts)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt >= cutoff
        except ValueError:
            pass
    cutoff_date = cutoff.strftime("%Y-%m-%d")
    return j.get("first_seen", "") >= cutoff_date


def write_step_summary(content: str) -> None:
    """Append content to the GitHub Actions step summary, or print to stdout locally."""
    summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_file:
        with open(summary_file, "a") as f:
            f.write(content)
    else:
        print(content)
