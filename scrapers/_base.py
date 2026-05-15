import html
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone


_BLOCK_END_RE = re.compile(r"</p>|</li>|</h[1-6]>", re.I)
_BR_RE = re.compile(r"<br\s*/?>", re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\n{3,}")


def html_to_text(raw: str) -> str:
    text = html.unescape(raw or "")
    text = _BR_RE.sub("\n", text)
    text = _BLOCK_END_RE.sub("\n", text)
    text = _TAG_RE.sub("", text)
    lines = [line.strip() for line in text.splitlines()]
    return _WHITESPACE_RE.sub("\n\n", "\n".join(line for line in lines if line)).strip()


def parse_iso_date(s: str | None) -> date | None:
    """Parse an ISO-8601 date or datetime string (with optional Z suffix) to a date."""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except (ValueError, AttributeError):
        return None


def parse_timestamp_ms(ms: int | None) -> date | None:
    """Convert a millisecond Unix timestamp to a date."""
    if ms is None:
        return None
    try:
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).date()
    except (OSError, ValueError, TypeError):
        return None


def parse_timestamp_s(ts: int | None) -> date | None:
    """Convert a second Unix timestamp to a date."""
    if ts is None:
        return None
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc).date()
    except (OSError, ValueError, TypeError):
        return None


def build_location(*parts: str | None) -> str | None:
    """Join non-empty location parts with ', ', returning None if nothing remains."""
    return ", ".join(p for p in parts if p) or None


@dataclass
class Job:
    id: str
    company: str
    company_slug: str
    title: str
    url: str
    source: str          # "greenhouse", "lever", "ashby", "smartrecruiters"
    location: str | None = None
    remote: bool | None = None
    posted_at: date | None = None
    raw_text: str = ""


class ScraperError(Exception):
    pass
