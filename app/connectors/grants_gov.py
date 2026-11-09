"""Grants.gov (US federal) via the public REST API. No key required."""
import json
import logging
from concurrent.futures import ThreadPoolExecutor

from app import config
from app.connectors.base import Connector, guess_type
from app.util import parse_date, sha, strip_html

log = logging.getLogger("grants_gov")
API = "https://api.grants.gov/v1/api"
# Research-relevant funding categories: science & technology R&D, energy, environment, humanities,
# natural resources, agriculture. Plus anything with unrestricted eligibility (code 99).
SEARCHES = [{"fundingCategories": "ST|EN|ENV|HU|NR|AG"}, {"eligibilities": "99"}]


class GrantsGov(Connector):
    key = "grants_gov"
    name = "Grants.gov (US federal)"
    url = "https://www.grants.gov"
    method = "api"
    scope = "international"
    notes = "Public REST API (search2 + fetchOpportunity). Many calls are limited to US organizations."

    def fetch(self, known=None):
        known = known or {}
        hits = {}
        with self.http() as c:
            for extra in SEARCHES:
                body = {"oppStatuses": "forecasted|posted", "rows": 1000, **extra}
                r = c.post(f"{API}/search2", json=body)
                r.raise_for_status()
                data = r.json()
                if data.get("errorcode"):
                    raise RuntimeError(data.get("msg"))
                for h in data["data"]["oppHits"]:
                    hits[h["id"]] = h
            ordered = sorted(hits.values(), key=lambda h: (parse_date(h.get("closeDate")) is None,
                                                           parse_date(h.get("closeDate")) or 0))
            ordered = ordered[: config.GRANTS_GOV_MAX]

            def detail(h):
                hsh = sha(json.dumps(h, sort_keys=True))
                if known.get(h["id"]) == hsh:
                    return {"source_record_id": h["id"], "unchanged": True, "content_hash": hsh}
                try:
                    d = c.post(f"{API}/fetchOpportunity", json={"opportunityId": int(h["id"])}).json().get("data") or {}
                except Exception as e:  # keep the summary record if the detail call fails
                    log.warning("detail %s failed: %s", h["id"], e)
                    d = {}
                rec = self.normalize(h, d)
                rec["content_hash"] = hsh
                return rec

            with ThreadPoolExecutor(max_workers=8) as pool:
                return list(pool.map(detail, ordered))

    def normalize(self, h: dict, d: dict) -> dict:
        s = d.get("synopsis") or d.get("forecast") or {}
        ceiling = _num(s.get("awardCeiling"))
        floor = _num(s.get("awardFloor"))
        applicant_types = [a.get("description", "") for a in s.get("applicantTypes") or []]
        categories = [a.get("description") for a in s.get("fundingActivityCategories") or [] if a.get("description")]
        unrestricted = any(a.startswith("Unrestricted") for a in applicant_types)
        warnings = ["US federal funding: most Grants.gov calls are limited to US-based organizations. "
                    "Check the eligibility section before applying."]
        if unrestricted:
            warnings = ["Applicant types are listed as unrestricted, but participation by non-US institutions "
                        "depends on the full announcement. Check before applying."]
        elig = strip_html(s.get("applicantEligibilityDesc")) or ""
        if applicant_types:
            elig = (elig + "\n\nEligible applicant types: " + "; ".join(applicant_types)).strip()
        amount_text = None
        if ceiling or floor:
            parts = []
            if floor:
                parts.append(f"award floor USD {floor:,.0f}")
            if ceiling:
                parts.append(f"award ceiling USD {ceiling:,.0f}")
            amount_text = ", ".join(parts)
        deadline = parse_date(h.get("closeDate")) or parse_date(s.get("responseDateStr") or s.get("responseDate"))
        detail_url = f"https://www.grants.gov/search-results-detail/{h['id']}"
        contact = ", ".join(filter(None, [s.get("agencyContactName"), s.get("agencyContactEmail"),
                                          s.get("agencyContactPhone")])) or None
        return {
            "source_record_id": h["id"],
            "title": h.get("title"),
            "funder": h.get("agency") or s.get("agencyName"),
            "programme": h.get("number"),
            "type": guess_type(h.get("title") or ""),
            "scope": "international",
            "country": "United States",
            "emirate": None,
            "description": strip_html(s.get("synopsisDesc")) or None,
            "research_areas": categories,
            "keywords": [],
            "eligibility_text": elig or None,
            "eligibility_flags": {"country": "United States", "applicant_types": applicant_types},
            "eligibility_warnings": warnings,
            "amount_min": floor, "amount_max": ceiling, "currency": "USD" if (floor or ceiling) else None,
            "amount_text": amount_text,
            "duration": None,
            "open_date": parse_date(h.get("openDate")),
            "deadline": deadline,
            "deadlines": [deadline.isoformat()] if deadline else [],
            "deadline_text": s.get("responseDateDesc") or None,
            "source_status": h.get("oppStatus"),
            "apply_url": detail_url,
            "required_documents": None,
            "contact": contact,
            "source_url": detail_url,
            "raw": {"hit": h, "funding_desc_url": s.get("fundingDescLinkUrl")},
        }


def _num(v):
    try:
        n = float(str(v).replace(",", ""))
        return n if n > 0 else None
    except (TypeError, ValueError):
        return None
