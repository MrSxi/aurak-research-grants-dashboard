"""UAE funders. No public APIs exist, so programme pages are parsed conservatively.

Rules: one request per page per refresh, link to the funder's own page, never guess values.
A date or amount is only stored when it is printed on the page; its surrounding text is kept
as evidence (deadline_text / amount_text). Pages that cannot be parsed into individual calls
are recorded as "monitored": checked automatically every refresh, with no manual entry.
"""
import re
from datetime import date
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from app.connectors.base import Connector, guess_type
from app.util import parse_date, sha

MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
MON3 = "Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec"
DATE_RE = re.compile(
    rf"\b(\d{{1,2}}(?:st|nd|rd|th)?\s+(?:{MONTHS}|{MON3})\.?,?\s+20\d\d"
    rf"|(?:{MONTHS}|{MON3})\.?\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+20\d\d)\b")
DEADLINE_WORDS = re.compile(r"deadline|closing|close[sd]?\b|due\b|submission|last date", re.I)
OPEN_WORDS = re.compile(r"\bopen(s|ing)?\b|launch|call for", re.I)
AMOUNT_RE = re.compile(r"(?:up to|maximum of|of up to|funding of|worth|range[^.]{0,20}?)?\s*"
                       r"(AED|USD|US\$|\$|EUR|€)\s?([\d,.]+)\s?(million|M|K|thousand)?"
                       r"|([\d,.]+)\s?(million|M|K)?\s?(AED|USD|EUR)", re.I)


def _norm_date(s: str) -> date | None:
    s = re.sub(r"(?<=\d)(st|nd|rd|th)", "", s).replace(",", "").replace(".", "")
    s = re.sub(r"\bSept\b", "Sep", s)
    for fmt in ("%d %B %Y", "%d %b %Y", "%B %d %Y", "%b %d %Y"):
        try:
            from datetime import datetime
            return datetime.strptime(" ".join(s.split()), fmt).date()
        except ValueError:
            continue
    return None


def page_text(html: str, drop_patterns=()) -> tuple[BeautifulSoup, str]:
    soup = BeautifulSoup(html, "lxml")
    for t in soup(["script", "style", "nav", "header", "footer", "noscript", "form", "svg"]):
        t.decompose()
    main = soup.find("main") or soup.find("article") or soup.body or soup
    text = " ".join(main.get_text(" ").split())
    for p in drop_patterns:
        text = re.sub(p, " ", text)
    return soup, text


def extract_dates(text: str):
    """Return [(date, context)] for every full date (with year) printed in the text."""
    out = []
    for m in DATE_RE.finditer(text):
        d = _norm_date(m.group(1))
        if d:
            ctx = text[max(0, m.start() - 70): m.end() + 70]
            out.append((d, ctx.strip()))
    return out


def extract_amount(text: str):
    for m in AMOUNT_RE.finditer(text):
        ctx = text[max(0, m.start() - 60): m.end() + 40]
        if not re.search(r"up to|fund|grant|award|support|budget|per (project|centre|year)", ctx, re.I):
            continue
        if re.search(r"total value|awarded \d+|since 20\d\d", ctx, re.I):  # historical totals are not call amounts
            continue
        cur = (m.group(1) or m.group(6) or "").upper().replace("US$", "USD").replace("$", "USD").replace("€", "EUR")
        num, unit = (m.group(2), m.group(3)) if m.group(2) else (m.group(4), m.group(5))
        try:
            value = float(num.replace(",", ""))
        except (TypeError, ValueError):
            continue
        mult = {"million": 1e6, "m": 1e6, "k": 1e3, "thousand": 1e3}.get((unit or "").lower(), 1)
        return value * mult, cur, " ".join(ctx.split())
    return None, None, None


class ProgrammePageScraper(Connector):
    """Parses a funder site into one record per programme page."""
    method = "scraper"
    scope = "UAE"
    emirate = None
    pages: list[str] = []
    discover_from: str | None = None
    discover_pattern: str | None = None
    drop_patterns: tuple = ()
    eligibility_warning: str | None = None
    funder = ""

    def page_urls(self, c) -> list[str]:
        urls = list(self.pages)
        if self.discover_from:
            r = c.get(self.discover_from)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "lxml")
            host = urlparse(self.discover_from).netloc
            for a in soup.find_all("a", href=True):
                u = urljoin(self.discover_from, a["href"]).split("#")[0]
                if urlparse(u).netloc == host and re.search(self.discover_pattern, u, re.I) and u not in urls:
                    urls.append(u)
        return urls

    def fetch(self, known=None):
        out, errors = [], []
        with self.http() as c:
            for url in self.page_urls(c):
                try:
                    r = c.get(url)
                    r.raise_for_status()
                except Exception as e:
                    errors.append(f"{url}: {e}")
                    continue
                rec = self.parse(url, r.text)
                if rec:
                    out.append(rec)
        if errors and not out:
            raise RuntimeError("; ".join(errors))
        self.last_errors = errors
        return out

    def parse(self, url: str, html: str) -> dict | None:
        soup, text = page_text(html, self.drop_patterns)
        h1 = soup.find("h1")
        title = h1.get_text(" ", strip=True) if h1 else None
        og = soup.find("meta", attrs={"property": "og:title"})
        if not title and og:
            title = og.get("content", "").split("|")[0].split(" - ")[0].strip()
        if not title or len(text) < 200:
            return None
        meta = soup.find("meta", attrs={"name": "description"})
        description = text[:2500]
        if meta and meta.get("content") and meta["content"] not in description:
            description = meta["content"].strip() + "\n\n" + description

        today = date.today()
        dates = extract_dates(text)
        deadlines = sorted({d for d, ctx in dates if DEADLINE_WORDS.search(ctx[60:]) or DEADLINE_WORDS.search(ctx)})
        opens = sorted({d for d, ctx in dates if OPEN_WORDS.search(ctx) and d not in deadlines})
        upcoming = [d for d in deadlines if d >= today]
        deadline = upcoming[0] if upcoming else (deadlines[-1] if deadlines else None)
        deadline_ctx = next((ctx for d, ctx in dates if d == deadline), None)
        open_date = None
        future_opens = [d for d in opens if d > today]
        if future_opens and (not deadline or deadline < today or future_opens[0] < deadline):
            open_date = future_opens[0]
        elif opens:
            open_date = max(d for d in opens if d <= today) if any(d <= today for d in opens) else None
        if not deadlines and not future_opens:
            source_status = "unknown"
        else:
            source_status = None
        amount, currency, amount_ctx = extract_amount(text)
        warnings = [self.eligibility_warning] if self.eligibility_warning else []
        apply = None
        for a in soup.find_all("a", href=True):
            if re.search(r"apply|submit|application portal|grant management", a.get_text(" ", strip=True), re.I):
                apply = urljoin(url, a["href"])
                break
        return {
            "source_record_id": urlparse(url).path.strip("/") or url,
            "title": title,
            "funder": self.funder,
            "programme": title,
            "type": guess_type(title, text[:300]),
            "scope": "UAE",
            "country": "United Arab Emirates",
            "emirate": self.emirate,
            "description": description,
            "research_areas": [],
            "keywords": [],
            "eligibility_text": _sentences(text, r"eligib|open to|based in|applicants? (may|must|are)|invited to submit"),
            "eligibility_flags": {"emirate": self.emirate} if self.emirate else {},
            "eligibility_warnings": warnings,
            "amount_min": None, "amount_max": amount, "currency": currency,
            "amount_text": amount_ctx,
            "duration": _sentences(text, r"duration|up to (two|three|five|\d+) years|months", limit=1),
            "open_date": open_date,
            "deadline": deadline,
            "deadlines": [d.isoformat() for d in deadlines],
            "deadline_text": f"As printed on the funder page: “…{deadline_ctx}…”" if deadline_ctx else None,
            "source_status": source_status,
            "apply_url": apply or url,
            "required_documents": _sentences(text, r"required documents|must submit|submit (a|an|the)|letter of intent|"
                                                   r"proposal (must|should)|CV|budget justification", limit=3),
            "contact": _email(text),
            "source_url": url,
            "content_hash": sha(text),
            "raw": {"dates_found": [(d.isoformat(), c) for d, c in dates][:20]},
        }


def _sentences(text: str, pattern: str, limit: int = 3) -> str | None:
    sents = re.split(r"(?<=[.!?])\s+", text)
    hits = [s for s in sents if re.search(pattern, s, re.I) and 20 < len(s) < 600]
    return " ".join(hits[:limit]) or None


def _email(text: str):
    m = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", text)
    return m.group(0) if m else None


class DubaiRDI(ProgrammePageScraper):
    key = "dubai_rdi"
    name = "Dubai RDI Grant Initiative"
    funder = "Dubai Research, Development and Innovation Program (Dubai RDI)"
    url = "https://dubairdi.ae"
    emirate = "Dubai"
    discover_from = "https://dubairdi.ae/"
    discover_pattern = r"dubairdi\.ae/[^/]*(fund|grant|challenge)[^/]*/?$"
    # A site-wide "Upcoming Due Dates" widget repeats the 2025 round on every page; drop it.
    drop_patterns = (r"Upcoming Due Dates.*?Announcement of selected projects:\s*\w+\s+20\d\d",)
    eligibility_warning = "Dubai RDI calls are for universities and research institutes based in Dubai (Dubai-based PIs)."
    notes = ("Read through the site's public WordPress API (robots.txt allows crawling; one request per refresh). "
             "Dubai-based institutions only.")
    API = "https://dubairdi.ae/wp-json/wp/v2/pages"
    # Call / programme pages. Other pages on the site describe projects that were already funded.
    CALL_SLUG = re.compile(r"funding|fund$|fund-|round|challenge|call-for|program", re.I)

    def fetch(self, known=None):
        # The HTML pages refuse automated clients (HTTP 403), but the site's official JSON API is open,
        # so read the page contents from there in a single request.
        with self.http() as c:
            r = c.get(self.API, params={"per_page": 100, "_fields": "slug,link,title,content,modified"})
            r.raise_for_status()
            pages = r.json()
        out = []
        for p in pages:
            if not self.CALL_SLUG.search(p.get("slug") or ""):
                continue
            title = BeautifulSoup((p.get("title") or {}).get("rendered") or "", "lxml").get_text(" ", strip=True)
            body = (p.get("content") or {}).get("rendered") or ""
            rec = self.parse(p["link"], f"<html><body><main><h1>{title}</h1>{body}</main></body></html>")
            if rec:
                out.append(rec)
        return out


class AlQasimi(ProgrammePageScraper):
    key = "alqasimi"
    name = "Sheikh Saud bin Saqr Al Qasimi Foundation"
    funder = "Sheikh Saud bin Saqr Al Qasimi Foundation for Policy Research"
    url = "https://www.alqasimifoundation.com/grants"
    emirate = "Ras Al Khaimah"
    pages = ["https://www.alqasimifoundation.com/grants",
             "https://www.alqasimifoundation.com/transform-rak-grants",
             "https://www.alqasimifoundation.com/grants-and-residency"]
    notes = "Ras Al Khaimah funder; most relevant to AURAK."


class AlJalila(ProgrammePageScraper):
    key = "aljalila"
    name = "Al Jalila Foundation"
    funder = "Al Jalila Foundation"
    url = "https://aljalilafoundation.ae/en/what-we-do/research/"
    emirate = None
    pages = ["https://aljalilafoundation.ae/en/our-program/discovery/seed-grants/"]
    eligibility_warning = "Biomedical research; open to UAE-based researchers with institutional affiliation (per funder page)."
    notes = "Biomedical seed grants."


UAE_SCRAPERS = [DubaiRDI(), AlQasimi(), AlJalila()]


class MonitoredPage(Connector):
    """A funder page we check for reachability; calls are entered manually by an admin."""
    method = "monitored"
    scope = "UAE"

    def __init__(self, key, name, url, notes=""):
        self.key, self.name, self.url, self.notes = key, name, url, notes

    def fetch(self, known=None):
        with self.http() as c:
            r = c.get(self.url)
            r.raise_for_status()
        return []


MONITORED_SOURCES = [
    MonitoredPage("sandooq_alwatan", "Sandooq Al Watan / Researcher.ae", "https://sandooqalwatan.ae/",
                  "Checked every refresh. The site describes national-identity programmes; researcher.ae (its research funding portal) did not resolve."),
    MonitoredPage("atrc", "ATRC / ASPIRE (Abu Dhabi)", "https://www.atrc.gov.ae/",
                  "Checked every refresh. Abu Dhabi institutions only. No dated calls are published on the page yet."),
    MonitoredPage("eca", "Early Childhood Authority research grants", "https://www.eca.gov.ae/research/grants",
                  "Checked every refresh. The page is built in the browser with JavaScript, so it has no readable call text."),
    MonitoredPage("erdc", "ERDC TAWWER platform", "https://erdc.gov.ae/tawwer-platform",
                  "Checked every refresh. TAWWER is the UAE R&D tax-credit platform, not a grant call listing."),
    MonitoredPage("doh_ad", "Abu Dhabi Department of Health research", "https://www.doh.gov.ae/en/research/landing-page",
                  "Checked every refresh. Research priorities page; its tenders page covers procurement, not research grants."),
    MonitoredPage("mohap", "MOHAP UAE Health Research Portal", "https://mohap.gov.ae/en/w/the-uae-health-research-portal",
                  "Checked every refresh. A health-research publications portal, not a funding listing."),
]


class ManualSource(Connector):
    method = "manual"

    def __init__(self, key, name, url, scope, notes):
        self.key, self.name, self.url, self.scope, self.notes = key, name, url, scope, notes

    def fetch(self, known=None):
        return []


MANUAL_SOURCES = []  # no manual grant entry: every source is read automatically
