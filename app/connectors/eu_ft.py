"""EU Funding & Tenders portal via the public SEDIA search API (open + forthcoming topics)."""
import json
import logging
import time
from datetime import date

import httpx

from app import config
from app.connectors.base import Connector, guess_type
from app.util import parse_date, sha, strip_html

log = logging.getLogger("eu_ft")
SEARCH = "https://api.tech.ec.europa.eu/search-api/prod/rest/search"
STATUS = {"31094501": "forthcoming", "31094502": "open", "31094503": "closed"}
QUERY = {"bool": {"must": [{"terms": {"type": ["1"]}}, {"terms": {"status": ["31094501", "31094502"]}}]}}
EU_WARNING = ("EU programme: funding generally goes to organisations in EU Member States and associated "
              "countries. UAE-based organisations are usually not automatically eligible for funding and may "
              "join as associated partners or with own funds. Check the topic conditions.")


def _first(md, key):
    v = md.get(key) or []
    return v[0] if v else None


class EUFundingTenders(Connector):
    key = "eu_ft"
    name = "EU Funding & Tenders Portal"
    url = "https://ec.europa.eu/info/funding-tenders/opportunities/portal"
    method = "api"
    scope = "international"
    notes = "Public search API (apiKey=SEDIA). Open and forthcoming call topics."

    def fetch(self, known=None):
        out = []
        page = 1
        with self.http() as c:
            while len(out) < config.EU_MAX:
                files = {
                    "query": (None, json.dumps(QUERY), "application/json"),
                    "languages": (None, json.dumps(["en"]), "application/json"),
                    "sort": (None, json.dumps({"field": "deadlineDate", "order": "ASC"}), "application/json"),
                }
                # The portal sometimes answers slowly; retry a page before giving up.
                for attempt in range(3):
                    try:
                        r = c.post(SEARCH, params={"apiKey": "SEDIA", "text": "***", "pageSize": 100,
                                                   "pageNumber": page}, files=files)
                        r.raise_for_status()
                        break
                    except (httpx.TimeoutException, httpx.HTTPStatusError) as e:
                        if attempt == 2 or (isinstance(e, httpx.HTTPStatusError) and e.response.status_code < 500):
                            raise
                        log.warning("EU page %s attempt %s failed (%s), retrying", page, attempt + 1, e)
                        time.sleep(5 * (attempt + 1))
                results = r.json().get("results") or []
                if not results:
                    break
                for res in results:
                    rec = self.normalize(res)
                    if rec:
                        out.append(rec)
                page += 1
        return out[: config.EU_MAX]

    def normalize(self, res: dict) -> dict | None:
        md = res.get("metadata") or {}
        ident = _first(md, "identifier")
        title = _first(md, "title")
        if not ident or not title:
            return None
        today = date.today()
        deadlines = sorted({d for d in (parse_date(x) for x in md.get("deadlineDate") or []) if d})
        upcoming = [d for d in deadlines if d >= today]
        deadline = upcoming[0] if upcoming else (deadlines[-1] if deadlines else None)
        amount_min = amount_max = None
        expected = None
        try:
            bo = json.loads(_first(md, "budgetOverview") or "{}")
            for actions in (bo.get("budgetTopicActionMap") or {}).values():
                for a in actions:
                    if (a.get("action") or "").startswith(ident):
                        amount_min = a.get("minContribution") or amount_min
                        amount_max = a.get("maxContribution") or amount_max
                        expected = a.get("expectedGrants") or expected
        except (ValueError, TypeError):
            pass
        amount_text = None
        if amount_min or amount_max:
            amount_text = (f"EU contribution per project EUR {amount_min:,.0f} – {amount_max:,.0f}"
                           if amount_min and amount_max and amount_min != amount_max
                           else f"EU contribution per project up to EUR {(amount_max or amount_min):,.0f}")
            if expected:
                amount_text += f" ({expected} grants expected)"
        description = strip_html(_first(md, "descriptionByte"))
        conditions = strip_html(_first(md, "topicConditions"))
        programme_code = ident.split("-")[0]
        url = _first(md, "url") or res.get("url")
        if url and url.endswith(".json"):
            url = f"https://ec.europa.eu/info/funding-tenders/opportunities/portal/screen/opportunities/topic-details/{ident}"
        actions = md.get("typesOfAction") or []
        areas = [x for x in [_first(md, "callTitle"), _first(md, "destinationDescription")] if x]
        content = json.dumps({"t": title, "d": md.get("deadlineDate"), "b": _first(md, "budgetOverview"),
                              "s": md.get("status")}, sort_keys=True)
        return {
            "source_record_id": ident,
            "title": title,
            "funder": "European Commission",
            "programme": f"{programme_code} · {_first(md, 'callIdentifier') or ''}".strip(" ·"),
            "type": guess_type(title, " ".join(actions)),
            "scope": "international",
            "country": "European Union",
            "emirate": None,
            "description": description[:6000] or None,
            "research_areas": areas,
            "keywords": [k for k in md.get("keywords") or [] if not k.startswith(programme_code)][:15],
            "eligibility_text": conditions[:4000] or None,
            "eligibility_flags": {"country": "EU Member States / associated countries"},
            "eligibility_warnings": [EU_WARNING],
            "amount_min": amount_min, "amount_max": amount_max, "currency": "EUR" if (amount_min or amount_max) else None,
            "amount_text": amount_text,
            "duration": None,
            "open_date": parse_date(_first(md, "startDate")),
            "deadline": deadline,
            "deadlines": [d.isoformat() for d in deadlines],
            "deadline_text": f"{_first(md, 'deadlineModel')}" if _first(md, "deadlineModel") else None,
            "source_status": STATUS.get(_first(md, "status") or ""),
            "apply_url": url,
            "required_documents": ("See the topic conditions (admissibility, application form and page limits) "
                                   "on the portal page.") if conditions else None,
            "contact": None,
            "source_url": url,
            "content_hash": sha(content),
            "raw": {"identifier": ident, "typesOfAction": actions, "frameworkProgramme": md.get("frameworkProgramme")},
        }
