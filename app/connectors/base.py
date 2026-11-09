import re
import ssl

import httpx
import truststore

from app import config

SSL_CONTEXT = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

GRANT_FIELDS = [
    "source_record_id", "title", "funder", "programme", "type", "scope", "country", "emirate", "description",
    "research_areas", "keywords", "eligibility_text", "eligibility_flags", "eligibility_warnings",
    "amount_min", "amount_max", "currency", "amount_text", "duration", "open_date", "deadline", "deadlines",
    "deadline_text", "source_status", "apply_url", "required_documents", "contact", "source_url", "raw",
]


class Connector:
    key = ""
    name = ""
    url = ""
    method = "api"  # api / scraper / monitored / manual
    scope = "international"
    notes = ""

    def fetch(self, known: dict | None = None) -> list[dict]:
        """Return normalized grant dicts. `known` maps source_record_id -> content_hash of stored records."""
        raise NotImplementedError

    @staticmethod
    def http() -> httpx.Client:
        # The OS certificate store (truststore) completes chains that some government sites serve incomplete
        # (e.g. DoH Abu Dhabi), so certificates are still fully verified.
        return httpx.Client(timeout=httpx.Timeout(90, connect=20), follow_redirects=True, verify=SSL_CONTEXT,
                            transport=httpx.HTTPTransport(retries=2, verify=SSL_CONTEXT),
                            headers={"User-Agent": config.HTTP_USER_AGENT, "Accept-Language": "en"})


def guess_type(title: str, extra: str = "") -> str:
    t = f"{title} {extra}".lower()
    if re.search(r"scholarship", t):
        return "scholarship"
    if re.search(r"fellowship|postdoctoral|msca-pf|doctoral network", t):
        return "fellowship"
    if re.search(r"\bprize\b|\baward(s)?\b(?! ceiling)", t) and "grant" not in t:
        return "award"
    if re.search(r"challenge", t):
        return "challenge"
    return "grant"
