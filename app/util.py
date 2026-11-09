import hashlib
import html
import re
import unicodedata
from datetime import date, datetime

TITLES = {"dr", "prof", "professor", "mr", "mrs", "ms", "assoc", "asst", "assistant", "associate", "eng"}


def norm_text(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def norm_name_tokens(name: str) -> list[str]:
    return [t for t in norm_text(name).split() if t not in TITLES]


def name_key(name: str):
    t = norm_name_tokens(name)
    return (t[0], t[-1]) if len(t) >= 2 else None


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def strip_html(value: str | None) -> str:
    if not value:
        return ""
    value = re.sub(r"<(br|/p|/li|/h\d|/div)[^>]*>", "\n", value, flags=re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r"\n\s*\n+", "\n\n", value)
    return value.strip()


DOI_RE = re.compile(r"10\.\d{4,9}/[^\s\"<>]+", re.I)


def clean_doi(value: str | None) -> str | None:
    if not value:
        return None
    m = DOI_RE.search(value)
    return m.group(0).rstrip(".,;)").lower() if m else None


def openalex_work_id(value: str | None) -> str | None:
    m = re.search(r"\b(W\d{4,})\b", value or "", re.I)
    return m.group(1).upper() if m else None


def parse_date(value) -> date | None:
    if not value:
        return None
    if isinstance(value, date):
        return value
    value = re.sub(r"(?<=\d)(st|nd|rd|th)\b", "",str(value).strip())
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%b %d, %Y", "%d %B %Y", "%B %d, %Y", "%d %b %Y", "%B %d %Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    m = re.match(r"(\d{4}-\d{2}-\d{2})", value)
    if m:
        return datetime.strptime(m.group(1), "%Y-%m-%d").date()
    m = re.match(r"([A-Z][a-z]{2} \d{1,2}, \d{4})", value)
    if m:
        return datetime.strptime(m.group(1), "%b %d, %Y").date()
    return None


def compute_status(open_date: date | None, deadline: date | None, source_status: str | None = None,
                   today: date | None = None) -> str:
    today = today or date.today()
    if deadline and deadline < today:
        return "closed"
    if open_date and open_date > today:
        return "forthcoming"
    if source_status in ("forthcoming", "forecasted"):
        return "forthcoming"
    if source_status == "closed":
        return "closed"
    return "open"


def money(value: float | None, currency: str | None) -> str:
    if value is None:
        return ""
    return f"{currency or ''} {value:,.0f}".strip()


def reconstruct_abstract(inverted: dict | None) -> str | None:
    if not inverted:
        return None
    positions = [(p, w) for w, ps in inverted.items() for p in ps]
    return " ".join(w for _, w in sorted(positions)) or None
