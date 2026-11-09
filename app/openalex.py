"""OpenAlex (and Crossref fallback) client.

Faculty matching reuses the logic of scripts/openalex_test.py: ORCID first, then an exact
(first, last) name match among AURAK-affiliated OpenAlex authors, and an ORCID result is
rejected when it shares no name token with our record (catches mis-attributed ORCIDs).
The publication source is pluggable: Scopus / Google Scholar clients can implement the same
`lookup_publication` / `author_works` functions later.
"""
import logging
import re
import time
from datetime import datetime

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config
from app.models import Faculty, Publication
from app.util import clean_doi, name_key, norm_name_tokens, norm_text, openalex_work_id, reconstruct_abstract

log = logging.getLogger("openalex")
API = "https://api.openalex.org"
WORK_FIELDS = ("id,doi,title,display_name,publication_year,primary_location,authorships,keywords,"
               "topics,abstract_inverted_index")


def _get(client: httpx.Client, path: str, **params):
    if config.OPENALEX_API_KEY:
        params["api_key"] = config.OPENALEX_API_KEY
    if config.OPENALEX_MAILTO:
        params["mailto"] = config.OPENALEX_MAILTO
    for attempt in range(3):
        r = client.get(f"{API}{path}", params=params)
        if r.status_code == 404:
            return None
        if r.status_code == 429 and attempt < 2:
            time.sleep(2 * (attempt + 1))
            continue
        r.raise_for_status()
        return r.json()


def client() -> httpx.Client:
    return httpx.Client(timeout=30, headers={"User-Agent": config.HTTP_USER_AGENT}, follow_redirects=True)


def _orcid(value):
    m = re.search(r"\d{4}-\d{4}-\d{4}-\d{3}[\dX]", value or "")
    return m.group(0) if m else None


def work_to_record(w: dict) -> dict:
    loc = w.get("primary_location") or {}
    src = loc.get("source") or {}
    return {
        "openalex_id": (w.get("id") or "").rsplit("/", 1)[-1] or None,
        "doi": clean_doi(w.get("doi")),
        "title": w.get("display_name") or w.get("title"),
        "venue": src.get("display_name"),
        "year": w.get("publication_year"),
        "authors": "; ".join(a["author"]["display_name"] for a in w.get("authorships") or [] if a.get("author")),
        "abstract": reconstruct_abstract(w.get("abstract_inverted_index")),
        "keywords": [k["display_name"] for k in w.get("keywords") or []]
                    + [t["display_name"] for t in (w.get("topics") or [])[:2]],
        "url": loc.get("landing_page_url") or w.get("doi") or w.get("id"),
    }


def lookup_publication(identifier: str) -> tuple[dict | None, str]:
    """Look up a DOI or OpenAlex work ID. Returns (record, source) or (None, error message)."""
    doi, wid = clean_doi(identifier), openalex_work_id(identifier)
    if not doi and not wid:
        return None, "Enter a valid DOI (e.g. 10.1109/ACCESS.2026.3683610) or an OpenAlex work ID (W1234567890)."
    with client() as c:
        try:
            w = _get(c, f"/works/doi:{doi}" if doi else f"/works/{wid}", select=WORK_FIELDS)
            if w:
                return work_to_record(w), "openalex"
        except httpx.HTTPError as e:
            log.warning("OpenAlex lookup failed: %s", e)
        if doi:
            try:
                r = c.get(f"https://api.crossref.org/works/{doi}")
                if r.status_code == 200:
                    m = r.json()["message"]
                    authors = "; ".join(" ".join(filter(None, [a.get("given"), a.get("family")]))
                                        for a in m.get("author") or [])
                    year = None
                    for k in ("published-print", "published-online", "issued"):
                        parts = (m.get(k) or {}).get("date-parts") or [[None]]
                        if parts[0][0]:
                            year = parts[0][0]
                            break
                    return {
                        "openalex_id": None, "doi": doi,
                        "title": (m.get("title") or [None])[0],
                        "venue": (m.get("container-title") or [None])[0],
                        "year": year, "authors": authors,
                        "abstract": re.sub(r"<[^>]+>", " ", m.get("abstract") or "").strip() or None,
                        "keywords": m.get("subject") or [],
                        "url": m.get("URL") or f"https://doi.org/{doi}",
                    }, "crossref"
            except httpx.HTTPError as e:
                log.warning("Crossref lookup failed: %s", e)
    return None, "No publication was found for this ID in OpenAlex or Crossref. Check the ID and try again."


def fetch_aurak_authors(c: httpx.Client) -> list[dict]:
    authors, page_no = [], 1
    while True:
        page = _get(c, "/authors", filter=f"affiliations.institution.id:{config.AURAK_OPENALEX_ID}",
                    per_page=200, page=page_no,
                    select="id,display_name,display_name_alternatives,orcid,works_count,cited_by_count,topics")
        authors += page["results"]
        if len(authors) >= page["meta"]["count"] or not page["results"]:
            return authors
        page_no += 1


def match_faculty(db: Session, c: httpx.Client) -> dict:
    """Link every faculty record to an OpenAlex author. Returns counts by method."""
    aurak = fetch_aurak_authors(c)
    by_orcid = {_orcid(a.get("orcid")): a for a in aurak if a.get("orcid")}
    by_name: dict = {}
    for a in aurak:
        for n in [a["display_name"], *(a.get("display_name_alternatives") or [])]:
            k = name_key(n)
            if k:
                by_name.setdefault(k, {})[a["id"]] = a
    counts: dict = {}
    for f in db.scalars(select(Faculty)):
        author, method = None, "not_found"
        oid = _orcid(f.orcid_url)
        if oid:
            author = by_orcid.get(oid) or _get(c, f"/authors/orcid:{oid}",
                                                select="id,display_name,display_name_alternatives,orcid,works_count,cited_by_count,topics")
            if author:
                method = "orcid"
                ours = set(norm_name_tokens(f.full_name))
                theirs = set(norm_name_tokens(author["display_name"]))
                for alt in author.get("display_name_alternatives") or []:
                    theirs |= set(norm_name_tokens(alt))
                if not ours & theirs:
                    method, author = "orcid_name_mismatch_rejected", None
        if author is None and method != "orcid_name_mismatch_rejected":
            cands = by_name.get(name_key(f.full_name) or ("", ""), {})
            if len(cands) == 1:
                author, method = next(iter(cands.values())), "name_exact_aurak"
            elif len(cands) > 1:
                author = max(cands.values(), key=lambda a: a.get("works_count", 0))
                method = f"name_ambiguous_{len(cands)}_profiles"
        f.openalex_match_method = method
        if author:
            f.openalex_id = author["id"].rsplit("/", 1)[-1]
            f.works_count = author.get("works_count")
            f.cited_by_count = author.get("cited_by_count")
            f.openalex_topics = [t["display_name"] for t in (author.get("topics") or [])[:8]]
        else:
            f.openalex_id = None
        f.openalex_checked_at = datetime.utcnow()
        counts[method] = counts.get(method, 0) + 1
    db.flush()
    return counts


def sync_faculty_works(db: Session, c: httpx.Client) -> int:
    """Add new OpenAlex works for matched faculty. Faculty works are added without approval."""
    added, failed = 0, []
    for f in db.scalars(select(Faculty).where(Faculty.openalex_id.is_not(None))):
        try:
            data = _get(c, "/works", filter=f"author.id:{f.openalex_id}", sort="publication_year:desc",
                        per_page=config.OPENALEX_WORKS_PER_AUTHOR, select=WORK_FIELDS)
        except httpx.HTTPError as e:  # one slow/failed author must not stop the rest
            log.warning("OpenAlex works for %s failed: %s", f.full_name, e)
            failed.append(f.full_name)
            continue
        existing = db.execute(select(Publication.doi, Publication.openalex_id, Publication.title_norm)
                              .where(Publication.owner_type == "faculty", Publication.owner_id == f.id)).all()
        dois = {d for d, _, _ in existing if d}
        oids = {o for _, o, _ in existing if o}
        titles = {t for _, _, t in existing}
        for w in (data or {}).get("results", []):
            rec = work_to_record(w)
            if not rec["title"]:
                continue
            tn = norm_text(rec["title"])
            if rec["doi"] in dois or rec["openalex_id"] in oids:
                continue
            if tn in titles:  # same paper already in the dataset without DOI: enrich it instead
                pub = db.scalar(select(Publication).where(Publication.owner_type == "faculty",
                                                          Publication.owner_id == f.id, Publication.title_norm == tn))
                if pub:
                    pub.openalex_id = pub.openalex_id or rec["openalex_id"]
                    pub.doi = pub.doi or rec["doi"]
                    pub.abstract = pub.abstract or rec["abstract"]
                    pub.keywords = pub.keywords or rec["keywords"]
                continue
            db.add(Publication(owner_type="faculty", owner_id=f.id, title_norm=tn, source="openalex",
                               status="approved", **rec))
            dois.add(rec["doi"]), oids.add(rec["openalex_id"]), titles.add(tn)
            added += 1
        db.flush()
    if failed and len(failed) == db.query(Faculty).filter(Faculty.openalex_id.is_not(None)).count():
        raise RuntimeError(f"OpenAlex unreachable for all {len(failed)} authors")
    return added
