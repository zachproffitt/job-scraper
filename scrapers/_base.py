import html
import re
from dataclasses import dataclass
from datetime import date


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
