"""Test OpenAlex as the free publication/keyword source for AURAK faculty.

Matches every faculty member in the combined dataset to an OpenAlex author
(ORCID first, then exact name match among AURAK-affiliated authors), pulls
research topics and a sample of recent works with keywords, and writes a
coverage report.

Usage:
    python scripts/openalex_test.py
Optional: OPENALEX_API_KEY=... in ../.env (free key, larger daily budget).
"""
import csv
import io
import json
import pathlib
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATASET_ZIP = ROOT / "AURAK_combined_enriched_2026-10-14.zip"
FACULTY_CSV = "AURAK_combined_2026-10-14/faculty.csv"
OUT_DIR = ROOT / "openalex_test_output"
AURAK_OPENALEX_ID = "I4210153389"  # American University of Ras Al Khaimah (ROR 0440yjn92)
API = "https://api.openalex.org"
TITLES = {"dr", "prof", "professor", "mr", "mrs", "ms", "assoc", "asst", "assistant", "associate", "eng"}

spent_usd = 0.0


def load_api_key():
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("OPENALEX_API_KEY="):
                return line.split("=", 1)[1].strip() or None
    return None


API_KEY = load_api_key()


def get(path, **params):
    global spent_usd
    if API_KEY:
        params["api_key"] = API_KEY
    url = f"{API}{path}?{urllib.parse.urlencode(params)}"
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "AURAK-grants-dashboard/0.1 (student prototype)"})
            with urllib.request.urlopen(req, timeout=30) as r:
                spent_usd += float(r.headers.get("X-RateLimit-Cost-USD") or 0)
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code == 429 and attempt < 2:
                time.sleep(2 * (attempt + 1))
                continue
            raise


def norm_name(name):
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    tokens = [t for t in re.split(r"[^a-z]+", name) if t and t not in TITLES]
    return tokens


def name_key(name):
    t = norm_name(name)
    return (t[0], t[-1]) if len(t) >= 2 else None


def load_faculty():
    with zipfile.ZipFile(DATASET_ZIP) as z:
        text = z.read(FACULTY_CSV).decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(text)))


def orcid_id(value):
    m = re.search(r"\d{4}-\d{4}-\d{4}-\d{3}[\dX]", value or "")
    return m.group(0) if m else None


def fetch_aurak_authors():
    # page= paging works without a key (cursor= paging needs one); fine for ~600 authors
    authors, page_no = [], 1
    while True:
        page = get("/authors", filter=f"affiliations.institution.id:{AURAK_OPENALEX_ID}",
                   per_page=200, page=page_no,
                   select="id,display_name,display_name_alternatives,orcid,works_count,cited_by_count,topics")
        authors += page["results"]
        if len(authors) >= page["meta"]["count"] or not page["results"]:
            return authors
        page_no += 1


def main():
    faculty = load_faculty()
    aurak_authors = fetch_aurak_authors()
    by_orcid = {orcid_id(a.get("orcid")): a for a in aurak_authors if a.get("orcid")}
    by_name = {}
    for a in aurak_authors:
        for n in [a["display_name"], *(a.get("display_name_alternatives") or [])]:
            k = name_key(n)
            if k:
                by_name.setdefault(k, {})[a["id"]] = a

    rows = []
    for f in faculty:
        fid = f.get("﻿faculty_id") or f.get("faculty_id")
        author, method = None, "not_found"
        oid = orcid_id(f.get("orcid_url"))
        if oid:
            author = by_orcid.get(oid)
            if author is None:
                author = get(f"/authors/orcid:{oid}",
                             select="id,display_name,orcid,works_count,cited_by_count,topics")
            if author:
                method = "orcid"
                # an ORCID can be mis-attributed (in our data or in OpenAlex): require a shared name token
                ours = set(norm_name(f["full_name"]))
                theirs = set(norm_name(author["display_name"]))
                for alt in author.get("display_name_alternatives") or []:
                    theirs |= set(norm_name(alt))
                if not ours & theirs:
                    method = "orcid_name_mismatch_REVIEW"
        if author is None:
            cands = by_name.get(name_key(f["full_name"]) or ("", ""), {})
            if len(cands) == 1:
                author, method = next(iter(cands.values())), "name_exact_aurak"
            elif len(cands) > 1:
                # several OpenAlex profiles for one person (split profiles): keep the largest, flag it
                author = max(cands.values(), key=lambda a: a.get("works_count", 0))
                method = f"name_ambiguous_{len(cands)}_profiles"

        row = {"faculty_id": fid, "full_name": f["full_name"], "school": f["school"],
               "match_method": method, "openalex_id": "", "openalex_name": "",
               "works_count": "", "cited_by_count": "", "top_topics": "",
               "sample_keywords": "", "works_with_abstract": ""}
        if author:
            row.update(openalex_id=author["id"], openalex_name=author["display_name"],
                       works_count=author.get("works_count", ""),
                       cited_by_count=author.get("cited_by_count", ""),
                       top_topics="; ".join(t["display_name"] for t in (author.get("topics") or [])[:5]))
            works = get("/works", filter=f"author.id:{author['id'].rsplit('/', 1)[-1]}",
                        sort="publication_year:desc", per_page=10,
                        select="id,title,publication_year,keywords,abstract_inverted_index")
            kws = []
            for w in works["results"]:
                for k in w.get("keywords") or []:
                    if k["display_name"] not in kws:
                        kws.append(k["display_name"])
            row["sample_keywords"] = "; ".join(kws[:10])
            row["works_with_abstract"] = (f"{sum(1 for w in works['results'] if w.get('abstract_inverted_index'))}"
                                          f"/{len(works['results'])}")
        rows.append(row)

    OUT_DIR.mkdir(exist_ok=True)
    with open(OUT_DIR / "openalex_faculty_coverage.csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    matched = [r for r in rows if r["openalex_id"] and "REVIEW" not in r["match_method"]]
    for r in rows:
        if "REVIEW" in r["match_method"]:
            print(f"REVIEW: {r['full_name']} -> OpenAlex '{r['openalex_name']}' (ORCID points to a different name)")
    print(f"AURAK-affiliated OpenAlex authors fetched: {len(aurak_authors)}")
    print(f"Faculty in dataset: {len(rows)}")
    print(f"Matched to OpenAlex: {len(matched)} ({100 * len(matched) / len(rows):.0f}%)")
    for m in sorted({r['match_method'] for r in rows}):
        print(f"  {m}: {sum(1 for r in rows if r['match_method'] == m)}")
    for school in sorted({r['school'] for r in rows}):
        s = [r for r in rows if r["school"] == school]
        print(f"  {school}: {sum(1 for r in s if r in matched)}/{len(s)} matched")
    print(f"Matched faculty with topics: {sum(1 for r in matched if r['top_topics'])}")
    print(f"Matched faculty with keywords on recent works: {sum(1 for r in matched if r['sample_keywords'])}")
    print(f"API cost of this run: ${spent_usd:.4f} ({'own key' if API_KEY else 'no key, shared $0.10/day'})")
    print(f"Report: {OUT_DIR / 'openalex_faculty_coverage.csv'}")


if __name__ == "__main__":
    main()
